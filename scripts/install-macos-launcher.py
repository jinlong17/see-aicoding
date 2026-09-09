#!/usr/bin/env python3
"""Build a Finder/Dock application using the current Python environment."""
from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path.home() / "Desktop")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This application installer requires macOS.")
    import psutil  # Verify this interpreter has the server dependency.
    del psutil
    source = Path(__file__).resolve().parents[1] / "src"
    app = args.destination / "See AI 看板.app"
    if app.exists():
        parser.error(f"Already exists: {app}. Choose another destination to keep the existing app.")
    args.destination.mkdir(parents=True, exist_ok=True)
    command = shlex.join(["env", f"PYTHONPATH={source}", sys.executable, "-m", "see_aicoding.launcher"])
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''on run
    try
        do shell script "{escaped}"
    on error messageText
        display dialog messageText with title "See AI 看板" buttons {{"好"}} default button 1 with icon caution
    end try
end run
'''
    subprocess.run(["/usr/bin/osacompile", "-o", str(app), "-"], input=script, text=True, check=True)
    print(app)


if __name__ == "__main__":
    main()
