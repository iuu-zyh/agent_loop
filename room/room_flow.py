"""RoomFlow — 群聊编排器：投递、调度、跑轮、进出群。

分层分权（与既有架构一致）：
- `room.py`  纯数据（谁在场 / 说过什么）
- **本模块** 编排（把发言投给各 agent、决定谁说话、驱动回合）
- `room_history.py` 群窗投影
- `ws_channel.ChatHub` 只做路由与装配（提供锁、回合驱动、事件回推）

**本模块不 import `ws_channel`**（避免环），依赖由装配层注入三件：
`loop`（取名册/建 agent）、`run_turn`（跑一个回合）、`agent_lock` / `room_lock` / `turn_slot`。
于是它可以脱离 WS/游戏构造，用桩驱动单测。

──────────────────────────────────────────────────────────────────────
锁序（必须与 `handle_message` 一致，否则单聊与群聊会死锁）：

    room_lock  →  turn_slot  →  agent_lock
    room_lock  →  say_lock
    agent_lock →  say_lock

- 编排器持 `room_lock` 跨**整轮**（防两轮交错投递）；
- 每个发言者**各自取** `turn_slot` + `agent_lock`（不整轮霸占并发闸
  ⇒ 其他 NPC 的单聊/主动开口能插进来）；
- `(turn_slot, agent_lock)` 的相对顺序与 `handle_message` 相同 ⇒ 无环；
- `say_lock` 是**独立的落账短锁**（per-room），只包住「`room.say` + `_emit_say`」
  这一对，钉住「**UI 帧到达顺序 === Room.log 的 seq 顺序**」：`Room.say` 是同步的
  （seq 分配无竞态），但紧随其后的 `_emit_say` 有 `await` —— 两条发言各自的
  "落账 + 广播"交错就会让群窗气泡乱序。它的获取方向只有「`room_lock` 内」、
  「`agent_lock` 内（提案闸门落提案流水）」与「单独取」三种（见 `_say_and_emit`），
  从不反向索要 `room_lock` / `agent_lock` ⇒ 无环、无死锁。

单聊路径**不取** `room_lock`，所以玩家在群里开会时仍可打开某个成员的单聊窗：
消息走 `next-step`，会在该成员当前回合的下一步被领走（与既有「玩家插话」同一条路）。

★ **玩家插话不必等整轮**：`run_message` 把「落账 + 回显」放在 `room_lock` **之外**
（原先它在锁内 ⇒ 第二条消息连落账都要等整轮跑完，群窗上的表现是"发出去毫无反应"）。
于是本轮**尚未 `deliver`** 的成员（含"已点名但在等锁"的）本轮就能看到它 —— 惰性投递按
`seq > cursor` 自然算进去；已 `deliver` 的成员（正在跑 / 已说完）由**下一轮**的
`deliver` 补投 ⇒ **不丢**。轮次语义**不变**：第二个 `_run_round` 仍在 `room_lock`
上排队，一轮只有一个在跑。
──────────────────────────────────────────────────────────────────────

投递**必须**走 inbox 的 `next-step`，不能回合外直接 `session.append(..., surfaceOp=append)`。
后者会把 `history.project_ui_history` 与 `history_ops._turn_spans` 共同依赖的不变量
「每条 user/message 都属于某个 turn/start..turn/end」打穿，实测三个破口（且跑模拟**看不出来**，
两种形态下模型看到的消息流一模一样，坏的只有账本的记账结构）：
  ① 投影把游离消息算进上一个已闭合回合，但该回合的删除跨度不含它 ⇒ 删不掉；
  ② 玩家删掉那一回合后，该条落进永不发射的 turn-0 容器 ⇒ UI 里看着删了、
     `surface` 里还在 ⇒ **模型仍记得**；
  ③ 首回合之前的投递静默丢弃。
反例脚本：`scripts/dev/probe_group_history.py`（留存，勿删）。
"""

from __future__ import annotations

import contextlib
import random
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .. import log_setup
from ..agent.dialogue_agent import TurnAborted   # 读档打断回合：整轮就地收尾，不当成员故障
from ..tools.room_narrate import battle_line
from ..session.history_ops import DeleteError, append_delete, plan_message_delete
from .room import PLAYER, Room, Utterance, new_room_id
from .mention import parse_mentions, strip_mentions

log = log_setup.get_logger(__name__)

# 弃权哨兵：被点名 ≠ 非说不可。
# ★ 光有常量是死代码 —— 模型不知道有这个选项就永远不会回它。所以 nudge 文本里
#   必须写明（见 NUDGE_TEXT）。P2 的群聊规则段会再讲一遍，但 nudge 是自足的那一道。
SILENT_SENTINEL = "【沉默】"
_SILENT_VARIANTS = {
    "", SILENT_SENTINEL, "【沉默】。", "（沉默）", "(沉默)", "【静默】", "（静默）",
}

# 催话。**不进 `Room.log`**（它不是群里说的话，是编排层的促发）。
# `source.kind == "plugin"` 三个语义一次到位：模型可见（surfaceOp=append）、
# UI 不可见（history 滤除）、压缩材料跳过 —— 不需要任何新机制。
#
# 为什么把规则塞进 nudge 而不是只留 "（轮到你说话了。）"：
#   ① 弃权路径要活着（见上）；
#   ② 「不要写名字前缀」在这里讲最有效 —— 模型刚看完一串 `李四：…` 的转述，
#      正处在最容易模仿那个格式的位置；
#   ③ 它天然是 per-turn 的、挂在对话尾部 ⇒ 不扰动上游前缀缓存（工具表与 system 恒定）。
PLUGIN_NAME = "@python-harness/group"
NUDGE_TEXT = (
    "（轮到你说话了。只说你自己想说的话，不要写名字前缀，也不要替别人说话。"
    "若你此刻无话可说，只回【沉默】。）"
)

# ★ 被 @ 点名时的催话（11-21 用户要求：群里要能 @ 某个人单独说话）★
#
# 为什么要单独一条文案，而不是复用 NUDGE_TEXT：
#   ① 被 @ 的人**看不到**别人的反应（这条消息只进了他的账本），他很可能以为
#      "玩家是在群里当众说的" —— 若不告诉他"只对你说的"，他会写出"各位""你们"这种
#      对着空气的措辞，而群里其他人根本没收到这条；
#   ② 他要明白**别人没听到**：这决定了他可以说什么（私下的话说得出口）。
#
# 为什么这里要教模型"别回 @ 符号"：玩家那句话的 `@名字` 已经被 `strip_mentions` 剥掉了，
# 模型看到的正文里没有 @。但**他自己的历史里**（前几轮）可能见过带 @ 的原文（老存档），
# 于是会模仿成 `@玩家` 这种游戏里不存在的写法 —— 那串字会原样进气泡。
NUDGE_TEXT_MENTION = (
    "（玩家刚才**单独叫了你**（用 @ 点了你的名字），这句话只有你收到了，"
    "其他人都没听见 —— 你可以说只对他一个人说的话。"
    "只说你自己想说的话，不要写名字前缀，不要用 @ 符号（这个界面没有这个功能），"
    "也不要替别人说话。若你此刻无话可说，只回【沉默】。）"
)

# ★ 跳过那一轮的催话文案（与上面那条**刻意不同**，理由在此）。
#
# 「跳过」= 玩家这一轮不置一词、让 NPC 自己接下去。它与"玩家说了一句话"的差别
# 只有一处、但很关键：**这一轮没有任何新东西被投递**（`deliver` 按 `seq > cursor`
# 取增量，而游标在每人上一次被点名时就推到了当时的 `last_seq`）。
#
# ⚠ 必须与"模型看不见前情"区分开：前情**一直都在** —— 上一轮投给他的那些话已经
#   `append` 进他的 surface，`derive_messages()` 每回合把**整份 surface** 交给模型。
#   所以空的是"本轮增量"，不是"上下文"。真正的风险是另外两条：
#     ① 他上一条自己的回复往往就是【沉默】（上一轮弃权才轮得到跳过），紧接着又被
#        同一句话问一次 ⇒ 模型有很强的动机**保持一致**、再沉默一次；
#     ② 原句没说"玩家这一轮不会开口"，于是一个刚答完玩家的 NPC 完全有理由认为
#        **该等玩家接话** —— 他等的人恰恰就是按下跳过的人。
#   这两条都是**措辞**问题，不是数据问题，所以修在这一句上。
#
# 用户对这个功能的原话定义就是这句文案该说的话：「跳过 = 广播一句玩家没插话」。
# 广播的对象是**每个被点名的成员**（nudge 是 per-turn 注入的，人人都会收到），
# 只是它不进 `Room.log`、UI 不可见、压缩材料跳过 —— 这三条是 plugin 消息的既有语义。
#
# 为什么**不**真的往 `Room.log` 写一条「（玩家没有插话）」来当那次广播：
#   ① `Room.log` 只装 `Utterance`（`room.py` 的 `log: List[Utterance]`，`say()` 是
#      唯一写入口）—— 写进去只能是**一条玩家发言**，等于把话塞进玩家嘴里；
#   ② 它会以 `kind="room", speaker="player"` 投给每个成员 ⇒ 在 NPC 的历史里渲染成
#      一条**玩家气泡**（他"记得"玩家说过"（玩家没有插话）"），群窗历史上也留一行；
#   ③ 要放非发言事件得改数据模型（seq 分配 / `delta_for` / 群窗投影 / 按 uid 删除 /
#      落盘格式），为这个功能付这个代价不值得。
#   玩家可见的那点痕迹改由 **C# 在点击处本地落一行系统提示**（即时、不落盘、不污染记忆）。
NUDGE_TEXT_SKIP = (
    "（玩家没有插话，只是在听。轮到你说话了 —— 想说什么就说什么，可以接着话头说下去，"
    "也可以起个新话头。只说你自己想说的话，不要写名字前缀，也不要替别人说话。"
    "若你此刻实在无话可说，只回【沉默】。）"
)

# 跳过那一轮「一个人都没开口」时，终帧必须回给玩家的那一句。
#
# 为什么玩家发言轮**不**写、跳过轮**必须**写：终帧的 `text` 是轮级"这一轮为什么没有产出"
# 的通道，而 `_run_round` 此前只在「全员静默**且有人出错**」时写它 —— 注释给的理由是
# "大家都觉得没什么可说"是正常结果。那个判断在玩家发言轮成立：**屏幕上至少有玩家那条气泡**，
# 空不等于坏。跳过这一轮玩家**一条气泡都没发**，全员弃权的话屏幕上就是
# 「忙碌标记消失 + 零文字」—— 与"按钮坏了"在玩家眼里长得一模一样（本工程反复写死的
# 「按了没反应」形态）。所以判据是**这一轮有没有玩家发言**，不是"沉默正不正常"。
SILENT_ROUND_NOTE = "（没人接话——他们似乎都没什么想说的。）"

# 玩家**只写了 @名字**（没有任何正文）时，模型看到的这一句。
# 为什么要替换而不是原样发：原样发的话模型收到的是「@张三」四个字符 ——
# 它会以为玩家在念一个符号，或者干脆不知道"这是冲我来的"。
# 为什么不能把它写进 `Room.log`：群窗会显示玩家说了「（玩家在叫你。）」，
# 而那是系统话，不是玩家说过的话（复核时实测确认过这个 bug）。
PURE_PING_TEXT = "（玩家点了你的名字，想跟你说话。）"

# 房间流水的展示上限（群窗一次最多给多少条）。0 = 不限。
DEFAULT_HISTORY_LIMIT = 0


def is_silent(body: Any) -> bool:
    """回「【沉默】」或空 ⇒ 本回合弃权。

    容错收几个带标点的写法（模型偶尔会在哨兵后加句号或用全角括号），
    但**不做**模糊匹配 —— 把真实短回复误判成沉默，比漏判一次沉默严重得多。
    """
    return str(body or "").strip() in _SILENT_VARIANTS


def _member_error_note(exc: BaseException) -> str:
    """把"成员这一轮为什么没开口"翻成**人话**（进群窗那一行系统提示）。

    为什么必须翻、且必须是短句：这条提示是玩家**唯一**的解释来源 —— 09-17 实机，
    姜萌的流式请求被挂钟硬顶砍断（思考流已推 382 token），C# 那边一帧都没收到，
    于是"她的空气泡挂了一会儿自己消失了，然后另一个人开始说话"，玩家完全不知道
    发生了什么（日志里只有 traceback）。`traceback` 永远留在 `agent_loop.log`，
    上屏的只有归因；**不写内部类名/堆栈**（与错误帧"人话原因"同规约）。
    """
    if isinstance(exc, TimeoutError):        # py3.11 起 = asyncio.TimeoutError
        # 两种超时对玩家的体感完全不同（"一直没开口" vs "说到一半卡住"），文案分开说 ——
        # 判据是我们自己那两条存活看门狗写进异常消息的（见 llm/openai_client._guarded）。
        msg = str(exc)
        if "首字" in msg:
            return "模型一直没开口（首字超时）"
        if "停顿" in msg:
            return "模型说到一半卡住了（长时间没有新内容）"
        return "模型超时（未在时限内完成）"
    name = type(exc).__name__
    if name in ("CancelledError",):
        return "回合被取消"
    return "回合内部异常（详见 agent_loop.log）"


def plan_round(members: Sequence[str], speak_first: Iterable[str] = (),
               start: int = 0, rng: Any = None) -> List[str]:
    """本轮依次点名的**候选**名单（顺序即优先序）。纯函数，可单测。

    ── 顺序（11-21 用户拍板改成随机）────────────────────────────────────
    ① `speak_first` 里的人（被 @ 的 / 提案对手方）**按给定顺序排最前**；
    ② 其余成员**随机**打乱（不再按轮转指针）。

    为什么随机：轮转固定成 `A→B→C→A→B→C` 时，玩家几轮下来就能预测"下一个该谁说了"，
    群聊显得像排队报数。随机更接近一群人聊天。
    为什么 `speak_first` 仍优先：@ 了人却让旁人先说，是明确的错误行为
    （用户："如果被@了话那么只需要被@了的人说话"）。

    `start` 参数**保留但不再参与排序** —— 它是既有调用点与测试的形参，删掉会让
    所有调用点连带改一遍，而它现在已经没有语义。传什么都一样。
    （真要恢复轮转，把这个参数重新用起来即可；注释与测试都在。）

    ── 随机源 ──────────────────────────────────────────────────────────
    `rng` = 可选的 `random.Random` 实例（测试注入固定种子用）。
    缺省用模块级 `random` —— **不要**在这里 `random.seed()`：那会污染全局随机源，
    让别处（比如战斗随机）跟着可预测。

    ★ **本函数不认识"每轮上限 K"**（那是 `RoomFlow.k`）。候选名单刻意**比 K 长**：
    有人弃权就继续点下一个补位，所以"点了几个人"和"几个人真开口"是两件事，
    K 由调用方在跑轮时对**实际发言**计数。（早期签名上挂过一个不参与计算的 `k` 形参，
    是死的、且会让人以为这里会截断，已删。）
    """
    member_list = list(members)
    if not member_list:
        return []
    named = [m for m in dict.fromkeys(speak_first) if m in member_list]
    rest = [m for m in member_list if m not in named]
    # 随机源：注入的 rng 优先（测试可复现），否则用模块级 random
    _rng = rng if rng is not None else random
    try:
        _rng.shuffle(rest)
    except Exception:
        # 随机失败不能拖垮一轮：保持原顺序（宁可顺序固定，也不能"这一轮没人说话"）
        pass
    return named + rest


# ══════════════════════════════════════════════════════════════════
# 提案制（缔结类关系 / 讨要 / 赠予 / 买卖）
#
# 在场 NPC 之间这四类动作不走"单方面直写"：先**提出**（不碰数据、不调 C#），
# 对手方在自己的回合里回应才落定。四条硬规则（安全性不靠模型的自觉）：
#   ① 只有对手方能收口 —— 发起人重复调用被拦（防"自己提两次绕过对方"）；
#      对手方**反向做同一个动作**就是回应（关系互逆；赠予 ↔ 讨要互为应答；
#      买卖按**原条款回发**才成交）；
#   ② 拒绝 = 沉默 —— 没有答复就是没有答复，TTL 后作废并私信告知发起人；
#   ③ 只在**群聊回合**生效 —— 闸门由 `_one_speaker` 注入；单聊不挂（行为与改动前一致）；
#   ④ **公开性**（09-17 统一口径）—— 公开面（落 `Room.log`、经 `deliver` 进所有成员
#      session、重开群窗可回放 = 提案族 + 解除类）：
#        · 提出、应下、**过期（沉默也是结果）**、解除类（单方面直写但结果公开）；
#        · 应下 / 解除的公开行都在 **bridge 成功之后**才写（`_make_accept_settle` /
#          `_make_break_settle`），过期行在轮末由 `_expire_proposals` 写；
#        · 事没成就不是既成事实 —— 失败不进公共流水。
#      私密面（只在该 NPC 自己的 session 里，调用与结果都不碰 `Room.log`）：
#        只读工具、加/减好感、位移、偷窃。
#      私信只承载**系统话**：尚未发生的事（回应方式）与**没有发生**的事
#      （过期、应下但落刀失败 —— 后者仅为让发起人心里有底，不公开）。
#
# 判据（`judge_proposal`）是**纯函数**：输入明确、无副作用、可脱离运行时单测。
# 副作用（登记提案 / 落流水 / 排私信 / 消费）都在 `_make_gate` 里，紧挨着判据。
#
# ★ 闸门**只管这四类**（rel / ask / give / trade）。`movement` 与 `world_ai_action`
#   **刻意不拦** —— 这是用户 09-17 拍板的**有意为之**，不是漏了：
#     · `movement`（summon / teleport / travel）：位移不是"合意"行为。`teleport` 是自己过去、
#       `travel` 是独自前往，都不涉及对方；`summon` 把别人拉过来虽然单向，但它是**可观测、
#       可当场反驳**的物理事实（下一句就能说"你把我拽来做什么"），与"缔结关系/转移财物"
#       那种**落定后不可逆**的动作性质不同。给它加提案会让"叫个人过来说话"变成两轮往返。
#     · `world_ai_action`：C# 侧本来就**显式拒绝**一切非玩家对象（`ToolExecutor` 里那句
#       "对象只能是玩家"），所以它在群里根本没有"对手方"可言，无需闸门。
#       ★ 但"无需闸门"不等于"无需公开"（09-22 补）：切磋应战之后要占掉**一整场战斗**的时间
#       （`_run_round` 持 `room_lock` 串行跑成员），全群一个字都不知道只会以为这一轮卡死了。
#       所以切磋走 `pass_after` 落一条公共叙述行 —— 见 `_make_battle_settle` 与
#       `tools/room_narrate.py`（"结果全局可见"只能是发言行，不能共享 `tool/result`：
#       成员的 tool 结果要与他看到的 tool_call 成对，插别人的就是孤儿消息，网关直接拒）。
#   将来若要改（例如 summon 也要对方点头），改 `judge_proposal` 即可，
#   但**请连同这条注释一起改** —— 否则下一个人会以为这里是漏写的。
# ══════════════════════════════════════════════════════════════════

# 缔结类关系 op（要对方点头的那些）。加/减好感与解除类**不在此列**：
# 好感增减是单方面态度、解除本就是单方面行为（`DirectBreak` 双向写字段，不需要同意）。
REL_ESTABLISH_OPS = ("jie_yuan", "marry", "bai_shi", "shou_tu", "ren_yi_fu_mu", "jie_yi")
# 提案文案里的**动词短语**（"提出…之意 / 应了…之请"直接读它）：名词版（道侣/师徒）塞进
# 这些句式会读成"提出道侣之意"，对「结义」更是直接出错（"结为结义"）。
REL_ASK_CN: Dict[str, str] = {
    "jie_yuan": "结为道侣", "marry": "结为夫妻", "bai_shi": "拜师",
    "shou_tu": "收徒", "ren_yi_fu_mu": "认义亲", "jie_yi": "结义",
}
# 解除类关系 op：**单方面行为**（不需要对方同意，所以不走提案），但对在场 NPC 的
# **结果要公开**（与缔结的"应下"对称）—— 由 `_make_break_settle` 在落刀成功后补叙述行。
REL_BREAK_OPS = ("jie_chu_jie_yuan", "divorce", "jie_chu_bai_shi", "jie_chu_shou_tu",
                 "jie_chu_jie_yi", "jie_chu_ren_yi_fu_mu")
REL_BREAK_CN: Dict[str, str] = {
    "jie_chu_jie_yuan": "道侣之约", "divorce": "夫妻之约", "jie_chu_bai_shi": "师徒之约",
    "jie_chu_shou_tu": "师徒之约", "jie_chu_jie_yi": "结义之约",
    "jie_chu_ren_yi_fu_mu": "义亲之谊",
}
# 提案的有效期（完整回合数）。2 = 提出后还有两轮轮转，对手方两次被优先点名。
PROPOSAL_TTL_ROUNDS = 2

# 私信的 plugin 名（与 NUDGE 的 "@python-harness/group" 分开：便于日志/测试区分
# "催话"与"提案通知"。三条语义与 nudge 相同：模型可见 / UI 不可见（history 滤除）/ 压缩材料跳过）。
NOTICE_PLUGIN = "@python-harness/proposal"


def _pair_key(room_id: str, kind: str, disc: str, a: str, b: str) -> tuple:
    """提案表的 key：**方向无关**（方向在记录的 proposer/target 上，不在 key 里）。"""
    return (str(room_id), str(kind), str(disc or ""), frozenset((str(a), str(b))))


def _trade_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        try:
            return int(str(x).strip() or default)
        except Exception:
            return default


def _trade_fields(args: Dict[str, Any]):
    """归一化 trade 四要素 → (seller, buyer, item, count, price)；形态不全/自语返回 None。

    归一化口径必须与 C# 对齐（`SafeInt` + count≥1 / price≥0 的钳制）—— 否则"同条款"
    判定会漂移：模型把 count 写成字符串、或省略 price 时，提出与回应必须仍落到同一笔交易上。
    """
    seller = str(args.get("seller") or "").strip()
    buyer = str(args.get("buyer") or "").strip()
    item = str(args.get("item") or "").strip()
    if not seller or not buyer or not item or seller == buyer:
        return None
    return (seller, buyer, item,
            max(1, _trade_int(args.get("count"), 1)),
            max(0, _trade_int(args.get("price"), 0)))


def _trade_sig(seller: str, buyer: str, item: str, count: int, price: int) -> str:
    """一桩买卖的**条款签名**：用于"同条款回发 = 成交"与"重复报价"两个判定。"""
    return f"{seller}|{buyer}|{item}|{count}|{price}"


def _deal_cn(rec: Dict[str, Any]) -> str:
    """买卖的价钱说法（0 价即"不取分文"——它确实是赠送语义，但走的是 trade 的整笔口径）。"""
    p = _rec_price(rec)
    return f"价{p}灵石" if p > 0 else "不取分文"


# ★ 09-17：这里原先有 `ID_ARG_KEYS` + `translate_member_ids(args, room)`（闸门里的"边界翻译"）。
#   已**整体搬到 `DialogueAgent._inject_person_ids`**，理由三条：
#     ① 单聊也需要（闸门只在群聊挂载 ⇒ 单聊那条路以前**压根没有** `target_id`）；
#     ② 映射表的来源不止房间身份表（还有自己 / 玩家 / 查询学到的），而闸门只看得见房间；
#     ③ "两个实现必然漂移" —— 键表与注入规则各留一份，早晚有一份忘了改。
#   闸门现在只做判据（它吃的仍是中文名），身份注入由 `DialogueAgent` 在调用闸门**之前**完成。


def judge_proposal(name: str, args: Dict[str, Any], *, caller: str, members: Sequence[str],
                   proposals: Dict[tuple, Dict[str, Any]], room_id: str):
    """群聊提案闸门的**纯判据** → (verdict, key, text)。

    - ("pass",    None, None)   不动它，照常走 C#（加/减好感、解除、偷窃、位移…）
    - ("block",   None, 文案)   短路成错误结果（形状同构，模型不会死循环重试）
    - ("propose", key,  None)   短路成"已提出"；调用方登记 + 落流水 + 排私信
    - ("accept",  key,  None)   对手方在回应：调用方消费该提案，**放行到 C# 真落刀**

    四种 kind：`rel`（缔结类关系）/ `ask`（讨要）/ `give`（赠予，单件）/ `trade`（买卖）。
    `members` = 房间成员（真名）——"当面"就由"是不是在场者"近似。
    """
    caller = str(caller or "")
    args = args or {}
    op = str(args.get("op") or "")
    tgt = str(args.get("target") or "").strip()

    def _presence(t: str):
        if t == caller:
            return "target 不能是你自己（对象是玩家时省略 target 即可）。"
        if t not in members:
            return (f"此处没有名为「{t}」的在场者。这种事要当面提出（在场名单见「在场」段）；"
                    f"若对象是玩家，省略 target 即可。")
        return None

    # ---- 缔结类关系（提出 = 结下之意，需对方点头）----
    if name == "social_relation":
        if op not in REL_ESTABLISH_OPS or not tgt:
            return ("pass", None, None)
        bad = _presence(tgt)
        if bad:
            return ("block", None, bad)
        key = _pair_key(room_id, "rel", op, caller, tgt)
        rec = proposals.get(key)
        if rec is None:
            return ("propose", key, None)
        if rec.get("proposer") == caller:
            # 发起人重复提：拦（否则自己提两次就绕过了对方的同意）
            return ("block", None, f"你已向{tgt}提过{REL_ASK_CN.get(op, '')}之意，等对方回应即可。")
        return ("accept", key, None)       # 对手方反向调用 = 接受

    # ---- 赠予（提出 = 欲赠，对方"领取"才交付）----
    if name == "economy_item":
        if not tgt:
            return ("pass", None, None)
        items = [it for it in (args.get("items") or [])
                 if isinstance(it, dict) and str(it.get("item_name") or "").strip()]
        if not items:
            return ("pass", None, None)    # 形态不对交给 C# 报错（那是它的活）
        bad = _presence(tgt)
        if bad:
            return ("block", None, bad)
        if len(items) != 1:
            return ("block", None, "一次只提议赠予一件（当前是多件）。请拆成多次调用。")
        item = str(items[0].get("item_name") or "").strip()
        # 对手方在"交付"：他向我讨要过这件东西 ⇒ 这是回应（赠予 ↔ 讨要互为应答）
        for k, r in proposals.items():
            if (r.get("kind") == "ask" and r.get("room_id") == room_id
                    and r.get("proposer") == tgt and r.get("target") == caller
                    and r.get("item_name") == item):
                return ("accept", k, None)
        key = _pair_key(room_id, "give", item, caller, tgt)
        if key in proposals:
            return ("block", None, f"你已向{tgt}提过赠予{item}之意，等对方收下即可。")
        return ("propose", key, None)

    # ---- 讨要（提出 = 开口要，对方"交付"才转移）----
    if name == "item_acquire":
        if op != "ask_for" or not tgt:
            return ("pass", None, None)    # 偷窃本无同意可言；对玩家走原路
        item = str(args.get("item_name") or "").strip()
        if not item:
            return ("pass", None, None)
        bad = _presence(tgt)
        if bad:
            return ("block", None, bad)
        # 对手方在"领取"：他向我赠予过这件东西 ⇒ 这是回应
        for k, r in proposals.items():
            if (r.get("kind") == "give" and r.get("room_id") == room_id
                    and r.get("proposer") == tgt and r.get("target") == caller
                    and r.get("item_name") == item):
                return ("accept", k, None)
        key = _pair_key(room_id, "ask", item, caller, tgt)
        if key in proposals:
            return ("block", None, f"你已向{tgt}提过讨要{item}之意，等对方回应即可。")
        return ("propose", key, None)

    # ---- 买卖（提出 = 报价，对方**按原条款回发**才成交；改条款 = 还价）----
    if name == "trade":
        f = _trade_fields(args)
        if f is None:
            return ("pass", None, None)    # 形态不全 / 自语 / 灵石当商品：交给 C# 报错
        seller, buyer, item, count, price = f
        if "灵石" in item:
            return ("pass", None, None)    # 同上（C# 有专门的拒绝文案）
        if not (seller in members and buyer in members):
            # 任一方不在场：可能是玩家（C# 弹确认窗）、也可能是异地 NPC（老路径）——
            # Python 认不出"玩家名"，所以这条边界交给 C#（那里 IsPlayerUnit 是权威）。
            return ("pass", None, None)
        if caller not in (seller, buyer):
            # 双方都在场但发起人不是买卖一方：替别人做主 = 两边的同意都没取得，拦下
            return ("block", None, "买卖要有一方是你自己（另一方的同意由提案取得，替他人做主不行）。")
        other = buyer if caller == seller else seller
        sig = _trade_sig(seller, buyer, item, count, price)
        for k, r in proposals.items():
            if (r.get("kind") == "trade" and r.get("room_id") == room_id
                    and r.get("proposer") == other and r.get("target") == caller
                    and r.get("sig") == sig):
                return ("accept", k, None)      # 对手方按**原条款**回发 = 成交
        key = _pair_key(room_id, "trade", sig, caller, other)
        if key in proposals:
            return ("block", None,
                    f"你已向{other}提过这一条款，等对方回应即可（改条款 = 还价，可以再提）。")
        return ("propose", key, None)

    return ("pass", None, None)


def _rec_count(rec: Dict[str, Any]) -> int:
    try:
        n = int(rec.get("count") or 1)
    except Exception:
        n = 1
    return max(1, n)


def _rec_price(rec: Dict[str, Any]) -> int:
    try:
        return max(0, int(rec.get("price") or 0))
    except Exception:
        return 0


def _build_record(key: tuple, room_id: str, proposer: str, args: Dict[str, Any],
                  expire_round: int) -> Dict[str, Any]:
    """从 key + 调用参数造一条提案记录（方向在这里落定）。"""
    kind = key[1]
    target = next(iter(key[3] - {str(proposer)}), "")
    rec: Dict[str, Any] = {"kind": kind, "room_id": str(room_id), "proposer": str(proposer),
                           "target": target, "expire_round": int(expire_round)}
    if kind == "rel":
        rec["op"] = key[2]
    elif kind == "trade":
        f = _trade_fields(args) or ("", "", key[2], 1, 0)
        rec.update({"seller": f[0], "buyer": f[1], "item_name": f[2],
                    "count": f[3], "price": f[4], "sig": key[2]})
    else:
        rec["item_name"] = key[2]
        if kind == "give":
            try:
                rec["count"] = int((args.get("items") or [{}])[0].get("count") or 1)
            except Exception:
                rec["count"] = 1
        else:
            try:
                rec["count"] = max(1, int(args.get("count") or 1))
            except Exception:
                rec["count"] = 1
    return rec


def _answered_count(name: str, args: Dict[str, Any]):
    """回应调用里的数量（私信文案用；读不到就返回 None，退回提案里的原始数）。"""
    try:
        if name == "economy_item":
            return {"count": int((args.get("items") or [{}])[0].get("count") or 1)}
        return {"count": int(args.get("count") or 1)}
    except Exception:
        return None


def _proposal_line(rec: Dict[str, Any]) -> str:
    """提案落进 `Room.log` 的**叙述行**（挂在发起人名下）：全群可见、可回溯。"""
    t, k = rec.get("target"), rec.get("kind")
    if k == "rel":
        return f"（向{t}提出{REL_ASK_CN.get(rec.get('op', ''), '')}之意）"
    if k == "give":
        return f"（欲赠{t}{rec.get('item_name')}×{_rec_count(rec)}）"
    if k == "trade":
        if rec.get("proposer") == rec.get("buyer"):
            return f"（欲买{t}的{rec.get('item_name')}×{_rec_count(rec)}，{_deal_cn(rec)}）"
        return f"（欲将{rec.get('item_name')}×{_rec_count(rec)}卖予{t}，{_deal_cn(rec)}）"
    return f"（向{t}讨要{rec.get('item_name')}×{_rec_count(rec)}）"


def _proposal_note(rec: Dict[str, Any]) -> str:
    """给**对手方**的私信：带上回应所需的确切调用形状（模型照做即可，不用猜）。"""
    me, t, k = rec.get("proposer"), rec.get("target"), rec.get("kind")
    if k == "rel":
        return (f"（{me}当众向你提出{REL_ASK_CN.get(rec.get('op', ''), '')}之意。"
                f"你愿意便用 social_relation：op={rec.get('op')}、target={me}；不愿意就不必回应。）")
    if k == "give":
        return (f"（{me}欲赠你{rec.get('item_name')}×{_rec_count(rec)}。你愿意收下便用 item_acquire："
                f"op=ask_for、target={me}、item_name={rec.get('item_name')}、count={_rec_count(rec)}；"
                f"不愿意就不必回应。）")
    if k == "trade":
        return (f"（{me}提出的买卖条款：seller={rec.get('seller')}、buyer={rec.get('buyer')}、"
                f"item={rec.get('item_name')}、count={_rec_count(rec)}、price={_rec_price(rec)}。"
                f"你愿意便用 trade 照原样再发一遍（即为成交）；改条款即为还价；不愿意就不必回应。）")
    return (f"（{me}向你讨要{rec.get('item_name')}×{_rec_count(rec)}。你愿意给便用 economy_item："
            f'target={me}、items=[{{"item_name": "{rec.get("item_name")}", "count": {_rec_count(rec)}}}]；'
            f"不愿意就不必回应。）")


def _res_count(res: Optional[Dict[str, Any]], fallback: int) -> int:
    """从落刀结果里读**实际件数**（形状因工具而异）；读不到退回提案里记的数。"""
    try:
        d = (res or {}).get("data") or {}
        v = d.get("count")
        if v is None and isinstance(d.get("items"), list) and d["items"]:
            v = (d["items"][0] or {}).get("actual")
        if v is not None:
            return max(1, int(v))
    except Exception:
        pass
    return max(1, int(fallback))


def _resolve_line(rec: Dict[str, Any], name: str, args: Dict[str, Any],
                  res: Optional[Dict[str, Any]] = None) -> str:
    """**应下的公开叙述行**（挂对手方名下，落 `Room.log`）：与提出那条对称。

    与 `_proposal_line` 的分工：那条记"提了什么"，这条记"应了什么、成了什么"。
    数量优先取落刀结果里的实际件数（回应时声明的数可能与实得数不同），读不到用提案里的数。
    """
    p, k = rec.get("proposer"), rec.get("kind")
    n = _rec_count(rec)
    ans = _answered_count(name, args)
    if isinstance(ans, dict) and ans.get("count"):
        n = int(ans["count"])
    n = _res_count(res, n)
    if k == "rel":
        return f"（应了{p}{REL_ASK_CN.get(rec.get('op', ''), '')}之请，此事已成）"
    if k == "give":
        return f"（收下了{p}赠的{rec.get('item_name')}×{n}）"
    if k == "trade":
        return f"（应了{p}的买卖：{rec.get('item_name')}×{_rec_count(rec)}，{_deal_cn(rec)}）"
    return f"（应了{p}的讨要，给了{rec.get('item_name')}×{n}）"


def _fail_note(rec: Dict[str, Any], err: str = "") -> str:
    """给**发起人**的私信：对手方应下了，但落刀失败。

    刻意**不公开** —— 事没成，就不是既成事实（公开面只在 `_resolve_line` 那一条）。
    但也不能不告诉发起人：记录已被消费，他等不到"成了"也等不到过期通知。
    """
    t, k = rec.get("target"), rec.get("kind")
    n = _rec_count(rec)
    tail = f"（{err}）" if err else ""
    if k == "rel":
        return f"（{t}应了你{REL_ASK_CN.get(rec.get('op', ''), '')}之请，但此事最终没能办成{tail}。）"
    if k == "give":
        return f"（{t}应了你的赠予，但{rec.get('item_name')}×{n}最终没能交成{tail}。）"
    if k == "trade":
        return f"（{t}应了这桩买卖，但最终没能成交{tail}。）"
    return f"（{t}应了你的讨要，但{rec.get('item_name')}×{n}最终没能交成{tail}。）"


def _expire_note(rec: Dict[str, Any]) -> str:
    """给**发起人**的私信：石沉大海（拒绝 = 沉默，但发起人有权知道结果）。"""
    t, k = rec.get("target"), rec.get("kind")
    if k == "rel":
        return f"（你向{t}提出的{REL_ASK_CN.get(rec.get('op', ''), '')}之意，{t}始终没有回应。）"
    if k == "give":
        return f"（你欲赠{t}的{rec.get('item_name')}×{_rec_count(rec)}，{t}始终没有收下。）"
    if k == "trade":
        return f"（你向{t}提出的买卖（{rec.get('item_name')}×{_rec_count(rec)}，{_deal_cn(rec)}）没有回音。）"
    return f"（你向{t}讨要的{rec.get('item_name')}×{_rec_count(rec)}，{t}始终没有回应。）"


def _expire_line(rec: Dict[str, Any]) -> str:
    """过期的**公开**叙述行（挂发起人名下，落 `Room.log`）：沉默也是群聊里看得见的结果。

    与 `_expire_note` 的分工：那条是"发起人自己要知道"的私信（他收不到自己这条公开行 ——
    `delta_for` 不投自己的话），这条是"全群都该看到"的记录。两件都留。
    """
    t, k = rec.get("target"), rec.get("kind")
    if k == "rel":
        return f"（向{t}提出的{REL_ASK_CN.get(rec.get('op', ''), '')}之意，{t}始终没有回应）"
    if k == "give":
        return f"（欲赠{t}的{rec.get('item_name')}×{_rec_count(rec)}，{t}始终没有收下）"
    if k == "trade":
        return f"（向{t}提出的买卖（{rec.get('item_name')}×{_rec_count(rec)}，{_deal_cn(rec)}）没有回音）"
    return f"（向{t}讨要的{rec.get('item_name')}×{_rec_count(rec)}，{t}始终没有回应）"


def _proposal_result(rec: Dict[str, Any]) -> Dict[str, Any]:
    """"已提出"的成功帧（形状与桥结果同构；渲染层有对应的 proposal 分支）。"""
    data: Dict[str, Any] = {"proposal": "asked", "counterparty": rec.get("target")}
    kind = rec.get("kind")
    if kind == "rel":
        data["op"] = rec.get("op")
    elif kind == "trade":
        # trade 的渲染器读 `item`（不是 item_name）—— 跟着既有形状走，别给它多开一个键
        data["item"] = rec.get("item_name")
        data["count"] = _rec_count(rec)
        data["price"] = _rec_price(rec)
    else:
        data["item_name"] = rec.get("item_name")
        data["count"] = _rec_count(rec)
    return {"success": True, "data": data}


def _null_cm():
    """空上下文管理器（未注入并发闸时用）。"""
    return contextlib.nullcontext()


def _null_say_lock(_room_id: str):
    """空锁（未注入 `say_lock` 时用）。形状对齐 `Callable[[str], 上下文管理器]`。"""
    return contextlib.nullcontext()


class RoomFlow:
    """群聊编排器。一个进程一份（由 `ChatHub` 持有）。"""

    def __init__(
        self,
        loop: Any,
        *,
        run_turn: Callable[..., Any],
        agent_lock: Callable[[str], Any],
        room_lock: Callable[[str], Any],
        say_lock: Optional[Callable[[str], Any]] = None,
        turn_slot: Optional[Callable[[], Any]] = None,
        emit: Optional[Callable[..., Any]] = None,
        max_speakers_per_round: int = 3,
        load_room: Optional[Callable[[str], Any]] = None,
        delete_room: Optional[Callable[[str], Any]] = None,
    ):
        self.loop = loop
        self._run_turn = run_turn
        self._agent_lock = agent_lock
        self._room_lock_impl = room_lock
        # 落账短锁：只包住「room.say + emit」，保证 UI 帧顺序 === 流水 seq 顺序。
        # 它**不**防两轮交错（那是 room_lock 的活）—— 所以两把锁各管一段、互不嵌套反向。
        self._say_lock_impl = say_lock or _null_say_lock
        self._turn_slot = turn_slot or _null_cm
        self._emit = emit
        self.k = max(1, int(max_speakers_per_round))
        self._load_room = load_room
        # 解散后的落盘收尾（写 index + unlink 流水）。**注入**而不是直接 import
        # `persistence`：与本类不 import `ws_channel` 同一个理由 —— 保持它能脱离
        # WS/磁盘、用桩驱动单测。为 None 时"解散"只摘内存（测试/无盘场景）。
        self._delete_room = delete_room
        # 轮转指针：room_id → 下一位候选的下标。**运行期状态，不落盘**（挂起的轮次
        # 不值得持久化）；进程重启后退回 0，代价只是"可能又从头点一次名"。
        self._pointer: Dict[str, int] = {}
        # 本次进程内已解散的群 id（运行期状态，不落盘）。只为一件事：让正在跑的那一轮
        # 在下一个候选之前就地收尾 —— 否则它会继续点名，把发言落进一个已经从名册里
        # 摘掉、jsonl 也已删掉的对象（丢话 + 指针复活）。
        self._dissolved: set = set()
        # ── 提案制（缔结类关系 / 讨要 / 赠予的"两阶段"）──────────────────────────
        # 三张**内存**表，与 `_pointer` 同级：挂起的东西不值得持久化 —— 进程重启后
        # 提案消失 = 人们重聊一遍（可接受），而"提案过了三个回合还算数"才是灾难。
        #   `_proposals` key = (room_id, kind, 判别串, frozenset({发起人, 对手方}))；
        #               kind ∈ {"rel"(缔结类关系), "ask"(讨要), "give"(赠予，单件)}；
        #               判别串 = op（rel）或 item_name（经济两类）—— 方向**不在 key 里**，
        #               在记录的 proposer/target 上（一次查表即可判"你在重复提"还是"你在回应"）。
        #   `_round_no`  每房间的轮次计数（提案过期坐标；同样不落盘）
        #   `_notices`   待注入私信：npc_id → [(提案 key|None, 文本)]，回合开跑前领走
        self._proposals: Dict[tuple, Dict[str, Any]] = {}
        self._round_no: Dict[str, int] = {}
        self._notices: Dict[str, List[tuple]] = {}
        # TTL（完整回合数）：实例属性而不是常量直读 —— 测试可以调小它把过期跑到边界。
        self.proposal_ttl_rounds = PROPOSAL_TTL_ROUNDS

    # ══════════════════════════════════════════════════════════════
    # 名字册 / 生命周期
    # ══════════════════════════════════════════════════════════════

    def set_max_speakers_per_round(self, value: Any) -> int:
        """改「一轮里最多几个人真的开口」（K），返回生效后的值。

        热生效入口：跑轮时**每轮现读** `self.k`（见 `_run_round` 的名额判定），
        所以改完对**下一轮**生效，不需要重启进程、也不用重开群窗。

        夹取口径与构造参数一致（`max(1, int(value))`）：0/负数没有语义（一轮谁都不许
        开口＝群聊停摆），一律抬到 1；**不封顶** —— 候选名单本来就以成员数为上限
        （`plan_round`），所以填一个很大的值等于"每轮所有成员都可能开口"。
        解析失败（非数字）时保持原值不动并只记日志，绝不把 K 变成 0。
        """
        try:
            new_k = max(1, int(value))
        except (TypeError, ValueError):
            log.warning("每轮发言人数上限改不动（不是整数）：%r，保持 k=%s", value, self.k)
            return self.k
        old_k = self.k
        self.k = new_k
        if new_k != old_k:
            log.info("每轮发言人数上限已生效：k %s → %s（下一轮起）", old_k, new_k)
        return new_k

    def _rooms(self) -> Dict[str, Room]:
        return self.loop.rooms

    def _require(self, room_id: str) -> Room:
        rid = str(room_id or "")
        room = self._rooms().get(rid)
        # 读档后内存里可能只有**名录**（`AgentLoop.load_rooms` 刻意不读流水，见其
        # docstring）—— 这里统一补齐，两种情形都要覆盖：
        #   ① 内存完全没有（未被 load_rooms 载入）⇒ 整份读；
        #   ② 有名录、没流水（懒加载的中间态）⇒ 只补读流水。
        # ★ 必须在**任何** `last_seq` 被读之前发生：`last_seq` 是 `Room.say` 的 seq
        #   来源，也是 `add_member(joined_at=)` 与游标推进的坐标 —— 它假报 0 会让
        #   读档后的新发言从 seq=1 重发，与磁盘上的旧流水**撞号**（静默数据损坏）。
        # 注入方（`ChatHub._load_room_data`）保证：对已在内存且有流水的群无 IO 早退。
        if self._load_room is not None and (room is None or not room.log):
            loaded = self._load_room(rid)
            if loaded is not None:
                room = loaded
        if room is None:
            raise KeyError(f"没有这个群：{rid!r}")
        return room

    def open_room(self, members: Iterable[str], room_id: str = "",
                  title: str = "", member_unit_ids: Optional[Dict[str, str]] = None) -> Room:
        """建群或重开。`room_id` 给了就重开那个群（成员以库里为准，可另传 members 加人）。

        **同一批人不会自动合成同一个群** —— 那是 C# 的入口策略（通讯录里点哪个群就开哪个）；
        Python 只认 `room_id`。重开时 `id` 不变 ⇒ 历史与游标都续上。

        `member_unit_ids`（可选）= `{npc_id: unitID}`，**可以只覆盖一部分成员**。
        C# 在「建群选人确认」那一刻手里握着真身（通讯录关系层条目就是按 unitID 精确查出来的），
        所以这条路能给出精确身份；老调用点不给也不报错，只影响"渲染他的立绘要多扫一次全图"
        （而重名时那一次扫描**可能扫到另一个人**，见 `Room.member_unit_ids` 的注释）。
        """
        uids = member_unit_ids or {}
        rid = str(room_id or "").strip()
        if rid:
            room = self._require(rid)
            if title:
                room.title = str(title)
            # 重开时另传的 members 是**中途加人**（群已有流水）⇒ 入群位置 = 当前末尾，
            # 他们看不到入群前的话。这与"建群时的初始成员"是两回事，别合并。
            added = [m for m in (members or []) if room.add_member(m, joined_at=room.last_seq,
                                                                 unit_id=str(uids.get(str(m)) or ""))]
            # 重开时也可能**补上**老成员的精确身份（本功能上线前建的群，这次开窗时 C# 手上有了）
            for m in list(room.members):
                fresh = str(uids.get(m) or "").strip()
                if fresh and room.member_unit_ids.get(m) != fresh:
                    room.member_unit_ids[m] = fresh
            if added:
                self._on_members_changed(room, added, [])
            return room
        # 新建：初始成员的入群位置 = 0（这一整场他们都要经历）
        return self.create_room(members, title=title, member_unit_ids=uids)

    def create_room(self, members: Iterable[str], title: str = "",
                    member_unit_ids: Optional[Dict[str, str]] = None) -> Room:
        """**只建群，不回放历史** —— 通讯录「＋新建群聊」确认走这条路。

        与 `open_room` 的分工是刻意的：**建群是改数据，开窗是开窗**。

          ① **语义**：通讯录建完群要**留在通讯录**（玩家点那一行才开窗，与"点好友开单聊"
             对称）。让建群去走开窗的 API，就等于把"弹出一个对话窗"当成建群的副作用 ——
             而失败时那个窗已经开了、历史是空的、标题只能显示占位符（09-17 实机）。
          ② **依赖**：`open_room` 必须回放历史 ⇒ 必须 import `room_history`（群窗投影）。
             建群根本用不到投影。分开之后**建群不再依赖那个模块** —— 这正是 09-17
             `zlib.error: incorrect header check` 的爆点（函数内 import 一个从没加载过的
             模块，而服务器 exe 在运行中被换掉）。少一个依赖就少一类"只在这条路上才炸"。
          ③ **入口唯一**：C# 侧 `open_room` 的发送点被契约钉死**只有 1 处**
             （群窗的唯一开启路径，见 `test_there_is_exactly_one_open_room_path`）。
             通讯录再发一个就是第二个真相源。

        初始成员的入群位置 = 0（这一整场他们都要经历）；不走 `enter/leave`
        （群标签只在**发言回合**内有效，见 `enter`）——所以建群是纯数据操作，
        不会留下"人已入场却没开窗"的中间态。
        """
        uids = member_unit_ids or {}
        # ★ `retired` 必须一起传：只排现有 id 的话，刚解散的 id 会被立刻发回来，
        #   而老成员的 `room/cursor` 还指着它 ⇒ 他们在新群里收不到任何消息（见 `new_room_id`）。
        #   用 getattr 是因为 `loop` 是注入的：测试桩不保证带这个字段（缺了就等于"没有退休"）。
        rid = new_room_id(self._rooms().keys(), getattr(self.loop, "retired_rooms", None) or ())
        room = Room(id=rid, members=[], created=int(time.time() * 1000))
        for m in members or []:
            room.add_member(m, joined_at=0, unit_id=str(uids.get(str(m)) or ""))
        room.title = str(title or "").strip() or room.default_title()
        self._rooms()[rid] = room
        log.info("建群：%s（%s）成员=%s（精确身份 %d/%d）",
                 rid, room.title, room.members, len(room.member_unit_ids), len(room.members))
        return room

    def close_room(self, room_id: str) -> bool:
        """关窗：全员 `leave()`（成员 `scene` 复位成 `""`）。**不销毁群**、不清流水。"""
        rid = str(room_id or "").strip()
        room = self._rooms().get(rid)
        if room is None:
            return False
        for m in list(room.members):
            agent = self.loop.get(m)
            if agent is not None:
                self.leave(agent)
        self._pointer.pop(rid, None)
        log.info("关窗：%s（成员 scene 已复位；群与流水保留）", rid)
        return True

    def dissolve_room(self, room_id: str) -> bool:
        """**解散**：群没了、公共账本没了。与 `close_room` 是两件事（后者只关窗）。

        删掉的是**容器**：内存名册 + `index.json` 条目 + `<id>.jsonl` 流水 + 成员的在场标记。
        **不删**的是每个 NPC 的记忆 —— 成员 session 里那些 `source.kind="peer"` 的消息
        是他**经历过**的事，与"一个 session = 他经历的一切"这条定案一致；
        session 里的 `room/cursor` 注记同样**刻意留着**（清它要动 session schema 并维持
        "reset 之前一律不算"的新读取规则，而 `cursor_for` 必须对四条下游路径隐形）。
        代价是群 id **绝不能重用** —— 由 `room.new_room_id` 的 `retired` 高水位保证。

        顺序：先摘内存名册（此后任何 `_require` 都会 KeyError ⇒ 新消息进不来），
        再全员 `leave()`，最后交给注入的 `delete_room` 落盘（它保证"先 index 后 unlink"）。

        **并发**：本方法**同步**，RPC 处理路径也是同步的，所以拿不到 `room_lock`
        （asyncio 锁要 await）。撞上正在跑的一轮时：那一轮手里的 `Room` 对象已经不在
        名册里，它 `say` 出来的话会落进一个没人再引用、jsonl 也已删掉的对象 ⇒
        **丢掉一句**（不损坏，只是丢）。`self._dissolved` 让 `_run_round` 在下一个候选
        之前就地收尾，把窗口收到最小；`finally` 里的 `room_state(phase="end")` 照常发，
        所以 C# 的忙碌态不会挂。
        """
        rid = str(room_id or "").strip()
        room = self._rooms().pop(rid, None)
        if room is None:
            return False
        self._dissolved.add(rid)
        # 全员出群：与 close_room 同一套。漏掉的后果是"之后的单聊回合都带着群标签，
        # 在 1:1 窗里凭空消失，而且**是静默的**"（同 `leave` 的注释）。
        for m in list(room.members):
            agent = self.loop.get(m)
            if agent is not None:
                self.leave(agent)
        self._pointer.pop(rid, None)
        # 提案随群消亡：群都不在了，未收口的提案无处安放；尚未送达的私信一并丢掉
        # （否则会对着一条永远无法回应的"向你提出…"发呆）。
        for _key in [k for k, r in self._proposals.items() if r.get("room_id") == rid]:
            self._proposals.pop(_key, None)
            self._drop_notices(_key)
        self._round_no.pop(rid, None)
        if self._delete_room is not None:
            try:
                self._delete_room(rid)
            except Exception:
                # 内存已经摘了、盘上还在 ⇒ 重开游戏这个群会"复活"。如实记，不假装成功。
                log.exception("解散 %s：落盘收尾失败（内存已摘，重开游戏后可能重现）", rid)
        log.info("解散：%s（%s）成员 %s 已复位，%d 条流水作废",
                 rid, room.title, room.members, len(room.log))
        return True

    def add_member(self, room_id: str, npc_id: str, unit_id: str = "") -> bool:
        """中途加人：**入群位置 = 当前末尾** ⇒ 他看不到入群前的话（与微信一致）。

        代价是他对之前聊过的事一无所知；想让新人对上下文有底，应在
        nudge 或规则段里给一句由 `Room.log` 折叠的短摘要（P2 可选，不在 P1）。

        `unit_id` 可选（C# 在「拉人确认」那一刻握着真身时会带上），缺了就只影响立绘取图。
        """
        room = self._require(room_id)
        if not room.add_member(npc_id, joined_at=room.last_seq, unit_id=unit_id):
            return False
        self._on_members_changed(room, [str(npc_id)], [])
        return True

    def kick(self, room_id: str, npc_id: str) -> bool:
        room = self._require(room_id)
        if not room.kick(npc_id):
            return False
        self._on_members_changed(room, [], [str(npc_id)])
        return True

    def _on_members_changed(self, room: Room, added: List[str], removed: List[str]) -> None:
        """成员变动后的收尾。

        ★ 这里**不**去刷新他人的 `roster` —— 早期设计写的是"遍历在场者重设 roster"，
        但 roster 的作用域已收敛到**回合**（见 `enter`/`leave` 与 §4.3 的说明）：
        下一个回合开跑前会按最新 `members` 现算，L1「在场」段的差分自然触发。
        真正的义务只有一条：**被踢的人如果正开着群标签，要复位**，否则他之后的单聊
        回合会带着群标签，在 1:1 窗里**静默**消失。
        """
        for m in removed:
            agent = self.loop.get(m)
            if agent is not None:
                self.leave(agent)
        log.info("群 %s 成员变动：+%s -%s → %s", room.id, added, removed, room.members)

    # ══════════════════════════════════════════════════════════════
    # 场合标记（进群 / 出群）
    # ══════════════════════════════════════════════════════════════

    def enter(self, agent: Any, room: Room) -> None:
        """打上群标签。**在持有该 agent 回合锁的前提下调用**（见模块头锁序）。

        名单与**身份**同一次写（`set_roster(names, unit_ids)` 单入口）：
        名单给模型（「在场」段/同席段，全是中文），身份给 C#（取数/取图/工具落刀）。
        分开写就会出现"名单是新的、身份是上一轮的" —— 取数会挂到**别人**身上，
        而名字看着完全正常（比全空更糟）。
        """
        agent.set_scene(room.id)
        agent.set_roster(room.roster_for(agent.id), room.member_unit_ids)
        # 我自己是谁（供工具注入 `initiator_id`，**模型看不到这个键**）。
        # 取不到就是空串 ⇒ 不注入 ⇒ C# 回落按名，与本功能上线前逐字一致。
        try:
            agent.set_unit_id(room.unit_id_of(agent.id))
        except Exception:
            pass

    def leave(self, agent: Any) -> None:
        """复位成单聊。

        ★ `set_scene("")` 在全代码里只有**两个**调用点，都在复位方向、互为防线：
        ① 本方法（成员被踢 / 关窗时）；
        ② `ChatHub._run_turn` 的 `finally`（每个回合结束都复位）。
        漏设的后果是"之后的单聊回合都带着群标签 ⇒ 在 1:1 窗里凭空消失，
        而且**是静默的**"（回合照跑、模型照看，只有 UI 少东西）。

        早期的 docstring 这里写的是"只有这一个调用点"，与紧邻的下一句自相矛盾，
        —— 复核时改正。**设置方向**（`set_scene(room.id)`）同样有两处：
        本类的 `enter()`，以及 `_run_turn(scene=...)` 的形参。
        """
        agent.set_scene("")
        agent.set_roster([])          # 名单与身份一起清（单入口，见 set_roster）
        # 我自己是谁也一并复位：出群之后这个字段没有意义，留着会让**之后的单聊回合**
        # 把一个群里查来的 unitID 当成自己的（换存档/换人时会静默错人）。
        try:
            agent.set_unit_id("")
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════
    # 投递（核心）
    # ══════════════════════════════════════════════════════════════

    def deliver(self, room: Room, agent: Any) -> List[Utterance]:
        """把「比我的游标新、且**不是我说的**」的发言按序投给我。返回投了哪些。

        为什么走 inbox 的 `next-step`（而不是直接 append）：见模块 docstring。
        `Inbox.claim()` 先吃光 `next-step`、再吃一条 `next-turn` ⇒ 全部增量按序落在
        本回合 step 1，紧跟着才是 nudge —— 正是想要的顺序。

        **顺序规则**：① 先投递（`agent.send`，落一条 `agent/inbox/spliced`）
        ② 后推游标（`session.append("room/cursor")`）。两条都 append 到**同一个 log**、
        走**同一次 flush**，顺序由 append 顺序保证。反过来（先推游标）的话，
        "消息还在路上"这件事就没有记录，崩溃时会丢。
        """
        me = str(agent.id)
        cursor = agent.cursor_for(room.id)
        delta = room.delta_for(me, cursor)
        for u in delta:
            # ★ 纯点名（玩家只写了 "@张三"）：流水里存的是原文（群窗要显示玩家真说的话），
            #   而模型看到的应该是**一句促发语** —— 否则它会收到「@张三」这四个字符，
            #   以为玩家在念一个符号。替换只发生在**投递副本**上，`Room.log` 不动。
            #   为什么不用 plugin 消息：`deliver` 是"把群里说过的话按序投给我"的唯一入口，
            #   在这里替换等于"这条发言对模型的长相"，与 speaker 前缀（`_apply_speaker_labels`）
            #   同一层——都是"发给模型的这一份"，不是账本原文。
            _text = PURE_PING_TEXT if getattr(u, "pure_ping", False) else u.text
            agent.send(
                _text,
                target="next-step",      # 回合内落面，不唤醒
                wake=False,
                # ★ 玩家的话**从 09-17 起也带锚点**（`kind="room"`）：群窗删除要在各成员
                #   记忆里按 uid 定位这一条，而"逐字节与单聊相同"（`source=None`）当年
                #   是刻意留的、恰恰让玩家消息事后定位不到。单聊路径**不变** ——
                #   那条契约（`handle_message` 不传 source）没有放松，R1 的断言口径随之更新。
                #   `_apply_speaker_labels` 有对应分支把它渲染成 `玩家：`；`history.py`
                #   有显式分支落回 user 气泡（其兜底本就是"未知 kind → user"）。
                source={"kind": "peer", "speaker": u.speaker, "uid": u.uid}
                       if u.speaker != PLAYER
                       else {"kind": "room", "speaker": PLAYER, "uid": u.uid},
            )
        # 游标推进：`cursor is None` 时即使 delta 为空也要写 —— 那是"入群"这件事的记录
        # （新成员从此开始听，看不到入群前的话）。已在同步位置且无新增则不写，免噪声。
        if cursor is None or delta:
            agent.session.append("room/cursor", {"room_id": room.id, "seq": room.last_seq})
        return delta

    # ══════════════════════════════════════════════════════════════
    # 跑一轮
    # ══════════════════════════════════════════════════════════════

    async def _say_and_emit(self, room: Room, speaker: str, text: str,
                            narration: bool = False,
                            mentions: Optional[List[str]] = None,
                            pure_ping: bool = False,
                            echo_text: str = "") -> Utterance:
        """落账 + 广播，作为**一个不可分割的单元**（`say_lock` 短临界区）。

        `narration=True` = **旁白**：照常落账、照常广播，但帧上多带 `narration:true` ——
        C# 群窗据此把它画成**「行动记录」行而不是气泡**（`ChatPresenter.ApplyRoomSay` 的旁白分支
        → `ChatWindow.AppendActionRow`）。为什么必须当旁白（09-22 用户报）：`（与唐炎切磋了一场 —— …）`
        这类第三人称叙述走气泡通道会被画成"这位 NPC 说了这句话"，而行里还夹着**玩家的原话** ——
        挂谁名下都不对。形式选「工具返回结果那种行」是用户拍板的：那条旁白讲的就是"发生了一次
        工具调用带来的后果"，与工具行同域；重开窗走 `project_room_history` 的 `tool_result` 条目，
        与直播同形。

        为什么要一把锁：`Room.say` 是同步的（asyncio 单线程下 seq 分配无竞态），
        但紧随其后的 `_emit_say` 会 `await` 一次 WS 写。两条发言的"落账 + 广播"
        一旦交错，**UI 帧的到达顺序就可能不等于 `Room.log` 的 seq 顺序** ——
        群窗上表现为"你后说的那句话显示在前"。这把锁就是钉住这一个不变量的。

        锁序：三个调用方向都是**单向**的，从不反向索要 `room_lock` / `agent_lock`
        ⇒ 无环、无死锁：
          ① `room_lock` 内（`_run_round` 落 NPC 的账）；
          ② 单独（`run_message` 落玩家的账，在锁外）；
          ③ `agent_lock` 内（提案闸门落提案流水 —— 见 `_make_gate`）。
        临界区是"一次同步 say + 一次 WS 写"（毫秒级），所以插话等它也不会退化成"等整轮"。
        """
        async with self._say_lock_impl(room.id):
            u = room.say(speaker, text, narration=narration, mentions=mentions,
                         pure_ping=pure_ping)
            await self._emit_say(room, u, echo_text=echo_text)
        return u

    def _note_own_utterance(self, room: Room, npc_id: str, seq: int,
                            node: Optional[int] = None) -> None:
        """把「这条群发言 = 我账本里的哪个节点」记进**发言者自己**的 session（best-effort）。

        为什么要单记一条：NPC 自己的话不投递给自己（`Room.delta_for` 排除 me），
        于是它账本里那一条是 `assistant/message`、没有 `source.uid` 可认 —— 群窗删除
        要能"连他自己说的那句也一并遗忘"，就得有这个锚点（见 `DialogueAgent.room_utt_nodes`）。

        `node` 由 `_one_speaker` 在**持锁时**取好并传进来（理由见它的 docstring）。

        失败只记日志：锚点丢了最多让这条以后删不掉"他自己的那份"，不该拖垮一轮发言
        （与 `_emit_say` 的尽力而为同一条纪律）。
        """
        try:
            agent = self.loop.get(npc_id)
        except Exception:
            agent = None
        note = getattr(agent, "note_own_utterance", None)
        if note is None:
            return
        try:
            note(room.id, seq, node)
        except Exception:
            log.warning("群 %s：%s 的发言锚点落账失败（seq=%s）", room.id, npc_id, seq,
                        exc_info=True)

    # ══════════════════════════════════════════════════════════════
    # 群窗删除一条发言（§9.2：群记录 + 各成员记忆，两边同删）
    #
    # 三个落点（缺一不可）：
    #   ① 群记录：`Room.deleted_seqs` 墓标 —— `delta_for` 跳过（还没听到的人永不再收到）、
    #      `project_room_history` 跳过（群窗与重开回放一致）；
    #   ② 各成员记忆：逐成员 `history/delete{reason:"room_delete"}`（走 `drop_seqs` 路径）；
    #   ③ 顺序：**先后者再墓标** —— 崩在中途重试可收敛（幂等），反过来会"群记录没了、
    #      记忆还在"且无从补救。
    #
    # 守卫分两层：成员进程态（回合中/压缩中）与"一轮进行中"在 `ChatHub` 的 RPC 层
    # （那儿才看得见 `_compacting` 与 `room_lock`）；"待回应提案的叙述行"在本模块
    # （提案表在这儿）。任何一条不满足 ⇒ **整体拒绝**，不做"跳过某个成员"。
    #
    # 只清**当前成员**的记忆：已被踢出群的人那份**刻意不动** —— 与"删群 ≠ 删记忆"
    # 同一条口径（他确实经历过那句话；而且他可能已经不在我们管辖的名单里了）。
    # ══════════════════════════════════════════════════════════════

    def _room_delete_targets(self, room: Room, uids: Iterable[str]) -> List[int]:
        """uid 列表 → 房间 seq 列表。fail-closed 校验，**不许跨群、不许发明 uid**。"""
        out: List[int] = []
        for raw in uids or []:
            s = str(raw or "").strip()
            if not s:
                continue
            head, _, tail = s.rpartition("#")
            if head != room.id or not tail.isdigit() or int(tail) <= 0:
                raise ValueError(f"uid 不属于本群（{room.id}）：{s!r}")
            seq = int(tail)
            if not any(u.seq == seq for u in room.log):
                raise ValueError(f"群里没有这条发言：{s!r}")
            if seq in room.deleted_seqs:
                raise ValueError(f"这条发言此前已被删除：{s!r}")
            if seq not in out:
                out.append(seq)
        if not out:
            raise ValueError("需要至少一个 uid（要删哪条发言）")
        return out

    def _guard_pending_proposal_lines(self, room: Room, seqs: Sequence[int]) -> None:
        """待回应提案的**叙述行**挂起期间禁止删（§9.2 定稿的守卫）。

        不挡的话会出现"提案还挂着、公开记录没了"：对手方的私信指向一条不存在的当众之言，
        而发起人那边等来的可能是"应了"或"始终没有回应"—— 两种结局都在对着空气说话。
        收口（应下 / 过期）后记录被消费，这条行照常可删。
        """
        pending = {int(r.get("seq") or 0) for r in self._proposals.values()
                   if r.get("room_id") == room.id}
        blocked = sorted(set(int(s) for s in seqs) & pending)
        if blocked:
            raise DeleteError("这条发言是仍在等待回应的提案（等它收口后再删）："
                              + "、".join(f"{room.id}#{s}" for s in blocked))

    def plan_room_delete(self, room_id: str, uids: Iterable[str]) -> Dict[str, Any]:
        """**预演**群窗删除：算出每名成员要剔除什么、哪些删不掉（账本零改动）。

        每个成员的方案逐条套 `history_ops.plan_message_delete`（与"删回合"同款规则：
        纪要覆盖闭包 / 替换节点豁免 / fail-closed 配对校验）。任一成员的方案会把它的账本
        弄成配对不平衡 ⇒ **整体拒绝**（`DeleteError`）—— 预览与执行走**同一条**计算路径，
        所以预览说"能删"而执行拒绝这种事不会发生。
        """
        room = self._require(room_id)
        seqs = self._room_delete_targets(room, uids)
        self._guard_pending_proposal_lines(room, seqs)
        report, plans = self._plan_room_delete(room, seqs)
        report["dry_run"] = True
        report["history"] = self.history(room.id)
        return report

    def _memory_agent(self, npc_id: str) -> Any:
        """取该成员的**记忆**：内存（活体 / 冻结舱）优先，其次**磁盘账本**（resume 加载）。

        ★ 三级缺一不可，每一级都对应一种"记得但容易看不见"的成员：

        - **活体**（`store`）：正开着窗 / 刚跑过回合的人；
        - **冻结舱**（`_frozen`）：关过窗但未固化的人 —— 本工程"关窗"是**冻结而不是
          销毁**，账本还活生生在内存里、重开窗原样复活。只看活体会把他报成"没有副本"；
        - **磁盘**（jsonl）：**重启 / 读档后尚未激活**的人（`discard_all` 清空内存，
          而开群窗不激活任何成员）—— 他磁盘账本里可能留着当初投递的副本，
          只看内存会漏删，删除后他一开窗照旧记得（**静默**，见 §9.2 的 fan-out）。

        磁盘**没有**账本 ⇒ 返回 None（如实报"没有可删的副本"，不空建对象）；
        磁盘**有**但读不出来 ⇒ 抛错（由 `DeleteError` 转成整体拒绝 ——
        **宁可删不成，不许静默漏删**）。

        与"要驱动回合"的路径相反：那边必须用 `loop.get`（冻结体 / 磁盘态不凭空驱动
        回合，得先 create 复活）—— 本方法只服务"要改记忆"的删除 fan-out。
        """
        get_any = getattr(self.loop, "get_any", None)
        agent = get_any(npc_id) if get_any is not None else self.loop.get(npc_id)
        if agent is not None:
            return agent
        resume = getattr(self.loop, "resume_if_on_disk", None)
        if resume is None:
            return None                      # 老管家（无此方法）：退回纯内存口径
        try:
            return resume(npc_id)
        except Exception as e:
            raise DeleteError(
                f"「{npc_id}」的记忆在磁盘上读取失败，已整体拒绝删除"
                f"（不许静默漏删）：{e}") from e

    def _plan_room_delete(self, room: Room, seqs: Sequence[int]):
        """→ (报告, [(npc_id, agent|None, plan|None)])。**不写任何账本**。

        "不写"包括预演路径：`_memory_agent` 可能把磁盘账本 **resume 进内存**
        （重启/读档后未激活的成员），那只是**读盘缓存**——不追加任何事件、不改墓碑，
        等价于"打开过他的窗"；真正的写入在 `room_delete` 的执行段。
        """
        plans: List[tuple] = []
        members: List[Dict[str, Any]] = []
        for m in list(room.members):
            agent = self._memory_agent(m)
            if agent is None or not hasattr(agent, "room_utt_nodes"):
                # 内存与磁盘都没有 ⇒ 记忆里本就没有（零成本成员，账本都没建过）
                members.append({"npc": m, "no_memory": True, "removed_nodes": 0,
                                "deleted": [], "folded": [],
                                "absent": [f"{room.id}#{s}" for s in seqs],
                                "note": "内存与磁盘都没有他的副本（从没被投递过）"})
                plans.append((m, None, None))
                continue
            nodes_by_seq: Dict[int, List[int]] = {s: agent.room_utt_nodes(room.id, s)
                                                  for s in seqs}
            targets = sorted({n for ns in nodes_by_seq.values() for n in ns})
            plan = plan_message_delete(agent.session, targets) if targets else None
            if plan is not None and not plan.get("ok"):
                raise DeleteError(f"「{m}」的记忆：{plan.get('reason')}")
            found = set(plan["found"]) if plan else set()
            folded = set(plan["folded"]) if plan else set()
            deleted, folded_uids, absent = [], [], []
            for s in seqs:
                ns = set(nodes_by_seq.get(s) or [])
                uid = f"{room.id}#{s}"
                if ns & folded:
                    folded_uids.append(uid)      # 已折进纪要、保留（无法逐字遗忘）
                elif ns & found:
                    deleted.append(uid)
                else:
                    absent.append(uid)           # 没听到 / 后加入 / 此前已被删（1:1 删回合等）
            members.append({
                "npc": m, "no_memory": False,
                "removed_nodes": int(plan["removed_nodes"]) if plan else 0,
                "deleted": deleted, "folded": folded_uids, "absent": absent,
                "note": ("部分记忆已被压缩且覆盖范围无法核对（老账本），"
                         "可能删不干净") if (plan and plan.get("unknown_summaries")) else "",
            })
            plans.append((m, agent, plan))
        report: Dict[str, Any] = {
            "ok": True,
            "room_id": room.id,
            "uids": [f"{room.id}#{s}" for s in seqs],
            "members": members,
        }
        return report, plans

    def room_delete(self, room_id: str, uids: Iterable[str]) -> Dict[str, Any]:
        """**执行**群窗删除：各成员先落 `history/delete`，最后写 Room 墓标。

        前置校验与预演同源（`_room_delete_targets` + 提案守卫 + 逐成员方案），
        所以"预览通过 ⇒ 执行不会因方案被拒"。执行段**没有 await** ⇒ 在 asyncio
        单线程下与正在开跑的一轮天然互斥（`ChatHub` 另有一道 `room_lock.locked()` 守卫）。
        """
        room = self._require(room_id)
        seqs = self._room_delete_targets(room, uids)
        self._guard_pending_proposal_lines(room, seqs)
        report, plans = self._plan_room_delete(room, seqs)
        # ① 各成员 session：剔除（**先 session**，崩在中途重试可收敛）
        touched = []
        for npc, agent, plan in plans:
            if plan is None:
                continue
            append_delete(agent.session, plan, reason="room_delete")
            touched.append((npc, plan["removed_nodes"]))
        # ② 群记录：墓标（`Room.log` 保持只增不改）
        for s in seqs:
            room.delete_utterance(s)
        report["dry_run"] = False
        report["history"] = self.history(room.id)
        log.info("群窗删除：%s 删 %s；成员剔除 %s",
                 room.id, report["uids"], [f"{n}:{k}" for n, k in touched])
        return report

    # ══════════════════════════════════════════════════════════════
    # 提案制（表 / 闸门 / 私信 / 过期）
    # ══════════════════════════════════════════════════════════════

    def _queue_notice(self, npc_id: str, text: str, key: Optional[tuple] = None) -> None:
        self._notices.setdefault(str(npc_id), []).append((key, str(text)))

    def drain_notices(self, npc_id: str) -> List[str]:
        """取走该 NPC 的待注入私信（`ChatHub._run_turn` 在回合开跑前调用，见其注释）。"""
        return [t for _, t in self._notices.pop(str(npc_id), [])]

    def _drop_notices(self, key: tuple) -> None:
        """丢掉某条提案**尚未送达**的私信（过期时用：不能再对一个已作废的提议说"向你提出…"）。"""
        for who in list(self._notices):
            self._notices[who] = [x for x in self._notices[who] if x[0] != key]
            if not self._notices[who]:
                self._notices.pop(who, None)

    def _make_gate(self, room: Room, speaker: str):
        """"群聊回合的策略闸门"（闭包注入 `agent.tool_gate`）—— 判放行 / 登记提案 / 拦重复。

        为什么是闭包而不是参数：判据需要**能力**（members、写 Room.log、查表），不是信息。
        挂载点由 `_one_speaker` 注入、由 `ChatHub._run_turn` 的 finally 复位（与场合标记同处）；
        单聊回合根本不挂 ⇒ 这条链在单聊上**一行都不跑**。

        锁序：本闸门在发言者回合内（持 turn_slot + agent_lock）被调，落提案流水时取
        `say_lock` ⇒ 新增 `agent_lock → say_lock` 一条边（单向，见模块头）。

        原子性：判据 + 登记是**同步段**（中间没有 await）—— 同一条助手消息里的并行
        tool_calls（模型一次吐两个一样的调用）不会双开提案；只有落流水才 await。
        接受分支同理：pop 是同步段 ⇒ 并发的重复"应下"只有一个能消费到记录。

        "应下"与"解除类"的**收尾**（公开叙述行 / 失败私信）不在这里做：返回
        `__pass_after__` 标记，由 `DialogueAgent` 在 bridge 返回后调
        `_make_accept_settle` / `_make_break_settle` 的闭包 ——
        只有知道落刀**真实结果**才写得对（成功才宣告、失败不虚报）。
        """
        async def gate(name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            # ★ 边界翻译已**收编到 `DialogueAgent._inject_person_ids`**（09-17）：
            #   群聊与单聊现在走同一个注入点（映射表 = 自己 + 玩家 + 同席 + 查询学到的），
            #   闸门只负责判据 —— 不再自己译一份（两份实现必然漂移；且单聊没有闸门，
            #   那条路以前压根拿不到 `target_id`）。判据吃的仍是中文名。
            verdict, key, text = judge_proposal(
                name, args, caller=speaker, members=room.members,
                proposals=self._proposals, room_id=room.id)
            if verdict == "pass":
                # 解除类（对象是在场 NPC）：单方面直写（不走提案），但**结果公开** ——
                # 用 pass_after 在落刀成功后补一条叙述行（与缔结的应下对称）；
                # 失败不公开（没发生的事不进公共流水），动作方自己在工具结果里看到错误。
                if (name == "social_relation" and str(args.get("op") or "") in REL_BREAK_OPS
                        and str(args.get("target") or "").strip() in room.members):
                    return {"__pass_after__": True,
                            "after": self._make_break_settle(room, speaker, args)}
                # 战斗类（切磋 / 攻击）：对象只能是玩家（C# 硬拒 NPC→NPC），所以它们**没有提案可言**
                # —— 玩家本人就是应答方，原生剧情窗就是他的"接受/拒绝"。但这件事全群都该知道：
                # 应战之后它要占掉一整场战斗的时间（`_run_round` 持 `room_lock` 串行跑成员，见 :1418），
                # 一句话不说，别的成员只会以为这一轮卡死了。
                if name == "world_ai_action" and str(args.get("op") or "") in ("spar", "attack"):
                    return {"__pass_after__": True,
                            "after": self._make_battle_settle(room, speaker)}
                return None
            if verdict == "block":
                return {"success": False, "error": text}
            if verdict == "accept":
                # 对手方在回应：**消费**（放行即消费 —— 重复调用不会二次落刀；它若顽固重提，
                # 会记成一条新提案重新等回应，语义自洽），但**不在这里发回执** ——
                # 把收尾交给 bridge 返回后的事后钩子（见 `_make_accept_settle`）：
                # 成功才写**公开叙述行**（应下是当众的事，与提出那条对称）；失败私信发起人。
                rec = self._proposals.pop(key, None)
                if rec is None:
                    return None                 # 理论到不了（判据刚命中）；放行即可
                return {"__pass_after__": True,
                        "after": self._make_accept_settle(room, speaker, rec, name, args)}
            rec = _build_record(key, room.id, speaker, args,
                                self._round_no.get(room.id, 0) + self.proposal_ttl_rounds)
            self._proposals[key] = rec
            self._queue_notice(rec["target"], _proposal_note(rec), key)
            u = await self._say_and_emit(room, speaker, _proposal_line(rec))
            # ★ 记下这条叙述行的 seq：**挂起期间它是"删不得"的**（见 `room_delete` 的守卫）——
            #   "提案还挂着、公开记录没了"会让对手方收到一条指向不存在之物的私信。
            #   收口（应下/过期）后记录被消费，这条行就照常可删。
            rec["seq"] = u.seq
            log.info("提案：%s → %s（%s，叙述行 seq=%s）",
                     rec["proposer"], rec["target"], rec["kind"], u.seq)
            return _proposal_result(rec)
        return gate

    def _make_battle_settle(self, room: Room, speaker: str):
        """战斗类动作（切磋 / 攻击）落定后的**公开叙述行**（群聊），由 `DialogueAgent` 在 bridge 返回后调。

        为什么不在发起时就写一行：那会儿 C# 还没过"同格"那道闸，它可能直接回绝（异地），
        而"没发生的事不进公共流水"是本文件既有的规约（accept/break 两条收尾同理）。
        等结果出来再写，一行就把"发生了"和"打成什么样"一起说清 —— 反正这一轮本来就被
        `room_lock` 串行挡住，别人等的是整场战斗，不是这一行字。

        ★ **旁白、不进群窗**（09-22 用户报：这行原来被画成 `speaker` 的气泡）★
        它是**写给别的成员的账本**的（别以为这一轮卡死了），不是那位 NPC 的台词 ——
        而且行里引用了**玩家的原话**，挂在谁名下都不对。玩家侧不丢信息：那次工具调用
        与结果本来就在「行动记录」里（群聊 `step` 帧带 `room_id`）。
        机理见 `Room.say(narration=)` 与 `room_history.project_room_history` 的跳过。
        """
        async def after(res: Optional[Dict[str, Any]]) -> None:
            line = battle_line(speaker, res if isinstance(res, dict) else None)
            if not line:
                return
            await self._say_and_emit(room, speaker, line, narration=True)
        return after

    def _make_accept_settle(self, room: Room, speaker: str, rec: Dict[str, Any],
                            name: str, args: Dict[str, Any]):
        """"应下"的**收尾闭包**（bridge 返回后由 `DialogueAgent` 调，参数 = 落刀结果）。

        - 成功 → 写**公开叙述行**（挂对手方名下，落 `Room.log`）：与提出那条对称 ——
          全群可见、重开窗可回放、每个成员的 session 里都会收到（经 `deliver`）。
          发起人不再需要私信：他和其他人一样在群里看到"应了"。
        - 失败 → **不公开**（事没成，就不是既成事实），改私信发起人一句实话。

        锁序：本闭包在发言者回合内（持 agent_lock）被调，落公开行时取 `say_lock` ——
        与 propose 同一条边（`agent_lock → say_lock`，单向，见模块头）。
        """
        async def settle(res: Any) -> None:
            ok = isinstance(res, dict) and bool(res.get("success", True))
            if ok:
                await self._say_and_emit(room, speaker, _resolve_line(rec, name, args, res))
                log.info("提案达成：%s ⇄ %s（%s）",
                         rec.get("proposer"), speaker, rec.get("kind"))
            else:
                err = str(res.get("error") or "") if isinstance(res, dict) else ""
                self._queue_notice(rec["proposer"], _fail_note(rec, err))
                log.info("提案应下但落刀失败：%s ⇄ %s（%s）：%s",
                         rec.get("proposer"), speaker, rec.get("kind"), err)
        return settle

    def _make_break_settle(self, room: Room, speaker: str, args: Dict[str, Any]):
        """"解除类"的收尾闭包（bridge 返回后由 `DialogueAgent` 调，参数 = 落刀结果）。

        解除是**单方面行为**（不需要对方同意，所以不走提案），但它是有社会后果的当众之举 ——
        结果与缔结一样公开：成功才写一条叙述行（挂动作方名下，全群可见、进各成员 session）。
        失败（关系本就不存在等）只让动作方在工具结果里看到错误，不宣告（没发生的事不进公共流水）。
        """
        op = str(args.get("op") or "")
        tgt = str(args.get("target") or "").strip()

        async def settle(res: Any) -> None:
            if isinstance(res, dict) and res.get("success", True):
                await self._say_and_emit(
                    room, speaker, f"（与{tgt}解除{REL_BREAK_CN.get(op, '关系')}）")
                log.info("解除关系（公开）：%s → %s（%s）", speaker, tgt, op)
        return settle

    async def _expire_proposals(self, room: Room) -> None:
        """轮末清理过期提案：TTL 用尽 = "石沉大海"（拒绝的一种）。

        **两件都做**（09-17 统一口径）：
        - 公开面：一条叙述行挂发起人名下，落 `Room.log`（全群可见、进各成员 session）——
          沉默也是结果；
        - 私信面：告知发起人本人（他**收不到**自己那条公开行 —— `delta_for` 不投自己的话）。
        """
        cur = self._round_no.get(room.id, 0)
        for key in [k for k, r in self._proposals.items()
                    if r.get("room_id") == room.id and int(r.get("expire_round") or 0) <= cur]:
            rec = self._proposals.pop(key, None)
            if rec is None:
                continue
            self._drop_notices(key)
            self._queue_notice(rec["proposer"], _expire_note(rec))
            await self._say_and_emit(room, rec["proposer"], _expire_line(rec))
            log.info("提案过期：%s → %s（%s）", rec["proposer"], rec["target"], rec["kind"])

    async def run_message(self, room_id: str, text: str,
                          speak_first: Iterable[str] = ()) -> Dict[str, Any]:
        """玩家在群里说一句 → 落房账 → 接力一轮。返回本轮摘要（给 RPC 回执/测试断言）。

        ★ **插话不必等整轮**：落账与回显走 `_say_and_emit`，在 `room_lock` **之外**
        执行（原先三行都在锁内 —— 第二条消息连"落账"都要等整轮跑完，群窗上的表现
        是"我发了话，十几秒毫无反应"）。于是：

        - 玩家的第二条**立刻**进 `Room.log`、**立刻**在群窗显示；
        - 本轮**尚未 `deliver`** 的成员（含"已点名但在等锁"的）本轮就能看到它 ——
          惰性投递按 `seq > cursor` 自然算进去；
        - 已 `deliver` 的成员（正在跑 / 已说完）本轮看不到，由**下一轮**的 `deliver`
          补投 ⇒ **不丢**。

        轮次语义**不变**：下面的 `_run_round` 仍持 `room_lock` ⇒ 第二个 `_run_round`
        在锁上排队，一轮只有一个在跑（"防两轮交错"那条纪律没有放松）。
        """
        room = self._require(room_id)
        raw = str(text or "")

        # ★ @ 点名（11-21）：解析在**落账之前** ★
        # 顺序关键：`Room.say` 要带着 `mentions` 一起落，否则这一条会被投给全群
        # （`delta_for` 只看流水里那一条的字段，事后补写等于没写）。
        _m = parse_mentions(raw, room.members)
        if _m.names is not None and not _m.names:
            # @ 了但一个都对不上（名字打错 / 那人已被踢）：**不猜、不广播**。
            # 退回"全员"是最糟的方向 —— 玩家以为在私下点名，实际广播给了所有人。
            # 所以：这条仍落账（玩家确实说了话，群窗要看得见），但不进任何人的账本
            # （`mentions=[]` ⇒ `delta_for` 谁都不给），并给玩家一句提示。
            note = ("群里没有「" + "」「".join(_m.unknown) + "」这个人，"
                    "这句话没有投给任何人。可用 @" + "/@".join(room.members) + "。") \
                if _m.unknown else "没有认出 @ 的对象，这句话没有投给任何人。"
            log.info("群 %s：@ 解析失败（unknown=%s），本条不投递", room.id, _m.unknown)
            # `echo_text=raw` 与正常分支同理由（这条 `body` 就是 raw，但保持一致的写法）
            first = await self._say_and_emit(room, PLAYER, raw, mentions=[], echo_text=raw)
            async with self._room_lock_impl(room.id):
                await self._emit_state(room, phase="end", speaking=[], spoken=[], note=note)
            return {"room_id": room.id, "seq": first.seq, "spoken": [],
                    "last_seq": room.last_seq, "mention_unknown": _m.unknown}

        # 正文剥掉 `@名字`：寻址信息不该进模型的眼睛（见 `strip_mentions` 的注释）。
        #
        # ★ 剥空了（玩家只写了 "@张三"）时，流水里存**原文**、促发语只给模型 ★
        #   第一版把促发语当成正文落了账，结果群窗上显示玩家说了「（玩家在叫你。）」——
        #   那是系统话，不是玩家说过的话。玩家只打了 `@张三`，群窗就该显示 `@张三`。
        #   （复核时实测确认：`room.log[0].text` 变成了那句促发语。）
        #   现在改成：`Room.log` 存原文（群窗与历史回放都正确），促发语走
        #   `pure_ping` 标记 → 由 `deliver` 以 **plugin 消息**注入（模型可见 / UI 不可见），
        #   与 `post_note` 同一条既有机制，不需要新概念。
        body = strip_mentions(raw, _m.names or []) if _m.names else raw
        pure_ping = bool(_m.names) and not body
        if pure_ping:
            # 流水存原文（含 @名字）：群窗显示玩家真正打出的东西。
            body = raw

        # ① 落账 + 回显：短锁、锁外 —— 不等正在跑的整轮。
        #    `mentions=_m.names` ⇒ 只有被 @ 的人的 `delta_for` 会给出这一条。
        # `echo_text=raw` ⇒ 回声帧带上玩家**原文**（含 `@名字`）。
        # C# 本地那条气泡画的是原文，拿 `body`（已剥）去对号永远对不上 ⇒ 会画两遍
        # （11-21 用户截图：`@姜萌 你在干什么` 与 `你在干什么` 各一条）。
        first = await self._say_and_emit(room, PLAYER, body, mentions=_m.names,
                                        pure_ping=pure_ping, echo_text=raw)
        # ② 跑轮：长锁 —— 两轮仍然串行（本轮跑完才轮到排队的那一轮）。
        async with self._room_lock_impl(room.id):
            # ★ 两条入口的差别**在两个调用点上写全**（不靠 `_run_round` 里的缺省值猜）：
            #   玩家发言轮 = 常规催话、全员沉默时**不**写终帧文案（屏幕上至少有他刚发的气泡）。
            #
            # ★ @ 时只让被 @ 的人开口（11-21 用户拍板）★
            # 候选名单直接收窄成被 @ 的人，**其余人不发言、也不补位**。
            # 为什么不补位：@ 是明确点名，"我 @ 张三，李四跳出来接话"是错的；
            # 被 @ 的人弃权就是弃权（与 `【沉默】` 既有语义一致），这一轮到此为止。
            # 实现上就是传 `only=` —— 而不是"传 speak_first 然后靠 k 截断"，因为
            # `speak_first` 的语义是**优先**（其余人仍会补位），两者不是一回事。
            # 催话文案按"有没有被 @ "分岔：被点名的人要知道"只有我听见了"（见
            # `NUDGE_TEXT_MENTION` 的两条理由）。其余情况与改动前逐字相同。
            spoken = await self._run_round(
                room, speak_first,
                nudge=NUDGE_TEXT_MENTION if _m.names else NUDGE_TEXT,
                note_on_silence="",
                only=_m.names)
            return {"room_id": room.id, "seq": first.seq, "spoken": spoken,
                    "last_seq": room.last_seq,
                    **({"mention": list(_m.names)} if _m.names else {})}

    async def run_round(self, room_id: str, speak_first: Iterable[str] = ()) -> List[str]:
        """**跳过按钮的入口**：玩家这一轮不置一词，按轮转顺序让 NPC 自己接下去。

        与 `run_message` 的关系：少掉"玩家那句话"这一步（不落账、不广播、不进任何人的
        记忆），其余**逐字相同** —— 同一把 `room_lock`、同一个 `_run_round`。所以
        「触发轮次的方式有两种，轮次本身只有一条实现」：想在别处再开一个入口时，
        请先回答"它和这两个有什么不同"，而不是再加一个 `_run_*`。

        ★ 这里**不**写 `Room.log`：跳过不是"说了一句没有字的话"，玩家确实什么都没说。
        （若要留痕，见 `NUDGE_TEXT_SKIP` 上方那段"为什么不真写一条"。）

        ★ 与 `speak_first` 的关系：跳过**不点名**，`speak_first` 只为测试/将来留口
        （真机上空着 ⇒ 完全由轮转指针决定，即"自动按顺序发言"）。
        """
        room = self._require(room_id)
        async with self._room_lock_impl(room.id):
            # 跳过专用催话 + 跳过专用终帧文案（见两个常量的长注释）。
            return await self._run_round(room, speak_first,
                                         nudge=NUDGE_TEXT_SKIP,
                                         note_on_silence=SILENT_ROUND_NOTE)

    async def _run_round(self, room: Room, speak_first: Iterable[str], *,
                         nudge: str, note_on_silence: str,
                         only: Optional[Sequence[str]] = None) -> List[str]:
        """接力一轮：按候选名单逐个点名，直到 `k` 人**实际开口**或名单用尽。

        调用方须已持 `room_lock`。

        `nudge` / `note_on_silence` **必须由调用方显式给**（刻意不给缺省值）：
        它们是"玩家发言轮"与"跳过轮"仅有的两处行为差异，摆成缺省值就等于把差异
        藏进函数体、让下一个读调用点的人看不出这两条路哪里不一样。
        """
        start = self._pointer.get(room.id, 0)
        self._round_no[room.id] = self._round_no.get(room.id, 0) + 1
        # 待回应的提案让**对手方优先发言**（复用"被点名优先"的既有机制）：被当面问了话，
        # 不该排在别人后面 —— 他若就在本轮候选里，本轮就能答。
        pending_first = [r["target"] for r in self._proposals.values()
                         if r.get("room_id") == room.id and r.get("target") in room.members]
        # ★ `only` = 本轮**只允许**这些人开口（@ 点名）★
        # 与 `speak_first` 的区别是**语义级**的：
        #   · `speak_first` = 优先（其余人仍在候选里，被点名者弃权就由他们补位）；
        #   · `only`        = 白名单（名单外的**根本不进候选**，这一轮只有他们可能说话）。
        # @ 必须走后者：用户明确要求"@ 了谁就只有谁说话"，补位会变成"我 @ 张三，李四接话"。
        # 提案对手方的优先权在 `only` 下**失效** —— 这是对的：玩家点名时，
        # 让一个没被叫的人插进来回应提案，比"提案晚一轮"糟得多。
        _pool = list(only) if only is not None else room.members
        cands = plan_round(_pool,
                           speak_first=list(dict.fromkeys([*pending_first, *speak_first])),
                           start=start)
        spoken: List[str] = []
        # 成员级异常：{成员: 人话原因}。用途有二 ——
        #   ① 出错**当场**发 `phase="error"` 帧（C# 撤掉/定稿他那条泡 + 补一行系统提示）；
        #   ② 整轮**一个人都没开口**时，写进终帧的 note（"这一轮为什么没有产出"）。
        # 为什么要②：终帧是轮级的"为什么没产出"通道，全员静默时它本来是空的 ——
        #   而"全员静默"与"全员出错"在玩家眼里长得一模一样（屏幕上一个字都没有）。
        errs: Dict[str, str] = {}
        try:
            for npc_id in cands:
                if len(spoken) >= self.k:
                    break
                # 群在本轮途中被解散（RPC 同步执行，拿不到 room_lock）⇒ 就地收尾：
                # 再点名只会把发言落进一个已摘除、流水已删的对象。终帧仍由 finally 发。
                if room.id in self._dissolved:
                    log.info("群 %s 本轮途中被解散，就地收尾（已发言 %s）", room.id, spoken)
                    break
                # 点名即广播"谁在说话"：C# 群窗据此把该成员标成"正在输入"。
                await self._emit_state(room, phase="speak", speaking=[npc_id], spoken=spoken)
                try:
                    body, reply_node = await self._one_speaker(room, npc_id, nudge)
                except TurnAborted as _ab:
                    # ★ 读档/取消打断群回合：整轮就地收尾，**不算成员故障**（11-20）★
                    # 与下面那条 Exception 分支的区别是"谁的责任"：
                    #   · Exception = 这位成员自己出了事 ⇒ 跳过她、其余照常、给她挂一行原因；
                    #   · TurnAborted = 整个世界被换掉了 ⇒ 剩下的人也不该再跑（他们的 agent
                    #     已经被 `discard_all` 摘出名册），继续点名只会一个个撞同一堵墙。
                    # 不发 `phase="error"`：屏幕上不该出现"某某回合异常"，那是在报一个不存在的故障。
                    log.info("群 %s：%s 的回合因读档/取消中止（本轮就地收尾）", room.id, npc_id)
                    break
                except Exception as exc:
                    log.exception("群 %s：%s 的回合异常（本轮跳过该成员，其余照常）", room.id, npc_id)
                    # ★ 09-17：异常**必须发帧**。以前这里只 `continue`，C# 那边什么都收不到 ⇒
                    #   `speak` 开的那条空气泡一直挂着、忙碌态不复位，直到**下一个成员的 speak**
                    #   顺手把泡撤掉（实机："姜萌的气泡过了一会自己消失，然后另一个人说话"），
                    #   而玩家**永远不知道她为什么没开口**（与"不许静默失败"直接冲突）。
                    #   不复位忙碌态：轮还在跑（与 `silent` 同规约）。
                    errs[npc_id] = _member_error_note(exc)
                    await self._emit_state(room, phase="error", speaking=[npc_id],
                                           spoken=spoken, note=errs[npc_id])
                    continue
                if is_silent(body):
                    log.info("群 %s：%s 弃权（游标已照常推进）", room.id, npc_id)
                    # ★ 弃权要**显式告诉 C#**：`phase="speak"` 已经给它开了一条流式泡
                    #   （「甲：」），而弃权不写流水 ⇒ 永远等不到 `room_say` 去定稿它 ⇒
                    #   那条空气泡会一直留在群窗上（模型照提示回【沉默】时，哨兵文本还会
                    #   被流式打进气泡，屏幕上就是「甲：【沉默】」）。
                    #   旧 C# 不认识这个 phase（`ApplyRoomState` 的 switch 落空）⇒ 行为
                    #   与本改动前逐字相同，不会更糟；新 C# 借此撤掉那条泡。
                    await self._emit_state(room, phase="silent", speaking=[npc_id], spoken=spoken)
                    continue
                # 与玩家的落账共用同一把短锁 ⇒ **UI 帧顺序 === 流水 seq 顺序**
                # （即使玩家此刻正在插话，两条发言的"落账 + 广播"也不会交错）。
                u = await self._say_and_emit(room, npc_id, body)
                # ★ 发言锚点：往**发言者自己**的 session 记一条 `room/utt`，注明"这条群发言
                #   = 我账本里的哪个节点"。NPC 自己的话不投递给自己（`delta_for` 排除 me），
                #   没有这条注记，"群窗删除"就找不到"我自己说过的那句"（见 §9.2 的两种锚点）。
                #   节点在 `_one_speaker` 出锁**之前**就取好了（`reply_node`）—— 出锁后现扫
                #   可能被并发的单聊回合顶掉"最后一条 assistant"。
                #   注记对 Surface / UI 投影 / 压缩材料 / Inbox 全隐形（同 `room/cursor`）。
                self._note_own_utterance(room, npc_id, u.seq, reply_node)
                spoken.append(npc_id)
            # 轮末清账：TTL 用尽的提案在此作废。公开行要写在**终帧之前**（与发言同序，
            # 群窗按 `room_say` 顺序落气泡）；解散时跳过 —— 群都没了，公开行无处可落。
            if room.id not in self._dissolved:
                await self._expire_proposals(room)
        finally:
            # ★ 回合终帧**必须无条件发**（放在 finally 里）：它是 C# 群窗复位忙碌态的唯一依据。
            # 只靠 `room_say` 收尾是不行的 —— 一轮里所有人都弃权时**一条 room_say 都不会有**
            # （弃权不写流水、不广播），忙碌态就永远挂着，表现为"群里没人说话但一直显示正在输入"。
            # 这条在 `docs/archive/2026-09-17-群聊编排/设计定稿.md` §6.2 里原本没有，是实现时踩出来的。
            #
            # note：**全员都没开口且有异常**时才写。理由：终帧的 note 是轮级"这一轮为什么
            # 没有产出"的通道，而这条通道此前只在轮级异常（FALLBACK_TEXT / 群里的 /compact）时用；
            # 单成员异常已经把解释写在 `phase="error"` 那一帧上了（C# 当场显示系统提示行），
            # 这里再写一遍就是同一件事说两遍。全员静默（没有异常）在**玩家发言轮**刻意不写 ——
            # "大家都觉得没什么可说"是正常结果，而且屏幕上还有玩家自己那条气泡，空不等于坏。
            #
            # ★ 跳过那一轮是例外（`note_on_silence` 非空时生效）：那一轮玩家一条气泡都没发，
            #   全员弃权的话屏幕上**彻底空白**，与"按钮坏了"无法区分。异常那条**优先**于它 ——
            #   "有人卡住了"比"没人想说话"更值得上屏，两条都写就是同一件事说两遍。
            _note = ""
            if not spoken and errs:
                _note = "本轮没有人开口（" + "；".join(f"{k}：{v}" for k, v in errs.items()) + "）"
            elif not spoken and note_on_silence:
                _note = note_on_silence
            await self._emit_state(room, phase="end", speaking=[], spoken=spoken, note=_note)
        if room.id not in self._dissolved:
            self._advance_pointer(room, spoken)
        log.info("群 %s：本轮实际发言 %s（k=%s，候选 %s）", room.id, spoken, self.k, cands)
        return spoken

    async def _one_speaker(self, room: Room, npc_id: str, nudge: str):
        """驱动一个成员的群聊回合 → `(发言正文, 收口节点 seq)`（正文可能为空 = 弃权）。

        `nudge` = 本轮那句"轮到你说话了"（两条入口的**唯一**注入差异，见 `_run_round`）。

        收口节点 = "这句话落在我账本里的哪一条"（`last_reply_node`，与 `ChatHub._collect_reply`
        同一判据）。**必须在 `agent_lock` 里取**：出锁之后并发进来的单聊回合可能追加新一条
        assistant，把"最后一条"顶掉 ⇒ `room/utt` 锚点指错人（群窗删除就会删错节点）。
        """
        agent = self.loop.get(npc_id) or self.loop.create(npc_id)
        async with self._turn_slot():                 # ② 全局并发闸（每个发言者各自取）
            async with self._agent_lock(npc_id):      # ③ 该 NPC 串行
                self.enter(agent, room)               # 群标签只在本回合有效
                self.deliver(room, agent)
                # 提案闸门：群聊回合的"策略层"（判放行 / 登记提案 / 拦重复）。
                # 只在本回合有效 —— 由 `ChatHub._run_turn` 的 finally 与场合标记一起复位；
                # 漏复位会让之后的单聊回合带着它去查一个跟它无关的提案表。
                agent.tool_gate = self._make_gate(room, npc_id)
                # 本群用量的归集**不在这里**：它在 `ChatHub._run_turn` 的 finally 里
                # （回合前后取差 → 并在**发 stats_update 之前**入账）。放在这里会晚一帧 ——
                # 那一帧报的还是"上一位的账"（实测：本回合自己的量要等**下一个成员**才出现）。
                body = await self._run_turn(
                    agent, npc_id, nudge,
                    {"kind": "plugin", "plugin": PLUGIN_NAME},
                    room.id, room.roster_for(npc_id),
                    room.member_unit_ids,      # ★ 名单与**身份**同一次写（见 enter/set_roster）
                )
                node = None
                try:
                    getter = getattr(agent, "last_reply_node", None)
                    node = getter() if getter is not None else None
                except Exception:
                    node = None
                return body, node

    def _advance_pointer(self, room: Room, spoken: List[str]) -> None:
        """轮转指针 → 本轮**最后一个实际发言者**的下一位。

        没人说话则不动（否则全员弃权会空转一圈）。保证不会永远从同一个人开始。
        已解散的群直接跳过 —— 否则会往 `_pointer` 里重新塞一个刚 pop 掉的键（泄漏一条）。
        """
        if room.id in self._dissolved:
            return
        if not spoken or not room.members:
            return
        try:
            self._pointer[room.id] = (room.members.index(spoken[-1]) + 1) % len(room.members)
        except ValueError:
            self._pointer[room.id] = 0

    # ══════════════════════════════════════════════════════════════
    # 事件回推（尽力而为：没有 emit 或回推失败都不该拖垮一轮）
    # ══════════════════════════════════════════════════════════════

    async def _emit_say(self, room: Room, u: Utterance, echo_text: str = "") -> None:
        """把一条发言推给群窗。

        `echo_text`（仅玩家发言用）：**玩家输入的原文**（含 `@名字`）。
        `u.text` 是剥掉 `@名字` 之后的正文（见 `run_message` 的 `body`），
        而 C# 侧本地那条气泡画的是玩家敲进去的原文 —— 两者字面不同，
        按 `u.text` 对号**永远对不上**，结果是同一条消息在群窗里出现两遍
        （11-21 用户截图实证：`@姜萌 你在干什么` + `你在干什么`）。
        所以回声帧额外交一份原文，让 C# 用精确相等销账。
        """
        if self._emit is None:
            return
        try:
            # `speaker_id` = 说话人的**精确 unitID**（来自 `Room.member_unit_ids`）。
            # ★ 为什么随帧带上它：C# 群窗每一行都要取说话人的立绘，而它手上只有中文名 ——
            #   没有 id 就只能按名全图解析，**重名时画上别人的脸且不报错、不缺字段**。
            #   身份在 Python 侧本来就知道（建群/拉人时 C# 把真身交上来的），没有理由
            #   让 C# 再猜一次。见 `docs/archive/2026-09-17-群聊编排/设计定稿.md` §十九。
            # 玩家恒为空：C# 那边玩家行走 `resolve` 的 ①玩家直达（零歧义），不需要 id。
            await self._emit("room_say", room_id=room.id, seq=u.seq, uid=u.uid,
                             speaker=u.speaker, text=u.text,
                             speaker_id="" if u.speaker == PLAYER else room.unit_id_of(u.speaker),
                             # 旁白只在真为旁白时带这个键（普通发言的帧保持逐字节原样）：
                             # C# 据此改画「行动记录」行而不是气泡，见 `Room.Utterance.narration`
                             **({"narration": True} if u.narration else {}),
                             # 玩家回声的对号键：原文（含 @名字）。没有它 C# 只能拿
                             # 剥过的 `text` 去比本地气泡（原文）⇒ 永远不等 ⇒ 画两遍。
                             # 只在**确实不同**时才带 —— 普通发言/无 @ 的帧保持逐字节原样。
                             **({"echo_text": echo_text}
                                if echo_text and echo_text != u.text else {}))
        except Exception:
            log.warning("room_say 回推失败（群 %s seq=%s）——流水已落，不影响正确性",
                        room.id, u.seq)

    async def _emit_state(self, room: Room, *, phase: str, speaking: List[str],
                          spoken: Optional[List[str]] = None, note: str = "") -> None:
        """群窗状态帧。`phase="speak"` 标记谁在说话；`phase="end"` 是本轮**终帧**。

        C# 契约（务必照此实现，否则群窗会挂）：
        - 收到 `phase="end"` ⇒ **复位忙碌态**；`text`（= 本函数的 `note`）是轮级说明
          （"这一轮为什么没有产出"），C# 渲染成一行系统提示。
        - 收到 `phase="speak"` ⇒ 点名广播：C# 记下"现在是谁在说"（忙碌态 + 逐行归属），
          **不预开气泡**（09-17 起：正文到了才起泡，见下）。
        - 收到 `phase="silent"` ⇒ **该成员弃权**：撤掉可能存在的空气泡，
          **不复位忙碌态**（轮还在跑）。
          ★ 09-17 新增；旧 C# 不认识这个 phase ⇒ 落空到"什么都不做"，与本改动前一致。
        - 收到 `phase="error"` ⇒ **该成员这一轮出错了、本轮跳过**（09-17 新增）：
          撤空气泡 / 定稿半截正文 + 用 `note` 显示一行系统提示，**不复位忙碌态**。
          与 `silent` 分开是**故意的**：弃权是模型的选择（正常结果），出错是我们的故障 ——
          混成一个 phase 会让"她不想说"和"她卡死了"在屏幕上长得一模一样。
        - `room_say` 且 `speaker != "player"` ⇒ 该成员的**最终正文**（流式期间已经用
          `text_delta` 显示了半截，所以这是"定稿/替换"，**不是新气泡**）；
          与单聊里 `npc_reply` 的角色完全相同。
        - `room_say` 且 `speaker == "player"` ⇒ 玩家这句的**回声**。发送方本地已经显示过，
          按 `uid` 去重即可（`uid` 就是为这种去重留的）。
        """
        if self._emit is None:
            return
        try:
            # `speaking_ids`：与 `speaking` **同序同长**的精确 unitID 名单（见 `_emit_say`）。
            # 名单里可能出现玩家（`members` 只放 NPC，但 `speaking` 的调用点有可能塞玩家名）——
            # 查不到就留空串，消费侧回落按名，绝不写占位。
            await self._emit("room_state", room_id=room.id, phase=phase,
                             members=list(room.members), speaking=list(speaking),
                             speaking_ids=[room.unit_id_of(m) if m != PLAYER else ""
                                           for m in speaking],
                             spoken=list(spoken or []), last_seq=room.last_seq,
                             text=note or "")
        except Exception:
            log.warning("room_state 回推失败（群 %s phase=%s）", room.id, phase)

    # ══════════════════════════════════════════════════════════════
    # 名录 / 历史
    # ══════════════════════════════════════════════════════════════

    def list_rooms(self) -> List[Dict[str, Any]]:
        return [r.to_meta() for r in self._rooms().values()]

    def room_meta(self, room_id: str) -> Dict[str, Any]:
        """单个群的名录条目（加人/踢人的回执用它，省一次全表遍历）。

        走 `_require` ⇒ 懒加载的群在这里补读流水，所以 `last_seq` 是**真值**
        而不是名录兜底值。加/踢人自己已经 `_require` 过，这一步通常无 IO。
        """
        return self._require(room_id).to_meta()

    def history(self, room_id: str, limit: int = DEFAULT_HISTORY_LIMIT,
                before_seq: int = 0) -> Dict[str, Any]:
        """群窗历史（从 `Room.log` 投影，**不从任何成员的 session**）。

        为什么不能读成员 session：① 玩家自己没有 session；② 各成员的会话可能被压缩、
        被删、或**是后加入的**（他的 session 里根本没有入群前的话）；
        ③ 实测同一条房间发言在不同成员账本里的 turn 号不同，跨成员不可比。
        """
        from .room_history import project_room_history

        return project_room_history(self._require(room_id), limit=limit,
                                    before_seq=before_seq)
