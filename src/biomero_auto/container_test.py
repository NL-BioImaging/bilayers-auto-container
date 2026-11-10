"""Build the workflow image and run it on a sample job the way BIOMERO would (Docker; Podman optional)."""
from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Sequence

import yaml

from .compare import compare_outputs, unpack
from .contract import biomero_argv, write_sample_input
from .fair import CRATE_METADATA
from .sample_data import Synthetic, write_synthetic

logger = logging.getLogger(__name__)

RUNTIMES = ("docker", "podman")
CONTAINER_DATA = PurePosixPath("/")  # BIOMERO's $DATA_PATH inside the container: /data/in, /data/out


def detect_runtime(runtime: str = "docker") -> str:
    """Check the container engine (Docker by default) responds."""
    if runtime not in RUNTIMES:
        raise ValueError(f"runtime must be one of {RUNTIMES}")
    if shutil.which(runtime) and subprocess.run([runtime, "info"], capture_output=True).returncode == 0:
        return runtime
    raise RuntimeError(f"{runtime} is not running; start Docker Desktop (or pass the runtime you use)")


def image_name(config: Path) -> tuple[str, str]:
    """``(org/name:tag, platform)`` from the config's docker_image."""
    docker = yaml.safe_load(config.read_text())["docker_image"]
    return f"{docker['org']}/{docker['name']}:{docker['tag']}", docker.get("platform", "linux/amd64")


def build_context(config: Path) -> Path:
    """The build context: the template root (with pyproject.toml) holding the config, as for the
    examples; otherwise the config's own folder, as in a workflow repository."""
    for folder in [config.resolve().parent, *config.resolve().parents]:
        if (folder / "pyproject.toml").exists():
            return folder
    return config.resolve().parent


def build_command(runtime: str, context: Path, image: str, platform: str, config: Path | None = None) -> list[str]:
    """Build with the Dockerfile next to the config (else the root one), passing its folder as WORKFLOW_DIR."""
    command = [runtime, "build", "--platform", platform, "-t", image]
    if config is not None:
        workflow_dir = config.resolve().parent
        dockerfile = workflow_dir / "Dockerfile" if (workflow_dir / "Dockerfile").exists() else context / "Dockerfile"
        command += ["-f", str(dockerfile), "--build-arg",
                    f"WORKFLOW_DIR={workflow_dir.relative_to(context.resolve()).as_posix()}"]
    return command + [str(context)]


def run_command(runtime: str, image: str, platform: str, data_folder: Path, args: Sequence[str]) -> list[str]:
    volume = f"{data_folder.resolve()}:/data" + (":Z" if runtime == "podman" else "")
    return [runtime, "run", "--rm", "--platform", platform, "-v", volume, image, *args]


def container_test(config: Path, *, context: Path | None = None, runtime: str = "docker",
                   sample: Path | None = None, params: dict | None = None, build: bool = True,
                   keep: Path | None = None, expected: Path | None = None, synthetic: Synthetic | None = None,
                   min_dice: float = 0.9, rtol: float = 1e-3) -> list[Path]:
    """Build the image, run it on a BIOMERO-style job and check the results; returns the outputs.

    The input is ``sample`` (a folder of images), else ``synthetic`` images (whose ground-truth
    labels become the expected output unless ``expected`` is given), else a small OME-Zarr.
    With expected outputs, every one must match an output (see ``compare.compare_outputs``).
    """
    config = Path(config)
    context = Path(context) if context else build_context(config)
    runtime = detect_runtime(runtime)
    image, platform = image_name(config)
    if build:
        _run(build_command(runtime, context, image, platform, config))
    job = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="biomero-job-"))
    data = job / "data"
    if (data / "in").exists():
        shutil.rmtree(data / "in")
    if sample:
        shutil.copytree(sample, data / "in")
    elif synthetic:
        if expected is None:
            expected = job / "expected"
            if expected.exists():
                shutil.rmtree(expected)
        write_synthetic(data / "in", expected, synthetic)
    else:
        write_sample_input(data / "in")
    (data / "out").mkdir(parents=True, exist_ok=True)
    args = biomero_argv(config, CONTAINER_DATA, **(params or {}))
    _run(run_command(runtime, image, platform, data, args))
    outputs = sorted((data / "out").iterdir())
    if not outputs:
        raise RuntimeError(f"The workflow produced no files in {data / 'out'} (BIOMERO fails such jobs)")
    zips = [path for path in outputs if path.suffix == ".zip"]
    if zips:
        with zipfile.ZipFile(zips[0]) as archive:
            has_crate = CRATE_METADATA in archive.namelist()
    else:
        has_crate = (data / "out" / CRATE_METADATA).exists()
    if not has_crate:
        logger.warning("No %s in the output", CRATE_METADATA)
    logger.info("Outputs in %s: %s", data / "out", [path.name for path in outputs])
    if expected is not None:
        results = compare_outputs(unpack(data / "out", job), Path(expected), min_dice=min_dice, rtol=rtol)
        for result in results:
            logger.info("%s", result)
        failed = [result for result in results if not result.ok]
        if failed:
            raise RuntimeError(f"{len(failed)} of {len(results)} expected outputs do not match:\n"
                               + "\n".join(str(result) for result in failed))
    return outputs


def _run(argv: Sequence[str]) -> None:
    logger.info("$ %s", shlex.join(argv))
    subprocess.run(argv, check=True)
