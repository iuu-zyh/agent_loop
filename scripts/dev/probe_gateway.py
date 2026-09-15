# -*- coding: utf-8 -*-
"""探测 8123 网关的流式 chunk 结构：reasoning 字段叫什么名、content 里有没有 <think> 标签。"""
import json, sys, io, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

body = json.dumps({
    "model": "mimo-v2.5-free",
    "messages": [{"role": "user", "content": "9.11 和 9.8 哪个大？先想一想再答，一句话。"}],
    "stream": True,
    "max_tokens": 1024,
}).encode("utf-8")

req = urllib.request.Request(
    "http://127.0.0.1:8123/v1/chat/completions",
    data=body,
    headers={"Content-Type": "application/json", "Authorization": "Bearer free"},
)

field_keys = set()   # 所有 delta 里出现过的键
think_samples = []
content_has_think_tag = False
n = 0
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                print("[DONE]")
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            field_keys.update(delta.keys())
            rc = delta.get("reasoning_content")
            r2 = delta.get("reasoning")
            c = delta.get("content")
            if rc or r2:
                if len(think_samples) < 3:
                    think_samples.append((rc or r2)[:60])
            if c and ("<think>" in c or "</think>" in c):
                content_has_think_tag = True
            n += 1
            if n <= 6:
                print("chunk", n, "delta keys:", sorted(delta.keys()),
                      "| reasoning_content:", (rc or "")[:30] if rc else None,
                      "| content:", (c or "")[:30] if c else None)
except Exception as e:
    print("探测失败:", type(e).__name__, e)
    sys.exit(0)

print()
print("总 chunk 数:", n)
print("delta 出现过的全部键:", sorted(field_keys))
print("reasoning_content 字段:", "有" if "reasoning_content" in field_keys else "无")
print("reasoning 字段:", "有" if "reasoning" in field_keys else "无")
print("content 内嵌 <think> 标签:", "有" if content_has_think_tag else "无")
print("think 样本:", think_samples if think_samples else "（无任何思考通道输出）")
