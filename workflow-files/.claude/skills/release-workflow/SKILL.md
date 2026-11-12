---
name: release-workflow
description: Release a new version of this bilayers workflow container — set the version, build and publish its image, tag and create the GitHub release, in the order the Slurm client (BIOMERO) needs. Use when the user asks to release, tag, version, bump or publish this workflow or its image.
---

# Release a new version of this workflow

The Slurm client (BIOMERO) picks a workflow version by its git tag: it reads `config.yaml` at that
tag and pulls the image named in its `docker_image` (`<org>/<name>:<tag>`). So, for every
release, the git tag, `docker_image.tag` in `config.yaml` at that tag, and the image tag in the
registry must be the same version, and the image must exist before the tag does.

The **Release** workflow (`.github/workflows/release.yml`) does all of it, in that order:

1. sets `docker_image.tag` in `config.yaml` to the version and commits it (not pushed yet);
2. validates the config, runs the tests (`pytest -m "not container"`), builds the image from that
   commit and, when its `PUBLISH` is `"true"`, pushes `<org>/<name>:<version>` (and `latest`,
   except for pre-releases) and checks it is in the registry;
3. only then pushes the commit to the default branch, tags it and creates the GitHub release.

If it fails before step 3, nothing is published or released. Never do any of these steps by
hand: do not edit `docker_image.tag`, create tags (`git tag`, the web UI) or releases yourself,
and never move, delete or reuse a released tag (a deployment may already use it).

## Steps

1. **Ready to release?** The default branch has everything that goes into the release, and its
   last CI run passed: `git status` clean, `git fetch && git status` up to date,
   `gh run list --workflow CI --branch <default branch> --limit 1`.
   If the release changes the tool or its parameters, the checks of the `update-workflow` skill
   (including the container test with images) have passed for it.
2. **Choose the version** (`vMAJOR.MINOR.PATCH`). Current: `docker_image.tag` in `config.yaml`
   and `gh release list --limit 5`.
   - MAJOR: users' existing runs break — a parameter or output removed or renamed, its type or
     meaning changed, a required input added.
   - MINOR: new parameters (with defaults), outputs or formats, or a tool upgrade that changes
     results.
   - PATCH: fixes that keep the interface and the results (e.g. a Dockerfile or metadata fix).
   - `vX.Y.Z-rc.N` for a pre-release (not tagged `latest`, marked pre-release).
   Propose the version with the reason and **ask the user to confirm it** before running.
3. **Run it** (the user's explicit request is needed; it publishes):
   `gh workflow run release.yml --ref <default branch> -f version=vX.Y.Z`,
   then `gh run list --workflow release.yml --limit 1` and `gh run watch <run id> --exit-status`.
4. **Check the result:**
   - `gh release view vX.Y.Z`;
   - `git fetch --tags && git show vX.Y.Z:config.yaml` has `tag: "vX.Y.Z"` under `docker_image`;
   - with `PUBLISH: "true"`: `docker buildx imagetools inspect <org>/<name>:vX.Y.Z`. With
     `"false"` the image was only built: say that the Slurm client cannot pull it until it is
     published.
   - `git pull` locally (the workflow added the version commit).

Report the version, the release URL and the image reference.

## When it fails

- **Version format / already tagged / not on the default branch**: fix the input and run again.
- **Validate and test**: fix it in a normal commit (CI must pass), then release again.
- **Registry credentials missing / login fails**: the user adds the repository (or organization)
  Actions secrets `REGISTRY_USERNAME` and `REGISTRY_TOKEN` (or `DOCKERHUB_USERNAME` and
  `DOCKERHUB_TOKEN`) with push rights to `docker_image.org`. Never ask for, read or handle the
  credentials yourself.
- **Push of the version commit rejected**: the default branch moved during the run, or it is
  protected. The image may already be pushed, but no tag or release exists, so running again with
  the same version (after the branch is settled, or after allowing GitHub Actions to bypass the
  protection) safely replaces it.
- **Tag pushed but the release step failed**: the tag is valid; create only the release:
  `gh release create vX.Y.Z --verify-tag --generate-notes`.

To publish images from now on (`PUBLISH` was `"false"`), set `PUBLISH: "true"` in `release.yml`,
make sure `docker_image.org` is the registry namespace (registry host included for any registry
but Docker Hub, e.g. `quay.io/<org>`), and have the user add the secrets; then release a new version.

Registering the release in BIOMERO is separate and only on request; see the template's
[biomero-contract.md](https://github.com/NL-BioImaging/bilayers-auto-container/blob/main/.claude/skills/create-bilayers-container/references/biomero-contract.md#registering-a-workflow-optional).
