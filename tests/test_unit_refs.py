"""人名引用层：**唯一化显示名 + 临时映射表 + 注入 + 落盘**（09-17 用户拍板）

## 它守的是什么

**模型看到、写出的永远只有名字**；unitID 只活在 harness 侧：

    查询结果（C# 带 name+unit_id）
        → unit_refs.annotate  ← 顺带登记进映射表；重名者就地打上 display
        → text_render         ← 把 id 全部抹掉，模型只看到名字
        → 模型写 target="益婉容(化神殿·结晶后期)"
        → unit_refs.inject_ids ← 拆信封、查表、注入 target_id
        → C#：id 优先，失效才回落按名（ResolveArgExact）

## 为什么重名要给"显示名"而不是给 id

原实现把 id 交给模型（`益婉容(Xs6JDI)` + `inspect_unit(unit_id=…)`），那是对 D.1
重名事故的修复，但代价是模型会自己编 id、把 id 当名字念。改法：**唯一化显示名** =
`益婉容(化神殿·结晶后期)`，它既是渲染出来的字符串、也是映射表的键 ——
模型照抄即可精确指人，而它看到的仍然只是"名字"。

## 为什么映射表必须落盘

模型的历史里留着显示名。钥匙（本表）只在内存的话，重进游戏后那个显示名**再也解不开**，
只能退回裸名按名猜 —— 正是显示名要避免的事，而且静默。落盘时机跟游戏存档
（`save_happened → AgentLoop.flush_all`），与会话账本同寿命。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_loop.agent.dialogue_agent import DialogueAgent
from agent_loop.session.session import Session
from agent_loop.tools import text_render, unit_refs
from agent_loop.storage.unit_id_map import UnitIdMapService

ROOT = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════
# ① 纯逻辑：显示名 / 拆信封
# ══════════════════════════════════════════════════════════════════

def test_split_display_roundtrip():
    assert unit_refs.split_display("益婉容") == ("益婉容", "")
    assert unit_refs.split_display("益婉容(化神殿·结晶后期)") == ("益婉容", "化神殿·结晶后期")
    assert unit_refs.split_display("益婉容#2") == ("益婉容", "#2")
    assert unit_refs.bare_name("益婉容(化神殿)") == "益婉容"
    assert unit_refs.bare_name(" 李四 ") == "李四"
    # 名字里本来就有括号（极少见）也不会被误拆：只认**结尾**那一组
    assert unit_refs.bare_name("阿(小)婉") == "阿(小)婉"


def test_annotate_single_ref_stays_bare():
    """不需要区分时不加后缀 —— 模型的输入里不该平白多出 `(宗门·境界)` 噪音。"""
    res = {"success": True, "data": {"name": "李四", "unit_id": "L1"}}
    assert unit_refs.annotate("inspect_unit", res) == ([("李四", "L1", False)], set())
    assert "display" not in res["data"]


def test_annotate_batch_duplicate_gets_unique_names():
    """同一批里同名两个人（search_units 一次返回两个李四）→ 各给一个唯一名。"""
    res = {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L1", "sect": "化神殿", "realm": "金丹"},
        {"name": "李四", "unit_id": "L2", "sect": "玄天宗", "realm": "元婴"},
        {"name": "王五", "unit_id": "W1"},
    ]}}
    out, amb = unit_refs.annotate("search_units", res)
    assert amb == {"李四"}
    assert out == [("李四(化神殿·金丹)", "L1", True),
                   ("李四(玄天宗·元婴)", "L2", True),
                   ("王五", "W1", False)]
    items = res["data"]["items"]
    assert items[0]["display"] == "李四(化神殿·金丹)" and items[1]["display"] == "李四(玄天宗·元婴)"
    assert "display" not in items[2]


def test_annotate_ambiguous_candidates_and_header_share_one_name():
    """`inspect_unit` 的同名歧义：档案头与候选块里**是同一个人**，必须拿到同一个显示名
    （否则会多出一个 `名#2` 指向他自己，渲染出来的"另一位是…"里就会出现他本人）。"""
    res = {"success": True, "data": {
        "name": "益婉容", "unit_id": "A1", "realm": "结晶后期", "sect": "化神殿",
        "ambiguous": {"target": "益婉容", "matched": 2, "used": "A1", "candidates": [
            {"name": "益婉容", "unit_id": "A1", "realm": "结晶后期", "sect": "化神殿"},
            {"name": "益婉容", "unit_id": "B2", "realm": "登仙境", "sect": "玄天宗"},
        ]}}}
    out, amb = unit_refs.annotate("inspect_unit", res)
    assert amb == {"益婉容"}
    assert out == [("益婉容(化神殿·结晶后期)", "A1", True),
                   ("益婉容(玄天宗·登仙境)", "B2", True)]
    assert res["data"]["display"] == "益婉容(化神殿·结晶后期)"
    cands = res["data"]["ambiguous"]["candidates"]
    assert cands[0]["display"] == "益婉容(化神殿·结晶后期)"
    assert cands[1]["display"] == "益婉容(玄天宗·登仙境)"


def test_annotate_uses_index_when_no_discriminator():
    """一个判别字段都拿不到（关系簿只有 name+unit_id）→ 序号兜底，仍然唯一。"""
    res = {"success": True, "data": {"relationships": {"friend_units": [
        {"name": "张三", "unit_id": "Z1"}, {"name": "张三", "unit_id": "Z2"}]}}}
    out, amb = unit_refs.annotate("inspect_unit", res)
    assert out == [("张三#1", "Z1", True), ("张三#2", "Z2", True)] and amb == {"张三"}


def test_annotate_cross_batch_clash_and_known_ambiguous():
    """跨批次才发现的重名（③）与"此前已判重名"（④）都走显示名。"""
    res = {"success": True, "data": {"items": [{"name": "李四", "unit_id": "L9", "sect": "玄天宗"}]}}
    out, amb = unit_refs.annotate("search_units", res, known={"李四": "L1"})
    assert out == [("李四(玄天宗)", "L9", True)] and amb == {"李四"}
    res2 = {"success": True, "data": {"items": [{"name": "李四", "unit_id": "L1", "sect": "化神殿"}]}}
    out2, amb2 = unit_refs.annotate("search_units", res2, known_ambiguous={"李四"})
    assert out2 == [("李四(化神殿)", "L1", True)] and amb2 == {"李四"}


def test_annotate_hash_name_is_stable_across_batches():
    """★ `名#序号` 兜底必须**跨批次绑定到人**，不能按出现顺序发。

    真机形态：关系簿的引用只有 `{name, unit_id}`（没有宗门/境界，拿不到判别字段），
    而"谁排在前面"由 C# 的遍历序决定 —— 换个顺序，按出现顺序发的编号就会指到另一个人，
    表里那两条键随即双双被判冲突而**撤回**，模型历史里的显示名**再也解不开**
    （只剩回落按名解析 ⇒ 同名时静默错人）。这条钉的就是"编号跟着人走"。
    """
    def payload(uids):
        return {"success": True, "data": {"name": "张三", "unit_id": "ME",
                "relationships": {"sworn": [{"name": "李四", "unit_id": u} for u in uids]}}}

    known, amb = {}, set()
    li = lambda out: [e for e in out if e[0].startswith("李四")]     # 档案头（张三）不参与本断言
    out1, _ = unit_refs.annotate("inspect_unit", payload(["AAA", "BBB"]), known, amb)
    assert li(out1) == [("李四#1", "AAA", True), ("李四#2", "BBB", True)]
    known = {"李四#1": "AAA", "李四#2": "BBB"}
    amb = {"李四"}
    # 同一批人，顺序反过来：编号必须**跟着人**（否则两条键都会被撤回）
    out2, _ = unit_refs.annotate("inspect_unit", payload(["BBB", "AAA"]), known, amb)
    assert li(out2) == [("李四#2", "BBB", True), ("李四#1", "AAA", True)], out2
    # 只见到其中一个人：仍然沿用他自己的编号（不会把 #1 发给另一个人）
    out3, _ = unit_refs.annotate("inspect_unit", payload(["BBB"]), known, amb)
    assert li(out3) == [("李四#2", "BBB", True)], out3
    # 表里没有编号可用（表空）+ 表里 #1 已被别人占用 ⇒ 取一个没被占用的号
    out4, _ = unit_refs.annotate("inspect_unit", payload(["CCC"]), {"李四#1": "AAA"}, amb)
    assert li(out4) == [("李四#2", "CCC", True)], out4


def test_harvest_marks_the_bare_name_not_the_display_key(tmp_path):
    """冲突时记进"已知重名"的必须是**裸名**（那张表的口径），不能是 `名(宗门)` / `名#1`。"""
    a = _agent(tmp_path)
    a._harvest_ids("search_units", {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L1", "sect": "化神殿"}]}})
    a._harvest_ids("search_units", {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L2", "sect": "玄天宗"}]}})
    # 第二次的键是 `李四(玄天宗)`（判别字段不同 → 不冲突）；再造一次真冲突：
    a._harvest_ids("search_units", {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L3", "sect": "玄天宗"}]}})
    assert "李四" in a.ambiguous_names
    assert not [x for x in a.ambiguous_names if "(" in x or "#" in x], \
        f"已知重名表里混进了显示名：{sorted(a.ambiguous_names)}"


# ══════════════════════════════════════════════════════════════════
# ② 注入：每个工具注到哪个键
# ══════════════════════════════════════════════════════════════════

def test_inject_ids_per_tool_keys():
    """指人键 → 注入键的对应表就是契约；不在表里的工具一个键都不加。"""
    m = {"甲": "U1", "乙": "U2"}
    a = {"target": "甲", "op": "add_intim"}
    assert unit_refs.inject_ids("social_relation", a, m.get) == ["target→target_id"]
    assert a["target_id"] == "U1"
    b = {"target": "甲"}
    assert unit_refs.inject_ids("inspect_unit", b, m.get) == ["target→unit_id"]
    assert b["unit_id"] == "U1"          # inspect 的注入键仍是 unit_id（C# 一直在读它）
    c = {"seller": "甲", "buyer": "乙", "item": "剑", "price": 1}
    assert unit_refs.inject_ids("trade", c, m.get) == ["seller→seller_id", "buyer→buyer_id"]
    assert (c["seller_id"], c["buyer_id"]) == ("U1", "U2")
    # 没有指人参数的工具：一个字都不加
    d = {"topic": "events", "count": 3}
    assert unit_refs.inject_ids("query_world", d, m.get) == []
    assert "target_id" not in d and "unit_id" not in d


def test_inject_ids_never_writes_empty_and_never_overwrites():
    """查不到 → 不写那个键（空串会让"C# 回落按名"分不清"没给"与"给了个空"）；
    已有 `*_id` → 不覆盖（幂等，重复过闸门不会改值）。"""
    a = {"target": "查无此人"}
    assert unit_refs.inject_ids("social_relation", a, lambda _s: "") == []
    assert "target_id" not in a
    b = {"target": "甲", "target_id": "KEEP"}
    assert unit_refs.inject_ids("social_relation", b, lambda _s: "U1") == []
    assert b["target_id"] == "KEEP"


def test_inject_ids_unwraps_display_name():
    """显示名是"模型 ⇄ harness"的信封：注入时**拆掉**，真名留给判据/日志/C# 按名兜底。"""
    a = {"target": "益婉容(化神殿·结晶后期)", "op": "add_intim"}
    seen = []

    def lookup(s):
        seen.append(s)
        return {"益婉容(化神殿·结晶后期)": "A1"}.get(s, "")

    assert unit_refs.inject_ids("social_relation", a, lookup) == ["target→target_id"]
    assert a["target"] == "益婉容" and a["target_id"] == "A1"
    assert seen == ["益婉容(化神殿·结晶后期)"], "查表要用**原始串**（显示名就是键）"


# ══════════════════════════════════════════════════════════════════
# ③ 渲染：模型侧一个 id 都不许出现
# ══════════════════════════════════════════════════════════════════

INSPECT_RES = {"success": True, "data": {
    "name": "益婉容", "unit_id": "A1", "sex": "女", "realm": "结晶后期", "sect": "化神殿",
    "ambiguous": {"target": "益婉容", "matched": 2, "used": "A1", "candidates": [
        {"name": "益婉容", "unit_id": "A1", "realm": "结晶后期", "sect": "化神殿"},
        {"name": "益婉容", "unit_id": "B2", "realm": "登仙境", "sect": "玄天宗"}]},
    "relationships": {"friend_units": [{"name": "李四", "unit_id": "L1"},
                                       {"name": "王五", "unit_id": "W1"}]},
}}


def test_render_never_prints_unit_ids():
    """渲染出来的文本里不许出现任何 unitID —— 这是"模型不接触 id"的**唯一**执行点。"""
    unit_refs.annotate("inspect_unit", INSPECT_RES)
    out = text_render.render("inspect_unit", {"target": "益婉容"}, INSPECT_RES)
    for uid in ("A1", "B2", "L1", "W1"):
        assert uid not in out, f"渲染结果里漏出了 unit_id={uid}"
    assert "益婉容(化神殿·结晶后期)" in out, "同名者要给唯一名（模型据此改查另一位）"
    assert "李四" in out and "王五" in out
    # 搜索结果同样不许带 id
    sres = {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L1", "relation": "好友", "intim": 10, "sect": "化神殿"}],
        "total": 1, "filters": {}}}
    unit_refs.annotate("search_units", sres)
    sout = text_render.render("search_units", {"filters": {}}, sres)
    assert "L1" not in sout and "李四" in sout


def test_schemas_have_no_id_parameters():
    """**工具 schema 里不许有 id 参数**（模型能填的只有名字）。"""
    from agent_loop.tools.schemas import ALL_TOOL_SCHEMAS

    for s in ALL_TOOL_SCHEMAS:
        props = (s.get("function") or {}).get("parameters", {}).get("properties", {})
        for k in props:
            assert not k.endswith("_id"), f"{s['function']['name']} 的 schema 里有 id 参数：{k}"
        blob = json.dumps(s, ensure_ascii=False)
        assert "unit_id" not in blob, f"{s['function']['name']} 的描述里还在讲 unit_id"


def test_prompt_does_not_tell_the_model_to_use_ids():
    txt = (ROOT / "prompts/sections/tool_usage.txt").read_text(encoding="utf-8")
    assert "认人认 unit_id" not in txt and "unit_id" not in txt, \
        "提示词里不许再教模型用 id（那是 09-13 的老口径，已反转）"


# ══════════════════════════════════════════════════════════════════
# ④ agent 层：注入 / 收集 / 与 store 的接线
# ══════════════════════════════════════════════════════════════════

def _agent(tmp_path, name: str = "林婉清") -> DialogueAgent:
    from agent_loop.agent.system_prompt import SystemPrompt

    return DialogueAgent(session=Session(id=name, header={"id": name}),
                         system_prompt=SystemPrompt.instance())


def test_agent_injection_covers_self_player_and_roster(tmp_path):
    """映射表的四个来源：自己 / 玩家 / 同席（群成员）—— 单聊与群聊都是这一个入口。"""
    a = _agent(tmp_path)
    a.set_unit_id("ME01")
    a.set_player("韩立", "PL01")
    a.set_roster(["李四", "王五"], {"李四": "L1"})      # 王五没登记 → 缺项很正常
    args = {"seller": "韩立", "buyer": "林婉清", "item": "剑", "price": 1}
    a._inject_person_ids("trade", args)
    assert args["seller_id"] == "PL01", "玩家那一格（单聊也要有）"
    assert args["buyer_id"] == "ME01", "我自己那一格"
    t = {"target": "李四"}
    a._inject_person_ids("social_relation", t)
    assert t["target_id"] == "L1", "同席/群成员那一格"
    u = {"target": "王五"}
    a._inject_person_ids("social_relation", u)
    assert "target_id" not in u, "表里没有的人不注入（C# 回落按名）"


def test_agent_harvest_feeds_map_and_marks_duplicates(tmp_path):
    """查询结果 → 映射表；重名者的裸名**不登记**（登记谁都是错的）。"""
    a = _agent(tmp_path)
    res = {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L1", "sect": "化神殿"},
        {"name": "李四", "unit_id": "L2", "sect": "玄天宗"},
        {"name": "王五", "unit_id": "W1"}]}}
    a._harvest_ids("search_units", res)
    assert a.unit_id_map == {"李四(化神殿)": "L1", "李四(玄天宗)": "L2", "王五": "W1"}
    assert "李四" not in a.unit_id_map, "重名者的裸名不许登记"
    assert "李四" in a.ambiguous_names
    # 模型照抄唯一名 → 精确命中；只写裸名 → 什么都不注入
    ok = {"target": "李四(玄天宗)"}
    a._inject_person_ids("social_relation", ok)
    assert ok["target_id"] == "L2" and ok["target"] == "李四"
    bad = {"target": "李四"}
    a._inject_person_ids("social_relation", bad)
    assert "target_id" not in bad


def test_agent_harvest_reports_collision_and_withdraws(tmp_path):
    """同一个键第二次指向另一个人 ⇒ 撤回该键并记为已知重名（此后走显示名）。"""
    a = _agent(tmp_path)
    a._harvest_ids("search_units", {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L1", "sect": "化神殿"}]}})
    assert a.unit_id_map["李四"] == "L1"
    a._harvest_ids("search_units", {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L2", "sect": "玄天宗"}]}})
    assert "李四" not in a.unit_id_map, "冲突的键必须撤回"
    assert a.unit_id_map["李四(玄天宗)"] == "L2"
    assert "李四" in a.ambiguous_names


def test_agent_shares_the_world_store(tmp_path):
    """接上 store 之后，agent 写的就是**世界级那一份**（所有 agent 共享、换档整表换）。"""
    st = UnitIdMapService(str(tmp_path))
    a, b = _agent(tmp_path, "甲"), _agent(tmp_path, "乙")
    a.set_identity_store(st)
    b.set_identity_store(st)
    a._harvest_ids("search_units", {"success": True, "data": {"items": [
        {"name": "李四", "unit_id": "L1"}]}})
    assert b.unit_id_map.get("李四") == "L1", "另一个 agent 立刻看得到（知识是世界级的）"
    assert st.units.get("李四") == "L1"


# ══════════════════════════════════════════════════════════════════
# ⑤ 落盘：跟游戏存档，且按存档隔离
# ══════════════════════════════════════════════════════════════════

def test_store_roundtrip_and_atomic_file(tmp_path):
    st = UnitIdMapService(str(tmp_path))
    st.set_world("WORLD1")
    st.remember("李四", "L1")
    st.remember("李四(化神殿)", "L1")
    st.mark_ambiguous("李四")
    assert st.save() == 2
    assert not list(Path(st.root).glob("*.tmp")), "原子写不许留下 tmp"
    st2 = UnitIdMapService(str(tmp_path))
    st2.set_world("WORLD1")
    assert st2.units == {"李四": "L1", "李四(化神殿)": "L1"}
    assert st2.ambiguous == {"李四"}


def test_store_is_per_world(tmp_path):
    """换存档 ⇒ 换目录（目录本身就是隔离）；旧档的表在新档里**不可见**。"""
    a = UnitIdMapService(str(tmp_path))
    a.set_world("WORLD1")
    a.remember("李四", "L1")
    a.save()
    b = UnitIdMapService(str(tmp_path))
    b.set_world("WORLD2")
    assert b.units == {}, "另一个存档的表不许串进来"
    b.remember("李四", "L9")
    b.save()
    # 切回 WORLD1：读到的仍是它自己那份
    a2 = UnitIdMapService(str(tmp_path))
    a2.set_world("WORLD1")
    assert a2.units == {"李四": "L1"}


def test_store_drops_memory_on_world_switch(tmp_path):
    st = UnitIdMapService(str(tmp_path))
    st.set_world("A")
    st.remember("李四", "L1")
    st.set_world("B")
    assert st.units == {} and st.ambiguous == set(), \
        "换档必须丢弃内存表（旧档的 unitID 在新档里可能是别人）"


def test_store_tolerates_garbage_and_never_throws(tmp_path):
    st = UnitIdMapService(str(tmp_path))
    st.set_world("W")
    Path(st.path).parent.mkdir(parents=True, exist_ok=True)
    for junk in ("", "{", "[]", '{"units": "不是字典", "ambiguous": 5}',
                 '{"units": {"": "X", "甲": ""}}'):
        Path(st.path).write_text(junk, encoding="utf-8")
        st2 = UnitIdMapService(str(tmp_path))
        st2.set_world("W")
        assert st2.units == {} and st2.ambiguous == set()


def test_store_does_not_create_empty_file(tmp_path):
    st = UnitIdMapService(str(tmp_path))
    st.set_world("W")
    assert st.save() == 0
    assert not Path(st.path).exists(), "空表不该凭空造文件"


def test_flush_all_writes_the_map(tmp_path):
    """★ 落盘时机 = **游戏存档**（`save_happened → AgentLoop.flush_all`）。"""
    from agent_loop.agent_loop import AgentLoop

    loop = AgentLoop.reset_for_test(storage_root=str(tmp_path))
    loop.set_world("W1", "韩立", "PL01")
    assert loop.player_unit_id == "PL01"
    loop.identity.remember("李四", "L1")
    loop.flush_all()
    on_disk = json.loads(Path(loop.identity.path).read_text(encoding="utf-8"))
    assert on_disk["units"] == {"李四": "L1"}


def test_agent_wiring_from_loop_uses_the_world_store(tmp_path):
    """`AgentLoop.create` 与 `_run_turn` 都要把 agent 接到世界级那一份表上。"""
    from agent_loop.agent_loop import AgentLoop

    loop = AgentLoop.reset_for_test(storage_root=str(tmp_path))
    loop.set_world("W1", "韩立", "PL01")
    agent = loop.create("林婉清")
    assert agent.identity_store is loop.identity
    agent.set_unit_id("ME01")
    agent.set_player(loop.player_name, loop.player_unit_id)
    args = {"target": "韩立"}
    agent._inject_person_ids("world_ai_action", args)
    assert args["target_id"] == "PL01", "玩家那一格来自 set_player（原样 id，不是 sanitize 过的）"


# ══════════════════════════════════════════════════════════════════
# ⑥ 接线点（单测直接调私有方法，抓不到"接线被删"—— 这两条钉的是调用位置本身）
# ══════════════════════════════════════════════════════════════════

def test_model_supplied_reserved_keys_are_stripped(tmp_path):
    """★ 模型自带的 harness 专用键（`*_id` / `initiator*`）必须**抹掉重建**。

    为什么这是正确性而不是洁癖：C# 侧 **id 优先于名字**（`ResolveArgExact`），所以模型
    一旦自己写了个 `target_id`，它就会压过它自己写的名字 —— 而那条路的失败形态是
    "落到另一个人身上、`success` 依然为真"，模型与玩家都看不见。这些键不在任何 schema 里，
    正常模型不写；但函数调用协议不强制校验，提示注入可以把它带出来。
    """
    a = _agent(tmp_path)
    a.set_unit_id("ME01")
    a.set_player("韩立", "PL01")
    a.set_roster(["李四"], {"李四": "L1"})
    seen = {}

    class _B:
        def call_tool(self, name, args, npc_id=None):
            seen.clear()
            seen.update(args)
            return {"success": True, "data": {}}

    a.bridge = _B()
    asyncio.run(a._execute_tool_calls([{"id": "c1", "name": "social_relation",
        "arguments": {"op": "add_intim", "value": 1, "target": "李四",
                      "target_id": "HACK", "initiator": "别人", "initiator_id": "HACK2"}}], 1, 1))
    assert seen["target_id"] == "L1", f"必须由 harness 按映射表重注：{seen}"
    assert seen["initiator"] == a.id and seen["initiator_id"] == "ME01", seen

    # 映射表里查不到的人：模型硬塞的 id 也必须消失（宁可回落按名，也不许它自己指定身份）
    seen.clear()
    asyncio.run(a._execute_tool_calls([{"id": "c2", "name": "social_relation",
        "arguments": {"op": "add_intim", "value": 1, "target": "路人", "target_id": "HACK"}}], 1, 1))
    assert "target_id" not in seen, f"模型塞的 id 漏进了调用：{seen}"


def test_initiator_id_falls_back_to_the_name_map(tmp_path):
    """★ 09-28 修（真机「未找到发起方 管星贤」）★

    前提：主动传音 / 剧情窗是**第三条会话入口**，不经过 `open_chat` ⇒ 本会话
    `agent.unit_id` 为空（这是真机现状，不是构造）。而发起方注入原先**只读**
    `self.unit_id` —— 于是世界级映射表里明明有 `管星贤 → 8DQUkF` 也没人问它，
    `initiator_id` 不注入 ⇒ C# 只能按名猜（那次按名恰好也失败 ⇒ 本回合 5 个
    动作工具全部 `未找到发起方`）。

    现在发起方与 `target/seller/buyer` 走**同一个查表口**（`_lookup_unit_id`）：
    表里有就注入，没有依旧不注入（C# 回落按名，逐字同旧行为）。
    """
    a = _agent(tmp_path, name="管星贤")
    # 模拟"这个身份此前在别处学到过"（查询结果回收 / 上一场会话 —— 表是世界级+落盘的）
    a._harvest_ids("search_units", {"success": True, "data": {"items": [
        {"name": "管星贤", "unit_id": "8DQUkF"}]}})
    assert a._unit_id() == "", "前提：本会话没有自身身份（未经 open_chat）"
    seen = {}

    class _B:
        def call_tool(self, name, args, npc_id=None):
            seen.clear()
            seen.update(args)
            return {"success": True, "data": {}}

    a.bridge = _B()
    asyncio.run(a._execute_tool_calls([{"id": "c1", "name": "social_relation",
        "arguments": {"op": "reduce_intim", "value": 5}}], 1, 1))
    assert seen["initiator"] == "管星贤", seen
    assert seen["initiator_id"] == "8DQUkF", f"发起方身份必须从表里补上：{seen}"

    # 反面：表里没有自己 ⇒ 依旧不注入（不许猜）
    b = _agent(tmp_path, name="无名氏")
    seen.clear()
    b.bridge = a.bridge
    asyncio.run(b._execute_tool_calls([{"id": "c2", "name": "social_relation",
        "arguments": {"op": "reduce_intim", "value": 5}}], 1, 1))
    assert "initiator_id" not in seen, f"表里没有就不该注入：{seen}"


def test_injection_and_harvest_are_wired_at_the_right_places():
    """两个调用点的**位置**就是契约：

      · `_inject_person_ids` 必须在**调桥之前** —— 否则 C# 拿到的只有名字（退回按名扫）；
      · `_harvest_ids` 必须在**渲染之前** —— 渲染会把 id 抹掉，而唯一化显示名要由它
        在渲染前就地写进载荷；顺序反了，模型看到的显示名就与映射表的键对不上，
        那张表**永远用不上**（而且完全静默）。

    没有这条，把两行接线删掉照样"全绿"（单测都在直接调那两个私有方法）—— 变异验证实测。
    """
    src = (ROOT / "agent" / "dialogue_agent.py").read_text(encoding="utf-8")
    body = src[src.index("async def _execute_tool_calls("):]
    nxt = body.find("\n    async def ")
    if nxt > 0:
        body = body[:nxt]
    assert "self._inject_person_ids(name, args)" in body, "注入的接线被删了"
    assert "self._harvest_ids(name, res)" in body, "人名引用收集的接线被删了"
    i_bridge = body.index("res = self.bridge.call_tool(")
    i_harvest = body.index("self._harvest_ids(name, res)")
    i_render = body.rindex("text_render.render(")
    assert body.index("self._inject_person_ids(name, args)") < i_bridge, \
        "注入必须在调桥之前"
    assert i_bridge < i_harvest < i_render, \
        "收集必须在**调桥之后、渲染之前**（显示名要赶在渲染前写进载荷）"
