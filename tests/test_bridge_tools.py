"""脱离游戏：bridge + tools + L1/Context + tool_calls 往返（各司其职）"""

import asyncio
import json

from agent_loop.agent_loop import AgentLoop
from agent_loop.system_prompt import SystemPrompt
from agent_loop.bridge import StubGameBridge
from agent_loop.tools.schemas import TOOL_ORDER
from agent_loop.tools import text_render as tr
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.llm.echo_client import EchoLlmClient


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

    def patched(npc_id):
        r = orig_get_context(npc_id)
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
    """传音模式判定：异格（same_grid=False）玩家段成文带"神识传音"，同格带"面对面"。"""
    sp = SystemPrompt.instance()
    raw = {"npc_id": "林婉清", "player": {"name": "韩立", "relation": "道友", "intim": 60, "same_grid": False}}
    segs = sp.format_l1_context("林婉清", raw)
    assert "神识传音" in segs["player"] and "异地相隔" in segs["player"], segs["player"]
    raw["player"]["same_grid"] = True
    segs2 = sp.format_l1_context("林婉清", raw)
    assert "面对面" in segs2["player"] and "神识传音" not in segs2["player"], segs2["player"]
    print(f"✓ 传音模式判定：异地=神识传音 / 同格=面对面 → {segs['player'][:44]}…")


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
    """位置成文（09-13）：自身段与玩家段都要有位置；C# 给的是 `point{x,y}`，成文侧却读 `pos`。

    事故：`format_l1_context` 写的是 `pos = self_raw["pos"]` → `栖居于{pos}`，而 **C# 全仓从没发过
    `pos` 这个键**（`grep '["pos"]' csharp/*.cs` = 0 处）——C# 发 `point{x,y}`、成文侧等地名，
    两边各按自己的假设写 → 自身段位置恒空、谁都不报错。用户拍板：显示坐标即可。
    """
    sp = SystemPrompt.instance()
    raw = {
        "npc_id": "云含", "sex": "女",
        "point": {"x": 31, "y": 78},
        "player": {"name": "缪嘉歆", "point": {"x": 20, "y": 8}, "same_grid": False},
    }
    segs = sp.format_l1_context("云含", raw)
    assert "坐标(31,78)" in segs["self"], segs["self"]
    assert "坐标(20,8)" in segs["player"], segs["player"]

    # 地名若将来由 C# 提供（pos），优先于坐标（保留原有措辞）
    segs2 = sp.format_l1_context("云含", dict(raw, pos="青雲鎮"))
    assert "栖居于青雲鎮" in segs2["self"] and "坐标(31,78)" not in segs2["self"], segs2["self"]

    # 旧 C# 载荷（无 point/pos）不得回归；半截坐标不得编出"坐标(31,None)"
    old = sp.format_l1_context("云含", {"npc_id": "云含", "player": {"name": "韩立"}})
    assert "坐标" not in old["self"] and "坐标" not in old["player"], (old["self"], old["player"])
    half = sp.format_l1_context("云含", {"npc_id": "云含", "point": {"x": 31},
                                         "player": {"name": "韩立", "point": {"y": 8}}})
    assert "31" not in half["self"] and "None" not in half["player"], (half["self"], half["player"])
    print("✓ L1 位置成文：自身/玩家段都给坐标 / 地名优先 / 缺字段与半截坐标不回归")


def test_l1_luck_wording():
    """自身段带气运：born/added 成文带 desc（用户拍板 L1 要 desc；desc 换行折叠）；缺 luck 不出空句。"""
    sp = SystemPrompt.instance()
    raw = {
        "npc_id": "林婉清",
        "luck": {
            "born": ["天妒(金)", "剑心(紫)"],
            "added": [{"name": "陷入瓶颈", "desc": "修为停滞，需要突破瓶颈才可步入炼气后期。\n突破瓶颈的方法在逆天改命界面(快捷键：J)中说明。"}],
        },
    }
    segs = sp.format_l1_context("林婉清", raw)
    assert "命带气运天妒(金)、剑心(紫)" in segs["self"], segs["self"]
    assert "后天又得陷入瓶颈（修为停滞" in segs["self"], segs["self"]
    assert "\n" not in segs["self"], "desc 换行应折叠"
    segs2 = sp.format_l1_context("林婉清", {"npc_id": "林婉清"})
    assert "气运" not in segs2["self"], segs2["self"]
    print(f"✓ L1 自身段带气运+desc：…{segs['self'][-40:]}")


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

    def counting(npc_id):
        calls["n"] += 1
        return orig(npc_id)

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

    def counting(npc_id):
        calls["n"] += 1
        return orig(npc_id)

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
    from agent_loop.bridge import WsGameBridge

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
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "csharp" / "ToolExecutor.cs"
    s = src.read_text(encoding="utf-8")
    i = s.index("private static int ReadLingshiHeld(WorldUnitBase u)")
    body = s[i: s.index("\n        }", i)]
    assert "totalSchoolMoney" not in body, "宗门钱兜底又回来了（语义不同的账户）"
    assert "catch { return -1; }" in body, "读取失败必须返回 -1（哨兵值），由调用点显式处理"
    # 三个调用点都要先判 -1
    for anchor, why in (("buyerMoneyBefore < 0", "trade 买方预检"),
                        ("have < 0", "economy_item 赠送预览"),
                        ("haveMoney < 0", "TransferLingshi 转账")):
        assert anchor in s, f"{why} 没有处理 -1（会把 -1 当余额算）"
    print("✓ 灵石：读不到即显式失败，不回退宗门钱，-1 在三处调用点都被拦")
