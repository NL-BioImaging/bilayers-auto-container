"""Bilayers <-> BIAFLOWS.

* :func:`biaflows_tool` runs an existing BIAFLOWS/NEUBIAS ``wrapper.py`` (which parses
  its arguments with ``BiaflowsJob.from_cli``) from a bilayers-described container,
  calling it the way BIOMERO calls BIAFLOWS workflows.
* :func:`bilayers_from_descriptor` drafts a bilayers config from a BIAFLOWS
  ``descriptor.json`` (cytomine-0.1), e.g. when wrapping a NEUBIAS workflow.
* :func:`descriptor_from_bilayers` writes a BIAFLOWS descriptor for deployments
  that only read ``descriptor.json``; the container accepts the resulting
  ``--infolder/--outfolder/--gtfolder/--local -nmc/--<name>`` arguments.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from typing import Any, Callable, Mapping, Sequence

from .spec import BOOLEAN_TYPES, BilayersSpec

logger = logging.getLogger(__name__)

SERVER_ID_PREFIX = "cytomine"


def biaflows_args(ctx) -> list[str]:
    """BIAFLOWS-style arguments for a run context (see runner.RunContext)."""
    infolder = next(iter(ctx.inputs.values()))
    outfolder = next(iter(ctx.outputs.values()))
    gtfolder = ctx.workdir / "gt"
    gtfolder.mkdir(parents=True, exist_ok=True)
    args = ["--infolder", str(infolder), "--outfolder", str(outfolder), "--gtfolder", str(gtfolder), "--local", "-nmc"]
    skip = set(ctx.spec.output_dir_params())
    for name, value in ctx.params.items():
        if value is None or name in skip:
            continue
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, list):
            value = ",".join(str(entry) for entry in value)
        args += [f"--{name}", str(value)]
    return args


def biaflows_tool(command: str | Sequence[str]) -> Callable:
    """Tool function running a BIAFLOWS wrapper, e.g. ``biaflows_tool("python3.7 /app/biaflows/wrapper.py")``."""
    def run_biaflows(ctx) -> None:
        argv = (shlex.split(command) if isinstance(command, str) else list(command)) + biaflows_args(ctx)
        logger.info("Running BIAFLOWS workflow: %s", shlex.join(argv))
        subprocess.run(argv, check=True)
    return run_biaflows


def _bilayers_type(param: Mapping[str, Any]) -> dict[str, Any]:
    if param.get("value-choices"):
        options = [{"label": str(choice), "value": choice} for choice in param["value-choices"]]
        return {"type": "dropdown", "options": options, "multiselect": False}
    biaflows_type = param.get("type", "String")
    default = param.get("default-value")
    if biaflows_type == "Number":
        return {"type": "float" if isinstance(default, float) or "." in str(default) else "integer"}
    if biaflows_type == "Boolean":
        return {"type": "checkbox", "append_value": True}
    return {"type": "textbox"}


def _image_io(name: str, label: str, cli_order: int, formats: Sequence[str], folder: str, subtype: Sequence[str]) -> dict:
    return {
        "name": name, "type": "image", "label": label, "description": label, "cli_tag": f"--{name}",
        "cli_order": cli_order, "default": "directory", "optional": False, "format": list(formats),
        "unique_string": ["*"], "folder_name": folder, "file_count": "multiple", "section_id": name, "mode": "beginner",
        "subtype": list(subtype), "depth": False, "timepoints": False, "tiled": False, "pyramidal": False,
    }


def bilayers_from_descriptor(descriptor: Mapping[str, Any], *, cli_command: str = "python /app/biaflows/wrapper.py",
                             input_formats: Sequence[str] = ("tiff",), output_formats: Sequence[str] = ("tiff",),
                             output_subtype: Sequence[str] = ("label",)) -> dict[str, Any]:
    """Draft bilayers config for a BIAFLOWS workflow; review the image flags, citations and docker tag."""
    image = descriptor.get("container-image", {}).get("image", "org/name")
    org, _, name = image.rpartition("/")
    name, _, tag = name.partition(":")
    parameters = []
    for index, param in enumerate(p for p in descriptor.get("inputs", []) if not p["id"].startswith(SERVER_ID_PREFIX)):
        default = param.get("default-value")
        parameters.append({
            "name": param["id"], **_bilayers_type(param),
            "label": param.get("name") or param["id"], "description": param.get("description") or param.get("name", ""),
            "default": default if default is not None else "",
            "cli_tag": param.get("command-line-flag", "--@id").replace("@id", param["id"]),
            "cli_order": 10 + index, "optional": bool(param.get("optional", False)),
            "section_id": "parameters", "mode": "beginner",
        })
    return {
        "citations": [{"name": descriptor.get("name", name), "doi": "", "license": "",
                       "description": descriptor.get("description", "")}],
        "docker_image": {"org": org or "org", "name": name, "tag": tag or "latest", "platform": "linux/amd64"},
        "algorithm_folder_name": descriptor.get("name", name),
        "exec_function": {"name": "generate_cli_command", "cli_command": cli_command, "hidden_args": None},
        "inputs": [_image_io("infolder", "Input images", 1, input_formats, "/data/in", ["grayscale", "color"])],
        "outputs": [_image_io("outfolder", "Output images", 2, output_formats, "/data/out", output_subtype)],
        "parameters": parameters,
        "display_only": None,
    }


def descriptor_from_bilayers(spec: BilayersSpec, *, image: str | None = None) -> dict[str, Any]:
    """BIAFLOWS descriptor (cytomine-0.1) exposing the spec's parameters as ``--<name>``."""
    docker = spec.parsed["docker_image"]
    citation = next(iter(spec.parsed["citations"].values()), {})
    inputs = []
    for name, item in spec.parameters.items():
        if item.get("output_dir_set"):
            continue  # set from --outfolder
        if item.get("type") in BOOLEAN_TYPES:
            biaflows_type = "Boolean"
        elif item.get("type") in ("integer", "float"):
            biaflows_type = "Number"
        else:
            biaflows_type = "String"
        param = {
            "id": name, "value-key": "@ID", "command-line-flag": "--@id", "name": item.get("label", name),
            "description": item.get("description", ""), "default-value": item.get("default"),
            "set-by-server": False, "optional": bool(item.get("optional", False)), "type": biaflows_type,
        }
        if item.get("options"):
            param["value-choices"] = [option.get("value") for option in item["options"]]
        inputs.append(param)
    return {
        "name": citation.get("name") or spec.parsed.get("algorithm_folder_name"),
        "description": citation.get("description", ""),
        "container-image": {"image": image or f"{docker['org']}/{docker['name']}", "type": "singularity"},
        "command-line": "python wrapper.py " + " ".join(param["id"].upper() for param in inputs),
        "inputs": inputs,
        "schema-version": "cytomine-0.1",
    }
