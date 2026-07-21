"use strict";

const SECTION_ORDER_DEFAULT = ["coding", "overview", "leaders", "processes", "storage", "runtime"];
const SECTION_LABELS = {
  coding: "AI workloads",
  overview: "Activity and alerts",
  leaders: "Top resource users",
  processes: "Programs and processes",
  storage: "Storage",
  runtime: "Runtime",
};

const state = {
  snapshot: null,
  paused: false,
  view: "overview",
  processMode: "programs",
  processQuery: "",
  processScope: "all",
  processStatus: "all",
  processSortKey: "cpu",
  processSortDir: "desc",
  processLimit: 80,
  selectedPid: null,
  selectedDetail: null,
  eventSource: null,
  fallbackTimer: null,
  thresholdRenderSignature: "",
  historyRange: "live",
  historyModel: null,
  historyRequest: 0,
  services: null,
  serviceQuery: "",
  networkAttribution: null,
  runtimeTimer: null,
  containers: null,
  containerTimer: null,
  expandedAiSessions: new Set(),
  preferences: {
    density: "compact",
    hidden_sections: [],
    show_idle_ai: false,
    section_order: [...SECTION_ORDER_DEFAULT],
    process_columns: ["identity", "pid", "user", "state", "cpu", "memory", "gpu", "disk", "network", "threads", "age"],
  },
  preferenceTimer: null,
};

const el = Object.fromEntries(
  [
    "connectionState", "pauseBtn", "hostLine", "factUptime", "factProcesses", "factLoad",
    "cpuState", "cpuValue", "cpuDetail", "cpuChart", "cpuMeter",
    "gpuState", "gpuValue", "gpuDetail", "gpuChart", "gpuMeter",
    "memoryState", "memoryValue", "memoryDetail", "memoryChart", "memoryMeter",
    "storageState", "storageValue", "storageDetail", "storageChart", "storageMeter",
    "networkValue", "networkDetail", "networkChart", "networkMeter",
    "processValue", "processDetail", "processChart", "processMeter",
    "resourceTrendChart", "diskReadRate", "diskWriteRate", "networkDownRate", "networkUpRate",
    "updateTime", "gpuMetricCard", "gpuLegend",
    "topCpu", "topMemory", "topGpu", "processSummary", "processTotal", "processRunning",
    "processSearch", "processScope", "processStatus", "processTableHead", "processTableBody",
    "visibleProcessCount", "showMoreProcesses", "storageReadRate", "storageWriteRate",
    "storageIops", "storageLatency", "diskGrid", "diskIoChart", "sensorList",
    "smartProvider", "smartDeviceList", "deviceIoList", "aiTotals", "aiZones", "detailsDrawer",
    "alertBadge", "alertSummary", "activeAlerts", "thresholdGrid", "saveThresholds",
    "eventCount", "eventTimeline", "persistenceFacts",
    "runtimeSummary", "servicesProvider", "servicesNote", "serviceSearch",
    "refreshServices", "serviceTableBody", "serviceVisibleCount",
    "networkProvider", "networkSummary", "networkNote", "refreshNetwork",
    "networkTableBody", "networkVisibleCount",
    "containerProvider", "containerSummary", "containerNote", "refreshContainers",
    "containerGrid", "containerPanel", "thirdLeaderCard", "thirdLeaderGlyph",
    "thirdLeaderTitle", "thirdLeaderNote", "showIdleAiToggle", "customizeMenu",
    "resetPreferences", "sectionOrderList",
    "drawerScrim", "detailsTitle", "detailsBody", "closeDetails", "toast",
  ].map((id) => [id, document.getElementById(id)])
);

const COLORS = {
  cpu: "var(--resource-cpu)",
  memory: "var(--resource-memory)",
  gpu: "var(--resource-gpu)",
  storage: "var(--resource-storage)",
  network: "var(--resource-network)",
  process: "var(--resource-process)",
  read: "var(--io-read)",
  write: "var(--io-write)",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatBytes(bytes, digits = 1) {
  const number = Number(bytes || 0);
  const absolute = Math.abs(number);
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = absolute;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const precision = index <= 1 || value >= 100 ? 0 : digits;
  const sign = number < 0 ? "-" : "";
  return `${sign}${value.toFixed(precision).replace(/\.0$/, "")}${units[index]}`;
}

function formatRate(bytesPerSecond) {
  return `${formatBytes(bytesPerSecond)}/s`;
}

function formatPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${Number(value).toFixed(digits)}%`;
}

function formatNumber(value) {
  return new Intl.NumberFormat("en-US").format(Number(value || 0));
}

function formatDuration(seconds) {
  let remaining = Math.max(0, Math.floor(Number(seconds || 0)));
  const days = Math.floor(remaining / 86400);
  remaining %= 86400;
  const hours = Math.floor(remaining / 3600);
  remaining %= 3600;
  const minutes = Math.floor(remaining / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${remaining % 60}s`;
  return `${remaining}s`;
}

function formatEventTime(timestamp) {
  const date = new Date(Number(timestamp || 0) * 1000);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("en-US", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).format(date);
}

function clamp(value, min = 0, max = 100) {
  return Math.max(min, Math.min(max, Number(value || 0)));
}

function initials(value) {
  const clean = String(value || "process").trim();
  const parts = clean.split(/[\s._-]+/).filter(Boolean);
  if (parts.length > 1) return `${parts[0][0]}${parts[1][0]}`.slice(0, 2);
  return clean.slice(0, 2);
}

function statusLabel(status) {
  return {
    running: "Running",
    sleeping: "Sleeping",
    stopped: "Stopped",
    zombie: "Zombie",
    idle: "Idle",
    disk_sleep: "I/O wait",
    waking: "Waking",
    locked: "Locked",
    waiting: "Waiting",
    unknown: "Unknown",
  }[status] || status || "Unknown";
}

function pressureState(value, available = true) {
  if (!available) return { label: "Unavailable", className: "" };
  const number = Number(value || 0);
  if (number >= 85) return { label: "High", className: "is-high" };
  if (number >= 65) return { label: "Elevated", className: "is-medium" };
  return { label: "Normal", className: "is-good" };
}

function setMetricState(node, value, available = true) {
  const current = pressureState(value, available);
  node.textContent = current.label;
  node.className = `metric-state ${current.className}`.trim();
}

function setMeter(node, value) {
  node.style.width = `${clamp(value)}%`;
}

function setGauge(node, value, label = null) {
  const percent = clamp(value);
  node.style.setProperty("--gauge-value", `${percent}%`);
  const text = node.querySelector("span");
  if (text) text.textContent = label ?? `${Math.round(percent)}%`;
}

function normalizedPoints(values, scaleMax, height = 40, width = 100, pad = 2) {
  const list = Array.isArray(values) ? values.map((value) => Number(value || 0)) : [];
  if (!list.length) return [];
  const usableWidth = width - pad * 2;
  const usableHeight = height - pad * 2;
  const denominator = Math.max(1, scaleMax);
  return list.map((value, index) => {
    const x = list.length === 1 ? width / 2 : pad + (index / (list.length - 1)) * usableWidth;
    const y = height - pad - clamp(value, 0, denominator) / denominator * usableHeight;
    return [x, y];
  });
}

function renderChart(series, options = {}) {
  const height = options.height || 40;
  const width = 100;
  const allValues = series.flatMap((item) => item.values || []);
  const scaleMax = options.scaleMax || Math.max(1, ...allValues.map((value) => Number(value || 0)));
  const grid = options.grid
    ? [0.25, 0.5, 0.75].map((ratio) => `<line class="chart-grid" x1="0" x2="100" y1="${height * ratio}" y2="${height * ratio}"></line>`).join("")
    : "";
  const drawings = series.map((item) => {
    const points = normalizedPoints(item.values, scaleMax, height, width, 2);
    if (!points.length) return "";
    const pointText = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
    const [lastX, lastY] = points[points.length - 1];
    const areaPath = `M ${points[0][0].toFixed(2)} ${height} L ${pointText.replaceAll(",", " ")} L ${lastX.toFixed(2)} ${height} Z`;
    return `
      ${item.fill === false ? "" : `<path class="chart-area" d="${areaPath}" fill="${item.color}"></path>`}
      <polyline class="chart-line" points="${pointText}" style="--chart-color:${item.color}"></polyline>
      <circle class="chart-end" cx="${lastX.toFixed(2)}" cy="${lastY.toFixed(2)}" r="1.35" style="--chart-color:${item.color}"></circle>`;
  }).join("");
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img">${grid}${drawings}</svg>`;
}

function setConnection(label, mode = "live") {
  el.connectionState.querySelector("span:last-child").textContent = label;
  el.connectionState.classList.toggle("is-waiting", mode === "waiting");
  el.connectionState.classList.toggle("is-error", mode === "error");
  el.connectionState.classList.toggle("is-paused", mode === "paused");
}

function showToast(message, isError = false) {
  el.toast.textContent = message;
  el.toast.classList.toggle("is-error", isError);
  el.toast.classList.add("is-visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.toast.classList.remove("is-visible"), 2200);
}

async function copyText(value, label = "Copied") {
  const text = String(value || "");
  if (!text) {
    showToast("There is nothing to copy", true);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  showToast(label);
}

function setView(view) {
  state.view = view;
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    const active = panel.dataset.viewPanel === view;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (state.snapshot) renderCurrentView();
  if (view === "events") fetchHistory(state.historyRange);
  if (view === "runtime" && !state.services) fetchServices();
  if (view === "runtime") {
    if (!state.containers) fetchContainers();
    fetchNetwork(Boolean(state.networkAttribution));
    if (!state.runtimeTimer) {
      state.runtimeTimer = window.setInterval(() => {
        if (state.view === "runtime" && !state.paused) fetchNetwork(true);
      }, 4000);
    }
    if (!state.containerTimer) {
      state.containerTimer = window.setInterval(() => {
        if (state.view === "runtime" && !state.paused) fetchContainers(true);
      }, 8000);
    }
  } else {
    if (state.runtimeTimer) {
      window.clearInterval(state.runtimeTimer);
      state.runtimeTimer = null;
    }
    if (state.containerTimer) {
      window.clearInterval(state.containerTimer);
      state.containerTimer = null;
    }
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderOverview(snapshot) {
  const system = snapshot.system || {};
  const cpu = system.cpu || {};
  const memory = system.memory || {};
  const swap = system.swap || {};
  const gpu = system.gpu || {};
  const disks = system.disks || [];
  const systemDisk = disks.find((disk) => disk.is_system) || disks[0] || {};
  const diskIo = system.disk_io || {};
  const network = system.network || {};
  const processSummary = system.process_summary || {};
  const history = system.history || {};
  const cpuValue = Number(cpu.percent || 0);
  const memoryValue = Number(memory.percent || 0);
  const gpuAvailable = Boolean(gpu.available && gpu.utilization_percent !== null && gpu.utilization_percent !== undefined);
  const gpuValue = gpuAvailable ? Number(gpu.utilization_percent || 0) : 0;
  const diskValue = Number(systemDisk.percent || 0);

  el.hostLine.textContent = `${system.user || "user"}@${system.hostname || "localhost"} · ${system.platform || "Local system"}`;
  el.factUptime.textContent = formatDuration(system.uptime_seconds || 0);
  el.factProcesses.textContent = formatNumber(processSummary.total || snapshot.processes?.items?.length || 0);
  el.factLoad.textContent = Number(cpu.load_1 || 0).toFixed(2);

  el.cpuValue.textContent = cpuValue.toFixed(0);
  el.cpuDetail.textContent = `${system.physical_cpus || "--"} physical · ${system.logical_cpus || "--"} logical${cpu.frequency_mhz ? ` · ${(cpu.frequency_mhz / 1000).toFixed(2)}GHz` : ""}`;
  setMetricState(el.cpuState, cpuValue);
  setMeter(el.cpuMeter, cpuValue);
  setGauge(el.cpuChart, cpuValue);

  el.gpuValue.textContent = gpuAvailable ? gpuValue.toFixed(0) : "--";
  const gpuDevice = gpu.devices?.[0];
  el.gpuDetail.textContent = gpuAvailable
    ? `${gpuDevice?.name || "GPU"} · ${gpu.provider || "provider"}${gpu.memory_used_bytes !== null && gpu.memory_used_bytes !== undefined ? ` · ${formatBytes(gpu.memory_used_bytes)} used` : ""}`
    : gpu.note || "No supported GPU telemetry provider";
  setMetricState(el.gpuState, gpuValue, gpuAvailable);
  setMeter(el.gpuMeter, gpuValue);
  setGauge(el.gpuChart, gpuValue, gpuAvailable ? null : "N/A");
  el.gpuMetricCard.hidden = !gpuAvailable;
  el.gpuLegend.hidden = !gpuAvailable;

  el.memoryValue.textContent = memoryValue.toFixed(0);
  el.memoryDetail.textContent = `${formatBytes(memory.used_bytes)} used · ${formatBytes(memory.available_bytes)} free${swap.total_bytes ? ` · swap ${formatPct(swap.percent, 0)}` : ""}`;
  setMetricState(el.memoryState, memoryValue);
  setMeter(el.memoryMeter, memoryValue);
  setGauge(el.memoryChart, memoryValue);

  el.storageValue.textContent = disks.length ? diskValue.toFixed(0) : "--";
  el.storageDetail.textContent = disks.length
    ? `${formatBytes(systemDisk.used_bytes)} used · ${formatBytes(systemDisk.free_bytes)} free · ${systemDisk.mountpoint || "Local"}`
    : "No readable local mount point";
  setMetricState(el.storageState, diskValue, Boolean(disks.length));
  setMeter(el.storageMeter, diskValue);
  setGauge(el.storageChart, diskValue, disks.length ? null : "N/A");

  const downloadRate = Number(network.download_bytes_per_s || 0);
  const uploadRate = Number(network.upload_bytes_per_s || 0);
  const combinedNetwork = downloadRate + uploadRate;
  const networkHistory = (history.network_download_bytes_per_s || []).map((value, index) =>
    Number(value || 0) + Number((history.network_upload_bytes_per_s || [])[index] || 0)
  );
  const networkPeak = Math.max(combinedNetwork, ...networkHistory, 1);
  const networkGauge = combinedNetwork / networkPeak * 100;
  el.networkValue.textContent = formatRate(combinedNetwork);
  el.networkDetail.textContent = `Down ${formatRate(downloadRate)} · Up ${formatRate(uploadRate)}`;
  setMeter(el.networkMeter, networkGauge);
  setGauge(el.networkChart, networkGauge, "I/O");

  const processTotal = Number(processSummary.total || snapshot.processes?.items?.length || 0);
  const processRunning = Number(processSummary.running || 0);
  const runningShare = processTotal ? processRunning / processTotal * 100 : 0;
  el.processValue.textContent = formatNumber(processTotal);
  el.processDetail.textContent = `${formatNumber(processRunning)} running · ${formatNumber(processSummary.threads || 0)} threads`;
  setMeter(el.processMeter, runningShare);
  setGauge(el.processChart, runningShare, `${formatNumber(processRunning)} run`);

  if (state.historyRange === "live") {
    const liveSeries = [
      { values: history.cpu_percent || [], color: COLORS.cpu },
      { values: history.memory_percent || [], color: COLORS.memory, fill: false },
    ];
    if (gpuAvailable) liveSeries.push({ values: history.gpu_percent || [], color: COLORS.gpu, fill: false });
    el.resourceTrendChart.classList.remove("skeleton-block");
    el.resourceTrendChart.innerHTML = renderChart(liveSeries, { scaleMax: 100, height: 64, grid: true });
    el.persistenceFacts.innerHTML = `<span><b>${formatNumber(history.cpu_percent?.length || 0)}</b> in-memory samples</span><span><b>${Number(snapshot.refresh_interval || 0).toFixed(1)}s</b> refresh</span><span><b>Live</b> current session</span>`;
  }

  el.diskReadRate.textContent = formatRate(diskIo.read_bytes_per_s || 0);
  el.diskWriteRate.textContent = formatRate(diskIo.write_bytes_per_s || 0);
  el.networkDownRate.textContent = formatRate(network.download_bytes_per_s || 0);
  el.networkUpRate.textContent = formatRate(network.upload_bytes_per_s || 0);
  el.updateTime.textContent = new Date((snapshot.generated_at || 0) * 1000).toLocaleTimeString("en-US", { hour12: false });

  const resources = snapshot.resources || {};
  el.topCpu.innerHTML = renderLeaders(resources.top_cpu || [], "cpu");
  el.topMemory.innerHTML = renderLeaders(resources.top_memory || [], "memory");
  renderThirdLeader(resources, gpu);
}

function networkLeaderItems() {
  return (state.networkAttribution?.items || []).filter((item) =>
    Number(item.received_bytes_per_s || 0) + Number(item.sent_bytes_per_s || 0) > 0 || Number(item.connection_count || 0) > 0
  );
}

function renderThirdLeader(resources, gpu) {
  const gpuItems = resources.top_gpu || [];
  if (gpuItems.length) {
    el.thirdLeaderCard.classList.add("leader-gpu");
    el.thirdLeaderCard.classList.remove("leader-network");
    el.thirdLeaderGlyph.textContent = "G";
    el.thirdLeaderTitle.textContent = "GPU";
    el.thirdLeaderNote.textContent = "Attributed utilization";
    el.topGpu.innerHTML = renderLeaders(gpuItems, "gpu", gpu.note);
    return;
  }
  el.thirdLeaderCard.classList.add("leader-network");
  el.thirdLeaderCard.classList.remove("leader-gpu");
  el.thirdLeaderGlyph.textContent = "N";
  el.thirdLeaderTitle.textContent = "Network";
  el.thirdLeaderNote.textContent = state.networkAttribution?.throughput_available ? "Per-process throughput" : "Attributed sockets";
  el.topGpu.innerHTML = renderNetworkLeaders(networkLeaderItems());
}

function renderNetworkLeaders(items) {
  if (!items.length) return `<div class="empty-state compact">No attributable process network activity</div>`;
  const throughput = Boolean(state.networkAttribution?.throughput_available);
  const sorted = [...items].sort((a, b) => {
    const aValue = throughput ? Number(a.received_bytes_per_s || 0) + Number(a.sent_bytes_per_s || 0) : Number(a.connection_count || 0);
    const bValue = throughput ? Number(b.received_bytes_per_s || 0) + Number(b.sent_bytes_per_s || 0) : Number(b.connection_count || 0);
    return bValue - aValue;
  }).slice(0, 5);
  const max = Math.max(1, ...sorted.map((item) => throughput
    ? Number(item.received_bytes_per_s || 0) + Number(item.sent_bytes_per_s || 0)
    : Number(item.connection_count || 0)));
  return sorted.map((item, index) => {
    const value = throughput ? Number(item.received_bytes_per_s || 0) + Number(item.sent_bytes_per_s || 0) : Number(item.connection_count || 0);
    return `<button class="leader-row" data-pid="${Number(item.pid || 0)}" type="button" style="--leader-width:${Math.max(3, value / max * 100).toFixed(1)}%;--leader-color:${COLORS.network}">
      <span class="leader-rank">${String(index + 1).padStart(2, "0")}</span>
      <span class="leader-name">${escapeHtml(item.name || `PID ${item.pid}`)}<small>PID ${Number(item.pid || 0)} · ${formatNumber(item.connection_count || 0)} sockets</small></span>
      <span class="leader-value">${throughput ? formatRate(value) : formatNumber(value)}</span>
    </button>`;
  }).join("");
}

function leaderMetric(item, type) {
  if (type === "memory") return { value: Number(item.memory_bytes || 0), label: formatBytes(item.memory_bytes || 0) };
  if (type === "gpu") {
    if (item.gpu_percent !== null && item.gpu_percent !== undefined) return { value: Number(item.gpu_percent), label: formatPct(item.gpu_percent) };
    return { value: Number(item.gpu_memory_bytes || 0), label: formatBytes(item.gpu_memory_bytes || 0) };
  }
  return { value: Number(item.cpu_capacity_percent || 0), label: formatPct(item.cpu_capacity_percent || 0) };
}

function renderLeaders(items, type, emptyNote = "") {
  if (!items.length) {
    return `<div class="empty-state compact">${escapeHtml(emptyNote || "No attributable application data")}</div>`;
  }
  const metrics = items.map((item) => leaderMetric(item, type));
  const max = Math.max(1, ...metrics.map((metric) => metric.value));
  return items.slice(0, 5).map((item, index) => {
    const metric = metrics[index];
    const width = Math.max(3, Math.min(100, metric.value / max * 100));
    const detail = item.process_count > 1 ? `${item.process_count} processes` : `PID ${item.primary_pid}`;
    return `
      <button class="leader-row" data-pid="${Number(item.primary_pid || 0)}" type="button" style="--leader-width:${width.toFixed(1)}%;--leader-color:${type === "memory" ? COLORS.memory : type === "gpu" ? COLORS.gpu : COLORS.cpu}">
        <span class="leader-rank">${String(index + 1).padStart(2, "0")}</span>
        <span class="leader-name">${escapeHtml(item.label || `PID ${item.primary_pid}`)}<small>${escapeHtml(detail)}</small></span>
        <span class="leader-value">${escapeHtml(metric.label)}</span>
      </button>`;
  }).join("");
}

function processSearchMatch(item, mode) {
  if (!state.processQuery) return true;
  const query = state.processQuery.toLowerCase();
  const haystack = mode === "programs"
    ? [item.label, item.primary_pid, ...(item.pids || []), ...(item.usernames || []), ...(item.members || []).map((member) => `${member.name} ${member.cmdline}`)]
    : [item.label, item.name, item.pid, item.ppid, item.username, item.cmdline, item.cwd, item.exe];
  return haystack.join(" ").toLowerCase().includes(query);
}

function processScopeMatch(item, mode) {
  if (state.processScope !== "mine") return true;
  const currentUser = state.snapshot?.system?.user || "";
  return mode === "programs" ? (item.usernames || []).includes(currentUser) : item.username === currentUser;
}

function processStatusMatch(item, mode) {
  if (mode === "programs" || state.processStatus === "all") return true;
  return item.status === state.processStatus;
}

function processSortValue(item, key, mode) {
  if (key === "name") return String(mode === "programs" ? item.label : item.label || item.name || "").toLowerCase();
  if (key === "pid") return Number(mode === "programs" ? item.primary_pid : item.pid || 0);
  if (key === "memory") return Number(item.memory_bytes || 0);
  if (key === "gpu") return Number(item.gpu_percent ?? item.gpu_memory_bytes ?? 0);
  if (key === "disk") return Number(item.read_bytes_per_s || 0) + Number(item.write_bytes_per_s || 0);
  if (key === "network") return networkForPids(mode === "programs" ? item.pids || [] : [item.pid]).total;
  if (key === "threads") return Number(item.threads || 0);
  if (key === "age") return Number(item.age_seconds || 0);
  return Number(item.cpu_capacity_percent || 0);
}

function sortedProcessItems(items, mode) {
  const direction = state.processSortDir === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    const aValue = processSortValue(a, state.processSortKey, mode);
    const bValue = processSortValue(b, state.processSortKey, mode);
    if (typeof aValue === "string") {
      const result = aValue.localeCompare(bValue, "en-US");
      if (result) return result * direction;
    } else if (aValue !== bValue) {
      return (aValue - bValue) * direction;
    }
    return Number((mode === "programs" ? a.primary_pid : a.pid) || 0) - Number((mode === "programs" ? b.primary_pid : b.pid) || 0);
  });
}

function sortButton(key, label, numeric = false) {
  const active = state.processSortKey === key;
  const marker = active ? (state.processSortDir === "asc" ? "↑" : "↓") : "";
  return `<button class="sort-control ${active ? "is-active" : ""}" data-process-sort="${key}" type="button"><span>${escapeHtml(label)}</span><span aria-hidden="true">${marker}</span></button>`;
}

function selectedProcessColumns(mode) {
  const selected = new Set(state.preferences.process_columns || []);
  selected.add("identity");
  const gpuAvailable = Boolean(state.snapshot?.system?.gpu?.available);
  const networkAvailable = Boolean(state.networkAttribution?.available && state.networkAttribution?.items?.length);
  const order = mode === "programs"
    ? ["identity", "pid", "user", "cpu", "memory", "gpu", "disk", "network"]
    : ["identity", "pid", "user", "state", "cpu", "memory", "gpu", "disk", "network", "threads", "age"];
  return order.filter((key) => selected.has(key) && (key !== "gpu" || gpuAvailable) && (key !== "network" || networkAvailable));
}

function processHeadCell(key, mode) {
  const numeric = ["pid", "cpu", "memory", "gpu", "disk", "network", "threads", "age"].includes(key);
  const labels = mode === "programs"
    ? { identity: "Application / command", pid: "Procs", user: "User", cpu: "CPU", memory: "Memory", gpu: "GPU", disk: "Disk R/W", network: "Network ↓/↑" }
    : { identity: mode === "tree" ? "Process tree / command" : "Process / command", pid: "PID / PPID", user: "User", state: "State", cpu: "CPU", memory: "RSS", gpu: "GPU", disk: "Disk R/W", network: "Network ↓/↑", threads: "Threads", age: "Age" };
  const sortable = ["identity", "pid", "cpu", "memory", "gpu", "disk", "network", "threads", "age"].includes(key);
  const sortKey = key === "identity" ? "name" : key;
  return `<th class="${numeric ? "num" : ""}">${sortable ? sortButton(sortKey, labels[key] || key) : escapeHtml(labels[key] || key)}</th>`;
}

function renderProcessHead(mode) {
  return `<tr>${selectedProcessColumns(mode).map((key) => processHeadCell(key, mode)).join("")}</tr>`;
}

function usageCell(value, label, color = COLORS.cpu) {
  const percent = clamp(value);
  return `<div class="usage-cell"><span class="usage-bar"><span style="width:${percent}%;background:${color}"></span></span><span class="mono">${escapeHtml(label)}</span></div>`;
}

function networkForPids(pids) {
  const wanted = new Set((pids || []).map(Number));
  const matched = (state.networkAttribution?.items || []).filter((item) => wanted.has(Number(item.pid)));
  const down = matched.reduce((sum, item) => sum + Number(item.received_bytes_per_s || 0), 0);
  const up = matched.reduce((sum, item) => sum + Number(item.sent_bytes_per_s || 0), 0);
  return { down, up, total: down + up, connections: matched.reduce((sum, item) => sum + Number(item.connection_count || 0), 0) };
}

function ratePair(down, up) {
  return `<span class="stacked-stat"><span>↓ ${formatRate(down)}</span><span>↑ ${formatRate(up)}</span></span>`;
}

function renderProgramRow(item) {
  const users = item.usernames || [];
  const gpuLabel = item.gpu_percent !== null && item.gpu_percent !== undefined
    ? formatPct(item.gpu_percent)
    : item.gpu_memory_bytes ? formatBytes(item.gpu_memory_bytes) : "--";
  const network = networkForPids(item.pids || []);
  const cells = {
    identity: `<td><div class="process-name-cell"><span class="process-avatar">${escapeHtml(initials(item.label))}</span><span class="process-name">${escapeHtml(item.label || "Unknown application")}<span class="process-sub">PID ${Number(item.primary_pid || 0)} · ${escapeHtml(item.detail || "grouped application")}</span></span></div></td>`,
    pid: `<td class="num mono">${formatNumber(item.process_count || 0)}</td>`,
    user: `<td title="${escapeHtml(users.join(", "))}">${escapeHtml(users.length > 1 ? `${users[0]} +${users.length - 1}` : users[0] || "--")}</td>`,
    cpu: `<td class="num">${usageCell(item.cpu_capacity_percent || 0, formatPct(item.cpu_capacity_percent || 0))}</td>`,
    memory: `<td class="num mono">${formatBytes(item.memory_bytes || 0)}</td>`,
    gpu: `<td class="num mono">${gpuLabel}</td>`,
    disk: `<td class="num mono">${ratePair(item.read_bytes_per_s || 0, item.write_bytes_per_s || 0)}</td>`,
    network: `<td class="num mono">${ratePair(network.down, network.up)}</td>`,
  };
  return `<tr class="process-row" data-pid="${Number(item.primary_pid || 0)}" tabindex="0">${selectedProcessColumns("programs").map((key) => cells[key] || "").join("")}</tr>`;
}

function renderProcessRow(item, treeDepth = null, hasChildren = false) {
  const gpuLabel = item.gpu_percent !== null && item.gpu_percent !== undefined
    ? formatPct(item.gpu_percent)
    : item.gpu_memory_bytes ? formatBytes(item.gpu_memory_bytes) : "--";
  const treePrefix = treeDepth === null ? "" : `<span class="tree-indent" style="--tree-depth:${Math.min(12, treeDepth)}"><i>${hasChildren ? "⌄" : "·"}</i></span>`;
  const network = networkForPids([item.pid]);
  const mode = treeDepth === null ? "processes" : "tree";
  const cells = {
    identity: `<td title="${escapeHtml(item.cmdline || "")}"><div class="process-name-cell">${treePrefix}<span class="process-avatar">${escapeHtml(initials(item.label || item.name))}</span><span class="process-name">${escapeHtml(item.label || item.name || `PID ${item.pid}`)}<span class="process-sub">${escapeHtml(item.cmdline || "Command unavailable")}</span></span></div></td>`,
    pid: `<td class="num mono"><span class="stacked-stat"><span>${Number(item.pid || 0)}</span><span>ppid ${Number(item.ppid || 0)}</span></span></td>`,
    user: `<td title="${escapeHtml(item.username || "")}">${escapeHtml(item.username || "--")}</td>`,
    state: `<td><span class="status-badge status-${escapeHtml(item.status || "unknown")}">${escapeHtml(statusLabel(item.status))}</span></td>`,
    cpu: `<td class="num">${usageCell(item.cpu_capacity_percent || 0, formatPct(item.cpu_capacity_percent || 0))}</td>`,
    memory: `<td class="num mono">${formatBytes(item.memory_bytes || 0)}</td>`,
    gpu: `<td class="num mono">${gpuLabel}</td>`,
    disk: `<td class="num mono">${ratePair(item.read_bytes_per_s || 0, item.write_bytes_per_s || 0)}</td>`,
    network: `<td class="num mono">${ratePair(network.down, network.up)}</td>`,
    threads: `<td class="num mono">${formatNumber(item.threads || 0)}</td>`,
    age: `<td class="num mono">${formatDuration(item.age_seconds || 0)}</td>`,
  };
  return `<tr class="process-row ${treeDepth === null ? "" : "tree-row"}" data-pid="${Number(item.pid || 0)}" tabindex="0">${selectedProcessColumns(mode).map((key) => cells[key] || "").join("")}</tr>`;
}

function processTreeRows(items) {
  const byPid = new Map(items.map((item) => [Number(item.pid), item]));
  const matched = new Set(items.filter((item) =>
    processSearchMatch(item, "tree") && processScopeMatch(item, "tree") && processStatusMatch(item, "tree")
  ).map((item) => Number(item.pid)));
  const included = new Set(matched);
  for (const pid of matched) {
    let cursor = byPid.get(pid);
    const trail = new Set([pid]);
    while (cursor && byPid.has(Number(cursor.ppid)) && !trail.has(Number(cursor.ppid))) {
      const parentPid = Number(cursor.ppid);
      included.add(parentPid);
      trail.add(parentPid);
      cursor = byPid.get(parentPid);
    }
  }
  const children = new Map();
  for (const pid of included) {
    const item = byPid.get(pid);
    if (!item) continue;
    const parentPid = Number(item.ppid);
    if (!children.has(parentPid)) children.set(parentPid, []);
    children.get(parentPid).push(item);
  }
  const roots = [...included]
    .map((pid) => byPid.get(pid))
    .filter((item) => item && (!included.has(Number(item.ppid)) || Number(item.ppid) === Number(item.pid)));
  const rows = [];
  const visited = new Set();
  function visit(item, depth) {
    const pid = Number(item.pid);
    if (visited.has(pid)) return;
    visited.add(pid);
    const childItems = sortedProcessItems(children.get(pid) || [], "tree");
    rows.push({ item, depth, hasChildren: childItems.length > 0 });
    childItems.forEach((child) => visit(child, depth + 1));
  }
  sortedProcessItems(roots, "tree").forEach((root) => visit(root, 0));
  return rows;
}

function renderProcesses(snapshot) {
  const mode = state.processMode;
  const source = mode === "programs" ? snapshot.resources?.programs || [] : snapshot.processes?.items || [];
  const filtered = mode === "tree" ? processTreeRows(source) : sortedProcessItems(source.filter((item) =>
    processSearchMatch(item, mode) && processScopeMatch(item, mode) && processStatusMatch(item, mode)
  ), mode);
  const visible = filtered.slice(0, state.processLimit);
  const summary = snapshot.system?.process_summary || {};
  const tree = snapshot.processes?.tree || {};

  el.processSummary.textContent = mode === "programs" ? `${formatNumber(source.length)} application groups with helper processes merged.`
    : mode === "tree" ? `${formatNumber(tree.root_count || 0)} roots · ${formatNumber(tree.max_depth || 0)} levels; filters retain parent context.`
    : `${formatNumber(source.length)} readable processes. Select a row for full resource details.`;
  el.processTotal.textContent = formatNumber(summary.total || snapshot.processes?.items?.length || 0);
  el.processRunning.textContent = formatNumber(summary.running || 0);
  el.processTableHead.innerHTML = renderProcessHead(mode);
  el.processTableBody.innerHTML = visible.length
    ? visible.map((row) => mode === "programs" ? renderProgramRow(row) : mode === "tree" ? renderProcessRow(row.item, row.depth, row.hasChildren) : renderProcessRow(row)).join("")
    : `<tr><td colspan="${selectedProcessColumns(mode).length}"><div class="empty-state">No ${mode === "programs" ? "applications" : "processes"} match the current filters</div></td></tr>`;
  el.visibleProcessCount.textContent = `Showing ${formatNumber(visible.length)} of ${formatNumber(filtered.length)}`;
  el.showMoreProcesses.hidden = visible.length >= filtered.length;
  document.querySelectorAll("[data-process-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.processMode === mode));
  document.querySelectorAll(".process-only").forEach((node) => { node.hidden = mode === "programs"; });
}

function renderStorage(snapshot) {
  const system = snapshot.system || {};
  const disks = system.disks || [];
  const diskIo = system.disk_io || {};
  const storageHealth = system.storage_health || {};
  const history = system.history || {};
  el.storageReadRate.textContent = formatRate(diskIo.read_bytes_per_s || 0);
  el.storageWriteRate.textContent = formatRate(diskIo.write_bytes_per_s || 0);
  el.storageIops.textContent = `${(Number(diskIo.read_iops || 0) + Number(diskIo.write_iops || 0)).toFixed(1)}/s`;
  el.storageLatency.textContent = `R ${Number(diskIo.read_latency_ms || 0).toFixed(1)} · W ${Number(diskIo.write_latency_ms || 0).toFixed(1)} ms`;
  el.diskGrid.innerHTML = disks.length ? disks.map((disk) => `
    <article class="disk-card">
      <div class="disk-head">
        <div class="disk-label"><h3>${escapeHtml(disk.mountpoint || disk.device || "Local disk")}</h3><p>${escapeHtml([disk.device, disk.filesystem, disk.is_system ? "System volume" : ""].filter(Boolean).join(" · "))}</p></div>
        <span class="disk-percent">${formatPct(disk.percent || 0, 0)}</span>
      </div>
      <div class="capacity-bar" aria-label="${formatPct(disk.percent || 0, 0)} used"><span style="width:${clamp(disk.percent)}%"></span></div>
      <div class="disk-numbers">
        <span>Total<b>${formatBytes(disk.total_bytes || 0)}</b></span>
        <span>Used<b>${formatBytes(disk.used_bytes || 0)}</b></span>
        <span>Free<b>${formatBytes(disk.free_bytes || 0)}</b></span>
      </div>
    </article>`).join("") : `<div class="empty-state">No readable local mount points</div>`;

  el.diskIoChart.innerHTML = renderChart([
    { values: history.disk_read_bytes_per_s || [], color: COLORS.read },
    { values: history.disk_write_bytes_per_s || [], color: COLORS.write, fill: false },
  ], { height: 64, grid: true });

  el.smartProvider.textContent = storageHealth.available ? `SMART · ${String(storageHealth.provider || "provider").toUpperCase()}` : "SMART · UNAVAILABLE";
  const smartDevices = storageHealth.devices || [];
  el.smartDeviceList.innerHTML = smartDevices.length ? smartDevices.map((device) => {
    const healthLabel = device.health === "passed" ? "Healthy" : device.health === "failed" ? "Failed" : "Unknown";
    const details = [
      device.temperature_c !== null && device.temperature_c !== undefined ? `${Number(device.temperature_c).toFixed(1)}°C` : null,
      device.percentage_used !== null && device.percentage_used !== undefined ? `${Number(device.percentage_used).toFixed(0)}% life used` : null,
      device.available_spare_percent !== null && device.available_spare_percent !== undefined ? `${Number(device.available_spare_percent).toFixed(0)}% spare` : null,
      device.media_errors !== null && device.media_errors !== undefined ? `${formatNumber(device.media_errors)} media errors` : null,
    ].filter(Boolean);
    return `<div class="device-row">
      <div class="device-title"><strong>${escapeHtml(device.model || device.identifier || device.device)}</strong><span>${escapeHtml([device.device, device.protocol, device.solid_state === true ? "SSD" : device.solid_state === false ? "HDD" : ""].filter(Boolean).join(" · "))}</span></div>
      <span class="health-badge is-${escapeHtml(device.health || "unknown")}">${healthLabel} · ${escapeHtml(device.smart_status || "Unknown")}</span>
      <div class="device-metrics">${details.length ? details.map((detail) => `<span>${escapeHtml(detail)}</span>`).join("") : `<span>The provider returned no detailed SMART attributes</span>`}</div>
    </div>`;
  }).join("") : `<div class="empty-state compact">${escapeHtml(storageHealth.note || "No readable SMART data for this device")}</div>`;

  const ioDevices = diskIo.devices || [];
  el.deviceIoList.innerHTML = ioDevices.length ? ioDevices.map((device) => `
    <div class="device-row io-device-row">
      <div class="device-title"><strong>${escapeHtml(device.device || "disk")}</strong><span>${formatRate(Number(device.read_bytes_per_s || 0) + Number(device.write_bytes_per_s || 0))} total throughput</span></div>
      <div class="device-metrics io-device-metrics">
        <span>Read <b>${Number(device.read_iops || 0).toFixed(1)}</b> IOPS · <b>${Number(device.read_latency_ms || 0).toFixed(2)}</b> ms</span>
        <span>Write <b>${Number(device.write_iops || 0).toFixed(1)}</b> IOPS · <b>${Number(device.write_latency_ms || 0).toFixed(2)}</b> ms</span>
      </div>
    </div>`).join("") : `<div class="empty-state compact">This platform exposes no device-level I/O counters</div>`;

  const sensors = system.sensors || [];
  const battery = system.battery;
  const rows = sensors.map((sensor) => `
    <div class="sensor-row"><span>${escapeHtml(sensor.label || sensor.group)}</span><b>${Number(sensor.current_c || 0).toFixed(1)}°C</b></div>`);
  if (battery) rows.push(`<div class="sensor-row"><span>Battery${battery.plugged ? " · plugged in" : ""}</span><b>${formatPct(battery.percent, 0)}</b></div>`);
  el.sensorList.hidden = rows.length === 0;
  el.sensorList.innerHTML = rows.join("");
}

function serviceStateLabel(stateValue, subState = "") {
  const key = String(stateValue || "unknown");
  const label = {
    running: "Running",
    active: "Active",
    inactive: "Inactive",
    exited: "Exited",
    failed: "Failed",
    activating: "Starting",
    deactivating: "Stopping",
    reloading: "Reloading",
  }[key] || key;
  return subState && subState !== key ? `${label} · ${subState}` : label;
}

function renderServices(model) {
  const summary = model?.summary || {};
  const query = state.serviceQuery.toLowerCase();
  const source = model?.items || [];
  const items = source.filter((item) => [item.name, item.id, item.description, item.state, item.sub_state]
    .join(" ").toLowerCase().includes(query));
  el.servicesProvider.textContent = model?.available ? `${String(model.provider || "service manager").toUpperCase()} · ${String(model.scope || "")}` : "UNAVAILABLE";
  el.servicesNote.textContent = model?.note || "No system service provider is available on this platform.";
  renderRuntimeSummary();
  el.serviceTableBody.innerHTML = items.length ? items.map((item) => {
    const statusClass = item.state === "running" || item.state === "active" ? "running" : item.state === "failed" ? "zombie" : "stopped";
    return `<tr>
      <td><div class="service-name"><strong>${escapeHtml(item.name || item.id)}</strong><span>${escapeHtml(item.description || item.id || "--")}</span></div></td>
      <td><span class="status-badge status-${statusClass}">${escapeHtml(serviceStateLabel(item.state, item.sub_state))}</span></td>
      <td class="num mono">${item.pid === null || item.pid === undefined ? "--" : Number(item.pid)}</td>
      <td class="num mono">${item.status_code === null || item.status_code === undefined ? "--" : Number(item.status_code)}</td>
      <td>${escapeHtml(item.scope || model.scope || "--")}</td>
    </tr>`;
  }).join("") : `<tr><td colspan="5"><div class="empty-state">${model?.available ? "No system services match the search" : escapeHtml(model?.note || "Service inventory unavailable")}</div></td></tr>`;
  el.serviceVisibleCount.textContent = `Showing ${formatNumber(items.length)} of ${formatNumber(source.length)}`;
}

function renderRuntimeSummary() {
  const serviceSummary = state.services?.summary || {};
  const networkSummaryModel = state.networkAttribution?.summary || {};
  const containerSummaryModel = state.containers?.summary || {};
  const facts = [];
  if (state.services?.available) facts.push(`<span><b>${formatNumber(serviceSummary.running || 0)}</b> running services</span>`);
  if (state.networkAttribution?.available) facts.push(`<span><b>${formatNumber(networkSummaryModel.process_count || 0)}</b> network processes</span>`);
  if (state.containers?.installed) facts.push(`<span><b>${formatNumber(containerSummaryModel.running || 0)}</b> running containers</span>`);
  el.runtimeSummary.innerHTML = facts.join("") || `<span><b>0</b> available providers</span>`;
}

function renderContainers(model) {
  const summary = model?.summary || {};
  const items = model?.items || [];
  el.containerPanel.hidden = model?.installed === false;
  el.containerProvider.textContent = `${String(model?.provider || "CONTAINER RUNTIME").toUpperCase()} · ${model?.available ? "CONNECTED" : model?.installed ? "OFFLINE" : "NOT INSTALLED"}`;
  el.containerNote.textContent = model?.note || "No container runtime is available.";
  el.containerSummary.innerHTML = `
    <span><small>Running</small><b>${model?.available ? `${formatNumber(summary.running || 0)} / ${formatNumber(summary.total || 0)}` : "--"}</b></span>
    <span><small>CPU</small><b>${model?.metrics_available ? formatPct(summary.cpu_percent || 0) : "--"}</b></span>
    <span><small>Memory</small><b>${model?.metrics_available ? formatBytes(summary.memory_usage_bytes || 0) : "--"}</b></span>
    <span><small>Network I/O</small><b>${model?.metrics_available ? `${formatBytes(summary.network_received_bytes || 0)} ↓ · ${formatBytes(summary.network_sent_bytes || 0)} ↑` : "--"}</b></span>`;
  el.containerGrid.innerHTML = items.length ? items.map((item) => `
    <article class="container-card ${item.running ? "is-running" : ""}">
      <div class="container-head">
        <div class="container-identity"><span class="container-cube" aria-hidden="true">◇</span><div><h4>${escapeHtml(item.name || item.short_id || "container")}</h4><p>${escapeHtml(item.image || item.short_id || "--")}</p></div></div>
        <span class="health-badge ${item.running ? "is-passed" : ""}">${escapeHtml(item.state || "unknown")}</span>
      </div>
      <div class="container-metrics">
        <span><small>CPU</small><b>${item.cpu_percent === null || item.cpu_percent === undefined ? "--" : formatPct(item.cpu_percent)}</b></span>
        <span><small>Memory</small><b>${item.memory_usage_bytes === null || item.memory_usage_bytes === undefined ? "--" : formatBytes(item.memory_usage_bytes)}</b></span>
        <span><small>Network</small><b>${item.network_received_bytes === null || item.network_received_bytes === undefined ? "--" : `${formatBytes(item.network_received_bytes)} / ${formatBytes(item.network_sent_bytes || 0)}`}</b></span>
        <span><small>PIDs</small><b>${formatNumber(item.pid_count || 0)}</b></span>
      </div>
      <div class="container-foot"><span>${escapeHtml(item.status || "--")}</span><span title="${escapeHtml(item.ports || "")}">${escapeHtml(item.ports || "No published ports")}</span></div>
    </article>`).join("") : `<div class="empty-state container-empty">${escapeHtml(model?.note || "No containers")}</div>`;
  renderRuntimeSummary();
}

function renderNetworkAttribution(model) {
  const summary = model?.summary || {};
  const items = (model?.items || []).slice(0, 120);
  const throughput = Boolean(model?.throughput_available);
  el.networkProvider.textContent = `${String(model?.provider || "NETWORK").toUpperCase()} · ${throughput ? "THROUGHPUT" : "CONNECTIONS"}`;
  el.networkNote.textContent = model?.note || "Per-process network attribution is unavailable.";
  el.networkSummary.innerHTML = `
    <span><small>Download</small><b>${throughput ? formatRate(summary.received_bytes_per_s || 0) : "Sockets only"}</b></span>
    <span><small>Upload</small><b>${throughput ? formatRate(summary.sent_bytes_per_s || 0) : "Sockets only"}</b></span>
    <span><small>Connections</small><b>${formatNumber(summary.connection_count || 0)}</b></span>
    <span><small>Processes</small><b>${formatNumber(summary.process_count || 0)}</b></span>`;
  el.networkTableBody.innerHTML = items.length ? items.map((item) => {
    const endpoints = item.remote_endpoints || [];
    return `<tr>
      <td><div class="process-name-cell"><span class="process-avatar">${escapeHtml(initials(item.name))}</span><span class="process-name">${escapeHtml(item.name || `PID ${item.pid}`)}<span class="process-sub">${formatNumber(item.established_count || 0)} established · ${formatNumber(item.listen_count || 0)} listen</span></span></div></td>
      <td class="num mono">${Number(item.pid || 0)}</td>
      <td class="num mono">${throughput && item.received_bytes_per_s !== null ? formatRate(item.received_bytes_per_s || 0) : "--"}</td>
      <td class="num mono">${throughput && item.sent_bytes_per_s !== null ? formatRate(item.sent_bytes_per_s || 0) : "--"}</td>
      <td class="num mono">${formatNumber(item.connection_count || 0)}</td>
      <td title="${escapeHtml(endpoints.join(", "))}"><span class="endpoint-list">${escapeHtml(endpoints.slice(0, 2).join(" · ") || "--")}</span></td>
    </tr>`;
  }).join("") : `<tr><td colspan="6"><div class="empty-state">${escapeHtml(model?.note || "No readable per-process network activity")}</div></td></tr>`;
  el.networkVisibleCount.textContent = `Showing ${formatNumber(items.length)} of ${formatNumber(model?.items?.length || 0)}${throughput ? " · rates refresh every 4 seconds" : " · throughput attribution unavailable"}`;
  renderRuntimeSummary();
  if (state.snapshot) {
    renderThirdLeader(state.snapshot.resources || {}, state.snapshot.system?.gpu || {});
    renderProcesses(state.snapshot);
  }
}

function renderRuntime() {
  if (state.containers) renderContainers(state.containers);
  if (state.services) renderServices(state.services);
  if (state.networkAttribution) renderNetworkAttribution(state.networkAttribution);
}

async function fetchServices(force = false) {
  el.refreshServices.disabled = true;
  try {
    const response = await fetch(`/api/services${force ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.services = payload;
    renderServices(payload);
  } catch (error) {
    state.services = { available: false, items: [], summary: {}, note: error.message || "Failed to read services" };
    renderServices(state.services);
  } finally {
    el.refreshServices.disabled = false;
  }
}

async function fetchNetwork(force = false) {
  el.refreshNetwork.disabled = true;
  try {
    const response = await fetch(`/api/network-attribution${force ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.networkAttribution = payload;
    renderNetworkAttribution(payload);
  } catch (error) {
    state.networkAttribution = { available: false, throughput_available: false, items: [], summary: {}, note: error.message || "Failed to read network attribution" };
    renderNetworkAttribution(state.networkAttribution);
  } finally {
    el.refreshNetwork.disabled = false;
  }
}

async function fetchContainers(force = false) {
  el.refreshContainers.disabled = true;
  try {
    const response = await fetch(`/api/containers${force ? "?refresh=1" : ""}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    state.containers = payload;
    renderContainers(payload);
  } catch (error) {
    state.containers = { available: false, installed: false, items: [], summary: {}, note: error.message || "Failed to read containers" };
    renderContainers(state.containers);
  } finally {
    el.refreshContainers.disabled = false;
  }
}

function eventActionLabel(action) {
  return {
    opened: "Opened",
    escalated: "Escalated",
    deescalated: "De-escalated",
    resolved: "Resolved",
  }[action] || action || "Changed";
}

function resourceLabel(resource, fallback = "Resource") {
  return {
    cpu: "CPU utilization",
    memory: "Memory utilization",
    gpu: "GPU utilization",
    disk: "Disk utilization",
    swap: "Swap utilization",
    disk_latency: "Average disk latency",
  }[resource] || fallback;
}

function eventMessage(item) {
  const label = resourceLabel(item.resource, item.label || "Resource");
  const value = item.value === null || item.value === undefined ? null : `${Number(item.value).toFixed(1)}${item.unit || ""}`;
  if (item.action === "resolved" && value === null) return `${label} became unavailable; the alert was closed.`;
  if (item.action === "resolved") return `${label} recovered to ${value}.`;
  if (item.action === "deescalated") return `${label} de-escalated from critical to warning at ${value}.`;
  if (item.action === "escalated") return `${label} escalated to critical at ${value}.`;
  return `${label} crossed the ${item.current_severity || item.severity || "configured"} threshold at ${value}.`;
}

function renderAlertBadge(snapshot) {
  const active = Number(snapshot.observability?.summary?.active || 0);
  el.alertBadge.hidden = active <= 0;
  el.alertBadge.textContent = active > 99 ? "99+" : String(active);
}

function renderEvents(snapshot) {
  const model = snapshot.observability || {};
  const summary = model.summary || {};
  const active = model.active || [];
  const events = model.events || [];
  const thresholds = model.thresholds || {};
  renderAlertBadge(snapshot);

  el.alertSummary.innerHTML = `<span><b>${formatNumber(summary.active || 0)}</b> active</span><span><b>${formatNumber(summary.critical || 0)}</b> critical</span><span><b>${formatNumber(summary.warning || 0)}</b> warning</span>`;
  el.activeAlerts.innerHTML = active.length ? active.slice(0, 2).map((item) => `
    <article class="active-alert is-${escapeHtml(item.severity)}">
      <span class="alert-indicator" aria-hidden="true"></span>
      <div><strong>${escapeHtml(resourceLabel(item.resource, item.label))}</strong><p>Above ${Number(item.threshold || 0).toFixed(0)}${escapeHtml(item.unit)} since ${escapeHtml(formatEventTime(item.since))}</p></div>
      <b>${Number(item.value || 0).toFixed(1)}${escapeHtml(item.unit)}</b>
    </article>`).join("") : `<div class="empty-state">No active resource alerts</div>`;

  const thresholdSignature = JSON.stringify(thresholds);
  if (state.thresholdRenderSignature !== thresholdSignature) {
    state.thresholdRenderSignature = thresholdSignature;
    el.thresholdGrid.innerHTML = Object.entries(thresholds).map(([resource, rule]) => `
      <article class="threshold-card" data-threshold-resource="${escapeHtml(resource)}">
        <div class="threshold-card-head"><strong>${escapeHtml(rule.label || resource)}</strong><label class="switch-label"><input data-threshold-enabled type="checkbox" ${rule.enabled ? "checked" : ""}><span>Enabled</span></label></div>
        <div class="threshold-inputs">
          <label>Warning <span><input data-threshold-warning type="number" min="0" max="99" step="1" value="${Number(rule.warning || 0)}"><i>${escapeHtml(rule.unit || "%")}</i></span></label>
          <label>Critical <span><input data-threshold-critical type="number" min="1" max="100" step="1" value="${Number(rule.critical || 0)}"><i>${escapeHtml(rule.unit || "%")}</i></span></label>
        </div>
      </article>`).join("") || `<div class="empty-state">No configurable thresholds</div>`;
  }

  el.eventCount.textContent = `${formatNumber(events.length)} total`;
  el.eventTimeline.innerHTML = events.length ? events.slice(0, 4).map((item) => `
    <article class="timeline-event is-${escapeHtml(item.severity)} ${item.action === "resolved" ? "is-resolved" : ""}">
      <div class="timeline-marker"><span></span></div>
      <div class="timeline-copy">
        <div><strong>${escapeHtml(resourceLabel(item.resource, item.label))}</strong><span class="event-action">${escapeHtml(eventActionLabel(item.action))}</span><time>${escapeHtml(formatEventTime(item.timestamp))}</time></div>
        <p>${escapeHtml(eventMessage(item))}</p>
      </div>
      <span class="timeline-value">${item.value === null || item.value === undefined ? "--" : `${Number(item.value).toFixed(1)}${escapeHtml(item.unit)}`}</span>
    </article>`).join("") : `<div class="empty-state">No threshold transitions yet. Events appear when a resource crosses a configured threshold.</div>`;
  if (state.historyModel) renderPersistentHistory(state.historyModel);
}

function renderPersistentHistory(model) {
  if (state.historyRange === "live") return;
  const series = model.series || {};
  const persistence = model.persistence || {};
  const pointCount = Number(model.point_count || 0);
  el.resourceTrendChart.classList.remove("skeleton-block");
  el.resourceTrendChart.innerHTML = pointCount ? renderChart([
    { values: series.cpu_percent || [], color: COLORS.cpu },
    { values: series.memory_percent || [], color: COLORS.memory, fill: false },
    { values: series.gpu_percent || [], color: COLORS.gpu, fill: false },
  ], { height: 66, scaleMax: 100, grid: true }) : `<div class="empty-state compact">No persistent samples in this range yet. The database writes every ${Number(persistence.persist_interval_seconds || 5)} seconds.</div>`;
  el.persistenceFacts.innerHTML = persistence.available ? `
    <span><b>${formatNumber(pointCount)}</b> chart points</span>
    <span><b>${formatNumber(persistence.sample_count || 0)}</b> raw samples</span>
    <span><b>${formatBytes(persistence.database_bytes || 0)}</b> SQLite</span>
    <span><b>${Number(model.resolution_seconds || 0)}s</b> resolution</span>
    <span title="${escapeHtml(persistence.path || "")}"><b>${formatNumber(persistence.retention_days || 0)} days</b> retention</span>` : `<span class="is-error">SQLite unavailable: ${escapeHtml(persistence.error || "Could not create the local database")}</span>`;
  document.querySelectorAll("[data-history-range]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.historyRange === state.historyRange);
  });
}

async function fetchHistory(range = "1h") {
  state.historyRange = range;
  const request = ++state.historyRequest;
  document.querySelectorAll("[data-history-range]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.historyRange === range);
  });
  if (range === "live") {
    if (state.snapshot) renderOverview(state.snapshot);
    return;
  }
  el.resourceTrendChart.classList.add("skeleton-block");
  try {
    const response = await fetch(`/api/history?range=${encodeURIComponent(range)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    if (request !== state.historyRequest) return;
    state.historyModel = payload;
    renderPersistentHistory(payload);
  } catch (error) {
    if (request !== state.historyRequest) return;
    el.resourceTrendChart.classList.remove("skeleton-block");
    el.resourceTrendChart.innerHTML = `<div class="empty-state compact">${escapeHtml(error.message || "History query failed")}</div>`;
  }
}

async function saveThresholds() {
  const thresholds = {};
  for (const card of el.thresholdGrid.querySelectorAll("[data-threshold-resource]")) {
    const warning = Number(card.querySelector("[data-threshold-warning]").value);
    const critical = Number(card.querySelector("[data-threshold-critical]").value);
    if (!Number.isFinite(warning) || !Number.isFinite(critical) || warning < 0 || warning >= critical || critical > 100) {
      showToast(`Invalid thresholds for ${card.querySelector("strong").textContent}`, true);
      return;
    }
    thresholds[card.dataset.thresholdResource] = {
      warning,
      critical,
      enabled: card.querySelector("[data-threshold-enabled]").checked,
    };
  }
  el.saveThresholds.disabled = true;
  try {
    const response = await fetch("/api/thresholds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thresholds }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    if (state.snapshot?.observability) state.snapshot.observability.thresholds = payload.thresholds;
    state.thresholdRenderSignature = "";
    renderEvents(state.snapshot);
    showToast("Resource thresholds saved");
  } catch (error) {
    showToast(error.message || "Could not save thresholds", true);
  } finally {
    el.saveThresholds.disabled = false;
  }
}

function normalizePreferences(value = {}) {
  const defaults = {
    density: "compact",
    hidden_sections: [],
    show_idle_ai: false,
    section_order: SECTION_ORDER_DEFAULT,
    process_columns: ["identity", "pid", "user", "state", "cpu", "memory", "gpu", "disk", "network", "threads", "age"],
  };
  const density = value.density === "comfortable" ? "comfortable" : "compact";
  const hidden = Array.isArray(value.hidden_sections) ? value.hidden_sections.filter((item) => ["leaders", "processes", "storage", "runtime", "coding"].includes(item)) : [];
  const columns = Array.isArray(value.process_columns) ? value.process_columns.filter((item) => defaults.process_columns.includes(item)) : defaults.process_columns;
  const requestedOrder = Array.isArray(value.section_order) ? value.section_order : defaults.section_order;
  const sectionOrder = [...new Set(requestedOrder.filter((item) => SECTION_ORDER_DEFAULT.includes(item)))];
  SECTION_ORDER_DEFAULT.forEach((item) => {
    if (!sectionOrder.includes(item)) sectionOrder.push(item);
  });
  return {
    density,
    hidden_sections: [...new Set(hidden)],
    show_idle_ai: Boolean(value.show_idle_ai),
    section_order: sectionOrder,
    process_columns: ["identity", ...columns.filter((item) => item !== "identity")],
  };
}

function renderSectionOrderControls() {
  el.sectionOrderList.innerHTML = state.preferences.section_order.map((id, index, order) => {
    const label = SECTION_LABELS[id] || id;
    return `<div class="section-order-row">
      <span class="section-order-index">${String(index + 1).padStart(2, "0")}</span>
      <span class="section-order-name">${escapeHtml(label)}</span>
      <span class="section-order-actions">
        <button class="order-btn" data-section-move="${escapeHtml(id)}" data-move-direction="-1" type="button" aria-label="Move ${escapeHtml(label)} up" ${index === 0 ? "disabled" : ""}>↑</button>
        <button class="order-btn" data-section-move="${escapeHtml(id)}" data-move-direction="1" type="button" aria-label="Move ${escapeHtml(label)} down" ${index === order.length - 1 ? "disabled" : ""}>↓</button>
      </span>
    </div>`;
  }).join("");
}

function applySectionOrder() {
  const main = document.getElementById("mainContent");
  state.preferences.section_order.forEach((id) => {
    const section = main.querySelector(`[data-dashboard-order="${id}"]`);
    if (section) main.appendChild(section);
  });
  renderSectionOrderControls();
}

function applyPreferences(preferences, rerender = true) {
  state.preferences = normalizePreferences(preferences);
  document.body.dataset.density = state.preferences.density;
  applySectionOrder();
  document.querySelectorAll("[data-dashboard-section]").forEach((section) => {
    section.hidden = state.preferences.hidden_sections.includes(section.dataset.dashboardSection);
  });
  document.querySelectorAll("[data-section-toggle]").forEach((input) => {
    input.checked = !state.preferences.hidden_sections.includes(input.dataset.sectionToggle);
  });
  document.querySelectorAll('input[name="dashboardDensity"]').forEach((input) => {
    input.checked = input.value === state.preferences.density;
  });
  document.querySelectorAll("[data-process-column]").forEach((input) => {
    input.checked = state.preferences.process_columns.includes(input.dataset.processColumn);
  });
  el.showIdleAiToggle.checked = state.preferences.show_idle_ai;
  if (rerender && state.snapshot) {
    renderProcesses(state.snapshot);
    renderAi(state.snapshot);
  }
}

async function fetchPreferences() {
  try {
    const response = await fetch("/api/dashboard-preferences", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    applyPreferences(payload.preferences || {});
  } catch {
    applyPreferences(state.preferences, false);
  }
}

function schedulePreferenceSave() {
  clearTimeout(state.preferenceTimer);
  state.preferenceTimer = window.setTimeout(async () => {
    try {
      const response = await fetch("/api/dashboard-preferences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferences: state.preferences }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      applyPreferences(payload.preferences || state.preferences, false);
    } catch (error) {
      showToast(error.message || "Could not save dashboard preferences", true);
    }
  }, 180);
}

function updatePreferencesFromControls() {
  const hidden = [...document.querySelectorAll("[data-section-toggle]")]
    .filter((input) => !input.checked)
    .map((input) => input.dataset.sectionToggle);
  const columns = ["identity", ...[...document.querySelectorAll("[data-process-column]")]
    .filter((input) => input.checked)
    .map((input) => input.dataset.processColumn)];
  const density = document.querySelector('input[name="dashboardDensity"]:checked')?.value || "compact";
  applyPreferences({
    density,
    hidden_sections: hidden,
    show_idle_ai: el.showIdleAiToggle.checked,
    section_order: state.preferences.section_order,
    process_columns: columns,
  });
  schedulePreferenceSave();
}

function moveDashboardSection(id, direction) {
  const order = [...state.preferences.section_order];
  const index = order.indexOf(id);
  const nextIndex = index + Number(direction || 0);
  if (index < 0 || nextIndex < 0 || nextIndex >= order.length) return;
  [order[index], order[nextIndex]] = [order[nextIndex], order[index]];
  applyPreferences({ ...state.preferences, section_order: order }, false);
  schedulePreferenceSave();
}

function renderAiProjects(projects) {
  const visible = (projects || []).slice(0, 3);
  if (!visible.length) return "";
  return `<div class="zone-projects" aria-label="Active projects">${visible.map((project) => {
    const cpu = Number(project.cpu_capacity_percent || 0);
    const width = clamp(cpu);
    return `<div class="zone-project">
      <div class="zone-project-head">
        <span title="${escapeHtml(project.name)}">${escapeHtml(project.name)}</span>
        <small>${formatNumber(project.process_count || 0)}p · ${formatPct(cpu)} · ${formatBytes(project.memory_bytes || 0)}</small>
      </div>
      <span class="workload-track" role="progressbar" aria-label="${escapeHtml(project.name)} CPU activity" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${width.toFixed(1)}"><span style="--workload-value:${width.toFixed(1)}%"></span></span>
    </div>`;
  }).join("")}</div>`;
}

function renderAiChildren(session) {
  if (!state.expandedAiSessions.has(session.id)) return "";
  const children = (session.children || []).slice(0, 10);
  if (!children.length) return `<div class="ai-child-empty">No child processes</div>`;
  return `<div class="ai-child-list" aria-label="Child processes">${children.map((child) => `
    <button class="ai-child-row" data-pid="${Number(child.pid || 0)}" type="button" title="${escapeHtml(child.cmdline || child.name)}">
      <span class="ai-child-name">${escapeHtml(child.label || child.name)}<small>${statusLabel(child.status)} · PID ${Number(child.pid || 0)} · ${escapeHtml(child.age_label || formatDuration(child.age_seconds || 0))}</small></span>
      <span class="ai-child-metrics">${formatPct(child.cpu_capacity_percent || 0)}<small>${formatBytes(child.memory_bytes || 0)}</small></span>
    </button>`).join("")}${(session.children || []).length > children.length ? `<div class="ai-child-overflow">+${(session.children || []).length - children.length} more processes</div>` : ""}</div>`;
}

function renderAiSession(session) {
  const cpu = Number(session.cpu_capacity_percent || 0);
  const width = clamp(cpu);
  const childCount = (session.children || []).length;
  const expanded = state.expandedAiSessions.has(session.id);
  return `<div class="session-group">
    <div class="session-main">
      <button class="session-row" data-pid="${Number(session.root?.pid || 0)}" type="button">
        <span class="session-name">${escapeHtml(session.project || session.kind_label)}<small>${escapeHtml(session.status || "IDLE")} · PID ${Number(session.root?.pid || 0)} · ${formatDuration(session.uptime_seconds || 0)}</small></span>
        <span class="session-metrics">${formatPct(cpu)} CPU<br>${formatBytes(session.memory_bytes || 0)} RSS</span>
      </button>
      ${childCount ? `<button class="session-expand" data-ai-session-toggle="${escapeHtml(session.id)}" type="button" aria-expanded="${expanded}" aria-label="${expanded ? "Hide" : "Show"} ${childCount} child processes" title="${expanded ? "Hide" : "Show"} ${childCount} child processes"><span>${formatNumber(childCount)}</span><span aria-hidden="true">${expanded ? "−" : "+"}</span></button>` : ""}
    </div>
    <span class="session-workload workload-track" role="progressbar" aria-label="${escapeHtml(session.project || session.kind_label)} CPU activity" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${width.toFixed(1)}"><span style="--workload-value:${width.toFixed(1)}%"></span></span>
    ${renderAiChildren(session)}
  </div>`;
}

function renderAi(snapshot) {
  const allZones = snapshot.zones || [];
  const zones = state.preferences.show_idle_ai ? allZones : allZones.filter((zone) => Number(zone.session_count || 0) > 0);
  const ai = snapshot.ai || {};
  el.aiTotals.innerHTML = `<span><b>${formatNumber(ai.active_session_count || 0)}</b> active sessions</span><span><b>${formatBytes(ai.memory_bytes || 0)}</b> memory</span><span><b>${formatPct(ai.cpu_capacity_percent || 0)}</b> CPU</span>`;
  el.aiZones.innerHTML = zones.length ? zones.map((zone) => {
    const sessions = (zone.sessions || []).filter((session) => state.preferences.show_idle_ai || session.active).slice(0, 6);
    const color = zone.id === "claude" ? "var(--claude)" : zone.id === "codex" ? "var(--codex)" : "var(--cursor)";
    const history = (zone.history || []).map((value) => Number(value || 0));
    const currentCpu = Number(zone.cpu_capacity_percent || 0);
    const peakCpu = Math.max(currentCpu, 0, ...history);
    const activityWidth = clamp(currentCpu);
    return `<article class="zone-card" style="--zone-color:${color}">
      <div class="zone-head"><div><h3>${escapeHtml(zone.title)}</h3><p>${formatNumber(zone.process_count || 0)} related processes</p></div><span class="zone-total">${formatNumber(zone.session_count || 0)} live</span></div>
      <div class="zone-metrics"><span>CPU<b>${formatPct(zone.cpu_capacity_percent || 0)}</b></span><span>Memory<b>${formatBytes(zone.memory_bytes || 0)}</b></span><span>Projects<b>${formatNumber(zone.projects?.length || 0)}</b></span></div>
      <div class="zone-activity">
        <div class="zone-activity-head"><span>CPU activity</span><span>Now <b>${formatPct(currentCpu)}</b> · Peak <b>${formatPct(peakCpu)}</b></span></div>
        <span class="zone-workload workload-track" role="progressbar" aria-label="${escapeHtml(zone.title)} CPU activity" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${activityWidth.toFixed(1)}"><span style="--workload-value:${activityWidth.toFixed(1)}%"></span></span>
        <div class="zone-sparkline" aria-label="${escapeHtml(zone.title)} recent CPU activity">${renderChart([{ values: history, color }], { scaleMax: Math.max(10, peakCpu), height: 28 })}</div>
      </div>
      ${renderAiProjects(zone.projects)}
      <div class="session-list">${sessions.length ? sessions.map(renderAiSession).join("") : `<div class="empty-state compact">No active sessions</div>`}</div>
    </article>`;
  }).join("") : `<div class="empty-state compact">No active AI coding sessions. Enable “Show idle providers” to inspect inactive integrations.</div>`;
}

function renderCurrentView() {
  if (!state.snapshot) return;
  if (state.view === "overview") renderOverview(state.snapshot);
  if (state.view === "processes") renderProcesses(state.snapshot);
  if (state.view === "events") renderEvents(state.snapshot);
  if (state.view === "storage") renderStorage(state.snapshot);
  if (state.view === "runtime") renderRuntime();
  if (state.view === "ai") renderAi(state.snapshot);
}

function renderAll() {
  if (!state.snapshot) return;
  renderOverview(state.snapshot);
  renderAlertBadge(state.snapshot);
  renderProcesses(state.snapshot);
  renderEvents(state.snapshot);
  renderStorage(state.snapshot);
  renderRuntime();
  renderAi(state.snapshot);
}

function processSnapshotByPid(pid) {
  return (state.snapshot?.processes?.items || []).find((item) => Number(item.pid) === Number(pid));
}

function openProcess(pid) {
  const numericPid = Number(pid || 0);
  if (!numericPid) return;
  state.selectedPid = numericPid;
  state.selectedDetail = null;
  const fallback = processSnapshotByPid(numericPid);
  el.detailsTitle.textContent = fallback?.label || fallback?.name || `PID ${numericPid}`;
  el.detailsBody.innerHTML = `<div class="drawer-loading">Reading details for PID ${numericPid}...</div>`;
  el.detailsDrawer.classList.add("is-open");
  el.detailsDrawer.setAttribute("aria-hidden", "false");
  el.drawerScrim.hidden = false;
  document.body.style.overflow = "hidden";
  fetchProcessDetails(numericPid);
}

function closeProcess() {
  state.selectedPid = null;
  state.selectedDetail = null;
  el.detailsDrawer.classList.remove("is-open");
  el.detailsDrawer.setAttribute("aria-hidden", "true");
  el.drawerScrim.hidden = true;
  document.body.style.overflow = "";
}

async function fetchProcessDetails(pid) {
  try {
    const response = await fetch(`/api/process/${encodeURIComponent(pid)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    if (state.selectedPid !== Number(pid)) return;
    state.selectedDetail = payload;
    renderProcessDetails(payload);
  } catch (error) {
    if (state.selectedPid !== Number(pid)) return;
    el.detailsBody.innerHTML = `<div class="empty-state">${escapeHtml(error.message || "Could not read process details")}</div>`;
  }
}

function detailField(label, value) {
  return `<div class="detail-stat"><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`;
}

function renderProcessDetails(detail) {
  const sampled = processSnapshotByPid(detail.pid) || {};
  const ioRead = sampled.read_bytes_per_s || 0;
  const ioWrite = sampled.write_bytes_per_s || 0;
  const gpuValue = sampled.gpu_percent !== null && sampled.gpu_percent !== undefined
    ? formatPct(sampled.gpu_percent)
    : sampled.gpu_memory_bytes ? formatBytes(sampled.gpu_memory_bytes) : "Unavailable";
  el.detailsTitle.textContent = detail.name || `PID ${detail.pid}`;
  const fileList = detail.open_files?.length
    ? detail.open_files.map((path) => `<div class="detail-block"><dd>${escapeHtml(path)}</dd></div>`).join("")
    : `<div class="empty-state compact">No readable open files</div>`;
  const connections = Object.entries(detail.connections || {});
  const connectionText = connections.length ? connections.map(([name, count]) => `${name} ${count}`).join(" · ") : "None or access denied";
  const networkModel = detail.network_attribution || {};
  const networkItem = networkModel.item || {};
  const networkDown = networkModel.throughput_available && networkItem.received_bytes_per_s !== null && networkItem.received_bytes_per_s !== undefined ? formatRate(networkItem.received_bytes_per_s) : "Unavailable";
  const networkUp = networkModel.throughput_available && networkItem.sent_bytes_per_s !== null && networkItem.sent_bytes_per_s !== undefined ? formatRate(networkItem.sent_bytes_per_s) : "Unavailable";
  const endpointList = networkItem.remote_endpoints?.length ? networkItem.remote_endpoints.map((endpoint) => `<div class="detail-block"><dd>${escapeHtml(endpoint)}</dd></div>`).join("") : `<div class="empty-state compact">${escapeHtml(networkModel.note || "No readable remote endpoints")}</div>`;
  const managementButtons = detail.manageable ? `
    <button class="action-btn" data-process-action="${detail.status === "stopped" ? "resume" : "suspend"}" type="button">${detail.status === "stopped" ? "Resume process" : "Suspend process"}</button>
    <button class="danger-btn" data-confirm-terminate type="button">Terminate process</button>
    <div id="terminateConfirm"></div>` : `<p class="manage-note">${escapeHtml(detail.management_reason || "This process cannot be managed")}</p>`;
  el.detailsBody.innerHTML = `
    <section class="detail-hero">
      <div class="detail-identity"><span class="process-avatar">${escapeHtml(initials(detail.name))}</span><div><h3>${escapeHtml(detail.name || `PID ${detail.pid}`)}</h3><p>${escapeHtml(detail.username || "unknown")} · PID ${Number(detail.pid)} · ${escapeHtml(statusLabel(detail.status))}</p></div></div>
    </section>
    <div class="detail-grid">
      ${detailField("CPU capacity", formatPct(sampled.cpu_capacity_percent ?? (detail.cpu_percent / (state.snapshot?.system?.logical_cpus || 1))))}
      ${detailField("CPU time", formatDuration(detail.cpu_time_seconds || 0))}
      ${detailField("Resident memory", formatBytes(detail.memory_bytes || 0))}
      ${detailField("Virtual memory", formatBytes(detail.virtual_memory_bytes || 0))}
      ${detailField("GPU", gpuValue)}
      ${detailField("Threads", formatNumber(detail.threads || 0))}
      ${detailField("Disk read", `${formatRate(ioRead)} · ${formatBytes(detail.read_bytes || 0)} total`)}
      ${detailField("Disk write", `${formatRate(ioWrite)} · ${formatBytes(detail.write_bytes || 0)} total`)}
      ${detailField("Age", formatDuration(detail.age_seconds || 0))}
      ${detailField("Parent", `PID ${detail.ppid || 0}`)}
      ${detailField("Children", formatNumber(detail.children?.length || 0))}
      ${detailField("Network sockets", connectionText)}
      ${detailField("Download", networkDown)}
      ${detailField("Upload", networkUp)}
    </div>
    <section class="detail-section"><h4>Working directory</h4><div class="command-block">${escapeHtml(detail.cwd || "Unavailable")}</div><div class="copy-actions"><button class="secondary-btn" data-copy-field="cwd" type="button">Copy directory</button></div></section>
    <section class="detail-section"><h4>Command</h4><pre class="command-block">${escapeHtml(detail.cmdline || "Unavailable")}</pre><div class="copy-actions"><button class="secondary-btn" data-copy-field="pid" type="button">Copy PID</button><button class="secondary-btn" data-copy-field="cmdline" type="button">Copy command</button></div></section>
    <section class="detail-section"><h4>Network attribution · ${escapeHtml(networkModel.provider || "unavailable")}</h4><div class="detail-list">${endpointList}</div></section>
    <section class="detail-section"><h4>Open files</h4><div class="detail-list">${fileList}</div></section>
    <section class="detail-section"><h4>Process management</h4><div class="process-actions">${managementButtons}</div></section>`;
}

function showTerminateConfirmation() {
  const detail = state.selectedDetail;
  if (!detail) return;
  const target = document.getElementById("terminateConfirm");
  if (!target) return;
  target.innerHTML = `<div class="confirm-box"><p>Terminating PID ${Number(detail.pid)} may discard unsaved data. This sends a termination signal only to a process owned by the current user.</p><div class="copy-actions"><button class="secondary-btn" data-cancel-terminate type="button">Cancel</button><button class="danger-btn" data-process-action="terminate" type="button">Terminate PID ${Number(detail.pid)}</button></div></div>`;
}

async function runProcessAction(action) {
  const pid = state.selectedPid;
  if (!pid) return;
  try {
    const response = await fetch(`/api/process/${encodeURIComponent(pid)}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    showToast(action === "terminate" ? `Termination signal sent to PID ${pid}` : action === "suspend" ? `PID ${pid} suspended` : `PID ${pid} resumed`);
    if (action === "terminate") {
      closeProcess();
    } else {
      setTimeout(() => fetchProcessDetails(pid), 350);
    }
    fetchSnapshot();
  } catch (error) {
    showToast(error.message || "Process action failed", true);
  }
}

async function fetchSnapshot() {
  try {
    const response = await fetch("/api/snapshot", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const snapshot = await response.json();
    if (!state.paused) {
      state.snapshot = snapshot;
      renderAll();
    }
    setConnection(state.paused ? "Paused" : "Live", state.paused ? "paused" : "live");
  } catch {
    setConnection("Offline", "error");
  }
}

function startFallbackPolling() {
  if (state.fallbackTimer) return;
  fetchSnapshot();
  state.fallbackTimer = window.setInterval(fetchSnapshot, 2200);
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
        renderAll();
      }
      setConnection(state.paused ? "Paused" : "Live", state.paused ? "paused" : "live");
    } catch {
      setConnection("Data error", "error");
    }
  });
  state.eventSource.addEventListener("error", () => setConnection("Reconnecting", "waiting"));
  state.eventSource.onerror = () => {
    setConnection("Reconnecting", "waiting");
    if (!state.snapshot) {
      state.eventSource.close();
      startFallbackPolling();
    }
  };
}

document.addEventListener("click", (event) => {
  const aiSessionToggle = event.target.closest("[data-ai-session-toggle]");
  if (aiSessionToggle) {
    const sessionId = aiSessionToggle.dataset.aiSessionToggle;
    if (state.expandedAiSessions.has(sessionId)) state.expandedAiSessions.delete(sessionId);
    else state.expandedAiSessions.add(sessionId);
    renderAi(state.snapshot);
    return;
  }
  const sectionMoveButton = event.target.closest("[data-section-move]");
  if (sectionMoveButton) {
    moveDashboardSection(sectionMoveButton.dataset.sectionMove, sectionMoveButton.dataset.moveDirection);
    return;
  }
  const navButton = event.target.closest("[data-view]");
  if (navButton) {
    setView(navButton.dataset.view);
    return;
  }
  const targetButton = event.target.closest("[data-view-target]");
  if (targetButton) {
    setView(targetButton.dataset.viewTarget);
    return;
  }
  const modeButton = event.target.closest("[data-process-mode]");
  if (modeButton) {
    state.processMode = modeButton.dataset.processMode;
    state.processLimit = 80;
    state.processSortKey = "cpu";
    state.processSortDir = "desc";
    renderProcesses(state.snapshot);
    return;
  }
  const historyButton = event.target.closest("[data-history-range]");
  if (historyButton) {
    fetchHistory(historyButton.dataset.historyRange);
    return;
  }
  const sortButtonNode = event.target.closest("[data-process-sort]");
  if (sortButtonNode) {
    const key = sortButtonNode.dataset.processSort;
    if (state.processSortKey === key) state.processSortDir = state.processSortDir === "asc" ? "desc" : "asc";
    else {
      state.processSortKey = key;
      state.processSortDir = key === "name" || key === "pid" ? "asc" : "desc";
    }
    renderProcesses(state.snapshot);
    return;
  }
  const pidRow = event.target.closest("[data-pid]");
  if (pidRow) {
    openProcess(pidRow.dataset.pid);
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.key === "Enter" || event.key === " ") && event.target.matches("tr[data-pid]")) {
    event.preventDefault();
    openProcess(event.target.dataset.pid);
  }
  if (event.key === "Escape" && state.selectedPid) closeProcess();
});

el.pauseBtn.addEventListener("click", () => {
  state.paused = !state.paused;
  el.pauseBtn.classList.toggle("is-paused", state.paused);
  el.pauseBtn.setAttribute("aria-label", state.paused ? "Resume live updates" : "Pause live updates");
  const label = el.pauseBtn.querySelector(".pause-label");
  if (label) label.textContent = state.paused ? "Resume" : "Pause";
  setConnection(state.paused ? "Paused" : "Live", state.paused ? "paused" : "live");
});

el.processSearch.addEventListener("input", (event) => {
  state.processQuery = event.target.value.trim();
  state.processLimit = 80;
  renderProcesses(state.snapshot);
});

el.processScope.addEventListener("change", (event) => {
  state.processScope = event.target.value;
  state.processLimit = 80;
  renderProcesses(state.snapshot);
});

el.processStatus.addEventListener("change", (event) => {
  state.processStatus = event.target.value;
  state.processLimit = 80;
  renderProcesses(state.snapshot);
});

el.showMoreProcesses.addEventListener("click", () => {
  state.processLimit += 100;
  renderProcesses(state.snapshot);
});

el.saveThresholds.addEventListener("click", saveThresholds);

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-section-toggle], [data-process-column], input[name='dashboardDensity'], #showIdleAiToggle")) {
    updatePreferencesFromControls();
  }
});

el.resetPreferences.addEventListener("click", () => {
  applyPreferences({
    density: "compact",
    hidden_sections: [],
    show_idle_ai: false,
    section_order: [...SECTION_ORDER_DEFAULT],
    process_columns: ["identity", "pid", "user", "state", "cpu", "memory", "gpu", "disk", "network", "threads", "age"],
  });
  schedulePreferenceSave();
  showToast("Dashboard preferences reset");
});

el.serviceSearch.addEventListener("input", (event) => {
  state.serviceQuery = event.target.value.trim();
  if (state.services) renderServices(state.services);
});

el.refreshServices.addEventListener("click", () => fetchServices(true));
el.refreshNetwork.addEventListener("click", () => fetchNetwork(true));
el.refreshContainers.addEventListener("click", () => fetchContainers(true));

el.closeDetails.addEventListener("click", closeProcess);
el.drawerScrim.addEventListener("click", closeProcess);

el.detailsBody.addEventListener("click", (event) => {
  const copyButton = event.target.closest("[data-copy-field]");
  if (copyButton && state.selectedDetail) {
    const field = copyButton.dataset.copyField;
    copyText(state.selectedDetail[field], field === "pid" ? "PID copied" : "Content copied");
    return;
  }
  if (event.target.closest("[data-confirm-terminate]")) {
    showTerminateConfirmation();
    return;
  }
  if (event.target.closest("[data-cancel-terminate]")) {
    const target = document.getElementById("terminateConfirm");
    if (target) target.innerHTML = "";
    return;
  }
  const actionButton = event.target.closest("[data-process-action]");
  if (actionButton) runProcessAction(actionButton.dataset.processAction);
});

applyPreferences(state.preferences, false);
fetchPreferences();
startEvents();
fetchHistory(state.historyRange);
fetchServices();
fetchNetwork();
fetchContainers();
state.runtimeTimer = window.setInterval(() => {
  if (!state.paused) fetchNetwork(true);
}, 4000);
state.containerTimer = window.setInterval(() => {
  if (!state.paused) fetchContainers(true);
}, 8000);
