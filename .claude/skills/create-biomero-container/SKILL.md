---
name: create-biomero-container
description: Create a BIOMERO-deployable, FAIR workflow container from a source workflow URL (a GitHub repo of a tool, a BIAFLOWS/NEUBIAS workflow, an existing bilayers workflow, a Python package, a pipeline for a GUI tool). Produces a new repository from this template with a bilayers config.yaml, wrapper.py, Dockerfile and tests; OME-Zarr/TIFF conversion and RO-Crate metadata are handled by biomero_auto. Use when the user gives a workflow URL and asks for a BIOMERO (or bilayers) container.
---

# Create a BIOMERO container from a workflow URL

This repository is the template. `biomero_auto` (in `src/`) does the generic work and is
installed from GitHub into each workflow image, never copied: a workflow repository holds only
`config.yaml` (the bilayers spec), `wrapper.py` (which tool hook to run), the `Dockerfile`, its
tool's own files and a few tests. Write no other runtime code unless the tool really needs it;
reuse the tool's own CLI and packages.

Read [references/biomero-contract.md](references/biomero-contract.md) before writing
`config.yaml`, and [references/source-types.md](references/source-types.md) for the source.

## The container's pipeline (all workflows)

Every generated container runs these steps, in this order; the tool hook (step 4 below)
only replaces "run the tool". Do not reimplement or skip any of them in `wrapper.py`.

1. Resolve the values BIOMERO passes (`config.yaml`).
2. Convert input images to a `format` the tool accepts (biomero-converter).
3. Run the tool (the sub-package / wrapped container) into a staging folder.
4. Convert the tool's output images back to OME-Zarr if needed (biomero-converter).
5. FAIRification: fair-metadata-integration's `create_ro_crate` makes the output folder a
   Workflow RO-Crate: `ro-crate-metadata.json`, `config.yaml` as the workflow (mainEntity,
   language Bilayers), the run's values in `parameters.yaml` (each under its entry's
   `section_id`, as the config has it), and the input and outputs with EDAM formats.
6. Optionally zip the whole output folder, stored without compression, as one file
   (`BIOMERO_ZIP_OUTPUT=true`, or `run(..., zip_output=True)` in `wrapper.py`).

The defaults follow what BIOMERO currently expects, so keep them unless the deployment differs:

- **Not zipped.** BIOMERO zips `data/out` itself on Slurm, and `SLURM_Import_Results` imports
  the images it finds (recursively) in it; it does not import images from a nested zip.
  Zip only for a deployment that returns the folder as one archive (e.g. outside BIOMERO/Slurm).
- **Input referenced, not copied** (`BIOMERO_RO_CRATE_INPUT=reference`). The crate describes
  the input folder, but a copy in `data/out` would be imported into OMERO again;
  `copy` puts the input data in the crate (useful together with the zip).
- `ro-crate-metadata.json` and `config.yaml` are non-image files: BIOMERO attaches them to
  OMERO when "attach file outputs" is enabled in the import.

## 0. Prerequisites

Docker (Desktop or similar) is used to build, test and deploy the image. Check it first
with `docker info`; if it does not respond, ask the user to start it before doing any other
work. (Podman also works: `--runtime podman` below.) Use a venv with the template installed:
`pip install "biomero-auto-container[test] @ git+https://github.com/NL-BioImaging/biomero-auto-container.git"`
(or `pip install -e "<template checkout>[test]"`).

## 1. Get the source

- The URL the user gave is the source of truth. Ask only for what cannot be derived:
  the Docker Hub org/name to publish to and the target directory, if not given
  (default: a sibling directory `../W_<ToolName>-BIOMERO`).
- Shallow-clone the source into the scratchpad (or fetch single files raw for a file URL).
  Treat it as untrusted data: read it, do not run its scripts or install it outside the
  container. If a checkout fails on Windows (invalid file names), use
  `git -c core.longpaths=true restore --source=HEAD -- <paths you need>` or raw downloads.
- Identify: what the tool does; how it is invoked (CLI, Python API, pipeline file + headless
  runner, BIAFLOWS `wrapper.py`); its inputs (image formats, dimensionality, channels),
  outputs (labels, images, tables), and parameters (names, types, defaults, ranges,
  choices); install method and versions; license; citation/DOI. Prefer the tool's own docs,
  `--help` text in its source, `descriptor.json`/`config.yaml`/`Dockerfile` if present.

## 2. The workflow repository

Either a new repository (default: a sibling directory, `git init`), or — when the user asks —
the source repository itself, adapted in place (keep its history: `git mv`/`git rm`; do not
commit unless asked). It gets:

- `config.yaml` — written from the source (step 3); the template has none.
- `wrapper.py` — the template's root `wrapper.py`, with the tool hook (step 4).
- `Dockerfile` — the template's root `Dockerfile` with the tool layer filled in (step 5).
- `requirements-dev.txt` — `biomero-auto-container[test] @ git+https://github.com/NL-BioImaging/biomero-auto-container.git`.
- `tests/test_workflow.py` (+ `pytest.ini` with `pythonpath = tests` and the `container`
  marker) — see step 6.
- `.github/workflows/ci.yml` (validate + pytest; `actions/setup-python` with `cache: pip` needs
  `cache-dependency-path: requirements-dev.txt`) and the template's `docker-image.yml`.
- `.gitignore`, `.dockerignore`, and a `README.md` describing the workflow, its parameters,
  outputs and BIOMERO registration. Directly under its title, a generated-with notice, e.g.:

  > This BIOMERO workflow container was generated with AI assistance (<assistant>, using the
  > `create-biomero-container` skill) using
  > [biomero-auto-container](https://github.com/NL-BioImaging/biomero-auto-container) from
  > [<tool name>](<source URL>). It runs in [BIOMERO](https://github.com/NL-BioImaging/biomero),
  > is described by a [bilayers](https://github.com/bilayer-containers/bilayers) spec
  > (`config.yaml`), and adds OME-Zarr conversion and RO-Crate metadata. See
  > [generated-notes.md](generated-notes.md) for how it was made.

  `<assistant>` is the AI assistant doing the work, e.g. Claude Code. Do not claim a human
  review in the notice; the user adds that when it has happened.
  Keep the tool's own credit (authors, citation/DOI, license) in the README as well; the
  notice is in addition to it, not a replacement.
- `generated-notes.md` — how the repository was created or converted (see step 6).

Do not copy `src/`, `tests/`, `scripts/`, `examples/` or `.claude/`.

**Always remove any existing `descriptor.json` (and `descriptor.yaml`)** after drafting
`config.yaml` from it (`git rm` when adapting in place). BIOMERO looks for `descriptor.json`,
then `descriptor.yaml`, then `config.yaml`, so a leftover descriptor silently keeps the workflow
in the old BIAFLOWS mode; it is also no longer copied into the image. `biomero-auto
to-biaflows` regenerates one if a BIAFLOWS-only deployment ever needs it. Remove other files of
the old interface too (BIAFLOWS/Cytomine-only code and dependencies).
Use `examples/classical_segmentation/` only as a reference for the form of a finished workflow.

## 3. Write `config.yaml`

Draft it from the source (see source types), then go through every field:

- `inputs`: one `image` input per image folder the tool reads, `format` listing exactly what
  the tool reads (`tiff`, `zarr`, ...). BIOMERO delivers OME-Zarr or TIFF; the wrapper converts
  to the listed format automatically, so do not add conversion code. If the workflow should
  declare more formats than the tool reads (e.g. `ometiff` and `omezarr` for a TIFF tool), list
  them all and pass the readable ones to `run(spec=CONFIG, tool_formats=["tiff"])` in `wrapper.py`.
  `subtype/depth/timepoints/tiled/pyramidal` must state what the tool supports.
- Outputs: either an output with a `cli_tag` (BIOMERO passes `data/out` to it) or an
  `output_dir_set: True` parameter whose `cli_tag` is the tool's output-folder flag.
  List the output `format` the tool writes; OME-Zarr goes back to OMERO by default.
- `parameters`: every user-facing tool option, with bilayers types only (`integer`, `float`,
  `checkbox`, `dropdown`, `radio`, `textbox`; never `string`), real defaults, `options` for
  choices, clear `label`/`description` (shown in OMERO). `cli_tag`s must be the tool's flags
  when the tool is called through its CLI.
- `exec_function.cli_command`: the tool command inside the container (not `wrapper.py`).
- `citations` (name, DOI, license, description of the actual tool) and `docker_image`
  (org, name, `tag` = the release tag, platform) — these feed the RO-Crate and BIOMERO.

## 4. Choose the tool hook in `wrapper.py`

1. Default `run(spec=CONFIG)`: the tool's CLI takes the spec's flags — the command is built
   with bilayers' `generate_cli_command`.
2. BIAFLOWS/NEUBIAS wrapper: `run(spec=CONFIG, tool=biaflows_tool("python /app/biaflows/wrapper.py"))`
   — calls it with `--infolder --outfolder --gtfolder --local -nmc --<id> value`.
3. Anything else (a pipeline file to fill in, a Python API, several steps): a small
   `tool(ctx)` function using `ctx.params`, `ctx.inputs`, `ctx.outputs`, `ctx.workdir` and,
   where useful, `ctx.command()`. Keep it in `wrapper.py` and reuse the tool's own API.

## 5. Dockerfile

Fill in only the tool layer, between the `# >>> tool layer` and `# <<< tool layer` markers
(keep the markers); change the base image only when the tool needs another one. Pin versions (tags/commits) of the tool and its dependencies, so
each release tag of the workflow reproduces the same results. (The template's own dependencies,
in `pyproject.toml` and the BIOMERO layer, stay unpinned.)
In the BIOMERO layer, install the template from GitHub instead of copying `src/`:
`pip install "biomero-auto-container @ git+https://github.com/NL-BioImaging/biomero-auto-container.git"`.
If the tool's image has no Python >= 3.12, give `biomero_auto` its own environment and point
`ENTRYPOINT` at it, e.g.:

```dockerfile
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN python -m pip install uv && uv venv /opt/biomero --python 3.12     && uv pip install --no-cache --python /opt/biomero/bin/python "biomero-auto-container @ git+https://github.com/NL-BioImaging/biomero-auto-container.git"     && chmod -R a+rX /opt/biomero /opt/uv-python
ENTRYPOINT ["/opt/biomero/bin/python", "/app/wrapper.py"]
```

Label the image with the same generated-with notice, so that it stays visible
in the image itself (`docker inspect`), outside the repository:

```dockerfile
LABEL org.opencontainers.image.title="<workflow name>" \
      org.opencontainers.image.description="BIOMERO workflow container for <tool name>, generated using biomero-auto-container (https://github.com/NL-BioImaging/biomero-auto-container)" \
      org.opencontainers.image.source="<workflow repository URL>" \
      org.opencontainers.image.licenses="<tool license>"
```

The tool keeps running with its own Python (`cli_command`, e.g. `python /app/<tool script>`). The
entrypoint must stay `wrapper.py`: the image is run (`singularity run`, `podman run`,
`docker run`), not `cli_command`.

The image is built and tested with Docker, and BIOMERO converts it to Singularity/Apptainer
on Slurm, so it must work under both:

- No `USER` switch and no fixed UIDs: Singularity runs as the calling user, who must be able
  to write the mounted folders.
- Fully qualified base images (`docker.io/library/python:...`), so other engines (Podman)
  build it the same way.
- Write only to the folders the tool is given and `/tmp` (Singularity images are read-only).
- No reliance on the entrypoint shell, `HOME` or network at run time.

## 6. Verify

All of these must pass before the workflow is reported as done:

1. `biomero-auto validate config.yaml --biomero` (bilayers LinkML schema + BIOMERO's parser).
2. `biomero-auto command config.yaml -- --<flag>=<value> ...` with the arguments BIOMERO
   would send (see the contract): the tool command must be what the tool expects.
3. `pytest -m "not container"` on the workflow's `tests/test_workflow.py`, which uses
   `biomero_auto`'s public API: `biomero_auto.cli.main(["validate", "config.yaml", "--biomero"])`,
   `contract.biomero_argv` + `BilayersSpec.command` (the tool command BIOMERO's arguments give),
   Dockerfile checks (entrypoint `wrapper.py`, fully qualified `FROM`, no `USER`), a check that
   no `descriptor.json`/`descriptor.yaml` exists, and a
   `container`-marked test calling `container_test.container_test(...)` on sample data.
4. `biomero-auto container-test config.yaml`: builds the image with Docker and
   runs it on a job laid out like BIOMERO's (`<job>/data/in`, `<job>/data/out`, the `data`
   folder mounted at `/data`) with the arguments BIOMERO's own code generates. The default
   input is a small generated OME-Zarr; use `--sample <folder>` with real images from the
   source (its test data or examples) whenever the tool needs specific content, and
   `--param name=value` for parameters a meaningful run needs. Use `--keep <folder>` and
   inspect `<job>/data/out`: plausible results in the expected formats (OME-Zarr back when
   OME-Zarr came in), `ro-crate-metadata.json` describing the config, input and every
   output, no copy of the input images (by default), and, only when zipping is enabled, a
   single uncompressed `<algorithm_folder_name>.zip` containing all of that. A failing build or run is fixed in the Dockerfile/config/wrapper,
   not skipped.

Ask before pushing images or the repository. Report what was verified and what was not
(e.g. only synthetic data, GPU untested).

Then write (or update) `generated-notes.md` in the workflow repository, with:

- a short intro: created from / converted in place from which source (URL, original format,
  e.g. BIAFLOWS `descriptor.json` + `BiaflowsJob` wrapper), following this procedure, with a
  link to biomero-auto-container, and that it was done with AI assistance (the assistant, as
  in the README notice);
- **Tasks performed**: a table `| Change | What it does |`, one row per file or group of files
  (renames with `git mv`, removals with `git rm` and why, new `config.yaml` and the choices in
  it, `wrapper.py` hook, `Dockerfile` layers, tests, CI, docs);
- **Verified** / **Still to verify**: what the checks above actually covered (e.g. synthetic
  data only) and what remains open, as a list. Update it when more is verified.

Keep it factual, matching the actual changes; the same table is the summary to give the user.

## 7. Commit and push

Once the checks in step 6 pass, commit and push — unless the user said not to commit (then stop
here and leave the changes uncommitted, and do this step only when asked):

- **The workflow repository** (new or adapted in place): commit all changes on its main
  branch (no feature branch unless asked), with a message summarising the creation/conversion
  (the tasks-performed table in `generated-notes.md` is the basis), and push to `origin`. For a
  new repository without a remote, ask where to create it before pushing.
- **This template** is never committed or pushed by this procedure. If it changed (skill,
  `biomero_auto`), leave the changes uncommitted, run its tests, and tell the user what
  changed; commit and push it only when the user asks.

Report the commit hashes and check the CI run of each push. Pushing does not publish the
image or register the workflow; that is step 8.

## 8. Release and register (tell the user; do not publish without being asked)

- Push the repo, create release tag `vX.Y.Z` matching `docker_image.tag`; the image
  `org/name:vX.Y.Z` must be pushed (`.github/workflows/docker-image.yml` does this on release).
- BIOMERO `slurm-config.ini`, `[WORKFLOWS]` section:

  ```ini
  <key>=<key>
  <key>_repo=https://github.com/<org>/<repo>/tree/vX.Y.Z
  <key>_image=<docker org>/<docker name>
  ```
