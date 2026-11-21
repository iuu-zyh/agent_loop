/// <summary>
/// NPC 主动互动触发监测 —— 感知侧（只在游戏主线程跑）
///
/// 分层分权（对齐神识传音 §3.4 主动交互）：
///  - 本类只做「何时 / 对谁 / 什么意图」的触发检测：挂 g.events.On(WorldAddDay) 每游戏日一次，
///    内部节流一日一试 + 单NPC双冷却（现实600s + 游戏3日），产出事件经 WsClient.SendInitiative 发出；
///  - Python 端 ChatHub.handle_initiative 负责把意图变成开口回合（LLM 编排），
///    本类不碰对话逻辑、不碰提示词（意图文案在 Python initiative.py，可热改）。
///
/// 触发实现（混合日节拍 + 状态闸）：
///  - 全局：每游戏日最多尝试一次（OnWorldAddDay），先过状态闸（战斗/确认窗），再过 0-100 日概率
///    （<阈值好感减半，对齐神识传音 us_strings:2047）；
///  - 单NPC：双冷却 max(现实 NpcCooldownRealSec, 游戏 NpcCooldownDays)；
///  - 候选 = 玩家关系记录全集（十容器+好友簿+**仇人簿**+GetAllGoodRelationUnitID 含敌）+ 手动通讯录好友
///    + 同格所有人（陌生人当面可搭话）；门槛：非通讯录成员仅同格可发起（见 README.md 附录二 G（功能设计底稿））；
///  - 意图按亲密度派发：负（含仇人）→负向4条，正→5条，高好感≥120 40%进思念/情感；BuildReason 配缘由。
///
/// 真机联调 TODO（与 ToolExecutor 同风格）：
///  - 战斗/确认窗等状态闸的准确判定待真机核对，当前 best-effort + try/catch 放行。
///
/// 诊断强制触发（自主交互系统观测加速器）：
///  - `&lt;Mod根&gt;\_diag_initiative_force.txt` 存在即进入强制模式（DiagSwitches 每 ~2s 复查，
///    增删文件即时生效，不需要重启游戏/Python）；
///  - 只放宽「时间」四闸：日节拍、同日夜守卫、每日概率、单人双冷却 → 改为按现实秒节拍
///    （默认 20s，文件 `interval=` 可改）持续触发，不必推进游戏日；
///  - 候选集、通讯录/同格门槛、意图派发（`intent=` 可指定）、当面确认窗全部仍走真实链路；
///  - 强制模式**不写冷却表**（关掉开关后不留痕，不影响正常游玩判定）。
/// </summary>
using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using Newtonsoft.Json.Linq;      // 11-13：优先队列 RPC 回包（list_mail_pending）
using UnityEngine;

namespace AgentLoopBridge
{
    public class NpcInitiativeMonitor
    {
        private readonly WsClient _ws;

        // —— 游戏日节拍 ——
        private int _lastDay = -1; // 上次尝试的 totalDays

        // —— 单NPC冷却（双闸）——
        private readonly Dictionary<string, DateTime> _cooldownReal = new Dictionary<string, DateTime>();
        private readonly Dictionary<string, int> _cooldownDay = new Dictionary<string, int>();

        // —— 捎话优先队列（11-13，用户需求）——
        //
        // 语义（用户 11-13 拍板，**边界很明确**）：
        //   「只改变排序（仍受一日一次限制），不会跳过 daily_chance」。
        // 所以这里存的是**排序加成**，不是"绕过闸门"的名单。换个说法：
        //   · 日节拍 / 全局日概率 / 单人双冷却 / 能说闸 / 忙闲 —— **一条都不动**；
        //   · 只有 `SortRows` 的比较键多一项：有未读捎话的人排在前面。
        //
        // 数据源 = Python `list_mail_pending`（信箱是**账本**的一部分，C# 看不到也不该复制，
        // 见 `agent/mailbox.pending_map`）。两个灌入时机：
        //   ① 连接建立/重连（`RefreshMailPending`，见 ModMain.OnWsReconnected）—— Python 重启后
        //      这边必须重新问一次，否则那份名单永远停在上次的样子；
        //   ② 每次换日节拍**之前**异步刷新一次（`RequestMailPending`），问不到就用手上这份旧的。
        //      **刻意不阻塞**：RPC 回调是主线程排队执行的，而日节拍本身就在主线程，
        //      同步等会把它卡住（换日是玩家推进剧情的那一刻，卡顿最刺眼）。
        //      用旧名单的后果只是"某人这次没排到最前"，与没有这个功能等价 —— 可接受。
        private HashSet<string> _mailPending = new HashSet<string>();
        /// <summary>收信人 → 发信人真名列表（只给舞台指令用；判据只有 `_mailPending` 那一个）。</summary>
        private Dictionary<string, List<string>> _mailSenders = new Dictionary<string, List<string>>();
        private bool _mailInFlight;

        /// <summary>把 Python 给的 `{npc_id: [发信人…]}` 灌进优先队列（主线程）。
        /// 空表也是有效答案（= 现在没人在等），必须照写 —— 否则旧的 Forever 留着。</summary>
        internal void SetMailPending(JObject items)
        {
            var next = new HashSet<string>();
            var senders = new Dictionary<string, List<string>>();
            try
            {
                if (items != null)
                {
                    foreach (var kv in items)
                    {
                        if (string.IsNullOrEmpty(kv.Key)) continue;
                        next.Add(kv.Key);
                        var list = new List<string>();
                        var arr = kv.Value as JArray;
                        if (arr != null)
                        {
                            foreach (var t in arr)
                            {
                                string nm = t == null ? "" : t.ToString();
                                if (!string.IsNullOrEmpty(nm) && !list.Contains(nm)) list.Add(nm);
                            }
                        }
                        senders[kv.Key] = list;
                    }
                }
            }
            catch (Exception e)
            {
                // 解析失败**不覆盖**旧名单：宁可用旧数据，也别把"有信"误清成"没信"
                // （那会让一个真在等回话的人被排到最后，而这不会有任何报错）。
                ModMain.P("[NpcInitiativeMonitor] 捎话优先队列解析失败（保留旧名单）: " + e.Message);
                return;
            }
            _mailPending = next;
            _mailSenders = senders;
            // ══ 探针（11-15 联调，验完删）══ 每次灌名单都吼一行：这是判断"优先队列到底有没有
            // 拿到数据"的唯一现场证据。**没有这一行就分不清**三种情况：Python 没名单、
            // RPC 没回来、还是回来了但解析成空（11-13 那个 resp["items"] 读错顶层的 bug
            // 正是靠这个对比才现形的）。grep `[MAIL-PROBE]`
            try
            {
                var names = new List<string>();
                foreach (var n in next) names.Add(n);
                ModMain.P("[MAIL-PROBE] 优先队列已刷新："
                          + (names.Count == 0 ? "空（当前无人有未读捎话）"
                                              : names.Count + " 人 → " + string.Join("、", names.ToArray()))
                          + (senders.Count > 0 ? "｜发信人：" + DescribeSenders(senders) : ""));
            }
            catch { }
        }

        /// <summary>「收信人←发信人」的可读串（探针专用，验完与调用点一起删）。</summary>
        private static string DescribeSenders(Dictionary<string, List<string>> senders)
        {
            var parts = new List<string>();
            foreach (var kv in senders)
                parts.Add(kv.Key + "←" + (kv.Value == null || kv.Value.Count == 0
                                          ? "?"
                                          : string.Join("/", kv.Value.ToArray())));
            return string.Join("、", parts.ToArray());
        }

        /// <summary>JObject 的顶层键名（探针专用，验完删）。</summary>
        private static IEnumerable<string> KeysOf(JObject o)
        {
            if (o == null) yield break;
            foreach (var p in o.Properties()) yield return p.Name;
        }

        /// <summary>该 NPC 有没有未读捎话（= 要不要排到前面）。名字判据与冷却表同源（真名）。</summary>
        private bool HasMailPending(string name)
        {
            return !string.IsNullOrEmpty(name) && _mailPending.Contains(name);
        }

        /// <summary>该 NPC 未读捎话的**发信人真名**（多个时取第一个；没有则空串）。
        /// 只给舞台指令用（"张三托人捎了句话给你"），不参与任何判断。</summary>
        private string MailSenderOf(string name)
        {
            if (string.IsNullOrEmpty(name)) return "";
            List<string> senders;
            return _mailSenders.TryGetValue(name, out senders) && senders != null && senders.Count > 0
                ? senders[0] : "";
        }

        /// <summary>问 Python 要一次优先队列快照（异步；问不到就继续用旧的）。</summary>
        internal void RequestMailPending()
        {
            if (_ws == null || _mailInFlight) return;
            _mailInFlight = true;
            try
            {
                _ws.SendRequest("list_mail_pending", new JObject(), resp =>
                {
                    _mailInFlight = false;
                    try
                    {
                        if (resp == null) return;
                        if (resp["ok"] != null && !(bool)resp["ok"]) return;    // 未连接/超时：保持旧名单
                        // ★ 名单在 **data 里面**（`{"type":"response","ok":true,"data":{"items":…}}`）。
                        //   这里原先直接读 `resp["items"]` —— 那是协议信封的**上一层**，永远是 null，
                        //   而 `SetMailPending(null)` 是"合法的空答案"（见它的注释）⇒ 每次都把名单
                        //   清空 ⇒ **优先队列一次都没生效过，且没有任何报错**。
                        //   线上真实回包（`WsServer._run_request` 装帧）：
                        //     {"type":"response","req_id":"u7","ok":true,
                        //      "data":{"items":{"李四":["张三"]},"world_known":true,…}}
                        //   与本文件其余所有 RPC 消费方同一条读取路径（`resp["data"]`，见
                        //   ConfigPresenter / ContactDuty）—— 只有这里当初写成了顶层。
                        var data = resp["data"] as JObject;
                        // ══ 探针（11-15 联调，验完删）══ 把"回包长什么样"逐层打出来。
                        // 有了它，`data` 为空 / `items` 缺失 / 解析成空表 三种情况一眼可分，
                        // 不用再靠猜。grep `[MAIL-PROBE]`
                        try
                        {
                            ModMain.P("[MAIL-PROBE] 回包到达：顶层键=["
                                      + string.Join(",", new List<string>(KeysOf(resp)).ToArray())
                                      + "]｜data " + (data == null ? "=null" : "有")
                                      + "｜items " + (data?["items"] == null ? "=null" : "有"));
                        }
                        catch { }
                        SetMailPending(data?["items"] as JObject);
                    }
                    catch (Exception e)
                    {
                        ModMain.P("[NpcInitiativeMonitor] 优先队列回包处理失败: " + e.Message);
                    }
                }, 8000);
            }
            catch (Exception e)
            {
                _mailInFlight = false;
                ModMain.P("[NpcInitiativeMonitor] 优先队列请求失败: " + e.Message);
            }
        }

        // —— 可配参数（默认值对齐混合方案，config 热更）——
        private int _dailyChance = 15;          // 0-100，每日总闸，0=关闭，100=日日必试
        private int _npcCooldownDays = 3;       // 单人游戏日冷却
        private int _npcCooldownRealSec = 600;  // 单人现实秒冷却（防挂机不推日）
        private int _lowThresh = 60;            // <阈值视为低好感（2星≈60）
        private bool _lowHalve = true;          // 低好感概率减半
        /// <summary>主动互动总开关（config `initiative.enabled`，新增）。
        /// false = 关掉**日节拍**（玩家可见的正常触发：`TryTriggerForDay` 直接返回）；
        /// `_diag_initiative_force.txt` 强制路径**不受影响**——那是开发诊断，不是玩法开关。
        /// 玩家主动开窗对话、工具、其他一切都不受影响（本开关只管 NPC 自己找上门）。</summary>
        private bool _enabled = true;
        private bool _disabledLogged;           // 「总开关关着」只吼一次（重新打开时复位）

        /// <summary>**我方自制确认窗**的 UI 名 —— 判据已搬到 `WorldStateFacts.OwnDramaUiName`，
        /// 这里只留一个别名，免得既有引用散落（本文件内已无使用）。
        /// ★别在这里重新写一遍"哪些窗算剧情窗"★ 那是 `WorldStateCore.DramaWindowReason` 的唯一实现。</summary>
        private const string DramaUiOwnName = WorldStateFacts.OwnDramaUiName;

        // 意图键（与 Python initiative.py 的 INITIATIVE_INTENTS 对齐）
        private static readonly string[] PositiveIntents = { "greet", "smalltalk", "courteous", "life", "recent" };
        private static readonly string[] PositiveCloseIntents = { "missing", "affection" };
        private static readonly string[] NegativeIntents = { "malice", "vent", "provocation", "disdain" };

        // ─────────── 动作意图池（10-16 新增）───────────
        //
        // 动机（用户原话）："npc会更加主动的调用工具…实际体验发现 npc 更多是被动调用工具，
        // 只有玩家提到了才会去调用"。根因不是模型不听话，而是**意图池里一个动作键都没有** ——
        // 11 个键全是言语行为（打招呼/闲聊/表达思念…），`format_initiative_message` 只描述
        // "说什么"，从不邀请"做什么"。
        //
        // 池分两维，判据都来自 `ToolExecutor.cs` 各 op 的 SameGrid 校验（异地会 Fail）：
        //   · **能不能远程做** —— 异地池 ⊂ 同格池（单调包含，见 `PickIntent` 的注释）；
        //   · **要不要名分** —— 双修/传功要求道侣/师徒弟名分，而 `PickIntent` 刻意**不查名分**
        //     （用户拍板"不给真实输入"）⇒ 这两键暂缓，否则会出现"想双修但没名分"。
        //
        // 关系类（`social_relation` 家族：缔结/解除/求婚）用户明确**暂不加**（"要慎重"）。
        // 讨要/偷窃也不加：含糊的"同意互动" 不等于 同意被要东西。

        /// <summary>异地也能做的动作（远程池 —— 同格时同样可用）。</summary>
        private static readonly string[] RemoteActionIntents = {
            "gift",      // 赠予  economy_item      ToolExecutor:2955 有专门的"异地以传讯方式赠达"分支
            "lun_dao",   // 论道  world_ai_action   :2411 注释明写「言语往来，可远程（IL 实证 1037）」
            "yao_yue",   // 邀约  world_ai_action   :2418 无 SameGrid 校验（邀约本就是"约见面"）
        };

        /// <summary>**只有同格**且**敌对**才能做的动作（异地必 Fail）。
        ///
        /// ⚠ 敌对只配 `attack`，**不配 `spar`**（10-17 用户指出）：切磋是友善的较量，
        /// 对一个好感为负的人提"切磋"语义上拧 —— 他说"我看你不顺眼"，然后邀你过两招。
        /// 敌对同格时 `spar`/`attack` 各 12.5%，等于一半的敌对动作是拧的。
        /// </summary>
        private static readonly string[] HostileActionIntents = {
            "attack",    // 攻击  world_ai_action   :2335 Fail("攻击需同格，当前异地")
        };

        /// <summary>**只有同格**才能做的动作（异地必 Fail）—— 非敌对档用（切磋是友善的较量）。</summary>
        private static readonly string[] LocalActionIntents = {
            "spar",      // 切磋  world_ai_action   :2278 Fail("切磋需同格，当前异地")
            "attack",    // 攻击  world_ai_action   :2335 Fail("攻击需同格，当前异地")
        };

        /// <summary>触发后走「动作支路」的概率。
        ///
        /// 用**常数**而不是按好感分档的函数：好感的影响已经由"池的构成"承担了
        /// （敌对只配攻击、正向配赠予/论道/邀约），再给概率加一档是重复建模。
        ///
        /// 频率实算（给调参的人一个锚）：动作支路命中后还要过池子的随机，
        /// 异地正向时实际落到具体动作键约 0.25×(1/3)≈8%；再叠加 `initiative.daily_chance`
        /// （默认 15，≈每 6.7 游戏日一次主动交互）⇒ **约 50~80 游戏日才有一次动作型主动交互**。
        /// 若验收时"看不到效果"，先看 `daily_chance`，那才是主瓶颈。
        /// </summary>
        private const float ActionChance = 0.25f;
        /// <summary>全集：诊断强制模式校验 intent= 是否合法用。
        /// ⚠ 新增动作键必须同步加进来 —— 否则 `_diag_initiative_force.txt` 写 `intent=gift`
        /// 会被当成"未知 intent"静默退回按好感派发，而那是**验收新行为的主要手段**。</summary>
        private static readonly string[] AllIntents = {
            "greet", "smalltalk", "courteous", "life", "recent",
            "missing", "affection",
            "malice", "vent", "provocation", "disdain",
            "gift", "lun_dao", "yao_yue", "spar", "attack",
        };

        // —— 触发时刻的「可否当面弹出」备忘（09-13 用户拍板 (b)）——
        // 收尾分流②此前用**回复到达那一刻**的同格/忙闲重新判一次，于是出现
        // "触发时异地（走传音）→ 回复时 NPC 走过来变同格 → 没同意就当面开窗"
        // （实机复现两次：22:17 触发 sameGrid=False / 22:18 分流② 同格=True；
        //   22:58 又复现一次，两条日志并列摆着）。现在把**触发时**的判定记下来，收尾只认它。
        // 值 = `needConsent`（10-16 起）= `!busy && (sameGrid || 动作意图)` —— **不是**原先的
        // `sameGrid && !busy`。异地动作现在也走确认窗并预开对话窗，所以它同样该享受"当面弹出"；
        // 而 busy 的触发仍走直发传音（弹窗=打扰），不该事后补弹。
        private static readonly Dictionary<string, bool> _canPopAtTrigger = new Dictionary<string, bool>();

        /// <summary>该意图键是否属于**动作向**（会调工具去改变世界），而不是纯言语。
        ///
        /// 用途：决定"要不要先弹确认窗"（异地动作要问，异地言语不问）与
        /// "要不要预开对话窗"。与 Python `initiative.ACTION_INTENTS` 集合相等
        /// （由 `tests/test_initiative.py::test_action_intent_keys_are_paired_with_csharp` 对账）。
        ///
        /// ⚠ **不要**把它当成"这个回合一定会调工具" —— 意图只是给模型的倾向，
        /// 是否真调由模型决定。它只回答"这个意图**可能**涉及动作"。
        /// </summary>
        private static bool IsActionIntent(string intent)
        {
            if (string.IsNullOrEmpty(intent)) return false;
            return Array.IndexOf(RemoteActionIntents, intent) >= 0
                || Array.IndexOf(LocalActionIntents, intent) >= 0;
        }

        /// <summary>记下本次触发的"可否当面弹出"（绑定到**触发时刻**的判定）。
        ///
        /// ★ 10-16 语义扩展 ★ 调用方现在传的是"本回合**走过确认窗**"（`needConsent`），
        /// 而不是原来的"同格且不忙"。因为异地动作现在也会走确认窗并预开对话窗，
        /// 它的回复理应享受分流①（直播已读）/②（当面弹出），不该被登记成未读 ——
        /// 玩家明明刚刚点过同意。
        ///
        /// 保持"触发时判定"这条铁律不变（09-13 用户拍板 b）：绝不在"回复到达那一刻"
        /// 用现场状态重判一次，否则会出现"触发时异地（没弹窗）→ 回复时人走过来了 →
        /// 没同意就当面开窗"（22:17 / 22:58 两次实机）。
        /// </summary>
        internal static void RememberTriggerPop(string npc, bool mayPop, bool _unused = false)
        {
            if (string.IsNullOrEmpty(npc)) return;
            _canPopAtTrigger[npc] = mayPop;
        }

        /// <summary>该 NPC 最近一次触发时**是否走过确认窗**（= `needConsent` = `!busy && (sameGrid || 动作意图)`；
        /// 未知=false：不弹，宁可只亮红点）。
        ///
        /// ⚠ 名字里的 "Pop" 是 09-13 的历史命名，那时语义确实等价于"同格且不忙"。
        /// 10-16 起异地动作也会走确认窗并预开对话窗 ⇒ 它的回复也该当面弹出，
        /// 所以判据改成了 needConsent。**不要再按字面理解成"此刻能不能弹"**——调用方
        /// `ContactDuty` 还会在此基础上再要求"回复到达时仍同格、无窗、不忙"。</summary>
        internal static bool CanPopAtTrigger(string npc)
        {
            if (string.IsNullOrEmpty(npc)) return false;
            return _canPopAtTrigger.TryGetValue(npc, out var v) && v;
        }

        // —— 诊断强制模式计时（_diag_initiative_force.txt）——
        private float _forceAcc;    // 距上次强制触发的现实秒累计
        private bool _forceSpent;   // 「一次即停」：本次武装已收口（见 MarkForceSpent）
        private long _forceSpentStamp;  // 收口时的开关文件时间戳（变更即重新武装）

        // —— 同意后自动开窗（用户拍板：立刻开窗吃流式）——
        // 为什么要延迟几帧：选项回调此刻正处在"剧情窗关闭/翻页"的窗口期，而 09-10 事故的
        // 根因之一就是"在剧情窗创建/销毁期调 g.ui.OpenUI"（会打乱 UI 层布局、把立绘槽位
        // 打成 0 尺寸）。推迟到窗口关完再开，既保住"点同意立刻有反应"，又绕开那个窗口期。
        //
        // 修（同格实感延迟的真凶）：旧版是"3 帧后**只试一次**"，失败就退化成
        // "回复到达时再弹"（= 玩家点完同意干等十几秒才蹦出窗口）。而失败的原因恰恰是那把
        // 铁律闸：`AbPanelProber` 当年判 `g.ui.GetUI("UICustomDramaDyn")!= null`
        // —— 那是我们自己这个确认窗的名字，且判的是**存在**不是可见
        // （`AbChatPanel` 注释明说"管理器 CloseUI 不一定销毁实例"）。所以"本局第一次同格互动"
        // 极易命中：点同意 → 3 帧后闸门还拦着 → 宿主建不出来 → 回退成回复时才开窗。
        // 现在改成：3 帧后**每帧重试**，成功即止，`PendingOpenTimeoutSec` 秒超时仍回落旧路径。
        // 零回归：若 3 帧时闸门本就通过，第 1 次就成功，行为与旧版完全一致（日志多一个"第 1 次"）。
        // 【09-21 后记】这条根因已随闸门整改消除：现在判**可见** + 名字家族
        //   （`DramaWindowName`），确认窗刚关、实例未销毁不会再拦住任何人。
        private WorldUnitBase _pendingOpenUnit;
        private int _pendingOpenFrames;
        private float _pendingOpenDeadline;   // 重试截止（Time.unscaledTime）
        private int _pendingOpenTries;        // 已尝试次数（成功日志/超时日志用）
        private bool _pendingOpenWarned;      // 首次失败打一行（之后静默重试，防刷屏）
        private const int PendingOpenDelayFrames = 3;
        private const float PendingOpenTimeoutSec = 5f;

        // ── 10-21（用户拍板）：读档后的「日历校准期」双闸 ──────────────────
        // 【症状】（Player-prev.log 11:57 局逐行实证）读档后头 ~25 秒里，游戏日历在快速补表：
        //   0 → 42780 → 1383 → 1388。旧守卫只挡「同一天重复」，日历每跳一次日节拍就完整跑一遍：
        //   连掷两次日概率骰、两个 NPC 先后主动开口，确认窗创建（实测 324ms）等开销全堆在
        //   游戏自身读档流送的低帧窗口里 —— 玩家观感 = 「刚进游戏又卡又乱」。
        // 【闸 A：进世界宽限期（用户方案）】落地后 30s 内日节拍整段不走、也不掷骰。
        //   为什么必须用时间闸而不是只靠闸 B：补表也可能**一格一格 +1** 地跳（1383→1384→…），
        //   那种小碎步每步都"合法"，跳变守卫抓不住，只有时间闸能整段压掉。
        //   到期后按当前日**补评一次**（保留"每天一次日概率"的原语义，只是挪到日历稳定后）。
        // 【闸 B：跳变守卫】宽限期后日历一跳超过 1 日（或倒退）⇒ 视为噪声：静默对齐、不掷骰。
        //   防晚到的跳变、别的 mod 动日历、以及跨存档读旧档（顺带修掉旧版一个潜在卡死：
        //   同一次游戏进程里先读新档再读旧档，cur 变小 → 旧守卫 `< 1` 恒真 → 日节拍永久停摆）。
        // 【锚点】「进世界」= playerUnit 真实存在的时刻，**不是**"日历可读"的时刻 ——
        //   11:57 局实证读档途中日历就已能读出垃圾值，若以它为锚，30s 会提前烧完。
        //   回菜单/换档（playerUnit 变 null）→ 锚点与日序一并复位，下次落地重新计 30s。
        private const float WorldEntryGraceSec = 30f;
        private float _worldAnchorAt = -1f;   // 本次进世界锚点（Time.unscaledTime）；-1=还没落地
        private bool _graceLogged;            // 宽限结束只吼一行（实机对表用）

        public NpcInitiativeMonitor(WsClient ws)
        {
            _ws = ws;
        }

        /// <summary>热更配置（ModMain 拉 config 后调）。 clamp 0-100。
        /// `enabled` = 主动互动总开关（新增）：保存配置后由 `ConfigPresenter` 立刻重拉一次
        /// （`ModMain.RefreshInitiativeFromConfig`）→ 全部字段热生效，不必重启 Python。</summary>
        public void Configure(int dailyChance, int cooldownDays, int cooldownRealSec, int lowThresh,
                              bool lowHalve, bool enabled = true)
        {
            _dailyChance = Math.Max(0, Math.Min(100, dailyChance));
            _npcCooldownDays = Math.Max(0, cooldownDays);
            _npcCooldownRealSec = Math.Max(0, cooldownRealSec);
            _lowThresh = Math.Max(0, lowThresh);
            _lowHalve = lowHalve;
            bool changed = _enabled != enabled;
            _enabled = enabled;
            if (enabled) _disabledLogged = false;   // 重新打开后，下次关闭还要能吼一声
            ModMain.P($"[NpcInitiativeMonitor] config: dailyChance={_dailyChance} cooldownDays={_npcCooldownDays} realSec={_npcCooldownRealSec} lowThresh={_lowThresh} lowHalve={_lowHalve} enabled={_enabled}"
                      + (changed ? "（总开关状态变化 → 即时生效，无需重启）" : ""));
        }

        /// <summary>兼容旧 Frame 挂载：保留但转日节拍（过渡期双挂不重）。</summary>
        public void OnUpdate()
        {
            // 诊断（因果对照用）：mod 被整体停用 / 单独停监测器后不再做任何实质工作
            if (ModMain.Suspended) return;
            // 待开窗（同意后自动开窗）：放在所有闸与强制模式分支**之前**——
            // 它是一次 UI 收尾动作，与"今天还要不要触发"无关；放在后面会被 IgnoreState/
            // 强制模式/日节拍的 return 吃掉。
            TickPendingOpen();
            if (DiagSwitches.NoMonitor) return;
            // 诊断强制模式（_diag_initiative_force.txt）：跳过日节拍/概率/冷却，按现实秒节拍持续触发
            if (DiagSwitches.InitiativeForce) { ForceTick(); return; }
            // 旧 Frame 仍挂着时，按日节拍兜底（若 WorldAddDay 未触发，如读档当日）
            try
            {
                int cur = CurrentGameDay();
                if (cur == -1) return; // 取不到日历时静默（锚点复位由 DayRollGate 的 playerUnit 判据负责）
                // 10-21：同日到没到、跳变、宽限期，三道判定收进同一道门（与 OnWorldAddDay 共用）
                if (!DayRollGate(cur)) return;
                long __tpDay = TickProfiler.Begin();
                try { TryTriggerForDay(cur); }
                finally { TickProfiler.End("npcmon.day", __tpDay); }
            }
            catch (Exception e)
            {
                ModMain.P("[NpcInitiativeMonitor] OnUpdate tick error: " + e.Message);
            }
        }

        /// <summary>同意后自动开窗的帧级延迟执行（OnUpdate 每帧调一次；见 _pendingOpenUnit 注释）。
        /// 3 帧后开始尝试，失败则**每帧重试**至 5s 超时（旧版只试一次 → 退化成回复时才开窗）。</summary>
        private void TickPendingOpen()
        {
            if (_pendingOpenUnit == null) return;
            if (--_pendingOpenFrames > 0) return;
            var unit = _pendingOpenUnit;
            if (_pendingOpenTries == 0) _pendingOpenDeadline = Time.unscaledTime + PendingOpenTimeoutSec;
            _pendingOpenTries++;
            try
            {
                if (unit == null) { ResetPendingOpen(); return; }
                string name = NameOf(unit);
                if (ChatLauncher.OpenForUnit(unit))
                {
                    // ★ 10-17：这里**不再**调 `ChatLauncher.SetThinking(true)` ★
                    //   那句是直接写 `ChatWindow._busy`（绕过 presenter 的 `_busy` 字段），
                    //   而现在忙标由 `TurnRegistry` 统一驱动：
                    //     · 回合起点早在同意回调里就 `Begin` 了（见 `FireOne` 的 onOk）；
                    //     · `OpenForUnit` → `ChatPresenter.OpenForNpc` → `RefreshSoloBusy`
                    //       会按登记表把忙标点亮（这正是"首 token 前 4~13s 显示斟酌中"要的效果）。
                    //   留着它反而有害：两处各写各的会让 `presenter._busy` 与窗口标签**不一致**
                    //   （守卫看字段、玩家看标签），正是注释里点名过的"实机 bug 的最后一环"。
                    ModMain.P("[NpcInitiativeMonitor] 同意后已自动开窗（流式直落该窗）：name=" + name +
                              "（第 " + _pendingOpenTries + " 次尝试成功）");
                    ResetPendingOpen();
                    return;
                }
                // 未成功：多半是宿主创建闸撞上"我们自己那个确认窗实例还没销毁"（见字段区注释）。
                // 首次失败留一行现场，之后静默重试——每帧都打会把日志刷爆。
                if (!_pendingOpenWarned)
                {
                    _pendingOpenWarned = true;
                    ModMain.P("[NpcInitiativeMonitor] 同意后开窗第 1 次未成功（宿主创建被闸住，多为确认窗实例未销毁）" +
                              "→ 每帧重试至 " + PendingOpenTimeoutSec + "s：name=" + name);
                }
                if (Time.unscaledTime >= _pendingOpenDeadline)
                {
                    ModMain.P("[NpcInitiativeMonitor] 同意后自动开窗重试 " + _pendingOpenTries +
                              " 次仍未成功（回退：回复到达时 ContactDuty 分流②再弹）name=" + name);
                    ResetPendingOpen();
                }
            }
            catch (Exception e)
            {
                ModMain.P("[NpcInitiativeMonitor] TickPendingOpen: " + e.Message);
                ResetPendingOpen();
            }
        }

        /// <summary>清掉"同意后待开窗"的全部状态（成功/超时/异常/被顶替共用）。</summary>
        private void ResetPendingOpen()
        {
            _pendingOpenUnit = null;
            _pendingOpenFrames = 0;
            _pendingOpenTries = 0;
            _pendingOpenWarned = false;
            _pendingOpenDeadline = 0f;
        }

        /// <summary>游戏日事件（g.events.On WorldAddDay 调）。</summary>
        public void OnWorldAddDay(ETypeData e)
        {
            if (DiagSwitches.NoMonitor) return;   // 诊断开关：停监测器
            try
            {
                int cur = CurrentGameDay();
                if (cur == -1) { cur = _lastDay + 1; if (_lastDay == -1) cur = 0; }
                if (cur == _lastDay) return;
                // 10-21：事件路走**同一道门**（读档补表期游戏也会连发 WorldAddDay，
                //   11:57 局的 1383→1388 就是这么漏进来的——只闸 Frame 路等于没闸）
                if (!DayRollGate(cur)) return;
                // 耗时采样（TickProfiler）：换日 → 关系网收集（day.rel）；仅在关系网无人
                //   可派发时才做同格懒扫描（day.same，09-28 的懒化设计）
                long __tpDay = TickProfiler.Begin();
                try { TryTriggerForDay(cur); }
                finally { TickProfiler.End("npcmon.day", __tpDay); }
            }
            catch (Exception ex)
            {
                ModMain.P("[NpcInitiativeMonitor] OnWorldAddDay error: " + ex.Message);
            }
        }

        // ---------- 日触发 ----------

        private void TryTriggerForDay(int curDay)
        {
            _lastDay = curDay;
            if (!_enabled)
            {
                // 主动互动总开关关着（config initiative.enabled）→ 日节拍整条不走。
                // 只吼一次（每天一次太吵；重新打开后下次关闭会再吼）。
                // 注意：诊断强制路径（_diag_initiative_force.txt）不走这里，照旧可用。
                if (!_disabledLogged)
                {
                    _disabledLogged = true;
                    ModMain.P("[NpcInitiativeMonitor] 主动互动总开关已关闭（initiative.enabled=false）" +
                              "→ 日节拍不再触发；玩家主动对话/工具不受影响。要恢复：配置面板（对话窗 ⚙）打开开关，或改 config.json");
                }
                return;
            }

            // ── ⓪ 优先队列刷新（11-13）：**放在所有闸门之前、且不阻塞** ──
            //   为什么在闸门之前：`daily_chance` 可能直接把今天关掉，而那时我们仍然希望
            //   名单被更新一次（否则"今天没抽中"与"名单过期"会混在一起，下次排查时分不清）。
            //   为什么**不 await**：RPC 回调排在本线程的消息队列里，而本方法就在主线程 ——
            //   同步等会卡住换日那一刻（玩家推进剧情时最刺眼）。用旧名单的后果只是
            //   "这次某人没排到最前"，与没有本功能等价，可接受。
            RequestMailPending();

            // ── ① 闸门：总开关 → 能说闸 → 忙闲 → 全局日概率（分段埋点 day.gate）──
            string busy = "";
            long __tGate = TickProfiler.Begin();
            try
            {
                if (_ws == null) return;
                string speak = SpeechBlockReason();
                if (speak.Length > 0)
                {
                    // 一日一次，不刷屏；这行是"正常游玩时 NPC 从不主动"的第一现场证据
                    ModMain.P("[NpcInitiativeMonitor] 日节拍跳过：能说闸命中（原因：" + speak + "）day=" + curDay);
                    return;
                }
                // 忙碌不禁言语（两级闸）：只把 busy 交给 FireOne → 事件帧带 busy:true，
                // Python 端本回合只给只读工具（动作工具剔除），同格也不弹确认窗而直接传音。
                busy = BusyReason();

                // 全局日概率（0=关，100=必试；低好感减半在候选内按人再半）
                if (_dailyChance <= 0) return;
                if (_dailyChance < 100)
                {
                    // 全局骰：<dailyChance 才继续
                    if (UnityEngine.Random.Range(0, 100) >= _dailyChance) return;
                }
            }
            finally { TickProfiler.End("day.gate", __tGate); }

            var player = g.world.playerUnit;
            var contactIds = new HashSet<string>();
            var seen = new HashSet<string>();

            // ── ② 第 1 轮候选：关系网（①~④）+ 手动通讯录 ——便宜（数十人级）──
            List<WorldUnitBase> units;
            long __tRel = TickProfiler.Begin();
            try
            {
                units = new List<WorldUnitBase>();
                try { RelationNetwork.CollectKnownUnits(units, seen, contactIds); } catch { }
                CollectManualContacts(units, seen, contactIds);
            }
            finally { TickProfiler.End("day.rel", __tRel); }

            // ── ③ 预取 → 排序 → 逐个试（零 interop；一日只一人）──
            if (TryDispatch(ReadRows(units, contactIds, player), player, curDay, busy)) return;

            // ── ④ 第 2 轮（懒扫描，09-28）：同格陌生人 ——贵（O(全地图单位)，实测单次 227ms），
            //     只在"关系网一个都派不出去"时才做。同格陌生人几乎都是 0/负好感，在原
            //     "全局按好感降序"里本就排最后；关系网里有人可派发时他们几乎不可能中选。
            //     语义差异（09-28 用户拍板接受）：关系网优先于同格陌生人。
            long __tSame = TickProfiler.Begin();
            try
            {
                var fresh = new List<WorldUnitBase>();
                int scanned = RelationNetwork.CollectSameGridUnits(fresh, seen);
                ModMain.P("[NpcInitiativeMonitor] 同格懒扫描：全量 " + scanned + " 单位，同格新人 "
                          + fresh.Count + " 名（关系网无人可派发才走到这里）");
                if (fresh.Count == 0) return;
                units = fresh;
            }
            finally { TickProfiler.End("day.same", __tSame); }

            TryDispatch(ReadRows(units, contactIds, player), player, curDay, busy);
        }

        // ==================================================================== 换日候选：预取快照
        //
        // 09-28 卡顿/成本优化（用户拍板）：原实现"全量候选 → 排序 → 逐个试"里有三笔重复劳动：
        //   ① 排序比较器里现调 `SafeIntim` —— O(n·log n)×2 次 interop，同一个人被反复问价；
        //   ② 试循环又把 name/intim/sameGrid 各读一遍（与排序重复）；
        //   ③ 排序比较器用 `Random.Range(-1,2)` 当"同好感打散"，违反严格弱序契约
        //      （极端情况 `List.Sort` 抛 `InvalidOperationException: IComparer 不一致`）。
        // 现在：候选构建后每人恰好读一次（`ReadRows`）→ 排序只比 int → 试循环只查内存。
        // ====================================================================

        /// <summary>候选行的预取快照（排序与试循环共用；`Row` 是本工程自己的类，不是 IL2CPP 对象）。</summary>
        private sealed class Row
        {
            public WorldUnitBase Unit;
            public string Name = "";
            public string UnitId = "";
            public int Intim;
            public bool SameGrid;
            public bool InContacts;
            /// <summary>有未读捎话（11-13）：排序时排到前面。**只影响排序**，不豁免任何闸门。</summary>
            public bool HasMail;
        }

        /// <summary>预取：每个候选恰好一组 interop（名字/unitID/同格/好感）；空名者剔除（同旧行为）。
        /// 注意 player 由调用方传入 —— 换日一刻取一次，别在循环里反复读 `g.world.playerUnit`。</summary>
        /// ★ 11-13 起是**实例方法**（原 static）：要读 `_mailPending` 填 `HasMail`。
        ///   静态化本来只是为了"不碰状态"，而这里读的是只读快照 —— 影响可忽略。
        private List<Row> ReadRows(List<WorldUnitBase> units, HashSet<string> contactIds, WorldUnitBase player)
        {
            var rows = new List<Row>(units != null ? units.Count : 0);
            if (units == null) return rows;
            foreach (var u in units)
            {
                try
                {
                    if (u == null) continue;
                    var r = new Row();
                    r.Unit = u;
                    r.Name = NameOf(u);
                    if (string.IsNullOrEmpty(r.Name)) continue;
                    try { r.UnitId = u.data.unitData.unitID; } catch { }
                    // 死者剔除（10-15 待办③）：活人容器对死者不可解析，正常到不了这里；
                    // 防御性一行——ID 精准 O(1) miss，成本可忽略。FireOne 还有末道断言兜底。
                    if (UnitSnapshot.IsDead(r.UnitId, r.Name)) continue;
                    r.InContacts = !string.IsNullOrEmpty(r.UnitId) && contactIds != null && contactIds.Contains(r.UnitId);
                    r.SameGrid = player != null && UnitSnapshot.IsSameGrid(u, player);
                    r.Intim = SafeIntim(u);
                    // 优先队列（11-13）：只有**排序**用它，闸门一条都不动（见 _mailPending 注释）
                    r.HasMail = HasMailPending(r.Name);
                    rows.Add(r);
                }
                catch { }
            }
            return rows;
        }

        /// <summary>按好感降序排序；同好感**整段内**洗牌（等价于原"同好感随机打散"，但比较器是
        /// 严格弱序 —— 顺手修掉原 `Random.Range(-1, 2)` 的隐患）。比较对象是预取快照（零 interop）。</summary>
        private static void SortRows(List<Row> rows)
        {
            // 判据全部搬去 `InitiativeSortCore`（纯逻辑、可单测）：那里是"优先队列比好感高"
            // 这一条需求的**唯一实现**，而它出错不报错（理由见该文件头）。
            // 这里只做两件事：把两条排序键摊平成数组 → 按回来的下标重排 Row。
            var keys = new InitiativeSortKey[rows.Count];
            for (int i = 0; i < rows.Count; i++)
                keys[i] = new InitiativeSortKey(rows[i].Intim, rows[i].HasMail);
            var order = InitiativeSortCore.SortOrder(keys, (a, b) => UnityEngine.Random.Range(a, b + 1));
            var sorted = new List<Row>(rows.Count);
            for (int i = 0; i < order.Length; i++) sorted.Add(rows[order[i]]);
            rows.Clear();
            rows.AddRange(sorted);
        }

        /// <summary>从预取快照里挑一个派发（一日只一人）。排序与试循环全程零 interop。
        /// 判序与原实现逐条对齐：冷却 → 资格（通讯录 or 同格）→ 低好感减半 → 派发。</summary>
        private bool TryDispatch(List<Row> rows, WorldUnitBase player, int curDay, string busy)
        {
            if (rows == null || rows.Count == 0) return false;

            long __tSort = TickProfiler.Begin();
            try { SortRows(rows); }
            finally { TickProfiler.End("day.sort", __tSort); }

            long __tTry = TickProfiler.Begin();
            try
            {
                foreach (var r in rows)
                {
                    try
                    {
                        if (IsCoolingDown(r.Name, curDay)) continue;

                        // 通讯录门槛：非通讯录成员只有同格（当面）才能发起；通讯录成员任意位置可传音
                        if (!r.InContacts && !r.SameGrid) continue;   // 结构上不会发生，保险断言

                        // 低好感减半：陌生/仇人 50% 跳过（对齐 us_strings:2047）；负仇恨绝对值小也算低
                        if (_lowHalve && r.Intim < _lowThresh && UnityEngine.Random.value < 0.5f) continue;

                        // ★ 11-13 捎话优先（只管"说什么"，不管"能不能说"）★
                        //   走到这里说明：状态闸过了、日概率过了、冷却过了、资格过了、减半也过了
                        //   —— 与没有本功能时的判序**逐条相同**。只在"已经决定要派发这个人"之后，
                        //   把意图换成 `mail_relay`（让模型照着那句托付去说/去办）。
                        //   为什么优先于 PickIntent 的骰子：`PickIntent` 选的是"他今天的心情"
                        //   （问候/闲聊/思念/赠予…），而"有人托他办件事"是**具体事实** ——
                        //   心情可以随机，事实不行。有信时就把这一轮让给那件事。
                        //   为什么仍然调 PickIntent 的分支逻辑：不需要 —— 直接给键，
                        //   文案由 Python `format_initiative_message` 按 intent 分派。
                        string intent = r.HasMail
                            ? "mail_relay"
                            : PickIntent(r.Intim, r.SameGrid, busy.Length > 0);
                        // 空串 = 这一档**没有可用的动作键**（敌对 + 异地：远程池里没有敌对手段，
                        // 而同格的 attack 又做不了）。此时不派发、**也不落冷却** ——
                        // 落冷却会把"这次没发出任何东西"记成"已交互过"，白占这个 NPC 三天。
                        // 直接跳到下一个候选（下一个人可能同格，或不是敌对）。
                        if (string.IsNullOrEmpty(intent)) continue;
                        // reason 对两种意图含义不同（Python 侧按 intent 分派文案）：
                        //   · 普通意图 = "由头"（念及情谊/心中不忿…）
                        //   · mail_relay = **发信人真名**（舞台指令要靠它说清"谁托的你"）
                        string reason = intent == "mail_relay"
                            ? MailSenderOf(r.Name)
                            : BuildReason(r.Intim);

                        // 冷却先落：无论玩家同意与否，本次自主交互都视为已发生（README.md 附录二 G）
                        _cooldownReal[r.Name] = DateTime.Now;
                        _cooldownDay[r.Name] = curDay;

                        FireOne(r.Unit, player, r.Name, r.Intim, r.SameGrid, intent, reason, curDay, false, busy);
                        return true;   // 一日只一人
                    }
                    catch (Exception e)
                    {
                        ModMain.P("[NpcInitiativeMonitor] candidate skipped: " + e.Message);
                    }
                }
                return false;
            }
            finally { TickProfiler.End("day.try", __tTry); }
        }

        // ---------- 发起（日节拍与诊断强制模式共用，保证两条路径同源） ----------

        /// <summary>发起一次主动交互——只做派发，不判定任何闸门（闸门判定在各自调用方）。
        /// 抽出来是为让"日节拍"与"诊断强制"两条路径共享同一份 意图文案/确认窗/日志，
        /// 避免诊断路径悄悄偏离真实路径。curDay 仅供日志。
        ///
        /// busy（两级闸）：玩家正忙时 —— ① 同格**不弹确认窗**，降级为直接传音
        /// （弹窗=打扰，与"忙碌只言语"矛盾）；② 事件帧带 busy:true，Python 端本回合剔除动作工具。</summary>
        private void FireOne(WorldUnitBase wub, WorldUnitBase player, string name, int intim, bool sameGrid,
                             string intent, string reason, int curDay, bool forced, string busy)
        {
            // 末道断言：自主交互的语义恒为「NPC → 玩家」，目标永不可为玩家本人。
            // 候选集已在 BuildCandidates 末端剔除，这里是**与来源无关的兜底**——万一今后
            // 又冒出新的候选来源（或 ForceTick 直连），也绝不再出现"自己给自己传音"。
            if (UnitSnapshot.IsPlayerUnit(wub))
            {
                ModMain.P("[NpcInitiativeMonitor] 拒绝发起：目标是玩家自身（name=" + name +
                          "）——候选集过滤失守的兜底，请查候选来源");
                return;
            }
            // 末道断言（10-15 待办③）：**死者绝不自主开口**。现行候选源（活人容器）天然
            // 不含死者、ReadRows 也已剔除，这里是**与来源无关的兜底**——万一今后候选源
            // 混入死者（或 ForceTick 直连），也不该出现"坟头传音"。
            {
                string wid = "";
                try { wid = wub.data.unitData.unitID; } catch { }
                if (UnitSnapshot.IsDead(wid, name))
                {
                    ModMain.P("[NpcInitiativeMonitor] 拒绝发起：「" + name +
                              "」已身故（死亡账本命中）——死者不应出现在候选里，请查候选来源");
                    return;
                }
            }
            bool isBusy = busy.Length > 0;
            // 诊断开关：把本次强制触发**按异地判定**，用于可复现地观察"异地传音"那条路
            // （不弹确认窗 → 直发 → 收尾落分流③ → HUD 横幅 + 传钮红点）。只影响 forced 路径。
            bool forceRemoteDiag = forced && DiagSwitches.InitiativeForceForceRemote;
            if (forceRemoteDiag) sameGrid = false;
            bool noToolsDiag = forced && DiagSwitches.InitiativeForceNoTools;
            // ★ 10-16 用户拍板的矩阵：决定"要不要先弹确认窗 + 预开对话窗"（下称 needConsent）
            //
            //   忙            → 不问（直发，走未读+横幅）；动作支路也已被 PickIntent 跳掉
            //   闲 + 异地 + 言语 → 不问（直发，走未读+横幅）—— 保住"异地传音不打扰"
            //   闲 + 异地 + 动作 → **问**（动作会弹原生界面，无预警地弹才是真打扰）
            //   闲 + 同格 + 任意 → 问（现行语义不变）
            //
            // 为什么异地动作要问：原先异地一律直发，模型可能直接调 `lun_dao`/`economy_item`
            // → **工具原生界面在屏幕上空无一物时弹出**，玩家不知道发生了什么（真机 00:21：
            // "NPC 就在玩家旁边论道了一场，玩家屏幕上什么都没发生"，见 ActionWatcher 头注释）。
            // 先问一句、并把对话窗预开，工具界面弹出时才有上下文。
            //
            // 语义边界（用户确认）：异地「不同意」与同格「不同意」**完全同构** ——
            // 都不发 npc_initiative 事件 ⇒ 该回合根本不开始 ⇒ **不存在"消息被丢掉"**
            // （主动开口事件是回合的**起点**，不是一条已经在路上的消息）。
            bool actionIntent = IsActionIntent(intent);
            bool needConsent = !isBusy && (sameGrid || actionIntent);

            // 触发时刻的判定存下来：收尾分流只认它，不再用"回复到达时"的状态重判一次
            // （否则异地传音的 NPC 走过来就会变成"没同意就当面开窗"）。
            // ★ 10-16 语义扩展：从"同格且不忙"改成"**本回合走过确认窗**" —— 异地动作现在也走，
            //   所以它同样有权在回复到达时当面弹出（分流②）。这是"开过窗"而不是"能不能当面"
            //   的问题；不这么改，异地动作的回复会被登记成未读（玩家明明已经点过同意）。
            RememberTriggerPop(name, needConsent, false);
            // 标记"该 NPC 有一个自主开口回合在路上"：本回合若真的执行了动作工具，ActionWatcher
            // 会在**动作完成那一刻**按当时的同格/忙闲决定是否把对话 UI 推给玩家。
            // 只对自主开口回合生效——玩家自己发起的对话，窗本来就在玩家眼前。
            // 第二参数（第三版）= 本回合是否**已经**因"确认窗 + 点同意"开过窗：
            // 走 needConsent 的路径点同意即开窗 → 动作完成不再补开；直发路径传 false
            // → 保留"动作完成即开窗"。（忙 不会到这里带动作意图：PickIntent 已跳掉动作支路）
            ActionWatcher.NoteInitiativeTurn(name, needConsent);
            if (needConsent)
            {
                // 当面/异地动作：先弹确认窗（立绘照常），玩家同意才生成对话——婉拒=无声中断不浪费回合，
                // 冷却已前置所以婉拒同样计入（用户拍板的语义）。
                //
                // 文案分两支（异地动作别再用"当面"口吻）：两条都刻意**不承诺具体动作** ——
                // 点同意那一刻模型还没跑，谁也不知道它到底会不会/能不能调工具；
                // 把话说死（"X 想与你论道"）会造成"说好的论道呢"的承诺错配。
                string confirmText = sameGrid
                    ? name + " 希望向你发出互动"
                    : name + " 自远方传来神识，似有事相商";
                string npcName = name;
                ShowDramaService.ShowConfirmSimple(
                    ModIds.DramaInitiativeBase,
                    player,     // left = 屏幕左位 = 玩家（★09-12 修正★：原先传的是 wub，
                                //             立绘左右与其余四个工具确认窗相反 → 用户实机看到
                                //             "右侧立绘是玩家"；语义见 ShowConfirmSimple 注释）
                    wub,        // right = 屏幕右位 = 对方 NPC
                    confirmText,
                    "同意", "不同意",
                    onOk: () =>
                    {
                        // ① 先发事件：让回合尽早开始（LLM 首 token 要 4~13s，不占在开窗上）；
                        // ② 再排"延迟几帧开窗"：绕开剧情窗关闭窗口期，开完就吃流式。
                        // unitId：**手里就是真身**（`wub`）——主动传音这条路不经过 open_chat，
                        // 不带它就等于让 Python 按名猜（09-28 实锤："未找到发起方 管星贤"）。
                        _ws.SendInitiative(npcName, intent, reason, consented: true, debug: forced, busy: isBusy,
                                           speechOnly: noToolsDiag, unitId: DramaNativeClaim.UidOf(wub),
                                           sameGrid: sameGrid);
                        // ★ 10-17：**回合起点**（玩家刚点下同意 ⇒ 这一轮真的开始了）——
                        //   登记进 `TurnRegistry`，忙标由它驱动（见该类文件头）。
                        //   位置刻意在同意回调**之内**：确认窗弹着等玩家表态时**不算忙**
                        //   （那时还没有回合在跑，见本方法头的矩阵注释）。
                        try { TurnRegistry.Begin(npcName, "自主交互：玩家已同意"); } catch { }
                        ModMain.P($"[NpcInitiativeMonitor] consented trigger name={npcName} intent={intent} intim={intim}");
                        ResetPendingOpen();   // 清上一次的残留计数（重试次数/告警位）
                        _pendingOpenUnit = wub;
                        _pendingOpenFrames = PendingOpenDelayFrames;
                        if (forced) MarkForceSpent("玩家已同意");
                    },
                    onNo: () =>
                    {
                        ModMain.P($"[NpcInitiativeMonitor] declined by player: name={npcName} intent={intent}");
                        // 婉拒也停表：玩家已经明确表态过这一轮，再每 20s 弹一次纯属打扰；
                        // 想继续测就改一下开关文件或写 repeat=1。
                        if (forced) MarkForceSpent("玩家已婉拒");
                    });
            }
            else
            {
                // 异地，或同格但玩家忙（降级传音：不弹窗、只走未读+横幅）
                // unitId：同上面那条（手里就是真身 `wub`）—— 主动传音的主路径，必须带。
                _ws.SendInitiative(name, intent, reason, debug: forced, busy: isBusy, speechOnly: noToolsDiag,
                                   unitId: DramaNativeClaim.UidOf(wub), sameGrid: sameGrid);
                // ★ 10-17：**回合起点**（直发路径：事件发出即回合开始，没有"玩家点击"这个收口点）。
                //   与上面同意路径同源 —— 两条路都必须登记，否则异地传音的忙标永远不亮。
                try { TurnRegistry.Begin(name, busy.Length > 0 ? "自主交互：忙时直发" : "自主交互：异地直发"); } catch { }
                // 这条路径没有"玩家点击"这个收口点，发出即算一次（一次即停：不再连发）
                if (forced) MarkForceSpent(busy.Length > 0 ? "已降级直发传音（玩家忙）" : "已发出传音（异地）");
            }
            string tail = isBusy ? (" busy=True（" + busy + "）") : "";
            if (forced)
            {
                ModMain.P($"[NpcInitiativeMonitor] forced trigger（诊断强制）day={curDay} name={name} intent={intent} intim={intim} sameGrid={sameGrid} interval={DiagSwitches.InitiativeForceInterval}s{tail}" +
                          (forceRemoteDiag ? " forceRemote=ON（按异地判定 → 应落分流③：横幅+红点）" : "") +
                          (noToolsDiag ? " noTools=ON（本回合一个工具都不调）" : ""));
            }
            else
            {
                ModMain.P($"[NpcInitiativeMonitor] trigger day={curDay} name={name} intent={intent} intim={intim} sameGrid={sameGrid} dailyChance={_dailyChance}{tail}");
            }
        }

        // ---------- 诊断强制模式（_diag_initiative_force.txt） ----------

        /// <summary>强制模式节拍：按现实秒累计（unscaled，不受暂停/倍速影响），到点触发一次。</summary>
        private void ForceTick()
        {
            try
            {
                // 一次即停：成功发起过一次（同意/婉拒/已发出传音）后就停表，
                // 不再每 interval 秒弹同格确认窗打扰游玩。重新武装 = 改一下开关文件
                // （内容/保存时间变化 → mtime 变化即视为"再来一次"）；写 repeat=1 恢复连发。
                if (_forceSpent && !DiagSwitches.InitiativeForceRepeat)
                {
                    long stamp = DiagSwitches.InitiativeForceStamp;
                    if (stamp == _forceSpentStamp) { _forceAcc = 0f; return; }
                    _forceSpent = false;
                    ModMain.P("[NpcInitiativeMonitor] 诊断强制：开关文件已变更 → 重新武装（可再来一次）");
                }
                _forceAcc += Time.unscaledDeltaTime;
                float interval = DiagSwitches.InitiativeForceInterval;
                if (interval < 1f) interval = 1f;
                if (_forceAcc < interval) return;
                _forceAcc = 0f;
                TryTriggerForced();
            }
            catch (Exception e)
            {
                ModMain.P("[NpcInitiativeMonitor] force tick error: " + e.Message);
            }
        }

        /// <summary>「一次即停」落表：记录本次收口原因 + 当时的开关文件时间戳
        /// （之后只有 mtime 变化才重新武装）。仅对诊断强制路径调用。</summary>
        private void MarkForceSpent(string why)
        {
            if (_forceSpent) return;
            _forceSpent = true;
            _forceSpentStamp = DiagSwitches.InitiativeForceStamp;
            ModMain.P("[NpcInitiativeMonitor] 诊断强制：本次已收口（" + why + "）→ **停表**，不再弹。" +
                      "要再来一次：改一下 _diag_initiative_force.txt（保存即重新武装）；" +
                      "要连发：文件里写 repeat=1。");
        }

        /// <summary>强制触发一次：只放宽「时间」四闸（日节拍/同日夜守卫/每日概率/双冷却），
        /// 其余全部走真实链路——候选集与门槛照旧（BuildCandidates + 通讯录/同格判定），
        /// 意图默认按亲密度真实派发（intent= 可指定），同格仍弹确认窗。
        /// 不写冷却表：关掉开关后不留痕，不影响正常游玩的判定。</summary>
        private void TryTriggerForced()
        {
            if (_ws == null) return;
            int curDay = CurrentGameDay();
            // 能说闸（未进世界/死亡窗）→ 丢弃；忙碌不拦言语（两级闸），只把 busy 带到事件里
            string speak = SpeechBlockReason();
            string busy = BusyReason();
            // 层快照：拦与放行都打，用来核对层判据在真实状态下的取值
            string layers = LayerSnapshot();
            if (speak.Length > 0 && !DiagSwitches.InitiativeForceIgnoreState)
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：能说闸命中（原因：" + speak +
                          "）——判定误报时可在 _diag_initiative_force.txt 写 ignore_state=1 强制放行（热生效）" +
                          "｜层快照：" + layers);
                return;
            }
            if (speak.Length > 0)
            {
                // 放行了也要留证：这条原因就是修闸的依据
                ModMain.P("[NpcInitiativeMonitor] forced trigger：能说闸命中（原因：" + speak +
                          "）但 ignore_state=1 放行｜层快照：" + layers);
            }
            else
            {
                ModMain.P("[NpcInitiativeMonitor] 层快照（能说闸通过" +
                          (busy.Length > 0 ? "；玩家忙：" + busy : "") + "）：" + layers);
            }

            var contactIds = new HashSet<string>();
            var candidates = BuildCandidates(contactIds, true);
            if (candidates.Count == 0)
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：候选集为空（关系网/通讯录/同格都没人）");
                return;
            }
            var player = g.world.playerUnit;

            // 按好感降序（同好感不随机打散：强制模式要可复现）
            candidates.Sort((a, b) => SafeIntim(b).CompareTo(SafeIntim(a)));

            string wantName = DiagSwitches.InitiativeForceName;
            WorldUnitBase target = null;
            if (!string.IsNullOrEmpty(wantName))
            {
                foreach (var wub in candidates)
                {
                    if (NameOf(wub) == wantName) { target = wub; break; }
                }
                if (target == null)
                {
                    ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：候选集里没有 name=" + wantName +
                              "（非通讯录成员必须与玩家同格才会进候选）");
                    return;
                }
            }
            else
            {
                if (DiagSwitches.InitiativeForceRandom)
                {
                    // pick=random：在候选里随机取一人（看"不同 NPC 都会来找我"用；代价是每次新建一个
                    // session/agent，且同格者会弹确认窗）。先收有效姓名成列表，避免随机到空名。
                    var valid = new List<WorldUnitBase>();
                    foreach (var wub in candidates)
                    {
                        if (!string.IsNullOrEmpty(NameOf(wub))) valid.Add(wub);
                    }
                    if (valid.Count == 0)
                    {
                        ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：候选集里取不到有效姓名");
                        return;
                    }
                    target = valid[UnityEngine.Random.Range(0, valid.Count)];
                }
                else
                {
                    foreach (var wub in candidates)
                    {
                        if (!string.IsNullOrEmpty(NameOf(wub))) { target = wub; break; }
                    }
                    if (target == null)
                    {
                        ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：候选集里取不到有效姓名");
                        return;
                    }
                }
            }

            string name = NameOf(target);
            // 与日节拍同款门槛断言（候选集本已保证，这里只是"两条路径判定一致"的显式对齐）
            string unitId = null;
            try { unitId = target.data.unitData.unitID; } catch { }
            bool inContacts = unitId != null && contactIds.Contains(unitId);
            bool sameGrid = player != null && UnitSnapshot.IsSameGrid(target, player);
            if (!inContacts && !sameGrid)
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：name=" + name +
                          " 既不在通讯录也不与玩家同格（自主交互不允许异地陌生人传音）");
                return;
            }

            string intentOverride = DiagSwitches.InitiativeForceIntent;
            if (!string.IsNullOrEmpty(intentOverride) && Array.IndexOf(AllIntents, intentOverride) < 0)
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger: 未知 intent=" + intentOverride + "（改用按亲密度派发）");
                intentOverride = null;
            }
            int intim = SafeIntim(target);
            // 与日节拍同源（同格/忙闲都要传：诊断路径也必须走"异地不选同格专属动作"这条规则，
            // 否则 forced 复现出来的行为与真实路径不一致，验收结论不可信）
            string intent = string.IsNullOrEmpty(intentOverride)
                ? PickIntent(intim, sameGrid, busy.Length > 0)
                : intentOverride;
            // 空串 = 这一档没有可用动作键（敌对 + 异地）。诊断路径直接说清楚，别静默不动。
            if (string.IsNullOrEmpty(intent))
            {
                ModMain.P("[NpcInitiativeMonitor] forced trigger 跳过：name=" + name +
                          " 本档无可派发意图（敌对 + 异地：远程池无敌对手段，同格攻击又做不了）" +
                          " → 可用 intent= 指定一个，或走到同格再试");
                return;
            }
            string reason = BuildReason(intim);

            FireOne(target, player, name, intim, sameGrid, intent, reason, curDay, true, busy);
        }

        // ---------- 游戏日历 ----------

        // internal：ModMain/UI 的进世界探测（AbPanelProber.OnFrame）复用本判据——
        // 能取到游戏日历 = 存档世界已加载、登录 UI 早已完成
        internal static int CurrentGameDay()
        {
            try
            {
                var run = g.world.run;
                if (run != null)
                {
                    int m = 0, d = 0;
                    try { m = (int)run.roundMonth; } catch { }
                    try { d = (int)run.roundDay; } catch { }
                    // roundMonth 为总月数，roundDay 为当月日（0起），30天≈1月近似
                    return m * 30 + d;
                }
            }
            catch { }
            return -1;
        }

        /// <summary>
        /// 日节拍唯一入口闸（10-21，Frame 兜底与 WorldAddDay 事件**共用**）。返回 true 才允许
        /// 走「新的一天」评估。三道判定按代价从低到高排：
        ///   ① 锚点：playerUnit 不在（菜单/读档中）→ 复位锚点与日序（下次落地重新计宽限期；
        ///      顺带修掉"同进程读旧档 → cur 永远小于残留 _lastDay → 日节拍永久停摆"）；
        ///   ② 闸 A 宽限期：落地后 30s 内整段不走（压掉读档补表期的所有跳变，含 +1 碎步）；
        ///   ③ 闸 B 跳变守卫：一跳超过 1 日或倒退 ⇒ 视为噪声，**静默对齐、不掷骰**；
        ///   ④ 同日到过 → 不重跑。
        /// 只在主线程调用（两个调用方都是帧回调/事件回调）。
        /// </summary>
        private bool DayRollGate(int cur)
        {
            // ① 锚点判据用 playerUnit，**不用**"日历可读"——读档途中日历就能读出垃圾值（实证 0/42780）
            WorldUnitBase pu = null;
            try { pu = g.world != null ? g.world.playerUnit : null; } catch { }
            if (pu == null)
            {
                _worldAnchorAt = -1f;
                _lastDay = -1;
                _graceLogged = false;
                return false;
            }
            float now = Time.unscaledTime;
            if (_worldAnchorAt < 0f) { _worldAnchorAt = now; _graceLogged = false; }
            // ② 闸 A：进世界宽限期。注意**不动 _lastDay**——到期后 _lastDay 仍是 -1，
            //    第一帧自然走"首次评估"路径补掷一次日概率骰（保留每日一机的原语义）。
            if (now - _worldAnchorAt < WorldEntryGraceSec) return false;
            if (!_graceLogged)
            {
                _graceLogged = true;
                ModMain.P("[NpcInitiativeMonitor] 进世界宽限期结束（" + (int)WorldEntryGraceSec +
                          "s）→ 日节拍恢复，按当前日补评一次");
            }
            // ③ 闸 B：跳变（>1 日前跳或任意倒退）→ 对齐不掷骰
            if (_lastDay != -1 && (cur - _lastDay > 1 || cur < _lastDay))
            {
                ModMain.P("[NpcInitiativeMonitor] 日历跳变 " + _lastDay + "→" + cur +
                          "（>1日或倒退）→ 视为噪声：只对齐，不掷骰");
                _lastDay = cur;
                return false;
            }
            // ④ 同日不重跑
            return cur != _lastDay;
        }

        /// <summary>
        /// 当前**账面月**（1年1月=1、2年1月=13；与 `DataUnitLog.LogItemData.month` 同标度，
        /// 换算见 `UnitSnapshot.CnYearMonth`）。日历不可用（未进世界 / 世界未加载）返回 -1。
        ///
        /// 标度实证：`roundMonth` 是**0 起总月数** —— 两处参照 mod 独立写法都印证
        /// （`roundMonth/12+1` 年 + `roundMonth%12+1` 月；`ConvertToYearsMonths(roundMonth+1)`），
        /// 且 `roundDay` 同样是 0 起（各处以 `roundDay+1` 当日）。故账面月 = `roundMonth + 1`。
        /// IL2CPP 反编只读得到 `WorldRunMgr{ roundMonth, roundDay, roundDayResidue, roundDayMax }`
        /// ——**没有 roundYear**，年必须由总月数除出来，别去找不存在的字段。
        /// </summary>
        internal static int CurrentAccountMonth()
        {
            try
            {
                var run = g.world.run;
                if (run == null) return -1;
                int m = 0;
                try { m = (int)run.roundMonth; } catch { return -1; }
                return m + 1;                      // 0 起总月数 → 账面月
            }
            catch { return -1; }
        }

        /// <summary>
        /// 当月**日**（1 起，与参照 mod 的 `roundDay + 1` 一致）；日历取不到返回 -1。
        /// `roundDay` 是 0 起的**月内**日（`roundDayMax` 为当月天数，`CurrentGameDay()` 的
        /// `roundMonth*30 + roundDay` 近似即建立在此）。
        /// </summary>
        internal static int CurrentRoundDay()
        {
            try
            {
                var run = g.world.run;
                if (run == null) return -1;
                int d = 0;
                try { d = (int)run.roundDay; } catch { return -1; }
                return d + 1;                      // 0 起 → 1 起
            }
            catch { return -1; }
        }

        /// <summary>
        /// 当前游戏日期文本，形如 `1年1月3日`；日历取不到返回**空串**。
        /// **L1 的 `raw.now.text` 与消息时间戳共用本函数**（经 `UnitSnapshot.CnDate`），
        /// 保证模型在「当前时间」段与消息前缀里看到的是同一个串。
        /// </summary>
        internal static string CurrentDateText()
        {
            return UnitSnapshot.CnDate(CurrentAccountMonth(), CurrentRoundDay());
        }

        /// <summary>
        /// 玩家消息时间戳标签，形如 `[1年1月3日]`；日历取不到时返回**空串**。
        ///
        /// 调用方必须把空串当"不打时间戳"正常放行 —— 时间戳是锦上添花，绝不能因为读不到
        /// 日历就把玩家的话卡住不发（世界未加载/存档切换的瞬间就可能读不到）。
        ///
        /// 由 `ChatPresenter.OnSubmitRequested` 拼在玩家消息**最前面**再进 WS：NPC 此前完全
        /// 感知不到时间，账本带上它之后才能说出"上次见你是三月前"。
        /// </summary>
        internal static string CurrentTimeLabel()
        {
            string d = CurrentDateText();
            return d.Length > 0 ? "[" + d + "]" : "";
        }

        // ---------- 状态闸（**判据已搬到 `state/`，这里只剩转发**） ----------
        //
        // 为什么保留同名转发、而不是把 24 个文件 73 处调用全改一遍：
        //   ① 那些调用问的本来就是"现在什么状态"，**语义没变**，只是判据换了住址；
        //   ② 改 73 处引用是纯风险（其中 5 个文件在禁改清单里），没有收益；
        //   ③ 转发函数本身是**活文档**：看这一节就知道全仓的"状态"入口有哪些。
        //
        // 判据现在的归属（三层，边界由 `tests/test_state_module_boundary.py` 钉住）：
        //   · `state/WorldStateFacts` —— 只读世界电平（唯一碰 g.world / g.ui 的地方）；
        //   · `state/WorldStateCore`  —— 纯逻辑，四个视角的组合（零 Unity，有单测）；
        //   · `state/WorldStateGate`  —— 组装快照 + 注入剧情标记 + 对外入口。
        //
        // ⚠ 别在这里重新实现任何判据 —— 一旦这里出现第二份组合逻辑，
        //   就回到了"同一个忙字三份口径"的老路上（那正是 10-25 这次重构要消灭的东西）。

        /// <summary>能不能开口（非空 = 本次主动互动触发丢弃）。→ `WorldStateGate.SpeechBlockReason`</summary>
        private static string SpeechBlockReason() => WorldStateGate.SpeechBlockReason();

        /// <summary>能不能发起动作（非空 = 玩家忙）。→ `WorldStateGate.BusyReason`</summary>
        private static string BusyReason() => WorldStateGate.BusyReason();

        /// <summary>诊断用：只要能做动作闸的原因串（空=不忙）。→ `WorldStateGate.BusyReasonText`</summary>
        internal static string BusyReasonText() => WorldStateGate.BusyReasonText();

        /// <summary>别弹窗打扰（能说闸 ∪ 能做动作闸）。→ `WorldStateGate.IsPlayerBusy`</summary>
        internal static bool IsPlayerBusy() => WorldStateGate.IsPlayerBusy();

        /// <summary>让步判据（排队器用）。→ `WorldStateGate.YieldReason`</summary>
        internal static string YieldReason() => WorldStateGate.YieldReason();

        /// <summary>此刻在不在打架（战斗位取或）。→ `WorldStateFacts.BattleActive`</summary>
        internal static bool BattleActive() => WorldStateFacts.BattleActive();

        /// <summary>进战斗预告窗是否显示。→ `WorldStateFacts.BattlePreShowing`</summary>
        internal static bool BattlePreShowing() => WorldStateFacts.BattlePreShowing();

        /// <summary>剧情窗判据（唯一实现）。→ `WorldStateFacts.DramaWindowReason`</summary>
        internal static string DramaWindowName() => WorldStateFacts.DramaWindowReason();

        /// <summary>剧情窗是否正显示（`DramaWindowName` 的布尔形态）。</summary>
        internal static bool IsDramaWindowShowing() => DramaWindowName().Length > 0;

        /// <summary>游戏动作界面是否在层顶（交接证据）。→ `WorldStateFacts.ActionShowing`</summary>
        internal static bool ActionShowing() => WorldStateFacts.ActionShowing();

        /// <summary>游戏动作界面是否存在（不看层顶）。→ `WorldStateFacts.ActionActive`</summary>
        internal static bool ActionActive() => WorldStateFacts.ActionActive();

        /// <summary>UI 层顶名字（定标探针用）。→ `WorldStateFacts.LayerTopNameOfUi`</summary>
        internal static string LayerTopNameOfUi() => WorldStateFacts.LayerTopNameOfUi();

        /// <summary>8 层逐层快照（诊断用）。→ `WorldStateFacts.LayerSnapshot`</summary>
        internal static string LayerSnapshot() => WorldStateFacts.LayerSnapshot();

        /// <summary>UI 实例是否真的显示着（存在 ≠ 可见）。→ `WorldStateFacts.UiVisible`</summary>
        internal static bool UiVisible(UnityEngine.MonoBehaviour uiObj) => WorldStateFacts.UiVisible(uiObj);

        // ---------- 候选（RelationNetwork 关系记录全集 + 手动好友 + 同格陌生人） ----------

        /// <summary>
        /// 候选 = ①关系十容器+好友 ②仇人簿 ③关系记录簿(GetAllGoodRelationUnitID 含敌)
        ///       ④手动通讯录好友 ⑤同格所有人（陌生人当面可搭话）。
        /// contactIds 回填"通讯录成员"来源的 unitID（①~④）；⑤同格陌生人不记。
        /// 门槛：非通讯录成员必须与玩家同格才可发起（当面），异地只有通讯录成员能传音。
        ///
        /// dump=true（诊断强制模式）额外打印候选快照，用于肉眼确认玩家自身是否混入。
        /// </summary>
        private List<WorldUnitBase> BuildCandidates(HashSet<string> contactIds, bool dump)
        {
            var list = new List<WorldUnitBase>();
            var seen = new HashSet<string>();
            try { RelationNetwork.CollectKnownUnits(list, seen, contactIds); } catch { }
            CollectManualContacts(list, seen, contactIds);
            // ⑤ 同格所有人（含陌生人）——**诊断强制路径**仍要完整候选（dump 快照要能看到他们）；
            // 日路径（TryTriggerForDay）走懒扫描，不经过这里（09-28）。
            try { RelationNetwork.CollectSameGridUnits(list, seen); } catch { }

            string pid = UnitSnapshot.PlayerUnitId();
            if (dump) ModMain.P("[NpcInitiativeMonitor] 候选快照（玩家=" + (pid.Length > 0 ? pid : "?") + "）：" + DumpCandidates(list));
            DropPlayerSelf(list);
            return list;
        }

        /// <summary>④ 手动通讯录好友（镜像来自 ContactPresenter，真相在 Python contacts.json）。
        /// 09-28 从 `BuildCandidates` 拆出：日路径的懒扫描第 1 轮只跑"关系网 + 手动"这一半。</summary>
        private static void CollectManualContacts(List<WorldUnitBase> list, HashSet<string> seen, HashSet<string> contactIds)
        {
            try
            {
                var manual = ContactStore.ManualContactNames;   // 静态镜像：与通讯录面板实例解耦（09-10 定案）
                if (manual != null)
                {
                    foreach (var nm in manual)
                    {
                        if (string.IsNullOrEmpty(nm)) continue;
                        // ★ 先精确、后按名。这一处以前只有 `UnitLookup.Resolve(nm)` —— 按名猜，
                        //   猜错的后果是**"你没加过的那个同名者会主动来找你说话"**，而你真正
                        //   加的那个永远沉默（症状极难归因）。现在 contacts.json 里存着
                        //   精确 unit_id（NPC 面板点「加好友」那一刻从真身读到），先按它查。
                        WorldUnitBase u = null;
                        string storedId = ContactStore.ManualUnitId(nm);
                        if (!string.IsNullOrEmpty(storedId))
                        {
                            List<UnitLookup.Candidate> _;
                            u = UnitLookup.ResolveEx(nm, storedId, out _);
                            if (u == null)
                                ModMain.P("[NpcInitiativeMonitor] 手动好友精确 unitID 失效（" + nm +
                                          " → " + storedId + "），回落按名解析（可能撞重名）");
                        }
                        if (u == null) u = UnitLookup.Resolve(nm);
                        if (u == null) continue;
                        // 玩家真名被当成"好友"加进来时（自加/误加），这里同样要挡掉
                        if (UnitSnapshot.IsPlayerUnit(u))
                        {
                            ModMain.P("[NpcInitiativeMonitor] 通讯录条目命中玩家自身，跳过：" + nm);
                            continue;
                        }
                        string id = null;
                        try { id = u.data.unitData.unitID; } catch { }
                        if (string.IsNullOrEmpty(id) || !seen.Add(id)) continue;
                        string name = null;
                        try { name = u.data.unitData.propertyData.GetName(); } catch { }
                        if (string.IsNullOrEmpty(name)) continue;
                        list.Add(u);
                        contactIds.Add(id);
                    }
                }
            }
            catch (Exception e) { ModMain.P("[NpcInitiativeMonitor] manual contacts: " + e.Message); }
        }

        /// <summary>末端统一剔除玩家自身（09-28 从 `BuildCandidates` 原样搬出；与候选来源无关，
        /// 今后新增来源也自动安全）。玩家在游戏里就是一个 WorldUnitBase，`GetUnits(true)` 会把他
        /// 一并返回；而他与"自己"必然同格、好感恒 0，非通讯录也能过"同格才可发起"的门槛，
        /// 于是变成"自己给自己传音"（当日日志：forced trigger name=缪嘉歆 = 玩家真名）。</summary>
        private static void DropPlayerSelf(List<WorldUnitBase> list)
        {
            string pid = UnitSnapshot.PlayerUnitId();
            for (int i = list.Count - 1; i >= 0; i--)
            {
                if (!UnitSnapshot.IsPlayerUnit(list[i])) continue;
                ModMain.P("[NpcInitiativeMonitor] 候选集剔除玩家自身：" + NameOf(list[i]) +
                          "(" + (pid.Length > 0 ? pid : "?") + ")");
                list.RemoveAt(i);
            }
        }

        /// <summary>候选快照（诊断强制模式打印；上限 12 人，超出只报总数）。主线程。</summary>
        private static string DumpCandidates(List<WorldUnitBase> list)
        {
            var sb = new System.Text.StringBuilder();
            int cap = Math.Min(list.Count, 12);
            for (int i = 0; i < cap; i++)
            {
                if (i > 0) sb.Append(" ｜ ");
                var u = list[i];
                sb.Append(NameOf(u)).Append("(好感").Append(SafeIntim(u));
                try { if (UnitSnapshot.IsSameGrid(u, g.world.playerUnit)) sb.Append(",同格"); } catch { }
                if (UnitSnapshot.IsPlayerUnit(u)) sb.Append(",★玩家自身");
                sb.Append(")");
            }
            if (list.Count == 0) sb.Append("（空）");
            else if (list.Count > cap) sb.Append(" …等共").Append(list.Count).Append("人");
            return sb.ToString();
        }

        private bool IsCoolingDown(string name, int curDay)
        {
            // 现实冷却
            if (_cooldownReal.TryGetValue(name, out var lastReal))
            {
                if ((DateTime.Now - lastReal).TotalSeconds < _npcCooldownRealSec)
                    return true;
            }
            // 游戏日冷却
            if (_cooldownDay.TryGetValue(name, out var lastDay))
            {
                if (curDay - lastDay < _npcCooldownDays)
                    return true;
            }
            return false;
        }

        /// <summary>取姓名（IL2CPP 取名字段链可能抛，统一吞掉返回空串）。</summary>
        private static string NameOf(WorldUnitBase wub)
        {
            try { return wub == null ? "" : (wub.data.unitData.propertyData.GetName() ?? ""); }
            catch { return ""; }
        }

        private static int SafeIntim(WorldUnitBase wub)
        {
            try { return wub.data.unitData.relationData.GetIntim(g.world.playerUnit); }
            catch { return 0; }
        }

        /// <summary>挑一个意图键。**动作支路与言语支路各自独立掷骰**。
        ///
        /// 三个入参都是**已经算好的、零额外成本**的量，不是"真实输入"（不读背包/境界/名分）：
        ///   · `intim`    —— `Row` 预取时已读（`ReadRows` 注释："每人恰好一组 interop"）；
        ///   · `sameGrid` —— `Row.SameGrid`，同上；
        ///   · `busy`     —— `TryDispatch` 的入参。
        ///
        /// ★ 为什么不给 busy 就出错（静默）★
        /// 忙时动作工具会被执行层拦下（`dialogue_agent._execute_tool_calls`），而**工具表本身
        /// 从不裁剪**（前缀缓存）⇒ 模型看得到、也会去调、然后拿到一条 error。后果不是报错，
        /// 而是 NPC 对玩家许诺"改日把此物赠你"这类**无法兑现**的话（没有任何机制在未来兑现）。
        /// 所以忙时**整条动作支路跳掉**，决不给它这个念头。
        ///
        /// ★ 异地池 ⊂ 同格池（单调包含）★
        /// 远程可用的动作（赠予/论道/邀约）在同格下**同样合法**，反之不成立
        /// （切磋/攻击异地必 `Fail`）。所以"按触发时是否同格收紧"是**安全方向**：
        /// 触发时异地而执行时已同格 → 仍合法（只是本可多选几个）；
        /// 唯一的坏方向是"触发时同格、执行时已异地"，那由工具自己的 `SameGrid` 校验兜底
        /// （返回明确 error，`tool_usage.txt` 已有"error → 讲原因给替代方案"的处置规则）。
        /// </summary>
        private static string PickIntent(int intim, bool sameGrid, bool busy)
        {
            // ── ① 动作支路（概率 ActionChance）──
            //
            // ★ 为什么这里是两次独立掷骰，以及概率为什么**恰好**是对的 ★
            // 结构是"早期返回"：命中动作支路就直接 return（走不到②）；没命中才落到②。
            //   P(动作) = ActionChance = 0.25
            //   P(落到②) = 1 − ActionChance = 0.75
            // ② 内部再掷的那枚骰子只决定**言语池的分档**（`≥120` 亲密键 vs 正向键），
            // 与①不是同一枚 —— 这一点是必要的，否则既有分布会被改写：
            //   若②复用①的值，写成 `if (rnd < 0.25) 动作; else if (≥120 && rnd < 0.4) 亲密;`
            //   则 P(亲密) = P(0.25 ≤ rnd < 0.4) = **15%**，而不是刻意保留的 20%；
            //   P(正向) 则是 `≥120` 时的 60%。整条言语分布被"动作支路"这个新功能顺手改了。
            //   独立掷骰 ⇒ 动作支路对言语支路是干净的 25% 抽头，②内部 40/60 的比例原封不动。
            //
            // 实测分布（400k 次采样，种子固定）：
            //   0~119 同格 → 言语每键 15%  ·动作每键 5%
            //   0~119 异地 → 言语每键 15%  ·远程动作每键 8.33%
            //   ≥120  同格 → 亲密每键 15%  ·正向每键 9%  ·动作每键 5%
            //   敌对  同格 → 负面每键 18.75% ·attack 25%
            //   敌对  异地 → 负面每键 18.75% ·空串 25%（今天不找他）
            //   忙        → 动作支路整条跳过 ⇒ 言语每键 20%，**与 10-16 之前逐字相同**
            if (!busy && UnityEngine.Random.value < ActionChance)
            {
                if (intim < 0)
                {
                    // 敌对只配敌对向的动作（`attack`），不配切磋 —— 见 HostileActionIntents 注释。
                    // 异地返回空串：远程池里没有敌对手段（赠予/论道/邀约都太友善），
                    // 而同格的攻击又做不了 ⇒ 这一档今天就不找他。
                    return sameGrid ? RandomOf(HostileActionIntents) : "";
                }
                return RandomOf(sameGrid ? Concat(RemoteActionIntents, LocalActionIntents)
                                         : RemoteActionIntents);
            }

            // ── ② 言语支路：**逐字保留原实现**，比例关系（负面 / 亲密40% / 正向）不动 ──
            var rnd = UnityEngine.Random.value;
            if (intim < 0) return RandomOf(NegativeIntents);
            if (intim >= 120 && rnd < 0.4f) return RandomOf(PositiveCloseIntents);
            return RandomOf(PositiveIntents);
        }

        /// <summary>拼两个池（保持 `RandomOf` 的入参形态，不引入 List 分配热路径）。
        /// 只被 `PickIntent` 在**同格**时调用（异地直接走 `RemoteActionIntents` 原数组）。</summary>
        private static string[] Concat(string[] a, string[] b)
        {
            var all = new string[a.Length + b.Length];
            Array.Copy(a, 0, all, 0, a.Length);
            Array.Copy(b, 0, all, a.Length, b.Length);
            return all;
        }

        /// <summary>给模型一个开口的抓手（`（缘由：…）`）。
        ///
        /// ★ 10-16 补 0~60 档 ★ 原先这一档返回**空串** —— 而它恰恰是**最常见**的一档
        /// （陌生人到普通交情）。结果是舞台指令退化成孤零零一句「（NPC主动传音：与玩家发起闲聊）」，
        /// 模型没有任何情境抓手，只能泛泛寒暄。补一句"由头"不改变闸门，只让开场有落点。
        ///
        /// 文案刻意**不承诺具体动作**（不说"我带了东西给你"）：是否真调工具由模型按人设决定，
        /// 这里把话说死会造成"说了没做"。
        /// </summary>
        private static string BuildReason(int intim)
        {
            if (intim >= 120) return "念及与你多日情谊，特来以神识传音寻你";
            if (intim >= 60) return "想起你，想与你聊上几句";
            if (intim < 0) return "心中不忿，寻你理论";
            return "闲来无事，想起还有你这样一位道友";     // ← 0~60：最常见档，原先为空
        }

        private static string RandomOf(string[] arr)
        {
            return arr.Length == 0 ? "" : arr[(int)(UnityEngine.Random.value * arr.Length) % arr.Length];
        }
    }
}
