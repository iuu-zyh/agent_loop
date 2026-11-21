/// <summary>
/// DramaTextCapture —— 原生剧情窗文本捕获（**邀约地点**专用）。
///
/// 解决什么：`world_ai_action(op=yao_yue)` 走游戏原生邀约剧情，接受后会弹第二层剧情
/// 「我先前在**新达镇**附近发现了一处幽静之地…我会在新达镇附近等你三个月。」——
/// 地点就在这句话里，但工具过去只回 `accepted=true`，地点丢失（玩家知道、模型不知道，
/// NPC 后续无法自洽地提这个约定）。本类把这句话抓下来随工具结果透出。
///
/// **唯一数据源 = `UIDramaBase.GetDialogueText` 的返回值**（hook 见 UI/DramaTextHook.cs）。
/// 真机实测（两次邀约）定案，三次尝试的结论：
///   ① `DramaTool.lastOpenDramaDialogueText` 是**未替换的模板**（含 `{0}`）。
///      人物占位符 `{call|B|A}` 已被 ConfRoleLogLocal.LS 解析，只剩位置占位符 {0}。
///   ② `DramaTool.lastOpenDramaDialogueValues`（Dictionary<int, Il2CppStringArray>）
///      **不含 key 0**（`values0=<no-key-0>`）→ 曾据此写的"自己填 {0}"兜底路**已证伪并删除**。
///   ③ 所以只剩 GetDialogueText：它的返回值**就是游戏填好的成品句**（`{0}` → "新达镇"），
///      即玩家屏幕上看到的那句——零推断，直接用。
///   ④ 第二层剧情家族 = `RoleLogLocal.keyID == "drama_dialogue81012"`（6 个变体，模板都含 {0}）；
///      第一层是 `drama_dialogue81002`。取"最后一条"即天然拿到第二层。
///
/// 配对策略（不硬编码任何剧情 ID）：
///   `BeginInvite(npc)` 武装 → 每次 `UIDramaBase.InitData` 记录一条（覆盖式，取最后一条）
///   → `TryTakeInvite()` 在 OnEnd 结局组装处取走。全程只在 Unity 主线程调用。
///   过滤条件 = 窗内参与者含发起 NPC 的 unitID（unitLeft/unitRight/unit 任一）。
///
/// 失败即静默：抓不到就**整个字段不透出**（工具结果契约：字段缺失 ≠ 否定），
/// 邀约本身的行为与加本类之前完全一致，绝不因本模块抛异常而影响邀约。
/// </summary>
using System;

namespace AgentLoopBridge
{
    internal static class DramaTextCapture
    {
        // ---- 武装态（BeginInvite → TryTakeInvite 之间）----
        private static bool _armed;
        private static string _npcUnitID = "";

        // ---- 捕获结果（每次 Note 覆盖 → 留最后一条）----
        private static bool _has;
        private static int _dramaId;
        private static string _keyId = "";
        private static string _text;

        /// <summary>本页 `GetDialogueText` 的返回值（成品句）。只暂存不落库：等同一 InitData
        /// 周期内、时序在后的 `Note()` 把它归到本页（避免跨页串文本）。</summary>
        private static string _pageUiText;

        /// <summary>
        /// **任意**剧情页的成品句（屏幕上那句）——无条件记录，不限邀约武装态。
        /// 加：成品句是通用资源，`DramaAiOption`（剧情窗那枚「AI 对话」按钮要发出去的就是这句）
        /// 直接复用它，不必再走 `DramaTool.lastOpenDramaDialogueText` 那条**已被真机证伪的模板路**。
        /// 时序不变式同 `Note()`：`GetDialogueText` 在 `InitData` 内部被调用 → 本 hook 的 postfix 先跑、
        /// `InitData` 的 postfix 后跑，所以读到的永远是"本页"的句子。
        /// </summary>
        internal static string LastUiText;

        /// <summary>
        /// 本页说话人**在哪一侧**：`ConfDramaDialogueItem.speaker` 配置列原值。
        /// `1`=左、`2`=右、`0`/`-1`=无说话者（0 = 两侧都没人 / 系统公告；-1 = 奇遇小界面等），
        /// 详见 21926 行**游戏自带**配置表与表头注释：`说话者` 列旁注 `1-左` `2-右`。
        /// 侧别→"是玩家还是 NPC"由 DramaAiOption 拿本页 DramaData 的 unitLeft/unitRight 现算。
        /// 与 `LastUiText` **同进同出**（见 NoteUiText）——两者同源同一行，串页即喂反事实。
        /// </summary>
        internal static int LastSpeakerSide;

        /// <summary>发起邀约前武装。必须在 `CreateAction` 之前调用（第一层剧情随之同步弹出）。</summary>
        internal static void BeginInvite(WorldUnitBase npc)
        {
            _armed = true;
            _npcUnitID = "";
            try { if (npc != null) _npcUnitID = npc.data.unitData.unitID ?? ""; } catch { }
            Clear();
        }

        /// <summary>动作没能发起（撞窗 busy / CreateAction 抛异常）时收枪，避免残留武装态误捕获。</summary>
        internal static void Abort()
        {
            _armed = false;
            _npcUnitID = "";
            Clear();
        }

        /// <summary>`GetDialogueText` postfix 调用：暂存本页成品句 + 本页说话人侧别（未武装时零开销）。
        ///
        /// `speakerSide` = `ConfDramaDialogueItem.speaker`（1=左 / 2=右 / 其他=无说话者）。
        /// 它与 `uiText` **同一行配置**，故与 `uiText` 同进同出：只有句子非空（= 本页确实产出了这一行）
        /// 才更新，否则保留上一页的侧别就是串页——比"判不出"更糟，等于**自信地喂反事实**。
        /// </summary>
        internal static void NoteUiText(string uiText, int speakerSide)
        {
            // 无条件记「本页成品句」：这是通用资源（DramaAiOption 也读它），与邀约武装态无关。
            if (!string.IsNullOrEmpty(uiText))
            {
                LastUiText = uiText;
                LastSpeakerSide = (speakerSide == 1 || speakerSide == 2) ? speakerSide : 0;
            }
            if (!_armed) return;
            if (!string.IsNullOrEmpty(uiText)) _pageUiText = uiText;
        }

        /// <summary>
        /// `UIDramaBase.InitData` postfix 调用。武装期间每次剧情窗都记一条（覆盖式），
        /// 只有「本页有成品句 + 参与者含发起 NPC」的窗才计入——邀约两层都满足，取最后一条即第二层。
        /// </summary>
        internal static void Note(int dramaId, DramaData data)
        {
            if (!_armed) return;
            string ui = _pageUiText;
            _pageUiText = null;                       // 消费掉，防跨页复用
            if (string.IsNullOrEmpty(ui)) return;
            if (!MatchesArmedNpc(data)) return;

            _has = true;
            _dramaId = dramaId;
            try { var it = DramaTool.lastLogItem; if (it != null) _keyId = it.keyID ?? ""; } catch { }
            _text = ui;
        }

        /// <summary>
        /// OnEnd 结局组装处取走结果并收枪。返回 false = 没捕到（调用方不写字段，行为同改动前）。
        /// 真机观察点：成功 `[DramaCapture] 捕获 dramaId=… keyID=… text=…`；
        /// 失败 `[DramaCapture] 未捕获第二层文本`（邀约照常返回，只是不带地点原句）。
        /// </summary>
        internal static bool TryTakeInvite(out string text)
        {
            text = _has ? _text : null;
            try
            {
                if (!string.IsNullOrEmpty(text))
                    ModMain.P("[DramaCapture] 捕获 dramaId=" + _dramaId
                        + " keyID=" + (_keyId.Length == 0 ? "-" : _keyId)
                        + " text=" + text);
                else
                    ModMain.P("[DramaCapture] 未捕获第二层文本（邀约照常返回，只是不带地点原句）");
            }
            catch { }
            Abort();
            return !string.IsNullOrEmpty(text);
        }

        // ------------------------------------------------------------------

        /// <summary>窗内参与者（unitLeft/unitRight/unit）任一等于发起 NPC 的 unitID 即算命中。
        /// 取不到 NPC unitID 时不做过滤（宁可多捕获，事后由 accepted 把关）。</summary>
        private static bool MatchesArmedNpc(DramaData data)
        {
            if (string.IsNullOrEmpty(_npcUnitID)) return true;
            if (data == null) return false;
            return SameUnit(data.unitLeft) || SameUnit(data.unitRight) || SameUnit(data.unit);
        }

        private static bool SameUnit(WorldUnitBase u)
        {
            if (u == null) return false;
            try
            {
                string id = u.data.unitData.unitID;
                return !string.IsNullOrEmpty(id) && id == _npcUnitID;
            }
            catch { return false; }
        }

        private static void Clear()
        {
            _has = false;
            _dramaId = 0;
            _keyId = "";
            _text = null;
            _pageUiText = null;
        }
    }
}
