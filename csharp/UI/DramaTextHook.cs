/// <summary>
/// DramaTextHook —— `UIDramaBase.GetDialogueText` 返回值的捕获（剧情文本"成品/模板"实证用）。
///
/// 为什么加这个（用户质疑驱动）：
///   DramaProbe 实测出 `DramaTool.lastOpenDramaDialogueText` 是**未替换的模板**（含 `{0}`），
///   但玩家屏幕上显然不会显示 `{0}` —— 说明替换发生在别处。那么"成品句在哪"有三个候选：
///     a. 剧情窗的 UnityEngine.UI.Text.text（玩家实际看到的，最权威，但要认哪个 Text 是台词）
///     b. 本 hook 捕获的 `GetDialogueText` 返回值（UI 即将渲染的串）
///     c. `DramaTool.lastOpenDramaDialogueValues` 原始值 + 自己 Replace（推断，风险最高）
///   调用链实证：`InitData → UIDramaDialogue.UpdateUI → GetDialogueText → GetText → LS`，
///   而 DramaTool 存的是模板 → 替换多半发生在 GetText 之内/之后，故 (b) 很可能就是成品。
///
/// 本 hook 只做一件事：把 `__result` 交给 DramaTextCapture 记录，由它在日志里与 (c) 并排打出来。
/// 两者一比即可定案：`uiText` 不含 '{' → 用 (b)，彻底不猜；仍含 '{' → 只能落到 (c) 或去读 Text 组件。
///
/// 只 patch 无参歧义的 `GetDialogueText`（UIDramaBase 上仅一个重载；`GetText` 有两个重载故不碰）。
/// 时序：GetDialogueText 在 `InitData` 内部被调用 → 本 postfix 先跑，`InitData` postfix 后跑，
/// 因此 DramaTextCapture.Note() 里读到的一定是"本页"的返回值（配对成立）。
///
/// 第四轮：**同一个 postfix 顺带取走本页说话人**。
///   `GetDialogueText(ConfDramaDialogueItem item, DramaData dramaData)`（互操作程序集实证：
///   `NativeMethodInfoPtr_GetDialogueText_Public_Static_String_ConfDramaDialogueItem_DramaData_0`）
///   —— `item` 就是**本页那一行配置**，它自带 `speaker` 列（游戏自带表头注释：`1-左` `2-右`；
///   21926 行数据里 115 行 `npcLeft=0 且 speaker=2` 独立佐证"2=右"）。
///   **"谁在说"与"说了什么"于是来自同一行、同一次调用**——不必另挂钩子，也不可能串页。
/// 异常一律吞：Harmony postfix 抛异常会顺着 UI 渲染链上炸。
/// </summary>
using HarmonyLib;

namespace AgentLoopBridge
{
    [HarmonyPatch(typeof(UIDramaBase), "GetDialogueText")]
    internal static class DramaTextHook
    {
        /// <summary>`__0` = 本页的 `ConfDramaDialogueItem`（Harmony 按位置注入原参）。</summary>
        private static void Postfix(string __result, ConfDramaDialogueItem __0)
        {
            int side = 0;
            try { if (__0 != null) side = __0.speaker; } catch { }
            try { DramaTextCapture.NoteUiText(__result, side); } catch { }
        }
    }
}
