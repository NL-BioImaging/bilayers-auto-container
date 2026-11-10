import shutil
import subprocess
from pathlib import Path

import pytest

from bilayers_auto import container_test as ct
from bilayers_auto.conversion import ZARR, find_images, image_format
from conftest import CONFIG as WORKFLOW_CONFIG, EXAMPLE, ROOT


def test_image_name_from_config():
    image, platform = ct.image_name(WORKFLOW_CONFIG)
    assert image.count("/") == 1 and ":" in image
    assert platform.startswith("linux/")


def test_example_is_built_with_its_own_dockerfile():
    command = ct.build_command("docker", ROOT, "org/name:v1", "linux/amd64", WORKFLOW_CONFIG)
    assert command[command.index("-f") + 1] == str(EXAMPLE / "Dockerfile")
    assert "WORKFLOW_DIR=examples/classical_segmentation" in command
    assert command[-1] == str(ROOT)


def _generic_lines(dockerfile):
    lines, in_tool_layer = [], False
    for line in dockerfile.read_text().splitlines():
        if line.startswith("# >>> tool layer"):
            in_tool_layer = True
        elif line.startswith("# <<< tool layer"):
            in_tool_layer = False
        elif not in_tool_layer:
            lines.append(line)
    return lines


def test_example_dockerfile_matches_template_outside_tool_layer():
    assert _generic_lines(EXAMPLE / "Dockerfile") == _generic_lines(ROOT / "Dockerfile")


def test_run_command_mounts_the_job_data_folder(tmp_path):
    docker = ct.run_command("docker", "org/name:v1", "linux/amd64", tmp_path, ["--folder=/data/in"])
    assert docker[:6] == ["docker", "run", "--rm", "--platform", "linux/amd64", "-v"]
    assert docker[6] == f"{tmp_path.resolve()}:/data"
    assert docker[7:] == ["org/name:v1", "--folder=/data/in"]
    podman = ct.run_command("podman", "org/name:v1", "linux/amd64", tmp_path, [])
    assert podman[6].endswith(":/data:Z")  # SELinux relabel


def test_slurm_arguments_use_container_paths():
    args = ct.slurm_argv(WORKFLOW_CONFIG, ct.CONTAINER_DATA)
    assert any(arg.endswith("=/data/in") for arg in args)
    assert any(arg.endswith("=/data/out") for arg in args)


def test_sample_input_is_ome_zarr(tmp_path):
    [store] = ct.write_sample_input(tmp_path / "in")
    assert image_format(store) == ZARR and find_images(tmp_path / "in") == [store]


def test_no_engine_is_reported(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="docker is not running"):
        ct.detect_runtime()


def _engine():
    try:
        return ct.detect_runtime()
    except RuntimeError:
        return None


@pytest.mark.container
@pytest.mark.skipif(_engine() is None, reason="Docker is not running")
def test_build_and_run_like_slurm(tmp_path):
    outputs = ct.container_test(WORKFLOW_CONFIG, keep=tmp_path / "job")
    assert outputs, "no outputs"


def test_build_context_is_the_config_folder_in_a_workflow_repository(tmp_path):
    config = tmp_path / "workflow" / "config.yaml"
    config.parent.mkdir()
    config.write_text("")
    assert ct.build_context(config) == config.parent.resolve()
    assert ct.build_context(WORKFLOW_CONFIG) == ROOT


def _fake_engine(monkeypatch, write_output):
    """No Docker: the 'run' step calls write_output(data folder) instead."""
    monkeypatch.setattr(ct, "detect_runtime", lambda runtime="docker": runtime)

    def fake_run(argv):
        if argv[1] == "run":
            write_output(Path(argv[argv.index("-v") + 1].rsplit(":/data", 1)[0]))
    monkeypatch.setattr(ct, "_run", fake_run)


def test_synthetic_input_is_compared_with_its_ground_truth(tmp_path, monkeypatch):
    import tifffile

    def segment(data):  # a perfect segmentation: the ground truth labels
        for labels in (data.parent / "expected").iterdir():
            tifffile.imwrite(data / "out" / f"{labels.stem}_labels.tif", tifffile.imread(labels))
    _fake_engine(monkeypatch, segment)
    synthetic = ct.Synthetic(objects=3)
    outputs = ct.container_test(WORKFLOW_CONFIG, keep=tmp_path / "job", synthetic=synthetic)
    assert [output.name for output in outputs] == ["synthetic_labels.tif"]
    assert find_images(tmp_path / "job" / "data" / "in")[0].name == "synthetic.ome.zarr"

    _fake_engine(monkeypatch, lambda data: (data / "out" / "synthetic_labels.tif").write_bytes(b""))
    with pytest.raises(RuntimeError, match="1 of 1 expected outputs do not match"):
        ct.container_test(WORKFLOW_CONFIG, keep=tmp_path / "job2", synthetic=synthetic,
                          expected=tmp_path / "job" / "expected")
