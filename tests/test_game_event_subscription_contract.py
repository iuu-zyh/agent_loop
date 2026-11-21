"""`g.events` 订阅必须成对：每个 `On` 都要有对应的 `Off`（11-19 立）。

## 事故现象

真机 `Player.log`（5 段世界）里，`OnIntoWorldEvent` 每次进世界都跑 **两遍**。
判据是 6 处互相独立的副作用，条数**一比一**（不是某一行重复打印）：

    load_happened            2 次
    [PortraitCache] 世界重载  2 次
    [ToolPanel] 换存档        2 次
    [UnitIdRegistry] 换存档   2 次
    [Ledger] 已加载盘上账本   2 次
    世界已就绪                2 次

而且两次是**先后完成**的、不是并发：第一次 `PortraitCache` 清掉 3 条，
第二次清掉 0 条（已被第一次清空）。

## 根因形状

`EventsMgr.On(name, handler, priority, once)` 把处理器加进一张**按事件名索引的表**，
`Emit` 时把表里每一项都调一遍。所以：

  · `On` 只加不减 ⇒ 表只会变长 ⇒ 每进一次世界多调一次；
  · `Destroy` 里**只退订了 `WorldAddDay`**（`SaveData` / `IntoWorld` 从来没退过）。

这是"注册与退订不对称"的经典形态，而且坏起来**完全静默**：不报错、不崩溃，
只是每进一次世界多跑一遍换档清理。

## 为什么每次多跑一遍是**有实际危害**的（不只是浪费）

`OnIntoWorldEvent` 里有两处**不幂等**的收口：

  · `DeferredActionQueue.OnWorldChanged()` —— 作废所有排队中的动作。
    玩家若在两次执行之间刚提交一个动作（"帮我送个礼"），第二遍会把它当上一世界的
    残留直接丢掉：提示弹了、事情永远不发生。
  · `DramaGate.ForceResolveAll(...)` —— 强制收掉所有在飞的剧情槽位。
  · `_worldEntrySeq` 连加 2 ⇒ `StartIdentityRetry` 里那条"序号变了就自我作废"
    的重试轮会立刻失效，身份可能永远补不回来。

## 本文件钉什么

① `ModMain` 里每个 `g.events.On` 都必须在 `Destroy` 里有对应 `Off`；
② `Init` 侧在注册前也要先 `Off` 一次 —— `Init` 与 `Destroy` **不保证严格配对**
   （真机日志里出现过只 `Init` 不见 `Destroy` 的段），注册点自己清一遍是唯一
   不依赖调用顺序的做法；
③ `Off` 必须先于把字段置 `null` —— 反过来的话拿不到处理器引用，
   表里那一份就永久留下（"看起来写了退订、实际没退"的经典写法错误）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODMAIN = ROOT / "csharp" / "app" / "ModMain.cs"


def _code_only(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//.*", "", src)


def _method_body(code: str, signature: str) -> str:
    """按花括号配平取方法体（正则的非贪婪会被内层 `catch { }` 截断）。"""
    i = code.find(signature)
    assert i >= 0, f"找不到 {signature}"
    j = code.index("{", i)
    depth, end = 0, -1
    for k in range(j, len(code)):
        if code[k] == "{":
            depth += 1
        elif code[k] == "}":
            depth -= 1
            if depth == 0:
                end = k
                break
    assert end > j, f"{signature} 方法体没闭合"
    return code[j:end]


def test_every_on_has_a_matching_off_in_destroy():
    """`ModMain` 里订阅的每一个游戏事件，都必须在 `Destroy` 里退订。

    `EventsMgr` 的订阅是按事件名存在表里的，`On` 只加不减 ⇒ 表只会变长、每次派发
    多跑一遍。`SaveData` / `IntoWorld` 原先从来没退过，`OnIntoWorldEvent` 因此
    每次进世界跑两遍（真机 6 处副作用条数一比一）。
    """
    code = _code_only(MODMAIN.read_text(encoding="utf-8"))
    ons = set(re.findall(r"g\.events\.On\(\s*EGameType\.(\w+)", code))
    assert ons, "ModMain 里没有任何 g.events.On 订阅（是不是被重构走了？）"

    destroy = _method_body(code, "public void Destroy()")
    offs = set(re.findall(r"g\.events\.Off\(\s*EGameType\.(\w+)", destroy))

    missing = sorted(ons - offs)
    assert not missing, (
        f"这些事件在 Destroy 里没有退订：{missing}"
        "\n订阅只加不减 ⇒ 每进一次世界多派发一遍（真机已实测 OnIntoWorldEvent 跑两遍）。"
        "\n其中 DeferredActionQueue.OnWorldChanged / DramaGate.ForceResolveAll 不幂等，"
        "\n多跑一遍会把玩家刚提交的排队动作当作旧世界残留丢掉。"
    )


def test_init_unsubscribes_before_resubscribing():
    """`Init` 侧注册前要先 `Off` 一次 —— 不依赖 `Init`/`Destroy` 严格配对。

    真机日志里出现过只 `Init` 不见 `Destroy` 的段。只有 `Destroy` 一侧做退订，
    那种段会永久多留一份 handler。
    """
    code = _code_only(MODMAIN.read_text(encoding="utf-8"))
    init = _method_body(code, "public void Init()")
    # Init 里每个 On，其各自的事件名前必须也出现过 Off
    ons = re.findall(r"g\.events\.On\(\s*EGameType\.(\w+)", init)
    assert ons, "Init 里没有任何 g.events.On"
    for ev in set(ons):
        assert re.search(r"g\.events\.Off\(\s*EGameType\." + ev + r"\b", init), (
            f"Init 里注册了 EGameType.{ev} 却在注册前没有先 Off 一次。"
            "\nInit/Destroy 不保证严格配对，注册点自己清一遍才是唯一不依赖调用顺序的做法。"
        )
    # 每个 On 事件名的 `Off` 必须出现在它的 `On` **之前**
    for ev in set(ons):
        on_at = re.search(r"g\.events\.On\(\s*EGameType\." + ev + r"\b", init).start()
        off_at = re.search(r"g\.events\.Off\(\s*EGameType\." + ev + r"\b", init).start()
        assert off_at < on_at, (
            f"Init 里 EGameType.{ev} 的 Off 出现在 On **之后** —— 那样先加一份再退一份，"
            "旧的那份仍然留在表里（等于没修）"
        )


def test_off_precedes_field_null():
    """`Off` 必须**先于**把 handler 字段置 null。

    反过来的话 `Off` 拿不到处理器引用（传 null 进去什么也不做），表里那一份永久留下。
    这是"看起来写了退订、实际没退"的经典写法错误，且完全没有日志。
    """
    code = _code_only(MODMAIN.read_text(encoding="utf-8"))
    destroy = _method_body(code, "public void Destroy()")
    for field in ("_worldAddDayHandlerIl2Cpp", "_saveDataHandlerIl2Cpp", "_intoWorldHandlerIl2Cpp"):
        off_at = destroy.find(f"g.events.Off(")
        # 找到该字段被 Off 的那一段
        m = re.search(r"g\.events\.Off\([^;]*" + field, destroy)
        assert m, f"Destroy 里没有对 {field} 调用 Off"
        null_at = re.search(field + r"\s*=\s*null", destroy)
        assert null_at, f"Destroy 里没有把 {field} 置 null（对象图残留引用）"
        assert m.start() < null_at.start(), (
            f"{field} 的置 null 出现在 Off **之前** —— Off 拿不到处理器引用，订阅永远留在表里"
        )


def test_into_world_handler_counts_entry():
    """`OnIntoWorldEvent` 必须把"第几次进入"打进日志。

    没有它，"一行 = 一次调用"与"两行 = 两次调用"分不开 —— 排查时曾把
    `WorldMgr:IntoWorld()` 的**调用栈装饰行**误当成调用次数。
    带序号之后：同一 N 出现两次 = 一次调用打两行；两个不同 N = 真的调了两次。
    """
    code = _code_only(MODMAIN.read_text(encoding="utf-8"))
    body = _method_body(code, "private void OnIntoWorldEvent(")
    assert "_worldEntrySeq" in body, (
        "OnIntoWorldEvent 没有打 _worldEntrySeq —— 重复派发与否无法从日志判断"
    )
    assert re.search(r"P\([^;]*_worldEntrySeq", body) or "第 " in body, (
        "OnIntoWorldEvent 里没有把进入次数写进日志"
    )
