"""log_setup 测试：装配幂等 / 落盘与上下文 / 级别与热重配 / 相对路径锚包根 /
阶段慢告警（有界封顶）/ 异常钩子 / 命名空间 / 职责哨兵（唯 log_setup 可配置 logging）。

约定：测试一律显式给 logging 块（tmp 路径 + console=False），避免污染项目 logs/。
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import pytest

from agent_loop import log_setup


def _block(tmp_path, **over) -> dict:
    cfg = {
        "enabled": True, "level": "DEBUG", "file": str(tmp_path / "t.log"),
        "console": False, "rotation": "none", "slow_ms": 0, "slow_escalations": 0,
    }
    cfg.update(over)
    return {"logging": cfg}


@pytest.fixture(autouse=True)
def _clean_logging():
    """每个用例后收口，避免全局 handler 泄漏到后续用例。"""
    yield
    log_setup.setup_logging({"logging": {"enabled": False, "console": False, "file": None}}, force=True)
    log_setup.shutdown_logging()


def _read(tmp_path, name: str = "t.log") -> str:
    p = tmp_path / name
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def test_setup_writes_file_with_context(tmp_path):
    """落盘 + 上下文栏：npc/turn 注入后，日志行必须带出来（这是 grep 单 NPC 时间线的基础）。"""
    r = log_setup.setup_logging(_block(tmp_path), force=True)
    assert r["enabled"] is True and os.path.isfile(r["path"])

    log = log_setup.get_logger("agent_loop.demo")
    log.info("无上下文")
    with log_setup.turn_scope(npc="林婉清", turn=3):
        log.info("有上下文")
    text = _read(tmp_path)
    assert "无上下文" in text and "[-] " in text
    assert "[林婉清 t3] " in text
    assert re.search(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} INFO ", text, re.M), "时间戳/级别齐全"
    print("✓ 落盘 + 上下文栏")


def test_turn_scope_restores_and_nests(tmp_path):
    log_setup.setup_logging(_block(tmp_path), force=True)
    with log_setup.turn_scope(npc="A", turn=1):
        assert log_setup.current_context()["npc"] == "A"
        with log_setup.turn_scope(step=2):
            ctx = log_setup.current_context()
            assert ctx["npc"] == "A" and ctx["step"] == 2, "内层继承外层"
        assert "step" not in log_setup.current_context(), "内层退出后恢复"
    assert log_setup.current_context() == {}, "全部退出后清空"
    print("✓ turn_scope 嵌套与恢复")


def test_setup_idempotent_no_duplicate_handlers(tmp_path):
    block = _block(tmp_path)
    log_setup.setup_logging(block, force=True)
    r2 = log_setup.setup_logging(block)          # 同配置重复调用 → 不重建
    assert r2.get("unchanged") is True and r2["handlers"] == 1
    log_setup.get_logger("agent_loop.demo").info("只应出现一次")
    assert _read(tmp_path).count("只应出现一次") == 1, "handler 重复会导致日志翻倍"
    print("✓ 幂等：不重复挂 handler")


def test_reconfigure_switches_level_hot(tmp_path):
    """热重配：改级别后已取出的 logger 对象无需更换（配置 UI 里调级别不必重启）。"""
    log_setup.setup_logging(_block(tmp_path, level="INFO"), force=True)
    log = log_setup.get_logger("agent_loop.demo")
    log.debug("调试行")
    assert "调试行" not in _read(tmp_path)
    log_setup.reconfigure(_block(tmp_path, level="DEBUG"))
    log.debug("调试行")
    assert "调试行" in _read(tmp_path)
    print("✓ 热重配级别生效")


def test_disabled_writes_nothing(tmp_path):
    r = log_setup.setup_logging(_block(tmp_path, enabled=False), force=True)
    assert r["path"] is None
    log_setup.get_logger("agent_loop.demo").critical("不该落盘")
    assert not (tmp_path / "t.log").exists()
    print("✓ enabled=false 不建文件")


def test_relative_path_anchors_package_root(tmp_path):
    """相对路径必须锚包根——C# Launcher 把 cwd 设成 scripts/，锚 cwd 会写错地方。"""
    r = log_setup.setup_logging({"logging": {"level": "INFO", "console": False,
                                             "file": "logs/_pytest_anchor.log"}}, force=True)
    assert r["path"] is not None
    p = Path(r["path"])
    assert p.parent == Path(log_setup.__file__).resolve().parent / "logs", f"锚点不对: {p}"
    log_setup.shutdown_logging()
    try:
        p.unlink()
    except OSError:
        pass
    print("✓ 相对路径锚包根")


def test_stage_slow_warning_is_bounded(tmp_path):
    """慢告警：按 1x/2x 触发且**次数封顶**（不预支长尾开销），结束时补一条超阈值完成行。"""
    log_setup.setup_logging(_block(tmp_path, slow_ms=80, slow_escalations=2), force=True)
    with log_setup.stage("llm", turn=1, step=2):
        time.sleep(0.30)
    text = _read(tmp_path)
    assert text.count("慢告警") == 2, "告警次数应封顶为 slow_escalations"
    assert "阶段 llm 已运行" in text and "卡在等待" in text
    assert re.search(r"阶段 llm 完成 \d+ms（超阈值 80ms）", text), "收口行要带耗时与阈值"
    assert re.search(r"\[- t1 s2 llm\]", text), "阶段打点要带上下文（无 npc 时占位 '-'）"
    print("✓ 阶段慢告警有界 + 收口耗时")


def test_stage_fast_logs_debug_only(tmp_path):
    log_setup.setup_logging(_block(tmp_path, slow_ms=30000), force=True)
    with log_setup.stage("quick"):
        pass
    text = _read(tmp_path)
    assert "阶段 quick 完成" in text and "慢告警" not in text
    print("✓ 未超阈值：只记 DEBUG 耗时")


def test_get_logger_namespacing():
    assert log_setup.get_logger("ws_channel").name == "agent_loop.ws_channel"
    assert log_setup.get_logger("agent_loop.llm.router").name == "agent_loop.llm.router"
    assert log_setup.get_logger().name == "agent_loop"
    print("✓ logger 名字一律归入 agent_loop.*")


def test_flag_reads_logging_switches(tmp_path):
    """细粒度开关：逐帧 trace 等高频埋点靠 flag() 单独控制，不必整体降级。"""
    log_setup.setup_logging(_block(tmp_path, trace_frames=True), force=True)
    assert log_setup.flag("trace_frames") is True
    assert log_setup.flag("capture_root") is False, "缺省取 DEFAULT_LOGGING"
    assert log_setup.flag("no_such_flag", default=True) is True
    print("✓ flag() 读 logging 开关")


def test_process_hooks_record_unhandled(tmp_path, monkeypatch):
    """异常钩子：未捕获异常必须留痕（否则排障时"进程怎么没的"完全无解）。"""
    log_setup.setup_logging(_block(tmp_path), force=True)
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None)   # 静音默认打印
    log_setup.install_process_hooks()
    saved = sys.excepthook
    try:
        try:
            raise ValueError("故意炸一下")
        except ValueError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = saved
    text = _read(tmp_path)
    assert "主线程未捕获异常" in text and "故意炸一下" in text
    print("✓ 未捕获异常留痕")


def test_no_module_configures_logging_itself():
    """职责哨兵：只有 log_setup 能碰全局 logging 配置。

    其它模块若自行 basicConfig / addHandler / getLogger，会与装配层打架
    （重复 handler → 日志翻倍；级别被踩 → 排障时看不到该看的行）。
    """
    pkg_root = Path(log_setup.__file__).resolve().parent
    # 目录整理：verify_csharp/verify_smoke/backup_prefab_*/backup_gamedir_* 已删除，白名单同步精简；
    # 新增 "dev"——scripts/dev/ 是开发期一次性工具（不进运行时），不受"只有 log_setup 装管道"约束。
    skip_dirs = {"backup", "tests", "reference", "ui_preview", "__pycache__",
                 "generated-images", "dev"}
    patterns = {
        "logging.basicConfig": re.compile(r"logging\.basicConfig\s*\("),
        ".addHandler(": re.compile(r"\.addHandler\s*\("),
        "logging.getLogger": re.compile(r"logging\.getLogger\s*\("),
        "logging.FileHandler": re.compile(r"logging\.(File|Stream)Handler\s*\("),
    }
    offenders = []
    for path in list(pkg_root.rglob("*.py")) + list((pkg_root / "scripts").rglob("*.py")):
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.name in ("log_setup.py", "conftest.py"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, rx in patterns.items():
            if rx.search(text):
                offenders.append(f"{path.relative_to(pkg_root)} → {label}")
    assert not offenders, "以下文件自行配置了 logging（应改用 log_setup.get_logger）:\n" + "\n".join(offenders)
    print("✓ 职责哨兵：无模块自行配置 logging")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tp = Path(d)
        test_setup_writes_file_with_context(tp)
        test_setup_idempotent_no_duplicate_handlers(tp)
        test_reconfigure_switches_level_hot(tp)
        test_stage_slow_warning_is_bounded(tp)
        test_stage_fast_logs_debug_only(tp)
    test_turn_scope_restores_and_nests(Path(tempfile.mkdtemp()))
    test_get_logger_namespacing()
    test_no_module_configures_logging_itself()
    print("\nlog_setup 测试全部通过")
