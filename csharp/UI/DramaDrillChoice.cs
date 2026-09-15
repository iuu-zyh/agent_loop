/// <summary>
/// 原生「切磋确认窗」的选项点击 → **立即**定案 world_ai_action(spar) 的工具结果。
///
/// ## 为什么
/// 用户报："切磋弹剧情窗，同意/不同意两个选项，但**现在都是一样的**"。
/// 旧实现（`ToolExecutor.WorldAiAction` 的 `case "spar"`）是 `CreateAction` 完就
/// `return Ok(...)` —— 压根不看玩家选了什么，Python 侧又写死一句
/// 「已向你发起切磋，即将进入战斗/切磋界面」，于是**玩家婉拒了，NPC 还照着"双方已开始切磋"
/// 往下演**。同一 switch 里的邀约(`yao_yue`)/论道/双修/传功早就是"等玩家选完再回"，
/// **切磋是唯一漏掉的那个**（反编 `unit_action_sigs.txt`：`UnitActionRoleDrill` 与
/// `UnitActionRoleInvite`/`TeachSkill` 同构，都有 `OnEnd()` 与落定字段 `isDrillComplete`）。
///
/// ## 判据为什么是"选项"而不是 isDrillComplete
/// 两条路都能拿到结局，但**只有选项 id 是免校准的**：
///
/// | 路径 | 判据 | 问题 |
/// |---|---|---|
/// | 动作结束（OnEnd） | `drill.isDrillComplete` | 哪个 bool = 同意**没有真机样本**。同类字段 `isInviteComplete` 当年就猜错过：旧判定 `!accepted \|\| npcUpset` 把**接受**路径误报成拒绝，用户踩中 |
/// | **选项点击** | 配表行 id `212042`/`212041` | **配置表里的确定事实**，`DramaDialogue.json` id=21204 `options="212042\|212041"` 直接可查，无需样本 |
///
/// 用户还拍板了"**选项一落定就回，不等战斗**"——`OnEnd` 在同意路径要等整场切磋打完才触发，
/// 满足不了；`ClickOption` 正好是选项落定的那一刻。所以主判据走选项，`OnEnd` 只当兜底。
///
/// ## 归属（这一页是不是"我们那次切磋"的窗）
/// 门只有在我们挂起了一次 spar 之后才武装，命中即解除；`212041/212042` 这对选项在全表里
/// 只属于剧情 21204，而 21204 只在"NPC 向玩家提出切磋"时出现 ⇒ 门武装 + 选项命中 = 就是它。
/// 未武装时 `Observe` 第一行就 return ⇒ 对玩家自己开的任何剧情窗**零打扰**。
///
/// ## 兜底（玩家没选就关窗）
/// 玩家按 ESC / 点窗外关掉剧情窗时不会触发 `ClickOption`。此路走
/// `UnitActionHooks` 里新加的 `UnitActionRoleDrill.OnEnd` postfix → `UnitActionPending.Complete`
/// → 本文件 `case "spar"` 组的那份 BuildResult → 如实报"没有明确答复"。
/// **刻意不读 `isDrillComplete`**——语义未校准就不猜（字段缺失/不可信 ≠ 否定）。
///
/// ## 铁律
/// Postfix 必须吞异常（同 `UnitActionHooks`）：抛出去会顺着游戏自身的点击链往上炸。
/// </summary>
using System;
using HarmonyLib;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    /// <summary>Harmony postfix on `UIDramaBase.ClickOption(ConfDramaOptionsItem)`。
    ///
    /// 挂基类即可覆盖全部剧情窗变体：反编确认 `ClickOption` **只声明在 `UIDramaBase`**，
    /// `UIDramaDialogue`/`UIDramaBigTexture` 等子类均无覆写（源码 dump 里 0 次命中）。
    ///
    /// 与本文件不冲突的既有机制：玩家侧那枚「AI 对话」按钮是**克隆模板后整体换掉 onClick**
    /// （`DramaAiOption` 明写"清持久化监听"），点击它不会走 `ClickOption` ⇒ 不会误判成选边。</summary>
    [HarmonyPatch(typeof(UIDramaBase), "ClickOption")]
    internal static class DramaDrillChoiceHook
    {
        private static void Postfix(UIDramaBase __instance, ConfDramaOptionsItem __0)
        {
            try { DrillChoiceGate.Observe(__instance, __0); } catch { }
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
        /// 命中时用它撤掉 `UnitActionPending` 的登记（否则动作真结束时 OnEnd 兜底会再 Resolve 一次）。</summary>
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

        /// <summary>`ClickOption` postfix 调用。命中即定案 + 解除武装；未命中/未武装一律零打扰。</summary>
        public static void Observe(UIDramaBase ui, ConfDramaOptionsItem opt)
        {
            int slot = _slot;
            if (slot < 0) return;                       // 没有待定切磋：玩家自己的剧情窗，不碰
            int verdict = ClassifyOption(opt);
            if (verdict == 0) return;                   // 不是这一对选项

            string npc = _npc;
            IntPtr ptr = _actionPtr;
            Reset();                                    // 先解除武装：Resolve 会回调进本类，别重入

            // 撤掉动作完成登记：动作真走完时 OnEnd 兜底就找不到条目，不会二次 Resolve。
            // （DramaGate.Resolve 本身幂等，但少发一条垃圾 response 更好；顺带立刻吊销归因凭证 ——
            //  选择已落定，这扇窗的使命结束，后续剧情窗该不该注入 AI 按钮回归默认判定。）
            try { if (ptr != IntPtr.Zero) UnitActionPending.Remove(ptr); } catch { }

            try
            {
                UnityEngine.Debug.Log("[DrillChoice] 玩家选了「" + (verdict > 0 ? "应战" : "婉拒")
                                      + "」npc=" + npc + " slot=" + slot);
            }
            catch { }

            DramaGate.Resolve(slot, new JObject
            {
                ["op"] = "spar",
                ["target"] = npc,
                ["accepted"] = verdict > 0,
                ["answered"] = true,
            });
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
