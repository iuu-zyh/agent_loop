using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;

namespace AgentLoopBridge
{
    /// <summary>
    /// 击杀统计 —— 按单位名字聚合「本场杀了什么、各多少只」。
    ///
    /// ══════════════════════════════════════════════════════════════════════
    ///  为什么只认 `UnitCtrlBase.Die` 的 Postfix（反汇编 + 真机双重定案）
    /// ══════════════════════════════════════════════════════════════════════
    ///
    /// ## 两个容易混淆的入口
    ///
    /// ```text
    /// BattleUnitMgr.OnUnitDie  @0x2B064C0
    ///   182b064c0: xor r8d,r8d
    ///   182b064c3: jmp 0x182b05020        ← 转发到 BattleUnitMgr::DelUnitCache
    /// ```
    /// **名字叫 OnUnitDie，实际是「把单位从战场缓存里删掉」。**
    /// `DelUnitCache` 全程**不检查 `isDie`** —— 因为「离开缓存」不等于「死亡」：
    /// 撤退、换场、战斗结束清场都会走它。
    ///
    /// `UnitCtrlBase.Die` @0x2CB1DB0 才是真死，关键顺序：
    /// ```text
    ///   182cb1e68: cmp BYTE PTR [rbx+0x10],0x0   ; 幂等检查 mIsDie
    ///   182cb1e7b: jne 0x182cb2317               ; 已死 → 直接返回
    ///   182cb1f99: call UnitDie::.ctor           ; 构造死亡事件
    ///   182cb1fb3: call EventsMgr::Emit          ; 广播
    ///   182cb1fbf: mov BYTE PTR [rbx+0x10],0x1   ; ★ 写 mIsDie = 1 ★
    /// ```
    /// `isDie` 属性读的就是 `+0x10` 这个字段。
    ///
    /// ⇒ **`Die` 的 Postfix 里 `isDie` 必然为 True**，天然排除「只是离开」的单位。
    /// 且 `Die` 开头有幂等检查 ⇒ **同一只怪不会统计两次**，不需要额外去重。
    ///
    /// ## 真机数据（11:21 一局，151 个事件配对 75 组）
    ///
    /// ```text
    /// [isDie] OnUnitDie ... name="竹妖" isDie=False   ← 先，离开缓存
    /// [isDie] Die       ... name="竹妖" isDie=True    ← 后，真死
    /// ```
    /// **每只小怪都成对，无一例外，且 `Die` 时 `isDie` 恒为 True。**
    /// 这就是本类只挂 `Die` 的依据。
    /// ══════════════════════════════════════════════════════════════════════
    /// </summary>
    internal static class BattleKillStats
    {
        private const string Tag = "[KillStats]";

        // ══════════════════════════════════════════════════════════════════════
        //  ★ 生命周期契约（10-21 显式化，配合 TeamContainerInject 顶部那张总表读）★
        // ══════════════════════════════════════════════════════════════════════
        //
        //  **累积**：`Die` 钩子（`OnUnitDie`）逐只累加，全程不清。
        //  **输出**：`LogReport` 在 `OnBattleExit` **最开头**（早于任何可能 return 的分支）。
        //  **清零**：`OnBattleExit` 的 finally（`Reset`）。
        //
        //  ★ 铁律：**不许在 `BattleEndFront` 开头清零。** ★
        //    那里清 = 本场战果还没报就被抹掉，日志永远「本场无击杀记录」——
        //    真机踩过：明明打了竹妖（`[death] Die ... isDie=True`），却报无击杀。
        //    `ResetIfReported` 是给"中途退出副本/读档导致 BattleExit 没触发"的残留兜底，
        //    它只清**已经输出过**的（`_reported == true`），不会误伤正在累积的这一场。
        //
        // | 字段 | 清于 | 为什么 |
        // |---|---|---|
        // | `Enabled`  | 从不清（配置开关） | 临时静音用 |
        // | `_kills`   | `Reset`          | 本场战果，报完即弃 |
        // | `_counted` | `Reset`          | 对象引用去重表，跨场无意义且会泄漏 |
        // | `_reported`| `Reset` 置 false | 标记"本场已输出过"，供 `ResetIfReported` 判残留 |
        // ══════════════════════════════════════════════════════════════════════

        /// <summary>统计开关。关掉时钩子仍挂，但入口直接返回（便于临时静音）。不参与战斗生命周期。</summary>
        internal static bool Enabled = true;

        /// <summary>本场击杀：名字 → 只数。<b>清于 `Reset`（OnBattleExit 的 finally）。</b></summary>
        private static readonly Dictionary<string, int> _kills = new Dictionary<string, int>();

        /// <summary>已统计过的单位（按对象引用去重，防 `Die` 被异常重入）。<b>清于 `Reset`。</b></summary>
        private static readonly List<object> _counted = new List<object>();

        /// <summary>本场是否已经有击杀（用于决定要不要写"空手而归"）。</summary>
        internal static bool HasAny { get { return _kills.Count > 0; } }

        internal static int TotalKills
        {
            get
            {
                int n = 0;
                foreach (var kv in _kills) n += kv.Value;
                return n;
            }
        }

        /// <summary>
        /// 清空本场统计。
        ///
        /// 调用时机（两处，缺一不可）：
        ///   · `TeamBattleFollowup.OnBattleExit` 的 finally —— 正常收口；
        ///   · `TeamContainerInject.ResetForBattle`（BattleEndFront 开头）—— 
        ///     **只清"上一场已输出过"的残留**，见 `_reported` 的注释。
        /// </summary>
        internal static void Reset()
        {
            _kills.Clear();
            _counted.Clear();
            _reported = false;
        }

        /// <summary>
        /// 只清"上一场已输出过"的残留（用于中途退副本/读档导致 BattleExit 没触发的场景）。
        ///
        /// 为什么不能无条件 Reset：本方法由 `ResetForBattle` 在 `BattleEndFront`
        /// **开头**调用，而那时本场统计刚累积完、还没输出（`_reported=false`）——
        /// 无条件清会把本场战果抹掉（这正是 10-21 真机"本场无击杀记录"的根因）。
        /// </summary>
        internal static void ResetIfReported()
        {
            if (!_reported) return;
            Reset();
        }

        /// <summary>本场战报是否已经输出过（防止同一场重复打日志）。</summary>
        private static bool _reported;


        /// <summary>
        /// `UnitCtrlBase.Die` 的 Postfix 入口。
        ///
        /// 判据顺序（每一步都必须，缺一会算错）：
        ///   ① 非 null、`data` 非 null —— 真机存在 `data=null` 的人形单位
        ///   ② `isDie == true` —— 确认真死（理论上必为真，作为自证）
        ///   ③ **不是队友** —— 队友倒地不算击杀
        ///   ④ 名字非空 —— 真机存在 `name=""` 的死亡
        ///   ⑤ 按对象引用去重 —— 双保险
        /// </summary>
        internal static void OnUnitDie(object unit)
        {
            if (!Enabled) return;
            try
            {
                if (unit == null) return;

                object data = Refl.Get(unit, "data");
                if (data == null) return;                       // ① data=null（真机存在）

                // ② 确认真死
                bool die = false;
                die = (bool)(Refl.Get(unit, "isDie") ?? false);
                if (!die) return;

                // ③ **必须处于战斗中**（10-21 自查补上，这是个真 bug）
                //
                //   `UnitCtrlBase.Die` 是**全局**钩子，不区分场景。
                //   不加这道闸的话，玩家在大地图上杀 NPC / 野兽也会被算进
                //   "本场副本击杀"，导致战报出现与副本无关的名字。
                //   判据用引擎自己的 `g.world.battle.isBattle`（与 `pair`/`auto` 同源）。
                bool inBattle = false;
                try { var wb = g.world?.battle; inBattle = (wb != null && wb.isBattle); } catch { }
                if (!inBattle) return;

                // ④ 队友排除（队友倒下有自己的重伤流程，不算击杀）
                if (TeamContainerInject.IsTeammateBattleUnit(unit)) return;

                // ⑤ 名字
                string nm = (Refl.Get(data, "name") as string) ?? "";
                nm = nm.Trim();
                if (nm.Length == 0) nm = "未知单位";

                // ⑥ 引用去重
                foreach (var x in _counted)
                    if (ReferenceEquals(x, unit)) return;
                _counted.Add(unit);

                int c;
                _kills.TryGetValue(nm, out c);
                _kills[nm] = c + 1;
            }
            catch (Exception e)
            {
                ModMain.P(Tag + " 统计异常: " + e.GetType().Name + " " + e.Message);
            }
        }

        /// <summary>
        /// 生成一行战报，例如：`竹妖 ×12、蛇 ×3`。
        /// 无击杀时返回空串（调用方据此决定是否写进经历）。
        /// </summary>
        internal static string SummaryLine()
        {
            if (_kills.Count == 0) return "";
            var list = _kills.ToList();
            list.Sort((a, b) =>
            {
                int d = b.Value.CompareTo(a.Value);             // 数量降序
                return d != 0 ? d : string.CompareOrdinal(a.Key, b.Key);
            });
            var sb = new StringBuilder();
            foreach (var kv in list)
            {
                if (sb.Length > 0) sb.Append("、");
                sb.Append(kv.Key).Append(" ×").Append(kv.Value);
            }
            return sb.ToString();
        }

        /// <summary>
        /// 写进「经历」文本的句子（沉浸感用）。
        /// 例：`斩杀竹妖 12 只、蛇 3 只。`
        /// </summary>
        internal static string LogSentence()
        {
            if (_kills.Count == 0) return "";
            var list = _kills.ToList();
            list.Sort((a, b) =>
            {
                int d = b.Value.CompareTo(a.Value);
                return d != 0 ? d : string.CompareOrdinal(a.Key, b.Key);
            });
            var sb = new StringBuilder("斩杀");
            foreach (var kv in list)
            {
                if (sb.Length > 3) sb.Append("、");
                sb.Append(kv.Key).Append(' ').Append(kv.Value).Append(" 只");
            }
            sb.Append('。');
            return sb.ToString();
        }

        /// <summary>本场战报日志（战斗结束时打一行）。</summary>
        /// <summary>
        /// 输出本场战报，**输出后自行清零**。
        ///
        /// ## 为什么清零必须在这里（10-21 真机踩过）
        ///
        /// 原先把清零放在 `TeamContainerInject.ResetForBattle()`，而那个方法在
        /// `BattleEndFront` **开头**执行；本方法却在同一次 BattleEndFront 的**末尾**
        /// （BattleExit 处理链）才被调用 —— 于是刚统计好的战果被提前抹掉，
        /// 日志永远「本场无击杀记录」。
        ///
        /// 真机症状：明明打了 `竹妖`（`[death] Die ... isDie=True` 有记录），
        /// 却打 `[KillStats] 本场无击杀记录`。
        ///
        /// ⇒ 顺序固定为「统计 → 输出 → 清零」，全部收在本方法内，不依赖外部时机。
        /// </summary>
        internal static void LogReport()
        {
            try
            {
                if (_kills.Count == 0)
                    ModMain.P(Tag + " 本场无击杀记录");
                else
                    ModMain.P(Tag + " 本场击杀 " + TotalKills + " 只：" + SummaryLine());
            }
            catch { }
            finally
            {
                _reported = true;
                // ⚠ 必须在**读完战报之后**再清。`WriteJourneyLogs` 还会调
                //   `SummaryLine()`/`LogSentence()` 取文案，所以真正的清零点
                //   由调用方 `OnBattleExit` 在写完经历后调 `Reset()` —— 见那里注释。
            }
        }
    }
}
