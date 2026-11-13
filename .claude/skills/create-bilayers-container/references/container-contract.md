# The container contract

What a container built from this template takes and produces, independent of who runs it.
How BIOMERO calls it and what OMERO does with the results: [biomero-contract.md](biomero-contract.md).

## Arguments

The image **entrypoint** (`wrapper.py`) gets the `config.yaml` values as arguments:

- `<cli_tag>=<folder>` for each folder-type input (`image`, `file`, `array`, `measurement`,
  `executable`), normally `/data/in`;
- `<cli_tag>=<folder>` for each output with a `cli_tag`, and for each parameter with
  `output_dir_set: true`, normally `/data/out`;
- `<cli_tag>=<value>` for every other parameter, booleans as `True`/`False`; parameters left
  out take their spec defaults.

Also accepted: bilayers-UI style, BIAFLOWS style (`--infolder --outfolder --gtfolder --local
-nmc --<id> <value>`) and `--params params.yaml` (`{name: value}`).

Inputs are single images (OME-Zarr or TIFF) in the input folder, one image per file or store.
The run must write at least one file to the output folder.

## What the wrapper does with them

`bilayers_auto.run`:

1. Parses the arguments against the spec; validates choices, numbers and required values.
2. For each `image` input whose files are not in the input's `format` list (or in `tool_formats`,
   when the workflow accepts more formats than the tool reads), converts them (biomero-converter)
   into a staging folder next to the output folder.
3. Runs the tool into a staging folder.
4. Converts output images only when required (`OUTPUT_FORMAT`: `auto` converts images
   in a format the output `format` does not list, to one it lists, OME-Zarr first; `zarr`/`tiff`
   force a format; `keep` never converts).
5. FAIRification: `create_ro_crate` (fair-metadata-integration) writes the Workflow RO-Crate
   into the output folder: `ro-crate-metadata.json`, the config, the run's values
   (`parameters.yaml`, by the config's sections) and the outputs; the input is
   described in it (`RO_CRATE_INPUT=reference`, default) or copied (`copy`).
6. With `ZIP_OUTPUT=true` (default `false`): replaces the contents of the output folder by
   `<algorithm_folder_name>.zip`, stored (no compression).

The defaults (no zip, input referenced rather than copied) suit a caller that imports every
image it finds in the output folder; see [biomero-contract.md](biomero-contract.md).

## Settings

Environment overrides: `CONVERT_INPUTS`, `TOOL_FORMATS`, `OUTPUT_FORMAT`, `OMEZARR_VERSION`,
`RO_CRATE`, `RO_CRATE_INPUT`, `ZIP_OUTPUT`, `WORKDIR` (see `src/bilayers_auto/runner.py`).

## Running it with Docker

Only the entrypoint arguments matter. Lay out a job folder with `data/in` and `data/out` and
mount `data`, so staging stays on the host disk:

```
docker run --rm -v <job>/data:/data <image> --<input tag>=/data/in --<output tag>=/data/out --<param>=<value>
docker run --rm -v <job>/data:/data <image> --params /data/params.yaml
```

Podman takes the same arguments (add `:Z` to the volume on SELinux hosts).
`bilayers-auto container-test` builds the image and runs it this way. Each workflow repository's
`run.sh` (from [run.sh](run.sh)) does it for any input and output folder, with Docker, Podman or
Apptainer/Singularity, staging in a temporary folder (`WORKDIR`).
