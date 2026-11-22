/// <summary>
/// 须知面板的**内容本体**（纯数据，零 Unity、零 Newtonsoft）—— 文案只写在这一个文件里。
///
/// ── 为什么要单独一个文件 ────────────────────────────────────────────────
/// 面板（`NoticePanel`）只负责画，不认识任何一个字。这样：
///   · 改文案不需要碰布局代码，改错字不会把面板改崩；
///   · `tests/test_notice_panel_contract.py` 能钉住"面板里不许硬编码公告"（问文案只能问这里）；
///   · `csharp/tests/NoticeCoreTests` 能在 WSL 里直接对内容自检（序号有序、日期格式、非空）。
///
/// ── 序号（Seq）是唯一判据，不是版本字符串 ───────────────────────────────
/// "玩家看过没有"用**单调递增的整数水位**判，不用比较 `1.0.10` vs `1.0.9`：
///   · 版本字符串比较要写 semver 解析，`1.0.10` 会被判成比 `1.0.9` 旧；
///   · 内容改了忘改版本号时，字符串判据会**静默不再弹**（最坏的一种失败：公告消失且无报错）。
/// 规矩：**每次往 `Releases` 头部加一条，Seq 必须比现有的都大**。
///
/// ── 写作口径（改文案前先读）────────────────────────────────────────────
///   · 公告照工坊更新日志的写法：**日期 + 作者 + 编号条目**，一条一句话，不写解释；
///     玩家扫一眼就知道这版改了什么（"修复已知的问题"就是一句完整的话，不需要展开）。
///   · 须知是**操作指引**，写"点哪里""看到什么"就够，不解释原理、不写"建议您"。
///   · 全篇不用「AI」字样（`tests/test_no_ai_wording_contract.py` 全量扫）；
///     不用星号强调、不堆排比、不写"让…更加…"这类空话。
/// </summary>
namespace AgentLoopBridge
{
    /// <summary>使用须知的一节：一行小标题 + 若干行正文。`KeyCap` 非空时在标题左侧画一枚键帽。</summary>
    internal sealed class NoticeSection
    {
        internal string Title;
        /// <summary>非空 = 本节讲某个按键，标题左侧画键帽（如 "C"）。只有一节用得上，别滥用。</summary>
        internal string KeyCap;
        internal string[] Lines;
    }

    /// <summary>
    /// 一个版本的更新公告。`Seq` 是水位判据（见文件头），`Version` 与 `About.Version` 对齐
    /// （**不显示在公告里**，只作"加了公告忘提版本"的自检锚点）。`Date` 直接是工坊那种写法。
    /// </summary>
    internal sealed class NoticeRelease
    {
        internal int Seq;
        internal string Version;
        /// <summary>如 "10 月 23 日 下午 1:35"（工坊更新日志的格式，原样显示）。</summary>
        internal string Date;
        internal string Author;
        /// <summary>一条一句话，按数组顺序编号显示（1. 2. 3. …）。</summary>
        internal string[] Items;
    }

    internal static class NoticeContentCore
    {
        internal const string GuideTitle = "使用须知";
        internal const string UpdatesTitle = "更新公告";

        /// <summary>
        /// 使用须知（用户 10-23 定：**只保留前三条** —— 其余入口在工坊页，面板里不重复）。
        /// </summary>
        internal static readonly NoticeSection[] Guide =
        {
            new NoticeSection
            {
                Title = "连不上，先看杀毒隔离区",
                Lines = new[]
                {
                    "被杀毒软件隔离不是删除，文件还在隔离区里，游戏那边就找不到 AgentLoopServer.exe 了。",
                    "打开 Windows 安全中心，进「病毒和威胁防护」，点「保护历史记录」，找标着「已隔离威胁」的那一条。",
                    "展开，选「操作」→「还原」。然后再点一次「操作」→「在设备上允许」。只还原不加允许，下次扫描还会被杀。",
                    "顺手把 ModAssets 文件夹加进「排除项」，以后更新不用再来一遍。",
                    "保护历史记录只留两周，更早的查不到，那时重装一次就行。",
                },
            },
            new NoticeSection
            {
                Title = "历史消息往上滚才会出来",
                Lines = new[]
                {
                    "打开对话只先加载最近一段，上面还有没加载的，滚到最顶会自己接着往下取。",
                    "取完那一段会停在原处，刚看的内容不会跑掉，继续往上滚就行。",
                    "对话窗按住标题栏能拖动，位置下次打开还在。",
                },
            },
            new NoticeSection
            {
                Title = "通讯录快捷键",
                KeyCap = "C",
                Lines = new[]
                {
                    "按 C 打开通讯录，Esc 关掉最上面那层。",
                    "这个窗口开着的时候，游戏自己的快捷键不会响应，放心打字。",
                },
            },
            new NoticeSection
            {
                Title = "填完钥匙要点测试连接",
                Lines = new[]
                {
                    "对话窗右上角「设置」，进「大模型」页，填接口地址、API Key、模型名。",
                    "填完点一次「测试连接」，看到「连接正常（xxxx ms）」才算通了。只保存不测试，写错了不会报错，NPC 只会复读你的话。",
                    "用支持思考模式的模型效果更好。测试通过后会多出「思考强度」一行，自动填 low（你选过就不动）。",
                },
            },
            new NoticeSection
            {
                Title = "测试交流群",
                Lines = new[]
                {
                    "遇到问题、想反馈、想看更新进度，都可以进群：790461321。",
                    "报问题时说一下版本号和卡在哪一步，比只说「用不了」好查得多。",
                },
            },
        };

        /// <summary>
        /// 更新公告，**最新在最前**。加新版本时：在头部插一条、`Seq` 取现有最大值 +1。
        /// 历史条目照抄工坊更新日志（9-15 起），日期写法保持一致。
        /// </summary>
        internal static readonly NoticeRelease[] Releases =
        {
            new NoticeRelease
            {
                Seq = 8,
                Version = "1.2.3",
                Date = "11 月 22 日 下午 12:50",
                Author = "iuuuuuu",
                Items = new[]
                {
                    "设置、提示词和自建人设搬到用户目录，更新不再覆盖",
                    "首次启动自动迁移，已填的接口地址、Key 和自建人设都会保留",
                    "新版本增加的内置人设会自动补进来，用户改过的不动",
                    "窗口位置、公告已读状态、剧情账本也一起搬过去",
                },
            },
            new NoticeRelease
            {
                Seq = 7,
                Version = "1.2.2",
                Date = "11 月 21 日 下午 4:10",
                Author = "iuuuuuu",
                Items = new[]
                {
                    "群聊里可以 @ 某个成员，被点名的人一定会接话",
                    "打 @ 时会弹出成员候选，回车选中，方向键切换",
                    "对话窗可以按住标题栏拖动，位置下次打开还在",
                    "拖动不再卡，消息再多也跟手",
                    "开窗先给最近一段历史，往上滚到顶会自动加载更早的，看完的那段停在原位",
                    "群聊也按同样的方式分页加载",
                    "气泡宽度跟着文字长短走，不再一律挤成固定宽度",
                    "读完档继续对话时，工具调用恢复正常，不再反复报错",
                    "对话里提到的 @ 名字不会在回显里重复出现",
                    "等回复时会显示当前阶段：读取状态、斟酌、回话，各自分开",
                    "断线时会明确提示，不再让人分不清是模型慢还是没连上",
                    "读档后第一句话不再整轮作废（此前会报错并丢掉这一轮）",
                },
            },
            new NoticeRelease
            {
                Seq = 6,
                Version = "1.2.1",
                Date = "11 月 16 日 下午 1:54",
                Author = "iuuuuuu",
                Items = new[]
                {
                    "对话上下文新增「同格」一段：NPC 能看到与自己同一格的角色",
                    "新增捎话：让一个 NPC 把一句话带给另一个 NPC，对方下次开口时读到",
                    "关系与好感的增减按事件结算并写进存档",
                    "已亡故的角色仍可查看生前资料",
                    "入宗、退宗会记进 NPC 的经历",
                    "存档改为增量写入，NPC 数量多时耗时下降",
                    "修复工具使用规则未随系统提示下发的问题",
                },
            },
            new NoticeRelease
            {
                Seq = 5,
                Version = "1.2.0",
                Date = "11 月 10 日 下午 11:50",
                Author = "iuuuuuu",
                Items = new[]
                {
                    "对话框文字加了行距，长回复不再挤成一团",
                    "NPC 说话能分颜色了，不同的词会上不同色",
                    "气泡变高时圆角和尾巴不再被拉变形",
                    "查人物储物可以只看某一类，也能按名字找某件东西",
                    "NPC 会自己决定入宗或退宗，还能挑门派和师父",
                    "压缩后的对话纪要可以点开看，也能手动改",
                    "纪要改完会影响后续对话，锁着的时候会说明原因",
                    "修复模型把颜色标签写坏时露出半截标签的问题",
                    "修复正文里的尖括号被当成格式吃掉的问题",
                    "字号改写后颜色不再糊在气泡底色上看不清",
                },
            },
            new NoticeRelease
            {
                Seq = 4,
                Version = "1.1.0",
                Date = "10 月 28 日 上午 10:30",
                Author = "iuuuuuu",
                Items = new[]
                {
                    "NPC 之间也可以开打，玩家在场旁观",
                    "也可以叫上玩家一起上，开打前会先问一句",
                    "组队进决斗时队友会真的进场出手",
                    "通讯录新增「临时」页，说过话但没加好友的人都在这儿",
                    "亡故的人不再从通讯录消失，还能点开翻旧账",
                    "长战斗的剧情会写进经历，不再中途丢掉",
                    "人生经历只发新增的几条，同一场对话更省开销",
                    "修复经历文本混进颜色标签、被当成正文的问题",
                    "修复开战流程里又开一场仗、对话框点了没反应的问题",
                    "修复对战结果把对手名字说成玩家的问题",
                },
            },
            new NoticeRelease
            {
                Seq = 3,
                Version = "1.0.1",
                Date = "10 月 23 日 下午 1:35",
                Author = "iuuuuuu",
                Items = new[]
                {
                    "UI 细节优化：气泡不再带日期，改成按游戏历显示的时间分隔行",
                    "通讯录「最近」页显示时间，对话窗可以拖动，上翻历史时来了新回复底部会浮出提示",
                    "修复传送后调用工具失败的问题",
                },
            },
            new NoticeRelease
            {
                Seq = 2,
                Version = "1.0.0",
                Date = "10 月 21 日 下午 11:23",
                Author = "iuuuuuu",
                Items = new[]
                {
                    "对话中的头像可以点击放大观赏，可发送图片",
                    "支持组队功能",
                    "自主交互会主动调用工具",
                    "对话 UI 新增工具按钮，点开可以看到有哪些工具",
                },
            },
            new NoticeRelease
            {
                Seq = 1,
                Version = "0.9.0",
                Date = "10 月 16 日 下午 1:19",
                Author = "iuuuuuu",
                Items = new[]
                {
                    "修复好感度系统，现在工具调用会有好感度",
                    "完善 NPC 死亡后的相关逻辑",
                    "修复已知的问题",
                },
            },
        };

        /// <summary>当前最大水位（= 最新一条公告的 Seq）。自动弹出与"新"标记都以它为基准。</summary>
        internal static int MaxSeq
        {
            get
            {
                int m = 0;
                for (int i = 0; i < Releases.Length; i++)
                    if (Releases[i].Seq > m) m = Releases[i].Seq;
                return m;
            }
        }

        /// <summary>最新版本号（= `Releases[0].Version`，与 `About.Version` 必须一致）。</summary>
        internal static string LatestVersion
        {
            get { return Releases.Length > 0 ? (Releases[0].Version ?? "") : ""; }
        }

        /// <summary>最新一条公告的作者（页脚署名用）。</summary>
        internal static string Author
        {
            get { return Releases.Length > 0 ? (Releases[0].Author ?? "") : ""; }
        }
    }
}
