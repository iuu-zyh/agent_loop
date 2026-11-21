"""攻击链「收尾占位」契约（09-22）：处置页收口之后、链还没演完的那段时间，不许发起新动作。

**要解决什么（真机实测）**：`ResolveAtVerdictPage` 一调，`DramaGate` 的 busy 键就释放了 ——
而链的尾页 10 秒后才来：

    22:53:14.833  收口（处置页 20403，探针在同一帧看到"台账在飞"）
    22:53:24.474  尾页 20413 InitData
    22:53:26.019  关窗（链真的结束）

那段时间 `world.battle.isBattle` 仍为真（游戏自己在结算），而 `ToolExecutor` 唯一的闸
（`DramaGate.TryDefer`）已经放行 ⇒ 「玩家在处置页上边聊、NPC 边发起一个新动作」是通的，
会与正在演出的链并行；而"剧情窗创建/销毁期开 UI"正是 09-10 那次立绘槽位变 0 尺寸事故的形状。

**收住的口径**（用户拍板：先收住，实机验证过游戏能接受再放开）：
  · 收口时若**剧情窗还开着** ⇒ 起占位（判据刻意不写"处置页"：逃跑/战败那两支的离场点击与关窗
    同毫秒，天然不吃这一条）；
  · 占位期间：`TryDefer` 拒新票（已在表里的槽位不受影响）+ `game_drama` 那一轮带 `busy`
    （只许说话 + 只读）；
  · 占位清除：剧情窗关了**且战斗位已落**、并连续 1.5s 安静（`BattleOutcome.ChainHoldTick`；
    单看"窗不可见"会被"别的 UI 盖顶"骗 —— 玩家点「对话」开面板那一瞬层顶就不再是剧情窗）/
    超时（`DramaGate.Tick`）/ 换世界（`ForceClose`）/ 新台账开始（`Arm`）。

本文件锁这些事 —— 每一条都是"删一行就静默回退，而真机上要过十几秒才看得出来"的地方。

  A. `DramaGate` 有占位状态与拒票判据，且拒票原因**可读**（不能与"撞窗 busy"共用一句话）
  B. 占位超时的检查**在 `_pending` 早退之前**（占位生效时表里通常一个槽都没有，否则永远不过期）
  C. `BattleOutcome` 收口时按"窗还开着"起占位、窗关即撤，并在换世界/新台账时兜底撤
  D. 四处领票点的拒绝文案都走 `RejectText`（别把"链在收尾"说成"玩家没响应"—— 后者会让模型重试）

跨语言那一半（busy 帧 → Python 剔动作工具 + `BUSY_NOTE`）早已在 `ws_channel.py` /
`initiative.py` 就位（那是 NPC 自主开口那条路在用的同一份），这里只需钉 C# 侧真的把它带上。
"""

from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------- A. 占位状态与拒票判据 ----------


def test_gate_has_chain_hold_and_reject_text():
    """占位状态 + 拒票原因必须是**两个可读的口**（`ChainHold` / `RejectText`）。"""
    g = _read(CS / "drama" / "DramaGate.cs")
    assert "public static bool ChainHold" in g, \
        "DramaGate 没有 ChainHold 出口 —— SendGameDrama 的 busy 与探针都读它"
    assert "public static void SetChainHold(string reason, int maxSeconds)" in g, "没有起占位的方法"
    assert "public static bool ClearChainHold(string why)" in g, "没有撤占位的方法"
    assert "public static string RejectText(string fallback)" in g, \
        "没有 RejectText —— 四处领票点只能共用「玩家没响应」那句（会误导模型空等重试）"


def test_defer_rejects_on_hold_before_the_busy_scan():
    """占位判据要写在**撞窗扫描之前**，并且把原因留下、下次撞窗时清掉。"""
    g = _read(CS / "drama" / "DramaGate.cs")
    m = re.search(r"public static bool TryDefer\(int dramaId, int expireSeconds, bool beat, out int slot\)"
                  r"(.*?)\n        \}", g, re.S)
    assert m, "找不到 TryDefer(int,int,bool,out int)"
    body = m.group(1)
    assert "if (_chainHold)" in body, "TryDefer 没有认占位 —— 收口之后新动作照开"
    assert body.index("if (_chainHold)") < body.index("if (p.DramaId == dramaId)"), \
        "占位判据写在撞窗扫描之后 —— 占位期间撞窗会给出误导的拒绝原因"
    assert "_lastReject = _chainHoldReason;" in body, "拒票没有留下「链在收尾」的原因"
    assert '_lastReject = "";' in body, \
        "没把上次的拒票原因清掉 —— 下一次撞窗会沿用那句旧原因（两件事混成一件）"


# ---------- B. 占位超时必须跑得到 ----------


def test_chain_hold_expiry_is_checked_before_the_pending_early_return():
    """占位生效时 `_pending` 通常为空 —— 超时检查若排在"表空即早退"之后，它永远不过期。"""
    g = _read(CS / "drama" / "DramaGate.cs")
    m = re.search(r"private static void Tick\(\)(.*?)\n        \}", g, re.S)
    assert m, "找不到 DramaGate.Tick"
    body = m.group(1)
    assert "if (_chainHold && DateTime.UtcNow >= _chainHoldUntil)" in body, "占位没有超时兜底"
    assert "if (_pending.Count == 0) return;" not in body, \
        "旧的「表空即早退」写法还在 —— 占位超时永远轮不到，动作系统会被挡到重启"
    assert body.index("_chainHoldUntil") < body.index("if (_pending.Count > 0)"), \
        "占位超时检查排在 `_pending` 分支之后 —— 早退会让它跑不到"


# ---------- C. BattleOutcome 里的起/撤时机 ----------


def test_outcome_holds_only_while_a_drama_window_is_still_open():
    """收口后**窗还开着**才起占位；**两个信号都说结束**才撤（否则玩家聊到一半想动手会一直被拦住）。"""
    so = _read(CS / "drama" / "BattleOutcome.cs")
    assert "DramaGate.SetChainHold(ChainHoldReasonText, ChainHoldSeconds)" in so, "收口时没有起占位"
    assert "NpcInitiativeMonitor.IsDramaWindowShowing()" in so, \
        "占位判据不是「剧情窗还开着」—— 逃跑/战败那两支会被误伤"
    assert "战局刚了" in so, "占位拒绝文案不见了（或被改回了「玩家没响应」那句）"

    m = re.search(r"private static void ChainHoldTick\(\)(.*?)\n        \}", so, re.S)
    assert m, "没有 ChainHoldTick（窗关撤销那条路）"
    body = m.group(1)
    assert "DramaGate.ClearChainHold(" in body, \
        "窗关了没有撤占位 —— 玩家聊到一半想让 NPC 递个东西会被一条过期占位拦住"
    assert "NpcInitiativeMonitor.BattleActive()" in body, \
        ("撤占位只看「剧情窗不可见」不够：玩家点「对话」把我方面板盖到层顶那一瞬，层顶就不再是剧情窗"
         "（而尾页可能还没来，实测收口→尾页 10s）⇒ 会在链还在演的时候就把动作放行。"
         "必须再要求「战斗位也落了」（实测 B1 一直撑到关窗那一刻）")
    assert "ChainHoldQuietSeconds" in body, \
        "两个信号一静默就立刻撤 —— UI 重建/销毁的中间帧会把占位抖掉；要连续安静一小段再撤"
    assert "bool w = true, fighting = true;" in body, \
        "取不到信号时默认成了「已结束」—— 铁律：查不到 ≠ 关着（要保守不撤）"
    assert body.index("if (w || fighting)") < body.index("DramaGate.ClearChainHold("), \
        "静默计时没有在「任一信号还在」时复位 —— 抖动会累积成误撤"

    t = re.search(r"private static void Tick\(\)(.*?)\n        \}", so, re.S)
    assert t, "找不到 BattleOutcome.Tick"
    tb = t.group(1)
    assert "ChainHoldTick();" in tb, "帧回调没有跑占位检查"
    assert tb.index("ChainHoldTick();") < tb.index("if (_slot <= 0) return;"), \
        "占位检查写在 `_slot <= 0` 早退之后 —— 收口后槽位已是 -1，占位永远不会被撤"

    # 两条兜底撤：换世界（ForceClose，在它自己的早退之前）与"新台账开始"（Arm）
    assert "try { DramaGate.ClearChainHold(reason); } catch { }" in so, "换世界没有撤占位"
    assert 'DramaGate.ClearChainHold("新的战斗台账开始")' in so, "新一场战斗开始时没有撤掉旧占位"


# ---------- D. 四处领票点的文案 ----------


def test_all_defer_sites_speak_the_right_reason():
    """每一处领票失败都必须有机会说出"链在收尾"，而不是一律"玩家没响应"。"""
    te = _cs_tools.tool_src()
    ss = _read(CS / "UI" / "ShowDramaService.cs")
    assert te.count("DramaGate.RejectText(") == 3, (
        "ToolExecutor 的领票点没有全部改走 RejectText（赠送 / 论道双修 / 原生动作三处）—— "
        "链在收尾时会被说成「已有一个行动在等待玩家响应」，模型会以为玩家没回应而反复重试")
    assert ss.count("DramaGate.RejectText(") == 1, "ShowDramaService 的领票点没有改走 RejectText"


def test_battle_close_releases_the_claim_session():
    """收口时**放行会话**：否则同窗翻页的尾页三处皆无（按钮 / 投递 / tool result）。

    会话（窗指针 + unitID，TTL 120s）**不随凭证吊销** —— 那是给 marry 那类"Resolve 之后游戏才开
    下一层"用的。但战斗链收口意味着这场戏的结局已经给过模型了：之后同窗翻出来的页若还被认领，
    就既不注入按钮（玩家聊不到）、也不进 tool result（台账已不在）、段里还标"照记不投"（投不出）。
    今晚尾页 20413 是**新窗实例**、侥幸没被认领；同窗翻页的支（如切磋 21401→21402）就会丢。
    """
    so = _read(CS / "drama" / "BattleOutcome.cs")
    assert "DramaNativeClaim.ReleaseSession(_uid)" in so, "收口没有放行会话"
    nc = _read(CS / "drama" / "DramaNativeClaim.cs")
    assert "public static void ReleaseSession(string unitID)" in nc, "凭证层没有 ReleaseSession"
    m = re.search(r"public static void ReleaseSession\(string unitID\)(.*?)\n        \}", nc, re.S)
    assert m, "找不到 ReleaseSession 的实现"
    body = m.group(1)
    assert "if (_sessionUnit != unitID) return;" in body, \
        "放行会话没认人 —— 会把别人的会话一起清掉（另一个动作的第二层翻页就漏了）"
    assert "_sessionPtr = IntPtr.Zero;" in body, "会话指针没清 —— 之后同窗翻页照样被认领"


def test_game_drama_carries_busy_from_the_hold():
    """`game_drama` 那条路要带 `busy`：判据是**占位**（不是"任何 UI 开着"，那是更大的口径）。"""
    wc = _read(CS / "bridge" / "WsClient.cs")
    # 09-24 扩参：末尾追加 `bool verdict = false`（处置页说明，契约见 test_verdict_page_contract.py）
    # —— 断言随之改为"前三个可选参数都在 + 新参数在"，本契约的口径（busy/action）不变。
    # 09-28 扩参：尾部再追加 `string unitId = null`（说话人精确身份，契约见
    # test_initiative_identity_contract.py）—— 本契约口径（busy/action）不变。
    assert "JArray lines = null, bool busy = false, bool action = false," in wc \
        and "bool verdict = false, string unitId = null)" in wc, \
        "SendGameDrama 没有 busy/action（或后续加的 verdict/unitId）形参"
    assert 'if (busy) frame["busy"] = true;' in wc, "busy 没有落到帧上"
    ui = _read(CS / "UI" / "DramaAiOption.cs")
    assert "lines, DramaGate.ChainHold," in ui \
        and "actionActive, verdictPage, DramaNativeClaim.UidOf(unit));" in ui, \
        "剧情窗那条路没有把「链在收尾」当 busy 带出去（或 09-28 的说话人身份被删）" \
        " —— 处置页上边聊边被发起动作 / 只能按名猜身份"
    # 审计对称：`source` 里两条分支（game_drama / 通用）都要记 busy，否则回看账本时
    # 分不出这一轮是"忙碌轮"（行为约束在，但事后对账少一条 —— 曾只给通用分支加了）
    wsc = _read(ROOT / "transport" / "ws_channel.py")
    assert wsc.count('**({"busy": True} if busy else {})') == 2, \
        "game_drama 分支的 source 没记 busy（或通用分支那条被删了）—— 审计字段两处都要带"
