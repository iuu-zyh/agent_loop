"""tools/text_render — 工具结果润色层（全系统唯一叙述来源）

分工契约（2026-09-10 定）：
- C# ToolExecutor 只返回**原始结构化数据**（data），不再组装叙述文本（note/text 退役）；
- 本模块按工具名把 data **忠实**翻译成自然语言，喂给 LLM（tool_result）与 UI（行动记录行）；
- **忠实优先于简短**（2026-09-10 用户定调）：data 里有的语义字段一律呈现——不截断列表、
  不隐藏失败项、不省略数值；省 token 只靠"去掉 JSON 结构包装"，不靠丢信息。
- **边界铁律（2026-09-10 用户定调）：渲染层禁止任何截断/上限/丢弃——N 条进必须 N 条出。**
  所有"取多少/留几条"的决策都归 C#（如 query_world 的 `count`/`top`、search_units 的前 10、
  经历分页每页 5 条）。Python 只做忠实翻译，不替 C# 做取舍。
  防回退哨兵：`tests/test_text_render.py::test_no_truncation_anywhere`。
  仅纯机器字段（内部 id / 坐标等）可省略。
- C# 的 conf 查表中文字段（personality / beauty_label / relation 中文等）属**数据本地化**，留在 C#；
- 渲染器缺失 / data 形态不认识 / 抛异常 → 返回 None，调用方回退 json.dumps(全量)
  （错误帧、未来新工具、渐进改造期的兜底，行为与旧版一致）。

人称约定：「你」=玩家；NPC 一律用名字。列表类结果换行分条（UI 与模型都更易读）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

_RENDERERS = {}


def renderer(name: str):
    """注册表装饰器：@renderer("inspect_unit")"""
    def _wrap(fn):
        _RENDERERS[name] = fn
        return fn
    return _wrap


def render(name: str, args: Dict[str, Any], res: Any) -> Optional[str]:
    """工具结果 → 润色文本。返回 None = 无渲染器/形态不认识/异常，调用方回退全量 JSON。"""
    try:
        if not isinstance(res, dict) or not res.get("success"):
            return None                       # 错误帧：error 字符串本身是人话，交调用方回退
        data = res.get("data")
        if not isinstance(data, dict):
            return None
        # ★ 排队受理（09-24）：动作工具在"此刻发不出去"（战斗中 / 剧情链收尾）时返回
        #   `{queued:true, summary, note}` —— 它不是动作结果，但**必须让模型看懂**：
        #   已受理、别重复调用、办完会有通知（C# 侧文案见 ToolExecutor.Execute 的排队分支）。
        #   放在各工具渲染器**之前**：这是六个动作工具共用的形状，单个渲染器都不认识它。
        if data.get("queued"):
            parts = [_s(data.get("summary")).strip(), _s(data.get("note")).strip()]
            body = " —— ".join(p for p in parts if p)
            return f"（系统：{body or '这个动作此刻发不出去，已受理，稍后自动办理。'}）"
        fn = _RENDERERS.get(name)
        if fn is None:
            return None
        text = fn(args or {}, data)
        if not isinstance(text, str):
            return None
        text = text.strip()
        return text or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _num(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _lst(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _names(v: Any) -> List[str]:
    """名字数组（或单值）→ 非空字符串列表。"""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x or "").strip()]
    s = str(v or "").strip()
    return [s] if s else []


def _named_refs(v: Any) -> List[str]:
    """`[{name, unit_id, display?}]`（关系簿）→ 名字列表；兼容旧的纯字符串数组。

    ★ 09-17 反转：**不再打印 unit_id**。原实现输出 `益婉容(Xs6JDI)` 并把 id 交给模型
    （那是对 D.1 重名事故的修复），但"模型接触 id"本身是更大的问题：它会自己编 id、
    把 id 当名字念。现在改由 `tools/unit_refs` 负责区分同名者：需要区分时那条引用会被
    就地打上 `display`（`益婉容(化神殿·结晶后期)`），渲染优先用它；映射表以该字符串为键，
    中间层在工具调用前把它翻成精确 unitID。模型看到的仍然只是"名字"。
    """
    out: List[str] = []
    items = v if isinstance(v, list) else [v]
    for x in items:
        if isinstance(x, dict):
            shown = _s(x.get("display")).strip() or _s(x.get("name")).strip()
            if shown:
                out.append(shown)
            continue
        s = _s(x).strip()
        if s:
            out.append(s)
    return out


def _kv(title: str, parts: List[str]) -> str:
    body = "，".join(p for p in parts if p)
    return f"{title}：{body}" if body else ""


def _lines(header: str, items: List[str]) -> str:
    return header + ("\n" + "\n".join(items) if items else "")


# ---------------------------------------------------------------------------
# inspect_unit（查询人物档案）
# ---------------------------------------------------------------------------

def _persona(data: Dict[str, Any]) -> str:
    parts: List[str] = []
    head = _s(data.get("display")).strip() or _s(data.get("name"))
    for key in ("sex", "realm"):
        if data.get(key):
            head += "，" + _s(data[key])
    if data.get("sect"):
        head += "/" + _s(data["sect"])
    if data.get("race"):
        head += "/" + _s(data["race"])
    if head:
        parts.append(head)
    if data.get("title"):
        parts.append("道号：" + _s(data["title"]))
    per = data.get("personality")
    if isinstance(per, dict):
        tags = []
        if per.get("inner"):
            tags.append("内" + _s(per["inner"]))
        for t in _lst(per.get("outer")):
            if t:
                tags.append("外" + _s(t))
        if tags:
            parts.append("性格：" + "·".join(tags))
        # 面板描述（inner_desc/outer_desc 与名字平行对齐）：名字——描述正文，只列有描述的
        notes: List[str] = []
        if per.get("inner") and per.get("inner_desc"):
            notes.append(f"{_s(per['inner'])}——{_s(per['inner_desc'])}")
        outs = [t for t in _lst(per.get("outer")) if t]
        ods = _lst(per.get("outer_desc"))
        for i, nm in enumerate(outs):
            d = _s(ods[i]) if i < len(ods) else ""
            if d:
                notes.append(f"{nm}——{d}")
        if notes:
            parts.append("性格注解：" + "；".join(notes))
    # 魅力/声望：面板档位词 + 原始值都给出（档位是面板口径，数值用于精确比较）
    bl, bv = _s(data.get("beauty_label")), _num(data.get("beauty"))
    if bl or bv is not None:
        parts.append("魅力" + (f"{bl}（{bv}）" if (bl and bv is not None) else (bl or _s(bv))))
    rl, rv = _s(data.get("reputation_label")), _num(data.get("reputation"))
    if rl or rv is not None:
        parts.append("声名" + (f"{rl}（{rv}）" if (rl and rv is not None) else (rl or _s(rv))))
    hobby = _names(data.get("hobby"))
    if hobby:
        parts.append("爱好：" + "、".join(hobby))
    if data.get("relation") is not None:
        # 关系三项是**被查者 → 发起者**的相对量，"对谁"由取数处随数据给出（`ref`）。
        # 这里不许再自己写「玩家」：查的若是玩家本人而措辞仍说「与玩家」，就成了
        # "玩家对自己关系陌生"，与上下文里写着的真实关系当面打架（09-21 真机）。
        # `ref` 缺失 = 发起方没解析到，回落成与玩家相对的老形状。
        ref = _s(data.get("ref")) or "玩家"
        same = bool(data.get("same_grid"))
        seg = f"与{ref}{'同处一地' if same else '异地'}，关系{_s(data.get('relation'))}"
        iv = _num(data.get("intim"))
        if iv is not None:
            seg += f"，好感{iv}"
        parts.append(seg)
    pt = data.get("point")
    if isinstance(pt, dict) and pt.get("x") is not None:
        parts.append(f"坐标({_s(pt.get('x'))},{_s(pt.get('y'))})")
    return "；".join(parts)


def _luck(lk: Any) -> str:
    """气运：先天/后天条目（含 desc——游戏文案属事实信息）。"""
    if not isinstance(lk, dict):
        return ""

    def items_of(key: str) -> List[str]:
        out = []
        for it in _lst(lk.get(key)):
            if isinstance(it, dict):
                nm = _s(it.get("name")).strip()
                ds = _s(it.get("desc")).strip().replace("\n", " ")
                if nm:
                    out.append(f"{nm}（{ds}）" if ds else nm)
            else:
                s = _s(it).strip()
                if s:
                    out.append(s)
        return out

    segs = []
    born, added = items_of("born"), items_of("added")
    if born:
        segs.append("先天" + "、".join(born))
    if added:
        segs.append("后天" + "、".join(added))
    return "气运：" + "；".join(segs) if segs else ""


# stats.attrs 三组属性的中文名（C# BuildAttrs 同口径；键=C# 输出键，缺失项直接跳过）
_ATTR_PAIRS = [  # (当前值键, 上限键, 中文)
    ("mood", "mood_max", "心情"),
    ("health", "health_max", "健康"),
    ("energy", "energy_max", "精力"),
    ("hp", "hp_max", "体力"),
    ("mp", "mp_max", "灵力"),
    ("sp", "sp_max", "念力"),
]
_ATTR_FLAT = {  # 组名 → [(键, 中文)]
    "combat": [
        ("attack", "攻击"), ("defense", "防御"),
        ("foot_speed", "脚力"), ("move_speed", "移速"),
        ("physical_free", "功法抗性"), ("magic_free", "灵根抗性"),
        ("crit", "会心"), ("guard", "护心"),
        ("crit_value", "暴击倍数"), ("guard_value", "抗暴倍数"),
    ],
    "aptitudes": [
        ("blade", "刀法"), ("spear", "枪法"), ("sword", "剑法"),
        ("fist", "拳法"), ("palm", "掌法"), ("finger", "指法"),
        ("fire", "火灵根"), ("froze", "水灵根"), ("thunder", "雷灵根"),
        ("wind", "风灵根"), ("earth", "土灵根"), ("wood", "木灵根"),
        ("refine_elixir", "炼丹"), ("refine_weapon", "炼器"), ("geomancy", "风水"),
        ("symbol", "画符"), ("herbal", "药材"), ("mine", "矿材"),
    ],
}


def _attrs_segments(attrs: Dict[str, Any]) -> List[str]:
    """全量动态属性 → 三段中文（个人属性/战斗属性/资质），与游戏 NPC 面板分组一致。
    只呈现 C# 实际采到的键；三组都空则返回空列表。"""
    segs: List[str] = []
    p = attrs.get("personal") if isinstance(attrs.get("personal"), dict) else {}
    bits: List[str] = []
    age, life = _num(p.get("age")), _num(p.get("life"))
    if age is not None or life is not None:
        bits.append(f"寿命{_s(age)}/{_s(life)}")
    for cur, mx, label in _ATTR_PAIRS:
        c, m = _num(p.get(cur)), _num(p.get(mx))
        if c is not None or m is not None:
            bits.append(f"{label}{_s(c)}/{_s(m)}")
    for k, label in (("luck", "幸运"), ("talent", "悟性")):
        v = _num(p.get(k))
        if v is not None:
            bits.append(f"{label}{v}")
    if bits:
        segs.append("个人属性：" + "，".join(bits))
    for group, title in (("combat", "战斗属性"), ("aptitudes", "资质")):
        g = attrs.get(group) if isinstance(attrs.get(group), dict) else {}
        gb = [f"{label}{v}" for k, label in _ATTR_FLAT[group] if (v := _num(g.get(k))) is not None]
        if gb:
            segs.append(f"{title}：" + "，".join(gb))
    return segs


def _stats(st: Dict[str, Any]) -> str:
    """属性全字段。attrs（动态全量）在→按面板分组三行渲染（root 重复键跳过防双列，
    魅力/声望不在 attrs 补上）；否则回退旧精简路径。道心两路都追加。"""
    parts: List[str] = []
    attrs = st.get("attrs")
    if isinstance(attrs, dict):
        parts.extend(_attrs_segments(attrs))
        for key, label in (("beauty", "魅力"), ("reputation", "声望")):   # 不在 attrs，从 root 补
            v = _num(st.get(key))
            if v is not None:
                parts.append(f"{label}{v}")
    else:
        hp, hpmax = _num(st.get("hp")), _num(st.get("hp_max"))
        if hp is not None or hpmax is not None:
            parts.append(f"气血{_s(hp)}/{_s(hpmax)}")
        for key, label in (("power", "攻击"), ("attack", "攻击"), ("defense", "防御"), ("energy", "精力"), ("mood", "心情"),
                           ("talent", "资质"), ("beauty", "魅力"), ("reputation", "声望")):
            v = _num(st.get(key))
            if v is not None:
                parts.append(f"{label}{v}")
    if st.get("heart"):
        seg = f"道心{_s(st['heart'])}"
        if st.get("heart_state"):
            seg += f"（{_s(st['heart_state'])}）"
        parts.append(seg)
    return _kv("属性", parts)


def _martial(o: Any) -> str:
    """功法槽：类型 + 名称 + id（id 是后续 chuan_gong 等工具的引用键，必须给）+ **技能说明**。

    技能说明（09-13 补）：C# 走 `UIMartialInfoTool.GetDesc`（面板「技能说明」同一入口），
    只剥富文本标签、不改字。原先只有名字和 id，模型挑功法（传功/学习/施法）时**看不到它干什么**，
    只能照名字猜 —— 与道具的 `desc` 是同一类缺口（见附录 D.6「道具介绍」）。
    """
    if not isinstance(o, dict):
        return ""
    name, sid, typ = _s(o.get("name")), _s(o.get("id")), _s(o.get("type"))
    if not name and not sid:
        return ""
    seg = f"{typ}「{name or sid}」"
    if sid:
        seg += f"(id={sid})"
    desc = _s(o.get("desc")).strip().replace("\n", " ")
    if desc:
        seg += f"——{desc}"
    return seg


def _abilities(ab: Dict[str, Any]) -> str:
    """功法**逐槽一行**：技能说明动辄上百字，全部塞进一行成句会糊成一片（id 必须留，它是引用键）。"""
    segs = []
    for key in ("skill_left", "skill_right", "step", "ultimate"):
        t = _martial(ab.get(key))
        if t:
            segs.append(t)
    for it in _lst(ab.get("abilitys")):
        t = _martial(it)
        if t:
            segs.append(t)
    if not segs:
        return ""
    return _lines("功法：", ["· " + s for s in segs])


def _inventory(inv: Dict[str, Any]) -> List[str]:
    """背包：每类一个标题行 + 条目分行（名称×数量、单价、小计，**介绍 desc**——悬浮窗同源文案）。
    杂项聚合（misc）单列一行（聚合无明细，自然无介绍）。装备带价值/介绍。灵石单列。"""
    out: List[str] = []
    pieces = 0
    for group in _lst(inv.get("props")):
        if not isinstance(group, dict):
            continue
        cat = _s(group.get("cat"))
        segs = []
        for it in _lst(group.get("items")):
            if not isinstance(it, dict):
                continue
            name, cnt = _s(it.get("name")), _num(it.get("count"))
            worth, tot = _num(it.get("worth")), _num(it.get("total"))
            if "灵石" not in name:
                pieces += cnt or 0        # 灵石是货币，不计入“件数”
            seg = f"{name}×{_s(cnt)}"
            detail = []
            if worth is not None:
                detail.append(f"单价{worth}")
            if tot is not None:
                detail.append(f"小计{tot}")
            if detail:
                seg += "（" + "，".join(detail) + "）"
            desc = _s(it.get("desc")).strip().replace("\n", " ")
            if desc:
                seg += f"——{desc}"
            segs.append(seg)
        misc = group.get("misc")
        if isinstance(misc, dict):
            kinds, mp, mw = _num(misc.get("kinds")), _num(misc.get("pieces")), _num(misc.get("worth"))
            if (kinds or 0) > 0:
                pieces += mp or 0
                seg = f"另有{kinds}种"
                if mp is not None:
                    seg += f"共{mp}件"
                if mw is not None:
                    seg += f"约值{mw}"
                segs.append(seg)
        if segs:
            out.append(f"{cat}：")
            out.extend("· " + x for x in segs)
    # 定向档（11-10）的抬头：inventory_cat / inventory_find 问的是"只看某一类""找某件东西"，
    # 与默认的"五类总览"是两种问题，抬头必须跟着变 —— 否则模型会以为自己看的是全部家当。
    # ★ 命中 0 条是**有效结论**（"他没有"）★，必须明说；否则模型容易把空结果当查询失败而反复重试。
    cats = _names(inv.get("cats"))
    find = _s(inv.get("find")).strip()
    if find:
        head = f"背包查找「{find}」：" + ("" if out else "未找到名称含此字串的道具。")
    elif cats:
        head = f"背包（{'、'.join(cats)}，全列）："
    else:
        head = f"背包（约{pieces}件）："
    # 默认档只在真有内容时加抬头（旧行为：空背包不输出任何行，避免"背包（约0件）"这种噪声）
    if out or find:
        out.insert(0, head)
    equips = [e for e in _lst(inv.get("equips")) if isinstance(e, dict)]
    if equips:
        out.append("身着：")
        for e in equips:
            w = _num(e.get("worth"))
            seg = _s(e.get("name")) + (f"（值{w}）" if w is not None else "")
            desc = _s(e.get("desc")).strip().replace("\n", " ")
            if desc:
                seg += f"——{desc}"
            out.append("· " + seg)
    money = _num(inv.get("money"))
    if money is not None:
        out.append(f"灵石：{money}")
    return out


_REL_BOOK = [
    ("married", "道侣"), ("lover", "恋人"), ("master", "师尊"), ("student", "徒弟"),
    ("brother_back", "结义"), ("parent", "父母"), ("children", "子女"),
    ("brother", "兄弟姐妹"), ("parent_back", "义父母"), ("children_back", "义子女"),
    ("friend_units", "好友"), ("enemy_units", "仇人"),
]


def _relation_book(rl: Dict[str, Any]) -> str:
    """关系簿：12 容器全列（不再限 3 名 —— 忠实优先），附人情。

    名字走 `_named_refs`（需要区分同名者时那条引用自带 `display`）；**不再附 unit_id**。
    """
    parts = []
    for key, label in _REL_BOOK:
        names = _named_refs(rl.get(key))
        if names:
            parts.append(f"{label}：" + "、".join(names))
    hv = _num(rl.get("human_value"))
    if hv:
        parts.append(f"人情{hv}")
    return _kv("关系簿", parts)


def _month_label(m: Any) -> str:
    """账面月 → N年M月（1年1月=1、2年1月=13）。"""
    v = _num(m)
    if v is None or v < 1:
        return ""
    return f"{(v - 1) // 12 + 1}年{(v - 1) % 12 + 1}月"


def _logs(logs: Dict[str, Any]) -> str:
    """经历：筛选/时间范围/分页信息 + 本页全部条目（月份前缀）。

    `log_filter=all` 时重要件加 `★` 前缀并在表头给图例——重要/常规是两条独立的流，
    取并集后只有 C# 侧的 `tier` 字段能区分（`tools/schemas.py` 的 log_filter 说明同步）。
    """
    flt = _s(logs.get("filter"))
    entries: List[tuple] = []
    for it in _lst(logs.get("items")):
        if not isinstance(it, dict):
            continue
        t = _s(it.get("text")).strip()
        if not t:
            continue
        entries.append((_month_label(it.get("month")), t, _s(it.get("tier"))))
    if not entries:
        total = _num(logs.get("total"))
        return "经历：无记录" if not total else "经历：本页无条目"
    mark_important = flt in ("all", "with_me")
    lines = []
    for m, t, tier in entries:
        mark = "★" if (mark_important and tier == "important") else ""
        lines.append(f"{mark}（{m}）{t}" if m else mark + t)
    # with_me = 「与我相关」（游戏 NPC 面板·经历页同款页签）：条目级筛选、跨两桶，
    # 所以和 all 一样是"重要+常规混流"，★ 图例必须照给，否则模型不知道 ★ 是什么意思。
    fmap = {"important": "重要", "regular": "常规", "all": "全部", "with_me": "与我相关"}
    flt_cn = fmap.get(flt, flt)
    page, total = _num(logs.get("page")) or 1, _num(logs.get("total"))
    # 时间范围回显（log_since_month/until_month 账面月 → N年M月）
    rng = ""
    sm, um = _month_label(logs.get("since_month")), _month_label(logs.get("until_month"))
    if sm or um:
        rng = f"｜范围 {sm or '…'}～{um or '…'}"
    head = f"经历（{flt_cn}{rng}｜第{page}页"
    if total is not None:
        head += f"，共{total}条"
    # 顺序恒为【新→旧】（C# 取数处统一反转，见 UnitSnapshot.LogsArr），故 has_more 指的是
    # "还有更早的"——只说"还有更多"会让模型以为下一页更新，方向搞反。
    head += "，还有更早的" if logs.get("has_more") else "，已到末页"
    if mark_important and any(tier for _, _, tier in entries):
        head += "；★=重要"
    return _lines(head + "）：", lines)


@renderer("inspect_unit")
def render_inspect(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    """按 data 里实际存在的块组装（C# 按 classes 采集，块存在=玩家要看的）。"""
    blocks: List[str] = []
    # 同名歧义（重名事故）必须**顶在最前面**：这份档案是按名字全图匹配来的，
    # 若命中多人，C# 只能取遍历序第一个 —— 不说的话模型会把另一个同名者的档案当成目标本人
    # （真机：想查结晶后期/声望3379 的益婉容，拿到的是登仙境/声望3129 的那个，两者都是"益婉容"）。
    # 措辞要点：① 明说"可能不是你要找的人"；② 说清本次这份是谁的（用**显示名**）；
    #          ③ 给出去查另一位的方法 —— 把 target 写成那个带括号的显示名。
    # ★ 09-17：不再打印 unit_id（模型只看名字）。区分同名者改由**唯一化显示名**承担，
    #   名字由 `tools/unit_refs.annotate` 在渲染前写进 `display`（中间层同时以它为映射键）。
    amb = data.get("ambiguous")
    if isinstance(amb, dict) and (_num(amb.get("matched")) or 0) > 1:
        cands = [c for c in _lst(amb.get("candidates")) if isinstance(c, dict)]
        me = _s(data.get("display")).strip() or _s(data.get("name")).strip()

        def _shown(c: Dict[str, Any]) -> str:
            return _s(c.get("display")).strip() or _s(c.get("name")).strip()

        others = [x for x in (_shown(c) for c in cands) if x and x != me]
        blocks.append(
            f"⚠️同名提醒：「{_s(amb.get('target'))}」全图有 {_num(amb.get('matched'))} 人同名，"
            f"以下是**「{me}」**这一位的档案，**未必是你要找的人**。"
            + ("另一位是 " + "、".join(others) + "。" if others else "")
            + "要查另一位，把 target 原样写成上面那个带括号的名字。")
    # 死者档案（10-15）：dead=true = 死亡账本回退命中的亡者。死亡事实顶在最前；
    # 请求了但死者拿不到的块（dead_unavailable）给通用语句 —— 这是渲染层唯一
    # 知道"这是亡者档案"的地方，块函数对缺键本来就是干净跳过，无需逐个改。
    if data.get("dead"):
        who = _s(data.get("display")).strip() or _s(data.get("name")).strip()
        die: List[str] = []
        dm = _month_label(data.get("died_month"))
        if dm:
            die.append(f"殁于{dm}")
        dp = _s(data.get("die_point"))
        if dp:
            die.append(f"卒于（{dp}）")
        kn = _s(data.get("killer"))
        if kn:
            die.append(f"死于{kn}之手")
        line = f"☠ {who}已身故" + ("（" + "，".join(die) + "）" if die else "") + "。"
        un = [_s(x) for x in _lst(data.get("dead_unavailable")) if _s(x)]
        if un:
            cnmap = {"stats": "属性", "abilities": "功法", "inventory": "储物"}
            line += "生前档案以下部分已不可查考：" + "、".join(cnmap.get(k, k) for k in un) + "。"
        blocks.append(line)
    for fn in (_persona,):
        t = fn(data)
        if t:
            blocks.append(t)
    t = _luck(data.get("luck"))
    if t:
        blocks.append(t)
    if isinstance(data.get("stats"), dict):
        t = _stats(data["stats"])
        if t:
            blocks.append(t)
    if isinstance(data.get("abilities"), dict):
        t = _abilities(data["abilities"])
        if t:
            blocks.append(t)
    if isinstance(data.get("inventory"), dict):
        inv = _inventory(data["inventory"])
        if inv:
            blocks.append("\n".join(inv))
    if isinstance(data.get("relationships"), dict):
        t = _relation_book(data["relationships"])
        if t:
            blocks.append(t)
    if isinstance(data.get("logs"), dict):
        t = _logs(data["logs"])
        if t:
            blocks.append(t)
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# search_units（全局找人）
# ---------------------------------------------------------------------------

@renderer("search_units")
def render_search(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    items = [it for it in _lst(data.get("items")) if isinstance(it, dict)]
    total = _num(data.get("total")) or 0
    filters = data.get("filters")
    flt = ""
    if isinstance(filters, dict):
        flt = "、".join(f"{k}={_s(v)}" for k, v in filters.items() if _s(v))
    top = _num(data.get("top")) or len(items) or 10
    head = f"找人结果（命中{total}人，按好感降序，列前{top}）"
    if flt:
        head += f"｜筛选：{flt}"
    if not items:
        return head + "：无匹配。"
    lines = []
    for it in items:
        seg = f"{_s(it.get('display')).strip() or _s(it.get('name'))}（"
        pfx = []
        # 性别：filters 支持按 sex 筛人，结果行却不带性别 —— 筛完「找女修」拿到一串名字仍不知谁是谁
        if _s(it.get("sex")):
            pfx.append(_s(it.get("sex")))
        pfx.append(f"关系{_s(it.get('relation'))}，好感{_s(it.get('intim'))}")
        seg += "，".join(pfx)
        if it.get("realm") or it.get("sect"):
            seg += f"，{_s(it.get('realm'))}/{_s(it.get('sect'))}"
        if it.get("region"):
            seg += f"，{_s(it.get('region'))}"
        lines.append(seg + "）")
    return _lines(head + "：", lines)


# ---------------------------------------------------------------------------
# query_world（events / rankings / places / sects）
# ---------------------------------------------------------------------------

@renderer("query_world")
def render_query_world(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    topic = _s(args.get("topic"))
    fn = _QUERY_WORLD.get(topic)
    return fn(args, data) if fn else None  # type: ignore[return-value]


def _qw_events(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    events = [e for e in _lst(data.get("events")) if isinstance(e, dict)]
    if not events:
        return "近期天下大事：无记录。"
    lines = []
    for e in events:
        t, m = _s(e.get("text")).strip(), _s(e.get("month")).strip()
        if not t:
            continue
        lines.append(f"（{m}月）{t}" if m else t)
    cnt = _num(data.get("count"))
    return _lines(f"近期天下大事（{cnt if cnt is not None else len(lines)}条，时间倒序）：", lines)


def _qw_rankings(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    board = _s(data.get("board") or args.get("board"))
    b = {"power": "战力", "reputation": "声望", "beauty": "魅力", "money": "灵石"}.get(board, board or "榜单")
    items = [it for it in _lst(data.get("items")) if isinstance(it, dict)]
    total = _num(data.get("total"))
    if not items:
        return f"{b}榜：暂无数据（榜内共{total if total is not None else 0}人）。"
    lines = []
    for i, it in enumerate(items):
        extra = [x for x in (_s(it.get("realm")), _s(it.get("sect"))) if x]
        lines.append(f"{i + 1}. {_s(it.get('name'))} {_s(it.get('score'))}"
                     + (f"（{'/'.join(extra)}）" if extra else ""))
    head = f"{b}榜（列出{len(items)}人" + (f"，榜内共{total}人" if total is not None else "") + "）："
    return _lines(head, lines)


def _qw_places(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    """可去地点：按类别分组 + 类别计数，带坐标。region/cat/name 由 C# 已筛，这里只回显筛选条件。"""
    args = args or {}
    region = _s(args.get("region"))
    cat = _s(args.get("cat"))
    name = _s(args.get("name"))
    flt = "、".join(x for x in (f"名含={name}" if name else "",
                               f"分类={cat}" if cat and cat != "全部" else "",
                               f"州={region}" if region else "") if x)
    places = [p for p in _lst(data.get("places")) if isinstance(p, dict)]
    total = _num(data.get("total")) or len(places)
    if not places:
        if name:
            note = _s(data.get("note"))
            return note or f"未找到名字含「{name}」的地点。"
        why = "该筛选条件下" if flt else "当前"
        return f"可去地点：无（{why}没有匹配的地点）。"
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in places:
        groups.setdefault(_s(p.get("cat")) or "其他", []).append(p)
    order = [c for c in ("城镇", "宗门", "突破材料", "器灵材料") if c in groups] + [c for c in groups if c not in ("城镇", "宗门", "突破材料", "器灵材料")]
    counts = "、".join(f"{c}{len(groups[c])}" for c in order)
    lines = []
    for c in order:
        for p in groups[c]:
            pt = p.get("point")
            pos = f"({_s(pt.get('x'))},{_s(pt.get('y'))})" if isinstance(pt, dict) and pt.get("x") is not None else ""
            lines.append(f"[{c}] {_s(p.get('region'))}·{_s(p.get('name'))}{pos}")
    head = (f"{flt}可去地点共{total}处" if flt else f"可去地点共{total}处") + f"（{counts}）"
    return _lines(f"{head}：", lines)


def _qw_sects(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    """宗门：名号主/支/源后三项/层级/存亡被占/宗主弟子声望敌对/宗旨/气运/坐标。
    已按用户裁剪：不再输出 名词组1/2、类型+立场值、id。可按 region 过滤或按 name 精准查
    单一宗门族系（C# 已筛，这里只反映数量与措辞）。"""
    args = args or {}
    region = _s(args.get("region"))
    name = _s(args.get("name"))
    sects = [s for s in _lst(data.get("sects")) if isinstance(s, dict)]
    if not sects:
        if name:
            note = _s(data.get("note"))
            return note or f"「{name}」宗门查询：未找到同名宗门（天下未闻此名，或已灭除籍）。"
        if region:
            return f"{region}宗门概览：该地区未取到宗门（可能已灭或本来就无名）。"
        return "宗门概览：未取到任何宗门（天下未闻有宗门传世）。"
    lines = []
    for s in sects:
        seg = f"{_s(s.get('region'))}·{_s(s.get('name'))}"
        bits = []
        if s.get("main_name"):
            bits.append(f"主名{_s(s.get('main_name'))}")
        if s.get("branch_name"):
            bits.append(f"支名{_s(s.get('branch_name'))}")
        if s.get("name_origin"):
            bits.append(f"源名{_s(s.get('name_origin'))}")
        if bits:
            seg += "｜名号：" + "，".join(bits)
        if s.get("is_top"):
            subs = _names(s.get("sub_schools"))
            seg += "｜层级：主宗" + (f"，下辖{len(subs)}宗（{'、'.join(subs)}）" if subs else "，无下辖")
        elif s.get("top_school"):
            seg += f"｜层级：分宗，隶属{_s(s.get('top_school'))}"
        else:
            seg += "｜层级：未定（无主宗信息）"
        if s.get("is_hold"):
            seg += "｜状况：" + (f"被占据（持有方{_s(s.get('hold_by'))}）" if s.get("hold_by") else "被占据（持有方未知）")
        else:
            seg += "｜状况：未被占据"
        facts = [f"宗主{_s(s.get('leader'))}" if s.get("leader") else "宗主未知"]
        if s.get("member_count") is not None:
            facts.append(f"弟子{_s(s.get('member_count'))}人")
        if s.get("reputation") is not None:
            facts.append(f"声望{_s(s.get('reputation'))}")
        if s.get("enemy"):
            facts.append(f"敌对{_s(s.get('enemy'))}")
        seg += "｜" + "，".join(facts)
        sl = []
        for x in _lst(s.get("slogans")):
            if isinstance(x, dict):
                sg, ds = _s(x.get("slogan")).strip(), _s(x.get("desc")).strip()
                if sg:
                    sl.append(f"{sg}（{ds}）" if ds and ds != sg else sg)
        if sl:
            seg += "｜宗旨：" + "；".join(sl)
        if s.get("fate"):
            seg += f"｜气运：{_s(s.get('fate'))}"
        pt = s.get("point")
        if isinstance(pt, dict) and pt.get("x") is not None:
            seg += f"｜坐标({_s(pt.get('x'))},{_s(pt.get('y'))})"
        lines.append("· " + seg)
        lines.extend(_sect_detail_lines(s))

    scope = _s(data.get("scope"))
    if name:
        if scope == "state":
            head = f"「{name}」{region}内的宗门（{len(sects)}处）"
        elif scope == "self":
            head = f"「{name}」宗门（1处）"
        else:
            head = f"「{name}」宗门族系（主宗+分舵共{len(sects)}处）"
    else:
        head = (f"{region}宗门概览" if region else "宗门概览") + f"（共{len(sects)}个）"
    note = _s(data.get("note")).strip()
    if note:
        head += f"｜{note}"
    return _lines(head, lines)


def _person(p: Any) -> str:
    """人事树里的一个人：名字（境界，已故/未在世，代理谁）。C# 只给有值的字段。"""
    if not isinstance(p, dict):
        return ""
    name = _s(p.get("name")).strip() or "未名"
    parts = []
    rl = _s(p.get("realm")).strip()
    if rl:
        parts.append(rl)
    st = _s(p.get("status")).strip()
    if st:
        parts.append(st)
    dm = _s(p.get("dead_month")).strip()
    if dm:
        parts.append(dm)
    ag = _s(p.get("agent")).strip()
    if ag:
        parts.append("代理" + ag)
    return name + ("（" + "，".join(parts) + "）" if parts else "")


def _sect_people_lines(people: Any) -> List[str]:
    """detail=people 的人事树：宗主 → 大长老（辖哪几堂）→ 各堂长老 → 各自真传。
    降级时 C# 给的是扁平 elders_flat/inherit_flat（无部门与师徒归属）。"""
    if not isinstance(people, dict) or not people:
        return []
    out: List[str] = []
    main = people.get("main")
    if isinstance(main, dict) and main:
        out.append("  宗主：" + _person(main))
    be = [x for x in _lst(people.get("big_elders")) if isinstance(x, dict)]
    if be:
        out.append(f"  大长老（{len(be)}）：")
        for p in be:
            ds = [_s(x) for x in _lst(p.get("depts")) if _s(x)]
            out.append("    · " + _person(p) + ("｜辖" + "、".join(ds) if ds else ""))
    els = [x for x in _lst(people.get("elders")) if isinstance(x, dict)]
    if els:
        out.append(f"  长老（{len(els)}）：")
        for p in els:
            dept = _s(p.get("dept")).strip()
            if p.get("vacant"):
                out.append(f"    · {dept}：空缺" if dept else "    · 空缺")
                continue
            tail = []
            ihn = [x for x in _lst(p.get("inherit")) if isinstance(x, dict)]
            if ihn:
                tail.append("真传：" + "、".join(_person(x) for x in ihn))
            if p.get("inner_count") is not None:
                tail.append(f"内门{_s(p.get('inner_count'))}人")
            if p.get("outer_count") is not None:
                tail.append(f"外门{_s(p.get('outer_count'))}人")
            seg = (f"    · {dept} " if dept else "    · ") + _person(p)
            if tail:
                seg += "｜" + "；".join(tail)
            out.append(seg)
    for key, label in (("elders_flat", "长老"), ("inherit_flat", "真传弟子")):
        arr = [x for x in _lst(people.get(key)) if isinstance(x, dict)]
        if arr:
            out.append(f"  {label}（{len(arr)}）：" + "、".join(_person(x) for x in arr))
    return out


def _sect_detail_lines(s: Dict[str, Any]) -> List[str]:
    """detail=info 的追加子行。每段独立：C# 没给这个字段就不出这一行（字段缺失≠否定）。"""
    out: List[str] = []

    bt = [_s(x) for x in _lst(s.get("bas_types")) if _s(x)]
    if bt:
        out.append("  功法偏好：" + "、".join(bt))

    mem = s.get("member") if isinstance(s.get("member"), dict) else {}
    cap = s.get("capacity") if isinstance(s.get("capacity"), dict) else {}
    parts = []
    for key, label in (("big_elders", "大长老"), ("elders", "长老"), ("inherit", "真传"),
                       ("inner", "内门"), ("outer", "外门")):
        n = mem.get(key)
        if n is None:
            continue
        c = cap.get(key)
        parts.append(f"{label}{_s(n)}/{_s(c)}" if c is not None else f"{label}{_s(n)}")
    seg = "、".join(parts)
    if mem.get("total") is not None:
        seg = (seg + "，" if seg else "") + f"共{_s(mem.get('total'))}人"
    if seg:
        out.append("  人数：" + seg)

    eco = []
    if s.get("money") is not None:
        eco.append(f"灵石{_s(s.get('money'))}")
    if s.get("faith_money") is not None:
        eco.append(f"信仰{_s(s.get('faith_money'))}")
    if s.get("power") is not None:
        eco.append(f"战力{_s(s.get('power'))}")
    if eco:
        out.append("  经济：" + "、".join(eco))

    honor = s.get("honor") if isinstance(s.get("honor"), dict) else {}
    if honor.get("score") is not None or honor.get("rank") is not None:
        seg = []
        if honor.get("score") is not None:
            seg.append(f"{_s(honor.get('score'))}分")
        if honor.get("rank") is not None:
            seg.append(f"第{_s(honor.get('rank'))}名")
        out.append("  战绩：宗门大比 " + "，".join(seg))

    intim = [x for x in _lst(s.get("intim")) if isinstance(x, dict)]
    if intim:
        out.append("  交情：" + "、".join(f"{_s(x.get('name'))}{_s(x.get('value'))}" for x in intim))

    out.extend(_sect_people_lines(s.get("people")))

    pn = _s(s.get("people_note")).strip()
    if pn:
        out.append("  人事提示：" + pn)
    return out


def _qw_regions(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    """topic=region：当前世界实际存在的地区（州）名列表（供 query_world region 过滤/destination region 消歧用）。"""
    regions = [r for r in _lst(data.get("regions")) if r]
    if not regions:
        return "地区（州）列表：暂未取到任何地区信息。"
    return _lines(f"当前世界地区（州）共{len(regions)}个：", [f"· {_s(r)}" for r in regions])


_QUERY_WORLD = {
    "events": _qw_events,
    "rankings": _qw_rankings,
    "places": _qw_places,
    "sects": _qw_sects,
    "region": _qw_regions,
}


# ---------------------------------------------------------------------------
# social_relation（关系管理）
# ---------------------------------------------------------------------------

_SOCIAL_OP_CN = {
    "jie_yuan": "道侣", "jie_chu_jie_yuan": "道侣", "marry": "夫妻", "divorce": "夫妻",
    "bai_shi": "师徒", "shou_tu": "师徒", "jie_chu_bai_shi": "师徒", "jie_chu_shou_tu": "师徒",
    "jie_yi": "结义", "jie_chu_jie_yi": "结义", "ren_yi_fu_mu": "义父母",
}
# 提案文案的动词短语（"提出…之意"）：名词版（道侣/师徒）塞进这个句式会读成"提出道侣之意"，
# 对「结义」更是直接出错（"结为结义"）。与 `room_flow.REL_ASK_CN` 是同一套说法，改一处记得改另一处。
_SOCIAL_ASK_CN = {
    "jie_yuan": "结为道侣", "marry": "结为夫妻", "bai_shi": "拜师",
    "shou_tu": "收徒", "ren_yi_fu_mu": "认义亲", "jie_yi": "结义",
}
_BREAK_OPS = {"jie_chu_jie_yuan", "divorce", "jie_chu_bai_shi", "jie_chu_shou_tu", "jie_chu_jie_yi"}

# 引擎关系裁决码（`UnitActionRelationSet.IsCreate(bool)` 的返回码）——**0 = 同意；非 0 = 拒绝**。
# 权威版本（含反汇编 RVA 与逐条判据）在 C# `csharp/ToolExecutor.cs` 关系段顶部的注释块里；
# 这张表只负责**给人看的措辞**，两边必须同步改。
_RELATION_ENGINE_CODE = {
    0: "引擎同意",
    2: "目标是自己",
    3: "两人之间已经有一条关系（引擎里一对人只记一条，要结新的必须先解除旧的）",
    4: "认义父母另有前置未满足",
    5: "同性不能结为夫妻/道侣",
    6: "异性不能结义",
    7: "对方或自己已有配偶",
    8: "结婚前必须先结为道侣",
    9: "解除路径的提示码",
}


def _relation_code_cn(code: Any) -> str:
    n = _num(code)
    if n is None:
        return "未知"
    return _RELATION_ENGINE_CODE.get(n, f"未知码 {n}")


def _social_refuse_facts(target: str, cp: str, data: Dict[str, Any]) -> List[str]:
    """缔结失败的**现状事实**（原有关系 / 关系有没有被改 / 双方境界 / 师徒位占用）。

    只罗列 C# 给的读数，不替引擎下结论；取不到的项不出现（`-1` = 读不到，也不算数）。
    """
    who = cp or "你"
    out: List[str] = []
    before = _s(data.get("relation_before"))
    if before and before not in ("陌生", "无", "无关系"):
        out.append(f"{who}与{target}的关系簿里已是{before}")
    after = _s(data.get("relation_after"))
    if after and after != before:
        out.append(f"关系簿现为{after}")
    d = data.get("diag") if isinstance(data.get("diag"), dict) else {}
    nr, pr = _s(d.get("npc_realm")), _s(d.get("player_realm"))
    if nr or pr:
        out.append(f"双方境界：{target}{nr or '（未知）'}／{who}{pr or '（未知）'}")
    pm, nm = _num(d.get("player_masters")), _num(d.get("npc_masters"))
    ns, ps = _num(d.get("npc_students")), _num(d.get("player_students"))
    if pm is not None and pm >= 1:
        out.append(f"{who}已有{pm}位师傅" if pm > 1 else f"{who}已有师傅")
    if nm is not None and nm >= 1:
        out.append(f"{target}已有师傅")
    if ns is not None and ns >= 3:
        out.append(f"{target}门下徒弟已满（{ns}）")
    if ps is not None and ps >= 3:
        out.append(f"{who}门下徒弟已满（{ps}）")
    return out


def _social_refuse(target: str, cp: str, relation: str, data: Dict[str, Any]) -> str:
    """缔结类失败的**如实**回执（09-17 "收徒静默失败"事故；同日二次修正"关系被强拆"）。

    引擎码语义（GameAssembly.dll 反汇编实锤，表见 `_RELATION_ENGINE_CODE` 与 C# 同名注释块）：
    **0 = 同意（CreateAction 才会走 OnCreate）；非 0 = 拒绝原因码**。09-17 第一版文案按
    "≥1 = 已执行、0 = 拒绝"写反了 —— 那套读法把"引擎拒绝（码 3：两人已有夫妻关系）"读成
    "可以建"，还让预检实例的 `isCreateCall` 免检标记带进落刀，硬拆了玩家已有的夫妻关系。

      · `precheck` 非 0（且非 -2 = 没问出来）⇒ **预检就被拒**：`create_ret = -1`，未落刀；
      · `create_ret` 非 0 ⇒ 落刀被引擎驳回，`OnCreate` 根本没跑，关系簿未改动；
      · `create_ret == 0` 却读不回 ⇒ 引擎说执行了、我们看不到，同样不写成成功；
      · 缺 `create_ret`/`precheck`（旧 C# 载荷）⇒ 保留旧文案，两侧升级不同步时不回归。
    """
    ret = _num(data.get("create_ret"))
    pre = _num(data.get("precheck"))
    who = f"{target}与{cp}" if cp else f"你与{target}"
    if ret is None and pre is None:
        return f"{who}结为{relation}，但状态校验未通过（可能未生效，需核对）。"
    if pre is not None and pre not in (0, -2):
        seg = (f"{who}的「{relation}」未能结下：游戏引擎在动手之前就判定这个动作建不起来"
               f"（引擎码 {pre}：{_relation_code_cn(pre)}），未落刀，关系簿未改动")
        facts = _social_refuse_facts(target, cp, data)
        if facts:
            seg += "；" + "；".join(facts)
        return seg + "。以游戏弹出的提示为准，请如实告诉玩家未能结下。"
    if ret is not None and ret != 0:
        seg = (f"{who}的「{relation}」未能结下：游戏引擎驳回了这次关系变更"
               f"（引擎码 {ret}：{_relation_code_cn(ret)}），关系簿未改动")
        facts = _social_refuse_facts(target, cp, data)
        if facts:
            seg += "；" + "；".join(facts)
        return seg + "。以游戏弹出的提示为准，请如实告诉玩家未能结下。"
    return f"{who}结为{relation}，但引擎已执行（引擎码 0）却读不回该关系，需人工核对。"


def _intim_delta(who: str, whom: str, d, after=None) -> str:
    """「{who}对{whom}的好感提升/降低N点（现为M）」——工具结果里好感变化的统一句式。

    Δ 缺失（原生路径没这字段 / 读不到起点被 C# `PutIntim` 滤掉）返回空串，调用方据此**不追加**
    —— 这正是"原生的不显示、我们落刀的才显示"的实现方式：字段有无即路径有无。
    0 显示「未变化」（真实发生过 0 实得——如档位系数把请求吃光；与"缺失=未知"不同）。

    ★ 11-13 补 `after`（回读后的**真实好感**，C# `PutIntimWithAfter` 写 `{key}_after`）★
    用户原话："工具返回结果注意返回回读之后的真实好感度就行"。
    只给 Δ 的话模型知道"这一步动了多少"，却不知道"现在总共多少" ——
    而它下一步（还要不要继续加、这句台词该多热络/多冷）恰恰取决于当前落在哪一档。
    `after` 缺失（旧 DLL / 读不到）就只报 Δ，**绝不编一个数字**。
    """
    v = _num(d)
    if v is None:
        return ""
    tail = ""
    a = _num(after)
    if a is not None:
        tail = f"（现为{a}）"
    if v > 0:
        return f"{who}对{whom}的好感提升{v}点{tail}"
    if v < 0:
        return f"{who}对{whom}的好感降低{-v}点{tail}"
    return f"{who}对{whom}的好感未变化{tail}"


def _intim_decay_note(req: int | None, actual: int) -> str:
    """实得 ≠ 请求时补一句"为什么"——**引擎按当前好感档位的百分系数换算**（10-15 定论）。

    `RoleClose.json`：0~60 全额 / 60~120 五折 / 120~180 25% / 180~240 12.5% / 240~300 6.25%
    （负侧同构；减好感同样衰减）。写这句是为了让模型别把"加 30 只涨 8"当成工具坏了去重试，
    也让它在台词里能自圆其说（"情分已厚，再添一分都难"）。
    """
    if req is None or req == 0 or actual == req:
        return ""
    if actual == 0:
        return "；实得 0 —— 当前好感档位的系数把它吃光了（好感越高每次加得越少，非工具出错）"
    pct = round(100.0 * abs(actual) / abs(req))
    return (f"；实得与请求不符是**引擎按当前好感的百分系数换算**的结果（本次约 {pct}%；"
            f"档位：0~60 全额 / 60~120 五折 / 120~180 25% / 180~240 12.5% / 240~300 6.25%），不是失败")


@renderer("social_relation")
def render_social(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    op = _s(data.get("op") or args.get("op"))
    target = _s(data.get("target"))
    cp = _s(data.get("counterparty"))          # 非空 = 对手方不是玩家（NPC↔NPC，免窗直写）
    if op == "chuan_hua":
        # 捎话（11-12）：话已投进对方信箱，**当次没有任何回应**。
        # 措辞必须把这两件事都说清：① 送到了；② 他此刻不知道 —— 否则模型会当场写出
        # "他听了点头应下"这种**对方根本没有过**的反应（对方账本里连这一步都还没发生）。
        to = cp or target
        return (f"你托人把话捎给了{to}，已经放进他的信箱；"
                f"他此刻并不知情，要等他下回与玩家说话时才会读到。")
    if data.get("proposal"):
        # 提案（09-17）：只提出、未落刀 —— 该样本由 Python 群聊闸门合成（C# 永不发）。
        # 不放这里的话会落进下面的"结为…"分支，把还没发生的事说成既成事实。
        return f"你已向{cp or '对方'}提出{_SOCIAL_ASK_CN.get(op, '')}之意，等对方回应（对方应下才算数）。"
    if op in ("add_intim", "reduce_intim"):
        actual, req = _num(data.get("actual_delta")), _num(data.get("requested"))
        before, after = _num(data.get("intim_before")), _num(data.get("current_intim"))
        if actual is None:
            return None  # type: ignore[return-value]
        verb = (f"好感提升了{actual}点" if actual > 0 else
                f"好感降低了{-actual}点" if actual < 0 else "好感未变化")
        det = []
        if req is not None:
            det.append(f"请求{req}点")
        if before is not None and after is not None:
            det.append(f"{before}→{after}")
        to = cp if cp else "你"
        seg = f"{target}对{to}的{verb}" + ("（" + "，".join(det) + "）" if det else "")
        return seg + _intim_decay_note(req, actual)
    relation = _s(data.get("relation")) or _SOCIAL_OP_CN.get(op, "")
    if op in _BREAK_OPS:
        if cp:
            seg = (f"{target}与{cp}解除了{relation}关系" if relation
                   else f"{target}与{cp}解除了关系（op={op}）")
        else:
            seg = (f"你同意了与{target}解除{relation}关系" if relation
                   else f"你同意了与{target}解除关系（op={op}）")
        if _num(data.get("create_ret")) not in (None, 0):
            # 原生解除动作没跑起来（引擎码非 0）→ 已经走字段直写兜底。这是"为什么走直写"，不是失败。
            seg += f"（原生解除动作没跑起来：引擎码 {_num(data.get('create_ret'))}，本次走字段直写）"
        if data.get("readback_still_related") is True:
            seg += "；但读回显示关系仍存在，需人工核对"
        elif data.get("readback_still_related") is False:
            seg += "；读回确认关系已解除"
        if data.get("bw_err"):
            seg += f"；原生 BreakWith 异常：{_s(data.get('bw_err'))}"
        return seg
    if relation:
        if data.get("verified") is False:
            return _social_refuse(target, cp, relation, data)
        head = f"{target}与{cp}结为{relation}" if cp else f"你与{target}结为{relation}"
        if "verified" not in data:
            return f"{head}（C# 未返回状态校验）。"   # 直写兜底路径
        # 引擎的 OnCreate 是"先 ClearRelation 再写"的**覆盖**语义：能落刀 = 旧关系已经被删。
        # 目前只有结婚（道侣→夫妻）会正常走到这里；其他 op 在预检就被码 3 拦下了 ——
        # 真出现别的替换，必须让模型看见（09-17 收徒强拆夫妻就是没让它看见）。
        note = ""
        before, after = _s(data.get("relation_before")), _s(data.get("relation_after"))
        if before and before not in ("陌生", "无", "无关系") and after and after != before:
            note = f"（引擎把原有的「{before}」关系换成了「{after}」—— 引擎里一对人只记一条关系）"
        return f"{head}。{note}"
    if op:
        return f"{target}的关系操作已执行（op={op}）。"   # 兜底：不退回 JSON
    return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# movement（召唤/传送/赶路）
# ---------------------------------------------------------------------------

@renderer("movement")
def render_movement(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    op = _s(data.get("op") or args.get("op"))
    target = _s(data.get("target"))
    cp = _s(data.get("counterparty"))          # 非空 = 位移对象不是玩家
    if op == "summon":
        if cp:
            if data.get("moved"):
                return f"{cp}已被{target}召到身边（{cp}移动到{target}所在处）。"
            return f"{target}与{cp}已在同一处，无需召唤。"
        if data.get("moved"):
            return f"你已被召唤到{target}身边（你移动到{target}所在处）。"
        return f"你与{target}已在同一处，无需召唤。"
    if op == "teleport":
        if cp:
            if data.get("moved"):
                return f"{target}已动身前往{cp}身边（{target}移动到{cp}那处）。"
            return f"{target}与{cp}已在同一处，无需传送。"
        if data.get("moved"):
            return f"{target}已传送到你身边（{target}移动到你这处）。"
        return f"你与{target}已在同一处，无需传送。"
    if op == "travel":
        dest, region, cat = _s(data.get("destination")), _s(data.get("region")), _s(data.get("cat"))
        where = dest + (f"（{region}）" if region else "") + (f"[{cat}]" if cat else "")
        if data.get("already_there"):
            return f"{target}此刻就在{where}。"
        seg = f"{target}已动身前往{where}，并在此地等候。"
        pt = data.get("point")
        if isinstance(pt, dict) and pt.get("x") is not None:
            seg += f"（目的地坐标{_s(pt.get('x'))},{_s(pt.get('y'))}）"
        return seg
    return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# world_ai_action（战斗/论道/双修/邀约/传功）
# ---------------------------------------------------------------------------

# ★「屏幕括注」的视角（09-24 真机，用户当场指出）★ —— `（…）` 是**游戏写在玩家屏幕上**的
# 叙述（不是任何人说的话），它跟在某句台词后面**一起**被采集（C# 的 `SegmentLine.Text` =
# `GetDialogueText` 成品整串）。屏幕视角恒为玩家：括注里的「你」指玩家；而**台词里的「你」
# 是相对说话人的**（NPC 说「你」=玩家、玩家说「你」=NPC），一个字都不能动。
#
# 为什么要摘出来（实测后果）：attack 战败逃跑那页的整串是
# 「别跑！…下次别让我遇见你！！！（对方战败后，趁你放松的时刻拔腿就跑，你连忙追上去却不见了踪影）」
# 渲染成「唐炎说：…」后模型把括注里的「你」接到了**自己**身上 ⇒ 读成"对方（玩家）战败逃走、
# 我没追上"——**胜负正好读反**（会话记录里它的推理就是 "NPC 获胜、唐炎 fled"，
# 回复成"我管星贤的拳，你躲不过第二次"）。
#
# 规则（只看结构，不做措辞匹配 —— 战后台词出自语句池、措辞随人设关系变，同 `_battle_said`
# 的告诫）：括注**含「你」**→ 摘出来单独成一行、前缀「叙述：」，并把括注内的「你」换成
# 玩家真名；不含「你」的括注（"（冷笑）"这类）原样留在台词里 —— 没有指代冲突，不动阅读体验。
# 「你们」避让：那是对着人说的话，归属不定，不许替换成"唐炎们"。
_NOTE_RE = re.compile(r"（([^（）]*)）")


def _split_note(text: str, player: str) -> Tuple[str, List[str]]:
    """屏幕成品句 → (台词本体, [叙述行…])。视角与边界见 `_NOTE_RE` 上方说明。"""
    notes: List[str] = []

    def _take(m: Any) -> str:
        body = m.group(1)
        if "你" not in body:
            return m.group(0)              # 无「你」= 无指代冲突，原样留在台词里
        body = re.sub(r"你(?!们)", player, body)
        notes.append(f"（叙述：{body}）")
        return ""

    return _NOTE_RE.sub(_take, text).strip(), notes


def _emit_said(out: List[str], who: str, text: str, player: str) -> None:
    """「X说：…」+ 紧随其后的「叙述：…」行（说话人换算 + 括注摘取，见 `_NOTE_RE`）。

    三处渲染共用（战斗原话 / 单流 / 旧形状兜底）—— 分开写迟早漏一处，而漏掉的那一处
    正是模型读反胜负的入口。
    """
    speaker = "你" if who == "npc" else (player if who == "player" else "有人")
    body, notes = _split_note(text, player)
    if body:
        out.append(f"{speaker}说：{body}")
    out.extend(notes)


def _battle_said(data: Dict[str, Any]) -> List[str]:
    """把 C# 带回来的链上原话渲染成「谁说：原话」列表（**台词一字不改**；
    屏幕括注按 `_NOTE_RE` 摘成独立「叙述：」行 —— 那里的视角说明必须读）。

    ★ 视角（09-22 扳正）：读这段的是**那个 NPC**（`Agent.id` = 它，L1 也写着"你是某某"）
    ⇒ `who=="npc"` 写"你"、`who=="player"` 写**玩家的名字**。旧实现正好写反：玩家那句
    「厉害，我输了。」被渲染成「你说：…」，于是**赢的那个 NPC 以为是自己认的输**。
    （`world_ai_action` 的其余分支——邀约/论道/传功/双修——仍是玩家视角的旧措辞，
    同一类病，等一次统一的视角梳理再改，不在战斗这条链里顺手混着改。）

    `who` 三态由 C# 的说话人判定给出（player / npc / 空=判不出）。判不出的那句写成
    「有人」而不是猜一个 —— 归属猜反，模型就会把"我输了"安到赢的人头上。

    ★**不要在这里做关键词判胜负**★：游戏那几句战后台词出自 `RoleLogLocal` 的**语句池**
    （带 condition/group 与 `{call|B|A}` 占位，措辞随人设、关系、性别称谓变）——
    实测同一场切磋的两句话是「厉害，我输了。」/「看来是我赢了！」，而紧邻的同池句还有
    「侥幸罢了！…你也很厉害！」、「我胜了！…你服是不服？！」。
    拿"我输了/我赢了"去匹配，迟早撞上既不认输也不直说的那一句。
    原话 + 归属交给模型读，它判得出来，而且判错了也查得到依据在哪。
    """
    lines = data.get("lines")
    out: List[str] = []
    if not isinstance(lines, list):
        return out
    player = _s(data.get("player")) or "对方"
    for it in lines:
        if not isinstance(it, dict):
            continue
        # ★09-23：单流里 `kind=option` 是**玩家选择**，不是"链上原话"★ —— 只用来判"打没打过"
        #   的 `spoken` 不能把选择算进去（选择只说明玩家答了话，不代表动过手）。
        if _s(it.get("kind")) == "option":
            continue
        text = _s(it.get("text"))
        if not text:
            continue
        # 说话人换算（npc→"你" / player→名字 / 判不出→"有人"）+ 屏幕括注摘取，
        # 都在 `_emit_said` 里（三处共用，别各写一份）
        _emit_said(out, _s(it.get("who")), text, player)
    return out


def _speak_ordered(data: Dict[str, Any]) -> List[str]:
    """链上内容 → 一条**按时序**的对话（原文 + 玩家选择）。

    ★视角与 `_battle_said` 同一条：读这段的是那个 NPC —— `who=="npc"` 写"你"、
    `who=="player"` 写玩家名（判不出写"有人"）；玩家的选择写成「X选择了：「…」」。

    ★09-23 起 C# 侧是**单流**★：`lines` 本身就是"追加序 = 时序"的一条流（每行带 `kind`：
    `page` / `option`）⇒ 这里**只按顺序翻译**。旧实现要拿 `choices[].after_drama_id` 去跟
    台词行的 `drama_id` 事后交错 —— 那正是切磋结果顺序出错的根源（战前点的「接受切磋」被排到
    战后台词之后）。旧的"两个数组"形状保留兜底（C# 升级不同步时仍能看）。

    ★屏幕括注（`（…）`）★：与说话人换算一起走 `_emit_said` —— 括注里的「你」指玩家
    （屏幕视角），摘成独立「叙述：」行；台词里的「你」不动。见 `_NOTE_RE` 的说明。
    """
    lines = data.get("lines")
    lines = lines if isinstance(lines, list) else []
    player = _s(data.get("player")) or "对方"
    picked_who = _s(data.get("player")) or "玩家"

    # ---- 新形状：单流（每行带 kind）----
    if any(isinstance(ln, dict) and "kind" in ln for ln in lines):
        out_stream: List[str] = []
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            text = _s(ln.get("text"))
            if not text:
                continue
            if _s(ln.get("kind")) == "option":
                out_stream.append(f"{picked_who}选择了：「{text}」")
                continue
            _emit_said(out_stream, _s(ln.get("who")), text, player)
        return out_stream

    # ---- 旧形状兜底：`lines` + `choices` 按 after_drama_id 交错 ----
    choices = data.get("choices")
    choices = choices if isinstance(choices, list) else []
    done = [False] * len(choices)
    out: List[str] = []

    def _say(it: Any) -> List[str]:
        text = _s(it.get("text"))
        if not text:
            return []
        rows: List[str] = []
        _emit_said(rows, _s(it.get("who")), text, player)
        return rows

    def _pick(it: Any) -> str:
        text = _s(it.get("text"))
        return f"{picked_who}选择了：「{text}」" if text else ""

    for ln in lines:
        if not isinstance(ln, dict):
            continue
        out.extend(_say(ln))
        did = ln.get("drama_id")
        # 这一页若有玩家应答，紧跟在本页台词之后（`after_drama_id` = 它答的那页）
        for i, ch in enumerate(choices):
            if done[i] or not isinstance(ch, dict):
                continue
            if did is not None and ch.get("after_drama_id") == did:
                s2 = _pick(ch)
                if s2:
                    out.append(s2)
                done[i] = True
    for i, ch in enumerate(choices):
        if done[i] or not isinstance(ch, dict):
            continue
        s2 = _pick(ch)
        if s2:
            out.append(s2)
        done[i] = True
    return out


def _render_battle_result(data: Dict[str, Any]) -> str:
    """战斗类动作（spar / attack）跨过整场战斗之后的结局（`BattleOutcome` 收的口）。

    四态分开写，是因为"没看到结局"有三种完全不同的成因，模型得知道是哪一种才能说对话：
      · 抓到链上台词 → 原话 + 归属，胜负由它读；
      · 战斗打完了但一句词都没抓到 → 明说没拿到话；
      · 进了战斗却没等到结束 → 明说结果没回来，且**不许推断成"没打"**；
      · 连战斗都没开始（应战后战斗没起来 / 中途退出世界）→ 同上口径。
    每一态都带一句"别自行推断"，这是本工具结果唯一的兜底：宁可让 NPC 说"方才那阵我没看清"，
    也不要它编一个不存在的胜负 —— 玩家会立刻看出来。

    attack 多一态：C# 的 `verdict_page=true` = 链收在**处置页**（NPC 战败求饶，生死握在玩家
    手里、游戏还没结算）⇒ 必须明写"生死未定"，否则模型会替玩家做一个没发生的决定。
    """
    # ★对手名在两个分支里住的地方不同★（10-27 复核发现的真坑）
    #   · 打玩家 / spar：对手**就是玩家** ⇒ `data["player"]`（= `PlayerName()`）正好是它。
    #     老代码一直这么用，是对的 —— 别改。
    #   · `together`：对手是另一个 NPC（B）⇒ `data["player"]` 是玩家自己，
    #     真正的对手名在 `data["target"]`（`JoinPlayerAttack` 传给
    #     `BattleOutcome.Arm(slot, foeName, …)` 的那个）。用错了就会打出
    #     "玩家带着你和队友向唐炎动了手"，而唐炎是玩家。
    #   （注：老攻击分支的 `Arm(…, target, …)` 传的其实是**发起方 NPC 自己的名字**，
    #     所以那里的 `data["target"]` 也不能当对手名 —— 只有 together 这一支能。）
    together = _s(data.get("mode")) == "together"
    player = (_s(data.get("target")) if together else "") or _s(data.get("player")) or "对方"
    spoken = _battle_said(data)          # 链上原话（只在判"打没打过"时算证据）
    # ★台词与选择按页号交错★（09-23 用户当场指出顺序错）：玩家**战前**点的「接受切磋」必须
    # 紧跟发问那一页、而不是被排到战后台词后面。判"打过没有"只看战斗信号与**原话**，
    # 不看选择（选择只说明玩家答了话，不代表动过手）。
    said = _speak_ordered(data)
    # ★"打起来了"只认战斗信号，不认"有没有台词"★（10-28 真机）
    #   逃跑判定这场：引擎弹了 `UnitActionFeedback101`（"被他人发起战斗时的反馈"），
    #   玩家点「确定」后链上确实有台词（"哼！逃得倒是挺快！"），但**没有进过战斗**。
    #   旧判据把 `spoken` 也算成"打过"，于是报成"两边打起来"——与事实相反。
    #   台词只用于"下面要不要贴原话"，不再参与"打没打"的判断。
    fought = bool(data.get("battle_seen") or data.get("battle_finished"))
    if _s(data.get("op")) == "attack":
        # ★`together` 里"谁动手、谁被处置"与 solo/打玩家**相反**（10-27）★
        #   solo     = 你和 B 单挑、玩家旁观；处置页 20403 出现 = **你**战败被处置；
        #   打玩家   = 你出刀、玩家应战；处置页 = **玩家**战败，玩家处置你；
        #   together = **玩家当发起方**打 B；同一个处置页 = **B 战败**，
        #              握着生杀大权的是玩家，不是你。
        #   三者写成同一句会把处境讲反，模型于是替玩家做决定
        #   （本仓最忌的"自信地说反话"）。`player` 已在上面按分支取好对手名。
        if together:
            # "你"= 工具调用者 A（跟着玩家一起上的那个），玩家用真名。
            # 对手名 = `player`（见本函数顶部那段取值说明）。
            who = _s(data.get("player")) or "玩家"
            head = (f"你和{who}一起向{player}动了手，两边打起来。" if fought
                    else f"你和{who}一起向{player}动了手。")
        else:
            head = f"你向{player}动了手，两人打起来。" if fought else f"你向{player}动了手。"
        if said:
            # 生死未定那一支：台词照给，但收尾必须钉住"接下来那一下不是你能定的"
            if not data.get("verdict_page"):
                # ★有台词但**没打成**时也要给结论★（10-28 真机：姜萌逃跑那场）
                #   逃跑判定（`UnitActionFeedback101`）会弹一页带台词的反馈，玩家点「确定」后
                #   链上就有话 —— 但它不是战报。只贴台词会让模型以为"打了、还说了一句话"。
                #   判据同 `fought`：只认战斗信号，不认台词。
                if fought:
                    tail = ""
                elif data.get("no_battle"):
                    # C# 抓到了反馈页 20102 ⇒ **确凿**是"没打起来"（不是"还在等"）
                    tail = (f"\n（这场**没能打起来** —— {player}没应战就退了，别当成已经交过手。）")
                else:
                    tail = ""
            elif together:
                tail = (f"\n（{player}落了下风，正由**玩家**决定怎么处置他 —— 那一下不是你能定的，"
                        "别替他决定杀不杀，也别写成是你放过的。）")
            else:
                tail = (f"\n（{player}还没开口处置你 —— **生死未定**，别替他决定杀不杀，"
                        "也别当作已经逃过。）")
            return head + "\n" + "\n".join(said) + tail
        if data.get("verdict_page"):
            if together:
                return (head + f"{player}战败，正由**玩家**处置他，**生死未定** "
                               "—— 别自行推断玩家会怎么决定，也别写成是你下的手。")
            return (head + f"你落在下风，正由{player}处置，**生死未定** "
                           "—— 别自行推断他会怎么决定。")
        if data.get("battle_finished"):
            return head + "打完了，但没拿到场上的话，谁胜谁负未知 —— 别自行推断。"
        if data.get("battle_seen"):
            return head + "可这场战斗的结果没有回来（不代表你没打，也不代表谁输）—— 别自行推断。"
        return head + f"不过没等到动手的下文（{player}没应战，或战斗中途断了）—— 别据此推断胜负。"
    head = f"{player}应下了你的切磋，两人动起手来。" if fought else f"{player}应下了你的切磋。"
    if said:
        return head + "\n" + "\n".join(said)
    if data.get("battle_finished"):
        return head + "打完了，但没拿到战后的话，谁胜谁负未知 —— 别自行推断。"
    if data.get("battle_seen"):
        return head + "可这场战斗的结果没有回来（不代表你没打，也不代表谁输）—— 别自行推断。"
    return head + "不过没等到动手的下文（战斗未开始或中途断了）—— 别据此推断胜负。"


# 处置页（20403）的结局 → 中文。**键与 C# `NpcDuel.SetOutcome` 的取值一一对应**
# （`kill` / `spare_took_items` / `insult` / `spare` / `pill` / `closed`）。
# 只在 `judgement.label` 读不到时兜底用 —— 别拿它去替换 C# 送来的原话（那才是配表原文）。
_WATCH_OUTCOME_TEXT = {
    "kill": "击杀此人",
    "spare_took_items": "东西不错我收下了，放你一马",
    "insult": "收下物品，羞辱此人",
    "spare": "算了，东西不要了，你走吧",
    "pill": "喂他服下一枚丹药",
    "closed": "你没有发话，由他去了",
}


def _watch_judgement(data: Dict[str, Any], me: str, other: str) -> str:
    """战后处置页那一下（C# 端 `data.judgement`）：**有才说，没有一个字都不多说**。

    字段缺失 ≠ 否定（本仓铁律）：没走到处置（没人倒下 / 页没开成 / 玩家还没选）时
    C# **不加**这个键，这里就必须返回空串 —— 不许替它编"你杀了他 / 你放了他"。

    `label` 缺失时用 `outcome` 兜底（表见 `_WATCH_OUTCOME_TEXT`）；两者都认不出就原样报
    `outcome`，不猜它的中文。败者是**读这段的 NPC 自己**时换一种说法（观战里发起方也可能输），
    别写成"你处置了你自己"。
    """
    j = data.get("judgement")
    if not isinstance(j, dict):
        return ""
    loser = _s(j.get("loser")) or other
    outcome = _s(j.get("outcome"))
    if not loser and not outcome:
        return ""          # 空壳：当作没有处置这件事
    if outcome == "closed":
        if loser == me:
            return f"你倒在{other}面前，没人处置你，由你去了。"
        return f"{loser}倒在你面前，你没有处置，由他去了。"
    label = _s(j.get("label")) or _WATCH_OUTCOME_TEXT.get(outcome, outcome)
    if not label:
        return ""
    if loser and loser == me:
        base = f"你倒在{other}手边，被{other}那边的人处置：{label}。"
    else:
        base = f"你上前处置了{loser}：{label}。"
    # 战败重伤（C# `judgement.hurt`）：**有才说**，而且只说 C# 明确报"挂上了"的那一种。
    # 那一格是自由文本（`战败重伤=气运 5011（表 RoleBattle.hurtFeatureID）挂载成功｜…`），
    # 这里只认"挂载成功 / 已在身"两个明确词；其余（已击杀 / 未落账 / 跳过）**一个字都不加** ——
    # 不许替 C# 说一句它没有确认过的后果（本仓"字段缺失 ≠ 否定"的同一条纪律）。
    hurt = _s(j.get("hurt"))
    if hurt and ("挂载成功" in hurt or "已在身" in hurt):
        if loser and loser == me:
            base += "你伤势不轻（「重伤」在身）。"
        else:
            base += f"{loser}伤势不轻（「重伤」在身）。"
    return base


def _render_npc_watch(data: Dict[str, Any]) -> str:
    """NPC 对 NPC 观战的战报（读者 = 发起方 NPC，"你" = 它本人）。

    ★ 只报事实 ★：`battle_seen` 决定"打没打起来"；`winner` **只在恰好一方倒下时存在**
    （C# 端 `BuildResult` 的判据），缺失 = 谁都没倒/判不出 —— 绝不替它编一个胜负
    （本仓一贯口径：字段缺失 ≠ 否定，更 ≠ 可以自己补）。

    `judgement` 同理：只有真的走到了战后处置页（`DramaDialogue 20403`）并拿到玩家的选择时
    才存在（`csharp/drama/NpcDuel.cs`）；缺失 = 没走到处置，文案**一个字都不加**。
    """
    left = data.get("left") if isinstance(data.get("left"), dict) else {}
    right = data.get("right") if isinstance(data.get("right"), dict) else {}
    me = _s(left.get("name")) or "你"
    other = _s(right.get("name")) or _s(data.get("target")) or "对方"

    if data.get("battle_seen") is False:
        return f"你与{other}的这场对战没能开起来（引擎没有进入战斗）。"

    # 玩家是**旁观的第三方**（C# 端 `watched`）：这一场不是 1v1 的私斗，而是当着人打的。
    # 名字读不到就不提 —— 不替它编一个"众目睽睽"。
    spectator = _s(data.get("spectator"))
    seen = f"（{spectator}在旁看着）" if spectator else ""

    winner = _s(data.get("winner"))
    if winner:
        loser = other if winner == me else me
        return f"你与{other}当场动手{seen}，{winner}占了上风，{loser}落败。" + _watch_judgement(data, me, other)

    # 没有确定的胜负：把血量当事实报出来（有就报，没有就不提）
    hp_bits = []
    for label, side in ((me, left), (other, right)):
        hp, hp_max = _num(side.get("hp")), _num(side.get("hp_max"))
        if hp is not None and hp_max:
            hp_bits.append(f"{label} {hp}/{hp_max}")
    tail = ("（" + "，".join(hp_bits) + "）") if hp_bits else ""
    return (f"你与{other}当场动手{seen}，这一场打完双方都没有倒下{tail} —— 胜负未定，"
            f"别自行断言输赢，按场面说话。")


def _render_sect_membership(data: Dict[str, Any]) -> str:
    """入宗/退宗（10-29）。三种形态：**没动手只要你去问**（ask）／已落地／已受理待落地。

    读这段的是**发起方 NPC 自己**，所以一律用"你"；`ask` 那一支还附带该宗在任人数与
    可选师父——那是问玩家时该说的实料，别让 NPC 空口问一句"你要我入哪门"。
    """
    op = _s(data.get("op"))
    sect = _s(data.get("sect"))
    roster = data.get("roster") if isinstance(data.get("roster"), dict) else {}

    if data.get("ask"):
        lines = []
        counts = []
        for k, label in (("out", "外门"), ("in", "内门"), ("inherit", "真传")):
            if roster.get(k) is not None:
                counts.append(f"{label}{_s(roster.get(k))}人")
        if counts:
            lines.append("该宗在任：" + "、".join(counts))
        elders = [x for x in _lst(roster.get("elders")) if isinstance(x, dict)]
        if elders:
            segs = []
            for e in elders:
                if e.get("vacant"):
                    segs.append(f"{_s(e.get('dept'))}（空缺）")
                else:
                    segs.append(f"{_s(e.get('dept'))}·{_s(e.get('name'))}"
                                f"（门下真传{_s(e.get('inherit')) if e.get('inherit') is not None else '?'}人）")
            lines.append("可选师父：" + "、".join(segs))
        q = _s(data.get("question")) or "身份还没定"
        head = f"（系统：这一调没有动手 —— {q}）"
        return head + ("\n" + "\n".join(lines) if lines else "")

    reason = _s(data.get("reason")).strip()
    tail = f"（你给的理由：{reason}）" if reason else ""

    if op == "join_sect":
        post = _s(data.get("post"))
        dept = _s(data.get("master_dept"))
        at = f"「{sect}」" if sect else ""
        via = f"，拜在{dept}长老门下" if dept else ""
        who = f"，身份是{post}" if post else ""
        if data.get("joined"):
            now = _s(data.get("now_sect")) or sect
            now_post = _s(data.get("now_post"))
            if now_post and now_post != post:
                who = f"，身份是{now_post}"
            return f"你已入{at}{who}{via}（现属{now}）。{tail}".strip()
        if data.get("pending"):
            return (f"你入{at}{who}{via}的事引擎已受理，名册下一拍才更新——别当没成。{tail}".strip())
        return f"你入{at}{who}{via}的结果未知（C# 未给结局）。{tail}".strip()

    if op == "leave_sect":
        frm = _s(data.get("from"))
        at = f"「{frm}」" if frm else "本宗"
        if data.get("left"):
            return f"你已退出{at}，如今是散修。{tail}".strip()
        if data.get("pending"):
            return f"你退出{at}的事引擎已受理（现在名册仍显示{_s(data.get('now_sect')) or '本宗'}）——别当没成。{tail}".strip()
        return f"你退出{at}的结果未知（C# 未给结局）。{tail}".strip()
    return None  # type: ignore[return-value]


@renderer("world_ai_action")
def render_world_ai(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    op = _s(data.get("op") or args.get("op"))
    target = _s(data.get("target"))
    # 宗门人事（10-29）：入宗/退宗既没有战斗也没有原生窗，走独立渲染（含"该去问玩家"那一支）
    if op in ("join_sect", "leave_sect"):
        return _render_sect_membership(data)
    # NPC 对 NPC 观战（10-26）：与"打玩家"是两条完全不同的链 —— 没有战前窗、没有处置页，
    # 走的是引擎的 `dramaUnitBattle_1_2`（`npcFightUnits` + 观战副本 4001），打完才回。
    # 判据只认 C# 给的 `mode`，不猜。
    # `solo` = 10-27 起的正式值（"你自己上，玩家旁观"）；`watch` 是同日早先的开发名，一并认。
    if _s(data.get("mode")) in ("solo", "watch"):
        return _render_npc_watch(data)
    if op in ("spar", "attack"):
        # 战斗类：C# 挂起、跨过整场战斗才回，结局由 `BattleOutcome` 带回来（链上每页原话 + 谁在说）。
        # 切磋（原生 21204 窗「好，就让我和你切磋一下 | 我现在没有空」）前 C# 是"发了就回"，
        #   两条分支给模型**同一句话** ⇒ 玩家婉拒了，NPC 还照着"双方已开始切磋"演（用户报的"都一样"）。
        # 攻击链更长：逃跑 / 战败求饶 / 战胜后被处置，玩家胜那一支收在处置页、生死未定。
        # ★视角：读这段的是**发起方的 NPC**，所以句式是"你邀他 / 他应下 / 他婉拒"。旧文案写成
        #   "你应下了姜萌的切磋"（姜萌就是读的人），等于告诉 NPC 应战的是它自己 —— 09-22 扳正。
        # `accepted` 缺失 = 玩家没选就直接关掉了窗（OnEnd 兜底路径）——
        # 契约同 yao_yue：**字段缺失 ≠ 否定**，不许替玩家编一个"婉拒"。
        player = _s(data.get("player")) or "对方"
        if data.get("pending"):
            return (f"你邀{player}切磋（等待他在原版剧情中回应）。" if op == "spar"
                    else f"你向{player}发起攻击（等待他在进战斗前的界面里应战）。")
        if op == "attack":
            return _render_battle_result(data)
        acc = data.get("accepted")
        if acc is True:
            return _render_battle_result(data)
        if acc is False:
            return f"{player}婉拒了你的切磋。"
        return f"你邀{player}切磋，但他未作答复（剧情窗被直接关掉了）。"
    if op in ("lun_dao", "shuang_xiu"):
        name = "论道" if op == "lun_dao" else "双修"
        if data.get("completed") is True:
            # ★带上交互内容（09-23）★：`TalkOutcome` 把这场交互的**链上原文**（谁在说）与
            # **玩家每次的选择**都收进结果了；这里不渲染的话，模型只知道"完成了"，不知道
            # 论了什么、玩家答了什么 —— 正是用户报的"结果草率"。视角与战斗链同一条。
            said = _speak_ordered(data)
            # 好感（10-15 补显示，本项目规则：配表无 lun_dao 键，完成后 C# 双向各 +10 并透出实得）：
            # 本渲染器视角"你"= 发起方（AI）—— npc_to_player = 你对玩家、player_to_npc = 玩家对你。
            # 双修由原生自算（C# 只探针不透出字段）⇒ 天然不显示。
            intim = data.get("intim") if isinstance(data.get("intim"), dict) else {}
            note = "，".join(p for p in (
                _intim_delta("你", target, intim.get("npc_to_player")),
                _intim_delta(target, "你", intim.get("player_to_npc"))) if p)
            head = f"你与{target}的{name}已结束。" + (note + "。" if note else "")
            return head + "\n" + "\n".join(said) if said else head
        if data.get("completed") is False:
            return f"{name}未完成（玩家婉拒或中途离开）。"
        return f"{target}已向你发起{name}，等待完成（C# 未返回结局）。"   # 字段缺失=非挂起形态
    if op == "yao_yue":
        if data.get("pending"):
            return f"{target}已向你发出邀约（等待你在原版剧情中回应）。"
        if data.get("accepted") is True:
            # 接受后带上第二层原生剧情的原句（C# DramaTextCapture 从 GetDialogueText 抓的成品句，
            # 邀约地点就在句子里的「在××附近」）→ 给模型沉浸感与可复用的地名。
            # 抓不到就不写该字段，退回旧文案（契约：字段缺失 ≠ 否定，不许编地点）。
            said = _s(data.get("invite_text"))
            if said:
                return (f"你接受了{target}的邀约。{target}说：「{said}」\n"
                        f"（原版剧情里玩家已看过这句话，勿复述，接着往下说即可。）")
            return f"你接受了{target}的邀约（将按剧情赴约）。"
        if data.get("accepted") is None:
            return f"{target}已向你发出邀约，等待你在原版剧情中回应（C# 未返回结局）。"
        base = f"你拒绝了{target}的邀约"
        return base + "（对方似有不悦，好感或受影响）。" if data.get("upset") else base + "。"
    if op == "chuan_gong":
        skill = _s(data.get("skill"))
        tail = f"「{skill}」" if skill else ""
        # 功法来源：C# 会区分 `gainSkill`（玩家在游戏原生功法面板**自己选的**）
        # 与 `seed`（用 NPC 功法槽播种/模型指定）。此前该字段一直没渲染 —— 玩家自选时
        # 模型会把对方选的功法说成"我传你这部"，语气反了。字段缺失 ≠ seed，不做任何推断。
        by_player = _s(data.get("skill_source")) == "gainSkill"
        who = "你选定要学的" if by_player else ""
        if data.get("pending"):
            return f"{target}欲将功法{tail}传授于你（等待你在原版剧情中回应）。"
        if data.get("accepted") is True:
            return (f"你接受了{target}传授的功法{tail}（{who}）。" if by_player
                    else f"你接受了{target}传授的功法{tail}。")
        if data.get("accepted") is None:
            return f"{target}欲将功法{tail}传授于你，等待你在原版剧情中回应（C# 未返回结局）。"
        base = f"你拒绝了{target}的传功{tail}"
        return base + "（对方似有不悦，好感或受影响）。" if data.get("upset") else base + "。"
    return None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# economy_item（NPC 赠送 → 玩家；发起方=args.initiator，接收方=data.target=玩家名）
# ---------------------------------------------------------------------------

def _deliver(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    via = _s(data.get("via") or args.get("via"))
    if via == "letter":
        return "以信件方式送达"
    if data.get("same_grid") is True:
        return "当面送达"
    if data.get("same_grid") is False:
        return "异地以传讯方式送达"
    return ""


@renderer("economy_item")
def render_economy(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    """忠实呈现：逐项请求数/实得数/失败原因、部分收下或全拒收、灵石即时到账、送达方式。"""
    if data.get("proposal"):
        # 提案（09-17）：只表达赠予之意、尚未交付（样本由 Python 闸门合成；见 room_flow 提案制）
        who = _s(data.get("counterparty")) or "对方"
        nm, n = _s(data.get("item_name")), _num(data.get("count"))
        return f"你已向{who}表达了赠予{nm}×{_s(n)}之意，等对方收下。"
    giver = _s(args.get("initiator")) or "对方"
    # 接收方：对手方字段（非玩家）优先 → `data.target`（=玩家名）→ 你。
    # 三个来源是历史形状，不是"三处真相"：C# 只在接收方**不是**玩家时才发 counterparty。
    target = _s(data.get("counterparty")) or _s(data.get("target")) or "你"
    ok_props, money_in, failed = [], [], []
    for it in _lst(data.get("items")):
        if not isinstance(it, dict):
            continue
        name, req, act = _s(it.get("item")), _num(it.get("requested")), _num(it.get("actual"))
        err = _s(it.get("error"))
        if err:
            failed.append(f"{name}（请求{_s(req)}，未达成：{err}）")
            continue
        (money_in if (it.get("mode") == "money" or name == "灵石") else ok_props).append((name, req, act))

    def fmt(seq) -> str:
        out = []
        for name, req, act in seq:
            seg = f"{name}×{_s(act)}"
            if req is not None and act is not None and req != act:
                seg += f"（请求{req}，实得{act}）"    # 库存不足等差额——忠实标注
            out.append(seg)
        return "、".join(out)

    refused = _num(data.get("refused_count"))
    accepted_count = _num(data.get("accepted_count"))
    accepted = data.get("accepted")
    prop_text, money_text = fmt(ok_props), fmt(money_in)

    segs = []
    if prop_text:
        if accepted_count is not None and accepted is not False and accepted_count < len(ok_props):
            # ★ 引擎真值 < 我们报的件数（09-17 真机实锤）：原生赠礼窗**一次只处理一件**
            #   （`UnitActionRoleGive.propsData` 是单数；配表文案「我这里有一个{0}准备赠于你」）。
            #   旧文案按"愿望单"宣布全部收下 —— 用户看到的正是"工具说成功、身上只有一件"。
            #   这里按引擎真值（receiveProps 计数）说，并点明其余没送到。
            got = fmt(ok_props[:accepted_count])
            miss = fmt(ok_props[accepted_count:])
            seg = f"{target}收下了赠礼（{got}）" if got else f"{target}没有收下赠礼"
            if miss:
                seg += f"；未送达：{miss}（引擎一次只处理一件，其余已退回发起方）"
            segs.append(seg)
        elif refused and (accepted_count is None or accepted_count > 0):
            # 部分拒收：还有收下件（accepted_count = receiveProps 计数）
            segs.append(f"{target}收下了部分赠礼（{prop_text}），拒收了其余{refused}件")
        elif accepted is False and refused:
            segs.append(f"{target}拒绝了赠礼（{prop_text}），选择了「我不需要这个」")
        else:
            segs.append(f"{target}收下了赠礼（{prop_text}）")
    if money_text:
        segs.append(f"{target}收下了{money_text}")   # fmt 已含「灵石×N」
    if failed:
        segs.append("未送达：" + "、".join(failed))
    if not segs:
        return None  # type: ignore[return-value]
    deliver = _deliver(args, data)
    # 好感变化（10-15 补显示）：我们落刀的赠礼路径透出 `intim`（收礼方对送礼方的实得 Δ）；
    # 原生单件窗没有该字段 ⇒ 天然不显示（字段有无即路径有无）。措辞用全名——本渲染器里"你"=玩家。
    note = _intim_delta(target, giver, data.get("intim"), data.get("intim_after"))
    return (f"{giver}赠予{target}：" + "；".join(segs)
            + (f"（{deliver}）" if deliver else "")
            + (f"；{note}" if note else ""))


# ---------------------------------------------------------------------------
# trade（买卖：卖方出货 → 买方，买方灵石 → 卖方；双方都由模型指定）
# ---------------------------------------------------------------------------

@renderer("trade")
def render_trade(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    """忠实呈现：谁把什么卖给了谁、成交价、双方余额变化、以及"货是否真的到了买方"。

    `item_moved` 是 C# 落刀后的**核对结果**（买方该道具实收件数）——入包被背包规则吃掉时
    C# 会自己回滚并改成 error 帧，所以这里 False 只可能是极端竞态，照实说不要粉饰。
    """
    if data.get("proposal"):
        # 提案（09-17）：只报了条款、未成交（样本由 Python 闸门合成）。不放这里的话会落进
        # 下面的"成交"文案，把还没发生的交易说成既成事实。
        who = _s(data.get("counterparty")) or "对方"
        item_p = _s(data.get("item"))
        n_p = _num(data.get("count"))
        p_p = _num(data.get("price")) or 0
        deal_p = f"价 {p_p} 灵石" if p_p > 0 else "不取分文"
        return f"你已向{who}提出这桩买卖：「{item_p}」×{_s(n_p)}，{deal_p}（对方应下才算数）。"
    seller = _s(data.get("seller") or args.get("seller"))
    buyer = _s(data.get("buyer") or args.get("buyer"))
    item = _s(data.get("item") or args.get("item"))
    count = _s(_num(data.get("count")) or args.get("count") or 1)
    price = _num(data.get("price"))
    if price is None:
        price = _num(args.get("price")) or 0
    if not (seller or buyer or item):
        return None  # type: ignore[return-value]
    money = f"，价 {price} 灵石" if price > 0 else "（不取分文）"
    seg = f"{seller}把「{item}」×{count}卖给了{buyer}{money}"
    if price > 0:
        sb, sa = _num(data.get("seller_money_before")), _num(data.get("seller_money_after"))
        bb, ba = _num(data.get("buyer_money_before")), _num(data.get("buyer_money_after"))
        if sb is not None and sa is not None:
            seg += f"（{seller}灵石 {sb}→{sa}"
            if bb is not None and ba is not None:
                seg += f"，{buyer}灵石 {bb}→{ba}"
            seg += "）"
    if data.get("item_moved") is False:
        seg += "；但买方背包实收件数对不上，已按回滚处理"
    else:
        # 到货实证：C# 落刀后核对了买方该道具的件数，before/after 一直在发，
        # 但渲染层此前只看了 item_moved 这个布尔 —— 模型看不到「0→3」这个**唯一可核对的事实**。
        ib, ia = _num(data.get("buyer_item_before")), _num(data.get("buyer_item_after"))
        if ib is not None and ia is not None:
            seg += f"（{buyer}的「{item}」{ib}→{ia}）"
    same = data.get("same_grid")
    if same is True:
        seg += "，当面交割"
    elif same is False:
        seg += "，异地交割"
    # 好感变化（10-15 补显示，本项目规则：原生交易不挂好感，成交后 C# 落刀透出 `intim`）：
    # 有价成交 = 双向各 +5（seller_to_buyer / buyer_to_seller）；price=0 赠予 = 只有 buyer_to_seller。
    # 读不到起点的方向已被 C# `PutIntim` 滤掉 ⇒ 这里缺失即不显示该方向。
    intim = data.get("intim")
    if isinstance(intim, dict):
        parts = [p for p in (_intim_delta(seller, buyer, intim.get("seller_to_buyer"),
                                          intim.get("seller_to_buyer_after")),
                             _intim_delta(buyer, seller, intim.get("buyer_to_seller"),
                                          intim.get("buyer_to_seller_after"))) if p]
        if parts:
            seg += "。" + "，".join(parts)
    return seg + "。"


# ---------------------------------------------------------------------------
# item_acquire（偷窃/讨要 → 玩家）
# ---------------------------------------------------------------------------

@renderer("item_acquire")
def render_item_acquire(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    # 失败帧（足额不足/窗未开成/玩家在确认窗点了"不给"）原样进上下文 —— error 文本自带
    # 原因，绝不渲染成"等待回应"（此前 Fail 帧会掉进 accepted is None 分支被误读成挂起）。
    if isinstance(data, dict) and data.get("success") is False:
        return None  # type: ignore[return-value]
    op = _s(data.get("op") or args.get("op"))
    target, item = _s(data.get("target")), _s(data.get("item_name"))
    cp = _s(data.get("counterparty"))          # 非空 = 被偷/被讨的人不是玩家（免窗直转）
    if data.get("proposal"):
        # 提案（09-17）：只讨要、未转移（样本由 Python 闸门合成）
        who = _s(data.get("counterparty")) or "对方"
        return f"你已向{who}讨要「{item}」×{_s(_num(data.get('count')))}，等对方回应。"
    if op == "steal_item":
        n = _num(data.get("stack_count"))
        who = f"{cp}的" if cp else "你的"
        return f"{target}偷取了{who}「{item}」" + (f"，整栈共{n}个。" if n else "。")
    if op == "ask_for":
        count = _num(data.get("count"))
        # 10-15：多件（items）与单件共用同一套句子，只换"东西"这一截（单件逐字不变）
        what = _acquire_what(data, item, count)
        if cp:
            if data.get("accepted") is True:
                # 好感（10-15 补显示）：讨要达成 = 双边 —— 取得方（target，AI）对交付方（cp）+；
                # 交付方对取得方 −（`intim_upset`）。方向缺失时只显示另一边。
                note = "，".join(p for p in (
                    _intim_delta(target, cp, data.get("intim"), data.get("intim_after")),
                    _intim_delta(cp, target, data.get("intim_upset"), data.get("intim_upset_after"))) if p)
                return f"{cp}把{what}交给了{target}。" + (note + "。" if note else "")
            if data.get("accepted") is None or data.get("pending"):
                return f"{target}向{cp}讨要{what}。"
            base = f"{cp}拒绝了{target}的讨要（{what}）"
            return base + "，对方似有不悦（好感或受影响）。" if data.get("upset") else base + "。"
        if data.get("pending"):
            return f"{target}向你讨要{what}（等待你在原版剧情中回应）。"
        # receiving（09-21）：这一笔是玩家**主动要送**、NPC 用同一条 ask_for 收下 —— 措辞不能
        # 再写"讨要/拒绝讨要"，否则模型读到"拒绝讨要"会以为是自己开口要失败了。
        if data.get("receiving"):
            if data.get("accepted") is True:
                # 好感（10-15 补显示）：收下赠礼 = 单边 —— 收礼方（target，AI）对送礼方（你，玩家）+。
                note = _intim_delta(target, "你", data.get("intim"), data.get("intim_after"))
                return f"你赠的{what}被{target}收下了。" + (note + "。" if note else "")
            if data.get("accepted") is None:
                return f"你赠{target}的{what}还没回音（C# 未返回结局）。"
            return f"你把要赠{target}的{what}收回了。"
        if data.get("accepted") is True:
            # 好感（10-15 补显示）：讨要达成 = 双边 —— 取得方（target，AI）对你（交付方）+；
            # 你对它 −（`intim_upset`，被讨要的不痛快）。原生单件讨要无字段 ⇒ 不显示。
            note = "，".join(p for p in (
                _intim_delta(target, "你", data.get("intim"), data.get("intim_after")),
                _intim_delta("你", target, data.get("intim_upset"), data.get("intim_upset_after"))) if p)
            return f"你把{what}交给了{target}。" + (note + "。" if note else "")
        if data.get("accepted") is None:
            return f"{target}向你讨要{what}，等待你在原版剧情中回应（C# 未返回结局）。"
        base = f"你拒绝了{target}的讨要（{what}）"
        return base + "，对方似有不悦（好感或受影响）。" if data.get("upset") else base + "。"
    return None  # type: ignore[return-value]


def _acquire_what(data: Dict[str, Any], item: str, count: int | None) -> str:
    """item_acquire 结果里的"东西"那一截：单件 = `「名」×N`（逐字沿用旧文案）；
    多件（`items` 数组，10-15 新增）= 逐项 `「名」×N` 用「、」连起来。"""
    items = data.get("items")
    if isinstance(items, list) and items:
        segs = []
        for it in items:
            if not isinstance(it, dict):
                continue
            nm = _s(it.get("item"))
            if not nm:
                continue
            segs.append(f"「{nm}」×{_s(_num(it.get('requested')))}")
        if segs:
            return "、".join(segs)
    return f"「{item}」×{_s(count)}"


# ---------------------------------------------------------------------------
# team —— 组队（invite / dismiss / status）
# ---------------------------------------------------------------------------

def _intim_tail(data: Dict[str, Any], multi: bool = False) -> str:
    """解散的好感代价那一句（**单边**：只有被请离的 NPC 对玩家掉好感）。

    为什么要说出来：这是玩家看不见的代价 —— 关系面板不主动翻就发现不了。
    模型不知道"踢人得罪了谁"，下一轮就会把人当成还热络，说错话。

    缺失即未知：`intim_lost` 读不到时（起点好感读失败）**不编数字**，只给中性描述。
    """
    lost = _num(data.get("intim_lost"))
    detail = data.get("intim_detail")
    if lost is not None and lost != 0:
        if multi and isinstance(detail, list) and detail:
            parts = []
            for it in detail:
                if not isinstance(it, dict):
                    continue
                nm, dv = _s(it.get("name")), _num(it.get("delta"))
                if nm and dv is not None and dv != 0:
                    parts.append(f"{nm}（{dv:+d}）")
            if parts:
                return f"他对你的好感随之下降：{'、'.join(parts)}。"
        return f"对方对你的好感下降了 {abs(lost)}。"
    return ""


@renderer("team")
def render_team(args: Dict[str, Any], data: Dict[str, Any]) -> Optional[str]:  # type: ignore[return-value]
    """组队结果 → 人话。

    三个 op 的形状差别很大（invite 谈"谁加入了"、dismiss 谈"谁走了/整队散了"、
    status 谈"现在队里有谁"），所以分三支写，不硬套一个模板。
    """
    op = _s(data.get("op")) or _s(args.get("op"))

    # ── status：列队 ──────────────────────────────────────────
    if op == "status":
        n = _num(data.get("count")) or 0
        limit = _num(data.get("limit"))
        members = data.get("members")
        if not n:
            return "你眼下没有队友（可邀请他人结伴同行）。"
        names = []
        hurt = []
        if isinstance(members, list):
            for m in members:
                if not isinstance(m, dict):
                    continue
                nm = _s(m.get("name"))
                if not nm:
                    continue
                if m.get("hurt"):
                    hurt.append(nm)
                    names.append(f"{nm}（重伤中）")
                else:
                    names.append(nm)
        who = "、".join(names) if names else f"{n} 人"
        cap = f"／上限 {_s(limit)} 人" if limit else ""
        text = f"队伍现有 {_s(n)} 人{cap}：{who}。"
        # 不满编要说清后果：`NpcCondition`/`DungeonTrigger` 是「一槽一枚气运」，
        # 缺的号位在副本里取到空 —— 所以只有满编才是稳定的三人同行。
        left = _num(data.get("slots_left"))
        if left:
            text += f"还差 {_s(left)} 人满编，凑满 3 人下副本才会三人同行。"
        if data.get("warning"):
            text += f"（{_s(data.get('warning'))}）"
        if hurt:
            text += f"其中 {len(hurt)} 人正处「内外重伤」，暂不宜再下副本。"
        return text

    # ── invite ───────────────────────────────────────────────
    if op == "invite":
        target = _s(data.get("target")) or "对方"
        if data.get("already"):
            return f"{target}本来就在你的队伍里，不必再邀。"
        if data.get("joined"):
            return f"{target}答应与你结伴同行，往后下副本会同行。"
        return None

    # ── dismiss ──────────────────────────────────────────────
    if op == "dismiss":
        if data.get("already"):
            target = _s(data.get("target")) or "对方"
            return f"{target}本就不在你的队伍里。"
        if data.get("left"):
            target = _s(data.get("target")) or "对方"
            return f"{target}已离队，各自珍重。" + _intim_tail(data)
        removed = data.get("removed")
        if isinstance(removed, list) and removed:
            who = "、".join(_s(x) for x in removed if _s(x))
            return f"队伍就此散了（{who} 各自离去）。" + _intim_tail(data, multi=True)
        n = _num(data.get("disbanded"))
        if n == 0:
            return "你眼下没有队伍可散。"
        return None

    return None
