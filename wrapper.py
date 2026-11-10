"""bilayers container entrypoint.

Resolves the values the Slurm client passes (see config.yaml), converts OME-Zarr/TIFF where the
tool needs it, runs ``exec_function.cli_command`` and writes RO-Crate metadata.

To run something other than the spec's command line, pass a tool function, e.g.::

    from bilayers_auto.biaflows import biaflows_tool
    run(spec=CONFIG, tool=biaflows_tool("python3.7 /app/biaflows/wrapper.py"))

or ``tool=my_function`` taking a bilayers_auto.RunContext (ctx.params, ctx.inputs,
ctx.outputs, ctx.command()).
"""
from pathlib import Path

from bilayers_auto import run

CONFIG = Path(__file__).with_name("config.yaml")

if __name__ == "__main__":
    raise SystemExit(run(spec=CONFIG))
