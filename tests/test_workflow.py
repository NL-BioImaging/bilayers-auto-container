"""Checks for the workflow(s) in this repository: the root config.yaml (written per workflow; the
template has none) and the examples. Keep these in generated workflow repositories."""
from pathlib import Path

import pytest

from bilayers_auto import BilayersSpec, validate_config
from bilayers_auto.cli import _biomero_check
from conftest import ROOT, biomero_argv

CONFIGS = [path for path in [ROOT / "config.yaml", *sorted(ROOT.glob("examples/*/config.yaml"))] if path.exists()]
DOCKERFILES = [path for path in [ROOT / "Dockerfile", *sorted(ROOT.glob("examples/*/Dockerfile"))] if path.exists()]


def _id(path: Path) -> str:
    return path.parent.relative_to(ROOT).as_posix() or "root"


@pytest.mark.parametrize("config", CONFIGS, ids=_id)
def test_config_is_valid_bilayers(config):
    assert validate_config(config) == []


@pytest.mark.parametrize("config", CONFIGS, ids=_id)
def test_biomero_can_parse_config(config):
    assert _biomero_check(str(config)) == []


@pytest.mark.parametrize("config", CONFIGS, ids=_id)
def test_biomero_arguments_resolve(config, tmp_path):
    """The arguments BIOMERO generates (with defaults) are accepted and produce a tool command."""
    spec = BilayersSpec(config)
    values = spec.parse_args(biomero_argv(config, tmp_path))
    for name in spec.folder_inputs():
        if not spec.inputs[name].get("optional"):
            assert values.inputs[name] == tmp_path / "data" / "in"
    assert any(Path(path) == tmp_path / "data" / "out" for path in [*values.outputs.values(), *(
        values.params[name] for name in spec.output_dir_params())] if path), "no output folder set to data/out"
    if spec.cli_command and "wrapper.py" not in spec.cli_command:
        assert spec.command(values)


def _instructions(dockerfile: Path, keyword: str) -> list[str]:
    lines = dockerfile.read_text().splitlines()
    return [line.split(None, 1)[1] for line in lines if line.upper().startswith(keyword + " ")]


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=_id)
def test_dockerfile_uses_wrapper_entrypoint(dockerfile):
    entrypoint = _instructions(dockerfile, "ENTRYPOINT")
    assert entrypoint and "wrapper.py" in entrypoint[-1], "BIOMERO runs the image entrypoint; it must be wrapper.py"


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=_id)
def test_base_images_are_fully_qualified(dockerfile):
    """Podman does not resolve short names like python:3.12-slim by default."""
    for base in _instructions(dockerfile, "FROM"):
        image = base.split()[0]
        if image.startswith("$") or image.lower() == "scratch":
            continue
        assert "." in image.split("/")[0], f"use a fully qualified image (e.g. docker.io/library/...): {image}"


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=_id)
def test_no_user_switch(dockerfile):
    """Singularity runs as the calling user; a USER breaks writing the mounted folders."""
    assert _instructions(dockerfile, "USER") == []


def test_no_descriptor_shadows_config():
    """BIOMERO reads descriptor.json/descriptor.yaml before config.yaml."""
    assert not [name for name in ("descriptor.json", "descriptor.yaml") if (ROOT / name).exists()]
