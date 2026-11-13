"""Write the read-outputs notebook template (.claude/skills/create-bilayers-container/references/read-outputs.ipynb).

Edit the cells here, then run: python scripts/make_read_outputs_notebook.py
"""
import json
import sys
from pathlib import Path

MD, CODE = "markdown", "code"

CELLS = [
(MD, """# Reading the output of @WORKFLOW_NAME@

This notebook reads the output folder of one run of the @WORKFLOW_NAME@ container (`run.sh`,
`docker run`, or BIOMERO on Slurm, downloaded from OMERO): provenance, folder structure, every
image and table, and the workflow's own outputs ready for analysis. The
`workflow-outputs` skill (`.claude/skills/workflow-outputs/SKILL.md`) describes what each file
holds.

Requirements: `pip install numpy pandas "zarr>=3" tifffile pyyaml matplotlib` (zarr 3 reads
OME-Zarr 0.4, 0.5 and 0.6).

Set `OUTPUT` to the output folder (or the `.zip` the run produced) and run all cells."""),
(CODE, """from pathlib import Path

OUTPUT = Path("@EXAMPLE_OUTPUT@")  # the run's output folder or its .zip"""),
(CODE, """import json
import zipfile

import numpy as np
import pandas as pd
import tifffile
import yaml
import zarr

# A zipped run (ZIP_OUTPUT=true, or downloaded as one archive): extract it next to the zip.
if OUTPUT.is_file() and OUTPUT.suffix == ".zip":
    extracted = OUTPUT.with_suffix("")
    if not extracted.exists():
        with zipfile.ZipFile(OUTPUT) as archive:
            archive.extractall(extracted)
    OUTPUT = extracted
elif OUTPUT.is_dir() and [p.suffix for p in OUTPUT.iterdir()] == [".zip"]:
    archive_path = next(OUTPUT.iterdir())
    extracted = archive_path.with_suffix("")
    if not extracted.exists():
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extracted)
    OUTPUT = extracted
assert OUTPUT.is_dir(), f"no output folder at {OUTPUT}"
print(OUTPUT.resolve())"""),
(MD, """## Provenance

The output folder is a Workflow RO-Crate: `ro-crate-metadata.json` records the run (`#run`),
the workflow (`config.yaml`), the container image, the input and every output; `parameters.yaml`
holds the values this run used."""),
(CODE, """crate_file = OUTPUT / "ro-crate-metadata.json"
graph = {}
if crate_file.exists():
    graph = {entity["@id"]: entity for entity in json.loads(crate_file.read_text(encoding="utf-8"))["@graph"]}


def ids(value):
    \"\"\"The @ids an RO-Crate property refers to (one reference or a list).\"\"\"
    items = value if isinstance(value, list) else [value] if value else []
    return [item["@id"] if isinstance(item, dict) else item for item in items]


run = graph.get("#run", {})
for key in ("name", "startTime", "endTime"):
    print(f"{key:10} {run.get(key, '-')}")
for key in ("instrument", "object", "result"):
    print(f"{key:10} {', '.join(ids(run.get(key))) or '-'}")
for entity in graph.values():
    if "SoftwareApplication" in str(entity.get("@type")):
        print(f"{'software':10} {entity.get('name')} {entity.get('softwareVersion', entity.get('version', ''))}")

parameters_file = OUTPUT / "parameters.yaml"
parameters = yaml.safe_load(parameters_file.read_text(encoding="utf-8")) if parameters_file.exists() else {}
print(yaml.safe_dump(parameters, sort_keys=False) if parameters else "no parameters.yaml")"""),
(MD, """## Folder structure

OME-Zarr stores are shown down to their groups, not every chunk."""),
(CODE, """def is_zarr(path):
    return path.is_dir() and ((path / "zarr.json").exists() or (path / ".zattrs").exists() or (path / ".zgroup").exists())


def is_array(path):
    if (path / ".zarray").exists():
        return True
    meta = path / "zarr.json"
    return meta.exists() and json.loads(meta.read_text(encoding="utf-8")).get("node_type") == "array"


def tree(folder, prefix=""):
    entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name))
    if is_zarr(folder):
        # an OME-Zarr store: its groups (labels, wells, ...); its arrays (resolution levels) on one line
        levels = [p.name for p in entries if p.is_dir() and is_array(p)]
        if levels:
            print(f"{prefix}└── arrays: {', '.join(levels)}" if not any(p.is_dir() and not is_array(p) for p in entries)
                  else f"{prefix}├── arrays: {', '.join(levels)}")
        entries = [p for p in entries if p.is_dir() and not is_array(p)]
    for index, path in enumerate(entries):
        last = index == len(entries) - 1
        size = f"  ({path.stat().st_size:,} bytes)" if path.is_file() else ""
        print(f"{prefix}{'└── ' if last else '├── '}{path.name}{'/' if path.is_dir() else ''}{size}")
        if path.is_dir():
            tree(path, prefix + ("    " if last else "│   "))


print(OUTPUT.name + "/")
tree(OUTPUT)"""),
(MD, """## Images and tables

Every OME-Zarr image (and the label images in its `labels/` group), TIFF and table in the output.
`open_image` returns the full-resolution array with its axes and pixel sizes."""),
(CODE, """def zarr_attrs(group):
    attrs = dict(group.attrs)
    return attrs.get("ome", attrs)  # OME-Zarr 0.5 and 0.6 nest the metadata under "ome"


def axes_and_scale(multiscale, dataset):
    \"\"\"Axis names and the scale of a dataset, from OME-Zarr 0.4/0.5 or 0.6 (RFC-5) multiscales metadata.\"\"\"
    transforms = []
    for transform in dataset.get("coordinateTransformations", []):
        # 0.6: one transformation (or a sequence) from the dataset to a named coordinate system
        transforms += transform["transformations"] if transform["type"] == "sequence" else [transform]
    axes = multiscale.get("axes")
    if axes is None:  # 0.6: the axes belong to the coordinate system the dataset maps to
        systems = multiscale.get("coordinateSystems", [])
        output = next((t["output"].get("name") for t in dataset.get("coordinateTransformations", [])
                       if isinstance(t.get("output"), dict)), None)
        axes = next((s["axes"] for s in systems if s.get("name") == output), systems[0]["axes"] if systems else [])
    names = [axis["name"] if isinstance(axis, dict) else axis for axis in axes]
    scale = next((t["scale"] for t in transforms if t["type"] == "scale"), None)
    return names, scale


def open_image(path):
    \"\"\"Full-resolution array, axes (e.g. "tczyx") and pixel sizes per axis of an OME-Zarr image
    (0.4, 0.5 or 0.6) or TIFF.\"\"\"
    path = Path(path)
    if is_zarr(path):
        group = zarr.open_group(str(path), mode="r")
        multiscale = zarr_attrs(group)["multiscales"][0]
        dataset = multiscale["datasets"][0]
        axes, scale = axes_and_scale(multiscale, dataset)
        array = group[dataset["path"]]
        return array, "".join(axes) or None, dict(zip(axes, scale)) if scale and axes else None
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        return series.asarray(), series.axes.lower(), None


def find_images(folder):
    images = []
    for path in sorted(folder.rglob("*")):
        if is_zarr(path) and not is_array(path) and "multiscales" in zarr_attrs(zarr.open_group(str(path), mode="r")):
            if any(parent in images for parent in path.parents) and path.parent.name != "labels":
                continue
            images.append(path)
        elif path.is_file() and path.suffix.lower() in (".tif", ".tiff") and not any(is_zarr(p) for p in path.parents):
            images.append(path)
    return images


def is_labels(path):
    \"\"\"A label image: OME-Zarr image-label metadata, in a labels/ group, or named like one.\"\"\"
    path = Path(path)
    if is_zarr(path) and "image-label" in zarr_attrs(zarr.open_group(str(path), mode="r")):
        return True
    name = path.name.lower()
    return path.parent.name == "labels" or any(word in name for word in ("label", "mask", "seg", "object"))


def describe(path):
    array, axes, scale = open_image(path)
    data = np.asarray(array[...]) if array.size <= 2e8 else None  # skip statistics of very large images
    row = {"image": path.relative_to(OUTPUT).as_posix(), "axes": axes, "shape": tuple(array.shape),
           "dtype": str(array.dtype), "pixel sizes": scale}
    if data is not None:
        row.update(min=data.min(), max=data.max())
        if is_labels(path) and np.issubdtype(data.dtype, np.integer):
            row["objects"] = len(np.unique(data)) - int((data == 0).any())
    return row


images = find_images(OUTPUT)
tables = sorted(p for p in OUTPUT.rglob("*") if p.suffix.lower() in (".csv", ".tsv") and not any(is_zarr(q) for q in p.parents))
pd.DataFrame([describe(path) for path in images])"""),
(CODE, """def read_table(path):
    return pd.read_csv(path, sep="\\t" if path.suffix.lower() == ".tsv" else ",")


for path in tables:
    table = read_table(path)
    print(f"{path.relative_to(OUTPUT)}: {len(table)} rows, columns {list(table.columns)}")
    display(table.head())"""),
(MD, """## A first look

The middle plane of each image (the first channel and time point); label images in random
colours on black."""),
(CODE, """import matplotlib.pyplot as plt


def plane(path):
    array, axes, _ = open_image(path)
    axes = axes or "yx"[-array.ndim:].rjust(array.ndim, "?")
    index = tuple(slice(None) if axis in "yx" else (array.shape[i] // 2 if axis == "z" else 0)
                  for i, axis in enumerate(axes))
    return np.asarray(array[index])


def show(path, ax):
    data = plane(path)
    if is_labels(path) and np.issubdtype(data.dtype, np.integer) and data.min() >= 0:
        colours = np.random.default_rng(0).random((int(data.max()) + 1, 3))
        colours[0] = 0
        ax.imshow(colours[data], interpolation="nearest")
    else:
        ax.imshow(data, cmap="gray")
    ax.set_title(path.relative_to(OUTPUT).as_posix(), fontsize=8)
    ax.axis("off")


shown = images[:8]
if shown:
    fig, axes = plt.subplots(1, len(shown), figsize=(4 * len(shown), 4), squeeze=False)
    for path, ax in zip(shown, axes[0]):
        show(path, ax)
    plt.tight_layout()"""),
(MD, """## The outputs of @WORKFLOW_NAME@, ready for analysis

The workflow's own outputs per input image, joined as the `workflow-outputs` skill describes
(*How the outputs fit together*)."""),
(CODE, """@LOADER@"""),
(CODE, """@ANALYSIS@"""),
]


def notebook(cells):
    def source(text):
        lines = text.split("\n")
        return [line + "\n" for line in lines[:-1]] + [lines[-1]]

    return {
        "cells": [
            {"cell_type": kind, "metadata": {}, "source": source(text),
             **({"execution_count": None, "outputs": []} if kind == CODE else {})}
            for kind, text in cells
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "create-bilayers-container" / "references" / "read-outputs.ipynb"
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    out.write_text(json.dumps(notebook(CELLS), indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print("wrote", out)
