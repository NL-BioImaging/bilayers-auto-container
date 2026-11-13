"""The read-outputs notebook template: up to date with its generator, and its cells run on
container-like output folders (OME-Zarr 0.4, 0.5 and 0.6, TIFF, tables, RO-Crate, zipped)."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
zarr = pytest.importorskip("zarr", minversion="3")
tifffile = pytest.importorskip("tifffile")
matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / ".claude" / "skills" / "create-bilayers-container" / "references" / "read-outputs.ipynb"
GENERATOR = ROOT / "scripts" / "make_read_outputs_notebook.py"

LOADER = "loaded = {p.name: read_table(p) for p in tables}"
ANALYSIS = "summary = {name: len(table) for name, table in loaded.items()}"


def _labels(shape, n, seed):
    rng = np.random.default_rng(seed)
    data = np.zeros(shape, dtype=np.uint16)
    yy, xx = np.mgrid[: shape[-2], : shape[-1]]
    for i in range(1, n + 1):
        cy, cx = rng.integers(10, shape[-2] - 10, size=2)
        data[..., (yy - cy) ** 2 + (xx - cx) ** 2 < 25] = i
    return data


def _ome_zarr(path, data, axes, version, label=False):
    """An OME-Zarr image with two levels, metadata as biomero-converter writes each version."""
    group = zarr.open_group(str(path), mode="w", zarr_format=2 if version == "0.4" else 3)
    axes_meta = [{"name": a, "type": "space" if a in "zyx" else "channel"} for a in axes]
    datasets = []
    for level in range(2):
        name = str(level) if version == "0.4" else f"s{level}"
        array = data[..., :: 2 ** level, :: 2 ** level]
        group.create_array(name, data=array, chunks=array.shape)
        scale = {"type": "scale", "scale": [1.0] * (len(axes) - 2) + [0.5 * 2 ** level] * 2}
        if version != "0.6":
            datasets.append({"path": name, "coordinateTransformations": [scale]})
        else:  # RFC-5: one transformation (here a sequence for level 1) from the dataset to "physical"
            transform = scale if level == 0 else {"type": "sequence", "transformations": [
                scale, {"type": "translation", "translation": [0.0] * len(axes)}]}
            datasets.append({"path": name, "coordinateTransformations": [
                {**transform, "input": {"path": name}, "output": {"name": "physical"}}]})
    multiscale = {"datasets": datasets}
    if version == "0.6":
        multiscale["coordinateSystems"] = [{"name": "physical", "axes": axes_meta}]
    else:
        multiscale["axes"] = axes_meta
    extra = {"image-label": {"colors": []}} if label else {}
    if version == "0.4":
        group.attrs.update({"multiscales": [{"version": "0.4", **multiscale}], **extra})
    else:
        group.attrs.update({"ome": {"version": version, "multiscales": [multiscale], **extra}})
    return group


def _output(folder, version):
    folder.mkdir(parents=True)
    _ome_zarr(folder / "img1_labels.ome.zarr", _labels((64, 64), 5, 1), "yx", version)
    raw = (np.random.default_rng(2).random((2, 3, 64, 64)) * 1000).astype(np.uint16)
    image = _ome_zarr(folder / "img2.ome.zarr", raw, "czyx", version)
    labels = image.create_group("labels")
    labels.attrs.update({"labels": ["nuclei"]} if version == "0.4" else {"ome": {"version": version, "labels": ["nuclei"]}})
    _ome_zarr(folder / "img2.ome.zarr" / "labels" / "nuclei", _labels((1, 3, 64, 64), 4, 3), "czyx", version, label=True)
    tifffile.imwrite(folder / "img3_labels.tif", _labels((64, 64), 3, 4))
    pd.DataFrame({"label": [1, 2], "area": [80, 81]}).to_csv(folder / "img1_objects.tsv", sep="\t", index=False)
    (folder / "config.yaml").write_text("name: example\n", encoding="utf-8")
    (folder / "parameters.yaml").write_text("segmentation:\n  sigma: 2.0\n", encoding="utf-8")
    (folder / "ro-crate-metadata.json").write_text(json.dumps({"@graph": [
        {"@id": "#run", "@type": "CreateAction", "name": "example run", "instrument": {"@id": "config.yaml"},
         "object": [{"@id": "file:///data/in/img1.ome.zarr"}], "result": [{"@id": "img1_labels.ome.zarr/"}]},
        {"@id": "#software", "@type": "SoftwareApplication", "name": "org/bilayers_example", "version": "v1.0.0"},
    ]}), encoding="utf-8")
    return folder


def _run_cells(output):
    namespace = {"display": print}
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    for index, cell in enumerate(c for c in nb["cells"] if c["cell_type"] == "code"):
        code = ("".join(cell["source"]).replace("@EXAMPLE_OUTPUT@", output.as_posix())
                .replace("@LOADER@", LOADER).replace("@ANALYSIS@", ANALYSIS))
        exec(compile(code, f"<cell {index}>", "exec"), namespace)
    import matplotlib.pyplot as plt
    plt.close("all")
    return namespace


def test_notebook_matches_its_generator(tmp_path):
    generated = tmp_path / "read-outputs.ipynb"
    subprocess.run([sys.executable, "-I", str(GENERATOR), str(generated)], check=True, capture_output=True)
    assert generated.read_text(encoding="utf-8") == NOTEBOOK.read_text(encoding="utf-8"), \
        "run python scripts/make_read_outputs_notebook.py"


@pytest.mark.parametrize("version", ["0.4", "0.5", "0.6"])
def test_notebook_reads_an_output_folder(tmp_path, version):
    ns = _run_cells(_output(tmp_path / "out", version))
    found = {path.relative_to(ns["OUTPUT"]).as_posix() for path in ns["images"]}
    assert found == {"img1_labels.ome.zarr", "img2.ome.zarr", "img2.ome.zarr/labels/nuclei", "img3_labels.tif"}
    rows = {row["image"]: row for row in map(ns["describe"], ns["images"])}
    assert rows["img1_labels.ome.zarr"]["objects"] == 5
    assert rows["img2.ome.zarr/labels/nuclei"]["objects"] == 4
    assert "objects" not in rows["img2.ome.zarr"]  # an intensity image, not labels
    assert rows["img2.ome.zarr"]["axes"] == "czyx"
    assert rows["img2.ome.zarr"]["pixel sizes"] == {"c": 1.0, "z": 1.0, "y": 0.5, "x": 0.5}
    assert ns["run"]["name"] == "example run"
    assert ns["parameters"] == {"segmentation": {"sigma": 2.0}}
    assert ns["summary"] == {"img1_objects.tsv": 2}


def test_notebook_reads_a_zipped_output(tmp_path):
    source = _output(tmp_path / "source", "0.5")
    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(out / "example.zip", "w", zipfile.ZIP_STORED) as archive:
        for path in source.rglob("*"):
            archive.write(path, path.relative_to(source))
    ns = _run_cells(out)
    assert ns["OUTPUT"] == out / "example"
    assert len(ns["images"]) == 4
