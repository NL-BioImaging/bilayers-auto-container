"""FAIRification of a run's output: Workflow RO-Crate (fair-metadata-integration) and packaging."""
from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from .spec import BilayersSpec, RunValues

logger = logging.getLogger(__name__)

CRATE_METADATA = "ro-crate-metadata.json"


def write_ro_crate(config_path: Path, input_paths: Sequence[Path], output_paths: Sequence[Path], dest_folder: Path,
                   copy_inputs: bool = False, parameters: Mapping[str, Any] | None = None) -> Path:
    """Make ``dest_folder`` the run's RO-Crate, with fair-metadata-integration.

    The crate holds ``ro-crate-metadata.json``, the bilayers config (the workflow, as
    mainEntity), the run's ``parameters`` written to ``parameters.yaml`` (about the run), and
    copies of each output, the run's result. The inputs are its object, referenced by their
    file URI unless ``copy_inputs``: BIOMERO imports every image found in the output folder,
    so a copy would re-import the input into OMERO.
    """
    from fair_metadata_integration import create_ro_crate

    create_ro_crate(dest_path=str(dest_folder), workflow_schema_filename=str(config_path),
                    input_paths=[str(path) for path in input_paths], output_paths=[str(path) for path in output_paths],
                    copy_inputs=copy_inputs, copy_outputs=True, parameters=parameters)
    logger.info("RO-Crate written to %s", dest_folder)
    return dest_folder / CRATE_METADATA


def run_parameters(spec: BilayersSpec, values: RunValues) -> dict[str, Any]:
    """The run's parameter values, each under its config entry's section_id as the config has it, and any
    BIAFLOWS-style arguments under ``extra``."""
    from fair_metadata_integration.bilayers import value_path

    parameters: dict[str, Any] = {}
    for name, value in values.params.items():
        *sections, key = value_path(spec.parameters[name])
        target = parameters
        for section in sections:
            target = target.setdefault(section, {})
        target[key] = value
    if values.extra:
        parameters["extra"] = dict(values.extra)
    return parameters


def zip_folder(folder: Path, name: str) -> Path:
    """Replace the contents of ``folder`` by ``<name>.zip`` holding them, stored without compression."""
    archive = folder.parent / f".{name}.zip.partial"
    entries = sorted(folder.rglob("*"))
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zip_file:
        for entry in entries:
            zip_file.write(entry, entry.relative_to(folder).as_posix())
    for entry in folder.iterdir():
        shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
    target = folder / f"{name}.zip"
    shutil.move(str(archive), str(target))
    logger.info("Output zipped (stored) to %s", target)
    return target
