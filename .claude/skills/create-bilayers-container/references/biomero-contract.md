# How BIOMERO runs a bilayers workflow

BIOMERO-specific details: how BIOMERO calls the container and what OMERO does with its
results. The container's own contract is in [container-contract.md](container-contract.md).

## Calling the container

From BIOMERO 2.x (`biomero/schema_parsers.py`, `biomero/slurm_client.py`), which reads the
repository's `config.yaml` directly at the configured tag. It looks for `descriptor.json`,
`descriptor.yaml` and `config.yaml` in that order, so the repository must not contain a descriptor:

- The config is mapped to biomero-schema by `BilayersSchemaAdapter`; types map as
  `checkbox`→boolean, `radio`/`dropdown`/`textbox`→string, `integer`/`float` as is,
  folder types (`image`, `file`, `array`, `measurement`, `executable`) are kept.
- The job runs `singularity run <image>.sif $INPARAMS $OUTPARAMS $PARAMS` on Slurm (the
  Docker image converted to Singularity/Apptainer), i.e. the image **entrypoint** gets:
  - `INPARAMS`: `<cli_tag>="$DATA_PATH/data/in"` for each non-optional folder-type input;
  - `OUTPARAMS`: `<cli_tag>="$DATA_PATH/data/out"` for each output with a `cli_tag`, and for
    each parameter with `output_dir_set: true`;
  - `PARAMS`: `<cli_tag>="<value>"` for every other parameter, values from the OMERO script
    UI (defaults from the spec). Parameters are also exported as environment variables.
- `docker_image.org/name` is the image; the version comes from the repository tag.
- `label` is shown as the parameter name in OMERO, `description` as help, `mode`/`section_id`
  are informational.

`tests/conftest.py::slurm_argv` reproduces these arguments with BIOMERO's own functions.

## Images from OMERO

Images are exported from OMERO into `data/in`: OME-Zarr, or TIFF when converted by BIOMERO.
They are single OMERO images: pixels, axes, pixel sizes, OMERO channel names and the `omero`
rendering block, but no tool-specific metadata (e.g. Fractal ROI tables or channel
`wavelength_id`s) and no HCS plate/well layout. The tests' "export-like copy" is the test data
reduced to this.

## What BIOMERO does with `data/out`

The job fails if `data/out` is empty. Otherwise BIOMERO zips `data/out` (7z/zip), copies the
zip to the OMERO server, and `SLURM_Import_Results` (biomero-scripts) unzips it and:

- imports every image it finds, recursively (`.tif`, `.ome.tif(f)`, `.png`, `.zarr`/`.ome.zarr`
  and other Bio-Formats extensions; only the outermost Zarr stores, so new images need names
  ending in `.zarr`) into the chosen dataset/plate, or attaches them;
- turns `.csv` files into OMERO tables (when chosen);
- attaches other files (`.json`, `.yaml`, arrays, ...) only with "attach file outputs";
- can attach the whole job zip to the project.

It has no RO-Crate handling, and images inside a nested zip are not imported. Hence the
container's defaults: no zip, and no input copy in `data/out` (it would be imported again).

## Older deployments

BIOMERO versions that only read `descriptor.json` (BIAFLOWS, `cytomine-0.1`) call the
container BIAFLOWS style (see [container-contract.md](container-contract.md)); generate the
descriptor with `bilayers-auto to-biaflows config.yaml -o descriptor.json`.
