import json
import sys
from pathlib import Path

import yaml

from bilayers_auto import BilayersSpec, run, validate_config
from bilayers_auto.biaflows import bilayers_from_descriptor, biaflows_tool, descriptor_from_bilayers
from conftest import DATA, slurm_argv

DESCRIPTOR = json.loads((DATA / "biaflows_descriptor.json").read_text())


def write_config(tmp_path, config) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def test_bilayers_from_descriptor_is_valid(tmp_path):
    path = write_config(tmp_path, bilayers_from_descriptor(DESCRIPTOR))
    assert validate_config(path) == []
    spec = BilayersSpec(path)
    assert list(spec.parameters) == ["nuclei_diameter_range", "size_smoothing_filter"]
    assert spec.parameters["size_smoothing_filter"]["type"] == "integer"
    assert spec.parsed["docker_image"]["name"] == "w_nucleisegmentation-cellprofiler"


def test_descriptor_round_trip(config_path):
    descriptor = descriptor_from_bilayers(BilayersSpec(config_path))
    assert descriptor["schema-version"] == "cytomine-0.1"
    assert [param["id"] for param in descriptor["inputs"]] == ["threshold_method", "min_diameter", "max_diameter"]
    assert descriptor["inputs"][0]["value-choices"] == ["otsu", "li"]


def test_biaflows_tool_gets_biaflows_arguments(tmp_path, tiff_folder):
    """A BIAFLOWS wrapper behind a bilayers spec, called by the Slurm client as a bilayers workflow."""
    path = write_config(tmp_path, bilayers_from_descriptor(DESCRIPTOR))
    record = tmp_path / "argv.json"
    fake_wrapper = tmp_path / "wrapper.py"
    fake_wrapper.write_text(
        "import json, sys, pathlib\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--outfolder') + 1])\n"
        "(out / 'result.txt').write_text('done')\n"
    )
    data = tmp_path / "job"
    (data / "data").mkdir(parents=True)
    tiff_folder.rename(data / "data" / "in")
    argv = slurm_argv(path, data, size_smoothing_filter=3)
    assert run(argv, spec=path, tool=biaflows_tool([sys.executable, str(fake_wrapper)]), ro_crate=False) == 0
    received = json.loads(record.read_text())
    assert received[:2] == ["--infolder", str(data / "data" / "in")]
    assert received[2:4] == ["--outfolder", str(data / "data" / "out")]
    assert received[6:] == ["--local", "-nmc", "--nuclei_diameter_range", "15,4000", "--size_smoothing_filter", "3"]
    assert (data / "data" / "out" / "result.txt").read_text() == "done"
