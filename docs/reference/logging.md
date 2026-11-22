# 日志规约与排障

> 数据源：`docs/reference/csharp-iron-laws.md` §B 拆出（2026-11-20）。
> 「慢 / 卡住 / 没回复 / 进程没了」先看这里。

---


### 1 分工与四条原则

```
装配层 scripts/server.py（唯一）
        │  setup_logging(CFG) + install_process_hooks() + install_asyncio_hooks()
        ▼
log_setup.py  ← 唯一「装管道」者：handler / formatter / 滚动 / 上下文 / 异常钩子 / 阶段打点原语
        │  （只依赖标准库，不认识任何业务模块）
        ▼
各业务模块  ← 只「用管道」：log = log_setup.get_logger(__name__)
              turn_scope() 补 npc/turn、stage() 记阶段耗时
```

1. **装管道与用管道分离**：只有装配层能配置全局日志状态；业务模块**绝不** `basicConfig` / `addHandler` / `setLevel`（违规会导致 handler 重复=日志翻倍，或级别被踩=排障时看不到该看的行）。哨兵 `tests/test_log_setup.py::test_no_module_configures_logging_itself` 强制。
2. **日志只做记录，不做恢复**：自愈/重试/兜底归各业务模块；日志不参与控制流，`stage()` / formatter 内部全部 try 包死——**日志坏了也不能拖垮回合**。
3. **事件驱动，不预支轮询开销**：慢告警靠"阶段存续期内挂有界定时器 + 次数封顶"，阶段一结束立刻取消。
4. **记录业务事实的是各模块自己**：log_setup 不写任何业务语义。

> **单位坑（已踩）**：`stage(slow_ms=…)` 单位是**毫秒**，而 `WsServer.request_timeout` 等单位是**秒**。把秒直接传给 `slow_ms` 会让阈值缩到毫秒级 → 超时窗口内连发告警。正确写法 `min(limit * 500.0, 30000.0)`（半个超时，封顶 30s），由 `tests/test_ws_channel.py::test_rpc_slow_warning_threshold_is_milliseconds` 把守。
> `log_setup.flag(name)` 供"高频但默认关"的埋点单独取闸（如 `trace_frames`）。

### 2 总闸：一个词决定开/关

| 写法 | 效果 |
|---|---|
| `off` / `false` / `0` / `no` / `none` / `silent` | 完全关闭 |
| `on` / `true` / `1` / `yes` | 打开，沿用 config 里的 `level` |
| `debug` / `info` / `warning` / `error` / `critical` | 打开并指定级别 |
| 未设置 / 无法识别 | 沿用 `config.json` 的 `logging` 块 |

```bash
python scripts/server.py                       # 沿用 config.json 的 logging 块
python scripts/server.py --log debug           # 开到 DEBUG（排查）
python scripts/server.py --no-log              # 完全关闭（部署；也可 --log off）
AGENT_LOOP_LOG=info python scripts/server.py   # 环境变量方式
```

**优先级：CLI `--log` / `--no-log` / `--log-level` > 环境变量 `AGENT_LOOP_LOG` > `config.json`**（与 `AGENT_LOOP_WS` 同一规约：env 在「使用处」解释，`config_loader` 只管文件）。

**关闭是真的关闭**：不挂任何 handler、不建日志文件，包 logger 定在「静默」级（高于 CRITICAL）；子 logger 有效级别继承自包 logger，所有 `log.xxx()` 立即返回（参数是惰性 `%s`，不会被格式化），`stage()` 连慢告警定时器都不挂——**近似零开销，可留在正式部署**。启动时往 stderr 打一行 `[server] 日志已关闭：…（需要时用 --log debug）`。
**C# 侧**：`Launcher` 目前不传参数，游戏里想换开关改 `config.json` 的 `logging.enabled` / `level`（由重启或保存任意配置项触发热重配）。

### 3 配置块（`config.json` → `logging`，不入 UI 白名单，手改文件）

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关；false 时不落文件（仅保留 stderr 兜底） |
| `level` | `"INFO"` | 包 logger 级别；排障调 `"DEBUG"` |
| `file` | `"logs/agent_loop.log"` | **相对路径锚包根**（非 cwd）；空串/null = 不落文件 |
| `rotation` | `"daily"` | `daily` / `size` / `none` |
| `backup_days` | `7` | 保留份数（daily=天数；size=文件数） |
| `max_bytes` | `4MB` | `rotation=size` 时单文件上限 |
| `console` | `true` | 是否同时打 stderr（C# 拉起时有浮动控制台可见） |
| `console_level` | `"WARNING"` | 控制台级别（默认只让 WARNING+ 上屏） |
| `slow_ms` | `30000` | 阶段慢告警阈值（**毫秒**）；`0` = 关闭 |
| `slow_escalations` | `3` | 慢告警最多次数（1x/2x/4x…），封顶防长尾开销 |
| `trace_frames` | `false` | 逐帧记 C#↔Python 线上帧（DEBUG 级 + 本开关双闸；异常类帧恒记） |
| `capture_root` | `false` | true = 连第三方库日志一起收进本文件 |
| `modules` | `{}` | 模块级级别覆盖，如 `{"agent_loop.llm": "DEBUG"}` |
| `quiet_libs` | `httpx`/`httpcore`/`openai`/`websockets`/`urllib3`/`asyncio` | 第三方库降噪到 WARNING |

`config_loader.DEFAULT_CONFIG["logging"]` 是给 UI/诊断看的镜像；**真相源是 `log_setup.DEFAULT_LOGGING`**（缺键由后者补齐，两处无需严格同步）。任何一次配置写回都会触发热重配。

### 4 行格式与平台坑

```
2026-09-11 19:25:37.389 INFO  [-] agent_loop.server: WS 通道已监听 127.0.0.1:8766，等待 C# 连接（日志：F:\agent_loop\logs\agent_loop.log）
%(asctime)s.%(msecs)03d %(levelname)-5s [%(ctx)s] %(name)s: %(message)s
```

上下文栏 `[npc t{turn} s{step} {stage}]`：缺项自动省略，全缺为 `[-]`。由 contextvar 提供（`turn_scope()` 注入），**`asyncio.create_task` 会复制 context**，所以并发 NPC 回合互不串台。副作用：单个 NPC 的时间线可直接 grep：

```
findstr /C:"[林婉清 " logs\agent_loop.log
```

1. **相对路径锚包根，不锚 cwd**：C# `Launcher` 传 `WorkingDirectory = server.py 所在目录`（即 `scripts/`），用 cwd 会把日志写进 `scripts/logs/`。`log_setup._resolve_path` 统一锚 `Path(log_setup.__file__).parent`。
2. **C# 拉起时不重定向 stderr**：控制台 handler 的输出只在一个浮动窗口里，**不是**持久证据；唯一持久证据是文件 handler。
3. **日志只在 `agent_loop.*` 命名空间内生效**：handler 挂在 `agent_loop` logger 上并 `propagate=False`，不劫持真 root（避免第三方库刷屏）。`py.warnings` 被单独收进文件；需要连第三方库一起收时开 `capture_root`。
4. **shutdown 路径**：`WsServer` 收到 `shutdown` RPC 后 0.6s 调 `os._exit(0)`，已在其前插入 `log_setup.shutdown_logging()`，保证尾巴日志不丢。

### 5 排障手册（grep 清单）

**「很慢」** → grep `LLM 完成`，先看 `ttft` 与 `decode` 谁占 `llm` 大头：

* `ttft ≈ llm`（decode 极小）→ **慢在"发出去到第一个 token"**：网关排队 / 上游 prefill / prompt 太大。此时 `in=`（prompt tokens，同一条日志里）就是关键自变量，拿它和 `ctx_window` 比：接近阈值说明该让它压缩了。实测样本（姜萌回合）：`llm=43102ms ttft=43101ms decode=1ms` 与 `llm=14135ms ttft=13917ms decode=218ms`——ttft 占 98.5%~99.998%，整回合 65.3s = 13.9s 等首字 + 7.9s 工具 + 43.1s 等首字。**结论：瓶颈不在模型出字速度，先查网关与 prompt 规模。**
* `decode` 占大头 → 模型逐字生成慢（模型/配额/负载）。
* 再看 `工具 X 完成（Nms）` 找慢工具；`rpc.call_tool` 阶段行能区分"等 C# 主线程"还是"动作本身耗时"。
* 其次看 `慢告警` 行给出的"卡在哪一段"。

**「卡住不动」** → grep `慢告警`：`阶段 llm 已运行 …` 连发 = LLM 侧挂起（查本地网关 `127.0.0.1:8123`）。09-18 起超时判据是两条**存活看门狗**（首字 `llm.timeout` 120s / 中途停顿 `llm.idle_timeout` 60s），所以"一直没内容"最多等这么久就会被判死并进重试；**没有**整趟耗时判据（长回复只要一直在出字就不该被砍）。想看是哪一种超时，grep `首字超时：` 或 `输出停顿超时：`（`llm/openai_client._guarded` 抛的文案）；`阶段 rpc.<method> 已运行 …` = 等 C# 主线程无应答（模态窗没关？游戏卡帧？）；`排队 N.Ns 才取得 LLM 并发槽` = 别的 NPC 回合占着槽，属"排队"而非"卡死"。

**「回复没出来 / UI 空窗」** → grep `回合收口`（有没有走到收口）、`事件 npc_reply 未送达/发送失败`、`C# 连接已断开`。

**「一直显示正在压缩中」**（C# 提示行只在收到 `compact_result` 时才更新）：

1. grep `/compact 收到` —— **完全没有这行 = 事件没到 Python**：可能是连接闪断把事件丢在死 socket 上（看 `C# 连接已断开`）、`npc_id` 为空（看 `缺少 npc_id` 告警）、或 C# 侧 `ws==null` 根本没发出。
2. 有 `/compact 收到` 但没有 `/compact 结束` → 压缩中途卡住：`compact.manual]` 慢告警连发 = 卡在压缩内部；`压缩重入被拒 … 上一次压缩卡住了` = 占坑未释放，**再按 /compact 只会立刻被拒，需重启 Python**；`压缩等待回合锁 N.Ns` = 被同 NPC 的回合占着锁。
3. 有 `压缩开始` 但无 `压缩成功/压缩未执行` → 卡在摘要 LLM：看 `compact.summarize]` 慢告警与 `摘要 LLM 调用失败`。
4. 有 `/compact 结束` 但 UI 仍显示压缩中 → 回推失败，看 `事件 compact_result 未送达/发送失败`。

**「进程莫名其妙没了」** → grep `CRITICAL`，异常钩子会带完整堆栈（含线程名）。
**「回答变哑 / 答非所问」** → grep `L1 快照` / `L1 上下文取数失败`：模型可能没拿到人物状态。

**哨兵测试族**：`tests/test_log_setup.py`（装配幂等、落盘与上下文栏、级别热重配、`enabled=false`、相对路径锚包根、慢告警有界封顶、未捕获异常留痕、**职责哨兵**）、`tests/test_log_switch.py`（开关词解析、覆盖 config 的 enabled/level、`resolve_log_switch` 优先级、`--no-log` 压过环境变量且不建文件）。
**未做**：C# 侧 request/response 帧 trace 镜像；日志轮转的磁盘总上限；结构化日志（JSON）。（`llm.timeout` 默认 `null` 那条**已不成立**：09-17 起默认 120s，09-18 起语义是"首字看门狗"，见 docs/features/brain-liveness.md §4 与 2026-09-17-超时语义双身份.md。）

---
