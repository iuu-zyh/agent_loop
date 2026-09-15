"""日志开关测试：一个词决定开/关/级别；CLI > 环境变量 > config；关闭态零开销。

对应需求：「测试时要日志、正式部署关日志」——总闸要足够方便，且关闭必须真的不干活。
"""
from __future__ import annotations

import importlib.util
import logging
import time
from pathlib import Path

import pytest

from agent_loop import log_setup

_SERVER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "server.py"


@pytest.fixture(autouse=True)
def _clean_logging():
    yield
    log_setup.setup_logging({"logging": {"enabled": False, "console": False, "file": None}},
                            force=True, switch=None)
    log_setup.shutdown_logging()


@pytest.fixture(scope="module")
def server_mod():
    """按文件路径加载 scripts/server.py（scripts/ 不是包，不能用 import 语句）。"""
    spec = importlib.util.spec_from_file_location("_server_under_test", _SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cfg(tmp_path, **over):
    c = {"enabled": True, "level": "INFO", "file": str(tmp_path / "t.log"),
         "console": False, "rotation": "none", "slow_ms": 0, "slow_escalations": 0}
    c.update(over)
    return {"logging": c}


def _read(tmp_path) -> str:
    p = tmp_path / "t.log"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


# ---------------------------------------------------------------- parse_switch

def test_parse_switch_variants():
    """一个词的开关：关 / 开 / 开并指定级别；未设置或无法识别 → 不改动。"""
    for off in ("off", "OFF", " off ", "false", "0", "no", "none", "silent", "disable", "关"):
        assert log_setup.parse_switch(off) == {"enabled": False}, off
    for on in ("on", "true", "1", "yes", "enable", "开"):
        assert log_setup.parse_switch(on) == {"enabled": True}, on
    for lv in ("debug", "DEBUG", "Info", "warning", "error", "critical"):
        assert log_setup.parse_switch(lv) == {"enabled": True, "level": lv.upper()}, lv
    assert log_setup.parse_switch(None) == {}
    assert log_setup.parse_switch("") == {}
    assert log_setup.parse_switch("   ") == {}
    assert log_setup.parse_switch("乱写的词") == {}, "无法识别 → 不改动配置"
    assert log_setup.parse_switch(True) == {"enabled": True}
    assert log_setup.parse_switch(False) == {"enabled": False}
    print("✓ parse_switch：off/on/级别/未设置/无法识别")


def test_describe_switch_readable():
    assert "关闭" in log_setup.describe_switch("off")
    assert "DEBUG" in log_setup.describe_switch("debug")
    assert "沿用" in log_setup.describe_switch(None)
    print("✓ describe_switch 可读回执")


# ---------------------------------------------------------------- 开关覆盖 config

def test_switch_off_beats_config_enabled(tmp_path):
    """开关优先级高于 config：config 开着也能被一个词关掉，且不建文件。"""
    r = log_setup.setup_logging(_cfg(tmp_path, enabled=True), force=True, switch="off")
    assert r["enabled"] is False and r["path"] is None and r["handlers"] == 0
    log = log_setup.get_logger("agent_loop.demo")
    log.critical("关闭态不该落盘")
    assert not (tmp_path / "t.log").exists(), "关闭态不得创建日志文件"
    print("✓ 开关 off 覆盖 config enabled=true")


def test_switch_level_beats_config_level(tmp_path):
    log_setup.setup_logging(_cfg(tmp_path, level="WARNING"), force=True, switch="debug")
    log_setup.get_logger("agent_loop.demo").debug("调试行")
    assert "DEBUG" in _read(tmp_path) and "调试行" in _read(tmp_path)
    print("✓ 开关级别覆盖 config 级别")


def test_switch_on_keeps_config_level(tmp_path):
    log_setup.setup_logging(_cfg(tmp_path, level="DEBUG"), force=True, switch="on")
    log_setup.get_logger("agent_loop.demo").debug("调试行")
    assert "调试行" in _read(tmp_path)
    print("✓ 开关 on：沿用 config 里的级别")


def test_reconfigure_preserves_startup_switch(tmp_path):
    """配置写回会触发热重配——不能把启动时的 --log off 丢掉。"""
    log_setup.setup_logging(_cfg(tmp_path, enabled=True), force=True, switch="off")
    log_setup.reconfigure(_cfg(tmp_path, enabled=True))     # 模拟 set_config 后的重配
    assert log_setup.is_enabled() is False, "热重配不应把启动开关恢复成 config 值"
    assert not (tmp_path / "t.log").exists()

    log_setup.reconfigure(_cfg(tmp_path), switch="info")     # 显式换开关可以打开
    assert log_setup.is_enabled() is True
    print("✓ 热重配沿用启动开关；显式换开关可生效")


# ---------------------------------------------------------------- 零开销

def test_disabled_is_silent_and_zero_overhead(tmp_path):
    """关闭态：包 logger 静默（连 CRITICAL 都不输出）、stage 不挂定时器、不建文件。"""
    log_setup.setup_logging(_cfg(tmp_path), force=True, switch="off")
    log = log_setup.get_logger("agent_loop.ws_channel")
    assert log.isEnabledFor(logging.DEBUG) is False
    assert log.isEnabledFor(logging.CRITICAL) is False, "关闭态应高于 CRITICAL"

    # stage 在关闭态不挂慢告警定时器（否则会白跑一个线程/定时器）
    with log_setup.stage("llm", slow_ms=10, slow_escalations=3):
        time.sleep(0.15)
    assert not (tmp_path / "t.log").exists()
    assert log_setup.flag("trace_frames") is False, "关闭态 flag 仍可安全读取"
    print("✓ 关闭态静默 + stage 不挂定时器")


# ---------------------------------------------------------------- 入口优先级

def test_resolve_log_switch_precedence(server_mod, tmp_path):
    """CLI 参数 > 环境变量 AGENT_LOOP_LOG > None（沿用 config）。"""
    R = server_mod.resolve_log_switch
    p = server_mod._arg_parser()

    assert R(p.parse_args(["--no-log"])) == "off"
    assert R(p.parse_args(["--log", "debug"])) == "debug"
    assert R(p.parse_args(["--log-level", "warning"])) == "warning"
    assert R(p.parse_args([])) is None
    assert R(p.parse_args([]), {"AGENT_LOOP_LOG": "off"}) == "off", "无 CLI 时读环境变量"
    assert R(p.parse_args(["--log", "debug"]), {"AGENT_LOOP_LOG": "off"}) == "debug", "CLI 优先于 env"
    assert R(p.parse_args([]), {}) is None
    print("✓ resolve_log_switch：CLI > env > config")


def test_bootstrap_logging_cli_beats_env_and_creates_no_file(tmp_path, monkeypatch, server_mod):
    """端到端（装配步）：--no-log 压过环境变量，且不建文件、给出明确提示。"""
    monkeypatch.setenv("AGENT_LOOP_LOG", "debug")
    monkeypatch.setattr(server_mod, "CFG", _cfg(tmp_path))
    _args, switch, snap = server_mod.bootstrap_logging(["--no-log"])
    assert switch == "off", "CLI --no-log 应压过 env=debug"
    assert snap["enabled"] is False and snap["handlers"] == 0
    assert not (tmp_path / "t.log").exists()

    # 再验证环境变量单独也能开关
    _args, switch2, snap2 = server_mod.bootstrap_logging([])
    assert switch2 == "debug" and snap2["enabled"] is True and snap2["level"] == "DEBUG"
    print("✓ 入口装配：CLI > env；关闭态不建文件")


if __name__ == "__main__":
    import tempfile

    test_parse_switch_variants()
    test_describe_switch_readable()
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d)
        test_switch_off_beats_config_enabled(tp)
        test_switch_level_beats_config_level(tp)
        test_switch_on_keeps_config_level(tp)
        test_reconfigure_preserves_startup_switch(tp)
        test_disabled_is_silent_and_zero_overhead(tp)
    print("\n日志开关测试全部通过")
