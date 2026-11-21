/// <summary>
/// DeferredActionCore —— 「此刻发不出去的动作」排队器的**纯逻辑核**（09-24，用户拍板 B 方案）。
///
/// 【它解决什么】玩家在战斗 / 剧情链收尾期间请求的动作（实测：处置页上让 NPC 赠送灵石）游戏
/// **发不出去** —— 原生窗进了 `WorldSystemMgr` 的系统队列不出队，硬发就是 90 秒后静默超时
/// （`RPC call_tool 返回失败（90031ms）：玩家长时间未响应`）。旧形态只有两条路：硬发（静默超时）
/// 或 Fail 让模型"稍后再试"——后者被用户否掉（不许把强制行为寄希望于模型记住）。
///
/// 本核 = 第三条路的状态机：**立即回「已受理」→ 战斗链干净后自动执行 → 执行完主动告知**。
///
/// 【状态机（每个转移都对应一个静默失败形态，所以逐条写清）】
///   · 入队：`TryEnqueue`（FIFO，上限 `MaxWaiting`；满则拒，由接线层换成句人话）
///   · 等待：`Poll(now, blocked, edgeSeen)` 每帧问一次 —— blocked = 现在能不能执行（接线层给的
///           **让步判据**：链收尾/战斗中/剧情窗/Mask·Window 层界面/别的动作在等玩家/不在主世界）
///   · 执行：条件消失后还要**稳定一段静默**才轮到 `Execute` —— 防"战斗位刚假、处置页/尾页还没登场"
///           就抢跑（`DramaGate` 收尾占位与 `BattleOutcome` 的同一类教训）。静默**分两档**：
///           无边沿证据 1.5s（只靠轮询）、有边沿证据 0.5s（`BattleEndMgr.BattleEnd` / 收尾占位被撤）
///   · 挂起：执行体自己也在等玩家（弹了原生确认窗）→ `MarkSlot` 记下票据，`Poll` 不再推进，
///           直到结局经接线层（`DramaGate.OnResolved`）走 `CompleteHead` 回来
///   · 退让：执行那一刻又忙起来了（接线层的执行体返回"还不能"）→ `RequeueHead` **退回等待**，
///           不判失败（用户 09-24 拍板）—— 谁都不该因为"恰好又撞上"而丢掉这件事
///   · 收口：硬顶 `HardCeilingSeconds`（与 `BattleOutcome` 同值），到点必然收摊，
///           绝不出现"排了队却没有下文"；世界重载 `Clear` 一律作废
///   · 告知：`CompleteHead` 把结局排进 `_notices`（FIFO + `NoticeTtlSeconds`）——
///           接线层**能说才说**（玩家还在忙就先攒着）
///
/// 【为什么核单独一个文件、且零 Unity / 零 Newtonsoft】
/// 本仓库的既有约定（`DramaSegmentCore` / `ChatFrameQueue` / `BubbleCommitPolicy` …）：
/// **判据归核、接线归层**。核里每一条时间/顺序规则出错在真机上都是静默的（排了队不执行 /
/// 抢跑 / 排两次 / 重复执行），所以必须能被 `csharp/tests/DeferredActionCoreTests` 直接链接
/// 编译、在开发机上跑断言；而"注入判据、执行闭包、发通知"这些必须碰主工程类型的东西，
/// 留在 `DramaGate.cs` 的 `DeferredActionQueue` 里。
/// 时间一律由调用方传入（`now`）：核自己不读时钟 ⇒ 单测能精确构造"过了 599 秒""稳定窗差 10 毫秒"。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal sealed class DeferredActionCore
    {
        /// <summary>同时最多几个待办。到顶就**拒收**（不是丢最老）：动作是玩家/模型明确要的结果，
        /// 悄悄丢一个比明确说"排不下了"坏得多（后者模型能自己改主意，前者无人知道）。</summary>
        public const int MaxWaiting = 8;

        /// <summary>单个待办的硬顶（秒，与 `BattleOutcome` 同值）。到点必然收摊并留痕。</summary>
        public const int HardCeilingSeconds = 600;

        /// <summary>告知在队列里的存活时间（秒）。一直说不出口（玩家一直在战斗/剧情里）就作废 ——
        /// 半分钟后凭空冒出一句"刚才那件事办好了"比不说更让人摸不着头脑。</summary>
        public const int NoticeTtlSeconds = 120;

        /// <summary>**无边沿证据**时的稳定窗（秒）：只靠轮询判出"不忙了"（战斗位/剧情窗刚落下），
        /// 保守一点。取 1.5 与 `BattleOutcome.ChainHoldQuietSeconds` 同值 —— 同一类
        /// "UI 重建/销毁中间帧抖动"（那边的原话是"吃掉 UI 重建/销毁中间帧的抖动"）。</summary>
        public const double StableSeconds = 1.5;

        /// <summary>**有边沿证据**时的稳定窗（秒）：`BattleEndMgr.BattleEnd`（游戏自己报"整条流程走完了"）
        /// 或"收尾占位被撤"刚刚发生过 ⇒ 链确实收尾了，剩下的只是游戏关窗 / 销毁它自己的 UI。
        /// 这半秒买的是"**别在销毁中间帧里开新窗**"（09-10 立绘槽位变 0 尺寸事故的形状）。
        ///
        /// ★ 为什么不再往 0.2s / 1 帧压（用户 09-24 问能否到 1s/0.5s）★
        /// 代价是**不对称**的：太早 → 新窗又沉进游戏系统队列不出队 → 90 秒静默超时（正是本类要修的
        /// 故障，而且是静默的）；多等 0.5 秒相对"几分钟的战斗/链"完全可忽略。所以下限就定在这里，
        /// 再短要靠实机日志证明"0.5s 档执行后从未出现系统队列不出队"再动。</summary>
        public const double StableEdgeSeconds = 0.5;

        /// <summary>一个待办。`Tag` 只由接线层解释（挂执行闭包），核不认识它 —— 保持核零依赖。</summary>
        public sealed class Item
        {
            public string NpcId = "";
            public string Op = "";
            public string Summary = "";
            public DateTime EnqueuedAt;
            /// <summary>执行体弹了原生确认窗时记下的票据（>=0 = 正在等玩家点）；结局经
            /// 接线层 `CompleteHead` 回来。`-1` = 还没发起。</summary>
            public int Slot = -1;
            /// <summary>接线层载荷（执行闭包）。核只负责把它随条目一起搬运。</summary>
            public object Tag;

            public double WaitedSeconds(DateTime now)
            {
                return (now - EnqueuedAt).TotalSeconds;
            }
        }

        /// <summary>一条待发告知（结局 → 一句话）。</summary>
        public sealed class Notice
        {
            public string NpcId = "";
            public string Text = "";
            public DateTime At;
        }

        private readonly List<Item> _queue = new List<Item>();
        private readonly List<Notice> _notices = new List<Notice>();
        /// <summary>条件**首次**满足的时刻（稳定窗起点）。`DateTime.MinValue` = 当前还没满足/已重置。</summary>
        private DateTime _readySince = DateTime.MinValue;

        public int Count { get { return _queue.Count; } }
        public bool IsFull { get { return _queue.Count >= MaxWaiting; } }
        /// <summary>队首（= 当前唯一有资格执行的那个）。空队返回 null。</summary>
        public Item Head { get { return _queue.Count > 0 ? _queue[0] : null; } }
        public int NoticeCount { get { return _notices.Count; } }

        /// <summary>排队中的人话描述（第二个动作被拒 / 日志用；空队返回空串）。</summary>
        public string Describe(DateTime now)
        {
            var head = Head;
            if (head == null) return "";
            string s = head.Summary + "（已排队 " + (int)head.WaitedSeconds(now) + "s）";
            if (_queue.Count > 1) s += "，另有 " + (_queue.Count - 1) + " 个在排队";
            return s;
        }

        /// <summary>入队。返回 false = 队列已满（调用方改用"排不下"的拒绝文案）。
        /// `tag` = 接线层的执行闭包（核不解释）。</summary>
        public bool TryEnqueue(string npcId, string op, string summary, DateTime now, object tag)
        {
            if (_queue.Count >= MaxWaiting) return false;
            var it = new Item
            {
                NpcId = npcId ?? "",
                Op = op ?? "",
                Summary = string.IsNullOrEmpty(summary) ? (op ?? "") : summary,
                EnqueuedAt = now,
                Tag = tag,
            };
            _queue.Add(it);
            // 只有"变成队首"才重置稳定窗：排在后面的还没轮到，它的稳定窗从它自己成为队首那刻起算
            if (_queue.Count == 1) _readySince = DateTime.MinValue;
            return true;
        }

        /// <summary>每帧问一次：这一步该干什么。</summary>
        public enum Step
        {
            /// <summary>空队（无事可做）。</summary>
            Idle,
            /// <summary>继续等（被挡 / 稳定窗未满 / 正等玩家点窗）。</summary>
            Wait,
            /// <summary>轮到执行队首了（接线层此刻调它的执行体）。</summary>
            Execute,
            /// <summary>硬顶到了：接线层给队首收个失败尾（并排告知）。</summary>
            Ceiling,
        }

        /// <summary>推进一格。`blocked` = 此刻不能执行（接线层注入的**让步判据**：链收尾/战斗中/剧情窗/
        /// Mask·Window 层界面/别的动作在等玩家/不在主世界 —— 有竞争就等，见 `ModMain` 的注入点）；
        /// `edgeSeen` = 已经有"链收尾结束"的**边沿证据**（`BattleEndMgr.BattleEnd` / 收尾占位被撤）
        /// —— 它把稳定窗从保守档降到短档（`StableEdgeSeconds`）。
        ///
        /// 判定顺序**必须**是：硬顶 → 等玩家 → 被挡 → 稳定窗 —— 每一步都有理由：
        ///   · 硬顶先判：连"正在等玩家"的条目也要能被收掉（票据万一丢了就没有别的出口了）；
        ///   · 等玩家（Slot>=0）先于被挡：那段等待的结局不来自这里，来自 `CompleteHead`；
        ///   · 被挡先于稳定窗：被挡期间必须**清零**稳定窗 —— 竞争一出现就把续期时间归零，
        ///     恢复的那一刻从头再计（用户 09-24 拍板："有竞争就等待，续期时间置为零"）。</summary>
        public Step Poll(DateTime now, bool blocked, bool edgeSeen)
        {
            var head = Head;
            if (head == null) { _readySince = DateTime.MinValue; return Step.Idle; }
            if (head.WaitedSeconds(now) > HardCeilingSeconds) return Step.Ceiling;
            if (head.Slot >= 0) return Step.Wait;
            if (blocked) { _readySince = DateTime.MinValue; return Step.Wait; }
            if (_readySince == DateTime.MinValue) { _readySince = now; return Step.Wait; }
            double need = edgeSeen ? StableEdgeSeconds : StableSeconds;
            if ((now - _readySince).TotalSeconds < need) return Step.Wait;
            return Step.Execute;
        }

        /// <summary>执行体弹了确认窗：记下票据（此后 `Poll` 不再推进它，等结局回来）。</summary>
        public void MarkSlot(int slot)
        {
            var head = Head;
            if (head != null) head.Slot = slot;
        }

        /// <summary>这个票据是不是队首在等的（`OnResolved` 分发时用：别的工具/别人的票据一概不认）。</summary>
        public bool IsHeadSlot(int slot)
        {
            var head = Head;
            return head != null && head.Slot == slot && slot >= 0;
        }

        /// <summary>执行时又忙起来了：**退回等待**（用户拍板不判失败）。稳定窗重新计。</summary>
        public void RequeueHead(DateTime now)
        {
            _readySince = DateTime.MinValue;
        }

        /// <summary>队首收摊：出队，并把一句人话排进告知队列（空文本 = 不告知）。
        /// 超时收尾（`Ceiling`）、执行完、等玩家等出结局 —— 三条路都汇到这里。</summary>
        public void CompleteHead(DateTime now, string noticeText)
        {
            var head = Head;
            if (head == null) return;
            _queue.RemoveAt(0);
            _readySince = DateTime.MinValue;
            if (!string.IsNullOrEmpty(noticeText))
                _notices.Add(new Notice { NpcId = head.NpcId ?? "", Text = noticeText, At = now });
        }

        /// <summary>丢弃所有条目与待发告知（世界重载/读档：排队中的动作随旧世界一起没意义了 ——
        /// 与 `DramaGate.ForceResolveAll` 同一个钩子、同一个理由）。返回被清掉的待办数。</summary>
        public int Clear()
        {
            int n = _queue.Count;
            _queue.Clear();
            _readySince = DateTime.MinValue;
            _notices.Clear();
            return n;
        }

        /// <summary>作废过期的告知（返回它们，供接线层留痕）。要在取用之前调。</summary>
        public List<string> ExpireNotices(DateTime now)
        {
            List<string> gone = null;
            for (int i = _notices.Count - 1; i >= 0; i--)
            {
                if ((now - _notices[i].At).TotalSeconds > NoticeTtlSeconds)
                {
                    gone = gone ?? new List<string>();
                    gone.Add(_notices[i].Text);
                    _notices.RemoveAt(i);
                }
            }
            if (gone != null) gone.Reverse();     // 与入队顺序一致（旧的先报）
            return gone;
        }

        /// <summary>取一条待发告知（FIFO）。返回 false = 没有可说的。
        /// `blocked` 为真时**一条都不取**（玩家还在忙 → 先攒着，别打断）—— 判据只写在这里，
        /// 免得接线层各自记得查。</summary>
        public bool TryTakeNotice(DateTime now, bool blocked, out string npcId, out string text)
        {
            npcId = ""; text = "";
            if (blocked) return false;
            while (_notices.Count > 0)
            {
                var n = _notices[0];
                if ((now - n.At).TotalSeconds > NoticeTtlSeconds) { _notices.RemoveAt(0); continue; }
                _notices.RemoveAt(0);
                npcId = n.NpcId; text = n.Text;
                return true;
            }
            return false;
        }
    }
}
