"""DX (`remote-helper`) CLI plugin bridge for DX PinShot (`dx pinshot` / `dx pin` / `dx p`)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def _resolve_python() -> str:
    """Prefer the shared DX voice/textual/PyQt5 virtualenv when present."""
    candidates = [
        Path.home() / ".local" / "share" / "dx-voice" / "venv" / "bin" / "python",
        Path("/home/axgu/.local/share/dx-voice/venv/bin/python"),
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return sys.executable


def register_cli(subparsers) -> None:
    specs = (
        (
            "pinshot",
            "status",
            "Inspect DX PinShot status or run subcommands (gui, tui, config, install-hotkey).",
        ),
        (
            "pin",
            "tui",
            "Open DX PinShot captures & settings TUI (or forward gui/config subcommands).",
        ),
        (
            "p",
            "tui",
            "Shortcut alias for `dx pin` (opens DX PinShot TUI or forwards subcommands).",
        ),
    )
    for name, default_action, help_text in specs:
        parser = subparsers.add_parser(name, help=help_text, description=help_text)
        parser.add_argument(
            "pinshot_args",
            nargs=argparse.REMAINDER,
            help=f"Arguments forwarded to dx-pinshot (default: {default_action}).",
        )
        parser.set_defaults(extension_handler=execute, pinshot_default=default_action)


def execute(args) -> int:
    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{prev}" if prev else src_dir

    forwarded = list(getattr(args, "pinshot_args", []) or [])
    if not forwarded:
        forwarded = [getattr(args, "pinshot_default", "tui")]

    cmd = [_resolve_python(), "-m", "dx_pinshot", *forwarded]
    return subprocess.run(cmd, env=env).returncode
