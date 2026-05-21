# see-aicoding

Live system-wide monitor for **Claude Code**, **Codex**, and **Cursor** AI coding processes on macOS / Linux. Identifies every Claude/Codex/Cursor session running on your machine, groups them by tool + project, walks the descendant process tree for accurate per-session totals, and renders a color-coded TUI dashboard with progress bars, sparklines, and a three-zone layout.

Built for the common case: **you run multiple AI coding sessions across several projects at once** and want to know which one is eating your CPU / RAM.

![preview](docs/preview.txt)

```
┌─ see-aicoding   AI coding process monitor ───────────────────────────┐
│ AI CPU  ▰▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱  243.2%  (30.4% of 8 cores)  CPU 94%   │
│ AI MEM  ▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱  3.0GB / 16.0GB  (18.9%)     MEM 85%   │
│ Trend   ▂▃▄▅▆▇█▇▆▅▄▃▂▁▁▂▃▄▅▆  31 sessions  169 processes            │
├─ 🅒 Claude ──────────┬─ 🅞 Codex / OpenAI ──┬─ 🅒 Cursor IDE ───────┤
│ ▰▰▰▱▱▱▱▱▱▱  47%      │ ▰▰▰▰▰▰▰▰▱▱  145%     │ ▰▰▱▱▱▱▱▱▱▱  25%       │
│ 14 sessions          │ 5 sessions           │ 1 session             │
│ ● XAI_Desktop · CLI  │ ● (Codex Desktop)    │ ● (Cursor IDE)        │
│   25.3% 165MB 🟢     │   118% 1.0GB 🔴      │   38.7% 920MB 🟢      │
│ ● Any2K · CLI        │ ● XAI_Desktop · CLI  │                       │
│   2.4%  88MB  🟢     │   26.3% 182MB 🟢     │ Installed AI exts:    │
│ ● XAI_Desktop · ext  │   ├─ rustc 14.9%     │  ● Claude Code 2.1.118│
│   2.7%  65MB  🟡     │   ├─ codex   7.2%    │  ● OpenAI ChatGPT     │
│ ...                  │   └─ SkyCpUse 0.6%   │  ● GitHub Copilot     │
└──────────────────────┴──────────────────────┴───────────────────────┘
```

## What it detects

| Tool family | Detection |
|---|---|
| Claude Code CLI (standalone) | `~/.local/share/claude/versions/...` and `@anthropic-ai/claude-code` |
| Claude Code inside Cursor / VS Code | `~/.cursor/extensions/anthropic.claude-code-*` |
| Codex Desktop App | `/Applications/Codex.app/` and Electron helper tree |
| Codex CLI | `@openai/codex` and `~/.codex/` |
| OpenAI / Codex extension in Cursor / VS Code | `openai.chatgpt-*`, `openai.codex-*` |
| Other AI extensions | GitHub Copilot, Cline, Continue, Cody, Tabnine, Codeium |
| Cursor IDE itself | `/Applications/Cursor.app/` |
| MCP servers (children) | `@modelcontextprotocol/`, `mcp-server` |

Each AI tool gets attributed to a **session** = root process + all descendants. The header shows machine-wide AI total; each zone shows its tool family's slice.

## Install

### Option A — From GitHub (recommended, syncs across machines)

```bash
pip install --user git+https://github.com/jinlong17/see-aicoding.git
```

That installs the `see-aicoding` command into your `pip --user` bin (usually `~/.local/bin/` on Linux, `~/Library/Python/3.x/bin/` on macOS). Make sure that's on your `PATH`. Tip: if not, run `python3 -m site --user-base` to find the prefix.

### Option B — pipx (isolated install, cleanest)

```bash
pipx install git+https://github.com/jinlong17/see-aicoding.git
```

### Option C — From local clone (development)

```bash
git clone https://github.com/jinlong17/see-aicoding.git
cd see-aicoding
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
see-aicoding
```

### Option D — Dedicated venv (no PATH pollution, what's used on the author's machine)

```bash
python3 -m venv ~/.local/share/see-aicoding/venv
~/.local/share/see-aicoding/venv/bin/pip install git+https://github.com/jinlong17/see-aicoding.git
# Then create a tiny shell wrapper somewhere on $PATH:
cat > /opt/homebrew/bin/see-aicoding << 'EOF'
#!/bin/sh
exec "$HOME/.local/share/see-aicoding/venv/bin/see-aicoding" "$@"
EOF
chmod +x /opt/homebrew/bin/see-aicoding
```

## Usage

```bash
see-aicoding                  # live mode, default 1.5s refresh
see-aicoding -i 0.5           # 0.5s refresh
see-aicoding -a               # show idle sessions (< 0.5% CPU)
see-aicoding --no-tree        # collapse descendants — one row per session
see-aicoding --once           # single snapshot, no live loop (for logs/cron)
see-aicoding --full-screen    # alternate-screen mode (no scrollback)
```

| Flag | Default | Effect |
|---|---|---|
| `-i / --interval SECS` | `1.5` | Refresh interval (minimum 0.5s) |
| `-a / --all` | off | Show idle sessions instead of hiding them |
| `--no-tree` | off | Hide descendant process tree |
| `--once` | off | Single snapshot, exit |
| `--full-screen` | off | Use alternate-screen TUI (clears terminal on exit) |

## Layout reference

- **Header** — total AI CPU/MEM with progress bars, machine totals, sparkline trend of last 40 samples, session/process counts.
- **Claude zone** — every Claude Code session (CLI + Cursor/VS Code extensions). Each row = one session = root process + descendant tree summary.
- **Codex / OpenAI zone** — Codex Desktop app, Codex CLI, OpenAI ChatGPT / Codex extensions.
- **Cursor IDE zone** — Cursor application itself + a list of installed AI extensions (whether running or not).

Within each zone:
- Sessions are sorted **newest first** (shortest uptime on top). Stable order — list does not reshuffle as CPU% fluctuates.
- CPU% / memory rows are color-graded: gray (< 5%) → green → yellow → red.
- Status badge: 🔴 high-load (≥70%), 🟢 live (≥10% or has remote conn), 🟡 lite (≥1%), ⚪ idle.
- Idle sessions (< 0.5% CPU) are hidden by default — use `-a` to show.

## Cross-machine sync workflow

Once the GitHub repo is set up, syncing the tool to a new machine is one line:

```bash
pip install --user git+https://github.com/jinlong17/see-aicoding.git
```

To update later:

```bash
pip install --user --upgrade --force-reinstall git+https://github.com/jinlong17/see-aicoding.git
```

If you tweak something on machine A:

```bash
# machine A
git commit -am "tweak: new ext detection"
git push

# machine B
pip install --user --upgrade --force-reinstall git+https://github.com/jinlong17/see-aicoding.git
```

## Architecture

```
src/see_aicoding/
├── __init__.py
├── __main__.py        # `python -m see_aicoding`
├── cli.py             # argparse + main loop + Live
├── monitor.py         # ProcSample, Sampler, Session, History, classify
├── cursor_ext.py      # Cursor / VS Code extension inventory scanner
└── render.py          # rich.Layout 3-zone view, panels, progress bars
```

**How a session is identified:**
1. `Sampler.snapshot()` walks every `psutil` process owned by the current user and classifies it by command-line pattern (`claude-cli`, `claude-cursor`, `codex-desktop`, etc.).
2. `build_sessions()` picks the topmost-of-its-kind process in each PPID chain as the session root — preventing Electron-helper explosion (Codex Desktop launches 70+ helpers; we collapse all of them into one Codex Desktop session).
3. Every other process is attributed to its nearest session root via PPID walk.
4. CPU% is delta-sampled (psutil holds a Process object across refreshes for accurate inter-sample CPU deltas).

**Known limitations:**
- macOS RSS includes shared library pages (V8, Electron framework), so AI total memory is typically 10–15 % over the "true" private working set. For the use case of "which tool is hogging memory," it's the right granularity.
- For purely API-based extensions (no subprocess), per-extension CPU isn't separable from the Cursor Extension Host process. The extensions inventory still shows whether each is installed.
- Reverse-DNS lookup is intentionally not used to attribute network activity — the connection-count badge (🌐N) is a cheap proxy for "is this session talking to something."

## Sync setup (first-time, after cloning to GitHub)

```bash
# In the see-aicoding repo on machine A:
git remote add origin https://github.com/jinlong17/see-aicoding.git
git branch -M main
git push -u origin main

# On any other machine:
pip install --user git+https://github.com/jinlong17/see-aicoding.git
```

## License

MIT — see [LICENSE](LICENSE).
