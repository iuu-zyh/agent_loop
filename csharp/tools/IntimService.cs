/// <summary>
/// IntimService —— 好感写入层：方向/换算/步长/回读全在这里，**全仓唯一写入口**。
/// （11-19 由 6711 行的 `ToolExecutor.cs` 拆出，11-20 按 Python 的域归并；成员逐字搬移。）
/// 跨类调用一律 `<类名>.<方法>`；`internal` = 仅本程序集可见。
/// </summary>
using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using static AgentLoopBridge.ToolResult;

namespace AgentLoopBridge
{
    internal static class IntimService
    {
        /// <summary>取某人对某人的好感（读不到给 -1 = 未知，绝不冒充 0）。</summary>
        internal static int IntimBetween(WorldUnitBase a, WorldUnitBase b)
        {
            try { return (int)a.data.unitData.relationData.GetIntim(b); } catch { return -1; }
        }

        // ============ 好感写入层（10-12 建；**10-15 改口径：引擎换算、我们只回读**）============
        //
        // 游戏侧事实（本层全部常量的出处，改前先读）：
        //   · 面板刻度：半心=30、一星=60、满值=300（社区 2021–2024 多源实测一致）。
        //   · **「衰减」的精确机制 = `RoleClose.json` 的百分比系数表**（10-15 从官方教程自带的
        //     原版配表读出，本机 `…\鬼谷八荒\Mod\modFQA\配置修改教程\配置（只读）Json格式\RoleClose.json`）：
        //       按**当前好感**分档取系数（百分数，100=不衰减）——
        //         0~60 → 100% ｜ 60~120 → 50% ｜ 120~180 → 25% ｜ 180~240 → 12.5% ｜ 240~300 → 6.25%
        //         负侧同构：-60~0 → 100% ｜ -120~-60 → 50% ｜ -180~-120 → 25% ｜ -240~-180 → 12.5% ｜ -300 及以下 → 6.25%
        //       即"好感越高，同一交互加得越少"，且**减好感同样按此衰减**（closeReduce/hateIncrease/hateReduce 同表）。
        //       例：当前好感 150 时 `AddIntim(+30)` 实得 ≈ 30×25% = 7.5（面板只动 7~8）——这就是
        //       用户 10-12 报的"传参与实得不匹配"的量化解释。
        //   · **写入语义（10-15 用户定案）**：`AddIntim/AddHate` **只传"请求值"，换算由引擎决定**。
        //     我们**不做任何校正/补差**（10-12 版的 SetIntim 校正与"补差"已按用户意见删除），
        //     只**回读实得**并如实写进结果（渲染层据此说明"存在衰减"，见 `render_social`）。
        //     理由：游戏本身就是"输入一个值、按系数自动换算"；直写工具不该私自绕过引擎的规则。
        //   · API（GGBH_API.chm → DataUnit.RelationData，Mono.Cecil 枚举实证）：
        //       AddIntim(string unitID, float value, int clampNewIntim=0, string uiTip="", bool isTip=true)
        //       AddHate (string unitID, float value, ...)   SetIntim(string unitID, float value)
        //       GetIntim(string|WorldUnitBase) → int        ClearIntim(string unitID)
        //     （`SetIntim` 仍在接口里，但本项目**不再使用** —— 保留这一行只为将来要"写绝对值"时知道它在。）
        //   · 交互挂好感的配表（ConfMgr 属性 → 键，枚举实证；括号内数值＝官方教程自带的原版配表原值，10-15 读出）：
        //       roleGive   .closeParaA=15（每单基准）· .closeOnceAddMax=60（单次上限）
        //       roleAskfor .closeParaA=+8（讨要达成）· .closeParaB=-8（交付方对索取方）·
        //                  .closeParaC=-25（被拒方）· .closeAddMax=120 / .closeReduceMax=30
        //       roleChat   .closeParaB=15（闲聊成功/失败）· .closeParaA=100 · .hateParaC=-100
        //       roleDrill  .addCloseMin/Max=5/10（切磋）· roleTrains .closeAddMin/Max=10/20（双修）
        //       roleInvite .intimateDecline=-120（爽约）· roleStealItem .exposeReduceClosePara=100（被发现）
        //       roleSave   .addClose=200（救命）· roleKill .hateAddKill=500 · roleViewItem .unlockCloseParaA=10
        //       （**交易无原生钩子**——ConfRole* 全枚举无 Trade 类；`roleTeachSkill` 也没有 close 键。）
        //       凡走原生动作的路径本层只做探针、不重复落刀——见各处 [Intim] 行。
        //   · 方向（社区共识 + 本项目 09-17 探针结论）：**收下东西的一方对给出东西的一方 +** ——
        //       NPC 送玩家礼 ⇒ 玩家→NPC +；玩家应了 NPC 的讨要 ⇒ NPC→玩家 +（面板可见这条）。

        /// <summary>好感「一次交互」的基准步长：配表两处基准值都是 15（RoleGive.closeParaA / RoleChat.closeParaB）。</summary>
        internal const int IntimStepDefault = 15;

        /// <summary>直写工具（add/reduce_intim）单次上限：两星 = 120（够"重大事件"级，不越过一次满心）。</summary>
        internal const int IntimStepMax = 120;

        /// <summary>论道（本项目规则，游戏无配表）：完成后双方各 +10（同样只传"请求值"，实得由引擎换算）。</summary>
        internal const int LunDaoIntimEach = 10;

        /// <summary>有价成交（本项目规则，游戏无钩子）：买卖双方各 +5（同上）。</summary>
        internal const int TradeIntimEach = 5;

        /// <summary>读配表整数值：≤0（未配/读取失败）回退 fallback。</summary>
        internal static int ConfIntim(int value, int fallback)
        {
            return value > 0 ? value : fallback;
        }

        /// <summary>赠礼一次的好感加值：`RoleGive.closeParaA`（回退 15），`closeOnceAddMax` 封顶（若配了）。</summary>
        internal static int GiftIntimValue()
        {
            int v = IntimStepDefault;
            try { v = ConfIntim(g.conf.roleGive.closeParaA, IntimStepDefault); } catch { }
            try
            {
                int cap = g.conf.roleGive.closeOnceAddMax;
                if (cap > 0 && v > cap) v = cap;
            }
            catch { }
            return v;
        }

        /// <summary>讨要达成的好感加值：`RoleAskfor.closeParaA`（回退 15），`closeAddMax` 封顶（若配了）。</summary>
        internal static int AskforIntimValue()
        {
            int v = IntimStepDefault;
            try { v = ConfIntim(g.conf.roleAskfor.closeParaA, IntimStepDefault); } catch { }
            try
            {
                int cap = g.conf.roleAskfor.closeAddMax;
                if (cap > 0 && v > cap) v = cap;
            }
            catch { }
            return v;
        }

        /// <summary>
        /// 讨要**被应允**时交付方的反向减项：`RoleAskfor.closeParaB`（原值 -8，取绝对值；回退 8）。
        /// 配表里 closeParaA=+8 / closeParaB=-8 是一对 —— 索取方 +8、**交付方 -8**（"被讨要"的不痛快）。
        /// </summary>
        internal static int AskforIntimReduceValue()
        {
            int v = 8;
            try { v = ConfIntim(-g.conf.roleAskfor.closeParaB, 8); } catch { }
            return v;
        }

        /// <summary>
        /// 好感写入：**只走原生 `AddIntim/AddHate`，不做任何校正**（2026-10-15 定案）——
        /// 引擎按 `RoleClose` 系数自动换算（当前好感越高，同一请求实得越少），我们只回读并如实报出。
        /// 返回实得 Δ（起点读不到返回 int.MinValue）；每笔留 `[Intim]` 探针行（tag 标明来路）。
        /// </summary>
        // internal（不是 private）：`TeamTool` 解散队伍时要扣好感，那是**同一套写入语义**
        // （只传请求值、引擎按 RoleClose 换算、我们只回读实得），不该在组队侧再实现一遍。
        internal static int ApplyIntim(WorldUnitBase holder, WorldUnitBase other, int delta, string tag)
        {
            if (holder == null || other == null || delta == 0) return 0;
            string oid = "";
            try { oid = (string)other.data.unitData.unitID; } catch { }
            if (string.IsNullOrEmpty(oid)) return int.MinValue;
            int before = IntimBetween(holder, other);
            if (before < 0) return int.MinValue;
            var rel = holder.data.unitData.relationData;
            try
            {
                if (delta > 0) rel.AddIntim(oid, delta, 0, "", true);
                else rel.AddHate(oid, -delta, 0, "", true);
            }
            catch (Exception e)
            {
                try { ModMain.P("[Intim] " + tag + " 原生写入异常: " + e.Message); } catch { }
                return int.MinValue;
            }
            int after = IntimBetween(holder, other);
            int actual = after - before;
            try
            {
                ModMain.P("[Intim] " + tag + " 请求" + (delta > 0 ? "+" : "") + delta + " 实得" + actual
                          + "（" + before + "→" + after + "）"
                          + (actual != delta ? "｜引擎按 RoleClose 系数换算（好感越高实得越少）" : ""));
            }
            catch { }
            return actual;
        }

        /// <summary>
        /// 把写入层的实得 Δ 放进结果字段：**读不到起点（`int.MinValue`）时字段缺失**。
        /// 口径与 trade 的余额同源（"读得到才写；读不到不报数字"）——若直放，JSON 里会出现
        /// -2147483648 这种垃圾大数，渲染/模型/日志都会把它当真值读。缺失=未知，渲染层容忍缺失。
        /// </summary>
        internal static void PutIntim(JObject o, string key, int delta)
        {
            if (o != null && key != null && delta != int.MinValue) o[key] = delta;
        }

        /// <summary>
        /// 把**写入后的真实好感**（回读值）放进结果：`{key}` = Δ、`{key}_after` = 回读真值。
        ///
        /// ★ 11-13 为什么必须成对给 ★（用户原话："传值减 120、回读掉 30 也没问题，
        /// 总之告诉模型存在衰减机制，然后工具返回结果注意返回回读之后的真实好感度就行"）：
        ///   · 只给 Δ，模型只知道"这一步动了多少"，**不知道现在总共多少**；
        ///     而它下一步（要不要继续加、这句台词该怎么定性）恰恰取决于当前值落在哪一档。
        ///   · `_intim_decay_note`（Python 渲染层）会解释"为什么 120 只掉 30"，
        ///     但它需要 `requested`/`actual` 两个数；**当前值只有 C# 读得到**。
        /// 与 `PutIntim` 同口径：起点/终点读不到（`int.MinValue`）就**不写这个键**，宁缺不假。
        /// </summary>
        internal static void PutIntimWithAfter(JObject o, string key, int delta, WorldUnitBase holder, WorldUnitBase other)
        {
            if (o == null || key == null) return;
            PutIntim(o, key, delta);
            int after = IntimBetween(holder, other);
            if (after >= 0) o[key + "_after"] = after;
        }

        /// <summary>赠礼一次的好感副作用：**收礼方对送礼方** +（配表值）。返回实得 Δ。</summary>
        internal static int ApplyGiftIntim(WorldUnitBase receiver, WorldUnitBase giver, string tag)
        {
            return ApplyIntim(receiver, giver, GiftIntimValue(), "赠礼·" + tag);
        }

        /// <summary>讨要达成的好感副作用：**取得方对交付方** +（配表 `RoleAskfor.closeParaA`）。返回实得 Δ。</summary>
        internal static int ApplyAskforIntim(WorldUnitBase taker, WorldUnitBase holder, string tag)
        {
            return ApplyIntim(taker, holder, AskforIntimValue(), "讨要·" + tag);
        }

        /// <summary>
        /// 讨要达成的**反向**副作用：**交付方对取得方** -（配表 `RoleAskfor.closeParaB`，原值 -8）。
        /// 与 `ApplyAskforIntim` 成对使用 —— 原生讨要窗两向都扣/加，我方自制窗（灵石/多件）照此复刻。
        /// </summary>
        internal static int ApplyAskforUpset(WorldUnitBase holder, WorldUnitBase taker, string tag)
        {
            return ApplyIntim(holder, taker, -AskforIntimReduceValue(), "讨要·交付方不满·" + tag);
        }

        /// <summary>
        /// 论道的好感（本项目规则，`ConfRole*` 无 lun_dao 键）：完成后**双方各 +`LunDaoIntimEach`**。
        /// 只传"请求值"，实得（含衰减）由引擎决定；两个方向各回读一次，交给结果透出。
        /// </summary>
        internal static void ApplyLunDaoIntim(WorldUnitBase npc, out int npcToPlayer, out int playerToNpc)
        {
            npcToPlayer = int.MinValue;
            playerToNpc = int.MinValue;
            var player = g.world.playerUnit;
            if (player == null || npc == null) return;
            npcToPlayer = ApplyIntim(npc, player, LunDaoIntimEach, "论道·NPC对玩家");
            playerToNpc = ApplyIntim(player, npc, LunDaoIntimEach, "论道·玩家对NPC");
        }
    }
}
