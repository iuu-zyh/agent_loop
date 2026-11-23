# LLM 网关协议兼容 —— 实现现状

> **一句话**：客户端只发网关认得的形状 —— **没有工具就把 `tools`/`tool_choice` 整对省掉**，
> 不把 `None` 序列化成 `null`；`base_url` 填到 `/v1` 那一层就够，模型名必须用 API id。

> 这一条的来历（10 项字段矩阵、被推翻的 header 假设）在
> [`docs/archive/journal/2026-11-23-兼容严格schema网关/`](../archive/journal/2026-11-23-兼容严格schema网关/README.md)，
> **日常开发不需要读**。

---

## 0. 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| 客户端自己保证「不发明网关不认的键」 | 靠网关宽容（严格 schema 的网关直接 400） |
| 三种协议各发自己的形状（openai / anthropic / responses） | 「统一都发 OpenAI 形状」（`/messages` 与 `/responses` 各有一套） |
| 报错原文进日志，配置面板「测试连接」复现同一发 | 静默失败（本仓明令禁止） |

---

## 1. 三条硬规则

### ① 空工具列表 → 整对省略，绝不发 `null`

`llm/openai_client.py` 的 `_tool_kwargs()`：

```python
if not oai_tools:
    return {}
return {"tools": oai_tools, "tool_choice": "auto"}
```

为什么不能用 SDK 的默认行为：**OpenAI Python SDK 对 `tools=None` 的处理是把它序列化成
`"tools": null`**，不是省略这个键。宽松网关无视，严格 schema 的网关直接 400：

```json
{"error":{"message":"Invalid input: expected array, received null","type":"invalid_request_error","param":"tools"}}
```

而"本轮没有工具"是**正常**情形 —— 探针（`tools=[]`）、纯聊天回合、压缩摘要都走这条。
在这些网关上，旧行为等于这些场景必然失败，症状是玩家侧一句「传音法阵没接上」，
日志里只有一行 400，**与配置对不对毫无关系**。

`tool_choice: null` 同样被拒（`Invalid input`, param `tool_choice`），所以两个键必须成对。

同样的形状在另外两条协议里早就对了，别去改：`anthropic_client.py` 与 `responses_client.py`
的 `_body` 都是 `if wire_tools: body["tools"]` —— 空列表根本不进请求体。

### ② `base_url` 填到哪一层

`llm/_http.py` 的 `HttpxSseTransport.url()` 判据：

| `base_url` 形态 | 实际请求 |
|---|---|
| 已经以目标 path 结尾 | 原样 |
| 末段是 `v<数字>`（如 `.../provider/v1`） | `base + path` |
| 其它 | `base + "/v1" + path` |

所以 **`https://<gateway>/provider/v1` 是正确填法**。
填成 `.../provider/v1/chat/completions` 会变成 `.../chat/completions/chat/completions`，
网关回 `404 ... is not a registered API route`。

### ③ 模型名用 API id，不用面板显示名

面板显示名（`DeepSeek V4.1 Flash Fast (latest)`）发过去是
`400 Model "..." is not supported on this endpoint.` code `unsupported_model`。
正确形态是 `<provider>/<model-id>` 这种带厂商前缀的 id。列表从 `GET /provider/v1/models` 拿。

---

## 2. 严格 schema 网关还会拒什么

实测同一个网关（`/provider/v1` 这一家）：**任何字段值为 `null` 都直接 400，
而且只报第一个**。

| 字段 | `null` 时 |
|---|---|
| `tools` / `tool_choice` / `parallel_tool_calls` | 400 `Invalid input` |
| `temperature` / `top_p` / `presence_penalty` / `max_tokens` / `stop` | 400 `Invalid input: expected number` 等 |
| `stream_options` / `response_format` | 400 |
| `messages[].content` | **允许**（assistant + `tool_calls` 的标准形态）——不能剥 |

唯一例外是 `messages[].content`。除此之外，"值为 None 就当没传"是安全默认。

---

## 3. 报错怎么读

配错时的四个典型症状与对应处置：

| 报错原文 | 真因 | 处置 |
|---|---|---|
| `Invalid input: expected array, received null` + `param: tools` | 客户端发了 `tools: null` | 本文件的 ①；旧版本才有 |
| `... /chat/completions/chat/completions is not a registered API route` | `base_url` 写太深 | ② |
| `Model "X" is not supported on this endpoint.` | 用了显示名 | ③ |
| `403 error code: 1010` | 请求没带 User-Agent，被前置 Cloudflare 拦 | 在 `headers` 加一条 `User-Agent: ...`（SDK 默认 UA 不会被拦） |

最后一条值得单独记：`403 1010` **不是鉴权失败**，key 是对的。
它只在"完全不带 UA"时出现 —— 用 `urllib` 裸打会遇到，用 `openai` SDK 不会。

---

## 4. 谁读这些判据

| 位置 | 作用 |
|---|---|
| `llm/openai_client.py:_tool_kwargs` | ① 的唯一实现点（5 个调用点共用） |
| `llm/_http.py:url()` | ② 的唯一实现点（三条协议共用） |
| `llm/probe.py` | 面板「测试连接」：`tools=[]` 真发一次，把网关原文回填面板 |
| `tests/test_llm_no_null_fields_contract.py` | 钉住 ①：源码形状 + 4 条行为 |

`probe.py` 的 `classify` 把 400/422 归 `bad_request`，`_refine_hint` 再按原文特化
（`model not supported` / Cloudflare 1010 / …）。新增网关特有的报错时，**加在 `_refine_hint`**，
不要在调用方打补丁。
