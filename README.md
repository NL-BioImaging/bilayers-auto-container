# bilayers-auto-container

Template for Slurm workflow containers described by a
[bilayers](https://github.com/bilayer-containers/bilayers) spec, with OME-Zarr ⇄ TIFF conversion
([biomero-converter](https://github.com/NL-BioImaging/biomero-converter)) and Workflow RO-Crate
metadata ([fair-metadata-integration](https://github.com/NL-BioImaging/fair-metadata-integration)).

To create a container for a workflow, ask Claude Code in this repository, e.g.
*"create a bilayers container for https://github.com/Neubias-WG5/W_NucleiSegmentation-CellProfiler"*;
the [`create-bilayers-container`](.claude/skills/create-bilayers-container/SKILL.md) skill does the rest.

## Layout

| File | Role |
|---|---|
| `config.yaml` | bilayers spec of the workflow; the Slurm client reads it from the repository at a release tag. Not in the template: written per workflow from its source |
| `wrapper.py` | image entrypoint; picks the tool hook |
| `Dockerfile` | generic image; the tool layer (between the `>>>`/`<<<` markers) is filled in per workflow |
| `src/bilayers_auto/` | generic glue (below) |
| `examples/classical_segmentation/` | a complete example workflow (`config.yaml` + `Dockerfile`), used by the tests and CI |

The example is bilayers' classical segmentation, which reads TIFF: OME-Zarr from the Slurm client is converted
to TIFF for it and its TIFF labels back to OME-Zarr.

## What runs in the container

`wrapper.py` → `bilayers_auto.run`:

1. **Reads the spec and the arguments** (`spec.py`): Slurm style (`--<cli_tag>="value"`, folders
   set to `data/in`/`data/out`), bilayers-interface style, BIAFLOWS style
   (`--infolder --outfolder --gtfolder --local -nmc --<name> value`), and/or `--params params.yaml`
   (`{name: value}`). Precedence: spec defaults < params file < command line. Gives a
   `RunValues` with `.as_dict()` for use inside the container.
2. **Converts inputs** to a `format` the spec accepts (`conversion.py`). When the workflow accepts
   more formats than the tool reads (e.g. `format: [ometiff, omezarr]` for a tool that reads TIFF),
   `run(spec=..., tool_formats=["tiff"])` converts the others.
3. **Runs the tool**: by default `exec_function.cli_command` with the arguments built by bilayers'
   own `generate_cli_command`; or `biaflows_tool(...)` for an existing BIAFLOWS wrapper
   (`biaflows.py`); or any `tool(ctx)` function.
4. **Converts outputs** only when the workflow requires it (`auto`: images in a format the output
   `format` does not list are converted to one it lists; `OUTPUT_FORMAT=zarr|tiff|keep`).
5. **FAIRifies the output folder** (`fair.py`): fair-metadata-integration makes it a Workflow RO-Crate
   (`ro-crate-metadata.json`, the config, the run's values in `parameters.yaml`, the outputs; the input is
   referenced by its `file://` URI, not copied).
6. **Optionally zips it**, stored without compression (`ZIP_OUTPUT=true`).

The defaults match the Slurm client: it zips `data/out` itself and imports every image found in it, so the
output is not zipped and holds no copy of the input (`RO_CRATE_INPUT=copy` adds it).

Overrides via environment: `CONVERT_INPUTS`, `TOOL_FORMATS` (comma-separated), `OUTPUT_FORMAT` (`auto|zarr|tiff|keep`),
`OMEZARR_VERSION`, `RO_CRATE`, `RO_CRATE_INPUT` (`reference|copy`),
`ZIP_OUTPUT`, `WORKDIR`.

## Running the image

Build and test with Docker; the Slurm client runs the same image with Singularity/Apptainer on Slurm:

```
docker build -t <org>/<name> .
docker run --rm -v ./job/data:/data <org>/<name> --<input tag>=/data/in --<output tag>=/data/out --<param>=<value>
```

For the example: `bilayers-auto container-test examples/classical_segmentation/config.yaml`
(builds it with its own `Dockerfile` and runs it like the Slurm client).

`job/data/in` holds the images (OME-Zarr or TIFF), results land in `job/data/out`. Mounting the
whole `data` folder keeps the conversion staging folder on the host disk.

## Command line

```
bilayers-auto validate config.yaml [--slurm]     # bilayers LinkML schema (+ the Slurm client's parser)
bilayers-auto parse config.yaml -- <args>          # resolved values as JSON
bilayers-auto command config.yaml -- <args>        # the tool command line
bilayers-auto from-biaflows <descriptor.json|URL>  # draft config.yaml for a BIAFLOWS workflow
bilayers-auto from-fractal <manifest|workflow>     # draft config.yaml for a Fractal task or workflow (+ fractal_steps.json)
bilayers-auto from-galaxy <tool.xml|wf.ga|URL>     # draft config.yaml for a Galaxy tool or workflow
bilayers-auto to-biaflows config.yaml              # descriptor.json for BIAFLOWS-only deployments
bilayers-auto container-test [config.yaml]         # build the image with Docker and run it like the Slurm client
bilayers-auto synthetic <in> [--expected <dir>]    # synthetic input images + ground-truth labels
bilayers-auto compare <out> <expected>             # compare a workflow's outputs with expected ones
```

`container-test` options: `--sample <folder>` (default: generated OME-Zarr), `--param name=value`,
`--runtime podman` (default `docker`), `--no-build`, `--keep <job folder>`,
`--expected <folder>` (compare the outputs with expected ones), `--synthetic` (synthetic input,
compared with its ground truth), `--min-dice`, `--rtol`, `--mode auto|labels|intensities`.

Fractal tasks run with `bilayers_auto.fractal.fractal_tool` as the tool in `wrapper.py`: the task is
called as Fractal calls it (`--args-json`/`--out-json`) on a copy of each input image; new images
it reports are the output (renamed to end in `.zarr`), and channels get the `wavelength_id`
Fractal expects. A `prepare` function can add other Fractal metadata the exported images lack (ROI tables). `fractal_workflow("fractal_steps.json")` runs a Fractal
workflow (an export or template, drafted with `from-fractal`) step by step, selecting images by their types
as Fractal does; converters of raw microscope data are left out, `Import OME-Zarr` adds the ROI tables.

Synthetic images (`synthetic`, or `container-test --synthetic` with the same options prefixed
`--synthetic-`): `--size`, `--z`, `--t`, `--channels`, `--objects`, `--radius`, `--noise`,
`--dtype`; `synthetic` also takes `--images`, `--format zarr|tiff`, `--seed`, `--name`.
Images are written as the Slurm client provides them (OME-Zarr by default), their instance labels as TIFF
files of the same name in the `--expected` folder.

Comparison: each expected file must match an output with the same name (ignoring the image
format) or whose name starts with it (`a.tif` ~ `a_labels.ome.zarr`). Label images (integer images
whose objects are flat: neighbouring foreground pixels share their value) must overlap
(foreground Dice >= `--min-dice`, default 0.9) and have the same object count (label values may
differ; a binary mask is compared by overlap only); other images and CSV/TSV numbers must agree
within `--rtol` (default 1e-3); other files must be identical. RO-Crate metadata is not compared.

## Development

```
pip install -e ".[test]"
pytest
```

`tests/conftest.py::slurm_argv` builds container arguments with the Slurm client's own code, so the tests
check the actual Slurm contract.

bilayers' `parse.py` and `cli_generator.py` are vendored unmodified in
`src/bilayers_auto/_vendor/bilayers` (BSD-3-Clause): bilayers is not on PyPI and its repository
cannot be checked out on Windows. Update them with `python scripts/sync_vendor.py`.

## Release

Tag `vX.Y.Z` equal to `docker_image.tag` in `config.yaml`; `.github/workflows/docker-image.yml`
builds the image and, when its `PUBLISH` is `"true"` (set when the workflow repository is created
with a registry), pushes it to the registry in `docker_image.org` (Docker Hub, or e.g.
`quay.io/<org>`) with the repository's Actions secrets (`REGISTRY_USERNAME`, `REGISTRY_TOKEN`; or
`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`). Once the image is published, register the workflow with
the Slurm client: see
[`biomero-contract.md`](.claude/skills/create-bilayers-container/references/biomero-contract.md#registering-a-workflow).

## Use of AI

Generative AI coding assistants were used in developing this software, including tests and documentation.
All changes are directed and approved by a maintainer, and checked by automated tests where applicable.

## License

[Apache-2.0]


[Apache-2.0]: http://opensource.org/licenses/Apache-2.0
