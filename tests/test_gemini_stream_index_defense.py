"""Gemini 流式 tool_call `index:null` 防御契约（10-16）。

背景：Gemini OpenAI 兼容层的流式 tool_call delta 是 `"index": null`（键存在、值为
None）——`.get(key, 0)` / `getattr(x, "index", 0)` 的默认值**只管"不存在"**，管不了
"存在但为 None" ⇒ `int(None)` → TypeError。症状：**模型调工具的回合永远失败**
（第一帧就炸、重试再炸），纯文本回合正常——"Gemini 只能成功一次"的真因。
截图里把锅归给 `chunk.usage.completion_tokens` 是误诊（usage 校验行 432 早有
isinstance 防护）。

本契约钉住聚合处的防御形态，防重构洗掉。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "llm" / "openai_client.py"


def test_stream_tool_delta_index_defended():
    src = SRC.read_text(encoding="utf-8")
    # 裸 int() 转换必须消失（两种形态都防不住 "存在但为 None"）
    assert 'int(tc.get("index"' not in src, \
        "裸 int(tc.get(\"index\", 0)) 回来了——Gemini 的 index:null 会炸"
    assert 'int(getattr(tc, "index"' not in src, \
        "裸 int(getattr(tc, \"index\", 0)) 回来了——属性存在但为 None 时炸"
    # 防御形态必须就位（dict + 对象两路，isinstance 比 is not None 稳——顺带防脏类型）
    n = src.count("int(raw) if isinstance(raw, int) else 0")
    assert n >= 2, f"index 防御形态不足（现 {n} 处，应 dict/对象两路各 1）"
    # 守卫语义注释在位（防"看不懂为什么多此一举"被顺手删）
    assert "index" in src and "Gemini" in src, "防御注释丢了（下一个维护者会删掉'多此一举'的守卫）"
