"""Double-click launcher: reuse a healthy local server, otherwise start one."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


def server_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/dashboard-preferences", timeout=1) as response:
            if not response.headers.get("Server", "").startswith("see-aicoding-web/"):
                return False
            return isinstance(json.load(response).get("preferences"), dict)
    except (OSError, URLError, ValueError):
        return False


def launch(port: int = 8765, open_browser: bool = True) -> str:
    url = f"http://127.0.0.1:{port}/"
    runtime_dir = Path.home() / "Library" / "Logs" / "see-aicoding"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    # Keep the lock through readiness so concurrent clicks cannot spawn duplicates.
    with (runtime_dir / f"launcher-{port}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not server_ready(port):
            log_path = runtime_dir / f"web-{port}.log"
            if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                log_path.replace(log_path.with_suffix(".previous.log"))
            env = os.environ.copy()
            source_root = str(Path(__file__).resolve().parent.parent)
            env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
            with log_path.open("ab") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "see_aicoding.cli", "--web", "--host", "127.0.0.1", "--port", str(port)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    start_new_session=True, close_fds=True, env=env,
                    cwd=str(Path.home()),
                )
            deadline = time.monotonic() + 30
            while not server_ready(port):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"看板启动失败，请查看日志：{log_path}")
                time.sleep(.25)
    if open_browser:
        webbrowser.open(url)
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    try:
        print(launch(args.port, open_browser=not args.no_open))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
