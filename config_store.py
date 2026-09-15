"""config_store — config.json 写回服务（唯一写侧；读侧仍归 config_loader，各司其职）

分层分权：
- config_loader：唯一负责「文件 → dict」（只读，探测路径/深合并/旧键兼容）——不动。
- config_store：唯一负责「UI 改动 → 文件」（写回）。读当前值时借道 config_loader，
  写时读原文件 JSON、只更新白名单键、保留用户其余未动键。
- 装配层（scripts/server.py）负责组合：set_config 后按 hot_keys 逐键落地——llm 块换
  LlmRouter 内芯、compaction 块重建 Compressor、initiative.min_interval 改 ChatHub 属性、
  network.request_timeout 改 WsServer 属性；其余键（network 的 host/port 等）装配期一次性
  传参，需重启。

约定：
- 白名单外的键一律拒绝（fail-closed）：UI 表单只暴露确认过的字段，防止误写坏配置。
- 原 JSON 非法时拒绝写入（防覆盖丢键），提示手工修复。
- 写回格式：indent=2 + ensure_ascii=False（中文原样，与现有 config.json 一致）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from . import log_setup
from .config_loader import _CONFIG_CANDIDATES, load_config
from . import textio as _textio

log = log_setup.get_logger(__name__)

# UI 可见可改的白名单：块 → 键集合（cool_down 等专业字段不暴露，手改文件）
# ui.show_advanced 仅是「面板显隐开关」，见 config_loader.DEFAULT_CONFIG["ui"] 注释
UI_WHITELIST: Dict[str, tuple] = {
    # headers（进白名单）：可选的自定义 HTTP 头，默认空。**后端早就支持**
    # （config_loader 透传 → llm/factory.py:117 → openai_client 的 SDK default_headers），
    # 缺的只是"面板能改"这条路 —— 在此之前它只能手编 config.json，而面板保存又不会碰它。
    # 空 dict = 明确「不要附加头」，写回时会把该键删掉（见 set_config），不留 "headers": {}。
    #
    # 起 timeout / timeout_nonstream / total_budget / retries 也进白名单（用户要求"面板里能改"）。
    # 它们本来被归为"专业字段、手改文件"，但改完超时才能治「一等六分钟且零报错」，藏起来不合适。
    # 进白名单 ≠ 面板一定会提交它们：C# 侧对这四个键的规则是"空/0 = 不提交"（见
    # ConfigPresenter.SaveConfig 的 submit* 注释），老 AB（预制件没有这四行）则四个都不提交——
    # 所以不会出现"面板一保存就把用户手编的值冲掉"。
    "llm": ("base_url", "api_key", "model", "image", "headers",
            "timeout", "timeout_nonstream", "total_budget", "retries"),
    "network": ("host", "port", "request_timeout"),
    # enabled = 自主交互总开关（面板【主动互动】组第一行）。漏在白名单外会让整份表单被
    # fail-closed 拒收（`白名单外键拒绝: initiative.enabled`）——面板一保存就全页失败。
    "initiative": ("enabled", "min_interval", "daily_chance", "npc_cooldown_days", "npc_cooldown_real_s", "low_intim_threshold", "low_intim_halve"),
    "compaction": ("ctx_window", "retain_ratio", "threshold_ratio", "enabled"),
    "ui": ("show_advanced", "portraits_enabled"),
}

# 数值型键的强制类型（写回前归一，防 UI 传字符串坏掉下游 int()/float()）
_INT_KEYS = {"port", "ctx_window", "daily_chance", "npc_cooldown_days", "npc_cooldown_real_s", "low_intim_threshold",
             "retries"}
_FLOAT_KEYS = {"request_timeout", "min_interval", "retain_ratio", "threshold_ratio",
               "timeout", "timeout_nonstream", "total_budget"}
_BOOL_KEYS = {"low_intim_halve", "show_advanced", "enabled", "portraits_enabled"}

# llm.headers 的量纲上限：不是安全边界，是"别让一次手滑把 config.json 写成一个巨型字典"。
# 20 条×200 字对真实网关（通常 1~3 条）绰绰有余。
_HEADERS_MAX = 20
_HEADERS_FIELD_MAX = 200


def _coerce_headers(value: Any) -> Dict[str, str]:
    """`llm.headers` 归一为 dict[str, str]；`None` / 空 → `{}`（表示"不要附加头"）。

    为什么 fail-closed（非法就抛，而不是"非法当空"）：header 填错的表现是**网关回 400/403**，
    那跟"压根没填"长得一模一样。静默吞掉非法值会让用户以为填了却没生效 —— 无从排查。
    """
    if value is None:
        return {}
    if isinstance(value, str):                     # 容错：整体给了一个 JSON 串
        s = value.strip()
        if not s:
            return {}
        try:
            value = json.loads(s)
        except Exception:
            raise ValueError(f"llm.headers 需要对象（面板里写 k1: v1; k2: v2），收到字符串: {s[:60]!r}")
    if not isinstance(value, dict):
        raise ValueError(f"llm.headers 需要对象（面板里写 k1: v1; k2: v2），收到: {type(value).__name__}")
    if len(value) > _HEADERS_MAX:
        raise ValueError(f"llm.headers 最多 {_HEADERS_MAX} 条，收到 {len(value)} 条")
    out: Dict[str, str] = {}
    for k, v in value.items():
        ks, vs = str(k).strip(), str(v).strip()
        if not ks:
            raise ValueError("llm.headers 里有空的名字")
        if any(c.isspace() or ord(c) < 0x21 or ord(c) == 0x7F for c in ks):
            # 合法 HTTP 头名是 RFC 7230 的 token：无空白、无控制字符。
            # 真机上最常见的错法是 `名字 : 值`（冒号前多一个空格），这里直接拦下来点明。
            raise ValueError(f"llm.headers 的名字含空白或控制字符，不是合法 HTTP 头名: {ks!r}"
                             "（写成 `名字: 值`，冒号前不要空格）")
        if len(ks) > _HEADERS_FIELD_MAX or len(vs) > _HEADERS_FIELD_MAX:
            raise ValueError(f"llm.headers 的名字/值过长（上限 {_HEADERS_FIELD_MAX}）: {ks!r}")
        out[ks] = vs
    return out

# 数值键的合法区间（越界**夹取**并告警，不做 fail-closed 拒收）：
# 面板滑杆/手输都可能越界，拒收会让整份表单一起失败；夹取后写回文件即真理，UI 重拉就看得到实际值。
# 越界必须留痕（WARNING）——静默改写会让玩家以为"我设的 0.9 生效了"。
# 形态：(块, 键) → (下限, 上限)；上限 None = 不封顶。
_RANGE_LIMITS: Dict[tuple, tuple] = {
    ("compaction", "retain_ratio"): (0.05, 0.6),      # 保留尾占比：太小压光近期上下文，太大压不动
    ("compaction", "threshold_ratio"): (0.3, 0.95),   # 阈值比：≥1 会永不触发，过低会反复压缩
    ("compaction", "ctx_window"): (4096, None),       # 窗口：小于 4096 时连保留尾预算都放不下
    ("initiative", "min_interval"): (0.0, None),      # 间隔：负数无意义（ChatHub 侧另有 max(0.0, x) 兜底）
    # ── AI 时间与重试──────────────────────────────────────────────
    # 下限一律 0（**不是** 1）：0 在这些键上有明确语义 =「关掉这个上限」，是写进
    # config.example.json 的合法后门。夹到 1 会把用户的显式选择悄悄变成"1 秒超时"，
    # 比不夹还糟。上界只是防手滑打多一个零（把 45 打成 450），不是安全边界。
    ("llm", "timeout"): (0.0, 600.0),                 # 600 = 旧 SDK 默认；再大就等于没设
    ("llm", "timeout_nonstream"): (0.0, 1800.0),
    ("llm", "total_budget"): (0.0, 3600.0),
    ("llm", "retries"): (0, 10),                      # 面板侧另限 0-5；手编文件放宽到 10
}

# 改动块 → 生效方式。四块都不必重启 Python：
# - llm：经 LlmRouter 热换内芯；
# - ui：只是面板显隐/立绘开关，改完下一次读就生效；
# - initiative：min_interval 由装配层改活（ChatHub 每次现读），其余键由 C# 消费——C# 保存成功后
#   会自己重拉 get_config，同样不需要重启本进程；
# - compaction：整块都是压缩器的构造参数，装配层重建 Compressor 热挂回（见 scripts/server.py）。
# 真正需要重启的只剩 network 的 host/port 等装配期一次性传参项。
HOT_BLOCKS = ("llm", "ui", "initiative", "compaction")

# 逐键热生效：块内个别键不属装配期，装配层能直接改活（见 scripts/server.py
# ConfigService）。典型：network.request_timeout —— WsServer.request 每次现读
# self.request_timeout，改属性即生效。这类键值得免重启（用户在面板里实际会调）。
# 形态：(块, 键)；块整体热生效的走 HOT_BLOCKS。
HOT_KEYS = frozenset({("network", "request_timeout")})


def _is_hot(block: str, key: str) -> bool:
    """该键的改动是否需要重启 Python 才能生效（False = 可热生效）。"""
    return block in HOT_BLOCKS or (block, key) in HOT_KEYS


def _clamp(block: str, key: str, value: Any) -> Any:
    """按 _RANGE_LIMITS 夹取数值键；越界打 WARNING 说明原值与实际写入值。"""
    limits = _RANGE_LIMITS.get((block, key))
    if limits is None:
        return value
    lo, hi = limits
    fixed = value
    if lo is not None and fixed < lo:
        fixed = lo
    if hi is not None and fixed > hi:
        fixed = hi
    if fixed != value:
        log.warning("配置值越界已夹取：%s.%s %r → %r（允许范围 %s~%s）",
                    block, key, value, fixed,
                    lo if lo is not None else "-inf", hi if hi is not None else "+inf")
    return fixed


def _find_config_path(config_path: Optional[str | os.PathLike] = None) -> Path:
    """与 config_loader 同源的文件定位（测试可显式传 config_path）。"""
    if config_path:
        return Path(config_path)
    for p in _CONFIG_CANDIDATES:
        if p.is_file():
            return p
    return _CONFIG_CANDIDATES[0]  # 文件不存在时以首选路径为写回目标


def get_config(config_path: Optional[str | os.PathLike] = None) -> Dict[str, Any]:
    """读当前生效配置（load_config 深合并视图）并裁成白名单形状，供 UI 回填表单。"""
    cfg = load_config(config_path)
    out: Dict[str, Any] = {}
    for block, keys in UI_WHITELIST.items():
        src = cfg.get(block) or {}
        out[block] = {k: src.get(k) for k in keys}
    return out


def _coerce_value(block: str, key: str, value: Any) -> Any:
    """白名单键的轻量归一：数值键强转 + 范围夹取；llm.image 接受 bool 或 {enabled: bool}。"""
    if block == "llm" and key == "image":
        if isinstance(value, bool):
            return {"enabled": value}
        if isinstance(value, dict):
            return {"enabled": bool(value.get("enabled", False))}
        raise ValueError("llm.image 只接受 bool 或 {\"enabled\": bool}")
    if block == "llm" and key == "headers":
        return _coerce_headers(value)
    if key in _INT_KEYS:
        try:
            iv = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{block}.{key} 需要整数，收到: {value!r}")
        iv = _clamp(block, key, iv)
        # initiative 范围校验（fail-closed：这些键的越界值不是"夹一下就好"，而是配置写错了）
        if block == "initiative" and key == "daily_chance" and not (0 <= iv <= 100):
            raise ValueError(f"{block}.{key} 需要 0-100，收到: {value!r}")
        if block == "initiative" and key in ("npc_cooldown_days", "npc_cooldown_real_s", "low_intim_threshold") and iv < 0:
            raise ValueError(f"{block}.{key} 需要 >=0，收到: {value!r}")
        return iv
    if key in _FLOAT_KEYS:
        try:
            fv = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{block}.{key} 需要数字，收到: {value!r}")
        return _clamp(block, key, fv)
    if key in _BOOL_KEYS:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes", "on"): return True
            if low in ("false", "0", "no", "off"): return False
        raise ValueError(f"{block}.{key} 需要布尔，收到: {value!r}")
    return value


def _sanitize_partial(partial: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """校验 UI 传入的改动：只认白名单块/键，非法即拒（fail-closed），并做类型归一。"""
    if not isinstance(partial, dict) or not partial:
        raise ValueError("set_config 需要非空 dict（形如 {\"llm\": {...}, ...}）")
    clean: Dict[str, Dict[str, Any]] = {}
    for block, kv in partial.items():
        if block not in UI_WHITELIST:
            raise ValueError(f"未知配置块: {block}（白名单: {', '.join(UI_WHITELIST)}）")
        if not isinstance(kv, dict) or not kv:
            raise ValueError(f"配置块 {block} 需要非空 dict")
        for key, value in kv.items():
            if key not in UI_WHITELIST[block]:
                raise ValueError(f"白名单外键拒绝: {block}.{key}")
            clean.setdefault(block, {})[key] = _coerce_value(block, key, value)
    return clean


def set_config(partial: Dict[str, Any], config_path: Optional[str | os.PathLike] = None) -> Dict[str, Any]:
    """写回白名单键到 config.json，返回 {"updated", "effective", "path", "hot_keys"}。

    - 只更新白名单键，用户文件里其余键原样保留（读原文 → 差分改 → 整体写回）
    - 与现值相同的键不计入改动（UI 全量提交时 effective 依然准确）
    - effective: "none"（无实际改动）/"hot"（改动全部可热生效）/"restart"（含装配期参数）/
      "mixed"（两者都有）；由装配层据此决定是否热换 + 是否请 C# 重启本进程
    - hot_keys: 本次可热生效的键（"块.键"），供装配层逐键落地（llm 换芯 / compaction 重建压缩器 /
      initiative.min_interval 改 ChatHub 属性 / request_timeout 改 WsServer 属性）
    - 文件不存在 → 以白名单改动新建；原 JSON 非法 → 拒绝写入（防覆盖丢键）
    """
    clean = _sanitize_partial(partial)
    path = _find_config_path(config_path)

    raw: Dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(_textio.read_text(path))   # 容 BOM/GBK，见 textio
        except Exception as e:
            raise ValueError(f"config.json 不是合法 JSON，拒绝写入以免丢键（请手工修复）: {e}")
        if isinstance(loaded, dict):
            raw = loaded

    updated: list = []
    for block, kv in clean.items():
        existed = isinstance(raw.get(block), dict)
        target = raw.get(block) if existed else {}
        block_changed = False
        for key, value in kv.items():
            # llm.headers 空 dict = 「不要附加头」→ **删键**，别在用户 config.json 里
            # 留一个 "headers": {}（那会让人以为还配着什么）。删掉也算真改动，照计 updated
            # → llm 在 HOT_BLOCKS 里，前端仍拿到 hot 并触发换芯。
            if block == "llm" and key == "headers" and not value:
                if existed and key in target:
                    target.pop(key, None)
                    updated.append(f"{block}.{key}")
                    block_changed = True
                continue
            if existed and target.get(key) == value:
                continue  # 与现值相同：不计入改动（UI 全量提交也能给出准确 effective）
            target[key] = value
            updated.append(f"{block}.{key}")
            block_changed = True
        if block_changed:
            raw[block] = target

    if updated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 生效判定按「键」而非「块」：块内可有可热生效的键（如 network.request_timeout），
    # 故先逐键分类，再汇总成 hot / restart / mixed。
    hot_keys = [u for u in updated if _is_hot(*u.split(".", 1))]
    cold_keys = [u for u in updated if u not in hot_keys]
    effective = "none" if not updated else (
        "hot" if hot_keys and not cold_keys else ("restart" if cold_keys and not hot_keys else "mixed")
    )
    return {"updated": updated, "effective": effective, "path": str(path), "hot_keys": hot_keys}
