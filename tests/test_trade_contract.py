"""trade（买卖）源码契约（09-13）

`trade` 的实质是四条字段变更：卖方背包 −道具 / 买方背包 +道具；买方灵石 −价 / 卖方灵石 +价。
它没法离线跑（要游戏运行时 + 真实背包），所以这里钉住三类**只能靠读源码守住**的静默出错：

  1. **拆栈只能用引擎接口**：`DataProps.DelProps(soleID, n)`。
     手写 `propsCount = n` 不是"取出 n 个"而是"把整栈改成 n 个"——余量既没有新栈承载、
     也无法在失败时找回（真丢道具）。这条坑 `TakePropsFromBag` 里已经用注释钉过一次。
  2. **数量必须整笔满足**：买卖是定价交易，少给货却收全款不成立。
     与 `economy_item` 赠送的"有多少送多少"语义相反，所以必须显式 `got < count` → 回滚 + 失败。
  3. **落刀顺序 = 拆栈 → 灵石 → 入包**：前两步失败时道具都还在手上，`RollbackGive` 能无损退回卖方；
     反序（先入包）失败时得从买方"抠回来"，此时栈可能已被合并、口径不稳。
     同时每一步失败都要有补偿（钱/货双向），不许吞东西。
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "csharp" / "ToolExecutor.cs"


def _method_body(src: str, signature: str) -> str:
    """按大括号配平从签名处截出方法体。"""
    i = src.index(signature)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
        k += 1
    raise AssertionError(f"方法体未闭合：{signature}")


def test_trade_never_writes_props_count():
    """拆栈必须走 DelProps；源码里不得出现 `propsCount =` 赋值（那是"整栈改成 n"不是"取出 n"）。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private JObject Trade(JObject args)")
    assert "TakePropsFromBag" in body, "trade 没有复用已验过的两段式拆栈（TakePropsFromBag）"
    assert not re.search(r"propsCount\s*=", body), (
        "trade 里出现了 propsCount 赋值——那不是「取出 N 个」而是「把整栈改成 N 个」，"
        "余量会凭空消失且失败时找不回（TakePropsFromBag 的坑注）"
    )
    print("✓ trade：拆栈走引擎接口，无 propsCount 直写")


def test_trade_requires_exact_count():
    """卖方存货不足必须整笔作废并回滚，不得像赠送那样"有多少给多少、收全款"。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private JObject Trade(JObject args)")
    assert re.search(r"if\s*\(\s*got\s*<\s*count\s*\)", body), (
        "缺少 got < count 的整笔校验：买卖是定价交易，少给货却收全款不成立"
    )
    # 该分支必须回滚已拆出的道具（否则道具凭空停在"已取出"状态）
    seg = body[body.index("got < count") :]
    seg = seg[: seg.index("}")]
    assert "RollbackGive" in seg, "整笔不足的分支没有 RollbackGive，拆出来的道具会悬空"
    assert "预校验" in body or "have < count" in body, "预校验阶段没有先查卖方存量（会弹了窗才发现做不了）"
    print("✓ trade：整笔满足（预校验 + 竞态兜底都回滚）")


def test_trade_deduction_order_and_compensation():
    """落刀顺序必须是 拆栈 → 灵石 → 入包，且失败路径钱货双向补偿。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private JObject Trade(JObject args)")
    i_take = body.index("TakePropsFromBag")
    i_money = body.index("TransferLingshi")
    i_add = body.index("buyerDp.AddProps")
    assert i_take < i_money < i_add, (
        "落刀顺序被改成「拆栈→入包→灵石」之类：先入包的话，之后任一步失败都得从买方把道具抠回来，"
        "而那时栈可能已被合并，口径不稳。正解是让道具在付款前始终握在手上（RollbackGive 可无损退回）"
    )
    # 灵石失败 → 退道具；入包失败 → 抠回道具 + 退灵石
    assert body.count("RollbackGive") >= 2, "补偿路径不足：灵石失败/入包失败都应能把道具退回卖方"
    assert "TransferLingshi(seller, buyer" in body, "入包失败时没有把灵石退还给买方（吞币）"
    assert re.search(r"CostPropItem\(propsID", body), (
        "入包失败时没有按 propsID 从买方抠回道具——按 soleID 抠在栈被合并后会失败"
    )
    print("✓ trade：拆栈→灵石→入包 + 钱货双向补偿")


def test_trade_gated_by_confirm_window():
    """必须弹确认窗（复用组装好的 ShowConfirm/DramaGate），且复用 economy_item 段不新占 ModExcel 段。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private JObject Trade(JObject args)")
    assert "ShowDramaService.ShowConfirm" in body, "trade 没有走已组装好的确认窗"
    assert "Claimed(" in body, "trade 没有用 Claimed 包住（原生「AI 应对」选项会重复弹）"
    assert "ModIds.DramaEconomyItemBase" in body, (
        "trade 应复用 economy_item 段：DramaGate 全局同时只允许一个挂起窗，且壳条目正文/选项"
        "全部运行时覆盖，新占一段只会在 ModExcel 里多一条永远用不到的登记"
    )
    assert "case \"trade\"" in src, "ToolExecutor.Execute 没有分发 trade"
    print("✓ trade：确认窗 + DramaGate 挂起 + 复用 economy 段")


if __name__ == "__main__":
    test_trade_never_writes_props_count()
    test_trade_requires_exact_count()
    test_trade_deduction_order_and_compensation()
    test_trade_gated_by_confirm_window()
    print("\ntrade 源码契约：4/4 通过")
