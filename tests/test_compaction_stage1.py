"""压缩 Stage1 测试：meter 估算（中文计量）+ pruner 修剪 + 重放一致"""

import asyncio
import json

from agent_loop.compaction.meter import estimate_text, estimate_message, estimate_header
from agent_loop.compaction.pruner import ToolResultPruner
from agent_loop.session import Session
from agent_loop.persistence import save_session, load_session


ROOT = "/tmp/agent_loop_test_compact1"


def _reset():
    from agent_loop.agent_loop import AgentLoop

    return AgentLoop.reset_for_test(storage_root=ROOT, llm=None)


def test_meter_cjk_weighting():
    """中文按 1 字/token，非中文按 4 字/token；中文对话不能低估。"""
    # 中文：4 个汉字 ≈ 4 token（1/token）
    assert estimate_text("四字汉字") >= 4
    # ASCII：每 4 字符 1 token
    assert estimate_text("abcd" * 4) == 4  # 16 字符 / 4 = 4
    print(f"✓ 中文计量偏向：\"四字汉字\" -> {estimate_text('四字汉字')} token；\"abcd\"*4 -> {estimate_text('abcd' * 4)} token")


def test_meter_message_header():
    msg = {"role": "user", "content": [{"type": "text", "text": "道友安好？你好啊"}]}
    t_msg = estimate_message(msg)
    assert t_msg >= 1 + 4  # 文本至少 1 token + framing
    hdr = {"system": "你是林婉清，鬼谷八荒修仙者。", "tools": [{"type": "function", "function": {"name": "inspect_unit"}}]}
    t_hdr = estimate_header(hdr)
    assert t_hdr > 0
    assert estimate_header(None) == 0
    print(f"✓ estimate_message={t_msg}, estimate_header={t_hdr}")


def test_pruner_trims_and_replays():
    """超预算 tool/result 修剪 + save/load 后 surface 一致。"""
    _reset()
    sess = Session(id="修剪", header={"id": "修剪", "cwd": "/tmp"})
    # 造一条超大 tool/result：10000 中文字符
    big = "修" * 10000
    tool_msg = {
        "role": "user",
        "content": [{"type": "tool-result", "toolCallId": "c1", "content": [{"type": "text", "text": big}], "isError": False}],
        "id": "m1",
        "source": {"kind": "tool", "callId": "c1"},
    }
    sess.append("tool/call", {"callId": "c1", "name": "inspect_unit", "arguments": "{}"})
    sess.append("tool/result", {"message": tool_msg}, {"surfaceOp": "append", "sourceEventSeqs": [0]})

    def _run(pruner, s):
        return asyncio.run(pruner.prune_session(s))

    pruner = ToolResultPruner(threshold_chars=8192, head_chars=4096, tail_chars=1024)
    report = _run(pruner, sess)
    assert report["charsRemoved"] > 0
    # 修剪后 surface 里那条 tool/result 长度应明显小于 8192+marker 上限
    msgs = sess.derive_messages()
    tool_text = json.dumps(msgs, ensure_ascii=False)
    assert "修" * 9000 not in tool_text  # 中段被裁掉
    assert "[... 工具结果中间已折叠 ...]" in tool_text  # marker 在
    # 记账事件在
    assert any(ev["type"] == "compaction/prune" for ev in sess.log)

    # 重放一致：save → load → derive 仍是修剪后（不还原成大原文）
    save_session(sess, ROOT)
    loaded = load_session("修剪", ROOT)
    assert loaded is not None
    lmsgs = loaded.derive_messages()
    ltext = json.dumps(lmsgs, ensure_ascii=False)
    assert "修" * 9000 not in ltext
    assert "[... 工具结果中间已折叠 ...]" in ltext
    # 裁剪后内容量应远小于原文
    assert len(ltext) < 8192 + 64
    print(f"✓ pruner 修剪 + 重放一致，charsRemoved={report['charsRemoved']}")


def test_pruner_keeps_under_budget():
    """未超预算的 tool/result 不动。"""
    _reset()
    sess = Session(id="修剪小", header={"id": "修剪小", "cwd": "/tmp"})
    tool_msg = {
        "role": "user",
        "content": [{"type": "tool-result", "toolCallId": "c2", "content": [{"type": "text", "text": "小结果"}], "isError": False}],
        "id": "m2",
        "source": {"kind": "tool", "callId": "c2"},
    }
    sess.append("tool/call", {"callId": "c2", "name": "query_world", "arguments": "{}"})
    sess.append("tool/result", {"message": tool_msg}, {"surfaceOp": "append", "sourceEventSeqs": [0]})
    pruner = ToolResultPruner()
    report = asyncio.run(pruner.prune_session(sess))
    assert report["pruned"] == []
    assert len(sess.log) == 2  # 未新增事件
    print("✓ 未超预算的 tool/result 不动")


async def test_pruner_llm_summary():
    """LLM 摘要优先：stub LlmClient 返回摘要替换 tool-result content，不落确定性 marker。"""
    _reset()
    sess = Session(id="摘要", header={"id": "摘要", "cwd": "/tmp"})
    # 超预算原文
    big = "灵草" * 6000  # 12000 中文字符，远超 8192
    tool_msg = {
        "role": "user",
        "content": [{"type": "tool-result", "toolCallId": "c3", "content": [{"type": "text", "text": big}], "isError": False}],
        "id": "m3",
        "source": {"kind": "tool", "callId": "c3"},
    }
    sess.append("tool/call", {"callId": "c3", "name": "query_world", "arguments": "{}"})
    sess.append("tool/result", {"message": tool_msg}, {"surfaceOp": "append", "sourceEventSeqs": [0]})

    from agent_loop.llm.stub_client import StubLlmClient

    # stub：无论入内，返回固定摘要
    stub = StubLlmClient(fn=lambda req: {"text": "[灵草摘要] 获得灵草若干", "tool_calls": []})
    pruner = ToolResultPruner(threshold_chars=8192, head_chars=4096, tail_chars=1024, llm=stub)
    report = await pruner.prune_session(sess)
    assert report["pruned"], "应有一条结果被压缩"
    msgs = sess.derive_messages()
    text = json.dumps(msgs, ensure_ascii=False)
    # 摘要替换了原文：不含超长原文片段，含摘要文本
    assert "灵草" * 100 not in text
    assert "灵草摘要" in text
    # 走 LLM 路径，不引入确定性 marker
    assert "[... 工具结果中间已折叠 ...]" not in text
    # 记账仍在
    assert any(ev["type"] == "compaction/prune" for ev in sess.log)
    print("✓ pruner LLM 摘要路径正常")


if __name__ == "__main__":
    test_meter_cjk_weighting()
    test_meter_message_header()
    test_pruner_trims_and_replays()
    test_pruner_keeps_under_budget()
    asyncio.run(test_pruner_llm_summary())
    print("\nStage1 压缩测试（meter + pruner）全部通过")