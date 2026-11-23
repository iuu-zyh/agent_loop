# 2026-11-23 原生剧情窗第二句两侧立绘/名签变成同一人

## 症状

原生 `UIDramaDialogue` 连续两页（23205 → 23206，配表都是 `npcLeft=0 / npcRight=0`）：
第一页正常（左=汪刚、右=经飘），第二页两侧都画成左位那个人，名签也都写「汪刚」。

## 真机证据（Player.log 14:18:15）

- 23205：`左=汪刚[NPC] 右=经飘[NPC]`；`名签 左="汪刚" 右="经飘"`；立绘对象 `汪刚(x=-3,亮1.0) / 经飘(x=+3,亮0.3)`。
- 23206：`左=汪刚[NPC] 右=经飘[NPC]`；`名签 左="汪刚" 右="汪刚"`。
- 10-21 那版「`unitRightID=11` 顶替」的 `立绘位ID归零` 行**没有出现** ⇒ InitData 之前两个 ID 都是 0。
- 23206 时旧的 `SanitizePortraitSlots` 命中（右名签=汪刚 ≠ unitRight 名），归零 ID 后调 `UpdateUI()`
  **没有任何变化** ⇒ 脏值不在 `unitLeftID/unitRightID`，而在 `id==0` 的解析里（或被别的字段带进去）。

## 修复

把改写点挪到**立绘位解析点**：`UIDramaDialogue.UpdateUnit(..., int id, bool isLeft)` 的第二个 Harmony Prefix
（`TeamContainerInject.DramaUpdateUnitSlotPrefix`；190 那条 `DramaUpdateUnitPrefix` 原样保留、单独挂载）。

- 判据只看**这次调用拿到的 `id`**：非 0（1=玩家 / 11=unitLeft / 12=unitRight…）说明配表写死了这一侧，一列不动；
  只有 `id==0`（运行时取人）才按侧别改成 11 / 12。
- 侧别优先按「这次真正要刷的名签」判定（实参 `Text __4` 与 `textLeftName/textRightName` 比指针），`isLeft` 只作兜底。
- `unitLeft/unitRight` 任一为空、或两侧是同一个人 → 不动（单人页/独白页/190）。

### 为什么「右侧这次调用一定是 id==0」

同一页配表 id 是静态的，而 23205 正常、23206 出错 ⇒ 差异只能来自 `id==0` 分支里读的可变字段
（`DramaData` 的立绘位 ID）。所以**至少右侧这次调用是 id==0**；若 `npcRight` 是 11/12/1 则两页必然一样。
左侧无论是 0（改成 11）还是 11（不动），结果都是 `unitLeft`=汪刚。⇒ 这条判据不依赖配表具体值。

### 为什么名签会跟着立绘一起恢复

两页之间 `data.unitRight` 始终是经飘，而右名签从「经飘」变成「汪刚」 ⇒ 名签文本来自**这一位解析出来的单位**，
不是静态的 `unitRightName` 字段。解析对了，名签就对了。

## 验证状态

**已真机验证（2026-11-23 14:59 那一轮）**

启动行：

```
[TeamInject] …190 立绘左位改写=已挂(Prefix);0/0 立绘位=已挂(Prefix);★容器直塞=已挂★…
```

每页两行（23205、23206 各一遍，UID 是两个不同的人）：

```
[DramaAiOption] 立绘位强制 page=23205 左 id 0→11（名签左；FiDLuZ / kTuQhr）
[DramaAiOption] 立绘位强制 page=23205 右 id 0→12（名签右；FiDLuZ / kTuQhr）
[DramaAiOption] 立绘位强制 page=23206 左 id 0→11（名签左；FiDLuZ / kTuQhr）
[DramaAiOption] 立绘位强制 page=23206 右 id 0→12（名签右；FiDLuZ / kTuQhr）
```

两页的说话人判定：`名签 左="濮阳冷亦"[活] 右="计天明"[活]` —— **第二页也恢复成两个人**；
旧的 `立绘位纠偏` 一行都没有出现。

- 编译 0 错误；`tests/test_drama_slots_contract.py` 3/3；三处 DLL md5 一致 `3E2CDB3F336BE647A6E0B982565082F1`。
