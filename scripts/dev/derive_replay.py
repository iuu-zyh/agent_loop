# -*- coding: utf-8 -*-
"""复刻 session surface 投影 + derive，看左庚账本在 turn 2 请求时刻（seq<=16）的消息组成。"""
import json

events = []
with open(r"C:\Users\iu\.sessions\%E5%B7%A6%E5%BA%9A.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or '"session"' in line:
            continue
        events.append(json.loads(line))

SURFACE = {"user/message", "assistant/message", "tool/result"}

def derive(ev):
    t = ev.get("type")
    d = ev.get("data", {})
    if t == "user/message":
        return d
    if t == "assistant/message":
        msg = d.get("message", {})
        return msg if msg.get("content") else None
    if t == "tool/result":
        return d.get("message")
    return None

nodes = []
for ev in events:
    if ev.get("type") in SURFACE and ev.get("surfaceOp") == "append":
        nodes.append(ev["seq"])

print(f"surface nodes(seq): {nodes}")
print()
print("== turn2 请求时刻可 derive 的 canonical messages ==")
for seq in nodes:
    ev = events[seq] if seq < len(events) else None
    if ev is None or ev["seq"] > 16:   # 只看 turn2 generate 时刻之前已落账的
        continue
    m = derive(ev)
    if m is None:
        continue
    content = m.get("content")
    role = m.get("role")
    if isinstance(content, list):
        types = [c.get("type") for c in content if isinstance(c, dict)]
        txt = "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")[:50]
        print(f"  seq{seq:>2} [{role}] blocks={types} txt={txt!r}")
    else:
        print(f"  seq{seq:>2} [{role}] txt={str(content)[:50]!r}")
