# How BIOMERO runs a bilayers workflow

From BIOMERO 2.x (`biomero/schema_parsers.py`, `biomero/slurm_client.py`), which reads the
repository's `config.yaml` directly at the configured tag. It looks for `descriptor.json`,
`descriptor.yaml` and `config.yaml` in that order, so the repository must not contain a descriptor:

- The config is mapped to biomero-schema by `BilayersSchemaAdapter`; types map as
  `checkbox`→boolean, `radio`/`dropdown`/`textbox`→string, `integer`/`float` as is,
  folder types (`image`, `file`, `array`, `measurement`, `executable`) are kept.
- The job runs `singularity run <image>.sif $INPARAMS $OUTPARAMS $PARAMS`, i.e. the image
  **entrypoint** gets:
  - `INPARAMS`: `<cli_tag>="$DATA_PATH/data/in"` for each non-optional folder-type input;
  - `OUTPARAMS`: `<cli_tag>="$DATA_PATH/data/out"` for each output with a `cli_tag`, and for
    each parameter with `output_dir_set: true`;
  - `PARAMS`: `<cli_tag>="<value>"` for every other parameter (trailing `=` stripped from the
    tag), booleans as `True`/`False`, values from the OMERO script UI (defaults from the spec).
- Images are exported from OMERO into `data/in` (OME-Zarr, or TIFF when converted by BIOMERO);
  everything in `data/out` is imported back. The job fails if `data/out` is empty.
- `docker_image.org/name` is the image; the version comes from the repository tag.
- `label` is shown as the parameter name in OMERO, `description` as help, `mode`/`section_id`
  are informational.

`tests/conftest.py::biomero_argv` reproduces these arguments with BIOMERO's own functions.

## What the wrapper does with them

`bilayers_auto.run`:

1. Parses the arguments against the spec (also bilayers-UI style and BIAFLOWS style, plus
   `--params params.yaml`); validates choices, numbers and required values.
2. For each `image` input whose files are not in the input's `format` list (or in `tool_formats`,
   when the workflow accepts more formats than the tool reads), converts them (biomero-converter)
   into a staging folder next to `data/out`.
3. Runs the tool into a staging folder.
4. Converts output images only when required (`BIOMERO_OUTPUT_FORMAT`: `auto` converts images
   in a format the output `format` does not list, to one it lists, OME-Zarr first; `zarr`/`tiff`
   force a format; `keep` never converts).
5. FAIRification: `create_ro_crate` (fair-metadata-integration) writes the Workflow RO-Crate
   into `data/out`: `ro-crate-metadata.json`, the config, the run's values (`parameters.yaml`,
   by the config's sections) and the outputs; the input is
   described in it (`BIOMERO_RO_CRATE_INPUT=reference`, default) or copied (`copy`).
6. With `BIOMERO_ZIP_OUTPUT=true` (default `false`): replaces the contents of `data/out` by
   `<algorithm_folder_name>.zip`, stored (no compression).

## What BIOMERO does with `data/out`

BIOMERO on Slurm zips `data/out` (7z/zip), copies the zip to the OMERO server, and
`SLURM_Import_Results` (biomero-scripts) unzips it and:

- imports every image it finds, recursively (`.tif`, `.ome.tif(f)`, `.png`, `.zarr`/`.ome.zarr`
  and other Bio-Formats extensions; only the outermost Zarr stores) into the chosen
  dataset/plate, or attaches them;
- turns `.csv` files into OMERO tables (when chosen);
- attaches other files (`.json`, `.yaml`, arrays, ...) only with "attach file outputs";
- can attach the whole job zip to the project.

It has no RO-Crate handling, and images inside a nested zip are not imported. Hence the
defaults: no zip, and no input copy in `data/out`.

Environment overrides: `BIOMERO_CONVERT_INPUTS`, `BIOMERO_TOOL_FORMATS`, `BIOMERO_OUTPUT_FORMAT`, `BIOMERO_ZARR_FORMAT`,
`BIOMERO_RO_CRATE`, `BIOMERO_RO_CRATE_INPUT`, `BIOMERO_ZIP_OUTPUT`, `BIOMERO_WORKDIR`
(see `src/bilayers_auto/runner.py`).

## Running outside Slurm (Docker)

Only the entrypoint arguments matter. Mirror the BIOMERO job layout and mount the `data`
folder, so staging stays on the host disk:

```
docker run --rm -v <job>/data:/data <image> --<input tag>=/data/in --<output tag>=/data/out --<param>=<value>
docker run --rm -v <job>/data:/data <image> --params /data/params.yaml
```

Podman takes the same arguments (add `:Z` to the volume on SELinux hosts).

## Older deployments

BIOMERO versions that only read `descriptor.json` (BIAFLOWS, `cytomine-0.1`) call the
container with `--infolder --outfolder --gtfolder --local -nmc --<id> <value>`. The wrapper
accepts these too; generate the descriptor with `bilayers-auto to-biaflows config.yaml -o descriptor.json`.
