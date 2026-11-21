/// <summary>
/// ShowDramaService —— 通用模态确认窗构建器（静态）。
///
/// 职责：把"弹确认窗 → 玩家点选 → 执行真动作 → 把真实结果
/// 塞回延迟 response"封装成一个调用。正文由本类填。
///
/// 窗体定案（用户拍板不设降级）：立绘剧情窗 UICustomDramaDyn（NPC 立绘 + 自定义
/// 按钮文案 okLabel/noLabel）。硬前提：ModExcel/DramaDialogue 配置表必须有本段 ID 条目——
/// OpenUI → DramaTool.OpenDrama → RandomDramaID 查表，缺条目打"找不到剧情ID：xxx"后
/// **静默不开窗**、不抛异常 → DramaGate 120s 超时（上午"赠送灵石不弹窗"事故根因）。
/// 壳条目已补：`ModExcel/{DramaDialogue,DramaOptions,LocalText}.json`（8 段，正文/选项/回调
/// 全部运行时覆盖；weight 即 RandomDramaID 随机权重）。ModIds 私有段常量：既作开窗 ID，
/// 也作 DramaGate 撞窗检查 key。表缺失时直接 120s 超时暴露问题，不做静默换窗。
///
/// 时序（配合 DramaGate + WsClient）：
///   ShowConfirm → DramaGate.TryDefer（登记 slot）
///     → 返回 __pending__ 标记（ToolExecutor 原样上抛 → WsClient 憋住 response）
///   玩家点「确认」→ TryClaim（原子领票，防超时后迟到点击误执行）→ onOk() 真动作
///     → Resolve(slot, 动作真实结果) → WsClient 补发 response → LLM 收口说话
///   玩家点「拒绝」→ TryClaim → Resolve(slot, 婉拒结果)
///
/// 硬约束：本类方法必须在 Unity 主线程调用（ToolExecutor/按钮回调天然满足）。
/// </summary>
using System;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    internal static class ShowDramaService
    {
        /// <param name="dramaBase">ModIds 段基址（窗=+0，确认选项=+1，取消选项=+2）</param>
        /// <summary>
        /// 直通开关：true 时不弹窗、直接执行 onOk 并返回真实结果。
        /// 工具验证工程（历史存在，已从仓库删除）置 true——无玩家交互时弹窗会导致
        /// pending 标记被判 FAIL 且无人应答；生产对话 mod 保持 false（正常弹窗走延迟 response）。
        /// 由宿主在启动时设置。
        /// </summary>
        internal static bool AutoConfirm = false;

        /// <param name="onOk">点确认后执行的真动作，返回 Ok/Fail 形态结果（塞进延迟 response）</param>
        /// <param name="okTip">确认后的浮字提示（可空）</param>
        /// <param name="noTip">拒绝语义文案（进 tool/result 的 error，可空，默认"玩家婉拒了该请求"）</param>
        /// <param name="onNo">**拒绝时要做的善后**（可空）。09-24 补：有的调用方**在弹窗之前**就已经把
        /// 实物挪到手上了（多件赠送必须先拆栈才能列出清单），玩家点"不收"若没人放回，那批东西就
        /// 凭空消失（真机洞，见 `ToolExecutor` 多件赠送分支）。语义 = "玩家没收下 ⇒ 把这些还原"，
        /// 在 `Resolve` **之前**执行（否则模型先收到"没送成"，而游戏状态还没还原，两边对不上账）。</param>
        /// <returns>Ok/Fail 形态 JObject：成功时是 DramaGate pending 标记（含 __pending__/__slot__）</returns>
        public static JObject ShowConfirm(
            int dramaBase,
            WorldUnitBase left,
            WorldUnitBase right,
            string text,
            string okLabel,
            string noLabel,
            Func<JObject> onOk,
            string okTip = null,
            string noTip = null,
            Action onNo = null)
        {
            if (AutoConfirm)
            {
                // 直通：跳过窗口与 DramaGate，动作结果原样返回（verify 判定照常）
                try
                {
                    var r = onOk();
                    if (okTip != null) { try { UITipItem.AddTip("[自动确认] " + okTip, 2f); } catch { } }
                    return r;
                }
                catch (Exception e) { return ToolResult.Fail("执行失败: " + e.Message); }
            }

            int dramaId = ModIds.Window(dramaBase);   // 仅作 DramaGate 撞窗 key（不再据此开窗）

            if (!DramaGate.TryDefer(dramaId, out int slot))
                // 同 ToolExecutor：撞窗用既有措辞，链在收尾用专属那句（`RejectText`）
                return ToolResult.Fail(DramaGate.RejectText("已有一个确认窗待玩家处理，请稍后再试"));

            try
            {
                System.Action onOkCb = () =>
                {
                    if (!DramaGate.TryClaim(slot))
                    {
                        try { UITipItem.AddTip("该确认已超时失效", 2f); } catch { }
                        return;
                    }
                    JObject result;
                    try { result = onOk(); }
                    catch (Exception e) { result = ToolResult.Fail("执行失败: " + e.Message); }
                    if (okTip != null) { try { UITipItem.AddTip(okTip, 2f); } catch { } }
                    DramaGate.Resolve(slot, result);
                };
                System.Action onNoCb = () =>
                {
                    if (!DramaGate.TryClaim(slot)) return;
                    // ★ 先善后、再收口（09-24 方案 A）★ 多件赠送这类调用方在弹窗**之前**就把实物挪到手上了
                    // （要先拆栈才列得出清单），玩家点"不收"时若不还原，那批东西就凭空消失。
                    // 必须在 `Resolve`（= 把工具结果补发给 Python）**之前**执行；异常一律吞 ——
                    // 这是拒绝路径，善后炸了也不该让窗收不了口（收不了口 = 槽位永久占用）。
                    try { if (onNo != null) onNo(); } catch { }
                    DramaGate.Resolve(slot, ToolResult.Fail(noTip ?? "玩家婉拒了该请求"));
                };

                // 立绘剧情窗（表条目为壳，正文/选项文字/回调全部运行时覆盖）。
                // 三步写法：System.Action 局部量隐式转 Il2CppSystem.Action（CS1660 教训同款）。
                ClearShellOptions(dramaId);        // ★必须在构造之前★（构造/Init 会读表建选项）
                // ★ 同样必须在构造之前 ★：配表 id 决定画面两侧画谁，构造时就被读走了
                FixPortraitSides(dramaId, left, right);
                var dyn = new UICustomDramaDyn(dramaId);
                var b = (UICustomDramaBase)dyn;
                b.dramaData.dialogueText[dramaId] = text;
                b.dramaData.unitLeft = left != null ? left : g.world.playerUnit;   // 立绘空会 NRE，兜底玩家
                b.dramaData.unitRight = right;
                b.dramaData.dialogueOptions[dramaId + ModIds.OffPrimary] = okLabel;
                b.dramaData.dialogueOptions[dramaId + ModIds.OffCancel] = noLabel;
                dyn.SetOptionCall(dramaId + ModIds.OffPrimary, onOkCb);
                dyn.SetOptionCall(dramaId + ModIds.OffCancel, onNoCb);
                dyn.OpenUI();
            }
            catch (Exception e)
            {
                DramaGate.Cancel(slot);   // 窗没开成：静默注销，不补发（WsClient 尚未登记）
                return ToolResult.Fail("确认窗弹出失败: " + e.Message);
            }
            return DramaGate.MakePendingMarker(slot);
        }

        /// <summary>
        /// 轻量确认窗（无 DramaGate 槽位）：纯本地决策用——自主互动"同意/不同意"这类
        /// 没有延迟 response 要补发的场景，回调直接执行。同款 UICustomDramaDyn（立绘/正文/两选项）。
        ///
        /// **立绘位语义（务必照此传参）**：`left` = 屏幕左位 = **玩家**，`right` = 屏幕右位 = **对方 NPC**。
        /// 依据：① 游戏自身代码同款——`DramaData{ unitLeft = g.world.playerUnit, unitRight = unit }`
        /// （反编 types36 多处，如 9136/9200 婚礼与邀请剧情）；② 09-08 用户拍板"玩家在左、NPC 在右"
        /// （对齐游戏原版神识传音窗 unitLeft=player 方向）。**09-12 教训**：自主互动确认窗曾传反
        /// （left=NPC / right=玩家），实机表现为"右侧立绘是玩家"，与其余四个工具窗不一致。
        ///
        /// 注意：本类被 verify 工程链接编译，此处不得引用 ModMain（不在 verify 清单，CS0103 教训）。
        /// 仅主线程调用。
        /// </summary>
        public static void ShowConfirmSimple(
            int dramaBase,
            WorldUnitBase left,
            WorldUnitBase right,
            string text,
            string okLabel,
            string noLabel,
            Action onOk,
            Action onNo = null)
        {
            if (AutoConfirm)
            {
                // verify 无交互场景：直通 onOk（与 ShowConfirm 同语义）
                try { onOk(); } catch (Exception e) { TryTip("[自动确认] 执行失败: " + e.Message); }
                return;
            }
            try
            {
                // 立绘剧情窗（同 ShowConfirm；配置壳见 ModExcel 三张表——起本 mod 自带）
                int win = ModIds.Window(dramaBase);
                ClearShellOptions(win);            // ★必须在构造之前★（构造/Init 会读表建选项）
                FixPortraitSides(win, left, right);  // 同 ShowConfirm：右侧必须读 unitRight（见该方法注释）
                var dyn = new UICustomDramaDyn(win);
                var b = (UICustomDramaBase)dyn;
                b.dramaData.dialogueText[win] = text;
                b.dramaData.unitLeft = left != null ? left : g.world.playerUnit;   // 立绘空会 NRE，兜底玩家
                b.dramaData.unitRight = right;
                b.dramaData.dialogueOptions[win + ModIds.OffPrimary] = okLabel;
                b.dramaData.dialogueOptions[win + ModIds.OffCancel] = noLabel;
                // 三步写法：lambda 先落 System.Action 局部，再隐式转 Il2CppSystem.Action
                // （lambda 无法直接命中用户定义隐式转换，CS1660——§11 委托桥同款）
                System.Action okCb = () => { try { onOk(); } catch (Exception e) { TryTip("执行失败: " + e.Message); } };
                System.Action noCb = () => { try { onNo?.Invoke(); } catch (Exception e) { TryTip("处理失败: " + e.Message); } };
                dyn.SetOptionCall(win + ModIds.OffPrimary, okCb);
                dyn.SetOptionCall(win + ModIds.OffCancel, noCb);
                dyn.OpenUI();
            }
            catch (Exception e)
            {
                TryTip("确认窗弹出失败: " + e.Message);
                onNo?.Invoke();   // 弹窗失败按婉拒处理（调用方决定语义）
            }
        }

        /// <summary>
        /// 把壳表 `DramaDialogue.options` 清成 "0"（= 表里一个选项都不声明），按钮全交运行时
        /// `dramaData.dialogueOptions`。**开窗之前必须调**。
        ///
        /// 【为什么非清不可（10-18 真机取证，别删）】
        /// 游戏建选项时**两个来源相加、不去重**：
        ///   ① `ConfDramaDialogueItem.options` 声明的 id（文案取 `ConfDramaOptions.text` → LocalText）
        ///   ② `dramaData.dialogueOptions` 里同一对 id（文案取我们写的）
        /// 09-11 建壳表时给每个窗都声明了 `+1|+2`（当时以为"声明+运行时覆盖文案"，
        /// 其实覆盖不到表那一份）。LocalText 表早年是**空表**（23 字节），表侧那对取不到文字
        /// ⇒ 不建按钮 ⇒ 一直是 2 枚，问题被掩盖。10-18 组队表导出顺手补全了 LocalText，
        /// 壳占位文案「确定（占位，运行时覆盖）」有了字 ⇒ 表侧那对**现形**，
        /// 自主交互确认窗变成 4 枚（用户截图）：「同意｜不同意｜确定（占位）｜取消（占位）」。
        /// 探针实证（`[DramaConfDump]`）：`activeOptionsData` 4 项 = 同一对 id 各两遍。
        ///
        /// 【为什么清表而不是改表 xlsx / 不写 dialogueOptionsAddText】
        ///   · 改 xlsx 要开编辑器重导出，且**旧存档/已装用户**还得再更新一次；
        ///   · 神识传音（同款 API 的参考实现）就是「表声明 0 + 代码写字典」：
        ///     它的自制按钮窗 `MID+7` 在表里 `options='0'`，按钮全靠 `dialogueOptions`；
        ///     而它表里声明了选项的行（`MID+1..4` 原生邀请那类）代码里从不写字典。
        ///   · `options` 是**可写属性**（已用编译探针确认有 setter），运行时清空零成本。
        ///
        /// 【清空后点击链路不变】
        /// `SetOptionCall(id, cb)` 挂的是 `onOptionsCall` 字典，与表无关；玩家点按钮时
        /// `UIDramaBase.ClickOption(ConfDramaOptionsItem)` 仍按 id 反查（那两行 `DramaOptions`
        /// 条目**必须保留**，本方法只清窗口行的 options 列，不动 DramaOptions 表）。
        ///
        /// 【失败怎么办】读不到/抛异常 ⇒ 只打日志、照常开窗：退回"多一对占位按钮"的旧表现，
        ///   绝不因为清壳失败就把窗开不出来（那会让工具 120s 超时，比多俩按钮严重得多）。
        /// 仅主线程调用（与开窗同处）。
        /// </summary>
        /// <summary>
        /// 修正确认窗的**立绘位**：让左侧画 `unitLeft`、右侧画 `unitRight`（10-24 真机事故）。
        ///
        /// ## 事故
        /// 组队邀请窗（`agentloop_team_invite_180`）实机**两侧都画玩家**（用户截图 +
        /// `Player.log`）。代码里明明传的是 `left=player / right=项淑仪`。
        ///
        /// ## 根因：立绘画谁由**配表 id** 决定，`DramaData` 只是被读的容器
        /// `UIDramaDialogue.UpdateUI` 会按配表 `npcLeft`/`npcRight` 两次调 `UpdateUnit`：
        /// <code>
        ///   id==1  → g.world.playerUnit        ← 1 = 玩家，**硬编码，根本不看 DramaData**
        ///   id==0  → 按 isLeft 取 unitLeft/unitRight，空则退回 dramaData.unit
        ///   id==11 → dramaData.unitLeft
        ///   id==12 → dramaData.unitRight
        /// </code>
        /// 组队邀请窗的壳配表是 `npcLeft=1 / npcRight=11`（与 190 同一套壳），于是：
        ///   · 左 = id 1 → 玩家（对）；
        ///   · 右 = id 11 → **unitLeft = 玩家**（错！右侧本该是 NPC）。
        /// 两侧都读玩家 ⇒ 两个立绘、两个名签全是玩家。
        /// 190 段（队友倒下）修的是同一件事，但它要"两侧同一个人"，方向相反
        /// （见 `TeamContainerInject.DramaInitDataPrefix`）。
        ///
        /// ## 做法：运行时把这一行的两个 id 改成 11 / 12
        /// 与 `ClearShellOptions` 同一条已验证的路（`g.conf.dramaDialogue.GetItem(...)`
        /// 拿到 `ConfDramaDialogueItem`，字段是 `public int`，直接赋值，**只影响本进程**）。
        /// 选它而不是"改加密配表 + 重导出"：旧存档/已装用户不需要再更新一次。
        ///
        /// `npcLeft=11`（读 unitLeft=玩家）→ 与原来 id=1 的结果一致，**左侧画面不变**；
        /// `npcRight=12`（读 unitRight=NPC）→ 修好右侧。幂等，每次开窗前调一遍即可。
        /// </summary>
        private static void FixPortraitSides(int windowId, WorldUnitBase left, WorldUnitBase right)
        {
            try
            {
                // ★ 只碰**本 mod 自己的段** ★（`Mod + 100 … Mod + 999`）。
                //   `g.conf.dramaDialogue.GetItem()` 返回的是配置表里**那一行的共享实例**，
                //   写一次本进程内一直有效；万一哪天有人传了游戏原生的 windowId 进来，
                //   这条守卫能保证我们不会去改别人的立绘位。
                if (windowId <= ModIds.Mod || windowId >= ModIds.Mod + 1000)
                {
                    TryLog("[Drama] 立绘位修正跳过：win=" + windowId + " 不在本 mod 段内");
                    return;
                }
                var item = g.conf?.dramaDialogue?.GetItem(windowId);
                if (item == null)
                {
                    TryLog("[Drama] 立绘位修正跳过：DramaDialogue 无此行 win=" + windowId);
                    return;
                }
                int oldL = -1, oldR = -1;
                try { oldL = item.npcLeft; oldR = item.npcRight; } catch { }
                // 只在"右侧会读错人"时才动，且记下原值便于排查
                if (oldL == 11 && oldR == 12) return;          // 已是目标形态（幂等）
                try { item.npcLeft = 11; } catch (Exception e1) { TryLog("[Drama] 写 npcLeft 失败: " + e1.Message); }
                try { item.npcRight = 12; } catch (Exception e2) { TryLog("[Drama] 写 npcRight 失败: " + e2.Message); }
                int newL = -1, newR = -1;
                try { newL = item.npcLeft; newR = item.npcRight; } catch { }
                bool ok = (newL == 11 && newR == 12);
                TryLog("[Drama] 立绘位修正 win=" + windowId + "：" + oldL + "/" + oldR
                       + " → " + newL + "/" + newR
                       + (ok ? "（左=unitLeft 右=unitRight）" : " ⚠ 写入未生效（配表只读？）")
                       + "｜left=" + UnitIdentity.NameOr(left, "(空)", "(无名)", "(取不到)") + " right=" + UnitIdentity.NameOr(right, "(空)", "(无名)", "(取不到)"));
            }
            catch (Exception e) { TryLog("[Drama] 立绘位修正异常: " + e.Message); }
        }

        private static void ClearShellOptions(int windowId)
        {
            try
            {
                var item = g.conf?.dramaDialogue?.GetItem(windowId);
                if (item == null) { TryLog("[Drama] 清壳失败：DramaDialogue 无此行 win=" + windowId); return; }
                string cur = null;
                try { cur = item.options; } catch { }
                if (cur == "0") return;                          // 已是空的（幂等）
                item.options = "0";
            }
            catch (Exception e) { TryLog("[Drama] 清壳异常：win=" + windowId + " " + e.Message); }
        }

        private static void TryTip(string msg)
        {
            try { UITipItem.AddTip(msg, 2f); } catch { }
        }

        /// <summary>只打日志、绝不抛（本类被 verify 工程链接编译，不得引用 ModMain —— CS0103 教训）。</summary>
        private static void TryLog(string msg)
        {
            try { UnityEngine.Debug.Log("[AgentLoopBridge] " + msg); } catch { }
        }

    }
}
