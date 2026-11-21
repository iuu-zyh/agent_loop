"""None-参数防御契约（10-21）：`.get(key, default)` 护不住"键在值为 null"的 int() 炸点。

**用户报告**：接 Gemini API 后点设置面板"保存"，弹出
`TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'`。

**模式根因**：`int(cfg.get("ctx_window", 32768))` 这类写法只防"键缺失"；手编 config.json
（Gemini 用户心智里 null=自动）或模型显式发 `"count": null`（Gemini 兼容层惯把未填参数
全量补 null）时，`.get` 返回 **None**，`int(None)` 当场炸。炸点若设置在保存链路上
（compactor 热换 → swap_error 原样回显给面板），玩家看到的就是"**点保存就报类型错**"。

**修法**：五处生产点位全部改成"先取值、显式判 None、再回落"。钉两件事：
  A. 五个点位的旧裸形态**绝迹**、新形态在场（源码契约）
  B. 行为回归：账本里混入 `turn:null` 事件，历史投影不炸且按顺序补号（行为测试）

不在本契约范围：`transport/bridge.py`（StubGameBridge 仅离线开发用）、
`session/stats.py`（三处 int 各被紧邻 try/except 包住，坏一条跳一条，不中断）。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    src = re.sub(r"#.*", "", src)
    return src


# ---------- A. 五个点位的源码契约 ----------

def test_history_projection_turn_step_none_guarded():
    src = _code_only(_read("session/history.py"))
    assert 'int(data.get("turn",' not in src and 'int(data.get("step",' not in src, \
        "旧的裸 .get 默认值形态回来了（键在值为 null 时它护不住）"
    assert '_tv = data.get("turn")' in src and "int(_tv) if _tv is not None else turn_no + 1" in src
    assert '_sv = data.get("step")' in src and "int(_sv) if _sv is not None else step_no + 1" in src


def test_server_compactor_ctx_none_guarded():
    src = _code_only(_read("scripts/server.py"))
    assert 'int(cc.get("ctx_window"' not in src, "压缩器热换的裸 int(.get) 形态回来了（保存报错原点）"
    assert '_ctx_raw = cc.get("ctx_window")' in src
    assert "if _ctx_raw is None:" in src
    assert "int(_ctx_raw)" in src
    # 同文件另一处：启动装配的三个数值（null 值键会在装配期炸穿整个服务）
    assert 'int(room.get("max_speakers_per_round") or 3)' not in src, "中途形态（or 3 把 0 也吞了）不该留在树里"
    assert "max_speakers_per_round=int(_spk) if _spk is not None else 3" in src
    assert "float(_mi) if _mi is not None else 300.0" in src, \
        "min_interval 同款洞：float(None) 装配期炸（报错文案会是 float() 开头，同族病）"


def test_room_cursors_none_guarded():
    src = _code_only(_read("room/room.py"))
    assert 'int(self.joined.get(' not in src, "投递游标的裸形态回来了"
    assert "int(_jv) if _jv is not None else int(self.last_seq or 0)" in src
    assert "int(joined_at) if joined_at is not None else 0" in src, \
        "入群位置写入端的 None 容错不在（None 写进 joined 会毒化后续投递）"


def test_ws_delete_turns_filters_none():
    src = _code_only(_read("transport/ws_channel.py"))
    assert "for t in (p.get(\"turns\") or []) if t is not None]" in src, \
        "删除回合的 turns 列表没有滤 null 项"


# ---------- B. 行为回归：turn=null 的账本不炸投影，且顺序补号 ----------

def _import_history():
    import sys
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    from agent_loop.session import history  # type: ignore  # noqa
    return history


def test_projection_survives_null_turn_events():
    h = _import_history()
    log = [
        {"type": "turn/start", "data": {"turn": None, "scene": ""}},
        {"type": "step/start", "data": {"turn": None, "step": None}},
        {"type": "turn/end", "data": {"turn": None, "reason": {"kind": "completed"}}},
        {"type": "turn/start", "data": {"turn": 2, "scene": ""}},
        {"type": "turn/end", "data": {"turn": 2, "reason": {"kind": "completed"}}},
    ]
    session = SimpleNamespace(log=log)
    out = h.project_ui_history(session, max_turns=0)
    # 第一回合 turn=null → 回落"顺序 +1"补成 1；第二回合显式 2 不受影响
    turns = [it for row in out["items"] for it in ([row] if isinstance(row, dict) else [])]
    assert out.get("complete_turns", 0) == 2, \
        "null 事件被当成炸弹或整回合丢弃（应回落顺序号）：%r" % (out,)
