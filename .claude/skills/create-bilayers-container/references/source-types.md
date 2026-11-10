# Source types

How to get from a source URL to `config.yaml` and the tool hook. Mixed sources exist; pick
the closest and combine.

## Existing bilayers workflow (`config.yaml` with `docker_image`, `exec_function`)

Reuse its `config.yaml`; check folder inputs/outputs against the Slurm contract (an
output folder the Slurm client can set), fix types the schema rejects (`bilayers-auto validate`).
If its image entrypoint is already a Slurm-aware wrapper, it may not need this template.

## BIAFLOWS / NEUBIAS workflow (`descriptor.json` with `schema-version: cytomine-0.1`, `wrapper.py`)

- Draft: `bilayers-auto from-biaflows <descriptor URL or path> -o config.yaml`, then delete
  `descriptor.json` (the Slurm client would otherwise pick it before `config.yaml`).
  (`--input-format`/`--output-format` from what the wrapper reads/writes). Then fill in
  citations, license, image flags (`depth`, `timepoints`, ...), output `subtype`, labels.
- Keep the original wrapper and its pipeline/macros unchanged in `/app/biaflows/`; install
  its own dependencies (BIAFLOWS utilities, Cytomine client, tool) as in its Dockerfile.
- Hook: `biaflows_tool("<its python> /app/biaflows/wrapper.py")`. Parameter ids stay the
  descriptor ids. If the wrapper needs Python < 3.12, give `bilayers_auto` its own venv.

## Fractal task (a package with `__FRACTAL_MANIFEST__.json`; https://fractal-analytics-platform.github.io/)

A Fractal task is a Python function run as `<python> <task>.py --args-json <file> --out-json
<file>` (fractal-task-tools' `run_fractal_task`) on OME-Zarr images, given by path:
`zarr_url` for a `parallel` task (one image per call), `zarr_urls` + `zarr_dir` for a
`non_parallel` one, and init (non-parallel, returns a `parallelization_list`) then compute
(parallel) for a `compound` one. It needs no Fractal server. Do not use BIAFLOWS for it.

- Draft: `bilayers-auto from-fractal <manifest path or URL> [--task <name>] --cli-command
  "/opt/fractal/bin/python -m <package>.<task module>" -o config.yaml`; it prints the
  `wrapper.py` line. Parameters come from the manifest's pydantic JSON schemas (`zarr_url(s)`,
  `zarr_dir`, `init_args` are set by the hook, not the user); nested objects become
  `<argument>__<field>` parameters (e.g. `channel__label`), empty values are left out so the
  task's defaults and validators apply. Then prune: drop Fractal-only options (e.g. a channel
  `wavelength_id` when the user picks channels by name; `overwrite_input`, since the task runs on
  a copy anyway) and write labels/descriptions for users.
- Draft from the manifest of the **release you pin**, not `main`: tasks and their arguments
  change between releases (read the task function at that tag).
- Hook: `fractal_tool([TASK_PYTHON, "-m", "<package>.<task module>"], kind=..., prepare=...,
  exclude=[...])` (`bilayers_auto.fractal`). It copies each input image into the output folder
  (tasks write into the image they are given), runs the task on the copies, renames new images
  to names ending in `.zarr` (tasks often write `<zarr_url><suffix>`, e.g. `a.ome.zarr_fused`,
  which would not be imported), and removes the copies when the task reported new images
  (`image_list_updates`), else keeps them (changed in place).
- **What the exported images lack** (the Slurm client gives single exported OME-Zarr images, not
  Fractal's HCS plates). Read the task code for each of these; test with an export-like copy of
  the test data (Fractal tables and `wavelength_id`s removed):
  - `omero.channels[].wavelength_id`: fractal-tasks-core requires it; `fractal_tool` adds
    `C01`, `C02`, ... when missing (`wavelength_ids=True`). Let users pick channels by label.
  - ROI tables (`tables/FOV_ROI_table`, `well_ROI_table`; AnnData in the store): not exported.
    Rebuild what the task needs in `prepare(ctx, images)`, from parameters (e.g. a FOV grid:
    tile size and overlap) and with the task's own Python and fractal-tasks-core
    (`write_table`), in a small script in the workflow repository; fail clearly when it cannot.
    Check the rebuilt table against the one Fractal wrote for the test data.
  - Plate/well structure: plate-level tasks (converters, `zarr_dir` + plate init tasks,
    well metadata updates) do not apply per image; image-level parallel tasks do. Logged
    errors like "no Zarr group was found" for the well are expected for single images.
  - Defaults that only make sense in Fractal (e.g. a required channel): fill them in
    `prepare` (it may set `ctx.params`) from the image (e.g. the first channel's label).
- Environment: the task in its own venv (`python -m venv /opt/fractal`, Python within the
  package's `requires-python`), installed from `requirements-tool.txt`: the `pip freeze` of a
  working environment (drop platform-only packages, e.g. `colorama` from Windows) with the
  package pinned to its release commit (`git+https://...@<sha>` when it is not on PyPI).
  fractal-tasks-core 1.x tasks need zarr 2 while `bilayers_auto` uses zarr 3, so they cannot
  share an environment (2.x, built on ngio, uses zarr 3; keep the separate venv anyway, for its
  pins). Use the same environment for local tests (`FRACTAL_PYTHON`) and CI as in the image.
- Inputs/outputs: OME-Zarr (`format: [zarr]`), `pyramidal: True`; set `depth` from the task's
  tags (2D/3D) and `tiled` for tasks working on fields of view.
- Test data: Fractal task packages usually have tests with small OME-Zarr data (`tests/data`)
  or download it (Zenodo via pooch). Without expected outputs in the source, make them with a
  **reference run of the original task**, called directly (`--args-json`), outside bilayers_auto,
  and check the container reproduces it; do the same for the export-like input (the reference
  then gets the same rebuilt tables). `--out-json` must not exist yet (the task refuses to run).
- Slurm: `meta_parallel`/`meta_non_parallel` in the manifest (cpus, memory) are what the task
  needs per call; note them in the README for the Slurm settings.

### Fractal workflow (JSON: an export from fractal-web, or a workflow template)

A Fractal workflow is a list of tasks with the arguments each step uses. Its JSON comes in three
forms, all read by `bilayers-auto from-fractal`: an export (`{name, description, task_list}`,
each task `{"pkg_name", "version", "name"}`, with `args_non_parallel`, `args_parallel`,
`type_filters`, `meta_*`, `alias`, `description`), a legacy export (fractal-server < 2.11: tasks
as `{"source": "pip_remote:<pkg>:<version>:<extras>::<task>"}`, filters in `input_filters.types`),
and a template from the Fractal website (the workflow under `data`, with `name`, `version`,
`description`). Do not use BIAFLOWS for it.

- Draft: `bilayers-auto from-fractal <workflow.json> -o config.yaml` writes the config and
  `fractal_steps.json`, the plan `wrapper.py` runs:
  `run(spec=CONFIG, tool=fractal_workflow(Path(__file__).with_name("fractal_steps.json"), task_python=...))`.
  The export has no argument schemas, only values: each package's manifest is read from its
  wheel at the exported version (`pip download --no-deps` for `--python-version`, default 3.12:
  fractal-tasks-core 1.x is not installable for newer Pythons). For packages not on PyPI (many
  are not, e.g. abbott, scmultiplex, Cellpose-SAM, ilastik and UZH converter tasks, which
  Fractal installs from GitHub release wheels), pass `--manifest <pkg>==<version>=<wheel or manifest>`.
  `version: null` in an export means "latest" in Fractal; pin a version (task names change, e.g.
  fractal-tasks-core 2.0 moved its converters into another package).
- Parameters: every argument the workflow sets, as `<step>__<argument>` (nested
  `<step>__<arg>__<field>`), in a section per step, with the workflow's value as default, typed
  from the task's schema (unions such as `segmentation_method` by the workflow's values; lists
  and objects as JSON text). Prune to what users should tune (channel, thresholds, label/table
  names); the plan keeps the workflow's values for the rest.
- The plan leaves out **converters of raw microscope data** (Cellvoyager, Harmony, IC6000, ...;
  task types `converter_*`, or by name in older manifests, which have no types): the Slurm client gives
  OME-Zarr. It keeps **Import OME-Zarr** (a converter of an existing OME-Zarr: `zarr_dir` +
  `zarr_name`), run per image: it adds the image/grid ROI tables and channel metadata the
  exported images lack, so later steps can use `image_ROI_table` (set their `roi_table` to it where the
  workflow used `well_ROI_table`/`FOV_ROI_table`). It flags plate/multiplexing steps (HCS plate,
  `reference_acquisition`): replace them with the package's image-level task where there is one
  (e.g. `Project Image (HCS Plate)` -> `Project Image`), or leave them out and say so.
- Steps run like Fractal runs them on its image list: each step on the images its
  `type_filters` and its task's `input_types` select (a missing type is false), new images join
  the list with the task's `output_types`, a call that reports no images changed its input in
  place. Input images start with the types Fractal's converters give (`is_3D` from Z), else a
  `Project Image` would select nothing. Without filters, steps after a type-changing step run on
  every image (e.g. segmenting the 3D input and its projection); the drafter notes this: add
  `type_filters` (e.g. `{"is_3D": false}`) as a Fractal user would.
- Output: the images the workflow made (with their labels/tables), the input copies removed; or
  the changed copies when it made none.
- In the survey of 14 real workflows (Fractal templates, the Fractal paper, lab repositories):
  almost all start with a raw-data converter; 9 of 14 have plate/multiplexing steps; image-level
  segmentation/measurement chains (e.g. Project Image, Threshold Segmentation, Measure Features
  of fractal-tasks-core 2.0) run on the Slurm client's images unchanged.
- Test like a single task: a reference run of the same steps called directly
  (`python -m <module> --args-json`) on the same (export-like) input, compared with the container's
  output (image, labels, tables).

## Galaxy tool or workflow (Tool Shed XML wrapper, or a `.ga` workflow; e.g. https://usegalaxy.org/workflows/list_published)

A Galaxy tool is an XML wrapper: `<requirements>` (conda packages, pinned), a Cheetah
`<command>` (and `<configfiles>`, often a JSON of the parameters) calling a script in the
tool's repository (`$__tool_directory__/x.py`) or a program, `<inputs>` (params, sections,
conditionals), `<outputs>` and `<tests>` with test data. A workflow (`.ga`, JSON) chains Tool
Shed tools by id and version; each step's `tool_state` fixes its parameter values. The
container runs the tools' scripts/programs directly, without Galaxy. Do not use BIAFLOWS for it.
(Done for the IWC nuclei segmentation workflow: `bilayers_GalaxyNucleiSegmentation`.)

- **Source of the workflow**: prefer its IWC repository (github.com/galaxyproject/iwc,
  `workflows/<topic>/<workflow>/`: the `.ga` with its `release`, `CHANGELOG.md`,
  `<workflow>-tests.yml` and `test-data/`) over a copy on a Galaxy server
  (`https://<galaxy>/api/workflows/<id>/download?format=json-download`), which can differ
  (name, runtime inputs, uuids). Record the IWC commit.
- **Tools at the pinned versions**: each step pins a Tool Shed repository changeset
  (`tool_shed_repository.changeset_revision`) and tool version. The Tool Shed API names the
  source (`/api/repositories?owner=<o>&name=<n>`: `remote_repository_url`), but its archives
  and raw files refuse scripted downloads (HTTP 403). Take the files from the source repository
  at the commit where the wrapper has the pinned version (`@TOOL_VERSION@` +
  `@VERSION_SUFFIX@` tokens or `version=`): a blobless clone and `git log -- tools/<tool>`;
  when several commits have it, use the merge to the main branch (the published one) and check
  that the scripts do not differ. Shared macros (`creators.xml`, `tests.xml`, ...) are git
  symlinks to `../../macros/`: copy the files they point to, not the link. Keep a table of tool,
  version, Tool Shed changeset and source commit (e.g. `galaxy/SOURCES.md`) and the license.
- **Draft**: `bilayers-auto from-galaxy <tool .xml | workflow .ga, path or URL> -o config.yaml`.
  For a tool: its params (sections and conditionals flattened to `<name>__<param>`, the
  conditional's options noted in each description), data input formats and outputs; macros
  from the same file are expanded (with their tokens and `<yield/>`), macros from imported
  files and repeats are reported, add those by hand if user-facing. For a workflow: it lists
  the steps (tool id, Tool Shed repository, inputs) on stderr; the parameters are its
  `parameter_input` steps and every value the tool steps fix (`<step>__<param>`;
  `--no-step-params` for only the former). Pass the tools' wrappers
  (`galaxy.bilayers_from_workflow(workflow, tools={<xml id>: <xml>})`) to get their types,
  options and help, and to leave out **stale `tool_state` values**: a workflow updated to newer
  tool versions keeps keys the pinned wrapper no longer has (`galaxy.stale_state`; e.g.
  `dark_bg`, `block_size` of the threshold step), which have no effect and must not become
  parameters. Then prune to what a user should tune (e.g. a filter sigma, a threshold method);
  keep the rest fixed in `wrapper.py`, with the workflow's values. Parameter names start with
  a letter (the Slurm client also makes shell variables of them; steps named like `2d_...` become `s2d_...`).
- **Which steps**: keep the analysis; drop steps that only make outputs for viewing in Galaxy
  (contrast enhancement and conversion to PNG for an overlay, overlays): the image viewer shows the labels
  over the image itself, and a PNG would be imported as another image. Say which steps are left
  out (README, notes).
- **Environment: one per tool**, as Galaxy resolves each tool's `<requirements>` into its own
  conda environment (or mulled biocontainer). Tools' pins conflict (e.g. giatools 0.6.0 vs
  0.7.3) and old tools pin old stacks (e.g. scikit-image 0.14 on Python 3.7), so one shared
  environment would not reproduce the workflow. Write `envs/<tool>.yml` from the requirements
  (channels `conda-forge`, `bioconda`, as Galaxy), resolve them once for linux-64 (e.g. in
  `mambaorg/micromamba:<version>`) and commit explicit lock files
  (`micromamba env export --explicit --md5`); the image installs a pinned micromamba and creates
  `/opt/envs/<tool>` from each lock, without solving again. Packages may be on bioconda only
  (giatools); check versions with `https://api.anaconda.org/package/<channel>/<name>`.
- **Hook**: a `tool(ctx)` in `wrapper.py` that, per image (Galaxy tools take single files, not
  folders), runs each step as its Cheetah `<command>` would: the step's environment's Python,
  the same script and arguments (including fixed outputs such as `./out.tiff` with the working
  directory set), and the params file its `<configfile>` renders (as JSON; Galaxy booleans
  render `true`/`false`, select values unquoted where the template has no quotes). Write the
  rendering as small functions per step and test them against the template for the default
  and each conditional branch.
- **Formats**: a wrapper's input `format` list says what Galaxy passes it, not that the pinned
  environment reads every variant: test each step on export-like OME-Zarr in the container. Keep
  the workflow's pins (do not upgrade a tool's requirements to make it read a format); if a
  pinned step cannot read OME-Zarr (e.g. giatools 0.6.0: `KeyError: 'unit'` on the channel axis,
  which OME-NGFF gives no unit), declare `format: [tiff]` so bilayers_auto converts (required by
  the workflow), and say why in `config.yaml` and the notes. Converted OME-TIFF is TCZYX and tools
  keep the input's axes, so their outputs may be 5-D where Galaxy's test gave 2-D; old steps can
  refuse that (scikit-image 0.14: "Only 2-D and 3-D images supported"): drop singleton T/C/Z
  axes between steps in the wrapper. Outputs stay in the format the tools write (TIFF; tables as
  TSV) when the workflow declares it. One channel: imaging tools often refuse multi-channel
  images, which exported images often are; add a channel parameter and extract it (only when the
  image has more than one).
- **Test data and expected outputs**: the workflow's IWC test (`<workflow>-tests.yml`: inputs,
  expected outputs, how they are compared, e.g. `image_diff` with `metric: iou`, `diff` with
  `lines_diff`); these come from Galaxy, so they are independent reference results. Name the
  expected files as the container writes them. Also the tools' `<tests>` (`test-data/`). Test
  the IWC input as is, as export-like OME-Zarr, and multi-channel with the channel parameter.
- **Local runs** need the per-tool environments (`GALAXY_ENVS`); on Windows rely on the
  container test, and create them in CI from the lock files (`mamba-org/setup-micromamba`).
- **Credit**: the workflow's `creator` (with ORCID), `license` and IWC location, the tools'
  repository license and citation (`CITATION.cff`, `<citations>` DOIs).

## Command-line tool (a CLI with flags for input/output folders)

- Parameters from its `--help` / argparse / click definitions; `cli_tag` = its flags.
- An input folder flag → `image` input; output folder flag → output with `cli_tag` or an
  `output_dir_set` parameter. Tools that take single files rather than folders need a
  `tool(ctx)` that loops over `ctx.inputs[...]` and calls the CLI per file.
- Hook: default (`run(spec=CONFIG)`).

## Python package / library / notebook

- Prefer an existing CLI entry point of the package. Otherwise write a minimal
  `tool(ctx)` in `wrapper.py` calling the package's public API with `ctx.params`; read and
  write images with the formats you declared (the wrapper converts around them).
- Parameters: the function's documented arguments that a user would tune.

## Pipeline/macro for a GUI application run headless (e.g. CellProfiler, Fiji/ImageJ, QuPath, ilastik)

- The application is the tool; the pipeline/macro/project file is part of the workflow repo.
- Parameters: the settings the source exposes (or that its README says to tune). Map them
  into the pipeline in `tool(ctx)` using the application's own mechanism where it has one
  (command-line overrides, macro arguments, project parameters); edit the file only if there
  is no such mechanism, and keep that edit minimal and covered by a test.
- Install a pinned application version; mind its Python/Java requirements.

## Container image only

Inspect the image's documentation and entrypoint for its CLI; write `config.yaml` for that
CLI and build `FROM` that image, adding the bilayers layer.
