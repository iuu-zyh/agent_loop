"""initiative — NPC 主动开口（神识传音式伪 user 意图）

分层分权（对齐神识传音 §3.4 主动交互）：
- C# 只在游戏主线程做「触发检测」：何时、对谁、什么意图（NpcInitiativeMonitor），
  把意图键与可选缘由经 WS 事件 npc_initiative 交给 Python；
- Python 持「意图键 → 文案」映射，包装成带括号的伪 user 消息驱动 DialogueAgent 正常 turn
  （NPC 以口吻主动开场），不新开通道、不改回合循环。

意图键语义（复刻神识传音 11 种）：正向 7 条给好友/高好感，负向 4 条给敌对/低好感。
"""

from __future__ import annotations

from typing import Dict

# 意图键 → 文案。键由 C# NpcInitiativeMonitor 按关系/亲密度派发，英文键不落模型（只映射文案）
INITIATIVE_INTENTS: Dict[str, str] = {
    # ── 言语向（11 个，神识传音原样）──
    "greet": "向玩家打个招呼",
    "smalltalk": "与玩家发起闲聊",
    "courteous": "向玩家进行问候",
    "life": "与玩家聊聊人生境遇",
    "recent": "向玩家谈谈最近动态",
    "missing": "向玩家表达思念",
    "affection": "向玩家表达情感",
    "malice": "向玩家表达恶意",
    "vent": "向玩家发泄不满",
    "provocation": "向玩家发起挑衅",
    "disdain": "向玩家发出不屑",
    # ── 动作向（10-16 新增，5 个）──
    # 为什么要有这一批：原 11 键全是**言语行为**，`format_initiative_message` 只描述"说什么"，
    # 从不邀请"做什么" ⇒ NPC 主动开口回合虽然有完整工具表，却永远不会想到用。
    # 键名与 C# `NpcInitiativeMonitor` 的 `RemoteActionIntents`/`LocalActionIntents` 成对；
    # 异地/同格的**可用性**由 C# 侧选池保证（异地池 ⊂ 同格池），这里只负责文案。
    "gift": "想送玩家一件东西",
    "lun_dao": "想与玩家论道",
    "yao_yue": "想约玩家见面",
    "spar": "想与玩家切磋",
    "attack": "想对玩家动手",
    # ── 信箱向（11-13）──
    # 不是"C# 日节拍骰出来的心情"，而是"有人捎了话给他"这个具体事实。
    # 它与上面 16 个键的关系是**替代**：命中它时，舞台指令不再说"你想找玩家聊点什么"，
    # 而是直接给出那位发信人与原话，并明确"这一轮你主动找他，是为了这件事"。
    "mail_relay": "收到了别人捎来的一句话，要主动找玩家说这件事",
}

# 意图键 → UI 短标签（只给对话窗分隔条用，见 intent_label）
INITIATIVE_LABELS: Dict[str, str] = {
    "greet": "打个招呼",
    "smalltalk": "闲聊",
    "courteous": "问候",
    "life": "聊人生境遇",
    "recent": "谈最近动态",
    "missing": "表达思念",
    "affection": "表达情感",
    "malice": "表达恶意",
    "vent": "发泄不满",
    "provocation": "挑衅",
    "disdain": "不屑",
    # 动作向
    "gift": "赠礼",
    "lun_dao": "论道",
    "yao_yue": "相约",
    "spar": "切磋",
    "attack": "动手",
    "mail_relay": "捎话回访",
    # 系统向（非 C# 派发，由事件变体注入）
    "game_drama": "游戏交互",
    "action_done": "动作回执",
}

# 动作向意图键（与 C# 的 `RemoteActionIntents ∪ LocalActionIntents` 一致）。
# 用途：`format_initiative_message` 据此追加"这一轮可以动手"的授权句。
ACTION_INTENTS: frozenset = frozenset({"gift", "lun_dao", "yao_yue", "spar", "attack"})


def intent_label(intent: str) -> str:
    """意图键 → **UI 短标签**（对话窗分隔条 `—— 主动传音 · 表达思念 ——` 用）。

    与 INITIATIVE_INTENTS 分开：那份是喂给模型的舞台指令（"向玩家表达思念"），
    这份是给人看的四到六字标签。未知键回落原键——将来 C# 新增意图时不至于空白。
    """
    key = (intent or "").strip()
    return INITIATIVE_LABELS.get(key, key)


# 动作向意图的**授权句**（10-16）：C# 选到动作键时，舞台指令必须明确"这一轮可以动手"。
#
# 为什么必须是独立一句、而不是指望模型自己想到：工具表的触发句（`tools/schemas.py`）
# 全部写在**玩家一侧**（"玩家问起…必须调我"），`tool_usage.txt` 里唯一那条强祈使还是
# **禁止**（"不许只用台词收场"）。所以模型对"主动做事"没有任何授权 —— 它只会说话。
#
# 措辞只**授权**、不**命令**：是否真调工具仍由模型按人设与情境判断（避免它与
# "不必强行办事"的人设冲突）。同时点明"若此刻办不了，就说清楚缘由"——这一句是给
# 异地/名分不够这类"工具会 Fail"的场景兜底，让失败变成一句解释而不是硬撑。
_ACTION_LICENSE = (
    "（这一轮你不只是回一句话——若你真想有所行动，"
    "现在就调用对应的工具把它办成；若你只是想说说，也可以不动手。"
    "若眼下办不成（相隔两地、身份不合、手头没有），照实说清缘由即可，不必勉强。）"
)

# 方位说明（10-16）：只给**动作向**意图追加，让话术与实际可行性对齐。
#
# 为什么要分开写：同一个动作在两地完全不同调 ——
#   · 同格：「我把这枚丹药递到他手里」——递物/切磋/动手都成立
#   · 异地：「我托神识将这枚丹药送过去」——递物成立，但切磋/动手**根本做不了**
#     （`ToolExecutor` 对 spar/attack 有 `SameGrid` 硬校验，异地直接 Fail）
# 不点明的话，模型会按"同格"的口吻写台词（"你我一战"），然后工具报错，台词就成了空话。
_SAME_GRID_NOTE = "（你此刻与他同处一地，有事当面办即可。）"
_REMOTE_NOTE = (
    "（你与他相隔两地，只能以神识传音行事：赠物、论道、相约皆可；"
    "切磋、动手这类非当面不可的事，这一轮做不成。）"
)


#: 信箱向意图键（11-13）：有人捎了话给他 ⇒ 他主动去找玩家**说这件事**。
MAIL_RELAY_INTENT = "mail_relay"

#: 信箱回访的舞台指令（用户 11-13：「要求其根据传话内容去主动交互即可」）。
#:
#: 与其余 intent 的根本差别：那些写的是「**你想**做什么」（心情/冲动），这条写的是
#: 「**有人托了你**一件事」—— 主体是托付，不是心情。措辞因此必须交代三件事：
#:   ① 那句原话就在上面的材料里（`【{who}托人捎来】`），别另编；
#:   ② 这一轮的目的是**把这件事办到玩家面前**（说清、问明、或真去办）；
#:   ③ 这件事跟他本人有没有兴趣无关 —— 受人所托就得有个交代。
#: 缺 ② 的话，模型会演成"我正好也想找你"，捎话沦为寒暄的引子；缺 ③ 则会因为
#: "我跟这个人不熟"而敷衍过去（而托付恰恰是**不需要**熟悉的）。
#: `{tail}` 而不是把"该办的就调工具去办"hardcode 在正文里（11-13 复核）：玩家忙时那句话
#: 是**做不到**的（执行层 `speech_only_turn` 拦掉全部动作工具），得整句换掉，
#: 只追加一句"别发起动作"会被前半句抵消。
_MAIL_RELAY_BODY = (
    "（NPC主动传音：你受人所托，主动找玩家说一件事）"
    "（缘由：{sender}托人捎了句话给你，**就在上面的记录里**——这一轮先照那句原话办："
    "该问的问明白、该转达的转达、{tail}别只当寒暄的引子，"
    "也别把他的那句话当成玩家说的。）"
)

#: 正文里"接下来怎么办"那半句的两种形态。
#:   常态：可以真去办（授权句与方位句会在后面另加）；
#:   忙时：只能把话带到 —— 动作留到他空闲。
#: 第二种**不能只靠追加**（见 `_MAIL_RELAY_BODY` 的注释）。
_MAIL_RELAY_TAIL_IDLE = "该办的就调工具去办，"
_MAIL_RELAY_TAIL_BUSY = "能办的先记下、这轮别动手（玩家正忙），"


def _format_mail_relay_message(sender: str, same_grid: "bool | None" = None,
                               busy: bool = False) -> str:
    """信箱回访的舞台指令（见 `_MAIL_RELAY_BODY` 的完整理由）。

    `sender` = 托人捎话的那位（C# 从 `list_mail_pending` 拿到的发信人真名）。
    落账仍是 `source.kind="initiative"` —— 它不是玩家说的话，与其余 intent 同一条规定。

    `busy`（11-13 复核补）：**玩家正忙 ⇒ 不许给动作授权**。见下面那段 ★。
    """
    # 发信人名字是 C# 从 `list_mail_pending` 带过来的（`reason` 字段）。
    # 缺失时**不编人名**（"一位道友"）：模型看到具体名字却对不上任何材料，
    # 反而会去猜"是不是玩家说的"。用泛称则由"上面那条【X托人捎来】"自己提供名字。
    who = (sender or "").strip() or "一位道友"
    # ★ 玩家忙时**不能**给动作授权与方位句（11-13 复核抓到的第二处）★
    #   其余 intent 不会撞上这个矛盾：`PickIntent` 里 `!busy` 直接把整条动作支路跳掉，
    #   所以忙回合的 intent 必然是言语向，而 `_ACTION_LICENSE` 只追加给动作向 —— 天然互斥。
    #   `mail_relay` 是**绕开 PickIntent 直接指定**的（见 `TryDispatch`），于是它会在
    #   busy 回合里照样附上"现在就调用对应的工具把它办成"，而同一回合执行层正被
    #   `speech_only_turn` 拦着所有动作工具。后果：NPC 说"我这就把丹药送去"，
    #   工具回一条 error —— 许诺了一件这一轮**必然做不到**的事。
    #   忙碌时只保留"把话带到"这一层，动手留到玩家空闲（BUSY_NOTE 会再重申一次边界）。
    #   ★ 正文里那半句也要换掉，不能只靠后面追加：前半句"该办的就调工具去办"与
    #     追加的"别发起动作"方向相反，模型会照着更具体的那句走（`_MAIL_RELAY_TAIL_BUSY`）。
    text = _MAIL_RELAY_BODY.format(
        sender=who,
        tail=_MAIL_RELAY_TAIL_BUSY if busy else _MAIL_RELAY_TAIL_IDLE,
    )
    if busy:
        text += "（玩家此刻正忙，这一轮**先把话带到**：该说清的说清，别发起动作，留到他空闲时再办。）"
        return text
    # 方位句与动作授权：捎话常常**就是**托他办事（"帮我送枚丹药给他"），
    # 所以这里允许动手；但能不能做仍由模型按同格与否判断（异地做不了的照实说）。
    if same_grid is True:
        text += _SAME_GRID_NOTE
    elif same_grid is False:
        text += _REMOTE_NOTE
    text += _ACTION_LICENSE
    return text


def format_initiative_message(intent: str, reason: str = "",
                              same_grid: "bool | None" = None,
                              busy: bool = False) -> str:
    """把意图包装成伪 user 文本：括号舞台指令，模型据此以 NPC 口吻主动开场。

    括号语义（同神识传音 `(向玩家打个招呼)`）：模型与后续历史都能认出这是
    「叙述指令」而非玩家真言；缘由（可选事实）给模型开口的抓手。

    动作向意图（`ACTION_INTENTS`）额外追加 `_ACTION_LICENSE` —— 见该常量的完整理由。

    `same_grid`（10-16 可选）：本次触发时是否同格。**只影响动作向意图的口吻**，
    因为同一个动作在两地的话术完全不同（"当面交给你" vs "托传讯送过去"）：
      · `True`  → 「你此刻与他同处一地，当面行事即可」
      · `False` → 「你与他相隔两地，只能以神识传音行事」（并点明哪些动作做不了）
      · `None`  → **不加任何方位句**，输出与 10-16 之前逐字相同（老 C# 兼容）
    言语向意图忽略此参数 —— 说话本来就没有方位差别。

    `busy`（11-13 复核补）：本回合玩家正忙（`speech_only_turn` 会拦掉所有动作工具）。
    为 True 时**不追加**动作授权与方位句 —— 否则指令在劝它做一件执行层马上会拒掉的事。
    默认 False ⇒ 所有既有调用逐字不变。
    """
    # 信箱向（11-13）：**不复用**上面那套"你想找玩家聊点什么"的口吻。
    # 那套描述的是"你自己的心情"（想问候/想表达思念/想约他），而这条是**别人托付的事**：
    # 模型若沿用心情的口吻，就会把它演成"我恰好也想起你"，捎话内容退化成顺带提一句。
    # 这里直接给出**发信人 + 原话**（原话就在上一条 `【X托人捎来】` 正文里，这里点名是为了
    # 让指令与那条材料对上号），并明确本轮的目的是这件事本身。
    if intent == MAIL_RELAY_INTENT:
        return _format_mail_relay_message(reason, same_grid, busy=busy)

    desc = INITIATIVE_INTENTS.get(intent) or INITIATIVE_INTENTS["greet"]
    text = f"（NPC主动传音：{desc}）"
    reason = (reason or "").strip()
    if reason:
        text += f"（缘由：{reason}）"
    if intent in ACTION_INTENTS and not busy:
        if same_grid is True:
            text += _SAME_GRID_NOTE
        elif same_grid is False:
            text += _REMOTE_NOTE
        text += _ACTION_LICENSE
    return text


# 「动作办妥」系统说明（09-24）：C# 的 `DeferredActionQueue` 把"当时发不出去"的动作
# （战斗中/剧情链收尾 —— 硬发会沉进游戏系统队列不出队、90 秒静默超时）排进队，等收场后
# 自动执行完，发 intent=action_done、reason=一句结果摘要（"邀约姜萌：已完成"）。
#
# 为什么**不复用** game_drama：那条说的是"游戏里正在演这一幕，顺着说下去、别重复发起"；
# 这条说的是"你之前请求的那个动作**已经有结局了**"—— 动作指令不同（一个是"别动"，
# 一个是"可以交代一声"），合成一条会让模型把"已办完"读成"正在进行"。
_ACTION_DONE_NOTE = (
    "（系统：你之前请求的那个动作，游戏里**刚刚办完了** —— 结果：{summary}）"
    "（注意：这是**已经发生的事实**，不是玩家的新请求；也**不要再重复发起同一个动作**。"
    "就这件事对玩家说一句即可：办妥了就交代一声，没办成就说明缘由。）"
)


def format_action_done_message(summary: str = "") -> str:
    """动作办妥通知 → 伪 user 舞台指令（C# `DeferredActionQueue` 的 `Notifier` 发来）。

    `summary` 是 C# 组好的一句**事实**（`ActionCatalog.DescribeAction` + 结局：
    "邀约姜萌：已完成" / "向姜萌赠送物品：未能完成（…）"），本函数只负责把它包成
    模型看得懂的舞台指令 —— 与 `format_initiative_message` 同款括号语义：
    模型与后续历史都能认出这是「叙述指令」而非玩家真言。
    """
    s = (summary or "").strip() or "（C# 没附上结果说明）"
    return _ACTION_DONE_NOTE.format(summary=s)


# 整段注入的上限：一段相遇理论上可以很长（一场战斗来回翻页），全塞进去会把上下文挤爆。
# 超了就**明写省略**（"更早的 N 句略"），绝不静默丢 —— 静默丢会让模型以为这就是全部经过。
_MAX_DRAMA_LINES = 40

# 「游戏里这场交互还在进行中」的系统说明（09-23）：由 C# 在点「对话」那一刻采集
# （`NpcInitiativeMonitor.ActionActive`：UI 里 `ActionCourseInfo` 还在）→ 帧带 `action`。
# 为什么必须有：不给这个事实，模型会把**已经发生**的交互当成没开始，转头再发起一遍同款工具
# （实测：论道页上点「对话」，模型又调一次 lun_dao，再弹一遍原生论道界面）。
_IN_ACTION_NOTE = (
    "（系统：**游戏里这场交互还在进行中**（你发起的那个动作尚未结束，上面列的就是它的实时内容）。"
    "**不要再调用同一个动作工具**（论道/邀约/切磋这类会再弹一遍原生界面）；"
    "顺着上面的情境往下说即可。）"
)

# 「这一页是**战斗处置页**」的系统说明（09-24 用户需求）：C# 在点「对话」那一刻判**当前页号**
# （`BattleOutcome.IsVerdictDramaId`：20403=NPC 送东西求饶 / 20404=NPC 任凭处置 —— **配表事实**，
# 不是文本匹配；且**只认"当前页就是处置页"**，翻到后页再开口不注入 —— 用户 09-24 拍板甲案，
# 理由：后页那些回合里，处置页的选项/台词本来就会随段增量投进去，模型看得见"发生了什么"，
# 需要被拦的是**在处置页上当场**想去"执行"它的手）。
#
# 为什么必须有：处置页上写着"献物求饶"这类内容，但**交付/收取由游戏原版流程办、处置权在玩家**
# ——不给这个事实，模型会拿工具去"执行"页面上的事（09-24 真机：排队执行后失败 ——
# 「玩家背包中没有『三品化瘀丹』」，就是它想调 item_acquire 去"收下"一件已经交付的礼物）。
#
# 与工具结果那句 `verdict_page`（`text_render._battle_said` 一族：'生死未定，别替他决定杀不杀'）
# **分工不同、互不重复**：那句管**生死**（处置页收口那一刻随 tool_result 发），这句管**动作**
# （玩家在处置页上点「对话」这一刻）。
#
# 最后一句"只限这一页"是用户点名要的：不许它因此认定"我以后不能赠送道具"——
# 之后玩家要它赠别的东西，照常走工具。
_VERDICT_NOTE = (
    "（系统：这是**你战败后的处置页**——那件东西收不收、留不留你这条命，全由玩家决定；"
    "页面上写的献物、求饶是**游戏内置流程**，**不要调用任何工具**去赠送、交付、索取或收下"
    "（item_acquire / economy_item 都别调）。**只限这一页**：之后玩家要你赠别的东西，照常照办。）"
)

# 序号字形（①~⑳）；超过 20 句退回 "N."。纯显示用，不参与任何判据。
_DRAMA_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _drama_index(n: int) -> str:
    return _DRAMA_CIRCLED[n - 1] if 1 <= n <= len(_DRAMA_CIRCLED) else f"{n}."


def _drama_rows(lines) -> tuple[list[tuple[str, str, str, str]], int]:
    """把 C# 传来的 `lines` 洗成 `[(kind, who, name, text)]`：丢掉没有原文的项（空行对模型没意义，
    只占 token），超上限时**保留最近的 `_MAX_DRAMA_LINES` 句**并报告丢了几句。

    `kind`：`"page"` = 页面正文；`"option"` = **玩家点的选项**（09-23 加）——选项文本不在页面
    正文里（`GetDialogueText` 不含选项），不带上它，论道/邀约这类问答式交互模型就"只知道疑问、
    不知道玩家选了什么"。缺省/非法值一律当 `"page"`（老 C# 不带这个字段，兼容）。

    容错是刻意的：这段数据跨语言而来（C# 的 `DramaSegmentRecorder` → WS → 这里），
    字段缺失/类型不对都不该让一个回合炸掉 —— 坏项直接丢，剩下的照常渲染。
    """
    rows: list[tuple[str, str, str, str]] = []
    if not isinstance(lines, list):
        return rows, 0
    for it in lines:
        if not isinstance(it, dict):
            continue
        t = str(it.get("text") or "").strip()
        if not t:
            continue
        kind = str(it.get("kind") or "page").strip().lower()
        if kind not in ("page", "option"):
            kind = "page"
        rows.append((kind, str(it.get("speaker") or "").strip().lower(),
                     str(it.get("name") or "").strip(), t))
    dropped = max(0, len(rows) - _MAX_DRAMA_LINES)
    if dropped:
        rows = rows[dropped:]
    return rows, dropped


def _drama_row_text(kind: str, who: str, name: str, text: str) -> str:
    """一行的渲染：**归属写在行首**，判不出就明写判不出（别让模型自己猜谁在说）。

    选项行（`kind=="option"`）渲染成"玩家（X）选择了：…"——它是**玩家的动作**，不是台词。
    不写清楚的话，模型会把"赏花/闲聊/接受"这类选项当成 NPC 说的话。
    """
    if kind == "option":
        head = f"玩家（{name}）选择了" if name else "玩家选择了"
        return f"{head}：{text}"
    if who == "player":
        head = f"玩家（{name}）" if name else "玩家"
    elif who == "npc":
        head = f"你（{name}）" if name else "你"
    else:
        head = "（说话人没判出来的一句）"
    return f"{head}：{text}"


def format_game_drama_message(text: str = "", speaker: str = "", lines=None,
                              in_action: bool = False, verdict_page: bool = False) -> str:
    """游戏内交互舞台指令：C# DramaAiOption 在原生剧情窗注入「AI 对话」按钮，点击后把
    **本页屏幕上的成品句**经 npc_initiative(intent=game_drama, text=原文, speaker=谁说的) 传进来。

    **引文归属由「屏幕上的说话人」决定，不由「谁主动」决定**（09-13 定案）：
      ① `speaker="npc"` —— 这句是 NPC 自己说的（NPC 找上门：过月寻仇 / 邀约 / 赠送；也包括
         玩家点闲聊后 NPC 回的那句问候）。措辞只陈述「这句出自你口」，**不声称谁主动**；
         动作指令与 ② **分叉**（09-22）：②说"接住这个话头"，①说"顺着自己这句往下说、
         别当成玩家的话去回应"——共用那句会把刚写对的归属又抢回"对方在说话"那一侧。
      ② `speaker="player"` —— 这句是玩家说的（玩家在剧情里选了话 / 说了话），引文归玩家；
      ③ 空 / 未知 —— **不点名**（保底中性文案）。
    为什么必须有归属：屏幕句本身不含说话人，「我这里有一个青须藤*48准备赠于你，你需要此物吗？」
    两头都可能说；一律中性地丢给模型 → 09-13 真机猜反（NPC 送礼那一页被当成"玩家送我"，
    NPC 开口道谢）。**C# 侧判据 = 剧情窗两张立绘 `RawImage` 的底色明暗**：听者被游戏压到
    `rgba=0.30`、说者保持 `1.00`，亮者即说者；左右按世界坐标 x 分组取组内最亮再比，
    凑不齐两侧或明暗同档一律判不出（返回空 → 落回本函数的中性支路）。真机数据与
    **已被证伪的旧判据**（`imgBgPlayerDark`/`imgBgOtherDark` 压暗遮罩——整个剧情窗没有
    任何名字含 dark 的节点）见 docs/reference/design-notes.md §1。原文可能为空（C# 侧取文本失败兜底）——此时只有舞台指令。

    **`lines`（09-22 新增，可选）：一次当面相遇的整段原文**，每项
    `{seq, drama_id, speaker, name, text}`，由 C# 的「剧情相遇分段器」攒出，**只含没投过的增量**。
      ① **≤1 句**：走下面这几条**单句支路**（措辞是真机一轮轮调出来的，别为统一而改）；
      ② **≥2 句**：按发生先后编成①②③…，每行自带归属，结尾按**最后一句是谁说的**选动作词
         —— 最后一句是玩家说的才说"接住这个话头"；是 NPC 自己的话就得说"顺着自己这句往下说、
         别当成玩家的话去回应"（同单句支路那条 09-22 的教训："接住"这个动词预设了话是对方抛来的）。
    为什么要有它（用户需求）：原来点「对话」只把**屏幕上此刻那一句**交给模型，而一次相遇往往
    翻了好几页（切磋是「约战 → 打完 → 战后两页」这种量级），先发生的事模型全看不见。

    **`verdict_page`（09-24 新增，可选）**：C# 判"**当前这一页**是不是战斗处置页"
    （页号 20403/20404，配表事实；翻到后页不注入，用户拍板甲案）—— 是则追加
    `_VERDICT_NOTE`（"别调工具去执行页面上的献物/求饶"，完整理由见常量定义处）。
    """
    text = (text or "").strip()
    who = (speaker or "").strip().lower()

    # 两条系统说明**可同时出现**（来源不同、互不替代；各自的完整理由见常量定义处）：
    #   · `_IN_ACTION_NOTE`（in_action）—— 「这场交互还在进行中」：C# 在点击那一刻采集、
    #     实时投影，补投时不再成立（与 busy 同口径，别当漏改补上）；
    #   · `_VERDICT_NOTE`（verdict_page）—— 「这一页是战斗处置页」：C# 判当前页号（配表事实）。
    note = (_IN_ACTION_NOTE if in_action else "") + (_VERDICT_NOTE if verdict_page else "")

    rows, dropped = _drama_rows(lines)
    if len(rows) <= 1:
        # 单句：沿用原有分支（`lines` 只有一句时以它为更准的来源，其余一字不动）
        if rows:
            text = rows[0][3] or text
            who = rows[0][1] or who
    else:
        head = "（游戏内交互：你和玩家之间刚经过了这样一幕，按发生的先后列在下面，都是**已经说过的话**——）"
        body = "\n".join(
            f"{_drama_index(i)} {_drama_row_text(k, w, n, t)}" for i, (k, w, n, t) in enumerate(rows, 1)
        )
        if dropped:
            head += f"（更早的 {dropped} 句已略）"
        last_kind, last_who = rows[-1][0], rows[-1][1]
        if last_who == "player" or last_kind == "option":
            # 选项行 = 玩家刚做完一个选择（玩家视角的最后一步），与"玩家刚说完"同一档处理
            tail = "（玩家刚说完最后一句。请以你的人设与当下心境自然地接住这个话头，不要复读括号内容。）"
        elif last_who == "npc":
            # 同单句 npc 支路的教训：最后一句是**你自己**说的，说"接住话头"会让模型把它当玩家的话
            tail = ("（最后一句是**你自己已经说出口的话**，玩家还没接茬。请顺着自己这句往下说、"
                    "或另起话头，**别把它当成玩家说的话去回应**，不要复读括号内容。）")
        else:
            tail = "（请以你的人设与当下心境自然地接住这个话头，不要复读括号内容。）"
        return f"{head}\n{body}\n{note}{tail}"

    if not text:
        return "（游戏内交互：你和玩家之间刚发生了一段交谈，请以你的人设与当下心境自然地接住。）" + note
    if who == "player":
        return (f"（游戏内交互：玩家刚对你说了这样一句——「{text}」）"
                "（请以你的人设与当下心境自然地接住这个话头，不要复读括号内容。）" + note)
    if who == "npc":
        # ★ 09-22 真机：这句与 player 支路**共用**过"接住这个话头"，结果 NPC 自己说的告白
        #   被当成玩家递过来的话去回应（think 原文："唐炎说了句情话"）。"接住"这个动词
        #   本身就预设了话是**对方**抛来的 —— 归属写对了，动作词却把它抢回去。
        return (f"（游戏内交互：你刚对玩家说了这样一句——「{text}」）"
                "（这句是**你自己已经说出口的话**，玩家还没接茬。请顺着自己这句往下说、"
                "或另起话头，**别把它当成玩家说的话去回应**，不要复读括号内容。）" + note)
    return (f"（游戏内交互：你和玩家之间刚经过了这样一幕——「{text}」）"
            "（请以你的人设与当下心境自然地接住这个话头，不要复读括号内容。）" + note)