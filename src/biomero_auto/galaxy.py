"""Drafting bilayers configs from Galaxy tools and workflows (https://galaxyproject.org/).

A Galaxy workflow (``.ga``) is a chain of Tool Shed tools; each tool is an XML wrapper whose
Cheetah ``<command>`` (and ``<configfiles>``) call a script or program installed from its
``<requirements>`` (conda packages). The container runs those scripts/programs directly,
without Galaxy, in a ``tool(ctx)`` function in ``wrapper.py``.

* :func:`bilayers_from_tool` drafts a config from a tool wrapper: its ``<param>``s (sections
  and conditionals flattened to ``<name>__<param>``), data inputs and outputs.
* :func:`bilayers_from_workflow` drafts one from a workflow: its ``parameter_input`` steps and
  the values each tool step fixes (``tool_state``), as ``<step>__<param>``; prune these to
  what a user should tune.
* :func:`workflow_steps` lists the tools (Tool Shed repository, version) to port.
"""
from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable, Mapping

SEPARATOR = "__"
TOOLSHED = "https://toolshed.g2.bx.psu.edu/repos"
IMAGE_FORMATS = {"tiff": "tiff", "tif": "tiff", "zarr": "zarr", "ome.tiff": "ometiff", "png": "png", "jpg": "jpg"}
STATE_SKIP = {"chromInfo", "__page__", "__rerun_remap_job_id__", "__input_ext", "__current_case__", "__job_resource"}


def _identifier(text: str) -> str:
    """A parameter-safe name; it starts with a letter, as BIOMERO also uses names as shell variables."""
    name = re.sub(r"\W+", "_", text).strip("_").lower() or "step"
    return f"s{name}" if name[0].isdigit() else name


def _parameter(name: str, kind: dict, label: str, description: str, default: Any, order: int,
               optional: bool = True) -> dict:
    return {"name": name, **kind, "label": label or name, "description": description or label or name,
            "default": default, "cli_tag": f"--{name}", "cli_order": order, "optional": optional,
            "section_id": "parameters", "mode": "beginner"}


def _image_io(name: str, label: str, order: int, formats: Iterable[str], folder: str) -> dict:
    return {"name": name, "type": "image", "label": label, "description": label, "cli_tag": f"--{name}",
            "cli_order": order, "default": "directory", "optional": False, "format": sorted(set(formats)) or ["tiff"],
            "unique_string": ["*"], "folder_name": folder, "file_count": "multiple", "section_id": name,
            "mode": "beginner", "subtype": ["grayscale"], "depth": False, "timepoints": False, "tiled": False,
            "pyramidal": False}


def _config(name: str, description: str, license_: str, inputs: list, outputs: list, parameters: list,
            cli_command: str) -> dict:
    return {
        "citations": [{"name": name, "doi": "", "license": license_, "description": description}],
        "docker_image": {"org": "org", "name": _identifier(name).replace("_", "-"), "tag": "v0.0.1",
                         "platform": "linux/amd64"},
        "algorithm_folder_name": _identifier(name),
        "exec_function": {"name": "generate_cli_command", "cli_command": cli_command, "hidden_args": None},
        "inputs": inputs, "outputs": outputs, "parameters": parameters, "display_only": None,
    }


# --- tool wrappers ---

def _tokens(root: ET.Element) -> dict[str, str]:
    """The ``<token name="@X@">`` definitions in ``<macros>`` (not ``<token>`` elements of tests)."""
    macros = root.find("macros")
    if macros is None:
        return {}
    return {token.get("name"): token.text or "" for token in macros.findall("token")
            if (token.get("name") or "").startswith("@")}


def _formats(text: str | None) -> list[str]:
    return [IMAGE_FORMATS[entry] for entry in (text or "").replace(" ", "").split(",") if entry in IMAGE_FORMATS]


def _tool_param(param: ET.Element, name: str, order: int, when: str = "") -> dict | None:
    kind_name = param.get("type")
    label = param.get("label") or param.get("name")
    description = (param.get("help") or label or "") + (f" (used when {when})" if when else "")
    optional = param.get("optional") == "true"
    if kind_name == "integer":
        return _parameter(name, {"type": "integer"}, label, description, int(param.get("value") or 0), order, optional)
    if kind_name == "float":
        value = param.get("value")
        return _parameter(name, {"type": "float"}, label, description, float(value) if value else "", order, optional)
    if kind_name == "boolean":
        checked = (param.get("checked") or "false").lower() in ("true", "yes")
        return _parameter(name, {"type": "checkbox", "append_value": True}, label, description, checked, order)
    if kind_name == "select":
        options = [{"label": (option.text or option.get("value")).strip(), "value": option.get("value")}
                   for option in param.findall("option")]
        selected = next((option.get("value") for option in param.findall("option")
                         if (option.get("selected") or "").lower() == "true"), options[0]["value"] if options else "")
        return _parameter(name, {"type": "dropdown", "options": options, "multiselect": param.get("multiple") == "true"},
                          label, description, selected, order, optional)
    if kind_name in ("text", "color"):
        return _parameter(name, {"type": "textbox"}, label, description, param.get("value") or "", order, optional)
    return None  # data (an input), hidden, data_column, ...: not a user parameter


def _tool_inputs(element: Iterable[ET.Element], prefix: str, parameters: list, data: list, when: str = "") -> None:
    seen = {parameter["name"] for parameter in parameters}
    for child in element:
        if child.tag == "param":
            if child.get("type") == "data":
                data.append(child)
                continue
            name = prefix + child.get("name")
            if name in seen:  # the same parameter in several conditional cases
                continue
            parameter = _tool_param(child, name, 10 + len(parameters), when)
            if parameter:
                parameters.append(parameter)
                seen.add(name)
        elif child.tag == "section":
            _tool_inputs(child, f"{prefix}{child.get('name')}{SEPARATOR}", parameters, data, when)
        elif child.tag == "conditional":
            nested = f"{prefix}{child.get('name')}{SEPARATOR}"
            selector = child.find("param")
            if selector is not None:
                _tool_inputs(child.findall("param"), nested, parameters, data, when)
                for case in child.findall("when"):
                    _tool_inputs(case, nested, parameters, data, f"{selector.get('name')} = {case.get('value')}")
        # repeat/expand: not representable as fixed parameters; reported by unexpanded_macros/repeats


def _macro_body(macro: ET.Element, expand: ET.Element) -> list[ET.Element]:
    """The macro's children for one ``<expand>``: its ``@TOKEN@``s replaced by the expand's attributes
    (or the macro's ``token_<name>`` defaults), and ``<yield/>`` by the expand's own children."""
    values = {}
    for token in filter(None, (name.strip() for name in (macro.get("tokens") or "").split(","))):
        values[f"@{token.upper()}@"] = expand.get(token, macro.get(f"token_{token}", ""))
    holder = ET.Element("holder")
    for child in macro:
        text = ET.tostring(child, encoding="unicode")
        for token, value in values.items():
            text = text.replace(token, value)
        holder.append(ET.fromstring(text))
    for parent in list(holder.iter()):
        for index, item in reversed(list(enumerate(parent))):
            if item.tag == "yield":
                parent.remove(item)
                for offset, given in enumerate(expand):
                    parent.insert(index + offset, copy.deepcopy(given))
    return list(holder)


def _expand(element: ET.Element, macros: Mapping[str, ET.Element], depth: int = 0) -> None:
    """Replace ``<expand macro="x"/>`` by the body of ``<xml name="x">`` defined in the same file
    (with its tokens and ``<yield/>``), recursively; macros from imported files stay unexpanded."""
    for index, child in reversed(list(enumerate(element))):
        if child.tag == "expand" and child.get("macro") in macros and depth < 20:
            holder = ET.Element("holder")
            holder.extend(_macro_body(macros[child.get("macro")], child))
            _expand(holder, macros, depth + 1)  # macros used inside the macro
            element.remove(child)
            for offset, replacement in enumerate(list(holder)):
                element.insert(index + offset, replacement)
        else:
            _expand(child, macros, depth)


def _parse_tool(xml: str) -> ET.Element:
    for token, value in _tokens(ET.fromstring(xml)).items():
        xml = xml.replace(token, value)
    root = ET.fromstring(xml)
    macros = root.find("macros")
    if macros is not None:
        _expand(root, {macro.get("name"): macro for macro in macros.findall("xml")})
    return root


def bilayers_from_tool(xml: str, *, cli_command: str | None = None) -> dict[str, Any]:
    """Draft bilayers config for a Galaxy tool wrapper (macros from imported files are not expanded)."""
    root = _parse_tool(xml)
    parameters, data = [], []
    inputs_element = root.find("inputs")
    if inputs_element is not None:
        _tool_inputs(inputs_element, "", parameters, data)
    inputs = [_image_io(f"input_{index}" if index else "input_folder", param.get("label") or "Input images",
                        1 + index, _formats(param.get("format")), "/data/in") for index, param in enumerate(data[:1])]
    outputs = [_image_io("output_folder", "Output images", 2,
                         [f for out in root.iter("data") for f in _formats(out.get("format"))], "/data/out")]
    command = (root.findtext("command") or "").strip()
    script = re.search(r"\$__tool_directory__/([\w./-]+)", command)
    default_command = f"python /app/galaxy/{script.group(1)}" if script else command.split("\n")[0]
    return _config(root.get("name") or root.get("id"), (root.findtext("description") or "").strip(), "", inputs,
                   outputs, parameters, cli_command or default_command)


def unexpanded(xml: str) -> list[str]:
    """Macros and repeats in a tool wrapper the draft does not cover."""
    inputs = _parse_tool(xml).find("inputs")
    if inputs is None:
        return []
    return list(dict.fromkeys([f"macro {expand.get('macro')} (from an imported macro file)" for expand in inputs.iter("expand")]
                              + [f"repeat {repeat.get('name')}" for repeat in inputs.iter("repeat")]))


# --- workflows ---

def workflow_steps(workflow: Mapping) -> list[dict]:
    """The workflow's steps in order: label, type, tool id/version and Tool Shed repository URL."""
    steps = []
    for key, step in sorted(workflow.get("steps", {}).items(), key=lambda item: int(item[0])):
        repo = step.get("tool_shed_repository") or {}
        steps.append({
            "id": int(key), "type": step.get("type"), "name": step_name(step), "tool_id": step.get("tool_id"),
            "tool_version": step.get("tool_version"),
            "repository": f"{TOOLSHED}/{repo['owner']}/{repo['name']}" if repo else None,
            "changeset": repo.get("changeset_revision"),
            "inputs_from": sorted({link["id"] for links in (step.get("input_connections") or {}).values()
                                   for link in (links if isinstance(links, list) else [links])}),
        })
    return steps


def step_name(step: Mapping) -> str:
    repo = (step.get("tool_shed_repository") or {}).get("name")
    return _identifier(step.get("label") or repo or (step.get("tool_id") or "").split("/")[-1] or str(step.get("id")))


def _state_values(state: Mapping, prefix: str = "") -> Iterable[tuple[str, Any]]:
    for key, value in state.items():
        if key in STATE_SKIP or key.startswith("__"):
            continue
        if isinstance(value, dict):
            if "__class__" in value:  # ConnectedValue / RuntimeValue: set by the workflow, not fixed
                continue
            yield from _state_values(value, f"{prefix}{key}{SEPARATOR}")
        elif not isinstance(value, list) and value is not None:
            yield f"{prefix}{key}", value


def _value_parameter(name: str, value: Any, label: str, order: int) -> dict:
    text = str(value)
    if isinstance(value, bool) or text.lower() in ("true", "false"):
        return _parameter(name, {"type": "checkbox", "append_value": True}, label, label,
                          text.lower() == "true", order)
    for kind, cast in (("integer", int), ("float", float)):
        try:
            return _parameter(name, {"type": kind}, label, label, cast(text), order)
        except ValueError:
            pass
    return _parameter(name, {"type": "textbox"}, label, label, text, order)


def tool_xml_id(step: Mapping) -> str | None:
    """The tool's XML ``id`` from a step's Tool Shed tool id (``.../repos/<owner>/<repo>/<id>/<version>``)."""
    parts = (step.get("tool_id") or "").split("/")
    return parts[-2] if len(parts) >= 2 else step.get("tool_id")


def _tool_parameters(xml: str) -> dict[str, dict]:
    return {parameter["name"]: parameter for parameter in bilayers_from_tool(xml)["parameters"]}


def _from_tool(parameter: dict, name: str, value: Any, label: str, order: int) -> dict:
    """The tool wrapper's parameter (type, options, help), with the workflow's value as default."""
    parameter = {**parameter, "name": name, "cli_tag": f"--{name}", "cli_order": order,
                 "label": f"{label} ({parameter['label']})"}
    text = str(value)
    try:
        if parameter["type"] == "checkbox":
            parameter["default"] = text.lower() == "true"
        elif parameter["type"] == "integer":
            parameter["default"] = int(text)
        elif parameter["type"] == "float":
            parameter["default"] = float(text) if text else ""
        else:
            parameter["default"] = text
    except ValueError:
        parameter["default"] = text
    return parameter


def stale_state(workflow: Mapping, tools: Mapping[str, str]) -> list[str]:
    """``tool_state`` values of tool steps that the tool wrapper (XML, by tool id) does not have:
    left over from an older tool version, they have no effect on the pinned version."""
    stale = []
    for step in workflow_steps(workflow):
        xml = tools.get(tool_xml_id(workflow["steps"][str(step["id"])]) or "")
        if step["type"] != "tool" or xml is None:
            continue
        known = _tool_parameters(xml)
        state = json.loads(workflow["steps"][str(step["id"])].get("tool_state") or "{}")
        stale += [f"step {step['id']} {step['name']}: {path}" for path, _ in _state_values(state) if path not in known]
    return stale


def bilayers_from_workflow(workflow: Mapping, *, cli_command: str = "python /app/galaxy/workflow.py",
                           input_formats: Iterable[str] = ("tiff",), output_formats: Iterable[str] = ("tiff",),
                           step_params: bool = True, tools: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Draft bilayers config for a Galaxy workflow (``.ga``).

    ``tools`` maps tool ids (the XML ``id``, e.g. ``ip_threshold``) to the wrapper XML at the version
    the workflow pins: the steps' values then get the wrapper's types, options and help, and values
    the wrapper does not have (see ``stale_state``) are left out. Without it, types are guessed.
    """
    tools = tools or {}
    parameters, data_inputs = [], []
    for step in workflow_steps(workflow):
        raw = workflow["steps"][str(step["id"])]
        if step["type"] == "data_input":
            data_inputs.append(raw)
        elif step["type"] == "parameter_input":
            state = json.loads(raw.get("tool_state") or "{}")
            kind = state.get("parameter_type", "text")
            value = state.get("default", "")
            parameter = _value_parameter(step["name"], value, raw.get("label") or step["name"], 10 + len(parameters))
            if kind == "text":
                parameter.update(type="textbox", default=str(value))
            parameters.append(parameter)
        elif step["type"] == "tool" and step_params:
            state = json.loads(raw.get("tool_state") or "{}")
            xml = tools.get(tool_xml_id(raw) or "")
            known = _tool_parameters(xml) if xml is not None else None
            for path, value in _state_values(state):
                name = f"{step['name']}{SEPARATOR}{path}"
                label = f"{step['name']}: {path.replace(SEPARATOR, ' / ')}"
                if known is None:
                    parameters.append(_value_parameter(name, value, label, 10 + len(parameters)))
                elif path in known:
                    parameters.append(_from_tool(known[path], name, value, label, 10 + len(parameters)))
    inputs = [_image_io("input_folder", (data_inputs[0].get("label") if data_inputs else None) or "Input images", 1,
                        input_formats, "/data/in")]
    outputs = [_image_io("output_folder", "Output images", 2, output_formats, "/data/out")]
    creators = ", ".join(creator.get("name", "") for creator in workflow.get("creator", []) or [])
    description = (workflow.get("annotation") or "").strip() + (f" Creators: {creators}." if creators else "")
    return _config(workflow.get("name", "Galaxy workflow"), description.strip(), workflow.get("license") or "",
                   inputs, outputs, parameters, cli_command)
