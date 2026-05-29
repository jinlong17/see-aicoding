"use strict";

const state = {
  snapshot: null,
  paused: false,
  showAll: false,
  showTree: true,
  query: "",
  sortKey: "age",
  sortDir: "asc",
  projectFocus: null,
  selectedSessionId: null,
  fallbackTimer: null,
  eventSource: null,
};

const el = {
  hostLine: document.getElementById("hostLine"),
  searchInput: document.getElementById("searchInput"),
  activeBtn: document.getElementById("activeBtn"),
  allBtn: document.getElementById("allBtn"),
  treeToggle: document.getElementById("treeToggle"),
  pauseBtn: document.getElementById("pauseBtn"),
  aiCpu: document.getElementById("aiCpu"),
  aiCpuBar: document.getElementById("aiCpuBar"),
  aiCpuGauge: document.getElementById("aiCpuGauge"),
  aiMem: document.getElementById("aiMem"),
  aiMemBar: document.getElementById("aiMemBar"),
  aiMemGauge: document.getElementById("aiMemGauge"),
  sysCpu: document.getElementById("sysCpu"),
  sysCpuBar: document.getElementById("sysCpuBar"),
  sysCpuGauge: document.getElementById("sysCpuGauge"),
  sysMem: document.getElementById("sysMem"),
  sysMemBar: document.getElementById("sysMemBar"),
  sysMemGauge: document.getElementById("sysMemGauge"),
  networkRate: document.getElementById("networkRate"),
  networkGauge: document.getElementById("networkGauge"),
  updateTime: document.getElementById("updateTime"),
  sparkline: document.getElementById("sparkline"),
  trendPeak: document.getElementById("trendPeak"),
  connectionState: document.getElementById("connectionState"),
  zones: document.getElementById("zones"),
  topMemory: document.getElementById("topMemory"),
  topCpu: document.getElementById("topCpu"),
  detailsDrawer: document.getElementById("detailsDrawer"),
  detailsTitle: document.getElementById("detailsTitle"),
  detailsBody: document.getElementById("detailsBody"),
  closeDetails: document.getElementById("closeDetails"),
  toast: document.getElementById("toast"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  for (const unit of units) {
    if (value < 1024 || unit === units[units.length - 1]) {
      if (unit === "B" || unit === "KB") return `${Math.round(value)}${unit}`;
      return `${value.toFixed(1).replace(".0", "")}${unit}`;
    }
    value /= 1024;
  }
  return `${n}B`;
}

function formatRate(bytesPerSecond) {
  return `${formatBytes(bytesPerSecond)}/s`;
}

function formatPct(value, digits = 1) {
  return `${Number(value || 0).toFixed(digits)}%`;
}

function clampPercent(value) {
  return Math.max(0, Math.min(100, Number(value || 0)));
}

function setBar(node, percent) {
  node.style.width = `${clampPercent(percent)}%`;
}

function setGauge(node, percent) {
  if (!node) return;
  const value = clampPercent(percent);
  node.style.setProperty("--gauge-value", `${value}%`);
  node.querySelector("span").textContent = `${Math.round(value)}%`;
}

function networkGaugePercent(downloadBytes, uploadBytes) {
  const rate = Math.max(Number(downloadBytes || 0), Number(uploadBytes || 0));
  if (rate <= 0) return 0;
  return Math.min(100, Math.sqrt(rate / (5 * 1024 * 1024)) * 100);
}

function setConnection(text, mode) {
  if (!el.connectionState) return;
  el.connectionState.textContent = text;
  el.connectionState.classList.toggle("is-waiting", mode === "waiting");
  el.connectionState.classList.toggle("is-error", mode === "error");
}

function showToast(text) {
  el.toast.textContent = text;
  el.toast.classList.add("is-visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.toast.classList.remove("is-visible"), 1600);
}

async function copyText(text, label = "Copied") {
  const value = String(text || "");
  if (!value) {
    showToast("Nothing to copy");
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  showToast(label);
}

const TREND_SLOTS = 40;

function renderAreaChart(values, color, scaleMax = null, chartId = "trend") {
  const list = Array.isArray(values) ? values.slice(-TREND_SLOTS) : [];
  const max = Math.max(1, Number(scaleMax || 0), ...list);
  const padding = Array.from({ length: Math.max(0, TREND_SLOTS - list.length) }, () => 0);
  const slots = [...padding, ...list];
  const width = 240;
  const height = 54;
  const top = 5;
  const bottom = 47;
  const step = width / Math.max(1, TREND_SLOTS - 1);
  const points = slots.map((value, index) => {
    const ratio = Math.max(0, Math.min(1, Number(value || 0) / max));
    const x = Number((index * step).toFixed(2));
    const y = Number((bottom - ratio * (bottom - top)).toFixed(2));
    return [x, y];
  });
  const linePoints = points.map(([x, y]) => `${x},${y}`).join(" ");
  const areaPoints = `0,${bottom} ${linePoints} ${width},${bottom}`;
  const lastPoint = points[points.length - 1] || [0, bottom];
  const gradientId = `area-${String(chartId).replace(/[^a-z0-9_-]/gi, "")}`;
  return `
    <svg class="trend-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Trend ${formatPct(list[list.length - 1] || 0, 1)}">
      <defs>
        <linearGradient id="${gradientId}" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.72"></stop>
          <stop offset="58%" stop-color="${color}" stop-opacity="0.34"></stop>
          <stop offset="100%" stop-color="${color}" stop-opacity="0.08"></stop>
        </linearGradient>
      </defs>
      <path class="trend-area" d="M ${areaPoints} Z" fill="url(#${gradientId})"></path>
      <polyline class="trend-line" points="${linePoints}" style="--spark-color:${color}"></polyline>
      <circle class="trend-endpoint" cx="${lastPoint[0]}" cy="${lastPoint[1]}" r="2.5" style="--spark-color:${color}"></circle>
    </svg>`;
}

function zoneRgb(zoneId) {
  if (zoneId === "claude") return "180, 140, 255";
  if (zoneId === "codex") return "48, 213, 168";
  return "67, 191, 242";
}

function renderTrendLanes(snapshot) {
  const zones = snapshot.zones || [];
  const allValues = zones.flatMap((zone) => zone.history || []);
  const scaleMax = Math.max(10, ...allValues);
  if (!zones.length) return `<div class="empty">No trend samples</div>`;
  return zones
    .map((zone) => {
      const color = zoneStyle(zone);
      const history = zone.history || [];
      const peak = Math.max(0, ...history);
      return `
        <div class="trend-lane trend-${escapeHtml(zone.id)}" style="--trend-color:${color};--trend-rgb:${zoneRgb(zone.id)}">
          <div class="trend-lane-head">
            <span class="trend-dot"></span>
            <span class="trend-name">${escapeHtml(zone.title)}</span>
            <span>${formatPct(zone.cpu_capacity_percent || 0, 1)} cap</span>
            <span>Peak ${formatPct(peak, 1)}</span>
          </div>
          <div class="sparkline">${renderAreaChart(history, color, scaleMax, zone.id)}</div>
        </div>`;
    })
    .join("");
}

function sessionMatches(session) {
  if (!state.query) return true;
  const q = state.query.toLowerCase();
  const hay = [
    session.id,
    session.project,
    session.kind_label,
    session.kind,
    session.root?.pid,
    session.root?.cwd,
    session.root?.cmdline,
    ...(session.projects || []),
    ...(session.children || []).map((child) => `${child.pid} ${child.name} ${child.cmdline} ${child.cwd}`),
  ]
    .join(" ")
    .toLowerCase();
  return hay.includes(q);
}

function sessionProjects(session) {
  const projects = session.project_stats?.length
    ? session.project_stats.map((project) => project.name)
    : session.projects || [];
  return [...new Set(projects.filter(Boolean))];
}

function sessionHasProject(session, projectName) {
  if (!projectName) return true;
  return sessionProjects(session).includes(projectName);
}

function visibleSessions(zone) {
  return (zone.sessions || []).filter((session) => {
    if (!state.showAll && !session.active) return false;
    if (!sessionHasProject(session, state.projectFocus)) return false;
    return sessionMatches(session);
  });
}

const PROJECT_PALETTE = [
  ["#ffd166", "255, 209, 102"],
  ["#ff8fab", "255, 143, 171"],
  ["#b48cff", "180, 140, 255"],
  ["#30d5a8", "48, 213, 168"],
  ["#43bff2", "67, 191, 242"],
  ["#8bd17c", "139, 209, 124"],
  ["#f0a35e", "240, 163, 94"],
  ["#7aa2ff", "122, 162, 255"],
];

function projectColor(projectName) {
  let hash = 0;
  for (const char of String(projectName || "")) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return PROJECT_PALETTE[hash % PROJECT_PALETTE.length];
}

function projectStyle(projectName) {
  const [color, rgb] = projectColor(projectName);
  return `--project-color:${color};--project-rgb:${rgb}`;
}

function defaultSortDir(key) {
  return key === "age" ? "asc" : "desc";
}

function sortValue(session, key) {
  if (key === "cpu") return Number(session.cpu_capacity_percent || 0);
  if (key === "memory") return Number(session.memory_bytes || 0);
  return Number(session.uptime_seconds || 0);
}

function sortSessions(sessions) {
  const direction = state.sortDir === "asc" ? 1 : -1;
  return [...sessions].sort((a, b) => {
    const primary = (sortValue(a, state.sortKey) - sortValue(b, state.sortKey)) * direction;
    if (primary !== 0) return primary;
    const aCreated = Number(a.root?.create_time || 0);
    const bCreated = Number(b.root?.create_time || 0);
    if (aCreated !== bCreated) return bCreated - aCreated;
    return String(a.id || "").localeCompare(String(b.id || ""));
  });
}

function renderSortButton(key, label) {
  const active = state.sortKey === key;
  const marker = active ? (state.sortDir === "asc" ? "^" : "v") : "";
  const directionLabel = state.sortDir === "asc" ? "ascending" : "descending";
  const ariaLabel = active ? `${label}, sorted ${directionLabel}` : `Sort by ${label}`;
  return `
    <button class="sort-btn ${active ? "is-active" : ""}" data-sort-key="${key}" type="button" aria-label="${escapeHtml(ariaLabel)}">
      <span>${escapeHtml(label)}</span>
      <span class="sort-mark" aria-hidden="true">${marker}</span>
    </button>`;
}

function renderMetrics(snapshot) {
  const ai = snapshot.ai || {};
  const system = snapshot.system || {};
  const memory = system.memory || {};
  const network = system.network || {};
  el.hostLine.textContent = `${system.user || "user"}@${system.hostname || "localhost"} - ${system.platform || "local"}`;

  el.aiCpu.textContent = formatPct(ai.cpu_capacity_percent || 0, 1);
  setBar(el.aiCpuBar, ai.cpu_capacity_percent || 0);
  setGauge(el.aiCpuGauge, ai.cpu_capacity_percent || 0);
  el.aiMem.textContent = formatBytes(ai.memory_bytes || 0);
  setBar(el.aiMemBar, ai.memory_percent || 0);
  setGauge(el.aiMemGauge, ai.memory_percent || 0);
  el.sysCpu.textContent = formatPct(system.cpu_percent || 0, 1);
  setBar(el.sysCpuBar, system.cpu_percent || 0);
  setGauge(el.sysCpuGauge, system.cpu_percent || 0);
  el.sysMem.textContent = `${formatBytes(memory.used_bytes || 0)}/${formatBytes(memory.total_bytes || 0)}`;
  setBar(el.sysMemBar, memory.percent || 0);
  setGauge(el.sysMemGauge, memory.percent || 0);
  el.networkRate.textContent = `${formatRate(network.download_bytes_per_s || 0)} down`;
  setGauge(el.networkGauge, networkGaugePercent(network.download_bytes_per_s || 0, network.upload_bytes_per_s || 0));
  el.updateTime.textContent = `${formatRate(network.upload_bytes_per_s || 0)} up - ${new Date((snapshot.generated_at || 0) * 1000).toLocaleTimeString()}`;
  el.sparkline.innerHTML = renderTrendLanes(snapshot);
  el.trendPeak.textContent = "By tool";
}

function zoneStyle(zone) {
  if (zone.id === "claude") return "var(--claude)";
  if (zone.id === "codex") return "var(--codex)";
  return "var(--cursor)";
}

function renderProjects(projects) {
  const list = (projects || []).slice(0, 5);
  if (!list.length) return `<span class="tag">No active projects</span>`;
  return list
    .map(
      (project) => `
        <button
          class="tag project-filter ${state.projectFocus === project.name ? "is-active" : ""}"
          style="${projectStyle(project.name)}"
          data-project-name="${escapeHtml(project.name)}"
          type="button"
          title="${state.projectFocus === project.name ? "Clear project focus" : `Focus ${escapeHtml(project.name)}`}">
          <span>${escapeHtml(project.name)}</span>
          <span>${formatPct(project.cpu_capacity_percent || 0, 1)} cap</span>
          <span>${formatBytes(project.memory_bytes || 0)}</span>
        </button>`
    )
    .join("");
}

function renderProjectPills(session) {
  const projects = sessionProjects(session);
  if (!projects.length) return "";
  const visible = projects.slice(0, 4);
  const overflow = projects.length - visible.length;
  return `
    <span class="project-pills" aria-label="Session projects">
      ${visible
        .map(
          (projectName) => `
            <button
              class="project-pill ${state.projectFocus === projectName ? "is-active" : ""}"
              style="${projectStyle(projectName)}"
              data-project-name="${escapeHtml(projectName)}"
              type="button"
              title="${state.projectFocus === projectName ? "Clear project focus" : `Focus ${escapeHtml(projectName)}`}">
              ${escapeHtml(projectName)}
            </button>`
        )
        .join("")}
      ${overflow > 0 ? `<span class="project-more">+${overflow}</span>` : ""}
    </span>`;
}

function renderSessionRows(sessions) {
  if (!sessions.length) {
    return `<tr><td colspan="5"><div class="empty">No matching sessions</div></td></tr>`;
  }
  return sessions
    .map((session) => {
      const childRows =
        state.showTree && session.children && session.children.length
          ? session.children
              .slice(0, 12)
              .map(
                (child) => `
                  <tr class="child-row">
                    <td title="${escapeHtml(child.cmdline)}">
                      <div class="name-cell"><span class="name-main">- ${escapeHtml(child.label || child.name)}</span></div>
                    </td>
                    <td class="num">${formatPct(child.cpu_capacity_percent || 0, 1)}</td>
                    <td class="num">${formatBytes(child.memory_bytes || 0)}</td>
                    <td>${escapeHtml(child.age_label || "")}</td>
                    <td></td>
                  </tr>`
              )
              .join("")
          : "";
      return `
        <tr class="session-row" data-session-id="${escapeHtml(session.id)}">
          <td title="${escapeHtml(session.root?.cmdline || "")}">
            <div class="name-cell">
              <span class="dot" style="background:${escapeHtml(session.color)}"></span>
              <span class="name-main">
                ${escapeHtml(session.project || session.kind_label)}
                <span class="name-sub">${escapeHtml(session.kind_label)} - pid ${escapeHtml(session.root?.pid || "")}</span>
                ${renderProjectPills(session)}
              </span>
            </div>
          </td>
          <td class="num">${formatPct(session.cpu_capacity_percent || 0, 1)}</td>
          <td class="num">${formatBytes(session.memory_bytes || 0)}</td>
          <td>${escapeHtml(session.uptime_label || "")}</td>
          <td><span class="status status-${escapeHtml(session.status)}">${escapeHtml(session.status)}</span></td>
        </tr>
        ${childRows}`;
    })
    .join("");
}

function renderZones(snapshot) {
  el.zones.innerHTML = (snapshot.zones || [])
    .map((zone) => {
      const color = zoneStyle(zone);
      const sessions = sortSessions(visibleSessions(zone));
      return `
        <section class="zone zone-${escapeHtml(zone.id)}" data-zone="${escapeHtml(zone.id)}">
          <div class="zone-head">
            <div class="zone-title">
              <span class="dot" style="background:${color}"></span>
              <h2>${escapeHtml(zone.title)}</h2>
            </div>
            <div class="zone-stats">
              <span>${sessions.length} sessions</span>
              <span>${formatPct(zone.cpu_capacity_percent || 0, 1)} cap</span>
              <span>${formatBytes(zone.memory_bytes || 0)}</span>
            </div>
          </div>
          <div class="projects">${renderProjects(zone.projects)}</div>
          <div class="table-wrap">
            <table>
              <colgroup>
                <col style="width:44%">
                <col style="width:14%">
                <col style="width:16%">
                <col style="width:13%">
                <col style="width:13%">
              </colgroup>
              <thead>
                <tr>
                  <th>Session</th>
                  <th class="num">${renderSortButton("cpu", "CPU")}</th>
                  <th class="num">${renderSortButton("memory", "Memory")}</th>
                  <th>${renderSortButton("age", "Age")}</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>${renderSessionRows(sessions)}</tbody>
            </table>
          </div>
        </section>`;
    })
    .join("");
}

function leaderValue(group, primary) {
  return primary === "mem" ? Number(group.memory_bytes || 0) : Number(group.cpu_capacity_percent || 0);
}

function leaderAccent(index, primary) {
  if (primary === "mem") {
    return ["var(--warn)", "var(--hot)", "var(--claude)", "var(--codex)", "var(--cool)"][index] || "var(--dim)";
  }
  return ["var(--cool)", "var(--codex)", "var(--claude)", "var(--warn)", "var(--hot)"][index] || "var(--dim)";
}

function renderLeader(list, primary) {
  if (!list || !list.length) return `<div class="empty">No process samples</div>`;
  const maxValue = Math.max(1, ...list.map((group) => leaderValue(group, primary)));
  return list
    .map((group, index) => {
      const value = leaderValue(group, primary);
      const fill = Math.max(4, Math.min(100, (value / maxValue) * 100));
      const accent = leaderAccent(index, primary);
      const metric = primary === "mem" ? formatBytes(group.memory_bytes || 0) : formatPct(group.cpu_capacity_percent || 0, 1);
      const secondary = primary === "mem" ? formatPct(group.cpu_capacity_percent || 0, 1) : formatBytes(group.memory_bytes || 0);
      const members = (group.members || [])
        .map((member) => `${member.label || member.name || `pid ${member.pid}`} (${formatBytes(member.memory_bytes || 0)})`)
        .join(", ");
      const detail = group.detail || `${group.process_count || 0}p`;
      const title = [detail, members].filter(Boolean).join(" - ");
      return `
        <div class="leader leader-${primary}" style="--leader-color:${accent};--leader-fill:${fill.toFixed(1)}%" title="${escapeHtml(title)}">
          <span class="leader-rank">#${index + 1}</span>
          <span class="leader-name">${escapeHtml(group.label || `pid ${group.primary_pid}`)}<span class="leader-detail">${escapeHtml(detail)}</span></span>
          <span class="leader-metric">${metric}</span>
          <span class="leader-secondary">${secondary}</span>
          <span class="leader-bar" aria-hidden="true"><span></span></span>
        </div>`;
    })
    .join("");
}

function renderResources(snapshot) {
  const resources = snapshot.resources || {};
  el.topMemory.innerHTML = renderLeader(resources.top_memory, "mem");
  el.topCpu.innerHTML = renderLeader(resources.top_cpu, "cpu");
}

function findSession(id) {
  return (state.snapshot?.sessions || []).find((session) => session.id === id);
}

function renderDetails() {
  const session = findSession(state.selectedSessionId);
  if (!session) {
    el.detailsDrawer.classList.remove("is-open");
    el.detailsDrawer.setAttribute("aria-hidden", "true");
    return;
  }
  el.detailsDrawer.classList.add("is-open");
  el.detailsDrawer.setAttribute("aria-hidden", "false");
  el.detailsTitle.textContent = session.project || session.kind_label;
  const root = session.root || {};
  el.detailsBody.innerHTML = `
    <div class="detail-grid">
      <div class="detail-field"><div class="detail-label">Tool</div><div class="detail-value">${escapeHtml(session.kind_label)}</div></div>
      <div class="detail-field"><div class="detail-label">Status</div><div class="detail-value"><span class="status status-${escapeHtml(session.status)}">${escapeHtml(session.status)}</span></div></div>
      <div class="detail-field"><div class="detail-label">CPU capacity</div><div class="detail-value">${formatPct(session.cpu_capacity_percent || 0, 1)}</div></div>
      <div class="detail-field"><div class="detail-label">Memory</div><div class="detail-value">${formatBytes(session.memory_bytes || 0)}</div></div>
      <div class="detail-field"><div class="detail-label">PID</div><div class="detail-value">${escapeHtml(root.pid || "")}</div></div>
      <div class="detail-field"><div class="detail-label">Processes</div><div class="detail-value">${escapeHtml(session.process_count || 0)}</div></div>
    </div>
    <div class="copy-row">
      <button class="copy-btn" data-copy="${escapeHtml(root.pid || "")}" data-label="PID copied" type="button">Copy PID</button>
      <button class="copy-btn" data-copy="${escapeHtml(root.cwd || "")}" data-label="cwd copied" type="button">Copy cwd</button>
      <button class="copy-btn" data-copy="${escapeHtml(root.cmdline || "")}" data-label="command copied" type="button">Copy command</button>
    </div>
    <div class="detail-field">
      <div class="detail-label">cwd</div>
      <div class="detail-value" title="${escapeHtml(root.cwd || "")}">${escapeHtml(root.cwd || "unavailable")}</div>
    </div>
    <pre class="cmd">${escapeHtml(root.cmdline || "")}</pre>
  `;
}

function render() {
  if (!state.snapshot) return;
  renderMetrics(state.snapshot);
  renderZones(state.snapshot);
  renderResources(state.snapshot);
  renderDetails();
}

async function fetchSnapshot() {
  try {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const snapshot = await response.json();
    if (!state.paused) {
      state.snapshot = snapshot;
      render();
    }
    setConnection(state.paused ? "Paused" : "Live", state.paused ? "waiting" : "ok");
  } catch (error) {
    setConnection("Offline", "error");
  }
}

function startFallbackPolling() {
  if (state.fallbackTimer) return;
  fetchSnapshot();
  state.fallbackTimer = setInterval(fetchSnapshot, 2000);
}

function startEvents() {
  if (!window.EventSource) {
    startFallbackPolling();
    return;
  }
  state.eventSource = new EventSource("/events");
  state.eventSource.addEventListener("snapshot", (event) => {
    try {
      const snapshot = JSON.parse(event.data);
      if (!state.paused) {
        state.snapshot = snapshot;
        render();
      }
      setConnection(state.paused ? "Paused" : "Live", state.paused ? "waiting" : "ok");
    } catch {
      setConnection("Parse error", "error");
    }
  });
  state.eventSource.onerror = () => {
    setConnection("Reconnecting", "waiting");
    if (!state.snapshot) {
      state.eventSource.close();
      startFallbackPolling();
    }
  };
}

el.searchInput.addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  render();
});

el.activeBtn.addEventListener("click", () => {
  state.showAll = false;
  el.activeBtn.classList.add("is-active");
  el.allBtn.classList.remove("is-active");
  render();
});

el.allBtn.addEventListener("click", () => {
  state.showAll = true;
  el.allBtn.classList.add("is-active");
  el.activeBtn.classList.remove("is-active");
  render();
});

el.treeToggle.addEventListener("change", (event) => {
  state.showTree = event.target.checked;
  render();
});

el.pauseBtn.addEventListener("click", () => {
  state.paused = !state.paused;
  el.pauseBtn.textContent = state.paused ? "Resume" : "Pause";
  setConnection(state.paused ? "Paused" : "Live", state.paused ? "waiting" : "ok");
});

el.zones.addEventListener("click", (event) => {
  const projectButton = event.target.closest("[data-project-name]");
  if (projectButton) {
    event.stopPropagation();
    const projectName = projectButton.dataset.projectName;
    state.projectFocus = state.projectFocus === projectName ? null : projectName;
    render();
    return;
  }
  const sortButton = event.target.closest("[data-sort-key]");
  if (sortButton) {
    event.stopPropagation();
    const key = sortButton.dataset.sortKey;
    if (state.sortKey === key) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortKey = key;
      state.sortDir = defaultSortDir(key);
    }
    render();
    return;
  }
  const copyButton = event.target.closest("[data-copy]");
  if (copyButton) {
    event.stopPropagation();
    copyText(copyButton.dataset.copy, copyButton.dataset.label || "Copied");
    return;
  }
  const row = event.target.closest("[data-session-id]");
  if (row) {
    state.selectedSessionId = row.dataset.sessionId;
    renderDetails();
  }
});

el.detailsBody.addEventListener("click", (event) => {
  const copyButton = event.target.closest("[data-copy]");
  if (copyButton) copyText(copyButton.dataset.copy, copyButton.dataset.label || "Copied");
});

el.closeDetails.addEventListener("click", () => {
  state.selectedSessionId = null;
  renderDetails();
});

startEvents();
