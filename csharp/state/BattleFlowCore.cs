/// <summary>
/// BattleFlowCore —— 「战前 / 战中」两态判据的**纯逻辑核**（10-25，用户拍板）。
///
/// 【它解决什么】
/// 用户报的原状（`Player.log` 10-25 18:51）：NPC 在寻仇页上调 `world_ai_action(attack)`，
/// 被排队；4 秒后玩家自己点了那一页上的「战斗」→ 引擎开打；而排队那条攻击在
/// 18:51:28.687 **落刀又开了一次同一页** —— 两场架叠在一起。之后处置页上点「对话」，
/// 段里只剩「不投」的行 ⇒ `TakeDelta` 返回 0 ⇒ `Trigger` 静默返回，玩家看到的是"没反应"。
///
/// 结论（用户原话）：**即将进入战斗时，不许 NPC 再发起"会再开一场仗"的工具**
/// —— 攻击 / 切磋 / 传送 / 召唤 当场拒绝；其余（赠予 / 讨要 / 传功 / 邀约 / 论道 /
/// 双修 / 行走）排队等此间事了；只读一律照常。**不是一刀切禁全部工具**。
///
/// 【为什么"战前"必须记住，不能只查当下】
/// 实测那 20 秒里，几个电平有一段**同时为假**的空洞：
///   18:51:28.141 剧情页关（页级闸灭） → 18:51:30.395 应战窗才弹
/// 而排队那条攻击正是在 28.687 落刀。只查"此刻这一帧"必然漏掉它 ⇒ 战前是**记忆态**：
/// 一旦点亮就保持，直到出现**明确的结束证据**（见 `NotePage` / `NoteOptionClicked` / `NoteSampled`）。
///
/// 【三个入口，一个状态】
///   ① 页级：这一页上摆着"开战按钮"（寻仇页 20203 的 100003、切磋确认页 21204 的 212042）
///   ② 窗级：进战斗的应战窗亮着（`NpcInitiativeMonitor.BattlePreShowing`）
///   ③ 打起来了：`BattleActive()`（= `IntoBattle` 那一刻翻真，两侧日志同毫秒 18:51:32.349）
/// ①② ⇒ 战前；③ ⇒ 战中。**战中不需要记忆**（它是实时电平），战前需要。
///
/// 【退出条件（全是"明确证据"，不看秒数）】
///   · 开打了 ⇒ 转战中；
///   · 战前期间点了**非开战**按钮 ⇒ 婉拒，退出；
///   · 开了一页**没有开战按钮**的新页、且没点过开战按钮 ⇒ 这场戏往下走了，退出；
///   · 应战窗关了但没进战斗 ⇒ 取消了，退出；
///   · 兜底：`FallbackSeconds` 内**一条证据都没有** ⇒ 退出（见下）。
///
/// 【兜底为什么必须有、又不是"用时间猜因果"】
/// 战前是记忆态 ⇒ 必须有出口，否则一个假信号能让某个 NPC **永久**不能攻击/切磋/传送，
/// 而且不报错（本仓最警惕的失败形态）。用户 10-25 已确认保留兜底。
/// 它**不参与任何归因判断**（不回答"是哪一枚选项引起的开战"），只回答
/// "这个状态已经没有任何证据支撑，还要不要继续挂着"。
/// 而且只在"一条证据都没有"时才走：应战窗 / 战斗位 / 新的开战页 ⇒ 一律续期，
/// 所以 10-25 那个 2 秒的假关窗根本走不到兜底。
///
/// 【为什么纯逻辑、零 Unity / 零 Newtonsoft】
/// 同 `DeferredActionCore` / `DramaSegmentCore`：这里每条规则错在真机上都是静默的
/// （该挡没挡 = 打两场；不该挡却挡 = NPC 哑了），所以必须能编成 net8.0 控制台程序
/// 在开发机上跑断言（`csharp/tests/BattleFlowCoreTests`）。时间一律由调用方传入。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    /// <summary>现在处在哪个态。`Idle` = 两样都不是。</summary>
    internal enum BattleFlowState
    {
        /// <summary>既不在战前也不在战中。</summary>
        Idle = 0,
        /// <summary>即将进入战斗（页上有开战按钮 / 应战窗亮着 / 刚点了开战按钮）。**记忆态**。</summary>
        PreBattle = 1,
        /// <summary>正在打（实时电平，不需要记忆）。</summary>
        InBattle = 2,
    }

    /// <summary>一个动作工具此刻该怎么处置。</summary>
    internal enum ToolVerdict
    {
        /// <summary>照常执行（只读，或不在战前/战中）。</summary>
        Pass = 0,
        /// <summary>排队等此间事了（本身与战斗不冲突，只是此刻弹窗会沉底）。</summary>
        Queue = 1,
        /// <summary>**当场拒绝**：会再开一场仗 / 会把玩家挪走，等会儿补做等于"把已发生的事再办一遍"。</summary>
        Reject = 2,
    }

    /// <summary>
    /// 「正在处理这次点击」的**作用域**（`ClickOption` prefix 开、postfix 关）—— 10-25 复核新增，
    /// 从接线层搬进核（原来那 6 行在 `BattleFlowGate` 里，只能真机验证）。
    ///
    /// 【它为什么值得单独一个类型】
    /// 它是**唯一会"学错东西"的地方**：学错一个编号的后果不是"少挡一次"，而是
    /// **那枚选项从此每次都被当成开战按钮** ⇒ 那一页一弹出来就进入战前 ⇒ 攻击/切磋/传送/召唤
    /// 被持续误拒（"多挡"，而且落盘后跨存档、跨重启都在）。本仓最警惕的失败形态。
    ///
    /// 【泄漏自愈：为什么要帧号】
    /// `Exit` 写在 postfix 里，而 Harmony 的 postfix **在原方法正常返回之后**才跑 ——
    /// 引擎的 `ClickOption` 内部要跑完整个选项效果（`DramaFunction` → `IntoDungeon` → `IntoBattle`），
    /// 任一处抛异常，postfix 就不跑、作用域**永远不闭合**。此后"引擎自己发起的进副本命令"
    /// （与任何点击无关）都会被记到那枚早就点过的选项上 ⇒ 学错。
    /// 帧号是对它的**结构性**否定：真机栈证明 `ClickOption → OptionsFunction → IntoDungeon`
    /// 是**同步调用**（有嵌套栈帧才可能在栈上），同步 ⇒ **同一帧**。所以"作用域只在开启它的那一帧有效"
    /// 不是时间窗（没有任何秒数），而是那条实测事实的直接推论。
    /// 代价：万一真机上它跨帧，结果是**学不到**（= 少挡），方向安全。
    /// </summary>
    internal struct ClickScope
    {
        /// <summary>一次 `OptionInScope` 的结论。</summary>
        public enum Probe
        {
            /// <summary>不在作用域里（没点过、或点完了）—— 引擎自己发起的，不许学。</summary>
            Outside = 0,
            /// <summary>作用域是**上一帧**留下的（postfix 没跑到）—— 已自动作废，不许学。</summary>
            Expired = 1,
            /// <summary>就在这次点击的作用域里 —— **可以学**，`optionId` 是被点的那一枚。</summary>
            Hit = 2,
            /// <summary>就在这次点击的作用域里，但那枚选项的编号读不到（`OptionIdOf` 给了 0）。
            /// 与 `Outside` 分开记：这两种在日志上含义完全不同（一个是"根本没人点"，
            /// 一个是"点了但配表字段读不到"）—— 合并就查不出漏读字段那类故障。</summary>
            NoId = 3,
        }

        private int _depth;
        private int _optionId;
        private int _frame;

        /// <summary>当前嵌套深度（正常 0/1；只为读日志与断言）。</summary>
        public int Depth { get { return _depth; } }

        /// <summary>进入作用域。返回 `prevOptionId`（交给 postfix 还回去，支持理论上不出现的嵌套）。
        ///
        /// ★先把"跨帧残留"清掉再进★ 否则泄漏的那一层会被叠进来：`Exit` 的语义是
        /// "把 `Enter` 还回来的值放回去"，一旦外层是上个帧泄漏下来的编号，它就被**还原成活的作用域**
        /// —— 同一帧后面来的引擎自发命令会记到那枚无关选项上（又变回"学错"）。
        /// 同帧的嵌套（正常不出现）不受影响：帧号相同就不清。</summary>
        public int Enter(int optionId, int frame)
        {
            if (_depth > 0 && _frame != frame)
            {
                _depth = 0;
                _optionId = 0;
            }
            int prev = _optionId;
            _depth++;
            _optionId = optionId;
            _frame = frame;
            return prev;
        }

        /// <summary>退出作用域。`prevOptionId` 是 `Enter` 还回来的那一层。</summary>
        public void Exit(int prevOptionId)
        {
            if (_depth > 0) _depth--;
            if (_depth == 0) { _optionId = 0; _frame = 0; }
            else _optionId = prevOptionId;
        }

        /// <summary>问"此刻还在不在作用域里、是哪一枚"。`nowFrame` 由调用方给（核不读时钟也不读引擎）。
        /// 帧号不符 ⇒ **顺手作废**（自愈），并报 `Expired` 而不是悄悄当 `Outside` ——
        /// 这两种在日志上要能分开，否则"学不到"永远查不出是没点还是泄漏。</summary>
        public Probe OptionInScope(int nowFrame, out int optionId)
        {
            optionId = 0;
            if (_depth <= 0) return Probe.Outside;
            if (_frame != nowFrame)
            {
                _depth = 0;
                _optionId = 0;
                _frame = 0;
                return Probe.Expired;
            }
            optionId = _optionId;
            return optionId > 0 ? Probe.Hit : Probe.NoId;
        }
    }

    internal sealed class BattleFlowCore
    {
        /// <summary>兜底秒数：这么久**一条证据都没有**才放掉战前。用户 10-25 拍板 60s。</summary>
        public const int DefaultFallbackSeconds = 60;

        private readonly HashSet<int> _battleIds = new HashSet<int>();
        private readonly List<int> _idOrder = new List<int>();
        private readonly int _fallbackSeconds;

        private BattleFlowState _state = BattleFlowState.Idle;
        /// <summary>玩家已经点过开战按钮 ⇒ 这场架一定会打，此后不再因"页没了/新页"退出。</summary>
        private bool _confirmed;
        private bool _preWasShowing;
        /// <summary>进战中后**真的看见过**战斗位亮（`IntoBattle` 是边沿、早于电平，见 `NoteSampled`）。</summary>
        private bool _sawBattleLevel;
        private DateTime _lastEvidenceAt = DateTime.MinValue;
        private int _lastPageId;

        public BattleFlowCore(IEnumerable<int> battleOptionIds, int fallbackSeconds)
        {
            if (battleOptionIds != null)
                foreach (int id in battleOptionIds) AddBattleOption(id);
            _fallbackSeconds = fallbackSeconds > 0 ? fallbackSeconds : DefaultFallbackSeconds;
        }

        public BattleFlowState State { get { return _state; } }
        public bool Confirmed { get { return _confirmed; } }
        public int LastPageId { get { return _lastPageId; } }
        /// <summary>战前或战中（= 要按"作战中"处置动作工具）。</summary>
        public bool Armed { get { return _state != BattleFlowState.Idle; } }
        public int FallbackSeconds { get { return _fallbackSeconds; } }
        public int Count { get { return _idOrder.Count; } }

        public bool IsBattleOption(int optionId) { return _battleIds.Contains(optionId); }

        /// <summary>登记一个"开战按钮"编号。返回 true = 这是新学到的（接线层据此落盘 + 留痕）。</summary>
        public bool AddBattleOption(int optionId)
        {
            if (optionId <= 0) return false;                       // 我方自制窗的负 id 一律不收
            if (!_battleIds.Add(optionId)) return false;
            _idOrder.Add(optionId);
            return true;
        }

        /// <summary>全部已知开战按钮（落盘顺序稳定，便于人眼对数）。</summary>
        public IEnumerable<int> BattleOptionIds { get { return _idOrder; } }

        /// <summary>
        /// 一页剧情建好了选项（`CreateOptions` 之后调）。
        /// **只认游戏自己的页**（id &gt; 0）：本 mod 自制的确认窗 id 是负数
        /// （`ModIds.Mod` = -803_158_451 起，实测日志里 -803158271/-803158341…），
        /// 把它们算进来会让"我方赠礼确认窗"把战前状态误清掉。
        /// </summary>
        public bool NotePage(int dramaId, int[] optionIds, DateTime now, out string why)
        {
            why = "";
            if (dramaId <= 0) return false;                       // 我方自制窗：不参与
            if (_state == BattleFlowState.InBattle) return false; // 战中压倒一切
            _lastPageId = dramaId;

            int which = 0;
            if (optionIds != null)
                foreach (int id in optionIds)
                    if (IsBattleOption(id)) { which = id; break; }

            if (which != 0)
            {
                _lastEvidenceAt = now;
                return Enter(BattleFlowState.PreBattle, "页 " + dramaId + " 上有开战按钮 " + which, out why);
            }
            // 新的一页、上面没有开战按钮，且玩家还没点过开战 ⇒ 这场戏没往战斗走
            if (_state == BattleFlowState.PreBattle && !_confirmed)
                return Exit("新页 " + dramaId + " 上没有开战按钮", out why);
            return false;
        }

        /// <summary>
        /// 玩家点了一枚选项（`ClickOption` **prefix**，早于引擎跑选项效果）。
        /// 命中开战按钮 ⇒ 进战前并记下"已确认"；战前期间点了别的 ⇒ 婉拒，退出。
        /// </summary>
        public bool NoteOptionClicked(int optionId, DateTime now, out string why)
        {
            why = "";
            if (_state == BattleFlowState.InBattle) return false;

            if (IsBattleOption(optionId))
            {
                _lastEvidenceAt = now;
                bool changed = Enter(BattleFlowState.PreBattle, "点了开战按钮 " + optionId, out why);
                _confirmed = true;
                return changed;
            }
            if (_state == BattleFlowState.PreBattle && !_confirmed)
                return Exit("点了非开战按钮 " + optionId + "（婉拒）", out why);
            return false;
        }

        /// <summary>
        /// 每帧（接线层按节流采样）问一次的两个电平。
        ///
        /// 求值顺序**必须**是：战斗中 → 战中退出 → 应战窗 → 应战窗关了没打 → 兜底。
        ///   · 战斗中排第一：接受应战时"窗关"与"进战斗"实测**同一毫秒**（18:51:32.349），
        ///     先判窗就会把"接受"误读成"取消"；
        ///   · 兜底在续期之后：还有电平亮着 ⇒ 说明这事没结束，兜底不该走。
        /// </summary>
        public bool NoteSampled(bool preShowing, bool battleActive, DateTime now, out string why)
        {
            why = "";

            if (battleActive)
            {
                _preWasShowing = preShowing;
                _lastEvidenceAt = now;
                _confirmed = false;      // 已经打了，"点了开战按钮"这个记忆不再需要
                _sawBattleLevel = true;  // 真的看见战斗位亮了（下面退出判据要用）
                return Enter(BattleFlowState.InBattle, "已经在打了", out why);
            }
            if (_state == BattleFlowState.InBattle)
            {
                _preWasShowing = preShowing;
                _confirmed = false;
                // ★ 边沿早于电平 ★ `IntoBattle` 的前缀跑在"战斗真的开起来"**之前**，
                //   所以进战中后紧接着的一两帧，`battleActive` 还是假的。这时**不能**判"战斗结束"，
                //   否则整个战中态只有一帧寿命（而战中态正是"不许再开一场"的唯一依据）。
                //   判据是"有没有**真的看见过**战斗位亮"，不是"等几秒" —— 所以仍是零秒数。
                //   真没打起来（引擎拒了、或只是预演）时分两种情况退出：
                //     · 从没见过电平 ⇒ 不猜，等兜底（下面那条，60 秒零证据）；
                //     · 见过电平又落了 ⇒ 打完了，立刻退出。
                if (!_sawBattleLevel)
                {
                    if (_lastEvidenceAt != DateTime.MinValue
                        && (now - _lastEvidenceAt).TotalSeconds > _fallbackSeconds)
                        return Exit("兜底：进战中后连着 " + _fallbackSeconds
                                    + " 秒没看见战斗位（那场仗没真打起来）", out why);
                    return false;
                }
                return Exit("战斗结束", out why);
            }
            if (preShowing)
            {
                _preWasShowing = true;
                _lastEvidenceAt = now;
                return Enter(BattleFlowState.PreBattle, "应战窗亮着", out why);
            }

            bool wasShowing = _preWasShowing;
            _preWasShowing = false;
            if (_state == BattleFlowState.PreBattle && wasShowing && !_confirmed)
                return Exit("应战窗关了但没进战斗", out why);

            if (_state == BattleFlowState.PreBattle && _lastEvidenceAt != DateTime.MinValue
                && (now - _lastEvidenceAt).TotalSeconds > _fallbackSeconds)
                return Exit("兜底：连着 " + _fallbackSeconds + " 秒一条证据都没有", out why);

            return false;
        }

        /// <summary>`IntoBattle` 命中（游戏真的要开打了）。与 `battleActive` 同一件事，
        /// 但**早于**电平翻转，且能拿到走了哪个重载（诊断用）。</summary>
        public bool NoteBattleStart(string source, DateTime now, out string why)
        {
            if (_state == BattleFlowState.InBattle) { why = source; return false; }
            _lastEvidenceAt = now;
            _confirmed = false;
            _sawBattleLevel = false;      // 边沿：还没看见战斗位亮（见 NoteSampled）
            return Enter(BattleFlowState.InBattle, "IntoBattle（" + source + "）", out why);
        }

        /// <summary>世界重载（读档/换档）：旧世界的一切证据都失效（学到的编号**保留**）。</summary>
        public void Reset()
        {
            _state = BattleFlowState.Idle;
            _confirmed = false;
            _preWasShowing = false;
            _lastEvidenceAt = DateTime.MinValue;
            _lastPageId = 0;
            _sawBattleLevel = false;
        }

        private bool Enter(BattleFlowState s, string reason, out string why)
        {
            why = reason;
            if (_state == s) return false;
            _state = s;
            return true;
        }

        private bool Exit(string reason, out string why)
        {
            why = reason;
            if (_state == BattleFlowState.Idle) return false;
            _state = BattleFlowState.Idle;
            _confirmed = false;
            return true;
        }

        // ------------------------------------------------------------------
        // 处置表（用户 10-25 拍板的粒度：只掐"会再开一场仗"的，其余排队或照常）
        // ------------------------------------------------------------------

        /// <summary>
        /// 六个动作工具（与 Python `tools/schemas.ACTION_TOOLS` 同集合）在战前/战中怎么处置。
        /// **只读工具不在这张表里**（它们压根不经过这道闸，见 `ToolExecutor.Execute`）。
        ///
        /// 为什么 attack/spar/teleport/summon 是 Reject 而不是 Queue：
        ///   "排队"的语义是"等会儿办"，而攻击/切磋的语义是"把玩家拖去打一场"——
        ///   仗都已经打了，等会儿补一刀就是**把已经发生的事再办一遍**（10-25 那条
        ///   18:51:22 入队、28.687 落刀的攻击正是这么来的）。传送/召唤同族：会在战斗流程上
        ///   把玩家挪走/弹原生窗，把它排到战斗之后同样没有意义。
        /// 其余（赠予/讨要/传功/邀约/论道/双修/行走）与战斗不冲突，只是此刻弹窗会沉进
        /// 系统队列不出队 ⇒ 排队是正确处置（`DeferredActionQueue` 的老本行）。
        /// </summary>
        public static ToolVerdict Grade(string toolName, string op)
        {
            if (toolName == "world_ai_action")
            {
                if (op == "attack" || op == "spar") return ToolVerdict.Reject;
                return ToolVerdict.Queue;
            }
            if (toolName == "movement")
            {
                if (op == "teleport" || op == "summon") return ToolVerdict.Reject;
                return ToolVerdict.Queue;      // travel：走到别处去，与战斗不撞
            }
            // 捎话（11-12）**不进队列**：它不碰游戏世界（不落刀、不动背包、不改好感），
            // 只是往另一个 NPC 的账本里追加一句话 —— 排到"战斗之后"没有任何意义，
            // 反而会把一句立刻该说的话拖到战后再投（时序错、还可能因链断而丢掉）。
            if (toolName == "social_relation" && op == "chuan_hua")
                return ToolVerdict.Pass;
            if (toolName == "economy_item" || toolName == "trade"
                || toolName == "item_acquire" || toolName == "social_relation")
                return ToolVerdict.Queue;
            return ToolVerdict.Pass;
        }

        /// <summary>
        /// 被拒时回给模型的那句话（用户 10-25 明确：**不要**主动塞"你现在正忙"的提示，
        /// 只在真被拒这一下说清楚）。必须说三件事：发生了什么 / 为什么这次不行 / 别再重试。
        /// 缺了最后一句，模型会反复重调（09-24 排队文案就是为这个写的）。
        /// </summary>
        public static string RejectText(string toolName, string op, BattleFlowState state)
        {
            string when = state == BattleFlowState.InBattle
                ? "你和他正在打"
                : "这一战马上就要开打（游戏那边已经在安排了）";
            if (toolName == "world_ai_action")
            {
                if (op == "attack")
                    return "（" + when + "，**不需要也不允许**再发起一次攻击 —— 这一次没有执行，也不会补做。别重复调用。）";
                if (op == "spar")
                    return "（" + when + "，这时候发起切磋会和它撞在一起 —— 这一次没有执行，也不会补做。等这一场过去再说。）";
            }
            if (toolName == "movement")
            {
                if (op == "teleport")
                    return "（" + when + "，现在把你挪过去会和这场战斗撞上 —— 这一次没有执行，也不会补做。）";
                if (op == "summon")
                    return "（" + when + "，现在把玩家召过来会和这场战斗撞上 —— 这一次没有执行，也不会补做。）";
            }
            return "（" + when + "，这个动作此刻不能执行。）";
        }
    }
}
