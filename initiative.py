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
    "game_drama": "游戏交互",
}


def intent_label(intent: str) -> str:
    """意图键 → **UI 短标签**（对话窗分隔条 `—— 主动传音 · 表达思念 ——` 用）。

    与 INITIATIVE_INTENTS 分开：那份是喂给模型的舞台指令（"向玩家表达思念"），
    这份是给人看的四到六字标签。未知键回落原键——将来 C# 新增意图时不至于空白。
    """
    key = (intent or "").strip()
    return INITIATIVE_LABELS.get(key, key)


def format_initiative_message(intent: str, reason: str = "") -> str:
    """把意图包装成伪 user 文本：括号舞台指令，模型据此以 NPC 口吻主动开场。

    括号语义（同神识传音 `(向玩家打个招呼)`）：模型与后续历史都能认出这是
    「叙述指令」而非玩家真言；缘由（可选事实）给模型开口的抓手。
    """
    desc = INITIATIVE_INTENTS.get(intent) or INITIATIVE_INTENTS["greet"]
    text = f"（NPC主动传音：{desc}）"
    reason = (reason or "").strip()
    if reason:
        text += f"（缘由：{reason}）"
    return text


def format_game_drama_message(text: str = "", speaker: str = "") -> str:
    """游戏内交互舞台指令：C# DramaAiOption 在原生剧情窗注入「AI 对话」按钮，点击后把
    **本页屏幕上的成品句**经 npc_initiative(intent=game_drama, text=原文, speaker=谁说的) 传进来。

    **引文归属由「屏幕上的说话人」决定，不由「谁主动」决定**（09-13 定案）：
      ① `speaker="npc"` —— 这句是 NPC 自己说的（NPC 找上门：过月寻仇 / 邀约 / 赠送；也包括
         玩家点闲聊后 NPC 回的那句问候）。措辞只陈述「这句出自你口」，**不声称谁主动**；
      ② `speaker="player"` —— 这句是玩家说的（玩家在剧情里选了话 / 说了话），引文归玩家；
      ③ 空 / 未知 —— **不点名**（保底中性文案）。
    为什么必须有归属：屏幕句本身不含说话人，「我这里有一个青须藤*48准备赠于你，你需要此物吗？」
    两头都可能说；一律中性地丢给模型 → 09-13 真机猜反（NPC 送礼那一页被当成"玩家送我"，
    NPC 开口道谢）。**C# 侧判据 = 剧情窗两张立绘 `RawImage` 的底色明暗**：听者被游戏压到
    `rgba=0.30`、说者保持 `1.00`，亮者即说者；左右按世界坐标 x 分组取组内最亮再比，
    凑不齐两侧或明暗同档一律判不出（返回空 → 落回本函数的中性支路）。真机数据与
    **已被证伪的旧判据**（`imgBgPlayerDark`/`imgBgOtherDark` 压暗遮罩——整个剧情窗没有
    任何名字含 dark 的节点）见 APPENDIX G.1。原文可能为空（C# 侧取文本失败兜底）——此时只有舞台指令。
    """
    text = (text or "").strip()
    who = (speaker or "").strip().lower()
    if not text:
        return "（游戏内交互：你和玩家之间刚发生了一段交谈，请以你的人设与当下心境自然地接住。）"
    if who == "npc":
        head = f"（游戏内交互：你刚对玩家说了这样一句——「{text}」）"
    elif who == "player":
        head = f"（游戏内交互：玩家刚对你说了这样一句——「{text}」）"
    else:
        head = f"（游戏内交互：你和玩家之间刚经过了这样一幕——「{text}」）"
    return head + "（请以你的人设与当下心境自然地接住这个话头，不要复读括号内容。）"