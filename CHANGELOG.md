# Changelog

## Unreleased — 2026-07-22

### Language, appearance, and AI workload context

- Added a unified Settings entry for English/Simplified Chinese, five persisted
  themes, density, section ordering, process columns, and refresh policy.
- Standardized user-facing Codex/OpenAI workload labels as ChatGPT while
  retaining stable internal detector identifiers.
- Added per-provider quota cards for Claude, ChatGPT, and Cursor. Values are
  optional local percentages and remain explicitly unavailable until configured.
- Added staggered, cached project allocation measurements to AI workload,
  project, and session cards without running `du` on the hot sampling path.
- Added best-effort CPU temperature reporting with explicit unsupported states.

### Performance and resource management

- Cached stable process metadata and slow system probes, and removed repeated
  socket, SQLite, and system-memory reads from the snapshot hot path.
- Reduced the live SSE payload to summary data; full process/program arrays are
  fetched only while the process section is near the viewport.
- Added balanced 3-second and efficient 5-second refresh modes, visibility-aware
  pause/resume, lazy runtime polling, request de-duplication, and animation-frame
  render coalescing.
- Added tests for language/theme controls, quota normalization, compact streams,
  workload disk attribution, CPU temperature probing, and polling regressions.

## 0.4.0 — 2026-07-21

### Compact system dashboard

- Replaced the large tab-led Web layout with a dense single-page dashboard.
- Kept CPU, GPU, memory, storage, network, and process cards pinned at the top.
- Added persisted compact/comfortable density, section visibility, section
  ordering, idle AI provider visibility, and process-column preferences.
- Added automatic GPU/container visibility so unavailable providers do not
  reserve empty card space.

### AI workload context

- Restored per-provider CPU activity bars and recent trends.
- Restored project CPU, memory, and process summaries.
- Added expandable session child processes with PID, state, age, CPU, memory,
  and process-detail navigation.
- Kept Claude, Codex/OpenAI, and Cursor fixed to stable provider identity colors.

### Semantic color system

- Added unique tokens for CPU, GPU, memory, storage, network, processes, disk
  read/write, IOPS, latency, network directions, services, containers, and
  alert states.
- Routed JavaScript charts through CSS variables so the palette has one source
  of truth.
- Added a regression test that rejects duplicate semantic color values.

### Documentation and verification

- Updated README installation, Web startup, API verification, architecture,
  and troubleshooting commands.
- Replaced the obsolete pre-GitHub install instructions.
- Added a dashboard design-system reference and visual QA checklist.
- Bumped the package version to `0.4.0`.
