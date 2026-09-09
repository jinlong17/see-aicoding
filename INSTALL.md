# Installing see-aicoding

## Recommended install

`pipx` keeps the application isolated from the system Python environment:

```bash
pipx install git+https://github.com/jinlong17/see-aicoding.git
see-aicoding --version
see-aicoding --web --open
```

If `pipx` is unavailable, install into the current user's Python environment:

```bash
python3 -m pip install --user git+https://github.com/jinlong17/see-aicoding.git
see-aicoding --version
see-aicoding --web --open
```

The Web dashboard listens on `127.0.0.1:8765` by default. It does not expose
the monitor to the local network.

## Prerequisites

- Python 3.9 or newer
- macOS or Linux
- `pip` or `pipx`
- Internet access for the initial GitHub install

Optional providers are detected automatically:

- `smartctl` for Linux SMART/NVMe health
- `nvidia-smi` for NVIDIA telemetry
- Docker or Podman for the container section
- `nettop` on macOS for per-process network byte rates

Missing optional providers produce an explicit unavailable state; they do not
prevent the dashboard from starting.

## Updating

For a `pipx` install:

```bash
pipx upgrade see-aicoding
```

If the package was installed directly from GitHub and `pipx upgrade` does not
refresh the VCS source, reinstall it explicitly:

```bash
pipx install --force git+https://github.com/jinlong17/see-aicoding.git
```

For a user-level `pip` install:

```bash
python3 -m pip install --user --upgrade --force-reinstall git+https://github.com/jinlong17/see-aicoding.git
```

For the dedicated local wrapper used on some macOS machines:

```bash
~/.local/share/see-aicoding/venv/bin/python -m pip install --upgrade --force-reinstall git+https://github.com/jinlong17/see-aicoding.git
```

Always verify the executable and loaded package after an update:

```bash
which see-aicoding
see-aicoding --version
python3 -c "import see_aicoding; print(see_aicoding.__version__, see_aicoding.__file__)"
```

## Local development

```bash
git clone https://github.com/jinlong17/see-aicoding.git
cd see-aicoding
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
see-aicoding --web --open
```

Run the test suite from the repository root:

```bash
python3 -m pip install pytest
python3 -m pytest -q
```

## Run modes

```bash
see-aicoding --web --open     # compact Web dashboard and open the browser
see-aicoding --web            # compact Web dashboard without opening a browser
see-aicoding --web -i 0.5     # faster Web sampling
see-aicoding                  # focused terminal AI dashboard
see-aicoding --all            # terminal dashboard including idle sessions
see-aicoding --once           # one terminal snapshot
```

Use a different local port when `8765` is reserved:

```bash
see-aicoding --web --open --port 8877
```

## One-click macOS launcher

The desktop app is generated locally with macOS `osacompile`; it is not a
prebuilt download or a login item. Start with the **Local development** setup
above so the checkout and its Python environment are available, then run once:

```bash
source .venv/bin/activate
python3 scripts/install-macos-launcher.py
```

This creates `See AI 看板.app` on the Desktop. Double-click it or drag it to the
Dock. It opens `http://127.0.0.1:8765/`, reuses an existing healthy see-aicoding
server, or starts one in the background. Concurrent launches are serialized
with a file lock. A different application on that port is not treated as a
healthy dashboard.

- The app records this checkout and Python interpreter's absolute paths. Keep
  both in place. After moving either, regenerate the app.
- The installer refuses to overwrite an existing app. Move the old copy aside
  in Finder, or use `python3 scripts/install-macos-launcher.py --destination /path/to/another/folder`.
- Logs: `~/Library/Logs/see-aicoding/web-8765.log`. Logs over 2 MiB are rotated
  to `web-8765.previous.log` at the next server start.
- Closing the browser does **not** stop the detached server. To stop it, find
  the Python process for `see_aicoding.cli --web` in Activity Monitor, verify
  its command, and quit only that process. A terminal-launched foreground server
  can instead be stopped with Ctrl+C.
- The generated app uses port 8765. For another port, use
  `python3 -m see_aicoding.launcher --port 8877` from the same environment.
- This Finder/Dock app is macOS-only. Linux users should use the documented
  CLI Web or terminal modes.

## First-time dashboard setup

Open **Settings**, choose English or Simplified Chinese, a theme, and a text
size, then click **Save**. Click **Simple** in the header to use the record
cards; select one of the three simple styles in Settings. **Modules ▾** selects
which of the 17 modules to display; **S/M/L** changes card size independently of
text size. Header controls save immediately, unlike the Settings draft.

Single-click a card for details; double-click or use its expand icon for
full-window focus. Double-click again, press Esc, or use the exit button to
return. See the [Chinese user guide](./docs/USER_GUIDE.zh-CN.md) or
[English controls](./README.md#simple-mode-quick-start) for all shortcuts.

## Uninstalling

```bash
pipx uninstall see-aicoding
```

Or, for a user-level `pip` install:

```bash
python3 -m pip uninstall see-aicoding
```

The Web monitor stores history and preferences in the local application data
directory. Package removal does not delete that SQLite database automatically.
Removing the generated desktop app in Finder does not stop an already-running
server or delete its history. Stop the verified server process separately if
needed; preserve the local database unless you intentionally want to remove it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `see-aicoding: command not found` | Add `$(python3 -m site --user-base)/bin` to `PATH`, or use `pipx ensurepath` |
| Port `8765` is already in use | Open the existing monitor or run with `--port 8877` |
| `psutil` build fails on Linux | Install Python headers and a compiler, then retry |
| Some processes or sockets are missing | OS permissions limit which users and endpoints can be inspected |
| GPU, SMART, or containers show unavailable | Install the corresponding optional provider listed above |
| Terminal characters look broken | Use a Unicode-aware terminal such as WezTerm, iTerm2, or modern Terminal.app |
| Empty process inventory | Run `python3 -c "import psutil; print(len(list(psutil.process_iter())))"` |
| Launcher says `No module named psutil` | Activate the Python environment containing `pip install -e .`, then regenerate the app |
| Desktop app stops working after moving the project | Regenerate it with the new checkout/interpreter paths; see the log above |
| UI still looks old after an update | Restart the server to load backend changes, then reload the page; use Cmd+Shift+R on macOS if cached assets remain |
| A simple-mode module is missing | Check **Modules ▾** and use **Default modules** or **All modules** |
| Records are not moving | Check Pause, background-tab state, and the OS/browser reduced-motion preference; these intentionally pause motion |
| A card shows `--` or stale quota data | Check local provider availability; absence is not treated as zero usage. See README's quota section |
