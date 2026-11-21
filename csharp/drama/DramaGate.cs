/// <summary>
/// DramaGate —— 模态确认窗与 WS 延迟 response 之间的静态中间人。
///
/// 背景：工具弹 UICustomDramaDyn 确认窗后，LLM 回合必须挂起等玩家选择——
/// 但主线程绝不能阻塞（onClick 同为主线程 → 死锁）。正解：
///   ToolExecutor 弹窗后立即返回带 __pending__ 标记的 JObject → WsClient 憋住
///   response（暂存 reqId→slot）→ 玩家点击回调执行真动作 → Resolve(slot, 真实结果)
///   → WsClient 补发 response → Python future 被填上 → LLM 才收口。
/// Python 零改动（ws_channel 的 request 只认 req_id 配对，不在乎等多久）。
///
/// 线程约定：所有公共方法都在 Unity 主线程调用（工具执行/按钮回调/帧回调）。
///
/// 已知限制（有意为之）：
///   - 同一 dramaId 同时只允许一个待确认窗（模态 UI 语义）；撞窗时 TryDefer 返回
///     false，工具返回"已有确认窗待处理"让 LLM 稍后重试。
///   - 过期窗口不主动关闭（关窗 API 不明），但 TryClaim 保证迟到点击不会误执行动作。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class DramaGate
    {
        /// <summary>默认过期秒数：超时自动 Resolve 成"玩家未响应"，防 LLM 回合永久挂起、防窗位永久占用。
        ///
        /// ★必须**严格小于** Python 侧的 RPC 超时 `network.request_timeout`（发行配置 120s）★
        /// 09-23 定：120 → 90。两侧一样长（120 vs 120）是一条**边界竞争**：Python 的 deadline 从
        /// **发帧**起算、本类的 deadline 从 `TryDefer` 起算（晚几毫秒）⇒ 永远 Python 先超时。
        /// 后果正是 ws_channel 注释里点名的形态：模型看到的是"请求超时"（一句什么信息都没有的失败），
        /// 而几十毫秒后这边 Resolve 出的"玩家未响应"回来时 future 已被 pop —— **无人认领，静默丢弃**。
        /// 留出余量（90 对 120，30s 缓冲）后，"这次等待为什么结束"永远由**能拿到结局的这一侧**先说出口。
        /// 长等（跨战斗，分钟量级）不受影响：它们走 `SetWaitMode(..., beat:true)` + 心跳滑动，
        /// 与这个常量无关（见 `BattleOutcome`）。</summary>
        public const int DefaultExpireSeconds = 90;

        /// <summary>心跳间隔（秒）。只发给标了 <see cref="Pending.Beats"/> 的长等槽位
        /// （切磋要跨过一场战斗 = 分钟量级），Python 收到就把这条 RPC 的 deadline 往后推，
        /// 于是"对端失联"能在 90 秒内被发现，而不必把超时放大到小时级。见 <see cref="OnStillPending"/>。</summary>
        private const int BeatSeconds = 30;

        private sealed class Pending
        {
            public int Slot;
            public int DramaId;
            /// <summary>到点自动 Resolve。原来是「创建时刻 + 常量」的写法 —— 切磋跨战斗要
            /// 中途续期（<see cref="Renew"/>），所以改成直接存截止时刻。</summary>
            public DateTime Deadline;
            /// <summary>长等槽位：定期发心跳。</summary>
            public bool Beats;
            public DateTime NextBeatAt;
            /// <summary>给玩家看的一句话（「等待你的选择」/「战斗进行中」），随心跳帧带出去。
            /// 放这里而不是让 WsClient 去问 BattleOutcome：保持 bridge 层不依赖 drama 层的具体类。</summary>
            public string Phase;
        }

        private static readonly object _lock = new object();
        private static readonly Dictionary<int, Pending> _pending = new Dictionary<int, Pending>();
        private static int _slotSeq;

        /// <summary>「链在收尾」占位（`BattleOutcome` 在收口时置位；剧情窗一关 / 超时 / 换世界时清）。
        /// 语义 = **还能不能领新票**：置位期间 `TryDefer` 一律拒绝（**已经在表里的槽位不受影响**）。
        ///
        /// 为什么要它（09-22 实测）：处置页收口那一刻本类的 busy 键就释放了，而链的尾页 10 秒后才来
        /// （实证：收口 22:53:14.833 → 尾页 22:53:24.474 → 关窗 22:53:26.019），那段时间
        /// `world.battle.isBattle` 仍为真（游戏自己在结算）—— 此时发起新动作会与正在演出的链并行，
        /// 而"剧情窗创建/销毁期开 UI"正是 09-10 那次立绘槽位变 0 尺寸事故的形状。
        /// 放在本类的原因同 `SuppressAiOption`：它是"领票门"的一部分 —— 判据只写一处，
        /// 别指望每个动作工具各自记得查。</summary>
        private static bool _chainHold;
        private static DateTime _chainHoldUntil;
        private static string _chainHoldReason = "";
        /// <summary>上一次 `TryDefer` 被拒的**人话原因**。只有"链在收尾"会写它；撞窗 busy 留空，
        /// 由调用方用自己的既有措辞 —— 两件事的正确处置不同，不能共用一句话（见 `RejectText`）。</summary>
        private static string _lastReject = "";

        /// <summary>slot → 最终结果（Ok/Fail 形态的 JObject）。WsClient 订阅：反查 reqId 补发 response。</summary>
        public static event Action<int, JObject> OnResolved;

        /// <summary>长等槽位的"我还在等"心跳（每 <see cref="BeatSeconds"/> 秒一次，主线程）。
        /// WsClient 订阅：反查 reqId 发一条 `type:"pending"` 帧给 Python（它据此滑动 deadline），
        /// 并广播进 UiEvent（对话窗的忙标自愈据此不误判"帧丢了"）。</summary>
        public static event Action<int> OnStillPending;

        /// <summary>
        /// 「AI 对话」按钮抑制标志（ToolExecutor.Execute 期间置 true）。
        /// 放本类的原因：ToolExecutor 是 verify 工程链接编译的共享文件，而 DramaAiOption（读方）
        /// 仅存在于主工程——标志存这个两工程都编译的类里，两边都能过编译（UnitLookup CS0103 教训）。
        /// 仅主线程读写。
        /// </summary>
        public static bool SuppressAiOption;

        // 对话窗让位/自动恢复状态机已于 2026-09-09 整体退役：AbChatPanel 迁移到游戏 UI 管理器
        // 体系（g.ui.OpenUI + UIBase 继承，层级由游戏自动排"后开盖先开"），确认窗/论道弹窗
        // 后开自然盖住对话窗，无需手动让位/恢复。

        /// <summary>
        /// 登记一个待确认窗。同 dramaId 已有 pending 时返回 false（撞窗/busy）。
        /// **每次**调用都会 `EnsureTick()` 确保过期检查的帧回调在跑（11-19 起句柄归 `FrameTicker`：
        /// 世界重载把游戏那边的登记清掉之后，下一次登记就能自愈，不再靠"全进程一次的布尔"）。
        /// </summary>
        public static bool TryDefer(int dramaId, out int slot)
        {
            return TryDefer(dramaId, DefaultExpireSeconds, false, out slot);
        }

        /// <summary>登记一次挂起，并指定这个槽位的等待口径。
        /// `expireSeconds` = 无续期情况下的最长等待；`beat` = 定期发心跳（跨战斗这种分钟量级的等待，
        /// 光靠一个大超时数字不行：它既会误伤慢的战斗，又会在对端真失联时让人干等到天荒地老）。</summary>
        public static bool TryDefer(int dramaId, int expireSeconds, bool beat, out int slot)
        {
            EnsureTick();
            lock (_lock)
            {
                // 链在收尾：**新票一律不发**（已有槽位照常走完）。原因写进 `_lastReject`，
                // 调用方用 `RejectText(...)` 取出 —— 别说成"玩家没响应"，那句会把模型引向空等重试。
                if (_chainHold)
                {
                    _lastReject = _chainHoldReason;
                    slot = 0;
                    return false;
                }
                _lastReject = "";
                foreach (var p in _pending.Values)
                {
                    if (p.DramaId == dramaId) { slot = 0; return false; }
                }
                slot = ++_slotSeq;
                var now = DateTime.UtcNow;
                _pending[slot] = new Pending
                {
                    Slot = slot, DramaId = dramaId, Beats = beat,
                    Deadline = now.AddSeconds(expireSeconds),
                    NextBeatAt = now.AddSeconds(BeatSeconds),
                };
                return true;
            }
        }

        // ------------------------------------------------------------------ 链收尾占位（见 `_chainHold`）

        /// <summary>链是否还在收尾。置位期间：新动作被 `TryDefer` 拒（见 `_lastReject`/`RejectText`），
        /// **并且** `WsClient.SendGameDrama` 会把它当 `busy` 交给 Python —— 于是"玩家在处置页上点「对话」"
        /// 那一轮同样只许说话 + 只读，不会在结算半空中插进一个动作。</summary>
        public static bool ChainHold { get { lock (_lock) return _chainHold; } }

        /// <summary>起收尾占位（`BattleOutcome` 在收口时若剧情窗还开着就调）。重复调用只续期。</summary>
        public static void SetChainHold(string reason, int maxSeconds)
        {
            lock (_lock)
            {
                _chainHold = true;
                _chainHoldUntil = DateTime.UtcNow.AddSeconds(Math.Max(1, maxSeconds));
                if (!string.IsNullOrEmpty(reason)) _chainHoldReason = reason;
            }
        }

        /// <summary>撤收尾占位（剧情窗关了 / 换世界 / 新台账开始 / 超时）。返回是否真的撤掉，便于调用方去重日志。</summary>
        public static bool ClearChainHold(string why)
        {
            lock (_lock)
            {
                if (!_chainHold) return false;
                _chainHold = false;
                _chainHoldReason = "";
                _lastReject = "";
            }
            try { ModMain.P("[DramaGate] 收尾占位已撤（" + why + "）→ 新动作放行"); } catch { }
            // ★ 边沿证据（09-24）：收尾占位被撤 = 这条链的收尾确实结束了 ⇒ 排队的动作
            //   据此把稳定窗从保守档降到短档（判据见 `DeferredActionQueue.NoteChainOver`）。
            try { DeferredActionQueue.NoteChainOver("收尾占位已撤：" + why); } catch { }
            return true;
        }

        /// <summary>被拒时给 LLM 的那句话：`fallback` = "撞窗 busy"的既有措辞；
        /// 若这次拒是因为**链在收尾**，返回那句专属措辞（两者的正确处置完全不同，不能共用一句话）。</summary>
        public static string RejectText(string fallback)
        {
            lock (_lock) return string.IsNullOrEmpty(_lastReject) ? fallback : _lastReject;
        }

        /// <summary>把某个槽位的截止时刻往后推（滑动过期）。返回 false = 槽位已经不在了
        /// （已 Resolve / 已过期）→ 调用方必须收手，别再往这个槽位发结果。</summary>
        public static bool Renew(int slot, int expireSeconds)
        {
            lock (_lock)
            {
                Pending p;
                if (!_pending.TryGetValue(slot, out p)) return false;
                p.Deadline = DateTime.UtcNow.AddSeconds(expireSeconds);
                return true;
            }
        }

        /// <summary>槽位还挂着吗（调用方据此判断"我这次等待还有没有归宿"）。</summary>
        public static bool IsPending(int slot)
        {
            lock (_lock) return _pending.ContainsKey(slot);
        }

        /// <summary>改这个槽位的等待口径：开始发心跳 + 换过期秒数 + 给玩家一句话。
        /// 切磋用它从"等玩家点选项（120s 足够）"切到"等一场战斗打完（时长不可知）"。
        /// 返回 false = 槽位已不在 → 调用方收手。</summary>
        public static bool SetWaitMode(int slot, int expireSeconds, bool beat, string phase)
        {
            lock (_lock)
            {
                Pending p;
                if (!_pending.TryGetValue(slot, out p)) return false;
                var now = DateTime.UtcNow;
                p.Deadline = now.AddSeconds(expireSeconds);
                p.Beats = beat;
                if (p.NextBeatAt <= now) p.NextBeatAt = now.AddSeconds(BeatSeconds);
                if (!string.IsNullOrEmpty(phase)) p.Phase = phase;
                return true;
            }
        }

        /// <summary>这个槽位当前在等什么（随心跳帧发给界面显示；不在表里返回空串）。</summary>
        public static string PhaseOf(int slot)
        {
            lock (_lock)
            {
                Pending p;
                return _pending.TryGetValue(slot, out p) ? (p.Phase ?? "") : "";
            }
        }

        /// <summary>世界重载/读档：把所有在飞的槽位立刻按同一个原因收口。
        /// 为什么必须做：那场战斗/那扇窗随旧世界一起没了，**结局永远不会来**；而
        /// `ModIds.AiActionGate` 这个 busy 键是**所有动作工具共用**的（见 ToolExecutor
        /// .DeferNativeUnitAction），槽位不释放 = 之后每次送礼/邀约/传功/切磋都被挡成
        /// 「已有一个行动在等待玩家响应」，整套动作系统瘫到重启游戏为止。
        /// 同一形状的事故已有一次：立绘像素缓存 `_mem` 存场景级纹理，世界重载后留僵尸条目
        /// （ModMain.cs 09-19 那段注释）。病根都是**静态表活过世界重载**。</summary>
        public static void ForceResolveAll(string reason)
        {
            List<KeyValuePair<int, JObject>> hit = null;
            lock (_lock)
            {
                if (_pending.Count == 0) return;
                foreach (var kv in _pending)
                    if (kv.Value.Beats)   // 只动长等槽位：普通确认窗由它自己的 120s/点击收口
                    {
                        hit = hit ?? new List<KeyValuePair<int, JObject>>();
                        hit.Add(new KeyValuePair<int, JObject>(kv.Key, new JObject
                        {
                            ["success"] = false,
                            ["error"] = reason + "，这次动作没等到结局（不是玩家拒绝）",
                        }));
                    }
                if (hit != null) foreach (var e in hit) _pending.Remove(e.Key);
            }
            if (hit == null) return;
            try { ModMain.P("[DramaGate] " + reason + "：强制收口 " + hit.Count + " 个长等槽位"); } catch { }
            foreach (var e in hit) OnResolved?.Invoke(e.Key, e.Value);
        }

        /// <summary>生成给 WsClient 的 pending 标记（HandleMessage 据此憋住 response）。</summary>
        public static JObject MakePendingMarker(int slot)
        {
            return new JObject { ["__pending__"] = true, ["__slot__"] = slot };
        }

        /// <summary>
        /// 原子领票：玩家点击后先 Claim 再执行动作，防止"超时已 Resolve 但玩家又点了"导致动作迟到误执行。
        /// 领票成功（返回 true）后调用方必须 Resolve(slot, result)。
        /// </summary>
        public static bool TryClaim(int slot)
        {
            lock (_lock)
            {
                return _pending.Remove(slot);
            }
        }

        /// <summary>是否有待确认窗（供 NpcInitiativeMonitor 状态闸）。</summary>
        public static bool HasPending
        {
            get { lock (_lock) return _pending.Count > 0; }
        }

        /// <summary>补发结果（领票成功后调用；也供过期 Tick / 动作完成回调用）。幂等：无人订阅/未知 slot 自然无事发生。
        ///
        /// 修复：**Resolve 同时释放票据**。Resolve 的语义是"结果已定，不再需要等玩家"，
        /// 票据使命即结束。原先只有 TryClaim（玩家点击）与过期 Tick 会 Remove，
        /// 于是所有"不走玩家点击"的路径都会把票据留在表里 →
        /// `HasPending` 假挂起 → 状态闸被多挡到 ExpireSeconds(120s) 过期为止。
        /// 真机实测（邀约）：`yao_yue OnEnd 挂起命中 slot=1` 之后又空了 2 分钟才恢复触发
        /// （Player.log 1651→1978 共 6 次 `状态闸命中（原因：DramaGate.HasPending）`）。
        /// 泄漏点当时有三处：UnitActionPending.Complete（OnEnd）、ToolExecutor 的 AI 行动完成回调、
        /// ShowDramaService 自动确认分支——在 Resolve 里统一释放，比逐个补 TryClaim 更不易再漏。
        /// 语义不变：过期 Tick 仍先 Remove 再 Resolve（超时后玩家迟到点击照样被 TryClaim 拒绝）。</summary>
        public static void Resolve(int slot, JObject result)
        {
            lock (_lock)
            {
                _pending.Remove(slot);
            }
            OnResolved?.Invoke(slot, result);
        }

        /// <summary>窗弹出失败时静默注销（不发事件——WsClient 还没登记过这个 slot）。</summary>
        public static void Cancel(int slot)
        {
            lock (_lock)
            {
                _pending.Remove(slot);
            }
        }

        /// <summary>帧回调：① 过期未响应 → 自动 Resolve 成超时结果（玩家侧窗口变僵尸但 TryClaim 防误执行）；
        /// ② 长等槽位定期发心跳（机理见 <see cref="OnStillPending"/>）。</summary>
        private static void Tick()
        {
            List<KeyValuePair<int, JObject>> expired = null;
            List<int> beats = null;
            bool holdExpired = false;
            lock (_lock)
            {
                // ③ 收尾占位到期（钝兜底）。**必须放在 `_pending` 早退之前** —— 占位生效时表里
                //    通常一个槽都没有，早退会让它永远不过期、把动作系统挡到重启为止。
                if (_chainHold && DateTime.UtcNow >= _chainHoldUntil)
                {
                    _chainHold = false;
                    _chainHoldReason = "";
                    _lastReject = "";
                    holdExpired = true;
                }
                if (_pending.Count > 0)
                {
                    DateTime now = DateTime.UtcNow;
                    foreach (var kv in _pending)
                    {
                        if (now >= kv.Value.Deadline)
                        {
                            expired = expired ?? new List<KeyValuePair<int, JObject>>();
                            expired.Add(new KeyValuePair<int, JObject>(kv.Key,
                                new JObject { ["success"] = false, ["error"] = "玩家长时间未响应，确认已超时" }));
                        }
                        else if (kv.Value.Beats && now >= kv.Value.NextBeatAt)
                        {
                            beats = beats ?? new List<int>();
                            beats.Add(kv.Key);
                            kv.Value.NextBeatAt = now.AddSeconds(BeatSeconds);
                        }
                    }
                    if (expired != null)
                        foreach (var e in expired) _pending.Remove(e.Key);
                }
            }
            if (holdExpired)
            {
                try { ModMain.P("[DramaGate] 收尾占位超时（剧情窗一直没关）→ 新动作放行"); } catch { }
            }
            // 事件一律在锁外发：订阅方（WsClient / DramaNativeClaim）会回过来拿自己的锁
            if (beats != null)
                foreach (int s in beats) OnStillPending?.Invoke(s);
            if (expired != null)
                foreach (var e in expired) OnResolved?.Invoke(e.Key, e.Value);
        }

        /// <summary>确保常驻帧回调在跑（11-19：句柄交给 `FrameTicker`，不再用全进程一次的布尔）。
        /// 每次 `TryDefer` 都走到这里 ⇒ 世界重载清掉帧登记之后，下一次弹确认窗就自动救活。</summary>
        private static void EnsureTick()
        {
            FrameTicker.Ensure(FrameTicker.KDramaGate, "dramagate", () =>
            {
                // 耗时采样（TickProfiler，09-28 卡顿定位）：槽位过期/心跳检查（空表时很轻）
                long __t = TickProfiler.Begin();
                try { Tick(); }
                finally { TickProfiler.End("dramagate", __t); }
            });
        }
    }

    /// <summary>
    /// DeferredActionQueue —— 「此刻发不出去的动作」排队器的**接线层**（09-24，用户拍板 B 方案）。
    ///
    /// 【解决什么】玩家在战斗/链收尾期间请求的动作（实测：处置页上让 NPC 赠送灵石），游戏此刻
    /// **发不出去** —— 原生窗进了 `WorldSystemMgr` 的系统队列不出队，硬发就是 90 秒后静默超时
    /// （`RPC call_tool 返回失败（90031ms）：玩家长时间未响应`）。旧形态只有两条路：硬发（静默超时）
    /// 或 Fail 让模型"稍后再试"——后者被用户否掉（不许把强制行为寄希望于模型记住）。
    ///
    /// 本类 = 第三条路的**接线**（状态机/判据全在 `DeferredActionCore`，本文件只做四件事）：
    ///   · 注入：`WaitBlocker`（主工程给"现在能不能执行"的判据）、`TipShower`、`Notifier`、`Logger`；
    ///   · 搬运：`TryEnqueue` 把执行闭包挂进核的条目（`Tag`），核不解释它；
    ///   · 推进：`Tick` 每帧问核要动作 —— 执行 / 超时 / 等玩家 / 退让四条路都落在这里；
    ///   · 收口：结局（`DramaGate.OnResolved`）→ 一句人话 → 告知（**能说才说**）。
    ///
    /// 执行闭包由工具层给（`ToolExecutor` 的排队分支）：它内部**重新解析目标**（不持有旧 unit 包装），
    /// 且执行前会**复查一次"此刻还忙不忙"** —— 又忙了返回 null ⇒ 本类退回等待（用户拍板：不判失败）。
    ///
    /// 为什么全部靠注入：本类写在 `DramaGate.cs` 里（**双工程文件**，见 `UnitActionPending.cs`
    /// 头部的两工程约定），禁引 `ModMain`/`UITipItem` 等主工程类型。verify / 未接线时
    /// `WaitBlocker == null` ⇒ `ShouldQueue()` 恒 false ⇒ 工具走原路径，行为与本改造前一致。
    ///
    /// 【为什么"已有排队"也算"此刻发不出去"】队首正占着游戏自己的动作闸（`ModIds.AiActionGate`），
    /// 后到的动作**此刻同样发不出去**。按序排在后面，比"撞上去拿一句失败"更接近用户要的语义
    /// （他说的是"办完再办"，不是"排队期间不许再提"）。多槽 FIFO、上限 `DeferredActionCore.MaxWaiting`。
    /// </summary>
    internal static class DeferredActionQueue
    {
        /// <summary>现在能不能执行？返回非空 = 不能（人话原因，只进日志）。主工程注册。</summary>
        public static Func<string> WaitBlocker;
        /// <summary>入队时的玩家可见提示（`UITipItem.AddTip`）。主工程注册；未注册则静默。</summary>
        public static Action<string> TipShower;
        /// <summary>完成/失败告知（主工程发 `npc_initiative` intent=action_done）。主工程注册。</summary>
        public static Action<string, string> Notifier;
        /// <summary>日志（主工程注册为 `ModMain.P`；未注册则丢弃）。</summary>
        public static Action<string> Logger;

        /// <summary>状态机（纯逻辑核，有独立单测 `csharp/tests/DeferredActionCoreTests`）。
        /// 本类只搬它的结论，不自己判时间/顺序 —— 那些规则一出错就是静默的。</summary>
        private static readonly DeferredActionCore _core = new DeferredActionCore();

        private static bool _subscribed;

        /// <summary>「链收尾结束」的**边沿证据**时刻（`DateTime.MinValue` = 还没有过）。
        /// 两个来源：`BattleEndMgr.BattleEnd`（游戏自己报"整条流程走完了"）、收尾占位被撤
        /// （`ClearChainHold`）。它的唯一用途 = 把稳定窗从保守档（`StableSeconds`）降到短档
        /// （`StableEdgeSeconds`）：有事件就不必靠"猜多久算结束"（与 `BattleEndHook` 头注释同一口径）。
        /// 它是个**事实**（那条链确实收尾了），所以不做 TTL、一直留到条目收口；乱序/重复都不会有害。</summary>
        private static DateTime _chainOverAt = DateTime.MinValue;

        /// <summary>blocker 判据的采样窗（秒，09-28 卡顿修复）。判据是 UI 层扫描（多次 `GetUI` /
        /// `GetLayerTopUI` / `UiName` 封送 / `new UITypeBase`），贵；而它只服务于稳定窗判定
        /// （`StableEdgeSeconds` 0.5s / `StableSeconds` 1.5s），0.25s 粒度完全够 ——
        /// 最坏后果只是"条件消失后多等 ≤0.25s 才起算稳定窗"，相对 0.5~1.5s 的窗可忽略。</summary>
        private const double BlockerSampleSeconds = 0.25;
        private static DateTime _blockerAt = DateTime.MinValue;
        private static bool _blockerVal;

        public static bool HasPending { get { return _core.Count > 0; } }

        /// <summary>帧回调路径专用的 blocker 采样缓存（同一次 Tick 内两个调用点共用）。
        /// **不改变 <see cref="BlockerNow"/> 的实时语义**：`ShouldQueue()`（新动作进来时）
        /// 与工具执行前的复查仍走实时判据 —— 那是低频事件，且"此刻"必须是最新的。</summary>
        private static bool BlockerCached(DateTime now)
        {
            if ((now - _blockerAt).TotalSeconds < BlockerSampleSeconds) return _blockerVal;
            _blockerAt = now;
            _blockerVal = BlockerNow().Length > 0;
            return _blockerVal;
        }

        /// <summary>边沿入口（主工程的两个信号源调；幂等、可重复）。
        /// `source` 只进日志 —— 排障时要能看出"这次为什么敢用 0.5s 档"。</summary>
        public static void NoteChainOver(string source)
        {
            bool first = _chainOverAt == DateTime.MinValue;
            _chainOverAt = DateTime.UtcNow;
            if (first)
                try { Logger?.Invoke("[DeferQueue] 链收尾结束（边沿=" + source + "）→ 稳定窗降到 "
                                     + DeferredActionCore.StableEdgeSeconds + "s 档"); } catch { }
        }

        /// <summary>排队中的人话描述（队列满 / 日志用；空 = 没排队）。</summary>
        public static string Describe()
        {
            return _core.Describe(DateTime.UtcNow);
        }

        /// <summary>
        /// 新动作调用进来时的判据（`ToolExecutor` 的闸）：**此刻发不出去**或**已有排队**。
        /// 未接线（verify / 主工程还没 Init）恒 false —— 这条保证"没接线 = 行为与改造前一致"。
        /// </summary>
        public static bool ShouldQueue()
        {
            if (_core.Count > 0) return true;
            return BlockerNow().Length > 0;
        }

        /// <summary>此刻的"不能执行"原因（空串 = 可以执行）。未注入/抛异常一律当"可以" ——
        /// 判据坏了不该把动作系统整个卡死（宁可恢复旧行为：硬发）。</summary>
        internal static string BlockerNow()
        {
            var f = WaitBlocker;
            if (f == null) return "";
            try { return f() ?? ""; } catch { return ""; }
        }

        /// <summary>接线（主工程 `ModMain.Init` 调一次）：挂帧回调 + 订阅结局。重复调用无副作用；
        /// `TryEnqueue` 里也各挂一份（双保险）—— 谁先跑都不影响。</summary>
        public static void Attach()
        {
            EnsureSubscribed();
            EnsureTick();
        }

        /// <summary>入队。返回 false = 排不下（队列已满）—— 调用方改用"排不下"的拒绝文案。
        /// 注意：**"已有一个在排队"不再是拒绝理由**（09-24 用户拍板：多槽 FIFO，继续排）。
        /// `npcId` 为空时不排（告知没有收件人 = 排了等于静默丢 —— 交调用方走原路径硬发）。</summary>
        public static bool TryEnqueue(string npcId, string op, string summary, Func<JObject> exec)
        {
            if (exec == null || string.IsNullOrEmpty(npcId)) return false;
            var now = DateTime.UtcNow;
            if (!_core.TryEnqueue(npcId, op, summary, now, exec))
            {
                try { Logger?.Invoke("[DeferQueue] 排不下（上限 " + DeferredActionCore.MaxWaiting + "）：" + summary); } catch { }
                return false;
            }
            Attach();
            try { Logger?.Invoke("[DeferQueue] 入队：" + summary + "（当前发不出去，等战斗链收场后自动执行）"); } catch { }
            try { TipShower?.Invoke(summary + "：待此间事了自动办理"); } catch { }
            return true;
        }

        /// <summary>帧回调（主工程 Init 也会挂一份，双保险；重复挂由 `FrameTicker` 挡住）。
        ///
        /// ★09-28 卡顿修复（实机 Player.log 定位）★：原先每帧无条件求值**两次** `BlockerNow()`
        /// （本方法一次 + `DrainNotice` 一次），而常态（队列空、无待发告知）下那些判据是**纯空转**。
        /// 判据要扫 UI 层（6×`GetUI` / 6×`GetLayerTopUI` / `UiName` 封送 / `new UITypeBase`），
        /// 实测 avg 0.3~0.46ms/帧、尖峰 96ms / 42ms / 12ms —— 正是"大地图行走隔几秒一顿"的主犯。
        /// 现在：① 队列空且无待发告知 → 一帧都不算（这一帧没有任何事可做）；
        ///       ② 有活时按 `BlockerSampleSeconds` 节流；
        ///       ③ 同一次 Tick 内 blocker 只求值一次，两个调用点共用。</summary>
        public static void Tick()
        {
            var now = DateTime.UtcNow;
            // ① 空转早退。`_readySince` 不必在这条路上重置 —— 下一次入队时 TryEnqueue 会重置
            //    （`if (_queue.Count == 1) _readySince = DateTime.MinValue;`）。
            if (_core.Count == 0 && _core.NoticeCount == 0) return;

            bool blocked = BlockerCached(now);
            try { DrainNotice(now, blocked); } catch { }
            try
            {
                // 边沿证据（BattleEnd / 收尾占位被撤）在位 ⇒ 用 0.5s 短档；否则保守 1.5s。
                var step = _core.Poll(now, blocked, _chainOverAt != DateTime.MinValue);
                if (step == DeferredActionCore.Step.Ceiling)
                {
                    var head = _core.Head;
                    string txt = (head != null ? head.Summary : "那个动作") + "：未能完成（等待战斗结束超时（"
                               + DeferredActionCore.HardCeilingSeconds + "秒））";
                    _core.CompleteHead(now, txt);
                    try { Logger?.Invoke("[DeferQueue] 硬顶收口：" + txt); } catch { }
                    return;
                }
                if (step == DeferredActionCore.Step.Execute) ExecuteHead(now);
            }
            catch (Exception e)
            {
                try { Logger?.Invoke("[DeferQueue] Tick 异常（本次跳过，下一帧继续）: " + e.Message); } catch { }
            }
        }

        /// <summary>执行队首。四条出口：等玩家（pending 标记 → 记票据）/ 退让（执行体返回 null）/
        /// 收口（真结果 → 组人话 → 告知）/ 异常（当失败收口，绝不留悬挂）。</summary>
        private static void ExecuteHead(DateTime now)
        {
            var it = _core.Head;
            if (it == null) return;
            // 落刀留痕：实机排障要能一眼看出"这次是按哪一档静默执行的"（0.5s 档是否够用，
            // 全靠这条日志与随后的执行结果对齐判断）。
            try
            {
                Logger?.Invoke("[DeferQueue] 落刀：" + it.Summary + "（静默档=" +
                               (_chainOverAt != DateTime.MinValue
                                    ? DeferredActionCore.StableEdgeSeconds + "s（有边沿证据）"
                                    : DeferredActionCore.StableSeconds + "s（轮询判出）") + "）");
            }
            catch { }
            var exec = it.Tag as Func<JObject>;
            if (exec == null)
            {
                _core.CompleteHead(now, it.Summary + "：未能完成（内部错误：执行体丢失）");
                return;
            }
            JObject res;
            try { res = exec(); }
            catch (Exception e) { res = Fail("执行异常：" + e.Message); }

            // ★ 执行体说"此刻还是发不出去"（返回 null）→ **退回等待，不判失败**（用户 09-24 拍板）：
            //   谁都不该因为"恰好又撞上"而丢掉这件事；核会把稳定窗重新计一遍。
            if (res == null)
            {
                _core.RequeueHead(now);
                try { Logger?.Invoke("[DeferQueue] 执行时又忙起来了 → 退回等待：" + it.Summary); } catch { }
                return;
            }

            if (res["__pending__"] != null && res["__pending__"].Value<bool>())
            {
                // 执行体自己也在等玩家（弹了确认窗）：记下票据，结局经 `OnResolved` 回来
                int slot = -1;
                try { if (res["__slot__"] != null) slot = res["__slot__"].Value<int>(); } catch { }
                _core.MarkSlot(slot);
                try { Logger?.Invoke("[DeferQueue] 已执行：" + it.Summary + "（已发起，等玩家响应 slot=" + slot + "）"); } catch { }
                return;
            }
            _core.CompleteHead(DateTime.UtcNow, Summarize(it, res));
        }

        /// <summary>结果 → 一句人话（Python 侧再包成舞台指令；本类不做文案）。</summary>
        private static string Summarize(DeferredActionCore.Item it, JObject res)
        {
            bool ok = res != null && res["success"] != null && res["success"].Value<bool>();
            if (!ok)
            {
                string err = res != null && res["error"] != null ? res["error"].ToString() : "未知原因";
                try { Logger?.Invoke("[DeferQueue] 收口（失败）：" + it.Summary + " → " + err); } catch { }
                return it.Summary + "：未能完成（" + err + "）";
            }
            var data = res["data"] as JObject;
            if (data != null)
            {
                var acc = data["accepted"];
                if (acc != null && acc.Type == JTokenType.Boolean && !acc.Value<bool>())
                {
                    try { Logger?.Invoke("[DeferQueue] 收口：" + it.Summary + " → 对方没有收下"); } catch { }
                    return it.Summary + "：已完成（对方没有收下）";
                }
            }
            try { Logger?.Invoke("[DeferQueue] 收口：" + it.Summary + "：已完成"); } catch { }
            return it.Summary + "：已完成";
        }

        /// <summary>告知派发：**能说才发**（blocker 为空 = 玩家不忙）；一直说不出口的由核按
        /// TTL 作废，这里只留痕（"半分钟后凭空开口"比不说更糟）。
        /// `now`/`blocked` 由 `Tick` 传入 —— 09-28 卡顿修复：同一次 Tick 只取一次时钟、
        /// 只求值一次 blocker 判据（原先这里自己再算一遍，空转翻倍）。</summary>
        private static void DrainNotice(DateTime now, bool blocked)
        {
            var gone = _core.ExpireNotices(now);
            if (gone != null)
                foreach (var t in gone)
                    try { Logger?.Invoke("[DeferQueue] 告知超时作废（一直没能开口）：" + t); } catch { }

            string npc, text;
            if (!_core.TryTakeNotice(now, blocked, out npc, out text)) return;
            try { Logger?.Invoke("[DeferQueue] 告知已发：" + text); } catch { }
            try { Notifier?.Invoke(npc, text); } catch { }
        }

        /// <summary>世界重载（读档/换档）：排队中的动作随旧世界作废（与 `DramaGate.ForceResolveAll` 同钩子）。
        /// 为什么必须做：条目里的目标（unitID/名字）换档即失效，最坏情形是那个 id 在新档里是**另一个人**。</summary>
        public static void OnWorldChanged()
        {
            int n = _core.Clear();
            _chainOverAt = DateTime.MinValue;   // 边沿同理作废：那场链属于**旧世界**
            if (n > 0)
                try { Logger?.Invoke("[DeferQueue] 世界重载：作废 " + n + " 个排队中的动作"); } catch { }
        }

        /// <summary>结局订阅（幂等）：执行体挂起后的结果由 `DramaGate.Resolve` 送回来。</summary>
        private static void EnsureSubscribed()
        {
            if (_subscribed) return;
            _subscribed = true;
            DramaGate.OnResolved += (slot, res) =>
            {
                if (!_core.IsHeadSlot(slot)) return;     // 不是队列在等的那张票 → 与本类无关
                var it = _core.Head;
                if (it == null) return;
                _core.CompleteHead(DateTime.UtcNow, Summarize(it, res));
            };
        }

        /// <summary>确保常驻帧回调在跑（11-19：句柄交给 `FrameTicker`，不再用全进程一次的布尔）。
        /// 每次 `TryDefer` 都走到这里 ⇒ 世界重载清掉帧登记之后，下一次弹告知/确认窗就自动救活。</summary>
        private static void EnsureTick()
        {
            FrameTicker.Ensure(FrameTicker.KDeferQ, "deferq", () =>
            {
                // 耗时采样（TickProfiler，09-28 卡顿定位）：每帧 BlockerNow（5 个 interop 判定）也在内
                long __t = TickProfiler.Begin();
                try { Tick(); }
                finally { TickProfiler.End("deferq", __t); }
            });
        }

        private static JObject Fail(string error)
        {
            return new JObject { ["success"] = false, ["error"] = error };
        }
    }
}
