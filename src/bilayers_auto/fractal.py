"""Fractal tasks (https://fractal-analytics-platform.github.io/) in a bilayers container.

* :func:`fractal_tool` runs a Fractal task the way Fractal does: ``<command> --args-json
  <file> --out-json <file>``, with ``zarr_url`` (parallel tasks), ``zarr_urls`` + ``zarr_dir``
  (non-parallel tasks) or both, init then compute (compound tasks).
* :func:`bilayers_from_manifest` drafts a bilayers config from a task in a package's
  ``__FRACTAL_MANIFEST__.json`` (its pydantic JSON schemas).

Fractal tasks change the OME-Zarr images they are given (they add images, labels and tables
to them), so each input image is first copied into the output folder and the task runs on
the copies. Images the task reports as new (``image_list_updates``) are the outputs; the
copies are then removed, as the input would otherwise be imported again. When the task
reports no new images, it changed the copies in place and they are the output.

Nested argument objects are flattened into parameters named ``<argument>__<field>``
(e.g. ``channel__label``); empty values are left out, so the task's defaults apply.

What the exported images lack and Fractal tasks often need (see ``fractal_tool``): the channels'
``wavelength_id`` (added), ROI tables such as ``FOV_ROI_table`` (a ``prepare`` step), and the
HCS plate/well structure (images come one by one, so plate-level tasks do not apply).
New images get a name ending in ``.zarr`` (``a.ome.zarr_fused`` -> ``a_fused.ome.zarr``).
"""
from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .conversion import find_images, image_format, image_stem

logger = logging.getLogger(__name__)

SEPARATOR = "__"
KINDS = ("parallel", "non_parallel", "compound")
# set by Fractal itself, never by the user
RESERVED = {"zarr_url", "zarr_urls", "zarr_dir", "init_args"}


def task_args(params: Mapping[str, Any], names: Iterable[str] | None = None) -> dict:
    """Fractal task arguments from flat parameter values: nested ``a__b`` -> ``{"a": {"b": ...}}``."""
    args: dict = {}
    for name, value in params.items():
        if names is not None and name not in names:
            continue
        if value is None or value == "":
            continue
        if isinstance(value, str) and value.strip()[:1] in "[{":
            try:
                value = json.loads(value)  # list/object arguments given as JSON text
            except json.JSONDecodeError:
                pass
        target = args
        *parents, leaf = name.split(SEPARATOR)
        for parent in parents:
            target = target.setdefault(parent, {})
        target[leaf] = value
    return args


def _call(command: Sequence[str], args: dict, workdir: Path, name: str) -> dict:
    args_json, out_json = workdir / f"{name}.args.json", workdir / f"{name}.out.json"
    args_json.write_text(json.dumps(args, indent=2))
    argv = [*command, "--args-json", str(args_json), "--out-json", str(out_json)]
    logger.info("Running Fractal task: %s\n%s", shlex.join(argv), json.dumps(args, indent=2))
    subprocess.run(argv, check=True)
    # no file, an empty one or ``null``: the task reported nothing
    return (json.loads(out_json.read_text()) if out_json.exists() and out_json.read_text().strip() else None) or {}


def _split(command: str | Sequence[str]) -> list[str]:
    return shlex.split(command) if isinstance(command, str) else list(command)


def add_wavelength_ids(image: Path) -> None:
    """Give each ``omero`` channel the ``wavelength_id`` Fractal expects (fractal-tasks-core's
    OmeroChannel requires it; exported images do not have it): ``C01``, ``C02``, ... when missing."""
    attrs_path = image / ".zattrs"
    if not attrs_path.exists():  # OME-Zarr 0.5 (zarr.json) is not used by Fractal tasks
        return
    attrs = json.loads(attrs_path.read_text())
    channels = attrs.get("omero", {}).get("channels", [])
    if channels and any("wavelength_id" not in channel for channel in channels):
        for index, channel in enumerate(channels):
            channel.setdefault("wavelength_id", f"C{index + 1:02d}")
        attrs_path.write_text(json.dumps(attrs, indent=2))


def _image_name(path: Path, copies: Sequence[Path]) -> Path:
    """A name ending in ``.zarr`` for a new image (only those are imported): Fractal tasks
    often append a suffix to the input name (``a.ome.zarr`` -> ``a.ome.zarr_fused``)."""
    if image_format(path):
        return path
    for copy in copies:
        if path.name.startswith(copy.name):
            suffix = copy.name[len(image_stem(copy)):]
            return path.with_name(image_stem(copy) + path.name[len(copy.name):] + suffix)
    return path.with_name(path.name + ".zarr")


def _matches(types: Mapping[str, bool], filters: Mapping[str, bool]) -> bool:
    """Fractal's type filter: every filtered type must have that value (a missing type is false)."""
    return all(types.get(key, False) == value for key, value in filters.items())


def image_types(image: Path) -> dict[str, bool]:
    """The types Fractal's converters give an image, from its metadata: ``is_3D`` (Z > 1)."""
    attrs_path = image / ".zattrs"
    if not attrs_path.exists():
        return {}
    multiscales = json.loads(attrs_path.read_text()).get("multiscales") or [{}]
    axes = [axis["name"] if isinstance(axis, dict) else axis for axis in multiscales[0].get("axes", [])]
    datasets = multiscales[0].get("datasets") or [{}]
    zarray = image / datasets[0].get("path", "0") / ".zarray"
    if "z" not in axes or not zarray.exists():
        return {"is_3D": False}
    return {"is_3D": json.loads(zarray.read_text())["shape"][axes.index("z")] > 1}


def _merge(base: Mapping, override: Mapping) -> dict:
    merged = dict(base)
    for key, value in override.items():
        nested = isinstance(value, dict) and isinstance(merged.get(key), dict)
        merged[key] = _merge(merged[key], value) if nested else value
    return merged


def _command(step: Mapping, key: str, task_python: str) -> list[str]:
    command = step.get(key)
    if not command:
        raise ValueError(f"step {step['id']!r} has no {key}")
    return [task_python if part == "{python}" else part for part in _split(command)]


def run_step(step: Mapping, images: list[dict], out: Path, calls: Path, params: Mapping[str, Any],
             task_python: str = "python") -> None:
    """Run one Fractal task (a workflow step) on the images its type filters select, updating ``images``
    as Fractal updates its image list: new images are added, types set from output types and updates.

    ``params`` (flat, ``a__b`` nested) override the step's arguments; names in the step's
    ``args_non_parallel`` (or ``non_parallel_params``) go to the init/non-parallel call."""
    name, kind = step["id"] or "task", step["kind"]
    filters = {**(step.get("input_types") or {}), **(step.get("type_filters") or {})}
    selected = [image for image in images if _matches(image["types"], filters)]
    if not selected:
        logger.warning("Step %s: no image matches %s; skipped", name, filters)
        return
    fixed_np, fixed_p = step.get("args_non_parallel") or {}, step.get("args_parallel") or {}
    np_names = set(fixed_np) | set(step.get("non_parallel_params", ()))
    overrides = task_args(params)
    args_np = _merge(fixed_np, {k: v for k, v in overrides.items() if k in np_names})
    args_p = _merge(fixed_p, {k: v for k, v in overrides.items() if k not in np_names})
    urls = [image["zarr_url"] for image in selected]
    updates, in_place = [], set()

    def collect(result: Mapping, sources: Iterable[str]) -> None:
        # Fractal: a call reporting no images changed its input image(s) in place
        reported = result.get("image_list_updates") or []
        updates.extend(reported)
        if not reported:
            in_place.update(sources)

    if kind == "parallel":
        for index, url in enumerate(urls):
            collect(_call(_command(step, "command_parallel", task_python), {**args_p, "zarr_url": url},
                          calls, f"{name}-{index}"), [url])
    elif kind == "non_parallel":
        collect(_call(_command(step, "command_non_parallel", task_python),
                      {**args_np, "zarr_urls": urls, "zarr_dir": out.as_posix()}, calls, name), urls)
    elif kind == "converter_non_parallel" and step.get("imports_ome_zarr"):
        for index, url in enumerate(urls):  # import an existing OME-Zarr (adds tables/metadata), image by image
            result = _call(_command(step, "command_non_parallel", task_python),
                           {**args_np, "zarr_dir": Path(url).parent.as_posix(), "zarr_name": Path(url).name,
                            **({"zarr_urls": []} if "zarr_urls" in step.get("non_parallel_schema_args", ()) else {})},
                           calls, f"{name}-{index}")
            own = [u for u in result.get("image_list_updates") or [] if Path(u["zarr_url"]).name == Path(url).name]
            collect({"image_list_updates": own}, [url])
    elif kind == "compound":
        init = _call(_command(step, "command_non_parallel", task_python),
                     {**args_np, "zarr_urls": urls, "zarr_dir": out.as_posix()}, calls, f"{name}-init")
        for index, item in enumerate(init.get("parallelization_list") or []):
            collect(_call(_command(step, "command_parallel", task_python), {**args_p, **item},
                          calls, f"{name}-{index}"), [item.get("zarr_url")])
    else:
        raise ValueError(f"step {name}: a {kind} task cannot run on the Slurm client's images")
    by_url = {image["zarr_url"]: image for image in images}
    output_types = step.get("output_types") or {}
    for update in updates:
        url = Path(update["zarr_url"]).resolve().as_posix()
        image = by_url.get(url)
        if image is None:
            origin = by_url.get(Path(update.get("origin") or url).resolve().as_posix(), {})
            image = {"zarr_url": url, "types": dict(origin.get("types", {})), "origin": origin.get("zarr_url")}
            images.append(image)
            by_url[url] = image
        image["types"].update({**output_types, **(update.get("types") or {})})
    for url in in_place:
        if url in by_url:
            by_url[url]["types"].update(output_types)


def fractal_workflow(steps: Sequence[Mapping] | str | Path, *, task_python: str = "/opt/fractal/bin/python",
                     keep_inputs: bool | None = None, prepare: Callable | None = None,
                     wavelength_ids: bool = True, exclude: Iterable[str] = ()) -> Callable:
    """Tool function running Fractal tasks one after another, as a Fractal workflow does.

    ``steps`` (or a JSON file of them, as ``from-fractal`` writes for a workflow) are mappings with
    ``id``, ``kind`` (the task type), ``command_parallel``/``command_non_parallel`` (``{python}``
    stands for ``task_python``), the workflow's ``args_non_parallel``/``args_parallel`` and
    ``type_filters``, and the task's ``input_types``/``output_types``. Parameters named
    ``<step id>__<argument>`` override that step's arguments; a step with an empty id takes all
    parameters (a single task).

    Each input image is copied into the output folder (tasks change the images they get), with the
    types Fractal's converters would give it (``is_3D``). Images the steps report as new are the
    output: the copies are then removed (``keep_inputs``) and new images get names ending in
    ``.zarr``. ``prepare(ctx, images)`` runs on the copies first and may set ``ctx.params``.
    """
    if isinstance(steps, (str, Path)):
        steps = json.loads(Path(steps).read_text())
    steps = list(steps)
    exclude = set(exclude)

    def run_fractal(ctx) -> None:
        out = Path(next(iter(ctx.outputs.values()))).resolve()
        out.mkdir(parents=True, exist_ok=True)
        copies = []
        for folder in ctx.inputs.values():
            for image in find_images(Path(folder)):
                copy = out / image.name
                shutil.copytree(image.resolve(), copy)
                if wavelength_ids:
                    add_wavelength_ids(copy)
                copies.append(copy)
        if not copies:
            raise RuntimeError("no OME-Zarr images in the input folder")
        if prepare is not None:
            prepare(ctx, copies)
        skip = set(ctx.spec.output_dir_params()) | exclude
        params = {name: value for name, value in ctx.params.items() if name not in skip}
        images = [{"zarr_url": copy.resolve().as_posix(), "types": image_types(copy), "origin": None}
                  for copy in copies]
        calls = ctx.workdir / "fractal"
        calls.mkdir(parents=True, exist_ok=True)
        for step in steps:
            prefix = f"{step['id']}{SEPARATOR}" if step["id"] else ""
            own = {name[len(prefix):]: value for name, value in params.items() if name.startswith(prefix)}
            run_step(step, images, out, calls, own, task_python)
        inputs = {copy.resolve().as_posix() for copy in copies}
        new = [Path(image["zarr_url"]) for image in images if image["zarr_url"] not in inputs]
        logger.info("Images made by the workflow: %s", [path.name for path in new] or "none (changed in place)")
        for path in new:
            renamed = _image_name(path, copies)
            if renamed != path and path.exists():
                logger.info("Renaming %s -> %s", path.name, renamed.name)
                path.rename(renamed)
        if keep_inputs is False or (keep_inputs is None and new):
            for copy in copies:
                if copy.exists():
                    shutil.rmtree(copy)
        # fractal-tasks-core locks the "well" (here: the output folder) while updating its metadata
        for lock in out.glob("*.lock"):
            lock.unlink()
    return run_fractal


def fractal_tool(command: str | Sequence[str], *, kind: str = "parallel", init_command: str | Sequence[str] | None = None,
                 init_params: Iterable[str] = (), exclude: Iterable[str] = (), keep_inputs: bool | None = None,
                 prepare: Callable | None = None, wavelength_ids: bool = True) -> Callable:
    """Tool function running one Fractal task, e.g.
    ``fractal_tool("/opt/fractal/bin/python -m fractal_ome_zarr_hcs_stitching.stitching_task")``.

    ``kind`` is the manifest's task type; a compound task also needs ``init_command`` (its
    ``executable_non_parallel``) and the names of the parameters that belong to the init
    task (``init_params``; the others go to the compute task). Parameters in ``exclude`` are
    not passed to the task (e.g. ones only ``prepare`` uses). See ``fractal_workflow`` for the
    rest: copies, ``prepare``, ``wavelength_ids``, ``keep_inputs``.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if kind == "compound" and init_command is None:
        raise ValueError("a compound task needs init_command")
    step = {"id": "", "kind": kind, "non_parallel_params": sorted(init_params),
            "command_parallel" if kind != "non_parallel" else "command_non_parallel": _split(command)}
    if kind == "compound":
        step["command_non_parallel"] = _split(init_command)
    return fractal_workflow([step], keep_inputs=keep_inputs, prepare=prepare, wavelength_ids=wavelength_ids,
                            exclude=exclude)


# --- drafting a bilayers config from a Fractal manifest ---

def _resolve(schema: Mapping, node: Mapping) -> Mapping:
    """Follow $ref / single-entry allOf, and drop ``null`` from anyOf (optional arguments)."""
    while True:
        if "$ref" in node:
            ref = node["$ref"].split("/")[-1]
            node = {**schema.get("$defs", schema.get("definitions", {}))[ref],
                    **{key: value for key, value in node.items() if key != "$ref"}}
        elif len(node.get("allOf", [])) == 1:
            node = {**node["allOf"][0], **{key: value for key, value in node.items() if key != "allOf"}}
        elif "anyOf" in node and len([o for o in node["anyOf"] if o.get("type") != "null"]) <= 1:
            options = [option for option in node["anyOf"] if option.get("type") != "null"]
            node = {**options[0], **{key: value for key, value in node.items() if key != "anyOf"}} if options else {}
        else:
            return node


def _parameter(name: str, node: Mapping, required: bool, order: int) -> dict:
    default = node.get("default")
    if "enum" in node:
        kind = {"type": "dropdown", "multiselect": False,
                "options": [{"label": str(value), "value": value} for value in node["enum"]]}
    elif node.get("type") == "boolean":
        kind = {"type": "checkbox", "append_value": True}
    elif node.get("type") == "integer":
        kind = {"type": "integer"}
    elif node.get("type") == "number":
        kind = {"type": "float"}
    else:
        kind = {"type": "textbox"}
        if node.get("type") in ("array", "object") and default is not None:
            default = json.dumps(default)
    description = node.get("description") or node.get("title") or name
    if node.get("type") in ("array", "object"):
        description += " (JSON)"
    return {
        "name": name, **kind, "label": node.get("title") or name, "description": description,
        "default": default if default is not None else ("" if kind["type"] != "checkbox" else False),
        "cli_tag": f"--{name}", "cli_order": order, "optional": not required or default is not None,
        "section_id": "parameters", "mode": "beginner",
    }


def _parameters(schema: Mapping, node: Mapping | None = None, prefix: str = "", required: bool = True,
                start: int = 10) -> list[dict]:
    """Parameters for an args schema; nested objects become ``<argument>__<field>`` parameters."""
    node = _resolve(schema, schema if node is None else node)
    parameters = []
    for name, child in node.get("properties", {}).items():
        if not prefix and name in RESERVED:
            continue
        child = _resolve(schema, child)
        is_required = required and name in node.get("required", [])
        if child.get("type") == "object" and child.get("properties"):
            parameters += _parameters(schema, child, f"{prefix}{name}{SEPARATOR}", is_required, start + len(parameters))
        else:
            parameters.append(_parameter(f"{prefix}{name}", child, is_required, start + len(parameters)))
    return parameters


def find_task(manifest: Mapping, task: str | None) -> Mapping:
    tasks = manifest.get("task_list", [])
    if task is None and len(tasks) == 1:
        return tasks[0]
    for entry in tasks:
        if task in (entry["name"], entry.get("executable_parallel"), entry.get("executable_non_parallel")):
            return entry
    raise ValueError(f"task {task!r} not in the manifest; tasks: {[entry['name'] for entry in tasks]}")


def task_kind(entry: Mapping) -> str:
    """The manifest's task type (parallel, non_parallel, compound, converter_non_parallel, converter_compound)."""
    if entry.get("type"):
        return entry["type"]
    has_init, has_compute = "executable_non_parallel" in entry, "executable_parallel" in entry
    return "compound" if has_init and has_compute else ("non_parallel" if has_init else "parallel")


def bilayers_from_manifest(manifest: Mapping, task: str | None = None, *,
                           cli_command: str = "python -m <package>.<task module>") -> dict[str, Any]:
    """Draft bilayers config for a Fractal task; review the image flags, citations and docker image."""
    entry = find_task(manifest, task)
    kind = task_kind(entry)
    parameters = []
    if kind in ("non_parallel", "compound"):
        parameters += _parameters(entry.get("args_schema_non_parallel", {}))
    if kind in ("parallel", "compound"):
        parameters += _parameters(entry.get("args_schema_parallel", {}), start=10 + len(parameters))
    image = {
        "type": "image", "cli_order": 1, "default": "directory", "optional": False, "format": ["zarr"],
        "unique_string": ["*"], "file_count": "multiple", "mode": "beginner",
        "depth": "3D" in entry.get("tags", []), "timepoints": False, "tiled": False, "pyramidal": True,
    }
    name = entry["name"]
    return {
        "citations": [{"name": name, "doi": "", "license": "", "description": entry.get("docs_info", "").strip()}],
        "docker_image": {"org": "org", "name": name.lower().replace(" ", "-"), "tag": "v0.0.1", "platform": "linux/amd64"},
        "algorithm_folder_name": name.replace(" ", "_"),
        "exec_function": {"name": "generate_cli_command", "cli_command": cli_command, "hidden_args": None},
        "inputs": [{**image, "name": "input_folder", "label": "Input OME-Zarr images", "cli_tag": "--input_folder",
                    "description": "OME-Zarr images the task runs on", "folder_name": "/data/in",
                    "section_id": "input_folder", "subtype": ["grayscale"]}],
        "outputs": [{**image, "name": "output_folder", "label": "Output OME-Zarr images", "cli_tag": "--output_folder",
                     "cli_order": 2, "description": "Images the task wrote", "folder_name": "/data/out",
                     "section_id": "output_folder", "subtype": ["grayscale"]}],
        "parameters": parameters,
        "display_only": None,
    }


def wrapper_hint(manifest: Mapping, task: str | None = None) -> str:
    """The ``run(...)`` line for wrapper.py, as a starting point."""
    entry = find_task(manifest, task)
    kind = task_kind(entry)
    module = lambda executable: executable.removesuffix(".py").replace("/", ".")  # noqa: E731
    if kind == "compound":
        init = [p["name"] for p in _parameters(entry.get("args_schema_non_parallel", {}))]
        return (f'run(spec=CONFIG, tool=fractal_tool("<python> -m <package>.{module(entry["executable_parallel"])}", '
                f'kind="compound", init_command="<python> -m <package>.{module(entry["executable_non_parallel"])}", '
                f"init_params={init!r}))")
    executable = entry.get("executable_parallel") or entry.get("executable_non_parallel")
    return f'run(spec=CONFIG, tool=fractal_tool("<python> -m <package>.{module(executable)}", kind="{kind}"))'


# --- Fractal workflows (exported from fractal-server / fractal-web, or its workflow templates) ---

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def load_workflow(data: Mapping) -> dict:
    """A Fractal workflow as ``{name, description, steps}``, from any of its JSON forms: a workflow
    export (tasks as ``{pkg_name, version, name}``), a legacy export (fractal-server < 2.11: tasks as
    ``{source: "pip_remote:<pkg>:<version>:<extras>::<task>"}``, ``input_filters``), or a workflow
    template (the workflow under ``data``)."""
    workflow = data.get("data", data) if isinstance(data.get("data"), dict) else data
    steps = []
    for index, entry in enumerate(workflow.get("task_list", [])):
        task = entry.get("task") or entry.get("task_legacy") or {}
        if "source" in task and "pkg_name" not in task:
            pkg, version, *_rest = task["source"].split(":")[1:4] + [None]
            task = {"pkg_name": pkg.replace("_", "-"), "version": version, "name": task["source"].split("::")[-1]}
        filters = entry.get("type_filters")
        if filters is None:
            filters = (entry.get("input_filters") or {}).get("types", {})
        steps.append({
            "index": index, "pkg_name": task.get("pkg_name"), "version": task.get("version"),
            "task_name": task.get("name"), "alias": entry.get("alias"), "description": entry.get("description"),
            "args_non_parallel": entry.get("args_non_parallel") or {}, "args_parallel": entry.get("args_parallel") or {},
            "type_filters": filters or {}, "meta_non_parallel": entry.get("meta_non_parallel") or {},
            "meta_parallel": entry.get("meta_parallel") or {},
        })
    return {"name": workflow.get("name", "fractal-workflow"), "description": workflow.get("description") or "",
            "steps": steps}


def is_workflow(data: Mapping) -> bool:
    """A workflow (export or template) rather than a package manifest."""
    workflow = data.get("data", data) if isinstance(data.get("data"), dict) else data
    tasks = workflow.get("task_list") or []
    return bool(tasks) and ("task" in tasks[0] or "task_legacy" in tasks[0])


def fetch_manifest(pkg_name: str, version: str | None, folder: Path, python_version: str = "3.12") -> tuple[dict, str]:
    """The ``__FRACTAL_MANIFEST__.json`` of a task package (from its PyPI wheel, downloaded with pip for
    the task environment's Python, not installed or run) and the package's import name."""
    import sys
    import zipfile

    folder.mkdir(parents=True, exist_ok=True)
    requirement = f"{pkg_name}=={version}" if version else pkg_name
    subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:", "-q",
                    "--python-version", python_version, "-d", str(folder), requirement], check=True)
    wheels = sorted(folder.glob(f"{_norm(pkg_name)}-{version or ''}*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise FileNotFoundError(f"no wheel for {requirement}")
    return read_manifest(wheels[-1])


def read_manifest(source: Path) -> tuple[dict, str | None]:
    """A task package's manifest and import name, from its wheel or the manifest file in its package folder."""
    import zipfile

    source = Path(source)
    if source.suffix == ".whl":
        with zipfile.ZipFile(source) as wheel:
            name = next(n for n in wheel.namelist() if n.endswith("__FRACTAL_MANIFEST__.json"))
            return json.loads(wheel.read(name)), name.split("/")[0]
    return json.loads(source.read_text(encoding="utf-8")), source.parent.name


def _manifest_task(manifest: Mapping, name: str) -> Mapping | None:
    for task in manifest.get("task_list", []):
        if _norm(task["name"]) == _norm(name) or name in (task.get("executable_parallel"),
                                                           task.get("executable_non_parallel")):
            return task
    return None


def _plan_step(step: Mapping, task: Mapping | None, package: str | None, ids: set) -> dict:
    """The step as fractal_workflow runs it, and whether/why it is left out for the Slurm client."""
    base = _identifier(step["alias"] or step["task_name"] or f"step{step['index']}")
    step_id, n = base, 2
    while step_id in ids:
        step_id, n = f"{base}_{n}", n + 1
    ids.add(step_id)
    plan = {"id": step_id, "task": f"{step['pkg_name']}=={step['version']}::{step['task_name']}",
            "kind": task_kind(task) if task else None,
            "args_non_parallel": step["args_non_parallel"], "args_parallel": step["args_parallel"],
            "type_filters": step["type_filters"], "input_types": (task or {}).get("input_types") or {},
            "output_types": (task or {}).get("output_types") or {}, "tags": (task or {}).get("tags") or [],
            "meta_parallel": step["meta_parallel"],
            "meta_non_parallel": step["meta_non_parallel"]}
    for key, executable in (("command_parallel", "executable_parallel"),
                            ("command_non_parallel", "executable_non_parallel")):
        module = _module((task or {}).get(executable), package)
        if module:
            plan[key] = ["{python}", "-m", module]
    schema_np = (task or {}).get("args_schema_non_parallel") or {}
    plan["non_parallel_schema_args"] = list(schema_np.get("properties", {}))
    plan["imports_ome_zarr"] = "zarr_name" in plan["non_parallel_schema_args"]
    name = _norm((task or {}).get("name") or step["task_name"] or "")
    # converter task types exist since fractal-tasks-core 1.3; older manifests have none
    converter = (plan["kind"] or "").startswith("converter") or "to_ome_zarr" in name or plan["imports_ome_zarr"]
    if task is None:
        plan["skip"] = "task not found in its package manifest (give it with --manifest)"
    elif converter and not plan["imports_ome_zarr"]:
        plan["skip"] = "converts raw microscope data; the Slurm client provides OME-Zarr images"
    elif converter:
        plan["kind"] = "converter_non_parallel"
        plan["note"] = "imports the OME-Zarr image (adds ROI tables and metadata the exported images lack)"
    elif "hcs_plate" in name or "multiplex" in name or "reference_acquisition" in {**step["args_non_parallel"],
                                                                                **step["args_parallel"]}:
        plan["note"] = ("works on HCS plates / multiplexing acquisitions; the Slurm client gives single images "
                        "(use an image-level task if the package has one)")
    return plan


def _module(executable: str | None, package: str | None) -> str | None:
    """``python -m`` module of a manifest executable (``tasks/x.py`` in package ``pkg`` -> ``pkg.tasks.x``)."""
    if not executable:
        return None
    module = executable.removesuffix(".py").replace("/", ".")
    return f"{package}.{module}" if package else module


def _identifier(text: str) -> str:
    name = _norm(text) or "step"
    return f"s{name}" if name[0].isdigit() else name


def _schema_node(schema: Mapping, path: Sequence[str]) -> Mapping | None:
    """The JSON schema of a (nested) argument, or None where it cannot be told (e.g. a union)."""
    node: Mapping = schema
    for key in path:
        node = _resolve(schema, node)
        properties = node.get("properties")
        if not properties or key not in properties:
            return None
        node = properties[key]
    node = _resolve(schema, node)
    return None if "anyOf" in node or "oneOf" in node else node


def _value_kind(value: Any) -> dict:
    if isinstance(value, bool):
        return {"type": "checkbox", "append_value": True}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "float"}
    return {"type": "textbox"}


def _flatten(args: Mapping, prefix: tuple = ()) -> Iterable[tuple[tuple, Any]]:
    for key, value in args.items():
        if isinstance(value, dict) and value:
            yield from _flatten(value, (*prefix, key))
        else:
            yield (*prefix, key), value


def _workflow_parameters(plan: Mapping, task: Mapping | None, start: int) -> list[dict]:
    parameters = []
    for args, schema_key in ((plan["args_non_parallel"], "args_schema_non_parallel"),
                             (plan["args_parallel"], "args_schema_parallel")):
        schema = (task or {}).get(schema_key) or {}
        for path, value in _flatten(args):
            if path[0] in RESERVED:
                continue
            node = _schema_node(schema, path) if schema else None
            name = plan["id"] + SEPARATOR + SEPARATOR.join(path)
            if node is not None and node.get("type") not in ("array", "object"):
                parameter = _parameter(name, {**node, "default": value}, False, start + len(parameters))
            else:
                parameter = _parameter(name, {"description": (node or {}).get("description", ""),
                                              "title": (node or {}).get("title"), "default": value},
                                       False, start + len(parameters))
                parameter.update(_value_kind(value) if not isinstance(value, (list, dict)) and value is not None
                                 else {"type": "textbox"})
                if isinstance(value, (list, dict)):
                    parameter["default"] = json.dumps(value)
                    parameter["description"] += " (JSON)"
                elif value is None:
                    parameter["default"] = ""
            title = (node or {}).get("title") or path[-1]
            parameter["label"] = f"{plan['id']}: {' / '.join(path[:-1] + (title,))}"
            parameter["section_id"] = plan["id"]
            parameters.append(parameter)
    return parameters


def bilayers_from_workflow(workflow: Mapping, manifests: Mapping[str, tuple[Mapping, str]]) -> tuple[dict, list[dict]]:
    """Draft bilayers config and the step plan (for ``fractal_workflow``) for a Fractal workflow.

    ``manifests`` maps ``pkg_name==version`` to ``(manifest, package import name)``. Every argument the
    workflow sets becomes a parameter ``<step>__<argument>`` with the workflow's value as default;
    prune them to what users should tune. Converters of raw data are left out of the plan."""
    ids: set = set()
    plan, parameters = [], []
    changed_types: set = set()  # types an earlier step sets, e.g. is_3D by a projection
    for step in workflow["steps"]:
        manifest, package = manifests.get(f"{step['pkg_name']}=={step['version']}", ({}, None))
        task = _manifest_task(manifest, step["task_name"] or "")
        planned = _plan_step(step, task, package, ids)
        unfiltered = changed_types - set(planned["type_filters"]) - set(planned["input_types"])
        if "skip" not in planned and unfiltered:
            planned["note"] = (planned.get("note", "") + "; " if planned.get("note") else "") + (
                f"runs on every image, before and after the earlier change of {sorted(unfiltered)} "
                "(Fractal does the same); add type_filters to run it only on some")
        if "skip" not in planned:
            changed_types |= set(planned["output_types"])
        plan.append(planned)
        if "skip" not in planned:
            parameters += _workflow_parameters(planned, task, 10 + len(parameters))
    kept = [step for step in plan if "skip" not in step]
    depth = any("3D" in step["tags"] for step in kept)
    packages = sorted({step["task"].split("::")[0] for step in kept})
    image = {"type": "image", "cli_order": 1, "default": "directory", "optional": False, "format": ["zarr"],
             "unique_string": ["*"], "file_count": "multiple", "mode": "beginner", "depth": depth,
             "timepoints": False, "tiled": False, "pyramidal": True, "subtype": ["grayscale"]}
    name = workflow["name"]
    config = {
        "citations": [{"name": name, "doi": "", "license": "",
                       "description": (workflow["description"] + " Fractal tasks: " + ", ".join(packages)).strip()}],
        "docker_image": {"org": "org", "name": _identifier(name).replace("_", "-"), "tag": "v0.0.1",
                         "platform": "linux/amd64"},
        "algorithm_folder_name": _identifier(name),
        "exec_function": {"name": "generate_cli_command", "cli_command": "fractal workflow (see wrapper.py)",
                          "hidden_args": None},
        "inputs": [{**image, "name": "input_folder", "label": "Input OME-Zarr images", "cli_tag": "--input_folder",
                    "description": "OME-Zarr images the workflow runs on", "folder_name": "/data/in",
                    "section_id": "input_folder"}],
        "outputs": [{**image, "name": "output_folder", "label": "Output OME-Zarr images", "cli_tag": "--output_folder",
                     "cli_order": 2, "description": "Images the workflow wrote", "folder_name": "/data/out",
                     "section_id": "output_folder"}],
        "parameters": parameters,
        "display_only": None,
    }
    return config, plan
