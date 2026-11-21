#!/usr/bin/env python3
"""变异验证 —— Anthropic 协议内芯 + 协议自动识别（09-18 新增）。

**为什么必须有这个脚本**：这一层的失效形态**全都不出声** ——
  · system 没走顶层字段 / max_tokens 漏发 → 网关回一个 400，玩家看到"配好了却全哑"；
  · 工具结果没回指 tool_use_id → 模型第二轮开始胡言乱语（不报错）；
  · 用量口径漏加 cache_read → **账本静默算错**（缓存命中被算成负数/输入被算重）；
  · 流式不上报用量 → `LlmResult.usage` 恒为 None，token 统计永远空白；
  · 自动识别判据放宽 → 每次对话都白发两发请求，或把同一段话给玩家演两遍；
  · 骨架被抄一份 → 09-14/09-17 两次线上事故的修复以后只修一半。

覆盖的判据（回退 → 期望变红的用例）：
  ①~⑪    Anthropic 互译：system 顶层 / max_tokens 必发 / tool_result 回指 / 同角色合并且
         tool_result 前置 / tool_use 的 input 发对象 / 空文本块 / input_schema 形状 /
         图片 base64 与 url 两形态 / 认不出的图片丢弃
  ⑫~⑱   用量口径与截断：prompt_tokens 含 cache_read / 全零 → None / message_start 不算首字 /
         流式必须上报 / 被 max_tokens 顶断要出声
  ⑲~㉕   传输与解析：x-api-key / anthropic-version / URL 拼接 / SSE 半行缓冲 /
         input_json_delta 累积 / thinking 不混进正文 / 错误体先读全再退上下文 / close 幂等
  ㉖~㉛   自动识别：判据窄度（503 与网络错误不许切）/ 已推内容不许切 / 成功即锁定 /
         失败不反复试 / 两条都失败报第一条 / protocol_name 如实上报
  ㉜~㉟   工厂与配置：auto 包两层且 OpenAI 在前 / 显式协议不试探 / 拼错的 protocol 降级而非崩 /
         枚举与配置白名单逐字一致
  ㊱~㊳   探针：上报实际协议 / 尊重手编的 protocol
  ㊴~㊵   骨架复用：不许重写 generate/_call_stream，该覆盖的钩子必须覆盖
  ㊸~㊿   Responses 协议：instructions 顶层 / 工具条目独立且带 call_id / 工具定义扁平 /
         助手历史用 output_text / 用量口径**不**加 cache_read（与 Anthropic 相反）/
         done 事件不得重复计正文 / 参数只在没有片时才取 done 全量 / 不发 max_output_tokens
  ⑤①~⑤② 第三条协议的识别：auto 要能一路试到 responses 并锁定 / 存疑只拦"定性失败"

用法：
    python3 scripts/dev/mutate_check_llm_protocol.py            # 全部
    python3 scripts/dev/mutate_check_llm_protocol.py ⑦          # 只跑名字里含 ⑦ 的

注意：会**临时改源码再还原**（备份在系统临时目录）。两道保险绳：跑前先跑一次未变异基线
（源码树本来就不绿时直接退出 —— 否则"故障点"会被当成"变异被抓到"），跑完逐字节校验 md5。
末行必须是"全部变异都被捕获" + "还原校验通过"。**跑这个脚本时不要 Ctrl-C**。
"""
from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）

import hashlib
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / "llm" / "llm_adapter.py"
CLIENT = ROOT / "llm/anthropic_client.py"
RESP = ROOT / "llm/responses_client.py"
HTTP = ROOT / "llm/_http.py"
AUTO = ROOT / "llm/auto_client.py"
FACTORY = ROOT / "llm/factory.py"
PROBE = ROOT / "llm/probe.py"
STORE = ROOT / "config" / "config_store.py"
LOADER = ROOT / "config" / "config_loader.py"

PYTEST = [sys.executable, "-X", "utf8", "-m", "pytest",
          "tests/test_llm_anthropic.py", "tests/test_llm_responses.py",
          "tests/test_llm_protocol_auto.py", "tests/test_llm_probe.py",
          "tests/test_config_loader.py", "-q"]

MUTATIONS = [
    # ---------------- 互译 ----------------
    (
        "① system 塞进 messages 首条（Anthropic 不认 role:system，整趟 400）",
        ADAPTER,
        [('    return (system or ""), out, anth_tools',
          '    if system:\n        out.insert(0, {"role": "system", "content": system})\n'
          '    return (system or ""), out, anth_tools')],
    ),
    (
        "② tool_use_id 不回指（模型拿不到对应关系，第二轮开始胡言乱语且不报错）",
        ADAPTER,
        [('    blk: Dict[str, Any] = {"type": "tool_result", "tool_use_id": str(call_id),\n'
          '                           "content": [{"type": "text", "text": text}]}',
          '    blk: Dict[str, Any] = {"type": "tool_result",\n'
          '                           "content": [{"type": "text", "text": text}]}')],
    ),
    (
        "③ tool_use 的 input 发 JSON 字符串（Anthropic 要对象 → 整趟 400）",
        ADAPTER,
        [('                    tu: Dict[str, Any] = {"type": "tool_use", "id": blk.get("id") or _new_id(),\n'
          '                                          "name": blk.get("name", ""), "input": args}',
          '                    tu: Dict[str, Any] = {"type": "tool_use", "id": blk.get("id") or _new_id(),\n'
          '                                          "name": blk.get("name", ""),\n'
          '                                          "input": json.dumps(args, ensure_ascii=False)}')],
    ),
    (
        "④ 同角色不合并（Anthropic 要求 user/assistant 交替）",
        ADAPTER,
        [('        if out and out[-1]["role"] == role:',
          '        if False and out and out[-1]["role"] == role:')],
    ),
    (
        "⑤ 合并后 tool_result 不前置（Anthropic 硬要求工具结果在文字前）",
        ADAPTER,
        [('                merged = [b for b in blocks if b.get("type") == "tool_result"] + \\\n'
          '                         [b for b in prev_list if b.get("type") == "tool_result"] + \\\n'
          '                         [b for b in prev_list if b.get("type") != "tool_result"] + \\\n'
          '                         [b for b in blocks if b.get("type") != "tool_result"]',
          '                merged = prev_list + blocks')],
    ),
    (
        "⑥ 空文本块照发（Anthropic 拒收空 text 块）—— 工具结果那条消息里",
        ADAPTER,
        [('                elif c.get("type") in ("text", "reasoning"):\n'
          '                    txt = c.get("text", "")\n'
          '                    if txt:                       # Anthropic 不收空文本块\n'
          '                        others.append({"type": "text", "text": txt})',
          '                elif c.get("type") in ("text", "reasoning"):\n'
          '                    others.append({"type": "text", "text": c.get("text", "")})')],
    ),
    (
        "⑥b 空文本块照发 —— 普通 user 消息里",
        ADAPTER,
        [('                if t in ("text", "reasoning"):\n'
          '                    txt = c.get("text", "")\n'
          '                    if txt:\n'
          '                        blocks.append({"type": "text", "text": txt})',
          '                if t in ("text", "reasoning"):\n'
          '                    blocks.append({"type": "text", "text": c.get("text", "")})')],
    ),
    (
        "⑥c 同一条 user 消息里 tool_result 不前置（[文字, 工具结果] 是合法 Canonical）",
        ADAPTER,
        [('            _push("user", results + others)', '            _push("user", others + results)')],
    ),
    (
        "⑦ 工具定义发 parameters 而不是 input_schema（Anthropic 认不出工具）",
        ADAPTER,
        [('            item: Dict[str, Any] = {"name": fn.get("name", ""),\n'
          '                                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}}',
          '            item: Dict[str, Any] = {"name": fn.get("name", ""),\n'
          '                                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}')],
    ),
    (
        "⑧ 图片用 url 形态塞 data URI（Anthropic 只认 base64/url 两种，data URI 不是 url）",
        ADAPTER,
        [('        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}',
          '        return {"type": "image", "source": {"type": "url", "url": s}}')],
    ),
    (
        "⑨ 认不出的图片来源照发坏结构（整趟请求 400，比少一张图严重得多）",
        ADAPTER,
        [('    if s.startswith(("http://", "https://")):\n'
          '        return {"type": "image", "source": {"type": "url", "url": s}}\n'
          '    return None',
          '    return {"type": "image", "source": {"type": "url", "url": s}}')],
    ),
    (
        "⑩ text 块用换行拼接（与流式的空串 join 口径不一致 → 两条路产出不同）",
        ADAPTER,
        [('    return "".join(texts), normalize_anthropic_tool_calls(tool_blocks), "".join(think), stop_reason',
          '    return "\\n".join(texts), normalize_anthropic_tool_calls(tool_blocks), "".join(think), stop_reason')],
    ),
    (
        "⑪ content 是字符串时不认（有些代理会把 content 压成字符串 → 正文整段丢）",
        ADAPTER,
        [('    if isinstance(content, str):\n        return content, [], "", stop_reason',
          '    if isinstance(content, str):\n        return "", [], "", stop_reason')],
    ),
    # ---------------- 用量与截断 ----------------
    (
        "⑫ 用量漏加 cache_read（★账本静默算错★：缓存命中被减成负数）",
        CLIENT,
        [('    inp = _i("input_tokens") + cache_read + cache_write',
          '    inp = _i("input_tokens")')],
    ),
    (
        "⑬ 全零用量当成真用量上报（「没有用量」与「零用量」混为一谈）",
        CLIENT,
        [('    if inp == 0 and out == 0:\n        return None                     # 全零 = 没有可用用量，别往账本里塞个 0',
          '    if False:\n        return None')],
    ),
    (
        "⑭ 用量只认首份（message_start 那份是 0，真用量在 message_delta → 统计永远空白）",
        CLIENT,
        [('            cur = self._usage.get(k)\n'
          '            self._usage[k] = v if cur is None else max(cur, v)',
          '            cur = self._usage.get(k)\n'
          '            self._usage[k] = cur if cur is not None else v')],
    ),
    (
        "⑮ 流式不上报用量（usage 恒为 None，token 统计永远空白且不报错）",
        CLIENT,
        [('        if self._usage_sent or not self._usage:\n            return',
          '        if True or self._usage_sent or not self._usage:\n            return')],
    ),
    (
        "⑯ 被 max_tokens 顶断不出声（玩家只看到「话说了半句」，日志里什么都没有）",
        CLIENT,
        [('        if stop_reason == "max_tokens":\n            log.warning(',
          '        if False:\n            log.warning(')],
    ),
    (
        "⑰ max_tokens 缺省发 0（Anthropic 必填且必须为正 → 直接 400）",
        CLIENT,
        [('DEFAULT_MAX_TOKENS = 8192', 'DEFAULT_MAX_TOKENS = 0')],
    ),
    (
        "⑱ 报文体里不带 max_tokens（Anthropic 必填）",
        CLIENT,
        [('        body: Dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens,\n'
          '                                "messages": wire_messages}',
          '        body: Dict[str, Any] = {"model": self.model, "messages": wire_messages}')],
    ),
    # ---------------- 传输与解析 ----------------
    (
        "⑲ 鉴权只用 Authorization: Bearer（opencode 的 /messages 回 401 Missing API key）",
        CLIENT,
        [('                auth_headers={"x-api-key": self.api_key} if self.api_key else {},',
          '                auth_headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},')],
    ),
    (
        "⑳ 不发 anthropic-version（真 Anthropic 端点直接 400）",
        CLIENT,
        [('                extra_headers={**{"anthropic-version": ANTHROPIC_VERSION}, **(self.headers or {})},',
          '                extra_headers={**(self.headers or {})},')],
    ),
    (
        "㉑ URL 无条件补 /v1（base_url 已带 /v1 时会双拼 → 404）",
        HTTP,
        [('        if base.rsplit("/", 1)[-1].startswith("v") and base.rsplit("/", 1)[-1][1:].isdigit():\n'
          '            return base + path\n'
          '        return base + "/v1" + path',
          '        return base + "/v1" + path')],
    ),
    (
        "㉒ SSE 不做半行缓冲（一行跨两个 chunk 时该行整条丢）",
        CLIENT,
        [('            line, sep, rest = self._buf.partition("\\n")\n'
          '            if not sep:\n'
          '                break                       # 半行：留到下一个 chunk',
          '            line, sep, rest = self._buf.partition("\\n")\n'
          '            if not sep:\n'
          '                self._buf = ""\n'
          '                break')],
    ),
    (
        "㉓ input_json_delta 不累积（工具参数永远空对象 → 工具被调成瞎猜）",
        CLIENT,
        [('            elif dtype == "input_json_delta":\n'
          '                part = delta.get("partial_json")\n'
          '                if part:\n'
          '                    yield ("tool", [{"index": idx, "function": {"arguments": part}}])',
          '            elif dtype == "input_json_delta":\n'
          '                pass')],
    ),
    (
        "㉔ thinking_delta 当正文发（思考内容混进 NPC 台词）",
        CLIENT,
        [('            elif dtype == "thinking_delta":\n'
          '                think = delta.get("thinking") or ""\n'
          '                if think:\n'
          '                    yield ("think", think)',
          '            elif dtype == "thinking_delta":\n'
          '                think = delta.get("thinking") or ""\n'
          '                if think:\n'
          '                    yield ("text", think)')],
    ),
    (
        "㉕ 错误响应不读 body 就退出上下文（连接不还池；fd 在 5xx 风暴里被吃光）",
        HTTP,
        [('                    try:\n                        resp.read()\n'
          '                    except Exception:  # noqa: BLE001\n                        pass\n',
          '                    pass\n')],
    ),
    (
        "㉖ tool_use 的 id/name 不来自 content_block_start（工具调用没有身份，结果回不去）",
        CLIENT,
        [('            if btype == "tool_use":\n'
          '                # 只报"这个索引是工具、它叫什么"；参数留给后续 partial_json 片\n'
          '                yield ("tool", [{"index": idx, "id": blk.get("id"),\n'
          '                                 "function": {"name": blk.get("name"), "arguments": ""}}])',
          '            if btype == "tool_use":\n'
          '                yield ("tool", [{"index": idx, "function": {"arguments": ""}}])')],
    ),
    # ---------------- 自动识别 ----------------
    (
        "㉗ 判据放宽到「任何 5xx 都切」（503 上游抖动也切 → 每次对话白发两发请求）",
        AUTO,
        [('    return status == 500 and _BARE_500_HINT in low and len(low) < 400',
          '    return status >= 500')],
    ),
    (
        "㉘ 判据不要求 status（网络超时也切 → 等待翻倍）",
        AUTO,
        [('    status = getattr(e, "status_code", None)\n'
          '    if not isinstance(status, int):\n'
          '        return False',
          '    status = getattr(e, "status_code", None)\n'
          '    if not isinstance(status, int):\n'
          '        return True')],
    ),
    (
        "㉙ 已推内容后照样切协议（★同一段话被演两遍★）",
        AUTO,
        [('                if last or not probing or pushed["n"] > 0 or not mis:',
          '                if last or not probing or not mis:')],
    ),
    (
        "㉚ 识别成功后不锁定（每个回合都重跑一次试探）",
        AUTO,
        [('            if i > 0:\n'
          '                self._locked = getattr(c, "protocol_name", None)',
          '            if False:\n'
          '                self._locked = getattr(c, "protocol_name", None)')],
    ),
    (
        "㉛ 识别失败后不设存疑态（两条路都不通的模型每次对话都白发两发）",
        AUTO,
        [('                self._suspect = True\n', '                self._suspect = False\n')],
    ),
    (
        "㉜ 两条都失败时报第二条（用户看到的是自己没配的那条路的文案）",
        AUTO,
        [('                    if i > 0 and last:\n'
          '                        log.warning("协议识别的候选（%s）也失败：%s", name, str(e)[:200])\n'
          '                    # **报回第一条原始错误**（它对应配置里写的那条路，文案稳定，面板的归类与\n'
          '                    # 建议也是照它给的）；后续几条的原文挂进日志与 __cause__，排障时都在。\n'
          '                    raise first_err from e if first_err is not e else e',
          '                    raise e')],
    ),
    (
        "㉝ protocol_name 恒为 auto（探针/日志里看不出实际在用哪条协议）",
        AUTO,
        [('        return self._locked or "auto"', '        return "auto"')],
    ),
    # ---------------- 工厂与配置 ----------------
    (
        "㉞ auto 干脆不包（只在 /messages 或 /responses 上的模型照样全哑 —— 本功能的全部意义）",
        FACTORY,
        [('        return AutoProtocolLlmClient(\n'
          '            [openai_client,\n'
          '             AnthropicLlmClient(client=client, max_tokens=max_tokens, **common),\n'
          '             OpenAIResponsesLlmClient(client=client, **common)],\n'
          '            primary_name="openai")',
          '        return openai_client')],
    ),
    (
        "㉟ 候选顺序反了（默认先打 Anthropic → 绝大多数 OpenAI 兼容网关都要白跑）",
        FACTORY,
        [('            [openai_client,\n'
          '             AnthropicLlmClient(client=client, max_tokens=max_tokens, **common),',
          '            [AnthropicLlmClient(client=client, max_tokens=max_tokens, **common),\n'
          '             openai_client,')],
    ),
    (
        "㊱ 拼错的 protocol 直接抛（一个手滑就让整个 mod 起不来）",
        FACTORY,
        [('        log.warning("llm.protocol=%r 不认识（可选：%s）→ 按 auto 处理（自动识别）",\n'
          '                    protocol, " / ".join(PROTOCOLS))\n'
          '        proto = "auto"',
          '        raise ValueError(f"llm.protocol 不认识: {protocol!r}")')],
    ),
    (
        "㊲ 配置白名单丢掉 protocol（面板一保存，用户手编的协议就没了）",
        STORE,
        [('            "protocol", "max_tokens"),', '            ),')],
    ),
    (
        "㊳ protocol 枚举与工厂漂开（用户能存进一个工厂不认的值）",
        LOADER,
        [('        "protocol": "auto",', '        "protocol": "chat",')],
    ),
    # ---------------- 探针 ----------------
    (
        "㊴ 探针不上报实际协议（面板里模型名一换就成功时，看不到「为什么」）",
        PROBE,
        [('    state["protocol"] = getattr(client, "protocol_name", None)\n'
          '    return _out(True, reply=text[:200], usage=getattr(result, "usage", None))',
          '    return _out(True, reply=text[:200], usage=getattr(result, "usage", None))')],
    ),
    (
        "㊵ 探针不把 protocol 传给工厂（手编的 protocol 在「测试连接」里被无视）",
        PROBE,
        [('                                   protocol=protocol)',
          '                                   None)')],
    ),
    # ---------------- Responses 协议 ----------------
    (
        "㊸ instructions 塞进 input（Responses 的 system 是顶层字段）",
        ADAPTER,
        [('    return (system or ""), items, resp_tools',
          '    if system:\n        items.insert(0, {"role": "system", "content": [{"type": _RESP_TEXT, "text": system}]})\n'
          '    return (system or ""), items, resp_tools')],
    ),
    (
        "㊹ 工具调用参数发对象而不是字符串（Responses 的 arguments 是字符串化的 JSON）",
        ADAPTER,
        [('                    if isinstance(args, dict):\n'
          '                        args = json.dumps(args, ensure_ascii=False)',
          '                    if isinstance(args, str):\n'
          '                        try:\n                            args = json.loads(args)\n'
          '                        except Exception:\n                            pass')],
    ),
    (
        "㊺ 工具结果塞进 role 消息而不是独立 function_call_output 条目",
        ADAPTER,
        [('                    items.append({"type": "function_call_output", "call_id": str(call_id),\n'
          '                                  "output": _extract_text_from_tool_result_content(c.get("content", ""))})',
          '                    texts.append({"type": _RESP_TEXT,\n'
          '                                  "text": _extract_text_from_tool_result_content(c.get("content", ""))})')],
    ),
    (
        "㊻ 工具定义套 function 键（Responses 要扁平结构）",
        ADAPTER,
        [('            item: Dict[str, Any] = {"type": "function", "name": fn.get("name", ""),\n'
          '                                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}',
          '            item: Dict[str, Any] = {"type": "function", "function": {"name": fn.get("name", ""),\n'
          '                                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}}')],
    ),
    (
        "㊼ 助手历史用 input_text 回放（规范形态是 output_text）",
        ADAPTER,
        [('                        blocks.append({"type": _RESP_OUT_TEXT, "text": txt})',
          '                        blocks.append({"type": _RESP_TEXT, "text": txt})')],
    ),
    (
        "㊽ 用量口径照抄 Anthropic（把 cache_read 再加进 prompt_tokens → ★账本静默算错★）",
        RESP,
        [('    d: Dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion,',
          '    _cr = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0\n'
          '    prompt = prompt + (int(_cr) if isinstance(_cr, (int, float)) else 0)\n'
          '    d: Dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion,')],
    ),
    (
        "㊾ output_text.done 的合并文本再发一次（★同一段话演两遍★）",
        RESP,
        [('        if etype == "response.output_text.delta":',
          '        if etype == "response.output_text.done":\n'
          '            if ev.get("text"):\n'
          '                yield ("text", str(ev["text"]))\n'
          '            return\n'
          '        if etype == "response.output_text.delta":')],
    ),
    (
        "㊿ 工具参数既收片又收 done 全量（拼两遍 → 参数 JSON 解不动）",
        RESP,
        [('                if idx not in self._args_seen and item.get("arguments"):',
          '                if item.get("arguments"):')],
    ),
    (
        "⑤① 报文体带上 max_output_tokens（推理 token 计入 output，卡小了截断长思考）",
        RESP,
        [('        body: Dict[str, Any] = {"model": self.model, "input": wire_messages}',
          '        body: Dict[str, Any] = {"model": self.model, "input": wire_messages,\n'
          '                                "max_output_tokens": self.max_tokens}')],
    ),
    (
        "⑤② auto 不再尝试第三条协议（只在 /responses 上的模型照样全哑）",
        FACTORY,
        [('             OpenAIResponsesLlmClient(client=client, **common)],',
          '             ],')],
    ),
    (
        "⑤③ 存疑把瞬时失败也算进去（503 抖动后再也不给识别机会 → 永远卡在错协议上）",
        AUTO,
        [('                    if switched and not _transient(e):',
          '                    if switched:')],
    ),
    # ---------------- 骨架复用 ----------------
    (
        "㊶ Anthropic 侧抄一份 generate（可靠性骨架从此有两份，以后只会修一半）",
        CLIENT,
        [('    # ---------- 传输 ----------\n'
          '    def _get_transport(self) -> Any:',
          '    async def generate(self, system, messages, tools, on_token=None, on_reasoning=None):\n'
          '        return await super().generate(system, messages, tools, on_token=on_token,\n'
          '                                      on_reasoning=on_reasoning)\n\n'
          '    # ---------- 传输 ----------\n'
          '    def _get_transport(self) -> Any:')],
    ),
    (
        "㊷ 漏覆盖 _event_parser（拿 OpenAI 的 chunk 解析器去读 Anthropic 的 SSE → 整趟零产出）",
        CLIENT,
        [('    def _event_parser(self):\n'
          '        # 流式路径的 stop_reason 只在 message_delta 里出现一次，而骨架不认这个字段\n'
          '        # （它只认 usage/text/think/tool 四种 kind），所以截断判定就地做在解析器里。\n'
          '        return _AnthropicEventParser(on_stop_reason=self._warn_if_truncated)',
          '    pass')],
    ),
]


def _backup(path: Path) -> Path:
    import tempfile

    fd, name = tempfile.mkstemp(prefix="mutbak_", suffix=path.name)
    with open(fd, "wb") as f:
        f.write(path.read_bytes())
    return Path(name)


def _check(name: str, path: Path, repls) -> bool:
    bak = _backup(path)
    try:
        src = path.read_text(encoding="utf-8")
        for old, new in repls:
            if old not in src:
                print("  ✗ %s：待替换片段不存在（脚本要更新）：%r" % (name, old[:70]))
                return False
            src = src.replace(old, new)
        path.write_text(src, encoding="utf-8")
        r = _mutate_lib.run(PYTEST, cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if r.returncode == 0:
            print("  ✗ %s：测试仍然通过 —— 这条判据没有被任何用例覆盖" % name)
            return False
        out = (r.stdout or "") + (r.stderr or "")
        red = re.findall(r"^FAILED \S+::(\S+)", out, re.M)
        print("  ✓ %s：测试如实变红（%s）" % (name, "、".join(red[:3]) or "见输出"))
        return True
    finally:
        shutil.copy2(bak, path)


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def main() -> int:
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    picked = [(n, p, r) for n, p, r in MUTATIONS if not only or any(o in n for o in only)]
    files = sorted({p for _n, p, _r in picked})
    _mutate_lib.guard(files)   # 被中断也要还原（见 _mutate_lib.guard）
    before = {p: _md5(p) for p in files}
    print("[Python 契约 · tests/test_llm_anthropic.py + test_llm_protocol_auto.py + "
          "test_llm_probe.py + test_config_loader.py]"
          + ("  仅跑：%s" % "、".join(only) if only else ""))

    # ── 基线：先跑一次**未变异**的测试（红了说明源码树本来就不绿，后面每条"变红"都不成立）──
    base = _mutate_lib.run(PYTEST, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    if base.returncode != 0:
        print("★ 基线就是红的 —— 先修源码，变异结果不可信：")
        print((base.stdout or "")[-2000:])
        return 2
    print("  基线绿（未变异的源码树本来通过）")

    ok = True
    for name, path, repls in picked:
        ok &= _check(name, path, repls)

    # ── 收尾：逐字节还原校验 ──
    after = {p: _md5(p) for p in files}
    if after != before:
        for p in files:
            if after[p] != before[p]:
                print("★ 未还原：%s（%s → %s）" % (p.relative_to(ROOT), before[p], after[p]))
        ok = False
    else:
        print("  逐字节还原校验通过（%d 个文件 md5 一致）" % len(files))

    print()
    print("全部 %d 个变异都被捕获" % len(picked) if ok
          else "★ 有变异未被捕获 / 有残留 —— 补测试或修实现")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
