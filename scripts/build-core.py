"""Freeze the Python core into the Tauri sidecar for this machine and smoke-test it.

Used by scripts/package-windows.ps1 and the release workflow (Windows and macOS).
Run with the project's virtual environment Python from the repository root.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def local_browsers() -> list[Path]:
    return [
        path
        for path in (ROOT / ".venv").glob(
            "**/playwright/driver/package/.local-browsers/chromium-*"
        )
        if path.is_dir()
    ]


def bundled_chromium() -> Path:
    matches = local_browsers()
    if not matches:
        raise SystemExit(
            "Bundled Chromium is missing. Install it with PLAYWRIGHT_BROWSERS_PATH=0: "
            "python -m playwright install chromium --no-shell"
        )
    return matches[0]


def main() -> None:
    if sys.platform == "darwin":
        # PyInstaller cannot re-sign Chromium's .app bundle; the macOS core downloads
        # Chromium on first run instead (chromium_runtime.install_chromium).
        if local_browsers():
            raise SystemExit(
                "Remove Chromium from the Playwright package before a macOS build: "
                "it must not be installed with PLAYWRIGHT_BROWSERS_PATH=0 here."
            )
    else:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
        print(f"Chromium: {bundled_chromium().name}")
    # One folder in the app's resources (bundle.resources, tauri.production.conf.json).
    # A one-file core unpacks itself (~250 MB with Chromium) into temp on every start,
    # about 5 s before the app answers; on macOS the one-file loader also needs a System V
    # semaphore that some Macs deny ("Failed to initialize sync semaphore").
    name = "artist-core"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onedir",
            "--console",
            "--name",
            name,
            "--paths",
            "backend",
            "--add-data",
            str(ROOT / "backend/artist_lead_finder/imessage/Verse iMessage.shortcut")
            + os.pathsep
            + "artist_lead_finder/imessage",
            "--distpath",
            "src-tauri/core",
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
        ROOT / "src-tauri/core" / name / (name + (".exe" if os.name == "nt" else ""))
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/smoke-sidecar.py"), str(executable)],
        check=True,
    )
    print(f"Sidecar: {executable}")


if __name__ == "__main__":
    main()
