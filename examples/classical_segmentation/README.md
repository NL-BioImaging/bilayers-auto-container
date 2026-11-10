# Example: classical segmentation

bilayers' classical segmentation algorithm (from
[bilayers-algorithms](https://github.com/bilayer-containers/bilayers-algorithms)) as a Slurm
workflow, laid out as a generated workflow repository has it: `config.yaml` and a `Dockerfile`
that is the template's root `Dockerfile` with the tool layer filled in. The tool reads TIFF, so
OME-Zarr from the Slurm client is converted to TIFF for it and its TIFF labels back to OME-Zarr.

The template's tests and CI use it. Build and run it like the Slurm client would (from the repository root):

```
bilayers-auto container-test examples/classical_segmentation/config.yaml
```
