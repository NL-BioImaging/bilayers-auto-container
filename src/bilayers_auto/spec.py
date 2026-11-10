"""Generic bilayers spec reader.

Loading and command generation reuse bilayers' own parse/cli_generator modules
(vendored unmodified), validation reuses the LinkML schema from bilayers-schema.
What is added here is resolving the values of one run, from:

* command-line arguments, as BIOMERO passes them (``--<cli_tag>="<value>"``,
  folder inputs/outputs set to ``data/in``/``data/out``), as bilayers' interfaces
  pass them (``--tag value`` / bare checkbox flags), or BIAFLOWS-style
  (``--infolder --outfolder --gtfolder --local -nmc --<name> value``);
* an optional parameters YAML file (``--params params.yaml``) of ``{name: value}``;
* the defaults in the spec.

Precedence: spec defaults < parameters file < command-line arguments.
"""
from __future__ import annotations

import argparse
import copy
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from ._vendor.bilayers.cli_generator import generate_cli_command
from ._vendor.bilayers.parse import build_cli_sequence, load_config, parse_config

# The types bilayers' cli_generator treats as folders (passes folder_name for)
FOLDER_TYPES = {"image", "measurement", "file", "executable", "array"}
BOOLEAN_TYPES = {"checkbox", "boolean"}
TRUE_STRINGS = {"true", "1", "yes", "y", "on"}
FALSE_STRINGS = {"false", "0", "no", "n", "off", ""}
# BIAFLOWS calling convention, accepted when the spec does not use these flags itself
BIAFLOWS_FOLDER_FLAGS = {"--infolder": "inputs", "--outfolder": "outputs", "--gtfolder": None}
BIAFLOWS_SWITCHES = ("--local", "-nmc")
PARAMS_FILE_FLAGS = ("--params", "--biomero-params")


class SpecError(ValueError):
    """Invalid spec, or values that do not satisfy it."""


def has_cli_tag(item: Mapping[str, Any]) -> bool:
    return item.get("cli_tag") not in (None, "None")


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUE_STRINGS:
        return True
    if text in FALSE_STRINGS:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def validate_config(config_path: str | Path) -> list[str]:
    """Validate a bilayers config against the bilayers-schema LinkML schema.

    Same check as ``bilayers_cli validate``; needs the ``validate`` extra (linkml).
    Returns the list of issues (empty when valid).
    """
    try:
        import linkml.validator
    except ImportError as error:
        raise ImportError("LinkML validation needs linkml: pip install 'bilayers-auto-container[validate]'") from error
    from bilayers_schema import schema

    report = linkml.validator.validate(load_config(config_path), schema)
    return [f"[{result.severity.value}] {result.message}" for result in report.results]


@dataclass
class RunValues:
    """Resolved values of one run, keyed by bilayers ``name``."""

    params: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Path | None] = field(default_factory=dict)
    outputs: dict[str, Path | None] = field(default_factory=dict)
    hidden: dict[str, Any] = field(default_factory=dict)  # keyed by cli_tag, as in bilayers
    extra: dict[str, Any] = field(default_factory=dict)  # BIAFLOWS-style arguments (gtfolder, local, nmc)

    def as_dict(self) -> dict[str, Any]:
        """Flat ``{name: value}`` of inputs, outputs and parameters (paths as strings)."""
        folders = {name: str(path) if path is not None else None for name, path in {**self.inputs, **self.outputs}.items()}
        return {**folders, **self.params}


class BilayersSpec:
    """A bilayers ``config.yaml``, parsed with bilayers' own ``parse_config``."""

    def __init__(self, config_path: str | Path):
        self.path = Path(config_path)
        try:
            self.parsed = parse_config(self.path)
        except (KeyError, TypeError, AttributeError) as error:
            raise SpecError(f"Not a valid bilayers config: {self.path} ({error!r})") from error

    @property
    def inputs(self) -> dict[str, dict]:
        return self.parsed["inputs"]

    @property
    def outputs(self) -> dict[str, dict]:
        return self.parsed["outputs"]

    @property
    def parameters(self) -> dict[str, dict]:
        return self.parsed["parameters"]

    @property
    def hidden_args(self) -> dict[str, dict]:
        return self.parsed["exec_function"]["hidden_args"]

    @property
    def cli_command(self) -> str:
        return self.parsed["exec_function"].get("cli_command", "")

    def validate(self) -> list[str]:
        return validate_config(self.path)

    def output_dir_params(self) -> list[str]:
        """Parameters BIOMERO sets to the output folder (``output_dir_set: true``)."""
        return [name for name, item in self.parameters.items() if item.get("output_dir_set")]

    def folder_inputs(self) -> list[str]:
        return [name for name, item in self.inputs.items() if item.get("type") in FOLDER_TYPES]

    # --- resolving values -------------------------------------------------

    def coerce(self, item: Mapping[str, Any], value: Any) -> Any:
        """Convert a raw (string) value to the item's bilayers type."""
        if value is None:
            return None
        item_type = item.get("type")
        if item_type in FOLDER_TYPES:
            return Path(str(value))
        if item_type in BOOLEAN_TYPES:
            return to_bool(value)
        if item_type == "integer":
            number = float(value)
            if not number.is_integer():
                raise ValueError(f"not an integer: {value!r}")
            return int(number)
        if item_type == "float":
            return float(value)
        if item.get("options"):
            return self._coerce_option(item, value)
        return str(value) if isinstance(value, (int, float)) else value

    @staticmethod
    def _coerce_option(item: Mapping[str, Any], value: Any) -> Any:
        choices = [option.get("value") for option in item["options"]]
        values = value if isinstance(value, list) else str(value).split(",") if item.get("multiselect") else [value]
        selected = []
        for entry in values:
            match = next((choice for choice in choices if str(choice) == str(entry).strip()), None)
            if match is None:
                raise ValueError(f"{entry!r} is not one of {choices}")
            selected.append(match)
        return selected if item.get("multiselect") else selected[0]

    def build_parser(self, prog: str | None = None) -> argparse.ArgumentParser:
        """Argument parser accepting the spec's cli_tags plus BIAFLOWS-style arguments."""
        parser = argparse.ArgumentParser(prog=prog, description=self.parsed.get("algorithm_folder_name"), allow_abbrev=False)
        taken: set[str] = set()

        def add(flags: Sequence[str], **kwargs):
            flags = [flag for flag in flags if flag not in taken]
            if flags:
                taken.update(flags)
                parser.add_argument(*flags, default=argparse.SUPPRESS, **kwargs)

        sections = (("inputs", self.inputs), ("outputs", self.outputs), ("parameters", self.parameters))
        for section, items in sections:
            for name, item in items.items():
                dest = f"{section}:{name}"
                help_text = item.get("label") or item.get("description")
                is_flag = item.get("type") in BOOLEAN_TYPES
                flag_kwargs = {"nargs": "?", "const": "true"} if is_flag else {}
                if item.get("cli_tag") == "":
                    parser.add_argument(dest, nargs="?", default=argparse.SUPPRESS, help=help_text)
                    continue
                flags = [item["cli_tag"].rstrip("=")] if has_cli_tag(item) else []
                # --<name> as alias: BIAFLOWS descriptors generated from the spec use the name as id
                add(flags + [f"--{name}"], dest=dest, metavar=name.upper(), help=help_text, **flag_kwargs)
        for tag, item in self.hidden_args.items():
            add([tag.rstrip("=")], dest=f"hidden:{tag}", nargs="?", const=item.get("value"), help=argparse.SUPPRESS)
        for flag in BIAFLOWS_FOLDER_FLAGS:
            add([flag], dest=f"extra:{flag.lstrip('-')}", help=argparse.SUPPRESS)
        for flag in BIAFLOWS_SWITCHES:
            add([flag], dest=f"extra:{flag.lstrip('-')}", action="store_const", const=True, help=argparse.SUPPRESS)
        add(PARAMS_FILE_FLAGS, dest="params_file", help="YAML file with {name: value} parameter values")
        return parser

    def parse_args(self, argv: Sequence[str] | None = None, params_file: str | Path | None = None,
                   prog: str | None = None) -> RunValues:
        namespace = vars(self.build_parser(prog).parse_args(argv))
        params_file = namespace.pop("params_file", None) or params_file
        cli_values = {}
        for dest, value in namespace.items():
            section, _, name = dest.partition(":")
            cli_values.setdefault(section, {})[name] = value
        return self.resolve(self.read_params_file(params_file) if params_file else {}, cli_values)

    def read_params_file(self, path: str | Path) -> dict[str, dict[str, Any]]:
        """Read ``{name or cli_tag: value}`` from YAML, sorted into spec sections."""
        with open(path, encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise SpecError(f"Parameters file must contain a mapping: {path}")
        sections = {"inputs": self.inputs, "outputs": self.outputs, "parameters": self.parameters}
        by_key = {}
        for section, items in sections.items():
            for name, item in items.items():
                by_key[name] = (section, name)
                if has_cli_tag(item) and item["cli_tag"]:
                    by_key.setdefault(item["cli_tag"].rstrip("=").lstrip("-"), (section, name))
        values: dict[str, dict[str, Any]] = {}
        unknown = []
        for key, value in data.items():
            target = by_key.get(str(key).lstrip("-"))
            if target is None and f"--{str(key).lstrip('-')}" in BIAFLOWS_FOLDER_FLAGS:
                target = ("extra", str(key).lstrip("-"))
            if target is None:
                unknown.append(key)
                continue
            values.setdefault(target[0], {})[target[1]] = value
        if unknown:
            raise SpecError(f"Unknown parameters in {path}: {unknown}")
        return values

    def resolve(self, *layers: Mapping[str, Mapping[str, Any]]) -> RunValues:
        """Combine value layers (later layers win) with the spec defaults into RunValues."""
        merged: dict[str, dict[str, Any]] = {}
        for layer in reversed(layers):
            for section, values in layer.items():
                for name, value in values.items():
                    merged.setdefault(section, {}).setdefault(name, value)
        provided = lambda section: merged.get(section, {})  # noqa: E731
        extra = dict(provided("extra"))
        errors = []
        run = RunValues(extra=extra)

        for name, item in self.parameters.items():
            raw = provided("parameters").get(name, item.get("default"))
            try:
                run.params[name] = self.coerce(item, raw)
            except ValueError as error:
                errors.append(f"parameter {name!r}: {error}")
                continue
            if run.params[name] is None and not item.get("optional", False):
                errors.append(f"parameter {name!r} is required")

        folder_inputs = self.folder_inputs()
        for name, item in self.inputs.items():
            raw = provided("inputs").get(name)
            if raw is None and extra.get("infolder") and folder_inputs[:1] == [name]:
                raw = extra["infolder"]
            if raw is None:
                raw = item.get("folder_name") if item.get("type") in FOLDER_TYPES else item.get("default")
            run.inputs[name] = self.coerce(item, raw)
            if run.inputs[name] is None and not item.get("optional", False):
                errors.append(f"input {name!r} is required")

        # BIAFLOWS-style --outfolder sets the output_dir_set parameters not given explicitly
        for name in self.output_dir_params():
            if extra.get("outfolder") and name not in provided("parameters"):
                run.params[name] = str(extra["outfolder"])
        # Outputs without a cli_tag are written where the output_dir_set parameter points
        output_dir = next((run.params[name] for name in self.output_dir_params() if run.params.get(name)), None)
        for index, (name, item) in enumerate(self.outputs.items()):
            raw = provided("outputs").get(name)
            if raw is None and not has_cli_tag(item):
                raw = output_dir
            if raw is None and index == 0:
                raw = extra.get("outfolder")
            if raw is None:
                raw = item.get("folder_name")
            run.outputs[name] = Path(str(raw)) if raw is not None else None

        for tag, item in self.hidden_args.items():
            run.hidden[tag] = provided("hidden").get(tag, item.get("value"))

        if errors:
            raise SpecError("; ".join(errors))
        return run

    # --- building the tool command ----------------------------------------

    def command(self, values: RunValues, cli_command: str | Sequence[str] | None = None) -> list[str]:
        """The tool command (argv) for these values, built by bilayers' generate_cli_command.

        Outputs with a cli_tag are included in cli_order, the way BIOMERO passes them
        (bilayers itself only puts inputs, parameters and hidden args on the command line).
        """
        config = copy.deepcopy(self.parsed)
        if cli_command is not None:
            config["exec_function"]["cli_command"] = cli_command if isinstance(cli_command, str) else shlex.join(cli_command)
        for name, item in config["parameters"].items():
            item["default"] = _quoted(values.params.get(name))
        for name, item in config["inputs"].items():
            item["folder_name"] = item["default"] = _quoted(values.inputs.get(name))
        for name, item in config["outputs"].items():
            if has_cli_tag(item):
                path = _quoted(values.outputs.get(name))
                config["inputs"][f"output:{name}"] = {**item, "folder_name": path, "default": path}
        for tag, item in config["exec_function"]["hidden_args"].items():
            item["value"] = _quoted(values.hidden.get(tag))
        try:
            return shlex.split(generate_cli_command(config, return_as_string=True))
        except ValueError as error:
            raise SpecError(str(error)) from error

    def cli_order(self) -> list[str]:
        """Names in the order bilayers puts them on the command line."""
        return list(build_cli_sequence(self.parsed))


def _quoted(value: Any) -> Any:
    """Shell-quote a value so bilayers' space-joined command can be split back safely."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        value = ",".join(str(entry) for entry in value)
    value = str(value)
    return shlex.quote(value) if value else value
