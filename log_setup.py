"""log_setup — 全局日志装配（各司其职：只管「日志往哪写、长什么样、什么级别」）

分层分权：
- 本模块 = 唯一「日志装配」者：建 handler / formatter / 滚动 / 上下文 / 进程异常钩子 /
  阶段耗时打点原语。不解释任何业务事实，不 import 任何业务模块（只依赖标准库）。
- 装配层（scripts/server.py）启动时调用一次 `setup_logging(cfg)`；配置变更后 `reconfigure(cfg)`
  热重配。**只有装配层能配置全局日志状态。**
- 各业务模块只调 `get_logger(__name__)` 取 logger、用 `turn_scope()` / `stage()` 补上下文，
  **绝不自己 `addHandler` / `basicConfig` / `setLevel`**——否则 handler 重复、级别被踩。
- 边界：本模块不记录业务事件（"谁送了什么灵石"归各模块自己写），只提供管道与格式。

为什么需要它（2026-09-11 诊断结论）：
现状全项目仅 `ws_channel.py` 用了 logging，且**从未配置 handler** → INFO 级被 lastResort 丢弃、
WARNING+ 以无时间戳裸格式进 stderr；而 stderr 由 C# `Launcher` 以 `UseShellExecute` + 不重定向
拉起 → 只飘在浮动控制台窗口，关窗即失。结果：慢/卡死时没有一条可回溯的证据。

本模块补齐四件事：
  1. **文件落点**：按日滚动写入 `<包根>/logs/`（相对路径锚包根，不锚 cwd——C# 以 scripts/ 为 cwd）。
  2. **上下文**：npc / turn / step / stage 走 contextvar，日志行自动携带，单个 NPC 的时间线可直接 grep。
  3. **阶段打点 + 有界慢告警**：`stage()` 正常记 DEBUG 耗时；超阈值则按 1x/2x/4x 最多告警 N 次
     （**事件驱动、次数封顶，不做常驻巡检**）——"卡住"当场可见，不必等它结束。
  4. **异常兜底**：`sys.excepthook` / `threading.excepthook` / asyncio loop handler / Warnings。

典型用法：
    # 装配层（唯一）
    log_setup.setup_logging(CFG)
    log_setup.install_process_hooks()

    # 业务模块
    log = log_setup.get_logger(__name__)
    log.info("连接已建立 …")
    with log_setup.turn_scope(npc_id="林婉清", turn=1):
        with log_setup.stage("llm"):        # 超时自动告警
            ...
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import logging.handlers
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from . import paths as _paths

# 相对日志路径的锚点 = 「用户数据根」（见 paths.py）。
# 开发机数据根 == 包目录 → 行为与旧版一模一样，仍是 <包根>/logs；
# 发行版数据根 == Mod 根（C# 经 AGENT_LOOP_DATA 下发）→ 日志落在 <Mod根>/logs，用户可见。
# **绝不用 cwd**：C# Launcher 历史上把 cwd 设成 scripts/，用 cwd 会落到 scripts/logs/
# （与 config_loader 踩过的 cwd 坑同源）。
_PKG_ROOT = _paths.data_root()

# 本项目所有 logger 的公共父名：handler 只挂这一个，propage=False 避免与真 root 重复输出
_ROOT_LOGGER_NAME = "agent_loop"

# 缺省值 = 代码内真相；用户 config.json 的 logging 块只覆盖要改的键（config_loader 深合并）
DEFAULT_LOGGING: Dict[str, Any] = {
    "enabled": True,                 # 总开关：false → 不落文件、不挂 handler、包 logger 静默（零开销）
    "level": "INFO",                 # 包 logger 级别（业务模块用，要排障时调 DEBUG）
    "file": "logs/agent_loop.log",   # 相对路径锚包根；null/空串 = 不落文件
    "rotation": "daily",             # daily（按日）| size（按大小）| none
    "backup_days": 7,                # 保留份数（daily=天数；size=文件数）
    "max_bytes": 4 * 1024 * 1024,    # rotation=size 时单文件上限
    "console": True,                 # 是否同时打到 stderr（C# 拉起时有浮动控制台可见）
    "console_level": "WARNING",      # 控制台级别（默认只让 WARNING+ 上屏，避免刷屏）
    "slow_ms": 30000,                # 阶段慢告警阈值（毫秒）；<=0 关闭慢告警
    "slow_escalations": 3,           # 慢告警最多几次（1x/2x/4x…），封顶防长尾开销
    "trace_frames": False,           # 是否逐帧记 DEBUG（C#↔Python 线上帧；排障时开）
    "capture_root": False,           # 是否连第三方库（httpx/openai…）一起收进本文件
    "modules": {},                   # 模块级级别覆盖，如 {"agent_loop.llm": "DEBUG"}
    "quiet_libs": ["httpx", "httpcore", "openai", "websockets", "urllib3", "asyncio"],
}

# 「静默」级：高于 CRITICAL。关闭日志时把包 logger 定在此级——
# 子 logger 的有效级别取自最近的祖先（= 包 logger），故 isEnabledFor 恒 False，
# 所有 log.xxx() 立即返回（参数是惰性 %s，不会被格式化）→ 关闭即近似零开销。
_SILENT_LEVEL = logging.CRITICAL + 10

# 开关简写（parse_switch 用）：一个词决定「开/关/开到什么级别」
_SWITCH_ON = {"on", "true", "1", "yes", "y", "enable", "enabled", "开"}
_SWITCH_OFF = {"off", "false", "0", "no", "n", "none", "disable", "disabled", "silent", "关"}

# setup_logging(switch=…) 的「未传」哨兵：与显式传 None 区分——
# 未传 = 沿用上次的开关（配置写回触发的热重配不该丢掉启动参数）。
_UNSET: Any = object()


# 日志上下文（npc/turn/step/stage）。asyncio.create_task 会复制当前 context，
# 因此不同 NPC 的并发回合互不串台——这正是我们要的语义。
_ctx: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("agent_loop_log_ctx", default={})

_CTX_KEYS = ("npc", "turn", "step", "stage")

_lock = threading.RLock()
_state: Dict[str, Any] = {"configured": False, "handlers": [], "targets": [],
                         "path": None, "level": None, "block": None, "switch": None}


# ---------------------------------------------------------------- 上下文

def _render_ctx(record: logging.LogRecord) -> str:
    """日志行的上下文栏：显式 extra 优先，缺项回落 contextvar，再缺省 '-'。"""
    source = dict(_ctx.get() or {})
    for k in _CTX_KEYS:
        v = getattr(record, k, None)
        if v is not None:
            source[k] = v
    npc = source.get("npc") or "-"
    parts = [str(npc)]
    for k, p in (("turn", "t"), ("step", "s")):
        v = source.get(k)
        if v is not None:
            parts.append(f"{p}{v}")
    if source.get("stage"):
        parts.append(str(source["stage"]))
    return " ".join(parts)


class _CtxFormatter(logging.Formatter):
    """把上下文栏塞进格式；同时把异常多行堆栈缩进，便于 grep 时成块阅读。"""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "ctx"):
            try:
                record.ctx = _render_ctx(record)
            except Exception:
                record.ctx = "-"
        try:
            return super().format(record)
        except Exception:
            # 格式串/extra 出问题也绝不能让日志本身炸掉业务流程
            return f"{record.levelname} {record.name}: {record.getMessage()}"


@contextmanager
def turn_scope(npc: Optional[str] = None, turn: Optional[Any] = None,
               step: Optional[Any] = None) -> Iterator[None]:
    """为一段代码块注入日志上下文（单 NPC / 单回合 / 单步）。

    只传要设的项，其余沿用外层；退出即恢复（contextvar token 复位）。
    """
    new = dict(_ctx.get() or {})
    if npc is not None:
        new["npc"] = npc
    if turn is not None:
        new["turn"] = turn
    if step is not None:
        new["step"] = step
    token = _ctx.set(new)
    try:
        yield
    finally:
        _ctx.reset(token)


def current_context() -> Dict[str, Any]:
    """当前日志上下文快照（只读副本；诊断/测试用）。"""
    return dict(_ctx.get() or {})


# ---------------------------------------------------------------- 阶段打点

def _schedule(delay_s: float, fn) -> Any:
    """延时回调：优先 asyncio（call_later 会复制 context → 上下文栏照样有值）；
    无运行 loop（同步栈）退回守护线程 Timer。两者都支持 .cancel()。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        return loop.call_later(delay_s, fn)
    t = threading.Timer(delay_s, fn)
    t.daemon = True
    t.start()
    return t


class _SlowWatch:
    """阶段慢告警：进入阶段后按 1x/2x/4x… 阈值检查（**次数封顶**），退出即取消。

    设计取舍：不做常驻巡检、不预支轮询开销——只在阶段存续期间挂有界定时器；
    阶段结束立刻取消，最长开销 = slow_escalations 次告警。用于定位"卡住"：
    正常完成不会有告警，卡住则当场打出"已运行 Xs 仍未结束"。
    """

    def __init__(self, logger: logging.Logger, stage: str, base_ms: float,
                 escalations: int, ctx: Dict[str, Any]) -> None:
        self.logger = logger
        self.stage = stage
        self.base_ms = base_ms
        self.escalations = max(1, int(escalations))
        self.ctx = ctx
        self._i = 0
        self._handle = None
        self._t0 = time.monotonic()

    def start(self) -> None:
        self._schedule_next()

    def _schedule_next(self) -> None:
        if self._i >= self.escalations:
            return
        self._i += 1
        delay = (self.base_ms * (2 ** (self._i - 1))) / 1000.0
        try:
            self._handle = _schedule(delay, self._fire)
        except Exception:
            self._handle = None

    def _fire(self) -> None:
        elapsed = time.monotonic() - self._t0
        try:
            self.logger.warning(
                "阶段 %s 已运行 %.1fs 仍未结束（慢告警 %d/%d）——大概率卡在等待（网络/桥 RPC/并发闸）",
                self.stage, elapsed, self._i, self.escalations,
                extra=dict(self.ctx),
            )
        except Exception:
            pass
        self._schedule_next()

    def cancel(self) -> None:
        h = self._handle
        self._handle = None
        if h is not None:
            try:
                h.cancel()
            except Exception:
                pass


@contextmanager
def stage(name: str, *, slow_ms: Optional[float] = None, **fields: Any) -> Iterator[None]:
    """阶段耗时打点：正常结束记 DEBUG；超过阈值记 WARNING（并已提前慢告警）。

    `fields` 可临时附加/覆盖上下文项（如 npc="林婉清"、stage="llm#2"）。
    本原语**永不抛错**——日志坏了也不能拖垮业务流程。
    """
    log = get_logger(f"stage.{name}")
    block = _state.get("block") or DEFAULT_LOGGING
    threshold = block.get("slow_ms", DEFAULT_LOGGING["slow_ms"]) if slow_ms is None else slow_ms
    escalations = int(block.get("slow_escalations", DEFAULT_LOGGING["slow_escalations"]) or 0)

    ctx = dict(_ctx.get() or {})
    ctx.update({k: v for k, v in fields.items() if v is not None})
    ctx.setdefault("stage", name)

    watch: Optional[_SlowWatch] = None
    # 关闭日志时连定时器都不挂（真正零开销）；未开 WARNING 级的场合也没必要挂。
    if threshold and threshold > 0 and escalations > 0 and log.isEnabledFor(logging.WARNING):
        watch = _SlowWatch(log, name, float(threshold), escalations, ctx)
        try:
            watch.start()
        except Exception:
            watch = None

    t0 = time.monotonic()
    try:
        yield
    finally:
        if watch is not None:
            watch.cancel()
        ms = (time.monotonic() - t0) * 1000.0
        try:
            if threshold and threshold > 0 and ms >= float(threshold):
                log.warning("阶段 %s 完成 %.0fms（超阈值 %.0fms）", name, ms, float(threshold),
                            extra=dict(ctx))
            else:
                log.debug("阶段 %s 完成 %.0fms", name, ms, extra=dict(ctx))
        except Exception:
            pass


# ---------------------------------------------------------------- 装配

def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            merged.update(v)
            out[k] = merged
        else:
            out[k] = v
    return out


def _resolve_block(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """接受三类入参：None / 全量配置（含 logging 块）/ logging 块本身。"""
    if config is None:
        return dict(DEFAULT_LOGGING)
    if isinstance(config, dict) and isinstance(config.get("logging"), dict):
        return _merge(DEFAULT_LOGGING, config["logging"])
    if isinstance(config, dict):
        return _merge(DEFAULT_LOGGING, config)
    return dict(DEFAULT_LOGGING)


def _resolve_path(raw: Any) -> Optional[Path]:
    """相对路径锚包根（不锚 cwd）；空值 = 不落文件。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    p = Path(os.path.expanduser(s))
    if not p.is_absolute():
        p = _PKG_ROOT / p
    return p


def _build_file_handler(path: Path, block: Dict[str, Any]) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    rotation = str(block.get("rotation") or "daily").lower()
    backups = int(block.get("backup_days", DEFAULT_LOGGING["backup_days"]) or 0)
    if rotation == "daily":
        h: logging.Handler = logging.handlers.TimedRotatingFileHandler(
            str(path), when="midnight", backupCount=backups, encoding="utf-8")
    elif rotation == "size":
        h = logging.handlers.RotatingFileHandler(
            str(path), maxBytes=int(block.get("max_bytes", DEFAULT_LOGGING["max_bytes"]) or 0),
            backupCount=backups, encoding="utf-8")
    else:
        h = logging.FileHandler(str(path), encoding="utf-8")
    h.setLevel(logging.DEBUG)   # 级别由 logger/console 把关，文件尽量全量
    return h


_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s [%(ctx)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _make_formatter() -> logging.Formatter:
    return _CtxFormatter(_FORMAT, datefmt=_DATEFMT)


def _close_handlers() -> None:
    """拆掉上一次装配的 handler（热重配/重复调用时防重复行 + 防 fd 泄漏）。"""
    targets = list(_state.get("targets") or [logging.getLogger(_ROOT_LOGGER_NAME)])
    for h in list(_state.get("handlers") or []):
        for lg in targets:
            try:
                lg.removeHandler(h)
            except Exception:
                pass
        try:
            h.close()
        except Exception:
            pass
    _state["handlers"] = []
    _state["targets"] = []


def parse_switch(value: Any) -> Dict[str, Any]:
    """把「一个词」的开关解析成 logging 块覆盖项（纯函数，入口层负责从哪取值）。

    识别的写法（大小写/首尾空格不敏感）：
      off / false / 0 / no / none / silent / 关  → ``{"enabled": False}``（完全关闭）
      on  / true  / 1 / yes / 开                → ``{"enabled": True}``（沿用配置里的级别）
      debug / info / warning / error / critical → ``{"enabled": True, "level": "<大写>"}``
      None / ""（未设置）                       → ``{}``（不改动，沿用配置）
      其它无法识别的词                          → ``{}``（不改动，交由调用方决定是否提示）
    """
    if value is None:
        return {}
    if isinstance(value, bool):
        return {"enabled": value}
    s = str(value).strip().lower()
    if not s:
        return {}
    if s in _SWITCH_OFF:
        return {"enabled": False}
    if s in _SWITCH_ON:
        return {"enabled": True}
    lv = logging.getLevelName(s.upper())
    if isinstance(lv, int) and lv > 0:      # 只认标准级别名（NOTSET=0 不算）
        return {"enabled": True, "level": s.upper()}
    return {}


def describe_switch(value: Any) -> str:
    """开关的可读描述（启动日志用，避免"到底开没开"靠猜）。"""
    ov = parse_switch(value)
    if not ov:
        return "未指定（沿用 config.json 的 logging 块）"
    if not ov.get("enabled", True):
        return f"关闭（来源参数：{value!r}）"
    if ov.get("level"):
        return f"打开，级别 {ov['level']}（来源参数：{value!r}）"
    return f"打开（来源参数：{value!r}）"


def _level_of(raw: Any, default: int = logging.INFO) -> int:
    """把配置里的级别（"INFO"/"debug"/20）归一成 int；无法识别回落 default。"""
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip().upper()
        if not s:
            return default
        if s.isdigit():
            return int(s)
        lv = logging.getLevelName(s)
        if isinstance(lv, int):
            return lv
    return default


def setup_logging(config: Optional[Dict[str, Any]] = None, *, force: bool = False,
                  switch: Any = _UNSET) -> Dict[str, Any]:
    """装配全局日志（幂等；force=True 或 `reconfigure()` 时重建 handler）。

    `switch` = 一个词决定开关（`"off"` / `"debug"` / `"info"` / `"on"` …，见 `parse_switch`），
    优先级高于 config 里的 logging 块——这是给"测试时要日志、部署时关日志"的便捷总闸。
    不传（`_UNSET`）则沿用上一次的开关：配置写回触发的热重配不该丢掉启动参数。

    返回回执 dict（path/level/enabled/handlers/switch），供装配层打印与测试断言。
    **永不抛错**：日志装不起来也必须让服务照常起跑。
    """
    sw = _state.get("switch") if switch is _UNSET else switch
    block = _merge(_resolve_block(config), parse_switch(sw))
    enabled = bool(block.get("enabled", True))
    with _lock:
        if _state["configured"] and not force and _state.get("block") == block:
            return {"path": _state.get("path"), "level": _state.get("level"),
                    "enabled": enabled, "handlers": len(_state["handlers"]),
                    "switch": sw, "unchanged": True}
        try:
            _close_handlers()
        except Exception:
            pass

        pkg_logger = logging.getLogger(_ROOT_LOGGER_NAME)
        handlers: list = []
        path: Optional[Path] = None

        if not enabled:
            # 关闭：不挂任何 handler、包 logger 定在「静默」级。
            # 因为子 logger 的有效级别继承自包 logger，log.xxx() 会立即返回（参数惰性求值），
            # 所以关闭状态近似零开销——可以放心留在正式部署里。
            level = _SILENT_LEVEL
            targets: list = [pkg_logger]
        else:
            level = _level_of(block.get("level"), logging.INFO)
            if block.get("enabled", True):
                path = _resolve_path(block.get("file"))
                if path is not None:
                    try:
                        fh = _build_file_handler(path, block)
                        fh.setFormatter(_make_formatter())
                        handlers.append(fh)
                    except Exception as e:   # 目录不可写/磁盘满 → 不阻断启动
                        path = None
                        try:
                            print(f"[log_setup] 文件日志不可用（{e}），降级为仅控制台", file=sys.stderr)
                        except Exception:
                            pass

            if block.get("console", True):
                ch = logging.StreamHandler(sys.stderr)
                ch.setLevel(_level_of(block.get("console_level"), logging.WARNING))
                ch.setFormatter(_make_formatter())
                handlers.append(ch)

            # 挂载点：默认只挂包 logger（干净可预期）；另把 Python warnings 一并收进文件
            # （用户要的"报错记录"里 warnings 占比不低）；capture_root=true 时连第三方库一起收。
            targets = [pkg_logger]
            if block.get("capture_root", False):
                targets.append(logging.getLogger())
            else:
                targets.append(logging.getLogger("py.warnings"))
        for lg in targets:
            for h in handlers:
                lg.addHandler(h)
            lg.setLevel(level)
            try:
                lg.propagate = False    # 只走本项目 handler，避免与真 root 重复输出
            except Exception:
                pass

        # 模块级级别覆盖 + 第三方库降噪（关闭态无 handler，调级别无副作用，仍保持配置一致）
        for name, lv in (block.get("modules") or {}).items():
            try:
                logging.getLogger(str(name)).setLevel(_level_of(lv, level))
            except Exception:
                pass
        for lib in (block.get("quiet_libs") or []):
            try:
                logging.getLogger(str(lib)).setLevel(logging.WARNING)
            except Exception:
                pass

        _state.update({"configured": True, "handlers": handlers, "targets": targets,
                       "path": str(path) if path else None, "switch": sw,
                       "level": logging.getLevelName(level), "block": block})
        return {"path": _state["path"], "level": _state["level"],
                "enabled": enabled, "handlers": len(handlers), "switch": sw}


def reconfigure(config: Optional[Dict[str, Any]] = None, *, switch: Any = _UNSET) -> Dict[str, Any]:
    """热重配（配置 UI 保存后由装配层调用）：重建 handler，已取出的 logger 对象无需更换。

    不传 switch 时沿用启动时那个开关（热重配不应把 `--log off` 之类的启动参数丢掉）。
    """
    return setup_logging(config, force=True, switch=switch)


def shutdown_logging() -> None:
    """进程退出前 flush + 关闭（server 优雅退出路径调用，防丢尾巴日志）。"""
    with _lock:
        for h in list(_state.get("handlers") or []):
            try:
                h.flush()
            except Exception:
                pass
        _close_handlers()
        _state["configured"] = False


def is_configured() -> bool:
    return bool(_state.get("configured"))


def is_enabled() -> bool:
    """当前是否真的在记日志（关闭态为 False）；供入口层打印"到底开没开"。"""
    block = _state.get("block") or DEFAULT_LOGGING
    return bool(block.get("enabled", True)) and bool(_state.get("handlers"))


def current_log_path() -> Optional[str]:
    return _state.get("path")


def current_block() -> Dict[str, Any]:
    """当前生效的 logging 块快照（只读副本；诊断/回执用）。"""
    return dict(_state.get("block") or {})


def flag(name: str, default: bool = False) -> bool:
    """读一个 logging 开关（零拷贝，逐帧调用也安全）。

    用途：给"高频但默认关"的埋点（如逐帧 trace）一个细粒度闸，
    使 level=DEBUG 时仍可单独关掉噪声，而不必整体降级。
    """
    block = _state.get("block") or DEFAULT_LOGGING
    try:
        return bool(block.get(name, DEFAULT_LOGGING.get(name, default)))
    except Exception:
        return default


def log_dir() -> Path:
    """日志目录（不存在则建）；供诊断/脚本直接查。"""
    path = _resolve_path((_state.get("block") or DEFAULT_LOGGING).get("file"))
    d = path.parent if path is not None else (_PKG_ROOT / "logs")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


# ---------------------------------------------------------------- 取 logger

def get_logger(name: Optional[str] = None) -> logging.Logger:
    """各模块唯一入口。名字一律归入 `agent_loop.*`（传 `__name__` 即天然合规）。"""
    if not name:
        name = _ROOT_LOGGER_NAME
    name = str(name)
    if name != _ROOT_LOGGER_NAME and not name.startswith(_ROOT_LOGGER_NAME + "."):
        name = f"{_ROOT_LOGGER_NAME}.{name}"
    return logging.getLogger(name)


# ---------------------------------------------------------------- 异常兜底

def install_process_hooks() -> None:
    """进程级兜底：主线程/后台线程未捕获异常、asyncio 未处理异常、warnings 全部留痕。

    注意：只装「记录」职责，不做任何恢复/重启动作（那是各业务自愈机制的职责）。
    """
    log = get_logger("agent_loop.unhandled")

    def _excepthook(exc_type, exc, tb):
        try:
            log.critical("主线程未捕获异常（进程将终止）", exc_info=(exc_type, exc, tb))
        finally:
            sys.__excepthook__(exc_type, exc, tb)

    try:
        sys.excepthook = _excepthook
    except Exception:
        pass

    def _threadhook(args):
        try:
            log.critical("后台线程未捕获异常：%s", getattr(getattr(args, "thread", None), "name", "?"),
                         exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        except Exception:
            pass

    try:
        threading.excepthook = _threadhook
    except Exception:
        pass

    try:
        logging.captureWarnings(True)
    except Exception:
        pass


def install_asyncio_hooks(loop: Any = None) -> None:
    """asyncio 未处理异常兜底（create_task 里抛出的异常默认静默消失）。"""
    log = get_logger("agent_loop.unhandled")

    def _handler(lp, context):
        exc = context.get("exception")
        msg = context.get("message") or "asyncio 未处理异常"
        try:
            log.error("asyncio: %s", msg, exc_info=exc if exc is not None else None)
        except Exception:
            pass

    try:
        target = loop if loop is not None else asyncio.get_running_loop()
        target.set_exception_handler(_handler)
    except Exception:
        pass
