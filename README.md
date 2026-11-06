# biomero-auto-container

Template for [BIOMERO](https://github.com/NL-BioImaging/biomero) workflow containers described by a
[bilayers](https://github.com/bilayer-containers/bilayers) spec, with OME-Zarr ⇄ TIFF conversion
([biomero-converter](https://github.com/NL-BioImaging/biomero-converter)) and Workflow RO-Crate
metadata ([fair-metadata-integration](https://github.com/NL-BioImaging/fair-metadata-integration)).

To create a container for a workflow, ask Claude Code in this repository, e.g.
*"create a BIOMERO container for https://github.com/Neubias-WG5/W_NucleiSegmentation-CellProfiler"*;
the [`create-biomero-container`](.claude/skills/create-biomero-container/SKILL.md) skill does the rest.

## Layout

| File | Role |
|---|---|
| `config.yaml` | bilayers spec of the workflow; BIOMERO reads it from the repository at a release tag. Not in the template: written per workflow from its source |
| `wrapper.py` | image entrypoint; picks the tool hook |
| `Dockerfile` | generic image; the tool layer (between the `>>>`/`<<<` markers) is filled in per workflow |
| `src/biomero_auto/` | generic glue (below) |
| `examples/classical_segmentation/` | a complete example workflow (`config.yaml` + `Dockerfile`), used by the tests and CI |

The example is bilayers' classical segmentation, which reads TIFF: OME-Zarr from BIOMERO is converted
to TIFF for it and its TIFF labels back to OME-Zarr.

## What runs in the container

`wrapper.py` → `biomero_auto.run`:

1. **Reads the spec and the arguments** (`spec.py`): BIOMERO style (`--<cli_tag>="value"`, folders
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
4. **Converts outputs** back (`auto`: OME-Zarr in → OME-Zarr out).
5. **FAIRifies the output folder** (`fair.py`): fair-metadata-integration makes it a Workflow RO-Crate
   (`ro-crate-metadata.json`, the config, the run's values in `parameters.yaml`, the outputs; the input is
   referenced by its `file://` URI, not copied).
6. **Optionally zips it**, stored without compression (`BIOMERO_ZIP_OUTPUT=true`).

The defaults match BIOMERO: it zips `data/out` itself and imports every image found in it, so the
output is not zipped and holds no copy of the input (`BIOMERO_RO_CRATE_INPUT=copy` adds it).

Overrides via environment: `BIOMERO_CONVERT_INPUTS`, `BIOMERO_TOOL_FORMATS` (comma-separated), `BIOMERO_OUTPUT_FORMAT` (`auto|zarr|tiff|keep`),
`BIOMERO_ZARR_FORMAT`, `BIOMERO_RO_CRATE`, `BIOMERO_RO_CRATE_INPUT` (`reference|copy`),
`BIOMERO_ZIP_OUTPUT`, `BIOMERO_WORKDIR`.

## Running the image

Build and test with Docker; BIOMERO runs the same image with Singularity/Apptainer on Slurm:

```
docker build -t <org>/<name> .
docker run --rm -v ./job/data:/data <org>/<name> --<input tag>=/data/in --<output tag>=/data/out --<param>=<value>
```

For the example: `biomero-auto container-test examples/classical_segmentation/config.yaml`
(builds it with its own `Dockerfile` and runs it like BIOMERO).

`job/data/in` holds the images (OME-Zarr or TIFF), results land in `job/data/out`. Mounting the
whole `data` folder keeps the conversion staging folder on the host disk.

## Command line

```
biomero-auto validate config.yaml [--biomero]     # bilayers LinkML schema (+ BIOMERO's parser)
biomero-auto parse config.yaml -- <args>          # resolved values as JSON
biomero-auto command config.yaml -- <args>        # the tool command line
biomero-auto from-biaflows <descriptor.json|URL>  # draft config.yaml for a BIAFLOWS workflow
biomero-auto to-biaflows config.yaml              # descriptor.json for BIAFLOWS-only deployments
biomero-auto container-test [config.yaml]         # build the image with Docker and run it like BIOMERO
```

`container-test` options: `--sample <folder>` (default: generated OME-Zarr), `--param name=value`,
`--runtime podman` (default `docker`), `--no-build`, `--keep <job folder>`.

## Development

```
pip install -e ".[test]"
pytest
```

`tests/conftest.py::biomero_argv` builds container arguments with BIOMERO's own code, so the tests
check the actual BIOMERO contract.

bilayers' `parse.py` and `cli_generator.py` are vendored unmodified in
`src/biomero_auto/_vendor/bilayers` (BSD-3-Clause): bilayers is not on PyPI and its repository
cannot be checked out on Windows. Update them with `python scripts/sync_vendor.py`.

## Release

Tag `vX.Y.Z` equal to `docker_image.tag` in `config.yaml`; `.github/workflows/docker-image.yml`
pushes the image (secrets `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`). Register in BIOMERO's
`slurm-config.ini`:

```ini
[WORKFLOWS]
<key>=<key>
<key>_repo=https://github.com/<org>/<repo>/tree/vX.Y.Z
<key>_image=<docker org>/<docker name>
```

## Use of AI

Generative AI coding assistants were used in developing this software, including tests and documentation.
All changes are directed and approved by a maintainer, and checked by automated tests where applicable.

## License

[Apache-2.0]


[Apache-2.0]: http://opensource.org/licenses/Apache-2.0
