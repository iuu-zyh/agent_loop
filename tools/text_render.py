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
from typing import Any, Dict, List, Optional

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
    """`[{name, unit_id}]`（关系簿，09-13）→ `名(unit_id)`；兼容旧的纯字符串数组。

    **为什么要带 id**：模型拿「益婉容」三个字回查会撞同名 —— 真机上 `inspect_unit(target=
    "益婉容")` 返回了另一个益婉容（结晶后期/声望3379 被查成登仙境/声望3129，见附录 D.1
    重名事故）。带上 `益婉容(Xs6JDI)` 后模型可以用 `inspect_unit(unit_id=…)` 一步钉死，
    全程不经过名字。id 缺失时退化成只给名字（旧 C# 载荷照常可读）。
    """
    out: List[str] = []
    items = v if isinstance(v, list) else [v]
    for x in items:
        if isinstance(x, dict):
            nm, uid = _s(x.get("name")).strip(), _s(x.get("unit_id")).strip()
            if nm and uid:
                out.append(f"{nm}({uid})")
            elif nm or uid:
                out.append(nm or uid)
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
    head = _s(data.get("name"))
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
        same = bool(data.get("same_grid"))
        seg = f"与玩家{'同处一地' if same else '异地'}，关系{_s(data.get('relation'))}"
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
    if any(_s(l).endswith("：") or l.startswith("· ") for l in out):
        out.insert(0, f"背包（约{pieces}件）：")
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
    """关系簿：12 容器全列（不再限 3 名——忠实优先），每人附 `unit_id`，附人情。

    带 id 的理由见 `_named_refs`：这是模型**唯一**能拿到精确 unit_id 的地方之一
    （另一处是 `search_units`），没有它就只能拿名字回去按名猜。
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
    mark_important = flt == "all"
    lines = []
    for m, t, tier in entries:
        mark = "★" if (mark_important and tier == "important") else ""
        lines.append(f"{mark}（{m}）{t}" if m else mark + t)
    fmap = {"important": "重要", "regular": "常规", "all": "全部"}
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
    # 措辞要点：① 明说"可能不是你要找的人"；② 给出本次用的是谁；③ 给出候选 id 让模型能改查。
    amb = data.get("ambiguous")
    if isinstance(amb, dict) and (_num(amb.get("matched")) or 0) > 1:
        cands = [c for c in _lst(amb.get("candidates")) if isinstance(c, dict)]
        used = _s(amb.get("used")).strip()
        one = next((c for c in cands if _s(c.get("unit_id")).strip() == used), None)
        desc = ""
        if one:
            bits = [x for x in (_s(one.get("realm")).strip(), _s(one.get("sect")).strip()) if x]
            if _num(one.get("reputation")) is not None:
                bits.append(f"声望{_num(one.get('reputation'))}")
            pt = one.get("point")
            if isinstance(pt, dict) and pt.get("x") is not None:
                bits.append(f"坐标({pt.get('x')},{pt.get('y')})")
            if bits:
                desc = "，".join(bits)
        blocks.append(
            f"⚠️同名提醒：「{_s(amb.get('target'))}」全图有 {_num(amb.get('matched'))} 人同名，"
            f"以下是 unit_id={used}" + (f"（{desc}）" if desc else "") + " 这一位的档案，"
            "**未必是你要找的人**。其余同名者："
            + "、".join(f"{_s(c.get('name'))}({_s(c.get('unit_id'))})" for c in cands
                        if _s(c.get("unit_id")).strip() != used)
            + "。要查其中某一位，用 inspect_unit(unit_id=\"…\") 精确指定。")
    uid = _s(data.get("unit_id")).strip()
    if uid:
        blocks.append(f"unit_id：{uid}")
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
    head = f"找人结果（命中{total}人，按好感降序，最多列前10）"
    if flt:
        head += f"｜筛选：{flt}"
    if not items:
        return head + "：无匹配。"
    lines = []
    for it in items:
        seg = f"{_s(it.get('name'))}（"
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
        # unit_id（重名事故）：这是**唯一**能从搜索结果走到精确查询的桥。
        # 不带上它，模型只能拿名字回去按名猜 —— 真机正是这样查成了同名的另一个人。
        uid = _s(it.get("unit_id")).strip()
        if uid:
            seg += f"，unit_id={uid}"
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
    """可去地点：按类别分组 + 类别计数，带坐标。region/cat 由 C# 已筛，这里只回显筛选条件。"""
    region = _s((args or {}).get("region"))
    cat = _s((args or {}).get("cat"))
    flt = "、".join(x for x in (f"分类={cat}" if cat and cat != "全部" else "", f"州={region}" if region else "") if x)
    places = [p for p in _lst(data.get("places")) if isinstance(p, dict)]
    total = _num(data.get("total")) or len(places)
    if not places:
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
    已按用户裁剪：不再输出 名词组1/2、类型+立场值、id。可按 region 过滤（C# 已筛，这里只反映数量）。"""
    region = _s((args or {}).get("region"))
    sects = [s for s in _lst(data.get("sects")) if isinstance(s, dict)]
    if not sects:
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
    head = (f"{region}宗门概览" if region else "宗门概览") + f"（共{len(sects)}个）"
    return _lines(head, lines)


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
_BREAK_OPS = {"jie_chu_jie_yuan", "divorce", "jie_chu_bai_shi", "jie_chu_shou_tu", "jie_chu_jie_yi"}


@renderer("social_relation")
def render_social(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    op = _s(data.get("op") or args.get("op"))
    target = _s(data.get("target"))
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
        return f"{target}对你的{verb}" + ("（" + "，".join(det) + "）" if det else "")
    relation = _s(data.get("relation")) or _SOCIAL_OP_CN.get(op, "")
    if op in _BREAK_OPS:
        seg = (f"你同意了与{target}解除{relation}关系" if relation
               else f"你同意了与{target}解除关系（op={op}）")
        if data.get("readback_still_related") is True:
            seg += "；但读回显示关系仍存在，需人工核对"
        elif data.get("readback_still_related") is False:
            seg += "；读回确认关系已解除"
        if data.get("bw_err"):
            seg += f"；原生 BreakWith 异常：{_s(data.get('bw_err'))}"
        return seg
    if relation:
        if data.get("verified") is False:
            return f"你同意了与{target}结为{relation}，但状态校验未通过（可能未生效，需核对）。"
        if "verified" not in data:
            return f"你与{target}结为{relation}（C# 未返回状态校验）。"   # 直写兜底路径
        return f"你与{target}结为{relation}。"
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
    if op == "summon":
        if data.get("moved"):
            return f"你已被召唤到{target}身边（你移动到{target}所在处）。"
        return f"你与{target}已在同一处，无需召唤。"
    if op == "teleport":
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

_WAA_ACTION_NAME = {"attack": "攻击"}


@renderer("world_ai_action")
def render_world_ai(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    op = _s(data.get("op") or args.get("op"))
    target = _s(data.get("target"))
    if op == "spar":
        # 切磋：原生 21204 剧情窗弹「好，就让我和你切磋一下 | 我现在没有空」。
        # 前 C# 是"发了就回"，两条分支给模型**同一句话** ⇒ 玩家婉拒了，NPC 还照着
        # "双方已开始切磋"往下演。现在 C# 挂起等选项落定（`DrillChoiceGate` 盯 ClickOption），
        # 这里按三态渲染。`accepted` 缺失 = 玩家没选就直接关掉了窗（OnEnd 兜底路径）——
        # 契约同 yao_yue：**字段缺失 ≠ 否定**，不许替玩家编一个"婉拒"。
        if data.get("pending"):
            return f"{target}邀你切磋（等待你在原版剧情中回应）。"
        acc = data.get("accepted")
        if acc is True:
            return f"你应下了{target}的切磋。"
        if acc is False:
            return f"你婉拒了{target}的切磋。"
        return f"{target}邀你切磋，但你未作答复（剧情窗被直接关掉了）。"
    if op in _WAA_ACTION_NAME:
        return f"{target}已向你发起{_WAA_ACTION_NAME[op]}，即将进入战斗/切磋界面。"
    if op in ("lun_dao", "shuang_xiu"):
        name = "论道" if op == "lun_dao" else "双修"
        if data.get("completed") is True:
            return f"你与{target}的{name}已结束。"
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
    giver = _s(args.get("initiator")) or "对方"
    target = _s(data.get("target")) or "你"
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
        if refused and (accepted_count is None or accepted_count > 0):
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
    return f"{giver}赠予{target}：" + "；".join(segs) + (f"（{deliver}）" if deliver else "")


# ---------------------------------------------------------------------------
# trade（买卖：卖方出货 → 买方，买方灵石 → 卖方；双方都由模型指定）
# ---------------------------------------------------------------------------

@renderer("trade")
def render_trade(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    """忠实呈现：谁把什么卖给了谁、成交价、双方余额变化、以及"货是否真的到了买方"。

    `item_moved` 是 C# 落刀后的**核对结果**（买方该道具实收件数）——入包被背包规则吃掉时
    C# 会自己回滚并改成 error 帧，所以这里 False 只可能是极端竞态，照实说不要粉饰。
    """
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
    return seg + "。"


# ---------------------------------------------------------------------------
# item_acquire（偷窃/讨要 → 玩家）
# ---------------------------------------------------------------------------

@renderer("item_acquire")
def render_item_acquire(args: Dict[str, Any], data: Dict[str, Any]) -> str:
    op = _s(data.get("op") or args.get("op"))
    target, item = _s(data.get("target")), _s(data.get("item_name"))
    if op == "steal_item":
        n = _num(data.get("stack_count"))
        return f"{target}偷取了你的「{item}」" + (f"，整栈共{n}个。" if n else "。")
    if op == "ask_for":
        count = _num(data.get("count"))
        if data.get("pending"):
            return f"{target}向你讨要「{item}」×{_s(count)}（等待你在原版剧情中回应）。"
        if data.get("accepted") is True:
            return f"你把「{item}」×{_s(count)}交给了{target}。"
        if data.get("accepted") is None:
            return f"{target}向你讨要「{item}」×{_s(count)}，等待你在原版剧情中回应（C# 未返回结局）。"
        base = f"你拒绝了{target}的讨要（「{item}」×{_s(count)}）"
        return base + "，对方似有不悦（好感或受影响）。" if data.get("upset") else base + "。"
    return None  # type: ignore[return-value]
