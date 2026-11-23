"""tools/unit_refs — 「人」的引用层：**显示名 ⇄ 真名 + 精确 unitID**

## 它执行哪条纪律

**模型看到、写出的永远只有名字**（09-17 用户拍板）。所有 unitID 都留在 harness 侧：
C# 在查询结果里给出 `(name, unit_id)` 对 → 本模块把它们收成映射表 → 工具调用前
再由 `DialogueAgent` 注入 `<键>_id`。模型从头到尾不知道 id 的存在。

为什么不给模型看 id（这是对 09-13 那次"给模型一条精确路"的反转）：
  · 模型一旦看得见 id，就会**自己编 id**、把 id 当名字念、把 id 写进台词；
  · 而"这个名字是谁"本来就该由 harness 回答 —— C# 侧已经有 `ResolveEx(name, id)`。

## 重名怎么办（唯一化显示名）

用户口径：**重名是例外，不是常态** —— 模型要指的人要么是查出来的 NPC、要么是自己、
要么是玩家，这几种都在映射表里。真正会撞名的场合只有一个：**查出来的 NPC 本身重名**。
那时的做法不是把 id 给模型，而是给那个人一个**唯一化显示名**：

    益婉容(化神殿·结晶后期)          ← 判别字段（宗门·境界…）
    益婉容#2                         ← 一个判别字段都拿不到时的兜底（同一批里的序号）

**这个字符串就是映射表的键**：模型照抄它，中间层就选得中那个人；抄裸名则回落按名解析。
于是"模型只认名字"和"精确指人"两个目标同时成立。

## 谁用

  · `dialogue_agent._harvest_ids` —— 渲染**之前**调用 `annotate()`：就地给载荷里需要
    区分的条目打上 `display`，并取回该登记进映射表的 `(键, unitID)`。
  · `tools/text_render.py` —— 渲染时**优先用 `display`**（有则用，无则裸名），并且
    任何地方都不再打印 unit_id。
  · `dialogue_agent._inject_person_ids` —— 用 `bare_name()` 把模型写的显示名还原成真名
    （判据、日志、C# 的按名兜底都继续吃真名）。

## 边界

本模块**纯函数、零依赖**（不 import 任何其它项目模块），因此可以直接单测：
载荷形状变了要在这里改，渲染与登记两侧同时跟着变 —— 这是它存在的主要理由。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# 显示名后缀：`名(判别字段)` 或 `名#序号`（都只在**确有冲突**时出现）
_PAREN = re.compile(r"\(([^()]*)\)$")
_HASH = re.compile(r"#(\d+)$")

# 判别字段的优先序：先宗门（最能区分同名的两个人），再境界，最后区域
_BIT_KEYS = ("sect", "realm", "region")
_MAX_BITS = 2


def split_display(name: Any) -> Tuple[str, str]:
    """显示名 → `(真名, 后缀)`。裸名返回 `(名, "")`。"""
    s = str(name or "").strip()
    m = _PAREN.search(s)
    if m:
        return s[: m.start()].strip(), m.group(1).strip()
    m = _HASH.search(s)
    if m:
        return s[: m.start()].strip(), "#" + m.group(1)
    return s, ""


def bare_name(name: Any) -> str:
    """显示名 → 真名（注入前归一用；模型写裸名时原样返回）。"""
    return split_display(name)[0]


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _bits(d: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for k in _BIT_KEYS:
        v = _s(d.get(k))
        if v and v not in out:
            out.append(v)
        if len(out) >= _MAX_BITS:
            break
    return out


def _bits_with_fallback(d: Dict[str, Any]) -> List[str]:
    """判别字段：先看顶层，再看 `brief` 块（只查 `relationships` 时顶层没有境界/宗门）。"""
    b = _bits(d)
    if b:
        return b
    inner = d.get("brief")
    return _bits(inner) if isinstance(inner, dict) else []


def _display(bare: str, bits: List[str], idx: int) -> str:
    if bits:
        return f"{bare}({'·'.join(bits)})"
    return f"{bare}#{idx}"


def _hash_reuse(known: Dict[str, str]) -> Dict[Tuple[str, str], str]:
    """`(unitID, 真名) → 已经发给过他的 `名#序号` 键`（同一个 id 有多个则取编号最小的）。

    判别字段缺失时的兜底显示名 `名#k` 只有"编号"这一个自由度，而编号若按**出现顺序**
    分配就会在批次之间漂移（关系簿里谁排前面由 C# 的遍历序决定）—— 见 `_hash_display`。
    """
    out: Dict[Tuple[str, str], str] = {}
    for k, v in (known or {}).items():
        head, sep, tail = str(k).rpartition("#")
        if not sep or not tail.isdigit() or not head or not v:
            continue
        key = (str(v), head)
        cur = out.get(key)
        if cur is None or int(tail) < int(cur.rpartition("#")[2]):
            out[key] = str(k)
    return out


def _hash_display(bare: str, uid: str, idx: int, used: set,
                  reuse: Dict[Tuple[str, str], str], known: Dict[str, str]) -> str:
    """无判别字段时的兜底显示名：**优先复用映射表里这个人的既有编号**，其次取空号。

    为什么必须复用（这是本模块唯一的"跨批次"状态）：编号按出现顺序分配时，
    同一批人换个顺序就会把 `李四#1` 指到另一个人身上 —— 表里那条随即被判为冲突而**撤回**，
    于是模型历史里那个名字**再也解不开**，只能回落按名解析（同名时正是静默错人）。
    实测：批次1 `[甲,乙]` → `#1→甲、#2→乙`；批次2 `[乙,甲]` → 两条键双双被撤回。

    复用之后编号只与**这个人**绑定；第一次见的人取一个既没被本批占用、
    也没被表里别人占用的最小号。
    """
    hit = reuse.get((str(uid), str(bare)))
    if hit and hit not in used:
        return hit
    k = max(1, int(idx))
    while True:
        cand = f"{bare}#{k}"
        if cand not in used and not _s((known or {}).get(cand)):
            return cand
        k += 1


def _pair(d: Any) -> Optional[Tuple[str, str]]:
    """一个 ref 字典 → `(真名, unitID)`；形状不合法返回 None。

    **只认同时带 `name` 与 `unit_id` 的字典** —— 这是 C# 侧 `{name, unit_id}` 的约定
    （见 `UnitSnapshot` 的三处生产者）。缺一个都不收：那说明这条不是"一个人的引用"。
    """
    if not isinstance(d, dict):
        return None
    nm, uid = _s(d.get("name")), _s(d.get("unit_id"))
    if not nm or not uid:
        return None
    return nm, uid


# ---------------------------------------------------------------------------
# 载荷 → ref 列表（每个工具的结果形状在这里收敛成一处）
# ---------------------------------------------------------------------------

def walk_refs(node: Any, push) -> None:
    """深度遍历，把每个 `{name, unit_id}` 交给 `push`（形状与 C# 的 `CollectRefs` 对齐）。

    为什么用"深度遍历"而不是逐个容器写死：关系网有 12 个容器 + `married` + 好友/仇人簿，
    逐个列名字，将来 C# 新增一个容器（或换个键名）这边就会**静默漏收** ——
    而漏收的表现是"那个人查得到、指不到"（模型抄了名字，注入不了 id）。
    """
    if isinstance(node, dict):
        if _pair(node) is not None:
            push(node)
        for v in node.values():
            walk_refs(v, push)
    elif isinstance(node, list):
        for x in node:
            walk_refs(x, push)


def _collect(tool: str, res: Any) -> List[Tuple[Dict[str, Any], str, str, List[str], bool]]:
    """`(持有 dict, 真名, unitID, 判别字段, 是否来自同名候选块)` 列表（保持出现顺序）。"""
    out: List[Tuple[Dict[str, Any], str, str, List[str], bool]] = []
    if not isinstance(res, dict):
        return out
    data = res.get("data")
    if not isinstance(data, dict):
        return out

    def push(d: Any, amb: bool = False, bits: Optional[List[str]] = None) -> None:
        p = _pair(d)
        if p:
            out.append((d, p[0], p[1], bits if bits is not None else _bits_with_fallback(d), amb))

    if tool == "inspect_unit":
        amb = data.get("ambiguous")
        cands = [c for c in (amb.get("candidates") or []) if isinstance(c, dict)] \
            if isinstance(amb, dict) else []
        # 被查者本人：判别字段优先**照抄候选块里他那一条**（C# 给的 realm/sect 与候选同源，
        # 比从 data 顶层凑更一致；只查 relationships 时 data 顶层根本没有境界/宗门）。
        mine = _s(data.get("unit_id"))
        same = next((c for c in cands if _s(c.get("unit_id")) == mine and mine), None)
        push(data, bits=_bits(same) if same is not None else None)
        for c in cands:
            push(c, amb=True)                     # 同名候选：全都要唯一化
        # 关系网（classes=relationships）：12 个容器 + `married` + 好友/仇人簿，
        # 形状统一是 `{name, unit_id}` —— 这一块是"模型最常拿来当 target 的人"的来源。
        walk_refs(data.get("relationships"), push)
    elif tool == "search_units":
        items = data.get("items")
        if isinstance(items, list):
            for it in items:
                push(it)
    # 其它工具的结果里没有"人的引用"（`social_relation` 的 data["target"] 只有名字、没有 id）
    return out


def annotate(tool: str, res: Any,
             known: Optional[Dict[str, str]] = None,
             known_ambiguous: Optional[Iterable[str]] = None
             ) -> Tuple[List[Tuple[str, str, bool]], Set[str]]:
    """就地给载荷里**需要区分**的条目打 `display`。

    返回 `(entries, ambiguous_bare)`：
      · `entries` = `[(映射键, unitID, 是否显示名)]`，调用方据此登记；
      · `ambiguous_bare` = 本次判定为**重名**的裸名集合 —— 调用方必须据此
        **撤回**已登记的裸名键（"以前只知道一个李四、现在知道有两个"）并把它们记进
        "已知重名"，否则下一批单独遇到其中一个时又会把裸名登记回去。

    需要区分（= 必须用显示名）的四种情形，任一命中即算：
      ① 同一批里同名但 unitID 不同（`search_units` 一次返回两个「李四」）；
      ② 来自 C# 的**同名候选块**（`ambiguous`：全图扫描确认有多人同名）；
      ③ 已有映射里那个名字指向**另一个人**（跨批次才发现的重名）；
      ④ 这个名字**此前已经被判定为重名**（`known_ambiguous`）—— 之后单独再遇到他一次，
         也不能把裸名登记回去（登记谁都是错的）。

    不需要区分的（绝大多数）返回裸名 —— 显示名只在确有冲突时出现，
    否则模型的输入里会平白多出 `(宗门·境界)` 这种噪音。

    `known` = 已有映射（真名 → unitID），`known_ambiguous` = 已知重名的裸名，都只读。
    """
    refs = _collect(tool, res)
    if not refs:
        return [], set()
    known = known or {}
    amb_known = {_s(x) for x in (known_ambiguous or ()) if _s(x)}

    groups: Dict[str, List[int]] = {}
    entities: Dict[Tuple[str, str], List[int]] = {}
    for i, (_h, bare, uid, _bits_, _amb) in enumerate(refs):
        groups.setdefault(bare, []).append(i)
        entities.setdefault((bare, uid), []).append(i)   # 同一个人在一批里可能出现两次
                                                         # （如 inspect 的档案头 + 候选块里他那一条）

    need: Dict[str, bool] = {}
    for bare, idxs in groups.items():
        uids = {refs[i][2] for i in idxs}
        amb = any(refs[i][4] for i in idxs)
        clash = bool(known.get(bare)) and known.get(bare) not in uids
        need[bare] = (len(uids) > 1) or amb or clash or (bare in amb_known)

    reuse = _hash_reuse(known)
    out: List[Tuple[str, str, bool]] = []
    used: set = set()
    for bare, idxs in groups.items():
        if not need[bare]:
            i = idxs[0]
            out.append((bare, refs[i][2], False))
            used.add(bare)
            continue
        # ★ 按 **(真名, unitID)** 分组，而不是按 ref 逐条：同一个人在同一批里出现两次
        #   （档案头 + 候选块）必须得到**同一个**显示名，否则会多出一个 `名#2` 指向同一个人，
        #   渲染出来的"另一位是…"里就会出现他自己（模型会去查一个不存在的人）。
        mine = [(b, u) for (b, u) in entities if b == bare]
        mine.sort(key=lambda p: min(entities[p]))        # 保持出现顺序
        for n, ent in enumerate(mine, start=1):
            holders = entities[ent]
            bits = refs[holders[0]][3]
            disp = _display(bare, bits, n)
            # 判别字段撞了（同名同宗门）或压根没有判别字段 ⇒ 退到 `名#序号`。
            # ★ 那里**必须查表复用**，否则换个批次顺序就把同一个编号指到另一个人身上，
            #   两条键双双被撤回、模型历史里的显示名再也解不开（见 `_hash_display`）。
            if not bits or disp in used:
                disp = _hash_display(bare, ent[1], n, used, reuse, known)
            used.add(disp)
            for i in holders:
                refs[i][0]["display"] = disp              # ★ 渲染侧优先读它
            out.append((disp, ent[1], True))
    return out, {bare for bare, flag in need.items() if flag}


def ref_names(tool: str, res: Any) -> Iterable[str]:
    """载荷里出现过的**真名**（诊断/日志用，不含 id）。"""
    return (bare for _h, bare, _u, _b, _a in _collect(tool, res))


# ---------------------------------------------------------------------------
# 注入侧：模型写的名字 → `<键>_id`
# ---------------------------------------------------------------------------

# **会"指人"的工具 → ((参数键, 注入键), …)**。这张表本身就是契约：
# 只有这里列出的工具才注入 id；模型看不到任何 `<键>_id`（它们不在 schema 里，
# 由 `test_tool_contract_consistency.py` 的 `CS_ONLY_ALLOWED` 钉住）。
# 依赖的工具**不在表里** = 那个工具压根没有指人参数（`search_units` / `query_world`）。
PERSON_ARGS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "inspect_unit": (("target", "unit_id"),),
    "social_relation": (("target", "target_id"),),
    "movement": (("target", "target_id"),),
    "world_ai_action": (("target", "target_id"),),
    "economy_item": (("target", "target_id"),),
    "item_acquire": (("target", "target_id"),),
    "trade": (("seller", "seller_id"), ("buyer", "buyer_id")),
    # `team.target` 留空在 **status/dismiss** 下语义特殊（dismiss 留空 = 解散整队，
    # 不是"玩家"），但 `inject_ids` 对空串直接 continue ⇒ 不会错注入；填了名字才注入 id。
    "team": (("target", "target_id"),),
}

# **harness 专用、模型一个字都不许提供的参数**：全部注入键 + 发起方两键。
#
# 为什么要主动抹掉而不是"忽略"：C# 侧 **id 优先于名字**（`ResolveArgExact`），所以模型
# 一旦自己写了一个 `<键>_id`，它就会**压过它自己写的名字**；而那条路的失败形态是
# "落到另一个人身上、`success` 依然为真" —— 模型与玩家都察觉不到。这些键不在任何
# 工具 schema 里（`additionalProperties: false`），正常模型不会写；但函数调用协议并不
# 强制校验，而模型是可能被玩家的话带偏的（提示注入）。所以入口处一律清掉，再由
# harness 按映射表重新注入。
RESERVED_ARG_KEYS: Tuple[str, ...] = tuple(
    sorted({idkey for pairs in PERSON_ARGS.values() for (_arg, idkey) in pairs}
           | {"initiator", "initiator_id"})
)


def strip_reserved_args(args: Dict[str, Any]) -> List[str]:
    """抹掉模型自带的 harness 专用键（就地）。返回被抹掉的键名（调用方打日志用）。"""
    out: List[str] = []
    if not isinstance(args, dict):
        return out
    for k in RESERVED_ARG_KEYS:
        if k in args:
            args.pop(k, None)
            out.append(k)
    return out


def inject_ids(tool: str, args: Dict[str, Any], lookup) -> List[str]:
    """就地注入精确身份：`target` → `target_id`、`seller` → `seller_id`…（模型看不到这些键）。

    `lookup(原始串) -> unitID`（空串 = 不知道）。两条纪律：
      · **只在查得到时才写那个键** —— 绝不写空串（空串会让抓包/日志看起来"知道但为空"，
        而且 C# 的"id 不行就回落按名"要能区分"没给"与"给了个空"）；
      · **显示名一律归一成真名**（`args[key] = bare`）：判据（在场检查）、日志、C# 的按名兜底
        全都继续吃真名，显示名只是模型与 harness 之间的一个信封。

    返回注入记录（`["target→target_id"]`，调用方打日志用）。
    """
    out: List[str] = []
    if not isinstance(args, dict):
        return out
    for key, idkey in PERSON_ARGS.get(tool, ()):
        raw = _s(args.get(key))
        if not raw:
            continue
        bare, suffix = split_display(raw)
        uid = _s(lookup(raw))
        if bare and suffix and bare != raw:
            args[key] = bare                      # 信封拆掉，真名留给下游
        if uid and not _s(args.get(idkey)):
            args[idkey] = uid
            out.append(f"{key}→{idkey}")
    return out
