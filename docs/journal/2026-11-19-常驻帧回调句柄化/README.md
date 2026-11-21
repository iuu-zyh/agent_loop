# 2026-11-19 常驻帧回调句柄化

## 一句话

把"全进程一次的布尔"换成**句柄表**，让帧回调在世界重载后能自愈重挂。

## 结论（现在的状态）

- 8 个常驻 tick 全部经 `FrameTicker.Ensure(键, 名, 体)`，`Destroy` 时 `StopAll`
- 每帧只跑一份（`Once` 按帧号去重）
- `Describe()` 的 `idle` 分三态：`3`（正常）/ `~3`（刚挂上）/ `!90`（挂了没跑 = 真故障）
- 事件订阅补上 `Off`：`SaveData` / `IntoWorld` / `WorldAddDay` 三个都对称了

## 关键证据

真机 `Player.log`（20:25，5 段世界），体检表里 `n` 与 `帧=+N` 的比值：

| 键 | 会话1 | 会话2 | 会话3 | 会话4 | 会话5 |
|---|---|---|---|---|---|
| `deferq`（走 FrameTicker） | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| `battleflow`（走 FrameTicker） | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| `npcmon`（裸注册） | 1.00 | 1.00 | **2.00** | **2.00** | **2.00** |
| `hotkeys`（裸注册） | 1.00 | 1.00 | **2.00** | **2.00** | **2.00** |

裸注册的从第 3 段世界起恒为 2.00 次/帧，再也没回落。

另一条线索：`mapbtn` 只在第 1、2 段世界出现，**第 3 段起彻底消失**
（`MapMainContactButton._ticking` 全文件无复位路径）。

## 详情

见同目录的 `详文-排查与修法.md`（过程记录：走错的路、全仓扫描结论、owner 那个坑）。
相关契约测试：`tests/test_frame_tick_contract.py`、`tests/test_resident_tick_contract.py`、
`tests/test_game_event_subscription_contract.py`。
