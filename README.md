# see-aicoding

<div align="center">

**A focused terminal dashboard for AI coding processes.**

Track Claude Code, Codex, OpenAI extensions, Cursor, child processes, CPU, memory, uptime, project attribution, local storage, and network throughput from one compact TUI.

`pip install --user git+https://github.com/jinlong17/see-aicoding.git`

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB?style=flat-square)](https://www.python.org/)
[![Console TUI](https://img.shields.io/badge/interface-terminal-2f3340?style=flat-square)](#usage)
[![macOS / Linux](https://img.shields.io/badge/os-macOS%20%2F%20Linux-3ddc97?style=flat-square)](#install)

</div>

## Preview

```text
╭─ see-aicoding ─────────────────────────────────────────────────────────────╮
│ Time 12:34:56   Network download 80K/s   upload 31K/s                    │
│                         AI total 22 sessions   122 processes             │
│                                             lijinlong@MacBook  macOS 26.4 │
│ AI processor ▰▰▱▱▱▱▱▱▱▱ 21% capacity (172% total)   AI memory ▰▰▰▱▱ 3.1G │
│ Trend ▂▃▄▅▆▇█▇▆▅▄▃▂▁▁    AI memory total 3.1G   processor share 21%       │
│                                             System processor ▰▰▰▱ 37%     │
│                                             System memory 13G/16G 84%     │
│                                             Local storage 436G/460G       │
╰─────────────────────────────── AI workload with system context ───────────╯
╭─ ◆ Claude ─────────────╮╭─ ◆ Codex / OpenAI ───╮╭─ ◆ Cursor IDE ──────────╮
│ ▱▱▱▱▱▱▱▱ 26.1%          ││ ▰▰▱▱▱▱▱▱ 94.4%        ││ ▰▱▱▱▱▱▱▱ 52.2%          │
│ Memory 817.6MB         ││ Memory 1.3GB          ││ Memory 1.1GB            │
│ Projects               ││ Projects              ││ Projects                │
│   ◆ Any2K        9p    ││   ◆ see-aicoding 8p   ││   ◆ Any2K        42p    │
│   ◆ XAI_Desktop  5p    ││   ◆ XAI_Desktop 4p    ││   ◆ XAI_Desktop  10p    │
╰────────────────────────╯╰──────────────────────╯╰────────────────────────╯
```

## Why Use It

When several AI coding tools are open at once, the expensive process is often hidden behind Electron helpers, extension hosts, or child processes. `see-aicoding` groups that noise into readable sessions so you can quickly answer:

| Question | Where to look |
|---|---|
| Which AI tool is using the most processor time? | Zone totals and `CPU%` rows |
| Which project is active? | `Projects` rows in each zone and session |
| Is the activity from a root process or helper? | Tree rows under each session |
| Is a session actually doing work? | `Status`: `HOT`, `LIVE`, `WARM`, or `IDLE` |
| Which AI extensions are installed? | Cursor zone extension inventory |
| Is the whole machine under pressure? | Top-right system processor, memory, storage, and network rows |

## Features

| Area | What you get |
|---|---|
| AI process grouping | Claude Code, Codex Desktop, Codex CLI, Cursor, OpenAI extensions, and common helper processes |
| Project attribution | Project names inferred from cwd, repo markers, and Cursor extension-host process names |
| Stable ordering | Sessions sort by creation time, so rows do not jump around when CPU changes |
| Per-project totals | Process count, processor usage, and memory per detected project |
| System context | Time, network throughput, system processor, system memory, and local storage |
| Extension inventory | Installed Cursor / VS Code AI extensions with version and host |

## Install

Install from GitHub:

```bash
pip install --user git+https://github.com/jinlong17/see-aicoding.git
```

Or with `pipx`:

```bash
pipx install git+https://github.com/jinlong17/see-aicoding.git
```

For local development:

```bash
git clone https://github.com/jinlong17/see-aicoding.git
cd see-aicoding
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
see-aicoding
```

If the command is not found after a `pip --user` install, add your Python user-base bin directory to `PATH`:

```bash
python3 -m site --user-base
```

## Usage

```bash
see-aicoding                  # live dashboard, 1.5s refresh
see-aicoding -i 0.5           # faster refresh
see-aicoding --hide-idle      # hide sessions below 0.5% CPU
see-aicoding --no-tree        # one row per session
see-aicoding --once           # print one snapshot and exit
see-aicoding --full-screen    # alternate-screen mode
```

| Flag | Default | Effect |
|---|---:|---|
| `-i / --interval SECS` | `1.5` | Refresh interval, minimum 0.5s |
| `--hide-idle` | off | Hide sessions below 0.5% CPU |
| `-a / --all` | shown | Show all sessions, kept for compatibility |
| `--no-tree` | off | Hide descendant process tree |
| `--once` | off | Render one snapshot and exit |
| `--full-screen` | off | Use terminal alternate screen |

## Detection

| Tool family | Detection signal |
|---|---|
| Claude Code CLI | `~/.local/share/claude/versions/`, `@anthropic-ai/claude-code` |
| Claude in Cursor / VS Code | `anthropic.claude-code-*` extension paths |
| Codex Desktop | `/Applications/Codex.app/` and helper process tree |
| Codex CLI | `@openai/codex`, `~/.codex/`, `codex` executable paths |
| OpenAI extensions | `openai.chatgpt-*`, `openai.codex-*` |
| Cursor IDE | `/Applications/Cursor.app/` |
| Other AI extensions | Copilot, Cline, Continue, Cody, Tabnine, Codeium |

## Project Labels

Each session tries to show the project directory instead of only the app name.

For CLI tools, the label usually comes from the root process cwd. For desktop shells such as Codex Desktop and Cursor, `see-aicoding` also looks through child process cwd values and Cursor extension-host process names. If multiple projects belong to one desktop app tree, the session row is shown as `N projects`, with colored project rows underneath. Zone headers show one project per line with process count, CPU, and memory.

Child processes are grouped under their detected project when the cwd or Cursor extension-host command exposes one. In a single-project session, helper processes without their own project signal are folded into that project. In a multi-project desktop session, unassigned helpers stay under `helpers` instead of being guessed into the wrong project.

Ignored locations include app bundles, system directories, temporary directories, and extension/plugin cache folders.

## Stable Rows

Rows are sorted by creation time, newest first. CPU changes do not reshuffle rows.

All sessions are shown by default. Earlier versions hid idle sessions by default, which made Claude rows appear and disappear when CPU floated around the 0.5% threshold. Use `--hide-idle` only when you intentionally want a shorter, activity-only view.

## Architecture

```text
src/see_aicoding/
├── cli.py             # argparse, refresh loop, Live rendering
├── monitor.py         # process sampling, classification, session aggregation
├── render.py          # Rich layout, panels, colors, tables
├── cursor_ext.py      # Cursor / VS Code AI extension scanner
├── __main__.py        # python -m see_aicoding
└── __init__.py
```

Sampling flow:

1. `Sampler.snapshot()` walks processes owned by the current user.
2. `classify()` tags each process as Claude, Codex, Cursor, extension, MCP, or child.
3. `build_sessions()` picks root processes and attributes descendants through the parent-process chain.
4. Project names are inferred from cwd, repo markers, and selected desktop app child processes.
5. `render_all()` draws the header, three zones, footer, sparklines, and extension inventory.

## Notes

- macOS resident memory includes shared library pages, so Electron/V8 memory can read higher than private working set.
- AI processor capacity is normalized to all CPU cores; the total value is the summed per-process CPU percentage.
- System memory uses `total - available`, so the displayed size and percentage share the same pressure-oriented basis.
- Network speed is sampled from OS network counters, so it shows current machine traffic, not per-AI-process traffic.
- Pure extension API activity cannot always be separated from the Cursor Extension Host process.
- Network activity is treated as a lightweight live/idle signal; reverse-DNS attribution is intentionally avoided.
