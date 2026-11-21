"""★ 09-27 真机事故的两道防线：工具参数**结构**自检 + RPC 异常**必回帧** ★

## 事故原样（玩家："我打开背包没有看到物品，并没有成功"）

模型给 `economy_item` 生成的是（原始帧存于会话 jsonl 的 `tool/call` 记录）：

    {"items": "\\n[{\\"count\\": 1, \\"item_name\\": \\"荒角碧游鲲\\"}]\\n<parameter=target>\\n唐炎\\n"}

`items` 是**字符串**（本该是数组），里面还混进 `<parameter=target>` 那套 XML 标签；`target` 没成形。
随后四连响：

  ① C# `args["items"] as JArray` → null → `Fail("缺少 items…")`（一条本该能自纠的失败）；
  ② 该失败帧**不带 data**，而 `WsClient.ExecuteRequest` 里 `res["data"] != null &&
     res["data"]["queued"]` 对 `JValue(Null)` 取子键 → `InvalidOperationException` → **整帧丢失**；
  ③ Python 的 future 挂满 120s ⇒ 模型只看到「请求超时」；
  ④ NPC 随后把**没办成**的事讲成"已交割"（坐骑没回玩家背包）。

## 本文件钉住两半修复

  · Python：`tools.schemas.shape_error` —— 形状不合规 ⇒ **出站前**拦下（不进桥、零等待），
    回一条模型照着改就能过的错误；
  · C#：`WsClient` 不对 JSON null 取子键 + **执行异常也必须回一帧**；
    `ToolExecutor.Fail` 不再写 `["data"] = null`（`JValue(Null)` 的源头）。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from agent_loop.agent_loop import AgentLoop
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.llm.stub_client import StubLlmClient
from agent_loop.tools.schemas import shape_error
from agent_loop.transport.bridge import StubGameBridge
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]

#: 真机那一帧，原样抄（`items` 字符串里嵌着 JSON 文本 + `<parameter=target>` 标签）
INCIDENT_ARGS = {"items": "\n[{\"count\": 1, \"item_name\": \"荒角碧游鲲\"}]\n<parameter=target>\n唐炎\n"}


# ══════════════════════════════════════════════════════════════════
# ① Python：结构自检（shape_error）
# ══════════════════════════════════════════════════════════════════

def test_shape_error_catches_incident_frame():
    """真机那一帧必须被拦下，且错误要**说清哪里不对**（模型照着改就能过）。"""
    err = shape_error("economy_item", INCIDENT_ARGS)
    assert err, "真机那帧必须被拦下"
    assert "items" in err and "数组" in err and "字符串" in err, err
    assert "item_name" in err, "错误文案里要带靶子（数组元素样例）：%s" % err


def test_shape_error_missing_required_and_object_mismatch():
    assert "缺 `items`" in shape_error("economy_item", {"target": "唐炎"})
    assert "缺 `filters`" in shape_error("search_units", {})
    err = shape_error("search_units", {"filters": "keyword=张三"})
    assert "对象" in err and "filters" in err, err


def test_shape_error_allows_valid_and_scalar_loose():
    """**只拦结构**：合法调用放行；标量松散（`"3"` vs `3`）由 C# 容错，这里不许拦。"""
    assert shape_error("economy_item", {"items": [{"item_name": "荒角碧游鲲", "count": 1}]}) == ""
    # harness 自带的键（`initiator` / `*_id`）不在任何 schema 里，不参与判定
    assert shape_error("economy_item", {"items": [{"item_name": "丹药"}],
                                        "initiator": "司徒月", "initiator_id": "ikhWgN"}) == ""
    assert shape_error("economy_item", {"items": [{"item_name": "丹药", "count": "3"}]}) == ""
    # 未知工具不管（回放 / 老 Python）；None / 非 dict 也不许抛
    assert shape_error("不存在的工具", INCIDENT_ARGS) == ""
    assert shape_error("economy_item", None) != ""


# ══════════════════════════════════════════════════════════════════
# ② 端到端：拦在出站前（不进桥、零等待），合法调用照常进桥
# ══════════════════════════════════════════════════════════════════

def _run_one_tool_call(tmp_path, args, tool="economy_item"):
    """桩模型产一次 tool_call → 跑完一个回合；返回 (agent, bridge 实际收到的调用)。"""
    bridge = StubGameBridge()
    bridge.seed_unit("张三", {
        "realm": "筑基", "sect": "无宗门", "pos": "永宁州",
        "player": {"name": "韩立", "relation": "好友", "intim": 120, "same_grid": True},
        "items": ["丹药5"],
    })
    calls = []
    orig = bridge.call_tool

    def _spy(name, a=None, **kw):
        calls.append((name, dict(a or {})))
        return orig(name, a, **kw)

    bridge.call_tool = _spy          # type: ignore[assignment]

    async def llm_stub(request):
        has_tool_result = any(
            any(c.get("type") == "tool-result" for c in m.get("content", []))
            for m in request["messages"] if isinstance(m.get("content"), list))
        if not has_tool_result:
            return {"tool_calls": [{"id": "call_1", "name": tool, "arguments": args}], "text": "……"}
        return {"text": "知道了。"}

    loop = AgentLoop.reset_for_test(storage_root=str(tmp_path), bridge=bridge, llm=EchoLlmClient())
    agent = loop.create("张三", bridge=bridge, llm=StubLlmClient(llm_stub))
    agent.send("把坐骑还我")
    asyncio.run(agent.run_until_idle())
    return agent, calls


def test_guard_short_circuits_before_bridge(tmp_path):
    """形状不合格 ⇒ 不进桥；但 tool/result 必须落账（形状与另两道闸逐字同构）。"""
    agent, calls = _run_one_tool_call(tmp_path, dict(INCIDENT_ARGS))
    assert calls == [], "形状不合格的调用**不许进桥**（真机那次正是它把整帧弄丢的）：%s" % calls
    blob = json.dumps(agent.session.derive_messages(), ensure_ascii=False)
    assert "参数结构不对" in blob and "数组" in blob, blob[-800:]
    assert any(ev["type"] == "tool/call" for ev in agent.session.log)
    assert any(ev["type"] == "tool/result" and ev.get("sourceEventSeqs") for ev in agent.session.log), \
        "拦下的调用也要落 tool/result（否则模型看不到反馈会反复重试）"


def test_guard_lets_valid_call_through(tmp_path):
    """反向控制：合法调用**必须**照常进桥（别把正常赠送一起拦了）。"""
    agent, calls = _run_one_tool_call(tmp_path, {"items": [{"item_name": "丹药", "count": 1}]})
    assert [c[0] for c in calls] == ["economy_item"], "合法调用被误拦：%s" % calls
    blob = json.dumps(agent.session.derive_messages(), ensure_ascii=False)
    assert "参数结构不对" not in blob, blob[-800:]


# ══════════════════════════════════════════════════════════════════
# ③ C#：不许对 JSON null 取子键 + 异常必回帧 + Fail 不写 null data
# ══════════════════════════════════════════════════════════════════

WS_CLIENT = ROOT / "csharp" / "bridge" / "WsClient.cs"
TOOL_EXECUTOR = _cs_tools.TOOL_SRC
TOOL_RESULT = _cs_tools.tool_src()  # 信封在 game/ToolResult.cs
def _strip_comments(src: str) -> str:
    """剥 C# 注释：注释里会**引用**被禁止/被要求的写法，不能当成代码（沿用既有范式）。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _method(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员正文（含花括号）。签名必须唯一。"""
    src = _strip_comments(src)
    assert src.count(signature) == 1, "签名不唯一（%d 次）：%s" % (src.count(signature), signature)
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError("大括号不配对：" + signature)


def test_wsclient_no_child_access_on_json_null():
    """`res["data"] != null` **判不出 JSON null**（它是 `JValue(Null)`）—— 09-27 崩溃就在这一行。"""
    body = _method(WS_CLIENT.read_text(encoding="utf-8"),
                   "protected JObject ExecuteRequest(string method, JObject p)")
    assert 'res["data"]["queued"]' not in body, \
        "不许对可能为 JSON null 的 token 取子键（Cannot access child value on JValue）"
    assert 'var resData = res["data"] as JObject;' in body, \
        "queued 判定要先 `as JObject` 收窄（非对象一律当没有 queued）"


def test_wsclient_replies_error_frame_when_handler_throws():
    """执行侧抛异常**必须回一帧**：否则 Python 挂满 120s，只看到"请求超时"（错因被整条吞掉）。"""
    body = _method(WS_CLIENT.read_text(encoding="utf-8"),
                   "private void HandleMessage(WsTransport ws, string text)")
    assert "catch (Exception e)" in body, "取 task.Result 处要接住异常"
    head, tail = body[:body.index("catch (Exception e)")], body[body.index("catch (Exception e)"):]
    assert "task.Result" in head, "try 里取的应是主线程结果"
    assert '["req_id"] = reqId' in tail, "错误帧必须带 req_id（Python 靠它解 future）"
    assert '["ok"] = false' in tail and "工具执行异常（C# 侧" in tail, "错误帧要带明确错因"


def test_tool_executor_fail_omits_null_data():
    """`["data"] = null` 会被 Newtonsoft 存成 `JValue(Null)` —— 09-27 崩溃的源头，别再写回来。"""
    body = _method(_cs_tools.tool_file("../game/ToolResult.cs"),
                   "public static JObject Fail(string error, object data = null)")
    assert '["data"] = data == null ? null : JToken.FromObject(data)' not in body, \
        "失败帧**不写 data 键**（缺键 = null 引用，下游短路判断才生效）"
    assert 'if (data != null) res["data"] = JToken.FromObject(data);' in body
