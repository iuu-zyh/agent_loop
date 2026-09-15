"""配置热生效 + 新增配置键（09-13）：
① 新键默认值：compaction.enabled / ui.portraits_enabled（含 UI 回填形状）
② 白名单仍 fail-closed：cool_down / compaction_retries / 非白名单块一律拒
③ 数值键类型归一 + 越界夹取（夹取必须留 WARNING，不做静默改写）
④ 热分类：新键与 hot 键 → True，装配期键（network.port / storage_root）→ False；
   并钉住「min_interval 单改 = hot」（曾为 restart）与「装配期键仍 = restart」两条路径
⑤ compaction.enabled=false 只关自动路径：maybe() 返回 None 且不碰 session，compact_now() 照常压
⑥ ConfigService 落地：compaction 整块重建压缩器（参数/冷却表/活动 agent 全同步）+ min_interval 改活

分层分权：本文件只测「配置 → 活体」这条路，C# 侧的立绘/日概率消费不在 Python 测试范围。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_loop import config_loader, config_store
from agent_loop.compaction.compress import COMPACT_PLUGIN, Compressor
from agent_loop.session import Session

_SERVER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "server.py"


def _write_cfg(tmp_dir, data) -> str:
    path = os.path.join(str(tmp_dir), "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _read_raw(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- ① 新键默认值

def test_new_keys_have_defaults(tmp_path):
    """两个新键必须有代码内默认值：false 缺省会让老配置读不到 enabled 而误关自动压缩/立绘。"""
    cfg = config_loader.load_config(config_path="nonexistent_config_xyz.json")
    assert cfg["compaction"]["enabled"] is True
    assert cfg["ui"]["portraits_enabled"] is True
    assert cfg["initiative"]["enabled"] is True
    assert config_loader.DEFAULT_CONFIG["compaction"]["enabled"] is True
    assert config_loader.DEFAULT_CONFIG["ui"]["portraits_enabled"] is True
    assert config_loader.DEFAULT_CONFIG["initiative"]["enabled"] is True

    # UI 回填形状：白名单里的新键要出现在 get_config 结果里（否则面板上没有这一行）
    path = _write_cfg(tmp_path, {})
    view = config_store.get_config(config_path=path)
    assert view["compaction"]["enabled"] is True
    assert view["ui"]["portraits_enabled"] is True
    assert view["initiative"]["enabled"] is True
    print("✓ 新键默认值 true，且进入 UI 回填形状")


# ---------------------------------------------------------------- ② 白名单

def test_whitelist_still_fail_closed(tmp_path):
    """cool_down 明确不开放给 UI；compaction_retries / 非白名单块照旧拒绝。"""
    path = _write_cfg(tmp_path, {})
    for bad in ({"compaction": {"cool_down": 5.0}},
                {"compaction": {"compaction_retries": 9}},
                {"compaction": {"nope": 1}},
                {"storage": {"storage_root": "x"}},
                {"concurrency": {"max_concurrent_turns": 4}}):
        with pytest.raises(ValueError):
            config_store.set_config(bad, config_path=path)
    # 新键本身是白名单内（否则上面被测的"新增配置键"根本写不进去）
    r = config_store.set_config({"compaction": {"enabled": False}, "ui": {"portraits_enabled": False}},
                                config_path=path)
    assert sorted(r["updated"]) == ["compaction.enabled", "ui.portraits_enabled"]
    print("✓ 白名单外键拒绝；两个新键可写")


# 面板「保存」一次提交的全部键 —— 对应新面板 4 组 18 行，逐字对齐
# `csharp/UI/ConfigPresenter.cs` 的 BuildConfigJson（llm 四件 + network 两件 + initiative 七件
# + compaction 四件 + ui 一件）。**改面板行就必须同步这里**：它是"整表单能不能存下去"的护栏。
PANEL_FORM: dict = {
    "llm": {"base_url": "https://api.example.com/v1", "api_key": "sk-test",
            "model": "deepseek-chat", "image": {"enabled": True}},
    "network": {"port": 8766, "request_timeout": 60},
    "initiative": {"enabled": True, "min_interval": 300, "daily_chance": 15,
                   "npc_cooldown_days": 3, "npc_cooldown_real_s": 600,
                   "low_intim_threshold": 60, "low_intim_halve": True},
    "compaction": {"enabled": True, "ctx_window": 32768,
                   "retain_ratio": 0.16, "threshold_ratio": 0.8},
    "ui": {"portraits_enabled": True},
}


def test_panel_full_form_accepted(tmp_path):
    """整表单**必须一次全过**：白名单是 fail-closed 的，任何一个键漏登记都会让整页保存失败。

    09-13 真缺陷（面板实机前抓到）：`initiative.enabled` 只在 C#/预制件里加了，Python 白名单
    漏登记 → 新面板一按「保存」就回 `白名单外键拒绝: initiative.enabled`，**连同一个表单里的
    其它改动一起丢**。所以这里既查"每个键都在白名单"，也查"get_config 能把值回填回来"。

    基线取"配置已存过一次"的真实形态（5 个块都在文件里）——`set_config` 的差分是拿**文件原值**
    比的，块/键不在文件里也会算成改动，那属于首存的一次性行为，不是本测试要钉的东西。
    """
    base = {b: dict(kv) for b, kv in PANEL_FORM.items()}
    base["initiative"]["enabled"] = False          # 唯一与表单不同的现值
    path = _write_cfg(tmp_path, base)

    # ① 漂移护栏：白名单必须认下整表单的每一个键（漏一个 = 整页保存失败）
    for block, kv in PANEL_FORM.items():
        assert block in config_store.UI_WHITELIST, f"配置块 {block} 不在白名单"
        for key in kv:
            assert key in config_store.UI_WHITELIST[block], f"{block}.{key} 不在白名单 → 整表单必被拒"

    # ② 只有 initiative.enabled 变了 → 恰好一个热键，不许牵出 Python 重启
    r = config_store.set_config(PANEL_FORM, config_path=path)
    assert r["updated"] == ["initiative.enabled"], r
    assert r["effective"] == "hot" and r["hot_keys"] == ["initiative.enabled"], r

    # ③ 原样再存一次 = 无改动（否则玩家每点一次保存都误判"需重启"）
    r2 = config_store.set_config(PANEL_FORM, config_path=path)
    assert r2["updated"] == [] and r2["effective"] == "none", r2

    # ④ 值真的落盘，且 get_config 能把每个键回填给面板
    assert _read_raw(path)["initiative"]["enabled"] is True
    view = config_store.get_config(config_path=path)
    for block, kv in PANEL_FORM.items():
        for key in kv:
            assert key in view[block], f"{block}.{key} 没进回填视图（面板该行会读不到值）"
    assert view["initiative"]["enabled"] is True
    print("✓ 面板整表单（5 块 18 键）一次通过；单键改动=hot；重存无改动")


# ---------------------------------------------------------------- ③ 归一 + 夹取

def test_type_normalization_new_keys(tmp_path):
    """UI 传字符串是常态：数值键强转 int/float，布尔键认 true/false 字样。"""
    path = _write_cfg(tmp_path, {})
    config_store.set_config(
        {"compaction": {"ctx_window": "32768", "retain_ratio": "0.2", "threshold_ratio": 0.75,
                        "enabled": "false"},
         "ui": {"portraits_enabled": "true"},
         "initiative": {"min_interval": "120"}},
        config_path=path,
    )
    raw = _read_raw(path)
    assert raw["compaction"]["ctx_window"] == 32768 and isinstance(raw["compaction"]["ctx_window"], int)
    assert raw["compaction"]["retain_ratio"] == 0.2 and isinstance(raw["compaction"]["retain_ratio"], float)
    assert raw["compaction"]["threshold_ratio"] == 0.75
    assert raw["compaction"]["enabled"] is False
    assert raw["ui"]["portraits_enabled"] is True
    assert raw["initiative"]["min_interval"] == 120.0
    print("✓ 类型归一：str → int/float/bool")


def test_out_of_range_clamped_and_logged(tmp_path, monkeypatch):
    """越界夹取而不是拒收，但必须留 WARNING——静默改写会让玩家以为"设的 0.9 生效了"。

    日志桩直接替换模块 logger：agent_loop 根 logger 一旦被 log_setup 配过就 propagate=False，
    走 caplog 会随测试顺序时灵时不灵。
    """
    path = _write_cfg(tmp_path, {})
    warnings: list = []
    monkeypatch.setattr(config_store, "log",
                        SimpleNamespace(warning=lambda msg, *a: warnings.append(msg % a if a else msg)))

    config_store.set_config(
        {"compaction": {"retain_ratio": 0.9, "threshold_ratio": 0.1, "ctx_window": 1024},
         "initiative": {"min_interval": -5}},
        config_path=path,
    )
    raw = _read_raw(path)
    assert raw["compaction"]["retain_ratio"] == 0.6, "上限 0.6"
    assert raw["compaction"]["threshold_ratio"] == 0.3, "下限 0.3"
    assert raw["compaction"]["ctx_window"] == 4096, "下限 4096"
    assert raw["initiative"]["min_interval"] == 0.0, "下限 0"
    for key in ("compaction.retain_ratio", "compaction.threshold_ratio",
                "compaction.ctx_window", "initiative.min_interval"):
        assert any(key in w for w in warnings), f"{key} 越界必须留痕，实际警告：{warnings}"
    assert len(warnings) == 4

    # 区间内不动刀：合法值原样写回，不产生多余告警
    warnings.clear()
    config_store.set_config({"compaction": {"retain_ratio": 0.05, "threshold_ratio": 0.95, "ctx_window": 4096}},
                            config_path=path)
    assert warnings == [], f"区间内不应告警：{warnings}"
    print("✓ 越界夹取 + WARNING 留痕；区间内不改写")


# ---------------------------------------------------------------- ④ 热分类

def test_is_hot_matrix():
    """热键集合：新增/纳入热生效的键为 True；装配期一次性传参的键必须仍为 False。"""
    for block, key in (("compaction", "ctx_window"), ("compaction", "retain_ratio"),
                       ("compaction", "threshold_ratio"), ("compaction", "enabled"),
                       ("initiative", "min_interval"), ("initiative", "daily_chance"),
                       ("initiative", "npc_cooldown_days"), ("initiative", "low_intim_halve"),
                       ("ui", "portraits_enabled"), ("ui", "show_advanced"),
                       ("llm", "model"), ("network", "request_timeout")):
        assert config_store._is_hot(block, key) is True, f"{block}.{key} 应可热生效"
    for block, key in (("network", "port"), ("network", "host"),
                       ("storage", "storage_root"), ("concurrency", "max_concurrent_turns")):
        assert config_store._is_hot(block, key) is False, f"{block}.{key} 仍需重启"
    print("✓ _is_hot 矩阵：热键 True / 装配期键 False")


def test_effective_hot_for_new_keys_and_restart_for_assembly_keys(tmp_path):
    """effective 判定：新键与 min_interval 单改 → hot；装配期键单改 → restart（别把这条路弄丢）。"""
    path = _write_cfg(tmp_path, {"initiative": {"min_interval": 300.0}, "network": {"port": 8766},
                                 "compaction": {"ctx_window": 32768}})

    r_min = config_store.set_config({"initiative": {"min_interval": 90}}, config_path=path)
    assert r_min["effective"] == "hot" and r_min["hot_keys"] == ["initiative.min_interval"]

    r_comp = config_store.set_config(
        {"compaction": {"retain_ratio": 0.3, "threshold_ratio": 0.7, "enabled": False}}, config_path=path)
    assert r_comp["effective"] == "hot"
    assert sorted(r_comp["hot_keys"]) == ["compaction.enabled", "compaction.retain_ratio",
                                          "compaction.threshold_ratio"]

    r_new = config_store.set_config({"ui": {"portraits_enabled": False}}, config_path=path)
    assert r_new["effective"] == "hot" and r_new["hot_keys"] == ["ui.portraits_enabled"]

    r_port = config_store.set_config({"network": {"port": 8767}}, config_path=path)
    assert r_port["effective"] == "restart" and r_port["hot_keys"] == []
    print("✓ effective：新键/min_interval=hot，network.port 仍=restart")


# ---------------------------------------------------------------- ⑤ enabled 只管自动路径

class _StubSummaryLlm:
    async def generate(self, system, messages, tools, **kw):
        return SimpleNamespace(text="<compacted-summary>\n## 角色\n- 甲 冷静\n</compacted-summary>",
                               tool_calls=[])


class _UntouchableSession:
    """enabled=false 时 maybe() 应当连 session 都不读（一读就炸，比"没写盘"更严格）。"""

    id = "不许碰"

    def derive_messages(self):
        raise AssertionError("compaction.enabled=false 时 maybe() 不得触碰 session")


def _seeded_session(npc: str = "压缩者", pairs: int = 40) -> Session:
    sess = Session(id=npc, header={"id": npc, "cwd": "/tmp"})
    for i in range(pairs):
        sess.append("user/message", {"role": "user", "content": [
            {"type": "text", "text": f"玩家的话{i}喦佬问你最近有没有去过白帝城镇守边关"}], "id": f"u{i}"},
            {"surfaceOp": "append"})
        sess.append("assistant/message", {"message": {"role": "assistant", "content": [
            {"type": "text", "text": f"甲回{i}炼气期吐纳闭关一口真气上九霄"}], "id": f"a{i}"}},
            {"surfaceOp": "append"})
    return sess


def test_enabled_false_only_gates_auto_path():
    """关自动压缩：maybe() 直接 None 且 session 原封不动；同一份历史开关打开时确实会压。"""
    off = Compressor(llm=_StubSummaryLlm(), ctx_window=1200, retain_ratio=0.16, cool_down=0.0, enabled=False)
    assert asyncio.run(off.maybe(_UntouchableSession(), None)) is None, "不得读 session 就该返回 None"

    sess = _seeded_session()
    before = json.dumps(sess.log, ensure_ascii=False, sort_keys=True)
    assert asyncio.run(off.maybe(sess, sess.request_header())) is None
    assert json.dumps(sess.log, ensure_ascii=False, sort_keys=True) == before, "session 不得有任何改变"

    # 对照组：同参数只把开关打开（这份历史压力远超 1200×0.8）→ 自动路径真的会压
    on = Compressor(llm=_StubSummaryLlm(), ctx_window=1200, retain_ratio=0.16, cool_down=0.0, enabled=True)
    sess2 = _seeded_session()
    assert asyncio.run(on.maybe(sess2, sess2.request_header())) is not None, "开关打开时应触发压缩"
    print("✓ enabled=false：maybe() 不碰 session；enabled=true 对照组照压")


def test_enabled_false_manual_compact_still_works():
    """手动压缩是玩家的显式意图，不受 enabled 管：compact_now() 必须照常压缩成功。"""
    comp = Compressor(llm=_StubSummaryLlm(), ctx_window=1200, retain_ratio=0.16, cool_down=0.0, enabled=False)
    sess = _seeded_session()
    n_before = len(sess.log)

    result = asyncio.run(comp.compact_now(sess, sess.id))
    assert result is not None and "range" in result, "enabled=false 不该拦住手动压缩"
    assert len(sess.log) > n_before
    assert any(ev["type"] == "compaction/summary" for ev in sess.log)
    assert any(ev["type"] == "user/message"
               and (ev.get("data", {}).get("source", {}) or {}).get("plugin") == COMPACT_PLUGIN
               for ev in sess.log)
    assert any("甲 冷静" in json.dumps(m, ensure_ascii=False) for m in sess.derive_messages())
    print("✓ enabled=false：compact_now() 仍压缩成功（纪要已落面）")


# ---------------------------------------------------------------- ⑥ ConfigService 落地

@pytest.fixture(scope="module")
def server_mod():
    """按文件路径加载 scripts/server.py（scripts/ 不是包，沿用 test_log_switch 的做法）。"""
    spec = importlib.util.spec_from_file_location("_server_under_test_hot", _SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_configservice_hot_applies_compactor_and_initiative(server_mod, tmp_path, monkeypatch):
    """set_config 后：compaction 任何热键都走「整块重建压缩器」，min_interval 直接改活 ChatHub。

    只把读/写盘重定向到临时 config.json，ConfigService 的落地逻辑走原实现；
    日志重配（logging 块）本次不测，否则真跑会把全局日志管道切到仓库 logs/ 落盘。
    """
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"compaction": {"ctx_window": 32768}, "initiative": {"min_interval": 300.0}},
                               ensure_ascii=False), encoding="utf-8")
    real_set_config = config_store.set_config
    monkeypatch.setattr(server_mod, "load_config", lambda *a, **k: config_loader.load_config(config_path=path))
    monkeypatch.setattr(config_store, "set_config",
                        lambda partial, *a, **k: real_set_config(partial, config_path=path))
    monkeypatch.setattr(server_mod.log_setup, "reconfigure", lambda *a, **k: {})

    from agent_loop.agent_loop import AgentLoop
    from agent_loop.llm.echo_client import EchoLlmClient
    from agent_loop.ws_channel import ChatHub

    old = Compressor(llm=EchoLlmClient(), ctx_window=32768, retain_ratio=0.16,
                     threshold_ratio=0.8, cool_down=20000.0, enabled=True)
    old._cool_until = {"热更甲": 12345.0}   # 热换要继承冷却表：别让"刚压过"的会话立刻再压
    loop = AgentLoop.reset_for_test(storage_root=str(tmp_path), llm=EchoLlmClient(),
                                   compactor=old, context_window=32768)
    agent = loop.create("热更甲")
    hub = ChatHub(loop, None, min_initiative_interval=300.0)
    monkeypatch.setattr(server_mod, "_loop", loop)
    monkeypatch.setattr(server_mod, "_hub", hub)
    monkeypatch.setattr(server_mod, "_ws", None)
    monkeypatch.setattr(server_mod, "_router_ref", {"router": EchoLlmClient()})
    try:
        result = server_mod.ConfigService().set_config({
            "compaction": {"ctx_window": 16384, "retain_ratio": 0.3, "threshold_ratio": 0.5, "enabled": False},
            "initiative": {"min_interval": 60},
        })
        new = loop.compactor
        assert result["effective"] == "hot", result
        assert sorted(result["hot_keys"]) == ["compaction.ctx_window", "compaction.enabled",
                                             "compaction.retain_ratio", "compaction.threshold_ratio",
                                             "initiative.min_interval"]
        assert new is not old, "整块重建：必须换成新压缩器（否则改参数不生效）"
        assert (new.ctx_window, new.retain_ratio, new.threshold_ratio, new.enabled) == (16384, 0.3, 0.5, False)
        assert new.cool_down == 20000.0, "没被改的既有参数（cool_down）不能在重建时丢失"
        assert new._cool_until == {"热更甲": 12345.0}, "冷却表必须继承"
        assert loop.get("热更甲") is agent and agent.compactor is new, "活动 agent 也要拿到新压缩器"
        assert agent.stats.context_window == 16384, "token 预算同步"
        assert hub.min_initiative_interval == 60.0, "min_interval 直接改活（每次开口现读的属性）"

        # 新压缩器的 enabled=false 是真关：同一份历史 maybe() 什么都不做
        assert asyncio.run(new.maybe(_UntouchableSession(), None)) is None
    finally:
        AgentLoop.reset_for_test(storage_root=str(tmp_path), llm=EchoLlmClient())
    print("✓ ConfigService 热落地：压缩器整块重建 + 参数/冷却/预算同步 + min_interval 改活")
