#!/usr/bin/env python3
"""Compatibility entry point for the shared framework's license collector."""
from pathlib import Path
import runpy

_shared = Path(__file__).resolve().parents[2] / 'tyrell-game/tools/make_notices.py'
generate = runpy.run_path(str(_shared))['generate']
if __name__ == '__main__':
    runpy.run_path(str(_shared), run_name='__main__')
