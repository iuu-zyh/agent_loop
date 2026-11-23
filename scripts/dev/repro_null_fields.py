"""复现：严格 schema 网关拒收 SDK 序列化出来的 null 字段。

做法：起一个本地假网关，把收到的请求体原样打印，再回一个最小合法 SSE。
判定：请求体里出现任何值为 null 的键 = 复现成功。

用法： python scripts/dev/repro_null_fields.py
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

CAPTURED: list = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: A003
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {"_raw": raw.decode("utf-8", "replace")}
        CAPTURED.append((self.path, dict(self.headers), body))

        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            chunk = {"id": "x", "object": "chat.completion.chunk", "model": "m",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": "pong"}}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            done = {"id": "x", "object": "chat.completion.chunk", "model": "m",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            self.wfile.write(b"data: " + json.dumps(done).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        resp = {"id": "x", "object": "chat.completion", "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        payload = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def null_paths(obj, prefix=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if v is None:
                out.append(p)
            else:
                out.extend(null_paths(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(null_paths(v, f"{prefix}[{i}]"))
    return out


async def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from agent_loop.llm.openai_client import OpenAILlmClient

    client = OpenAILlmClient(
        base_url=f"http://127.0.0.1:{port}/v1", api_key="sk-test", model="test-model",
        retries=0, timeout=10, timeout_nonstream=10,
    )
    msgs = [{"role": "user", "content": [{"type": "text", "text": "ping"}]}]

    print("=" * 70)
    print("A. 流式 + tools=[]（探针那条路）")
    got = []
    await client.generate("sys", msgs, tools=[], on_token=lambda t: got.append(t) or asyncio.sleep(0))
    print("   收到:", "".join(got))

    print("=" * 70)
    print("B. 流式 + tools=None（无工具回合）")
    got2 = []
    await client.generate("sys", msgs, tools=None, on_token=lambda t: got2.append(t) or asyncio.sleep(0))
    print("   收到:", "".join(got2))

    print("=" * 70)
    print("C. 非流式 + tools=None（压缩那条路）")
    r = await client.generate("sys", msgs, tools=None)
    print("   收到:", r.text)

    print("=" * 70)
    bad = 0
    for i, (path, headers, body) in enumerate(CAPTURED):
        nulls = null_paths(body)
        print(f"[请求 {i}] path={path}  null 字段={nulls or '无'}")
        if nulls:
            bad += 1
            print("        body 摘要:", json.dumps({k: body[k] for k in body if k != 'messages'},
                                                  ensure_ascii=False)[:300])
    print("=" * 70)
    print(f"结论：{bad}/{len(CAPTURED)} 个请求带 null 字段" + ("（复现成功）" if bad else "（无问题）"))
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
