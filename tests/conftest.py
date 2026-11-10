import os
import shlex
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from bilayers_auto.contract import add_omero_metadata, biomero_argv  # noqa: F401 - used by tests

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "classical_segmentation"
CONFIG = EXAMPLE / "config.yaml"  # example workflow used by the library tests
DATA = Path(__file__).parent / "data"


@pytest.fixture
def config_path():
    return CONFIG


@pytest.fixture
def tiff_folder(tmp_path):
    """A folder with one small grayscale TIFF containing two bright blobs."""
    folder = tmp_path / "tiff"
    folder.mkdir()
    image = np.zeros((64, 64), dtype=np.uint16)
    image[10:20, 10:20] = 1000
    image[40:52, 40:52] = 1000
    tifffile.imwrite(folder / "blobs.tif", image)
    return folder


@pytest.fixture
def zarr_folder(tmp_path, tiff_folder):
    """The same image as OME-Zarr, as BIOMERO provides it in data/in."""
    from bilayers_auto.conversion import convert

    folder = tmp_path / "data" / "in"
    for store in convert(tiff_folder / "blobs.tif", folder, "zarr"):
        add_omero_metadata(store)
    return folder


@pytest.fixture
def python_cli_command():
    """The example tool's command, with this interpreter (the venv's python)."""
    return shlex.join([sys.executable, "-m", "bilayers_algorithms.classical_segmentation.classical_segmentation"])


@pytest.fixture(autouse=True)
def _clean_biomero_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("BIOMERO_"):
            monkeypatch.delenv(name)
