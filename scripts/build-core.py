"""Freeze the Python core into the Tauri sidecar for this machine and smoke-test it.

Used by scripts/package-windows.ps1 and the release workflow (Windows and macOS).
Run with the project's virtual environment Python from the repository root.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def target_triple() -> str:
    """Rust host triple, the suffix Tauri expects on externalBin files."""
    output = subprocess.run(
        ["rustc", "-vV"], capture_output=True, text=True, check=True
    ).stdout
    return next(
        line.split(": ", 1)[1]
        for line in output.splitlines()
        if line.startswith("host:")
    )


def bundled_chromium() -> Path:
    browsers = ROOT / ".venv"
    matches = [
        path
        for path in browsers.glob(
            "**/playwright/driver/package/.local-browsers/chromium-*"
        )
        if path.is_dir()
    ]
    if not matches:
        raise SystemExit(
            "Bundled Chromium is missing. Install it with PLAYWRIGHT_BROWSERS_PATH=0: "
            "python -m playwright install chromium --no-shell"
        )
    return matches[0]


def main() -> None:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    print(f"Chromium: {bundled_chromium().name}")
    triple = target_triple()
    name = f"artist-core-{triple}"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onefile",
            "--console",
            "--name",
            name,
            "--paths",
            "backend",
            "--distpath",
            "src-tauri/binaries",
            "--workpath",
            "build/freezer/work",
            "--specpath",
            "build/freezer",
            "backend/run_backend.py",
        ],
        cwd=ROOT,
        check=True,
    )
    executable = (
        ROOT / "src-tauri/binaries" / (name + (".exe" if os.name == "nt" else ""))
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/smoke-sidecar.py"), str(executable)],
        check=True,
    )
    print(f"Sidecar: {executable}")


if __name__ == "__main__":
    main()
