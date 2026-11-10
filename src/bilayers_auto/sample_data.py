"""Synthetic test images with ground-truth labels, shaped like the input a workflow expects.

Each image holds bright objects (discs, or balls when ``z > 1``) on a dark background, with
optional noise; the objects' instance labels are the expected output of a segmentation.
Images are written as the Slurm client provides them (OME-Zarr by default, or OME-TIFF), the labels as
TIFF files with the same name in the ``expected`` folder, for ``compare_outputs``.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

DTYPES = ("uint8", "uint16", "float32")


@dataclass(frozen=True)
class Synthetic:
    """What to generate: the image size and dimensions, and the objects in it."""

    size: int = 64            # Y and X
    z: int = 1
    t: int = 1
    channels: int = 1
    objects: int = 2          # objects per image
    radius: int = 6           # mean object radius (pixels)
    intensity: float = 1000.0
    noise: float = 0.0        # standard deviation of Gaussian noise
    dtype: str = "uint16"

    def __post_init__(self):
        if self.dtype not in DTYPES:
            raise ValueError(f"dtype must be one of {DTYPES}")
        if min(self.size, self.z, self.t, self.channels) < 1 or self.objects < 0 or self.radius < 1:
            raise ValueError("size, z, t, channels and radius must be >= 1, objects >= 0")
        if 2 * self.radius + 2 > self.size:
            raise ValueError("radius too large for the image size")


def synthetic_image(spec: Synthetic = Synthetic(), seed: int = 0):
    """``(image TCZYX, labels ZYX)``: non-overlapping objects, labelled 1..n."""
    import numpy as np

    rng = np.random.default_rng(seed)
    labels = np.zeros((spec.z, spec.size, spec.size), dtype=np.uint16)
    zz, yy, xx = np.indices(labels.shape)
    placed = 0
    for _ in range(spec.objects * 50):
        if placed == spec.objects:
            break
        radius = max(1, int(round(rng.uniform(0.7, 1.3) * spec.radius)))
        margin = min(radius + 1, spec.size // 2)
        y, x = rng.integers(margin, spec.size - margin, size=2)
        z = rng.integers(0, spec.z) if spec.z > 1 else 0
        zscale = radius if spec.z > 1 else np.inf
        ball = ((yy - y) ** 2 + (xx - x) ** 2) / radius ** 2 + ((zz - z) / zscale) ** 2 <= 1
        grown = ((yy - y) ** 2 + (xx - x) ** 2) / (radius + 2) ** 2 + ((zz - z) / (zscale + 2)) ** 2 <= 1
        if labels[grown].any():  # keep a gap, so objects stay separate
            continue
        placed += 1
        labels[ball] = placed
    if placed < spec.objects:
        raise ValueError(f"only {placed} of {spec.objects} objects fit; use fewer or smaller objects")

    foreground = (labels > 0).astype(np.float32)
    image = np.empty((spec.t, spec.channels, spec.z, spec.size, spec.size), dtype=np.float32)
    for c in range(spec.channels):
        image[:, c] = foreground * spec.intensity / (c + 1)  # channels differ in brightness
    if spec.noise:
        image += rng.normal(0, spec.noise, image.shape)
    limit = np.iinfo(spec.dtype).max if spec.dtype != "float32" else None
    image = np.clip(image, 0, limit)
    return image.astype(spec.dtype), labels


def write_synthetic(folder: Path, expected: Path | None = None, spec: Synthetic = Synthetic(), *,
                    images: int = 1, as_zarr: bool = True, seed: int = 0, name: str = "synthetic") -> list[Path]:
    """Write ``images`` synthetic images to ``folder`` and their labels to ``expected``; returns the images."""
    import tifffile

    from .contract import add_omero_metadata
    from .conversion import ZARR, convert

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if expected is not None:
        Path(expected).mkdir(parents=True, exist_ok=True)
    written = []
    for index in range(images):
        image, labels = synthetic_image(spec, seed + index)
        stem = f"{name}_{index}" if images > 1 else name
        tiff = folder / f"{stem}.ome.tif"
        tifffile.imwrite(tiff, image, ome=True, metadata={"axes": "TCZYX", "Name": stem})
        if expected is not None:
            tifffile.imwrite(Path(expected) / f"{stem}.tif", labels.squeeze(), photometric="minisblack")
        if as_zarr:
            for store in folder.glob(f"{stem}.*zarr"):  # from an earlier run
                shutil.rmtree(store)
            [store] = convert(tiff, folder, ZARR)
            tiff.unlink()
            add_omero_metadata(store, channels=spec.channels)
            written.append(store)
        else:
            written.append(tiff)
    return written
