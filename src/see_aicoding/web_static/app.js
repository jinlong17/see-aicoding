"use strict";

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
  historyRange: "1h",
  historyModel: null,
  historyRequest: 0,
  services: null,
  serviceQuery: "",
  networkAttribution: null,
  runtimeTimer: null,
  containers: null,
  containerTimer: null,
};

const el = Object.fromEntries(
  [
    "connectionState", "pauseBtn", "hostLine", "factUptime", "factProcesses", "factLoad",
    "cpuState", "cpuValue", "cpuDetail", "cpuChart", "cpuMeter",
    "gpuState", "gpuValue", "gpuDetail", "gpuChart", "gpuMeter",
    "memoryState", "memoryValue", "memoryDetail", "memoryChart", "memoryMeter",
    "storageState", "storageValue", "storageDetail", "storageChart", "storageMeter",
    "systemChart", "diskReadRate", "diskWriteRate", "networkDownRate", "networkUpRate",
    "updateTime", "runningProcesses", "threadCount", "swapUsage", "logicalCpus", "gpuProvider",
    "topCpu", "topMemory", "topGpu", "processSummary", "processTotal", "processRunning",
    "processSearch", "processScope", "processStatus", "processTableHead", "processTableBody",
    "visibleProcessCount", "showMoreProcesses", "storageReadRate", "storageWriteRate",
    "storageIops", "storageLatency", "diskGrid", "diskIoChart", "sensorList",
    "smartProvider", "smartDeviceList", "deviceIoList", "aiTotals", "aiZones", "detailsDrawer",
    "alertBadge", "alertSummary", "activeAlerts", "thresholdGrid", "saveThresholds",
    "eventCount", "eventTimeline", "persistentHistoryChart", "persistenceFacts",
    "runtimeSummary", "servicesProvider", "servicesNote", "serviceSearch",
    "refreshServices", "serviceTableBody", "serviceVisibleCount",
    "networkProvider", "networkSummary", "networkNote", "refreshNetwork",
    "networkTableBody", "networkVisibleCount",
    "containerProvider", "containerSummary", "containerNote", "refreshContainers",
    "containerGrid",
    "drawerScrim", "detailsTitle", "detailsBody", "closeDetails", "toast",
  ].map((id) => [id, document.getElementById(id)])
);

const COLORS = {
  cpu: "#58d6a5",
  memory: "#dcbf71",
  gpu: "#6fc8d8",
  read: "#6fc8d8",
  write: "#dcbf71",
};

let chartSequence = 0;

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
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function formatDuration(seconds) {
  let remaining = Math.max(0, Math.floor(Number(seconds || 0)));
  const days = Math.floor(remaining / 86400);
  remaining %= 86400;
  const hours = Math.floor(remaining / 3600);
  remaining %= 3600;
  const minutes = Math.floor(remaining / 60);
  if (days) return `${days}天 ${hours}小时`;
  if (hours) return `${hours}小时 ${minutes}分`;
  if (minutes) return `${minutes}分 ${remaining % 60}秒`;
  return `${remaining}秒`;
}

function formatEventTime(timestamp) {
  const date = new Date(Number(timestamp || 0) * 1000);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
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
    running: "运行中",
    sleeping: "睡眠",
    stopped: "已暂停",
    zombie: "僵尸",
    idle: "空闲",
    disk_sleep: "I/O 等待",
    waking: "唤醒中",
    locked: "锁定",
    waiting: "等待",
    unknown: "未知",
  }[status] || status || "未知";
}

function pressureState(value, available = true) {
  if (!available) return { label: "不可用", className: "" };
  const number = Number(value || 0);
  if (number >= 85) return { label: "高压", className: "is-high" };
  if (number >= 65) return { label: "偏高", className: "is-medium" };
  return { label: "正常", className: "is-good" };
}

function setMetricState(node, value, available = true) {
  const current = pressureState(value, available);
  node.textContent = current.label;
  node.className = `metric-state ${current.className}`.trim();
}

function setMeter(node, value) {
  node.style.width = `${clamp(value)}%`;
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
    const gradientId = `chart-gradient-${chartSequence++}`;
    const areaPath = `M ${points[0][0].toFixed(2)} ${height} L ${pointText.replaceAll(",", " ")} L ${lastX.toFixed(2)} ${height} Z`;
    return `
      <defs>
        <linearGradient id="${gradientId}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${item.color}" stop-opacity="0.62"></stop>
          <stop offset="100%" stop-color="${item.color}" stop-opacity="0"></stop>
        </linearGradient>
      </defs>
      ${item.fill === false ? "" : `<path class="chart-area" d="${areaPath}" fill="url(#${gradientId})"></path>`}
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

async function copyText(value, label = "已复制") {
  const text = String(value || "");
  if (!text) {
    showToast("当前字段没有可复制内容", true);
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

  el.hostLine.textContent = `LOCAL SYSTEM · ${system.user || "user"}@${system.hostname || "localhost"} · ${system.platform || ""}`;
  el.factUptime.textContent = formatDuration(system.uptime_seconds || 0);
  el.factProcesses.textContent = formatNumber(processSummary.total || snapshot.processes?.items?.length || 0);
  el.factLoad.textContent = Number(cpu.load_1 || 0).toFixed(2);

  el.cpuValue.textContent = cpuValue.toFixed(0);
  el.cpuDetail.textContent = `${system.physical_cpus || "--"} 个物理核心 · ${system.logical_cpus || "--"} 个逻辑核心${cpu.frequency_mhz ? ` · ${(cpu.frequency_mhz / 1000).toFixed(2)}GHz` : ""}`;
  setMetricState(el.cpuState, cpuValue);
  setMeter(el.cpuMeter, cpuValue);
  el.cpuChart.innerHTML = renderChart([{ values: history.cpu_percent || [], color: COLORS.cpu }], { scaleMax: 100 });

  el.gpuValue.textContent = gpuAvailable ? gpuValue.toFixed(0) : "--";
  const gpuDevice = gpu.devices?.[0];
  el.gpuDetail.textContent = gpuAvailable
    ? `${gpuDevice?.name || "GPU"}${gpuDevice?.cores ? ` · ${gpuDevice.cores} 核心` : ""}${gpu.memory_used_bytes !== null && gpu.memory_used_bytes !== undefined ? ` · ${formatBytes(gpu.memory_used_bytes)} 已分配` : ""}`
    : gpu.note || "未检测到受支持的 GPU 遥测提供器";
  setMetricState(el.gpuState, gpuValue, gpuAvailable);
  setMeter(el.gpuMeter, gpuValue);
  el.gpuChart.innerHTML = renderChart([{ values: history.gpu_percent || [], color: COLORS.gpu }], { scaleMax: 100 });

  el.memoryValue.textContent = memoryValue.toFixed(0);
  el.memoryDetail.textContent = `${formatBytes(memory.used_bytes)} 已用 · ${formatBytes(memory.available_bytes)} 可用 · 共 ${formatBytes(memory.total_bytes)}`;
  setMetricState(el.memoryState, memoryValue);
  setMeter(el.memoryMeter, memoryValue);
  el.memoryChart.innerHTML = renderChart([{ values: history.memory_percent || [], color: COLORS.memory }], { scaleMax: 100 });

  el.storageValue.textContent = disks.length ? diskValue.toFixed(0) : "--";
  el.storageDetail.textContent = disks.length
    ? `${formatBytes(systemDisk.used_bytes)} 已用 · ${formatBytes(systemDisk.free_bytes)} 剩余 · ${systemDisk.mountpoint || "本地"}`
    : "未读取到本地挂载点";
  setMetricState(el.storageState, diskValue, Boolean(disks.length));
  setMeter(el.storageMeter, diskValue);
  el.storageChart.innerHTML = renderChart([
    { values: history.disk_read_bytes_per_s || [], color: COLORS.read },
    { values: history.disk_write_bytes_per_s || [], color: COLORS.write, fill: false },
  ]);

  el.systemChart.classList.remove("skeleton-block");
  el.systemChart.innerHTML = renderChart([
    { values: history.cpu_percent || [], color: COLORS.cpu },
    { values: history.memory_percent || [], color: COLORS.memory, fill: false },
    { values: history.gpu_percent || [], color: COLORS.gpu, fill: false },
  ], { scaleMax: 100, height: 64, grid: true });

  el.diskReadRate.textContent = formatRate(diskIo.read_bytes_per_s || 0);
  el.diskWriteRate.textContent = formatRate(diskIo.write_bytes_per_s || 0);
  el.networkDownRate.textContent = formatRate(network.download_bytes_per_s || 0);
  el.networkUpRate.textContent = formatRate(network.upload_bytes_per_s || 0);
  el.updateTime.textContent = new Date((snapshot.generated_at || 0) * 1000).toLocaleTimeString("zh-CN", { hour12: false });
  el.runningProcesses.textContent = formatNumber(processSummary.running || 0);
  el.threadCount.textContent = formatNumber(processSummary.threads || 0);
  el.swapUsage.textContent = swap.total_bytes ? `${formatPct(swap.percent, 0)} · ${formatBytes(swap.used_bytes)}` : "未启用";
  el.logicalCpus.textContent = formatNumber(system.logical_cpus || 0);
  el.gpuProvider.textContent = gpu.provider || "none";

  const resources = snapshot.resources || {};
  el.topCpu.innerHTML = renderLeaders(resources.top_cpu || [], "cpu");
  el.topMemory.innerHTML = renderLeaders(resources.top_memory || [], "memory");
  el.topGpu.innerHTML = renderLeaders(resources.top_gpu || [], "gpu", gpu.note);
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
    return `<div class="empty-state compact">${escapeHtml(emptyNote || "当前没有可归因的程序数据")}</div>`;
  }
  const metrics = items.map((item) => leaderMetric(item, type));
  const max = Math.max(1, ...metrics.map((metric) => metric.value));
  return items.slice(0, 5).map((item, index) => {
    const metric = metrics[index];
    const width = Math.max(3, Math.min(100, metric.value / max * 100));
    const detail = item.process_count > 1 ? `${item.process_count} 个进程` : `PID ${item.primary_pid}`;
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
  if (key === "age") return Number(item.age_seconds || 0);
  return Number(item.cpu_capacity_percent || 0);
}

function sortedProcessItems(items, mode) {
  const direction = state.processSortDir === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    const aValue = processSortValue(a, state.processSortKey, mode);
    const bValue = processSortValue(b, state.processSortKey, mode);
    if (typeof aValue === "string") {
      const result = aValue.localeCompare(bValue, "zh-CN");
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

function renderProcessHead(mode) {
  if (mode === "programs") {
    return `<tr>
      <th style="width:29%">${sortButton("name", "程序")}</th>
      <th style="width:9%">进程</th>
      <th style="width:13%">用户</th>
      <th class="num" style="width:12%">${sortButton("cpu", "CPU")}</th>
      <th class="num" style="width:14%">${sortButton("memory", "内存")}</th>
      <th class="num" style="width:10%">${sortButton("gpu", "GPU")}</th>
      <th class="num" style="width:13%">${sortButton("disk", "磁盘 I/O")}</th>
    </tr>`;
  }
  return `<tr>
    <th style="width:25%">${sortButton("name", mode === "tree" ? "进程树" : "进程")}</th>
    <th style="width:8%">${sortButton("pid", "PID")}</th>
    <th style="width:11%">用户</th>
    <th style="width:10%">状态</th>
    <th class="num" style="width:10%">${sortButton("cpu", "CPU")}</th>
    <th class="num" style="width:13%">${sortButton("memory", "内存")}</th>
    <th class="num" style="width:9%">${sortButton("gpu", "GPU")}</th>
    <th class="num" style="width:9%">${sortButton("disk", "I/O")}</th>
    <th class="num" style="width:9%">${sortButton("age", "运行时间")}</th>
  </tr>`;
}

function usageCell(value, label, color = COLORS.cpu) {
  const percent = clamp(value);
  return `<div class="usage-cell"><span class="usage-bar"><span style="width:${percent}%;background:${color}"></span></span><span class="mono">${escapeHtml(label)}</span></div>`;
}

function renderProgramRow(item) {
  const users = item.usernames || [];
  const ioRate = Number(item.read_bytes_per_s || 0) + Number(item.write_bytes_per_s || 0);
  const gpuLabel = item.gpu_percent !== null && item.gpu_percent !== undefined
    ? formatPct(item.gpu_percent)
    : item.gpu_memory_bytes ? formatBytes(item.gpu_memory_bytes) : "--";
  return `<tr class="process-row" data-pid="${Number(item.primary_pid || 0)}" tabindex="0">
    <td><div class="process-name-cell"><span class="process-avatar">${escapeHtml(initials(item.label))}</span><span class="process-name">${escapeHtml(item.label || "未知程序")}<span class="process-sub">PID ${Number(item.primary_pid || 0)}</span></span></div></td>
    <td class="mono">${formatNumber(item.process_count || 0)}</td>
    <td title="${escapeHtml(users.join(", "))}">${escapeHtml(users.length > 1 ? `${users[0]} +${users.length - 1}` : users[0] || "--")}</td>
    <td class="num">${usageCell(item.cpu_capacity_percent || 0, formatPct(item.cpu_capacity_percent || 0))}</td>
    <td class="num mono">${formatBytes(item.memory_bytes || 0)}</td>
    <td class="num mono">${gpuLabel}</td>
    <td class="num mono">${formatRate(ioRate)}</td>
  </tr>`;
}

function renderProcessRow(item, treeDepth = null, hasChildren = false) {
  const ioRate = Number(item.read_bytes_per_s || 0) + Number(item.write_bytes_per_s || 0);
  const gpuLabel = item.gpu_percent !== null && item.gpu_percent !== undefined
    ? formatPct(item.gpu_percent)
    : item.gpu_memory_bytes ? formatBytes(item.gpu_memory_bytes) : "--";
  const treePrefix = treeDepth === null ? "" : `<span class="tree-indent" style="--tree-depth:${Math.min(12, treeDepth)}"><i>${hasChildren ? "⌄" : "·"}</i></span>`;
  return `<tr class="process-row ${treeDepth === null ? "" : "tree-row"}" data-pid="${Number(item.pid || 0)}" tabindex="0">
    <td title="${escapeHtml(item.cmdline || "")}"><div class="process-name-cell">${treePrefix}<span class="process-avatar">${escapeHtml(initials(item.label || item.name))}</span><span class="process-name">${escapeHtml(item.label || item.name || `PID ${item.pid}`)}<span class="process-sub">${treeDepth === null ? "" : `PPID ${Number(item.ppid || 0)} · `}${escapeHtml(item.cmdline || item.exe || "无命令信息")}</span></span></div></td>
    <td class="mono">${Number(item.pid || 0)}</td>
    <td title="${escapeHtml(item.username || "")}">${escapeHtml(item.username || "--")}</td>
    <td><span class="status-badge status-${escapeHtml(item.status || "unknown")}">${escapeHtml(statusLabel(item.status))}</span></td>
    <td class="num">${usageCell(item.cpu_capacity_percent || 0, formatPct(item.cpu_capacity_percent || 0))}</td>
    <td class="num mono">${formatBytes(item.memory_bytes || 0)}</td>
    <td class="num mono">${gpuLabel}</td>
    <td class="num mono">${formatRate(ioRate)}</td>
    <td class="num mono">${formatDuration(item.age_seconds || 0)}</td>
  </tr>`;
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

  el.processSummary.textContent = mode === "programs" ? `${formatNumber(source.length)} 个程序组，辅助进程已按应用归并。`
    : mode === "tree" ? `${formatNumber(tree.root_count || 0)} 个根节点 · 最大 ${formatNumber(tree.max_depth || 0)} 层；筛选结果会保留父级上下文。`
    : `${formatNumber(source.length)} 个可读取进程，点击任意行查看完整资源详情。`;
  el.processTotal.textContent = formatNumber(summary.total || snapshot.processes?.items?.length || 0);
  el.processRunning.textContent = formatNumber(summary.running || 0);
  el.processTableHead.innerHTML = renderProcessHead(mode);
  el.processTableBody.innerHTML = visible.length
    ? visible.map((row) => mode === "programs" ? renderProgramRow(row) : mode === "tree" ? renderProcessRow(row.item, row.depth, row.hasChildren) : renderProcessRow(row)).join("")
    : `<tr><td colspan="9"><div class="empty-state">没有匹配当前筛选条件的${mode === "programs" ? "程序" : "进程"}</div></td></tr>`;
  el.visibleProcessCount.textContent = `显示 ${formatNumber(visible.length)} / ${formatNumber(filtered.length)}`;
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
        <div class="disk-label"><h3>${escapeHtml(disk.mountpoint || disk.device || "本地磁盘")}</h3><p>${escapeHtml([disk.device, disk.filesystem, disk.is_system ? "系统卷" : ""].filter(Boolean).join(" · "))}</p></div>
        <span class="disk-percent">${formatPct(disk.percent || 0, 0)}</span>
      </div>
      <div class="capacity-bar" aria-label="已使用 ${formatPct(disk.percent || 0, 0)}"><span style="width:${clamp(disk.percent)}%"></span></div>
      <div class="disk-numbers">
        <span>总容量<b>${formatBytes(disk.total_bytes || 0)}</b></span>
        <span>已使用<b>${formatBytes(disk.used_bytes || 0)}</b></span>
        <span>剩余<b>${formatBytes(disk.free_bytes || 0)}</b></span>
      </div>
    </article>`).join("") : `<div class="empty-state">没有可读取的本地挂载点</div>`;

  el.diskIoChart.innerHTML = renderChart([
    { values: history.disk_read_bytes_per_s || [], color: COLORS.read },
    { values: history.disk_write_bytes_per_s || [], color: COLORS.write, fill: false },
  ], { height: 64, grid: true });

  el.smartProvider.textContent = storageHealth.available ? `SMART · ${String(storageHealth.provider || "provider").toUpperCase()}` : "SMART · UNAVAILABLE";
  const smartDevices = storageHealth.devices || [];
  el.smartDeviceList.innerHTML = smartDevices.length ? smartDevices.map((device) => {
    const healthLabel = device.health === "passed" ? "健康" : device.health === "failed" ? "故障" : "未知";
    const details = [
      device.temperature_c !== null && device.temperature_c !== undefined ? `${Number(device.temperature_c).toFixed(1)}°C` : null,
      device.percentage_used !== null && device.percentage_used !== undefined ? `寿命已用 ${Number(device.percentage_used).toFixed(0)}%` : null,
      device.available_spare_percent !== null && device.available_spare_percent !== undefined ? `备用 ${Number(device.available_spare_percent).toFixed(0)}%` : null,
      device.media_errors !== null && device.media_errors !== undefined ? `介质错误 ${formatNumber(device.media_errors)}` : null,
    ].filter(Boolean);
    return `<div class="device-row">
      <div class="device-title"><strong>${escapeHtml(device.model || device.identifier || device.device)}</strong><span>${escapeHtml([device.device, device.protocol, device.solid_state === true ? "SSD" : device.solid_state === false ? "HDD" : ""].filter(Boolean).join(" · "))}</span></div>
      <span class="health-badge is-${escapeHtml(device.health || "unknown")}">${healthLabel} · ${escapeHtml(device.smart_status || "Unknown")}</span>
      <div class="device-metrics">${details.length ? details.map((detail) => `<span>${escapeHtml(detail)}</span>`).join("") : `<span>提供器未返回详细 SMART 属性</span>`}</div>
    </div>`;
  }).join("") : `<div class="empty-state compact">${escapeHtml(storageHealth.note || "当前设备没有可读取的 SMART 数据")}</div>`;

  const ioDevices = diskIo.devices || [];
  el.deviceIoList.innerHTML = ioDevices.length ? ioDevices.map((device) => `
    <div class="device-row io-device-row">
      <div class="device-title"><strong>${escapeHtml(device.device || "disk")}</strong><span>${formatRate(Number(device.read_bytes_per_s || 0) + Number(device.write_bytes_per_s || 0))} 总吞吐</span></div>
      <div class="device-metrics io-device-metrics">
        <span>读 <b>${Number(device.read_iops || 0).toFixed(1)}</b> IOPS · <b>${Number(device.read_latency_ms || 0).toFixed(2)}</b> ms</span>
        <span>写 <b>${Number(device.write_iops || 0).toFixed(1)}</b> IOPS · <b>${Number(device.write_latency_ms || 0).toFixed(2)}</b> ms</span>
      </div>
    </div>`).join("") : `<div class="empty-state compact">当前平台没有暴露设备级 I/O 计数器</div>`;

  const sensors = system.sensors || [];
  const battery = system.battery;
  const rows = sensors.map((sensor) => `
    <div class="sensor-row"><span>${escapeHtml(sensor.label || sensor.group)}</span><b>${Number(sensor.current_c || 0).toFixed(1)}°C</b></div>`);
  if (battery) rows.push(`<div class="sensor-row"><span>电池${battery.plugged ? " · 已接电源" : ""}</span><b>${formatPct(battery.percent, 0)}</b></div>`);
  el.sensorList.innerHTML = rows.length ? rows.join("") : `<div class="empty-state compact">当前平台没有暴露可读取的温度或电池传感器</div>`;
}

function serviceStateLabel(stateValue, subState = "") {
  const key = String(stateValue || "unknown");
  const label = {
    running: "运行中",
    active: "活跃",
    inactive: "未运行",
    exited: "已退出",
    failed: "失败",
    activating: "启动中",
    deactivating: "停止中",
    reloading: "重载中",
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
  el.servicesNote.textContent = model?.note || "当前平台没有可用的系统服务提供器。";
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
  }).join("") : `<tr><td colspan="5"><div class="empty-state">${model?.available ? "没有匹配的系统服务" : escapeHtml(model?.note || "服务清单不可用")}</div></td></tr>`;
  el.serviceVisibleCount.textContent = `显示 ${formatNumber(items.length)} / ${formatNumber(source.length)}`;
}

function renderRuntimeSummary() {
  const serviceSummary = state.services?.summary || {};
  const networkSummaryModel = state.networkAttribution?.summary || {};
  const containerSummaryModel = state.containers?.summary || {};
  el.runtimeSummary.innerHTML = `
    <span><b>${state.services?.available ? formatNumber(serviceSummary.running || 0) : "--"}</b> 运行服务</span>
    <span><b>${state.networkAttribution?.available ? formatNumber(networkSummaryModel.process_count || 0) : "--"}</b> 网络进程</span>
    <span><b>${state.containers?.available ? formatNumber(containerSummaryModel.running || 0) : "--"}</b> 运行容器</span>`;
}

function renderContainers(model) {
  const summary = model?.summary || {};
  const items = model?.items || [];
  el.containerProvider.textContent = `${String(model?.provider || "CONTAINER RUNTIME").toUpperCase()} · ${model?.available ? "CONNECTED" : model?.installed ? "OFFLINE" : "NOT INSTALLED"}`;
  el.containerNote.textContent = model?.note || "没有可用的容器运行时。";
  el.containerSummary.innerHTML = `
    <span><small>运行中</small><b>${model?.available ? `${formatNumber(summary.running || 0)} / ${formatNumber(summary.total || 0)}` : "--"}</b></span>
    <span><small>CPU</small><b>${model?.metrics_available ? formatPct(summary.cpu_percent || 0) : "--"}</b></span>
    <span><small>内存</small><b>${model?.metrics_available ? formatBytes(summary.memory_usage_bytes || 0) : "--"}</b></span>
    <span><small>累计网络 I/O</small><b>${model?.metrics_available ? `${formatBytes(summary.network_received_bytes || 0)} ↓ · ${formatBytes(summary.network_sent_bytes || 0)} ↑` : "--"}</b></span>`;
  el.containerGrid.innerHTML = items.length ? items.map((item) => `
    <article class="container-card ${item.running ? "is-running" : ""}">
      <div class="container-head">
        <div class="container-identity"><span class="container-cube" aria-hidden="true">◇</span><div><h4>${escapeHtml(item.name || item.short_id || "container")}</h4><p>${escapeHtml(item.image || item.short_id || "--")}</p></div></div>
        <span class="health-badge ${item.running ? "is-passed" : ""}">${escapeHtml(item.state || "unknown")}</span>
      </div>
      <div class="container-metrics">
        <span><small>CPU</small><b>${item.cpu_percent === null || item.cpu_percent === undefined ? "--" : formatPct(item.cpu_percent)}</b></span>
        <span><small>内存</small><b>${item.memory_usage_bytes === null || item.memory_usage_bytes === undefined ? "--" : formatBytes(item.memory_usage_bytes)}</b></span>
        <span><small>网络</small><b>${item.network_received_bytes === null || item.network_received_bytes === undefined ? "--" : `${formatBytes(item.network_received_bytes)} / ${formatBytes(item.network_sent_bytes || 0)}`}</b></span>
        <span><small>PIDs</small><b>${formatNumber(item.pid_count || 0)}</b></span>
      </div>
      <div class="container-foot"><span>${escapeHtml(item.status || "--")}</span><span title="${escapeHtml(item.ports || "")}">${escapeHtml(item.ports || "无端口映射")}</span></div>
    </article>`).join("") : `<div class="empty-state container-empty">${escapeHtml(model?.note || "当前没有容器")}</div>`;
  renderRuntimeSummary();
}

function renderNetworkAttribution(model) {
  const summary = model?.summary || {};
  const items = (model?.items || []).slice(0, 120);
  const throughput = Boolean(model?.throughput_available);
  el.networkProvider.textContent = `${String(model?.provider || "NETWORK").toUpperCase()} · ${throughput ? "THROUGHPUT" : "CONNECTIONS"}`;
  el.networkNote.textContent = model?.note || "逐进程网络归因不可用。";
  el.networkSummary.innerHTML = `
    <span><small>归因下行</small><b>${throughput ? formatRate(summary.received_bytes_per_s || 0) : "连接模式"}</b></span>
    <span><small>归因上行</small><b>${throughput ? formatRate(summary.sent_bytes_per_s || 0) : "连接模式"}</b></span>
    <span><small>连接</small><b>${formatNumber(summary.connection_count || 0)}</b></span>
    <span><small>网络进程</small><b>${formatNumber(summary.process_count || 0)}</b></span>`;
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
  }).join("") : `<tr><td colspan="6"><div class="empty-state">${escapeHtml(model?.note || "没有可读取的逐进程网络活动")}</div></td></tr>`;
  el.networkVisibleCount.textContent = `显示 ${formatNumber(items.length)} / ${formatNumber(model?.items?.length || 0)}${throughput ? " · 字节率每 4 秒刷新" : " · 吞吐归因不可用"}`;
  renderRuntimeSummary();
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
    if (state.view === "runtime") renderServices(payload);
  } catch (error) {
    state.services = { available: false, items: [], summary: {}, note: error.message || "服务读取失败" };
    if (state.view === "runtime") renderServices(state.services);
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
    if (state.view === "runtime") renderNetworkAttribution(payload);
  } catch (error) {
    state.networkAttribution = { available: false, throughput_available: false, items: [], summary: {}, note: error.message || "网络归因读取失败" };
    if (state.view === "runtime") renderNetworkAttribution(state.networkAttribution);
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
    if (state.view === "runtime") renderContainers(payload);
  } catch (error) {
    state.containers = { available: false, installed: false, items: [], summary: {}, note: error.message || "容器清单读取失败" };
    if (state.view === "runtime") renderContainers(state.containers);
  } finally {
    el.refreshContainers.disabled = false;
  }
}

function eventActionLabel(action) {
  return {
    opened: "触发",
    escalated: "升级",
    deescalated: "降级",
    resolved: "恢复",
  }[action] || action || "变化";
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

  el.alertSummary.innerHTML = `<span><b>${formatNumber(summary.active || 0)}</b> 活跃</span><span><b>${formatNumber(summary.critical || 0)}</b> 严重</span><span><b>${formatNumber(summary.warning || 0)}</b> 警告</span>`;
  el.activeAlerts.innerHTML = active.length ? active.map((item) => `
    <article class="active-alert is-${escapeHtml(item.severity)}">
      <span class="alert-indicator" aria-hidden="true"></span>
      <div><strong>${escapeHtml(item.label)}</strong><p>自 ${escapeHtml(formatEventTime(item.since))} 起高于 ${Number(item.threshold || 0).toFixed(0)}${escapeHtml(item.unit)}</p></div>
      <b>${Number(item.value || 0).toFixed(1)}${escapeHtml(item.unit)}</b>
    </article>`).join("") : `<div class="empty-state">当前没有活跃资源告警</div>`;

  const thresholdSignature = JSON.stringify(thresholds);
  if (state.thresholdRenderSignature !== thresholdSignature) {
    state.thresholdRenderSignature = thresholdSignature;
    el.thresholdGrid.innerHTML = Object.entries(thresholds).map(([resource, rule]) => `
      <article class="threshold-card" data-threshold-resource="${escapeHtml(resource)}">
        <div class="threshold-card-head"><strong>${escapeHtml(rule.label || resource)}</strong><label class="switch-label"><input data-threshold-enabled type="checkbox" ${rule.enabled ? "checked" : ""}><span>启用</span></label></div>
        <div class="threshold-inputs">
          <label>警告 <span><input data-threshold-warning type="number" min="0" max="99" step="1" value="${Number(rule.warning || 0)}"><i>${escapeHtml(rule.unit || "%")}</i></span></label>
          <label>严重 <span><input data-threshold-critical type="number" min="1" max="100" step="1" value="${Number(rule.critical || 0)}"><i>${escapeHtml(rule.unit || "%")}</i></span></label>
        </div>
      </article>`).join("") || `<div class="empty-state">没有可配置的阈值</div>`;
  }

  el.eventCount.textContent = `${formatNumber(events.length)} 条状态变化`;
  el.eventTimeline.innerHTML = events.length ? events.map((item) => `
    <article class="timeline-event is-${escapeHtml(item.severity)} ${item.action === "resolved" ? "is-resolved" : ""}">
      <div class="timeline-marker"><span></span></div>
      <div class="timeline-copy">
        <div><strong>${escapeHtml(item.label)}</strong><span class="event-action">${escapeHtml(eventActionLabel(item.action))}</span><time>${escapeHtml(formatEventTime(item.timestamp))}</time></div>
        <p>${escapeHtml(item.message)}</p>
      </div>
      <span class="timeline-value">${item.value === null || item.value === undefined ? "--" : `${Number(item.value).toFixed(1)}${escapeHtml(item.unit)}`}</span>
    </article>`).join("") : `<div class="empty-state">尚无阈值状态变化；时间线会在资源越过阈值时出现事件。</div>`;
  if (state.historyModel) renderPersistentHistory(state.historyModel);
}

function renderPersistentHistory(model) {
  const series = model.series || {};
  const persistence = model.persistence || {};
  const pointCount = Number(model.point_count || 0);
  el.persistentHistoryChart.classList.remove("skeleton-block");
  el.persistentHistoryChart.innerHTML = pointCount ? renderChart([
    { values: series.cpu_percent || [], color: COLORS.cpu },
    { values: series.memory_percent || [], color: COLORS.memory, fill: false },
    { values: series.gpu_percent || [], color: COLORS.gpu, fill: false },
  ], { height: 66, scaleMax: 100, grid: true }) : `<div class="empty-state compact">当前范围还没有持久采样；数据库每 ${Number(persistence.persist_interval_seconds || 5)} 秒写入一次。</div>`;
  el.persistenceFacts.innerHTML = persistence.available ? `
    <span><b>${formatNumber(pointCount)}</b> 图表点</span>
    <span><b>${formatNumber(persistence.sample_count || 0)}</b> 原始采样</span>
    <span><b>${formatBytes(persistence.database_bytes || 0)}</b> SQLite</span>
    <span><b>${Number(model.resolution_seconds || 0)}s</b> 聚合粒度</span>
    <span title="${escapeHtml(persistence.path || "")}"><b>${formatNumber(persistence.retention_days || 0)} 天</b> 保留期</span>` : `<span class="is-error">SQLite 不可用：${escapeHtml(persistence.error || "无法创建本地数据库")}</span>`;
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
  el.persistentHistoryChart.classList.add("skeleton-block");
  try {
    const response = await fetch(`/api/history?range=${encodeURIComponent(range)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    if (request !== state.historyRequest) return;
    state.historyModel = payload;
    renderPersistentHistory(payload);
  } catch (error) {
    if (request !== state.historyRequest) return;
    el.persistentHistoryChart.classList.remove("skeleton-block");
    el.persistentHistoryChart.innerHTML = `<div class="empty-state compact">${escapeHtml(error.message || "历史查询失败")}</div>`;
  }
}

async function saveThresholds() {
  const thresholds = {};
  for (const card of el.thresholdGrid.querySelectorAll("[data-threshold-resource]")) {
    const warning = Number(card.querySelector("[data-threshold-warning]").value);
    const critical = Number(card.querySelector("[data-threshold-critical]").value);
    if (!Number.isFinite(warning) || !Number.isFinite(critical) || warning < 0 || warning >= critical || critical > 100) {
      showToast(`${card.querySelector("strong").textContent} 的阈值无效`, true);
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
    showToast("资源阈值已保存");
  } catch (error) {
    showToast(error.message || "阈值保存失败", true);
  } finally {
    el.saveThresholds.disabled = false;
  }
}

function renderAi(snapshot) {
  const zones = snapshot.zones || [];
  const ai = snapshot.ai || {};
  el.aiTotals.innerHTML = `<span><b>${formatNumber(ai.active_session_count || 0)}</b> 活跃会话</span><span><b>${formatBytes(ai.memory_bytes || 0)}</b> 内存</span><span><b>${formatPct(ai.cpu_capacity_percent || 0)}</b> CPU</span>`;
  el.aiZones.innerHTML = zones.map((zone) => {
    const sessions = (zone.sessions || []).filter((session) => session.active).slice(0, 8);
    const color = zone.id === "claude" ? "var(--claude)" : zone.id === "codex" ? "var(--codex)" : "var(--cursor)";
    return `<article class="zone-card" style="--zone-color:${color}">
      <div class="zone-head"><div><h3>${escapeHtml(zone.title)}</h3><p>${formatNumber(zone.process_count || 0)} 个相关进程</p></div><span class="zone-total">${formatNumber(zone.session_count || 0)} live</span></div>
      <div class="zone-metrics"><span>CPU<b>${formatPct(zone.cpu_capacity_percent || 0)}</b></span><span>内存<b>${formatBytes(zone.memory_bytes || 0)}</b></span><span>项目<b>${formatNumber(zone.projects?.length || 0)}</b></span></div>
      <div class="session-list">${sessions.length ? sessions.map((session) => `
        <button class="session-row" data-pid="${Number(session.root?.pid || 0)}" type="button">
          <span class="session-name">${escapeHtml(session.project || session.kind_label)}<small>${escapeHtml(session.kind_label)} · PID ${Number(session.root?.pid || 0)}</small></span>
          <span class="session-metrics">${formatPct(session.cpu_capacity_percent || 0)}<br>${formatBytes(session.memory_bytes || 0)}</span>
        </button>`).join("") : `<div class="empty-state compact">当前没有活跃会话</div>`}</div>
    </article>`;
  }).join("");
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
  if (state.view === "processes") renderProcesses(state.snapshot);
  if (state.view === "events") renderEvents(state.snapshot);
  if (state.view === "storage") renderStorage(state.snapshot);
  if (state.view === "runtime") renderRuntime();
  if (state.view === "ai") renderAi(state.snapshot);
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
  el.detailsBody.innerHTML = `<div class="drawer-loading">正在读取 PID ${numericPid} 的详细信息…</div>`;
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
    el.detailsBody.innerHTML = `<div class="empty-state">${escapeHtml(error.message || "无法读取进程详情")}</div>`;
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
    : sampled.gpu_memory_bytes ? formatBytes(sampled.gpu_memory_bytes) : "不可归因";
  el.detailsTitle.textContent = detail.name || `PID ${detail.pid}`;
  const fileList = detail.open_files?.length
    ? detail.open_files.map((path) => `<div class="detail-block"><dd>${escapeHtml(path)}</dd></div>`).join("")
    : `<div class="empty-state compact">没有可读取的打开文件</div>`;
  const connections = Object.entries(detail.connections || {});
  const connectionText = connections.length ? connections.map(([name, count]) => `${name} ${count}`).join(" · ") : "无或无权限读取";
  const networkModel = detail.network_attribution || {};
  const networkItem = networkModel.item || {};
  const networkDown = networkModel.throughput_available && networkItem.received_bytes_per_s !== null && networkItem.received_bytes_per_s !== undefined ? formatRate(networkItem.received_bytes_per_s) : "不可用";
  const networkUp = networkModel.throughput_available && networkItem.sent_bytes_per_s !== null && networkItem.sent_bytes_per_s !== undefined ? formatRate(networkItem.sent_bytes_per_s) : "不可用";
  const endpointList = networkItem.remote_endpoints?.length ? networkItem.remote_endpoints.map((endpoint) => `<div class="detail-block"><dd>${escapeHtml(endpoint)}</dd></div>`).join("") : `<div class="empty-state compact">${escapeHtml(networkModel.note || "没有可读取的远端端点")}</div>`;
  const managementButtons = detail.manageable ? `
    <button class="action-btn" data-process-action="${detail.status === "stopped" ? "resume" : "suspend"}" type="button">${detail.status === "stopped" ? "恢复进程" : "暂停进程"}</button>
    <button class="danger-btn" data-confirm-terminate type="button">结束进程</button>
    <div id="terminateConfirm"></div>` : `<p class="manage-note">${escapeHtml(detail.management_reason || "当前进程不可管理")}</p>`;
  el.detailsBody.innerHTML = `
    <section class="detail-hero">
      <div class="detail-identity"><span class="process-avatar">${escapeHtml(initials(detail.name))}</span><div><h3>${escapeHtml(detail.name || `PID ${detail.pid}`)}</h3><p>${escapeHtml(detail.username || "unknown")} · PID ${Number(detail.pid)} · ${escapeHtml(statusLabel(detail.status))}</p></div></div>
    </section>
    <div class="detail-grid">
      ${detailField("CPU 整机占比", formatPct(sampled.cpu_capacity_percent ?? (detail.cpu_percent / (state.snapshot?.system?.logical_cpus || 1))))}
      ${detailField("CPU 累计时间", formatDuration(detail.cpu_time_seconds || 0))}
      ${detailField("常驻内存", formatBytes(detail.memory_bytes || 0))}
      ${detailField("虚拟内存", formatBytes(detail.virtual_memory_bytes || 0))}
      ${detailField("GPU", gpuValue)}
      ${detailField("线程", formatNumber(detail.threads || 0))}
      ${detailField("磁盘读取", `${formatRate(ioRead)} · 共 ${formatBytes(detail.read_bytes || 0)}`)}
      ${detailField("磁盘写入", `${formatRate(ioWrite)} · 共 ${formatBytes(detail.write_bytes || 0)}`)}
      ${detailField("运行时间", formatDuration(detail.age_seconds || 0))}
      ${detailField("父进程", `PID ${detail.ppid || 0}`)}
      ${detailField("子进程", formatNumber(detail.children?.length || 0))}
      ${detailField("网络连接", connectionText)}
      ${detailField("网络下行", networkDown)}
      ${detailField("网络上行", networkUp)}
    </div>
    <section class="detail-section"><h4>工作目录</h4><div class="command-block">${escapeHtml(detail.cwd || "不可读取")}</div><div class="copy-actions"><button class="secondary-btn" data-copy-field="cwd" type="button">复制目录</button></div></section>
    <section class="detail-section"><h4>命令</h4><pre class="command-block">${escapeHtml(detail.cmdline || "不可读取")}</pre><div class="copy-actions"><button class="secondary-btn" data-copy-field="pid" type="button">复制 PID</button><button class="secondary-btn" data-copy-field="cmdline" type="button">复制命令</button></div></section>
    <section class="detail-section"><h4>网络归因 · ${escapeHtml(networkModel.provider || "unavailable")}</h4><div class="detail-list">${endpointList}</div></section>
    <section class="detail-section"><h4>打开的文件</h4><div class="detail-list">${fileList}</div></section>
    <section class="detail-section"><h4>进程管理</h4><div class="process-actions">${managementButtons}</div></section>`;
}

function showTerminateConfirmation() {
  const detail = state.selectedDetail;
  if (!detail) return;
  const target = document.getElementById("terminateConfirm");
  if (!target) return;
  target.innerHTML = `<div class="confirm-box"><p>结束 PID ${Number(detail.pid)} 可能导致未保存的数据丢失。此操作只向当前用户拥有的进程发送终止信号。</p><div class="copy-actions"><button class="secondary-btn" data-cancel-terminate type="button">取消</button><button class="danger-btn" data-process-action="terminate" type="button">确认结束 PID ${Number(detail.pid)}</button></div></div>`;
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
    showToast(action === "terminate" ? `已向 PID ${pid} 发送结束信号` : action === "suspend" ? `已暂停 PID ${pid}` : `已恢复 PID ${pid}`);
    if (action === "terminate") {
      closeProcess();
    } else {
      setTimeout(() => fetchProcessDetails(pid), 350);
    }
    fetchSnapshot();
  } catch (error) {
    showToast(error.message || "进程操作失败", true);
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
    setConnection(state.paused ? "已暂停" : "实时", state.paused ? "paused" : "live");
  } catch {
    setConnection("离线", "error");
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
      setConnection(state.paused ? "已暂停" : "实时", state.paused ? "paused" : "live");
    } catch {
      setConnection("数据错误", "error");
    }
  });
  state.eventSource.addEventListener("error", () => setConnection("重连中", "waiting"));
  state.eventSource.onerror = () => {
    setConnection("重连中", "waiting");
    if (!state.snapshot) {
      state.eventSource.close();
      startFallbackPolling();
    }
  };
}

document.addEventListener("click", (event) => {
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
  el.pauseBtn.setAttribute("aria-label", state.paused ? "恢复实时更新" : "暂停实时更新");
  setConnection(state.paused ? "已暂停" : "实时", state.paused ? "paused" : "live");
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
    copyText(state.selectedDetail[field], field === "pid" ? "PID 已复制" : "内容已复制");
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

startEvents();
