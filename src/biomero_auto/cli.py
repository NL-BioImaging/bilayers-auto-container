"""``biomero-auto`` command: check and convert bilayers specs while building a container."""
from __future__ import annotations

import argparse
import json
import logging
import shlex
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml

from . import biaflows
from .sample_data import DTYPES, Synthetic
from .spec import BilayersSpec, SpecError, validate_config


def _read_text(source: str) -> str:
    """Read a path or URL (GitHub blob URLs are fetched raw)."""
    if source.startswith(("http://", "https://")):
        url = source.replace("://github.com/", "://raw.githubusercontent.com/").replace("/blob/", "/")
        with urllib.request.urlopen(url) as response:
            return response.read().decode("utf-8")
    return Path(source).read_text(encoding="utf-8")


def _read_mapping(source: str) -> dict:
    """Read a JSON/YAML mapping from a path or URL."""
    return yaml.safe_load(_read_text(source))


def _write(data: dict, output: str | None, as_json: bool) -> None:
    text = json.dumps(data, indent=2) + "\n" if as_json else yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _biomero_check(config: str) -> list[str]:
    """Parse the config with BIOMERO's own bilayers adapter (needs the biomero package)."""
    from biomero.schema_parsers import WorkflowDescriptorParser

    try:
        WorkflowDescriptorParser.parse_descriptor(yaml.safe_load(Path(config).read_text(encoding="utf-8")))
    except Exception as error:  # noqa: BLE001 - report whatever BIOMERO rejects
        return [f"[BIOMERO] {error}"]
    return []


def _synthetic_options(parser, prefix: str = "") -> None:
    """Options describing synthetic images (``prefix`` keeps them apart from other options)."""
    group = parser.add_argument_group("synthetic images")
    group.add_argument(f"--{prefix}size", type=int, default=64, help="Y and X size (default 64)")
    group.add_argument(f"--{prefix}z", type=int, default=1, help="Z planes; > 1 gives 3D objects")
    group.add_argument(f"--{prefix}t", type=int, default=1, help="time points")
    group.add_argument(f"--{prefix}channels", type=int, default=1)
    group.add_argument(f"--{prefix}objects", type=int, default=2, help="objects per image")
    group.add_argument(f"--{prefix}radius", type=int, default=6, help="mean object radius in pixels")
    group.add_argument(f"--{prefix}noise", type=float, default=0.0, help="Gaussian noise standard deviation")
    group.add_argument(f"--{prefix}dtype", choices=DTYPES, default="uint16")


def _synthetic_spec(args, prefix: str = "") -> Synthetic:
    value = lambda name: getattr(args, prefix.replace("-", "_") + name)  # noqa: E731
    return Synthetic(size=value("size"), z=value("z"), t=value("t"), channels=value("channels"),
                     objects=value("objects"), radius=value("radius"), noise=value("noise"), dtype=value("dtype"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="biomero-auto", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a bilayers config (bilayers-schema LinkML schema)")
    validate.add_argument("config")
    validate.add_argument("--biomero", action="store_true", help="also parse it with BIOMERO's bilayers adapter")

    for name, help_text in (("parse", "print the values resolved from ARGS as JSON"),
                            ("command", "print the tool command built from ARGS")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("config")
        sub.add_argument("args", nargs=argparse.REMAINDER, help="container arguments (after --)")

    from_biaflows = commands.add_parser("from-biaflows", help="draft a bilayers config from a BIAFLOWS descriptor.json")
    from_biaflows.add_argument("descriptor", help="path or URL")
    from_biaflows.add_argument("-o", "--output")
    from_biaflows.add_argument("--cli-command", default="python /app/biaflows/wrapper.py",
                               help="command running the BIAFLOWS wrapper inside the container")
    from_biaflows.add_argument("--input-format", nargs="+", default=["tiff"])
    from_biaflows.add_argument("--output-format", nargs="+", default=["tiff"])

    check = commands.add_parser("container-test", help="build the image with Docker and run it on a sample job like BIOMERO")
    check.add_argument("config", nargs="?", default="config.yaml")
    check.add_argument("--runtime", choices=("docker", "podman"), default="docker")
    check.add_argument("--sample", help="folder of input images (default: a generated OME-Zarr)")
    check.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="parameter value (repeatable)")
    check.add_argument("--no-build", action="store_true", help="use the already built image")
    check.add_argument("--keep", help="job folder to use and keep (default: a new temporary folder)")
    check.add_argument("--expected", help="folder of expected outputs to compare the outputs with")
    check.add_argument("--synthetic", action="store_true",
                       help="use synthetic images as input; their labels are the expected output unless --expected")
    check.add_argument("--min-dice", type=float, default=0.9, help="minimum foreground Dice for label images")
    check.add_argument("--rtol", type=float, default=1e-3, help="relative tolerance for intensities and numbers")
    check.add_argument("--mode", choices=("auto", "labels", "intensities"), default="auto",
                    help="compare images as labels or intensities (default: detect)")
    _synthetic_options(check, prefix="synthetic-")

    synthetic = commands.add_parser("synthetic", help="write synthetic input images and their ground-truth labels")
    synthetic.add_argument("folder", help="folder for the input images")
    synthetic.add_argument("--expected", help="folder for the ground-truth labels (TIFF, same names)")
    synthetic.add_argument("--images", type=int, default=1, help="number of images")
    synthetic.add_argument("--format", choices=("zarr", "tiff"), default="zarr", help="OME-Zarr (default) or OME-TIFF")
    synthetic.add_argument("--seed", type=int, default=0)
    synthetic.add_argument("--name", default="synthetic", help="image file name (stem)")
    _synthetic_options(synthetic)

    compare = commands.add_parser("compare", help="compare a workflow's output folder with expected outputs")
    compare.add_argument("output")
    compare.add_argument("expected")
    compare.add_argument("--min-dice", type=float, default=0.9, help="minimum foreground Dice for label images")
    compare.add_argument("--rtol", type=float, default=1e-3, help="relative tolerance for intensities and numbers")
    compare.add_argument("--mode", choices=("auto", "labels", "intensities"), default="auto",
                    help="compare images as labels or intensities (default: detect)")

    from_fractal = commands.add_parser("from-fractal", help="draft config.yaml for a Fractal task (__FRACTAL_MANIFEST__.json)"
                                       " or a Fractal workflow (export or template JSON)")
    from_fractal.add_argument("manifest", help="path or URL of a task manifest or a workflow")
    from_fractal.add_argument("--task", help="task name or executable (needed when the manifest has several)")
    from_fractal.add_argument("-o", "--output")
    from_fractal.add_argument("--steps", help="workflow: file for the step plan wrapper.py runs (default: "
                                               "fractal_steps.json next to --output)")
    from_fractal.add_argument("--wheels", default=".fractal-wheels",
                              help="workflow: folder for the task packages' wheels (their manifests are read from them)")
    from_fractal.add_argument("--manifest", dest="package_manifests", action="append", default=[], metavar="PKG==VERSION=PATH",
                              help="workflow: a package's wheel or __FRACTAL_MANIFEST__.json (packages not on PyPI)")
    from_fractal.add_argument("--python-version", default="3.12",
                              help="workflow: Python of the task environment, for downloading the packages' wheels")
    from_fractal.add_argument("--cli-command", default="python -m <package>.<task module>",
                              help="command running the task inside the container")

    from_galaxy = commands.add_parser("from-galaxy", help="draft config.yaml for a Galaxy tool (.xml) or workflow (.ga)")
    from_galaxy.add_argument("source", help="path or URL of a tool wrapper XML or a .ga workflow")
    from_galaxy.add_argument("-o", "--output")
    from_galaxy.add_argument("--no-step-params", action="store_true",
                             help="workflow: only its parameter_input steps, not the values its tool steps fix")
    from_galaxy.add_argument("--input-format", nargs="+", default=["tiff"], help="workflow: input image formats")
    from_galaxy.add_argument("--output-format", nargs="+", default=["tiff"], help="workflow: output image formats")
    from_galaxy.add_argument("--tool", action="append", default=[], metavar="XML",
                             help="workflow: a step's tool wrapper (path or URL), at the version the workflow pins; "
                                  "gives its parameters' types and options and reports stale tool_state (repeatable)")

    to_biaflows = commands.add_parser("to-biaflows", help="write a BIAFLOWS descriptor.json for a bilayers config")
    to_biaflows.add_argument("config")
    to_biaflows.add_argument("-o", "--output")

    args = parser.parse_args(argv)
    try:
        return _run(args)
    except SpecError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _run(args) -> int:
    if args.command == "validate":
        issues = validate_config(args.config) + (_biomero_check(args.config) if args.biomero else [])
        print("\n".join(issues) if issues else "No issues found")
        return int(bool(issues))
    if args.command in ("parse", "command"):
        spec = BilayersSpec(args.config)
        values = spec.parse_args(args.args[1:] if args.args[:1] == ["--"] else args.args)
        if args.command == "parse":
            print(json.dumps(values.as_dict(), indent=2, default=str))
        else:
            print(shlex.join(spec.command(values)))
        return 0
    if args.command == "from-biaflows":
        config = biaflows.bilayers_from_descriptor(_read_mapping(args.descriptor), cli_command=args.cli_command,
                                                   input_formats=args.input_format, output_formats=args.output_format)
        _write(config, args.output, as_json=False)
        return 0
    if args.command == "from-fractal":
        from . import fractal

        manifest = _read_mapping(args.manifest)
        if not fractal.is_workflow(manifest):
            _write(fractal.bilayers_from_manifest(manifest, args.task, cli_command=args.cli_command), args.output,
                   as_json=False)
            print(f"wrapper.py: {fractal.wrapper_hint(manifest, args.task)}", file=sys.stderr)
            return 0
        workflow = fractal.load_workflow(manifest)
        manifests = {}
        for entry in args.package_manifests:
            key, _, path = entry.rpartition("=")
            manifests[key] = fractal.read_manifest(Path(path))
        for step in workflow["steps"]:
            key = f"{step['pkg_name']}=={step['version']}"
            if key not in manifests:
                try:
                    manifests[key] = fractal.fetch_manifest(step["pkg_name"], step["version"], Path(args.wheels),
                                                            args.python_version)
                except (subprocess.CalledProcessError, FileNotFoundError, StopIteration) as error:
                    print(f"no manifest for {key}: {error}", file=sys.stderr)
                    manifests[key] = ({}, None)
        config, plan = fractal.bilayers_from_workflow(workflow, manifests)
        _write(config, args.output, as_json=False)
        steps_path = Path(args.steps) if args.steps else (Path(args.output).with_name("fractal_steps.json")
                                                          if args.output else None)
        kept = [step for step in plan if "skip" not in step]
        for step in plan:
            status = f"SKIPPED: {step['skip']}" if "skip" in step else step["kind"] + (f"; {step['note']}" if "note" in step else "")
            print(f"step {step['id']} ({step['task']}): {status}", file=sys.stderr)
        if steps_path:
            steps_path.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
            print(f"wrapper.py: run(spec=CONFIG, tool=fractal_workflow(Path(__file__).with_name({steps_path.name!r})))",
                  file=sys.stderr)
        return 0
    if args.command == "from-galaxy":
        from . import galaxy

        text = _read_text(args.source)
        if text.lstrip().startswith("<"):
            config = galaxy.bilayers_from_tool(text)
            for item in galaxy.unexpanded(text):
                print(f"not drafted (add by hand if user-facing): {item}", file=sys.stderr)
        else:
            workflow = json.loads(text)
            import xml.etree.ElementTree as ET

            tools = {}
            for source in args.tool:
                xml = _read_text(source)
                tools[ET.fromstring(xml).get("id")] = xml
            config = galaxy.bilayers_from_workflow(workflow, input_formats=args.input_format,
                                                   output_formats=args.output_format,
                                                   step_params=not args.no_step_params, tools=tools or None)
            for item in galaxy.stale_state(workflow, tools) if tools else []:
                print(f"stale tool_state (not in the pinned wrapper, left out): {item}", file=sys.stderr)
            for step in galaxy.workflow_steps(workflow):
                print(f"step {step['id']} {step['type']} {step['name']}: {step['tool_id'] or ''} "
                      f"{step['repository'] or ''} <- {step['inputs_from']}", file=sys.stderr)
        _write(config, args.output, as_json=False)
        return 0
    if args.command == "container-test":
        from .container_test import container_test

        logging.basicConfig(level=logging.INFO, format="%(message)s")
        params = dict(param.split("=", 1) for param in args.param)
        try:
            container_test(Path(args.config), runtime=args.runtime, sample=args.sample, params=params,
                           build=not args.no_build, keep=args.keep, expected=args.expected,
                           synthetic=_synthetic_spec(args, "synthetic-") if args.synthetic else None,
                           min_dice=args.min_dice, rtol=args.rtol, mode=args.mode)
        except (RuntimeError, subprocess.CalledProcessError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print("Container test passed")
        return 0
    if args.command == "synthetic":
        from .sample_data import write_synthetic

        images = write_synthetic(Path(args.folder), Path(args.expected) if args.expected else None,
                                 _synthetic_spec(args), images=args.images, as_zarr=args.format == "zarr",
                                 seed=args.seed, name=args.name)
        print("\n".join(str(image) for image in images))
        return 0
    if args.command == "compare":
        import tempfile

        from .compare import compare_outputs, unpack

        with tempfile.TemporaryDirectory() as workdir:
            results = compare_outputs(unpack(Path(args.output), Path(workdir)), Path(args.expected),
                                      min_dice=args.min_dice, rtol=args.rtol, mode=args.mode)
        print("\n".join(str(result) for result in results))
        return int(not all(result.ok for result in results))
    _write(biaflows.descriptor_from_bilayers(BilayersSpec(args.config)), args.output, as_json=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
