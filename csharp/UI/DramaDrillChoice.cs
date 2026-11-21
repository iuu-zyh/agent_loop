/// <summary>
/// 原生「切磋确认窗」的选项点击 → 定案 `world_ai_action(spar)` 的工具结果。
///
/// ## 为什么
/// 用户报："切磋弹剧情窗，同意/不同意两个选项，但**现在都是一样的**"。
/// 旧实现（`ActionTool.WorldAiAction` 的 `case "spar"`）是 `CreateAction` 完就
/// `return Ok(...)` —— 压根不看玩家选了什么，Python 侧又写死一句
/// 「已向你发起切磋，即将进入战斗/切磋界面」，于是**玩家婉拒了，NPC 还照着"双方已开始切磋"
/// 往下演**。同一 switch 里的邀约(`yao_yue`)/论道/双修/传功早就是"等玩家选完再回"，
/// **切磋是唯一漏掉的那个**。
///
/// ## 09-22 两条修正（都被真机日志实证，别再改回去）
///
/// **① 判据必须挂在 `ClickOption` 的 prefix 上，postfix 会输给兜底。**
/// 引擎在 `ClickOption` **内部同步**跑完选项效果：切磋就是在这里直接进战斗并触发
/// `UnitActionRoleDrill.OnEnd`（Player.log 4895→4899：`WorldBattleMgr:IntoBattle` →
/// `UnitActionRoleDrill:OnEnd()` → `[UnitAction] spar OnEnd 挂起命中 slot=2`）。
/// postfix 要等整个选项效果跑完才轮到 —— 那时槽位已被兜底收口、`req_id` 被 WsClient 摘走
/// （`WsClient.cs:373 if (reqId == null) return;`），于是**玩家明明应战了，模型拿到的却是
/// 「但你未作答复」**，NPC 按"你没应战"演下去（截图里那句"下回再堵我的路"就是这么来的）。
///
/// **② 09-14 那次拍板的前提是错的：`OnEnd` 不等整场战斗。**
/// 原注释写「`OnEnd` 在同意路径要等整场切磋打完才触发」，实测它在**进战斗那一刻**就触发。
/// 所以"选项一落定就回、不等战斗"这个决定，实际效果不是"早点回"而是"回得比兜底还晚"。
/// 现在应战路径的结局由 `BattleOutcome` 跨过战斗去收（含战后那两页的原话）。
///
/// ## 判据为什么是"选项"而不是 isDrillComplete
/// | 路径 | 判据 | 问题 |
/// |---|---|---|
/// | 动作结束（OnEnd） | `drill.isDrillComplete` | 哪个 bool = 同意**没有真机样本**。同类字段 `isInviteComplete` 当年把**接受**误报成拒绝，用户踩中 |
/// | **选项点击** | 配表行 id `212042`/`212041` | **配置表里的确定事实**，`DramaDialogue.json` id=21204 `options="212042\|212041"` 直接可查，无需样本 |
///
/// ## 归属（这一页是不是"我们那次切磋"的窗）
/// 门只有在我们挂起了一次 spar 之后才武装，命中即解除；`212041/212042` 这对选项在全表里
/// 只属于剧情 21204，而 21204 只在"NPC 向玩家提出切磋"时出现 ⇒ 门武装 + 选项命中 = 就是它。
/// 未武装时 `Observe` 第一行就 return ⇒ 对玩家自己开的任何剧情窗**零打扰**。
///
/// ## 兜底（玩家没选就关窗）
/// 玩家按 ESC / 点窗外关掉剧情窗时不会触发 `ClickOption`。此路走
/// `UnitActionHooks` 里的 `UnitActionRoleDrill.OnEnd` postfix → `UnitActionPending.Complete`
/// → `ToolExecutor` case "spar" 注册的那份 BuildResult → 如实报"没有明确答复"。
/// **刻意不读 `isDrillComplete`**——语义未校准就不猜（字段缺失/不可信 ≠ 否定）。
///
/// ## 铁律
/// 前缀必须吞异常且**恒 return true 放行**（同 `UnitActionHooks`）：抛出去或拦下来，都会顺着
/// 游戏自身的点击链出事 —— 前者炸链，后者玩家点不动选项。
/// </summary>
using System;
using HarmonyLib;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    /// <summary>Harmony **prefix** on `UIDramaBase.ClickOption(ConfDramaOptionsItem)`。
    ///
    /// 挂基类即可覆盖全部剧情窗变体：反编确认 `ClickOption` **只声明在 `UIDramaBase`**，
    /// `UIDramaDialogue`/`UIDramaBigTexture` 等子类均无覆写（源码 dump 里 0 次命中）。
    ///
    /// 与本文件不冲突的既有机制：玩家侧那枚「AI 对话」按钮是**克隆模板后整体换掉 onClick**
    /// （`DramaAiOption` 明写"清持久化监听"），点击它不会走 `ClickOption` ⇒ 不会误判成选边。</summary>
    [HarmonyPatch(typeof(UIDramaBase), "ClickOption")]
    internal static class DramaDrillChoiceHook
    {
        /// <summary>链外点击的日志额度（同 `FastKeyGate` 的"前几次逐条打，之后当金丝雀"套路）：
        /// 台账在飞时**每次都打**（那是采样现场），链外只打前 3 次 —— 证明这个钩子还活着，
        /// 又不把日志刷满（玩家平时翻剧情一直在点选项）。</summary>
        private static int _outsideChainLogLeft = 3;

        private static bool Prefix(UIDramaBase __instance, ConfDramaOptionsItem __0)
        {
            // ① 战斗结局台账：玩家点的这一枚选项 = "链在推进"的证据；命中 battleExit 位则是终局判据。
            //    放在 Observe **之前**：切磋的应战分支会在 Observe 里把台账武装起来（Arm），
            //    而这一击本身不该被算进新武装的那场链里（它是"应战"，不是链内的一步）。
            //    读 id 走 ClassifyOption 同一套 try/dynamic 双路（见下面 Report）。
            try
            {
                if (BattleOutcome.Active)
                    BattleOutcome.NoteOptionClicked(OptionIdOf(__0), Describe(__0));
                else if (_outsideChainLogLeft > 0)
                {
                    _outsideChainLogLeft--;
                    ModMain.P("[DrillChoice] 链外点击（探针，剩余额度 " + _outsideChainLogLeft + "）：" + Describe(__0));
                }
            }
            catch { }
            try { DrillChoiceGate.Observe(__instance, __0); } catch { }
            return true;     // 永不拦截：拦了玩家就点不动选项
        }

        /// <summary>选项行的配表 id（读不到返回 0）。与 `DrillChoiceGate.ClassifyOption` 同一套
        /// try + `dynamic` 双路 —— Unhollower 有的行暴露 `id`、有的只在运行时。</summary>
        private static int OptionIdOf(ConfDramaOptionsItem opt)
        {
            if (opt == null) return 0;
            try { return (int)opt.id; }
            catch { try { return (int)((dynamic)opt).id; } catch { return 0; } }
        }

        /// <summary>探针行：`id + function + text`。★`function` 可不可读**未验**★（
        /// `ConfDramaOptionsItem` 不在本仓任何 dump 里）—— 读不到就明写"读不到"，
        /// 判据本来只用 id 白名单，这一行只为攒校准样本；顺带把 `text` 也打出来，
        /// 将来若要把终局判据从 id 换成 `battleExit_*` 前缀，这里就是依据。</summary>
        private static string Describe(ConfDramaOptionsItem opt)
        {
            if (opt == null) return "选项=null";
            string fn = "?";
            try { fn = opt.function ?? ""; }
            catch { try { fn = (string)((dynamic)opt).function ?? ""; } catch { fn = "(读不到)"; } }
            string t = "";
            try { t = opt.text ?? ""; } catch { }
            if (t.Length > 24) t = t.Substring(0, 24) + "…";
            return "id=" + OptionIdOf(opt) + " function=" + fn + " text=" + t;
        }
    }

    /// <summary>切磋确认窗的"待定案"门（同一时刻最多一扇 —— `DramaGate` 本来就是单槽）。</summary>
    internal static class DrillChoiceGate
    {
        /// <summary>「好，就让我和你切磋一下」——`DramaDialogue` id=21204 的 options 左位。</summary>
        public const int AgreeOptionId = 212042;
        /// <summary>「我现在没有空」——同一行的右位（用户描述："左边同意、右边不同意"）。</summary>
        public const int RefuseOptionId = 212041;

        private static int _slot = -1;
        private static IntPtr _actionPtr = IntPtr.Zero;
        private static string _npc = "";

        /// <summary>登记一次待定的切磋。`actionPtr` = `UnitActionRoleDrill` 实例指针，
        /// 用它定位 `UnitActionPending` 的那条登记（应战 → 摘给 `BattleOutcome`；婉拒 → 撤掉）。</summary>
        public static void Arm(int slot, IntPtr actionPtr, string npcName)
        {
            _slot = slot;
            _actionPtr = actionPtr;
            _npc = npcName ?? "";
            try { ModMain.P("[DrillChoice] 已武装：等玩家在原生剧情窗里选（slot=" + slot + " npc=" + _npc + "）"); } catch { }
        }

        /// <summary>发起失败时收回（回滚用）。只认自己的 slot，防串台。</summary>
        public static void Disarm(int slot)
        {
            if (_slot != slot) return;
            Reset();
        }

        private static void Reset()
        {
            _slot = -1;
            _actionPtr = IntPtr.Zero;
            _npc = "";
        }

        /// <summary>`ClickOption` **prefix** 调用。命中即定案 + 解除武装；未命中/未武装一律零打扰。
        /// </summary>
        public static void Observe(UIDramaBase ui, ConfDramaOptionsItem opt)
        {
            int slot = _slot;
            if (slot < 0) return;                       // 没有待定切磋：玩家自己的剧情窗，不碰
            int verdict = ClassifyOption(opt);
            if (verdict == 0) return;                   // 不是这一对选项

            string npc = _npc;
            IntPtr ptr = _actionPtr;
            Reset();                                    // 先解除武装：Resolve 会回调进本类，别重入

            bool agree = verdict > 0;
            try { UnityEngine.Debug.Log("[DrillChoice] 玩家选了「" + (agree ? "应战" : "婉拒")
                                        + "」npc=" + npc + " slot=" + slot); } catch { }

            if (agree)
            {
                // 应战 = 结局要跨过一整场战斗。把登记**摘走**（不是撤掉）交给 BattleOutcome：
                //   · Take 不吊销归因凭证 → 战后那两页会被闸 1.5 认领，不再长出没用的「对话」按钮
                //     （实证 5076-5084：点了那颗按钮只会得到「世界界面尚未就绪」）；
                //   · 摘走之后，紧跟着在进战斗那一刻触发的 OnEnd 兜底就找不到条目，
                //     不会再把「未作答复」当成结局发回 Python。
                var e = UnitActionPending.Take(ptr);
                if (e == null)
                {
                    // 理论上不该再发生（这正是 09-22 那个竞态的现场）。槽位多半已被兜底收口，
                    // 这里补 Resolve 也只会撞上"req_id 已摘走"→ 只留痕，不假装能救回来。
                    try { ModMain.P("[DrillChoice] ★登记已被 OnEnd 抢先收走 → 这次切磋只能按'没看到结局'收口 slot=" + slot); } catch { }
                    return;
                }
                BattleOutcome.Arm(slot, npc, e.UnitID, "spar");
                return;
            }

            // 婉拒 = 没有战斗，立刻收口。
            // ★顺序：先取台账再撤登记★ —— `UnitActionPending.Remove` 会一并解武装台账（见那里注释），
            //   顺序反了这场交互的原文（21204 提问页）就进不了结果。
            //   注：玩家点「婉拒」的那一下**进不了结果** —— 游戏点链里 `ClickOption`（本闸）同步
            //   收口/补发 response，早于我们追加在按钮尾部的监听；选择文本在段里有（点对话可见）。
            var sparData = new JObject
            {
                ["op"] = "spar",
                ["target"] = npc,
                ["accepted"] = false,
                ["answered"] = true,
            };
            // ★09-23 起内容在段里★：婉拒也把这一场的原文/选择带上 —— 读在**吊销凭证之前**
            //（锚点 = 凭证首次登记时刻，早于 21204 提问页 ⇒ 提问页读得到）。
            // 玩家点「婉拒」的那一下**进不了本结果**（本方法挂在 `ClickOption` 前缀，早于我们追加在
            // 按钮尾部的监听，这里已经 Resolve）；选择文本在段里，点「对话」可见。
            try
            {
                var pe = ptr != IntPtr.Zero ? UnitActionPending.Take(ptr) : null;
                string puid = pe != null ? pe.UnitID : "";
                if (!string.IsNullOrEmpty(puid))
                {
                    System.Collections.Generic.List<SegmentLine> range;
                    if (DramaSegmentRecorder.TryReadRangeFromChain(puid, 0f, true, out range))
                    {
                        string pn = DramaSegmentRecorder.PlayerDisplayName();
                        sparData["lines"] = DramaSegmentRecorder.LinesToJson(range, pn);
                        if (!string.IsNullOrEmpty(pn)) sparData["player"] = pn;
                    }
                    DramaNativeClaim.Release(puid);   // 读完了，凭证照旧吊销
                }
            }
            catch (Exception re) { try { ModMain.P("[DrillChoice] 婉拒路径读段失败: " + re.Message); } catch { } }
            try { if (ptr != IntPtr.Zero) UnitActionPending.Remove(ptr); } catch { }
            // ★外层必须是 `Ok(...)` 那个形状（{success, data}）：`WsClient.OnDramaResolved` 按
            //   `result["success"]` 判成败，`text_render` 按 `res["data"]` 取字段。直接递裸 data
            //   会让 Python 收到 `{success:false, error:"未知错误"}` —— 婉拒又变回"说不通的一句"，
            //   正是本次要根治的那个症状（应战路径由 `BattleOutcome.Resolve` 包，形状本来就对）。
            DramaGate.Resolve(slot, new JObject { ["success"] = true, ["data"] = sparData });
        }

        /// <summary>选项 → 立场：+1 应战 / −1 婉拒 / 0 不是这一对。
        ///
        /// ① 先试配表行 id（`ConfBaseItem.id`，最权威）；
        /// ② 退 `text`——配表里存的是**本地化键** `drama_option212042`，但 UI 有可能已解析成
        ///    成品句，两种形态都认，免得依赖"哪一层做 LS"这个没核实的细节。
        ///
        /// 读 `.id` 用 try + `dynamic` 双路，是本仓库读 conf 行 id 的既有写法
        /// （`UnitSnapshot` 读 `luckData.id` 同款）——Unhollower 有的行暴露 `id`、有的只在运行时。</summary>
        private static int ClassifyOption(ConfDramaOptionsItem opt)
        {
            if (opt == null) return 0;
            int id = 0;
            try { id = (int)opt.id; }
            catch { try { id = (int)((dynamic)opt).id; } catch { id = 0; } }
            if (id == AgreeOptionId) return 1;
            if (id == RefuseOptionId) return -1;

            string t = "";
            try { t = opt.text ?? ""; } catch { }
            if (t.Length == 0) return 0;
            if (t.IndexOf("212042", StringComparison.Ordinal) >= 0
                || t.IndexOf("就让我和你切磋", StringComparison.Ordinal) >= 0) return 1;
            if (t.IndexOf("212041", StringComparison.Ordinal) >= 0
                || t.IndexOf("我现在没有空", StringComparison.Ordinal) >= 0) return -1;
            return 0;
        }
    }
}
