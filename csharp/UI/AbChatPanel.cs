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
using Newtonsoft.Json.Linq;   // JObject —— 群成员精确身份 member_unit_ids 的载体
using UnityEngine;
using UnityEngine.UI;

using static AgentLoopBridge.UiKit;
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

        /// <summary>某个群是否**正被显示**（`ChatFrameTee` 的收帧判据）。
        ///
        /// 与 `IsShowing` 同一族，但多一个"当前就是它"的判据 —— 开着**别的**群或单聊时，
        /// 这个群的帧仍然应该被缓冲（重开它时才重演）。判据必须包含 `IsRoomMode`：
        /// 单聊窗里 `CurrentRoomId` 可能是上一次的残值。
        ///
        /// ★ 失败姿态与快捷键屏蔽**相反**：异常/不确定一律返回 false（= 当作没显示）——
        /// 多缓冲的帧会被重放基线（seq 去重）兜住，而误判"在显示"会让帧真正丢失。</summary>
        public static bool IsShowingRoom(string roomId)
        {
            if (string.IsNullOrEmpty(roomId)) return false;
            var r = _resident;
            if (r == null) return false;
            try
            {
                return r._window != null && r._window.IsOpen && r._window.IsRoomMode
                       && r._window.CurrentRoomId == roomId;
            }
            catch { return false; }
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
                // 验活（GC 僵尸教训）：判空/访问都可能抛，统一丢弃重建。实现见 AbPanelLifecycle。
                if (AbPanelLifecycle.Alive(ref _resident) && _resident._assembled) return true;
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
                // 铁律：世界未就绪（日历 / playerUnit / MapMain 三条之一没到）绝不调 g.ui.OpenUI。
                // 剧情窗不在这条铁律里（09-21 删：它防的那条已被 09-10 复盘证伪，而它会把
                // 玩家亲手点的「对话」按钮拦死。来历见 AbPanelProber 文件头）。
                string gateWhy = AbPanelProber.GateReason();
                if (gateWhy.Length > 0)
                {
                    // NpcInitiativeMonitor 的"同意后开窗"是**每帧重试**（成功即止，5s 超时），
                    // 读档期本行会被连续调到 → 日志节流，并把是哪道闸门原样打出来。
                    if (Time.unscaledTime - _noCreateLogAt > 1f)
                    {
                        _noCreateLogAt = Time.unscaledTime;
                        ModMain.P("[AbChatPanel] 当前不宜创建（" + gateWhy + "），本次跳过");
                    }
                    return false;
                }
                // 四项前置（类型注册 / 核心 UI 类型 / 在线连接 / 预制体注入）——顺序与理由见
                // `AbPanelPreflight` 文件头：每一步都对应一次实机事故，不要在这里改动顺序。
                if (AbPanelPreflight.Check<AbChatPanel>(PrefabKey, true) != AbPanelPreflight.Ok) return false;
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
                UiKit.NormalizeLayout(go, "AbChatPanel");
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

        /// <summary>静态打开入口（ChatLauncher 调用）：确保宿主存在 → 实例重绑显形。
        /// `guessed` 原样透传（true = 这个对象是按名猜的，不许进登记表，见 ChatLauncher）。</summary>
        public static bool OpenForUnitStatic(WorldUnitBase unit, bool guessed = false)
        {
            if (unit == null) return false;
            if (!EnsureResident(ChatGlobals.WsClientInstance)) return false;
            return _resident.OpenForUnit(unit, guessed);
        }

        /// <summary>静态开群入口（ChatLauncher 调用）：确保宿主存在 → 交给 Presenter。
        /// 与 `OpenForUnitStatic` 同构，区别只是"绑定什么"：那边绑一个 NPC，这边切群模式。
        /// 参数合法性由 `ChatPresenter.OpenRoomById` 再校验一次（这里不重复判，免得两处漂移）。
        /// `memberUnitIds` 原样透传（建群时通讯录给的精确身份，见 ChatLauncher）。</summary>
        public static bool OpenForRoomStatic(string roomId, List<string> members, string titleHint,
                                             JObject memberUnitIds = null)
        {
            if (!EnsureResident(ChatGlobals.WsClientInstance)) return false;
            var r = _resident;
            var p = r != null ? r._presenter : null;
            if (p == null) { ModMain.P("[AbChatPanel] 宿主无 presenter，群聊窗未打开"); return false; }
            p.OpenRoomById(roomId ?? "", members, titleHint ?? "", memberUnitIds);
            return true;
        }

        /// <summary>群成员变了（拉人 / 踢人回执，通讯录侧发起）：把新名单交给对话窗。
        ///
        /// 做成静态入口的理由与 `NotifyRoomDissolvedStatic` 完全相同 —— 通讯录不该持有
        /// `ChatPresenter` 引用（那是跨面板强耦合），它只通过静态入口跟对话窗打交道。
        /// 宿主不在场就什么都不做：窗都没开，成员表的唯一消费者就是它。
        ///
        /// 判据同样复用 `AcceptsRoomFrame`（room_id 相等）：拉的是**别的群**的人，
        /// 不该动当前这个窗口的名单。
        ///
        /// `unitIds`（可选）= `{npc_id: unitID}`，C# 在选人确认那一刻从真身上取到的精确身份。
        /// 09-17 起它随 `open_room` 上行后**只留在 Python 的 `Room.member_unit_ids`**，
        /// 回包时由 Python 随帧携带（不再由 C# 灌进全局登记表，见 ChatPresenter 里那段说明）。</summary>
        public static void NotifyMembersChangedStatic(string roomId, List<string> members,
                                                     List<string> added, List<string> removed,
                                                     JObject unitIds = null)
        {
            try
            {
                var r = _resident;
                var p = r != null ? r._presenter : null;
                if (p != null) p.OnRoomMembersChanged(roomId ?? "", members, added, removed, unitIds);
            }
            catch (Exception e) { ModMain.P("[AbChatPanel] NotifyMembersChanged: " + e.Message); }
        }

        /// <summary>某个群被解散了（通讯录侧发起）：若正开着它，让对话窗回单聊外观。
        ///
        /// 做成静态入口而不是让通讯录持有 `ChatPresenter` 引用 —— 那是跨面板的强耦合，
        /// 而通讯录本来只通过 `ChatLauncher` 跟对话窗打交道。宿主不在场就什么都不做
        /// （窗都没开，没什么要复位的）。</summary>
        public static void NotifyRoomDissolvedStatic(string roomId)
        {
            try
            {
                var r = _resident;
                var p = r != null ? r._presenter : null;
                if (p != null) p.OnRoomDissolved(roomId ?? "");
            }
            catch (Exception e) { ModMain.P("[AbChatPanel] NotifyRoomDissolved: " + e.Message); }
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
                          " InviteBtn=" + (refs.InviteButton != null) +
                          " KickBtn=" + (refs.KickButton != null) +
                          " SkipBtn=" + (refs.SkipButton != null) +
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
                // ③ 粘滞位置位：能在这个世界里把对话宿主装配起来 = 世界 HUD 早就是好的
                //（战斗场景等"无 MapMain"场景里的重建靠它放行，见 AbPanelProber 文件头）
                try { AbPanelProber.NoteWorldHudAlive("对话宿主装配成功"); } catch { }
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
        public bool OpenForUnit(WorldUnitBase unit, bool guessed = false)
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
                    // 同 ChatLauncher：这条兜底路径同样握着真身，一并登记 —— 但**只有不是猜的**才登记。
                    // （正常装配下 ChatLauncher.OpenForUnit 已经登记过；Remember 幂等，值没变不写盘。）
                    if (!guessed) { try { UnitIdRegistry.Remember(npcId, unit.data.unitData.unitID, "打开对话(AB)"); } catch { } }
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
        /// 按**名字**开窗（没有真身）—— 死者 / unitID 已失效的通讯录条目走这条。
        ///
        /// 为什么必须存在：死者 `GetUnit(id)` 恒 null，旧路径在 `ContactPresenter.OpenChatFor`
        /// 就返回了（"已不在世间"），于是**旧对话永远打不开**。而 Python 侧的 `open_chat`
        /// 只认 `npc_id` 字符串（`unit_id` 是可选键），账本按名字从 jsonl 恢复 ——
        /// 也就是说下层本来就支持，缺的只是这个"不要求真身"的入口。
        ///
        /// 立绘：走 `RowAvatars.Apply`（它自带"有缓存就贴、没有就排队读盘、再没有就隐藏"），
        /// 于是**死前截过图的人还能看到脸**；`PortraitService.Fill` 那条路需要活体，不能用。
        /// ★ 不写 `UnitIdRegistry`（无真身，写进去就是把猜测洗成权威）★
        /// </summary>
        public bool OpenForName(string npcId, string unitId = null)
        {
            try
            {
                if (!_assembled || _window == null)
                {
                    ModMain.P("[AbChatPanel] 常驻宿主未装配，无法打开");
                    return false;
                }
                string id = (npcId ?? "").Trim();
                if (string.IsNullOrEmpty(id)) return false;
                gameObject.SetActive(true);
                ModMain.P("[AbChatPanel] OpenForName: npc=" + id + " unitId=" + (unitId ?? "-")
                          + "（无真身路径：死者/失效条目）");
                if (_presenter != null)
                {
                    _presenter.OpenForNpc(id);
                }
                else
                {
                    _window.OpenFor(id);
                    UnreadStore.NotifyChatOpen(id);
                }
                // 玩家侧立绘照旧（自己总是活着）；NPC 侧只能走缓存
                try
                {
                    if (_window.UserAvatarImg != null)
                        PortraitService.Fill(g.world.playerUnit, _window.UserAvatarImg);
                }
                catch { }
                try
                {
                    if (_window.NpcAvatarImg != null)
                        RowAvatars.Apply(_window.NpcAvatarImg, id, string.IsNullOrEmpty(unitId) ? null : unitId);
                }
                catch (Exception e) { ModMain.P("[AbChatPanel] OpenForName 立绘: " + e.Message); }
                FillPlayerName();
                return true;
            }
            catch (Exception e)
            {
                ModMain.P("[AbChatPanel] OpenForName: " + e);
                return false;
            }
        }

        /// <summary>静态入口（`ChatLauncher` 调用）：确保宿主存在 → 按名开窗。</summary>
        public static bool OpenForNameStatic(string npcId, string unitId = null)
        {
            if (string.IsNullOrEmpty(npcId)) return false;
            if (!EnsureResident(ChatGlobals.WsClientInstance)) return false;
            return _resident.OpenForName(npcId, unitId);
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
        /// 微信式：开窗只渲两张，玩家 → Useravatar、NPC → Npcavatar；**单聊**的克隆行共享该纹理
        /// （群聊不共享：每行按说话人逐行取图，见 `RowAvatars`），
        /// 且 ChatBubble 依据 texture 判显隐（失败/无 unit 时头像隐藏、气泡贴边）。
        /// 渲染成功后顺路入像素快照队列（PortraitCache）：延迟一帧抓 RT 落盘，供通讯录/群聊复用——
        /// 捕获寄生在本次渲染上，零额外渲染开销（通讯录头像缓存方案）。
        ///
        /// ★ 渲染前必须先撤掉在飞的全部捕获任务（2026-09-16）：这两个槽位是**共用**的，
        ///   而"排队 → 延迟 0.15s 抓图"意味着**旧任务到期时读到的可能是这次刚渲进去的脸**。
        ///   快速连开两个 NPC 时就会把后一个的脸存成前一个的 id（静默错脸，从此固化在盘上）；
        ///   群成员立绘预热也与单聊共用 Npcavatar 槽位，同理。撤掉 = 那些任务的目标画面已失效。</summary>
        private void FillPortraits(WorldUnitBase unit)
        {
            try
            {
                RoomPortraitWarmup.Stop();          // 与单聊共用槽位：先停预热
                PortraitCache.CancelAllCaptures();  // 再撤掉所有指向这两个槽位的在飞任务
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
                    // 摘掉可能残留的"按名开窗（死者）"待贴任务 —— 否则下一帧它会把
                    // 死者的缓存立绘盖到这位活人身上（看得见、但画错了人，零报错）
                    RowAvatars.ClearSlot(_window.NpcAvatarImg);
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
            // ★ BG 本身（10-19 补）：此前 AB 路径**从不填** `refs.Window`，那在代码版构建器里
            //   是「窗口面板」的意思，而 AB 预制体版把它换成了根节点 —— 于是 `refs.Window` 恒 null。
            //   右侧「可做的事」侧边栏与顶栏「工具」钮都要以 BG 为坐标基准（宽 1200×800 的那个
            //   面板），拿根节点（全屏 stretch）算出来的位置整个是错的。这里如实填 BG。
            r.Window = FindRt(root, "BG");

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
            // 群聊跳过键（可选节点，缺失=没有这个入口）：与发送键同列、正上方 8px，出厂隐藏
            r.SkipButton = FindGo(root, "BG/Input/SkipBtn");
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
            // 群聊动作按钮（P3 第 4 步）：可选节点，缺失=两个入口都没有（单聊窗不受影响）
            r.InviteButton = FindGo(root, "BG/InviteBtn");
            r.KickButton = FindGo(root, "BG/KickBtn");

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
