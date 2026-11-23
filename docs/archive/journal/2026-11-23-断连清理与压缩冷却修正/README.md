> ⚠ **已归档**。现状见 [`docs/features/`](../../../features/) 与 `CHANGELOG.md` 同日条目。
> 本文只保留"怎么发现的、改了什么、验到哪"，追溯结论时再读。
>
> **结局**：断连出口补上 `self.`（11-20 那次修复此前**从未执行过**）；`compaction.cool_down`
> 的 20000.0 按毫秒误写处理，三处默认值统一改成 20.0 秒；两条空转的契约测试改成能真正抓红。
>
> - **起止**：2026-11-23 ～ 2026-11-23
> - **触发**：审阅外部交付包时按附录逐条核对，三条静态疑点全部属实
> - **涉及**：`transport/ws_channel.py`、`tools/unit_refs.py`、`config/config_loader.py`、
>   `compaction/compress.py`、`scripts/server.py`、两条契约测试

# 断连清理与压缩冷却修正

## 一、断连判死：写对了逻辑，却没调用到

`transport/ws_channel.py` 的接收循环 `finally` 里写着：

```python
_drop_pending_on_disconnect()          # ← 少了 self.
log.warning("C# 连接已断开（等待其自动重连；重连周期由 C# 侧决定）")
```

方法定义在同一个类里（`:441`），裸名字在方法外解析不到 ⇒ 每次断连都抛
`NameError: name '_drop_pending_on_disconnect' is not defined`。后果两层：

1. 挂起的 RPC 不被判死 —— 11-20 那次「断连立刻收口」的修复**一次都没生效**，
   每条 RPC 仍要空等到自己的超时（真机 8 次，每次约 90 秒）；
2. 下一行的断连日志也被跳过，现场日志里根本看不到「连接断了」。

为什么测试没抓住：`tests/test_disconnect_contract.py` 当时只做源码字符串 grep
（`"_drop_pending_on_disconnect()" in seg`），缺 `self.` 时这个断言恒为真。
典型「守住了形状、守不住行为」。

**改法**：

- 调用改成 `self._drop_pending_on_disconnect()`；
- 契约断言精确到接收者（`"self._drop_pending_on_disconnect()" in seg`），并把原来那条
  恒真的 `seg.index(...) < len(seg)` 换成「调用必须早于断连日志行」；
- 新增行为用例 `test_drop_pending_marks_futures_dead`：真造一个 `WsServer`，往
  `_pending` 塞一个 Future，调清理，断言它变 `done`、`result()` 抛 `ConnectionError`、
  且从表里摘掉。

**变异验证**：把 `self.` 去掉重跑 → `test_disconnect_drops_pending_rpcs` 红
（报「断连出口没有调用清理（或漏了 self.）」），改回即绿。

## 二、cool_down：毫秒值写进了秒字段

`compaction.cool_down` 三处默认值都是 `20000.0`，而 `Compressor` 的用法是
`time.monotonic() + self.cool_down`（秒）。20000 秒 ≈ 5.5 小时，实际效果是
「一个会话只自动压一次」；日志会打印「冷却 20000s 内不再自动压缩」。

历史里查不到这个值的依据（初始公开版本就是 20000.0），注释写的是「防抖」，
按毫秒误写处理：`config_loader` / `compress` / `server`（含热换路径）统一改成 `20.0` 秒。
压缩成功后上下文压力已降到阈值以下，20 秒足够挡住同一轮里的反复触发。

**测试的空转**：`tests/test_config_hot.py` 有一条断言「没被改的既有参数（cool_down）
不能在重建时丢失」，但它的临时 config.json 里**根本没写** `cool_down`，重建时读到的
是默认值 —— 旧默认 20000.0 恰好与它给旧压缩器塞的 20000.0 相等，于是这条断言一直在
验证「默认值等于默认值」。现在临时 config 显式写 `cool_down: 60.0`，断言改成读回 60.0，
才真正覆盖「热换不丢配置值」。

## 三、顺手补的两处 typing 导入

`transport/ws_channel.py` 用了 `List` 但 import 里没有；`tools/unit_refs.py` 用了 `Set`
但 import 里没有。两处都靠 `from __future__ import annotations` 把注解变成字符串压着，
运行时不会抛，但会让 `typing.get_type_hints()` / 类型检查直接失败。已补齐。

## 四、验证到哪 / 没验证什么

- 定向测试：`tests/test_disconnect_contract.py`、`test_ws_channel.py`、`test_unit_refs.py`、
  `test_compaction_stage3.py`、`test_compaction_fixes.py`、`test_compaction_ctx_safety.py`、
  `test_compaction_edit_rpc.py`、`test_compaction_summary_view.py`、`test_config_loader.py`、
  `test_config_hot.py`、`test_config_rpc.py` → **123 passed**。
- 变异验证：去掉 `self.` → 契约红；改回 → 绿。
- **没验证**：本机没有正在运行的 C# 端，真实断连（关游戏 / 杀 Python）未实机复现；
  压缩冷却只验到单测与配置读取，没有在真机上等到一次「压缩后再次自动压缩」。
