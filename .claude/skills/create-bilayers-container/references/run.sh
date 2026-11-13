#!/usr/bin/env bash
# Run the @WORKFLOW_NAME@ container on a folder of images, without BIOMERO or Slurm.
#
# Usage: ./run.sh <input folder> <output folder> [--<param>=<value> ...] [--params <params.yaml>]
#
#   RUNTIME  docker (default), podman, apptainer or singularity
#   IMAGE    image to run (default: docker_image in config.yaml, <org>/<name>:<tag>);
#            with apptainer/singularity also a .sif file
#   OUTPUT_FORMAT, ZIP_OUTPUT, RO_CRATE, RO_CRATE_INPUT, CONVERT_INPUTS, TOOL_FORMATS,
#   OMEZARR_VERSION  passed on to the container when set
#
# Parameters left out take their defaults from config.yaml. The output folder is created if
# needed; staging data goes to a temporary folder that is removed afterwards.
set -euo pipefail

RUNTIME="${RUNTIME:-docker}"
IN_FLAG="@IN_FLAG@"
OUT_FLAG="@OUT_FLAG@"

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
case "${1:-}" in -h|--help) usage ;; esac
[ $# -ge 2 ] || usage 1

if [ -z "${IMAGE:-}" ]; then
  config="$(dirname "$0")/config.yaml"
  IMAGE=$(awk '
    /^docker_image:/ { block = 1; next }
    block && /^[^ #]/ { block = 0 }
    block && $1 ~ /^(org|name|tag):$/ { key = substr($1, 1, length($1) - 1); value = $2; gsub(/["\047]/, "", value); image[key] = value }
    END { if (image["name"] != "") print (image["org"] != "" ? image["org"] "/" : "") image["name"] (image["tag"] != "" ? ":" image["tag"] : "") }
  ' "$config" 2>/dev/null || true)
  [ -n "$IMAGE" ] || { echo "run.sh: no docker_image in $config; set IMAGE" >&2; exit 1; }
fi

# Absolute host path; Windows form (C:/...) under Git Bash, where Docker needs it.
abspath() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }
export MSYS_NO_PATHCONV=1

[ -d "$1" ] || { echo "run.sh: input folder not found: $1" >&2; exit 1; }
in_dir=$(abspath "$1")
mkdir -p "$2"
out_dir=$(abspath "$2")
shift 2

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
work_dir=$(abspath "$work")

args=()
params_dir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --params) [ $# -ge 2 ] || usage 1; params="$2"; shift 2 ;;
    --params=*) params="${1#--params=}"; shift ;;
    *) args+=("$1"); shift; continue ;;
  esac
  [ -f "$params" ] || { echo "run.sh: params file not found: $params" >&2; exit 1; }
  params_dir=$(abspath "$(dirname "$params")")
  args+=(--params "/data/params/$(basename "$params")")
done

docker_env=(-e WORKDIR=/data/work)
apptainer_env=(--env WORKDIR=/data/work)
for name in OUTPUT_FORMAT ZIP_OUTPUT RO_CRATE RO_CRATE_INPUT CONVERT_INPUTS TOOL_FORMATS OMEZARR_VERSION; do
  if [ -n "${!name:-}" ]; then
    docker_env+=(-e "$name=${!name}")
    apptainer_env+=(--env "$name=${!name}")
  fi
done

case "$RUNTIME" in
  docker|podman)
    mounts=(-v "$in_dir:/data/in" -v "$out_dir:/data/out" -v "$work_dir:/data/work")
    [ -z "$params_dir" ] || mounts+=(-v "$params_dir:/data/params:ro")
    # Run as the calling user on Linux, as Slurm (Apptainer) does, so the outputs are yours.
    user=()
    if [ "$RUNTIME" = docker ] && [ "$(uname -s)" = Linux ]; then user=(--user "$(id -u):$(id -g)"); fi
    "$RUNTIME" run --rm ${user[@]+"${user[@]}"} "${mounts[@]}" "${docker_env[@]}" \
      "$IMAGE" "$IN_FLAG=/data/in" "$OUT_FLAG=/data/out" ${args[@]+"${args[@]}"}
    ;;
  apptainer|singularity)
    binds="$in_dir:/data/in,$out_dir:/data/out,$work_dir:/data/work"
    [ -z "$params_dir" ] || binds+=",$params_dir:/data/params:ro"
    image="$IMAGE"
    [ -f "$image" ] || image="docker://$image"
    "$RUNTIME" run --cleanenv "${apptainer_env[@]}" --bind "$binds" \
      "$image" "$IN_FLAG=/data/in" "$OUT_FLAG=/data/out" ${args[@]+"${args[@]}"}
    ;;
  *) echo "run.sh: unknown RUNTIME '$RUNTIME' (docker, podman, apptainer or singularity)" >&2; exit 1 ;;
esac
