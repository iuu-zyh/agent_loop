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
            string noTip = null)
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
                catch (Exception e) { return Err("执行失败: " + e.Message); }
            }

            int dramaId = ModIds.Window(dramaBase);   // 仅作 DramaGate 撞窗 key（不再据此开窗）

            if (!DramaGate.TryDefer(dramaId, out int slot))
                return Err("已有一个确认窗待玩家处理，请稍后再试");

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
                    catch (Exception e) { result = Err("执行失败: " + e.Message); }
                    if (okTip != null) { try { UITipItem.AddTip(okTip, 2f); } catch { } }
                    DramaGate.Resolve(slot, result);
                };
                System.Action onNoCb = () =>
                {
                    if (!DramaGate.TryClaim(slot)) return;
                    DramaGate.Resolve(slot, Err(noTip ?? "玩家婉拒了该请求"));
                };

                // 立绘剧情窗（表条目为壳，正文/选项文字/回调全部运行时覆盖）。
                // 三步写法：System.Action 局部量隐式转 Il2CppSystem.Action（CS1660 教训同款）。
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
                return Err("确认窗弹出失败: " + e.Message);
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

        private static void TryTip(string msg)
        {
            try { UITipItem.AddTip(msg, 2f); } catch { }
        }

        private static JObject Ok(JObject data) => new JObject { ["success"] = true, ["data"] = data, ["error"] = null };
        private static JObject Err(string error) => new JObject { ["success"] = false, ["data"] = null, ["error"] = error };
    }
}
