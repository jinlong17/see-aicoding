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
