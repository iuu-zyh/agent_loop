"""群聊「每轮发言人数上限」配置键（09-18）：room.max_speakers_per_round。

背景：这个值原来是 `room_flow.RoomFlow.__init__` 的一个形参默认值（= 3），唯一构造点
（`ws_channel.ChatHub`）没传它 ⇒ 等于硬编码。现在它是白名单里的普通 int 键：
默认 3，玩家填多少就是多少（下限 1，不封顶 —— 候选名单本来就以成员数为上限）。

本文件只测「配置 → 活体」这条路：
  ① 默认值在 config_loader.DEFAULT_CONFIG 里（不在 config.json 也跑得起来）
  ② 白名单 + 类型归一 + 下限夹取（0/负数抬到 1，且留 WARNING 不静默）
  ③ 热分类：room 整块可热生效（装配层改 RoomFlow.k，不用重启 Python）
  ④ ChatHub 构造参数透传 + RoomFlow.set_max_speakers_per_round 的夹取口径
  ⑤ scripts/server.py 里两个落地口：装配时读配置、set_config 后分支 ⑥ 改活
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from agent_loop.config import config_loader, config_store
ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "scripts" / "server.py"


def _write_cfg(tmp_dir, data) -> str:
    path = os.path.join(str(tmp_dir), "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _read_raw(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- ① 默认值

def test_default_is_three_and_shows_up_in_the_panel_view(tmp_path):
    """默认 3；且必须出现在 get_config 的回填形状里（否则面板那一行显示不出值）。"""
    assert config_loader.DEFAULT_CONFIG["room"]["max_speakers_per_round"] == 3
    cfg = config_loader.load_config(config_path="nonexistent_room_cfg.json")
    assert cfg["room"]["max_speakers_per_round"] == 3

    path = _write_cfg(tmp_path, {})
    view = config_store.get_config(config_path=path)
    assert view["room"]["max_speakers_per_round"] == 3
    print("✓ 默认值 3，且进入 UI 回填形状（老 config.json 里没这个键也一样）")


# ---------------------------------------------------------------- ② 归一 / 夹取

def test_room_key_is_whitelisted_and_normalized(tmp_path):
    """白名单内、按 int 归一：字符串 "5" 也认（面板传的就是字符串）。"""
    path = _write_cfg(tmp_path, {})
    r = config_store.set_config({"room": {"max_speakers_per_round": "5"}}, config_path=path)
    assert r["updated"] == ["room.max_speakers_per_round"], r
    assert _read_raw(path)["room"]["max_speakers_per_round"] == 5
    assert isinstance(_read_raw(path)["room"]["max_speakers_per_round"], int)


def test_zero_and_negative_are_clamped_to_one_with_a_warning(tmp_path, monkeypatch):
    """0/负数没有语义（一轮谁都不许开口＝群聊停摆）⇒ 夹到 1，并且**必须留痕**。

    ★ 不经过日志框架收这条 WARNING ★：全套跑起来时，别的用例会（经 log_setup）把
    agent_loop 这棵树的 propagate 关掉、甚至把整棵树的级别抬上去，于是 caplog 与自挂
    handler 都会收不到 —— 断言就变成"看运行顺序决定红绿"的假红（本文件第一版正是这么红的）。
    这里把 config_store 的模块级 logger 换成一个记录桩：测的是"代码有没有喊"，
    而不是"当前日志配置恰好收不收得到"。
    """
    import agent_loop.config.config_store as cs

    seen: list[str] = []

    class _Stub:
        def warning(self, msg, *a, **k):
            seen.append(msg % a if a else msg)

        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    monkeypatch.setattr(cs, "log", _Stub())
    for bad in (0, -3):
        path = _write_cfg(tmp_path, {})
        cs.set_config({"room": {"max_speakers_per_round": bad}}, config_path=path)
        assert _read_raw(path)["room"]["max_speakers_per_round"] == 1, f"{bad} 没被夹到 1"
    assert any("max_speakers_per_round" in m for m in seen), \
        f"夹取必须留 WARNING（静默改写会让玩家以为 0 生效了）；实收：{seen}"


def test_big_value_is_kept_because_it_means_everyone(tmp_path):
    """不封顶：候选名单以成员数为上限，所以填大 = 「每轮全员都可能开口」，是合法用法。"""
    path = _write_cfg(tmp_path, {})
    config_store.set_config({"room": {"max_speakers_per_round": 50}}, config_path=path)
    assert _read_raw(path)["room"]["max_speakers_per_round"] == 50


def test_non_integer_is_rejected_fail_closed(tmp_path):
    """非数字 fail-closed 拒收（不是"夹一下就好"：那会把 3 悄悄变成 1）。"""
    path = _write_cfg(tmp_path, {})
    with pytest.raises(ValueError):
        config_store.set_config({"room": {"max_speakers_per_round": "abc"}}, config_path=path)


def test_unknown_room_key_is_rejected(tmp_path):
    """块内非白名单键照旧拒收（防面板/脚本写进任意键）。"""
    path = _write_cfg(tmp_path, {})
    with pytest.raises(ValueError):
        config_store.set_config({"room": {"max_speakers": 3}}, config_path=path)


# ---------------------------------------------------------------- ③ 热分类

def test_room_block_is_hot():
    """room 整块可热生效：装配层改 RoomFlow.k 即生效，不该让用户重启 Python。"""
    assert config_store._is_hot("room", "max_speakers_per_round") is True
    assert "room" in config_store.HOT_BLOCKS


def test_effective_is_hot_for_room_only(tmp_path):
    path = _write_cfg(tmp_path, {"room": {"max_speakers_per_round": 3}})
    r = config_store.set_config({"room": {"max_speakers_per_round": 4}}, config_path=path)
    assert r["effective"] == "hot", r
    assert "room.max_speakers_per_round" in r["hot_keys"]


# ---------------------------------------------------------------- ④ 活体：透传 + 夹取

def test_roomflow_setter_clamps_and_refuses_garbage():
    """夹取口径与构造参数一致：≥1、不封顶；非数字保持原值（绝不把 K 变成 0）。"""
    from agent_loop.room.room_flow import RoomFlow

    flow = RoomFlow(loop=None, run_turn=lambda *a, **k: None,
                    agent_lock=lambda *a, **k: None, room_lock=lambda *a, **k: None,
                    max_speakers_per_round=3)
    assert flow.k == 3
    assert flow.set_max_speakers_per_round(5) == 5 and flow.k == 5
    assert flow.set_max_speakers_per_round(0) == 1 and flow.k == 1
    assert flow.set_max_speakers_per_round(-9) == 1 and flow.k == 1
    assert flow.set_max_speakers_per_round(50) == 50           # 不封顶
    assert flow.set_max_speakers_per_round("abc") == 50        # 非数字：保持原值
    # 构造期同样夹取（0/负数不会造出 k=0 的编排器）
    flow0 = RoomFlow(loop=None, run_turn=lambda *a, **k: None,
                     agent_lock=lambda *a, **k: None, room_lock=lambda *a, **k: None,
                     max_speakers_per_round=0)
    assert flow0.k == 1


def test_chathub_passes_the_configured_cap_through():
    """ChatHub 的构造参数必须透传到 RoomFlow.k —— 断在这里，面板改的值就永远不生效。

    ★ 刻意不 import test_room 的 _hub/_seed ★：那套桩会把共享存储目录（tests 顶层的 ROOT）
    建起来又清掉，夹在中间跑会把 test_room.py 里**依赖落盘顺序**的用例（如
    test_r8_invariant_i3_save_load_keeps_cursor_with_memory）搞红 —— 那是测试间的顺序耦合，
    不是产品缺陷。这里只需要一个能构造的最小对象：ChatHub 只把 loop/ws 存着用。
    """
    from agent_loop.transport.ws_channel import ChatHub

    class _Ws:
        pass

    class _Loop:
        pass

    assert ChatHub(_Loop(), _Ws(), max_speakers_per_round=2).rooms.k == 2
    assert ChatHub(_Loop(), _Ws()).rooms.k == 3          # 不给就用默认 3


# ---------------------------------------------------------------- ⑤ 装配层两个口

def test_server_reads_the_key_at_assembly_and_has_a_hot_branch():
    """scripts/server.py：装配时读配置传进 ChatHub + set_config 后改活（分支 ⑥）。

    10-21 随迁：锚点从裸 `int(room.get(k, 3))` 迁到显式 None 判定形态——旧写法在
    "键存在值为 null"（手编 config / Gemini 式显式 null）时 int/float(None) 装配期炸穿。
    null=回默认；0 原样下传（由 RoomFlow 抬升闸处理，三层一致的老规矩，装配层不抢答）。
    """
    src = SERVER.read_text(encoding="utf-8")
    assert re.search(r'max_speakers_per_round=int\(_spk\) if _spk is not None else 3', src), \
        "装配时没有把 room.max_speakers_per_round 传进 ChatHub（且需 None 容错）"
    assert '_spk = room.get("max_speakers_per_round")' in src.replace("                      ", "") \
        or 'room.get("max_speakers_per_round")' in src, "_spk 的取值来源不在"
    i = src.index('if "room.max_speakers_per_round" in hot')
    seg = src[i: src.index("return result", i)]
    assert "set_max_speakers_per_round" in seg, "热生效分支没有调用 RoomFlow 的 setter"
    print("✓ 装配读取 + 热生效分支 ⑥ 都在")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
