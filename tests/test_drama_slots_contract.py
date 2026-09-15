"""确认窗立绘位契约：**左位 = 玩家、右位 = 对方**（全仓扫描，09-13）

规则本身写在 `csharp/UI/ShowDramaService.cs` 的文档注释里（09-08 用户拍板 + 反编实证
`DramaData{ unitLeft = g.world.playerUnit, unitRight = unit }`，对齐游戏原版神识传音窗方向）。
但它是**口头的**：编译器管不到，传反了不报错、不抛异常，只是玩家在屏幕上看到**两个自己**。

已经踩过两次，症状一模一样（"右侧立绘是玩家"）：
  · 09-12 自主互动确认窗把 `wub`（NPC）放左位 —— 修正留痕在 `NpcInitiativeMonitor.cs:352` 注释里
  · 09-13 `trade` 初版把 `(seller, buyer)` 直接当立绘位传 —— 卖家是 NPC、买家是玩家时右位落成玩家，
    用户实机截图：左右两个立绘与名字都是「缪嘉歆」

所以这里做一次全仓扫描：所有 `ShowDramaService.ShowConfirm*` 调用点的**左位实参**
不得是明显的 NPC 侧标识符。启发式但够用 —— 当前 5 处调用点全通过，而两次事故的写法都会被打红。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "csharp"

# 左位一旦是这些名字，基本可以断定放的是 NPC 侧（玩家侧在本仓恒为 player/target/dLeft）
NPC_SIDE = {"npc", "wub", "seller", "buyer", "npcUnit", "unit", "other"}


def _split_args(s: str) -> list[str]:
    """按顶层逗号切参数（括号/泛型尖括号内的逗号不算）。"""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "(<[":
            depth += 1
        elif ch in ")>]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _call_sites(src: str):
    """产出 (行号, [参数...]) —— 每个 ShowDramaService.ShowConfirm(Simple) 调用点。"""
    for m in re.finditer(r"ShowDramaService\.ShowConfirm(?:Simple)?\(", src):
        start = m.end()
        # 取到本次调用的参数列表结束：够长的窗口 + 顶层括号配平
        depth, i = 1, start
        while i < len(src) and depth > 0:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        args = _split_args(src[start : i - 1])
        line = src.count("\n", 0, m.start()) + 1
        yield line, args


def test_confirm_windows_keep_player_on_left():
    src_all = ""
    sites = []
    for f in sorted(ROOT.rglob("*.cs")):
        if any(p in f.parts for p in ("bin", "obj")):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        src_all += text
        for line, args in _call_sites(text):
            sites.append((f.name, line, args))

    assert len(sites) >= 5, f"只扫到 {len(sites)} 处确认窗调用，扫描逻辑可能失效了"
    bad = []
    for fname, line, args in sites:
        if len(args) < 3:
            bad.append((fname, line, "参数不足，解析失败：" + str(args)))
            continue
        left = args[1].strip().lstrip("(").strip()
        if left in NPC_SIDE:
            bad.append((fname, line, f"左位实参是 {left!r}（NPC 侧）"))
    assert not bad, (
        "确认窗立绘位传反了 —— 约定是 left=玩家、right=对方（见 ShowDramaService 文档）。"
        "传反的实机表现是「玩家看到两个自己的立绘与名字」，09-12 与 09-13 各出过一次：\n  "
        + "\n  ".join(f"{f}:{ln} {why}" for f, ln, why in bad)
    )
    print(f"✓ 确认窗立绘位：{len(sites)} 处调用点左位均为玩家侧")


def test_trade_derives_left_right_from_player():
    """trade 的两位当事人都不固定是玩家，必须按「谁是玩家」算出立绘位，不能直接透传 (seller, buyer)。"""
    src = (ROOT / "ToolExecutor.cs").read_text(encoding="utf-8")
    i = src.index("private JObject Trade(JObject args)")
    j = src.index("private static string FindStackSoleID")
    body = src[i:j]
    assert "IsPlayerUnit(buyer)" in body and "IsPlayerUnit(seller)" in body, (
        "trade 没有按「谁是玩家」决定立绘位（买卖双方都可能不是玩家）"
    )
    assert re.search(r"dLeft\s*,\s*dRight", body), "立绘位没有走 dLeft/dRight 分流"
    assert not re.search(r"DramaEconomyItemBase\s*,\s*\n\s*seller\s*,\s*buyer", body), (
        "trade 又把 (seller, buyer) 直接当立绘位传了"
    )
    print("✓ trade：立绘位按玩家所在侧推导（玩家买→左买右卖；NPC↔NPC→左卖右买）")


if __name__ == "__main__":
    test_confirm_windows_keep_player_on_left()
    test_trade_derives_left_right_from_player()
    print("\n确认窗立绘位契约：2/2 通过")
