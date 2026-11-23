"""网关现状核查（只读，给文档/结论提供实测依据）。

用法： python scripts/dev/check_gateway.py <base_url> <api_key> [model ...]

三件事：
  1. `GET <base>/v1/models` —— 这个 key 到底能用哪些模型 id
  2. UA 差异 —— 有的网关前置了 Cloudflare，**完全不带 UA** 会吃 `403 error code: 1010`
     （那不是鉴权失败，key 是对的）
  3. 逐个模型非流式 ping —— 确认 id 拼对了

不传 model 时自动取列表前 5 个；也可以显式列出要 ping 的 id。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://<gateway>").rstrip("/")
KEY = sys.argv[2] if len(sys.argv) > 2 else ""
WANT = sys.argv[3:]


def req(url: str, body: dict | None = None, ua: str | None = "OpenAI/Python 1.109.1"):
    headers = {"authorization": f"Bearer {KEY}"}
    data = None
    if body is not None:
        headers["content-type"] = "application/json"
        data = json.dumps(body).encode()
    if ua:
        headers["user-agent"] = ua
    r = urllib.request.Request(url, data=data, headers=headers,
                               method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(800).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


print("=== GET /v1/models ===")
code, text = req(BASE + "/v1/models")
ids: list[str] = []
if code == 200:
    try:
        for m in (json.loads(text).get("data") or []):
            ids.append(str(m.get("id")))
            print(f"  {m.get('id')}")
    except Exception:
        print("  ", text[:400])
else:
    print(f"[{code}]", text[:300])

print("\n=== UA 与 Cloudflare 1010 ===")
for ua in (None, "python-httpx/0.28.1", "OpenAI/Python 1.109.1", "curl/8.7.1"):
    c, t = req(BASE + "/v1/models", ua=ua)
    print(f"  [{c}] UA={ua!r} {t[:80] if c != 200 else ''}")

print("\n=== 模型 id 逐个可用性（非流式 ping）===")
for mid in (WANT or ids[:5]):
    c, t = req(BASE + "/v1/chat/completions",
               {"model": mid, "messages": [{"role": "user", "content": "ping"}]})
    print(f"  [{c}] {mid} {'' if c == 200 else t[:140]}")
