"""Compare a workflow's outputs with expected outputs (a reference run, or synthetic ground truth).

Every file in the expected folder must have a matching output: the output with the same name
(ignoring the image format, e.g. ``a.tif`` matches ``a.ome.zarr``), else one whose name starts
with the expected name (``a_labels.tif``), else the only output image when there is one.

- Label images (integer images whose objects are flat, see ``is_labels``; or ``mode="labels"``):
  the foreground overlap (Dice) must be at least ``min_dice`` and, when the output has instance
  labels, the object counts must match (label values themselves may differ).
- Other images (or ``mode="intensities"``): values must agree within ``rtol`` (relative to the
  expected value range).
- CSV/TSV tables: same shape, text cells equal, numbers within ``rtol``.
- Other files: identical content. Metadata the run generates (RO-Crate) is not compared.
"""
from __future__ import annotations

import csv
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .conversion import find_images, image_format, image_stem
from .fair import CRATE_METADATA

SKIPPED = {CRATE_METADATA, "parameters.yaml", "config.yaml", "ro-crate-preview.html", "ro-crate-preview_files"}
MODES = ("auto", "labels", "intensities")
LABEL_FLATNESS = 0.9  # share of equal neighbouring foreground pixels from which an image counts as labels


@dataclass
class Comparison:
    expected: Path
    actual: Path | None
    ok: bool
    detail: str

    def __str__(self) -> str:
        actual = self.actual.name if self.actual else "-"
        return f"{'OK  ' if self.ok else 'FAIL'} {self.expected.name} <-> {actual}: {self.detail}"


def read_image(path: Path):
    """The full-resolution array of a TIFF file or OME-Zarr store, without singleton axes."""
    import numpy as np

    if image_format(path) == "tiff":
        import tifffile

        return np.squeeze(tifffile.imread(path))
    import zarr

    group = zarr.open_group(str(path), mode="r")
    attrs = group.attrs.asdict()
    multiscales = attrs.get("multiscales") or attrs.get("ome", {}).get("multiscales")
    if not multiscales:  # a labels or plain array store
        return np.squeeze(np.asarray(next(iter(group.arrays()))[1]))
    return np.squeeze(np.asarray(group[multiscales[0]["datasets"][0]["path"]]))


def is_labels(array) -> bool:
    """An integer image whose objects are flat: neighbouring foreground pixels (almost) always have
    the same value. Intensity images, even with few grey values, vary from pixel to pixel."""
    import numpy as np

    if not np.issubdtype(array.dtype, np.integer) or array.ndim == 0 or array.shape[-1] < 2:
        return False
    left, right = array[..., :-1], array[..., 1:]
    pairs = (left > 0) & (right > 0)
    return not pairs.any() or float((left[pairs] == right[pairs]).mean()) >= LABEL_FLATNESS


def compare_images(actual, expected, min_dice: float = 0.9, rtol: float = 1e-3, mode: str = "auto") -> tuple[bool, str]:
    """``mode``: ``labels``, ``intensities`` or ``auto`` (labels when the expected image looks like one)."""
    import numpy as np

    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if actual.shape != expected.shape:
        return False, f"shape {actual.shape} != expected {expected.shape}"
    if mode == "labels" or (mode == "auto" and is_labels(expected) and np.issubdtype(actual.dtype, np.integer)):
        fg_actual, fg_expected = actual > 0, expected > 0
        total = fg_actual.sum() + fg_expected.sum()
        dice = 1.0 if total == 0 else 2 * np.logical_and(fg_actual, fg_expected).sum() / total
        n_actual, n_expected = len(np.unique(actual[fg_actual])), len(np.unique(expected[fg_expected]))
        detail = f"labels: Dice {dice:.3f} (min {min_dice}), objects {n_actual} (expected {n_expected})"
        counts_ok = n_actual == n_expected or n_actual <= 1  # a binary mask has no object count
        return bool(dice >= min_dice and counts_ok), detail
    expected_f, actual_f = expected.astype(np.float64), actual.astype(np.float64)
    scale = float(np.ptp(expected_f)) or 1.0
    error = float(np.max(np.abs(actual_f - expected_f))) / scale if expected_f.size else 0.0
    return error <= rtol, f"intensities: max relative error {error:.2g} (max {rtol})"


def _cells(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle, delimiter="\t" if path.suffix.lower() == ".tsv" else ","))


def compare_tables(actual: Path, expected: Path, rtol: float = 1e-3) -> tuple[bool, str]:
    rows_actual, rows_expected = _cells(actual), _cells(expected)
    if [len(row) for row in rows_actual] != [len(row) for row in rows_expected]:
        return False, f"table shape differs ({len(rows_actual)} rows, expected {len(rows_expected)})"
    for r, (row_actual, row_expected) in enumerate(zip(rows_actual, rows_expected)):
        for c, (a, e) in enumerate(zip(row_actual, row_expected)):
            try:
                a_number, e_number = float(a), float(e)
                # missing values (NaN) are equal to each other, as e.g. CellProfiler writes them
                same = (math.isnan(a_number) and math.isnan(e_number)) or \
                    math.isclose(a_number, e_number, rel_tol=rtol, abs_tol=rtol)
            except ValueError:
                same = a == e
            if not same:
                return False, f"row {r + 1}, column {c + 1}: {a!r} != expected {e!r}"
    return True, f"table: {len(rows_expected)} rows match"


def _match(expected: Path, outputs: list[Path], single_image: Path | None) -> Path | None:
    by_name = {output.name: output for output in outputs}
    if expected.name in by_name:
        return by_name[expected.name]
    stem = image_stem(expected) if image_format(expected) else expected.stem
    candidates = [output for output in outputs if bool(image_format(output)) == bool(image_format(expected))]
    for output in candidates:
        if (image_stem(output) if image_format(output) else output.stem) == stem:
            return output
    for output in candidates:
        if output.name.startswith(stem) and output.name[len(stem):][:1] in ("_", "-", "."):
            return output
    return single_image if image_format(expected) else None


def _entries(folder: Path) -> list[Path]:
    """Images (OME-Zarr stores count as one) and other files, recursively; RO-Crate metadata skipped."""
    entries = []
    for entry in sorted(folder.iterdir()):
        if entry.name in SKIPPED:
            continue
        if image_format(entry):
            entries.append(entry)
        elif entry.is_dir():
            entries += _entries(entry)
        else:
            entries.append(entry)
    return entries


def unpack(output: Path, workdir: Path) -> Path:
    """The output folder, or the contents of the single zip in it (when zipping is enabled)."""
    files = [entry for entry in output.iterdir() if entry.name not in SKIPPED]
    if len(files) == 1 and files[0].suffix == ".zip":
        target = workdir / files[0].stem
        with zipfile.ZipFile(files[0]) as archive:
            archive.extractall(target)
        return target
    return output


def _compare(actual: Path, expected: Path, min_dice: float, rtol: float, mode: str) -> tuple[bool, str]:
    if image_format(expected):
        return compare_images(read_image(actual), read_image(expected), min_dice, rtol, mode)
    if expected.suffix.lower() in (".csv", ".tsv"):
        return compare_tables(actual, expected, rtol)
    if expected.suffix.lower() == ".json":
        ok = json.loads(actual.read_text(encoding="utf-8")) == json.loads(expected.read_text(encoding="utf-8"))
        return ok, "JSON equal" if ok else "JSON differs"
    ok = actual.read_bytes() == expected.read_bytes()
    return ok, "identical" if ok else "content differs"


def compare_outputs(output: Path, expected: Path, min_dice: float = 0.9, rtol: float = 1e-3,
                    mode: str = "auto") -> list[Comparison]:
    """Compare every expected file with its matching output."""
    output, expected = Path(output), Path(expected)
    outputs = _entries(output)
    images = [entry for entry in outputs if image_format(entry)]
    expected_entries = _entries(expected)
    single = images[0] if len(images) == 1 and len(find_images(expected)) <= 1 else None
    results = []
    for entry in expected_entries:
        actual = _match(entry, outputs, single)
        if actual is None:
            results.append(Comparison(entry, None, False, "no matching output"))
            continue
        try:
            ok, detail = _compare(actual, entry, min_dice, rtol, mode)
        except Exception as error:  # noqa: BLE001 - an unreadable output is a failed comparison
            ok, detail = False, f"cannot read: {error}"
        results.append(Comparison(entry, actual, ok, detail))
    if not expected_entries:
        results.append(Comparison(expected, None, False, "the expected folder is empty"))
    return results
