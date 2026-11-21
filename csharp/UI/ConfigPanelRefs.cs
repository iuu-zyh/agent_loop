/// <summary>
/// 配置 UI 构建产物引用包 —— 纯数据类（非 MonoBehaviour，仿 ChatWindowRefs）。
///
/// 职责只有一项：把 ConfigUiBuilder 搭好的整棵 UGUI 节点树握在手里，
/// 让 ConfigPresenter 通过字段访问，而不是到处 Find/GetComponent。
/// 没有任何方法，也不持有逻辑。节点命名与 UIChatAi 同契约（BG/ 前缀，供未来 AB 版复用）。
/// </summary>
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    public class ConfigPanelRefs
    {
        // ---- 整棵树 ----
        /// <summary>Canvas 根（Canvas + CanvasScaler + GraphicRaycaster，sortingOrder=3100）</summary>
        public GameObject Root;

        // ---- 主面板 ----
        /// <summary>窗口面板（居中 760×640；AB 版可挂古风背景图于本节点）</summary>
        public RectTransform Window;
        /// <summary>关闭按钮（✕）</summary>
        public GameObject CloseButton;
        /// <summary>状态条（保存结果 / 生效提示，底部一行）</summary>
        public Text StatusLabel;

        // ---- Tab 页切换 ----
        /// <summary>「大模型」Tab 按钮</summary>
        public GameObject TabLlmButton;
        /// <summary>「提示词」Tab 按钮</summary>
        public GameObject TabPromptButton;
        /// <summary>大模型设置页（表单）</summary>
        public GameObject PageLlm;
        /// <summary>提示词编辑页</summary>
        public GameObject PagePrompt;

        // ---- 大模型表单（对应 config.json 白名单块）----
        public InputField BaseUrlInput;
        /// <summary>API Key（contentType=Password 掩码显示）</summary>
        public InputField ApiKeyInput;
        public InputField ModelInput;
        /// <summary>
        /// 自定义请求头（config llm.headers；**可空，默认不附加**）。
        /// 单行文本，格式 `k1: v1; k2: v2`（也接受整体 JSON）。仅当网关要求自报家门时才需要，
        /// 典型：OpenCode Go 要求 x-opencode-session，否则一律 400 MissingSessionID。
        /// </summary>
        public InputField HeadersInput;
        /// <summary>
        /// 思考强度（config llm.reasoning_effort，OpenAI 生态的**请求体**字段 reasoning_effort）。
        /// 只接受 low / high / max，空 = 不发送（Python 侧删键）。
        /// ★ 默认隐藏 ★：只有「测试连接」的能力探测（差分三发，Python llm/capability.py）
        /// 确认当前网关认识该键才显示 —— 未确认就注入，轻则静默失效（键名拼错被当未知键忽略）、
        /// 重则整个回合 400。可空 —— 老 AB / 从未探测时这一行不存在或不显示。
        /// </summary>
        public InputField ReasoningInput;
        /// <summary>
        /// 「测试连接」按钮（test_llm RPC）：拿**表单当前值**真调一次模型，把失败原因显示在状态条。
        /// 可空 —— 老预制件没有这个节点时按钮不出现，其余功能照常（见 AbConfigPanel.MissingCritical）。
        /// </summary>
        public GameObject TestLlmButton;
        /// <summary>多模态开关（config llm.image.enabled）</summary>
        public Toggle ImageToggle;
        /// <summary>WS 端口（config network.port，改动需重启）</summary>
        public InputField PortInput;
        /// <summary>回合内 RPC 超时秒（config network.request_timeout）</summary>
        public InputField TimeoutInput;
        /// <summary>
        /// LLM **首字看门狗**秒（config llm.timeout，默认 120）。
        /// 量的是"请求发出 → 第一个内容块"这段等待，**不是**整个请求时长 —— 持续吐字的长回复
        /// 不会被它砍，只有一直没开口才会被抓到。吐字中途的看门狗是 llm.idle_timeout（默认 60，
        /// 面板暂无该行，手编 config.json）。0/空 = 不设这个看门狗。详见 docs/features/brain-liveness.md §4。
        /// </summary>
        public InputField LlmTimeoutInput;
        /// <summary>
        /// LLM **非流式**单次上限秒（config llm.timeout_nonstream，默认 180）。
        /// 非流式在整段生成完前一个字节都不发，所以这里量的是完整生成时长（含首字延迟）。
        /// 唯一调用方是回合边界的上下文压缩 —— 拿流式那个短值去卡它会**静默废掉压缩**。
        /// </summary>
        public InputField LlmTimeoutNsInput;
        /// <summary>
        /// 一次 LLM 调用的绝对硬顶秒（config llm.total_budget，**默认 0 = 不设限**），含全部重试。
        /// 只约束流式路径，且只在显式设 >0 时生效（把两个看门狗收缩进剩余额度）。
        /// 默认关的原因：整趟耗时不是"卡住"的判据（09-17 事故，详见 docs/features/brain-liveness.md §4）。
        /// </summary>
        public InputField LlmBudgetInput;
        /// <summary>LLM 失败重试次数（config llm.retries，默认 1；总尝试 = 本值+1，0 = 不重试）</summary>
        public InputField LlmRetriesInput;
        /// <summary>NPC 主动开口节流秒（config initiative.min_interval，Python侧熔断，重启生效）</summary>
        public InputField MinIntervalInput;
        /// <summary>每游戏日触发概率 0-100（config initiative.daily_chance，重启生效）</summary>
        public InputField DailyChanceInput;
        /// <summary>单NPC游戏日冷却（config initiative.npc_cooldown_days）</summary>
        public InputField NpcCooldownDaysInput;
        /// <summary>单NPC现实秒冷却（config initiative.npc_cooldown_real_s）</summary>
        public InputField NpcCooldownRealInput;
        /// <summary>低好感阈值（config initiative.low_intim_threshold，&lt;阈值减半）</summary>
        public InputField LowThreshInput;
        /// <summary>低好感减半开关（config initiative.low_intim_halve）</summary>
        public Toggle LowHalveToggle;
        /// <summary>压缩用上下文窗口 tokens（config compaction.ctx_window）</summary>
        public InputField CtxWindowInput;
        /// <summary>保存大模型设置按钮（set_config）</summary>
        public GameObject SaveLlmButton;

        // ---- 09-13 分组改版新增（标题条 + 常显 + 整页滚动）----
        /// <summary>大模型页滚动区（行数增多后面板放不下，整页可滚）</summary>
        public ScrollRect LlmScroll;
        /// <summary>主动互动总开关（config initiative.enabled）</summary>
        public Toggle InitiativeEnabledToggle;
        /// <summary>自动压缩开关（config compaction.enabled；关=只禁用自动，手动压缩照常）</summary>
        public Toggle CompactionEnabledToggle;
        /// <summary>保留原文比例（config compaction.retain_ratio）</summary>
        public InputField RetainRatioInput;
        /// <summary>自动压缩触发阈值（config compaction.threshold_ratio）</summary>
        public InputField ThresholdRatioInput;
        /// <summary>显示立绘（config ui.portraits_enabled；关=不再调游戏立绘 API，槽位隐藏）</summary>
        public Toggle PortraitToggle;
        /// <summary>
        /// 群聊每轮发言人数上限（config room.max_speakers_per_round，默认 3）。
        /// 一轮里**最多几个人真的开口**（内部叫 K）：被点名的人可以弃权或出错，都不占名额，
        /// 候选会继续补位；候选名单本身以成员数为上限，所以填大 = 每轮全员都可能开口。
        /// 改完**即时生效**（Python 每轮现读 RoomFlow.k）。可空 —— 老 AB 没有这一行时
        /// 只是看不到这一项，config.json 仍可手改，面板本身照常打开。
        /// </summary>
        public InputField RoomSpeakersInput;

        // ---- 提示词页 ----
        /// <summary>左侧文件列表滚动区</summary>
        public ScrollRect FileScroll;
        /// <summary>文件列表内容容器（纵向排布；文件项按钮运行时克隆）</summary>
        public RectTransform FileListContent;
        /// <summary>文件项按钮模板（构建后 SetActive(false)）</summary>
        public GameObject FileItemTemplate;
        /// <summary>右侧提示词多行编辑框</summary>
        public InputField PromptInput;
        /// <summary>PromptInput 外挂滚动（AB 预制件 BG/PagePrompt/PromptInput 上挂 ScrollRect；
        /// 内容超框滚轮可读全文。选文件填充文本后置顶用。代码版构建无此组件，null 容错）</summary>
        public ScrollRect PromptScroll;
        /// <summary>当前选中文件（rel path，如 "personas/林婉清.txt"）；空 = 未选中</summary>
        public Text CurrentFileLabel;
        /// <summary>保存当前提示词文件按钮（write_prompt）</summary>
        public GameObject SavePromptButton;
        /// <summary>新建 NPC 人设按钮（文案随上下文变化："为「X」新建人设"；NPC 名取自打开面板时的 npcId）</summary>
        public GameObject NewNpcButton;
        /// <summary>删除选中提示词文件按钮（delete_prompt；仅新增文件可删，系统件 Python 侧拒绝。
        /// 两段确认：首击变「确认删除？」，3 秒内再击执行。预制体未加该节点时永远不触发）</summary>
        public GameObject DeletePromptButton;
        /// <summary>[已弃用] 手输 NPC 名输入框——NPC 名改由上下文提供，节点保留但隐藏（新旧预制件均 inactive）</summary>
        public InputField NewNpcInput;
        /// <summary>
        /// 重置选中提示词文件为内置默认（reset_prompt）。内容只能来自随包内置副本
        /// （`paths.bundled_prompts_root()`）；系统件 sections/default/_suffix 也能还原，
        /// 用户新增件由 Python fail-closed 拒绝并引导去用「删除文件」。
        /// 两段确认与删除同款（判定在 TwoStepConfirm）：首击变「确认重置？」，3 秒内再击执行。
        /// 可空 —— 老预制件没有这个节点时按钮不出现，其余功能照常
        /// （节点路径契约 BG/PagePrompt/ResetPromptBtn，与 scripts/dev/prefab_patch_prompt_reset.py 同步）。
        /// </summary>
        public GameObject ResetPromptButton;

        // ---- 悬停气泡模板（可选：预制件经「补气泡模板」编辑器菜单添加；缺失时 ConfigPresenter 运行时现造）----
        /// <summary>气泡浮层（深色圆角半透明底；raycast 已关不挡点击；节点契约 BG/Tooltip）</summary>
        public GameObject TooltipGo;
        /// <summary>气泡正文 Text（子节点契约 TooltipText）</summary>
        public Text TooltipText;
    }
}
