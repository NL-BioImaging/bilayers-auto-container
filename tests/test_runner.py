import json
import zipfile

import pytest
import tifffile
import yaml

from biomero_auto import BilayersSpec, run
from biomero_auto.conversion import allowed_outputs, find_images, output_target
from conftest import biomero_argv


@pytest.fixture
def spec(config_path, python_cli_command):
    spec = BilayersSpec(config_path)
    spec.parsed["exec_function"]["cli_command"] = python_cli_command
    return spec


@pytest.mark.filterwarnings("error::UserWarning:fair_metadata_integration")
def test_zarr_in_declared_tiff_out_with_ro_crate(spec, config_path, zarr_folder):
    """BIOMERO gives OME-Zarr; the TIFF tool runs on converted input (required: it reads TIFF only); its TIFF
    labels are not converted back, as the workflow declares TIFF output.

    The run's values are in parameters.yaml under their sections, as the config has them, so the crate
    raises no warning about values the workflow lacks."""
    data_path = zarr_folder.parents[1]
    argv = biomero_argv(config_path, data_path, threshold_method="otsu", min_diameter=1, max_diameter=50)
    assert run(argv, spec=spec) == 0
    out = data_path / "data" / "out"
    assert [path.name for path in find_images(out)] == ["blobs_output.tiff"]
    crate = json.loads((out / "ro-crate-metadata.json").read_text())
    ids = {entity["@id"].rstrip("/") for entity in crate["@graph"]}
    input_uri = (data_path / "data" / "in").resolve().as_uri()
    assert {config_path.name, "blobs_output.tiff", input_uri} <= ids
    for entity_id in (config_path.name, "blobs_output.tiff"):
        assert (out / entity_id).exists(), f"{entity_id} not in the crate"
    entities = {entity["@id"]: entity for entity in crate["@graph"]}
    assert entities["parameters.yaml"]["about"] == {"@id": "#run"}
    run_entity = entities["#run"]
    assert run_entity["startTime"] <= run_entity["endTime"] and "name" in run_entity
    software = [e for e in crate["@graph"] if e.get("@type") == "SoftwareApplication"]
    docker = spec.parsed["docker_image"]
    assert any(e.get("name") == f"{docker['org']}/{docker['name']}" and e.get("softwareVersion") == str(docker["tag"])
               for e in software), software
    values = yaml.safe_load((out / "parameters.yaml").read_text())
    assert values["input-args"] == {"threshold_method": "otsu", "min_diameter": 1, "max_diameter": 50}
    assert set(values) == {"input-args", "output-args"}
    # the input is referenced, not copied: BIOMERO would import it again from data/out
    assert not (out / "in").exists()
    assert not list(data_path.glob("data/biomero-work-*"))  # staging removed


def test_staging_folder_is_not_hidden(spec, tmp_path, tiff_folder):
    """Tools skip hidden (dot) folders, e.g. CellProfiler's default Images filter."""
    seen = {}

    def tool(ctx):
        seen["workdir"] = ctx.workdir
        (ctx.outputs["segmented_images"] / "x.txt").write_text("x")

    assert run(["--folder", str(tiff_folder), "--save_dir", str(tmp_path / "out")], spec=spec, tool=tool) == 0
    assert not any(part.startswith(".") for part in seen["workdir"].relative_to(tmp_path).parts)


def test_ro_crate_with_input_copy(spec, config_path, zarr_folder):
    data_path = zarr_folder.parents[1]
    argv = biomero_argv(config_path, data_path, min_diameter=1, max_diameter=50)
    assert run(argv, spec=spec, ro_crate_input="copy") == 0
    assert (data_path / "data" / "out" / "in" / "blobs.ome.zarr").is_dir()


def test_output_zipped_without_compression(spec, config_path, zarr_folder):
    data_path = zarr_folder.parents[1]
    argv = biomero_argv(config_path, data_path, min_diameter=1, max_diameter=50)
    assert run(argv, spec=spec, zip_output=True) == 0
    out = data_path / "data" / "out"
    assert [path.name for path in out.iterdir()] == ["classical_segmentation.zip"]
    with zipfile.ZipFile(out / "classical_segmentation.zip") as archive:
        names = archive.namelist()
        assert "ro-crate-metadata.json" in names
        assert "blobs_output.tiff" in names
        assert {info.compress_type for info in archive.infolist()} == {zipfile.ZIP_STORED}


def test_tiff_in_tiff_out(spec, config_path, tmp_path, tiff_folder):
    data_path = tmp_path / "job"
    (data_path / "data").mkdir(parents=True)
    tiff_folder.rename(data_path / "data" / "in")
    argv = biomero_argv(config_path, data_path, min_diameter=1, max_diameter=50)
    assert run(argv, spec=spec, ro_crate=False) == 0
    out = data_path / "data" / "out"
    labels = tifffile.imread(out / "blobs_output.tiff")
    assert labels.max() == 2


def test_output_format_override(spec, config_path, tmp_path, tiff_folder, monkeypatch):
    data_path = tmp_path / "job"
    (data_path / "data").mkdir(parents=True)
    tiff_folder.rename(data_path / "data" / "in")
    monkeypatch.setenv("BIOMERO_OUTPUT_FORMAT", "zarr")
    argv = biomero_argv(config_path, data_path, min_diameter=1, max_diameter=50)
    assert run(argv, spec=spec, ro_crate=False) == 0
    assert [path.name for path in find_images(data_path / "data" / "out")] == ["blobs_output.ome.zarr"]


def test_tool_failure_returns_nonzero(spec, config_path, tmp_path):
    def failing_tool(ctx):
        raise RuntimeError("boom")
    assert run(["--folder", str(tmp_path), "--save_dir", str(tmp_path / "out")], spec=spec, tool=failing_tool) == 1


def test_tool_function_gets_context(spec, tmp_path, tiff_folder):
    seen = {}

    def tool(ctx):
        seen.update(params=dict(ctx.params), inputs=dict(ctx.inputs), command=ctx.command())
        (ctx.outputs["segmented_images"] / "x.txt").write_text("x")

    out = tmp_path / "out"
    assert run(["--folder", str(tiff_folder), "--save_dir", str(out)], spec=spec, tool=tool, ro_crate=False) == 0
    assert seen["inputs"] == {"input_images": tiff_folder}
    assert seen["command"][seen["command"].index("--save_dir") + 1] == str(out)
    assert (out / "x.txt").exists()


@pytest.mark.parametrize("tool_formats, env", [(["tiff"], None), (None, "ome-tiff")])
def test_tool_formats_narrow_what_the_workflow_accepts(spec, tmp_path, tiff_folder, zarr_folder, monkeypatch,
                                                       tool_formats, env):
    """The workflow accepts OME-Zarr and TIFF, the tool reads TIFF: only the OME-Zarr is converted."""
    spec.inputs["input_images"]["format"] = ["ometiff", "omezarr"]
    if env:
        monkeypatch.setenv("BIOMERO_TOOL_FORMATS", env)
    seen = {}

    def tool(ctx):
        seen[ctx.values.inputs["input_images"].name] = sorted(path.name for path in ctx.inputs["input_images"].iterdir())
        (ctx.outputs["segmented_images"] / "x.txt").write_text("x")

    for folder in (tiff_folder, zarr_folder):
        assert run(["--folder", str(folder), "--save_dir", str(tmp_path / "out" / folder.name)], spec=spec, tool=tool,
                   tool_formats=tool_formats, ro_crate=False) == 0
    assert seen == {"tiff": ["blobs.tif"], "in": ["blobs.ome.tiff"]}


def test_tool_formats_must_be_image_formats(spec, tmp_path, tiff_folder):
    argv = ["--folder", str(tiff_folder), "--save_dir", str(tmp_path / "out")]
    assert run(argv, spec=spec, tool=lambda ctx: None, tool_formats=["png"]) == 1


@pytest.mark.parametrize("policy, inputs, outputs, expected", [
    ("auto", {"zarr"}, {"tiff"}, "tiff"),  # images not in TIFF are converted to it; TIFF stays
    ("auto", {"tiff"}, {"zarr", "tiff"}, "zarr"),
    ("auto", {"zarr"}, set(), None),  # no image format declared: nothing is converted
    ("keep", {"zarr"}, {"tiff"}, None),
    ("tiff", {"zarr"}, {"zarr"}, "tiff"),
])
def test_output_target(policy, inputs, outputs, expected):
    assert output_target(policy, inputs, outputs) == expected


def test_file_outputs_are_kept_in_the_ro_crate(spec, tmp_path, tiff_folder):
    """File outputs (e.g. CSV tables) stay files with their content; fixed in fair-metadata-integration 0.2.1."""
    def tool(ctx):
        (ctx.outputs["segmented_images"] / "Nuclei.csv").write_text("ObjectNumber,Area\n1,42\n")

    out = tmp_path / "out"
    assert run(["--folder", str(tiff_folder), "--save_dir", str(out)], spec=spec, tool=tool) == 0
    assert (out / "Nuclei.csv").read_text() == "ObjectNumber,Area\n1,42\n"
    entities = {entity["@id"]: entity for entity in json.loads((out / "ro-crate-metadata.json").read_text())["@graph"]}
    assert entities["Nuclei.csv"]["@type"] == "File"


def test_output_converted_only_when_the_workflow_requires_it(config_path, python_cli_command, tmp_path, tiff_folder):
    """The same TIFF tool, in a workflow declaring OME-Zarr output: then its labels are converted."""
    spec = BilayersSpec(config_path)
    spec.parsed["exec_function"]["cli_command"] = python_cli_command
    for output in spec.outputs.values():
        output["format"] = ["zarr"]
    out = tmp_path / "out"
    assert run(["--folder", str(tiff_folder), "--save_dir", str(out)], spec=spec, ro_crate=False) == 0
    assert [path.name for path in find_images(out)] == ["blobs_output.ome.zarr"]  # TIFF in, OME-Zarr out
    assert allowed_outputs("auto", {"zarr", "tiff"}, "zarr") == {"zarr", "tiff"}
    assert allowed_outputs("zarr", {"tiff"}, "zarr") == {"zarr"}
