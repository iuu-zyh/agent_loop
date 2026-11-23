"""对严格 schema 网关做逐项一致性实测：哪些字段能发、哪些发出去就 400。

用法： python scripts/dev/probe_strict_gateway.py <base_url> <api_key> <model>
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://<gateway>/provider").rstrip("/")
KEY = sys.argv[2] if len(sys.argv) > 2 else ""
MODEL = sys.argv[3] if len(sys.argv) > 3 else "<provider>/<model-id>"
URL = BASE + "/v1/chat/completions"
UA = "OpenAI/Python 1.109.1"


def post(body: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json", "authorization": f"Bearer {KEY}",
                 "user-agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(800).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


MSGS = [{"role": "user", "content": "ping"}]
TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "取当前时间",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]

CASES: list[tuple[str, dict]] = [
    ("① 非流式、不带 tools", {"model": MODEL, "messages": MSGS}),
    ("② 流式、不带 tools", {"model": MODEL, "messages": MSGS, "stream": True}),
    ("③ 流式 + stream_options.include_usage（mod 默认会发）",
     {"model": MODEL, "messages": MSGS, "stream": True,
      "stream_options": {"include_usage": True}}),
    ("④ 流式 + tools=[]", {"model": MODEL, "messages": MSGS, "stream": True, "tools": []}),
    ("⑤ 流式 + 真 tools", {"model": MODEL, "messages": MSGS, "stream": True, "tools": TOOLS}),
    ("⑥ tools=None（当前代码的报文）", {"model": MODEL, "messages": MSGS, "tools": None}),
    ("⑦ tool_choice=None", {"model": MODEL, "messages": MSGS, "tool_choice": None}),
    ("⑧ assistant content=''（多轮无文本）",
     {"model": MODEL, "messages": [{"role": "user", "content": "ping"},
                                   {"role": "assistant", "content": ""},
                                   {"role": "user", "content": "again"}], "stream": True}),
    ("⑨ tool 消息回灌",
     {"model": MODEL, "messages": [
         {"role": "user", "content": "现在几点"},
         {"role": "assistant", "content": "", "tool_calls": [
             {"id": "call_1", "type": "function",
              "function": {"name": "get_time", "arguments": "{}"}}]},
         {"role": "tool", "tool_call_id": "call_1", "content": "12:00"}],
      "tools": TOOLS, "stream": True}),
    ("⑩ 温度/上限等常见可选字段",
     {"model": MODEL, "messages": MSGS, "temperature": 0.7, "max_tokens": 64, "top_p": 1}),
]

for label, body in CASES:
    code, text = post(body)
    one = text.replace("\n", " ")[:200]
    print(f"[{code}] {label}")
    print(f"        {one}")

print("\n--- 流式内容的 SSE 字段名（看 mod 解析器认不认）---")
code, text = post({"model": MODEL, "messages": MSGS, "stream": True,
                   "stream_options": {"include_usage": True}})
print(f"[{code}] 前 3 个 data 块：")
for line in [l for l in text.splitlines() if l.startswith("data: ")][:3]:
    print("        ", line[:220])
