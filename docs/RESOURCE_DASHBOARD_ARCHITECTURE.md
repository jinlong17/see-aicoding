# 本地资源看板重构说明

更新日期：2026-07-22

## 结论

Web 看板的主模型已经从“AI Agent 会话监控”调整为“系统资源遥测 + 程序/进程清单”。AI 会话识别仍然保留，但作为专项工作负载视图消费同一份系统进程数据，不再支配首页信息架构。

当前 schema v3 已覆盖 CPU、GPU、内存、交换空间、本地挂载点、SMART、吞吐/IOPS/延迟、网络、传感器、全部可读取进程、进程树、系统服务、容器、程序聚合、Top 占用、进程详情，以及带安全边界的暂停/恢复/结束操作。阈值状态转换、事件和资源样本写入本地 SQLite，不因页面关闭而丢失。

## 行业调研摘要

调研优先采用官方文档或项目主仓库：

- [Apple Activity Monitor](https://support.apple.com/guide/activity-monitor/welcome/mac)：以 CPU、GPU、内存、能耗、磁盘和网络为顶层任务，并把定位问题进程、结束进程、刷新频率和系统诊断放在同一工具中。
- [Microsoft Task Manager](https://learn.microsoft.com/en-us/troubleshoot/windows-server/support-tools/support-tools-task-manager)：将“进程实时表格”和“硬件性能曲线”分开，前者回答谁在消耗资源，后者回答机器哪里出现瓶颈。
- [Glances 进程视图](https://glances.readthedocs.io/en/latest/aoa/ps.html)：同时提供进程摘要、可排序列表和选中进程扩展信息；在 CPU、内存或 I/O 告警时自动切换关键排序维度。
- [Glances GPU](https://glances.readthedocs.io/en/latest/aoa/gpu.html) 与 [磁盘 I/O](https://glances.readthedocs.io/en/latest/aoa/diskio.html)：GPU 的利用率、显存和温度，以及磁盘吞吐、IOPS 和延迟应当是独立资源域，并且需要明确平台/驱动可用性。
- [btop](https://github.com/aristocratos/btop)：高密度监控仍可保持快速交互；进程筛选、排序、树形结构、选中进程详情、暂停列表和信号操作是资源管理器的重要能力。
- [Netdata Anomaly Advisor](https://learn.netdata.cloud/docs/netdata-ai/troubleshooting/anomaly-advisor)：单纯显示实时值之后，最有价值的升级是把同一时间段内 CPU、内存、网络、磁盘等异常变化关联起来，帮助定位根因。
- [NVIDIA NVML](https://docs.nvidia.com/deploy/nvml-api/nvml-api-reference.html)：GPU 适配层需要覆盖设备利用率、显存、温度、功耗和运行进程，并允许字段因硬件能力不同而缺失。

这些工具的共同模式不是“把更多数字塞到一个首页”，而是三层渐进披露：全局状态、按资源排序的责任主体、单进程深挖与动作。

## 新的信息架构

### 1. 总览

首页只回答三个问题：

1. CPU、GPU、内存和主数据卷当前是否有压力；
2. 压力是否持续，以及磁盘/网络此刻是否繁忙；
3. 哪些程序对 CPU、内存和 GPU 压力贡献最大。

核心资源继续使用原有暗色卡片风格，但统一为同一种读法：状态、当前值、硬件/容量上下文、短期趋势和占用刻度。CPU、GPU、内存、存储、网络与进程分别使用稳定且不重复的语义颜色；颜色在顶部卡片、趋势、排行榜和明细中保持一致。

### 2. 程序与进程

“程序”将同一应用的辅助进程聚合，适合快速判断应用总成本；“进程”保留每个 PID；“进程树”按 PID/PPID 保留父级上下文。三种模式支持按名称、PID、CPU、内存、GPU、磁盘 I/O 和运行时间排序，搜索覆盖名称、用户、命令、工作目录和 PID。

详情抽屉按需读取较慢或敏感度更高的字段，包括完整命令、cwd、累计 I/O、打开文件、网络连接状态和子进程。这样避免把所有详细信息塞进每次 SSE 快照。

### 3. 阈值、事件与历史

CPU、内存、GPU、磁盘空间、交换空间和磁盘延迟具有可编辑的警告/严重阈值。事件引擎只记录触发、升级、降级和恢复，并使用滞回区间减少临界值抖动。SQLite 采用 WAL 模式，默认每 5 秒持久一个样本、保留 7 天；15 分钟至 7 天的查询在 SQL 层降采样，避免把全部原始点传给浏览器。

### 4. 存储

容量卡片按挂载点显示总量、已用和剩余空间；独立趋势图显示整机读取/写入吞吐。设备层通过 psutil 计数器差分得到 IOPS 与平均读写延迟；macOS 使用 `diskutil`，Linux 优先使用 `smartctl` 读取 SMART/NVMe 健康。权限不足时返回原因而不是假定健康。

### 5. 系统运行时

launchd/systemd 服务、逐进程网络和 Docker/Podman 容器都采用独立按需端点与短缓存。macOS `nettop` 提供逐进程累计字节差分；无法获得字节计数的平台降级为连接、监听和远端端点归因。容器 CLI 或守护进程不可用时保留明确的未安装/离线状态。

### 6. AI 工作负载

Claude、ChatGPT 和 Cursor 的识别、项目归因与会话聚合全部保留。它们现在位于顶部资源卡片之后的可移动专项区块，而不是独立 Tab；和普通系统进程共享采样结果，避免维护两套事实来源。每个工具保留固定身份色，并显示实时 CPU 活动条、短期趋势、项目摘要、会话状态和可展开子进程。

工作负载磁盘空间定义为“可归因项目目录的已分配大小”，而不是无法可靠归因到单个进程的整盘占用。后台线程每次只运行一个低优先级 `du` 探测，成功结果缓存 5 分钟，失败结果缓存 1 分钟；只测量当前用户主目录内的路径，超时或不可读时明确降级。

Claude、ChatGPT 与 Cursor 各有独立额度卡。ChatGPT 通过本机 `codex app-server` 的 `account/rateLimits/read` 读取当前活动 Codex profile；Claude 仅在用户明确把 `see-aicoding --capture-claude-usage` 配置为 Claude Code status-line 命令后，接收官方 status-line JSON 中的 `five_hour` / `seven_day` 字段。采集器只保存百分比、重置时间和采集时间，不读取或复制凭证、Cookie、session id；相同 status-line 值一分钟内不重复落盘。Cursor 没有假定非公开的个人接口，继续使用手动值或显示不可用。

额度采集在独立守护线程中运行，不进入系统资源热循环：正常缓存 5 分钟，单次本地请求超时 8 秒，失败从 30 秒指数退避到最长 10 分钟，手动刷新具有 10 秒冷却。`/api/provider-usage` 始终立即返回当前安全缓存；自动值优先，手动值只填补自动数据缺失的窗口。未配置的窗口显示不可用，不以 0% 伪装真实额度。

三个服务的额度采用紧凑卡片，每张卡以两个圆形进度环呈现 5 小时与每周窗口，环内显示已用百分比，旁边保留剩余额度、重置时间、来源与更新时间。AI Workloads 标题区提供即时显示/隐藏按钮，Settings 提供同一偏好的持久开关。隐藏额度卡时 UsageCollector 不再启动新的自动刷新线程；重新显示时恢复采集并立即读取安全缓存。UsageCollector 随 `see-aicoding --web` 启动，不需要额外守护进程或单独启动命令。

### 7. Compact Dashboard 与偏好设置

Web 看板采用单页渐进披露，不再依赖多个顶层 Tab。CPU、GPU、内存、存储、网络和进程卡片固定在顶部；AI 工作负载、趋势/告警、资源排行榜、进程清单、存储和运行时区块可调整上下顺序。用户可隐藏非必要区块、切换紧凑/舒适密度、控制是否显示空闲 AI provider，并选择进程表字段。

这些偏好通过 `/api/dashboard-preferences` 读写 SQLite settings，刷新页面或重启看板后仍然保留。统一设置入口同时管理中英文、五种主题、刷新策略和本地额度值。后端只接受白名单 language/theme/performance/section/column id，并自动补齐新版本增加的默认值，避免旧偏好破坏后续布局。

### 8. 颜色系统

颜色不是按组件随机分配，而是按数据域注册：CPU、GPU、内存、存储、网络、进程、磁盘读写、网络上下行、服务、容器和状态各有独立 token；Claude、Codex、Cursor 使用独立且固定的身份色。JavaScript 图表直接引用 CSS token，不维护第二套十六进制常量。

完整 token、组件映射和验收规则见 [Dashboard Design System](./DASHBOARD_DESIGN_SYSTEM.md)。测试 `tests/test_dashboard_palette.py` 会阻止语义 token 使用重复色值。

### 9. 温度与轻量刷新

CPU 温度优先使用 `psutil` 暴露的 CPU/package/core 传感器，并取当前可读的最高温度；macOS 可选兼容的无特权辅助程序。平台没有安全读取通道时返回 `available: false` 和原因，不调用 `sudo`，也不把系统热压力状态冒充为摄氏温度。

默认平衡模式的 SSE 间隔为 3 秒，另提供 1.5 秒实时模式和 5 秒节能模式。SSE 只推送总览、趋势、AI 工作负载和状态数据；完整进程/程序数组在进程区块接近视口时按需获取。服务、逐进程网络和容器继续使用独立端点，但轮询改为视口可见、页面激活且未暂停时才运行。

## 模块边界

```text
psutil processes ──> monitor.Sampler(system scope) ──> process inventory
                         │
                         └──> AI classifier/session adapter ──> AI workload view

OS counters ───────> telemetry.SystemTelemetry ─────> CPU / memory / disk / network
GPU providers ─────> telemetry.GpuCollector ────────> normalized GPU model
diskutil/smartctl ──> storage.SmartCollector ───────> normalized device health

samples ───────────> observability.ThresholdEngine ─> transition events
samples/events ────> persistence.HistoryStore ──────> SQLite WAL / range queries

all normalized data ──> snapshot schema v3 ──> compact cached SSE + full on-demand JSON

selected PID ──────> on-demand detail endpoint
confirmed action ──> guarded same-user process action endpoint
runtime adapters ──> launchd/systemd · nettop/sockets · Docker/Podman endpoints
local provider IPC ─> usage.UsageCollector ─────────> cached provider-usage endpoint
```

关键调整：

- `monitor.py` 负责进程采样和 AI 分类，不再承担硬件指标组装；Web 使用系统范围采样，TUI 仍默认当前用户。
- `telemetry.py` 是系统遥测边界，封装短期历史、吞吐/IOPS/延迟、传感器和 GPU 平台适配器；`storage.py` 独立处理慢速 SMART 探测。
- `observability.py` 只负责阈值状态机；`persistence.py` 负责 SQLite schema、保留策略和历史降采样，两者不依赖浏览器。
- `runtime.py` 封装服务、网络与容器命令适配器，全部具有超时、缓存和明确降级状态。
- `usage.py` 封装 Codex app-server、显式 Claude status-line 快照、缓存、超时与失败退避；采集线程与系统遥测线程解耦，Cursor 没有安全来源时保持明确不可用。
- `snapshot.py` 提供 schema v3，同时保留旧版 `cpu_percent` 和 `disk` 别名，降低现有客户端迁移成本。
- `web.py` 在刷新窗口内缓存快照，避免每个 SSE 客户端重复进行一次全系统采样；SSE 使用精简 JSON，完整进程信息独立按需读取；同时对白名单化 Dashboard 偏好进行规范化，并通过 SQLite settings 持久化语言、主题、刷新策略、区块顺序、可见性、密度、额度和表格字段。
- `web_static` 只消费规范化 JSON，不包含平台判断或系统命令；资源图表引用统一 CSS 语义色，不重复定义颜色常量；视口观察器控制重区块渲染和后台轮询。

## GPU 可用性约定

- Apple Silicon：通过无特权的 `IOAccelerator` 读取实时设备利用率和共享内存分配；当前接口不能可靠提供逐进程 GPU 归因。
- NVIDIA：通过 `nvidia-smi` 读取设备利用率、显存、温度、功耗和 compute 进程显存。
- Linux DRM：从 sysfs 读取驱动暴露的利用率和 VRAM；字段随驱动变化。
- 不支持的平台：返回显式 `available: false` 与原因，界面显示“不可用”，不会把缺失值伪装成 0%。

## 进程管理安全边界

进程操作只监听本机回环地址，并要求同源 JSON 请求。后端只允许操作当前用户拥有的进程，拒绝 PID 0/1、看板自身及其父进程。结束进程在界面内还需要二次确认。读取失败、权限不足和进程已退出均使用明确状态返回。

## 已完成与下一阶段建议

本轮已经完成阈值与事件、SQLite 历史、进程树与服务、SMART/IOPS/延迟、逐进程网络归因、容器视图、Compact Dashboard、持久偏好、区块排序、动态卡片，以及全局唯一语义色系统。后续仍建议按价值和架构依赖排序：

1. **异常关联**：基于 SQLite 历史识别“哪个进程启动后同时引发 CPU、磁盘和网络突增”，先用规则和 z-score，不急于引入重型 ML。
2. **历史导出与容量预测**：增加 CSV/JSON 导出、数据库大小上限和磁盘空间增长预测。
3. **服务与容器动作审计**：在独立权限检查、操作日志和确认流完成后，再增加启动/停止/重启。
4. **GPU 深度适配**：为 NVIDIA 增加 engine/encoder/decoder、ECC 和节流原因；为 AMD/Intel 补充正式库适配；Apple 逐进程 GPU 必须以明确的可用性研究为前置条件。
5. **Linux eBPF 网络适配**：把“连接归因”升级为字节率归因，但保持可选依赖和无权限降级路径。
6. **资源策略**：在有审计日志和回滚路径后，再增加 nice/优先级、CPU affinity、资源上限和批量动作。

当前架构已经为这些能力留出资源域、历史域和平台适配器边界；不需要再次重写前端信息架构。
