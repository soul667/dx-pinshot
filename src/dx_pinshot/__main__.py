"""Module entrypoint for `python3 -m dx_pinshot`."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
