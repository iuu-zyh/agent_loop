"""config_loader — 分块配置加载（唯一碰 config.json 的模块）

各司其职：
- 只有本模块负责「文件 → dict」：探测路径、读 json、与代码内默认值深合并、兼容旧扁平 llm 键。
- 各组件（WsServer/ChatHub/DialogueAgent/Compressor）一律不 import 本模块，
  装配层（server.py / llm.factory）读块后以「构造参数」注入——组件保持可测、可注入、无循环依赖。
- env 覆盖规则不在这里做：loader 只管文件；环境变量优先（如 AGENT_LOOP_WS / OPENAI_*）在使用处处理。

约定：用户 config 里只写想改的字段，缺的用代码内默认值（深合并），不是"写全才能跑"。
"""

from __future__ import annotations

import json as _json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths as _paths
from . import textio as _textio

# 项目根 config.json 探测候选：统一由 paths.config_candidates() 给出（单一事实来源）。
# 权威候选 ① 是「用户数据根」——开发机 = 包目录（源码树根即包自身），发行版 = Mod 根
# （由 C# 经 AGENT_LOOP_DATA 下发，见 csharp/ModPaths.cs）。
# 历史坑（保留作教训）：曾用 parent.parent 跳到源码树的**父目录**（那里没有 config.json）+ 依赖 cwd=包根，
# 而 C# Launcher 以 scripts/ 为工作目录拉起 server → config 读不到 → LLM 内芯恒为 Echo
# （实锤：游戏自启服务全是 echo 回显的根因）。
_CONFIG_CANDIDATES = _paths.config_candidates()

# 旧扁平 llm 键（升级前的 config.json 顶层写法，迁移期兼容）
_LEGACY_LLM_KEYS = ("base_url", "api_key", "model", "image")

# 全量配置默认值：代码内真相，用户 config 只覆盖需要改的键
DEFAULT_CONFIG: Dict[str, Any] = {
    "network": {
        "host": "127.0.0.1",
        "port": 8766,               # 端口另可经环境变量 AGENT_LOOP_WS 覆盖（测试/部署灵活）
        "request_timeout": 120.0,   # 回合内同步 RPC（get_context/call_tool）超时秒；120s 容纳模态确认窗（玩家点选后才回 response）
    },
    "concurrency": {
        "max_concurrent_turns": 2,  # 全局同时运行的回合数上限（防 LLM 并发爆炸）
        "max_parallel_tools": 5,    # 单回合内工具并发上限
    },
    "initiative": {
        # 总开关：false = 关掉【日节拍】自动触发（NPC 不再自己开口），由 C# NpcInitiativeMonitor 消费；
        # 诊断强制路径（_diag_initiative_force.txt）不受它影响——那是排障入口不是玩法。
        "enabled": True,
        "min_interval": 300.0,       # NPC 主动开口全局节流（秒，Python侧熔断；C#日节拍为主）
        "daily_chance": 15,          # 每游戏日触发概率 0-100，0=关闭（对齐神识传音 0-100滑杆，us_strings:2047）
        "npc_cooldown_days": 3,      # 单NPC游戏日冷却
        "npc_cooldown_real_s": 600,  # 单NPC现实秒冷却（防挂机不推日）
        "low_intim_threshold": 60,   # <阈值视为低好感（2星≈60），减半判定
        "low_intim_halve": True,     # 低好感是否减半（us_strings:2047 "<2星概率减半"）
    },
    "compaction": {
        "enabled": True,            # 自动压缩总开关：false = 不自动压（手动 /compact 与面板按钮照常可用）
        "threshold_ratio": 0.8,     # 超阈才自动压缩：pressure > ctx_window * ratio
        "retain_ratio": 0.16,       # 保留尾占比（最新消息不压）
        "ctx_window": 200000,       # LLM 上下文窗口（token；2026-09-14 由 32768 调高）
        "compaction_retries": 1,    # 摘要失败重试次数
        "cool_down": 20000.0,       # 一次压缩后的冷却秒数（防抖）
    },
    "storage": {
        "storage_root": "~/.sessions",  # 会话 jsonl 落盘目录（~ 由 AgentLoop 展开）
    },
    "llm": {
        "base_url": None,
        "api_key": None,
        "model": None,
        "image": {},                # image.enabled 由 factory 读
        "retries": 1,               # 网络/瞬时错误重试次数（总尝试 = retries+1；0 不重试）
        # 单次请求上限（秒）：httpx read timeout，量的是**相邻两个数据块之间**的间隔，
        # 不是整个请求的总时长（每收到一块就重置 → 持续吐字的长回复永不被它砍掉，
        # 只有"卡住不动"才会被抓到）。实测 TTFT 5~22s，45s 留 2 倍余量。
        # null/0 = 不下发，落到 SDK 内建默认 600s —— 那是"挂 6 分钟零报错"的成因，不建议。
        "timeout": 45.0,
        # 非流式单次上限（秒）。**必须与上面分开**：非流式请求在整段生成完之前一个字节都不发，
        # read timeout 量到的是"完整生成时长"（含首 token），量纲和流式的"块间隔"完全不同。
        # 唯一调用方是回合边界的上下文压缩（大 prompt，阈值 = ctx_window×0.8）。
        # 用 45s 去卡它会静默废掉压缩（压缩失败是软失败，只落一条 warning，回合照常继续）。
        "timeout_nonstream": 180.0,
        # 整个回合的挂钟硬顶（秒），含全部重试尝试。防的是"对端定期发 SSE 心跳，
        # 每次都重置 read 计时器 → timeout 永远不触发"这种情形。null/0 = 不设顶。
        # 只作用于**流式（聊天）**路径：它表达的是"玩家在等的这一回合最久等多久"。
        #   上下文压缩不受它约束（否则大摘要永远做不完），由 timeout_nonstream 封顶。
        # 玩家最坏等待 ≈ timeout×(retries+1)+退避，且被本值封顶。
        "total_budget": 100.0,
    },
    # UI 显示开关（不属于任何运行参数，只控制配置面板表单显隐；默认全关）
    "ui": {
        # true = 显示 initiative（自主交互）等高级行，供开发调试；终端用户保持 false/缺省
        "show_advanced": False,
        # 立绘开关：纯 C# 消费（Python 只登记默认值与白名单，不做别的——立绘是游戏内渲染）
        "portraits_enabled": True,
    },
    # 日志装配块（归 log_setup 解释；本块属「专业字段」，不进 UI 白名单，手改文件）
    # 默认值以 log_setup.DEFAULT_LOGGING 为真相源，此处仅给 UI/诊断一个可读镜像；
    # 缺键时 log_setup 会用自己的默认补齐，故无需两处严格同步。
    "logging": {
        "enabled": True,
        "level": "INFO",                 # 排障时调 DEBUG
        "file": "logs/agent_loop.log",   # 相对路径锚包根（非 cwd）；空串 = 不落文件
        "rotation": "daily",             # daily | size | none
        "backup_days": 7,
        "console": True,
        "console_level": "WARNING",
        "slow_ms": 30000,                # 阶段慢告警阈值（ms）；0 = 关闭
        "slow_escalations": 3,           # 慢告警次数封顶（1x/2x/4x…）
        "trace_frames": False,           # 逐帧记 C#↔Python 线上帧（DEBUG 级，排障时开）
        "capture_root": False,           # true = 连第三方库日志一起收进文件
        "modules": {},                   # 模块级覆盖，如 {"agent_loop.llm": "DEBUG"}
        "quiet_libs": ["httpx", "httpcore", "openai", "websockets", "urllib3", "asyncio"],
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并：override 中 dict 键递归合并，其余覆盖；只改出现的键。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalize_legacy_llm(data: Dict[str, Any]) -> Dict[str, Any]:
    """旧扁平 llm 键兼容：顶层出现 base_url/api_key/model/image 且无 llm 块时，归入 llm 块。"""
    d = dict(data)
    if "llm" not in d:
        legacy = {k: d.pop(k) for k in _LEGACY_LLM_KEYS if k in d}
        if legacy:
            d["llm"] = legacy
    return d


def load_config(config_path: Optional[str | os.PathLike] = None) -> Dict[str, Any]:
    """读 config.json → 深合并进 DEFAULT_CONFIG → 返回完整配置 dict。

    无文件 / 非 dict / 坏 json 一律返回默认全量（永不抛错，装配层安全起跑）。
    """
    paths = [Path(config_path)] if config_path else list(_CONFIG_CANDIDATES)
    for p in paths:
        if p.is_file():
            try:
                # 经 textio 读：玩家用记事本存成「UTF-8 带 BOM」是常见事，
                # 直接 read_text("utf-8") 会被 BOM 顶成 "Expecting value: line 1 column 1"
                data = _json.loads(_textio.read_text(p))
                if isinstance(data, dict):
                    data = _normalize_legacy_llm(data)
                    return _deep_merge(deepcopy(DEFAULT_CONFIG), data)
            except Exception:
                continue
    return deepcopy(DEFAULT_CONFIG)


def get_block(cfg: Dict[str, Any], name: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """取一个分块（load_config 已保证块存在；default 为兜底）。"""
    block = cfg.get(name)
    return block if isinstance(block, dict) else (default if default is not None else {})