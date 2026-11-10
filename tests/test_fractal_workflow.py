"""Fractal workflows: the JSON forms, drafting a config and step plan, and running steps like Fractal."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

from bilayers_auto import fractal
from bilayers_auto.cli import main

TASK = [sys.executable, str(Path(__file__).parent / "data" / "fake_fractal_task.py")]

MANIFEST = {"manifest_version": "2", "task_list": [
    {"name": "Convert Cellvoyager to OME-Zarr", "type": "converter_compound",
     "executable_non_parallel": "tasks/cellvoyager_init.py", "executable_parallel": "tasks/cellvoyager_compute.py",
     "args_schema_non_parallel": {"properties": {"zarr_dir": {"type": "string"}, "image_dirs": {"type": "array"}}}},
    {"name": "Import OME-Zarr", "type": "converter_non_parallel", "executable_non_parallel": "tasks/import_ome_zarr.py",
     "args_schema_non_parallel": {"properties": {"zarr_dir": {"type": "string"}, "zarr_name": {"type": "string"},
                                                 "add_image_roi_table": {"type": "boolean", "default": True}}}},
    {"name": "Project Image (HCS Plate)", "type": "compound", "executable_non_parallel": "tasks/init_projection.py",
     "executable_parallel": "tasks/projection_hcs.py", "input_types": {"is_3D": True}, "output_types": {"is_3D": False},
     "args_schema_non_parallel": {"properties": {"method": {"type": "string"}}}},
    {"name": "Project Image", "type": "parallel", "executable_parallel": "tasks/projection.py",
     "input_types": {"is_3D": True}, "output_types": {"is_3D": False}, "tags": ["3D"],
     "args_schema_parallel": {"$defs": {"Method": {"enum": ["mip", "mean"], "type": "string"}},
                              "properties": {"zarr_url": {"type": "string"},
                                             "method": {"allOf": [{"$ref": "#/$defs/Method"}], "title": "Method"}}}},
    {"name": "Threshold Segmentation", "type": "parallel", "executable_parallel": "tasks/threshold.py",
     "args_schema_parallel": {"properties": {
         "zarr_url": {"type": "string"},
         "channel": {"type": "object", "properties": {"identifier": {"type": "string", "title": "Identifier"}}},
         "segmentation_method": {"anyOf": [{"type": "object", "properties": {"method": {"const": "Otsu"}}},
                                           {"type": "object", "properties": {"threshold": {"type": "integer"}}}]}}}},
]}


def _entry(name, np=None, p=None, filters=None):
    return {"task": {"pkg_name": "fractal-tasks-core", "version": "2.0.0", "name": name}, "args_non_parallel": np,
            "args_parallel": p, "type_filters": filters or {}, "meta_non_parallel": {}, "meta_parallel": {}}


WORKFLOW = {"name": "Basic threshold", "description": "Projection and thresholding.", "task_list": [
    _entry("Convert Cellvoyager to OME-Zarr", np={"image_dirs": ["/raw"]}),
    _entry("Import OME-Zarr", np={"add_image_roi_table": True}),
    _entry("Project Image (HCS Plate)", np={"method": "mip"}),
    _entry("Project Image", p={"method": "mip"}),
    _entry("Threshold Segmentation", p={"channel": {"identifier": "DAPI"},
                                        "segmentation_method": {"method": "Simple Threshold", "threshold": 200},
                                        "features": [{"type": "Shape"}]}),
]}
MANIFESTS = {"fractal-tasks-core==2.0.0": (MANIFEST, "fractal_tasks_core")}


def test_workflow_forms_load_alike():
    export = fractal.load_workflow(WORKFLOW)
    template = fractal.load_workflow({"name": "T", "version": 1, "description": "d", "data": WORKFLOW})
    assert export["steps"] == template["steps"] and fractal.is_workflow({"data": WORKFLOW})
    legacy = fractal.load_workflow({"name": "mip", "task_list": [{
        "task": {"source": "pip_remote:fractal_tasks_core:1.1.0:fractal-tasks::maximum_intensity_projection_hcs_plate"},
        "input_filters": {"attributes": {}, "types": {"is_3D": True}}, "args_non_parallel": {"suffix": "mip"},
        "args_parallel": None, "meta_non_parallel": {}, "meta_parallel": {}}]})
    [step] = legacy["steps"]
    assert (step["pkg_name"], step["version"], step["task_name"]) == (
        "fractal-tasks-core", "1.1.0", "maximum_intensity_projection_hcs_plate")
    assert step["type_filters"] == {"is_3D": True}
    assert not fractal.is_workflow(MANIFEST)


def test_workflow_plan_and_parameters(tmp_path):
    config, plan = fractal.bilayers_from_workflow(fractal.load_workflow(WORKFLOW), MANIFESTS)
    steps = {step["id"]: step for step in plan}
    assert "raw microscope data" in steps["convert_cellvoyager_to_ome_zarr"]["skip"]
    assert steps["import_ome_zarr"]["kind"] == "converter_non_parallel" and steps["import_ome_zarr"]["imports_ome_zarr"]
    assert "HCS plates" in steps["project_image_hcs_plate"]["note"]
    assert steps["project_image"]["command_parallel"] == ["{python}", "-m", "fractal_tasks_core.tasks.projection"]
    assert "add type_filters" in steps["threshold_segmentation"]["note"]  # after the projection changed is_3D
    params = {p["name"]: p for p in config["parameters"]}
    assert "convert_cellvoyager_to_ome_zarr__image_dirs" not in params
    assert params["project_image__method"]["type"] == "dropdown" and params["project_image__method"]["default"] == "mip"
    assert params["threshold_segmentation__channel__identifier"]["label"] == "threshold_segmentation: channel / Identifier"
    assert params["threshold_segmentation__segmentation_method__threshold"]["type"] == "integer"  # a union: by value
    assert params["threshold_segmentation__features"]["default"] == '[{"type": "Shape"}]'
    assert params["threshold_segmentation__channel__identifier"]["section_id"] == "threshold_segmentation"
    assert config["inputs"][0]["depth"] is True
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    assert main(["validate", str(path), "--biomero"]) == 0


def test_cli_drafts_a_workflow_with_given_manifests(tmp_path):
    (tmp_path / "wf.json").write_text(json.dumps(WORKFLOW))
    package = tmp_path / "fractal_tasks_core"
    package.mkdir()
    (package / "__FRACTAL_MANIFEST__.json").write_text(json.dumps(MANIFEST))
    assert main(["from-fractal", str(tmp_path / "wf.json"), "-o", str(tmp_path / "config.yaml"),
                 "--manifest", f"fractal-tasks-core==2.0.0={package / '__FRACTAL_MANIFEST__.json'}"]) == 0
    steps = json.loads((tmp_path / "fractal_steps.json").read_text())
    assert [step["id"] for step in steps] == ["import_ome_zarr", "project_image_hcs_plate", "project_image",
                                              "threshold_segmentation"]
    assert main(["validate", str(tmp_path / "config.yaml"), "--biomero"]) == 0


def _ctx(tmp_path, params, z=2):
    image = tmp_path / "in" / "a.zarr"
    (image / "0").mkdir(parents=True)
    (image / ".zattrs").write_text(json.dumps({"multiscales": [{"axes": ["c", "z", "y", "x"],
                                                                 "datasets": [{"path": "0"}]}]}))
    (image / "0" / ".zarray").write_text(json.dumps({"shape": [1, z, 8, 8]}))
    (tmp_path / "work").mkdir()
    return SimpleNamespace(spec=SimpleNamespace(output_dir_params=lambda: []), params=params,
                           inputs={"in": tmp_path / "in"}, outputs={"out": tmp_path / "out"}, workdir=tmp_path / "work")


def test_image_types_from_the_image(tmp_path):
    assert fractal.image_types(_ctx(tmp_path / "3d", {}).inputs["in"] / "a.zarr") == {"is_3D": True}
    assert fractal.image_types(_ctx(tmp_path / "2d", {}, z=1).inputs["in"] / "a.zarr") == {"is_3D": False}


def test_steps_run_like_a_fractal_workflow(tmp_path):
    steps = [
        {"id": "project", "kind": "parallel", "command_parallel": TASK, "args_parallel": {"raw_suffix": "_mip"},
         "input_types": {"is_3D": True}, "output_types": {"is_3D": False}},
        {"id": "segment", "kind": "parallel", "command_parallel": TASK, "type_filters": {"is_3D": False},
         "args_parallel": {"channel": {"identifier": "DAPI"}, "level": 0}},
        {"id": "only_3d", "kind": "parallel", "command_parallel": TASK, "type_filters": {"is_3D": True},
         "args_parallel": {"marker": "3d"}},
    ]
    ctx = _ctx(tmp_path, {"segment__channel__identifier": "LAMIN", "segment__level": 2, "project__other": ""})
    fractal.fractal_workflow(steps, task_python=sys.executable)(ctx)
    out = tmp_path / "out"
    assert sorted(path.name for path in out.iterdir()) == ["a_mip.zarr"]  # the copy is removed, the new image renamed
    args = json.loads((out / "a_mip.zarr" / "task-args.json").read_text())
    # the projection's output only went to the segmentation; the user's values override the workflow's
    assert args["channel"] == {"identifier": "LAMIN"} and args["level"] == 2 and "marker" not in args
    calls = sorted(path.name for path in (tmp_path / "work" / "fractal").glob("*.args.json"))
    assert calls == ["only_3d-0.args.json", "project-0.args.json", "segment-0.args.json"]
    only_3d = json.loads((tmp_path / "work" / "fractal" / "only_3d-0.args.json").read_text())
    assert only_3d["zarr_url"].endswith("/a.zarr")  # the input copy stays 3D: the projection made a new image


def test_a_2d_image_skips_steps_for_3d(tmp_path):
    steps = [{"id": "project", "kind": "parallel", "command_parallel": TASK, "args_parallel": {"raw_suffix": "_mip"},
              "input_types": {"is_3D": True}, "output_types": {"is_3D": False}}]
    fractal.fractal_workflow(steps, task_python=sys.executable)(_ctx(tmp_path, {}, z=1))
    assert [path.name for path in (tmp_path / "out").iterdir()] == ["a.zarr"]  # nothing to project: input kept


def test_null_out_json_means_no_updates(tmp_path):
    script = tmp_path / "null_task.py"
    script.write_text("import sys, pathlib; pathlib.Path(sys.argv[sys.argv.index('--out-json') + 1]).write_text('null')")
    assert fractal._call([sys.executable, str(script)], {}, tmp_path, "call") == {}
