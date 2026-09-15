"""`llm.headers` 进面板白名单：读写往返 / 空即删键 / 非法即拒（fail-closed）

为什么这些用例值得存在：
  headers 写错的表现（网关回 400/403）与「压根没填」**长得一模一样**。若归一化静默吞掉非法值，
  用户会以为填了却没生效，且无从排查 —— 所以这里刻意全部走 fail-closed，用例就是钉住这一点。
"""
from __future__ import annotations

import json
import os

import pytest

from agent_loop import config_store


def _write_cfg(tmp_dir, data) -> str:
    path = os.path.join(tmp_dir, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _read_cfg(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_headers_in_whitelist():
    assert "headers" in config_store.UI_WHITELIST["llm"], "headers 必须在白名单里，否则 set_config 直接拒收"
    print("✓ headers 在白名单里")


def test_get_config_exposes_headers(tmp_path):
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k",
                                         "model": "m", "headers": {"x-opencode-session": "s1"}}})
    cfg = config_store.get_config(config_path=path)
    assert cfg["llm"]["headers"] == {"x-opencode-session": "s1"}
    print("✓ get_config 带出 headers")


def test_get_config_headers_absent_is_none(tmp_path):
    """没配过 → None（不是 {}）—— C# 侧要按"空行"回填，用它区分"没配"与"配了个空"。"""
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"}})
    assert config_store.get_config(config_path=path)["llm"]["headers"] is None
    print("✓ 未配置 → None")


def test_set_headers_roundtrip(tmp_path):
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"}})
    r = config_store.set_config(
        {"llm": {"headers": {"x-opencode-session": "agent-loop", "User-Agent": "gl/1.0"}}},
        config_path=path)
    assert "llm.headers" in r["updated"]
    assert r["effective"] == "hot", "llm 块是热生效块，改 headers 不该要求重启"
    assert _read_cfg(path)["llm"]["headers"] == {"x-opencode-session": "agent-loop",
                                                 "User-Agent": "gl/1.0"}
    print("✓ 写入并往返成功，且判定为 hot")


def test_empty_headers_deletes_key(tmp_path):
    """空 = 「不要附加头」。别在用户 config.json 里留一个 "headers": {}（那会让人以为还配着什么）。"""
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m",
                                         "headers": {"x-opencode-session": "old"}}})
    r = config_store.set_config({"llm": {"headers": {}}}, config_path=path)
    assert "llm.headers" in r["updated"], "删键也是真改动，要计入 updated（否则不会换芯）"
    assert "headers" not in _read_cfg(path)["llm"], "空 headers 应把键删掉"
    print("✓ 空 headers → 删键（并计为改动）")


def test_empty_headers_when_absent_is_noop(tmp_path):
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"}})
    r = config_store.set_config({"llm": {"headers": {}}}, config_path=path)
    assert "llm.headers" not in (r["updated"] or []), "本来就没有，不该报改动"
    assert r["effective"] == "none"
    print("✓ 本来就没配 + 提交空 → 无改动")


def test_same_headers_not_counted_as_change(tmp_path):
    h = {"x-opencode-session": "s1"}
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k",
                                         "model": "m", "headers": h}})
    r = config_store.set_config({"llm": {"headers": dict(h)}}, config_path=path)
    assert r["effective"] == "none", "全量提交时内容没变就不该触发换芯/重启"
    print("✓ 内容相同 → 不计改动（面板全量提交不会白重启）")


@pytest.mark.parametrize("bad,why", [
    ({"名字 带空格": "v"}, "头名含空白"),
    ({"key\twith\ttab": "v"}, "头名含制表符"),
    ({"": "v"}, "空头名"),
    (["not", "a", "dict"], "根本不是 dict"),
    (123, "数字"),
])
def test_illegal_headers_rejected(tmp_path, bad, why):
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"}})
    with pytest.raises(ValueError):
        config_store.set_config({"llm": {"headers": bad}}, config_path=path)
    assert "headers" not in _read_cfg(path)["llm"], f"被拒的写入不能落盘（{why}）"
    print(f"✓ 非法 headers 被拒：{why}")


def test_reject_does_not_write_anything(tmp_path):
    """fail-closed 的要点：整份 partial 被拒时，同一次提交里**别的块也不能落盘**。"""
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://old", "api_key": "k", "model": "m"}})
    with pytest.raises(ValueError):
        config_store.set_config({"llm": {"base_url": "http://new", "headers": {"bad name": "v"}}},
                                config_path=path)
    assert _read_cfg(path)["llm"]["base_url"] == "http://old", "被拒时不能写一半"
    print("✓ 整份提交被拒时不落半份（base_url 未动）")


def test_values_coerced_to_str(tmp_path):
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"}})
    config_store.set_config({"llm": {"headers": {"X-Num": 123, "X-Bool": True}}}, config_path=path)
    assert _read_cfg(path)["llm"]["headers"] == {"X-Num": "123", "X-Bool": "True"}
    print("✓ 键值一律强转 str（HTTP 头只能是字符串）")


def test_json_string_accepted(tmp_path):
    """容错：整体给一个 JSON 串也认（C# 解析失败时的兜底路径用得上）。"""
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"}})
    config_store.set_config({"llm": {"headers": '{"X-A": "1"}'}}, config_path=path)
    assert _read_cfg(path)["llm"]["headers"] == {"X-A": "1"}
    print("✓ JSON 字符串形式也被接受")


def test_too_many_headers_rejected(tmp_path):
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://x/v1", "api_key": "k", "model": "m"}})
    with pytest.raises(ValueError):
        config_store.set_config({"llm": {"headers": {f"X-{i}": "v" for i in range(21)}}},
                                config_path=path)
    print("✓ 超过 20 条被拒")


def test_real_world_opencode_case(tmp_path):
    """config.example.json 里 documented 的那个真实场景，端到端钉一遍。"""
    path = _write_cfg(tmp_path, {"llm": {"base_url": "https://opencode.ai/zen/go/v1",
                                         "api_key": "k", "model": "mimo-v2.5"}})
    config_store.set_config({"llm": {"headers": {
        "x-opencode-session": "agent-loop-guigubahuang",
        "User-Agent": "guigubahuang-agent-loop/1.0",
    }}}, config_path=path)
    got = _read_cfg(path)["llm"]["headers"]
    assert got["x-opencode-session"] == "agent-loop-guigubahuang"
    assert got["User-Agent"] == "guigubahuang-agent-loop/1.0"
    print("✓ OpenCode Go 真实案例端到端通过")
