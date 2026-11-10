"""A stand-in Fractal task: ``--args-json``/``--out-json`` like fractal_task_tools.run_fractal_task.

- init (``zarr_urls`` + ``zarr_dir``, ``mode=init``): returns a parallelization list
- parallel/compute (``zarr_url``): records its arguments in the image; with ``suffix`` it adds a
  new image ``<image stem><suffix>.zarr`` next to it (``raw_suffix``: ``<image name><suffix>``) and reports it (``image_list_updates``)
- non-parallel (``zarr_urls`` + ``zarr_dir``): records its arguments in ``zarr_dir``
"""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--args-json", required=True)
parser.add_argument("--out-json", required=True)
options = parser.parse_args()
args = json.loads(Path(options.args_json).read_text())
out = {}
if "zarr_url" in args:
    image = Path(args["zarr_url"])
    (image / "task-args.json").write_text(json.dumps(args))
    if args.get("suffix") or args.get("raw_suffix"):
        if args.get("raw_suffix"):  # like most Fractal tasks: <zarr_url><suffix>
            new = image.with_name(image.name + args["raw_suffix"])
        else:
            new = image.with_name(image.name.removesuffix(".zarr") + args["suffix"] + ".zarr")
        new.mkdir()
        (new / "task-args.json").write_text(json.dumps(args))
        out = {"image_list_updates": [{"zarr_url": new.as_posix(), "origin": args["zarr_url"]}]}
        (image.parent / ".zattrs.lock").touch()  # as fractal-tasks-core does for the well
elif args.get("mode") == "init":
    out = {"parallelization_list": [{"zarr_url": url, "init_args": {"index": index}}
                                    for index, url in enumerate(args["zarr_urls"])]}
else:
    (Path(args["zarr_dir"]) / "task-args.json").write_text(json.dumps(args))
if out:
    Path(options.out_json).write_text(json.dumps(out))
