/// <summary>
/// 对话 UI 视图门面（MonoBehaviour）—— 对外只说「视图语言」：
/// Open/Close、加一条用户气泡、加分隔条、回合过程折叠组、流式气泡（delta 合并）、历史替换、忙态。
///
/// 内聚三个性能措施：
///  ① delta 逐帧合并：AppendDelta 只写 StringBuilder 缓冲，LateUpdate 每帧一次刷 Text
///     ——流式 token 很密，不合并会逐 token 触发 Canvas rebuild（最常见的流式 UI 卡顿源）
///  ② 行数上限 MAX_ROWS：超出销毁最旧行（同步裁 list），长对话顶点数有界
///  ③ 智能滚底：仅当用户处于底部时才自动跟随（滚轮上翻即停；再向下滚回跟随），回看历史不被拽走
///
/// 点的手感：CloseButton / SendButton 走 Button + ClickUtils（委托桥三步写法，ClickCatcher 已退役）。
/// 明确不做：不解析 JSON 协议字段、不持有 WsClient 引用、不发起任何网络调用——
/// 那些都在 ChatPresenter。本类只知道 ChatWindowRefs 与行视图。
/// </summary>
using System;
using System.Collections.Generic;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    /// <summary>
    /// IL2CPP 注意：本类是自定义 MonoBehaviour，MelonLoader 0.5（Unhollower）要求
    /// 先用显式 ClassInjector.RegisterTypeInIl2Cpp&lt;ChatWindow&gt;() 注册进 IL2CPP 类型系统，
    /// 否则 AddComponent&lt;ChatWindow&gt;() 会抛 TypeInitializationException。
    /// 注册调用在宿主侧（主工程 ModMain / 冒烟 UiSmokeProbe）完成，本类不引 MelonLoader——
    /// 保持纯 Unity 依赖，可被 Unity 预览工程链接编译（神识传音同款：注册后才 AddComponent）。
    /// </summary>
    public class ChatWindow : MonoBehaviour
    {
        /// <summary>
        /// IL2CPP 互操作标准构造（Unhollower 按此签名实例化）。
        /// 仅 MelonLoader/Unhollower 环境需要；标准 Unity 编辑器不存在此签名，
        /// 用 #if MELONLOADER 隔离该符号下的差异（原先是为了同一源码能被已删除的 ui_preview 预览工程共用）。
        /// </summary>
#if MELONLOADER
        public ChatWindow(System.IntPtr ptr) : base(ptr) { }
#endif

        private ChatWindowRefs _r;
        private readonly StringBuilder _delta = new StringBuilder();
        private Text _bubbleText;              // 当前流式气泡的文本组件
        private GameObject _deltaBubble;       // 当前流式气泡行（Root）
        private bool _deltaDirty;
        private bool _followingBottom = true;
        private int _lastChildCount = -1;      // 跟随用：上一帧 Content 子节点数（判"内容长了"）
        private float _lastContentH = -1f;     // 跟随用：上一帧 Content 高度（文本流式增长也算）
        private int _rowCount;
        private bool _busy;

        private StepGroup _curGroup;           // 当前回合过程组（直播）
        private bool _stepOpen;
        private ThinkGroup _curThink;          // 当前内心思量折叠区（think 流式）
        private StepGroup _curSys;             // 当前回合「系统组装」折叠块（复用 StepGroup 模板）
        private StepGroup _curCtx;             // 当前回合「运行时上下文」折叠块（复用 StepGroup 模板）

        // ---- 同一回合同段正文「只成泡一次」+「主动传音分隔条只插一条」（修重复输出）----
        // 终稿正文会**两路到达**：① `step(text)`（每步正文 / 非流式后端整段正文）
        // ② `npc_reply`（回合收口帧，主动传音还带 initiative=true）。
        // 旧写法：① 先成泡并 ClearStreamingState（`_deltaBubble=null`），② 到的时候发现
        // "没有活跃流式气泡" → 又建一条新气泡（还补一条分隔条）→ 屏幕上同段话出现两遍
        // （真机 00:50 云含"论道婉拒"那回合：日志里 `step(text)` 与 `npc_reply` 逐字相同）。
        private int _curTurn = -1;             // 最近一个 step 帧的回合号（ChatPresenter.NoteTurn 写）
        private int _lastRenderTurn = -1;      // 最近一次成泡的回合号（去重判据之一）
        private string _lastRenderText;        // 该回合成泡的正文（去重判据之二）
        private GameObject _lastRenderRow;     // 那条气泡行（补分隔条时定位用）
        private int _dividerTurn = -1;         // 已插过「—— 主动传音 ——」分隔条的回合号（同回合只插一条）
        private int _inputTurn = -1;           // 输入块归属回合（换回合新建，防跨回合串块）
        private Text _tokensBar;                 // 底部 token 统计文本（节点由预制体提供：BG/StatsBar，缺失容错=不显示）
        private bool _statsBarWarned;            // 统计栏节点没解析到：只吼一次（见 SetStats）

        // ---- 微信式头像槽位（立绘预填到头像模板，克隆行共享纹理）----
        private RawImage _userAvatarImg;
        private RawImage _npcAvatarImg;
        public RawImage UserAvatarImg => _userAvatarImg;
        public RawImage NpcAvatarImg => _npcAvatarImg;
        // 旧顶部大立绘槽位（已废），保留只读属性以兼容未启用 AB_UI 的代码分支
        public RawImage PortraitLeft => null;
        public RawImage PortraitRight => null;

        /// <summary>渲染行数上限（200 → 1000）。
        /// 历史回放现在要**全部**（Python 侧 limit=0），200 行会在实机 26 回合 ≈ 104 行时刚好卡住、
        /// 更长一点就又开始偷偷丢最旧的（用户明确要求"我要看全部"）。1000 行 ≈ 250 回合，
        /// 覆盖实际规模；真的超了会**显式**在列表顶部插一行「更早的 N 条未显示」——
        /// 绝不再静默截断（今天两次误判都是"UI 悄悄少给"引起的）。
        /// 代价：Content 带 VerticalLayoutGroup + ContentSizeFitter，整表重排 O(行数)，
        /// 1000 行是"能忍"的上界；真要上千回合，正解是上拉分页/虚拟列表（仍未做）。</summary>
        private const int MAX_ROWS = 1000;
        /// <summary>本次 ResetContent 之后被裁掉的行数（ReplaceHistory 结束时据此插提示行）。</summary>
        private int _trimmedRows;
        private const float BOTTOM_EPS = 0.02f;

        // ---- 对外事件（Present 订阅）----
        public event Action CloseClicked;
        /// <summary>用户点了发送/回车（Present 读输入框文本再发 WS）</summary>
        public event Action SubmitRequested;
        /// <summary>用户点了标题栏 ⚙ 配置按钮（ModMain 订阅 → ConfigPanelOpener）。
        /// AB 预制体未加该节点时永远不触发，无副作用</summary>
        public event Action ConfigClicked;
        /// <summary>用户点了标题栏 🗑 删除按钮（Presenter 决定进入流程：忙时拒绝，闲时重放后入模式）。
        /// 预制体未加 BG/DeleteBtn 时永远不触发</summary>
        public event Action DeleteBtnClicked;
        /// <summary>用户点了标题栏「压缩」按钮（Presenter 发手动压缩请求，忙时即时拒绝）。
        /// 预制体未加 BG/CompactBtn 时永远不触发</summary>
        public event Action CompactClicked;
        /// <summary>删除模式两段确认后执行（Presenter 发 delete_history RPC，载荷=选中的回合号）</summary>
        public event Action<List<int>> DeleteConfirmed;
        /// <summary>删除确认按钮第一击武装时触发（Presenter 发 preview_delete_history 取弹窗数据，
        /// 回来后经 ApplyDeletePreview 展示摘要/拒绝原因）。预制体无删除功能则永不触发</summary>
        public event Action<List<int>> DeletePreviewRequested;

        // ---- 删除模式（微信式多选）----
        /// <summary>是否处于删除选择模式（行点击=切换选中、输入区禁用、底部操作条显示）</summary>
        public bool Selecting { get; private set; }
        private readonly List<RowMeta> _rows = new List<RowMeta>();   // 行注册表：与 Content 子节点同序，绑定 seqs/遮罩
        private RowMeta _lastMeta;            // 最近一次注册的行（Append* 系列取它回传）
        private bool _confirmArmed;            // 删除按钮两段确认的武装态
        private float _confirmArmTime;

        // ---- 状态（Present 查询）----
        public string CurrentNpcId { get; private set; }
        public bool IsOpen { get; private set; }
        public bool HasActiveBubble => _deltaBubble != null;
        public bool IsBusy => _busy;
        public InputField NpcLabel => _r != null ? _r.NpcLabel : null;
        /// <summary>玩家名显示框（BG/Playerput，只读；缺失容错）</summary>
        public InputField PlayerLabel => _r != null ? _r.PlayerLabel : null;
        public InputField ChatInput => _r != null ? _r.ChatInput : null;

        /// <summary>滚轮灵敏度**下限**（一格 ≈ 4.5 行正文；正文行高实测 19.79px）。
        ///
        /// 为什么在 C# 里再设一遍（"两条腿"，同 `AbConfigPanel.EnsureScrollInputTarget` 的约定）
        /// 预制件 `BG/Scroll` 的 `m_ScrollSensitivity` 在老 AB 里是 30 —— 聊天余量动辄 2000~4000px，
        /// 30/格从底爬到顶要 70~130 格，手感像"爬"。只改预制件的话**必须重打 AB 才生效**；
        /// 这里兜一道，手上这份老 AB 也能立刻好用，预制件那条腿只负责"把修复固化进资产"。
        ///
        /// 语义是**下限**不是赋值：预制件配得比它高就听预制件的（以后想更快改资产即可，不用动 C#）。
        /// </summary>
        private const float SCROLL_SENSITIVITY_MIN = 90f;

        /// <summary>把 `BG/Scroll` 的滚轮灵敏度抬到下限（低于才动，幂等；缺节点/异常只记一行不阻断开窗）。</summary>
        private void ApplyScrollSensitivityFloor()
        {
            try
            {
                var s = _r != null ? _r.Scroll : null;
                if (s == null) return;
                if (s.scrollSensitivity < SCROLL_SENSITIVITY_MIN)
                {
                    float was = s.scrollSensitivity;
                    s.scrollSensitivity = SCROLL_SENSITIVITY_MIN;
                    ModMain.P("[ChatWindow] 滚轮灵敏度兜底：" + was + " → " + SCROLL_SENSITIVITY_MIN
                              + "（预制件未重打 AB，走运行期兜底）");
                }
            }
            catch (Exception e) { ModMain.P("[ChatWindow] ApplyScrollSensitivityFloor: " + e.Message); }
        }

        public void Init(ChatWindowRefs refs)
        {
            _r = refs;
            ApplyScrollSensitivityFloor();   // 老 AB 也立刻好用，不必等重打
            if (_r.CloseButton != null)
            {
                var btn = _r.CloseButton.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => CloseClicked?.Invoke());
            }
            if (_r.ConfigButton != null)
            {
                var cfgBtn = _r.ConfigButton.GetComponent<Button>();
                if (cfgBtn != null) ClickUtils.Attach(cfgBtn, () => ConfigClicked?.Invoke());
            }
            if (_r.SendButton != null)
            {
                var btn = _r.SendButton.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => SubmitRequested?.Invoke());
            }
            // 删除模式节点（全部可选，缺失=无删除功能）
            if (_r.DeleteButton != null)
            {
                var btn = _r.DeleteButton.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, () => DeleteBtnClicked?.Invoke());
            }
            if (_r.CompactButton != null)
            {
                var btn = _r.CompactButton.GetComponent<Button>();
                if (btn != null)
                {
                    _compactBtnRef = btn;
                    ClickUtils.Attach(btn, () => CompactClicked?.Invoke());
                    // 忙态文案组件（TMP 优先，回落 uGUI Text）；取不到就只做置灰，不报错
                    try { _compactLabel = btn.GetComponentInChildren<TMPro.TextMeshProUGUI>(true); } catch { }
                    if (_compactLabel == null) { try { _compactLabel = btn.GetComponentInChildren<Text>(true); } catch { } }
                    // ：压缩按钮接线留痕——"点了没反应"时先看这行在不在
                    ModMain.P("[ChatWindow] 压缩按钮已接线（忙态文案组件=" +
                              (_compactLabel != null ? "找到" : "未找到→只置灰") + "）");
                }
            }
            if (_r.SelectAllBtn != null)
            {
                var btn = _r.SelectAllBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, SelectAllToggle);
            }
            if (_r.DeleteConfirmBtn != null)
            {
                var btn = _r.DeleteConfirmBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, OnDeleteConfirmClicked);
            }
            if (_r.CancelBtn != null)
            {
                var btn = _r.CancelBtn.GetComponent<Button>();
                if (btn != null) ClickUtils.Attach(btn, ExitSelecting);
            }
            if (_r.DeleteBar != null) _r.DeleteBar.SetActive(false);   // 平时隐藏，进选择模式才显示
            // 头像槽位：直取行模板内的 RawImage（模板 inactive，画布为零；克隆行时才激活）。
            _userAvatarImg = _r.UserAvatarImg;
            _npcAvatarImg = _r.NpcAvatarImg;
            // 底部 token 统计条：节点由预制体提供（BG/StatsBar），代码不再构建；缺失=null 容错
            _tokensBar = _r.StatsBarText;
            Close();
        }

        // ------------------------------------------------------------------
        // 底部 token 统计条（节点由预制体提供：BG/StatsBar，样式在编辑器里随意调；
        // 数值格式化仍在此处——对齐 stats.py 头注释："数字格式化由 C# UI 侧做"）
        // ------------------------------------------------------------------

        /// <summary>刷新底部 token 统计文本（接收 stats_update 事件 / open_chat 首屏基线）。</summary>
        public void SetStats(JObject stats)
        {
            if (stats == null) return;
            if (_tokensBar == null)
            {
                // 主动开口开窗后底部一直显示预制体的默认文本 "New Text"，
                // 有两种可能，必须分开：
                //   (a) 从没调到 SetStats —— 那是历史回放被丢弃（已在 ChatPresenter 侧修）
                //   (b) 节点没解析到 —— AbChatPanel 找的是 `BG/StatsBar/Text` ?? `BG/StatsBar`；
                //       若两者都不是 Text（可见那行在更深一层），`_tokensBar` 就是 null，
                //       本函数一直静默 no-op，界面永远停在预制体默认文本。
                // 这行把 (b) 单独钉死。只吼一次，不刷屏。
                if (!_statsBarWarned)
                {
                    _statsBarWarned = true;
                    ModMain.P("[ChatWindow] SetStats: ★统计栏节点未解析★（BG/StatsBar/Text 与 BG/StatsBar "
                              + "都没拿到 Text 组件）→ 统计被静默丢弃，界面会一直停在预制体的默认文本。"
                              + "反之若日志里没有这一行，问题就在调用侧（根本没调 SetStats）");
                }
                return;
            }
            string line = BuildStatsLine(stats);
            if (_tokensBar.text != line) _tokensBar.text = line;
        }

        private static string BuildStatsLine(JObject stats)
        {
            var tu = stats["tokenUsage"] as JObject;
            if (tu == null) return "";
            long total = LongOf(tu["totalTokens"]);
            long billed = LongOf(tu["billedInputTokens"]);
            long cache = LongOf(tu["cacheReadTokens"]);
            long uncached = LongOf(tu["uncachedInputTokens"]);
            long output = LongOf(tu["outputTokens"]);
            if (total == 0 && billed == 0 && output == 0) return "";
            string hit = tu["cacheHitPercent"]?.ToString();   // "79" 或 null(无输入不显示)
            var sb = new StringBuilder("总 ");
            sb.Append(FormatTok(total))
              .Append(" ｜ 输入 ").Append(FormatTok(billed))
              .Append("（缓存 ").Append(FormatTok(cache))
              .Append(" / 未缓存 ").Append(FormatTok(uncached)).Append("）")
              .Append(" ｜ 输出 ").Append(FormatTok(output));
            if (!string.IsNullOrEmpty(hit)) sb.Append(" ｜ 缓存率 ").Append(hit).Append("%");
            // 上下文：最新一次请求的输入规模（contextPressure.pressureTokens）；
            // 配置了 compaction.ctx_window 才有容量与占用率，缺省只显示规模
            var cp = stats["contextPressure"] as JObject;
            if (cp != null)
            {
                long ctx = LongOf(cp["pressureTokens"]);
                if (ctx > 0)
                {
                    sb.Append(" ｜ 上下文 ").Append(FormatTok(ctx));
                    long win = LongOf(cp["contextWindow"]);
                    if (win > 0) sb.Append("/").Append(FormatTok(win));
                    string occ = cp["occupancyPercent"]?.ToString();
                    if (!string.IsNullOrEmpty(occ)) sb.Append(" ").Append(occ).Append("%");
                }
            }
            var ss = stats["sessionStats"] as JObject;
            if (ss != null)
            {
                long turns = LongOf(ss["turns"]);
                long steps = LongOf(ss["steps"]);
                if (turns > 0 || steps > 0) sb.Append(" ｜ ").Append(turns).Append("轮/").Append(steps).Append("步");
            }
            return sb.ToString();
        }

        private static long LongOf(JToken t)
        {
            if (t == null) return 0;
            try { return Convert.ToInt64(t); } catch { }
            try { return (long)Convert.ToDouble(t); } catch { }
            return 0;
        }

        private static string FormatTok(long n)
        {
            if (n >= 1000000) return (n / 1000000.0).ToString("0.#") + "M";
            if (n >= 1000) return (n / 1000.0).ToString("0.#") + "k";
            return n.ToString();
        }

        private void Update()
        {
            PortraitCache.PumpCapture();   // 立绘快照队列：开窗渲染成功后延迟一帧抓 RT（窗口开着 RT 才有效）
            // ③ 智能滚底：上滚 → 停止跟随（回看历史）。
            //   下滚**不**在这里恢复跟随（手感修复）：旧实现是 `else if(wheel<0)_followingBottom=true`，
            //   而 LateUpdate 每帧 `verticalNormalizedPosition = 0f` —— 于是**向下滚一格 = 下一帧瞬移到底**，
            //   用户报的"向下一下子就到最新消息"就是这一行。跟随的恢复改由 LateUpdate 按**真实位置**判定
            //   （见 AtBottom），滚回底部才恢复；向上回看中途误触下滚不再把人拽走。
            var wheel = Input.mouseScrollDelta.y;
            if (wheel > 0.01f) _followingBottom = false;
            // 删除模式两段确认：武装超过 3 秒未再击 → 自动解除
            if (_confirmArmed && Time.time - _confirmArmTime > 3f) DisarmConfirm();
        }

        private void LateUpdate()
        {
            // ① delta 合并刷新：一帧一次写 Text
            if (_deltaDirty && _bubbleText != null)
            {
                _bubbleText.text = _delta.ToString();
                _deltaDirty = false;
            }
            // ③ 智能滚底
            if (!IsOpen || _r == null || _r.Scroll == null || _r.Content == null) return;
            try
            {
                if (!_followingBottom)
                {
                    // 未跟随：用户滚/拖回底部 → 自愈恢复跟随（判据是**位置**不是滚轮方向）
                    if (AtBottom()) _followingBottom = true;
                    return;
                }
                // 已跟随：只在**内容真的长高/多了行**时钉底。
                // 旧实现每帧无条件写 `= 0f`，于是拖动/惯性在下一帧就被抹掉（滚动条拖不动、
                // 内容拖不动，这是"手感很差"的另一半）；改成"只在增长时钉"后，用户拖动得以生效，
                // 下一分支再据此停止跟随。
                int cc = _r.Content.childCount;
                float ch = _r.Content.rect.height;
                bool grew = cc != _lastChildCount || ch > _lastContentH + 0.5f;
                _lastChildCount = cc;
                _lastContentH = ch;
                if (grew) _r.Scroll.verticalNormalizedPosition = 0f;   // 新内容到达 → 钉底
                else if (!AtBottom()) _followingBottom = false;        // 用户自己滚/拖走了 → 停止跟随
            }
            catch { }   // 布局尚未就绪时读 rect 可能抛，滚底失败不该影响主流程
        }

        /// <summary>滚动位置是否已在底部（跟随的唯一恢复判据）。
        ///
        /// `verticalNormalizedPosition` 0=底 1=顶，但**内容比视口矮时 Unity 返回 1**（没得滚却报"在顶"）
        /// —— 直接拿它判"在底部"会让**短对话永远判成不在底部**，新消息再也不会自动滚出来。
        /// 所以先用高度差判"没得滚"，那才是任何位置都算底部。
        /// 视口 rect 读不到（如预制件里 Viewport 是退化 0×0）时退回 ScrollRect 自身矩形。</summary>
        private bool AtBottom()
        {
            try
            {
                var s = _r.Scroll;
                float vh = s.viewport != null ? s.viewport.rect.height
                                              : ((RectTransform)s.transform).rect.height;
                float range = s.content.rect.height - vh;
                if (range <= 0.5f) return true;              // 没得滚 = 任何位置都在底部
                return s.verticalNormalizedPosition <= 0.02f;
            }
            catch { return true; }   // 读不到就当作在底部：退化成"永远跟随"，不会把跟随锁死
        }

        // ------------------------------------------------------------------
        // 开/关/状态
        // ------------------------------------------------------------------

        /// <summary>打开窗口（重置内容）并设定当前 NPC——Present 随后赶历史。
        /// npcId 非空时自动回填顶部 NpcInput（面板按钮/通讯录/主动传音唤出即带出当前 NPC 中文名；
        /// NpcInput 为只读展示，不再支持手改切换对话对象）</summary>
        public void OpenFor(string npcId)
        {
            CurrentNpcId = npcId ?? "";
            if (_r != null && _r.NpcLabel != null && CurrentNpcId.Length > 0)
                _r.NpcLabel.text = CurrentNpcId;
            Open();
        }

        /// <summary>重置内容并显示</summary>
        public void Open()
        {
            ResetContent();
            _r.Root.SetActive(true);
            IsOpen = true;
            _followingBottom = true;
        }

        /// <summary>填玩家名（BG/Playerput 左上铭牌）。只读展示，不做切换入口；空名兜底「玩家」</summary>
        public void SetPlayerName(string name)
        {
            if (_r == null || _r.PlayerLabel == null) return;
            _r.PlayerLabel.text = string.IsNullOrEmpty(name) ? "玩家" : name;
        }

        public void Close()
        {
            IsOpen = false;
            if (_r != null && _r.Root != null)
            {
                _r.Root.SetActive(false);
                // 事故定案：不要动 UI 层兄弟位次（见 AbContactPanel 注释）。
            }
            ClearStreamingState();
            // 定案（"对话窗打不开"事故根因）：删除旧 CloseAllButMapSafe 收尾——
            // 它=CloseAllUI 强清全部 UI（保留 MapMain），会把刚创建的对话窗实例自己连同游戏
            // 其他 UI 一起强关销毁。关闭登记由 AbChatPanel.CloseViaManager（g.ui.CloseUI）交游戏。
        }

        public void SetBusy(bool busy)
        {
            // 只在真变化时留痕：忙标"该灭没灭"是最难查的一类（卡死就是它），
            // 有这对 ON/OFF 行，下次一眼能看出是"没收到 OFF"还是"UI 没执行 OFF"。
            if (_busy != busy) { try { ModMain.P("[ChatWindow] 忙标 " + (busy ? "ON" : "OFF")); } catch { } }
            _busy = busy;
            if (_r != null && _r.BusyLabel != null)
            {
                if (busy) EnsureBusyText();
                _r.BusyLabel.SetActive(busy);
            }
        }

        /// <summary>忙提示文案兜底（同意后立刻开窗，首 token 前 4~13s 空窗期要有反馈）。
        /// 预制体里该节点已写字（AB 美术可自定义）则**不动**，只补空白的情形。</summary>
        private void EnsureBusyText()
        {
            try
            {
                if (_r == null || _r.BusyLabel == null) return;
                var t = _r.BusyLabel.GetComponentInChildren<Text>(true);
                if (t != null && string.IsNullOrEmpty(t.text)) t.text = "对方正在斟酌…";
            }
            catch { }
        }

        // ------------------------------------------------------------------
        // 静态行（用户气泡 / NPC 整泡 / 分隔条 / 系统提示）
        // ------------------------------------------------------------------

        public RowMeta AppendUserMessage(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            var b = new ChatBubble(_r, _r.Content, true, text);
            return RegisterRow(b.Row.gameObject);
        }

        public RowMeta AppendNpcStatic(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            var b = new ChatBubble(_r, _r.Content, false, text);
            return RegisterRow(b.Row.gameObject);
        }

        public GameObject AppendDivider(string label)
        {
            if (string.IsNullOrEmpty(label)) return null;
            var d = new ChatDivider(_r, _r.Content);
            d.SetText(label);
            if (d.Root != null) RegisterRow(d.Root);
            return d.Root;
        }

        /// <summary>系统提示行（居中灰字）。返回该行的 Text 供调用方原位更新
        /// （手动压缩的「正在压缩中」→ 结果反馈），普通调用忽略返回值即可。</summary>
        public Text AppendSystemNotice(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            var go = TemplateUtils.Instantiate(_r.SystemTemplate, _r.Content);
            if (go == null)
            {
                // （用户反馈"压缩按钮按下去没反应"）旧版这里静默 return null：
                // 预制体缺 BG/Templates/SystemLine 时，**压缩进度/删除提示/回合异常全部隐形**，
                // 看起来就像"按钮没接上"。同 09-13 红点事故的教训：不许静默失败。
                if (!_sysTplWarned)
                {
                    _sysTplWarned = true;
                    ModMain.P("[ChatWindow] ★系统提示行模板缺失（BG/Templates/SystemLine）→ " +
                              "所有系统提示都不会显示（压缩进度/删除提示/回合异常都静默消失）；" +
                              "请在该 AB 预制体补 BG/Templates/SystemLine 节点。");
                }
                return null;
            }
            var t = go.GetComponent<Text>();
            if (t != null) t.text = text;
            RegisterRow(go);
            return t;
        }
        private static bool _sysTplWarned;   // 模板缺失只吼一次（每行提示都吼会刷屏）

        // ------------------------------------------------------------------
        // 压缩按钮忙态（用户反馈"按下去没反应"）
        // ------------------------------------------------------------------
        private Button _compactBtnRef;        // 压缩按钮本体（忙态置灰用）
        private Component _compactLabel;      // 按钮文案组件（Text 或 TMP_Text，取到哪个用哪个）
        private string _compactLabelText;     // 原文案（还原用）

        /// <summary>预制体是否有压缩按钮（无 = 只能靠 /compact 命令）。</summary>
        public bool CompactSupported => _compactBtnRef != null;

        /// <summary>压缩按钮忙态：文案换「压缩中…」+ 置灰。
        /// 为什么需要：摘要 LLM 调用实测可达 **105 秒**（真机 17:23:46→17:25:31），
        /// 其间若界面毫无动效，玩家会判定"按钮没反应"——真机上用户就是这么连点了三次。
        /// 忙态画在按钮自身，不依赖系统提示行模板，提示行缺失时也看得见。</summary>
        public void SetCompactBusy(bool busy)
        {
            try
            {
                if (_compactBtnRef != null) _compactBtnRef.interactable = !busy;
                if (_compactLabel == null) return;
                if (busy)
                {
                    if (_compactLabelText == null) _compactLabelText = GetLabelText(_compactLabel);
                    SetLabelText(_compactLabel, "压缩中…");
                }
                else if (_compactLabelText != null)
                {
                    SetLabelText(_compactLabel, _compactLabelText);
                    _compactLabelText = null;
                }
            }
            catch (Exception e) { ModMain.P("[ChatWindow] SetCompactBusy: " + e.Message); }
        }

        /// <summary>取按钮文案（兼容 TMP 与 uGUI Text；都没有返回 null）。</summary>
        private static string GetLabelText(Component c)
        {
            try
            {
                var tmp = c as TMPro.TextMeshProUGUI;
                if (tmp != null) return tmp.text;
            }
            catch { }
            try
            {
                var t = c as Text;
                if (t != null) return t.text;
            }
            catch { }
            return null;
        }

        private static void SetLabelText(Component c, string s)
        {
            try
            {
                var tmp = c as TMPro.TextMeshProUGUI;
                if (tmp != null) { tmp.text = s; return; }
            }
            catch { }
            try
            {
                var t = c as Text;
                if (t != null) t.text = s;
            }
            catch { }
        }

        // ------------------------------------------------------------------
        // 回合过程折叠组（tool_call / tool_result 时间线）
        // ------------------------------------------------------------------

        public void BeginTurnProcess()
        {
            if (_stepOpen && _curGroup != null) return;
            _curGroup = new StepGroup(_r, _r.Content);
            _stepOpen = true;
            if (_curGroup.Root != null) RegisterRow(_curGroup.Root);
        }

        public void AddToolCall(string name, string argsJson)
        {
            if (_curGroup == null) BeginTurnProcess();
            if (_curGroup != null) _curGroup.AddCall(name ?? "", argsJson ?? "");
        }

        public void AddToolResult(string text, bool isError)
        {
            if (_curGroup == null) BeginTurnProcess();
            if (_curGroup != null) _curGroup.AddResult(text ?? "", isError);
        }

        /// <summary>回合收口：过程组头变“行动记录”，默认收起</summary>
        public void FinishStepProcess()
        {
            if (_curGroup != null) _curGroup.Finish();
            _curGroup = null;
            _stepOpen = false;
        }

        // ------------------------------------------------------------------
        // 输入侧折叠区（sys「系统组装」/ ctx「运行时上下文」：只实时，历史回放不重放）
        // ------------------------------------------------------------------

        /// <summary>按回合+类别取（或新建）输入折叠块并追加一段。
        /// turn 变化即换新块（旧块留在上方，形如 think 每回合一个）。复用 StepGroup（行动记录同款模板）。
        /// </summary>
        public void AppendInputPart(int turn, string kind, string title, string label, string text, bool changed)
        {
            try
            {
                if (_inputTurn != turn)
                {
                    _inputTurn = turn;
                    _curSys = null;
                    _curCtx = null;
                }
                StepGroup block = kind == "sys" ? _curSys : _curCtx;
                if (block == null)
                {
                    string labelTitle = string.IsNullOrEmpty(title)
                        ? (kind == "sys" ? "系统组装" : "运行时上下文") : title;
                    block = new StepGroup(_r, _r.Content, labelTitle, autoOpen: false);
                    if (kind == "sys") _curSys = block; else _curCtx = block;
                    if (block.Root != null) RegisterRow(block.Root);
                }
                block.AddPart(label ?? "?", text ?? "", changed);
            }
            catch (Exception e)
            {
                try { UnityEngine.Debug.Log("[ChatWindow] input block: " + e.Message); } catch { }
            }
        }

        // ------------------------------------------------------------------
        // 内心思量折叠区（think 通道：reasoning_content / 内嵌 <think> 拆出）
        // ------------------------------------------------------------------

        /// <summary>开始（或复用）当前回合的内心思量区</summary>
        public void BeginThink()
        {
            if (_curThink != null) return;
            _curThink = new ThinkGroup(_r.Content);
            if (_curThink.Root != null) RegisterRow(_curThink.Root);
        }

        /// <summary>思考流增量（Header 实时涨字数）。
        /// 新思考段开始（_curThink == null）即时间线分段点：先收口当前行动记录组——
        /// think 段在真实顺序里总紧跟上一工具段之后，不收口会让下一 step 的工具行
        /// 继续塞进旧组，视觉上「工具全挤一组、think 散在组外」（时间线乱序根因）。
        /// 收口后 AddToolCall 会另起新组，Content children 顺序 = 真实时间线（与历史回放一致）。</summary>
        public void AppendThinkDelta(string token)
        {
            if (_curThink == null)
            {
                FinishStepProcess();
                BeginThink();
            }
            _curThink?.AppendDelta(token);
        }

        /// <summary>思考整段写入（非流式 step / 历史回放）。
        /// 同 AppendThinkDelta：整段 think 也是时间线分段点，先收口行动记录组再建行
        /// （流式路径已在段首收口过，此处幂等 no-op；历史回放时无直播组同样 no-op）。</summary>
        public RowMeta AppendThinkStatic(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            FinishStepProcess();
            BeginThink();
            _curThink?.SetFull(text);
            _curThink?.Finish();
            _curThink = null;
            return _lastMeta;
        }

        /// <summary>思考流收尾</summary>
        public void FinishThink()
        {
            _curThink?.Finish();
            _curThink = null;
        }

        // ------------------------------------------------------------------
        // 流式气泡（delta 合并 → 终稿覆盖）
        // ------------------------------------------------------------------

        /// <summary>开始一条 NPC 气泡（可带初始文本；initiative=true 表示 NPC 主动传音，前置分隔条）</summary>
        public void BeginNpcBubble(string text, bool initiative = false)
        {
            if (initiative && _deltaBubble == null)
            {
                AppendDivider("—— 主动传音 ——");
                _dividerTurn = _curTurn;      // 记账：本回合的分隔条已就位
            }
            var b = new ChatBubble(_r, _r.Content, false, text ?? "");
            _deltaBubble = b.Root;
            _bubbleText = b.Text;
            _delta.Length = 0;
            _delta.Append(text ?? "");
            _deltaDirty = true;
            RegisterRow(_deltaBubble);
        }

        /// <summary>流式增量：只入缓冲，LateUpdate 合并刷一次</summary>
        public void AppendDelta(string token)
        {
            if (_deltaBubble == null || _bubbleText == null)
            {
                // 收口之后还能走到这里 = 无主增量凭空建泡（09-13 排查「回合完毕后
                //   删除模式进不去」时加的判据）：正常时序是「流式泡 → 收口帧清账」，
                //   若一条 delta 落在收口帧**之后**，它会新建一个没人收口的泡 →
                //   `HasActiveBubble` 永久为真 → 删除/压缩入口被守卫永久挡死。
                try { ModMain.P("[ChatWindow] 收口后仍有流式增量 → 凭空建泡 turn=" + _curTurn + " 长度=" + (token ?? "").Length); }
                catch { }
                BeginNpcBubble("");
            }
            _delta.Append(token ?? "");
            _deltaDirty = true;
        }

        /// <summary>
        /// 回合收口：**强制清掉「流式进行中」的记账**（不动已渲染的气泡行）。
        ///
        /// 为什么单独开一个口子：`HasActiveBubble` 是 `ChatPresenter` 里删除/压缩入口的守卫项之一
        /// （`_busy || HasActiveBubble`），而它只在 `FinishNpcBubble` 内部被清 —— 一旦那条渲染路径
        /// 中途抛异常（异常又被没人 await 的 Task 静默吞掉，见 `MainThreadDispatcher` 注释），
        /// 标记就永久泄漏，表现为「对话明明完了，删除模式却一直说回合进行中」。
        /// 收口帧一到，"这一回合没在飞了"是**固有语义**，绝不能依赖渲染成功。
        /// </summary>
        public void EndTurnStreaming()
        {
            ClearStreamingState();
        }

        /// <summary>回合定稿：用最终全文覆盖（流式期间缓冲已刷，终稿最准）；未开过流式→直接成整泡。
        /// **同一回合的同一段正文只成泡一次**（修「结果输出两遍」，见字段注释）。
        /// 整个渲染体走 `try/finally`：`ClearStreamingState()` 是**收口语义**，不能依赖渲染成功
        /// （09-13：渲染半途抛异常 → 标记泄漏 → 删除模式永久提示"回合进行中"；异常还被
        ///  MainThreadDispatcher 静默吞掉，日志里查不到）。</summary>
        public void FinishNpcBubble(string fullText, bool initiative = false)
        {
            try
            {
                if (!string.IsNullOrEmpty(fullText) && _curTurn > 0 &&
                    _curTurn == _lastRenderTurn && fullText == _lastRenderText)
                {
                    // 本回合同段正文已经渲染过（通常是 step(text) 先到、npc_reply 后到）：
                    // 不再成泡，只补"主动传音"分隔条（首次那条多半没带）+ 收尾流式状态。
                    // 判据带回合号：不同回合哪怕正文逐字相同（模型复读）也照常成泡。
                    if (initiative) EnsureInitiativeDivider(_lastRenderRow);
                    return;
                }
                if (_deltaBubble == null || _bubbleText == null)
                {
                    BeginNpcBubble(fullText, initiative);
                }
                else
                {
                    if (!string.IsNullOrEmpty(fullText))
                    {
                        _bubbleText.text = fullText;
                        _delta.Length = 0;
                        _deltaDirty = false;
                    }
                }
                _lastRenderTurn = _curTurn;
                _lastRenderText = fullText;
                _lastRenderRow = _deltaBubble;
                // 覆盖活跃流式气泡这条路不会经过 BeginNpcBubble → 分隔条要在这里补
                if (initiative) EnsureInitiativeDivider(_deltaBubble);
            }
            finally
            {
                ClearStreamingState();
            }
        }
        /// <summary>记下当前回合号（ChatPresenter 在每个 step 帧上调用）。`npc_reply` 不带 turn，
        /// 靠它把"回合收口的终稿"与"该回合 step 已渲染过的正文"认成同一份。</summary>
        public void NoteTurn(int turn)
        {
            if (turn > 0) _curTurn = turn;
        }

        /// <summary>保证本回合恰有一条「—— 主动传音 ——」分隔条，插在 above 这条气泡之前。
        /// 三条路都要覆盖：① 新泡（BeginNpcBubble 自己会插）② 覆盖活跃流式泡（不会插）
        /// ③ 去重跳过（首条由 step(text) 渲染，不带分隔条）。</summary>
        private void EnsureInitiativeDivider(GameObject above)
        {
            if (above == null || _dividerTurn == _curTurn) return;
            try
            {
                var div = AppendDivider("—— 主动传音 ——");
                if (div != null) div.transform.SetSiblingIndex(above.transform.GetSiblingIndex());
                _dividerTurn = _curTurn;
            }
            catch { }
        }

        /// <summary>撤销当前流式气泡（失败兜底：不留半截气泡）</summary>
        public void CancelActiveBubble()
        {
            if (_deltaBubble != null)
            {
                TemplateUtils.DestroySafe(_deltaBubble);
                _rowCount = Mathf.Max(0, _rowCount - 1);
            }
            ClearStreamingState();
        }

        private void ClearStreamingState()
        {
            _deltaBubble = null;
            _bubbleText = null;
            _delta.Length = 0;
            _deltaDirty = false;
        }

        // ------------------------------------------------------------------
        // 历史回放（Present 调；Python 只回已闭合 turn，重放与直播天然不交叠）
        // ------------------------------------------------------------------

        public void ReplaceHistory(JArray items)
        {
            ResetContent();
            if (items == null) return;

            StepGroup his = null;
            RowMeta hisMeta = null;   // 当前行动记录组的行元数据（组内所有 tool_call/tool_result 的 seqs 归入它）
            foreach (var token in items)
            {
                if (token == null || token.Type == JTokenType.Null) continue;
                var o = (JObject)token;
                string kind = (string)o["kind"] ?? "";
                switch (kind)
                {
                    case "user":
                        FinishHistoryGroup(his); his = null; hisMeta = null;
                        BindTurn(AppendUserMessage((string)o["text"] ?? ""), o);
                        break;
                    case "assistant":
                        FinishHistoryGroup(his); his = null; hisMeta = null;
                        BindTurn(AppendNpcStatic((string)o["text"] ?? ""), o);
                        break;
                    case "think":
                        FinishHistoryGroup(his); his = null; hisMeta = null;
                        BindTurn(AppendThinkStatic((string)o["text"] ?? ""), o);
                        break;
                    case "tool_call":
                        if (his == null) { his = new StepGroup(_r, _r.Content); hisMeta = RegisterRow(his.Root); }
                        his.AddCall((string)o["name"] ?? "", o["args"]?.ToString() ?? "");
                        BindTurn(hisMeta, o);
                        break;
                    case "tool_result":
                        if (his == null) { his = new StepGroup(_r, _r.Content); hisMeta = RegisterRow(his.Root); }
                        his.AddResult((string)o["text"] ?? "", o["isError"]?.Value<bool>() ?? false);
                        BindTurn(hisMeta, o);
                        break;
                    case "initiative":
                        {
                            FinishHistoryGroup(his); his = null; hisMeta = null;
                            // 短标签优先（Python initiative.intent_label 给中文四到六字）；
                            // 旧账本/未知意图回落英文键——不让分隔条空白
                            string lbl = (string)o["intent_text"];
                            if (string.IsNullOrEmpty(lbl)) lbl = (string)o["intent"] ?? "";
                            AppendDivider("—— 主动传音 · " + lbl + " ——");
                            BindTurn(_lastMeta, o);   // 分隔条也归属该回合（随回合删除）
                            // 回放已经给这个回合插过分隔条 → 记账，免得随后到达的直播终稿再插一条
                            try { _dividerTurn = o["turn"]?.Value<int>() ?? _dividerTurn; } catch { }
                        }
                        break;
                    case "turn_error":
                        FinishHistoryGroup(his); his = null; hisMeta = null;
                        AppendSystemNotice("回合异常：" + (string)o["text"] ?? "");
                        BindTurn(_lastMeta, o);
                        break;
                }
            }
            FinishHistoryGroup(his);
            // 超出 MAX_ROWS 被裁掉的行 → 顶部显式说明（绝不静默少给：今天的两次误判都源于此）。
            // 提示行 Turn=0 → 不可选，不参与删除；SetAsFirstSibling 把它顶到列表最前。
            if (_trimmedRows > 0)
            {
                var notice = AppendSystemNotice("…更早的 " + _trimmedRows + " 条历史未渲染（窗口上限 " +
                                                MAX_ROWS + " 行，删除模式同样看不到）");
                if (notice != null) notice.transform.SetAsFirstSibling();
            }
            _followingBottom = true;
        }

        /// <summary>把回放条目的账本回合号绑到行元数据上（删除模式的选中依据；turn 缺失/为 0 的行不可选）</summary>
        private static void BindTurn(RowMeta m, JObject o)
        {
            if (m == null) return;
            try { m.Turn = o?["turn"]?.Value<int>() ?? 0; } catch { }
        }

        private static void FinishHistoryGroup(StepGroup g)
        {
            if (g != null) g.Finish();
        }

        // ------------------------------------------------------------------
        // 删除模式（微信式多选）：进入/退出/切换选中/全选/两段确认
        // 选中单位=账本回合（与 Python plan_delete 对齐：只发 turn 编号，seq 跨度由
        // Python 从账本换算）。行可选性由回合绑定决定（回放行有、直播残留行无）；
        // 遮罩优先取行模板的 SelectOverlay 子节点（美术可自定义），没有则代码现造
        // 黑 α0/α0.35 兜底。
        // ------------------------------------------------------------------

        /// <summary>进入选择模式（Presenter 静默重放绑定回合后调用）：禁输入、藏发送、亮操作条、行挂点击遮罩</summary>
        public void EnterSelecting()
        {
            if (Selecting || _r == null) return;
            Selecting = true;
            _confirmArmed = false;
            foreach (var m in _rows)
            {
                if (m == null || m.Turn <= 0 || m.Row == null) continue;
                EnsureOverlay(m);
                if (m.Overlay != null) m.Overlay.SetActive(true);
                SetOverlayDark(m, false);
            }
            if (_r.ChatInput != null) _r.ChatInput.interactable = false;
            if (_r.SendButton != null) _r.SendButton.SetActive(false);
            if (_r.BusyLabel != null) _r.BusyLabel.SetActive(false);
            if (_r.DeleteBar != null) _r.DeleteBar.SetActive(true);
            UpdateCount();
        }

        /// <summary>退出选择模式：拆遮罩、恢复输入区与发送按钮、藏操作条、清选择集</summary>
        public void ExitSelecting()
        {
            if (!Selecting) return;
            Selecting = false;
            _confirmArmed = false;
            foreach (var m in _rows)
            {
                if (m?.Overlay != null)
                {
                    m.Overlay.SetActive(false);   // 隐藏而非销毁——模板自带遮罩下次进入复用（保留自定义样式）
                    SetOverlayDark(m, false);
                }
                m.Selected = false;
                m.Overlay = null;
                m.OverlayImg = null;
            }
            if (_r != null)
            {
                if (_r.ChatInput != null) _r.ChatInput.interactable = true;
                if (_r.SendButton != null) _r.SendButton.SetActive(true);
                if (_r.DeleteBar != null) _r.DeleteBar.SetActive(false);
            }
        }

        /// <summary>全选/取消全选（文字随状态切换）。单位=回合</summary>
        private void SelectAllToggle()
        {
            int selectable = 0, selected = 0;
            foreach (var m in _rows)
            {
                if (m == null || m.Turn <= 0) continue;
                selectable++;
                if (m.Selected) selected++;
            }
            bool target = selectable > 0 && selected < selectable;   // 未全选→全选；已全选→取消全选
            foreach (var m in _rows)
            {
                if (m == null || m.Turn <= 0) continue;
                m.Selected = target;
                SetOverlayDark(m, target);
            }
            UpdateCount();
        }

        /// <summary>删除确认（两段）：无选中不响应；第一击武装为「确认删除」并请求 preview摘要，
        /// 3 秒内再击才真正触发（触发前 Python 还会以 plan_delete fail-closed 兜底）</summary>
        private void OnDeleteConfirmClicked()
        {
            var turns = SelectedTurns();
            if (turns.Count == 0) return;
            if (!_confirmArmed)
            {
                _confirmArmed = true;
                _confirmArmTime = Time.time;
                SetConfirmText("确认删除");
                DeletePreviewRequested?.Invoke(turns);   // Presenter 发 preview RPC，回执进 ApplyDeletePreview
                return;
            }
            DisarmConfirm();
            DeleteConfirmed?.Invoke(turns);   // Presenter 发 RPC；保持选择模式等回执，成功才退出
        }

        /// <summary>当前选中的回合号（去重升序；Presenter 原样发 delete_history 的 turns 载荷）</summary>
        public List<int> SelectedTurns()
        {
            var result = new List<int>();
            foreach (var m in _rows)
            {
                if (m == null || !m.Selected || m.Turn <= 0) continue;
                if (!result.Contains(m.Turn)) result.Add(m.Turn);
            }
            result.Sort();
            return result;
        }

        /// <summary>preview_delete_history 回执落地：ok=true → 计数位换预览摘要（将删条数/字数/含纪要提示）；
        /// ok=false（fail-closed 预检失败）→ 解除武装（第二击不再执行），原因提示由 Presenter 发系统行</summary>
        public void ApplyDeletePreview(bool ok, string summary)
        {
            if (!Selecting) return;
            if (!ok)
            {
                DisarmConfirm();
                return;
            }
            if (_r?.CountLabel != null && !string.IsNullOrEmpty(summary)) _r.CountLabel.text = summary;
        }

        private void DisarmConfirm()
        {
            if (!_confirmArmed) return;
            _confirmArmed = false;
            SetConfirmText("删除");
            UpdateCount();   // 摘要退场，计数复位（武装解除=旧预览失效）
        }

        private void SetConfirmText(string t)
        {
            if (_r?.DeleteConfirmBtn == null) return;
            var txt = _r.DeleteConfirmBtn.GetComponentInChildren<Text>();
            if (txt != null) txt.text = t;
        }

        private void UpdateCount()
        {
            // 计数单位 = **回合**（去重），不是行：
            // 选中遮罩挂在行上，而一个回合有 4 行（分隔条 / 行动记录组 / 内心思量 / 气泡），
            // 旧写法逐行 +1 → 只显示了一个回合的窗口报「已选 4 回合」（用户实机误解："只显示了一句
            // 却说要删 4 轮"）。实际发出的载荷 `SelectedTurns()` 本来就是去重的，语义没错，
            // 是这里的数字和按钮文案对不上账本。
            var allTurns = new HashSet<int>();
            var selTurns = new HashSet<int>();
            foreach (var m in _rows)
            {
                if (m == null || m.Turn <= 0) continue;
                allTurns.Add(m.Turn);
                if (m.Selected) selTurns.Add(m.Turn);
            }
            if (_r?.CountLabel != null) _r.CountLabel.text = "已选 " + selTurns.Count + " 回合";
            if (_r?.SelectAllBtn != null)
            {
                var txt = _r.SelectAllBtn.GetComponentInChildren<Text>();
                if (txt != null) txt.text = (allTurns.Count > 0 && selTurns.Count == allTurns.Count) ? "取消全选" : "全选";
            }
        }

        /// <summary>切换一行的选中态。单位=回合：点同回合任意一行，整组行（气泡/工具组/分隔条）一起亮/灭，
        /// 与 Python「删除以完整回合为单位」的语义在视觉上对齐——亮的范围=将删的范围</summary>
        private void ToggleRow(RowMeta m)
        {
            if (m == null || m.Turn <= 0) return;
            bool target = !m.Selected;
            foreach (var r in _rows)
            {
                if (r == null || r.Turn != m.Turn) continue;
                r.Selected = target;
                SetOverlayDark(r, target);
            }
            UpdateCount();
            DisarmConfirm();   // 选择集变了，武装与旧预览作废重计
        }

        private void SetOverlayDark(RowMeta m, bool dark)
        {
            if (m?.OverlayImg != null)
                m.OverlayImg.color = dark ? new Color(0f, 0f, 0f, 0.35f) : new Color(0f, 0f, 0f, 0f);
        }

        /// <summary>行选中遮罩兼点击板：模板有 SelectOverlay 用之，否则现造。
        /// 全 stretch 的 Image（α0 待选/α0.35 已选）+ Button 收点击；置最末兄弟盖过行内内容；
        /// ignoreLayout——行根是 HorizontalLayoutGroup，遮罩不得参与排布。</summary>
        private void EnsureOverlay(RowMeta m)
        {
            if (m.Overlay != null) return;
            var t = m.Row.transform.Find("SelectOverlay");
            GameObject go = t != null ? t.gameObject : null;
            if (go == null)
            {
                go = new GameObject("SelectOverlay");
                go.layer = m.Row.layer;
                var rt = go.AddComponent<RectTransform>();
                rt.SetParent(m.Row.transform, false);
                rt.anchorMin = Vector2.zero;
                rt.anchorMax = Vector2.one;
                rt.offsetMin = Vector2.zero;
                rt.offsetMax = Vector2.zero;
                var img = go.AddComponent<Image>();
                img.raycastTarget = true;
                img.color = new Color(0f, 0f, 0f, 0f);
            }
            var le = go.GetComponent<LayoutElement>() ?? go.AddComponent<LayoutElement>();
            le.ignoreLayout = true;
            var btn = go.GetComponent<Button>() ?? go.AddComponent<Button>();
            var captured = m;
            ClickUtils.Attach(btn, () => ToggleRow(captured));
            m.OverlayImg = go.GetComponent<Image>() ?? go.AddComponent<Image>();
            m.OverlayImg.raycastTarget = true;   // 模板自带遮罩默认关 raycast（平时不挡点击），选择模式必须打开
            m.Overlay = go;
            go.transform.SetAsLastSibling();
            go.SetActive(true);
        }

        // ------------------------------------------------------------------
        // 内部：行登记 / 上限裁剪 / 重置
        // ------------------------------------------------------------------

        private RowMeta RegisterRow(GameObject row)
        {
            _lastMeta = new RowMeta { Row = row };
            _rows.Add(_lastMeta);
            _rowCount++;
            // ② 行数上限：删除最旧行（最旧必不是流式中的末条气泡），行注册表同步修剪
            while (_rowCount > MAX_ROWS && _rows.Count > 0)
            {
                var oldest = _rows[0];
                if (oldest?.Row != null) TemplateUtils.DestroySafe(oldest.Row);
                _rows.RemoveAt(0);
                _rowCount--;
                _trimmedRows++;   // 记账：ReplaceHistory 结束时会把它显式写进列表顶部
            }
            return _lastMeta;
        }

        private void ResetContent()
        {
            if (Selecting) ExitSelecting();   // 先拆遮罩/恢复输入区，再清行（遮罩随行销毁也无妨）
            // 内容整块重建 = 主体换了（开窗/换 NPC/重放）⇒ 忙标必须灭。
            //   补的是 `ChatLauncher.SetThinking` 注释里**声称存在、实际没有**的兜底：
            //   它写着"开窗重放也会清（ChatWindow.ReplaceHistory）"，但 ReplaceHistory 从未碰过忙标。
            //   漏网的时序：玩家发出消息 ⇒ busy=true ⇒ 关窗/换 NPC ⇒ 回包到达时
            //   `ChatPresenter.OnUiEvent` 因 `!IsOpen` / npcId 不符而早退（收口帧被丢弃）
            //   ⇒ 忙标再也没人复位，重开窗依旧挂着"NPC 回应中…"。
            //   放在 ResetContent 而不是 ReplaceHistory：Open() 也走这里，一次覆盖两条路径。
            SetBusy(false);
            _rows.Clear();
            _lastMeta = null;
            _trimmedRows = 0;
            // 内容整块重建（开窗回放/换 NPC/删除后重放）：成泡记账与分隔条记账一并清零，
            // 否则"重放后再来的直播终稿"会被误判成重复而不成泡（_curTurn 是直播回合号，保留）
            _lastRenderTurn = -1;
            _lastRenderText = null;
            _lastRenderRow = null;
            _dividerTurn = -1;
            if (_r != null && _r.Content != null)
            {
                // IL2CPP 下枚举 Transform 子节点必须用索引循环，foreach 会抛 InvalidCastException
                int count = _r.Content.childCount;
                for (int i = 0; i < count; i++)
                {
                    Transform c = _r.Content.GetChild(i);
                    if (c != null) TemplateUtils.DestroySafe(c.gameObject);
                }
            }
            _rowCount = 0;
            ClearStreamingState();
            _curGroup = null;
            _stepOpen = false;
            _curThink = null;
            _curSys = null;
            _curCtx = null;
            _inputTurn = -1;
            _followingBottom = true;
            _lastChildCount = -1;    // 内容整块重建 → 下一帧必判"长了" → 钉底一次（不重置会因
            _lastContentH = -1f;     // 子节点数恰好相同而漏掉钉底，开窗后停在顶部）
        }
    }

    /// <summary>
    /// 行注册表条目：行根 + 该行归属的账本回合号（删除模式的选中依据）。
    /// Python 侧删除以**完整回合**为单位（plan_delete 只信任 turn 编号，不信任裸 seq，
    /// seq 跨度由它自己从账本换算），因此 C# 只需把回放条目的 turn 绑到行上。
    /// 回放行由 ReplaceHistory 绑定（同回合的 user/think/assistant/tool_call/tool_result/
    /// initiative 分隔条/turn_error 共享同一 turn）；直播新产行 Turn=0=不可选，
    /// 进删除模式时的静默重放会给它们补上身份。遮罩节点（SelectOverlay）在进入
    /// 选择模式时启用，退出时隐藏（模板自带遮罩保留自定义样式）。
    /// </summary>
    public sealed class RowMeta
    {
        public GameObject Row;
        public int Turn;                // 账本回合号（0=未绑定，不可选）
        public bool Selected;
        public GameObject Overlay;      // 选中遮罩兼点击板（模板 SelectOverlay 或代码现造）
        public Image OverlayImg;
    }
}