# Changelog

## Unreleased — 2026-07-28

### Language, appearance, and AI workload context

- Added a unified Settings entry for English/Simplified Chinese, five persisted
  themes, density, section ordering, process columns, and refresh policy.
- Standardized user-facing Codex/OpenAI workload labels as ChatGPT while
  retaining stable internal detector identifiers.
- Added per-provider quota cards for Claude, ChatGPT, and Cursor. Values are
  populated automatically from the active local Codex profile and explicitly
  enabled Claude status-line data, with manual values filling missing windows.
  Cursor remains manual or unavailable because no supported personal source is
  assumed.
- Reworked quota cards into compact 5-hour and weekly circular gauges, with
  direct and Settings-based visibility controls. Hiding them pauses future
  automatic quota refreshes; showing them resumes collection.
- Added a local-only quota collector with a 5-minute cache, 8-second provider
  timeout, bounded exponential failure retry, manual refresh cooldown, and a
  sanitized user-only Claude snapshot that never stores session or credential
  fields. Identical Claude values are write-deduplicated for one minute.
- Automatically prefer and validate app-bundled Codex binaries before PATH
  candidates, and fall back when an older CLI lacks the required app-server
  protocol.
- Record every Claude status-line run, including runs that carry no
  `rate_limits`, so the Claude card distinguishes a command that has never been
  invoked (Claude Desktop does not execute Claude Code `statusLine`) from one
  waiting for its first response and from an account whose responses never
  publish quota. The manual-only verdict now comes from observed responses
  rather than the plan name; the sanitized subscription-type check is kept only
  to name the plan, is cached for 15 minutes, and never exposes or persists
  credentials.
- Added staggered, cached project allocation measurements to AI workload,
  project, and session cards without running `du` on the hot sampling path.
- Added best-effort CPU temperature reporting with explicit unsupported states.

### Performance and resource management

- Cached stable process metadata and slow system probes, and removed repeated
  socket, SQLite, and system-memory reads from the snapshot hot path.
- Build the compact SSE snapshot directly and lazily materialize the full
  process/program inventory at most once per sampling window. This removes the
  former build-full-then-trim work from every live event while preserving the
  public full schema and a shared generation timestamp.
- Cache bounded process classification and application-bundle grouping results;
  cache status, thread count, and accumulated process CPU time for 5 seconds;
  derive memory percentage from the already-read RSS value; and detect
  unsupported per-process I/O once instead of repeating failed calls on macOS.
- Reduced the live SSE payload to summary data; full process/program arrays are
  fetched only while the process section is near the viewport and now follow
  the selected refresh mode at 4.5, 9, or 15 seconds.
- Added balanced 3-second and efficient 5-second refresh modes, visibility-aware
  pause/resume, lazy runtime polling, request de-duplication, and animation-frame
  render coalescing.
- Skip unchanged event and process-table DOM replacement, keep lightweight
  process totals live between full inventory refreshes, scope Chinese
  localization to changed sections, and disable decorative meter/loading
  animation in Efficient mode.
- In a same-load development profile with about 687 readable processes, reduced
  a forced compact refresh from 73.46 ms to 41.02 ms (44.2%) and kept the
  compact event near 7% of the full JSON payload.
- Added tests for language/theme controls, quota normalization, compact streams,
  lazy full-snapshot materialization, workload disk attribution, CPU
  temperature probing, scoped rendering, and polling regressions.

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
