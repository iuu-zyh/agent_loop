/// <summary>
/// 原生剧情窗「AI 对话」按钮注入 —— Harmony Postfix on UIDramaBase.InitData(int, DramaData)。
///
/// 目标（用户需求）：游戏里跑原生剧情时（NPC 过月寻仇、玩家点「闲聊/宗门/好友」…），
/// 想把这段交互接进 AI 对话，过去得自己开面板 + 手工复述背景。本类在剧情窗注入一枚
/// 「AI 对话」按钮，点击后：
///   1) ChatLauncher.OpenForUnit(NPC) 打开 AI 对话面板；
///   2) SendGameDrama 把**本页屏幕上的成品句**发给 Python → handle_initiative(game_drama)
///      走自主传音同款管线，NPC 以自身人设接住这个话头，玩家直接接着聊。
///
/// **语义刻意中性**（用户定调）：同一枚按钮既服务「NPC 主动找上门」，也服务
/// 「玩家点了闲聊」——只把**交互内容**交给模型，不声称谁主动（Python 侧
/// `initiative.format_game_drama_message` 同口径）。早期文案写死「你主动找上玩家」，
/// 玩家点闲聊时会喂给模型一个反的事实。
///
/// 边界（「旁路选项版」，非 Prefix 接管版）：只捕获 + 加按钮，
/// 不抑制原 UI、不碰 onDramaEndCall/onOptionsClickCall 结算回调——原生选项（寻仇→战斗等）
/// 的机制效果完整保留，本按钮是表演层加法。设计文档：`docs/reference/design-notes.md` §1。
///
/// 实现要点：
///  ① 挂点：UIDramaBase.InitData 覆盖全部剧情窗变体（UIDramaDialogue/BigTexture/Fortuitous…，
///     types44_drama.txt 实锤）；每次初始化先清同名旧钮（剧情推进多页会反复 InitData，幂等）。
///  ② 捕获：DramaData.unitLeft/unitRight/unit 中非玩家者为「说话 NPC」（无 NPC 的系统公告类不注入）；
///     文本取 **DramaTextCapture.LastUiText**（= `GetDialogueText` 的返回值，屏幕成品句，零推断），
///     兜底才用 `DramaTool.lastOpenDramaDialogueText`（**已证伪的模板路**，见 ExtractText 注释）。
///  ③ 模板：**① typed 属性直取**（`UIDramaDialogueBase.btnRightLook` → `btnLeftLook` → `btnNext`
///     → `btnSkip`，第五轮）→ **② 按名深搜**同名四个候选（容忍 `G:` 前缀）→
///     **③ 退"第一个**可用**的 Button"**。判据：**模板必须"层级可见 + 带 Text/TMP + 有尺寸 +
///     Image 不透明"**（`IsUsableTemplate`）——否则克隆出"没字的隐形空热区"。
///     事故：名字路四个候选全 miss → 兜底捡到 `G:btnNext`（翻页指示图标，无文本组件）→
///     日志报"已注入"、玩家屏幕上什么都没有。**typed 属性直取正是为消灭这一整类脆弱**：
///     不必容忍 `G:` 前缀、预制件改名也不受影响。
///     **不用 `GetComponentInChildren<Button>(true)`**：它按层级序取第一个 Button，会带出
///     inactive 节点，克隆体继承 `activeSelf=false`（同源根因）。
///     克隆后**显式 `SetActive(true)`**；onClick 整体换新（清持久化监听，杜绝误触原选项逻辑）
///     + StripOperationItem 清组机制组件；文案「AI 对话」（Text + TMP 双支持），贴着模板放（默认左侧）。
///  ④ 点击：UI 回调即主线程 → Trigger()（开面板 + 发事件）。
///  ⑤ 每个剧情页打一行 `注入诊断`：**模板来源**（typed/按名/兜底，一眼看出走的哪条路）/模板/宿主/
///     克隆 active/世界坐标/屏坐标/屏内/尺寸，落点里带**名签来源**；
///     **typed 与按名都落空时**额外打一行 `剧情窗 Button 清单`（全窗 Button 的真名 + 激活 +
///     有无文本 + alpha + 尺寸）——"玩家看不到按钮"时这两行直接给判据，不必再靠猜。
/// </summary>
using System;
using System.Collections.Generic;
using HarmonyLib;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    [HarmonyPatch(typeof(UIDramaBase), nameof(UIDramaBase.InitData))]
    internal static class DramaAiOption
    {
        private const string ButtonName = "AgentLoopAiOptionButton";
        private const string Label = "对话";

        /// <summary>Button 清单 dump 的条数上限（防某个窗 Button 极多时刷屏）。</summary>
        private const int MaxDumpButtons = 40;

        /// <summary>按钮最小尺寸 —— 模板「查看」偏窄，原样套进去会截字。</summary>
        private const float MinButtonWidth = 110f;
        private const float MinButtonHeight = 40f;

        /// <summary>与左邻元素之间的缝隙（像素）。</summary>
        private const float HostGap = 8f;

        /// <summary>本作 UI 节点真名的前缀（`G:btnEmail`/`G:goGrid1`，已实证）。
        /// 按字段名找按钮时必须两种写法都认。</summary>
        private const string NodePrefix = "G:";

        /// <summary>
        /// 克隆模板的**候选字段名**（`UIDramaDialogueBase` 的字段名）。
        /// 顺序即优先级：`btnRightLook` = 右名签旁那枚「查看」，样式尺寸最合适。
        /// **按名匹配容忍 `G:` 前缀**——节点真名与字段名**不同源**（真名带前缀），
        /// 这一点 09-07 在 HUD 圆钮上已经踩过一次（prefer 列表全 miss 因缺前缀），
        /// 在剧情窗又踩了第二次（详见类头 ③）。
        /// **绝不**用 `GetComponentInChildren&lt;Button&gt;(true)`：它按层级序取第一个 Button，在对话窗里
        /// 极可能是 `btnSkip` 之类 **inactive** 的节点——`Object.Instantiate` 连 `activeSelf=false`
        /// 一起复制，克隆体直接隐形（「日志说已注入、玩家一个按钮都看不到」的根因）。
        /// 与 README 附录 D.3「绝不用树内任意首 Button 当模板」同源。
        /// </summary>
        private static readonly string[] TemplateNames = { "btnRightLook", "btnLeftLook", "btnNext", "btnSkip" };

        // 【已删除】原 `SuppressedDramaIds = {1031,1034,1037,1041,1044,22201}` 按 dramaId 黑名单。
        // 换成 `DramaNativeClaim` 归因凭证（闸 1.5）——旧写法分不清"谁发起的"：
        // 游戏本体自己发起的同一个 dramaId（NPC 用游戏 AI 找你双修、过月事件触发论道）也被一起误挡。
        // 新凭证只认「这张券是我方动作登记的」，两个方向都对。见 DramaNativeClaim 头注释。

        /// <summary>最近捕获的剧情上下文（点击时消费）。仅主线程读写（InitData/点击回调均在主线程）。</summary>
        private static WorldUnitBase _pendingUnit;

        /// <summary>
        /// **最近一页剧情里判出的"对方"**（10-25）。只读快照，供「决斗要不要带队友」的确认窗
        /// 取右位立绘与人名。
        ///
        /// 为什么从这里拿而不是自己再判一次：本类的 `FindNpcOf` / `DetectSpeaker` 已经把这套
        /// 判据（配置侧 `npcLeft/Right` + 名签 + 立绘亮度交叉校验）做完并踩过坑了 ——
        /// 在别处重写一份，迟早与它漂移（本仓最忌讳"两套判据各判各的"）。
        ///
        /// 写法：`NoteSegment` 每页一进来就覆盖（**判不出 uid 的页也覆盖**，因为我们要的只是
        /// "屏幕上那个人是谁"，与"能不能投递给模型"无关）。窗口关闭/换页都不清 ——
        /// 决斗的确认窗恰好弹在"寻仇页刚关"那一刻，清早了就拿不到人。
        /// 由使用方自己判断有效性（单位可能已离开世界）。
        /// </summary>
        private static WorldUnitBase _lastDramaUnit;
        internal static WorldUnitBase LastDramaUnit { get { return _lastDramaUnit; } }
        /// <summary>本页的**归属 uid**（点击时把选项记给谁）—— 归属**唯一依据**就是它：
        /// 有 unit 对象时从对象取；只有 uid 的页（无归属页借链目标 / 闸1 页）也照样有值。
        /// 空串 = 本页没有归属 ⇒ 点击丢弃（判不出就闭嘴）。</summary>
        private static string _pendingUid = "";
        private static string _pendingText = "";
        private static int _pendingDramaId;

        /// <summary>本页所在的剧情窗实例——「谁在说」的判定要在**点击那一刻**读它的立绘状态
        /// （压暗遮罩是 UI 侧逐页刷的，InitData 刚回来时可能还停在上页）。</summary>
        private static UIDramaBase _pendingUi;

        /// <summary>本页的 `DramaData` —— 「配置列 speaker 指的是哪一侧 → 那个单位是谁」要用它取
        /// `unitLeft`/`unitRight`。与 `_pendingUi`/`_pendingText`/`_pendingDramaId` **同一次 Postfix 里赋值**，
        /// 所以点击那一刻读到的必然是本页那一份（跨页会被下一页的 Postfix 覆盖）。
        /// 不存"说话人是玩家还是 NPC"的结论而存 data：判定要保持到点击那一刻才算，日志才好对。</summary>
        private static DramaData _pendingData;

        /// <summary>本页开始：清掉上一页的成品句 + 说话人侧别缓存。**这是"本页校验"**。
        ///
        /// `DramaTextCapture.LastUiText` / `LastSpeakerSide` 只在 `GetDialogueText` 返回**非空**时才被覆盖
        /// （DramaTextCapture.cs 的 `if (!string.IsNullOrEmpty(uiText))`）——所以本页若压根没调它、
        /// 或它返回了空，读到的就是**上一页（甚至上一个 NPC / 已关闭剧情）的旧句 + 旧侧别**，
        /// 却配上本页的 dramaId + npc 发出去：又一个"喂给模型反的事实"。
        /// Prefix 跑在 `InitData` 之前，而 `GetDialogueText` 是 `InitData` **内部**调的
        /// （`DramaTextHook` postfix 会把本页句子和侧别填回来）——「先清后填」天然只拿本页；
        /// 本页没填就保持 null/0，`ExtractText` 走空串兜底、`DetectSpeaker` 走立绘兜底
        /// （字段缺失 ≠ 否定），绝不串页。
        /// 不影响邀约路：那条走自己的 `_pageUiText` 消费机制。</summary>
        private static void Prefix(UIDramaBase __instance, int id, DramaData data)
        {
            try { DramaTextCapture.LastUiText = null; } catch { }
            try { DramaTextCapture.LastSpeakerSide = 0; } catch { }

            // 「本页两侧都用运行时单位」的页（配表 npcLeft==0 且 npcRight==0）把立绘位 ID 归零。
            //
            // 为什么：`UIDramaDialogue.UpdateUnit`@0x3300D80 在 **id==0** 时会先看
            // `dramaData.unitLeftID(+0x40)/unitRightID(+0x44)`，非 0 就用它覆盖 id
            // （左取 +0x40、右取 +0x44，见 0x18301363..0x183013A6）。而这两个字段挂在
            // **跨页共享**的 `DramaData` 上，`NextDialogue`/`InitData`/`UpdateUI` 全链路都不重置。
            // 真机 23205/23206（同一段话第一、二句，配表都是 0/0）实测：第二句右位被
            // `unitRightID=11`（= unitLeft）顶替，名签与立绘都变成左位那个人，
            // 而 `dramaData.unitLeft/unitRight` 始终是两个不同的人。
            //
            // 归零 = 恢复这类页的本义「左取 unitLeft / 右取 unitRight」；对本来正常
            // （ID 已是 0，或 ID 恰好指对侧）的页是幂等空操作。只碰 0/0 页，
            // npcLeft/npcRight 有配值的页（190 的 1/11、mod 壳的 11/12）一律不动。
            try
            {
                if (data == null) return;
                var item = g.conf?.dramaDialogue?.GetItem(id);
                if (item == null) return;
                if (item.npcLeft != 0 || item.npcRight != 0) return;
                int l0 = data.unitLeftID, r0 = data.unitRightID;
                if (l0 == 0 && r0 == 0) return;
                data.unitLeftID = 0;
                data.unitRightID = 0;
                ModMain.P("[DramaAiOption] 立绘位ID归零 page=" + id + " 原=" + l0 + "/" + r0);
            }
            catch { }
        }

        private static void Postfix(UIDramaBase __instance, int id, DramaData data)
        {
            // 结果级立绘纠偏：必须最先跑 —— 它可能在下面各闸之前重刷一次 UI。
            try { SanitizePortraitSlots(__instance, id, data); } catch { }
            // 剧情文本捕获（邀约第二层地点的 {0} 取值）：必须放在所有 early-return 之前——
            // 邀约第一层 81002 是在 ToolExecutor.Execute 同步期间开的（SuppressAiOption=True 会提前 return）。
            // 内部自带武装闸：只有 yao_yue 挂起期间才工作，平时零开销。
            try { DramaTextCapture.Note(id, data); } catch { }
            try
            {
                if (__instance == null) return;
                // 复核修·清旧钮必须**在所有闸之前、无条件执行**
                // 闸 1（`SuppressAiOption`）挡的是"工具自导的剧情窗"，而剧情窗实例是**跨页复用**的
                // （类头 ① 就是为跨页清钮写的）。若在被闸挡住的这一页不清，上一页克隆出的
                // 「AI 对话」按钮会留在窗里**仍然可点**——点下去用 `_pending*`（上一页的 NPC / 台词 / 窗）
                // 开面板并发事件：玩家看到的和发出去的不是同一幕。
                try
                {
                    var old = FindDeep(__instance.transform, ButtonName);
                    if (old != null) UnityEngine.Object.Destroy(old.gameObject);
                }
                catch { }
                if (DramaGate.SuppressAiOption)
                {
                    // 分段器：工具执行期弹的窗（闸 1）**照记** —— 正文"投不投"按链政策
                    // （内容会随工具结果送达 ⇒ 不投；结果只有语义字段 ⇒ 可投），
                    // 但**本页归属必须建、选项照记**（09-23 用户定：选项和 UI 文本是两回事）。
                    // 归属：优先本页自己的 NPC；判不出（无归属页）借"当前链的目标 uid"。
                    var npc1 = FindNpcOf(data, id);
                    string uid1 = npc1 != null ? DramaNativeClaim.UidOf(npc1) : "";
                    if (string.IsNullOrEmpty(uid1))
                    {
                        try { uid1 = DramaNativeClaim.CurrentChainUid(); } catch { }
                    }
                    bool noInject1 = true;
                    try { noInject1 = DramaNativeClaim.ContentViaResultOf(DramaNativeClaim.OpOf(uid1)); } catch { }
                    if (npc1 != null)
                    {
                        NoteSegment(__instance, id, data, npc1, noInject1);
                        SetPendingContext(__instance, data, id, npc1);
                    }
                    else if (!string.IsNullOrEmpty(uid1))
                    {
                        SetPendingContextUid(__instance, data, id, uid1);
                        string w1 = DetectSpeaker(__instance, data, __instance.transform, out string d1, null);
                        NoteSegmentByUid(__instance, id, data, uid1, noInject1, w1, _pendingText);
                        ModMain.P("[DramaAiOption] 闸1 无归属页 ⇒ 借用链目标 uid=" + uid1 + " 入段（" + d1 + "）");
                    }
                    else
                    {
                        ClearPendingContext();   // 没有归属对象：不记 + 清上下文（判不出就闭嘴）
                    }
                    return;      // 工具执行中弹的窗：不注入按钮（内容随后经结果/语义给模型）
                }
                InjectOptionButton(__instance, id, data);
            }
            catch (Exception e)
            {
                ModMain.P("[DramaAiOption] postfix: " + e.Message);
            }
        }

        /// <summary>清空"待处理剧情页"上下文（`_pending*`）—— **没有归属对象的页**（无 NPC 页 /
        /// 闸1 首窗）用它，防止上一页的残值被这一页的点击读到。
        ///
        /// 为什么必须存在（09-23 复核发现的归属错误）：这上下文**只在当前页有效**，但全类没有
        /// 关窗清理 —— 上一场普通剧情最后一页赋的值会一直留着：玩家在新窗上点**游戏自己的选项
        /// 按钮**（CreateOptions postfix 照样给它挂了监听）时读到的是上一个人 ⇒ 选择被错记进
        /// 上一个人的段（还会随那段下次投递发出去）。由此得到全类不变式：**每个会记录 UI 上下文
        /// 的页，Postfix 里必须二选一 —— `SetPendingContext`（有归属对象）或本方法（没有）。**
        /// 闸1 分支保留原地内联清空（契约测试钉了它与 NoteSegment 的相对顺序），语义相同。</summary>
        private static void ClearPendingContext()
        {
            _pendingUnit = null;
            _pendingUid = "";
            _pendingText = "";
            _pendingUi = null;
            _pendingDramaId = 0;
            _pendingData = null;
        }

        /// <summary>建立"当前页"的待处理上下文 —— **有归属对象的页**用它（正常注入页 / 认领页 /
        /// 对方不在场页 / 闸1 页）。归属 uid 从 unit 对象取；与 `ClearPendingContext` 成对，
        /// 保证"记录进段的页必有归属、点击必然落对段"。</summary>
        private static void SetPendingContext(UIDramaBase ui, DramaData data, int id, WorldUnitBase npc)
        {
            _pendingUnit = npc;
            _pendingUid = npc != null ? DramaNativeClaim.UidOf(npc) : "";
            _pendingDramaId = id;
            _pendingText = ExtractText();
            _pendingUi = ui;
            _pendingData = data;
        }

        /// <summary>**只有 uid** 的页（无归属页借"当前链的目标 uid"、闸1 无归属页）用它：
        /// 没有 unit 对象（开不了面板，所以这类页本来也不注入按钮），但**选项必须记** ——
        /// 归属用 uid（09-23 用户定）。</summary>
        private static void SetPendingContextUid(UIDramaBase ui, DramaData data, int id, string uid)
        {
            _pendingUnit = null;
            _pendingUid = uid ?? "";
            _pendingDramaId = id;
            _pendingText = ExtractText();
            _pendingUi = ui;
            _pendingData = data;
        }

        private static void InjectOptionButton(UIDramaBase ui, int id, DramaData data)
        {
            var root = ui.transform;
            if (root == null) return;
            // ① 幂等清旧钮已上移到 Postfix（必须在所有闸之前无条件执行，见那里的注释），此处不再重复

            // ② 捕获剧情上下文；无 NPC（系统公告类）不注入
            var npc = FindNpcOf(data, id);
            if (npc == null)
            {
                // 无归属页（实证：攻击逃跑页 20303 右名签 `""[链OFF]`，按 NPC 找人 = 整页跳过）。
                // 09-23 用户定：这类页**不能丢** —— 借"当前动作链的目标 uid"入段，并建本页归属，
                // 让这一页上的选择（如逃跑页的「离开」= battleExit）也能记进来、投得出去。
                // 没有活动链 ⇒ 维持"不记 + 清上下文"（判不出就闭嘴，绝不猜）。按钮一律不注入
                //（没有 NPC 就没有可以对话的对象）。
                string chainUid = "";
                try { chainUid = DramaNativeClaim.CurrentChainUid(); } catch { }
                if (!string.IsNullOrEmpty(chainUid))
                {
                    SetPendingContextUid(ui, data, id, chainUid);
                    string who = DetectSpeaker(ui, data, root, out string chainDiag, null);
                    bool noInj = true;
                    try { noInj = DramaNativeClaim.ContentViaResultOf(DramaNativeClaim.OpOf(chainUid)); } catch { }
                    NoteSegmentByUid(ui, id, data, chainUid, noInj, who, _pendingText);
                    ModMain.P("[DramaAiOption] 无 NPC 页 ⇒ 借用链目标 uid=" + chainUid + " 入段（"
                              + (string.IsNullOrEmpty(who) ? "说话人判不出" : who) + "）｜" + chainDiag);
                }
                else
                {
                    ClearPendingContext();   // 没有归属对象：不记 + 清上下文
                }
                return;
            }

            // ③ 闸 1.5：这个窗是不是「我方动作的后续」？——凭 `DramaNativeClaim` 的归因凭证认领。
            //    闸 1（SuppressAiOption）只管得住 `Execute` **执行期内**同步弹的窗；而工具发起的动作是
            //    异步延续的（玩家点完之后，游戏的 lambda 才在之后的某一帧弹下一层），那时闸 1 早已落回。
            //    凭证**认人不认 ID** —— 所以既挡得住我方的 22201，又放得过**游戏本体自己**发起的
            //    1031 双修（旧实现按 dramaId 黑名单，把后者一起误挡了）。机理见 DramaNativeClaim 头注释。
            //    放在 `_pending*` 赋值**之前**：认领成功就直接 return，不必污染待处理上下文。
            string claimUid = DramaNativeClaim.UidOf(npc);
            // 「记页」与「注入按钮」是两件事，本方法里各自算：`segmentRecorded` = 已经记过一次
            // （处置页走的是"记但不投、却仍要注入"那条路，见下面认领分支里的注释）。
            bool segmentRecorded = false;
            if (DramaNativeClaim.TryClaim(claimUid, ui))
            {
                // ★这条链的**政策**（09-23 用户定）★：按钮注入与正文"投不投"由"内容会不会随工具
                //   结果送达"决定，**不再由"是不是认领页"决定**。表在 `DramaNativeClaim` 里只写一处：
                //   · 论道/双修/切磋/攻击 ⇒ 内容 = 工具结果（区间读）⇒ 正文不投、不注入按钮；
                //   · 邀约 ⇒ 结果只有语义字段 ⇒ 正文可投、**注入按钮**（用户要的"场景里能点对话"）；
                //   · 赠送/讨要/传功（工具调用）⇒ 结果只有语义字段 ⇒ 正文可投、不注入（短决策窗）。
                string chainOp = "";
                try { chainOp = DramaNativeClaim.OpOf(claimUid); } catch { }
                bool contentViaResult = true, injectOnClaimed = false;
                try
                {
                    contentViaResult = DramaNativeClaim.ContentViaResultOf(chainOp);
                    injectOnClaimed = DramaNativeClaim.InjectOnClaimedOf(chainOp);
                }
                catch { }
                ModMain.P("[DramaAiOption] 闸1.5 认领（我方动作后续）dramaId=" + id + " unit=" + claimUid
                          + " op=" + (string.IsNullOrEmpty(chainOp) ? "(未知)" : chainOp)
                          + (contentViaResult ? "【内容随结果送达：正文不投】" : "【内容交给段：正文可投】")
                          + (injectOnClaimed ? "【注入按钮】" : ""));

                // ★本页归属（09-23 普遍化）★：认领页也必须把"玩家选了什么"留下来 —— 归属一建，
                //   这一页的选项就跟着入段（选项永不受"正文不投"影响，见 `NoteOption`）。
                SetPendingContext(ui, data, id, npc);
                // 说话人判定结果**提到外面**：记页与"处置页收口"共用同一份
                // （不重算 —— 重算一次立绘扫描是小事，两处判据漂移才是大事）
                string claimWho = DetectSpeaker(ui, data, root, out string claimDiag, npc);

                // ★先记页、再收口（09-23 修）★：处置页的收口要读"区间"（工具结果 = 段内容），
                //   先收口再记 ⇒ 结果里会**缺处置页这一行**（真机可复现的内容丢失）。
                NoteSegment(ui, id, data, npc, contentViaResult, claimWho);
                segmentRecorded = true;

                // ★攻击 B 支（玩家战胜）：这一页是 NPC 的**处置页**（20403 求饶 / 20404 任凭处置，
                //   选项是胜方手里的「击杀此人」「放你一马」）。链到此为止 —— 之后是玩家的处置权，
                //   不该让 NPC 的回合干等"别人替我决定生死"，所以**当场收口**，而且**不 return**：
                //   要让下面那条注入路径给这一页长出「对话」按钮，玩家点它就能和正在求饶的 NPC 说话。
                //   （那一行原文已随结果给过模型 ⇒ 标"不投"由 `contentViaResult` 负责；
                //    之后的尾页与「击杀/放过」选择留在段里，点对话即得。）
                bool chatHere = false;
                try
                {
                    // ★"没打起来"的反馈页（10-28）★ `20102` = 引擎的逃跑判定反馈。
                    //   这一页出现就说明**这场不会打了**（`CreateAction` 返回 0 只代表动作建好，
                    //   不代表战斗会发生）。不收口的话只能等静默 60s —— 真机实测卡了 67 秒。
                    //   位置放在处置页**之前**：那个分支的说明就紧跟在 `ResolveAtVerdictPage` 之后，
                    //   插在中间会把"该有「对话」按钮"那段注解挤远（`test_drama_segment_contract` 钉着它）。
                    if (BattleOutcome.IsNoBattleFeedbackPage(id))
                        chatHere = BattleOutcome.ResolveAtNoBattlePage(
                            "反馈页 dramaId=" + id + "（对方没应战，这场没打起来）");
                    else if (BattleOutcome.IsVerdictPage(id))
                        chatHere = BattleOutcome.ResolveAtVerdictPage("处置页 dramaId=" + id + " 已抓到");
                }
                catch { }

                if (!injectOnClaimed && !chatHere)
                {
                    ModMain.P("[DramaAiOption] 认领页不注入按钮（op="
                              + (string.IsNullOrEmpty(chainOp) ? "未知" : chainOp)
                              + "；页与选项照记）dramaId=" + id);
                    return;
                }
                if (chatHere)
                    ModMain.P("[DramaAiOption] ★处置页已收口，本页放行注入（该有「对话」按钮；"
                              + "原文已随结果给过模型，本行不再投）dramaId=" + id);
            }

            // ★「对方不在场」的页不注入按钮（用户需求，09-23 截图实证）★
            //   实拍：邀约场景的选择页（dramaId 81121）只有**玩家**立绘/名签，NPC 那侧名签 `""[链OFF]`、
            //   立绘判不出 —— 但 `DramaData` 里还挂着对方单位，于是按钮照样长出来、悬在空处
            //   （点了还会把"要和姜萌做什么…"这种界面话当台词投给模型）。
            //   判据见 `NpcTagPresent`；**按钮不注入 ≠ 内容不记**：这页照样入段（它是这场交互的一部分）。
            if (!NpcTagPresent(ui, npc))
            {
                if (!segmentRecorded)
                    NoteSegment(ui, id, data, npc, noInject: false);
                ModMain.P("[DramaAiOption] 对方不在场（NPC 名签缺失/链OFF）⇒ 不注入按钮；本页照记段 (dramaId="
                          + id + ", npc=" + SafeName(npc) + ")");
                // ★本页归属（09-23 普遍化）★：这一页虽然不注入按钮，但**是照常入段的**（noInject: false，
                //   可投）—— 选项必须跟它一起进段，否则段里有问没答（"只知道疑问、不知道选了什么"）。
                //   归属用**本页自己**的 npc（不是残留的上一页值），所以不会有张冠李戴。
                SetPendingContext(ui, data, id, npc);
                return;
            }

            // 正常注入页：本页归属（uid 从 unit 取）+ 成品句缓存。选项点击按 uid 入段。
            SetPendingContext(ui, data, id, npc);
            // 说话人 + 立绘压暗状态（每页一行；点击那一刻还会再读一次，那一次才作数）
            string pageSpeaker = DetectSpeaker(ui, data, root, out string sdiag);
            ModMain.P("[DramaAiOption] 说话人判定（注入时）：" + sdiag);
            // ★ 分段器：本页入段（正常可投）。说话人与成品句都用**同一次判定/同一次取值**的结果，
            //   不重算 —— 重算就是两处判据（迟早漂移），而且可能与即将发出去的那一句不同源。
            //   处置页除外：它在认领分支里已经按"照记不投"记过（说话人用的是那一趟判出来的 claimWho），
            //   这里再记一次就变成同一页两条。
            if (!segmentRecorded)
                NoteSegment(ui, id, data, npc, noInject: false, speakerOverride: pageSpeaker,
                            textOverride: _pendingText);

            // ③ 选模板：**按名优先，且必须"可用"**（= `IsUsableTemplate`：层级可见 + 带文本 + 有尺寸）。
            //    `btnRightLook` = 右名签旁那枚「查看」，样式尺寸正合适。
            //    两条硬要求都是真机教训买来的：
            //      · **名字要容忍 `G:` 前缀** —— 本作 UI 节点**真名带前缀**（`G:btnEmail`/`G:goGrid1`
            //        是 09-07 已实证的真名），只按字段名找会**四个候选全 miss**；
            //      · **必须有可改的 Text/TMP** —— 否则克隆出"没字的空热区"。
            //    事故正是两条同时踩：全 miss → 退兜底 → 捡到 `G:btnNext`（翻页指示图标，
            //    无文本组件）→ 日志说"已注入"、玩家屏幕上什么都没有。
            Button tpl = null;
            string tplName = "";
            string tplVia = "";
            // ① typed 属性（首选）
            //   游戏把这几枚按钮作为 typed 属性暴露在 `UIDramaDialogueBase` 上
            //   （`[p] Button btnRightLook / btnLeftLook / btnNext / btnSkip`，
            //   互操作程序集实证；`UIDramaDialogue : UIDramaDialogueBase : UIDramaBase`，
            //   所以全部剧情窗变体都拿得到）。**直接读引用，不按名字符串在树上找**——
            //   名字路要靠 `G:` 前缀容忍、且预制件一改名就全 miss（09-13 就是这么捡到
            //   无文本的 `btnNext`、报"已注入"而屏幕上什么都没有的）。见 TypedTemplate 头注释。
            try { tpl = TypedTemplate(ui, out tplVia); } catch { tpl = null; }
            if (tpl != null) tplName = tpl.gameObject.name;
            // ② 按名深搜（兜底：窗不是 UIDramaDialogueBase，或该属性为 null）
            if (tpl == null)
            {
                for (int i = 0; i < TemplateNames.Length; i++)
                {
                    var node = FindDeep(root, TemplateNames[i]);
                    if (node == null) continue;
                    var b = node.GetComponent<Button>();
                    if (b == null || !IsUsableTemplate(b)) continue;
                    tpl = b; tplName = node.name;   // 记节点**真名**（可能带 G: 前缀），便于对日志
                    tplVia = "按名深搜 " + TemplateNames[i];
                    break;
                }
            }
            // ③ "第一个可用 Button"（最后兜底）
            if (tpl == null)
            {
                tpl = FindBestTemplate(root);
                tplName = tpl != null ? tpl.gameObject.name : "";
                if (tpl != null)
                {
                    tplVia = "兜底(首个可用)";
                    ModMain.P("[DramaAiOption] typed 与按名候选全 miss，退回兜底模板=" + tplName + " (dramaId=" + id + ")");
                    DumpButtons(root, id);
                }
            }
            if (tpl == null)
            {
                ModMain.P("[DramaAiOption] 剧情窗无可用克隆模板（typed 与按名全 miss，且无「可见+带文本」的 Button），跳过注入 (dramaId=" + id + ")");
                DumpButtons(root, id);
                return;
            }
            var srcRt = tpl.GetComponent<RectTransform>();
            GameObject clone = UnityEngine.Object.Instantiate(tpl.gameObject);
            clone.name = ButtonName;
            var rt = clone.GetComponent<RectTransform>();
            if (rt == null)
            {
                UnityEngine.Object.Destroy(clone);
                return;
            }

            // 关键：模板可能是 inactive 节点（btnSkip 一类），Instantiate 会把 activeSelf=false
            //    一起复制过来 → 克隆体永久不可见。必须显式点亮。
            clone.SetActive(true);

            // 宿主：优先**与模板同父**（同层同布局上下文，位置可预测）；
            // 拿不到模板矩形时退回剧情根 + 屏幕右下角（旧行为）。
            Transform host = (srcRt != null && srcRt.parent != null) ? srcRt.parent : root;
            rt.SetParent(host, false);
            rt.localScale = Vector3.one;
            string anchorNote = "旧兜底(右下角)";
            if (srcRt != null)
            {
                // 抄模板的锚点体系（同一布局上下文；定位改用世界坐标算，锚点只影响拉伸行为）
                rt.anchorMin = srcRt.anchorMin;
                rt.anchorMax = srcRt.anchorMax;
                rt.pivot = srcRt.pivot;
                rt.anchoredPosition = srcRt.anchoredPosition;

                // 尺寸必须在定位【之前】定下来
                //   宽度决定右缘；先定位后加宽，矩形会以 pivot 为中心向两侧长回去，
                //   右缘吃掉左边的邻居。09-13 真机症状：「AI 对话」同时压住「查看」和 NPC 名签
                //   —— 偏移量按加宽前的窄宽算，加宽后向右多长了 (110-sw)/2。
                float w = rt.sizeDelta.x > 1f ? rt.sizeDelta.x : 120f;
                if (w < MinButtonWidth)
                    rt.sizeDelta = new Vector2(MinButtonWidth, Mathf.Max(rt.sizeDelta.y, MinButtonHeight));

                // 落点优先级：**NPC 名签的左侧**（用户指定：名字通常在右边，不能压名字）
                //   → 退「模板左侧」→ 再退剧情根 + 屏幕右下角。
                var nameRt = FindNpcNameRect(ui, root, data, npc, out string nameVia);
                if (nameRt != null && PlaceLeftOf(rt, nameRt, HostGap))
                    anchorNote = "名签左(" + nameRt.name + " w=" + (int)nameRt.rect.width + "；" + nameVia + ")";
                else if (PlaceLeftOf(rt, srcRt, HostGap))
                    anchorNote = nameRt == null ? "模板左(无名签)" : "模板左(名签定位失败)";
                else
                    PlaceAtRootCorner(rt);
            }
            else
            {
                PlaceAtRootCorner(rt);
            }

            // 克隆体残留的组机制组件销毁（防剧情窗操作组把它当自己的选项驱动）
            NpcPanelButton.StripOperationItemPublic(clone);

            SetLabel(clone, Label);   // Text + TMP 双支持（只有一种会被改到）

            var btn = clone.GetComponent<Button>() ?? clone.AddComponent<Button>();
            btn.onClick = new Button.ButtonClickedEvent();   // 整体换新：清掉克隆来的持久化监听
            ClickUtils.Attach(btn, Trigger);

            ModMain.P("[DramaAiOption] AI 对话按钮已注入 (dramaId=" + id + ", npc=" + SafeName(npc) + ")");
            LogInjectDiag(ui, clone, rt, tpl, tplName, srcRt, anchorNote, tplVia);
        }

        /// <summary>剧情根 + 屏幕右下角（最后的兜底，拿不到模板矩形时才走）。</summary>
        private static void PlaceAtRootCorner(RectTransform rt)
        {
            try
            {
                rt.anchorMin = new Vector2(1f, 0f);
                rt.anchorMax = new Vector2(1f, 0f);
                rt.pivot = new Vector2(1f, 0.5f);
                rt.sizeDelta = new Vector2(170f, 46f);
                rt.anchoredPosition = new Vector2(-24f, 64f);
            }
            catch { }
        }

        /// <summary>`UIDramaDialogueBase` 的左右名签**字段名**（真名带 `G:` 前缀，`FindDeep` 已容忍）。</summary>
        private static readonly string[] RightNameFields = { "textRightName" };
        private static readonly string[] LeftNameFields = { "textLeftName" };

        /// <summary>
        /// 找**NPC 那一侧**的名签矩形，用来把按钮放到它左边（09-13 用户指定落点：
        /// 「AI 对话」放 NPC 名签左侧 —— 不压名字、也不压旁边的「查看」）。
        ///
        /// 侧别判据 = `DramaData.unitLeft/unitRight` 与 npc 比 **native 指针**
        /// （**不用**引用相等，见 README 附录 D.1）；判不出来默认右侧
        /// （剧情窗常态：玩家在左、NPC 在右，`ShowDramaService` 同口径）。
        /// 该侧找不到就退另一侧；都没有返回 null → 调用方退回"模板左侧"。
        /// </summary>
        private static RectTransform FindNpcNameRect(UIDramaBase ui, Transform root, DramaData data,
                                                     WorldUnitBase npc, out string via)
        {
            via = "";
            // ① NPC 在哪一侧 = **名签文本**（实测可用）
            //    哪一侧的名签写着 NPC 的名字，NPC 就在哪一侧。这一条现在是**实测过的**：
            //    真机 `名签 左="唐炎"[活] 右="司空雨珍"[活] 玩家名="唐炎" NPC名="司空雨珍"`。
            //    ⚠ **旧的 `data.unitLeft/unitRight` 指针比对已证伪**：这两个属性
            //    **返回同一个单位**，于是 `r.Pointer == npc.Pointer` **恒为 true** → `onRight` 恒真。
            //    真机这些剧情 NPC 恰好在右，所以**结果一直碰巧是对的**；NPC 一旦出现在左侧就会锚错边。
            bool onRight = true;
            string sideVia = "";
            var dlg = AsDialogueBase(ui);
            string npcName = SafeName(npc);
            if (!string.IsNullOrEmpty(npcName) && dlg != null)
            {
                bool la, ra;
                string lt = NameTagText(dlg, true, out la);
                string rt = NameTagText(dlg, false, out ra);
                if (ra && !string.IsNullOrEmpty(rt) && rt == npcName) { onRight = true; sideVia = "名签右=" + npcName; }
                else if (la && !string.IsNullOrEmpty(lt) && lt == npcName) { onRight = false; sideVia = "名签左=" + npcName; }
            }
            if (sideVia.Length == 0)
            {
                // 名签判不出 → 退回旧指针比对。**它已证伪（恒 true）**，只作"保持既有行为"的最后兜底，
                // 并在 via 里明说，免得哪天又被人当成可信判据。
                try
                {
                    var r = SafeGet(() => data.unitRight);
                    var l = SafeGet(() => data.unitLeft);
                    if (r != null && npc != null && r.Pointer == npc.Pointer) onRight = true;
                    else if (l != null && npc != null && l.Pointer == npc.Pointer) onRight = false;
                }
                catch { }
                sideVia = "侧别退指针兜底(已证伪，恒右)";
            }
            // ② typed 属性（首选）：`textRightName`/`textLeftName` 是 `UIDramaDialogueBase` 上的
            //    typed `Text`，直接读引用比按名字符串深搜可靠（不必容忍 `G:` 前缀，改名也不受影响）。
            //    `Graphic.rectTransform` 就是矩形。
            try { var t = TypedNameRect(ui, onRight, out via); if (t != null) { via += "；" + sideVia; return t; } } catch { }
            // ③ 按名深搜（兜底）
            var node = FindFirstOf(root, onRight ? RightNameFields : LeftNameFields);
            if (node == null) node = FindFirstOf(root, onRight ? LeftNameFields : RightNameFields);
            if (node == null) { via = "名签两路全 miss；" + sideVia; return null; }
            via = "按名深搜 " + node.name + "；" + sideVia;
            try { return node.GetComponent<RectTransform>(); } catch { return null; }
        }

        private static Transform FindFirstOf(Transform root, string[] names)
        {
            for (int i = 0; i < names.Length; i++)
            {
                var t = FindDeep(root, names[i]);
                if (t != null) return t;
            }
            return null;
        }

        /// <summary>
        /// 把 `rt` 摆到 `anchor` 的**正左侧**：克隆体右缘 = 锚点左缘 − `gap`。
        ///
        /// 用**世界坐标**算，而不是拿两个 `anchoredPosition` 互相加减 —— 名签与克隆体
        /// 常常不在同一个父节点下，锚点/轴心也未必相同，直接加减会错位
        /// （「压在查看和名签上」的第二个成因）。假设无旋转（UI 常态）：
        ///   右缘 = position.x + (1 − pivot.x) · rect.width · lossyScale.x
        ///   左缘 = position.x − pivot.x · rect.width · lossyScale.x
        /// </summary>
        private static bool PlaceLeftOf(RectTransform rt, RectTransform anchor, float gap)
        {
            if (rt == null || anchor == null) return false;
            try
            {
                var sa = anchor.lossyScale;
                float anchorLeft = anchor.position.x - anchor.pivot.x * anchor.rect.width * sa.x;
                var sc = rt.lossyScale;
                float pivotToRightEdge = (1f - rt.pivot.x) * rt.rect.width * sc.x;
                var p = rt.position;
                rt.position = new Vector3(anchorLeft - gap - pivotToRightEdge, p.y, p.z);
                return true;
            }
            catch { return false; }
        }

        /// <summary>改按钮文案：**同时支持 legacy `Text` 与 `TextMeshProUGUI`**（项目铁律：
        /// 两种文本都可能在，只改一种会出现"改了字没变/仍显示模板原字"）。
        /// 模板是「查看」时它的标签通常是 legacy `Text`，但不同变体不保证，两边都试。</summary>
        private static void SetLabel(GameObject go, string text)
        {
            bool done = false;
            try { var t = go.GetComponentInChildren<Text>(true); if (t != null) { t.text = text; done = true; } } catch { }
            try
            {
                var p = go.GetComponentInChildren<TMPro.TextMeshProUGUI>(true);
                if (p != null) { p.text = text; done = true; }
            }
            catch { }
            if (!done)
                ModMain.P("[DramaAiOption] 警告：克隆体无 Text/TMP 组件，文案可能仍是模板原字");
        }

        /// <summary>
        /// 注入诊断：一次把"为什么看不见"的全部候选打出来——模板名/模板自身激活/宿主/
        /// 克隆体激活（自身 + 层级）/世界坐标/屏坐标/是否落在屏内/尺寸。
        /// 用户报"看不到按钮"时，这一行直接给判据，不必再靠猜（上一次就是靠猜绕了一整轮）。
        /// 每个剧情页一行，量很小。
        /// </summary>
        private static void LogInjectDiag(UIDramaBase ui, GameObject clone, RectTransform rt,
                                          Button tpl, string tplName, RectTransform srcRt,
                                          string anchorNote, string tplVia)
        {
            try
            {
                var sb = new System.Text.StringBuilder("[DramaAiOption] 注入诊断：");
                sb.Append("窗型=").Append(RealUiType(ui));
                sb.Append(" 模板来源=").Append(string.IsNullOrEmpty(tplVia) ? "?" : tplVia);
                sb.Append(" 模板=").Append(string.IsNullOrEmpty(tplName) ? "?" : tplName);
                if (tpl != null) sb.Append("(模板active=").Append(tpl.gameObject.activeSelf).Append(")");
                sb.Append(" 落点=").Append(string.IsNullOrEmpty(anchorNote) ? "?" : anchorNote);
                sb.Append(" 宿主=").Append(rt.parent != null ? rt.parent.name : "?");
                // 自身 active 与"祖链是否也活着"是两件事：后者才是"玩家能不能看见"
                sb.Append(" 克隆active=").Append(clone.activeSelf).Append("/").Append(clone.activeInHierarchy);
                var wp = rt.position;
                sb.Append(" 世界坐标=(").Append((int)wp.x).Append(",").Append((int)wp.y).Append(")");
                // Overlay 传 null；ScreenSpaceCamera/WorldSpace 要传画布相机。
                // ⚠ 必须走 DiagCamera（取**根 Canvas**判模式）——直接看 `ui.canvas.renderMode` 会踩
                // 嵌套 Canvas 的序列化残留，把相机算成 null → 屏幕坐标整个错（同 HoverTip 那条教训）。
                Camera cam = DiagCamera(ui);
                Vector2 sp = RectTransformUtility.WorldToScreenPoint(cam, wp);
                bool onScreen = sp.x >= 0f && sp.x <= Screen.width && sp.y >= 0f && sp.y <= Screen.height;
                sb.Append(" 屏坐标=(").Append((int)sp.x).Append(",").Append((int)sp.y).Append(")")
                  .Append(" 屏内=").Append(onScreen ? "是" : "**否**")
                  .Append(" 相机=").Append(cam == null ? "null(Overlay/未取到)" : cam.name)
                  .Append(" 尺寸=").Append((int)rt.sizeDelta.x).Append("x").Append((int)rt.sizeDelta.y);
                ModMain.P(sb.ToString());
            }
            catch { }
        }

        /// <summary>剧情窗的**渲染相机**（`WorldToScreenPoint` 用）。
        /// ⚠ 判模式必须看**根 Canvas**——`Unity` 规定嵌套 Canvas 的 `renderMode` 不生效，子节点上那个
        /// 只是序列化残留。只看 `ui.canvas.renderMode` 会把"根是 ScreenSpaceCamera"的子画布误判成
        /// Overlay → 相机传 null → **屏幕坐标整个算错**。**与 `HoverTip.ResolveCamera` 同一条教训**
        /// （那边已经因此出过"登记全在、气泡一个都不出"的真机事故）。
        /// 只有根 Canvas 真的是 Overlay 才返回 null。</summary>
        private static Camera DiagCamera(UIDramaBase ui)
        {
            try
            {
                var c = ui != null ? ui.canvas : null;
                if (c == null) return null;
                var root = c;
                try { var rc = c.rootCanvas; if (rc != null) root = rc; } catch { }
                if (root.renderMode == RenderMode.ScreenSpaceOverlay) return null;
                var cam = c.worldCamera;
                if (cam == null && root != c) cam = root.worldCamera;
                return cam != null ? cam : Camera.main;
            }
            catch { return null; }
        }

        /// <summary>递归找第一个**可用**的 Button 当兜底模板（可用 = 见 `IsUsableTemplate`）。
        /// IL2CPP：禁 `foreach(Transform)`，用索引循环。
        /// **不**用 `GetComponentInChildren&lt;Button&gt;(true)`（按层级序取第一个 Button，会带出
        /// inactive / 无文本的节点 → 克隆即隐形或没字，见 README 附录 D.3 铁律）。</summary>
        private static Button FindBestTemplate(Transform root)
        {
            if (root == null) return null;
            var b = root.GetComponent<Button>();
            if (b != null && IsUsableTemplate(b)) return b;
            int n = root.childCount;
            for (int i = 0; i < n; i++)
            {
                var r = FindBestTemplate(root.GetChild(i));
                if (r != null) return r;
            }
            return null;
        }

        /// <summary>
        /// 模板可用性 = "克隆出来玩家能看见、还能改字"。四条：
        ///   ① `activeInHierarchy`（**不是** `activeSelf`——只看自身会漏掉"父链整条关着"；
        ///      诊断行里两个都打了，就是为此）；
        ///   ② `RectTransform` 有尺寸（0 尺寸节点克隆出来也看不见）；
        ///   ③ 有可改的 `Text`/TMP —— 我们要往上面写「AI 对话」；
        ///   ④ 若有 `Image`，必须 enabled 且 alpha 不透明（透明热区克隆出来就是个隐形按钮）。
        /// ③ 是本事故的直接判据：`G:btnNext` 无文本组件，克隆出来连字都写不上。
        /// </summary>
        private static bool IsUsableTemplate(Button b)
        {
            if (b == null) return false;
            GameObject go;
            try { go = b.gameObject; } catch { return false; }
            if (go == null || !go.activeInHierarchy) return false;
            try
            {
                var rt = go.GetComponent<RectTransform>();
                if (rt == null || rt.sizeDelta.x < 1f || rt.sizeDelta.y < 1f) return false;
            }
            catch { }
            if (!HasLabel(go)) return false;
            try
            {
                var img = go.GetComponent<Image>();
                if (img != null && (!img.enabled || img.color.a < 0.05f)) return false;
            }
            catch { }
            return true;
        }

        /// <summary>克隆体上有没有可改的文本（`Text` 或 TMP 任一）。与 `SetLabel` 同判据。</summary>
        private static bool HasLabel(GameObject go)
        {
            try { if (go.GetComponentInChildren<Text>(true) != null) return true; } catch { }
            try { if (go.GetComponentInChildren<TMPro.TextMeshProUGUI>(true) != null) return true; } catch { }
            return false;
        }

        /// <summary>把剧情窗内**所有** Button 连同真名/激活/有无文本/透明/尺寸打成一行。
        /// 仅在"按名落空"或"找不到模板"时打（量很小）。09-13 事故就是缺这份清单、只能靠猜；
        /// 打出来一眼就能看到真名带不带 `G:` 前缀。</summary>
        private static void DumpButtons(Transform root, int dramaId)
        {
            try
            {
                var sb = new System.Text.StringBuilder("[DramaAiOption] 剧情窗 Button 清单 (dramaId=")
                         .Append(dramaId).Append(")：");
                int n = CollectButtons(root, sb, 0);
                if (n == 0) sb.Append("（无）");
                ModMain.P(sb.ToString());
            }
            catch { }
        }

        private static int CollectButtons(Transform t, System.Text.StringBuilder sb, int found)
        {
            if (t == null || found >= MaxDumpButtons) return found;
            Button b = null;
            try { b = t.GetComponent<Button>(); } catch { }
            if (b != null)
            {
                if (found > 0) sb.Append(" ｜ ");
                sb.Append(t.name).Append("[");
                try { sb.Append(t.gameObject.activeSelf ? "自活" : "自关"); } catch { }
                try { sb.Append(t.gameObject.activeInHierarchy ? "/链活" : "/链关"); } catch { }
                sb.Append(HasLabel(t.gameObject) ? "/有字" : "/无字");
                try
                {
                    var img = t.GetComponent<Image>();
                    if (img != null) sb.Append("/a=").Append(img.color.a.ToString("0.00"));
                }
                catch { }
                try
                {
                    var r = t.GetComponent<RectTransform>();
                    if (r != null) sb.Append("/").Append((int)r.sizeDelta.x).Append("x").Append((int)r.sizeDelta.y);
                }
                catch { }
                sb.Append("]");
                found++;
            }
            int n = t.childCount;
            for (int i = 0; i < n; i++)
            {
                found = CollectButtons(t.GetChild(i), sb, found);
                if (found >= MaxDumpButtons) return found;
            }
            return found;
        }

        /// <summary>点击「AI 对话」：打开该 NPC 的 AI 对话面板 + 把本页成品句发给 Python。主线程回调。</summary>
        private static void Trigger()
        {
            try
            {
                var unit = _pendingUnit;
                if (unit == null)
                {
                    ModMain.P("[DramaAiOption] 无待处理剧情，忽略点击");
                    return;
                }
                string name = SafeName(unit);
                if (string.IsNullOrEmpty(name))
                {
                    ModMain.P("[DramaAiOption] NPC 名解析失败，忽略点击");
                    return;
                }
                if (!ChatLauncher.OpenForUnit(unit))
                {
                    ModMain.P("[DramaAiOption] 打开对话面板失败（AB/代码版均未就绪）");
                    return;
                }
                var ws = ChatGlobals.WsClientInstance;
                if (ws == null)
                {
                    ModMain.P("[DramaAiOption] WsClient 未就绪");
                    return;
                }
                // 谁在说：读**本页那一行配置**的 speaker 列（点击这一刻读，与发出去了什么是同一时刻）
                string speaker = DetectSpeaker(_pendingUi, _pendingData, _pendingUi != null ? _pendingUi.transform : null, out string sdiag);
                ModMain.P("[DramaAiOption] 说话人判定（点击时）：" + sdiag);
                // ★ 这一刻的判定才是**权威的**：页事件发生在 `InitData`，而立绘压暗是 UI 侧逐页刷的，
                //   "刚回来的那一页"读到的可能是上一页的明暗 ⇒ 页时刻的归属有反的可能。
                //   先把它写回段里当前这一条，再取增量 —— 顺序不能颠倒（增量是写回后的快照）。
                NoteSegmentClick(unit, speaker);
                // ★ 分段器：投**这一段里还没投过**的那几句（一次当面相遇可能翻了好几页）。
                //   三种结果，语义各不相同：
                //     · null      = 分段器没有这个人的段 ⇒ 退回旧的"只投屏幕上这一句"；
                //     · 空表      = 有段但没有新增（反复点同一页 / 这是我方工具链的段）
                //                   ⇒ **只开面板，不重复投递**（重投会让模型以为又发生了一次）；
                //     · 有内容    = 投这一批增量（投完不清空：下次点只补新增的）。
                JArray lines = TakeSegmentDelta(unit);
                if (lines != null && lines.Count == 0)
                {
                    // 10-25 诊断补一句"战场态"：这条静默返回正是用户报"点了没反应"的现场，
                    // 排障时要能同时看出"当时是不是正好在战前/战中"（那会让段里只剩不投的行）。
                    ModMain.P("[DramaAiOption] AI 对话已发起（本段无新增内容，不重复投递）：" + name
                              + " dramaId=" + _pendingDramaId
                              + " 战场态=" + BattleFlowGate.Describe());
                    return;
                }
                // 单页支路要发的文本：正常就是本页成品句；万一本页取文本失败（ExtractText 两条路
                // 都落空），退回段里最后那句非空的 —— 否则单句包装会退化成"只有舞台指令"。
                string wireText = _pendingText ?? "";
                if (wireText.Length == 0 && lines != null && lines.Count > 0)
                {
                    JToken lastText = lines[lines.Count - 1]["text"];
                    wireText = lastText != null ? (string)lastText : "";
                }
                // busy = 链在收尾（见 DramaGate.ChainHold）：这一轮只许说话 + 只读，
                // 免得在"处置页之后还有尾页"的那十几秒里插进一个新动作（与正在演出的链并行）。
                // action = 游戏里"某个行动还在进行"（09-23）：论道/邀约这类交互期间点「对话」，
                // 模型看不到"仍在进行"的状态，会把已发生的交互当没开始、再去发起一遍同款工具。
                // 判据见 `NpcInitiativeMonitor.ActionActive`（**存在性**，不看层顶——页开着时它被盖住）。
                bool actionActive = false;
                try { actionActive = NpcInitiativeMonitor.ActionActive(); } catch { }
                // verdict = 当前这一页是不是**战斗处置页**（09-24 用户需求，甲案）：
                // 判据**只看页号**（20403 送东西求饶 / 20404 任凭处置，配表事实）——
                // 处置页当场收口时台账已被清（`IsVerdictPage` 要求"攻击台账在飞"⇒ 此刻恒 false），
                // 故走只读口 `IsVerdictDramaId`（同一个白名单，不在这里抄第二份数字）。
                // 命中 ⇒ Python 追加"别调工具去执行页面上的献物/求饶、只限这一页"的说明。
                // **只认"当前页就是处置页"**：翻到后页再点不带（甲案；那一轮里处置页的选项/
                // 台词本来就会随段增量投进去，模型看得见发生了什么）。
                bool verdictPage = false;
                try { verdictPage = BattleOutcome.IsVerdictDramaId(_pendingDramaId); } catch { }
                // unitId：本页归属 NPC 的精确编号（点击这一刻 `unit` 就是真身）。
                // 剧情窗这条路的回合同样**不经过** `open_chat` ⇒ 不给这个键，Python 侧
                // `agent.unit_id` 就是空的，工具调用只能按名猜（09-28 主动传音同类故障）。
                ws.SendGameDrama(name, wireText, _pendingDramaId, speaker, lines, DramaGate.ChainHold,
                                 actionActive, verdictPage, DramaNativeClaim.UidOf(unit));
                ModMain.P("[DramaAiOption] AI 对话已发起：" + name + " dramaId=" + _pendingDramaId
                          + " speaker=" + (speaker.Length == 0 ? "(未知)" : speaker)
                          + " 本批=" + (lines == null ? "单页（分段器无此段）" : lines.Count + " 句")
                          + " 动作进行中=" + (actionActive ? "是" : "否")
                          + " 处置页=" + (verdictPage ? "是（已附说明）" : "否")
                          // 10-25 诊断：点「对话」这一刻的战场状态。真机排障要能一眼回答
                          // "当时系统认为在战前/战中吗" —— 若这里是 Idle 而外面正在打，
                          // 说明某个入口没被点亮（据此判断该补哪一闸）。
                          + " 战场态=" + BattleFlowGate.Describe()
                          + " text=" + wireText);
            }
            catch (Exception e)
            {
                ModMain.P("[DramaAiOption] trigger: " + e);
            }
        }

        // ------------------------------------------------------------------
        // 剧情相遇分段器：本类的接线（判据在 drama/DramaSegmentCore，采样在 drama/DramaSegmentRecorder）
        //
        // 为什么要它（用户需求）：原来点「对话」只把**屏幕上此刻那一句**交给模型 —— 一次当面相遇
        // 往往翻了好几页（切磋整条链是 21401/21402 那样的多页），先发生的事模型看不见，只能看到
        // 最后一句；玩家打完仗再点，前面那几句已经随页翻过去了。
        // 分段器把"一页一页"攒成"一段"（每句带说话人 + 顺序号），点击时整段投给模型，并且
        // **投过的不再投**（`delivered` 游标）—— 反复点只补新增的，不重不漏。
        //
        // 本类只做三件事（判据一律不在这里重写）：
        //   ① 每页调 `NoteSegment` —— 把"谁在说 / 谁 / 哪句"交给分段器（用本类现成的判据）；
        //   ② 点击时调 `TakeSegmentDelta` —— 取增量（null = 没有段 ⇒ 退回旧的单页注入）；
        //   ③ 闸 1 / 闸 1.5 挡下的页**照记不投**（`noInject`）—— 那些内容已当 tool result 给过模型。
        // ------------------------------------------------------------------

        /// <summary>
        /// 把本页交给分段器（`noInject=true` = 照记但不投，用于我方工具链的页）。
        /// </summary>
        /// <param name="speakerOverride">已知的说话人判定结果（"npc"/"player"/""）。
        /// null = 本页还没判过，这里现判一次；**非 null 时绝不重算** —— 同一页两处判据
        /// 各判各的，迟早漂移（真机已踩过"看着像实测、其实是两套"的坑）。</param>
        /// <param name="textOverride">已知的成品句（正常路径传 `_pendingText`，省一次
        /// `ExtractText` 且与即将发出去的那一句**必然同源**）；null = 这里现取一次。</param>
        private static void NoteSegment(UIDramaBase ui, int id, DramaData data, WorldUnitBase npc,
                                        bool noInject, string speakerOverride = null, string textOverride = null)
        {
            try
            {
                if (npc == null) return;
                _lastDramaUnit = npc;          // 屏幕上的"对方"快照（见字段注释；判不出 uid 也要记）
                string uid = DramaNativeClaim.UidOf(npc);
                if (string.IsNullOrEmpty(uid))
                {
                    // 取不到精确编号 ⇒ 喂不进分段器（分段器认人不认名字，重名会张冠李戴），
                    // 但**不静默**：这会让"点了只投一句"，排障时一眼看出是哪一页。
                    ModMain.P("[DramaAiOption] 分段器：本页取不到对方 unitID ⇒ 不记 (dramaId=" + id
                              + ", npc=" + SafeName(npc) + ")");
                    return;
                }
                string speaker = speakerOverride
                    ?? DetectSpeaker(ui, data, ui != null ? ui.transform : null, out _, npc);
                DramaSegmentRecorder.NotePage(uid, id, speaker, SpeakerSideName(speaker, npc),
                                              textOverride ?? ExtractText(), noInject);
            }
            catch (Exception e)
            {
                ModMain.P("[DramaAiOption] 分段器记页失败: " + e.Message);
            }
        }

        /// <summary>
        /// 只有 uid 的页（**无归属页借链目标** / 闸1 无归属页）交给分段器：
        /// 归属来自"当前动作链"，没有 unit 对象 ⇒ 名字列留空（**判不出就不猜**，别挂一个错的名）。
        /// </summary>
        private static void NoteSegmentByUid(UIDramaBase ui, int id, DramaData data, string uid,
                                            bool noInject, string speakerOverride = null,
                                            string textOverride = null)
        {
            try
            {
                if (string.IsNullOrEmpty(uid)) return;
                string speaker = speakerOverride
                    ?? DetectSpeaker(ui, data, ui != null ? ui.transform : null, out _, null);
                DramaSegmentRecorder.NotePage(uid, id, speaker, "", textOverride ?? ExtractText(), noInject);
            }
            catch (Exception e)
            {
                ModMain.P("[DramaAiOption] 分段器记页失败（uid）: " + e.Message);
            }
        }

        /// <summary>
        /// 点击那一刻重判的归属写回段里（只动当前页那一条）。
        /// 判不出时核里会**拒绝覆盖**（不把已知降级为未知），所以这里照传即可。
        /// </summary>
        private static void NoteSegmentClick(WorldUnitBase unit, string speaker)
        {
            try
            {
                if (unit == null || string.IsNullOrEmpty(speaker)) return;
                string uid = DramaNativeClaim.UidOf(unit);
                if (string.IsNullOrEmpty(uid)) return;
                DramaSegmentRecorder.NoteClickSpeaker(uid, _pendingDramaId, speaker,
                                                      SpeakerSideName(speaker, unit));
            }
            catch (Exception e)
            {
                ModMain.P("[DramaAiOption] 点击重判写回失败: " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // 选项监听（09-23 新增）：把"玩家选了什么"记进段
        //
        // 为什么要它（用户报的割裂）：页面正文（`GetDialogueText`）**不含选项** —— 论道/邀约这类
        // 问答式交互里，只投正文会让模型"只知道疑问、不知道回答"，甚至把已完成/进行中的交互
        // 当成"还没开始"（实测：论道页上点「对话」，模型转头又发起一次论道工具）。
        //
        // 怎么拿到"选项文本"：**从选项按钮上读**（屏幕上的就是权威，与"成品句"同一条哲学）。
        // 挂点选 `UIDramaBase.CreateOptions` postfix（选项是在那里建出来的）：
        //   ① 参数里含"选项容器"，遍历它下面的每个按钮，`ClickUtils.Append` **追加**一个
        //      我们自己的监听（**不动游戏原回调**——用 AddListener 而非替换 onClick）；
        //   ② 玩家点按钮时，我们的监听拿到**那枚按钮上的文本**（= 玩家的选择），
        //      连同"当前页的对方/页号"交给分段器记一行。
        // 判重：已挂按钮的 IL2CPP 指针记在 `_boundOptionPtr`（自定义 MonoBehaviour 需要
        // `ClassInjector` 注册，本仓有过注册缺失的整片事故，不引新类型）。
        // 排除我们自己注入的「对话」按钮（否则点它会被误记成"玩家选择"）。
        // 全程 try/catch：失败只打日志，绝不影响游戏点击链。
        // ------------------------------------------------------------------

        /// <summary>已挂上报监听的选项按钮（IL2CPP 指针）。防同一按钮被多次 CreateOptions 叠加监听。</summary>
        private static readonly HashSet<IntPtr> _boundOptionPtr = new HashSet<IntPtr>();

        /// <summary>一次 `CreateOptions` 的扫描统计（**只用于诊断日志**）。
        /// 为什么要有：0 枚挂载是这个功能唯一的"静默失效"形态（组件类型 / 文本位置 / postfix 参数
        /// 任一处猜错都落到这里），而它原来一行日志都不打 —— 09-23 排查正是卡在"没有日志"上。</summary>
        private sealed class BindStat
        {
            public int Buttons;        // 扫到的 Button 总数
            public int Labeled;        // 其中能读到文本的
            public int InactiveHit;    // 文本靠"退含非激活子节点"才读到的
            public int Skipped;        // 已在 _boundOptionPtr 里（判重跳过）
            public int Bound;          // 本次新挂
            public readonly List<string> Labels = new List<string>();
        }

        /// <summary>`UIDramaBase.CreateOptions(GameObject, GameObject)` postfix 调。
        /// 两个参数里含选项容器（另一个可能是模板/父级）——**两个都试**，挂上算数。
        ///
        /// 无论挂几枚**都出声**（09-23 探针）：这几行数字把"静默失效"定位到具体一步 ——
        /// `Button=0` ⇒ postfix 参数不是选项容器（或选项按钮不是 `Button`）；
        /// `Button>0` 而 `有文本=0` ⇒ 文本不在按钮子树里（`ReadButtonLabel` 要扩到父级/兄弟）。</summary>
        internal static void NoteOptionsCreated(GameObject p0, GameObject p1)
        {
            try
            {
                BindStat st = new BindStat();
                BindOptionButtons(p0, st);
                BindOptionButtons(p1, st);
                ModMain.P("[DramaAiOption] 选项监听（CreateOptions）：p0=" + NodeName(p0) + " p1=" + NodeName(p1)
                          + "｜Button=" + st.Buttons + " 有文本=" + st.Labeled
                          + "（非激活兜底=" + st.InactiveHit + "）已绑跳过=" + st.Skipped + " 新挂=" + st.Bound);
                if (st.Bound > 0)
                    ModMain.P("[DramaAiOption] 选项按钮已挂 " + st.Bound + " 枚：" + string.Join(" ｜ ", st.Labels));
                if (st.Buttons == 0)
                    ModMain.P("[DramaAiOption] ⚠ 两个参数里都没扫到 Button —— postfix 参数不是选项容器，"
                              + "或选项按钮不是 UnityEngine.UI.Button ⇒ 选项文本完全采不到");
                else if (st.Labeled == 0)
                    ModMain.P("[DramaAiOption] ⚠ 扫到 " + st.Buttons + " 枚 Button 但都读不到文本 —— "
                              + "选项文本不在按钮子树里 ⇒ 监听永远挂不上");
            }
            catch (Exception e)
            {
                ModMain.P("[DramaAiOption] 选项监听挂载失败: " + e.Message);
            }
        }

        /// <summary>节点名（日志用；任何异常都不许漏出去）。</summary>
        private static string NodeName(GameObject go)
        {
            try { return go == null ? "(null)" : go.name; } catch { return "(?)"; }
        }

        /// <summary>给容器下（含根）每个"有文本的按钮"追加一个点击上报监听；统计进 `st`。</summary>
        private static void BindOptionButtons(GameObject root, BindStat st)
        {
            if (root == null) return;
            if (root.name == ButtonName) return;              // 我们自己注入的「对话」按钮
            try
            {
                var btn = root.GetComponent<Button>();
                if (btn != null)
                {
                    st.Buttons++;
                    bool viaInactive;
                    string label = ReadButtonLabel(root, out viaInactive);
                    if (!string.IsNullOrEmpty(label)) st.Labeled++;
                    if (viaInactive) st.InactiveHit++;
                    if (!string.IsNullOrEmpty(label))            // 无文本（翻页图标等）不挂
                    {
                        // ★不判重、每次都挂★（09-23 实机翻车修正）：原先按 `_boundOptionPtr`（IL2CPP 指针）
                        //   判"已绑"就跳过 —— 实测同一页第二次弹出时 `已绑跳过=5 新挂=2`，被跳过那 5 枚
                        //   在页面重建后**没有我们的回调**（游戏重建/复用按钮时回调没了），玩家点它们
                        //   **连"选项点击"日志都没有**（811013 / 811111 / 811221 三次选择静默丢失）。
                        //   现在一律重挂；重复挂载由 `OnOptionTapped` 的**同文本去抖**兜住。
                        //   `_boundOptionPtr` 只留作诊断（"这颗指针见过没有"），不再决定挂不挂。
                        if (_boundOptionPtr.Contains(btn.Pointer)) st.Skipped++;  // 诊断：指针见过（复用/重复调用）
                        else _boundOptionPtr.Add(btn.Pointer);
                        // 闭包只捕按钮本身：文本留到**点击那一刻**再读（见 `OnOptionFromButton`）——
                        // 按钮会被对象池复用，绑定时捕获的旧文本会把"这次点的"记成"上次那个选项"。
                        ClickUtils.Append(btn, () => OnOptionFromButton(btn));
                        st.Bound++;
                        st.Labels.Add(label);
                    }
                }
            }
            catch { }
            int c = root.transform.childCount;
            for (int i = 0; i < c; i++) BindOptionButtons(root.transform.GetChild(i).gameObject, st);
        }

        /// <summary>按钮上的显示文本。**两级读法**（09-23 探针修正）：
        ///   ① 先找**激活**子节点里的（屏幕上显示的就是它，正常情况走这条）；
        ///   ② 再退**含非激活**子节点（`includeInactive: true`）。
        /// 为什么必须有第②级：同文件 `HasLabel`（"剧情窗 Button 清单"里那个"有字"判据）就是带 `(true)`
        /// 读到的 —— 若这里不带，而文本恰在非激活节点里，就会读到空串 ⇒ **静默不挂监听**。
        /// `viaInactive` 回报是否走了第②级（探针日志里的"非激活兜底"计数）。
        /// 引擎两类都试（legacy `Text` 优先、TMP 兜底）。</summary>
        private static string ReadButtonLabel(GameObject go, out bool viaInactive)
        {
            viaInactive = false;
            try
            {
                var t = go.GetComponentInChildren<Text>();
                if (t != null && !string.IsNullOrEmpty(t.text)) return t.text.Trim();
            }
            catch { }
            try
            {
                var p = go.GetComponentInChildren<TMPro.TextMeshProUGUI>();
                if (p != null && !string.IsNullOrEmpty(p.text)) return p.text.Trim();
            }
            catch { }
            try
            {
                var t = go.GetComponentInChildren<Text>(true);
                if (t != null && !string.IsNullOrEmpty(t.text)) { viaInactive = true; return t.text.Trim(); }
            }
            catch { }
            try
            {
                var p = go.GetComponentInChildren<TMPro.TextMeshProUGUI>(true);
                if (p != null && !string.IsNullOrEmpty(p.text)) { viaInactive = true; return p.text.Trim(); }
            }
            catch { }
            return "";
        }

        /// <summary>最近一次上报的选项文本与时刻（**同文本去抖**用，见 `OnOptionTapped`）。</summary>
        private static string _lastTapLabel = "";
        private static float _lastTapAt = -99f;

        /// <summary>我们的选项监听入口：**点击那一刻从按钮上读当前文本**，再交给 `OnOptionTapped`。
        ///
        /// 为什么不在这里闭包捕获文本（09-23 修正）：选项按钮走对象池，同一实例会被下一页复用 ——
        /// 闭包里的旧文本会把"这次点的选项"记成"上一次那个词"。实时读文本则永远是玩家看到的那个词。</summary>
        private static void OnOptionFromButton(Button btn)
        {
            try
            {
                if (btn == null) return;
                string label = ReadButtonLabel(btn.gameObject, out _);
                OnOptionTapped(label);
            }
            catch { }   // 按钮可能已被销毁（MissingReference 等）——点击链不许被打断
        }

        /// <summary>玩家点了某个选项（我们的监听）：把那枚按钮的文本记进**本页归属的段**。
        ///
        /// 时序说明：我们的监听是 Append（在游戏监听之后跑），所以读的是"点击那一刻"的
        /// `_pendingUid`/`_pendingDramaId`。归属的**唯一依据是 `_pendingUid`**（可能没有 unit 对象：
        /// 无归属页借链目标那种），空串 = 这一击没有归属 ⇒ 丢弃（判不出就闭嘴）。
        /// 选项行**不受正文"不投"影响**（09-23 用户定：选项必须投），只有"被工具结果读走"才不再投。</summary>
        private static void OnOptionTapped(string label)
        {
            try
            {
                // 同文本去抖：重复挂载 / 按钮复用会让一次点击触发多个监听（每个都读一次当前文本）——
                // 同一文本在极短窗内只算一次。0.35s 够宽（人手连点也不会快过它）。
                float nowT = UnityEngine.Time.realtimeSinceStartup;
                if (label == _lastTapLabel && nowT - _lastTapAt < 0.35f) return;
                _lastTapLabel = label;
                _lastTapAt = nowT;

                string uid = _pendingUid ?? "";
                if (string.IsNullOrEmpty(uid))
                {
                    // 本页没有归属的点击：**不入段**（判不出就闭嘴，段那边原样保留），
                    // 但**入账本**（10-25）：账本是全量流水，"谁选了哪个"本身就是事实。
                    //   真机里这一类最要命的一笔正是「进入副本」那页上的选项（`610911`「深入探索」）——
                    //   它发生在队友进场之前，也正是"怎么进的这个副本"的唯一线索。
                    ModMain.P("[DramaAiOption] 选项点击：本页没有归属（无待处理剧情），忽略入段但记入账本（"
                              + label + "）");
                    try
                    {
                        string pn0 = SafeName(SafeGet(() => g.world.playerUnit)) ?? "";
                        DramaLedger.NoteOption("", _pendingDramaId, label, pn0);
                    }
                    catch { }
                    return;
                }
                var unit = _pendingUnit;   // 可能为 null（归属页借的链目标）—— 只用于日志
                // 探针（09-23）：点击那一刻的上下文 —— 对账"这一行归到哪一段、哪一页"。
                // 归属全压在 `_pending*` 上（我们的 Append 监听在游戏回调之后最后跑），所以这一行
                // 必须能看到它的值；若这里打出的页号/对方与玩家所见不符 ⇒ Append 顺序假设不成立。
                ModMain.P("[DramaAiOption] 选项点击：\"" + label + "\"｜页=" + _pendingDramaId
                          + " 对方=" + (unit != null ? SafeName(unit) + "(" + uid + ")" : uid));
                string pname = SafeName(SafeGet(() => g.world.playerUnit)) ?? "";
                DramaSegmentRecorder.NoteOption(uid, _pendingDramaId, label, pname);
            }
            catch (Exception e)
            {
                ModMain.P("[DramaAiOption] 选项上报失败: " + e.Message);
            }
        }

        /// <summary>
        /// 说话人那一侧的显示名：`player` → 玩家名，`npc` → 这个 NPC 的名，判不出 → 空串。
        ///
        /// **必须按说话人取**：把 NPC 的名挂在玩家那句话上，Python 会渲染成
        /// "玩家（方成和）：…" —— 与"把归属判反"是同一个反事实，只是换了个字段。
        /// 判不出就留空：Python 只写"你/玩家"，不猜。
        /// </summary>
        private static string SpeakerSideName(string speaker, WorldUnitBase npc)
        {
            if (speaker == "player") return SafeName(SafeGet(() => g.world.playerUnit)) ?? "";
            if (speaker == "npc") return SafeName(npc) ?? "";
            return "";
        }

        /// <summary>
        /// 取本段"还没投过"的增量 → JArray（发给 Python 的 `lines`）。
        /// **null = 分段器没有这个人的段** ⇒ 调用方退回旧的单页注入（入口永不哑掉）；
        /// 空 JArray = 有段但无新增 ⇒ 调用方**不发事件**（只开面板）。
        /// </summary>
        private static JArray TakeSegmentDelta(WorldUnitBase unit)
        {
            try
            {
                string uid = DramaNativeClaim.UidOf(unit);
                if (string.IsNullOrEmpty(uid)) return null;
                List<SegmentLine> delta;
                if (!DramaSegmentRecorder.TryTakeDelta(uid, out delta)) return null;
                JArray arr = new JArray();
                for (int i = 0; i < delta.Count; i++)
                {
                    SegmentLine l = delta[i];
                    arr.Add(new JObject
                    {
                        ["seq"] = l.Seq,
                        ["kind"] = string.IsNullOrEmpty(l.Kind) ? "page" : l.Kind,
                        ["drama_id"] = l.DramaId,
                        ["speaker"] = l.Speaker ?? "",
                        ["name"] = l.Name ?? "",
                        ["text"] = l.Text ?? "",
                    });
                }
                return arr;
            }
            catch (Exception e)
            {
                ModMain.P("[DramaAiOption] 取分段增量失败（退回单页注入）: " + e.Message);
                return null;
            }
        }

        /// <summary>剧情参与者里找「非玩家的 NPC」：unitLeft/unitRight/unit（types44：多种形态并存）。
        ///
        /// 玩家判据用 UnitSnapshot.IsPlayerUnit（unitID 主判据）——09-12 实锤：旧的
        /// `ReferenceEquals(u, player)` 在 IL2CPP 下对 DramaData 里的包装器不成立，
        /// 于是主角自己的剧情窗被当成了"NPC 剧情窗"，日志出现
        /// `AI 应对按钮已注入 (dramaId=-803158281, npc=缪嘉歆)` —— 缪嘉歆正是玩家真名，
        /// 点下去就是"自己和自己对话"。</summary>
        private static WorldUnitBase FindNpcOf(DramaData data, int dramaId)
        {
            if (data == null) return null;
            var a = SafeGet(() => data.unitLeft);
            var b = SafeGet(() => data.unitRight);
            var c = SafeGet(() => data.unit);
            bool sawPlayer = false;
            foreach (var u in new[] { a, b, c })
            {
                if (u == null) continue;
                if (UnitSnapshot.IsPlayerUnit(u)) { sawPlayer = true; continue; }
                if (!string.IsNullOrEmpty(SafeName(u))) return u;
            }
            if (sawPlayer)
                ModMain.P("[DramaAiOption] 剧情参与者只有玩家本人，跳过注入 (dramaId=" + dramaId + ")");
            return null;
        }

        /// <summary>
        /// 剧情原文 = **本页成品句**（玩家屏幕上看到的那句）。
        ///
        /// ① 首选 `DramaTextCapture.LastUiText` —— `UIDramaBase.GetDialogueText` 的返回值，
        ///    由 `UI/DramaTextHook.cs` 无条件缓存。**这是唯一没被证伪的数据源**：
        ///    游戏已把 `{call|B|A}` / `{0}` 之类占位符填好，零推断。
        /// ② 兜底才用 `DramaTool.lastOpenDramaDialogueText` —— 09-12 真机两次实测确认它是
        ///    **未替换的模板**（含 `{0}`）；闲聊台词 `dialogRoleChat1` 的 1050 条变体里 **649 条**
        ///    带 `{…}` 占位符，走这条会把 `{call|B|A},近来过得可好？` 原样发给模型。聊胜于无，故仅兜底。
        /// ③ **`@` 标记必须解成人话**：`GetDialogueText` 返回的是**带标记的编码文本**，
        ///    真机原样样例 `我这里有一个@w_rIsZH3|1|5031101|48|@准备赠于你`、
        ///    `（看起来@q_郦安|Lepxop@想和我说些什么。）` —— 直接发给模型，它读到的是 hash 乱码，
        ///    不知道送的是青须藤、也认不出是谁。复用**世界日志那条路已有的解码器**
        ///    `UnitSnapshot.CleanLogText`（正则 `@([a-zA-Z])_([^@]*)@` → 逐字段试解：纯数字字段查
        ///    `ItemProps` 表过 `GameTool.LS` 取中文名，字段0 当 unitID 查单位名，都不行退回字段0
        ///    并留一行 `@标记未解`）。该解码器当初**就是拿上面那句剧情原文当样本写的**，
        ///    只是从未挂到剧情这条路。
        /// 都取不到就空串——Python 侧有纯舞台指令兜底，不阻断。
        /// </summary>
        private static string ExtractText()
        {
            try
            {
                var t = DramaTextCapture.LastUiText;
                if (!string.IsNullOrEmpty(t)) return UnitSnapshot.CleanLogText(t);
            }
            catch { }
            try
            {
                var t = DramaTool.lastOpenDramaDialogueText;
                if (!string.IsNullOrEmpty(t)) return UnitSnapshot.CleanLogText(t);
            }
            catch { }
            return "";
        }

        private static WorldUnitBase SafeGet(Func<WorldUnitBase> f)
        {
            try { return f(); } catch { return null; }
        }

        private static string SafeName(WorldUnitBase u)
        {
            try { return u.data.unitData.propertyData.GetName(); } catch { return null; }
        }

        /// <summary>按名深搜（IL2CPP：索引循环遍历子节点，禁 foreach(Transform)）。
        /// **容忍 `G:` 前缀**：本作 UI 节点真名带前缀，字段名不带 —— 只按字段名找会全 miss。</summary>
        // ------------------------------------------------------------------
        // 「这一页是谁在说」——**游戏配置表自带这一列**（第四轮定案）
        // ------------------------------------------------------------------
        //
        // 为什么要它：同一枚「AI 对话」按钮既服务「NPC 主动找上门」（过月寻仇 / 邀约 / 赠送），
        // 也服务「玩家自己点的闲聊 / 宗门 / 好友」。而屏幕句本身**不含说话人**——
        // 「我这里有一个青须藤*48准备赠于你，你需要此物吗？」既可能是 NPC 说的，也可能是玩家说的；
        // 早先的中性文案把归属权丢给模型猜，真机就猜反了：NPC 送礼那一页被当成"玩家送我"，
        // NPC 开口道谢（`AI 对话已发起：惠都 dramaId=21701 text=我这里有一个@w_…@准备赠于你…`）。
        //
        // **判据 = `ConfDramaDialogueItem.speaker`，游戏配置表里就有这一列。**
        // 权威性来自游戏**自带的表头注释**（`Mod/modFQA/配置修改教程/配置表头/DramaDialogue.xlsx`，
        // 第 7 列中文名「说话者」，紧邻注释 `1-左` `2-右`）；数据侧独立佐证：
        //   全部 21926 行里 `speaker` 取值分布 `2:13825 / 0:4589 / 1:3476 / -1:34 / 12:2`，
        //   其中 **115 行 `npcLeft=0 且 npcRight≠0 → speaker=2`**（左边没人，只可能是右边在说），
        //   而 2602 行 `两侧都没人 → speaker=0`。
        //   **`1`/`2` 与左右两侧一一对应，不靠猜。**
        //
        // 拿到侧别后**现算**是谁：`1 → data.unitLeft`、`2 → data.unitRight`，
        // 再问 `UnitSnapshot.IsPlayerUnit(该单位)` → "player"/"npc"。
        // 这样**两个 NPC 对话的剧情也判得对**（speaker=1 时左侧是别的 NPC，不是玩家），
        // 而不是硬编码"左=玩家"。
        //
        // 被换掉的两条路（**勿回退**）：
        //   ① `imgBgPlayerDark`/`imgBgOtherDark` 具名遮罩 —— 真机实测节点不存在
        //      （`玩家遮罩=0.00(0处) 对方遮罩=0.00(0处)`），是从 `#Strings` 堆的相邻关系**推断**出来的，
        //      而堆里相邻 ≠ 同类。
        //   ② `UIDramaDialogue.Play(WorldUnitBase, Action)` 的 unit 参数 —— 方法确实存在
        //      （`NativeMethodInfoPtr_Play_Public_Void_WorldUnitBase_Action_0`），钩子也挂上了
        //      （真机 `[剧情说话人] 钩子已挂：UIDramaDialogue.Play（2 参数）`），**但从头到尾没被调用过**
        //      （全程无 `Play 首次命中`，判定行始终是 `**Play 未命中→退立绘亮度**`）。
        //      **"方法存在"≠"方法被调用"**，与 ① 是同一类错误。
        //   留立的教训：先找"游戏是不是已经把答案写在某个字段/配置里了"，
        //   实在没有才去反推现象（用户 09-13 质问：「你是不知道UI的具体层级架构吗才这样，
        //   不能准确判断吗？」——不能，不该猜，该查）。
        //
        // 立绘亮度降级为**兜底**：配置给出 0/-1/12（无说话者或异常值）或取不到该侧单位时才用，
        // 日志里标明来源。兜底本身是启发式（半盲状态下的产物），不作首选。

        /// <summary>返回 "npc"=NPC 在说 / "player"=玩家在说 / ""=判不出；diag 供日志。
        ///
        /// ** 09-13 第七轮：`DramaData.unitLeft/unitRight` 这条路被真机证伪，已弃用 **
        /// 第六轮加的交叉校验当场抓到：
        ///   `⚠说话人判据冲突：配置级=npc 立绘=player（配置 speaker=1）｜左=许其[NPC] 右=许其[NPC]`
        /// —— `speaker=1(左)` 明明是**玩家在说**（立绘 `唐炎 亮=1.000 x=-3` 亮着，`许其 亮=0.300 x=+3` 被压暗），
        /// 而 `data.unitLeft` 返回的却是**那个 NPC**，于是判成 `npc`：**一个反的事实**。
        /// 根因：**`unitLeft` 与 `unitRight` 同时返回同一个单位**（真机 `｜左=许其 右=许其`、`｜左=蒋博明 右=蒋博明`、
        /// `｜左=魏盼香 右=魏盼香`，三例一致）。两侧是同一人 ⇒ `speaker=1` 与 `=2` 必然给出同一答案，
        /// 这个映射从根上就不成立。**教训：用错的数据源比启发式更危险**——启发式至少会"判不出就闭嘴"，
        /// 而错的数据源会**自信地**给出反事实。
        ///
        /// **保留下来的是配置里的侧别**：`speaker` 1=左 / 2=右 本身没错——它与立绘独立同答 **6/6**
        /// （speaker=1 ↔ 亮者在左；speaker=2 ↔ 亮者在右）。错的只是"侧别→人"这一步。
        ///
        /// **现在"侧别→人"改用名签文本**（`textLeftName`/`textRightName`，`UIDramaDialogueBase` 的 typed `Text`）：
        /// 哪一侧的名签写着玩家名，那一侧就是玩家。**只在"名签链活 + 有字 + 与玩家名或 NPC 名精确相等"时才采信**；
        /// 任何一步含糊（名签关着/空着/名字对不上）→ **立刻退立绘探针**，绝不在含糊时硬给一个答案。
        /// 立绘路实测 **6/6 正确**，是目前唯一被反复验证过的判据，所以它留着兜底兼交叉校验。
        /// </summary>
        private static string DetectSpeaker(UIDramaBase ui, DramaData data, Transform root,
                                            out string diag, WorldUnitBase npcOverride = null)
        {
            try
            {
                // 三样证据一次全打出来：① 两侧单位（已知不可信，留作对照）② 两侧名签文本 ③ 玩家名/NPC名
                string sides = "｜左=" + SideDesc(data, true) + " 右=" + SideDesc(data, false);
                int side = 0;
                try { side = DramaTextCapture.LastSpeakerSide; } catch { }
                var dlg = AsDialogueBase(ui);
                bool la, ra;
                string lt = NameTagText(dlg, true, out la);
                string rt = NameTagText(dlg, false, out ra);
                string pn = SafeName(SafeGet(() => g.world.playerUnit));
                string nn = SafeName(npcOverride ?? _pendingUnit);
                string tags = "｜名签 左=\"" + (lt ?? "") + "\"" + (la ? "[活]" : "[链OFF]")
                            + " 右=\"" + (rt ?? "") + "\"" + (ra ? "[活]" : "[链OFF]")
                            + " 玩家名=\"" + (pn ?? "?") + "\" NPC名=\"" + (nn ?? "?") + "\"";

                // ① 配置侧别 + 名签定玩家侧 = 数据级定人
                //    **推法**：已知"玩家在哪一侧"且已知"哪一侧在说" → 两侧相同就是玩家在说，否则 NPC。
                //    这比"拿说话侧的名签去比对玩家名/NPC名"更宽——只要玩家侧认得出来就够，
                //    不要求 NPC 名也取得（`_pendingUnit` 取不到名时仍然判得出）。
                string verdict = null, how = "";
                bool playerLeft = false;
                bool playerSideKnown = TryPlayerSide(dlg, out playerLeft);
                if (side == 1 || side == 2)
                {
                    if (playerSideKnown)
                    {
                        verdict = ((side == 1) == playerLeft) ? "player" : "npc";
                        how = "配置侧+名签定玩家侧(" + (playerLeft ? "左" : "右") + ")";
                    }
                    else
                    {
                        // 玩家侧认不出 → 退一步：拿说话侧的名签直接对 NPC 名
                        string onSide = side == 1 ? lt : rt;
                        bool act = side == 1 ? la : ra;
                        if (act && !string.IsNullOrEmpty(onSide) && !string.IsNullOrEmpty(nn) && onSide == nn)
                        { verdict = "npc"; how = "配置侧+名签(名签=NPC名)"; }
                    }
                }

                // ② 立绘亮度：**无条件当交叉校验跑**（每剧情页两次，不是每帧，开销可忽略）
                string probe = "窗根为空";
                string guess = root != null ? PortraitProbe(root, dlg, out probe) : "";

                if (verdict != null)
                {
                    if (guess.Length > 0 && guess != verdict)
                        ModMain.P("[DramaAiOption] ⚠说话人判据冲突：" + how + "=" + verdict + " 立绘=" + guess
                                  + "（配置 speaker=" + side + "）" + sides + tags + "｜立绘=" + probe);
                    diag = how + " → " + verdict + "（配置 speaker=" + side + "）" + sides + tags
                         + "｜交叉校验 立绘=" + (guess.Length == 0 ? "(判不出)" : guess);
                    return verdict;
                }

                // ③ 名签不足采信 → 立绘探针（真机 ；按世界 x 符号分左右，看得出说话人在哪一侧）
                diag = "配置 speaker=" + side + " 不足以定人（名签路未采信）" + sides + tags
                     + " → 退立绘亮度｜立绘=" + probe;
                return guess;
            }
            catch (Exception e)
            {
                diag = "判定异常: " + e.Message;
                return "";
            }
        }

        /// <summary>玩家在剧情窗的**哪一侧**（`left`=true 即左）。**唯一依据 = 名签文本**：
        /// 哪一侧的名签写着玩家名，玩家就在哪一侧。真机实测可用：
        /// `名签 左="唐炎"[活] 右="司空雨珍"[活] 玩家名="唐炎"`。
        ///
        /// **采信门槛与 `DetectSpeaker` 同款（硬）**：名签必须「整条父链活着 + 有字 + 精确等于玩家名」。
        /// 判不出返回 **false**，调用方**不得据此下任何结论**（这是把"未知"和"在左边"分开的关键）。
        ///
        /// ⚠ **不要退回 `DramaData.unitLeft`/`unitRight`** —— 真机已证这两个属性返回同一个单位。
        /// </summary>
        private static bool TryPlayerSide(UIDramaDialogueBase d, out bool left)
        {
            left = false;
            if (d == null) return false;
            try
            {
                string pn = SafeName(SafeGet(() => g.world.playerUnit));
                if (string.IsNullOrEmpty(pn)) return false;
                bool la, ra;
                string lt = NameTagText(d, true, out la);
                string rt = NameTagText(d, false, out ra);
                if (la && !string.IsNullOrEmpty(lt) && lt == pn) { left = true; return true; }
                if (ra && !string.IsNullOrEmpty(rt) && rt == pn) { left = false; return true; }
            }
            catch { }
            return false;
        }

        /// <summary>
        /// 本页画面上**「对方」是否在场**（名签在 = 人在）。09-23 实证，用于"没有对方就别注入按钮"。
        ///
        /// 症状（用户实拍）：邀约场景的选择页（dramaId 81121）只有**玩家**立绘与名签，NPC 那侧
        /// 名签是 `""[链OFF]`、立绘也判不出 —— 但 `DramaData` 里仍挂着对方单位，按钮照样长出来，
        /// 悬在空处；点了还会把"要和姜萌做什么…"这类界面话当台词投给模型。
        ///
        /// 判据（**只用名签**，不用立绘——立绘探针是启发式，名签是游戏自己写的字）：
        ///   · 任一侧名签「链活 + 有字 + 精确等于 NPC 名」 ⇒ **在场**（正常剧情页都走这条）；
        ///   · **玩家名签在场**（链活 + 有字 + 精确等于玩家名）而 **NPC 名签缺失（链关/空）** ⇒ **不在场**；
        ///   · 其余（名字取不到 / 名签两侧判不出） ⇒ **保持既有行为（注入）** —— 宁可多一枚按钮，不误杀正常页。
        ///
        /// 只决定**要不要按钮**，不决定记不记段（页内容照记，见调用点）。
        /// </summary>
        private static bool NpcTagPresent(UIDramaBase ui, WorldUnitBase npc)
        {
            try
            {
                var dlg = AsDialogueBase(ui);
                if (dlg == null) return true;              // 拿不到 typed 名签 ⇒ 判不出，保持既有行为
                bool la, ra;
                string lt = NameTagText(dlg, true, out la);
                string rt = NameTagText(dlg, false, out ra);
                string nn = SafeName(npc);
                if (!string.IsNullOrEmpty(nn))
                {
                    if (ra && !string.IsNullOrEmpty(rt) && rt == nn) return true;   // NPC 名签在 ⇒ 在场
                    if (la && !string.IsNullOrEmpty(lt) && lt == nn) return true;
                }
                string pn = SafeName(SafeGet(() => g.world.playerUnit));
                if (!string.IsNullOrEmpty(pn))
                {
                    bool playerLeft = la && !string.IsNullOrEmpty(lt) && lt == pn;
                    bool playerRight = ra && !string.IsNullOrEmpty(rt) && rt == pn;
                    // 玩家名签认得出来，而 NPC 那侧名签缺失（链关/空）⇒ 屏幕上只有玩家 ⇒ 对方不在场
                    if (playerLeft && (!ra || string.IsNullOrEmpty(rt))) return false;
                    if (playerRight && (!la || string.IsNullOrEmpty(lt))) return false;
                }
                return true;                               // 判不出 ⇒ 保持既有行为（注入）
            }
            catch { return true; }
        }

        /// <summary>某一侧名签的显示文本（`textLeftName`/`textRightName`，typed `Text`）。
        /// `active` = 该名签**整条父链**是否活着——链关着的那个多半是上一页残留或空位，其文本不可信，
        /// 所以调用方只在 `active` 为真时才采信（与 `IsUsableTemplate` 同一条教训：看 `activeInHierarchy`
        /// 而不是 `activeSelf`）。</summary>
        private static string NameTagText(UIDramaDialogueBase d, bool left, out bool active)
        {
            active = false;
            if (d == null) return null;
            try
            {
                var t = left ? d.textLeftName : d.textRightName;
                if (t == null) return null;
                active = t.gameObject.activeInHierarchy;
                return t.text;
            }
            catch { return null; }
        }

        /// <summary>把一侧摊开成 `名字[玩家|NPC]`；空/无名/取不到都如实说（别把"取不到"写成"没有人"）。
        /// ⚠ 仅供日志对照——真机已证 `unitLeft`/`unitRight` 会返回同一个单位，**不要拿它做判定**。</summary>
        private static string SideDesc(DramaData data, bool left)
        {
            try
            {
                var u = left ? data.unitLeft : data.unitRight;
                if (u == null) return "(空)";
                string n = SafeName(u);
                if (string.IsNullOrEmpty(n)) return "(无名)";
                return n + (UnitSnapshot.IsPlayerUnit(u) ? "[玩家]" : "[NPC]");
            }
            catch { return "(取不到)"; }
        }

        // ------------------------------------------------------------------
        // 立绘实测探针——替换掉被真机否掉的「具名压暗遮罩」路
        //
        // 事实基础：`UIDramaDialogue.UpdateUnit(UnityEngine.UI.RawImage rimage, GameObject rimgRoot,
        // GameObject rimgRootAnim, GameObject goName, Text textName, Button btnLook, GameObject goHero,
        // GameObject goPreHero, int id, bool isLeft)`（types44_drama.txt）——**立绘是 RawImage**，
        // 且该方法的形参把左右的根/名签/查看钮全暴露了。
        // 判据：玩家立绘被压暗、说话方立绘正常（用户观察，两张截图都成立）。
        // 压暗可能落在 **RawImage.color / CanvasGroup.alpha / 覆盖层 Image** 三处之一，
        // 所以三样都打；亮度按 Rec.601 加权算，左右按**世界坐标 x** 分（不依赖相机）。
        //
        // **这个探针只负责一件事：测出"哪一侧亮"（真机）。** "亮的是谁"另算：
        //   亮侧 + 玩家在哪一侧(名签实测)→ 相同则玩家在说。
        // ⚠ 09-13 第七轮之前这里是 `return leftIsSpeaker ? "player" : "npc"`，
        //   把 **左=玩家** 焊死在代码里——于是"侧别→人"这一步**从来没被测过**，
        //   日志却长得跟实测一样。现在依据是实测还是假设，`diag` 里**明说**。
        // ------------------------------------------------------------------

        private static string PortraitProbe(Transform root, UIDramaDialogueBase dlg, out string diag)
        {
            diag = "";
            try
            {
                var found = new System.Collections.Generic.List<string>();
                var bright = new System.Collections.Generic.List<float>();
                var xs = new System.Collections.Generic.List<float>();
                var alive = new System.Collections.Generic.List<bool>();
                ScanPortraits(root, found, bright, xs, alive, 0);
                if (found.Count == 0) { diag = "全窗找不到 RawImage（立绘不是 RawImage？）"; return ""; }

                var sb = new System.Text.StringBuilder();
                for (int i = 0; i < found.Count && i < 6; i++)
                {
                    if (i > 0) sb.Append(" ｜ ");
                    sb.Append(found[i]).Append(" 亮=").Append(bright[i].ToString("0.000"))
                      .Append(" x=").Append((int)xs[i]);
                }
                sb.Append(" ｜ 暗层=").Append(DarkOverlays(root));
                diag = sb.ToString();

                // 只认「活着」的立绘（真机）
                // 前两张 RawImage 是 `G:rimgLeft`/`G:rimgRight`——**父链未激活的空占位**，
                // 两张亮度都算 0.000，于是旧写法取前两张 → 恒判"两侧同档"。
                // 真立绘在它们之后，**以角色名命名**（`唐炎`/`郦安`），底色才是明暗所在
                // （听者 0.30 / 说者 1.00）。所以：先滤掉未激活的，再取**亮度差最大**的两张。
                // 按 x **分左右两组**各取组内最大亮度再比（复核修）
                // 旧写法"取亮度差最大的两张"在活立绘 ≥3 张时（大图窗 / hero / goPreHero / 装饰 RawImage），
                // 最亮-最暗那一对**可能是同一侧**的两张，于是给出一个**自信的错答案**——
                // 正是这套机制要消灭的"喂给模型一个反的事实"。x 恰为 0 的不进任何组，凑不齐就判不出。
                int li = -1, ri = -1;
                for (int i = 0; i < found.Count; i++)
                {
                    if (!alive[i]) continue;
                    if (xs[i] < 0f) { if (li < 0 || bright[i] > bright[li]) li = i; }
                    else if (xs[i] > 0f) { if (ri < 0 || bright[i] > bright[ri]) ri = i; }
                }
                if (li < 0 || ri < 0) { diag += " → 活立绘没凑齐左右各一张，判不出"; return ""; }
                float best = Mathf.Abs(bright[li] - bright[ri]);
                if (best < 0.05f) { diag += " → 左右明暗同档（差=" + best.ToString("0.00") + "），判不出"; return ""; }
                // "亮侧是谁"不再硬编码——
                // 旧代码是 `return leftIsSpeaker ? "player" : "npc"`，即把 **左=玩家** 焊死在里面。
                // 那条路**只独立测出"哪一侧亮"**，"亮的是谁"是套上去的前提，从未被测过。
                // 现在改成：亮侧（**实测**）+ 玩家在哪一侧（**名签实测**）→ 两者相同就是玩家在说。
                // 名签判不出玩家侧时（非对话窗家族没有名签）才退回那个**假设**，并在 diag 里**明说**
                // 依据是实测还是假设——日志里一眼能分开，不会再出现"看着像实测、其实是假设"。
                bool litLeft = bright[li] > bright[ri];
                bool playerLeft;
                bool known = TryPlayerSide(dlg, out playerLeft);
                bool speakerIsPlayer = known ? (litLeft == playerLeft) : litLeft;
                string basis = known
                    ? "名签实测玩家侧=" + (playerLeft ? "左" : "右")
                    : "名签判不出玩家侧→按「左=玩家」假设";
                diag += " → 亮侧=" + (litLeft ? "左" : "右") + " 明暗差=" + best.ToString("0.00")
                      + " → 说者=" + (speakerIsPlayer ? "player" : "npc") + "（" + basis + "）";
                return speakerIsPlayer ? "player" : "npc";
            }
            catch (Exception e)
            {
                diag = "立绘探针异常: " + e.Message;
                return "";
            }
        }

        /// <summary>收集全窗 RawImage：描述串（名/color/alpha/active）、加权亮度、世界 x、是否“活着”</summary>
        private static void ScanPortraits(Transform t, System.Collections.Generic.List<string> desc,
                                          System.Collections.Generic.List<float> bright,
                                          System.Collections.Generic.List<float> xs,
                                          System.Collections.Generic.List<bool> alive, int depth)
        {
            if (t == null || desc.Count >= 10 || depth > 8) return;
            try
            {
                var img = t.GetComponent<RawImage>();
                if (img != null)
                {
                    float lum = img.color.r * 0.299f + img.color.g * 0.587f + img.color.b * 0.114f;
                    // 半透明也当变暗：压暗实现可能是"颜色变暗 + alpha 降"
                    lum *= Mathf.Clamp01(img.color.a);
                    float cg = 1f;
                    var g = t.GetComponent<CanvasGroup>();
                    if (g != null && g.enabled) cg = Mathf.Clamp01(g.alpha);
                    lum *= cg;
                    // 「活着」= 组件启用 **且** 整条父链激活（只看 activeSelf 会把空占位算进来）
                    bool on = img.enabled && t.gameObject.activeInHierarchy;
                    if (!on) lum = 0f;
                    desc.Add(t.name + "(rgba=" + img.color.r.ToString("0.00") + "," + img.color.g.ToString("0.00")
                             + "," + img.color.b.ToString("0.00") + "," + img.color.a.ToString("0.00")
                             + (img.enabled ? " on" : " OFF")
                             + (t.gameObject.activeInHierarchy ? "/链活" : "/链OFF")
                             + (cg < 0.999f ? " cg=" + cg.ToString("0.00") : "") + ")");
                    bright.Add(lum);
                    xs.Add(t.position.x);
                    alive.Add(on);
                }
                int n = t.childCount;
                for (int i = 0; i < n; i++) ScanPortraits(t.GetChild(i), desc, bright, xs, alive, depth + 1);
            }
            catch { }
        }

        /// <summary>偏暗的 Image 覆盖层（压暗若是独立遮罩，就在这几条里），上限 4 条</summary>
        private static string DarkOverlays(Transform root)
        {
            var sb = new System.Text.StringBuilder();
            try { CollectDarkImages(root, sb, 0); } catch { }
            return sb.Length == 0 ? "(无)" : sb.ToString();
        }

        private static void CollectDarkImages(Transform t, System.Text.StringBuilder sb, int depth)
        {
            if (t == null || depth > 8 || sb.Length > 200) return;
            try
            {
                var img = t.GetComponent<Image>();
                if (img != null)
                {
                    float mx = Mathf.Max(img.color.r, Mathf.Max(img.color.g, img.color.b));
                    if (mx < 0.7f && img.color.a > 0.05f)
                        sb.Append(t.name).Append("(rgba=").Append(img.color.r.ToString("0.00")).Append(',')
                          .Append(img.color.g.ToString("0.00")).Append(',')
                          .Append(img.color.b.ToString("0.00")).Append(',')
                          .Append(img.color.a.ToString("0.00")).Append(") ");
                }
                int n = t.childCount;
                for (int i = 0; i < n; i++) CollectDarkImages(t.GetChild(i), sb, depth + 1);
            }
            catch { }
        }

        // ------------------------------------------------------------------
        // typed 属性直取—— 取代"按名字符串在树上找"
        //
        // `UIDramaDialogueBase`（层次 `UIDramaDialogue : UIDramaDialogueBase : UIDramaBase : UIBase`）
        // 在互操作程序集里暴露了这些 **typed 属性**：
        //   `[p] Button btnRightLook / btnLeftLook / btnNext / btnSkip`
        //   `[p] Text   textRightName / textLeftName`
        // 直接读引用，比按名深搜少两层脆弱：① 不必容忍节点真名的 `G:` 前缀；
        // ② 预制件改名不影响。名字路仍保留作兜底（非该家族的窗变体）。
        //
        // **为什么用 `TryCast` 而不是 `as`**：IL2CPP 互操作下 `as` 对返回的包装体**静默给 null**
        // （本项目已在 `UiRects.Of` 踩过，当时改用 `GetComponent<RectTransform>()` 绕开）。
        // `Il2CppObjectBase.TryCast<T>()` 是互操作库自带的转型（`UnhollowerBaseLib.dll` 实证存在：
        // `T Cast<T>()` / `T TryCast<T>()`），失败返回 null、不抛异常；这里仍包 try/catch。
        // 全程**只读**：任何一步落空都只返回 null，由调用方退回原有按名/兜底路径，不改变行为。
        // ------------------------------------------------------------------

        /// <summary>运行时**真实**窗型名。`GetType().Name` 在 IL2CPP 下恒为声明类型（`Component`），
        /// 本项目已在 `StripOperationItem`/`UiGateDump` 上踩过——必须走 `GetIl2CppType().Name`。</summary>
        private static string RealUiType(UIDramaBase ui)
        {
            try
            {
                var t = ui.GetIl2CppType();
                if (t != null && !string.IsNullOrEmpty(t.Name)) return t.Name;
            }
            catch { }
            try { return ui.GetType().Name; } catch { return "?"; }
        }

        /// <summary>`UIDramaBase` → `UIDramaDialogueBase`（不属于该家族时返回 null）。</summary>
        private static UIDramaDialogueBase AsDialogueBase(UIDramaBase ui)
        {
            if (ui == null) return null;
            try { return ui.TryCast<UIDramaDialogueBase>(); } catch { return null; }
        }

        /// <summary>大图窗是**另一个家族**：`UIDramaBigTexture : UIDramaBigTextureBase : UIDramaBase`
        /// —— 与 `UIDramaDialogueBase` **平级**（互操作程序集实证），所以上面对它的 `TryCast` 会失败。
        /// 它没有 `btnRightLook`/`textRightName`，但有 typed `btnNext`/`textNextTip`/`goBtnItem`。</summary>
        private static UIDramaBigTextureBase AsBigTextureBase(UIDramaBase ui)
        {
            if (ui == null) return null;
            try { return ui.TryCast<UIDramaBigTextureBase>(); } catch { return null; }
        }

        /// <summary>从 typed 属性取克隆模板，顺序与 <see cref="TemplateNames"/> 一致。
        /// `IsUsableTemplate` 仍要过——typed 引用也可能指向未激活/无文本的节点。
        ///
        /// **覆盖面（实测的家族树）**：`UIDramaBase` 下有 **17 个平级家族**
        /// （`UIDramaDialogueBase`/`UIDramaBigTextureBase`/`UIDramaDialogueBigTextureBase`/
        /// `UIDramaFortuitousBase`/`UIDramaLetterBase`/`UIDramaPotmonBase`/`UIDramaSpriteBase`/
        /// `UIDramaSpriteSubBase`/`UIDramaSutraLetterBase`/`UIDramaHerdNPCBase`/
        /// `UIDramaImmortalAncestralBase`/`UIModDramaPreviewType5Base`…）。
        /// 它们在 `UIDramaBase` 之下**没有共同基类**声明这些按钮属性，所以只能逐个 `TryCast`。
        /// 目前显式覆盖**两个主力家族**（对话窗 + 大图窗）；其余落回按名深搜——
        /// 那条路本来就覆盖全部窗型，行为不劣化。**落空时打出真实窗型名**，
        /// 将来真在这些窗里出问题，一眼知道该补哪个家族，不必再猜。</summary>
        private static Button TypedTemplate(UIDramaBase ui, out string via)
        {
            via = "";
            var d = AsDialogueBase(ui);
            if (d == null)
            {
                // 大图窗家族：只有 btnNext 一个候选（它没有左右名签旁的「查看」）
                var bt = AsBigTextureBase(ui);
                if (bt == null) { via = "typed 未覆盖窗型=" + RealUiType(ui); return null; }
                var nb = SafeBtn(() => bt.btnNext);
                if (nb != null && IsUsableTemplate(nb)) { via = "typed(BigTexture) btnNext"; return nb; }
                via = "typed(BigTexture) btnNext 不可用";
                return null;
            }
            var b = SafeBtn(() => d.btnRightLook); if (b != null && IsUsableTemplate(b)) { via = "typed btnRightLook"; return b; }
            b = SafeBtn(() => d.btnLeftLook);      if (b != null && IsUsableTemplate(b)) { via = "typed btnLeftLook";  return b; }
            b = SafeBtn(() => d.btnNext);          if (b != null && IsUsableTemplate(b)) { via = "typed btnNext";      return b; }
            b = SafeBtn(() => d.btnSkip);          if (b != null && IsUsableTemplate(b)) { via = "typed btnSkip";      return b; }
            via = "typed 四属性皆不可用(" + RealUiType(ui) + ")";
            return null;
        }

        /// <summary>从 typed 属性取 NPC 那一侧的名签矩形。先按侧别取，落空再取另一侧
        /// （与按名路「该侧找不到就退另一侧」同口径）。</summary>
        private static RectTransform TypedNameRect(UIDramaBase ui, bool onRight, out string via)
        {
            via = "";
            var d = AsDialogueBase(ui);
            if (d == null) { via = "非 UIDramaDialogueBase"; return null; }
            var a = onRight ? SafeRect(() => d.textRightName) : SafeRect(() => d.textLeftName);
            if (a != null) { via = "typed " + (onRight ? "textRightName" : "textLeftName"); return a; }
            var b = onRight ? SafeRect(() => d.textLeftName) : SafeRect(() => d.textRightName);
            if (b != null) { via = "typed 退另一侧"; return b; }
            via = "typed 名签两属性皆空";
            return null;
        }

        private static Button SafeBtn(Func<Button> f)
        {
            try { return f(); } catch { return null; }
        }

        /// <summary>`Text` → 它的 `RectTransform`（`Graphic.rectTransform`）。</summary>
        private static RectTransform SafeRect(Func<Text> f)
        {
            try { var t = f(); return t == null ? null : t.rectTransform; } catch { return null; }
        }

        private static Transform FindDeep(Transform root, string name)
        {
            if (root == null) return null;
            if (NameIs(root.name, name)) return root;
            int n = root.childCount;
            for (int i = 0; i < n; i++)
            {
                var r = FindDeep(root.GetChild(i), name);
                if (r != null) return r;
            }
            return null;
        }

        /// <summary>结果级立绘纠偏 —— 直接看「右侧名签画的是不是 `unitRight` 那个人」。
        ///
        /// 为什么需要它（而不是只靠 Prefix 归零 ID）：
        ///   `UpdateUnit` 在 id==0 时会被 `dramaData.unitLeftID/unitRightID` 顶替
        ///   （反编 0x18301363–0x183013A6），而这两个 ID 挂在跨页共享的 `DramaData` 上。
        ///   玩家实测：**装本 mod 才会出现**，不装 mod 同一段剧情正常 —— 说明是本 mod 在
        ///   剧情窗上注入的那套 UI（`InitData` Postfix 克隆按钮 / 改父节点 / 重刷）让本页的
        ///   初始化多走了一次或走了不同分支，把游戏内部这两个 ID 写脏；mod 源码里没有任何
        ///   一处直接写它们。与其去追具体写入点（在游戏侧、随版本变），不如在**消费侧**
        ///   校验结果：0/0 页的两侧本来就该是 `unitLeft`/`unitRight` 两个人，画错就重刷。
        ///
        /// 判据三连（任一不满足就不动，保证对正常页是零开销空操作）：
        ///   ① 配表 `npcLeft==0 && npcRight==0`（本页语义 = 用运行时单位）；
        ///   ② `unitLeft`/`unitRight` 两个 unitID 都取得到且不相等（独白页两侧同一人不碰）；
        ///   ③ 右侧名签文本 ≠ `unitRight` 的名字（已经画对就不碰）。
        /// 命中后：ID 归零 → `UpdateUI()` 重刷一次（同帧内，玩家看不到闪动）。</summary>
        private static void SanitizePortraitSlots(UIDramaBase ui, int id, DramaData data)
        {
            if (ui == null || data == null) return;
            var item = g.conf?.dramaDialogue?.GetItem(id);
            if (item == null) return;
            if (item.npcLeft != 0 || item.npcRight != 0) return;

            var lu = data.unitLeft;
            var ru = data.unitRight;
            if (lu == null || ru == null) return;
            string lid = "", rid = "";
            try { lid = DramaNativeClaim.UidOf(lu) ?? ""; } catch { }
            try { rid = DramaNativeClaim.UidOf(ru) ?? ""; } catch { }
            if (string.IsNullOrEmpty(lid) || string.IsNullOrEmpty(rid) || lid == rid) return;

            string want = SafeName(ru);
            if (string.IsNullOrEmpty(want)) return;
            var tr = FindDeep(ui.transform, "textRightName");
            var t = tr != null ? tr.GetComponent<Text>() : null;
            if (t == null) return;
            string cur = t.text ?? "";
            if (cur == want) return;                 // 右侧画的就是 unitRight，正常

            int l0 = data.unitLeftID, r0 = data.unitRightID;
            data.unitLeftID = 0;
            data.unitRightID = 0;
            ui.UpdateUI();
            ModMain.P("[DramaAiOption] 立绘位纠偏 page=" + id + " 原ID=" + l0 + "/" + r0
                      + " 右名签=\"" + cur + "\" → \"" + want + "\"");
        }

        /// <summary>`actual` 是否就是 `want`（或带前缀的 `G:want`）。</summary>
        private static bool NameIs(string actual, string want)
        {
            if (string.IsNullOrEmpty(actual) || string.IsNullOrEmpty(want)) return false;
            if (actual == want) return true;
            return actual.Length == want.Length + NodePrefix.Length
                   && actual.StartsWith(NodePrefix, StringComparison.Ordinal)
                   && actual.EndsWith(want, StringComparison.Ordinal);
        }
    }

    /// <summary>`UIDramaBase.CreateOptions` postfix → 给本轮建出的选项按钮挂"点击上报"监听
    /// （09-23：把"玩家选了什么"记进段，供模型看见完整交互）。与探针/注入的挂点各自独立。</summary>
    [HarmonyPatch(typeof(UIDramaBase), "CreateOptions")]
    internal static class DramaOptionTapHook
    {
        private static void Postfix(GameObject __0, GameObject __1, UIDramaBase __instance)
        {
            try { DramaAiOption.NoteOptionsCreated(__0, __1); } catch { }
            // 10-18 取证探针：把"表侧 / 窗口侧 / 写入侧"三份真值一次打全（见 DramaConfDump 文件头）。
            // 只读、全程 try/catch —— 取证失败绝不影响开窗与点击链。
            try { DramaConfDump.NoteWindow(__instance, __0, __1); } catch { }
        }

        /// <summary>启动自检（`PatchAll` 之后由 `ModMain` 调）：报告本补丁是否真的武装上了。
        /// 为什么要它：`[HarmonyPatch]` 与方法签名对不上时 Harmony **静默跳过**；运行期异常又被
        /// postfix 里的 try/catch 吞掉 —— 两者都表现为"功能从未生效、日志一行没有"（09-23 排查
        /// 正是卡在这个盲区：分不清"没玩到"与"补丁没挂"）。</summary>
        internal static string PatchSelfCheck()
        {
            try
            {
                var m = HarmonyLib.AccessTools.Method(typeof(UIDramaBase), "CreateOptions");
                if (m == null)
                    return "⚠ 未找到 UIDramaBase.CreateOptions（游戏版本变了？）⇒ 选项监听不可能生效";
                var info = HarmonyLib.Harmony.GetPatchInfo(m);
                int pre = 0, post = 0;
                try { pre = info == null || info.Prefixes == null ? 0 : info.Prefixes.Count; } catch { }
                try { post = info == null || info.Postfixes == null ? 0 : info.Postfixes.Count; } catch { }
                return "签名=" + m + "（prefix=" + pre + " postfix=" + post + "）"
                     + (post > 0 ? " OK" : " ⚠ postfix=0 ⇒ 补丁没挂上，选项监听不会触发");
            }
            catch (Exception e) { return "自检异常: " + e.Message; }
        }
    }
}
