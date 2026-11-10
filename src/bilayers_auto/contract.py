"""The BIOMERO side of the contract, for tests and container checks (needs the ``biomero`` package)."""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePath

import yaml

# omero rendering block as in OMERO exports (biomero-converter output lacks it)
OMERO_METADATA = {"channels": [{"active": True, "color": "FFFFFF", "label": "c0",
                                "window": {"start": 0, "end": 1000, "min": 0, "max": 65535}}],
                  "rdefs": {"defaultT": 0, "defaultZ": 0, "model": "greyscale"}}


def biomero_argv(config: Path, data_path: PurePath, **user_params) -> list[str]:
    """The container arguments BIOMERO's own code produces for a bilayers workflow.

    Uses BIOMERO's descriptor parser, parameter filtering, folder flags and env-var
    rendering, then expands the job template's "$VAR" references like bash would.
    ``data_path`` is BIOMERO's ``$DATA_PATH`` (folders are ``<data_path>/data/in|out``).
    """
    from biomero.schema_parsers import WorkflowDescriptorParser
    from biomero.slurm_client import SlurmClient

    descriptor = WorkflowDescriptorParser.parse_descriptor(yaml.safe_load(Path(config).read_text())).model_dump(by_alias=True)
    client = SlurmClient.__new__(SlurmClient)  # only stateless helpers are used
    client.generic_descriptor_from_github = lambda workflow: descriptor
    params = client.get_workflow_parameters("workflow")
    folders = client.workflow_bilayers_folder_params_to_subs(descriptor)
    flags = client.workflow_params_to_subs({name: params[name] for name in user_params})["PARAMS"]
    env = {**client.workflow_params_to_envvars(**user_params), "DATA_PATH": data_path.as_posix().rstrip("/")}
    line = " ".join([folders["INPARAMS"], folders["OUTPARAMS"], flags])
    line = re.sub(r"\$(\w+)", lambda match: env[match.group(1)].strip('"'), line)
    return shlex.split(line)


def add_omero_metadata(store: Path, channels: int = 1) -> None:
    """Add the omero rendering block OMERO exports include to an OME-Zarr store."""
    attrs_path = store / ".zattrs"
    attrs = json.loads(attrs_path.read_text())
    [channel] = OMERO_METADATA["channels"]
    attrs["omero"] = {**OMERO_METADATA, "channels": [{**channel, "label": f"c{index}"} for index in range(channels)]}
    attrs_path.write_text(json.dumps(attrs))


def write_sample_input(folder: Path, as_zarr: bool = True) -> list[Path]:
    """A small 2D image with two objects, as BIOMERO provides it (OME-Zarr by default)."""
    import numpy as np
    import tifffile

    folder.mkdir(parents=True, exist_ok=True)
    image = np.zeros((64, 64), dtype=np.uint16)
    image[10:20, 10:20] = 1000
    image[40:52, 40:52] = 1000
    tiff = folder / "sample.tif"
    tifffile.imwrite(tiff, image)
    if not as_zarr:
        return [tiff]
    from .conversion import ZARR, convert

    stores = convert(tiff, folder, ZARR)
    tiff.unlink()
    for store in stores:
        add_omero_metadata(store)
    return stores
