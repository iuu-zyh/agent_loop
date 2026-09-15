/// <summary>
/// AB 预制体版对话面板 —— 游戏原生 UI 管理器体系（迁移）。
///
/// 迁移史：①独立 Overlay Canvas（sortingOrder 30000 盖一切）+ DramaGate 让位/自动恢复
/// 状态机，游戏弹窗层级永远要手动博弈；②神识传音考古实锤其全站 g.ui.OpenUI + UIBase
/// 继承、层级由游戏自动排（后开盖先开）→ 迁移到同款体系，让位/恢复方案整体退役。
///
/// 三件套（神识传音同款模式）：
///   RegisterTypeInIl2Cpp → g.ui.OpenUI(new UITypeBase("UIChatAi", (UILayer)0))
///   → .gameObject.AddComponent&lt;AbChatPanel&gt;()（逻辑装配 AssembleResident 不变）。
///
/// 关键时机约束（登录 UI 原生崩溃教训，ModMain UI-2 注释同源）：
///   继承 il2cpp 游戏类（UIBase）的类型【绝不能】在 MelonLoader 初始化早期注册——
///   半途注入毒化 il2cpp 类型系统 → 登录 UI（UIMgr:OpenUI 触碰静态数据）原生崩溃。
///   故注册 + OpenUI 全部推迟到首次打开对话（ChatLauncher → OpenForUnitStatic）懒执行；
///   神识传音同款实证：运行期按钮回调里注册 + OpenUI 全站可行。
///
/// 资源链：ModAbRes 已把预制体以 "UI/UIChatAi" + "UIChatAi" 双 key 注入 g.res.allRes
/// （OpenUI 内部按 UITypeBase 名取预制体，路径拼接规则未实证，双 key 兜底）。
///
/// 层级：创建时把根 Canvas sortingOrder 归 0（消预制体烘焙的 30000 残留），此后融入
/// 游戏 UI 层排序——工具唤起的确认窗/论道邀请等后开窗自然盖住对话窗，无需手动让位。
/// [由用户需求决定：样式壳走官方 AB 美术路线，动态逻辑保持现有代码层]
/// </summary>
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    /// <summary>ModMain 注入的单例桥（本类不直接持工具类引用）。
    /// 两种构建模式都可用：AB 面板与 DramaAiOption 经此复用同一 WS 连接。</summary>
    public static class ChatGlobals
    {
        public static WsClient WsClientInstance;
    }

#if AB_UI
    /// <summary>
    /// AB 版对话面板：懒创建常驻宿主 + 按 NPC 重绑（继承游戏 UIBase，走 g.ui 体系）。
    /// 装配（首次打开时 EnsureResident：OpenUI + AddComponent + 逻辑层一次完成）与
    /// 打开（ChatLauncher → OpenForUnitStatic → 实例 OpenForUnit：显隐 + 重绑 + 历史回放）分离；
    /// 不再 Instantiate 到自建宿主（旧 17:20 NRE 的根因是当时资源未注入 allRes，
    /// ModAbRes 双 key 注入后 OpenUI 链路已通——Player.log "g.res.Load(UI/UIChatAi) => OK" 实证）。
    /// </summary>
    public class AbChatPanel : UIBase
    {
        private ChatWindow _window;
        private ChatPresenter _presenter;

        // IL2CPP 硬约束（官方 Example.cs 实证「必须有这行代码，否则 MonoBehaviour 无法 AddComponent」）：
        // 缺此构造 → RegisterTypeInIl2Cpp NRE，半途注入毒化 il2cpp 类型系统 → 登录 UI 原生崩溃
        public AbChatPanel(IntPtr ptr) : base(ptr) { }

        private static AbChatPanel _resident;   // 常驻宿主（首次打开懒创建；游戏销毁后 fake-null 自动重建）
        private bool _assembled;                // 本实例是否已装配逻辑层（防重复 AddComponent/重复接事件）
        private static bool _typeRegistered;    // UIBase 派生类型只注册一次（重复注册会抛）

        /// <summary>[诊断] 确认窗实例名（= `AbPanelProber.DramaUiName`，闸门拦的就是它）。
        /// 加：同格"同意后开窗"每帧重试时，失败原因十有八九是**我们自己那个确认窗实例
        /// 还挂在 UIMgr 里**（闸判"存在"不判"可见"，`CloseUI` 不一定销毁实例）——打出来一看便知。</summary>
        private const string DramaUiProbeName = "UICustomDramaDyn";
        private static float _noCreateLogAt;    // 「当前不宜创建」日志节流（09-13 重试路径每帧会调到）

        /// <summary>AB 资源键（= UITypeBase 名；OpenUI 按名从 g.res 取预制体）</summary>
        public const string PrefabKey = "UIChatAi";

        public static AbChatPanel Resident => _resident;

        /// <summary>
        /// 对话窗此刻是否**真的显示着**（HotkeyGate 判「该不该屏蔽游戏原生快捷键」用）。
        ///
        /// 判据特意取 `ChatWindow.IsOpen` 而**不**取根节点 activeSelf：
        ///   · 装配完成后根节点是**故意保持 active** 的（见 AssembleResident 末尾 定案注释），
        ///     拿 activeSelf 会恒真 → 快捷键被永久屏蔽，比不屏蔽更糟；
        ///   · 关闭一律走 `CloseViaManager`（`_resident = null`）或面板真销毁（`OnDestroy` 置空），
        ///     所以 `_resident != null` 本身就等价于「开过且没关」；
        ///   · 两者取与，既排除「宿主建好但 OpenForUnit 还没跑」的同帧空窗，也排除
        ///     「管理器把实例关在后台但没通知我们」的残留引用。
        /// </summary>
        public static bool IsShowing
        {
            get
            {
                var r = _resident;
                if (r == null) return false;
                try { return r._window != null && r._window.IsOpen; }
                catch { return false; }   // GC 僵尸/已 Destroy：判否（宁可漏屏蔽，不可永久屏蔽）
            }
        }

        /// <summary>
        /// 懒创建常驻宿主（首次打开/实例被游戏场景切换销毁后重建）：
        /// 注册类型（运行期，避开登录 UI 崩溃窗口）→ OpenUI 挂游戏 UI 层 → 根 Canvas 归 0
        /// → 挂逻辑类装配。已就绪直接 true。
        /// </summary>
        public static bool EnsureResident(WsClient ws)
        {
            try
            {
                // 验活（GC 僵尸教训，AbContactPanel 同款）：判空/访问都可能抛，统一丢弃重建
                if (_resident != null)
                {
                    try { if (_resident.gameObject == null) _resident = null; }
                    catch { _resident = null; }
                }
                if (_resident != null && _resident._assembled) return true;
                // ①复用优先（方案 A，对齐 AbContactPanel）：管理器 CloseUI 不一定销毁实例，
                // 可能仍保有——查到就复用，绝不重复 OpenUI（重复创建=每次点开都重建宿主）。
                try
                {
                    var exist = g.ui.GetUI(new UIType.UITypeBase(PrefabKey, (UILayer)0));
                    if (exist != null)
                    {
                        var comp = exist.gameObject.GetComponent<AbChatPanel>();
                        if (comp != null && comp._assembled) { _resident = comp; return true; }
                    }
                }
                catch { }
                // 铁律：世界未就绪（MapMain 未建）/剧情窗展示中绝不调 g.ui.OpenUI
                if (!AbPanelProber.CanCreateNow())
                {
                    // ：NpcInitiativeMonitor 的"同意后开窗"已改成**每帧重试**（成功即止，5s 超时），
                    // 本行会被连续调到 → 日志节流 + 把最可能的拦路者原样打出来（见 DramaUiProbeName）。
                    if (Time.unscaledTime - _noCreateLogAt > 1f)
                    {
                        _noCreateLogAt = Time.unscaledTime;
                        bool dynExists = false;
                        try
                        {
                            dynExists = g.ui.GetUI(new UIType.UITypeBase(DramaUiProbeName, (UILayer)0)) != null;
                        }
                        catch { }
                        ModMain.P("[AbChatPanel] 当前不宜创建（世界未就绪或剧情窗展示中），本次跳过" +
                                  "｜确认窗 UICustomDramaDyn 实例仍存在=" + dynExists +
                                  "（存在即被闸住；确认窗刚关闭、实例未销毁时属预期，重试会等它消失）");
                    }
                    return false;
                }
                if (!_typeRegistered)
                {
                    UnhollowerRuntimeLib.ClassInjector.RegisterTypeInIl2Cpp<AbChatPanel>();
                    _typeRegistered = true;
                }
                if (ChatGlobals.WsClientInstance == null)
                { ModMain.P("[AbChatPanel] WsClient 未就绪，本轮不创建"); return false; }
                if (!ModAbRes.EnsureInjected(PrefabKey))
                { ModMain.P("[AbChatPanel] 预制体注入失败，本轮不创建"); return false; }
                ModMain.P("[AbChatPanel] g.ui.OpenUI(\"" + PrefabKey + "\") 创建宿主...");
                var ui = g.ui.OpenUI(new UIType.UITypeBase(PrefabKey, (UILayer)0));
                if (ui == null) { ModMain.P("[AbChatPanel] OpenUI 返回 null"); return false; }
                var go = ui.gameObject;
                go.name = "UIChatAi";
                // 事故结论（勿回退）：实例根上的 Canvas/GraphicRaycaster 是【游戏 UIMgr
                // 自己挂的】——销毁它会打乱 UI 层布局、连累同层的剧情窗。不要在此销毁实例组件。
                var panel = go.GetComponent<AbChatPanel>() ?? go.AddComponent<AbChatPanel>();
                // 层级完全交给游戏：UIMgr.CreateUI 自己给实例挂 Canvas 并按 UILayer 排序
                // （预制体自带的独立 Canvas 已由 ModAbRes 注入前剥离，防 "already added" NRE），
                // 此处不再触碰 sortingOrder——确认窗/论道弹窗后开自然盖住本窗。
                if (!panel.AssembleResident(ws))
                {
                    // 修（世界输入门控事故同款）：OpenUI 已在游戏 UIMgr 登记"打开中"，
                    // 直接 Object.Destroy 绕过关窗契约 → 登记项泄漏 → 世界输入被门控（人物动不了）
                    // 且该死实例仍挂登记表 → 同名 OpenUI 返回 null（对话窗打不开）。必须走管理器
                    // CloseUI 触发 CloseUIEnd 摘除登记并恢复世界输入。
                    try { g.ui.CloseUI(new UIType.UITypeBase(PrefabKey, (UILayer)0), false); }
                    catch (Exception e) { ModMain.P("[AbChatPanel] 装配失败 CloseUI: " + e.Message); }
                    return false;
                }
                _resident = panel;
                NormalizeLayout(go);
                ModMain.P("[AbChatPanel] 常驻宿主经 OpenUI 创建完成（游戏 UI 层，层级自动排）");
                DumpHierarchy(go);   // 【诊断】不可见问题排查：打印挂载链/Canvas 参数/矩形
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbChatPanel] OpenUI 创建异常: " + e);
                return false;
            }
        }

        /// <summary>不可见问题排查：从实例向上打印挂载链（active/Canvas 参数/矩形/缩放）
        /// + 实例根组件清单 + BG 板矩形。问题定位后移除。
        /// 注意：interop 包装对象 is 模式不可靠，一律 GetComponent&lt;T&gt;() 做 il2cpp 侧类型检查。</summary>
        private static void DumpHierarchy(GameObject go)
        {
            try
            {
                var sb = new System.Text.StringBuilder("挂载链(实例→根): ");
                var t = go.transform;
                int depth = 0;
                while (t != null && depth < 12)
                {
                    if (depth > 0) sb.Append(" <- ");
                    sb.Append(t.name);
                    sb.Append(t.gameObject.activeInHierarchy ? "[on]" : "[OFF]");
                    var cv = t.GetComponent<Canvas>();
                    if (cv != null)
                        sb.Append("(Canvas mode=" + cv.renderMode + " order=" + cv.sortingOrder
                            + " enabled=" + cv.enabled + ")");
                    var prt = t.GetComponent<RectTransform>();
                    if (prt != null)
                    {
                        var p = prt.anchoredPosition; var s = prt.localScale;
                        var a1 = prt.anchorMin; var a2 = prt.anchorMax;
                        sb.Append("(rt=" + prt.rect.width + "x" + prt.rect.height
                            + " pos=" + p.x + "," + p.y + " scale=" + s.x
                            + " aMin=" + a1.x + "," + a1.y + " aMax=" + a2.x + "," + a2.y + ")");
                    }
                    t = t.parent;
                    depth++;
                }
                ModMain.P("[AbChatPanel] " + sb.ToString());
                var comps = go.GetComponents<Component>();
                var names = new List<string>();
                foreach (var c in comps) { try { names.Add(c.GetIl2CppType().Name); } catch { names.Add("?"); } }
                ModMain.P("[AbChatPanel] 根组件: " + string.Join(", ", names.ToArray()));
                var bg = go.transform.Find("BG");
                if (bg != null)
                {
                    var bgRt = bg.GetComponent<RectTransform>();
                    if (bgRt != null)
                    {
                        var bp = bgRt.anchoredPosition; var bs = bgRt.sizeDelta;
                        ModMain.P("[AbChatPanel] BG active=" + bg.gameObject.activeInHierarchy
                            + " rect=" + bgRt.rect.width + "x" + bgRt.rect.height
                            + " pos=" + bp.x + "," + bp.y + " size=" + bs.x + "," + bs.y);
                    }
                    else ModMain.P("[AbChatPanel] BG active=" + bg.gameObject.activeInHierarchy + " (无 RectTransform)");
                }
            }
            catch (Exception e) { ModMain.P("[AbChatPanel] DumpHierarchy: " + e.Message); }
        }

        /// <summary>
        /// 布局归一化：UIMgr.CreateUI 会把实例 RectTransform 置零（scale=0、rect=0x0，真机 dump
        /// 实证）——游戏自家窗靠其开窗动画流程抬回 1；我们的常驻体不走该流程，scale 停 0
        /// 即完全不可见。此处直接设 scale=1 + 全屏 stretch（与旧独立 Canvas 架构的根一致，
        /// BG 面板 1000x600 是根的子节点，不受影响）。
        /// </summary>
        private static void NormalizeLayout(GameObject go)
        {
            try
            {
                var rt = go.GetComponent<RectTransform>();
                if (rt == null) { ModMain.P("[AbChatPanel] 归一化跳过：根无 RectTransform"); return; }
                rt.localScale = new Vector3(1f, 1f, 1f);
                rt.anchorMin = new Vector2(0f, 0f);
                rt.anchorMax = new Vector2(1f, 1f);
                rt.offsetMin = new Vector2(0f, 0f);
                rt.offsetMax = new Vector2(0f, 0f);
                rt.anchoredPosition = new Vector2(0f, 0f);
                ModMain.P("[AbChatPanel] 布局已归一化: scale=1 全屏stretch rect="
                    + rt.rect.width + "x" + rt.rect.height);
            }
            catch (Exception e) { ModMain.P("[AbChatPanel] NormalizeLayout: " + e.Message); }
        }

        /// <summary>静态打开入口（ChatLauncher 调用）：确保宿主存在 → 实例重绑显形。</summary>
        public static bool OpenForUnitStatic(WorldUnitBase unit)
        {
            if (unit == null) return false;
            if (!EnsureResident(ChatGlobals.WsClientInstance)) return false;
            return _resident.OpenForUnit(unit);
        }

        /// <summary>转调宿主窗口的「忙提示」（ChatLauncher.SetThinking 用：同意后立刻开窗、
        /// 首 token 前显示"对方正在斟酌…"）。宿主不在场返回 false，调用方自行兜底。</summary>
        public static bool SetBusyStatic(bool busy)
        {
            try
            {
                var r = _resident;
                if (r == null) return false;
                var w = r._window;
                if (w == null) return false;
                w.SetBusy(busy);
                return true;
            }
            catch { return false; }
        }

        /// <summary>
        /// 方案 A 契约（迁移，AbContactPanel.CloseViaManager 同款）：关闭交给游戏管理器
        /// （动画/CloseUIEnd 事件/"打开中"登记摘除全归它），绝不自己 SetActive(false)/Destroy。
        /// 历史连续性：关窗先 dispose_agent 落盘（Presenter 路径），重开 open_chat 自动 resume+回放。
        /// </summary>
        public static void CloseViaManager()
        {
            try { g.ui.CloseUI(new UIType.UITypeBase(PrefabKey, (UILayer)0), false); }
            catch (Exception e) { ModMain.P("[AbChatPanel] CloseUI: " + e.Message); }
            _resident = null;
        }

        /// <summary>一次性装配逻辑层（ChatWindow 渲染 + ChatPresenter 协议翻译 + ⚙接线），幂等</summary>
        private bool AssembleResident(WsClient ws)
        {
            try
            {
                if (_assembled) return true;
                ChatWindowRefs refs = CollectRefs(transform);
                var missing = MissingCritical(refs);
                if (missing.Count > 0)
                {
                    ModMain.P("[AbChatPanel] 预制件缺少关键节点: " + string.Join(", ", missing.ToArray()));
                    return false;
                }
                // （"压缩按钮按下去没反应"排查）：**可选节点清单一次打全**。
                // 这些节点缺失时全都不报错（按钮不存在=点了当然没反应；系统提示行缺失=提示隐形），
                // 只能靠这行日志一眼确认预制体到底有没有——配合 ChatWindow 的模板缺失告警使用。
                ModMain.P("[AbChatPanel] 可选节点清单：CompactBtn=" + (refs.CompactButton != null) +
                          " DeleteBtn=" + (refs.DeleteButton != null) +
                          " DeleteBar=" + (refs.DeleteBar != null) +
                          " ConfigBtn=" + (refs.ConfigButton != null) +
                          " StatsBar=" + (refs.StatsBarText != null) +
                          " BusyLabel=" + (refs.BusyLabel != null) +
                          " SystemLine=" + (refs.SystemTemplate != null) +
                          " Divider=" + (refs.DividerTemplate != null) +
                          " StepGroup=" + (refs.StepGroupTemplate != null));
                var rootGo = gameObject;
                _window = rootGo.GetComponent<ChatWindow>() ?? rootGo.AddComponent<ChatWindow>();
                _window.Init(refs);
                var client = ws ?? ChatGlobals.WsClientInstance;
                if (client != null)
                {
                    _presenter = rootGo.GetComponent<ChatPresenter>() ?? rootGo.AddComponent<ChatPresenter>();
                    _presenter.Init(_window, client);
                }
                // 标题栏 ⚙ → 对话内配置仅该Npc+全局（过滤由 ConfigPresenter+prompt_files 完成）
                _window.ConfigClicked += () => ConfigPanelOpener.Toggle(_window.CurrentNpcId);
                FillPlayerName();   // 装配即填玩家名：F9 直开窗（无 unit 上下文）也有名字
                // 定案（"对话窗打不开"事故）：不再初始隐藏——旧 `_window.Close` 会
                // ①根 OFF 打断 UIMgr 的 Canvas 激活流程（enabled=False 恒定 → 窗口永不可见）；
                // ②其内部 CloseAllButMapSafe=CloseAllUI 强清，把刚创建的实例自己连同游戏其他 UI
                // 一起强关销毁 → 每次打开都重复 OpenUI 重建。懒创建=用户正要看：根保持 active
                //（AbContactPanel 同款），同帧 OpenForUnit 重绑填数据。
                _assembled = true;
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbChatPanel] AssembleResident: " + e);
                return false;
            }
        }

        /// <summary>装配成败判定线：缺任一即拒绝（模板/立绘/忙标可选，ChatWindow 有运行时兜底）</summary>
        private static List<string> MissingCritical(ChatWindowRefs r)
        {
            var missing = new List<string>();
            if (r.Content == null) missing.Add("BG/Scroll/Viewport/Content");
            if (r.Scroll == null) missing.Add("BG/Scroll");
            if (r.ChatInput == null) missing.Add("BG/Input/ChatInput");
            if (r.SendButton == null) missing.Add("BG/Input/SendBtn");
            if (r.CloseButton == null) missing.Add("BG/CloseBtn（或旧位置 BG/Title/CloseBtn）");
            if (r.UserRowTemplate == null) missing.Add("BG/Templates/UserRow");
            if (r.NpcRowTemplate == null) missing.Add("BG/Templates/NpcRow");
            return missing;
        }

        /// <summary>
        /// 常驻打开：显形 + 按 NPC 重绑（历史回放/open_chat/立绘全在 Presenter 路径内）。
        /// 层级不再手动管理：宿主挂游戏 UI 层，工具唤起的确认窗/论道邀请等后开窗自然盖住
        /// 本窗、关闭后自然露出（用户拍板迁移，DramaGate 让位/恢复方案退役）。
        /// </summary>
        public bool OpenForUnit(WorldUnitBase unit)
        {
            try
            {
                if (!_assembled || _window == null)
                {
                    ModMain.P("[AbChatPanel] 常驻宿主未装配，无法打开");
                    return false;
                }
                gameObject.SetActive(true);
                // npc_id 统一发中文名（Python 侧会话/人设以中文名为键）；GetName 失败回退 unitID
                string npcId = "";
                if (unit != null)
                {
                    try { npcId = unit.data.unitData.propertyData.GetName(); } catch { }
                    if (string.IsNullOrEmpty(npcId)) npcId = unit.data.unitData.unitID;
                    // 同 ChatLauncher：这条兜底路径同样握着真身，一并钉住
                    try { UnitLookup.Pin(npcId, unit.data.unitData.unitID); } catch { }
                }
                ModMain.P("[AbChatPanel] OpenForUnit: npc=" + npcId + "，root 已显形");
                if (_presenter != null)
                {
                    _presenter.OpenForNpc(npcId);
                }
                else
                {
                    // 无 Presenter 极端兜底：只显形不清历史（正常装配下不会走到这里）
                    _window.OpenFor(npcId);
                    UnreadStore.NotifyChatOpen(npcId);
                }
                ModMain.P("[AbChatPanel] OpenForUnit: 绑定完成，填立绘");
                FillPortraits(unit);
                FillPlayerName();
                ModMain.P("[AbChatPanel] OpenForUnit: 完成");
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbChatPanel] OpenForUnit: " + e);
                return false;
            }
        }

        /// <summary>
        /// 旧直连入口兜底：本实例未装配则就地装配后重绑打开。
        /// 正常调用方（ChatLauncher）已统一走 OpenForUnitStatic，此方法仅兜底。
        /// </summary>
        public void InitData(WorldUnitBase unit)
        {
            try
            {
                if (!_assembled)
                {
                    if (!AssembleResident(ChatGlobals.WsClientInstance)) return;
                    if (_resident == null) _resident = this;
                }
                OpenForUnit(unit);
            }
            catch (Exception e)
            {
                ModMain.P("[AbChatPanel] InitData: " + e);
            }
        }

        /// <summary>玩家名填充（BG/Playerput 左上铭牌）：g.world.playerUnit 同款 GetName，失败兜底「玩家」</summary>
        private void FillPlayerName()
        {
            try
            {
                string name = "";
                try { name = g.world.playerUnit.data.unitData.propertyData.GetName(); }
                catch (Exception e) { ModMain.P("[AbChatPanel] 玩家名读取失败: " + e.Message); }
                _window?.SetPlayerName(name);
            }
            catch (Exception e) { ModMain.P("[AbChatPanel] FillPlayerName: " + e.Message); }
        }

        /// <summary>左右头像立绘预填（ChatBubble 行模板内的 Useravatar/Npcavatar RawImage）。
        /// 微信式：开窗只渲两张，玩家 → Useravatar、NPC → Npcavatar；克隆行共享该纹理，
        /// 且 ChatBubble 依据 texture 判显隐（失败/无 unit 时头像隐藏、气泡贴边）。
        /// 渲染成功后顺路入像素快照队列（PortraitCache）：延迟一帧抓 RT 落盘，供通讯录复用——
        /// 捕获寄生在本次渲染上，零额外渲染开销（通讯录头像缓存方案）。</summary>
        private void FillPortraits(WorldUnitBase unit)
        {
            try
            {
                if (_window?.UserAvatarImg != null)
                {
                    bool ok = PortraitService.Fill(g.world.playerUnit, _window.UserAvatarImg);
                    if (ok)
                    {
                        _window.UserAvatarImg.color = Color.white;
                        PortraitCache.RequestCapture(PortraitCache.IdOf(g.world.playerUnit), _window.UserAvatarImg);
                    }
                }
                if (_window?.NpcAvatarImg != null)
                {
                    bool ok = PortraitService.Fill(unit, _window.NpcAvatarImg);
                    if (ok)
                    {
                        _window.NpcAvatarImg.color = Color.white;
                        PortraitCache.RequestCapture(PortraitCache.IdOf(unit), _window.NpcAvatarImg);
                    }
                }
            }
            catch (Exception e)
            {
                ModMain.P("[AbChatPanel] portraits: " + e.Message);
            }
        }

        // ------------------------------------------------------------------
        // 预制体 → ChatWindowRefs（只做引用组装；外观完全由预制体负责）
        // ------------------------------------------------------------------
        private static ChatWindowRefs CollectRefs(Transform root)
        {
            var r = new ChatWindowRefs();
            r.Root = root.gameObject;

            r.Content = FindRt(root, "BG/Scroll/Viewport/Content");
            r.Scroll = FindComp<ScrollRect>(root, "BG/Scroll");
            r.Viewport = FindRt(root, "BG/Scroll/Viewport");
            // 名牌/按钮 2026-09-08 起从 BG/Title 挪到 BG 直下（Playerput 左上 / NpcInput 右上 / CloseBtn / ConfigBtn）；
            // 路径契约按「新位置优先、旧 BG/Title 位置回退」解析，新旧预制件都能装
            r.NpcLabel = FindComp<InputField>(root, "BG/NpcInput") ?? FindComp<InputField>(root, "BG/Title/NpcInput");
            if (r.NpcLabel != null) r.NpcLabel.readOnly = true;   // NpcInput = 只读展示当前 NPC 名（进入即自动填，不可手改切换）
            r.PlayerLabel = FindComp<InputField>(root, "BG/Playerput");
            if (r.PlayerLabel != null) r.PlayerLabel.readOnly = true;   // 玩家名只读展示，不做切换入口
            r.CloseButton = FindGo(root, "BG/CloseBtn") ?? FindGo(root, "BG/Title/CloseBtn");
            r.ConfigButton = FindGo(root, "BG/ConfigBtn") ?? FindGo(root, "BG/Title/ConfigBtn");
            r.ChatInput = FindComp<InputField>(root, "BG/Input/ChatInput");
            r.SendButton = FindGo(root, "BG/Input/SendBtn");
            r.BusyLabel = FindGo(root, "BG/BusyLabel");
            // token 统计条（可选）：StatsBar 根下有 Text 子节点按子节点取；Text 直挂根（SystemLine 同款）也认
            r.StatsBarText = FindComp<Text>(root, "BG/StatsBar/Text") ?? FindComp<Text>(root, "BG/StatsBar");
            // 删除模式（可选，缺失=无删除功能）：新位置优先、旧 BG/Title 位置回退
            r.DeleteButton = FindGo(root, "BG/DeleteBtn") ?? FindGo(root, "BG/Title/DeleteBtn");
            r.CompactButton = FindGo(root, "BG/CompactBtn");   // 可选节点，缺失=无压缩按钮（/compact 命令仍可用）
            r.DeleteBar = FindGo(root, "BG/DeleteBar");
            r.SelectAllBtn = FindGo(root, "BG/DeleteBar/SelectAllBtn");
            r.DeleteConfirmBtn = FindGo(root, "BG/DeleteBar/DeleteConfirmBtn");
            r.CancelBtn = FindGo(root, "BG/DeleteBar/CancelBtn");
            r.CountLabel = FindComp<Text>(root, "BG/DeleteBar/CountLabel");

            // 行模板（微信式：头像+气泡组成一行）+ 头像槽位（立绘预填于模板，克隆行共享）
            r.UserRowTemplate = FindGo(root, "BG/Templates/UserRow");
            r.NpcRowTemplate = FindGo(root, "BG/Templates/NpcRow");
            r.UserAvatarImg = FindComp<RawImage>(root, "BG/Templates/UserRow/Useravatar");
            r.NpcAvatarImg = FindComp<RawImage>(root, "BG/Templates/NpcRow/Npcavatar");
            r.StepGroupTemplate = FindGo(root, "BG/Templates/StepGroup");
            r.SystemTemplate = FindGo(root, "BG/Templates/SystemLine");
            r.DividerTemplate = FindGo(root, "BG/Templates/Divider");
            return r;
        }

        private static Transform FindTr(Transform root, string path)
        {
            return root != null ? root.Find(path) : null;
        }

        private static GameObject FindGo(Transform root, string path)
        {
            Transform t = FindTr(root, path);
            return t != null ? t.gameObject : null;
        }

        private static RectTransform FindRt(Transform root, string path)
        {
            var go = FindGo(root, path);
            return go != null ? go.GetComponent<RectTransform>() : null;
        }

        private static T FindComp<T>(Transform root, string path) where T : Component
        {
            var go = FindGo(root, path);
            return go != null ? go.GetComponent<T>() : null;
        }

        private void OnDestroy()
        {
            if (_resident == this) _resident = null;
            string npcId = _window != null ? _window.CurrentNpcId : "";
            UnreadStore.NotifyChatClosed(npcId);
            // 面板真销毁兜底发 dispose_agent（关窗=落盘销毁；X 关窗已在 Presenter 路径发过，幂等无害）
            ChatPresenter.DisposeAgentOnServer(ChatGlobals.WsClientInstance, npcId);
            if (_window != null) _window.Close();
        }
    }
#endif // AB_UI
}
