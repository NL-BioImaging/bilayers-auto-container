"""workflow-files/: the files copied verbatim into every generated workflow repository."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "workflow-files"
RELEASE = FILES / ".github" / "workflows" / "release.yml"


def _steps():
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]["release"]["steps"]


def _step(name):
    return next(s for s in _steps() if s.get("name") == name)


def _heredoc(run):
    return run.split("<<'PY'\n", 1)[1].rsplit("PY\n", 1)[0]


def _bump(tmp_path, text, version="v2.3.4"):
    (tmp_path / "config.yaml").write_text(text, encoding="utf-8")
    script = tmp_path / "bump.py"
    script.write_text(_heredoc(_step("Set docker_image.tag in config.yaml")["run"]), encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", str(script)], cwd=tmp_path, capture_output=True,
                            text=True, env={**os.environ, "VERSION": version})
    return result, (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_release_is_manual_with_a_version():
    workflow = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    on = workflow.get("on", workflow.get(True))
    assert list(on) == ["workflow_dispatch"]
    assert on["workflow_dispatch"]["inputs"]["version"]["required"] is True
    assert workflow["env"]["PUBLISH"] == "false"  # set to "true" per generated repository
    assert workflow["permissions"] == {"contents": "write"}


def test_release_publishes_the_image_before_the_tag():
    """The tag (what the Slurm client reads) must only appear once the image exists."""
    names = [s.get("name") or s.get("uses") for s in _steps()]
    order = [
        "Set docker_image.tag in config.yaml",
        "Validate and test the release",
        "Commit the version",
        "docker/build-push-action@v6",
        "Image is in the registry",
        "Push the version commit and tag",
        "Create the GitHub release",
    ]
    positions = [next(i for i, n in enumerate(names) if n.startswith(o)) for o in order]
    assert positions == sorted(positions)
    build = next(s for s in _steps() if s.get("uses", "").startswith("docker/build-push-action"))
    assert build["with"]["context"] == "."  # the version commit, not the pushed branch


@pytest.mark.parametrize("config", sorted(ROOT.glob("examples/*/config.yaml")), ids=lambda p: p.parent.name)
def test_bump_example_configs(tmp_path, config):
    original = config.read_text(encoding="utf-8")
    result, text = _bump(tmp_path, original)
    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(text)["docker_image"]["tag"] == "v2.3.4"
    changed = [(a, b) for a, b in zip(original.splitlines(), text.splitlines()) if a != b]
    assert len(changed) == 1 and len(original.splitlines()) == len(text.splitlines())


CONFIG = """\
# comment
name: x
docker_image:
  org: quay.io/lab
  name: tool

  tag: v1.0.0   # the release
  platform: "linux/amd64"
parameters:
  - name: tag
    tag: keep
"""


def test_bump_keeps_layout_and_other_tags(tmp_path):
    result, text = _bump(tmp_path, CONFIG)
    assert result.returncode == 0, result.stderr
    assert text == CONFIG.replace("tag: v1.0.0   #", 'tag: "v2.3.4"   #')


def test_bump_fails_without_a_tag(tmp_path):
    result, text = _bump(tmp_path, "docker_image:\n  org: lab\n  name: tool\nname: x\n")
    assert result.returncode != 0
    assert "tag: " not in text


def test_skills_have_frontmatter_named_after_their_folder():
    skills = sorted((FILES / ".claude" / "skills").glob("*/SKILL.md"))
    assert skills
    for skill in skills:
        front = re.match(r"---\n(.*?)\n---\n", skill.read_text(encoding="utf-8"), re.S)
        meta = yaml.safe_load(front.group(1))
        assert meta["name"] == skill.parent.name
        assert meta["description"]
