#!/usr/bin/env python3
"""
Runs every test module in its own pytest process.

Isolation is deliberate: several legacy modules replace ``PyQt6`` or redirect
stdio at import time and would poison a shared process. Tests marked
``docker``, ``gpu`` or ``integration`` are excluded (see pytest.ini);
pass ``--all`` to include them, or extra pytest arguments after ``--``.
"""
import os
import subprocess
import sys
from pathlib import Path

NO_TESTS_COLLECTED = 5  # every test in the module was deselected by markers


def discover(tests_dir: Path):
    files = []
    for root, dirs, names in os.walk(tests_dir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "sample_data")]
        files.extend(Path(root) / n for n in names if n.startswith("test_") and n.endswith(".py"))
    return sorted(files)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    run_marked = "--all" in argv

    root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")

    cmd_base = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if run_marked:
        cmd_base += ["-m", "docker or gpu or integration or not (docker or gpu or integration)"]

    print("=" * 70)
    print("RUNNING ALL READALOUD TESTS (one pytest process per module)")
    print("=" * 70)

    passed, failed = [], []
    for path in discover(root / "tests"):
        rel = path.relative_to(root)
        print(f"\nRunning test module: {rel}\n" + "-" * 70)
        res = subprocess.run(cmd_base + extra + [str(rel)], cwd=str(root), env=env)
        if res.returncode in (0, NO_TESTS_COLLECTED):
            passed.append(rel)
        else:
            failed.append(rel)
            print(f"FAILED: {rel} (exit code {res.returncode})")

    print("\n" + "=" * 70)
    print(f"Passed modules: {len(passed)}\nFailed modules: {len(failed)}")
    for rel in failed:
        print(f"  - {rel}")
    print("OVERALL STATUS:", "FAILED" if failed else "SUCCESS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
