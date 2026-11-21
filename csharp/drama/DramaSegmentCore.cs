/// <summary>
/// DramaSegmentCore —— 「剧情相遇分段器」的**纯逻辑核**（零 Unity 依赖，故单元测试工程直接编同一份源码）。
///
/// 它解决什么：现在系统只记得"屏幕上此刻那一句"（新页覆盖旧页）。改成把**一次当面相遇的多页原文**
/// 按顺序攒成一段（每条带"谁说的"），供原生剧情窗那枚「对话」按钮整段注入；并且**点了只投增量、
/// 不清空**，所以可以反复点、也可以在链走完之后再点。
///
/// ## 分段判据（全部有 09-22 真机探针实证，别改回去）
/// 段的边界**不是**"人变没变"，也不是内容像不像，而是：
///   **一次"没有被转场接走"的关窗** = 上一场到此为止。
/// 关窗那一刻（W 真→假）起 ±`EpsSeconds` 的待定窗，窗内**任一转场探针"假→真"** ⇒ 这次关窗是
/// 转场（挂起，不闭）；否则 ⇒ 干净关窗（收口）。实测数字（切磋全链）：
///   · 点「应战」→ 关窗与 B1（逻辑战斗位）起跳是**同一毫秒**（间隔 0ms）⇒ ε 取 1s 是给慢机器留余量；
///   · 战后首屏在关窗后 15.6s 到达，**到达时 B1 仍为真** ⇒ 回站判据不能要求"转场已结束"；
///   · 尾页关窗瞬间 **B1 仍是真**（随即翻假）⇒ 判定**只认"假→真"方向**，认电平会把干净关窗误判成挂起；
///   · 同一扇窗翻页（21401→21402、22604→22601）**W 连续、零毛刺** ⇒ 不设"新页撤销收口"，
///     收口判下之后来的页一律开新段（用户要的"结束之后再开口 = 两件事"就靠这条）；
///   · "继续"翻页**不产生 ClickOption**；有一次关窗**前面没有配对点击** ⇒ 收口判据不依赖点击；
///   · 选项 id 不是永远可读（实测 id=0）⇒ 判据不压在配表 id 上。
///   · ★页间"关窗 → 紧接同人新窗"是**同一场相遇**（09-23 论道实测，别改回去）★：
///     一个原生多窗交互（论道/邀约这类）**每页都是独立窗、页间没有转场起跳**，硬判会切成好几段 ——
///     论道一次实测被切成 3 段（22811 / 22813 / 22831+22848+22847+22815），玩家翻到最后一页点
///     「对话」只投得到最近那段，前两句（含"论道请求"）**永远进不了模型上下文**（静默丢）。
///     **判据 = 动作 UI 电平**（A 位）：那几段页间隙里 UI 层层顶都是 `ActionCourseInfo`
///     （游戏自己的"某个行动还在进行"，交互一结束就没了 —— 实测结束后面板/NPCInfo/MapMain）
///     ⇒ 关窗那一刻它亮着就记一条交接证据，走既有的"挂起 → 同人新页 ⇒ 回程"路径。
///     ★**不许再用"同人 + 时间近 + 未投"当兜底**（09-23 实证的教训）★：那次闲聊（22604/22601，
///     干净关窗、关窗时 A 没亮）之后 **0.5s**，论道开场页（22811）到了 —— 兜底把它嫁接到闲聊段上，
///     最后 8 条（闲聊 2 + 论道 6）一起投；模型拿着大杂烩、又见末句被标成自己说的，把**刚完成**的
///     论道读成了"我推辞了论道"，回复里凭空道歉。⇒ "时间近"分不出"同一场的下一页"与"另一件事"；
///     **A 灭的干净关窗就是真的分层**。采不到 A 的场景归入"已知残留 ③"，等样本再定，不拿时间猜。
///   · ★"场景类交互"（邀约这类"进一个场景里互动"）的页间隙**不设时间窗**★（09-23 邀约实测）：
///     进场景后 B1（逻辑战斗位）与 B2（战斗界面）**从进到出一直亮着**，页间每次"点选项 → 关窗 →
///     新页"都只有电平、没有任何起跳；关窗到新页的间隔由玩家的阅读/演出决定（实测 0.53s ~ 6.64s，
///     还可能更长）—— 固定窗接不住长间隙，放宽窗又会把"散场之后"的事误并（见上面那条教训）。
///     判据 = **关窗那刻 B1+B2 亮 ⇒ 这次关窗只是页间隙 ⇒ 直接挂起（`SuspendByScene`）**；
///     回程（同人新页到达）**额外要求"到达时 B1+B2 仍亮"**：人还在场景里，间隙多长都接得住；
///     人已散场（场景态解除），之后来的同人页就是**另一件事**，宁可另开一段
///     （`RoutePage` 的 `SceneSuspend` 分支）。收口走既有回程窗：转场（含场景态）全落假后满 20s 无新页。
///   · ★`A` 的"假→真"边沿也是证据（`A↑`）★（09-23 邀约实测）：关窗那刻 A=0、**0.5s 后 A 才起跳**
///     （层顶变 `ActionCourseInfo`）—— 旧实现只看"关窗那一瞬间的 A 电平"，把"游戏还有下一页在跑"
///     的信号整个漏掉。页开着时 A 被剧情窗盖住（采不到），所以这个边沿只在**关窗之后**采得到。
///
/// ## 四态（收口 ≠ 丢弃）
///   记录中 --关窗 + ε内无起跳--> 可投      （干净关窗：链到头，内容留着等你点）
///   记录中 --关窗 + ε内有起跳--> 挂起      （转场接走了：等它办完、等同人回来）
///   记录中 --关窗那刻 B1+B2 亮--> 挂起     （场景还在：只是页间隙，等场景内新页；见"场景类交互"）
///   挂起   --同人新页（未超预算；场景挂起还要求"到达时仍在场景"）--> 记录中 （回程，接着记）
///   挂起   --转场全落假后满 20s 无页 / 换世界 --> 可投
///   挂起   --段活过硬上限（`SegmentHardCeilingSeconds`，**不可续期**）--> 可投
///   可投   --TTL 10 分钟 / 段数超上限（丢最老）/ 换世界 --> 丢弃
/// 查询"有没有活动段"只认**记录中/挂起**两种；可投段只是内容暂存，不再接新页。
/// 新段**不吞**旧可投段（同人也一样）：各自算 TTL，上限 4 段；点对话时只投"当前那一段"。
///
/// ## 段是**唯一的内容流**（09-23 用户定；TalkOutcome/BattleOutcome 不再自己收内容）
/// 页与选项走同一条追加序（`SeqGlobal`）—— **追加序 = 时序**。消费者只有三个：
///   · 「对话」按钮 → `TakeDelta`（跳过行级"不投/已消费"，投递游标只前进不清空）；
///   · 工具结果 → `ReadRange`（**只读区间**、跨段归并、`consume=true` 把读走的行标成"已消费"）；
///   · 都不在 → 只记不投（"有按钮 / 有槽位才被消费"）。
/// 行级"不投"的两个来源：**静态**（工具链页：内容已随结果送达）与**消费**（被 `ReadRange` 读走）。
/// ★选项行永不静态"不投"（09-23 用户定）★：UI 正文可以因为"结果已给过模型"而不投，但
/// **玩家选了什么必须投** —— 旧实现让选项跟随上一行的标记，攻击处置页（不投）上点的
/// 「击杀此人 / 放你一马」会跟着不可投、直接丢。
///
/// ## 段寿命由**链**驱动（心跳，09-23 用户定）
/// 挂起期原有一条固定预算（180s）—— 它会把几分钟的长战斗切成两段（战前那句永远投不出去、
/// 工具结果还得跨段拼）。现在 **"链还在飞"就滑动预算**，两类信号取或：
///   · 探针（B1/B2/A）：覆盖**原生**长战斗/长交互（没有工具、没有心跳，只有探针）；
///   · 心跳（`RenewChain(uid)`；工具侧每几秒跳一次）：覆盖"探针全暗但戏没演完"的空档
///     （道具选择面板、加载、玩家慢读）。
/// 链一结束（心跳停 / 探针落）⇒ 现行规则立刻接管（转场落假满 20s 无页 ⇒ 收口）。
/// 防僵尸：`SegmentHardCeilingSeconds` 不可续期，到点无条件收口。
///
/// ## 「照记但不投」是**行级**标记（09-22 定案，别退回段级）
/// 谁会带它：闸 1（`SuppressAiOption` 期内的页）、闸 1.5（`DramaNativeClaim` 认领的页）、
/// **处置页**（原文已当 tool result 给过模型，但那一页要**放行注入按钮** —— 见 `DramaAiOption`）。
/// 为什么不能是段级：段可以**混合** —— 处置页（不投）与它之后的原生尾页（该投）落在同一段里
/// （处置页是开段那一页）。段级一票否决会让尾页那句永远投不出去，而日志只显示"本段无新增"，
/// 看起来跟正常一样（静默丢内容）。行级过滤后只投该投的那几行，纯工具链段则自然返回空。
///
/// ## 已知残留（刻意不修，只记日志——用真机样本再定）
///   ① 挂起期间**同一个人**插进另一件事 ⇒ 并进原段（代价：多一句相邻内容，不会张冠李戴——
///      每行自带说话人）；
///   ② 同一扇窗"关掉又重建而 W 无假帧"（本机实测未出现）⇒ 两件事会同段；
///   ③ 动作 UI 被别的窗盖住**且** B1/B2 也不亮时（关窗那刻三条证据都采不到）判不出"页间隙"
///      ⇒ 多窗交互会被切成多段、玩家只投到最近那段。现已补两条证据（`A↑` 边沿 / 场景态挂起），
///      仍未覆盖的样本等真机再定；**不许拿时间当兜底补它**（试过，会把两件相邻的事并成一段 —— 见上面教训）。
///
/// ## 说话人从哪来
/// 不由本类判定：由接线层（`DramaSegmentRecorder`）从 `DramaAiOption` 现成的"说话人判定"
/// 取结果传进来（**不许在本类重复实现判据**——那是"唯一实现"规矩）。取不到就传空串，
/// 本类照记、字段留空：判不出就闭嘴，绝不猜（猜错是喂反事实）。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    /// <summary>段的四个状态。`Discarded` 只是给日志/测试一个终态，运行时不再引用。</summary>
    internal enum SegmentState
    {
        Recording = 0,
        Suspended = 1,
        Deliverable = 2,
        Discarded = 3,
    }

    /// <summary>段里的一条：**原话一字不改** + 谁说的 + 页号 + 顺序号。</summary>
    internal sealed class SegmentLine
    {
        /// <summary>段内自增序号（1 起）。投递游标比的就是它。</summary>
        public int Seq;
        /// <summary>**全局**自增序号（1 起，跨段单调）。区间读数（`ReadRange`）跨段归并时按它排序 ——
        /// 段内 `Seq` 在段与段之间没有可比性，而"追加序 = 时序"要靠这一条才成立。</summary>
        public int SeqGlobal;
        public int DramaId;
        /// <summary>"page" = 页面正文；"option" = **玩家点的选项**（09-23 新增）。
        /// 为什么要有：页面正文（`GetDialogueText`）**不含选项** —— 论道/邀约这类问答式交互里，
        /// 只投正文会让模型"只知道疑问、不知道玩家选了什么"（用户报的割裂），
        /// 甚至把已完成的交互当成"还没开始"（再去调一次论道工具）。</summary>
        public string Kind = "page";
        /// <summary>"npc" / "player" / "choice"（玩家选了某项）/ ""（判不出）。</summary>
        public string Speaker = "";
        /// <summary>显示名（日志用；缺失为空串）。</summary>
        public string Name = "";
        /// <summary>屏幕上那句成品话，一字不改。</summary>
        public string Text = "";
        public float At;
        /// <summary>
        /// 「照记但不投」：**行级标记**（09-22 从段级下沉，别改回去）。
        ///
        /// 谁会带它：`DramaGate.SuppressAiOption` 期内的页（闸 1）、`DramaNativeClaim` 认领成功的页（闸 1.5），
        /// 以及**处置页**（它的原文已经当 tool result 给过模型，但那页必须放行注入按钮 —— 见 `DramaAiOption`）。
        /// 内容照记（对账/诊断要用），但 `TakeDelta` **永不返回它**：那份内容已经给过模型，再投就是双份。
        ///
        /// ★为什么必须是行级★：段可以是**混合**的 —— 我方工具链的页（处置页）与它之后的原生页（尾页）
        /// 落在同一段里。段级标记会一票否决整段 ⇒ 尾页那句永远投不出去，而日志只显示"本段无新增"，
        /// 看起来跟正常一样（静默丢内容，最难查的那种）。
        /// 标记由**页**带着走（`PageRecord.NoInject`），谁接收了这一页谁被标记 —— 因为页可能被
        /// 扣住（关窗待定期间），那时它最终落到哪一段还没定（见 `Append`）。
        /// </summary>
        public bool NoInject;
    }

    /// <summary>一段相遇。</summary>
    internal sealed class DramaSegment
    {
        /// <summary>对方的**精确编号**（unitID），不是名字——重名时名字判不出冲突。</summary>
        public string Counterpart = "";
        public SegmentState State = SegmentState.Recording;
        public readonly List<SegmentLine> Lines = new List<SegmentLine>();
        /// <summary>已投到第几（游标）。投递**不清空**内容，只推进它。</summary>
        public int Delivered;
        /// <summary>下一个序号（1 起）。</summary>
        public int NextSeq = 1;
        public float StartedAt;
        public float SuspendedAt = -1f;
        /// <summary>本次挂起是**因为"人还在场景里"**（关窗那刻 B1+B2 电平，见 `OnWindowClosed`）。
        /// 只影响**回程条件**：这类挂起要求"新页到达时 B1+B2 仍亮"才回程 —— 人已退出场景，
        /// 之后来的同人页就是**另一件事**，宁可另开一段（09-23 邀约实证：场景外的同人页
        /// 若并回邀约段，模型会把两场当成一场）。</summary>
        public bool SceneSuspend;
        public float SettledAt = -1f;
        public string SettleReason = "";
        /// <summary>本段内玩家点过几次选项（**只记，不参与任何判定**）。</summary>
        public int Clicks;
        // 【09-22 删】这里原来有一个段级的 `NoInject` —— 它会把"混合段"（工具链的处置页 + 之后的原生尾页）
        // 整段一票否决。同一个意思现在落在行上（见 `SegmentLine.NoInject`），取增量按行过滤。
    }

    internal sealed class DramaSegmentCore
    {
        // ---------------- 常量（依据见文件头） ----------------
        /// <summary>关窗与"转场起跳"的相邻判定窗（秒，双向）。实测 0ms，这里留余量。</summary>
        internal const float EpsSeconds = 1.0f;
        /// <summary>转场**全落假**之后，还愿意等"那个人回来"多久（秒）。</summary>
        internal const float ReturnWindowSeconds = 20f;
        /// <summary>挂起总预算（秒）：**"链还在飞"期间滑动**（见 `DriveSuspended`）—— 链一停，
        /// 回程窗（20s）先到点，所以它实际只在"探针与心跳同时坏掉"时兜底。防段变僵尸的
        /// 主兜底已由下面的硬上限承担。</summary>
        internal const float SuspendBudgetSeconds = 180f;
        /// <summary>心跳有效期（秒）：`RenewChain` 之后超过这么久没有下一跳 ⇒ 认为链已经停了
        /// （工具侧每 5s 一跳，这里给足余量：一次掉帧/一次卡顿不该把长战斗判死）。</summary>
        internal const float ChainBeatTtlSeconds = 60f;
        /// <summary>段活过的**硬上限**（秒，从 `StartedAt` 算，只在挂起期判定）：心跳与探针
        /// 可以把挂起预算一直往后推，这条**不可续期** —— 防"链卡死/探针卡死"把段变成僵尸。</summary>
        internal const float SegmentHardCeilingSeconds = 1800f;
        /// <summary>可投态保质期（秒）：玩家"打完才想起来点一下"的窗口。</summary>
        internal const float DeliverTtlSeconds = 600f;
        /// <summary>可投段同时最多留几个（超出丢最老的）。</summary>
        internal const int MaxDeliverables = 4;
        /// <summary>跃迁环保留时长（秒）——只服务"关窗 ±ε 内有没有起跳"。</summary>
        private const float EdgeMemorySeconds = 6f;

        private struct EdgeRecord { public string What; public float At; }
        private struct PageRecord
        {
            public string UnitId; public int DramaId; public string Speaker;
            public string Name; public string Text; public bool NoInject; public float At;
        }

        private readonly int _privateIdMin;
        private readonly int _privateIdMax;
        private readonly Action<string> _log;

        private DramaSegment _active;                                   // 记录中 / 挂起
        private readonly List<DramaSegment> _deliverables = new List<DramaSegment>();
        /// <summary>全局追加序（跨段单调；`SegmentLine.SeqGlobal` 从这里取）。</summary>
        private int _seqGlobal;
        /// <summary>心跳：哪个对方的活动链还在等结果（`RenewChain`），以及最后一跳的时刻。
        /// 链一停（超过 `ChainBeatTtlSeconds` 没有下一跳）就自然失效，不需要显式"结束"信号。</summary>
        private string _chainUnit = "";
        private float _chainBeatAt = -1f;

        private bool _probesInit;
        private bool _w, _b1, _b2;
        /// <summary>游戏动作 UI（`ActionCourseInfo`）此刻可见。页开着时它被剧情窗盖住（读到 false），
        /// 所以它有两条用法：**关窗那一刻的电平**（`OnWindowClosed`）与**Tick 里的"假→真"边沿**
        /// （`A↑`，关窗后 0.5s 才起来的那些照样能采到 —— 09-23 邀约实测）。</summary>
        private bool _a;
        private bool _transitionSeen;                                   // 本段挂起期间见过转场为真
        private float _transitionClearedAt = -1f;                       // 转场全落假的时刻

        private float _closeAt = -1f;                                   // 本次关窗时刻（待定中）
        private DramaSegment _closing;                                  // 待定收口的段
        private readonly List<PageRecord> _pendingPages = new List<PageRecord>();
        private readonly List<EdgeRecord> _edges = new List<EdgeRecord>();

        /// <param name="privateIdMin">我方私有段下界（含）——落进来的页一律不记。</param>
        /// <param name="privateIdMax">我方私有段上界（含）。</param>
        /// <param name="log">诊断日志出口（null = 静默）。</param>
        internal DramaSegmentCore(int privateIdMin, int privateIdMax, Action<string> log)
        {
            _privateIdMin = privateIdMin;
            _privateIdMax = privateIdMax;
            _log = log;
        }

        internal DramaSegment Active { get { return _active; } }
        internal List<DramaSegment> Deliverables { get { return _deliverables; } }

        internal bool IsPrivateId(int dramaId)
        {
            return dramaId >= _privateIdMin && dramaId <= _privateIdMax;
        }

        // ==================================================================== 帧采样

        /// <summary>旧签名（不带动作 UI）：等价于 `a=false`，服务既有测试与调用方。</summary>
        internal void Tick(bool w, bool b1, bool b2, float now)
        {
            Tick(w, b1, b2, false, now);
        }

        /// <summary>
        /// 每帧调一次（主线程）。第一帧只做基线，不做任何判定。
        /// 四个探针：W = 剧情窗可见；B1 = 逻辑战斗位；B2 = 战斗界面可见；
        /// **A = 游戏动作进行中**（层顶 `ActionCourseInfo`）—— 论道那种"页间关窗"的原生多窗交互，
        /// 交接证据只能从 A 上取（页开着时它被剧情窗盖住，采不到起跳边沿 ⇒ 在关窗那一帧按电平记）。
        /// （加载层 / 预告窗**不进判据**：实测加载层全程未亮，预告窗是攻击链专属。）
        /// </summary>
        internal void Tick(bool w, bool b1, bool b2, bool a, float now)
        {
            if (!_probesInit)
            {
                _probesInit = true;
                _w = w; _b1 = b1; _b2 = b2; _a = a;
                return;
            }

            bool wFell = _w && !w;
            bool b1Rose = !_b1 && b1;
            bool b2Rose = !_b2 && b2;
            // ★A 也有"假→真"边沿，且它**只在页关着时才采得到**（页开着时层顶被剧情窗盖住）。
            //   实机（09-23 邀约 10:46:10）：关窗那刻 A=0，**0.5s 后 A 才起跳** —— 旧实现只看
            //   "关窗那一瞬间的 A 电平"，把这个"游戏还有下一页在跑"的信号整个漏掉。
            bool aRose = !_a && a;

            _w = w; _b1 = b1; _b2 = b2; _a = a;

            if (b1Rose) PushEdge("B1", now);
            if (b2Rose) PushEdge("B2", now);
            if (aRose) PushEdge("A↑", now);
            // ★ W 的"假→真"**不是**交接信号：那是"另一扇窗开了"，按设计应当开新段（由页事件负责）。
            if (wFell) OnWindowClosed(now);

            ResolvePendingClose(now);
            DriveSuspended(b1, b2, a, now);
            ExpireDeliverables(now);
            PruneEdges(now);
        }

        // ==================================================================== 页事件

        /// <summary>
        /// 页事件（`UIDramaBase.InitData`）。**开段与续页的唯一入口**；
        /// 实测它与"W 0→1"同毫秒到达，所以开段不看 W 边沿。
        /// （本重载 = `noInject:false`；接线层那三条路径各有自己的标记，见下。）
        /// </summary>
        internal void OnPage(string unitId, int dramaId, string speaker, string name, string text, float now)
        {
            OnPage(unitId, dramaId, speaker, name, text, false, now);
        }

        /// <summary>
        /// 页事件（带"照记但不投"标记）。
        ///
        /// ★`noInject` 必须**跟着页走**，不能在接线层"记完再对活动段打标记"：页可能被
        /// **扣住**（关窗待定期间来的），ε 到点时它可能开成**新段**——那时再打标记就打在
        /// 上一段身上了（新段反而变成可投 ⇒ 工具链的原文被重复投给模型）。所以标记存在
        /// `PageRecord` 上，由真正接收它的那一段继承（见 `Append`）。
        /// </summary>
        internal void OnPage(string unitId, int dramaId, string speaker, string name, string text,
                             bool noInject, float now)
        {
            PageRecord rec = new PageRecord();
            rec.UnitId = unitId ?? "";
            rec.DramaId = dramaId;
            rec.Speaker = speaker ?? "";
            rec.Name = name ?? "";
            rec.Text = text ?? "";
            rec.NoInject = noInject;
            rec.At = now;

            if (rec.UnitId.Length == 0) return;      // 判不出对方 ⇒ 不记（判不出就闭嘴）
            if (IsPrivateId(dramaId)) return;        // 我方自制的窗 ⇒ 不记

            if (_closing != null)
            {
                // 待定收口期间来的页：先扣着，等 ε 到点按那次判定分派归属。
                // （干净关窗 ⇒ 这些页开新段；判成交接 ⇒ 归回原段。）
                _pendingPages.Add(rec);
                Log("待定收口期间来了新页（先扣住，等 ε 到点定归属）", now);
                return;
            }

            RoutePage(rec, now);
        }

        /// <summary>玩家点了某个选项。**只计数、只登记**——判交接只看"关窗 + 探针起跳"。</summary>
        internal void NoteOptionClick(float now)
        {
            if (_active != null) _active.Clicks++;
        }

        /// <summary>
        /// 玩家点了某个选项（09-23 新增）：把它当成段里的**一行**记下来，插在"当前页"之后。
        ///
        /// 为什么必须记：页面正文（`GetDialogueText`）**不含选项**。论道/邀约这类问答式交互里，
        /// 一句"你会如何做？"后面跟的**玩家选择**不在正文里 —— 只投正文，模型就"只知道疑问、
        /// 不知道回答"，整场交互都是割裂的；更糟的是它会把"已在进行的交互"当成"还没开始"
        /// （实测：论道页上点「对话」，模型转头又发起一次论道）。
        ///
        /// 归属与边界（判不出就闭嘴，不许猜）：
        ///   · 挂在 `unitId` 名下**当前那一段**；没有这个人的活动段 ⇒ **丢弃**（选项不属于任何相遇时
        ///     不凭空造段）；
        ///   · 文本由接线层**从选项按钮上读**（屏幕上的就是权威，与"成品句"同一条哲学）；
        ///   · ★`noInject` **不跟随**上一行（09-23 用户定：选项和 UI 文本是两回事）★：
        ///     UI 正文可以因为"已当 tool result 给过模型"而不投，但**玩家选了什么必须投** ——
        ///     旧实现跟随上一行，攻击处置页（不投）上点的「击杀此人 / 放你一马」会跟着不可投、直接丢。
        ///     唯一的压制途径是**被工具结果读走**（`ReadRange(consume:true)`）—— 那才是真给过模型。
        /// </summary>
        internal void NoteOption(string unitId, int dramaId, string text, string name, float now)
        {
            // 探针（09-23）：丢弃也要出声 —— 这一行的归属全压在"点击那一刻的 `_pending*`"上
            // （见 `DramaAiOption.OnOptionTapped`），静默丢弃会让"玩家选择"缺席得毫无痕迹，
            // 而它恰恰是用户报的那个割裂（"只知道疑问、不知道玩家选了什么"）。
            if (string.IsNullOrEmpty(unitId) || string.IsNullOrEmpty(text))
            {
                Log("选项丢弃（uid/文本为空）「" + text + "」", now);
                return;
            }
            DramaSegment seg = _active;
            if (seg == null || seg.Counterpart != unitId)
            {
                Log("选项丢弃（没有 " + unitId + " 的活动段；当前="
                    + (seg == null ? "无" : seg.Counterpart + "/" + seg.State) + "）「" + text + "」", now);
                return;
            }
            if (seg.State != SegmentState.Recording && seg.State != SegmentState.Suspended)
            {
                Log("选项丢弃（段状态=" + seg.State + "）「" + text + "」", now);
                return;
            }

            SegmentLine line = new SegmentLine();
            line.Seq = seg.NextSeq++;
            line.SeqGlobal = ++_seqGlobal;
            line.DramaId = dramaId;
            line.Kind = "option";
            line.Speaker = "choice";
            line.Name = name ?? "";
            line.Text = text;
            line.At = now;
            // ★选项永不静态"不投"★（09-23 用户定）：被工具结果读走（`ReadRange(consume:true)`）
            // 才是真的给过模型 —— 那一条由读者的消费标记负责，这里不给它上静态标记。
            line.NoInject = false;
            seg.Lines.Add(line);
            Log("记选项「" + text + "」（页=" + dramaId + "，对方=" + seg.Counterpart + "）", now);
        }

        /// <summary>
        /// **点击那一刻**把"这一页是谁在说"重判的结果写回去（接线层在 `Trigger` 里调）。
        ///
        /// 为什么要这一步：页事件发生在 `InitData`，而立绘压暗是 UI 侧逐页刷的 ——
        /// **刚回来的那一页读到的可能是上一页的明暗**（本类文件头那条实测注释就写着这件事）。
        /// 点击那一刻是本页彻底画好之后，所以那一刻的判定才是权威的（接线层的原话：
        /// "点击那一刻还会再读一次，那一次才作数"）。整段注入时，最后一条就是**当前这一页**，
        /// 用页时刻的判定有可能把"你说的"记成"玩家说的" —— 那正是喂反事实。
        ///
        /// 边界（都刻意如此）：
        ///   · 只改**最后一条**同页号的（同一页在段里只会出现一次）；
        ///   · 判不出（空串）**不覆盖** —— 宁可留着页时刻那个已知值，也不把已知变成未知；
        ///   · 名字跟着一起改（由接线层按新归属算好传进来）—— 否则会出现
        ///     "玩家（方成和）说"，即同一个反事实换了个字段。
        /// </summary>
        internal void OverrideSpeaker(string unitId, int dramaId, string speaker, string name, float now)
        {
            if (string.IsNullOrEmpty(speaker)) return;
            DramaSegment seg = Pick(unitId);
            if (seg == null) return;
            for (int i = seg.Lines.Count - 1; i >= 0; i--)
            {
                if (seg.Lines[i].DramaId != dramaId) continue;
                if (seg.Lines[i].Speaker == speaker && (seg.Lines[i].Name ?? "") == (name ?? "")) return;
                Log("点击时重判「" + seg.Lines[i].Text + "」的说话人：" + ShowSide(seg.Lines[i].Speaker)
                    + " → " + ShowSide(speaker) + "（页=" + dramaId + "）", now);
                seg.Lines[i].Speaker = speaker;
                seg.Lines[i].Name = name ?? "";
                return;
            }
        }

        /// <summary>
        /// 取"该投的增量"并推进游标：`seq > Delivered` **且不是「照记不投」**的那几条（按顺序）。
        /// 取的是**时间上最近的那一段**（活动段优先，否则最近收口的可投段）——不是"最近的
        /// 可投段"：玩家点的是**当前这一页**，把上一次相遇的原文投进来就是拿旧事当新闻。
        ///
        /// ★按**行**过滤（09-22 从段级下沉，别改回去）★
        /// 混合段（工具链的处置页 + 之后的原生尾页）里，只投该投的那几行；整段都是 noInject 时
        /// 自然返回空 —— 与"段级一票否决"在纯工具链段上**同效**，但混合段不再丢内容。
        /// 水位线不分行级：只推进到"已投的最大 Seq"，被跳过的行 Seq 已在水位线之下，永不会被补投。
        /// **投完什么都不清。**
        /// </summary>
        internal List<SegmentLine> TakeDelta(string unitId, float now)
        {
            List<SegmentLine> result = new List<SegmentLine>();
            DramaSegment seg = Pick(unitId);
            if (seg == null) return result;

            for (int i = 0; i < seg.Lines.Count; i++)
            {
                SegmentLine l = seg.Lines[i];
                if (l.Seq <= seg.Delivered) continue;   // 已投过
                if (l.NoInject) continue;               // 已经当 tool result 给过模型 ⇒ 不重复投
                result.Add(l);
            }

            if (result.Count > 0) seg.Delivered = result[result.Count - 1].Seq;
            return result;
        }

        /// <summary>
        /// 这个对方名下**有没有段**（**含"照记不投"的那种**）。
        ///
        /// 点击入口用它区分两种"投不出东西"——它们对玩家是两件事，不能混：
        ///   · **没有段** ⇒ 调用方退回旧的"单页注入"（分段器没记上时入口不许哑掉）；
        ///   · **有段但没新增 / 有段但不该投** ⇒ 调用方**什么也不发**
        ///     （同一页反复点不重投；工具链的段本就不该投）。
        /// </summary>
        internal bool HasAnySegment(string unitId)
        {
            return Pick(unitId) != null;
        }

        /// <summary>
        /// **链心跳**（09-23 用户定：段寿命由链驱动）：工具侧"还在等结果"时每几秒跳一次，
        /// 给 `unitId` 的活动段续命 —— 挂起期不再被固定预算掐断（长战斗/长论道不再被切成两段，
        /// "战前那句"因此不会永远投不出去，工具结果也不必跨段拼）。
        ///
        /// 语义边界：只影响**挂起期**（见 `DriveSuspended`，与探针 B1/B2/A 取或）；链一停
        /// （超过 `ChainBeatTtlSeconds` 没有下一跳）自然失效 —— **不需要显式"结束"信号**，
        /// `ReleaseChain` 只是提前清掉，省得最后一跳多护一段 TTL。
        /// </summary>
        internal void RenewChain(string unitId, float now)
        {
            if (string.IsNullOrEmpty(unitId)) return;
            _chainUnit = unitId;
            _chainBeatAt = now;
        }

        /// <summary>链结束（收口 / 取消 / 换人）⇒ 提前清心跳；只清**这个对方**的，不误伤别人。</summary>
        internal void ReleaseChain(string unitId, float now)
        {
            if (string.IsNullOrEmpty(unitId)) return;
            if (_chainUnit != unitId) return;
            _chainUnit = "";
            _chainBeatAt = -1f;
        }

        /// <summary>链心跳还新鲜吗（只认当前这个对方）。</summary>
        private bool ChainAlive(string unitId, float now)
        {
            if (_chainBeatAt < 0f || _chainUnit != (unitId ?? "")) return false;
            return now - _chainBeatAt <= ChainBeatTtlSeconds;
        }

        /// <summary>
        /// **区间读数**（工具结果专用，09-23）：把 `unitId` 在 `[from, until]` 内的**所有行**
        /// （页 + 选项，按 `SeqGlobal` = 追加序 = 时序）取出来。**不碰投递游标**（`Delivered`
        /// 只服务「对话」按钮的增量语义，两个消费者互不抢）。
        ///
        /// 为什么要跨段：链比段活得久是可能的（换人插页、探针空档、段被收口）—— 活动段之外
        /// 还要扫可投段，否则工具结果会缺前半截（极端情形：段被 TTL/上限挤掉，读不到，记日志）。
        ///
        /// `consume = true`：把这些行标成"已消费"（= 行级不投）—— 内容已随这份结果给过模型，
        /// 之后玩家再点「对话」不该重投。**这是选项行唯一的压制途径**（见 `NoteOption`）。
        ///
        /// 判不出就闭嘴：`unitId` 为空 ⇒ 空列表。
        /// </summary>
        internal List<SegmentLine> ReadRange(string unitId, float from, float until, bool consume)
        {
            List<SegmentLine> result = new List<SegmentLine>();
            if (string.IsNullOrEmpty(unitId)) return result;
            CollectRange(_active, unitId, from, until, result);
            for (int i = 0; i < _deliverables.Count; i++)
                CollectRange(_deliverables[i], unitId, from, until, result);
            result.Sort((x, y) => x.SeqGlobal.CompareTo(y.SeqGlobal));

            int marked = 0;
            if (consume)
            {
                for (int i = 0; i < result.Count; i++)
                {
                    if (result[i].NoInject) continue;
                    result[i].NoInject = true;
                    marked++;
                }
            }
            Log("区间读数（对方=" + unitId + "，[" + from.ToString("0.0") + "," + until.ToString("0.0")
                + "]）共 " + result.Count + " 行" + (consume ? "，标消费 " + marked + " 行" : "（只读）"), until);
            return result;
        }

        /// <summary>同一区间的**行数**（只读、不消费）：给收口判据当"链上已经有内容了"的证据
        /// （`BattleOutcome` 的三条规则用它替代旧的自建内容台账）。</summary>
        internal int CountRange(string unitId, float from, float until)
        {
            if (string.IsNullOrEmpty(unitId)) return 0;
            int n = CountRange(_active, unitId, from, until);
            for (int i = 0; i < _deliverables.Count; i++)
                n += CountRange(_deliverables[i], unitId, from, until);
            return n;
        }

        /// <summary>同一区间里的**页行**数（只读；选项行不算）：收口判据要的是"抓到了台词"，
        /// 玩家点了个选择只说明他答了话，不代表链上已经演到了结局（见 `BattleOutcome` 的三条规则）。</summary>
        internal int CountPages(string unitId, float from, float until)
        {
            if (string.IsNullOrEmpty(unitId)) return 0;
            int n = CountPages(_active, unitId, from, until);
            for (int i = 0; i < _deliverables.Count; i++)
                n += CountPages(_deliverables[i], unitId, from, until);
            return n;
        }

        private static int CountPages(DramaSegment seg, string unitId, float from, float until)
        {
            if (seg == null || seg.Counterpart != unitId) return 0;
            int n = 0;
            for (int i = 0; i < seg.Lines.Count; i++)
            {
                SegmentLine l = seg.Lines[i];
                if (l.At < from || l.At > until) continue;
                if (l.Kind == "option") continue;
                n++;
            }
            return n;
        }

        private static void CollectRange(DramaSegment seg, string unitId, float from, float until,
                                         List<SegmentLine> into)
        {
            if (seg == null || seg.Counterpart != unitId) return;
            for (int i = 0; i < seg.Lines.Count; i++)
            {
                SegmentLine l = seg.Lines[i];
                if (l.At < from || l.At > until) continue;
                into.Add(l);
            }
        }

        private static int CountRange(DramaSegment seg, string unitId, float from, float until)
        {
            if (seg == null || seg.Counterpart != unitId) return 0;
            int n = 0;
            for (int i = 0; i < seg.Lines.Count; i++)
            {
                SegmentLine l = seg.Lines[i];
                if (l.At < from || l.At > until) continue;
                n++;
            }
            return n;
        }

        /// <summary>世界被换掉（读档/新建/重载）：那场相遇已随旧世界消失 ⇒ 立刻全部丢弃。</summary>
        internal void OnWorldChanged(float now)
        {
            if (_active != null)
            {
                _active.State = SegmentState.Discarded;
                Log("世界被换掉 ⇒ 丢弃活动段（" + _active.Counterpart + "，共 " + _active.Lines.Count + " 条）", now);
                _active = null;
            }
            for (int i = 0; i < _deliverables.Count; i++)
                _deliverables[i].State = SegmentState.Discarded;
            _deliverables.Clear();
            _closing = null;
            _closeAt = -1f;
            _pendingPages.Clear();
            _transitionSeen = false;
            _transitionClearedAt = -1f;
            _chainUnit = "";
            _chainBeatAt = -1f;      // 链随旧世界一起没了：别让心跳把新世界的段续在一起
        }

        /// <summary>一行诊断快照（日志/心跳用）。带"不投"计数：混合段（处置页 + 尾页）在日志里一眼可见。</summary>
        internal string Snapshot(float now)
        {
            string a = _active == null
                ? "无活动段"
                : _active.State + "：" + _active.Counterpart + " " + _active.Lines.Count + " 条/已投 " + _active.Delivered
                  + NoInjectNote(_active);
            string d = "";
            for (int i = 0; i < _deliverables.Count; i++) d += NoInjectNote(_deliverables[i]);
            return a + "；可投 " + _deliverables.Count + " 段" + d + "；待定=" + (_closing != null ? "是" : "否");
        }

        /// <summary>诊断用：这一段里有多少行是「照记但不投」（没有则不占字符）。</summary>
        private static string NoInjectNote(DramaSegment seg)
        {
            int n = 0;
            for (int i = 0; i < seg.Lines.Count; i++) if (seg.Lines[i].NoInject) n++;
            return n == 0 ? "" : "/不投 " + n + " 条";
        }

        // ==================================================================== 内部

        private void OnWindowClosed(float now)
        {
            if (_active == null)
            {
                Log("关窗：手上没有活动段 ⇒ 忽略", now);
                return;
            }
            if (_closing != null) return;   // 上一笔待定还没结，按它继续等
            // ★动作 UI 在亮 = 游戏自己那个"交互"还没走完 ⇒ 这次关窗不是相遇结束（论道页间的形态）。
            //   把它记成一条**交接证据**（只是证据、不是判定 —— ε 到点时它和 B1/B2 起跳走同一条路）。
            //   为什么按电平而不是边沿：页开着时层顶被剧情窗盖住，A 的"假→真"采不到（见 `_a`）。
            if (_a) PushEdge("A", now);
            // ★场景态（B1+B2 同时亮）= 人还在那个场景里 ⇒ **这次关窗不是"相遇结束"，只是页间隙**。
            //   直接挂起（不设任何时间窗）：等"同人新页到达（回程条件要求到达时仍在场景态）"，
            //   或"场景态解除（B1/B2 落假）+ 20s 无新页"（走既有的回程窗机制收口）。
            //   凭什么不设窗：页间隙长度由玩家的阅读/演出决定（实测 0.53s ~ 6.64s，可能更长），
            //   固定窗接不住长间隙、放宽窗又会把"散场之后"的事误并（见文件头）。
            if (_b1 && _b2)
            {
                SuspendByScene(now);
                return;
            }
            _closeAt = now;
            _closing = _active;
            Log("关窗 ⇒ 起 " + EpsSeconds + "s 待定窗（等转场起跳" + (_a ? "；动作 UI 还亮着" : "")
                + "；对方=" + _closing.Counterpart + "）", now);
        }

        /// <summary>
        /// 场景态关窗 ⇒ 挂起（页间隙的开始）。与 `ResolvePendingClose` 里的挂起分支做了同样的事，
        /// 差别只在**证据来源**：那边是"关窗 ±ε 内的起跳/电平"，这里是"关窗那刻场景仍在"。
        ///
        /// 挂起后由 `DriveSuspended` 接住：B1/B2 一直亮 ⇒ 判"转场在飞"、不结算；B1/B2 落假 ⇒
        /// 起 20s 回程窗，窗内无新页就收口。**新页回程的条件**见 `RoutePage`（要求到达时仍在场景态）。
        /// </summary>
        private void SuspendByScene(float now)
        {
            DramaSegment seg = _active;
            if (seg == null) return;
            seg.State = SegmentState.Suspended;
            seg.SuspendedAt = now;
            seg.SceneSuspend = true;
            _transitionSeen = true;
            _transitionClearedAt = -1f;
            _active = seg;
            Log("关窗 ⇒ 场景内（B1+B2 电平）⇒ 挂起，等场景内新页回程：" + seg.Counterpart, now);
        }

        private void ResolvePendingClose(float now)
        {
            if (_closing == null) return;
            if (now - _closeAt < EpsSeconds) return;

            DramaSegment seg = _closing;
            float closeAt = _closeAt;              // ★ 先取出来：下面要清状态，但判定窗口还得用它
            _closing = null;
            _closeAt = -1f;

            string handoffBy = EdgeNearClose(closeAt);
            if (handoffBy.Length > 0)
            {
                seg.State = SegmentState.Suspended;
                seg.SuspendedAt = now;
                seg.SceneSuspend = false;        // "转场接走"的挂起：回程不要求场景态（与场景挂起刻意不同）
                _transitionSeen = true;          // 交接成立 ⇒ "见过转场"，回程窗逻辑随之启用
                _transitionClearedAt = -1f;
                _active = seg;
                Log("判交接（关窗 ±" + EpsSeconds + "s 内 " + HandoffDesc(handoffBy) + "）⇒ 挂起，等回程："
                    + seg.Counterpart, now);
            }
            else
            {
                Settle(seg, "干净关窗", now);
                _active = null;
            }

            // 待定期间扣住的页：现在按这次判定分派归属。
            List<PageRecord> held = new List<PageRecord>(_pendingPages);
            _pendingPages.Clear();
            for (int i = 0; i < held.Count; i++)
            {
                if (_closing != null) break;     // 理论上不会有（路由不会再产生待定）
                RoutePage(held[i], now);
            }
        }

        /// <summary>关窗 ±ε 内有哪些**交接证据**（返回名字，如 "B1" / "A" / "A↑"；空串 = 没有 ⇒ 干净关窗）。
        /// 证据形状不同：B1/B2/A↑ 是 Tick 里记的**边沿**，A 是关窗那一刻的**电平**
        /// （`A↑` = 关窗后 0.5s 才起的动作 UI —— 09-23 邀约实测，旧实现只看电平会漏）。</summary>
        private string EdgeNearClose(float closeAt)
        {
            string found = "";
            for (int i = 0; i < _edges.Count; i++)
            {
                if (_edges[i].At < closeAt - EpsSeconds || _edges[i].At > closeAt + EpsSeconds) continue;
                found = found.Length == 0 ? _edges[i].What : found + "+" + _edges[i].What;
            }
            return found;
        }

        /// <summary>
        /// 交接证据的人话（三种形状，不许都写成"起跳"——09-23 排障被措辞带偏过）：
        ///   · `B1` / `B2` = **边沿**：Tick 里 `PushEdge` 记的"假→真"；
        ///   · `A`  = **电平**：关窗那一刻 UI 层层顶还是 `ActionCourseInfo`（游戏自己的动作还没走完）；
        ///   · `A↑` = **边沿**：关窗 ±ε 内层顶**才**变成 `ActionCourseInfo`（09-23 邀约实测 0.5s 后起跳；
        ///            页开着时它被剧情窗盖住，所以这个边沿只在关窗之后采得到 —— 旧实现只看电平会漏）。
        /// </summary>
        private static string HandoffDesc(string by)
        {
            string[] parts = by.Split('+');
            string text = "";
            for (int i = 0; i < parts.Length; i++)
            {
                string s = parts[i] == "A"
                    ? "动作 UI 仍亮（关窗那刻层顶=ActionCourseInfo，电平）"
                    : parts[i] == "A↑"
                    ? "动作 UI 起跳（关窗后 ±" + EpsSeconds + "s 内层顶变 ActionCourseInfo）"
                    : parts[i] + " 起跳（假→真）";
                text = text.Length == 0 ? s : text + " + " + s;
            }
            return text;
        }

        private void DriveSuspended(bool b1, bool b2, bool a, float now)
        {
            DramaSegment seg = _active;
            if (seg == null || seg.State != SegmentState.Suspended) return;

            // 挂起期间"链还在飞"的判据：B1/B2（战斗/世界转场）+ **A（游戏动作进行中）**
            // + **我方动作链的心跳**（`RenewChain`）：长战斗/长论道（探针认得）与
            // "探针全暗但戏没演完"的空档（道具选择面板/加载/玩家慢读，只有心跳认得出）。
            bool chainAlive = ChainAlive(seg.Counterpart, now);
            bool trans = b1 || b2 || a || chainAlive;
            if (trans)
            {
                _transitionSeen = true;
                _transitionClearedAt = -1f;
                // ★链在飞 ⇒ 挂起预算**滑动**★（09-23 用户定：段的寿命由链驱动，不由固定数字）：
                //   几分钟的战斗不再被 180s 预算切段 —— 切了就会让"战前那句"永远投不出去
                //   （`Pick` 只投最近一段）、工具结果还得跨段拼。
                if (seg.SuspendedAt >= 0f) seg.SuspendedAt = now;
            }
            else if (_transitionClearedAt < 0f)
            {
                _transitionClearedAt = now;
                Log("转场全落假 ⇒ 起回程窗 " + ReturnWindowSeconds + "s（等 " + seg.Counterpart + " 回来）", now);
            }

            if (_transitionSeen && _transitionClearedAt >= 0f
                && now - _transitionClearedAt >= ReturnWindowSeconds)
            {
                Settle(seg, "转场结束后 " + ReturnWindowSeconds + "s 无新页", now);
                _active = null;
                return;
            }

            // ★不可续期的硬上限★：心跳/探针把预算一直往后推时，防"链卡死"把段变成僵尸。
            if (now - seg.StartedAt >= SegmentHardCeilingSeconds)
            {
                Settle(seg, "段活过硬上限 " + SegmentHardCeilingSeconds + "s", now);
                _active = null;
                return;
            }

            if (seg.SuspendedAt >= 0f && now - seg.SuspendedAt >= SuspendBudgetSeconds)
            {
                Settle(seg, "挂起超总预算 " + SuspendBudgetSeconds + "s（无心跳）", now);
                _active = null;
            }
        }

        private void RoutePage(PageRecord rec, float now)
        {
            DramaSegment seg = _active;

            if (seg == null)
            {
                // 干净关窗之后同人又来页 ⇒ **开新段**（A 灭的关窗就是分层）。
                // ★别再引入"同人 + 时间近 + 未投 ⇒ 续接"的兜底★：09-23 实证它会把手边的另一件事
                //   并进来（闲聊段 + 0.5s 后的论道开场 ⇒ 8 条一起投，模型读成"我推辞了论道"）。
                //   多窗交互的页间隙靠 A 判据（交接 → 挂起 → 回程）接住，不需要时间窗。
                OpenSegment(rec, now);
                return;
            }

            if (seg.State == SegmentState.Suspended)
            {
                bool samePerson = seg.Counterpart == rec.UnitId;
                bool withinBudget = seg.SuspendedAt >= 0f && now - seg.SuspendedAt <= SuspendBudgetSeconds;
                // ★场景挂起（`SceneSuspend`）的回程条件多一条：**到达时人还在场景里**（B1+B2 仍亮）。
                //   场景内页间隙多长都接得住（不设时间窗）；而**散场之后**来的同人页是另一件事 ——
                //   并回邀约段会让模型把两场当成一场（与"闲聊+论道被并"那次事故同形，宁可拆段）。
                bool sceneStillOn = !seg.SceneSuspend || (_b1 && _b2);
                if (samePerson && withinBudget && sceneStillOn)
                {
                    seg.State = SegmentState.Recording;
                    seg.SuspendedAt = -1f;
                    seg.SceneSuspend = false;
                    _transitionSeen = false;
                    _transitionClearedAt = -1f;
                    Append(seg, rec);
                    Log("回程 ⇒ 回记录中（对方=" + seg.Counterpart + "）", now);
                    return;
                }
                // 回不了程：换人 / 超预算 / **场景已退出** ⇒ 挂起段到此为止，新页开新段。
                Settle(seg, samePerson
                    ? (withinBudget ? "场景已退出（同人新页在场景外到达）" : "挂起超总预算 " + SuspendBudgetSeconds + "s")
                    : "换人（新页对方=" + rec.UnitId + "）", now);
                _active = null;
                OpenSegment(rec, now);
                return;
            }

            if (seg.Counterpart == rec.UnitId)
            {
                Append(seg, rec);               // 同人续页（含同窗翻页与战后回来）
                return;
            }

            Settle(seg, "换人（新页对方=" + rec.UnitId + "）", now);
            _active = null;
            OpenSegment(rec, now);
        }

        private void OpenSegment(PageRecord rec, float now)
        {
            // 注意：**不吞**旧可投段（即使是同一个人）——两者并存、各自算各自的 TTL，
            // 上限 MaxDeliverables 只管"最老的一段被挤掉"。理由：玩家要对哪一段说话，
            // 由"他点对话时哪一段是当前段"决定；把旧段提前丢掉会让"刚打完仗还没点"的内容
            // 因为"对方又开了个口"而消失。
            // （曾经有过一个例外：同人 + 未投 + 20s 内 ⇒ 续接合并。09-23 因它把"闲聊 + 论道"并成
            //   一段（模型据此说出"我推辞了论道"）已删除 —— 页间隙由 A 判据的交接/回程接住。）
            DramaSegment seg = new DramaSegment();
            seg.Counterpart = rec.UnitId;
            seg.State = SegmentState.Recording;
            seg.StartedAt = now;
            _active = seg;
            _transitionSeen = false;
            _transitionClearedAt = -1f;
            Append(seg, rec);
            Log("开段（对方=" + rec.UnitId + "，页=" + rec.DramaId + "）", now);
        }

        private void Append(DramaSegment seg, PageRecord rec)
        {
            SegmentLine line = new SegmentLine();
            line.Seq = seg.NextSeq++;
            line.SeqGlobal = ++_seqGlobal;
            line.DramaId = rec.DramaId;
            line.Speaker = rec.Speaker;
            line.Name = rec.Name;
            line.Text = rec.Text;
            line.At = rec.At;
            line.NoInject = rec.NoInject;          // 「照记但不投」跟着**页**落到这一行（理由见 OnPage 重载）
            seg.Lines.Add(line);

            // 逐行留痕（而不是"本段被标"）：混合段（处置页 + 尾页）在日志里一眼能看出哪几行不投。
            // 页被扣住、ε 到点开成新段的情形，标记照样落在**新段**的那一行上（打在活动段上就会漏）。
            if (rec.NoInject)
                Log("本行「照记但不投」（内容已当 tool result 给过模型）：" + seg.Counterpart
                    + " 第 " + line.Seq + " 条（页=" + rec.DramaId + "）", rec.At);
        }

        private void Settle(DramaSegment seg, string reason, float now)
        {
            seg.State = SegmentState.Deliverable;
            seg.SettledAt = now;
            seg.SettleReason = reason;
            _deliverables.Add(seg);
            while (_deliverables.Count > MaxDeliverables) _deliverables.RemoveAt(0);
            Log("收口（" + reason + "）：" + seg.Counterpart + " 共 " + seg.Lines.Count
                + " 条，可投 TTL " + DeliverTtlSeconds + "s", now);
        }

        private void ExpireDeliverables(float now)
        {
            for (int i = _deliverables.Count - 1; i >= 0; i--)
            {
                DramaSegment d = _deliverables[i];
                if (now - d.SettledAt < DeliverTtlSeconds) continue;
                d.State = SegmentState.Discarded;
                _deliverables.RemoveAt(i);
                Log("可投段过期未投出 ⇒ 丢弃（" + d.Counterpart + "，共 " + d.Lines.Count
                    + " 条，已投 " + d.Delivered + "）", now);
            }
        }

        /// <summary>
        /// 取"该看的那一段"：**时间上最近的**（活动段优先，否则最近收口的可投段）。
        ///
        /// ⚠ 不按"能不能投"来挑（旧写法会跳过 NoInject 段、去找更旧的可投段）——那种挑法在
        /// **工具链的窗**里点一下时，会把**上一次相遇**的原文塞进来（旧事被当成此刻发生的事，
        /// 正是要消灭的那类反事实）；正确的答案是"这一段不该投 ⇒ 什么也不发"。
        /// </summary>
        private DramaSegment Pick(string unitId)
        {
            if (string.IsNullOrEmpty(unitId)) return null;
            if (_active != null && _active.Counterpart == unitId) return _active;

            DramaSegment best = null;
            for (int i = 0; i < _deliverables.Count; i++)
            {
                DramaSegment d = _deliverables[i];
                if (d.Counterpart != unitId) continue;
                if (best == null || d.SettledAt > best.SettledAt) best = d;
            }
            return best;
        }

        /// <summary>日志里的归属显示（空串 = 判不出，别写成"没有人"）。</summary>
        private static string ShowSide(string speaker)
        {
            return string.IsNullOrEmpty(speaker) ? "(判不出)" : speaker;
        }

        private void PushEdge(string what, float now)
        {
            EdgeRecord e = new EdgeRecord();
            e.What = what;
            e.At = now;
            _edges.Add(e);
        }

        private void PruneEdges(float now)
        {
            for (int i = _edges.Count - 1; i >= 0; i--)
                if (now - _edges[i].At > EdgeMemorySeconds) _edges.RemoveAt(i);
        }

        private void Log(string what, float now)
        {
            if (_log == null) return;
            _log(what);
        }
    }
}
