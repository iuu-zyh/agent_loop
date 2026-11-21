/// <summary>
/// 配置 UI 控制器（MonoBehaviour，仿 ChatPresenter）：
/// 开面板即拉 get_config / list_prompts 回填；保存走 set_config / write_prompt / create_persona；
/// 按 effective 字段提示生效方式（hot=即时 / restart=需重启 / mixed=两者 / none=无改动）。
///
/// 各司其职：不建 UI 节点、不管布局（那是 ConfigUiBuilder 的事）；不碰传输细节（WsClient 的事）。
/// 所有 RPC 均经 SendRequest（C#→Python 读方向，req_id 配对，回调恒主线程）。
///
/// IL2CPP 约定：与 ChatPresenter 同规约——主工程需显式 RegisterTypeInIl2Cpp；
/// 按钮回调经 ClickUtils 三步写法；遍历子节点用索引循环（foreach 会抛 InvalidCastException）。
/// </summary>
using System;
using System.Globalization;
using UnityEngine;
using UnityEngine.UI;
using Newtonsoft.Json.Linq;

namespace AgentLoopBridge
{
    public class ConfigPresenter : MonoBehaviour
    {
        /// <summary>IL2CPP 互操作标准构造</summary>
        public ConfigPresenter(IntPtr ptr) : base(ptr) { }

        private ConfigPanelRefs _refs;
        private WsClient _ws;
        private string _currentPath = "";   // 当前选中的提示词文件（rel path）
        private string _currentNpcId = "";  // 当前对话Npc（对话内⚙传入，F11传入空=全量）
        private System.Collections.Generic.Dictionary<string, string> _promptCache = new System.Collections.Generic.Dictionary<string, string>(); // path->edited text 全量缓存
        private JArray _lastGroups = null; // 最近一次 list_prompts 结果，供批量提交枚举
        private UnityEngine.Events.UnityAction<string> _onPromptEditedAction;
        private System.Collections.Generic.Dictionary<string, bool> _deletable = new System.Collections.Generic.Dictionary<string, bool>(); // path->deletable（list_prompts 下发；缺=本地规则兜底）
        // 两个不可恢复按钮的两段确认（首击武装 / 3 秒内再击执行 / 换目标即解除）。
        // 判定抽在 TwoStepConfirm（零 Unity 纯逻辑，net8.0 单测锁边界：恰好 3.0 秒、过期重来、执行后不残留武装）。
        private readonly TwoStepConfirm _deleteConfirm = new TwoStepConfirm();
        private readonly TwoStepConfirm _resetConfirm = new TwoStepConfirm();
        // path->resettable（list_prompts 下发；**缺字段时乐观为 true**，权威判定在 Python）——
        // 与 _deletable 的兜底方向刻意相反：删除那边宁可保守（系统件别显示成可删），
        // 重置这边宁可乐观（漏判只会得到一句诚实的报错，而"灰掉一个本来能重置的文件"更糟：
        // 用户新建的 personas/{npc}.txt 与随包发的 {npc}.txt 在名字上分不出来）。
        private System.Collections.Generic.Dictionary<string, bool> _resettable = new System.Collections.Generic.Dictionary<string, bool>();

        // ---- 「自我人设」虚拟条目（09-16）----
        // 提示词页列表里的一条**不是文件**的条目：path 前缀 `config:`，背后是 config.json 的
        // player.persona（玩家自述，注入 system 的一段，见 Python system_prompt.set_player_persona）。
        // 为什么挂这里：人设要能在面板里改，而"提示词"页已有现成的多行编辑器 + 保存键；
        // 挂提示词页 = 不动预制件、不重打 AB（配置页那个 Content 是**绝对定位**的，
        // 往里插一行得重排其后所有行的 y 坐标）。
        // `config:` 前缀是与 Python `prompt_files.VIRTUAL_PREFIX` 的对外契约：带前缀的路径
        // **绝不**发给 read_prompt / write_prompts（那边 fail-closed 会拒掉整批），改走
        // get_config / set_config。改前缀必须同时改 prompt_files.py。
        private const string VirtualConfigPrefix = "config:";
        private string _playerPersona = "";  // get_config 拿到的 player.persona（**文件真相**；未保存的编辑在 _promptCache 里）

        // ---- 09-13 起：所有参数行常显（分组标题条 + 整页滚动），不再有"高级组隐藏"这种状态 ----

        // ---- Python 重启编排（effective=restart/mixed 或热换失败 → 自动重启 + 重连）----
        // 判据用「连接状态 + 计时兜底」，**不做端口探测**：
        //   旧实现用 TcpClient.BeginConnect + AsyncWaitHandle.WaitOne(400) 判「端口已释放」，
        //   但本机对「无监听端口」的 connect 不是立刻 RST：SYN 被丢一次、约 2s 后才回 ConnectionRefused
        //   （WsClient 的失败重连每轮 ~4s = 2s connect + 2s sleep，即同一现象）。400ms 窗口必然超时，
        //   于是 probe 恒返回「占用」→ 状态机永远卡在等待，既不放行拉起也不报错（09-12 实锤：
        //   Player.log 中 `owned python 已强制结束` 刷了 191 行，而 agent_loop.log 里新进程从未起来）。
        //   现已删掉该探测（Launcher.ProbePortFree）——进程死没死由 WsClient 的连接状态说话。
        private enum RestartStage { None, WaitShutdown, WaitReconnect }
        private RestartStage _restartStage = RestartStage.None;
        private float _absTimer;                     // 本阶段计时（只在阶段切换时清零）
        private float _statusTimer;                  // 状态条刷新节流（每 0.25s 更新一次耗时）
        private int _launchCount;                    // 已拉起次数（有界：最多 MAX_LAUNCH 次）
        private bool _killTried;                     // 本轮是否已尝试强杀 owned 进程
        private bool _sawDisconnect;                 // 本轮是否亲见连接断开（=「配置已生效」的必要证据）
        private const float KILL_AT = 2f;            // 连接还在且旧进程 owned 时，先强杀的时点
        private const float EXIT_HARD_LIMIT = 6f;    // 硬上限：到点无条件拉起（旧进程僵住时的兜底）
        private const float RELAUNCH_AGAIN_AT = 4f;  // 首拉后仍未重连 → 补拉一次（旧进程可能刚占着端口）
        private const float RECONNECT_GRACE = 18f;   // 拉起后等 WS 重连上限
        private const int MAX_LAUNCH = 2;
        private bool _portChangedDuringRestart;      // 端口被改时 C# 仍连旧端口，需整机重启
        private int _restartPort = ModConfigFile.DefaultPort;  // 本轮重启对应的端口（关停前旧端口）
        // 「测试连接」在途标志：防止连点发出多次真实 API 调用（每次都是钱 + 可能撞限流）
        private bool _testInflight;
        // 能力探测（09-21）：最近一次「测试连接」带回的 reasoning_effort 探测结论
        // （test_llm 回包 data.probe）。保存时随表单落盘到 config.llm.probe ——
        // Python factory 的注入门控靠它判断"这个键在当前网关/模型上真实可用"。
        // 测试失败时不更新（结论无从产生，保留旧值没有意义但也无害：指纹不符就不注入）。
        private JObject _lastProbe;
        // 「思考强度」行当前是否显示。默认隐藏：探测支持才显示（见 ApplyReasoningRowVisibility）。
        // 保存侧按它决定提不提交该键 —— 行没显示就绝不提交，绝不用隐藏行的空值冲掉手编配置。
        private bool _reasoningRowShown;

        // 状态栏两行机制（09-20）：第一行 = 操作反馈，第二行 = 粘性连接诊断。
        // 字段在这里声明（与其它状态位同处），渲染逻辑见文件末尾 RenderStatus/SetStickyDiag。
        private string _lastStatusMsg = "";
        private Color _lastStatusColor = StatusDim;
        private string _stickyDiag = "";

        // ---- 悬停气泡（文件说明 / 参数含义）：引擎在 HoverTip（纯视图件，无主工程依赖）----
        private HoverTip _tip;

        // 状态条配色（羊皮纸浅底）：深色系才可读，浅色系（旧深底面板遗留）在纸底上糊成一片
        private static readonly Color StatusDim = new Color(0.32f, 0.30f, 0.27f, 1f);
        private static readonly Color StatusOk = new Color(0.16f, 0.45f, 0.20f, 1f);
        private static readonly Color StatusWarn = new Color(0.62f, 0.38f, 0.05f, 1f);

        public void Init(ConfigPanelRefs refs, WsClient ws)
        {
            _refs = refs;
            _ws = ws;
            if (_refs?.Window != null) _refs.Window.gameObject.SetActive(false);

            ClickUtils.Attach(GetButton(_refs?.CloseButton), Hide);
            ClickUtils.Attach(GetButton(_refs?.TabLlmButton), () => SwitchTab(true));
            ClickUtils.Attach(GetButton(_refs?.TabPromptButton), () => SwitchTab(false));
            ClickUtils.Attach(GetButton(_refs?.SaveLlmButton), SaveConfig);
            // 「测试连接」：拿表单当前值真调一次模型，结果显示在底部状态条。
            // 按钮节点可空（老 AB 没有它）—— ClickUtils.Attach 对 null 安全，
            // 于是老预制件上这个功能只是不出现，其余一切照常。
            ClickUtils.Attach(GetButton(_refs?.TestLlmButton), TestLlm);
            ClickUtils.Attach(GetButton(_refs?.SavePromptButton), SavePromptFile);
            ClickUtils.Attach(GetButton(_refs?.NewNpcButton), CreatePersona);
            if (_refs?.DeletePromptButton != null)
                ClickUtils.Attach(GetButton(_refs.DeletePromptButton), DeleteCurrentFile);
            // 「重置为默认」（09-16）：把选中文件还原成随包内置的那一份（Python reset_prompt）。
            // 节点可空（老 AB 没有它）—— ClickUtils.Attach 对 null 安全，于是旧预制件上这个功能
            // 只是不出现，其余一切照常（与 TestLlmButton / DeletePromptButton 同款降级）。
            if (_refs?.ResetPromptButton != null)
                ClickUtils.Attach(GetButton(_refs.ResetPromptButton), ResetCurrentFile);
            System.Action<string> act = OnPromptEdited;
            _onPromptEditedAction = act;
            if (_refs?.PromptInput != null)
                _refs.PromptInput.onValueChanged.AddListener(_onPromptEditedAction);
            EnsurePromptLayoutBroken();

            // 悬停气泡：实例（预制体模板优先，缺失现造）+ 登记参数行说明
            _tip = HoverTip.Ensure(this, _refs?.Root, _refs?.TooltipGo);
            RegisterFormTooltips();

            // 行文案对齐实际生效方式（预制件里写死的是「重启生效」）：
            // 起 RPC 超时 / ctx_window 在 Python 侧热生效；起主动互动那一组（含总开关）
            // 也在 C# 侧热生效（保存后立刻重拉 get_config → Configure），唯一还需重启的只剩 Python 端口。
            RelabelRow(_refs?.HeadersInput, "自定义请求头（可空，即时生效）");
            RelabelRow(_refs?.TimeoutInput, "RPC 超时秒（即时生效）");
            RelabelRow(_refs?.CtxWindowInput, "上下文窗口 tokens（即时生效）");
            RelabelRow(_refs?.MinIntervalInput, "全局熔断秒（即时生效）");
            RelabelRow(_refs?.DailyChanceInput, "每日触发概率 0-100（即时生效）");
            RelabelRow(_refs?.NpcCooldownRealInput, "单人现实冷却秒（即时生效）");
            RelabelRow(_refs?.NpcCooldownDaysInput, "单人游戏日冷却（即时生效）");
            RelabelRow(_refs?.LowThreshInput, "低好感阈值（即时生效）");
            RelabelRow(_refs?.LowHalveToggle, "低好感减半（即时生效）");
            RelabelRow(_refs?.RetainRatioInput, "保留原文比例 0.05-0.6（即时生效）");
            RelabelRow(_refs?.ThresholdRatioInput, "自动压缩阈值 0.3-0.95（即时生效）");
            RelabelRow(_refs?.InitiativeEnabledToggle, "主动互动总开关（即时生效）");
            RelabelRow(_refs?.CompactionEnabledToggle, "自动压缩（即时生效）");
            RelabelRow(_refs?.PortraitToggle, "显示立绘（即时生效）");

            // 修复 小数参数行的输入框校验类型。
            // 根因：AB 预制件里 RetainRatioRow / ThresholdRatioRow 是**从 PortRow 克隆**出来的
            //   （scripts/dev/prefab_patch_config_groups.py：`src = find_by_name(content, "PortRow")`），
            //   克隆脚本只改了 Label 文本与 Placeholder，**没改 m_CharacterValidation**，
            //   于是这两个小数框继承了整数框的 Integer 校验。后果有两个，实机都撞到了：
            //     ① 小数点打不进去（Integer 校验器对 '.' 返回 0 = 丢弃）；
            //     ② 更隐蔽：uGUI 的 InputField.SetText 会把每个字符过一遍**同一个**校验器
            //        （characterValidation != None 时走逐字符 Append 分支），
            //        所以 get_config 回填 "0.16" 被滤成 "016" → 保存时按 16 解析 → 撞 0.05-0.6 越界，
            //        提示「保留原文比例需 0.05-0.6」，**怎么改都存不下去**。
            //   threshold_ratio 同理（"0.8" → "08" → 8.0，撞 0.3-0.95）。
            // 就地纠正即可，**不依赖重打 AB**：这样手上这份老 AB 也能救（同 EnsureScrollInputTarget 的思路）。
            // 位置很关键——必须早于 Show() 里的 RequestConfig()，否则回填那一刻又会被滤一遍。
            EnsureDecimalInput(_refs?.TimeoutInput, "RPC 超时");
            EnsureDecimalInput(_refs?.MinIntervalInput, "全局熔断");
            EnsureDecimalInput(_refs?.RetainRatioInput, "保留原文比例");
            EnsureDecimalInput(_refs?.ThresholdRatioInput, "自动压缩阈值");
        }

        private void OnPromptEdited(string v)
        {
            if (!string.IsNullOrEmpty(_currentPath))
                _promptCache[_currentPath] = v ?? "";
            UpdatePromptLayout();   // 打字/换行实时撑高（断死锁后的高度自管）
        }

        // ------------------------------------------------------------------
        // PromptInput 多行高度自管（死锁修复）
        //
        // 根因：uGUI InputField.UpdateLabel 以「Text 组件自身 rect」为生成范围，
        // 只把可视窗口内的行写回 Text（uGUI 源码 InputField.cs L2329-2345 的
        // Substring(m_DrawStart..m_DrawEnd)）。而 Content 的 VerticalLayoutGroup
        // (ChildControlHeight=1)+ContentSizeFitter 又把 Text.rect 高度驱动成
        // 「当前 label 的 preferred 高度」——label 初始一行 → rect 一行 → 生成
        // 范围一行 → label 永远一行，稳定死锁。纯预制体组件无解（官方机制如此）。
        //
        // 修复：Init 时断开反馈边（ctrlHeight=0 + fitter 禁用），高度改由本类在
        // 文本变化时用 TextGenerator 对全文量一次、直写 Text 与 Content 尺寸。
        // ------------------------------------------------------------------
        private void EnsurePromptLayoutBroken()
        {
            var content = _refs?.PromptScroll != null ? _refs.PromptScroll.content : null;
            if (content == null) return;
            var group = content.GetComponent<VerticalLayoutGroup>();
            if (group != null) group.childControlHeight = false;   // 不再用 label 的 preferred 驱动 Text 高度
            var fitter = content.GetComponent<ContentSizeFitter>();
            if (fitter != null) fitter.enabled = false;            // Content 高度改由 UpdatePromptLayout 直写
        }

        /// <summary>页面还没显形时量不到宽度 —— 记一笔，下一帧 Update 里重试（见字段注释）。</summary>
        private bool _promptLayoutPending;
        private bool _promptLayoutLogged;

        private void UpdatePromptLayout()
        {
            var input = _refs?.PromptInput;
            var scroll = _refs?.PromptScroll;
            if (input == null || input.textComponent == null || scroll?.content == null) return;
            var trt = input.textComponent.rectTransform;
            // 生成宽度 = 视口宽 - Content 左右内边距（预制体 10+10）
            //
            // ★ 真机 11-10：提示词页在预制件里出厂是 **inactive**（`PagePrompt.m_IsActive=0`），
            //   而「自我人设」是在面板一打开就把文本灌进来的 —— 那一刻视口宽 = 0，
            //   旧实现 `if (width <= 0f) return;` 直接放弃 ⇒ Text 高度停在默认一行，
            //   切到提示词页后也没人再量一次 ⇒ 玩家看到"文字高度没展开"。
            //   修法：宽度量不到时**记一笔挂起重试**（Update 里每帧补一次，量到就落地），
            //   并回落到 InputField 自己的 rect 宽（页面刚 SetActive 的那一帧视口还没结算，
            //   但输入框自身的 rect 往往已经是最终值）。
            float vw = scroll.viewport != null ? scroll.viewport.rect.width : 0f;
            if (vw <= 1f)
            {
                var irt = input.GetComponent<RectTransform>();
                if (irt != null) vw = irt.rect.width;
            }
            if (vw <= 1f)
            {
                _promptLayoutPending = true;
                return;
            }
            _promptLayoutPending = false;
            float width = vw - 20f;
            if (width <= 0f)
            {
                _promptLayoutPending = true;
                return;
            }
            // 顶部拉伸：高度向下长（input.text 是全量，label 才是被 InputField 截断的显示副本）
            trt.anchorMin = new Vector2(0f, 1f);
            trt.anchorMax = new Vector2(1f, 1f);
            trt.pivot = new Vector2(0.5f, 1f);
            var gen = new TextGenerator();
            var settings = input.textComponent.GetGenerationSettings(new Vector2(width, 1000000f));
            float h = 0f;
            if (gen.Populate(input.text ?? "", settings) && gen.lineCount > 0)
            {
                float top0 = gen.lines[0].topY;
                var last = gen.lines[gen.lineCount - 1];
                h = top0 - (last.topY - last.height);   // 首行顶 到 末行底 的距离 = 全文高度
            }
            if (h <= 0f) h = input.textComponent.preferredHeight;
            h += 2f;    // 余量，防最后一行差半像素被窗口裁掉
            trt.sizeDelta = new Vector2(trt.sizeDelta.x, h);
            scroll.content.sizeDelta = new Vector2(scroll.content.sizeDelta.x, h + 4f);   // 加 Content 上下 padding 各 2
            if (!_promptLayoutLogged)
            {
                _promptLayoutLogged = true;
                // 一次性几何：再出"高度没展开"时，这行能直接分清是**没量到**还是**量错了**
                ModMain.P("[ConfigPresenter] 提示词高度：视口宽=" + vw.ToString("F0")
                          + " 生成宽=" + width.ToString("F0") + " 文本高=" + h.ToString("F0")
                          + " 内容高=" + (h + 4f).ToString("F0") + " 行数="
                          + (input.textComponent != null && !string.IsNullOrEmpty(input.text) ? "见上" : "空"));
            }
        }

        private void Update()
        {
            // 迁移：F11 轮询与 Python 重启 tick 已移到常驻的 AbHotkeys（ModMain 的
            // g.timer.Frame 回调）——本组件所在根节点在关闭时是 OFF 的（防屏蔽世界输入），
            // Update 不再运行，因此不能再把常驻职责放在这里。
            TickGeometryProbe();
            // 提示词高度挂起重试：页面在预制件里出厂 inactive，量到宽度才能撑开。
            // 每帧一次 rect 读取，代价可忽略；量到即落地（_promptLayoutPending 归 false）。
            if (_promptLayoutPending) UpdatePromptLayout();
        }

        // 打开后第一帧打一行滚动几何（每会话一次）。排障用：**"滚不动"只有两种可能**——
        // ① 没有可滚动余量（内容高 ≤ 视口高）；② 事件进不来（ScrollRect 上没有可命中的 Graphic）。
        // 这行日志把两者一次说清，省得再靠推理（实机就栽在第②种）。
        private bool _geomProbe;
        private bool _geomLogged;

        private void TickGeometryProbe()
        {
            if (!_geomProbe || _geomLogged) return;
            _geomProbe = false;
            _geomLogged = true;
            try
            {
                var s = _refs?.LlmScroll;
                if (s == null) { ModMain.P("[ConfigPresenter] 滚动几何：LlmScroll=null（预制件没有滚动区）"); return; }
                var vp = s.viewport;
                var ct = s.content;
                var img = s.GetComponent<Image>();
                float vh = vp != null ? vp.rect.height : -1f;
                float ch = ct != null ? ct.rect.height : -1f;
                ModMain.P("[ConfigPresenter] 滚动几何：视口=" + vh.ToString("F0") + " 内容=" + ch.ToString("F0") +
                          " 余量=" + (ch - vh).ToString("F0") + "（>0 才滚得动） vertical=" + s.vertical +
                          " 灵敏度=" + s.scrollSensitivity +
                          " 命中层=" + (img != null && img.raycastTarget ? "有" : "无（滚轮/拖动进不来）"));
            }
            catch (System.Exception e) { ModMain.P("[ConfigPresenter] 滚动几何探测: " + e.Message); }
        }

        /// <summary>Python 重启状态机 tick（由 AbHotkeys 每帧调用；关闭态也要继续推进）。</summary>
        internal void TickRestart()
        {
            TickPythonRestart();
            TickStickyDiagRefresh();
        }

        /// <summary>
        /// 面板**开着**的时候，新的拉起失败也要能冒出来（自查发现并补齐）。
        ///
        /// 为什么需要：诊断目前只在 `RequestConfig()` 失败那一刻打一次。若面板已经开着、
        /// 玩家没再点任何按钮，而 BrainLink 那边又拉起失败了一次（`Launcher.FailureCount` 增长），
        /// 第二行就会**停在旧状态**（或干脆是空的）—— 与"每次失败都要弹"的要求不符。
        /// 横幅那边是独立驱动的（BrainLink.TickFailureBanner），面板这边得自己跟上。
        ///
        /// 判据用 `Launcher.FailureCount` 做水位线（与横幅同款思路）：只在**真的又失败了**时重画，
        /// 不每帧无脑重算（省得把一个已经正确的显示反复覆盖）。
        /// </summary>
        private void TickStickyDiagRefresh()
        {
            int fails;
            try { fails = Launcher.FailureCount; } catch { return; }
            if (fails == _diagRefreshedAt) return;    // 没有新失败，不折腾
            // 面板没开就别写（`AbConfigPanel.Tick` 只看宿主存活，关闭态也会调到这儿）。
            // **不推进水位线**：留到玩家真正打开面板时再补 —— 否则这次失败就没人看见，
            // 而下次打开面板时 `RequestConfig` 若恰好成功（重拉成功了），诊断就永远不出现。
            if (!IsPanelOpen) return;
            _diagRefreshedAt = fails;
            // 有新失败 → 补一条诊断（若此刻其实连着，就别吓唬玩家：连得上说明只是历史失败）
            bool connected = false;
            try { connected = _ws != null && _ws.IsConnected; } catch { }
            if (connected)
            {
                // 连上了：不该显示故障诊断。清掉（历史失败仍留在 _diag_last_failure.txt 里）
                if (_stickyDiag.Length > 0) SetStickyDiag("");
                return;
            }
            ShowConnectionDiagnosis();
        }

        /// <summary>已据以刷新过诊断的失败计数（水位线）。-1 = 本轮尚未同步过。</summary>
        private int _diagRefreshedAt = -1;

        private void OnDestroy()
        {
            _tip = null;
            _refs = null;
            _ws = null;
        }

        // ------------------------------------------------------------------
        // 开关 / Tab（AB 版 AbConfigPanel 也会调这几个公开门）
        // ------------------------------------------------------------------

        /// <summary>
        /// 面板是否处于显示状态（供 Opener/AB 宿主查询，也是 `AbConfigPanel.IsShowing` 的判据）。
        ///
        /// 必须用 `activeInHierarchy` 而不是 `activeSelf`：
        /// 关闭面板走 `CloseViaManager` → `g.ui.CloseUI`，管理器是**把实例根节点置灰**（或销毁），
        /// 而 `BG` 自身的 `activeSelf` 是"本地开关"——根灰了它照样是 true。于是关掉面板之后
        /// 一切问 `activeSelf` 的地方都把"已关"读成"已开"：
        ///   · `HandleToggle` 会走进"已开"分支 → 配置面板**再也打不开**；
        ///   · `OwnUiProbe` 会以为面板一直开着 → **游戏快捷键被永久屏蔽**。
        /// `activeInHierarchy` 会把父链的激活状态一并算进来，正好是"屏幕上有没有"的真相。
        /// </summary>
        public bool IsPanelOpen => _refs?.Window != null && _refs.Window.gameObject.activeInHierarchy;

        /// <summary>打开面板并拉取配置/文件列表（npcId空=全量；非空=仅全局+该Npc）</summary>
        public void ShowPanel(string npcId = null)
        {
            if (_refs?.Window == null) return;
            _currentNpcId = (npcId ?? "").Trim();
            Show();
        }

        /// <summary>隐藏面板</summary>
        public void HidePanel()
        {
            Hide();
        }

        /// <summary>切换显隐（F11 / ⚙ 的实际执行者）</summary>
        public void TogglePanel()
        {
            if (IsPanelOpen) HidePanel();
            else ShowPanel();
        }

        private void Toggle()
        {
            if (_refs?.Window == null) return;
            if (_refs.Window.gameObject.activeSelf) { Hide(); return; }
            Show();
        }

        /// <summary>对话内带Npc打开（⚙按钮）</summary>
        public void ShowPanelForNpc(string npcId) => ShowPanel(npcId);

        private void Show()
        {
            // 修复：关闭态根 OFF，打开前必须先把根激活；并提到层顶（挡地图 HUD）
            gameObject.SetActive(true);
            _refs.Window.gameObject.SetActive(true);
            _geomProbe = true;                      // 下一帧打一行滚动几何（见 TickGeometryProbe）
            UpdateNewNpcButton();
            SetStatus("加载配置中…", StatusDim);
            RequestConfig();
            RequestPrompts();
        }

        /// <summary>新建人设按钮随上下文换文案：带 Npc = 为「X」新建人设；F11 全局视图 = 提示需从对话内打开</summary>
        private void UpdateNewNpcButton()
        {
            var lbl = _refs?.NewNpcButton != null ? _refs.NewNpcButton.GetComponentInChildren<Text>(true) : null;
            if (lbl == null) return;
            lbl.text = string.IsNullOrEmpty(_currentNpcId)
                ? "新建人设（对话内⚙打开可用）"
                : "为「" + _currentNpcId + "」新建人设";
        }

        private void Hide()
        {
            // Python 重启进行中禁止关面板：重启状态机在实例上，中途销毁会丢流程
            if (_restartStage != RestartStage.None)
            {
                SetStatus("Python 重启中，完成后再关闭…", StatusWarn);
                return;
            }
            // 方案 A 契约：关闭交给游戏管理器（动画/CloseUIEnd/登记摘除全归它）。
            AbConfigPanel.CloseViaManager();
        }

        private void SwitchTab(bool llmTab)
        {
            if (_refs?.PageLlm == null || _refs.PagePrompt == null) return;
            _refs.PageLlm.SetActive(llmTab);
            _refs.PagePrompt.SetActive(!llmTab);
            // 切到提示词页：页面刚显形，视口宽这一帧可能还是 0 —— 先直接量一次，
            // 量不到就挂起，由 Update 下一帧补（真机 11-10「自我人设高度没展开」的落点）。
            if (!llmTab) UpdatePromptLayout();
            _tip?.ResetHover();
            // ：大模型页现在整页可滚（行数 13→22）。切回来时回到顶部——
            // 否则上次滚到底、再进来看到的是最后几行，会以为前面的参数没了。
            if (llmTab && _refs.LlmScroll != null) _refs.LlmScroll.verticalNormalizedPosition = 1f;
        }

        // ------------------------------------------------------------------
        // 大模型设置（get_config / set_config）
        // ------------------------------------------------------------------

        /// <summary>
        /// 配置读不到时，把「为什么读不到 + 该怎么办」贴成**粘性尾行**（09-20 新增）。
        ///
        /// 信息来源按精度排序（越靠前越具体，优先用）：
        ///   ① `Launcher.LastFailure/Hint` —— 拉起**失败过**时最精确（缺 exe / 被杀软拦 / 路径错），
        ///      这是本次新增的通道；以前这些信息只在 Player.log 里；
        ///   ② `BrainLink.StatusText` —— 连接状态机的结论（"反复停止，已放弃自动重启"等）；
        ///   ③ 兜底一句通用提示。
        ///
        /// 文案预算：状态栏第二行约 48 字（实测 736px / 字号 14）。超出会被**静默裁掉**，
        /// 所以这里做硬截断并显式加省略号 —— 宁可少说，也不要让玩家以为自己看到了全部。
        /// </summary>
        private void ShowConnectionDiagnosis()
        {
            string diag;
            if (Launcher.EverFailed && !string.IsNullOrEmpty(Launcher.LastFailure))
            {
                diag = Launcher.LastFailure;
                if (!string.IsNullOrEmpty(Launcher.LastFailureHint))
                    diag += " —— " + Launcher.LastFailureHint;
            }
            else
            {
                string why = "";
                try { why = BrainLink.StatusText; } catch { }
                if (string.IsNullOrEmpty(why))
                {
                    try { why = BrainLink.SendBlockedNotice(); } catch { }
                }
                diag = string.IsNullOrEmpty(why)
                    ? "未连接 Python —— 详见 _diag_last_failure.txt"
                    : why + " —— 详见 _diag_last_failure.txt";
            }
            const int budget = 48;
            if (diag.Length > budget) diag = diag.Substring(0, budget - 1) + "…";
            SetStickyDiag("⚠ " + diag);
            // 完整现场进日志（玩家贴日志时这两行是关键索引）
            try
            {
                P("[ConfigPresenter] 连接诊断：state=" + BrainLink.State
                  + " owned=" + Launcher.IsOwned
                  + " attempted=" + Launcher.LaunchAttempted
                  + " entry=" + Launcher.EntryResolved
                  + " failures=" + Launcher.FailureCount
                  + " lastFail=" + Launcher.LastFailure);
            }
            catch { }
        }

        private void RequestConfig()
        {
            if (_ws == null) { SetStatus("未连接 Python（WsClient 未启动）", StatusWarn); ShowConnectionDiagnosis(); return; }
            _ws.SendRequest("get_config", new JObject(), resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    SetStatus("读取配置失败: " + (resp["error"]?.ToString() ?? "未知错误"), StatusWarn);
                    ShowConnectionDiagnosis();
                    return;
                }
                var data = resp["data"] as JObject;
                if (data == null) return;

                // 读到了 = 连接正常 → 清掉粘性诊断（问题不存在了，别让它一直挂着占第二行）
                if (_stickyDiag.Length > 0) SetStickyDiag("");

                var llm = data["llm"] as JObject ?? new JObject();
                SetText(_refs.BaseUrlInput, llm["base_url"]?.ToString() ?? "");
                SetText(_refs.ApiKeyInput, llm["api_key"]?.ToString() ?? "");
                SetText(_refs.ModelInput, llm["model"]?.ToString() ?? "");
                // 自定义请求头：字典 → 单行 `k1: v1; k2: v2`。没配过 = 空行（可空语义）。
                SetText(_refs.HeadersInput, FormatHeaders(llm["headers"]));
                // 思考强度：值 + 显隐。行只在「探测结论支持**且结论属于当前这份 base_url+model**」
                // 时显示 —— 换过网关/模型没重测的旧结论不会把行撑出来。
                SetText(_refs.ReasoningInput, llm["reasoning_effort"]?.ToString() ?? "");
                ApplyReasoningRowVisibility(llm["probe"] as JObject,
                                            llm["base_url"]?.ToString() ?? "",
                                            llm["model"]?.ToString() ?? "");
                if (_refs.ImageToggle != null)
                    _refs.ImageToggle.isOn = llm["image"]?["enabled"]?.Value<bool>() ?? false;

                var net = data["network"] as JObject ?? new JObject();
                SetText(_refs.PortInput, net["port"]?.ToString() ?? "8766");
                SetText(_refs.TimeoutInput, net["request_timeout"]?.ToString() ?? "120");
                var init = data["initiative"] as JObject ?? new JObject();
                SetText(_refs.MinIntervalInput, init["min_interval"]?.ToString() ?? "300");
                SetText(_refs.DailyChanceInput, init["daily_chance"]?.ToString() ?? "15");
                SetText(_refs.NpcCooldownDaysInput, init["npc_cooldown_days"]?.ToString() ?? "3");
                SetText(_refs.NpcCooldownRealInput, init["npc_cooldown_real_s"]?.ToString() ?? "600");
                SetText(_refs.LowThreshInput, init["low_intim_threshold"]?.ToString() ?? "60");
                if (_refs.LowHalveToggle != null)
                    _refs.LowHalveToggle.isOn = init["low_intim_halve"]?.Value<bool>() ?? true;
                var comp = data["compaction"] as JObject ?? new JObject();
                SetText(_refs.CtxWindowInput, comp["ctx_window"]?.ToString() ?? "200000");
                SetText(_refs.RetainRatioInput, comp["retain_ratio"]?.ToString() ?? "0.16");
                SetText(_refs.ThresholdRatioInput, comp["threshold_ratio"]?.ToString() ?? "0.8");
                // AI 时间与重试：四个 llm.* 键。**必须带默认值回填**——它们不在
                // config.json 里也能跑（默认值在 Python 侧参与深合并），留空会让用户以为"没配"。
                // ⚠ 用 `?.ToString() ?? "x"`（不要用 `ToStringInvariant(...) ?? "x"`）：
                //    ToStringInvariant 对 null 返回**空串**而不是 null，于是 `"" ?? "120"` 得 ""
                //    —— 兜底是死代码，键一删面板就显示空白（09-20 修，见下 ToStringInvariant 注释）。
                SetText(_refs.LlmTimeoutInput, llm["timeout"]?.ToString() ?? "120");
                SetText(_refs.LlmTimeoutNsInput, llm["timeout_nonstream"]?.ToString() ?? "180");
                // 硬顶默认 0 = 不设限（09-18 起"整趟耗时"不再是默认判据，见 docs/features/brain-liveness.md §4）
                SetText(_refs.LlmBudgetInput, llm["total_budget"]?.ToString() ?? "0");
                SetText(_refs.LlmRetriesInput, llm["retries"]?.ToString() ?? "1");
                // 群聊每轮发言人数上限：**必须带默认值回填**（与上面四个 llm 键同理）——
                // 这个键不在 config.json 里也能跑（Python 侧深合并出默认 3），留空会让用户以为"没配"。
                SetText(_refs.RoomSpeakersInput, data["room"]?["max_speakers_per_round"]?.ToString() ?? "3");
                if (_refs.CompactionEnabledToggle != null)
                    _refs.CompactionEnabledToggle.isOn = comp["enabled"]?.Value<bool>() ?? true;
                if (_refs.InitiativeEnabledToggle != null)
                    _refs.InitiativeEnabledToggle.isOn = init["enabled"]?.Value<bool>() ?? true;
                if (_refs.PortraitToggle != null)
                    _refs.PortraitToggle.isOn = data["ui"]?["portraits_enabled"]?.Value<bool>() ?? true;

                // 自我人设（config.player.persona）：只更新"文件真相"缓存。
                // **不直接写编辑框** —— 提示词页此刻可能正开着这一条且有没保存的编辑，
                // 拿 get_config 的结果覆盖会把用户正在写的东西冲掉（脏值优先，见 FillPersonaEditor）。
                // 读法用 `.ToString()`：键存在但值为 JSON null 时 `?.Value<string>()` 会抛
                // InvalidCastException（本仓库踩过同款，见 ChatPresenter 的 BoolOf 注释）。
                _playerPersona = data["player"]?["persona"]?.ToString() ?? "";
                FillPersonaEditor();

                // 「高级参数整组隐藏」（ui.show_advanced）已废除：改为分组标题 + 常显，
                // 少用的项收进最后一组「高级」。原来那套会让隐藏的行在绝对定位的页面上留一块空洞。
                SetStatus("配置已加载（绝大部分改动即时生效；仅「Python 端口」需重启）", StatusDim);
            }, 10000);
        }

        private void SaveConfig()
        {
            if (_refs == null || _ws == null) { SetStatus("未连接 Python", StatusWarn); return; }

            // 显式校验：空/非法不再静默回退默认值，避免误重置。
            // 原先「自主交互一组默认隐藏、隐藏时不校验也不提交」的分支已废除：
            // 那些行现在常显（分组改版），必须参与校验/提交。
            int port;
            if (!TryParseIntStrict(_refs.PortInput, out port, "端口")) return;
            double timeout;
            if (!TryParseFloatStrict(_refs.TimeoutInput, out timeout, "RPC 超时")) return;
            double interval = 0;
            if (!TryParseFloatStrict(_refs.MinIntervalInput, out interval, "主动熔断间隔")) return;
            int dailyChance;
            if (!TryParseIntStrict(_refs.DailyChanceInput, out dailyChance, "每日触发概率")) return;
            if (dailyChance < 0 || dailyChance > 100) { SetStatus("每日触发概率需 0-100", StatusWarn); return; }
            int cdDays;
            if (!TryParseIntStrict(_refs.NpcCooldownDaysInput, out cdDays, "单人日冷却")) return;
            int cdReal;
            if (!TryParseIntStrict(_refs.NpcCooldownRealInput, out cdReal, "单人现实冷却")) return;
            int thr;
            if (!TryParseIntStrict(_refs.LowThreshInput, out thr, "低好感阈值")) return;
            bool halve = _refs.LowHalveToggle != null && _refs.LowHalveToggle.isOn;
            int ctx;
            if (!TryParseIntStrict(_refs.CtxWindowInput, out ctx, "上下文窗口")) return;
            // 自定义请求头（可空）。留空 = 不附加；Python 侧会把该键删掉。
            // 解析失败**直接拦下整次保存** —— 写坏的 header 在运行期表现为网关 400/403，
            // 与"压根没配"长得一模一样，事后极难归因，不如当场说清。
            // hasHeaders 的含义：老预制件里没有这一行 → 不提交该键（见下方 llmObj 注释）。
            bool hasHeaders = _refs.HeadersInput != null;
            System.Collections.Generic.Dictionary<string, string> headers = null;
            if (hasHeaders)
            {
                string hdrErr;
                headers = ParseHeaders(_refs.HeadersInput.text, out hdrErr);
                if (headers == null) { SetStatus("自定义请求头格式不对：" + hdrErr, StatusWarn); return; }
            }
            // 思考强度（可空，low/high/max）。**行没显示 = 不提交该键**（未探测 / 老 AB）——
            // 绝不用隐藏行的空值把用户手编的 reasoning_effort 冲掉（hasHeaders 同一条规矩）。
            // 值在这里就地校验：严格网关对非法档位是 400 掉整个回合（实测 11150），
            // 当场拦下比运行期兜底台词友好得多。空 = 明确「不发送」→ 提交空串（Python 删键）。
            bool hasReasoning = _refs.ReasoningInput != null && _reasoningRowShown;
            string reasoningEffort = "";
            if (hasReasoning)
            {
                reasoningEffort = (_refs.ReasoningInput.text ?? "").Trim().ToLowerInvariant();
                if (reasoningEffort.Length > 0 && reasoningEffort != "low"
                    && reasoningEffort != "high" && reasoningEffort != "max")
                {
                    SetStatus("思考强度只接受 low / high / max（留空 = 不发送）", StatusWarn);
                    return;
                }
            }
            // 新增行的四/五个控件**按存在与否决定是否提交**（老预制件没有这些节点时
            // 不提交对应键，而不是拿默认值把它们写进文件——set_config 是逐键部分更新）。
            double retain = 0.16;
            bool hasRetain = _refs.RetainRatioInput != null;
            if (hasRetain)
            {
                if (!TryParseFloatStrict(_refs.RetainRatioInput, out retain, "保留原文比例")) return;
                if (retain < 0.05 || retain > 0.6) { SetStatus("保留原文比例需 0.05-0.6（默认 0.16）", StatusWarn); return; }
            }
            double threshold = 0.8;
            bool hasThreshold = _refs.ThresholdRatioInput != null;
            if (hasThreshold)
            {
                if (!TryParseFloatStrict(_refs.ThresholdRatioInput, out threshold, "自动压缩阈值")) return;
                if (threshold < 0.3 || threshold > 0.95) { SetStatus("自动压缩阈值需 0.3-0.95（默认 0.8）", StatusWarn); return; }
            }
            bool hasCompactionOn = _refs.CompactionEnabledToggle != null;
            bool compactionOn = !hasCompactionOn || _refs.CompactionEnabledToggle.isOn;
            bool hasInitiativeOn = _refs.InitiativeEnabledToggle != null;
            bool initiativeOn = !hasInitiativeOn || _refs.InitiativeEnabledToggle.isOn;
            // ── AI 时间与重试（四个可空行）──────────────────────────────
            // 与 hasRetain/hasHeaders 同惯例：**预制件没有这一行就不提交该键**，而不是拿默认值
            // 写进文件（set_config 是逐键部分更新，硬塞默认值等于替用户改配置）。
            //
            // 空 / 0 的语义 = **不提交该键**（保持文件里的原值），与上面 hasRetain/hasHeaders 同惯例。
            // 为什么不把 0 当"非法"拒收：0 在 Python 侧是合法的「关掉这个上限」语义
            // （timeout=0 → 不下发，落 SDK 内建 600s）。面板若拒收，一个手编过 0 的用户
            // 就会连别项都存不了；面板若把 0 当 45 提交，又是**静默改掉用户的配置**。
            // 两者都糟 —— 所以学 headers：没有明确的新值就不碰这个键。
            double llmTimeout = 0, llmTimeoutNs = 0, llmBudget = 0;
            int llmRetries = 0;
            bool submitLlmTimeout = false, submitLlmTimeoutNs = false;
            bool submitLlmBudget = false, submitLlmRetries = false;
            if (_refs.LlmTimeoutInput != null && _refs.LlmTimeoutInput.text.Trim().Length > 0)
            {
                if (!TryParseFloatStrict(_refs.LlmTimeoutInput, out llmTimeout, "流式超时")) return;
                if (llmTimeout < 0) { SetStatus("流式超时不能为负（留空 = 不改这一项）", StatusWarn); return; }
                if (llmTimeout > 600) { SetStatus("流式超时上限 600 秒（再大就等于关掉了这个闸）", StatusWarn); return; }
                submitLlmTimeout = llmTimeout > 0;
            }
            if (_refs.LlmTimeoutNsInput != null && _refs.LlmTimeoutNsInput.text.Trim().Length > 0)
            {
                if (!TryParseFloatStrict(_refs.LlmTimeoutNsInput, out llmTimeoutNs, "压缩超时")) return;
                if (llmTimeoutNs < 0) { SetStatus("压缩超时不能为负（留空 = 不改这一项）", StatusWarn); return; }
                if (llmTimeoutNs > 1800) { SetStatus("压缩超时上限 1800 秒", StatusWarn); return; }
                submitLlmTimeoutNs = llmTimeoutNs > 0;
            }
            if (_refs.LlmBudgetInput != null && _refs.LlmBudgetInput.text.Trim().Length > 0)
            {
                if (!TryParseFloatStrict(_refs.LlmBudgetInput, out llmBudget, "硬顶")) return;
                if (llmBudget < 0) { SetStatus("硬顶不能为负（留空 = 不改这一项）", StatusWarn); return; }
                if (llmBudget > 3600) { SetStatus("硬顶上限 3600 秒", StatusWarn); return; }
                // ★ 0 在这里**会提交**（与上面三个键刻意相反）★：0 是硬顶的默认值、也是「不设限」，
                //   玩家想关掉一个手编过的 150 只有这一条路 —— 若沿用"0 = 不提交"，填 0 等于什么都
                //   不做，界面上看起来"改了却没生效"。config_store 的区间含 0，不会夹走它。
                //   见 docs/reference/csharp-iron-laws.md docs/features/brain-liveness.md §4。
                submitLlmBudget = true;
            }
            if (_refs.LlmRetriesInput != null && _refs.LlmRetriesInput.text.Trim().Length > 0)
            {
                if (!TryParseIntStrict(_refs.LlmRetriesInput, out llmRetries, "重试次数")) return;
                if (llmRetries < 0 || llmRetries > 5)
                {
                    // 上限 5 不是技术限制，是"别让一次手滑把最坏等待乘以 6"：
                    // 默认（没设硬顶）时玩家最坏等待 ≈ 首字看门狗 ×(retries+1)+退避，次数越多越久。
                    // 注意 0 在这里是**合法且会被提交**的（=明确要求不重试），不是"留空"。
                    SetStatus("重试次数需 0-5（默认 1；总尝试 = 本值 + 1）", StatusWarn);
                    return;
                }
                submitLlmRetries = true;
            }
            // 群聊每轮发言人数上限（config room.max_speakers_per_round，默认 3）。
            // 沿用上面四个 llm 键的惯例：**留空 = 不提交这一项**（保持文件里的原值），
            // 这样老 AB（没有这一行）与"用户没动这一行"都不会把手编的值冲掉。
            // 下限 1 在本地就拦住：0/负数没有语义（一轮谁都不许开口＝群聊停摆），
            // Python 侧 config_store 与 RoomFlow 也会各自抬到 1（三层一致）。
            int roomSpeakers = 0;
            bool submitRoomSpeakers = false;
            if (_refs.RoomSpeakersInput != null && _refs.RoomSpeakersInput.text.Trim().Length > 0)
            {
                if (!TryParseIntStrict(_refs.RoomSpeakersInput, out roomSpeakers, "群聊每轮发言人数上限")) return;
                if (roomSpeakers < 1)
                {
                    SetStatus("群聊每轮发言人数上限至少为 1（默认 3；留空 = 不改这一项）", StatusWarn);
                    return;
                }
                submitRoomSpeakers = true;
            }
            bool hasPortraits = _refs.PortraitToggle != null;
            bool portraitsOn = !hasPortraits || _refs.PortraitToggle.isOn;

            var iniObj = new JObject
            {
                ["min_interval"] = interval,
                ["daily_chance"] = dailyChance,
                ["npc_cooldown_days"] = cdDays,
                ["npc_cooldown_real_s"] = cdReal,
                ["low_intim_threshold"] = thr,
                ["low_intim_halve"] = halve,
            };
            if (hasInitiativeOn) iniObj["enabled"] = initiativeOn;
            var compObj = new JObject { ["ctx_window"] = ctx };
            if (hasRetain) compObj["retain_ratio"] = retain;
            if (hasThreshold) compObj["threshold_ratio"] = threshold;
            if (hasCompactionOn) compObj["enabled"] = compactionOn;

            var llmObj = new JObject
            {
                ["base_url"] = _refs.BaseUrlInput != null ? _refs.BaseUrlInput.text.Trim() : "",
                ["api_key"] = _refs.ApiKeyInput != null ? _refs.ApiKeyInput.text.Trim() : "",
                ["model"] = _refs.ModelInput != null ? _refs.ModelInput.text.Trim() : "",
                ["image"] = new JObject { ["enabled"] = _refs.ImageToggle != null && _refs.ImageToggle.isOn },
            };
            // 只在预制件真有这一行时才提交 headers —— 老 AB 缺这个节点时若提交空 dict，
            // Python 侧会把用户**手编在 config.json 里的 headers 删掉**（空 = 不要附加头）。
            // 这与 hasRetain / hasThreshold 是同一个惯例：按节点存在与否决定提不提交。
            if (hasHeaders) llmObj["headers"] = JObject.FromObject(headers);
            // 思考强度：只在行显示时提交（hasReasoning 判据见上方校验段）。空串 = 删键
            // （Python set_config 对该键的空值走 headers 同款「删键」分支）。
            if (hasReasoning) llmObj["reasoning_effort"] = reasoningEffort;
            // 探测结论随保存落盘：factory 的注入门控靠它判断"键在当前网关/模型上可用"。
            // 只提交**与本次表单一致**的结论 —— 用户改了 base_url/model 没重测，旧结论
            // 已经过期（per-model），写进去也会被 Python 的指纹比对拒绝，不如不写。
            if (_lastProbe != null
                && (_lastProbe["base_url"]?.ToString() ?? "") == (llmObj["base_url"]?.ToString() ?? "")
                && (_lastProbe["model"]?.ToString() ?? "") == (llmObj["model"]?.ToString() ?? ""))
            {
                llmObj["probe"] = _lastProbe;
            }
            // AI 时间与重试：只提交"用户确实给了新值"的项（空/0 = 保持文件原值，见上方注释）。
            // 老 AB（没有这四行）里 submit* 全为 false → llmObj 不含这四个键 →
            // set_config 的逐键部分更新不会碰它们，用户手编的值原样保留。
            if (submitLlmTimeout) llmObj["timeout"] = llmTimeout;
            if (submitLlmTimeoutNs) llmObj["timeout_nonstream"] = llmTimeoutNs;
            if (submitLlmBudget) llmObj["total_budget"] = llmBudget;
            if (submitLlmRetries) llmObj["retries"] = llmRetries;

            var cfg = new JObject
            {
                ["llm"] = llmObj,
                ["network"] = new JObject
                {
                    ["port"] = port,
                    ["request_timeout"] = timeout,
                },
                ["initiative"] = iniObj,
                ["compaction"] = compObj,
            };
            if (hasPortraits) cfg["ui"] = new JObject { ["portraits_enabled"] = portraitsOn };
            // 群聊块：只有一个键，且只在用户真给了新值时才提交（见上方 submitRoomSpeakers）。
            if (submitRoomSpeakers) cfg["room"] = new JObject { ["max_speakers_per_round"] = roomSpeakers };

            var p = new JObject { ["config"] = cfg };
            _ws.SendRequest("set_config", p, resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    SetStatus("保存失败: " + (resp["error"]?.ToString() ?? "未知错误"), StatusWarn);
                    return;
                }
                var data = resp["data"] as JObject ?? new JObject();
                string effective = data["effective"]?.ToString() ?? "";
                bool swapFailed = data["swap_error"] != null;
                string msg;
                switch (effective)
                {
                    // 「即时生效」不限于大模型：network.request_timeout 也已做成热生效
                    case "hot": msg = "已保存：设置已即时生效"; break;
                    case "restart": msg = "已保存：正在重启 Python 使其生效…"; break;
                    case "mixed": msg = "已保存：可即时生效的已生效；正重启 Python 使其余生效…"; break;
                    default: msg = "无改动"; break;
                }
                if (swapFailed) msg = "已保存：热生效未成功，正重启 Python 生效…";
                SetStatus(msg, effective == "restart" ? StatusWarn : StatusOk);
                P("[ConfigPresenter] set_config 应答 effective=" + effective + (swapFailed ? " swap_error" : ""));

                // 需要 Python 重启（restart/mixed 或 llm 热换失败）→ 自动重启 + 重连
                bool restartRequired = effective == "restart" || effective == "mixed" || swapFailed;
                if (restartRequired)
                {
                    StartPythonRestart();
                }
                else
                {
                    // 动态读取 没有重启 = 这一批改动全在内存里热生效了，但**C# 侧消费的那几个
                    // （initiative.* 与 ui.portraits_enabled）还拿着旧值** → 立刻重拉一次 get_config
                    // 并 Configure（一次往返，保证与文件一致）。这样"每日概率/两个冷却/总开关/立绘开关"
                    // 改完当场生效，不必再为了几个数字重启 Python（旧行为：重启才生效）。
                    try { ModMain.Instance?.RefreshInitiativeFromConfig(); }
                    catch (Exception e) { P("[ConfigPresenter] 保存后重拉配置失败: " + e.Message); }
                }
            }, 10000);
        }

        // ------------------------------------------------------------------
        // 测试连接（test_llm）
        // ------------------------------------------------------------------

        /// <summary>
        /// 「测试连接」：拿**表单当前值**真调一次模型，把结果摊在状态条上。
        ///
        /// 为什么要有它：配错 LLM 的三种典型故障（key 错 / base_url 漏了 /v1 / 网关要求自定义头）
        /// 在游戏里长得**一模一样** —— NPC 只会回「（传音法阵一时没接上你的话…）」，
        /// 玩家根本无从判断该改哪一项。这里把对方的原始错误 + 归类 + 处置建议直接摆出来。
        ///
        /// 为什么不先保存再测：`set_config` 会让 Python 重启（很重）。用户的真实用法是
        /// 「填完先试，试通了再保存」，所以这里只把表单值当**参数**发过去，Python 侧不落盘。
        /// </summary>
        private void TestLlm()
        {
            if (_refs == null || _ws == null) { SetStatus("未连接 Python（WsClient 未启动）", StatusWarn); return; }
            if (_testInflight) { SetStatus("上一次测试还没回来，稍候…", StatusDim); return; }

            // headers 先在本地解析：格式错就没必要发这一趟 RPC，而且当场报错更即时
            bool hasHeaders = _refs.HeadersInput != null;
            System.Collections.Generic.Dictionary<string, string> headers = null;
            if (hasHeaders)
            {
                string hdrErr;
                headers = ParseHeaders(_refs.HeadersInput.text, out hdrErr);
                if (headers == null) { SetStatus("自定义请求头格式不对：" + hdrErr, StatusWarn); return; }
            }

            var p = new JObject
            {
                ["base_url"] = _refs.BaseUrlInput != null ? _refs.BaseUrlInput.text.Trim() : "",
                ["api_key"] = _refs.ApiKeyInput != null ? _refs.ApiKeyInput.text.Trim() : "",
                ["model"] = _refs.ModelInput != null ? _refs.ModelInput.text.Trim() : "",
            };
            if (hasHeaders) p["headers"] = JObject.FromObject(headers);

            _testInflight = true;
            SetStatus("正在测试连接…（用当前表单里的值真发一次请求）", StatusDim);
            // 超时 30s：Python 侧自己卡 15s，这里留足往返与队列余量。
            // （get_config 用的 10s 对这里不够 —— 那可是真在等一次网络往返。）
            _ws.SendRequest("test_llm", p, resp =>
            {
                _testInflight = false;
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    SetStatus("测试请求本身失败: " + (resp["error"]?.ToString() ?? "未知错误"), StatusWarn);
                    return;
                }
                RenderTestResult(resp["data"] as JObject);
            }, 30000);
        }

        /// <summary>
        /// 把 test_llm 的结果摊到状态条上。
        ///
        /// **状态条只有一行**（预制件 `BG/Status` 736×32、字号 14 ≈ 50 个汉字），
        /// 所以这里只放「结论 + 归类 + 原文（截断）+ 建议（截断）」；
        /// **完整原文一律走 P() 进 Player.log** —— 用户要贴出来求助时，贴的是日志那一行。
        /// </summary>
        private void RenderTestResult(JObject data)
        {
            if (data == null) { SetStatus("测试返回为空", StatusWarn); return; }
            bool ok = data["ok"]?.Value<bool>() == true;
            int ms = data["latency_ms"]?.Value<int>() ?? 0;
            string model = data["model"]?.ToString() ?? "";
            string hdrs = "";
            var ha = data["header_names"] as JArray;
            if (ha != null)
            {
                var names = new System.Collections.Generic.List<string>();
                foreach (var t in ha) names.Add(t?.ToString() ?? "");
                hdrs = string.Join(",", names.ToArray());
            }

            if (ok)
            {
                string reply = data["reply"]?.ToString() ?? "";
                P("[ConfigPresenter] test_llm 成功 " + ms + "ms model=" + model
                  + " headers=[" + hdrs + "] reply=" + reply);
                SetStatus("连接正常（" + ms + "ms）：模型回了「" + Trunc(reply, 16) + "」", StatusOk);
                // 能力探测（09-21）：结论驱动「思考强度」行的显隐。探测失败被 Python 侧
                // 收口成 unknown —— 行保持现状（多半是隐藏），绝不连累这条绿色结论。
                var probe = data["probe"] as JObject;
                if (probe != null)
                {
                    _lastProbe = probe;
                    string verdict = probe["verdict"]?.ToString() ?? "unknown";
                    int rchars = probe["reasoning_chars"]?.Value<int>() ?? 0;
                    P("[ConfigPresenter] 能力探测 verdict=" + verdict
                      + " reasoning_chars=" + rchars + " why=" + (probe["why"]?.ToString() ?? ""));
                    if (_refs.ReasoningInput != null && (verdict == "supported" || verdict == "no_think"))
                    {
                        _reasoningRowShown = true;
                        var row = _refs.ReasoningInput.transform.parent;
                        if (row != null) row.gameObject.SetActive(true);
                        RelabelRow(_refs.ReasoningInput, verdict == "no_think"
                            ? "思考强度（该模型未观测到思维链）"
                            : "思考强度（已探测支持）");
                        // ★ 探测到思考模式真实可用 → 自动填 low 兜底档（09-21 用户拍板）。
                        //   只在**空行**时填：config 里已存过别的档位（回填在框里）不许覆盖 ——
                        //   那是用户的显式选择，重测一次就把 high 悄悄改成 low 属于静默改配置。
                        //   no_think（键可用但模型不出思维流）**不填**：注入了也看不到内心思量，
                        //   填默认值只会误导"为什么开了还是没思考"。
                        if (verdict == "supported" && string.IsNullOrWhiteSpace(_refs.ReasoningInput.text))
                            _refs.ReasoningInput.text = "low";
                    }
                }
                return;
            }

            var err = data["error"] as JObject ?? new JObject();
            string kind = err["kind"]?.ToString() ?? "unknown";
            string status = err["status"]?.ToString() ?? "";
            string code = err["code"]?.ToString() ?? "";
            string msg = err["message"]?.ToString() ?? "";
            string hint = err["hint"]?.ToString() ?? "";

            // 完整留痕（不截断）—— 状态条装不下的部分全在这里
            P("[ConfigPresenter] test_llm 失败 kind=" + kind + " status=" + status
              + " code=" + code + " model=" + model + " headers=[" + hdrs + "]"
              + " message=" + msg + " hint=" + hint);

            string head = "× " + kind + (status.Length > 0 ? " " + status : "")
                          + (code.Length > 0 ? " " + code : "");
            string line = head + "：" + Trunc(msg, 40);
            if (hint.Length > 0) line += "｜" + Trunc(hint, 48);
            SetStatus(line, StatusWarn);
        }

        /// <summary>状态条是单行，超长就地截断并留个省略号（完整内容见日志那一行）。</summary>
        private static string Trunc(string s, int max)
        {
            s = (s ?? "").Replace("\r", " ").Replace("\n", " ").Trim();
            return s.Length <= max ? s : s.Substring(0, max) + "…";
        }

        /// <summary>
        /// config 的 `llm.headers`（对象）→ 单行文本，供表单回填。
        /// 空/缺失 → 空串：面板上就是一行空的，与「可空」的语义一致（而不是写个 `{}` 吓人）。
        /// </summary>
        private static string FormatHeaders(JToken token)
        {
            var obj = token as JObject;
            if (obj == null || obj.Count == 0) return "";
            var parts = new System.Collections.Generic.List<string>();
            foreach (var kv in obj)
            {
                string v = kv.Value == null || kv.Value.Type == JTokenType.Null ? "" : kv.Value.ToString();
                parts.Add(kv.Key + ": " + v);
            }
            return string.Join("; ", parts.ToArray());
        }

        /// <summary>
        /// 单行文本 → headers 字典。空串 = 不附加（返回**空字典**，不是 null）。
        /// 认两种写法：`k1: v1; k2: v2`（主推，照文档抄即可）与整体 JSON（值里含 `;` 时的逃生口）。
        /// 返回 null 表示解析失败，原因走 error —— 调用方一律**拦下**，不静默忽略。
        ///
        /// 切分规则：按 `;` 分段，每段按**第一个** `:` 切 —— 于是 `X-Url: http://a` 这种
        /// 值里带冒号的写法天然正确。
        /// </summary>
        private static System.Collections.Generic.Dictionary<string, string> ParseHeaders(string raw, out string error)
        {
            error = null;
            raw = (raw ?? "").Trim();
            var result = new System.Collections.Generic.Dictionary<string, string>();
            if (raw.Length == 0) return result;

            if (raw.StartsWith("{"))
            {
                try
                {
                    var obj = JObject.Parse(raw);
                    foreach (var kv in obj)
                    {
                        string v = kv.Value == null || kv.Value.Type == JTokenType.Null ? "" : kv.Value.ToString();
                        result[kv.Key.Trim()] = v.Trim();
                    }
                    return result;
                }
                catch (Exception e)
                {
                    error = "看着像 JSON 但解析失败（" + e.Message + "）";
                    return null;
                }
            }

            string[] segs = raw.Split(';');
            for (int i = 0; i < segs.Length; i++)
            {
                string seg = segs[i].Trim();
                if (seg.Length == 0) continue;
                int idx = seg.IndexOf(':');
                if (idx <= 0)
                {
                    error = "这一段没有冒号：「" + Trunc(seg, 20) + "」（要写成 `名字: 值`）";
                    return null;
                }
                string k = seg.Substring(0, idx).Trim();
                string v = seg.Substring(idx + 1).Trim();
                if (k.Length == 0) { error = "有一项的名字是空的"; return null; }
                if (k.IndexOf(' ') >= 0 || k.IndexOf('\t') >= 0)
                {
                    error = "名字里有空格：「" + Trunc(k, 20) + "」（冒号前别留空格）";
                    return null;
                }
                result[k] = v;
            }
            return result;
        }

        /// <summary>探测结论 → 「思考强度」行的显隐。纯函数便于契约测试。</summary>
        /// <remarks>
        /// verdict ∈ {supported, no_think} 且 probe 里的 base_url/model 与 config 当前值
        /// **逐字一致**才显示 —— 探测结论是 per-model 的，换过网关/模型没重测的旧结论
        /// 不许把行撑出来（Python factory 的注入门控同一条判据，两处必须同源）。
        /// no_think（键可用但模型未观测到思维流）也显示：注入无害，开不开由用户。
        /// </remarks>
        internal static bool ShouldShowReasoningRow(JObject probe, string baseUrl, string model)
        {
            string verdict = probe?["verdict"]?.ToString() ?? "";
            if (verdict != "supported" && verdict != "no_think") return false;
            return (probe["base_url"]?.ToString() ?? "") == (baseUrl ?? "")
                && (probe["model"]?.ToString() ?? "") == (model ?? "");
        }

        private void ApplyReasoningRowVisibility(JObject probe, string baseUrl, string model)
        {
            if (_refs?.ReasoningInput == null) return;
            _reasoningRowShown = ShouldShowReasoningRow(probe, baseUrl, model);
            var row = _refs.ReasoningInput.transform.parent;   // 行节点 = Input 的父（MakeFormRow 契约）
            if (row != null) row.gameObject.SetActive(_reasoningRowShown);
        }

        // ------------------------------------------------------------------
        // 已废除：高级参数整组隐藏（ui.show_advanced）
        // ------------------------------------------------------------------
        // 原来 initiative 那 6 行默认 SetActive(false)，靠手改 config.json 的 ui.show_advanced 才显示。
        // 两个问题：① 玩家在面板里根本看不到主动互动参数（而那正是最影响日常体感的几个数字）；
        // ② 行是绝对定位（行距 35.22、页面上没有 layout group），整组隐藏会在页面中间留一块空洞。
        // 现在改为「分组标题条 + 全部常显 + 整页可滚动」（预制件侧：PageLlm 套 ScrollRect，
        // 少用的项收进最后一组「高级」），所以 `_advanced` 字段、`InitiativeRows()`、
        // `ApplyAdvancedVisibility()` 一并删除。

        // ------------------------------------------------------------------
        // Python 重启编排：保存后 effective=restart/mixed（或热换失败）→
        // shutdown RPC → 等旧进程让位（连接断开即证）→ 拉起同 server.py → 等 WS 自动重连
        // 全流程必定收口：要么「配置已生效（Python 已重连）」，要么一条明确的失败提示。
        // 实测预算：python 退出 0.6s + 拉起 0.1s + 监听就绪 0.7s + 重连 ≤2.3s ≈ 2~4s。
        // ------------------------------------------------------------------

        private void StartPythonRestart()
        {
            if (_restartStage != RestartStage.None) return; // 已在排
            // 挂起 BrainLink：本编排马上要**故意杀掉**旧进程，而 BrainLink 的
            // 判据是"进程死了就拉"。不挂起的话两边会同时 Process.Start → 抢端口，
            // 正是 09-12 那类故障。编排期间"拉起"这件事只允许有一个出口。
            BrainLink.Suspend("配置面板触发的 Python 重启编排");
            _restartPort = _ws != null ? _ws.ConnectedPort : ModConfigFile.DefaultPort;
            _portChangedDuringRestart = PortFieldChanged();
            _restartStage = RestartStage.WaitShutdown;
            _absTimer = 0f;
            _statusTimer = 0f;
            _launchCount = 0;
            _killTried = false;
            _sawDisconnect = false;
            SetStatus("已保存：正在重启 Python…（0.0s）", StatusWarn);
            P("[ConfigPresenter] Python 重启编排开始（owned=" + Launcher.IsOwned +
              "，port=" + _restartPort + "，portChanged=" + _portChangedDuringRestart + "）");
            // 请 Python 优雅退出（应答即证明它已排定 0.6s 后 flush + os._exit）。
            // 请求失败也照常推进：下方靠「连接断开 / 计时兜底」继续，绝不因一个 RPC 卡死。
            try
            {
                _ws.SendRequest("shutdown", new JObject(), resp =>
                {
                    P("[ConfigPresenter] shutdown 应答 ok=" + (resp["ok"]?.Value<bool>() == true));
                }, 2000);
            }
            catch (Exception e) { P("[ConfigPresenter] shutdown 请求异常: " + e.Message); }
        }

        /// <summary>端口输入框与当前连接端口不一致（Python 重启后 C# 仍连旧端口，需整机重启游戏）。</summary>
        private bool PortFieldChanged()
        {
            int p;
            if (_refs?.PortInput != null && int.TryParse(_refs.PortInput.text.Trim(), out p))
                return p != (_ws != null ? _ws.ConnectedPort : ModConfigFile.DefaultPort);
            return false;
        }

        private void TickPythonRestart()
        {
            if (_restartStage == RestartStage.None) return;
            // unscaledDeltaTime：时间缩放（过场/暂停）不该拖住「必须收口」的状态机
            float dt = Time.unscaledDeltaTime;
            _absTimer += dt;
            _statusTimer += dt;

            bool connected = _ws != null && _ws.IsConnected;
            if (!connected) _sawDisconnect = true;   // 「断开」是本轮重启最关键的观测（见下）

            if (_restartStage == RestartStage.WaitShutdown)
            {
                // 让位判据只有一个：**连接已断** = 旧进程已死。socket 随进程退出失效，
                // 而 WsClient 自己就维护着这个状态（成功重连后 IsConnected 又为真，故用
                // _sawDisconnect 记住「曾经断过」）。不查 HasExited：Launcher 用
                // UseShellExecute 拉起，句柄可能不追踪真实 python，以其为准会误判。
                if (_sawDisconnect)
                {
                    LaunchOrAbort();
                    return;
                }
                // 连接仍在：旧进程若是本 mod 拉起的 → 到点补一记强杀（Kill 幂等，句柄失真时内部已容错）
                if (Launcher.IsOwned && !_killTried && _absTimer >= KILL_AT)
                {
                    _killTried = true;
                    Launcher.KillOwned();
                }
                // 硬上限：非 owned（手起的 server.py）且僵死时无法强杀 → 仍照拉一次，
                // 拉起幂等（端口被占则新进程自己退），是否真成功由下方「必须见到新连接」把关。
                if (_absTimer >= EXIT_HARD_LIMIT)
                {
                    P("[ConfigPresenter] 等旧进程让位超 " + Fmt(_absTimer) + "s（未见到连接断开）→ 兜底硬拉起");
                    LaunchOrAbort();
                    return;
                }
                TickStatus("已保存：正在重启 Python…");
                return;
            }

            if (_restartStage == RestartStage.WaitReconnect)
            {
                // 成功判据 = 连接在 + 本轮确实断过：只认「新连接」，绝不在旧连接上误报成功
                // （旧进程僵死、socket 还活着时，若只看 connected 会谎报「配置已生效」）
                if (connected && _sawDisconnect)
                {
                    _restartStage = RestartStage.None;
                    if (_ws != null) _ws.SetFastRetry(false);   // 恢复常规重连节拍
                    BrainLink.Resume();   // 【09-14】编排收口：把存活监测交还回去
                    P("[ConfigPresenter] Python 重启完成，WS 已重连（用时 " +
                      Fmt(_absTimer) + "s）");
                    SetStatus("已保存并重启：配置已生效（Python 已重连）", StatusOk);
                    RequestConfig();   // 新进程读新配置，重拉表单回填
                    return;
                }
                // 首拉可能撞上「旧进程尚未让出端口」→ 新进程 bind 失败秒退；有界补拉一次兜住
                if (_launchCount < MAX_LAUNCH && _absTimer > RELAUNCH_AGAIN_AT)
                {
                    P("[ConfigPresenter] 拉起后 " + Fmt(_absTimer) + "s 仍未重连 → 补拉一次");
                    LaunchOrAbort();
                    return;
                }
                if (_absTimer > RECONNECT_GRACE)
                {
                    AbortPythonRestart(_sawDisconnect
                        ? "重启后未连上 Python（请看 server.py 控制台窗口的报错）"
                        : "旧 Python 未响应关停（非本 mod 拉起，无法强杀）：请手动关闭 server.py 控制台窗口后重试");
                    return;
                }
                TickStatus("Python 已重启，等待连接恢复…");
            }
        }

        /// <summary>拉起 Python；成功则转入等重连，失败即中止并如实报错（本流程唯一出口之一）。</summary>
        private void LaunchOrAbort()
        {
            string err = Launcher.RelaunchPython();
            if (err.Length > 0)
            {
                AbortPythonRestart("Python 重启失败：" + err);
                return;
            }
            _launchCount++;
            if (_portChangedDuringRestart)
            {
                // 新进程在「新端口」监听，但 C# 连接目标仍是旧端口 → 只能整机重启游戏收尾
                _restartStage = RestartStage.None;
                // 这里**故意不 Resume**：端口变了，C# 仍连旧端口，本来就只能整机重启。
                // 恢复监测只会让 BrainLink 一直显示"正在重连"，与面板上那句"请重启游戏"打架。
                SetStatus("已保存并重启：端口已变更，请重启游戏让 C# 连接新端口", StatusWarn);
                P("[ConfigPresenter] 端口已变更：Python 已重启到新端口，C# 需整机重启");
                return;
            }
            if (_ws != null) _ws.SetFastRetry(true);   // 拉起后让 WsClient 快速重试（0.3s 而非 2s）
            _restartStage = RestartStage.WaitReconnect;
            _absTimer = 0f;
            _statusTimer = 0f;
            SetStatus("Python 已重启，等待连接恢复…（0.0s）", StatusDim);
            P("[ConfigPresenter] 已拉起 Python（第 " + _launchCount + " 次），等待 WS 重连");
        }

        /// <summary>状态条耗时反馈（0.25s 节流；重启期间界面「有反应」全靠它）。</summary>
        private void TickStatus(string prefix)
        {
            if (_statusTimer < 0.25f) return;
            _statusTimer = 0f;
            SetStatus(prefix + "（" + Fmt(_absTimer) + "s）", StatusWarn);
        }

        private static string Fmt(float seconds) =>
            seconds.ToString("F1", System.Globalization.CultureInfo.InvariantCulture);

        private void AbortPythonRestart(string msg)
        {
            _restartStage = RestartStage.None;
            if (_ws != null) _ws.SetFastRetry(false);
            BrainLink.Resume();   // 【09-14】中止也要交还监测权，否则 BrainLink 永久哑火
            SetStatus(msg, StatusWarn);
            P("[ConfigPresenter] Python 重启编排中止: " + msg);
        }

        private static void P(string msg) => UnityEngine.Debug.Log("[AgentLoopBridge] " + msg);

        // ------------------------------------------------------------------
        // 提示词文件（list_prompts / read_prompt / write_prompt / create_persona）
        // ------------------------------------------------------------------

        private void RequestPrompts()
        {
            if (_ws == null) return;
            var p = new JObject();
            if (!string.IsNullOrEmpty(_currentNpcId)) p["npc_id"] = _currentNpcId;
            _ws.SendRequest("list_prompts", p, resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true) return;
                var data = resp["data"] as JObject;
                var groups = data?["groups"] as JArray;
                _lastGroups = groups ?? new JArray();
                RebuildFileList(_lastGroups);
            }, 10000);
        }

        private void RebuildFileList(JArray groups)
        {
            var content = _refs.FileListContent;
            var template = _refs.FileItemTemplate;
            if (content == null || template == null) return;

            // 清旧项（IL2CPP：索引循环，禁 foreach(Transform)）
            for (int i = content.childCount - 1; i >= 0; i--)
                UnityEngine.Object.Destroy(content.GetChild(i).gameObject);
            _tip?.Clear();               // 旧行已销毁，悬停登记一并清（表单行下方重登记）
            RegisterFormTooltips();

            foreach (var token in groups)
            {
                var item = token as JObject;
                string path = item?["path"]?.ToString() ?? "";
                if (path.Length == 0) continue;
                string group = item?["group"]?.ToString() ?? "";
                string name = item?["name"]?.ToString() ?? "";

                var clone = UnityEngine.Object.Instantiate(template);
                clone.transform.SetParent(content, false);
                clone.SetActive(true);
                var label = clone.GetComponentInChildren<Text>(true);
                // label 由 Python 下发优先（虚拟条目「自我人设（玩家）」需要自定义显示名），
                // 缺失才回落到"分组中文名/文件名"——老端兼容。
                string disp = item?["label"]?.ToString();
                if (label != null)
                    label.text = string.IsNullOrEmpty(disp) ? (GroupCn(group) + "/" + name) : disp;

                string captured = path; // 闭包捕获局部副本
                ClickUtils.Attach(clone.GetComponent<Button>(), () => SelectFile(captured));

                // 可删标记（delete_prompt 用）：Python 下发优先，缺失按本地规则兜底（系统件不可删）
                bool del = item?["deletable"]?.Value<bool>()
                           ?? IsDeletableLocal(group, name);
                _deletable[path] = del;

                // 可重置标记（reset_prompt 用）：Python 下发优先；缺键/JSON null 回落到**乐观 true**。
                // 为什么乐观：随包发的人设（personas/郦安.txt 这种）与用户新建的 personas/{npc}.txt
                // 光看名字分不出来，C# 没有可靠的本判据；而漏判的代价只是"点了之后得到一句诚实的报错"，
                // 误判成不可用的代价却是"明明能重置的文件被灰掉"。权威判定始终在 Python。
                // 用 ToString 判而不是 ?.Value<bool>()：后者遇到显式 JSON null 会抛 InvalidCastException
                // （自我人设那次踩过这个坑，写在文档里了）。
                var rtToken = item?["resettable"];
                _resettable[path] = rtToken == null || rtToken.ToString() != "False";

                // 悬停说明：Python list_prompts 下发的 desc 优先，缺失用 C# 分组规则兜底
                string desc = item?["desc"]?.ToString();
                if (string.IsNullOrEmpty(desc)) desc = FallbackFileTip(group, name);
                _tip?.Register(clone.GetComponent<RectTransform>(), desc);
            }
        }

        /// <summary>文件悬停说明的 C# 兜底（Python desc 缺失/旧端时按分组与文件名规则生成）</summary>
        private static string FallbackFileTip(string group, string name)
        {
            string stem = name.Contains(".") ? name.Substring(0, name.LastIndexOf('.')) : name;
            switch (group)
            {
                case "player":
                    return "你（玩家）是谁：写在这里的设定会作为 system 的一段注入给所有 NPC"
                         + "（存在 config.json 的 player.persona，上限 4000 字）。空 = 不注入；改完下个回合生效";
                case "sections": return "全局设定文件，对所有 NPC 生效（改完下个回合自动生效）";
                case "personas":
                    if (stem == "default") return "兜底人设：NPC 无专属人设文件时使用";
                    if (stem == "_suffix") return "人设尾部追加：所有 NPC 的人设末尾都会拼上这一段";
                    return stem + " 的专属人设（存在则覆盖 default）";
                case "traits": return stem + " 的静态标签变量（气运/性格等，人设里用 {变量} 插值）";
                case "compaction":
                    if (stem == "default") return "历史压缩指令模板（全局，作为 system 下发）";
                    return stem + " 的历史压缩指令模板（专属）";
                default: return "提示词文件";
            }
        }

        private static string GroupCn(string group)
        {
            switch (group)
            {
                case "player": return "自我人设";     // 仅列表 label 缺 `label` 字段时才会用到
                case "sections": return "全局设定";
                case "personas": return "人设";
                case "traits": return "标签";
                case "compaction": return "压缩指令";
                default: return group;
            }
        }

        private void SelectFile(string path)
        {
            if (_refs == null || _ws == null) return;
            DisarmDelete();   // 换文件作废删除武装——严防武装着删到别的文件
            DisarmReset();    // 同理：换文件作废重置武装，严防"在 A 上武装、在 B 上执行"
            // 切走前保存当前编辑内容到全量缓存，避免未点保存就丢
            if (!string.IsNullOrEmpty(_currentPath) && _refs.PromptInput != null)
                _promptCache[_currentPath] = _refs.PromptInput.text ?? "";
            _currentPath = path;
            bool virtualItem = IsVirtualConfigPath(path);
            if (_refs.CurrentFileLabel != null)
                _refs.CurrentFileLabel.text = virtualItem
                    ? "正在编辑: 自我人设（存于 config.json，对所有 NPC 生效）"
                    : "正在编辑: " + path;
            if (virtualItem)
            {
                // 不调 read_prompt：它**不是文件**（Python 对 config: 路径显式拒绝）。
                // 值来自 RequestConfig 的缓存，脏值优先 —— 没有 RPC，也就没有"已切走"竞态。
                FillPersonaEditor();
                SetStatus("自我人设：改完点「保存文件」写入 config.json（下个回合生效；清空 = 不再注入）", StatusDim);
                return;
            }
            _ws.SendRequest("read_prompt", new JObject { ["path"] = path }, resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    SetStatus("读取文件失败: " + (resp["error"]?.ToString() ?? ""), StatusWarn);
                    return;
                }
                var data = resp["data"] as JObject;
                if (data == null || data["path"]?.ToString() != _currentPath) return; // 已切走则放弃
                string txt = data["text"]?.ToString() ?? "";
                if (_refs.PromptInput != null)
                {
                    SetPromptEditorText(txt);
                    // 基准写入缓存，后续编辑通过 OnPromptEdited 增量更新
                    _promptCache[_currentPath] = txt;
                }
            }, 10000);
        }

        /// <summary>`config:` 虚拟条目判定（自我人设等：背后是 config.json 而非 prompts/ 文件）。</summary>
        private static bool IsVirtualConfigPath(string path)
        {
            return !string.IsNullOrEmpty(path)
                   && path.StartsWith(VirtualConfigPrefix, System.StringComparison.Ordinal);
        }

        /// <summary>把文本灌进提示词编辑框（含：临时摘掉编辑监听防回灌、按全文撑开、回顶部）。
        /// 原样搬自 SelectFile 的既有三步，折叠成一处 —— 自我人设那条路也要走同样的收尾。</summary>
        private void SetPromptEditorText(string txt)
        {
            if (_refs?.PromptInput == null) return;
            if (_onPromptEditedAction != null) _refs.PromptInput.onValueChanged.RemoveListener(_onPromptEditedAction);
            _refs.PromptInput.text = txt ?? "";
            if (_onPromptEditedAction != null) _refs.PromptInput.onValueChanged.AddListener(_onPromptEditedAction);
            UpdatePromptLayout();   // 灌入长文后按全文高度撑开 Text/Content（绕开 InputField 可视窗口死锁）
            // 外挂滚动（AB 预制件）：填充长文后回顶部（anchoredPosition y=0 即顶，与 CSF 重算时序无关）
            if (_refs.PromptScroll != null) _refs.PromptScroll.verticalNormalizedPosition = 1f;
        }

        /// <summary>把自我人设灌进编辑框（仅当正选中该条目）。**脏值优先**：
        /// `_promptCache` 里若有未保存的编辑就用它，否则用 get_config 的文件真相。</summary>
        private void FillPersonaEditor()
        {
            if (_refs?.PromptInput == null) return;
            if (!IsVirtualConfigPath(_currentPath)) return;
            string txt;
            if (!_promptCache.TryGetValue(_currentPath, out txt)) txt = _playerPersona ?? "";
            SetPromptEditorText(txt);
        }

        private void SavePromptFile()
        {
            if (_refs == null || _ws == null) return;
            if (_currentPath.Length == 0 && _promptCache.Count == 0) { SetStatus("请先在左侧选择文件", StatusWarn); return; }
            // 同步当前编辑框到全量缓存
            if (!string.IsNullOrEmpty(_currentPath) && _refs.PromptInput != null)
                _promptCache[_currentPath] = _refs.PromptInput.text ?? "";
            if (_promptCache.Count == 0)
            {
                SetStatus("无可保存的改动", StatusWarn);
                return;
            }
            // 分流：`config:` 虚拟条目（自我人设）走 set_config，其余仍走批量 write_prompts。
            // **必须分流**：Python 的 write_prompts 对 config: 路径 fail-closed 拒绝、且是 fail-fast
            // （首个非法即抛）→ 混进去会让整页"保存提示词"一起失败。
            var files = new JObject();
            bool hasPersona = false;
            string persona = "";
            foreach (var kv in _promptCache)
            {
                if (IsVirtualConfigPath(kv.Key)) { hasPersona = true; persona = kv.Value ?? ""; continue; }
                files[kv.Key] = kv.Value ?? "";
            }
            if (files.Count == 0)
            {
                if (!hasPersona) { SetStatus("无可保存的改动", StatusWarn); return; }
                SavePersona(persona, -1);   // 只改了人设：不发空的 write_prompts（后端会拒）
                return;
            }
            // 兼容旧端：单文件时仍可用批量接口（后端 bulk_write_prompts 兼容单条）
            var payload = new JObject { ["files"] = files };
            _ws.SendRequest("write_prompts", payload, resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    SetStatus("批量保存失败: " + (resp["error"]?.ToString() ?? ""), StatusWarn);
                    return;
                }
                var data = resp["data"] as JObject;
                int cnt = data?["count"]?.Value<int>() ?? files.Count;
                if (hasPersona) SavePersona(persona, cnt);
                else SetStatus("已批量保存 " + cnt + " 个文件（下个回合自动生效）", StatusOk);
            }, 15000);
        }

        /// <summary>保存自我人设（config.player.persona）。<paramref name="filesSaved"/> &lt; 0 = 本次只保存了人设。
        ///
        /// **空串照提交**：那是"清空"的合法表达（Python 侧 = 移除该段，system 里不再有这一段）。
        /// 与数值行的"空 = 不提交"惯例刻意相反 —— 那些键留空表示"不改这一项"，
        /// 而人设留空是明确要求"我不要这一段"，不提交就永远清不掉。
        /// `player` 在 Python 侧是热生效块（下个回合的 system 就是新文本），**不触发重启**。</summary>
        private void SavePersona(string persona, int filesSaved)
        {
            if (_ws == null) return;
            var payload = new JObject { ["player"] = new JObject { ["persona"] = persona ?? "" } };
            _ws.SendRequest("set_config", payload, resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    SetStatus("自我人设保存失败: " + (resp["error"]?.ToString() ?? "未知错误"), StatusWarn);
                    return;
                }
                _playerPersona = persona ?? "";
                _promptCache[_currentPath] = _playerPersona;
                string tail = filesSaved >= 0 ? ("已保存 " + filesSaved + " 个文件 + 自我人设") : "自我人设已保存";
                SetStatus(tail + (_playerPersona.Length > 0
                    ? ("（" + _playerPersona.Length + " 字，下个回合生效）")
                    : "（已清空：下个回合起不再注入）"), StatusOk);
            }, 15000);
        }

        private void CreatePersona()
        {
            if (_refs == null || _ws == null) return;
            // NPC 名直接取自上下文（对话内 ⚙ 打开即带 npcId，左侧文件列表同源过滤）——不再手输
            string npc = _currentNpcId;
            if (npc.Length == 0)
            {
                SetStatus("未指定 NPC——请从对话内 ⚙ 打开配置面板后再新建人设", StatusWarn);
                return;
            }
            _ws.SendRequest("create_persona", new JObject { ["npc_id"] = npc }, resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    SetStatus("新建失败: " + (resp["error"]?.ToString() ?? ""), StatusWarn);
                    return;
                }
                string path = resp["data"]?["path"]?.ToString() ?? ("personas/" + npc + ".txt");
                SetStatus("已新建人设 " + path, StatusOk);
                RequestPrompts();     // 刷新列表（新建文件可见）
                SelectFile(path);     // 直接进入编辑
            }, 10000);
        }

        // ------------------------------------------------------------------
        // 删除选中提示词文件：只许删新增件（personas/{npc}.txt /
        // traits/{npc}.json / compaction/{npc}.md），系统件（sections、default、
        // _suffix）Python delete_prompt fail-closed 拒绝，C# 本地预判只做友好提示。
        // 两段确认：首击武装「确认删除？」，3 秒内再击执行（判定在 TwoStepConfirm，
        // 单测锁着边界；点击间隔判超时、不占 Update——面板关闭态本组件 Update 不运行）。
        // 删除成功后必须把文件移出 _promptCache：全量保存 write_prompts 会把缓存整包写回，
        // 留着就会把刚删的文件原样复活。
        // ------------------------------------------------------------------

        private void DeleteCurrentFile()
        {
            if (_refs == null || _ws == null) return;
            string path = _currentPath ?? "";
            if (path.Length == 0)
            {
                DisarmDelete();
                SetStatus("请先在左侧选择要删除的文件", StatusWarn);
                return;
            }
            bool del;
            if (!_deletable.TryGetValue(path, out del)) del = IsDeletableRel(path);
            if (!del)
            {
                DisarmDelete();
                SetStatus("系统内置文件不可删除：" + path, StatusWarn);
                return;
            }
            // 两段确认（判定在 TwoStepConfirm，单测锁着"恰好 3 秒/过期重来/不残留武装"这些边界）
            var step = _deleteConfirm.Click(Time.time);
            if (step == TwoStepConfirm.Step.First)
            {
                SetBtnLabel(_refs.DeletePromptButton, "确认删除？");
                SetStatus("再点一次「确认删除？」执行（3 秒内有效，不可恢复）", StatusWarn);
                return;
            }
            if (step == TwoStepConfirm.Step.Expired)
            {
                DisarmDelete();   // 武装过期：本次点击视作重新开始
                SetStatus("确认已超时，请重新点击「删除文件」", StatusDim);
                return;
            }
            DisarmDelete();       // Second：Click 内部已解除武装，这里只负责把按钮文案还原
            _ws.SendRequest("delete_prompt", new JObject { ["path"] = path }, resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    SetStatus("删除失败: " + (resp["error"]?.ToString() ?? "未知错误"), StatusWarn);
                    return;
                }
                _promptCache.Remove(path);   // 关键：否则下次批量保存会把删掉的文件写回
                _deletable.Remove(path);
                if (_currentPath == path)
                {
                    _currentPath = "";
                    if (_refs.CurrentFileLabel != null) _refs.CurrentFileLabel.text = "（未选择文件）";
                    if (_refs.PromptInput != null) _refs.PromptInput.text = "";
                    UpdatePromptLayout();
                }
                SetStatus("已删除 " + path + "（下回合生效：人设回落 default，标签/压缩指令回落全局）", StatusOk);
                RequestPrompts();   // 刷新列表
            }, 10000);
        }

        /// <summary>解除删除武装并还原按钮文案</summary>
        private void DisarmDelete()
        {
            _deleteConfirm.Disarm();
            SetBtnLabel(_refs?.DeletePromptButton, "删除文件");
        }

        // ------------------------------------------------------------------
        // 重置选中提示词文件为**随包内置**的默认内容（09-16）
        //
        // 与「删除文件」的关系：两者都不可恢复，所以都用两段确认；但闸门完全不同 ——
        //   删除：只许删"用户新增件"（Python delete_prompt fail-closed）
        //   重置：只许还原"内置副本里有同名文件"的（Python reset_prompt 把关）——
        //         于是 sections / default / _suffix 这些**不可删**的系统件恰好是**可重置**的，
        //         它们正是"改坏了最需要救回来"的那批。
        // 用户新建件（personas/{npc}.txt 等）没有出厂版本 → Python 明确拒绝并提示去用「删除文件」。
        // 自我人设（`config:` 虚拟条目）不是文件：重置 = 清空 → 走 set_config，**绝不**发 reset_prompt
        //（Python 对 config: 路径显式拒绝，混进去只会得到一句看不懂的报错）。
        //
        // ⚠ 与保存路径的耦合（最容易踩的一处）：`SavePromptFile` 提交的是**全量 _promptCache**，
        //   所以重置成功后必须把新文本写回缓存 —— 否则用户下次点「保存文件」会把重置**之前**的
        //   内容原样写回去，看起来就像"重置自己反弹了"。删除路径的 `_promptCache.Remove` 是同一个坑。
        // ------------------------------------------------------------------

        private void ResetCurrentFile()
        {
            if (_refs == null || _ws == null) return;
            string path = _currentPath ?? "";
            if (path.Length == 0)
            {
                DisarmReset();
                SetStatus("请先在左侧选择要重置的文件", StatusWarn);
                return;
            }
            bool virtualItem = IsVirtualConfigPath(path);
            if (!virtualItem)
            {
                bool can;
                if (!_resettable.TryGetValue(path, out can)) can = true;   // 缺字段=乐观；权威判定在 Python
                if (!can)
                {
                    DisarmReset();
                    SetStatus("「" + path + "」没有内置默认版本（是你新建的文件）：要清掉请用「删除文件」", StatusWarn);
                    return;
                }
            }

            var step = _resetConfirm.Click(Time.time);
            if (step == TwoStepConfirm.Step.First)
            {
                SetBtnLabel(_refs.ResetPromptButton, "确认重置？");
                SetStatus(virtualItem
                    ? "再点一次「确认重置？」执行：清空自我人设（清空后下个回合起不再注入）"
                    : "再点一次「确认重置？」执行：用内置默认内容覆盖当前文件（3 秒内有效，不可恢复）",
                    StatusWarn);
                return;
            }
            if (step == TwoStepConfirm.Step.Expired)
            {
                DisarmReset();
                SetStatus("确认已超时，请重新点击「重置为默认」", StatusDim);
                return;
            }
            DisarmReset();        // Second：Click 内部已解除武装，这里只负责把按钮文案还原

            if (virtualItem)
            {
                // 自我人设的"默认"= 空：清编辑框 + 清缓存，然后走既有的人设保存路径（set_config）。
                // 复用 SavePersona 而不是另写一条 set_config：空串照提交那条约定它已经实现且被测过。
                SetPromptEditorText("");
                _promptCache[path] = "";
                SavePersona("", -1);
                return;
            }

            _ws.SendRequest("reset_prompt", new JObject { ["path"] = path }, resp =>
            {
                if (_refs == null) return;
                if (resp["ok"]?.Value<bool>() != true)
                {
                    string err = resp["error"]?.ToString() ?? "未知错误";
                    // 升级错配（新 DLL + 旧 exe）时 Python 没有这个方法，直接抛 unknown method ——
                    // 对用户来说那句话看不出该做什么，这里翻成一句能照做的。
                    if (err.IndexOf("unknown method", StringComparison.OrdinalIgnoreCase) >= 0)
                        err = "当前 Python 侧不支持重置（AgentLoopServer.exe 未升级），请更新后重试";
                    SetStatus("重置失败: " + err, StatusWarn);
                    return;
                }
                var data = resp["data"] as JObject;
                string txt = data?["text"]?.ToString() ?? "";
                bool changed = data?["changed"]?.ToString() != "False";
                // 关键一步：同步缓存（理由见本节头注释 —— 否则下次全量保存把旧内容写回）
                _promptCache[path] = txt;
                SetPromptEditorText(txt);
                SetStatus(changed
                    ? ("已重置 " + path + " 为内置默认（下个回合生效）")
                    : ("「" + path + "」本来就是内置默认内容，未改动"),
                    changed ? StatusOk : StatusDim);
            }, 15000);
        }

        /// <summary>解除重置武装并还原按钮文案（换文件 / 超时 / 执行完毕）</summary>
        private void DisarmReset()
        {
            _resetConfirm.Disarm();
            SetBtnLabel(_refs?.ResetPromptButton, "重置为默认");
        }

        private static void SetBtnLabel(GameObject btn, string text)
        {
            var txt = btn != null ? btn.GetComponentInChildren<Text>(true) : null;
            if (txt != null) txt.text = text;
        }

        /// <summary>C# 本地可删预判（与 Python _allow_create 同规约；权威判定在 Python）</summary>
        private static bool IsDeletableRel(string rel)
        {
            if (string.IsNullOrEmpty(rel)) return false;
            int slash = rel.IndexOf('/');
            if (slash <= 0 || slash >= rel.Length - 1) return false;
            string name = rel.Substring(slash + 1);
            string stem = name.Contains(".") ? name.Substring(0, name.LastIndexOf('.')) : name;
            return stem != "default" && stem != "_suffix";
        }

        /// <summary>分组+文件名版（list_prompts 未下发 deletable 字段时的兜底）</summary>
        private static bool IsDeletableLocal(string group, string name)
        {
            if (group == "sections") return false;
            if (group == "player") return false;   // 虚拟条目（自我人设）：它不是文件，删不掉；清空 = 清空编辑框再保存
            string stem = name.Contains(".") ? name.Substring(0, name.LastIndexOf('.')) : name;
            return stem != "default" && stem != "_suffix";
        }

        // ------------------------------------------------------------------
        // 悬停气泡：内容登记（引擎/检测/浮出全部在 HoverTip，纯视图件）
        // ------------------------------------------------------------------

        /// <summary>登记大模型页各参数行的悬停说明（悬停区 = 整行：输入框/开关的父节点）。
        /// 生效方式文案按实际改写：除「Python 端口」外全部即时生效（C# 侧参数在保存后
        /// 立刻重拉一次 get_config，Python 侧参数在 ConfigService 里热应用）。</summary>
        private void RegisterFormTooltips()
        {
            if (_tip == null) return;
            RegisterRow(_refs?.BaseUrlInput, "接口地址（填服务商的接口根地址，如 https://api.deepseek.com/v1）。保存后热切换即时生效");
            RegisterRow(_refs?.ApiKeyInput, "接口密钥（掩码显示）。保存后热切换即时生效");
            RegisterRow(_refs?.ModelInput, "模型名（如 deepseek-chat）。换模型即时生效，下一回合用新模型");
            RegisterRow(_refs?.ImageToggle, "多模态开关：允许把图片发给模型（llm.image.enabled）。热切换即时生效");
            RegisterRow(_refs?.PortInput, "Python WS 服务器监听端口（默认 8766）。★唯一需要重启的项：改后自动重启 Python；换端口还要重启游戏（C# 连的是旧端口）");
            RegisterRow(_refs?.TimeoutInput, "回合内等待游戏回应（get_context/call_tool）的超时秒数。保存即生效，不必重启");
            // AI 时间与重试：四个 llm.* 键。文案要点 = 「量的是什么」和「默认值够用，别乱调」，
            // 因为这两件事最容易搞错（尤其把"块间隔"当成"整个请求时长"）。
            RegisterRow(_refs?.LlmTimeoutInput,
                "首字超时秒（llm.timeout，默认 120）。量的是「请求发出 → 第一个字到达」这段等待，"
                + "★不是整个回复的时长★：只要模型还在出字，跑多久都不算超时（这一点 09-18 重做过，"
                + "旧版本拿它当整趟挂钟，把一条正常流着的长回复在第 45 秒砍断过）。"
                + "实测首字延迟最高 28.5 秒，120 留了约 4 倍余量。"
                + "吐字中途的看门狗是另一个键 llm.idle_timeout（默认 60，暂只能手编 config.json）。"
                + "填 0 或留空 = 不提交这一项（保持文件里的原值）；原值为 0 表示不设这个看门狗。");
            RegisterRow(_refs?.LlmTimeoutNsInput,
                "压缩超时秒（llm.timeout_nonstream，默认 180）。非流式请求在整段生成完之前一个字节都不发，"
                + "所以它量的是「完整生成时长」，和上面那个不是一个量纲。只用于回合边界的上下文压缩（一发大 prompt）。"
                + "调得太小会静默废掉压缩——压缩失败只写日志、不回退报错，现象是「聊久了越来越慢」。");
            RegisterRow(_refs?.LlmBudgetInput,
                "硬顶秒（llm.total_budget，默认 0 = 不设限）。一次请求连同全部重试在内的绝对上限。"
                + "★默认关闭，也不建议开★：整趟耗时不是「卡住」的判据——一条很长的回复只要一直在出字就该被允许写完，"
                + "而设了它就会按秒砍断（09-17 那次事故就是这么砍掉一位群成员的整轮发言的）。"
                + "填 >0 才会生效，效果是把上面两个看门狗收缩进剩余额度；只约束聊天路径，压缩不受它限制。"
                + "填 0 或留空 = 不提交这一项（保持文件里的原值）。");
            RegisterRow(_refs?.LlmRetriesInput,
                "失败重试次数（llm.retries，默认 1；总尝试 = 本值 + 1）。会重试：连不上、首字超时（一个字都没出）、上游流到一半掐断、HTTP 408/429/5xx。"
                + "不会重试：400/401/403 这类认证与格式错误，以及已经吐出过内容的流式中断（重发会让你把同一段话看两遍）。填 0 = 不重试。");
            RegisterRow(_refs?.RoomSpeakersInput,
                "群聊每轮发言人数上限（room.max_speakers_per_round，默认 3）。一轮里最多几个人真的开口："
                + "被点名的人可以弃权（回【沉默】）或者出错，两种情况都不占名额，候选会继续往下补位；"
                + "候选名单本身以群成员数为上限，所以填一个很大的值就等于「每轮所有成员都可能开口」。"
                + "调小的意义是省钱：8 人群默认一轮最多 3 次模型调用，旁听者零消耗。填 1 起；即时生效（下一轮就用新值）。");
            RegisterRow(_refs?.InitiativeEnabledToggle, "主动互动总开关：关了之后 NPC 不再自己找上门（日节拍不触发）。你主动找他对话、工具动作都不受影响。即时生效");
            RegisterRow(_refs?.DailyChanceInput, "每个游戏日尝试触发主动开口的概率 0-100，0=关闭（等于关了主动互动）。即时生效，下一个游戏日就用新值");
            RegisterRow(_refs?.NpcCooldownRealInput, "同一个 NPC 两次主动开口之间的现实秒冷却（防挂机时反复来）。即时生效");
            RegisterRow(_refs?.NpcCooldownDaysInput, "同一个 NPC 两次主动开口之间的游戏日冷却（与上面取较大者）。即时生效");
            RegisterRow(_refs?.MinIntervalInput, "全局熔断：两次主动开口至少间隔这么多现实秒，防跳日时一次涌来一堆。即时生效");
            RegisterRow(_refs?.LowThreshInput, "好感低于该值时视为低好感，主动开口概率按右边开关减半。即时生效");
            RegisterRow(_refs?.LowHalveToggle, "开启后：好感 < 低好感阈值的 NPC，主动开口概率减半（陌生人/仇人少来打扰）。即时生效");
            RegisterRow(_refs?.CompactionEnabledToggle, "自动压缩开关：对话变长时是否自动把早段合并成纪要。关掉后手动压缩（标题栏按钮 / /compact）仍然可用。即时生效");
            RegisterRow(_refs?.CtxWindowInput, "上下文窗口 token 预算（compaction.ctx_window）：压缩与占用率都按它算。填模型的真实窗口。即时生效");
            RegisterRow(_refs?.HeadersInput, "自定义请求头（config llm.headers，可空）：格式 `名字: 值`，多条用分号隔开，例如 x-opencode-session: agent-loop。仅当网关要求客户端自报家门时才需要（缺了会回 400/403）；留空 = 不附加任何头。改完即时生效，可先用「测试连接」验证");
            RegisterRow(_refs?.ReasoningInput, "思考强度（config llm.reasoning_effort，可空）：部分模型的思维链开关，只接受 low / high / max，留空 = 不发送。这一行只有「测试连接」探测到当前网关与模型支持时才出现；选了档位后 NPC 的「内心思量」才会出现。改完即时生效");
            RegisterRow(_refs?.RetainRatioInput, "保留原文比例 0.05-0.6（默认 0.16）：压缩时最近这段比例的对话原样保留，更早的才合并成纪要。调大=记得更细、上下文更大");
            RegisterRow(_refs?.ThresholdRatioInput, "自动压缩触发阈值 0.3-0.95（默认 0.8）：占用超过「窗口 × 该比例」才自动压缩。调小=更早开始压");
            RegisterRow(_refs?.PortraitToggle, "显示立绘：关掉后不再调用游戏立绘接口（立绘槽位隐藏）。立绘相关异常/崩溃时可作为逃生开关。即时生效");
            if (_refs?.DeletePromptButton != null)
                _tip?.Register(UiRects.Of(_refs.DeletePromptButton.transform),
                    "删除选中的新增文件（系统内置文件不可删）。两段确认：首击武装，3 秒内再击执行。删除后 NPC 人设回落 default，标签/压缩指令回落全局");
            if (_refs?.ResetPromptButton != null)
                _tip?.Register(UiRects.Of(_refs.ResetPromptButton.transform),
                    "把选中的文件还原成随 mod 内置的默认内容（你的改动会被覆盖，不可恢复）。两段确认：首击武装，3 秒内再击执行。"
                    + "系统内置文件（全局设定 / default / _suffix）也能重置——改坏了就用它救回来；"
                    + "自己新建的文件没有默认版本，请改用「删除文件」。自我人设条目上的重置 = 清空。改完下个回合生效");
        }

        /// <summary>登记一条参数行悬停（field 传输入框/开关组件，取其父行整行作悬停区）
        /// 取父行 RectTransform 走 `UiRects`：旧写法 `field.transform.parent as RectTransform`
        /// 在本环境静默返回 null → `Register` 直接 return → **一条登记都没有**（气泡全无的第二个原因）。</summary>
        private void RegisterRow(Component field, string text)
        {
            if (_tip == null || field == null) return;
            var row = UiRects.ParentOf(field);
            if (row == null)
            {
                if (!_rowRectWarned)
                {
                    _rowRectWarned = true;
                    ModMain.P("[ConfigPresenter] 悬停登记失败：取不到参数行 RectTransform（field=" +
                              field.name + "）——本行悬停气泡不会出现");
                }
                return;
            }
            _tip.Register(row, text);
        }
        private bool _rowRectWarned;   // 取不到行只吼一次（每行都吼会刷屏）

        /// <summary>
        /// 就地改某个参数行的标签文案：预制件里标签是**写死的**（如「RPC 超时秒（重启生效）」），
        /// 而生效方式随 Python 侧是否做了热生效而变——文案不跟着改就会骗人（用户会以为必须重启）。
        /// 行内标签节点名固定为 "Label"（与 ConfigUiBuilder.MakeFormRow 同构）；找不到只记日志，不抛。
        /// </summary>
        private static void RelabelRow(Component field, string newLabel)
        {
            try
            {
                if (field == null || field.transform == null || field.transform.parent == null) return;
                var row = field.transform.parent;
                for (int i = 0; i < row.childCount; i++)
                {
                    var child = row.GetChild(i);
                    if (child == null || child.name != "Label") continue;
                    var txt = child.GetComponent<Text>();
                    if (txt == null || txt.text == newLabel) return;
                    txt.text = newLabel;
                    P("[ConfigPresenter] 标签已更新：" + row.name + " → " + newLabel);
                    return;
                }
                P("[ConfigPresenter] 未找到标签节点（row=" + row.name + "），文案保持原样");
            }
            catch (Exception e) { P("[ConfigPresenter] RelabelRow: " + e.Message); }
        }

        /// <summary>
        /// 就地纠正「小数参数行」的输入框校验类型（实机 bug 修复）。
        ///
        /// 为什么必须有这个方法：AB 预制件是**权威**的（AB 构建下不走 ConfigUiBuilder，
        /// 那里写的 <c>ContentType.DecimalNumber</c> 是死代码），而预制件里这两个框是从
        /// 整数框 PortRow 克隆来的，Integer 校验被一并继承。C# 侧若不纠正，就只剩
        /// 「改预制件 → 重打 AB → 重新导出 → 玩家更新」一条路；本方法让**手上这份老 AB 也能救**，
        /// 与 <see cref="AbConfigPanel.EnsureScrollInputTarget"/> 是同一个思路（两边都修，
        /// 老包靠代码兜底、新包靠预制件）。
        ///
        /// 只动 <c>contentType</c> 一个属性：uGUI 的 <c>EnforceContentType()</c> 会连带把
        /// <c>m_KeyboardType</c>（NumberPad→DecimalPad）与 <c>m_CharacterValidation</c>
        /// （Integer→Decimal）一起改对，不必逐个字段写。**不会清空已有文本**（只改校验器）。
        /// </summary>
        private static void EnsureDecimalInput(InputField field, string label)
        {
            try
            {
                if (field == null) return;
                if (field.contentType == InputField.ContentType.DecimalNumber) return;
                var before = field.contentType;
                field.contentType = InputField.ContentType.DecimalNumber;
                P("[ConfigPresenter] " + label + " 输入框校验类型已纠正：" + before + " → DecimalNumber"
                  + "（预制件里是整数框克隆来的，会吞小数点；已就地修好，无需重打 AB）");
            }
            catch (Exception e) { P("[ConfigPresenter] EnsureDecimalInput(" + label + "): " + e.Message); }
        }

        // ------------------------------------------------------------------
        // 小工具
        // ------------------------------------------------------------------

        private static Button GetButton(GameObject go) => go != null ? go.GetComponent<Button>() : null;

        private static void SetText(InputField field, string value)
        {
            if (field != null) field.text = value ?? "";
        }

        /// <summary>
        /// JToken → 不变文化字符串；**null 一律返回 null**（不是空串），好让调用方的
        /// `?? "默认值"` 真正生效。
        ///
        /// ⚠ 09-20 修：原实现对 null 返回 `""`，于是 `ToStringInvariant(x) ?? "120"` 得 `""`
        ///    —— `""` 不是 null，`??` 不触发，兜底是**死代码**。后果：config.json 里删掉
        ///    `llm.timeout` 这类键（模板明说"删掉也能跑"）后，面板显示**空白输入框**，
        ///    而 Python 侧深合并其实正用着 120 —— 玩家会以为"这项没配"。
        ///    Float 分支也不再用 `(float)` 强转：那会把 double 精度截断（0.16 → 0.16f）。
        /// </summary>
        private static string ToStringInvariant(JToken token)
        {
            if (token == null || token.Type == JTokenType.Null) return null;
            if (token.Type == JTokenType.Float)
                return ((double)token).ToString("R", CultureInfo.InvariantCulture);
            return token.ToString();
        }
        private static int TryParseInt(InputField field, int fallback, string label)
        {
            return int.TryParse(field?.text?.Trim(), out var v) ? v : fallback;
        }

        private static double TryParseFloat(InputField field, double fallback, string label)
        {
            return double.TryParse(field?.text?.Trim(), NumberStyles.Float,
                                   CultureInfo.InvariantCulture, out var v) ? v : fallback;
        }

        private bool TryParseIntStrict(InputField field, out int value, string label)
        {
            string s = field?.text?.Trim() ?? "";
            if (int.TryParse(s, out value)) return true;
            SetStatus(label + " 需要整数，当前值: " + (s.Length==0?"(空)":s), StatusWarn);
            return false;
        }

        private bool TryParseFloatStrict(InputField field, out double value, string label)
        {
            string s = field?.text?.Trim() ?? "";
            if (double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out value)) return true;
            SetStatus(label + " 需要数字，当前值: " + (s.Length==0?"(空)":s), StatusWarn);
            return false;
        }

        /// <summary>写状态栏第一行（操作反馈）。诊断尾行由 <see cref="RenderStatus"/> 叠加。</summary>
        private void SetStatus(string msg, Color color)
        {
            _lastStatusMsg = msg ?? "";
            _lastStatusColor = color;
            RenderStatus();
        }

        // ------------------------------------------------------------------
        // 连接故障诊断 —— 方案 B：粘性尾行（09-20 新增）
        // ------------------------------------------------------------------
        //
        // 要解决的问题：Python 起不来时，失败原因（缺 exe / 杀软拦 / 端口占）**只在日志里**，
        // 玩家打开配置面板看到的是一句"读取配置失败: 未连接 Python"——他本来就知道没连上，
        // 想知道的是**为什么**。
        //
        // 为什么不能只是"把诊断写进状态栏"就完事：StatusLabel 是**复用位**，SetStatus 直接赋值。
        // 玩家一看空面板就会慌，然后乱点（测试连接 / 切换 Tab / 点保存）——**每一次都会把
        // 唯一的线索顶掉**，于是又回到完全静默。这正是本机制存在的理由。
        //
        // 方案 B（不改预制件能达到的最好效果）：
        //   · 正常提示照常显示在**第一行**（按钮反馈不能丢，否则玩家以为按钮坏了）；
        //   · 故障诊断**常驻在第二行**，直到连接真正恢复才清除。
        // 容量依据（实机量 prefab 得到）：BG 760 宽 → Status 736px；字号 14 → 约 50 字/行；
        // 高度 32（VerticalOverflow=Overflow）→ **恰好两行**。所以第一行 + 第二行正好用满，
        // 绝不能塞第三行（会被静默裁掉）。诊断文案必须收敛到 ≤ 约 48 字。

        /// <summary>设置/清除粘性诊断尾行。msg 为空 = 清除。</summary>
        private void SetStickyDiag(string msg)
        {
            _stickyDiag = msg ?? "";
            RenderStatus();
        }

        /// <summary>重画状态栏：第一行 = 最近一次操作反馈，第二行 = 粘性诊断（有才画）。
        ///
        /// ⚠ 颜色必须取**诊断**的警示色，而不是第一行的颜色（自查发现并修正）：
        ///   StatusLabel 是**一个** Text 组件 —— 两行文字共用一个 color 字段。
        ///   原先用 `_lastStatusColor` 会让"测试连接成功"（绿）把"⚠ 未找到入口"也染成绿色
        ///   —— 视觉上像一切正常，与文字内容自相矛盾。有诊断时一律走警示色：
        ///   那才是玩家此刻**最需要看见**的信息，第一行的绿/灰让位给它。</summary>
        private void RenderStatus()
        {
            if (_refs?.StatusLabel == null) return;
            string head = _lastStatusMsg ?? "";
            if (_stickyDiag.Length > 0)
            {
                _refs.StatusLabel.text = (head.Length > 0 ? head + "\n" : "") + _stickyDiag;
                _refs.StatusLabel.color = StatusWarn;   // 诊断在 = 当前是故障态，整条走警示色
            }
            else
            {
                _refs.StatusLabel.text = head;
                _refs.StatusLabel.color = _lastStatusColor;
            }
        }
    }
}
