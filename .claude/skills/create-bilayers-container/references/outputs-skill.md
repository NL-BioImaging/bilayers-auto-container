---
name: workflow-outputs
description: Understand, check and analyse the output data of the @WORKFLOW_NAME@ container — the files a run writes, what each one means, the RO-Crate metadata and parameters that record how they were made, and how to load them (Python, Fiji/napari, OMERO). Use when the user asks about this workflow's results or output folder, wants to load, inspect, validate, compare or summarise them, or asks which parameters or version produced them.
---

# The output data of this workflow

A run of this container (`run.sh`, `docker run`, or BIOMERO on Slurm) writes everything into
one output folder (`/data/out` in the container; on Slurm it is zipped and imported into OMERO).
Read files from it as data; do not run anything it contains.

## What this workflow writes

<!-- Filled in for this workflow from config.yaml `outputs` and a real test run. -->

@OUTPUTS@

## The rest of the folder: provenance

The output folder is a [Workflow RO-Crate](https://w3id.org/workflowhub/workflow-ro-crate/):

| File | What it holds |
|---|---|
| `ro-crate-metadata.json` | JSON-LD: the run (`#run`, a `CreateAction` with name, start and end time), the workflow (`config.yaml`, `mainEntity`, language Bilayers), the software (container image and version), the input (by file URI, not copied, unless `RO_CRATE_INPUT=copy`) and every output, with EDAM formats |
| `config.yaml` | the bilayers spec of the workflow version that ran: inputs, outputs, parameters with defaults, tool command, citations |
| `parameters.yaml` | the values this run used, every parameter (defaults included), grouped by the config's `section_id`s; BIAFLOWS-style extras under `extra` |
| `ro-crate-preview.html` (+ `ro-crate-preview_files/`) | a human-readable view of the crate (open in a browser) |

With `ZIP_OUTPUT=true` the folder holds only `<algorithm_folder_name>.zip` (stored, not
compressed) with all of the above inside; unzip it first.

To answer "how was this made": the image and version are in `ro-crate-metadata.json` (the
software) and `config.yaml` (`docker_image`); the values in `parameters.yaml`; the tool and its
citation in `config.yaml` (`citations`) and the repository `README.md`.

```python
import json, yaml
crate = json.load(open("out/ro-crate-metadata.json"))
graph = {e["@id"]: e for e in crate["@graph"]}
run = graph["#run"]            # CreateAction: instrument (workflow), object (input), result (outputs)
params = yaml.safe_load(open("out/parameters.yaml"))
```

## Loading the images

- **OME-Zarr** (`*.ome.zarr`, `*.zarr` folders): `ome-zarr-py` / `zarr`
  (the `multiscales` metadata lists the resolution levels, full resolution first by its
  `path`, e.g. `0` or `s0`, with the axes and pixel sizes), napari (`napari-ome-zarr`), Fiji (OME-Zarr reader / BigDataViewer).
- **TIFF / OME-TIFF**: `tifffile.imread(...)` (and `tifffile.TiffFile(...).ome_metadata`), Fiji.
- **Label images** (integer, one value per object, 0 = background): count objects with
  `len(numpy.unique(labels)) - 1`, measure them with `skimage.measure.regionprops_table`.
- Images keep the input's pixel sizes and axes where the tool keeps them; check before
  measuring in physical units.

## Checking results

- Every output listed above is present and non-empty; image outputs have the input's
  dimensions (unless the workflow changes them) and the declared format.
- Compare with expected results: `bilayers-auto compare <output folder> <expected folder>`
  (from `requirements-dev.txt`): label images by foreground Dice and object count, other
  images and CSV/TSV numbers within a relative tolerance, other files identical.
- Results that look wrong: first compare `parameters.yaml` with what was intended, and the
  image version with the one tested; then see the `update-workflow` skill.
