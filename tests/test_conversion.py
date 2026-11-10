import numpy as np
import pytest
import tifffile

from biomero_auto.conversion import TIFF, ZARR, convert, find_images, normalize_formats, prepare_input
from conftest import add_omero_metadata


def test_normalize_formats():
    assert normalize_formats(["tiff", "ometiff", "png"]) == {TIFF}
    assert normalize_formats(["ome.zarr", "TIF"]) == {ZARR, TIFF}
    assert normalize_formats(None) == set()


def test_zarr_tiff_round_trip(tmp_path, tiff_folder):
    original = tifffile.imread(tiff_folder / "blobs.tif")
    [store] = convert(tiff_folder / "blobs.tif", tmp_path / "zarr", ZARR)
    add_omero_metadata(store)
    [tiff] = convert(store, tmp_path / "tiff_out", TIFF)
    np.testing.assert_array_equal(np.squeeze(tifffile.imread(tiff)), original)


def test_zarr_without_omero_metadata_to_tiff(tmp_path, tiff_folder):
    """Fixed in biomero-converter 0.2.1."""
    [store] = convert(tiff_folder / "blobs.tif", tmp_path / "zarr", ZARR)
    [tiff] = convert(store, tmp_path / "tiff_out", TIFF)
    np.testing.assert_array_equal(np.squeeze(tifffile.imread(tiff)), tifffile.imread(tiff_folder / "blobs.tif"))


def test_rgb_and_multichannel_zarr_to_tiff(tmp_path):
    """3-channel OME-Zarr (8-bit RGB, 16-bit channels) to TIFF, names kept; fixed in biomero-converter 0.2.1."""
    rgb = np.random.default_rng(0).integers(0, 256, (64, 80, 3), dtype=np.uint8)
    channels = np.random.default_rng(1).integers(0, 4096, (3, 64, 80), dtype=np.uint16)
    tifffile.imwrite(tmp_path / "sample.ome.tif", rgb, photometric="rgb", ome=True,
                     metadata={"axes": "YXS", "Name": "sample"})
    tifffile.imwrite(tmp_path / "fluo_image.ome.tif", channels, ome=True,
                     metadata={"axes": "CYX", "Name": "fluo_image"})
    for name, data in (("sample", rgb), ("fluo_image", channels)):
        [store] = convert(tmp_path / f"{name}.ome.tif", tmp_path / "zarr", ZARR)
        [tiff] = convert(store, tmp_path / "tiff_out", TIFF)
        assert tiff.name == f"{name}.ome.tiff"
        np.testing.assert_array_equal(np.squeeze(tifffile.imread(tiff)), data)


def test_prepare_input_passes_accepted_formats_through(tiff_folder, tmp_path):
    assert prepare_input(tiff_folder, {TIFF}, tmp_path / "staging") == (tiff_folder, {TIFF})
    assert not (tmp_path / "staging").exists()


def test_prepare_input_converts_and_keeps_other_files(zarr_folder, tmp_path):
    (zarr_folder / "notes.txt").write_text("keep me")
    folder, found = prepare_input(zarr_folder, {TIFF}, tmp_path / "staging")
    assert found == {ZARR}
    assert sorted(path.name for path in folder.iterdir()) == ["blobs.ome.tiff", "notes.txt"]


def test_images_with_the_same_internal_name_do_not_overwrite_each_other(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    for name in ("a", "b"):  # both carry the OME image name "Image0"
        tifffile.imwrite(folder / f"{name}.ome.tif", np.zeros((8, 8), np.uint16), ome=True)
    staging, found = prepare_input(folder, {ZARR}, tmp_path / "staging")
    assert found == {TIFF}
    assert [image.name for image in find_images(staging)] == ["a.ome.zarr", "b.ome.zarr"]
    assert sorted(entry.name for entry in staging.iterdir()) == ["a.ome.zarr", "b.ome.zarr"]  # no leftovers
