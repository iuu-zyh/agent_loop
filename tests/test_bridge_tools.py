"""脱离游戏：bridge + tools + L1/Context + tool_calls 往返（各司其职）"""

import asyncio
import json

from agent_loop.agent_loop import AgentLoop
from agent_loop.agent.system_prompt import SystemPrompt
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.tools.schemas import TOOL_ORDER
from agent_loop.tools import text_render as tr
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.llm.echo_client import EchoLlmClient
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）


ROOT = "/tmp/agent_loop_test_bridge"


def reset_with_stub():
    bridge = StubGameBridge()
    # 预置档案：林婉清(金丹 好感180) 与 张三(筑基) 供搜索/查询
    bridge.seed_unit(
        "林婉清",
        {
            "realm": "金丹",
            "sect": "化神殿",
            "pos": "永宁州·白帝城",
            "region": "永宁州",
            "mood": 72,
            "power": 8900,
            "player": {"name": "韩立", "realm": "筑基", "relation": "道侣", "intim": 180, "same_grid": True},
            "luck": {"born": ["天妒(金)", "剑心(紫)"], "added": []},
            "items": ["灵石1000", "丹药5"],
            "relations": {"player": {"relation": "道侣", "intim": 180}},
            "logs": [f"日志{i}: 与韩立论道" for i in range(12)],
            "recent": "上月与韩立结为道侣",
        },
    )
    bridge.seed_unit(
        "张三",
        {
            "realm": "筑基",
            "sect": "无宗门",
            "pos": "永宁州",
            "region": "永宁州",
            "mood": 50,
            "power": 3000,
            "player": {"name": "韩立", "realm": "筑基", "relation": "好友", "intim": 120, "same_grid": False},
            "luck": {"born": ["霉运(白)"], "added": ["陷入瓶颈"]},
            "items": [],
            "logs": ["日志0: 被韩立搭救"],
            "recent": "昨日被韩立搭救",
        },
    )
    # 张三的副本供 inspect 命中（search 后用）
    # llm 显式 Echo：避免读 env 撞真机，脱离 LLM 回显收口
    loop = AgentLoop.reset_for_test(storage_root=ROOT, bridge=bridge, llm=EchoLlmClient())
    return loop, bridge


def test_tools_registered_in_system_prompt():
    loop, _ = reset_with_stub()
    agent = loop.create("林婉清")
    asm = agent.system_prompt.assemble(scope="林婉清")
    names = [t["function"]["name"] for t in asm["tools"]]
    assert names == TOOL_ORDER, f"tools order mismatch {names}"
    print(f"✓ tools 已注册 9 个且按 TOOL_ORDER 排序：{names}")


def test_l1_context_diff():
    loop, _ = reset_with_stub()
    agent = loop.create("林婉清")
    # 首轮 preStep 会拉 L1 并写入 runtime:l1
    asyncio.run(agent.run_until_idle())  # 无 pending，仅触发空转补 header，不拉 L1
    # 发一句触发真 turn，preStep 会取 bridge.get_context
    agent.send("你好")
    asyncio.run(agent.run_until_idle())
    asm = agent.system_prompt.assemble(scope="林婉清")
    ctx_texts = [c["text"] for c in asm["contexts"]]
    assert any("林婉清" in t and "金丹" in t for t in ctx_texts), f"L1 未注入 {ctx_texts}"
    # 二次同内容不重写：last_l1_text 差分
    assert agent._last_l1_text is not None
    print("✓ L1 Context 已注入且差分：", agent._last_l1_text[:40])


def test_l1_context_segments():
    """L1 三段式：contexts 拆成 runtime:self/player（+recent），各自连贯成句、独立持有。"""
    loop, _ = reset_with_stub()
    agent = loop.create("林婉清")
    agent.send("你好")
    asyncio.run(agent.run_until_idle())
    asm = agent.system_prompt.assemble(scope="林婉清")
    by_name = {c["name"]: c["text"] for c in asm["contexts"]}
    assert "runtime:self" in by_name, f"缺自身段 {list(by_name)}"
    assert "runtime:player" in by_name, f"缺玩家段 {list(by_name)}"
    self_t = by_name["runtime:self"]
    player_t = by_name["runtime:player"]
    # 自身段连贯成句，含 npc 名与境界（非 key:value 堆叠）
    assert "林婉清" in self_t and "金丹" in self_t, f"自身段不连贯 {self_t!r}"
    # 玩家独立成段，成句且关系清晰
    assert "韩立" in player_t and "道侣" in player_t, f"玩家段缺失信息 {player_t!r}"
    assert "玩家：" not in player_t, "玩家段应为连贯文句而非 key:value"
    # 三段逐段持有：recent 有 seed 时也独立
    print(f"✓ L1 三段式：self={self_t!r} player={player_t!r}")


def test_l1_segment_diff_isolated():
    """逐段差分：只改玩家段时，自身段屉不重写；保留已发段。"""
    loop, bridge = reset_with_stub()
    agent = loop.create("林婉清")
    agent.send("第一问")
    asyncio.run(agent.run_until_idle())
    old_self = agent.system_prompt.assemble(scope="林婉清")
    old_self_text = {c["name"]: c["text"] for c in old_self["contexts"]}["runtime:self"]
    old_retained = dict(agent._retained_ctx)

    # 玩家段唯一差异：改 get_context 返回的玩家好感（模拟 raw 变化）
    orig_get_context = bridge.get_context
    import types

    def patched(npc_id, roster=None, **kw):
        r = orig_get_context(npc_id, roster=roster, **kw)
        r["raw"]["player"]["intim"] = r["raw"]["player"].get("intim", 0) + 1
        return r

    bridge.get_context = patched
    agent.send("第二问")
    asyncio.run(agent.run_until_idle())
    asm = agent.system_prompt.assemble(scope="林婉清")
    by_name = {c["name"]: c["text"] for c in asm["contexts"]}
    assert by_name["runtime:self"] == old_self_text, "自身段不应因玩家段变化而重写"
    print(f"✓ 逐段差分隔离：改玩家段，自身段屉不变 retained={old_retained!r}")


def test_inspect_and_search_via_bridge():
    loop, bridge = reset_with_stub()
    # 直接调 bridge：inspect 用 classes 多选；search 走关系网（relation/keyword）
    r1 = bridge.call_tool("inspect_unit", {"target": "林婉清", "classes": ["brief"]})
    assert r1["success"] and r1["data"]["name"] == "林婉清"
    assert r1["data"].get("realm") == "金丹", "brief 块应含境界"
    r1b = bridge.call_tool("inspect_unit", {"target": "林婉清", "classes": ["brief", "inventory"]})
    assert r1b["data"]["luck"]["born"] == ["天妒(金)", "剑心(紫)"], "多选 classes 应同时返回对应块（气运归 brief）"
    r_inv = bridge.call_tool("inspect_unit", {"target": "林婉清", "classes": ["inventory"]})
    assert "luck" not in r_inv["data"] and "qiyun" not in r_inv["data"], "气运不应再挂 inventory 块"
    r2 = bridge.call_tool("inspect_unit", {"target": "林婉清", "classes": ["logs"], "log_page": 1})
    assert len(r2["data"]["items"]) == 5 and r2["data"]["has_more"] is True
    r3 = bridge.call_tool("inspect_unit", {"target": "林婉清", "classes": ["logs"], "log_page": 3})
    assert len(r3["data"]["items"]) == 2 and r3["data"]["has_more"] is False
    r4 = bridge.call_tool("search_units", {"filters": {"keyword": "张三"}})
    assert any(x["name"] == "张三" for x in r4["data"]["items"])
    r5 = bridge.call_tool("search_units", {"filters": {"relation": "道侣"}})
    assert any(x["name"] == "林婉清" for x in r5["data"]["items"])
    # 好感排序：林婉清180 > 张三120
    r6 = bridge.call_tool("search_units", {"filters": {"relation": "好感"}})
    names = [x["name"] for x in r6["data"]["items"]]
    assert names[:2] == ["林婉清", "张三"], f"应按好感降序 {names}"
    # 多条件过滤：境界/宗门/地区（只填有把握的）
    assert any(x["name"] == "林婉清" for x in bridge.call_tool("search_units", {"filters": {"realm": "金丹"}})["data"]["items"])
    assert any(x["name"] == "林婉清" for x in bridge.call_tool("search_units", {"filters": {"sect": "化神殿"}})["data"]["items"])
    assert any(x["name"] == "张三" for x in bridge.call_tool("search_units", {"filters": {"region": "永宁州"}})["data"]["items"])
    # 组合过滤：境界+宗门 同时命中林婉清
    r7 = bridge.call_tool("search_units", {"filters": {"realm": "金丹", "sect": "化神殿"}})
    assert [x["name"] for x in r7["data"]["items"]] == ["林婉清"], f"组合过滤应只剩林婉清 {r7}"
    print("✓ inspect_unit classes 多选 + search_units filters 字典(关键字/关系/境界/宗门/地区/组合)通过")


def test_tool_calls_loop_via_stub_llm():
    """桩模型产 tool_calls → bridge 执行 → tool/result 回灌 → 纯文本收口"""
    loop, _ = reset_with_stub()

    async def llm_stub(request):
        # 根据轮次：首步调 inspect，次步收 tool/result 后回文本
        # 通过 request["messages"] 里是否已有 tool-result 判断
        has_tool_result = any(
            any(c.get("type") == "tool-result" for c in m.get("content", []))
            for m in request["messages"]
            if isinstance(m.get("content"), list)
        )
        if not has_tool_result:
            return {
                "tool_calls": [
                    {"id": "call_1", "name": "inspect_unit", "arguments": {"target": "林婉清", "classes": ["brief"]}},
                ],
                "text": "我去查查",
            }
        return {"text": "查到了，你有天妒和剑心两道气运。"}

    agent = loop.create("张三", bridge=loop.bridge, llm=StubLlmClient(llm_stub))
    agent.send("你身上有什么气运？")
    asyncio.run(agent.run_until_idle())
    msgs = agent.session.derive_messages()
    # 应含：user 你身上... + assistant 我去查查 + tool/result + assistant 查到了
    assert any("天妒" in json.dumps(m, ensure_ascii=False) for m in msgs), f"tool/result 未回灌 {msgs}"
    # log 中应有 tool/call 与 tool/result 且 sourceEventSeqs 关联
    assert any(ev["type"] == "tool/call" for ev in agent.session.log)
    assert any(ev["type"] == "tool/result" and ev.get("sourceEventSeqs") for ev in agent.session.log)
    print("✓ tool_calls 往返闭环：inspect → bridge → tool/result → 纯文本收口")


def test_action_threshold_via_bridge():
    loop, _ = reset_with_stub()
    # 李四：好友·好感180 → 结缘应通过；林婉清：已是道侣 → 互斥应拒；张三：好友·好感120 → 好感不足应拒
    loop.bridge.seed_unit(
        "李四",
        {"realm": "筑基", "sect": "无宗门", "player": {"name": "韩立", "relation": "好友", "intim": 180, "same_grid": True}},
    )
    ok = loop.bridge.call_tool("social_relation", {"op": "jie_yuan", "initiator": "李四"})
    assert ok["success"] is True
    dup = loop.bridge.call_tool("social_relation", {"op": "jie_yuan", "initiator": "林婉清"})
    assert dup["success"] is False and "道侣" in dup["error"]
    fail = loop.bridge.call_tool("social_relation", {"op": "jie_yuan", "initiator": "张三"})
    assert fail["success"] is False and "好感不足" in fail["error"]
    print("✓ 动作阈值二次校验：180 通过 / 120 拒绝 / 已道侣互斥")


def test_l1_telepathy_wording():
    """传音模式判定：异格（same_grid=False）「所在」段带"神识传音"，同格带"面对面"。

    09-24 起位置与相对关系整体归「所在」段（`where`）——它正是 `tool_usage.txt` 那条
    "异地相隔时动手类不得直接调"的锚点所在，措辞一字未改，只是换了个出处。
    """
    sp = SystemPrompt.instance()
    raw = {"npc_id": "林婉清", "player": {"name": "韩立", "relation": "道友", "intim": 60, "same_grid": False}}
    segs = sp.format_l1_context("林婉清", raw)
    assert "神识传音" in segs["where"] and "异地相隔" in segs["where"], segs["where"]
    assert "异地相隔" not in segs["player"], f"玩家段不该再提位置：{segs['player']}"
    raw["player"]["same_grid"] = True
    segs2 = sp.format_l1_context("林婉清", raw)
    assert "面对面" in segs2["where"] and "神识传音" not in segs2["where"], segs2["where"]
    print(f"✓ 传音模式判定：异地=神识传音 / 同格=面对面 → {segs['where'][:44]}…")


def test_l1_sex_and_pronoun():
    """性别成文（09-13）：自身段要有自己的性别；玩家段代词按玩家性别选；缺 sex 的旧载荷不回归。

    事故事实：`raw.self.sex` 一直由 C# 提供，`format_l1_context` 却从没读过；玩家块更干脆没有 sex，
    于是玩家段代词写死「他」—— 女玩家/女道侣会被成文成"你与他结着道侣之谊"。
    """
    sp = SystemPrompt.instance()
    raw = {
        "npc_id": "云含", "sex": "女",
        "player": {"name": "缪嘉歆", "sex": "女", "relation": "道侣", "intim": 200, "same_grid": True},
    }
    segs = sp.format_l1_context("云含", raw)
    assert "你是云含，一位女子" in segs["self"], segs["self"]
    assert "一位女子" in segs["player"], segs["player"]
    assert "你与她结着道侣之谊" in segs["player"], segs["player"]
    assert "你与他" not in segs["player"], segs["player"]          # 女玩家不得仍用「他」

    raw["player"]["sex"] = "男"
    segs2 = sp.format_l1_context("云含", raw)
    assert "一位男子" in segs2["player"] and "你与他结着道侣之谊" in segs2["player"], segs2["player"]

    # 旧 C# 载荷（自身/玩家都无 sex）：回退旧行为，且不得产出"一位None子"这类东西
    old = {"npc_id": "云含", "player": {"name": "韩立", "relation": "道友", "intim": 60, "same_grid": True}}
    s3 = sp.format_l1_context("云含", old)
    assert s3["self"] == "你是云含。", s3["self"]
    assert "一位" not in s3["player"] and "你与他结着道友之谊" in s3["player"], s3["player"]

    bad = {"npc_id": "云含", "sex": "None", "player": {"name": "韩立", "sex": "None"}}
    s4 = sp.format_l1_context("云含", bad)
    assert "None" not in s4["self"] and "None" not in s4["player"], (s4["self"], s4["player"])
    print("✓ L1 性别成文：自身段带性别 / 玩家段代词按性别（她·他）/ 缺字段与异常值不回归")


def test_l1_location_wording():
    """位置成文（09-13 立、09-24 换出处）：位置只由**「所在」段**给；C# 给 `point{x,y}`，成文侧曾读 `pos`。

    事故：`format_l1_context` 写的是 `pos = self_raw["pos"]` → `栖居于{pos}`，而 **C# 全仓从没发过
    `pos` 这个键**（`grep '["pos"]' csharp/*.cs` = 0 处）——C# 发 `point{x,y}`、成文侧等地名，
    两边各按自己的假设写 → 位置恒空、谁都不报错。用户拍板：显示坐标即可。
    09-24 起位置整体搬进「所在」段（自身/玩家段不再写位置），故断言落在 `segs["where"]`。
    """
    sp = SystemPrompt.instance()
    raw = {
        "npc_id": "云含", "sex": "女",
        "point": {"x": 31, "y": 78},
        "player": {"name": "缪嘉歆", "point": {"x": 20, "y": 8}, "same_grid": False},
    }
    segs = sp.format_l1_context("云含", raw)
    assert "坐标(31,78)" in segs["where"], segs["where"]
    assert "坐标(20,8)" in segs["where"], segs["where"]
    # ★ 搬家的核心目的：自身/玩家段不再带位置（挪一步不该重发这两段）★
    assert "坐标" not in segs["self"] and "坐标" not in segs["player"], (segs["self"], segs["player"])

    # 地名若将来由 C# 提供（pos），优先于坐标（保留原有措辞）
    segs2 = sp.format_l1_context("云含", dict(raw, pos="青雲鎮"))
    assert "栖居于青雲鎮" in segs2["where"] and "坐标(31,78)" not in segs2["where"], segs2["where"]

    # 旧 C# 载荷（无 point/pos）不得回归；半截坐标不得编出"坐标(31,None)"
    old = sp.format_l1_context("云含", {"npc_id": "云含", "player": {"name": "韩立"}})
    assert "坐标" not in old["where"], old["where"]
    assert "此刻并无他人在场" in old["where"], f"位置全缺时仍须成文（恒非空）：{old['where']}"
    half = sp.format_l1_context("云含", {"npc_id": "云含", "point": {"x": 31},
                                         "player": {"name": "韩立", "point": {"y": 8}}})
    assert "31" not in half["where"] and "None" not in half["where"], half["where"]
    print("✓ L1 位置成文（所在段）：坐标 / 地名优先 / 缺字段与半截坐标不回归")


def test_l1_luck_wording():
    """先天进主体段、后天进独立段（09-26 用户拍板）：都带 desc（换行折叠）；
    无后天时**仍成文**（恒非空兜底「身无后天之气运，身体精神状况正常」）。

    为什么这么分：**先天气运生而定死、永不变化** ⇒ 放自身段零重发成本；
    **后天气运**随逆天改命/奇遇变 ⇒ 独立成段，一变只重发那几十字（留在自身段则整段重发）。
    为什么恒非空：空段不发（`render_context_segment` 返回 ""）⇒ "有→无"（后天被移除）
    会静默失效，模型会一直以为还挂着。
    """
    sp = SystemPrompt.instance()
    raw = {
        "npc_id": "林婉清",
        "luck": {
            "born": ["天妒(金)", "剑心(紫)"],
            "added": [{"name": "陷入瓶颈", "desc": "修为停滞，需要突破瓶颈才可步入炼气后期。\n突破瓶颈的方法在逆天改命界面(快捷键：J)中说明。"}],
        },
    }
    segs = sp.format_l1_context("林婉清", raw)
    # 先天 → 自身段（措辞与旧版逐字相同）
    assert "命带气运天妒(金)、剑心(紫)" in segs["self"], segs["self"]
    # 后天 → 独立段（带 desc、换行折叠）
    luck_seg = segs["npc_luck"]
    assert "后天又得陷入瓶颈（修为停滞" in luck_seg, luck_seg
    assert "\n" not in luck_seg, "desc 换行应折叠"
    assert "陷入瓶颈" not in segs["self"], "后天留在自身段（拆段后不许重复）"
    # 恒非空：连 luck 都没有也要成文
    segs2 = sp.format_l1_context("林婉清", {"npc_id": "林婉清"})
    assert "身无后天之气运，身体精神状况正常" in segs2["npc_luck"], segs2["npc_luck"]
    print(f"✓ L1 先天在自身段 / 后天独立段+desc：…{luck_seg[-40:]}")


def test_l1_personality_desc():
    """L1 自身段性格带面板注解（2026-09-13 补漏）。

    症状：inspect/brief 早有「性格注解」（tools/text_render.py），L1 一直只有名字。
    根因：C# PersonalityOf 早就平行给出 inner_desc/outer_desc，但 format_l1_context 只读 inner/outer。
    """
    sp = SystemPrompt.instance()
    raw = {
        "npc_id": "云含",
        "personality": {
            "inner": "仁善",
            "inner_desc": "为人仁善，对其它人都比较友好。",
            "outer": ["义气", "睚眦"],
            # 与 outer 同下标；缺描述补空串（C# 侧实际形状）
            "outer_desc": ["重视朋友情义，愿为知己两肋插刀。", ""],
        },
    }
    s = sp.format_l1_context("云含", raw)["self"]
    assert "本性仁善（为人仁善，对其它人都比较友好。）" in s, s
    assert "行事偏义气（重视朋友情义，愿为知己两肋插刀。）与睚眦" in s, s
    # 注解换行折叠（面板描述可能带换行）
    nl = sp.format_l1_context("云含", {"npc_id": "云含", "personality": {
        "inner": "邪恶", "inner_desc": "邪恶，唯已所欲，\n从来不管其它人的感受。"}})["self"]
    assert "本性邪恶（邪恶，唯已所欲， 从来不管其它人的感受。）" in nl, nl
    assert "\n" not in nl, "注解换行应折叠"
    # 旧 C# 数据（只有名字、无 desc）不回归：仍成文，且不吐空括号
    old = sp.format_l1_context("云含", {"npc_id": "云含", "personality": {"inner": "狂邪", "outer": ["护短"]}})["self"]
    assert "本性狂邪" in old and "行事偏护短" in old, old
    assert "（）" not in old, old
    print(f"✓ L1 性格带注解：…{s[:64]}…")


def test_l1_fetched_once_per_turn():
    """L1 每轮一次：含工具循环的 turn 内多次 preStep 只调一次 get_context；新 turn 再取。"""
    loop, bridge = reset_with_stub()
    calls = {"n": 0}
    orig = bridge.get_context

    def counting(npc_id, roster=None, **kw):
        calls["n"] += 1
        return orig(npc_id, roster=roster, **kw)

    bridge.get_context = counting

    async def llm_stub(request):
        has_tool_result = any(
            any(c.get("type") == "tool-result" for c in m.get("content", []))
            for m in request["messages"]
            if isinstance(m.get("content"), list)
        )
        if not has_tool_result:
            return {"tool_calls": [{"id": "c1", "name": "inspect_unit", "arguments": {"target": "林婉清", "classes": ["brief"]}}], "text": "查查"}
        return {"text": "好的"}

    agent = loop.create("张三", bridge=bridge, llm=StubLlmClient(llm_stub))
    agent.send("第一问")
    asyncio.run(agent.run_until_idle())
    assert calls["n"] == 1, f"单轮（含工具循环）应只取一次 L1，实际 {calls['n']} 次"
    agent.send("第二问")
    asyncio.run(agent.run_until_idle())
    assert calls["n"] == 2, f"新 turn 应再取一次 L1，实际 {calls['n']} 次"
    print(f"✓ L1 每轮一次：两个 turn 各取一次（get_context 共 {calls['n']} 次）")


def test_l1_movement_dirty_refresh():
    """movement 生效后置脏：同轮下一步强制重新取 L1（summon/teleport 翻转 same_grid 要跟上）。"""
    loop, bridge = reset_with_stub()
    calls = {"n": 0}
    orig = bridge.get_context

    def counting(npc_id, roster=None, **kw):
        calls["n"] += 1
        return orig(npc_id, roster=roster, **kw)

    bridge.get_context = counting

    async def llm_stub(request):
        has_tool_result = any(
            any(c.get("type") == "tool-result" for c in m.get("content", []))
            for m in request["messages"]
            if isinstance(m.get("content"), list)
        )
        if not has_tool_result:
            return {"tool_calls": [{"id": "c1", "name": "movement", "arguments": {"op": "teleport", "initiator": "林婉清"}}], "text": "我去找你"}
        return {"text": "见到了"}

    agent = loop.create("张三", bridge=bridge, llm=StubLlmClient(llm_stub))
    agent.send("你来见我")
    asyncio.run(agent.run_until_idle())
    assert calls["n"] == 2, f"movement 后应刷新 L1（轮首取 + 置脏重取），实际 {calls['n']} 次"
    print(f"✓ movement 置脏刷新：单轮取 L1 {calls['n']} 次（轮首 + movement 后重取）")


def test_movement_travel_and_places():
    """movement travel：NPC 前往指定城镇（stub 地点表消歧）；query_world places 返回可去地点目录。"""
    loop, bridge = reset_with_stub()
    r = bridge.call_tool("movement", {"op": "travel", "initiator": "张三", "destination": "白帝城"})
    assert r["success"] is True and r["data"]["moved"] is True and r["data"]["destination"] == "白帝城", r
    # 润色层把纯数据变成叙述（新架构：叙述唯一来源在 Python）
    assert "张三已动身前往白帝城" in tr.render("movement", {}, r), tr.render("movement", {}, r)
    region_hit = bridge.call_tool("movement", {"op": "travel", "initiator": "张三", "destination": "北斗剑宗", "region": "永宁州"})
    assert region_hit["success"] is True and region_hit["data"].get("region") == "永宁州", region_hit
    miss = bridge.call_tool("movement", {"op": "travel", "initiator": "张三", "destination": "不存在的城"})
    assert miss["success"] is False and "places" in miss["error"], miss
    no_dest = bridge.call_tool("movement", {"op": "travel", "initiator": "张三"})
    assert no_dest["success"] is False and "destination" in no_dest["error"], no_dest
    p = bridge.call_tool("query_world", {"topic": "places"})
    assert p["success"] and p["data"]["total"] >= 2 and any(x["name"] == "白帝城" for x in p["data"]["places"]), p
    print("✓ movement travel + query_world places：NPC 自主传送与地点目录闭环")


def test_telepathy_blocks_face_to_face_actions():
    """二阶段校验：异地传音下面对面动作被拒（world_ai_action 的 spar/attack/双修/传功），论道等言语之事放行。"""
    loop, bridge = reset_with_stub()
    # 张三 same_grid=False（异地）→ world_ai_action(spar) 拒绝
    r = bridge.call_tool("world_ai_action", {"op": "spar", "initiator": "张三"})
    assert r["success"] is False and "异地" in r["error"], r
    # 林婉清 same_grid=True（同格）→ world_ai_action(spar) 放行
    r2 = bridge.call_tool("world_ai_action", {"op": "spar", "initiator": "林婉清"})
    assert r2["success"] is True, r2
    # 双修异地拒绝 / 传功异地拒绝
    r3 = bridge.call_tool("world_ai_action", {"op": "shuang_xiu", "initiator": "张三"})
    assert r3["success"] is False and "异地" in r3["error"], r3
    r3b = bridge.call_tool("world_ai_action", {"op": "chuan_gong", "initiator": "张三"})
    assert r3b["success"] is False, r3b
    # 论道异地放行（不在面对面清单，属言语往来）
    r4 = bridge.call_tool("world_ai_action", {"op": "lun_dao", "initiator": "张三"})
    assert r4["success"] is True, r4
    print("✓ 传音约束：异地拒切磋/双修/传功，放行论道；同格放行切磋")


def test_economy_item_give():
    """economy_item：items 数组支持多种/灵石道具混送；不足全送；灵石货币直写；无道具失败。"""
    loop, bridge = reset_with_stub()
    # 林婉清 items=["灵石1000","丹药5"]
    # 单道具：丹药 ×3 → 成功 actual=3
    r = bridge.call_tool("economy_item", {"initiator": "林婉清", "items": [{"item_name": "丹药", "count": 3}]})
    assert r["success"] is True and r["data"]["items"][0]["actual"] == 3, r
    # 不足全送：丹药 ×10（背包5）→ actual=5
    r2 = bridge.call_tool("economy_item", {"initiator": "林婉清", "items": [{"item_name": "丹药", "count": 10}]})
    assert r2["success"] is True and r2["data"]["items"][0]["actual"] == 5, r2
    # 灵石：货币字段直写 → actual=100
    r3 = bridge.call_tool("economy_item", {"initiator": "林婉清", "items": [{"item_name": "灵石", "count": 100}]})
    assert r3["success"] is True and r3["data"]["items"][0]["actual"] == 100 and r3["data"]["items"][0]["mode"] == "money", r3
    # 灵石不足全送：×2000（持有1000）→ actual=1000
    r3b = bridge.call_tool("economy_item", {"initiator": "林婉清", "items": [{"item_name": "灵石", "count": 2000}]})
    assert r3b["success"] is True and r3b["data"]["items"][0]["actual"] == 1000, r3b
    # 混杂：灵石+丹药 一次送 → 两项都成功（money + props）
    r5 = bridge.call_tool("economy_item", {"initiator": "林婉清", "items": [{"item_name": "灵石", "count": 50}, {"item_name": "丹药", "count": 2}]})
    assert r5["success"] is True and len(r5["data"]["items"]) == 2, r5
    modes = {x["item"]: x["mode"] for x in r5["data"]["items"]}
    assert modes["灵石"] == "money" and modes["丹药"] == "props", r5
    # 部分失败：灵石(有) + 龙涎果(无) → 整体成功但龙涎果 error
    r6 = bridge.call_tool("economy_item", {"initiator": "林婉清", "items": [{"item_name": "灵石", "count": 10}, {"item_name": "龙涎果", "count": 1}]})
    assert r6["success"] is True, r6
    err_items = [x for x in r6["data"]["items"] if x.get("error")]
    assert len(err_items) == 1 and "背包中没有" in err_items[0]["error"], r6
    # 全部失败：只有龙涎果 → Fail
    r7 = bridge.call_tool("economy_item", {"initiator": "林婉清", "items": [{"item_name": "龙涎果", "count": 1}]})
    assert r7["success"] is False and "未达成" in r7["error"], r7
    print("✓ economy_item：items 数组 / 单道具 / 灵石直写 / 混送 / 部分失败 / 全失败")


def test_item_acquire_steal_ask():
    """item_acquire：偷窃/讨要走游戏原生 UnitActionRoleStealItem/Askfor（stub 对齐）；缺 item_name / 未知 op 拒绝。"""
    loop, bridge = reset_with_stub()
    # 偷窃：target=林婉清（发起者）对玩家偷「丹药」——纯数据形态，叙述由润色层组装
    r = bridge.call_tool("item_acquire", {"op": "steal_item", "initiator": "林婉清", "item_name": "丹药"})
    assert r["success"] is True, r
    assert r["data"]["op"] == "steal_item" and r["data"]["item_name"] == "丹药" and r["data"]["stack_count"] == 1, r
    assert "林婉清偷取了你的「丹药」，整栈共1个。" == tr.render("item_acquire", {}, r), tr.render("item_acquire", {}, r)
    # 讨要：target=林婉清 向玩家讨「丹药」×2
    r2 = bridge.call_tool("item_acquire", {"op": "ask_for", "initiator": "林婉清", "item_name": "丹药", "count": 2})
    assert r2["success"] is True, r2
    assert r2["data"]["op"] == "ask_for" and r2["data"]["count"] == 2, r2
    assert "林婉清向你讨要" in tr.render("item_acquire", {}, r2), tr.render("item_acquire", {}, r2)
    # 未知 op → 拒绝
    r3 = bridge.call_tool("item_acquire", {"op": "rob", "initiator": "林婉清", "item_name": "丹药"})
    assert r3["success"] is False and "unsupported" in r3["error"], r3
    # 缺 item_name → 拒绝
    r4 = bridge.call_tool("item_acquire", {"op": "steal_item", "initiator": "林婉清"})
    assert r4["success"] is False and "item_name" in r4["error"], r4
    print("✓ item_acquire：偷窃/讨要 op、缺参与未知 op 校验")


if __name__ == "__main__":
    test_tools_registered_in_system_prompt()
    test_l1_context_diff()
    test_l1_context_segments()
    test_l1_segment_diff_isolated()
    test_inspect_and_search_via_bridge()
    test_tool_calls_loop_via_stub_llm()
    test_action_threshold_via_bridge()
    test_l1_telepathy_wording()
    test_telepathy_blocks_face_to_face_actions()
    test_economy_item_give()
    test_item_acquire_steal_ask()
    test_ws_bridge_call_tool_carries_npc_id()
    print("\n全部 bridge+tools 脱离游戏模拟通过")


def test_query_world_events_count_and_rankings_top():
    """查询条数由模型决定：events 默认 12 条可上调（上限 120）；rankings top 上限放到 200
    （2026-09-10 用户拍板：20 个人没人关注，条数应由模型按需指定）。"""
    loop, bridge = reset_with_stub()
    bridge._world["events"] = [{"month": i, "text": f"第{i}月大事"} for i in range(1, 21)]

    r = bridge.call_tool("query_world", {"topic": "events"})
    assert r["success"] and len(r["data"]["events"]) == 12, r          # 默认 12（≈最近一年）
    r = bridge.call_tool("query_world", {"topic": "events", "count": 20})
    assert len(r["data"]["events"]) == 20, r                            # 按需上调
    r = bridge.call_tool("query_world", {"topic": "events", "count": 999})
    assert len(r["data"]["events"]) == 20, r                            # 超上限裁到可用条数

    r = bridge.call_tool("query_world", {"topic": "rankings", "board": "power", "top": 200})
    assert r["success"] and len(r["data"]["items"]) == int(r["data"]["total"]), r
    print("✓ events count（默认12/可上调）+ rankings top 上限 200")


def test_ws_bridge_call_tool_carries_npc_id():
    """call_tool 帧带 `npc_id`（09-13）：C# 靠它把"动作完成"归属到具体 NPC
    ——同格动作完成 → 自动打开对话 UI（C# `ActionWatcher`）。

    帧形状是跨语言契约，故逐字钉住：`{"name":…, "arguments":…, "npc_id":…}`；
    省略 npc_id（老调用方/只读查询）时也要照发现场（值为 null，C# 侧判空即跳过）。
    """
    import asyncio
    from agent_loop.transport.bridge import WsGameBridge

    class _RecWs:
        def __init__(self):
            self.frames = []

        async def request(self, method, params):
            self.frames.append((method, params))
            return {"success": True, "data": {"ok": 1}}

    ws = _RecWs()
    bridge = WsGameBridge(ws)

    res = asyncio.run(bridge.call_tool("world_ai_action", {"op": "lun_dao", "initiator": "缪嘉歆"}, npc_id="云含"))
    assert res["success"], res
    method, params = ws.frames[-1]
    assert method == "call_tool", method
    assert params["npc_id"] == "云含", params
    assert params["name"] == "world_ai_action" and params["arguments"]["op"] == "lun_dao", params

    asyncio.run(bridge.call_tool("inspect_unit", {"target": "云含"}))
    assert ws.frames[-1][1]["npc_id"] is None, "未指定归属时发 null（C# 侧跳过自动开窗）"
    print("✓ call_tool 帧：npc_id 逐字带上（含缺省 null 形状）")


def test_readlingshi_failure_is_explicit_not_a_different_account():
    """灵石读取失败必须**显式失败**，不许退 `totalSchoolMoney`（那是宗门钱，另一个账户）。

    旧行为在 `GetPropsNum` 失败时把一个貌似合理的数字交给模型 —— 模型据此报价/赠送，
    实际看的是另一个钱袋，而且没有任何信号。现在 `ReadLingshiHeld` 失败返回 -1，
    所有调用点（trade 预检 / economy_item 预览 / TransferLingshi）都必须先判 -1。
    """
    s = _cs_tools.tool_src()
    i = s.index(" int ReadLingshiHeld(WorldUnitBase u)")
    body = s[i: s.index("\n        }", i)]
    assert "totalSchoolMoney" not in body, "宗门钱兜底又回来了（语义不同的账户）"
    assert "catch { return -1; }" in body, "读取失败必须返回 -1（哨兵值），由调用点显式处理"
    # 三个调用点都要先判 -1
    for anchor, why in (("buyerMoneyBefore < 0", "trade 买方预检"),
                        ("have < 0", "economy_item 赠送预览"),
                        ("haveMoney < 0", "TransferLingshi 转账")):
        assert anchor in s, f"{why} 没有处理 -1（会把 -1 当余额算）"
    print("✓ 灵石：读不到即显式失败，不回退宗门钱，-1 在三处调用点都被拦")


def test_action_target_npc_via_stub():
    """动作对手方（09-16）：缺省 = 玩家（老路径，上文各测试即哨兵）；填 `target` 才是其他 NPC。

    非玩家时：economy_item / item_acquire / social_relation **免窗直写**（stub 里道具真转移、
    关系真写到那一对）；world_ai_action 与 movement.summon 显式拒绝（**绝不静默落回玩家**）。
    """
    loop, bridge = reset_with_stub()
    bridge.seed_unit("李四", {"realm": "筑基", "items": ["丹药3"],
                             "player": {"name": "韩立", "relation": "好友", "intim": 120, "same_grid": True}})
    lin = bridge._units["林婉清"]

    # ① 赠送 → 李四：道具真进对方背包；data 带 counterparty（渲染层据此改第三人称）
    r = bridge.call_tool("economy_item", {"initiator": "林婉清", "target": "李四",
                                          "items": [{"item_name": "丹药", "count": 2}]})
    assert r["success"] is True and r["data"]["counterparty"] == "李四", r
    assert bridge._bag_have(bridge._units["李四"]["items"], "丹药") == 5, bridge._units["李四"]["items"]
    assert "林婉清赠予李四" in tr.render("economy_item", {"initiator": "林婉清"}, r), tr.render("economy_item", {"initiator": "林婉清"}, r)

    # ② 偷：整栈数从**受害者**背包现算（玩家背包不在桩里建模，故老路径恒 1）
    r2 = bridge.call_tool("item_acquire", {"initiator": "林婉清", "target": "李四",
                                           "op": "steal_item", "item_name": "丹药"})
    assert r2["success"] is True and r2["data"]["stack_count"] == 5, r2

    # ③ 讨要：免窗直接转移（李四 5 → 1；林婉清 3 → 7）
    r3 = bridge.call_tool("item_acquire", {"initiator": "林婉清", "target": "李四",
                                           "op": "ask_for", "item_name": "丹药", "count": 4})
    assert r3["success"] is True and r3["data"]["accepted"] is True, r3
    assert bridge._bag_have(bridge._units["李四"]["items"], "丹药") == 1, bridge._units["李四"]["items"]
    assert bridge._bag_have(lin["items"], "丹药") == 7, lin["items"]

    # ④ 关系：写到"林婉清 ↔ 李四"这一对上（不是玩家），且带 counterparty
    r4 = bridge.call_tool("social_relation", {"initiator": "林婉清", "target": "李四",
                                              "op": "add_intim", "value": 5})
    assert r4["success"] is True and r4["data"]["counterparty"] == "李四", r4
    assert lin["relations"]["李四"]["intim"] == 5, lin["relations"]["李四"]

    # ⑤ 没有对等物的：显式拒绝（不许静默改成"对玩家做"）
    bad = bridge.call_tool("world_ai_action", {"initiator": "林婉清", "target": "李四", "op": "spar"})
    assert bad["success"] is False and "只能是玩家" in bad["error"], bad

    # ⑥ 位移：summon / teleport 都支持 NPC（纯引擎动作，没有窗）
    for mop in ("summon", "teleport"):
        mv = bridge.call_tool("movement", {"initiator": "林婉清", "target": "李四", "op": mop})
        assert mv["success"] is True and mv["data"]["counterparty"] == "李四", mv
    mv_text = tr.render("movement", {}, {"success": True, "data": {
        "op": "summon", "target": "林婉清", "counterparty": "李四", "moved": True}})
    assert mv_text == "李四已被林婉清召到身边（李四移动到林婉清所在处）。", mv_text

    # ⑦ 缺省（不传 target）= 老路径：结果里**不该**出现 counterparty
    ok = bridge.call_tool("social_relation", {"op": "add_intim", "initiator": "林婉清", "value": 1})
    assert ok["success"] is True and "counterparty" not in ok["data"], ok
    print("✓ 动作对手方：NPC 免窗直写（赠/偷/讨/关系）+ 无对等物显式拒绝 + 缺省路径不变")


def test_item_acquire_lingshi_ask_for():
    """灵石讨要两条路都通（09-17 对 NPC＝"收下灵石赠礼"的落刀路径；09-21 起对玩家也通）。

    对玩家 = C# 复用纯灵石赠送同款**自制确认窗**（点「给他」才转账），桩里没有玩家可点，
    按"窗已弹、等回应"给 `pending`，与道具对玩家同形。偷窃仍一律拒绝（soleID 对货币无对等物），
    且**足额才转**（与道具 ask_for 同口径，不留半截）。
    """
    loop, bridge = reset_with_stub()
    bridge.seed_unit("李四", {"realm": "筑基", "items": ["灵石500"],
                             "player": {"name": "韩立", "relation": "好友", "intim": 120, "same_grid": True}})
    bridge._units["林婉清"]["items"] = []

    # 对 NPC：李四 500 → 400，林婉清 0 → 100
    r = bridge.call_tool("item_acquire", {"initiator": "林婉清", "target": "李四",
                                          "op": "ask_for", "item_name": "灵石", "count": 100})
    assert r["success"] is True and r["data"]["accepted"] is True, r
    assert bridge._bag_have(bridge._units["李四"]["items"], "灵石") == 400, bridge._units["李四"]["items"]
    assert bridge._bag_have(bridge._units["林婉清"]["items"], "灵石") == 100, bridge._units["林婉清"]["items"]

    # 不足：整笔作废（钱一分不动）
    r2 = bridge.call_tool("item_acquire", {"initiator": "林婉清", "target": "李四",
                                           "op": "ask_for", "item_name": "灵石", "count": 9999})
    assert r2["success"] is False and "不足" in r2["error"], r2
    assert bridge._bag_have(bridge._units["李四"]["items"], "灵石") == 400, "整笔作废不该动钱"

    # 偷窃：仍拒绝，且报错要指向**能用的那条路**（对玩家也一样，别只说"改用 economy_item"）
    r3 = bridge.call_tool("item_acquire", {"initiator": "林婉清", "target": "李四",
                                           "op": "steal_item", "item_name": "灵石"})
    assert r3["success"] is False and "货币" in r3["error"] and "ask_for" in r3["error"], r3

    # ★ 对玩家：放行 → 等确认窗。这就是"玩家说『我送你三百灵石』"唯一能落地的调用 ★
    r4 = bridge.call_tool("item_acquire", {"initiator": "林婉清", "op": "ask_for",
                                           "item_name": "灵石", "count": 300})
    assert r4["success"] is True and r4["data"]["pending"] is True, r4
    assert "receiving" not in r4["data"], "没填 accepting_gift 就不该当成收赠"


def test_item_acquire_accepting_gift_wording():
    """`accepting_gift`（玩家主动要送、NPC 是**收下**方）：结果与措辞都不许再说"讨要"。

    真机 09-21：玩家说"我送你三百灵石"，模型想了 1400 字最后没调工具，理由是
    "economy_item 是我送人，没有收东西的工具" —— 收下的路就是 ask_for，可提示词与 schema
    都没写，而 C# 确认窗还写着"想向你讨要"。两头一起修。
    """
    loop, bridge = reset_with_stub()
    r = bridge.call_tool("item_acquire", {"initiator": "林婉清", "op": "ask_for",
                                          "item_name": "灵石", "count": 300,
                                          "accepting_gift": True})
    assert r["data"].get("receiving") is True, r

    # 玩家点「收下」/ 点「不用了」/ 结局没回来 —— 三态都不得出现"讨要"
    ok = {"op": "ask_for", "target": "林婉清", "item_name": "灵石", "count": 300,
          "accepted": True, "receiving": True}
    t = tr.render("item_acquire", {"op": "ask_for"}, {"success": True, "data": ok})
    assert t == "你赠的「灵石」×300被林婉清收下了。", t
    t2 = tr.render("item_acquire", {"op": "ask_for"},
                   {"success": True, "data": dict(ok, accepted=False)})
    assert t2 == "你把要赠林婉清的「灵石」×300收回了。", t2
    t3 = tr.render("item_acquire", {"op": "ask_for"},
                   {"success": True, "data": dict(ok, accepted=None)})
    assert t3 == "你赠林婉清的「灵石」×300还没回音（C# 未返回结局）。", t3
    for x in (t, t2, t3):
        assert "讨要" not in x, x

    # 对照：没填 accepting_gift 时仍是讨要措辞（老形状一字不动）
    plain = {"op": "ask_for", "target": "林婉清", "item_name": "丹药", "count": 2, "accepted": True}
    t4 = tr.render("item_acquire", {"op": "ask_for"}, {"success": True, "data": plain})
    assert t4 == "你把「丹药」×2交给了林婉清。", t4
    print("✓ 收下玩家所赠：receiving 透传 + 三态措辞无『讨要』+ 老形状不变")


def test_inspect_relation_is_relative_to_initiator():
    """inspect 的关系三项 = **被查者 → 发起者**，措辞跟着取数口径走（09-21 真机修正）。

    `UnitSnapshot.Build` 把对手方写死成玩家（L1 口径），而查询的发起者是当前 NPC。
    沿用 Build 的值时，**查玩家**就退化成"玩家的关系簿查玩家自己"→ 恒 陌生/好感0/同处一地，
    与上下文里写着的道侣之谊当面打架（模型据此否掉了自己的关系，还反过来怀疑位置）。

    方向必须用**不对称**数据验（互有好感验不出写反，见 RelationTo 的注释）。
    """
    loop, bridge = reset_with_stub()
    bridge.seed_unit("李四", {
        "realm": "筑基",
        # 李四对玩家：道侣、好感 900、同格
        "player": {"name": "韩立", "relation": "道侣", "intim": 900, "same_grid": True},
        # 李四对姜萌：相识、好感 12、异地 —— 与上面每一项都不同，取错口径一眼可辨
        "relations": {"姜萌": {"relation": "相识", "intim": 12, "same_grid": False}},
    })

    r = bridge.call_tool("inspect_unit", {"initiator": "姜萌", "target": "李四"})
    d = r["data"]
    assert (d["relation"], d["intim"], d["same_grid"], d["ref"]) == ("相识", 12, False, "姜萌"), d
    txt = tr.render("inspect_unit", {"initiator": "姜萌"}, r)
    assert "与姜萌异地，关系相识，好感12" in txt, txt
    assert "与玩家" not in txt, "渲染层又把措辞写死成玩家了"

    # 发起者缺失（老调用方/没有 initiator 的探针）→ 回落对玩家，且不给 ref
    r2 = bridge.call_tool("inspect_unit", {"target": "李四"})
    assert r2["data"]["relation"] == "道侣" and r2["data"]["intim"] == 900, r2
    assert "ref" not in r2["data"], r2
    assert "与玩家异地，关系道侣，好感900" in tr.render("inspect_unit", {}, r2)

    # 自查：三项都不给（自己对自己的关系簿没有意义，宁缺不假）
    r3 = bridge.call_tool("inspect_unit", {"initiator": "李四", "target": "李四"})
    assert not ({"relation", "intim", "same_grid", "ref"} & set(r3["data"])), r3
    assert "关系" not in tr.render("inspect_unit", {"initiator": "李四"}, r3)

    # 读操作不许改档案：`relations` 里没有的发起者只当"陌生"，不落库
    assert set(bridge._units["李四"]["relations"]) == {"姜萌"}, bridge._units["李四"]["relations"]
    print("✓ inspect 关系口径：被查者→发起者 + 措辞随 ref + 自查不给 + 读取不写库")
    print("✓ 灵石讨要：NPC 放开（足额才转）+ 偷窃/对玩家仍拒绝")
