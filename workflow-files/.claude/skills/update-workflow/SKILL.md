---
name: update-workflow
description: Change this bilayers workflow container — its parameters, inputs/outputs, the tool version, the Dockerfile or the wrapper.py tool hook — and verify it, including running the container on test images, before it is released. Use when the user asks to modify, fix, upgrade or test this workflow.
---

# Change and verify this workflow

This repository was generated from
[bilayers-auto-container](https://github.com/NL-BioImaging/bilayers-auto-container) (see
`README.md` and `generated-notes.md`). It holds only:

- `config.yaml` — the bilayers spec: inputs, outputs, parameters, the tool command
  (`exec_function.cli_command`), citations and `docker_image`;
- `wrapper.py` — the image's entrypoint: `bilayers_auto.run(...)` with the tool hook. It resolves
  the arguments, converts images, runs the tool and writes the RO-Crate; do not reimplement that;
- `Dockerfile` — the template's, with the tool layer between `# >>> tool layer` and
  `# <<< tool layer`;
- `run.sh` — runs the image on a folder of images without BIOMERO or Slurm (Docker, Podman or
  Apptainer); it reads the image from `docker_image` and has the input and output `cli_tag`s;
- `tests/` (+ `pytest.ini`), `requirements-dev.txt`, CI and the Release workflow.

`bilayers_auto` itself is installed from GitHub into the image; fix generic behaviour (conversion,
RO-Crate, arguments) in the template, not here. The template's
[container contract](https://github.com/NL-BioImaging/bilayers-auto-container/blob/main/.claude/skills/create-bilayers-container/references/container-contract.md)
and [BIOMERO contract](https://github.com/NL-BioImaging/bilayers-auto-container/blob/main/.claude/skills/create-bilayers-container/references/biomero-contract.md)
describe what the Slurm client sends and expects; read them before changing inputs, outputs or
parameters.

## Rules

- **Never edit `docker_image.tag`**; the Release workflow sets it (`release-workflow` skill).
- Parameters use bilayers types only (`integer`, `float`, `checkbox`, `dropdown`, `radio`,
  `textbox`), real defaults, clear `label`/`description`; `cli_tag`s are the tool's own flags.
  Removing or renaming a parameter or output breaks existing users' runs (a major version).
- Pin the tool and its dependencies (tags/commits, `requirements-tool.txt`), so each release
  reproduces the same results. An upgrade that changes results is noted in `generated-notes.md`.
- The image must keep working under Singularity/Apptainer: entrypoint `wrapper.py`, fully
  qualified `FROM` images, no `USER` or fixed UIDs, writes only to the given folders and `/tmp`,
  no network, `HOME` or shell needed at run time.
- No `descriptor.json`/`descriptor.yaml` (the Slurm client would prefer it over `config.yaml`).
- `.dockerignore` keeps `.claude`, `.git`, `.github` and `tests` out of the image.
- Keep `run.sh` (its input/output flags), the README's **Run it yourself** section (flags,
  parameter table, `params.yaml` example) and the `workflow-outputs` skill (what each output
  file holds) in step with `config.yaml` and what the container actually writes.

## Verify

Install the test tools in a venv: `pip install -r requirements-dev.txt`. Docker must be running
(`docker info`). All of these must pass before the change is done:

1. `bilayers-auto validate config.yaml --slurm`
2. `bilayers-auto command config.yaml -- --<flag>=<value> ...` with the arguments the Slurm client
   would send: the tool command is what the tool expects.
3. `pytest -m "not container"`
4. The container on test images: `pytest -m container` (it uses the test data in `tests/`), or
   `bilayers-auto container-test config.yaml` with `--sample <in> --expected <expected>` (or the
   `--synthetic ...` settings in `tests/test_workflow.py`), `--param name=value` as needed and
   `--keep <folder>` to inspect `<job>/data/out`. Outputs must match the expected results; find
   out why before changing a tolerance, and never loosen one only to pass.
5. `./run.sh <test input> <scratch output>` on that image (Git Bash on Windows) runs and writes
   the same outputs.

Testing with images means running this container, not deploying BIOMERO, OMERO or Slurm (only
on the user's request). Then update `generated-notes.md` (what changed, the test data and
results, what is still unverified), commit on the default branch, push, and check the CI run.
Releasing is a separate step, only when the user asks (`release-workflow` skill).
