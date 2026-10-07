"""Run the complete offline Python and browser-logic regressions (stdlib only)."""
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE_TESTS = (
    "test_map_view.cjs",
    "test_dispatch_ui.cjs",
    "test_planner_view.cjs",
    "test_choice_view.cjs",
    "test_professional_view.cjs",
)


def run_commands(root, commands):
    """Keep all diagnostics, but fail the overall run if any child fails."""
    failed = []
    for label, command in commands:
        print(f"\n=== {label} ===", flush=True)
        try:
            code = subprocess.run(command, cwd=root, check=False).returncode
        except OSError as exc:
            print(f"Could not start {label}: {exc}", flush=True)
            code = 1
        print(f"{label}: exit {code}", flush=True)
        if code != 0:
            failed.append(label)
    if failed:
        print("\nOffline regression FAILED: " + ", ".join(failed), flush=True)
        return 1
    print("\nAll offline regressions passed.", flush=True)
    return 0


def main():
    # Use identical UTF-8 logs in Windows terminals, redirected files and CI.
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")

    if not any((ROOT / "tests").glob("test_*.py")):
        print("No Python regression files found in tests/.", flush=True)
        return 1
    missing = [name for name in NODE_TESTS if not (ROOT / "tests" / name).is_file()]
    if missing:
        print("Missing Node regressions: " + ", ".join(missing), flush=True)
        return 1
    node = shutil.which("node")
    if node is None:
        print("Node.js is required. Install Node 24 and put node on PATH.", flush=True)
        return 1

    print(f"Repository: {ROOT}", flush=True)
    print(f"Python: {sys.version.split()[0]} ({sys.executable})", flush=True)
    print(f"Node: {node}", flush=True)
    commands = [("Python unittest discover", [
        # Explicitly prefer this checkout even for an embedded interpreter
        # whose ._pth may contain an older installation of traffic_agent.
        sys.executable, "-c",
        "import sys; sys.path.insert(0, '.'); import unittest; unittest.main(module=None)",
        "discover", "-s", "tests", "-v",
    ])]
    commands += [(f"Node {name}", [node, str(ROOT / "tests" / name)])
                 for name in NODE_TESTS]
    return run_commands(ROOT, commands)


if __name__ == "__main__":
    raise SystemExit(main())
