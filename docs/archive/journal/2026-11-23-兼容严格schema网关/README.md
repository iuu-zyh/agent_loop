> ⚠ **已归档**。现状见 [`docs/features/llm-gateway-compat.md`](../../../features/llm-gateway-compat.md)。
> 本文只保留当时的实测矩阵与被推翻的假设，**追溯「这个结论怎么来的」时才需要读**。
>
> **结局**：`llm/openai_client.py` 改成「没有工具就把 `tools`/`tool_choice` 整对省掉」，
> 严格 schema 网关（实测的那家 `/provider/v1`）从必然 400 变为 200。
>
> - **起止**：2026-11-23 ～ 2026-11-23
> - **触发**：玩家侧 NPC 只回兜底台词，`logs/agent_loop.log` 里一行
>   `400 - {'error': {'message': 'Invalid input: expected array, received null', 'param': 'tools'}}`
> - **涉及**：`llm/openai_client.py`、`llm/llm_adapter.py:191`、`llm/_http.py`

# 兼容严格 schema 网关（排查详文）

## 一、用户假设是 header，实际不是

玩家最初的判断是"需要补一个 HTTP 头"。日志里确实有一串试探：

```
model test: ... headers=['x-cli-environment']
model test: ... headers=['x-cmd-zdr']
model test: ... headers=['x-cli-environment','x-cmd-zdr','x-command-code-version','x-project-slug','x-taste-learning']
```

三条全绿？不 —— 三条都没改变结果。用 curl 对这五种头做笛卡尔组合再打
`tools: null`，**一律 400**，报错原文一字不差。头这条路走死了。

同一批试探里还混着两个**独立的**错，容易被一起误判成"配置不对"：

| 现象 | 真因 |
|---|---|
| `404 ... /chat/completions/chat/completions is not a registered API route` | `base_url` 已经写到 `/chat/completions`，客户端又拼了一次 |
| `Model "DeepSeek V4.1 Flash Fast (latest)" is not supported on this endpoint.` | 用了面板显示名，网关只认 API id（`<provider>/<model-id>`） |
| `401 Invalid 'Authorization' header or token` | key 填错/多空格，与上面两条无关 |

## 二、真因：SDK 把 None 序列化成 null

`llm/llm_adapter.py:191` 把空工具列表归一成 `None`：

```python
oai_tools = tools if tools else None
```

`llm/openai_client.py` 的 `_call` / `_open_stream` 再把它**显式**传给 SDK：

```python
tools=oai_tools,
tool_choice="auto" if oai_tools else None,
```

关键事实：**OpenAI Python SDK 不会因为参数是 `None` 就省掉这个键**，
它发的是 `"tools": null`。宽松网关无视它，严格 schema 网关直接拒。

实测（真网关，`POST /provider/v1/chat/completions`）：

| 请求体 | 结果 |
|---|---|
| `"tools": null` | **400** `Invalid input: expected array, received null`，param `tools` |
| `"tools": []` | 200 |
| 不写 `tools` | 200 |
| `"tools": [<真 function>]` | 200，`tool_calls` 正常 |
| `"tool_choice": null` | **400** `Invalid input`，param `tool_choice` |
| `"temperature": null` / `stop` / `max_tokens` / `stream_options` / `response_format` / `presence_penalty` 为 null | 全部 **400**，只报第一个 |
| `"messages[].content": null`（assistant + tool_calls 的标准形态） | **允许**，不能剥 |

所以这不是本仓独有的坑：**任何**传 `tools=None` 的 OpenAI 兼容客户端打这个网关都会 400。
"本轮没有可用工具"是完全正常的情形（探针 `tools=[]`、纯聊天回合、压缩摘要），
命中率其实很高 —— 表现却是玩家侧一句「传音法阵没接上」。

## 三、修法

`llm/openai_client.py` 新增 `_tool_kwargs()`：

```python
def _tool_kwargs(self, oai_tools):
    if not oai_tools:
        return {}
    return {"tools": oai_tools, "tool_choice": "auto"}
```

`_call` / `_open_stream`（含 `stream_options` 的 `TypeError` 回退分支）/
`chat_completions_create`（两处）共 5 个调用点改成 `**tool_kw`。

为什么是"整对省略"而不是"只把 None 换成 []"：不发 `tools` 就等于"没有工具"，
这是 OpenAI 兼容网关的共识；发 `[]` 也能过这个网关，但会有网关把它当"给了空工具集"
而拒绝 `tool_choice: auto`。语义上不需要那对键。

## 四、验证

1. **本地假网关**（`scripts/dev/repro_null_fields.py`，`ThreadingHTTPServer` 捕获请求体）：
   修前 3/3 请求带 `null 字段=['tool_choice','tools']`，修后 **0/3**，
   三条路径（流式+`tools=[]`、流式+`tools=None`、非流式+`tools=None`）全部正常返回。
2. **真网关字段矩阵**（`scripts/dev/probe_strict_gateway.py`，10 项）：见上表，全部符合预期；
   SSE `delta` 字段是 `reasoning_content`（思考）+ `content`（正文），本仓解析器两条都认。
3. **UA**：`UA=None` 会被 Cloudflare 拦成 `403 error code: 1010`；
   SDK 默认 UA（`OpenAI/Python 1.109.1`）、`python-httpx/0.28.1`、`curl/8.7.1` 均 200
   → **不需要额外 User-Agent 头**。
4. **端到端**（`scripts/dev/e2e_gateway.py`，用本仓自己的 `OpenAILlmClient`）：
   探针 `ok=True`；流式无工具出正文；流式带真工具 `tool_calls` 正常；
   非流式压缩路径正常出摘要。
5. **契约测试**：`tests/test_llm_no_null_fields_contract.py` 5 条。
   变异验证：把 `_tool_kwargs` 改回返回 `{"tools": None, "tool_choice": None}`
   → **4 failed / 1 passed**（唯一该绿的是"有工具时仍要发"，已还原）。
   第一轮变异只红了 3 条，因为源码契约当时只查 `tools=oai_tools`、
   没查 `"tools": None` —— 补断言后才抓住，记在这里免得下次又漏。
6. **真机**：`scripts/server.py` + WS 客户端打真网关 `<model-id>`，
   一轮对话拿到正文「在的。传音一道我向来不多用，道友寻我，可是有事？」，日志无 400。

## 五、这一轮不算证据的东西

- **`x-cmd-zdr` / `x-cli-environment` 这类头**：文档里 `x-cmd-zdr: 1` 只是"零数据留存"开关，
  与 400 无关。加不加都 400，加了反而会让后来者以为它有用。
- **`F:\` 盘上的旧构建目录**：`scripts/pack_release.py` 的默认 `BUILD_DIR` 落在仓库旁，
  而 `docs/PACKAGING.md` 里的示例路径是别人的开发机。照抄路径会得到"目录不存在"，
  不是打包脚本坏了。
- **全量测试"全绿"**：本仓 `main` 自带 8 项既有红（见 CHANGELOG 那条的"未验证"），
  在未采 `.depmap.json` 快照时还有 17 条 `test_run_tests_for_contract.py` 的 ERROR。
  本次只用"新增 0 红"作判据，没有为了让全量变绿去动无关文件。
