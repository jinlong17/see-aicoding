# Dashboard design system

Updated: 2026-07-22

## Purpose

The Web monitor is a compact developer dashboard, not a presentation screen.
Its visual system prioritizes fast scanning, stable spatial memory, readable
numbers, and explicit unavailable states. The existing dark surfaces and card
language remain, while color identifies data domains instead of decorating
arbitrary components.

## Color contract

The palette is registered once in `web_static/app.css`. Charts in `app.js`
reference the CSS custom properties with `var(...)`; JavaScript must not copy
the hex values.

### Resource domains

| Domain | CSS token | Value | Required uses |
|---|---|---:|---|
| CPU | `--resource-cpu` | `#39bdf8` | CPU metric, system history, CPU leaders, CPU usage bars |
| GPU | `--resource-gpu` | `#d289ff` | GPU metric, system history, GPU leaders |
| Memory | `--resource-memory` | `#f3c64e` | Memory metric, system history, memory leaders |
| Storage | `--resource-storage` | `#ff7d66` | Storage metric, capacity bars, storage section identity |
| Network | `--resource-network` | `#36d6b3` | Network metric, network leader, per-process network panel |
| Processes | `--resource-process` | `#f47cb7` | Process metric, inventory identity, process avatars |

### Secondary telemetry

| Domain | CSS token | Value |
|---|---|---:|
| Disk read | `--io-read` | `#63d8e8` |
| Disk write | `--io-write` | `#ffad5c` |
| IOPS | `--io-iops` | `#9dde6b` |
| I/O latency | `--io-latency` | `#f58b8b` |
| Network download | `--network-download` | `#5be28c` |
| Network upload | `--network-upload` | `#e889f5` |
| Services | `--runtime-service` | `#b8de6f` |
| Containers | `--runtime-container` | `#a99bff` |

### State colors

State colors describe condition, not resource identity.

| State | CSS token | Value | Meaning |
|---|---|---:|---|
| Live / healthy / resolved | `--state-live` | `#78d993` | Connection live, process running, SMART healthy, alert resolved |
| Warning / paused | `--state-warning` | `#ffd166` | Elevated pressure, warning event, paused or waiting state |
| Critical / destructive | `--state-critical` | `#ff5f73` | Critical pressure, failure, terminate action, error toast |

### AI provider identity

These are stable identity colors. Do not reassign them to system resources.

| Provider | CSS token | Value |
|---|---|---:|
| ChatGPT | `--codex` | `#65d9ae` |
| Claude | `--claude` | `#e4a77d` |
| Cursor | `--cursor` | `#95a8ff` |

Provider colors apply to the card edge, activity trend, progress bars, project
rows, session activity, and child-process tree guide. CPU percentages inside an
AI card remain measurements, but their visual identity follows the provider so
the three workload columns stay easy to distinguish.

## Non-overlap rules

1. Every semantic token listed above must have a unique hex value.
2. The same domain keeps the same token in cards, charts, leaders, tables, and details.
3. State colors may repeat across components only when the state meaning is the same.
4. Neutral surfaces, borders, text, and focus treatment do not borrow a resource color.
5. New telemetry domains require a new documented token and a palette test update.
6. Do not encode meaning by color alone; retain labels, values, status text, and accessible names.

`tests/test_dashboard_palette.py` enforces rules 1 and 2 for the static palette
and chart registry.

## Compact layout contract

- Resource cards remain pinned at the top and may wrap from six to three, two,
  or one column according to available width.
- Dashboard sections use the persisted order from `dashboard_preferences`.
- AI workload cards use three columns on desktop, two on medium screens, and one
  on mobile. Cards keep provider identity colors and scroll dense session data
  internally when child processes are expanded.
- Tables favor row density and horizontal table scrolling over shrinking text
  below a readable size.
- GPU and container cards may hide automatically when their providers are
  unavailable; hidden cards do not reserve empty grid space.
- The default density is `compact`; `comfortable` increases spacing without
  changing information architecture.

## Language and theme contract

- The single Settings entry owns language, theme, density, section, column,
  refresh, and optional quota preferences; there is no second appearance menu.
- English and Simplified Chinese use the same DOM and stable semantic values.
  Language changes labels and locale formatting, never provider or process ids.
- `light`, `warm`, `mint`, `dark`, and `deep` override neutral surfaces while
  preserving resource and provider identity tokens.
- Theme variables are applied before the stylesheet loads when a local cached
  choice exists, preventing a bright or dark flash during navigation.
- Mobile controls retain a minimum 44px touch target and dense tables scroll
  inside their container rather than widening the page.

## Component hierarchy

| Component | Primary signal | Secondary signal |
|---|---|---|
| Resource metric card | Domain-colored label, gauge, and progress bar | State badge and hardware/capacity detail |
| System activity | Three resource-colored history lines | Direction-colored disk/network rates |
| Resource leader | Domain-colored glyph and rank bar | Application identity and current value |
| Process inventory | Process identity color and CPU usage bar | Text state badge, PID/PPID, I/O, network |
| Storage | Storage capacity color | Separate read, write, IOPS, and latency colors |
| Runtime | Network, service, or container panel identity | Text provider and availability state |
| AI workload | Fixed provider identity color | CPU activity, project, session, and process values |
| Alerts | Warning/critical/resolved state color | Explicit action, timestamp, threshold, and message |

## Commands

Run the current Web dashboard:

```bash
see-aicoding --web --open
```

Run it from a development checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e . pytest
see-aicoding --web --open
```

Verify JavaScript, Python tests, and whitespace before committing:

```bash
node --check src/see_aicoding/web_static/app.js
python3 -m pytest -q
git diff --check
```

## Visual QA checklist

- Desktop: all available resource cards fit the intended row and every domain
  color is visibly distinct.
- AI workloads: Claude, ChatGPT, and Cursor keep their fixed colors; expanding a
  session does not leak another provider or system color into the tree.
- Themes: all five modes keep text, borders, focus rings, charts, and unavailable
  states legible without changing semantic resource colors.
- Languages: English and Simplified Chinese fit at desktop and narrow widths
  without truncating controls or changing metric values.
- Charts: CPU, memory, GPU, read, and write legend colors match their lines.
- Runtime: network, service, and container panels have different accents.
- Alerts: warning, critical, and resolved states remain distinguishable from
  resource colors and include text labels.
- Mobile: no page-level horizontal overflow; process tables may scroll inside
  their own container.
- Accessibility: progress bars retain names and numeric ARIA values; keyboard
  focus remains visible; color never replaces a textual label.
- Console: no application warnings or errors after loading, reordering a
  section, and expanding an AI session.
