# Python 侧存活检测：状态模型梳理（2026-09-14）

> **这份文档回答一个问题**：C# 怎么知道「Python 那半边还活着」？
>
> 现状是**没有**一个统一的答案，而是四份互不相干的状态位，其中一份还被拿去回答了它回答不了的问题。
> 本文先把模型理清楚，再谈怎么修 —— 因为直接打补丁会把一个**当初正确的修复**一起推翻（见 §5）。

---

## 1. 一条故障链，三个环节

2026-09-14 实测（Python 进程被外部 `taskkill` 掉之后）：

| # | 环节 | 现状 | 证据 |
|---|---|---|---|
| 1 | 进程死了不会重新拉起 | ❌ | `Launcher` 全仓库只有 2 个调用点：`ModMain.cs:196`（初始化一次）、`ConfigPresenter.cs:624`（F11 保存配置） |
| 2 | 玩家看不到任何提示 | ❌ | `ChatPresenter.cs:301` 的判据是 `_ws == null`，而它**永远不为 null**（见 §3） |
| 3 | 消息被静默丢弃 | ❌ | `WsClient.cs:568` `if (ws == null || !ws.IsOpen) return;` —— 不记日志、不报错 |

**只剩 1 个环节时玩家还能自己猜（"卡了吧"）；三个都缺，玩家屏幕上就是"什么都没发生"。**

这撞在这份代码自己立的规矩上 —— `ChatWindow.cs:397`：**「不许静默失败」**。

---

## 2. 现状：四份各管各的状态

| # | 状态 | 位置 | 谁写 | 谁读 | 回答什么问题 |
|---|---|---|---|---|---|
| ① | `_proc` | `Launcher.cs:34` | 拉起/重启时 | **只有 `KillOwned()`** | 「这个进程是不是我起的」（→ 能不能杀） |
| ② | `_pythonStarted` | `ModMain.cs:39` | 置位一次 | `LaunchPythonOnce()` | 「拉起过没有」 |
| ③ | `_ws`（transport） | `WsClient.cs:140/151` | 每次连接/断开 | `IsConnected` | 「现在能不能说话」 |
| ④ | `_restartStage` | `ConfigPresenter.cs:47` | 配置重启编排 | 编排自身 | 「配置保存的重启走到哪一步了」 |

**四者之间没有任何汇合点。** ① 明确声明自己**不**回答存活（`Launcher.cs:38-40`），② 是一次性闩锁，④ 只在配置保存时短暂存在，于是「活着吗」这个问题**实际上被 ③ 兼职回答了** —— 而 ③ 答不了。

---

## 3. 症结：用「传输层」推断「进程层」

### 3.1 那次刻意的设计决定

```csharp
// Launcher.cs:38-40
/// <summary>Python 是否由本 mod 拉起（owned → 重启时可 KillOwned；非 owned 只能靠 shutdown RPC）。
/// 注：进程句柄只用来「强杀」，**不用来判断它是否已退出**——UseShellExecute 拉起时句柄未必
/// 追踪真实 python，且「死没死」有更可靠的观测：WsClient 的连接状态（见 ConfigPresenter）。</summary>
public static bool IsOwned => _proc != null;
```

以及删掉端口探测时留下的注释：

```csharp
// ConfigPresenter.cs:45
//   现已删掉该探测（Launcher.ProbePortFree）——进程死没死由 WsClient 的连接状态说话。
```

**这句话在逻辑上是单向的，被当成双向用了。** 正确的蕴含关系只有一条：

```
WS 连着  ⟹  进程活着        （活着才能监听）
进程活着  ⇏  WS 连着          ← 反向不成立
```

WS 断开的真实原因至少有五种，其中只有一种是"进程死了"：

| WS 断的原因 | 进程状态 |
|---|---|
| 进程真的死了 | **死** |
| onefile 正在解压载荷（**热启动实测 ~2 秒**；冷启动 + 杀软扫描未实测上界） | 活（在启动） |
| 进程活着但还没 bind 端口 | 活（在启动） |
| WS 握手失败（端口被占 / 防火墙） | 活 |
| 正常的瞬时重连窗口（2 秒一轮） | 活 |

**只有第 1 行该触发"重新拉起"；后四行去拉，就是往一个正在启动的进程旁边再起一份 → 抢端口 → 反而制造故障。**

### 3.2 一个过期的理由

注释说「UseShellExecute 拉起时句柄未必追踪真实 python」—— 但实际代码是：

```csharp
// Launcher.cs:205
UseShellExecute = false,     // 必须 false：只有它才能下发环境变量
```

`UseShellExecute = false` 时 `Process.Start` 返回的是**直接创建的那个进程的句柄**，`HasExited` 对它可靠。**这条理由描述的是一个没有在用的配置**（大概是从早期版本遗留下来的）。

> ⚠ 但仍有一个真实限制：onefile 形态下 `_proc` 是 **bootloader**，真正跑 Python 的是它的子进程。好消息是 bootloader 会 `waitpid` 子进程再退出，所以 **bootloader 退出 ⟺ 整个 onefile 应用结束**，`HasExited` 依然是对的判据。

### 3.3 命名冲突放大了误读

两个字段都叫 `_ws`，但是不同的东西：

| 写法 | 真身 | 生命周期 |
|---|---|---|
| `ChatPresenter._ws`（`:37`） | **WsClient 对象** | `:71` 赋值一次后**永不为 null** |
| `WsClient._ws`（`:140/151`） | **WsTransport**（套接字） | 每次连接/断开反复置位与清空 |

所以 `ChatPresenter.cs:301` 的 `if (_ws == null)` 检查的是"客户端对象建没建"，而不是"连没连上"。**看起来检查了连接，实际上什么都没检查。** 而真正可用的 `IsConnected`（`WsClient.cs:69`）全仓库只在 `ConfigPresenter.cs:557` 用过一次。

---

## 4. 真值表：把三层摊开

「Python 侧活着吗」其实是**三个不同的问题**，各自有各自的真值来源：

| 层 | 问题 | 真值来源 | 现状 |
|---|---|---|---|
| **进程层** | 脑子还在不在？ | `_proc.HasExited` / 进程枚举 | ❌ 从未被查询 |
| **传输层** | 现在能不能说话？ | `WsClient.IsConnected` | ✅ 准确，但只有配置面板在用 |
| **应用层** | 脑子还清醒吗？ | 心跳 RPC（`SendRequest` 有超时，`:493`） | ❌ 完全没有 |

组合起来才能覆盖全部情况：

| 进程 | 传输 | 应用 | 实际含义 | 该做什么 | 现在会做什么 |
|---|---|---|---|---|---|
| 活 | 通 | 通 | 正常 | — | ✅ 正常 |
| 活 | 断 | — | 启动中 / 重连窗口 | 等，并显示"正在连接" | ✅ 重连（但玩家不知道要等） |
| 活 | 通 | **无响应** | Python 卡死（事件循环阻塞） | 提示 + 可选重启 | ❌ 看起来一切正常 |
| **死** | 断 | — | 脑子没了 | **重新拉起** | ❌ **永远重连，永不恢复** |
| 死 | — | — | 首次拉起失败 | 重试拉起 | ❌ `_pythonStarted` 闩锁已置位，**永不重试**（见 §6.3） |

---

## 5. 为什么当初会这么写（别把正确的修复一起推翻）

2026-09-12 事故：重启 Python 时用 `TcpClient.BeginConnect + WaitOne(400)` 探测端口是否释放，
但本机对无监听端口的 connect 要 **~2 秒**才回 `ConnectionRefused`，400ms 窗口必然超时
→ 探测恒返回"占用" → 状态机永远卡在等待，**既不放行拉起也不报错**
（实锤：`Player.log` 里 `owned python 已强制结束` 刷了 191 行，而 Python 侧新进程从未起来）。

**当时的修法是对的** —— 「旧进程让位了没有」这个问题，`IsConnected == false` 恰好是个好答案
（比端口探测可靠得多）。**错误发生在下一步**：把这个结论推广成了「进程死没死看 WS 状态」。

> **一句话**：`IsConnected` 回答「旧进程让出端口了吗」是称职的，回答「进程还在吗」是不称职的。
> 修的时候要保留前者，补齐后者。

---

## 6. 需要注意的坑（"相互耦合又存在不同"的具体位置）

### 6.1 启动期 ≠ 故障
onefile 自包含 exe 在**热缓存**下实测 **~2 秒**才 bind 端口（`--selftest` 全程：PyInstaller 1.84/1.77s，
Nuitka 2.16/1.93s）。这期间 `IsConnected` 必然是 false。

> ⚠️ **更正**：本文档早期版本写的是"实测 ~15 秒"，那个数字**不是测量值** —— 它来自一次
> `sleep 15` 之后去查端口、发现已在监听，于是把 15 当成了耗时。真实热启动约 2 秒。
> `StartGraceSeconds = 35s` 仍保持，但它的理由要改成"给**冷启动 + 未签名 exe 首次被杀软扫描**
> 留余量"（这一档本机无法实测，是估的），而不是"解压要 15 秒"。
**任何"断开超过 N 秒就拉起"的规则都必须先查进程层**，否则会把正在启动的实例当成死的，再拉一份抢端口。

### 6.2 进程层是唯一的拉起依据
> 拉起条件只允许是：`_proc == null || _proc.HasExited`
> **绝不允许**：`!IsConnected` 持续 N 秒

### 6.3 `_pythonStarted` 闩锁在拉起**之前**就置位，且忽略返回值
```csharp
// Launcher.cs:47-56
public static void LaunchPythonOnce()
{
    if (ModMain._pythonStarted) { ...; return; }
    ModMain._pythonStarted = true;      // ← 先置位
    LaunchCore(out _proc);              // ← 返回值被丢弃
}
```
首次拉起失败（exe 被杀软拦了、路径不对）→ 闩锁已置位 → **这一局再也不会尝试**。

### 6.4 `RelaunchPython` 先清句柄再拉
```csharp
// Launcher.cs:63-76
_proc = null;                  // ← 先清
string err = LaunchCore(out proc);
if (err != null) { ...; return err; }   // ← 失败就返回，_proc 保持 null
```
失败后 `IsOwned` 变 false → 后续 `KillOwned()` 静默失效。

### 6.5 非 owned 形态不能自动拉起
玩家自己 `python scripts/server.py` 起的（`IsOwned == false`）：**不能杀，也不该自动拉起** ——
那不是我们启动的，重复拉起会得到两个进程抢同一个端口。

### 6.6 与配置重启编排的冲突
`_restartStage`（`ConfigPresenter.cs:47`）是第四份状态。如果加 supervisor，两者会打架：
**编排想杀掉旧的、supervisor 想拉起新的。** 必须明确：编排期间挂起 supervisor，
或让 supervisor 成为唯一的拉起出口、由编排向它下单。

### 6.7 两侧是互相监督，别搞成循环拉起
Python 侧已有**反方向**的看门狗：`AGENT_LOOP_PPID` 指向游戏进程，游戏一退 Python 自己退
（`server.py` 的 `_idle()` 轮询）。这个对称性要保留：
- **C# 盯 Python 死活**（拉起）
- **Python 盯游戏死活**（自退）

---

## 7. 建议的模型：一个持有者，三个输入

```
                    ┌─────────────────────────────────────────┐
                    │  BrainLink（唯一状态持有者 + 唯一起作用点）  │
                    │  输入①  进程层   _proc.HasExited          │
                    │  输入②  传输层   WsClient.IsConnected     │
                    │  输入③  应用层   心跳 RPC 往返            │
                    │  输出   一个状态 + 一个动作                │
                    └─────────────────────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
        【拉起】只由①驱动      【重连】只由②驱动      【横幅】由聚合状态驱动
        dead → relaunch         断 → 重连（已有）      不健康就显示
        退避 + 上限            不涉及进程              不等玩家发消息
```

**状态定义（建议）**

| 状态 | 判据 | UI | 动作 |
|---|---|---|---|
| `Starting` | 进程活 且 从未连上 | 横幅"正在唤醒…" | 等（有上限，如 30s） |
| `Ready` | 进程活 且 `IsConnected` | 无 | — |
| `Reconnecting` | 进程活 且 曾连上 且 现在断 | 横幅"连接中断，重连中…" | 等 WS 重连（已有） |
| `Down` | `_proc.HasExited`（或 owned 且句柄失效） | 横幅"已停止，正在重启…" | **重新拉起**（退避 + 上限） |
| `Unresponsive` | `IsConnected` 但心跳超时 | 横幅"无响应" | 提示（自动重启待定） |

**三条不变量**

1. **不跨层推断**：WS 断 ≠ 进程死。进程死 ⟹ WS 断（单向）。
2. **拉起只有一个出口**：`BrainLink`。配置重启编排也走它（下单，不自己 `Process.Start`）。
3. **每个状态都必须有一个玩家可见的表现** —— 静默失败是这份代码明令禁止的。

---

## 8. 落地情况（2026-09-14 已实施 1–4）

| # | 改动 | 落点 |
|---|---|---|
| 1 | `WsClient.Send` 丢弃帧时记一行日志（按次数节流 1/20，**日志在锁外**） | `WsClient.cs` `Send`/`NoteDropped` |
| 2 | 发送前查 `IsConnected`，断则按**真实状态**提示玩家 | `ChatPresenter.cs` 提交路径 + 压缩按钮 |
| 3 | 修闩锁：`Init` 按**进程层事实**判断，而不是"一辈子只拉一次" | `Launcher.LaunchPythonOnce` |
| 4 | `BrainLink`：进程层判据 + 退避重生 + 横幅 | `BrainLink.cs`（新），`Launcher`/`ModMain`/`ConfigPresenter`/`UnreadBanner` 接线 |
| 5 | 心跳 RPC（应用层） | **未做**（见 §4 最后一行） |

顺带修掉的连带问题：
- **重连后不补 `open_chat`**：断线期间发出的 `open_chat` 被丢弃，而 `WsClient.Connected`
  原先只被用来重拉 initiative 参数 → "连接回来了但当前 NPC 的 agent 没激活"。
  现在 `BrainLink` 在"重新变成 Ready"时调 `ChatPresenter.ReopenAfterReconnect()`。
- **横幅复用了现成的 `UnreadBanner`**（HUD 级常驻、挂 `g.root`、纯提示不可点），
  新增 `ShowSystem()` 走同一宿主，抬头换成「系统」、正文预算放宽。
  **没有新建 UI**，也不需要改 AB 预制体。

---

## 8.1 自动拉起（#4）引入的新风险 —— 动手前必须知道

这一项**改变了发行行为**：以前 Python 死了只是"哑掉"（安静但无害），
现在 C# 会**主动在玩家机器上创建进程**。所以下面每一条都要有对应的闸。

| # | 风险 | 为什么会发生 | 已下的闸 |
|---|---|---|---|
| 1 | **拉起风暴 / 抢端口** | 判据若用"WS 断开 N 秒"，启动那几秒会被当成死亡 → 拉第二份 → bind 失败秒退 → 再拉… | 判据只用**进程层**；`StartGraceSeconds = 35s`（只在"本次拉起从未连上过"时生效）；退避 4/15/45s；`MaxRelaunch = 3` |
| 2 | **与配置重启编排打架** | 编排要**故意**杀旧进程，而 BrainLink 判据是"死了就拉" → 两边同时 `Process.Start` | `BrainLink.Suspend()` 包住整个编排，成功/中止两条路径都 `Resume()`；端口变更那条**故意不 Resume**（反正要整机重启） |
| 3 | **句柄失真时误判** | onefile 的句柄是 bootloader；父进程没了但 Python 子进程还活着的窗口内，会误以为"死了" | 破坏性动作要求**两层都同意**（进程层 `Dead` **且** 传输层断）——句柄失真时 WS 通常还连着，走不到拉起分支；`ProcessState()` 的 `Unknown` 一律**不下结论** |
| 4 | **非 owned 被误拉** | 玩家自己 `python scripts/server.py` 起的实例，拉了就两个进程抢端口 | `LaunchAttempted` 专门区分"我们负责"与"玩家自起"，`CanRelaunch = LaunchAttempted && EntryResolved` |
| 5 | **首次拉起失败后无限重试** | 缺 exe / 哨兵指错时重试无意义 | `EntryResolved` 为假 → 直接 `GaveUp`，不白试三轮 |
| 6 | **退出瞬间误拉** | 游戏退出/回主界面途中进程消失，可能拉起一个父进程正在死的孤儿 | `Destroy()` 先 `BrainLink.Stop()`；帧回调随场景销毁，主界面期间不监测 |
| 7 | **横幅刷屏** | 断线每 2s 一轮，"断开→恢复"会反复触发 | 只跨**严重度**变化才提示；非致命状态 10s 节流；`GaveUp` 不节流（必须让玩家看到） |
| 8 | **杀软反复拦截** | `Process.Start` 成功但进程秒死 → 退避重试到上限 | 上限 3 次后进 `GaveUp` 并横幅告知"请重启游戏"；另留**运行期哨兵** `_diag_no_relaunch.txt`，可让玩家放一个空文件就地停用自动拉起（**不用重新编译**） |
| 9 | **主线程阻塞** | `HasExited` 是系统调用，每帧查是浪费 | 轮询节流到 0.5s，且由 `g.timer.Frame` 驱动（天然主线程，可直接碰 UI） |

**仍然没解决的**：§4 最后一行「进程活着、socket 通、但 Python 事件循环卡死」——
这种情况 BrainLink 会一直认为 `Ready`。要覆盖它需要应用层心跳 RPC（#5，未做）。

---

## 9. 相关位置速查

| 关注点 | 文件:行 |
|---|---|
| 拉起入口 | `csharp/Launcher.cs:47`（once）/ `:63`（重启）/ `:140`（核心） |
| 进程句柄 | `csharp/Launcher.cs:34`、`:38-40`（那句"不用来判断存活"） |
| 启动闩锁 | `csharp/ModMain.cs:39`、`:196` |
| WS 状态机 | `csharp/WsClient.cs:128-158`（RunLoop） |
| 连接状态真值 | `csharp/WsClient.cs:69`（`IsConnected`） |
| 静默丢弃 | `csharp/WsClient.cs:564-571` |
| 误导的判据 | `csharp/UI/ChatPresenter.cs:37`、`:301-305` |
| 配置重启编排 | `csharp/UI/ConfigPresenter.cs:46-58`、`:557`、`:624` |
| 系统提示出口 | `csharp/UI/ChatWindow.cs:389`（`AppendSystemNotice`） |
| 现成的横幅控件 | `csharp/UI/AbContactPanel.cs:269`（`UnreadBanner`，可复用样式） |
| Python 侧看门狗 | `scripts/server.py`（`AGENT_LOOP_PPID` + `_idle()` 轮询，反方向） |
