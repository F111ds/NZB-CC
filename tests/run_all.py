"""Runs every test script in this folder and prints a summary.

    python tests/run_all.py

The tests never touch your real configuration or servers: they use temporary folders, mock
indexers/SABnzbd on 127.0.0.1, and briefly open (hidden) app windows. Requires the same packages
as the app (customtkinter; pillow optional).
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    scripts = sorted(p for p in HERE.glob("test_*.py"))
    failed = []
    for script in scripts:
        t0 = time.monotonic()
        proc = subprocess.run([sys.executable, str(script)], cwd=HERE.parent, capture_output=True, text=True)
        ok = proc.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  {script.name}  ({time.monotonic() - t0:.1f}s)")
        if not ok:
            failed.append(script.name)
            print((proc.stdout + proc.stderr).strip()[-3000:])
    print(f"\n{len(scripts) - len(failed)}/{len(scripts)} test files passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
