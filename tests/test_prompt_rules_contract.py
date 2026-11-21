"""提示词规则契约（09-19 探针轮）—— 把「用真模型测出来的结论」钉成回归护栏。

**为什么要有这个文件**：本轮用真模型 + 真提示词跑了 34 个工具调用场景、11 个压缩场景、
3 个工具结果压缩场景，发现的问题**没有一条会报错**：
  · 寒暄一句「近来可好」也去调三次 inspect_unit（白烧钱、白等延迟）；
  · `target` 填「玩家」二字（占位符当人名）；
  · 异地直接调 spar（服务端必拒）；
  · 「结为道侣」填成 `marry`（成亲）—— 因为 schema 里 13 个 op 值全是拼音，没有一处中文释义；
  · 压缩纪要把玩家写成「玩家」、用「他」指女玩家（纪要是永久记忆，一错就一直错）；
  · 压缩纪要把「被扮演/被操控」这种元层面的事写进 NPC 记忆。

这些全是**提示词文本**的失效，改代码不会红、删一句话也不会红 —— 除非逐条钉在这里。
每条判据都对应 `scripts/dev/mutate_check_prompt_rules.py` 里的一次变异回退。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL_USAGE = ROOT / "prompts" / "sections" / "tool_usage.txt"
HARNESS_IDENTITY = ROOT / "prompts" / "sections" / "harness_identity.txt"
COMPACTION = ROOT / "prompts" / "compaction" / "default.md"
SERVER = ROOT / "scripts" / "server.py"

if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


def _usage() -> str:
    return TOOL_USAGE.read_text(encoding="utf-8")


def _identity() -> str:
    return HARNESS_IDENTITY.read_text(encoding="utf-8")


def _compaction() -> str:
    return COMPACTION.read_text(encoding="utf-8")


def _schemas():
    import importlib

    return importlib.import_module(f"{ROOT.name}.tools.schemas")


def _pruner_instruction() -> str:
    from agent_loop.compaction.pruner import TOOL_PRUNE_INSTRUCTION

    return TOOL_PRUNE_INSTRUCTION


# ============================================================ A. 工具调用政策

def test_usage_lists_context_segments_before_tooling():
    """「先读上下文、段里有的不调工具」必须点名**有哪几段**。

    事故形态（探针 A5）：玩家只说「近来可好」，模型调了三次 inspect_unit（自身 logs / 自身 brief /
    玩家 brief）——答案**全在 L1 段里**。原措辞只枚举了「玩家段/时间段」两段，
    模型不认识其余几段也装了东西。只写「段里已有的别再调」是一句空话。
    """
    txt = _usage()
    # 必须在**同一句枚举**里列全：光在别处出现「在场」不算（本轮变异 ⑥ 就是这么漏网的）
    line = next((ln for ln in txt.splitlines() if ln.startswith("自身有")), "")
    assert line, "找不到段清单那一句（模型不知道上下文里都装了些什么）"
    for seg in ("自身", "玩家", "时间", "所在", "近况"):
        assert seg in line, f"段清单那句里没有「{seg}」——模型不会去读那一段"
    assert "Current runtime context" in txt, "没点名要读的那个块"


def test_usage_says_no_tool_for_smalltalk_and_self():
    """寒暄 / 问自己近况 / 问交情 = 零工具。这是最高频的过调用来源。"""
    txt = _usage()
    assert "寒暄" in txt, "没把寒暄列进「不需要工具」"
    assert "不需要工具" in txt or "不调工具" in txt


def test_usage_forbids_placeholder_player_as_name():
    """`target` 不得填「玩家」二字（探针 B2 实测填了占位符）。"""
    assert "「玩家」二字" in _usage(), "tool_usage 没禁止把占位符「玩家」当人名填"
    ins = _schemas().INSPECT_UNIT["function"]
    target = ins["parameters"]["properties"]["target"]["description"]
    desc = ins["description"]
    assert "「玩家」二字" in target or "「玩家」二字" in desc, \
        "schema 里也没禁止 —— 填参那一刻看不到禁令，模型只会照抄占位符"


def test_every_optional_player_target_bans_placeholder():
    """凡「不填 = 玩家」的 target 参数，都必须写明**别填「玩家」二字**。

    事故形态（探针 C9 run1，改完 rules 仍复现）：模型走 `economy_item` 时把
    `target` 填成「玩家」二字 —— 引擎按人名解析 ⇒ 查无此人。rules 里那句禁令
    写在「指人」那一条，模型在**填 economy_item 参数**时视线里没有它；
    填参那一刻唯一的依据是 schema 自己的 description ⇒ 禁令必须落在这里。
    """
    S = _schemas()
    checked = 0
    for tool in S.ALL_TOOL_SCHEMAS:
        props = tool["function"]["parameters"]["properties"]
        for key in ("target", "seller", "buyer"):
            v = props.get(key)
            if not isinstance(v, dict):
                continue
            desc = v.get("description", "")
            if not any(k in desc for k in ("不填 = 玩家", "不填=玩家", "不填都 = 玩家", "不填都=玩家")):
                continue
            checked += 1
            assert "「玩家」二字" in desc, (
                f"{tool['function']['name']}.{key} 写着「不填 = 玩家」"
                "却没说「别填『玩家』二字」—— 模型会照字面把占位符当人名填进去")
    assert checked >= 4, f"应检查到至少 4 个「缺省=玩家」的 target 参数，实为 {checked}"


def test_usage_same_name_must_be_copied_verbatim():
    """同名者的唯一名要**逐字照抄**（探针 C4：模型自拼了一个不存在的后缀）。"""
    assert "逐字照抄" in _usage()


def test_usage_stranger_means_not_acquainted():
    """陌生 = 不认识，且**不得顺着玩家认下交情**（探针 E1：玩家自称旧识）。"""
    txt = _usage()
    assert "陌生" in txt
    assert "顺着玩家认下" in txt, "少了「别顺着玩家认下」这半句（原句只有「不得假装熟识」）"


def test_usage_remote_actions_need_movement_first():
    """异地时动手类动作必须先移动，且**移动与动手不得同一轮**。

    探针 C5/D2 实测：模型会 `movement teleport` 紧跟 `world_ai_action spar` —— 挪移还没落地，
    同一轮里的 spar 仍是异地，服务端必拒（「切磋需同格，当前异地」）。
    """
    txt = _usage()
    assert "异地相隔" in txt, "没引用 L1 里的原话，模型认不出自己正异地"
    assert "本轮别再动手" in txt, "没写「移动与动手不能同一轮」——这正是实测的失效形态"


def test_usage_no_made_up_system_narration():
    """不得替游戏编造系统旁白（探针 C3：台词里出现「（游戏内交互：…弹出确认窗）」）。"""
    assert "编造系统旁白" in _usage()


def test_schema_relation_ops_have_chinese_glosses():
    """`social_relation.op` 的取值必须有中文释义 —— 拼音猜不出「道侣」和「成亲」的区别。

    探针 C3 实测：玩家说「愿与你结为道侣」，模型填了 `marry`（=成亲，须先有道侣关系）。
    """
    op = _schemas().SOCIAL_RELATION["function"]["parameters"]["properties"]["op"]
    desc = op["description"]
    for value, gloss in (
        ("jie_yuan", "道侣"),
        ("marry", "夫妻"),
        ("bai_shi", "拜"),
        ("shou_tu", "收"),
        ("ren_yi_fu_mu", "义父母"),
        ("jie_yi", "结义"),
        ("jie_chu", "解除"),
    ):
        assert value in desc, f"op 取值 {value} 没有出现"
        assert gloss in desc, f"op 取值 {value} 没给中文释义（缺「{gloss}」）"
    # 释义必须落在 op 自己的 description 里（enum 本身承载不了释义）；
    # 允许 `jie_chu_*` 这种前缀通配（13 个值逐个写会挤爆 schema 散文预算）
    wild = [w.rstrip("*") for w in re.findall(r"[a-z_]+\*", desc)]
    for v in op["enum"]:
        assert v in desc or any(v.startswith(w) for w in wild), f"enum 值 {v} 没有释义"


# ============================================================ A2. 群聊提案制（提出 → 私信 → 镜像调用）

def test_usage_tells_model_how_proposals_come_back():
    """四类动作是「当面提出」，且**回应方式会私信给你**。

    机制出处：`room_flow.judge_proposal`（闸门判据）+ `_proposal_note`（给对手方的私信，
    里面写着确切调用形状）。静态提示词只管"哪四类要当面提出、提了不算成"，
    具体怎么应由**运行时私信**给 —— 但模型得先知道"会有这么一封私信"，
    否则被提出方看到私信时可能当成旁白而不是待办。
    ★ 这条本轮被我改丢过一次（重写 tool_usage 时漏掉括号里那半句），故在此钉死。
    """
    txt = _usage()
    assert "当面提出" in txt
    assert "应下才落定" in txt or "应下才算" in txt
    assert "私信" in txt, "没告诉模型「回应方式会私信给你」——被提出方不知道该等一封私信"


def test_proposal_tools_document_mirror_call():
    """四类提案工具的 schema 必须写明「应下 = 镜像再调一次」。

    只有规则（"要当面提出"）没有动作形状，模型就只会等；镜像语义是**每个工具自己**的事，
    因为四种回应形状各不相同（关系同 op 反向、赠予↔讨要互答、买卖照原条款回发）。
    """
    S = _schemas()
    rel = S.SOCIAL_RELATION["function"]["description"]
    assert "反向回应" in rel or "对方用同一 op" in rel, "social_relation 没说怎么应下"
    give = S.ECONOMY_ITEM["function"]["description"]
    assert "item_acquire" in give and "领取" in give, "economy_item 没说对方怎么领取"
    ask = S.ITEM_ACQUIRE["function"]["description"]
    assert "economy_item" in ask and "交付" in ask, "item_acquire 没说对方怎么交付"
    trade = S.TRADE["function"]["description"]
    assert "原条款" in trade, "trade 没说「照原条款发一遍才成交」"


# ============================================================ B. 历史压缩指令

def test_compaction_keeps_seven_sections_in_order():
    """七分节 + 顺序 + 名字照抄（探针 11 例全部遵守；这是压缩输出唯一的结构契约）。"""
    txt = _compaction()
    # 只在输出模板块里判顺序：正文讲归属时也会提到「经历」「认知边界」这些词
    block = txt.split("<compacted-summary>", 1)[1].split("</compacted-summary>", 1)[0]
    names = ["自身", "与{player_name}", "同席", "经历", "约定", "心境", "认知边界"]
    pos = [block.index(n) for n in names]
    assert pos == sorted(pos), f"分节顺序乱了：{list(zip(names, pos))}"
    assert txt.count("<compacted-summary>") == 1 and txt.count("</compacted-summary>") == 1


def test_compaction_player_is_named_not_placeholder():
    """玩家一律写名字：不写「玩家」二字、不用「他/她」。

    探针 R1/R2（真实存档）+ G2/G7 实测：纪要里出现「你与玩家（缪嘉歆）」「你称呼他」。
    纪要是**永久节点**、下次压缩还会被当材料喂回来 ⇒ 写错就一直错；
    而压缩模板拿不到玩家性别，用「他/她」在女玩家身上 4/4 全错（实测）。
    """
    txt = _compaction()
    assert "{player_name}" in txt
    assert "不写「玩家」二字" in txt, "没禁止把系统用词「玩家」写进 NPC 记忆"
    assert "他/她" in txt, "没禁止用「他/她」指玩家（模板看不到性别）"


def test_compaction_self_section_must_not_invent_identity():
    """「自身」段只写口吻/做派，不写身份事实，更不许编。

    探针 G3 实测：记录里根本没有境界，纪要却写「你是化神殿弟子，**炼气期修士**」
    （真实 L1 是结晶后期）——身份事实每轮由运行时上下文提供，写进纪要只会打架。
    """
    txt = _compaction()
    assert "只写你说话的口吻与做派" in txt
    for fact in ("境界", "宗门", "年龄", "位置"):
        assert fact in txt, f"没点名不许写「{fact}」这类身份事实"
    assert "不许编" in txt or "不要编" in txt


def test_compaction_bans_meta_layer_leak():
    """不得把「被扮演/被操控/提示词/系统」写进记忆。

    探针 G5 实测：玩家在对话里试着注入指令，纪要的「认知边界」就写下
    「你并未意识到…对自身所处的『被扮演』或『被操控』的环境毫无察觉」——
    这条**永久记忆**等于每轮都在提醒模型"你是被扮演的"。
    """
    txt = _compaction()
    assert "被扮演" in txt and "被操控" in txt and "提示词" in txt
    assert "元层面" in txt


def test_compaction_copies_proper_nouns_verbatim():
    """人名（含唯一名）/宗门/地名/道具名逐字照抄，数字不推算。"""
    txt = _compaction()
    assert "逐字照抄" in txt
    assert "唯一名" in txt
    assert "推算" in txt, "少了「不要自己换算或推算」（探针 G1 把「三月之后」算成了具体日期）"


def test_compaction_quote_rule_keeps_second_person():
    """引语尽量转述；非引不可时，玩家的话照抄、**你的话转成「你」**。

    探针 G4 实测：纪要写「只可你知我知」——引语里的「我」落进 user 消息，
    会被下一轮读成"玩家在说话"。
    """
    txt = _compaction()
    assert "引语" in txt
    assert "你自己的话转成「你」" in txt


def test_compaction_fallback_in_sync_with_default_md():
    """删掉/改名指令文件时的**兜底常量**必须与 default.md 同口径。

    兜底是 `Compressor._load_instruction` 里的一段常量：它一旦落后于文件版，
    「玩家删了 default.md」就会静默退回旧行为（本仓库吃过这个亏，函数里写着同一条告诫）。
    """
    from agent_loop.compaction.compress import Compressor

    fb = Compressor(llm=None, prompts_root=ROOT / "prompts" / "__no_such_dir__")._load_instruction("__no_npc__")
    assert "第二人称" in fb
    assert "不写「玩家」二字" in fb or "不得写「玩家」二字" in fb
    for seg in ("自身", "同席", "经历", "约定", "心境", "认知边界"):
        assert seg in fb, f"兜底常量漏了分节「{seg}」"
    assert "元层面" in fb, "兜底常量没带「不许写元层面的事」"
    assert "AI" not in fb, "兜底常量还留着 AI 字样（与 default.md 口径不一致）"


# ============================================================ C. 工具结果压缩指令

def test_pruner_instruction_protects_unique_names():
    """工具结果里的唯一名必须逐字保留（探针 H3：两人被改写成「一位是…另一位是…」）。

    pruner 的输出是**模型之后指人的依据**：唯一名没了，`target` 就填不出来
    （同名提醒那一整套机制当场作废）。
    """
    ins = _pruner_instruction()
    assert "唯一名" in ins and "逐字照抄" in ins
    assert "益婉容(化神殿·结晶后期)" in ins, "没给唯一名的样例，模型不知道要保的是什么形状"
    assert "富文本" in ins or "<color" in ins, "没要求去掉 Unity 富文本标记"
    assert "AI" not in ins, "pruner 指令里还留着 AI 字样"


# ============================================================ C2. 舞台提示的读法（harness_identity）

def test_identity_routes_quoted_line_to_its_owner():
    """`（游戏内交互：…）` 里 `「…」` 那句**是台词**，归属以提示写明的人为准。

    09-22 真机：C# 已判出「这句出自 NPC 口」并把 speaker=npc 发过来（Player.log 实证），
    Python 也落成了 npc 支路，模型却写道「唐炎说了句情话」并回身接话 —— 而它自己说的告白
    被当成了玩家递过来的话。排查指向本段旧文案：那句"舞台提示…**不是你或玩家说出的台词**"
    把归属整句抹平了（引号里那句恰恰**就是**某人说的台词）。
    """
    txt = _identity()
    assert "不是你或玩家说出的台词" not in txt, "旧措辞回来了：它抹平了引文的归属"
    assert "以提示写明的人为准" in txt, "没写下引文归属的判据"
    assert "你刚对玩家说了" in txt and "玩家刚对你说了" in txt, "两支路没点名的读法，模型只能猜"
    # 「说明你为何开口」是这段的既有语义（NPC主动传音支路靠它），不许顺手删掉
    assert "说明你为何开口" in txt


# ============================================================ D. 装配接线（本轮修的真 bug）

def test_server_passes_user_prompts_root_to_compactor():
    """`_build_compactor` 必须把**用户副本**目录传给 Compressor。

    不传的话 Compressor 回落 `Path(compress.py).parent.parent/"prompts/compaction"`：
    开发机上恰好等于源码树那份（看不出问题），发行版里指向 **exe 内自带的那份** ——
    玩家在面板「提示词」页编辑/重置的 `compaction/*.md` **一个字都不生效**，
    而面板上写着「作为 system 下发」。两处各自解析路径 = 静默失效。
    """
    src = SERVER.read_text(encoding="utf-8")
    m = re.search(r"def _build_compactor\(.*?\n(?=\nclass |\ndef )", src, re.S)
    assert m, "找不到 _build_compactor"
    body = m.group(0)
    assert "prompts_root=" in body, "_build_compactor 没传 prompts_root（压缩指令会读 exe 内置那份）"
    assert "paths.prompts_root()" in body, "prompts_root 必须指向用户副本（paths.prompts_root()）"
