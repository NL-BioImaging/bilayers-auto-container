import json
import zipfile

import numpy as np
import pytest
import tifffile

from bilayers_auto.cli import main
from bilayers_auto.compare import compare_images, compare_outputs, image_stem, read_image, unpack
from bilayers_auto.conversion import ZARR, find_images, image_format
from bilayers_auto.sample_data import Synthetic, synthetic_image, write_synthetic


def test_synthetic_image_has_the_requested_objects_and_dimensions():
    spec = Synthetic(size=48, z=4, t=2, channels=3, objects=3, radius=4, noise=5)
    image, labels = synthetic_image(spec, seed=1)
    assert image.shape == (2, 3, 4, 48, 48) and image.dtype == np.uint16
    assert labels.shape == (4, 48, 48)
    assert sorted(np.unique(labels)) == [0, 1, 2, 3]
    assert image[0, 0][labels > 0].mean() > image[0, 0][labels == 0].mean() + 500


def test_synthetic_image_is_reproducible():
    first, second = synthetic_image(seed=3), synthetic_image(seed=3)
    assert np.array_equal(first[1], second[1])


def test_too_many_objects_are_reported():
    with pytest.raises(ValueError, match="fit"):
        synthetic_image(Synthetic(size=16, radius=5, objects=5))


def test_write_synthetic_ome_zarr_with_ground_truth(tmp_path):
    images = write_synthetic(tmp_path / "in", tmp_path / "expected", Synthetic(channels=2), images=2)
    assert [image.name for image in images] == ["synthetic_0.ome.zarr", "synthetic_1.ome.zarr"]
    assert find_images(tmp_path / "in") == images and all(image_format(image) == ZARR for image in images)
    assert read_image(images[0]).shape == (2, 64, 64)
    attrs = json.loads((images[0] / ".zattrs").read_text())
    assert [channel["label"] for channel in attrs["omero"]["channels"]] == ["c0", "c1"]
    labels = tifffile.imread(tmp_path / "expected" / "synthetic_0.tif")
    assert labels.shape == (64, 64) and labels.max() == 2


def test_write_synthetic_tiff(tmp_path):
    [image] = write_synthetic(tmp_path / "in", spec=Synthetic(z=3), as_zarr=False)
    assert image.name == "synthetic.ome.tif" and read_image(image).shape == (3, 64, 64)


def test_labels_compare_by_overlap_and_object_count():
    expected = synthetic_image(Synthetic(objects=3))[1].squeeze()
    relabelled = np.where(expected > 0, 10 - expected, 0).astype(np.uint16)  # other label values
    assert compare_images(relabelled, expected)[0]
    assert compare_images((expected > 0).astype(np.uint8), expected)[0]  # a binary mask
    merged = np.where(expected > 0, 1, 0) + (expected == 3)
    assert not compare_images(merged.astype(np.uint16), expected)[0]  # 2 objects instead of 3
    assert not compare_images(np.zeros_like(expected), expected)[0]
    assert not compare_images(expected[:10], expected)[0]


def test_intensities_compare_within_tolerance():
    expected = np.linspace(0, 1, 100, dtype=np.float32).reshape(10, 10)
    assert compare_images(expected + 1e-4, expected)[0]
    assert not compare_images(expected + 0.1, expected)[0]


def test_compare_outputs_matches_names_and_tables(tmp_path):
    out, expected = tmp_path / "out", tmp_path / "expected"
    write_synthetic(tmp_path / "in", expected, images=2)
    out.mkdir()
    for index in range(2):
        tifffile.imwrite(out / f"synthetic_{index}_labels.tif", tifffile.imread(expected / f"synthetic_{index}.tif"))
    (out / "ro-crate-metadata.json").write_text("{}")
    (out / "counts.csv").write_text("image,count\nsynthetic_0,2.0000001\n")
    (expected / "counts.csv").write_text("image,count\nsynthetic_0,2\n")
    (expected / "ro-crate-metadata.json").write_text('{"other": 1}')  # generated metadata is not compared
    results = compare_outputs(out, expected)
    assert [(result.actual.name, result.ok) for result in results] == [
        ("counts.csv", True), ("synthetic_0_labels.tif", True), ("synthetic_1_labels.tif", True)]

    (out / "counts.csv").write_text("image,count\nsynthetic_0,3\n")
    (out / "synthetic_1_labels.tif").unlink()
    results = {result.expected.name: result for result in compare_outputs(out, expected)}
    assert not results["counts.csv"].ok and "row 2" in results["counts.csv"].detail
    assert results["synthetic_1.tif"].actual is None and not results["synthetic_1.tif"].ok


def test_a_single_output_image_matches_a_single_expected_one(tmp_path):
    write_synthetic(tmp_path / "in", tmp_path / "expected")
    out = tmp_path / "out"
    out.mkdir()
    tifffile.imwrite(out / "mask.tif", tifffile.imread(tmp_path / "expected" / "synthetic.tif"))
    [result] = compare_outputs(out, tmp_path / "expected")
    assert result.ok and result.actual.name == "mask.tif"


def test_zipped_output_is_unpacked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(out / "job.zip", "w") as archive:
        archive.writestr("labels.tif", b"")
    assert (unpack(out, tmp_path) / "labels.tif").exists()
    assert image_stem(out / "a.ome.zarr") == "a" and image_stem(out / "a.tif") == "a"


def test_cli_synthetic_and_compare(tmp_path, capsys):
    assert main(["synthetic", str(tmp_path / "in"), "--expected", str(tmp_path / "expected"),
                 "--z", "3", "--noise", "50", "--format", "tiff"]) == 0
    assert main(["compare", str(tmp_path / "expected"), str(tmp_path / "expected")]) == 0
    assert "OK" in capsys.readouterr().out
    assert main(["compare", str(tmp_path / "in"), str(tmp_path / "expected")]) == 1  # noisy images are not labels


def test_intensity_images_are_not_mistaken_for_labels():
    from bilayers_auto.compare import is_labels

    rng = np.random.default_rng(0)
    intensities = rng.integers(100, 4000, size=(64, 64), dtype=np.uint16)
    labels = synthetic_image(Synthetic(objects=3))[1].squeeze()
    assert is_labels(labels) and not is_labels(intensities) and not is_labels(intensities.astype(np.float32))
    shifted = intensities.copy()
    shifted[:, 0] = 0  # same foreground almost everywhere, different values: not a match as intensities
    assert not compare_images(shifted, intensities)[0]
    assert compare_images((labels > 0).astype(np.uint8), labels, mode="labels")[0]
    assert not compare_images(labels.astype(np.uint16) * 7, labels, mode="intensities")[0]
