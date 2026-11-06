"""Container entrypoint logic.

Resolve values, convert inputs, run the tool, convert outputs back, FAIRify the output
folder into a Workflow RO-Crate, and optionally zip it (stored) for the return to OMERO.

Settings can be passed to :func:`run` or overridden with environment variables:

``BIOMERO_CONVERT_INPUTS``  convert input images to a format the spec accepts (default ``true``)
``BIOMERO_TOOL_FORMATS``    comma-separated image formats the tool reads, when fewer than the spec's
                            input ``format`` (what the workflow accepts); the others are converted
``BIOMERO_OUTPUT_FORMAT``   ``auto`` (OME-Zarr back if OME-Zarr came in), ``zarr``, ``tiff`` or ``keep``
``BIOMERO_ZARR_FORMAT``     biomero-converter zarr format (default ``omezarr04``)
``BIOMERO_RO_CRATE``        make the output folder a Workflow RO-Crate (default ``true``)
``BIOMERO_RO_CRATE_INPUT``  ``reference`` the input in the crate (default) or ``copy`` it into the output folder
``BIOMERO_ZIP_OUTPUT``      replace the output folder contents by one uncompressed zip (default ``false``)

The defaults match what BIOMERO expects: it zips ``data/out`` itself and imports every image
found in it (recursively), so the output is left unzipped and holds no copy of the input.
``BIOMERO_WORKDIR``         folder for staging data (default: next to the output folder)
"""
from __future__ import annotations

import copy
import logging
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import conversion
from .spec import BilayersSpec, RunValues, SpecError, to_bool

logger = logging.getLogger("biomero_auto")

Tool = Callable[["RunContext"], None]


@dataclass
class RunContext:
    """What a tool function gets: the spec, the resolved values and the folders to use."""

    spec: BilayersSpec
    values: RunValues  # as received; outputs are the final (BIOMERO) folders
    inputs: dict[str, Path]  # folders the tool reads (converted copies if needed)
    outputs: dict[str, Path]  # folders the tool writes (staging folders if outputs get converted)
    workdir: Path

    @property
    def params(self) -> dict:
        return self.values.params

    def tool_values(self) -> RunValues:
        """The values with input/output folders replaced by the folders the tool should use."""
        values = copy.deepcopy(self.values)
        replaced = {Path(self.values.outputs[name]): path for name, path in self.outputs.items() if self.values.outputs.get(name)}
        values.inputs.update(self.inputs)
        values.outputs.update(self.outputs)
        for name in self.spec.output_dir_params():
            if values.params.get(name) and Path(values.params[name]) in replaced:
                values.params[name] = str(replaced[Path(values.params[name])])
        return values

    def command(self, cli_command: str | Sequence[str] | None = None) -> list[str]:
        """The tool command line, built from the spec (bilayers' generate_cli_command)."""
        return self.spec.command(self.tool_values(), cli_command)


def run_command(ctx: RunContext) -> None:
    """Default tool: run ``exec_function.cli_command`` with the spec's arguments."""
    argv = ctx.command()
    logger.info("Running: %s", shlex.join(argv))
    subprocess.run(argv, check=True)


def _setting(name: str, value, default):
    env = os.environ.get(f"BIOMERO_{name.upper()}")
    if env is not None:
        return env
    return default if value is None else value


def _formats(value: Sequence[str] | str | None) -> set[str]:
    """Tool formats from a list or a comma-separated string; empty when not set."""
    if isinstance(value, str):
        value = [entry.strip() for entry in value.split(",") if entry.strip()]
    formats = conversion.normalize_formats(value)
    unknown = [entry for entry in value or [] if not conversion.normalize_formats(entry)]
    if unknown:
        raise SpecError(f"tool formats must be zarr/tiff formats, not {unknown}")
    return formats


def run(argv: Sequence[str] | None = None, *, spec: str | Path | BilayersSpec, tool: Tool | None = None,
        convert_inputs: bool | None = None, tool_formats: Sequence[str] | str | None = None,
        output_format: str | None = None, zarr_format: str | None = None,
        ro_crate: bool | None = None, ro_crate_input: str | None = None, zip_output: bool | None = None,
        workdir: str | Path | None = None) -> int:
    """Run the workflow; returns the process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    try:
        spec = spec if isinstance(spec, BilayersSpec) else BilayersSpec(spec)
        values = spec.parse_args(argv)
        logger.info("Values: %s", values.as_dict())
        if tool is None:
            entrypoint = Path(sys.argv[0]).name
            if entrypoint.endswith(".py") and entrypoint in [Path(arg).name for arg in shlex.split(spec.cli_command)]:
                raise SpecError(f"exec_function.cli_command runs {entrypoint} itself; pass tool= to run() instead")
            tool = run_command
        settings = {
            "convert_inputs": to_bool(_setting("convert_inputs", convert_inputs, True)),
            "tool_formats": _formats(_setting("tool_formats", tool_formats, None)),
            "output_format": _setting("output_format", output_format, "auto"),
            "zarr_format": _setting("zarr_format", zarr_format, None),
            "ro_crate": to_bool(_setting("ro_crate", ro_crate, True)),
            "ro_crate_input": _setting("ro_crate_input", ro_crate_input, "reference"),
            "zip_output": to_bool(_setting("zip_output", zip_output, False)),
        }
        if settings["ro_crate_input"] not in ("reference", "copy"):
            raise SpecError(f"BIOMERO_RO_CRATE_INPUT must be 'reference' or 'copy', not {settings['ro_crate_input']!r}")
        final_outputs = {name: path for name, path in values.outputs.items() if path is not None}
        default_workdir = (next(iter(final_outputs.values())).resolve().parent if final_outputs else Path.cwd())
        # not a hidden (dot) folder: tools skip those, e.g. CellProfiler's default Images filter
        work = Path(_setting("workdir", workdir, default_workdir)) / f"biomero-work-{uuid.uuid4().hex[:8]}"
        work.mkdir(parents=True)
        try:
            _run(spec, values, tool, final_outputs, work, settings)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return 0
    except Exception as error:  # noqa: BLE001 - report any failure as a non-zero exit code
        logger.exception("Workflow failed: %s", error)
        return 1


def _run(spec, values, tool, final_outputs, work, settings) -> None:
    tool_inputs, input_formats = {}, set()
    for name in spec.folder_inputs():
        path = values.inputs.get(name)
        if path is None:
            continue
        item = spec.inputs[name]
        accepted = set()
        if settings["convert_inputs"] and item["type"] == "image":
            accepted = conversion.normalize_formats(item.get("format"))
            if settings["tool_formats"]:
                accepted = settings["tool_formats"]
        tool_inputs[name], found = conversion.prepare_input(path, accepted, work / "in" / name, settings["zarr_format"])
        input_formats |= found

    image_outputs = [name for name in final_outputs if spec.outputs[name].get("type") == "image"]
    output_formats = set().union(*(conversion.normalize_formats(spec.outputs[name].get("format")) for name in image_outputs))
    target = conversion.output_target(settings["output_format"], input_formats, output_formats) if image_outputs else None
    # With FAIRification the tool always writes to staging: the RO-Crate is built into the output folder
    fair = settings["ro_crate"] and bool(tool_inputs)
    staging = {}
    for name, final in final_outputs.items():
        final.mkdir(parents=True, exist_ok=True)
        if fair or (target and name in image_outputs):
            staging.setdefault(final, work / "out" / str(len(staging)))
    tool_outputs = {name: staging.get(final, final) for name, final in final_outputs.items()}
    for folder in staging.values():
        folder.mkdir(parents=True, exist_ok=True)

    tool(RunContext(spec=spec, values=values, inputs=tool_inputs, outputs=tool_outputs, workdir=work))

    input_paths = [values.inputs[name] for name in spec.folder_inputs() if name in tool_inputs]
    for final, folder in staging.items():
        results = folder
        if target:
            results = work / "results" / folder.name if fair else final
            conversion.finalize_output(folder, results, target, settings["zarr_format"])
        if fair:
            from .fair import run_parameters, write_ro_crate

            write_ro_crate(spec.path, input_paths, sorted(results.iterdir()) if results.is_dir() else [], final,
                           copy_inputs=settings["ro_crate_input"] == "copy", parameters=run_parameters(spec, values))

    if settings["zip_output"]:
        from .fair import zip_folder

        for final in dict.fromkeys(final_outputs.values()):
            zip_folder(final, spec.parsed.get("algorithm_folder_name") or "output")

__all__ = ["RunContext", "run", "run_command"]
