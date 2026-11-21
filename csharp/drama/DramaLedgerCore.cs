/// <summary>
/// DramaLedgerCore —— 「剧情流水账」的**纯逻辑核心**（零 Unity 依赖，故单元测试工程直接编同一份源码）。
///
/// ## 它解决什么问题（真机证据，别省这一步）
/// 分段器（`DramaSegmentCore`）是「为了点『对话』时投增量」而设计的视图，天生**有损**：
///   · 段收口后只活 600 秒（`DeliverTtlSeconds`）⇒ 长战斗里内容会先死掉；
///   · 只留 4 段（`MaxDeliverables`）⇒ 人多时互相挤；
///   · 判不出「这页属于谁」就整页丢弃（`OnPage` 的 `UnitId.Length == 0` 分支）。
///
/// 10-24 真机实测（副本 4901，16 分钟）：
/// ```
/// 18:31:24  ★直塞完成★ + BattleStart       ← 事件
/// 18:31:31 ~ 18:36:17  剧情 24004…1450102  共 12 条
/// 18:36:44  段收口 ⇒ 起 600s 倒计时
/// 18:46:44  可投段过期未投出 ⇒ 丢弃（4TTqqu，共 12 条）   ← 内容在此刻消失
/// 18:47:17  BattleEndFront                ← 事件
/// ```
/// **战斗结束时内容已经没了 33 秒**。所以「打完再回头读段」这条路在最需要它的长副本里
/// 必然拿到空，而且日志看起来一切正常。⇒ 必须有一份**不过期**的流水账。
///
/// ## 三条纪律（每条都对应一次会静默失效的事故形状）
///   ① **只增不改、没有投递游标**：账本一旦有「消费」概念，早晚有人拿它当投递源 ⇒
///      「判不出就闭嘴」作废（会把别人的话投给当前对话对象）。投递永远是段的事。
///   ② **不设保质期，只设容量**：`MaxEntries` 满了丢最旧的，但**必须留痕**
///      （`DroppedTotal` / `TrimNote`）—— 静默丢内容是本工程最怕的失败形态。
///   ③ **不做语义判断**：不去重合并、不猜归属、不猜说话人。判不出就如实存空串，
///      由**读者**决定要不要闭嘴。
///
/// ## 为什么要有「事件」行
/// 从账本里抽「某场战斗的剧情」需要一个**边界**。用固定时间窗猜（"往前 60 秒"）在
/// 进副本这一幕（`610911`，比队友进场早 95 秒）上就会漏。改成让**事件自己插钉子**：
/// 战斗开始插 `battle_begin`、结束插 `battle_end`，抽取 = 取最后一对钉子之间的内容。
/// 边界由**发生的事实**给出，不靠猜时长。
///
/// 线程：全部主线程（与 `DramaSegmentCore` 同约束）。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    /// <summary>流水账里的一条。</summary>
    internal sealed class DramaLedgerEntry
    {
        /// <summary>全局自增（1 起），跨容量裁剪单调 —— 用它判"某条还在不在账里"。</summary>
        public int Seq;
        /// <summary>"page" = 剧情页正文；"option" = 玩家点的选项；"event" = 事件钉子。</summary>
        public string Kind = "page";
        /// <summary>归属对方的 unitID（判不出 = 空串）。事件行恒为空。</summary>
        public string UnitId = "";
        /// <summary>"npc" / "player" / "choice" / ""（判不出）。</summary>
        public string Speaker = "";
        /// <summary>说话人显示名（判不出 = 空串）。</summary>
        public string Name = "";
        /// <summary>屏幕上的成品话，一字不改；事件行 = 事件名。</summary>
        public string Text = "";
        public int DramaId;
        public float At;
        /// <summary>我方私有段的窗（`ModIds.Mod + 0..999`）。**照记**（账本是全量），
        /// 但读者可以据此过滤 —— 我们自己弹的窗不该出现在"剧情见闻"里。</summary>
        public bool Private;

        internal DramaLedgerEntry Copy()
        {
            DramaLedgerEntry e = new DramaLedgerEntry();
            e.Seq = Seq; e.Kind = Kind; e.UnitId = UnitId; e.Speaker = Speaker;
            e.Name = Name; e.Text = Text; e.DramaId = DramaId; e.At = At; e.Private = Private;
            return e;
        }
    }

    /// <summary>一次区间抽取的结果。</summary>
    internal sealed class DramaLedgerWindow
    {
        /// <summary>区间内的条目（按发生顺序）。</summary>
        public readonly List<DramaLedgerEntry> Items = new List<DramaLedgerEntry>();
        /// <summary>找到了配对的起点钉子（false 且 `HadAnyMarker` 为真 ⇒ 起点被裁掉了）。</summary>
        public bool StartFound;
        /// <summary>见过**任何**起点钉子（用来区分"这场根本没开过窗"与"开过但被裁掉了"）。</summary>
        public bool HadAnyMarker;
        /// <summary>账本此前裁掉过条目（结果可能不完整，调用方应如实上报）。</summary>
        public bool Trimmed;
    }

    internal sealed class DramaLedgerCore
    {
        // ---------------- 常量 ----------------

        /// <summary>容量上限（条）。一次副本最多几百页，3000 条够覆盖好几场。
        /// 满了丢最旧的 —— 只影响"回看能看多久"，不影响任何投递。</summary>
        internal const int MaxEntries = 3000;

        /// <summary>战斗区间起点的事件名（由 `★直塞完成★` 插 —— 那一拍才是"这场要写履历"的前提）。</summary>
        internal const string EventBattleBegin = "battle_begin";
        /// <summary>战斗区间终点的事件名（由 `BattleExit` 插，且**只在窗真的开着时**插）。</summary>
        internal const string EventBattleEnd = "battle_end";

        /// <summary>连续重复判定的最小间隔（秒）：同一条文本在这个窗内重复到达只记一次。
        /// 窗口重建 / 重复 `InitData` / 按钮复用都会造成重复上报（与 `NoteOption` 的去抖同源）。</summary>
        internal const float DupWindowSeconds = 0.5f;

        // ---------------- 状态 ----------------

        private readonly List<DramaLedgerEntry> _items = new List<DramaLedgerEntry>();
        private int _seq;

        internal int Count { get { return _items.Count; } }
        /// <summary>累计被裁掉的条数（>0 ⇒ 账本有断点，读者必须如实说"可能不全"）。</summary>
        internal int DroppedTotal { get; private set; }
        /// <summary>当前还留在账本里的最老序号（没有条目时 = 0）。</summary>
        internal int OldestSeq { get { return _items.Count == 0 ? 0 : _items[0].Seq; } }
        /// <summary>最新一条的序号（没有条目时 = 0）。</summary>
        internal int NewestSeq { get { return _items.Count == 0 ? 0 : _items[_items.Count - 1].Seq; } }

        // ---------------- 写入 ----------------

        /// <summary>
        /// 记一条。**不做任何归属判断**（判不出就存空串）—— 这是它与段的核心区别：
        /// 段的 `OnPage` 在 `unitId` 为空时整页丢弃，账本不许丢。
        ///
        /// 连续重复（同 kind + 同文本 + 同说话人 + 间隔 &lt; `DupWindowSeconds`）直接忽略：
        /// 窗口重建/重复上报会让同一页来两次，账本是给人看和给模型读的，重复没有价值。
        /// </summary>
        internal DramaLedgerEntry Add(string kind, string unitId, string speaker, string name,
                                      string text, int dramaId, bool isPrivate, float now)
        {
            string t = text == null ? "" : text.Trim();
            if (t.Length == 0) return null;

            string k = string.IsNullOrEmpty(kind) ? "page" : kind;
            if (_items.Count > 0)
            {
                DramaLedgerEntry last = _items[_items.Count - 1];
                if (last.Kind == k && last.Text == t
                    && (last.Name ?? "") == (name ?? "")
                    && (last.Speaker ?? "") == (speaker ?? "")
                    && now - last.At >= 0f && now - last.At < DupWindowSeconds)
                    return null;
            }

            DramaLedgerEntry e = new DramaLedgerEntry();
            e.Seq = ++_seq;
            e.Kind = k;
            e.UnitId = unitId ?? "";
            e.Speaker = speaker ?? "";
            e.Name = name ?? "";
            e.Text = t;
            e.DramaId = dramaId;
            e.Private = isPrivate;
            e.At = now;
            _items.Add(e);
            Trim();
            return e;
        }

        /// <summary>插一根**事件钉子**（`Text` = 事件名，如 `battle_begin` / `battle_end`）。
        /// 事件行不参与"剧情见闻"的渲染，只当边界用。</summary>
        internal DramaLedgerEntry Mark(string eventName, string unitId, float now)
        {
            return Add("event", unitId, "", "", eventName, 0, false, now);
        }

        /// <summary>当前是否有一个"开了没关"的战斗窗。</summary>
        internal bool WindowOpen { get { return _windowOpen; } }
        private bool _windowOpen;

        /// <summary>
        /// 开战斗窗。**已经开着就什么都不做**（`false`）。
        ///
        /// 为什么必须幂等：一场副本里队友可能被**多次**塞进容器（多波次 / 多次 `addBattleNPCData`），
        /// 而 `★直塞完成★` 会跟着打多次。若每次都插一根 `begin`，区间里就会多出几根钉子，
        /// 抽取时"最后一个 begin"会把前面几波切掉。
        /// </summary>
        internal bool BeginWindow(string unitId, float now)
        {
            if (_windowOpen) return false;
            if (Mark(EventBattleBegin, unitId, now) == null) return false;
            _windowOpen = true;
            return true;
        }

        /// <summary>
        /// 关战斗窗并插 `end`。**没开过就什么都不做**（`false`）。
        ///
        /// 为什么必须判"开过没有"（这条是真会静默出错的）：`BattleExit` 在**单人战斗**里也会来。
        /// 若那时也插一根 `end`，账本里就会出现"只有 end 没有 begin"的孤儿钉子；
        /// 而抽取是"从最后一个 end 往前找 begin、途中撞到另一个 end 就停"——
        /// 孤儿 end 会把**真正那场**的配对堵死，症状是履历里剧情永远是空的。
        /// </summary>
        internal bool EndWindow(string unitId, float now)
        {
            if (!_windowOpen) return false;
            _windowOpen = false;
            return Mark(EventBattleEnd, unitId, now) != null;
        }

        private void Trim()
        {
            int over = _items.Count - MaxEntries;
            if (over <= 0) return;
            _items.RemoveRange(0, over);
            DroppedTotal += over;
        }

        // ---------------- 读取 ----------------

        /// <summary>
        /// 取**最后一对**「begin … end」钉子之间的内容（`end` 取账本里最后一条 `end` ——
        /// 调用方通常在"刚刚"插完 `end` 才来读）。
        ///
        /// 为什么要"最后一对"而不是"最后一次 begin 之后"：一场副本里队友可能被**多次**塞进容器
        /// （多波次/多次 `addBattleNPCData`），那样会有好几个 `begin`。取"最后一个 begin"
        /// 会把前面几波的剧情切掉；取"上一个 end 之后的第一个 begin"才是整场。
        ///
        /// `includeEvents = false` 时区间里的事件行不返回（渲染用）；
        /// 起点被容量裁掉时 `StartFound=false` 且 `HadAnyMarker=true`，调用方**必须**如实上报
        /// "只拿到后半段"，不许装作完整。
        /// </summary>
        internal DramaLedgerWindow TakeWindow(string beginEvent, string endEvent, bool includeEvents)
        {
            DramaLedgerWindow w = new DramaLedgerWindow();
            w.Trimmed = DroppedTotal > 0;
            if (_items.Count == 0) return w;

            int endIdx = -1;
            for (int i = _items.Count - 1; i >= 0; i--)
                if (_items[i].Kind == "event" && _items[i].Text == endEvent) { endIdx = i; break; }
            if (endIdx < 0) return w;        // 没有结束钉子 ⇒ 这场没记过

            int beginIdx = -1;
            for (int i = endIdx - 1; i >= 0; i--)
            {
                if (_items[i].Kind != "event") continue;
                if (_items[i].Text == beginEvent) { beginIdx = i; break; }
                // 碰到更早的 end ⇒ 说明这一对不完整（begin 已被裁掉），停止
                if (_items[i].Text == endEvent) break;
            }
            w.HadAnyMarker = true;
            w.StartFound = beginIdx >= 0;

            int from = beginIdx >= 0 ? beginIdx + 1 : 0;
            for (int i = from; i < endIdx; i++)
            {
                if (!includeEvents && _items[i].Kind == "event") continue;
                w.Items.Add(_items[i]);
            }
            return w;
        }

        /// <summary>
        /// 按时间区间读（**不带钉子**的通用读法）。给"以后可能有的别的用途"用；
        /// 副本履历**不用**它（时间窗会在进副本这一幕上判错，见文件头）。
        /// </summary>
        internal List<DramaLedgerEntry> Range(float from, float until, bool includeEvents)
        {
            List<DramaLedgerEntry> r = new List<DramaLedgerEntry>();
            for (int i = 0; i < _items.Count; i++)
            {
                DramaLedgerEntry e = _items[i];
                if (e.At < from || e.At > until) continue;
                if (!includeEvents && e.Kind == "event") continue;
                r.Add(e);
            }
            return r;
        }

        /// <summary>
        /// 从盘上恢复一条（`EnsureLoaded` 用）。
        ///
        /// 与 `Add` 的区别只有三点，都是**为了让恢复出来的是原样**：
        ///   · 保留原 `Seq`（否则"最老序号"会漂，`TakeWindow` 的裁剪判定就失准）；
        ///   · **不做连续重复去重**（盘上已经去过重了；换个进程时钟也重置了，去重会误杀）；
        ///   · 不往回缩容量 —— 由调用方在恢复完后统一整一次（见 `TrimToCap`）。
        /// </summary>
        internal void Restore(DramaLedgerEntry e)
        {
            if (e == null || string.IsNullOrEmpty(e.Text)) return;
            _items.Add(e.Copy());
            if (e.Seq > _seq) _seq = e.Seq;
        }

        /// <summary>恢复完统一裁到容量上限（一次性，避免恢复过程中反复 O(n) 移位）。</summary>
        internal void TrimToCap()
        {
            Trim();
        }

        /// <summary>最新一条（没有条目时 null）。给接线层"刚插完钉子要落盘"用，
        /// 避免为了取一条而 `Snapshot()` 整份复制（战斗中会白分配）。</summary>
        internal DramaLedgerEntry Last { get { return _items.Count == 0 ? null : _items[_items.Count - 1]; } }

        /// <summary>换世界：整账作废（`unitID` 换档即失效，留着会把上一档的人当成这一档的）。</summary>
        internal void Reset()
        {
            _items.Clear();
            _seq = 0;
            DroppedTotal = 0;
            _windowOpen = false;
        }

        /// <summary>诊断用快照（调试日志/测试断言）。返回副本，外部改不到内部。</summary>
        internal List<DramaLedgerEntry> Snapshot()
        {
            List<DramaLedgerEntry> r = new List<DramaLedgerEntry>(_items.Count);
            for (int i = 0; i < _items.Count; i++) r.Add(_items[i].Copy());
            return r;
        }

        /// <summary>一行摘要（日志用）：条数 / 最老-最新序号 / 裁掉多少。</summary>
        internal string Brief()
        {
            return "账本 " + _items.Count + " 条（seq " + OldestSeq + "~" + NewestSeq
                 + (DroppedTotal > 0 ? "，已裁 " + DroppedTotal + " 条" : "") + "）";
        }
    }
}
