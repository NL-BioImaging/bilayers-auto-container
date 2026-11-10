import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from biomero_auto import fractal, galaxy
from biomero_auto.cli import main

DATA = Path(__file__).parent / "data"
TASK = [sys.executable, str(DATA / "fake_fractal_task.py")]

MANIFEST = {
    "manifest_version": "2",
    "task_list": [{
        "name": "Stitching Task", "type": "parallel", "executable_parallel": "stitching_task.py", "tags": ["2D", "3D"],
        "docs_info": "Stitches FOVs.",
        "args_schema_parallel": {
            "$defs": {
                "Method": {"enum": ["a", "b"], "type": "string"},
                "Channel": {"type": "object", "properties": {"wavelength_id": {"type": "string"},
                                                             "label": {"type": "string"}}},
            },
            "properties": {
                "zarr_url": {"type": "string"},
                "channel": {"$ref": "#/$defs/Channel", "description": "Channel for registration"},
                "overwrite_input": {"type": "boolean", "default": False},
                "level": {"type": "integer", "default": 0},
                "sigma": {"anyOf": [{"type": "number"}, {"type": "null"}], "default": None},
                "method": {"allOf": [{"$ref": "#/$defs/Method"}], "default": "b"},
                "sizes": {"type": "array", "items": {"type": "integer"}, "default": [1, 2]},
            },
            "required": ["zarr_url", "channel"], "type": "object",
        },
    }],
}


def test_fractal_manifest_draft(tmp_path):
    config = fractal.bilayers_from_manifest(MANIFEST, cli_command="python -m pkg.stitching_task")
    params = {p["name"]: p for p in config["parameters"]}
    assert list(params) == ["channel__wavelength_id", "channel__label", "overwrite_input", "level", "sigma",
                            "method", "sizes"]
    assert params["overwrite_input"]["type"] == "checkbox" and params["level"]["type"] == "integer"
    assert params["sigma"]["type"] == "float" and params["sigma"]["default"] == ""
    assert params["method"]["type"] == "dropdown" and params["method"]["default"] == "b"
    assert params["sizes"]["type"] == "textbox" and params["sizes"]["default"] == "[1, 2]"
    assert config["inputs"][0]["format"] == ["zarr"] and config["inputs"][0]["depth"] is True
    assert 'kind="parallel"' in fractal.wrapper_hint(MANIFEST)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    assert main(["validate", str(path)]) == 0


def test_task_args_nest_and_drop_empty_values():
    params = {"channel__label": "DAPI", "channel__wavelength_id": "", "level": 0, "sigma": None, "sizes": "[1, 2]"}
    assert fractal.task_args(params) == {"channel": {"label": "DAPI"}, "level": 0, "sizes": [1, 2]}


def _ctx(tmp_path, params, images=("a.zarr", "b.zarr")):
    inputs = tmp_path / "in"
    for name in images:
        (inputs / name).mkdir(parents=True)
        (inputs / name / ".zattrs").write_text("{}")
    (tmp_path / "work").mkdir()
    spec = SimpleNamespace(output_dir_params=lambda: [])
    return SimpleNamespace(spec=spec, params=params, inputs={"in": inputs}, outputs={"out": tmp_path / "out"},
                           workdir=tmp_path / "work")


def test_parallel_task_keeps_the_new_images(tmp_path):
    ctx = _ctx(tmp_path, {"channel__label": "DAPI", "channel__wavelength_id": "", "suffix": "_fused"})
    fractal.fractal_tool(TASK)(ctx)
    out = tmp_path / "out"
    # copies and the well lock file removed
    assert sorted(path.name for path in out.iterdir()) == ["a_fused.zarr", "b_fused.zarr"]
    args = json.loads((out / "a_fused.zarr" / "task-args.json").read_text())
    assert args["channel"] == {"label": "DAPI"} and args["zarr_url"] == (out / "a.zarr").resolve().as_posix()
    assert (tmp_path / "in" / "a.zarr").exists() and not (tmp_path / "in" / "a.zarr" / "task-args.json").exists()


def test_parallel_task_in_place_keeps_the_changed_copies(tmp_path):
    fractal.fractal_tool(TASK)(_ctx(tmp_path, {"level": 1}))
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == ["a.zarr", "b.zarr"]
    assert json.loads((tmp_path / "out" / "a.zarr" / "task-args.json").read_text())["level"] == 1


def test_new_images_get_a_zarr_name_and_channels_a_wavelength_id(tmp_path):
    ctx = _ctx(tmp_path, {"raw_suffix": "_fused", "grid": 2}, images=("a.ome.zarr",))
    (tmp_path / "in" / "a.ome.zarr" / ".zattrs").write_text(json.dumps({"omero": {"channels": [{"label": "DAPI"}, {}]}}))
    prepared = []
    fractal.fractal_tool(TASK, exclude=["grid"], prepare=lambda ctx, images: prepared.extend(images))(ctx)
    out = tmp_path / "out"
    assert [path.name for path in out.iterdir()] == ["a_fused.ome.zarr"]
    assert [path.name for path in prepared] == ["a.ome.zarr"]
    args = json.loads((out / "a_fused.ome.zarr" / "task-args.json").read_text())
    assert "grid" not in args
    zarr_url = Path(args["zarr_url"])  # the copy the task ran on (removed afterwards)
    assert zarr_url.name == "a.ome.zarr" and not zarr_url.exists()


def test_add_wavelength_ids(tmp_path):
    (tmp_path / ".zattrs").write_text(json.dumps({"omero": {"channels": [{"label": "DAPI"}, {"wavelength_id": "X"}]}}))
    fractal.add_wavelength_ids(tmp_path)
    channels = json.loads((tmp_path / ".zattrs").read_text())["omero"]["channels"]
    assert [channel["wavelength_id"] for channel in channels] == ["C01", "X"]


def test_non_parallel_and_compound_tasks(tmp_path):
    fractal.fractal_tool(TASK, kind="non_parallel")(_ctx(tmp_path / "np", {"level": 2}))
    args = json.loads((tmp_path / "np" / "out" / "task-args.json").read_text())
    assert len(args["zarr_urls"]) == 2 and args["zarr_dir"] == (tmp_path / "np" / "out").resolve().as_posix()

    tool = fractal.fractal_tool(TASK, kind="compound", init_command=TASK, init_params=["mode"])
    tool(_ctx(tmp_path / "c", {"mode": "init", "level": 3}))
    args = json.loads((tmp_path / "c" / "out" / "b.zarr" / "task-args.json").read_text())
    assert args["init_args"] == {"index": 1} and args["level"] == 3 and "mode" not in args


def test_fractal_task_failure_is_raised(tmp_path):
    with pytest.raises(Exception):
        fractal.fractal_tool([sys.executable, "-c", "raise SystemExit(1)"])(_ctx(tmp_path, {}))
    with pytest.raises(ValueError):
        fractal.fractal_tool(TASK, kind="compound")


TOOL_XML = """<tool id="ip_threshold" name="Threshold image" version="@TOOL_VERSION@+galaxy0">
    <macros>
        <token name="@TOOL_VERSION@">0.25.2</token>
        <xml name="inputs/offset"><param name="offset" type="float" value="0" label="Offset"/></xml>
    </macros>
    <command><![CDATA[python '$__tool_directory__/auto_threshold.py' --input '$input' --output ./out.tiff]]></command>
    <inputs>
        <param name="input" type="data" format="tiff,zarr,png" label="Input image"/>
        <conditional name="th_method">
            <param name="method_id" type="select" label="Method">
                <option value="manual">Manual</option>
                <option value="otsu" selected="True">Otsu</option>
            </param>
            <when value="manual"><param name="threshold1" type="float" value="0" label="Threshold"/></when>
            <when value="otsu"><expand macro="inputs/offset"/></when>
        </conditional>
        <section name="advanced"><param name="invert" type="boolean" checked="false" label="Invert"/></section>
        <expand macro="imported/elsewhere"/>
    </inputs>
    <outputs><data format="tiff" name="output" from_work_dir="out.tiff"/></outputs>
</tool>"""


def test_galaxy_tool_draft():
    config = galaxy.bilayers_from_tool(TOOL_XML)
    params = {p["name"]: p for p in config["parameters"]}
    assert list(params) == ["th_method__method_id", "th_method__threshold1", "th_method__offset", "advanced__invert"]
    assert params["th_method__method_id"]["default"] == "otsu"
    assert "used when method_id = otsu" in params["th_method__offset"]["description"]
    assert config["inputs"][0]["format"] == ["png", "tiff", "zarr"] and config["outputs"][0]["format"] == ["tiff"]
    assert config["exec_function"]["cli_command"] == "python /app/galaxy/auto_threshold.py"
    assert galaxy.unexpanded(TOOL_XML) == ["macro imported/elsewhere (from an imported macro file)"]


WORKFLOW = {
    "name": "Nuclei", "license": "MIT", "creator": [{"name": "A. Person"}], "annotation": "Segments nuclei.",
    "steps": {
        "0": {"id": 0, "type": "data_input", "label": "input_image", "tool_state": "{}", "input_connections": {}},
        "1": {"id": 1, "type": "parameter_input", "label": "Sigma",
              "tool_state": json.dumps({"parameter_type": "float", "default": 2.5}), "input_connections": {}},
        "2": {"id": 2, "type": "tool", "label": None, "tool_id": "toolshed.g2.bx.psu.edu/repos/imgteam/2d_auto_threshold/ip_threshold/0.25.2",
              "tool_version": "0.25.2", "tool_shed_repository": {"owner": "imgteam", "name": "2d_auto_threshold",
                                                                  "changeset_revision": "abc"},
              "tool_state": json.dumps({"input": {"__class__": "ConnectedValue"}, "chromInfo": "x", "dark_bg": "true",
                                        "th_method": {"method_id": "otsu", "__current_case__": 1, "offset": "0.0"},
                                        "block_size": "5"}),
              "input_connections": {"input": {"id": 0, "output_name": "output"}}},
    },
}


def test_galaxy_workflow_draft(tmp_path):
    config = galaxy.bilayers_from_workflow(WORKFLOW)
    params = {p["name"]: (p["type"], p["default"]) for p in config["parameters"]}
    assert params == {"sigma": ("float", 2.5), "s2d_auto_threshold__dark_bg": ("checkbox", True),
                      "s2d_auto_threshold__th_method__method_id": ("textbox", "otsu"),
                      "s2d_auto_threshold__th_method__offset": ("float", 0.0),
                      "s2d_auto_threshold__block_size": ("integer", 5)}
    assert config["citations"][0]["license"] == "MIT" and "A. Person" in config["citations"][0]["description"]
    [_, _, step] = galaxy.workflow_steps(WORKFLOW)
    assert step["repository"] == "https://toolshed.g2.bx.psu.edu/repos/imgteam/2d_auto_threshold"
    assert step["inputs_from"] == [0]
    assert [p["name"] for p in galaxy.bilayers_from_workflow(WORKFLOW, step_params=False)["parameters"]] == ["sigma"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    assert main(["validate", str(path), "--biomero"]) == 0


def test_cli_drafts(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps(MANIFEST))
    (tmp_path / "tool.xml").write_text(TOOL_XML)
    (tmp_path / "wf.ga").write_text(json.dumps(WORKFLOW))
    for args in (["from-fractal", str(tmp_path / "manifest.json")], ["from-galaxy", str(tmp_path / "tool.xml")],
                 ["from-galaxy", str(tmp_path / "wf.ga")]):
        assert main([*args, "-o", str(tmp_path / "config.yaml")]) == 0
        assert main(["validate", str(tmp_path / "config.yaml"), "--biomero"]) == 0


def test_galaxy_workflow_draft_with_tool_wrappers():
    """With the wrappers, step values get the tool's types and options; stale values are reported."""
    tools = {"ip_threshold": TOOL_XML}
    params = {p["name"]: p for p in galaxy.bilayers_from_workflow(WORKFLOW, tools=tools)["parameters"]}
    method = params["s2d_auto_threshold__th_method__method_id"]
    assert method["type"] == "dropdown" and method["default"] == "otsu"
    assert [option["value"] for option in method["options"]] == ["manual", "otsu"]
    assert params["s2d_auto_threshold__th_method__offset"]["default"] == 0.0
    assert "s2d_auto_threshold__dark_bg" not in params and "s2d_auto_threshold__block_size" not in params
    assert galaxy.stale_state(WORKFLOW, tools) == ["step 2 s2d_auto_threshold: dark_bg",
                                                   "step 2 s2d_auto_threshold: block_size"]
    assert galaxy.tool_xml_id(WORKFLOW["steps"]["2"]) == "ip_threshold"


def test_cli_galaxy_workflow_with_tool_wrappers(tmp_path, capsys):
    (tmp_path / "wf.ga").write_text(json.dumps(WORKFLOW))
    (tmp_path / "tool.xml").write_text(TOOL_XML)
    assert main(["from-galaxy", str(tmp_path / "wf.ga"), "--tool", str(tmp_path / "tool.xml"),
                 "-o", str(tmp_path / "config.yaml")]) == 0
    err = capsys.readouterr().err
    assert "stale tool_state" in err and "dark_bg" in err  # not a parameter of the pinned wrapper
    names = [p["name"] for p in yaml.safe_load((tmp_path / "config.yaml").read_text())["parameters"]]
    assert not any(name.endswith("dark_bg") for name in names)
    assert main(["validate", str(tmp_path / "config.yaml"), "--biomero"]) == 0
