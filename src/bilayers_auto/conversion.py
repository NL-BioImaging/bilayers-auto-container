"""OME-Zarr <-> TIFF conversion around a workflow, using biomero-converter.

Inputs are converted when the images the Slurm client provides are in a format the spec's
input ``format`` list does not accept; outputs are converted back (by default to
OME-Zarr when the inputs were OME-Zarr and the tool does not write OME-Zarr itself).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

ZARR, TIFF = "zarr", "tiff"
FORMAT_ALIASES = {
    "zarr": ZARR, "ome.zarr": ZARR, "omezarr": ZARR, "ome-zarr": ZARR, "ngff": ZARR,
    "tif": TIFF, "tiff": TIFF, "ometiff": TIFF, "ome.tiff": TIFF, "ome-tiff": TIFF, "ome.tif": TIFF,
}
# biomero-converter output_format values
CONVERTER_FORMATS = {TIFF: "ometiff", ZARR: "omezarr04"}
OUTPUT_POLICIES = ("auto", "zarr", "tiff", "keep")


def normalize_formats(formats: Iterable[str] | str | None) -> set[str]:
    """The zarr/tiff formats in a bilayers ``format`` list (other formats are ignored)."""
    if isinstance(formats, str):
        formats = [formats]
    return {FORMAT_ALIASES[entry.lower().lstrip(".")] for entry in formats or [] if entry.lower().lstrip(".") in FORMAT_ALIASES}


def image_format(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".zarr"):
        return ZARR
    if name.endswith((".tif", ".tiff")):
        return TIFF
    return None


def find_images(folder: Path) -> list[Path]:
    """Top-level OME-Zarr stores and TIFF files in a folder."""
    if not folder.is_dir():
        return []
    return sorted(entry for entry in folder.iterdir() if image_format(entry))


IMAGE_SUFFIXES = (".ome.zarr", ".zarr", ".ome.tiff", ".ome.tif", ".tiff", ".tif")


def image_stem(path: Path) -> str:
    """The name of an image without its format suffix (``a.ome.zarr`` -> ``a``)."""
    name = path.name
    for suffix in IMAGE_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return name


def convert(source: Path, dest_folder: Path, target: str, zarr_format: str | None = None) -> list[Path]:
    """Convert one image (file or zarr store) with biomero-converter; returns the written paths.

    The converter names its output after the image name in the metadata (e.g. ``Image0``), so
    images with the same internal name would overwrite each other. Each image is therefore
    converted into its own folder and named after the source: ``<stem><suffix>``, or
    ``<stem>_<converter name><suffix>`` when one source holds several images.
    """
    from biomero_converter.converter import convert as converter_convert

    dest_folder.mkdir(parents=True, exist_ok=True)
    output_format = zarr_format if target == ZARR and zarr_format else CONVERTER_FORMATS[target]
    logger.info("Converting %s -> %s (%s)", source, dest_folder, output_format)
    staging = Path(tempfile.mkdtemp(prefix=".converting-", dir=dest_folder))
    try:
        results = json.loads(converter_convert(str(source), str(staging), output_format=output_format))
        converted = [Path(result["full_path"]) for result in results]
        written = []
        for path in converted:
            suffix = path.name[len(image_stem(path)):]
            name = image_stem(source) if len(converted) == 1 else f"{image_stem(source)}_{image_stem(path)}"
            dest = dest_folder / f"{name}{suffix}"
            if dest.exists():
                raise FileExistsError(f"{dest} already exists (two input images with the same name?)")
            shutil.move(str(path), str(dest))
            written.append(dest)
        return written
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def link_or_copy(source: Path, dest: Path) -> None:
    try:
        os.symlink(source.resolve(), dest, target_is_directory=source.is_dir())
    except OSError:  # e.g. no symlink privilege on Windows
        if source.is_dir():
            shutil.copytree(source, dest)
        else:
            shutil.copy2(source, dest)


def prepare_input(folder: Path, accepted: set[str], staging: Path, zarr_format: str | None = None) -> tuple[Path, set[str]]:
    """Return the folder the tool should read, and the image formats originally found.

    If images need converting, a staging folder is filled with the converted images
    plus links to (or copies of) everything else in ``folder``.
    """
    images = find_images(folder)
    found = {image_format(image) for image in images}
    if not accepted or found <= accepted:
        return folder, found
    targets = accepted & {ZARR, TIFF}
    target = TIFF if TIFF in targets else ZARR
    staging.mkdir(parents=True, exist_ok=True)
    for entry in sorted(folder.iterdir()):
        if entry in images and image_format(entry) not in accepted:
            convert(entry, staging, target, zarr_format)
        else:
            link_or_copy(entry, staging / entry.name)
    return staging, found


def output_target(policy: str, input_formats: set[str], output_formats: set[str]) -> str | None:
    """The format outputs not in an allowed format (see ``allowed_outputs``) are converted to, or None
    to leave them as the tool wrote them.

    ``auto`` converts only what the workflow requires: images in a format its outputs do not
    list, to one they list (OME-Zarr first). ``input_formats`` are not used by it: OME-Zarr
    coming in does not by itself mean OME-Zarr must go back.
    """
    if policy not in OUTPUT_POLICIES:
        raise ValueError(f"output format policy must be one of {OUTPUT_POLICIES}, not {policy!r}")
    if policy == "keep":
        return None
    if policy in (ZARR, TIFF):
        return policy
    if not output_formats:
        return None
    return ZARR if ZARR in output_formats else TIFF


def allowed_outputs(policy: str, output_formats: set[str], target: str | None) -> set[str]:
    """Output image formats left as written: the workflow's output formats (``auto``), else the target."""
    return set(output_formats) if policy == "auto" else ({target} if target else set())


def convert_in_place(folder: Path, target: str, zarr_format: str | None = None,
                     allowed: set[str] | None = None) -> list[Path]:
    """Convert the images in ``folder`` not in an ``allowed`` format to ``target``, replacing them."""
    allowed = allowed or {target}
    written = []
    for image in find_images(folder):
        if image_format(image) not in allowed:
            written += convert(image, folder, target, zarr_format)
            shutil.rmtree(image) if image.is_dir() else image.unlink()
    return written


def finalize_output(tool_folder: Path, final_folder: Path, target: str, zarr_format: str | None = None,
                    allowed: set[str] | None = None) -> list[Path]:
    """Convert the tool's images not in an ``allowed`` format (default: the target) to ``target`` into
    ``final_folder``; move everything else."""
    final_folder.mkdir(parents=True, exist_ok=True)
    allowed = allowed or {target}
    written = []
    for entry in sorted(tool_folder.iterdir()):
        fmt = image_format(entry)
        if fmt is not None and fmt not in allowed:
            written += convert(entry, final_folder, target, zarr_format)
        else:
            dest = final_folder / entry.name
            shutil.move(str(entry), str(dest))
            written.append(dest)
    return written
