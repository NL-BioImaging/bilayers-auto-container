import hashlib
import json
from pathlib import Path

import pytest
import yaml

from bilayers_auto import BilayersSpec, SpecError, validate_config
from conftest import slurm_argv

VENDOR = Path(__file__).resolve().parents[1] / "src" / "bilayers_auto" / "_vendor" / "bilayers"


def test_vendored_bilayers_files_are_unmodified():
    upstream = json.loads((VENDOR / "UPSTREAM.json").read_text())
    for name, digest in upstream["sha256"].items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, f"{name} differs from upstream"


def test_example_config_is_valid(config_path):
    assert validate_config(config_path) == []


def test_invalid_config_is_reported(tmp_path, config_path):
    config = yaml.safe_load(config_path.read_text())
    config["parameters"][0]["type"] = "string"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    assert any("'string' is not one of" in issue for issue in validate_config(path))


def test_defaults(config_path):
    values = BilayersSpec(config_path).parse_args([])
    assert values.params == {"threshold_method": "otsu", "min_diameter": 5.0, "max_diameter": 20.0, "save_dir": "/data/out"}
    assert values.inputs == {"input_images": Path("/data/in")}
    assert values.outputs == {"segmented_images": Path("/data/out")}


def test_slurm_arguments(config_path, tmp_path):
    argv = slurm_argv(config_path, tmp_path, threshold_method="li", min_diameter=2.5)
    values = BilayersSpec(config_path).parse_args(argv)
    assert values.inputs["input_images"] == tmp_path / "data" / "in"
    assert values.params["save_dir"] == (tmp_path / "data" / "out").as_posix()
    assert values.outputs["segmented_images"] == tmp_path / "data" / "out"
    assert values.params["threshold_method"] == "li"
    assert values.params["min_diameter"] == 2.5


def test_params_file_and_precedence(config_path, tmp_path):
    params = tmp_path / "params.yaml"
    params.write_text(yaml.safe_dump({"threshold_method": "li", "min_size": 7, "max_diameter": 30}))
    values = BilayersSpec(config_path).parse_args(["--params", str(params), "--max_size", "40"])
    assert values.params["threshold_method"] == "li"
    assert values.params["min_diameter"] == 7.0  # by cli_tag
    assert values.params["max_diameter"] == 40.0  # command line wins over the file


def test_unknown_params_file_key(config_path, tmp_path):
    params = tmp_path / "params.yaml"
    params.write_text("not_a_parameter: 1\n")
    with pytest.raises(SpecError, match="not_a_parameter"):
        BilayersSpec(config_path).parse_args(["--params", str(params)])


def test_biaflows_style_arguments(config_path, tmp_path):
    argv = ["--infolder", str(tmp_path / "in"), "--outfolder", str(tmp_path / "out"), "--gtfolder", str(tmp_path / "gt"),
            "--local", "-nmc", "--threshold_method", "li", "--min_diameter", "3"]
    values = BilayersSpec(config_path).parse_args(argv)
    assert values.inputs["input_images"] == tmp_path / "in"
    assert values.params["save_dir"] == str(tmp_path / "out")
    assert values.outputs["segmented_images"] == tmp_path / "out"
    assert values.params["min_diameter"] == 3.0
    assert values.extra == {"infolder": str(tmp_path / "in"), "outfolder": str(tmp_path / "out"),
                            "gtfolder": str(tmp_path / "gt"), "local": True, "nmc": True}


@pytest.mark.parametrize("value", ["bad", ""])
def test_invalid_choice(config_path, value):
    with pytest.raises(SpecError, match="threshold_method"):
        BilayersSpec(config_path).parse_args([f"--threshold_method={value}"])


def test_invalid_number(config_path):
    with pytest.raises(SpecError, match="min_diameter"):
        BilayersSpec(config_path).parse_args(["--min_size", "small"])


def test_checkbox_forms(tmp_path, config_path):
    config = yaml.safe_load(config_path.read_text())
    config["parameters"].append({"name": "invert", "type": "checkbox", "label": "Invert", "description": "",
                                 "default": False, "cli_tag": "--invert", "optional": True, "append_value": False,
                                 "section_id": "input-args", "mode": "beginner"})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    spec = BilayersSpec(path)
    assert spec.parse_args(["--invert"]).params["invert"] is True  # bilayers interfaces
    assert spec.parse_args(["--invert=False"]).params["invert"] is False  # the Slurm client
    assert spec.parse_args(["--invert=True"]).params["invert"] is True
    values = spec.parse_args(["--invert"])
    assert "--invert" in spec.command(values)
    assert "--invert" not in spec.command(spec.parse_args([]))


def test_command_uses_bilayers_generator(config_path):
    spec = BilayersSpec(config_path)
    values = spec.parse_args(["--folder", str(Path("/data/my in")), "--threshold_method", "li"])
    assert spec.command(values) == [
        "python", "-m", "bilayers_algorithms.classical_segmentation.classical_segmentation",
        "--folder", str(Path("/data/my in")), "--threshold_method", "li", "--min_size", "5.0", "--max_size", "20.0",
        "--save_dir", "/data/out",
    ]


def test_outputs_with_cli_tag_are_passed(tmp_path, config_path):
    config = yaml.safe_load(config_path.read_text())
    config["outputs"][0]["cli_tag"] = "--out"
    config["outputs"][0]["cli_order"] = 6
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    spec = BilayersSpec(path)
    command = spec.command(spec.parse_args(["--out", "/results"]))
    assert command[-2:] == ["--out", str(Path("/results"))]
