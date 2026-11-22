#!/usr/bin/env python3
"""LONA v2 - frozen entry bootstrap (repo root).

WHY THIS FILE EXISTS: PyInstaller freezes the entry script as
top-level ``__main__`` with no parent package, so lona2/main.py's
relative imports (``from . import VERSION, invite, winshape``) fail
at startup in the frozen app - "attempted relative import with no
known parent package" (v2.0.0-rebuild.4 crashed on launch exactly
this way, while the tests passed because they import lona2 as a
package). The fix is this bootstrap: it imports lona2.main as a
module INSIDE the package (absolute import) and calls main().
lona2/main.py's relative imports stay as they are - the package
structure is unchanged; only the entry point moved.

PyInstaller entry: lona.spec points at this file.
From source: python run.py
"""
from lona2.main import main

if __name__ == "__main__":
    main()
