"""``adobesign`` command-line interface (optional ``cli`` extra).

The CLI is built for scripts and AI agents: JSON on stdout, JSON errors on
stderr, documented exit codes, ``--dry-run`` on every mutating command and a
``--yes`` gate on anything that emails people. Run ``adobesign --help``.

This module only holds the console-script entry point, so that a missing
``click`` produces an install hint instead of a traceback.
"""

from __future__ import annotations

import sys

INSTALL_HINT = (
    "The adobesign CLI needs the optional 'cli' extra. Install it with:\n"
    "  pip install 'python-adobesign[cli] @ "
    "git+https://github.com/BenA-SA/python-adobesign'\n"
)
MISSING_EXTRA_EXIT_CODE = 2


def main() -> None:
    """Console-script entry point."""
    try:
        from adobesign.cli.app import cli
    except ModuleNotFoundError as exc:
        if exc.name not in {"click", "tomli"}:
            raise
        sys.stderr.write(INSTALL_HINT)
        raise SystemExit(MISSING_EXTRA_EXIT_CODE) from exc
    cli()
