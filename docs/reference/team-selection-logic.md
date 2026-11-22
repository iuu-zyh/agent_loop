# 组队筛人逻辑 —— 反汇编定案（2026-10-19 16:1x）

> 查表用。完整定案过程在
> [`../archive/`](../archive)。

> ⚠ **已归档（2026-10-29）**。一次性调查/记录，保留备查，日常开发不用看。
> 本文只回答两个问题：
> **① 筛人逻辑到底是「每人一段」还是「所有条件对应一个人」？**
> **② 之前「一个气运出 3 人但重复」的配置，重复能不能从机制上消掉？**
>
> 全部结论来自 raw 反汇编（`GameAssembly.dll`）+ Cpp2IL 字段布局，逐条给 VA。

---

## 零、一句话结论

**`condition1..5` 是 5 个「并列的槽位」，每个槽位对「同一个打乱过的候选池」独立做一次 `Where` 过滤，
每个槽位取 `[0]` 出 1 个人。它们之间是「并集」，不是「交集」，也不是「级联」。**

⇒ 你要的「每个槽位指定一个人」在结构上是成立的（一槽一人），
**但当前 3 个气运版本出 0 人的原因不在这里** —— 是气运没进 `allLuck`（见第四节）。

---

## 一、筛人主链（实证链，全部反汇编）

```
ConfNpcCondition::GetUnits(int id, NpcConditionData)   @0x1859937F0
  └→ ConfNpcCondition::GetUnitsInID                    @0x185991CB0   ← 选人主函数
```

### 1.1 候选池（0x185992717 – 0x185992971）

```
0x185992717  new List<WorldUnitBase>()            → [r13+0x38]   ← 最终结果列表
0x18599275e  cmp dword [rax+0x18], 1              ; area.Length
0x185992768  cmp dword [rax+0x20], -1             ; area[0] == -1 ?
             ├─ 是 → 0x185992772: WorldMgr::GetUnit(0xB8)→[+0x28] ...
             │         → 0x18376C280 WorldUnitMgr::GetUnitsInArea(this, areaId, flag)
             └─ 否 → 0x18599284c …
0x185992913  → 0x18376C4B0 WorldUnitMgr::GetUnits(this, flag)      ← 全图
0x185992961  mov [r13+0x38], rax                  ; 结果列表 = 候选池
```

**⇒ 候选池 = 按 `area` 圈定的单位集合；我方 `area=0` 时走 `GetUnits(全图)`。**

### 1.2 打乱（0x185992DCD）

```
0x185992dcd  mov rbx, [r13+0x38]                  ; 候选池
0x185992dfb  call 0x18338F780                     ; ★ CommonTool::SortRandom ★
```

**⇒ 候选池被随机打乱一次（就一次），之后所有槽位共用这个顺序。**

### 1.3 ★五个槽位（0x185992E00 – 0x185993000）★

每个槽位都是同一套三段式代码，只换字段偏移：

| 槽 | condition 字段 | ABC 字段 | 反汇编 |
|---|---|---|---|
| 1 | `+0x38` | `+0x60` | `mov rdi,[rbx+0x38]` / `mov rbx,[rbx+0x60]` @0x185992E3E/0x185992E42 |
| 2 | `+0x40` | `+0x68` | `mov rdi,[rbx+0x40]` / `mov rbx,[rbx+0x68]` @0x185992E99/0x185992E9D |
| 3 | `+0x48` | `+0x70` | `mov rdi,[rbx+0x48]` / `mov rbx,[rbx+0x70]` @0x185992EF4/0x185992EF8 |
| 4 | `+0x50` | `+0x78` | `mov rdi,[rbx+0x50]` / `mov rbx,[rbx+0x78]` @0x185992F4F/0x185992F53 |
| 5 | `+0x58` | `+0x80` | `mov rdi,[rbx+0x58]` / `mov rbx,[rbx+0x80]` @0x185992FAA/0x185992FAE |

（`+0x38..+0x58` = `condition1..5`，`+0x60..+0x80` = `condition1ABC..5ABC`，
来自 Cpp2IL `ConfNpcConditionItem` 的 `[FieldOffset]`，见 `luck2.txt`）

每个槽位执行：

```
0x185992e46  new WhereObservable-ish object
0x185992e57  r9 = <静态闭包/委托>            ; 过滤谓词
0x185992e5e  r8 = rbx (= conditionNABC 串)
0x185992e61  rdx = rdi (= conditionN 串)
0x185992e67  call 0x18407C440               ; ★ Where::.ctor(source, condStr, abcStr, cb) ★
0x185992e75  r8 = <元素类型>
0x185992e84  call 0x185F27700               ; List::Add(槽位列表, where)
```

**⇒ 5 个槽位 = 5 个 `Where(候选池, conditionN, conditionNABC)`，各自独立，
全部挂在同一个 `SortRandom` 过的候选池上。**

### 1.4 每个槽位只取 1 个人

```
0x185992dcd ... 0x185992db1  jmp  →  取 [0]
```
槽位求值走 `b__8` @0x182BDD340（`ConfNpcCondition.<>c__DisplayClass10_0::<GetUnitsInID>b__8`），
内部 `ReturnAction4.Invoke → UnitCondition::Condition` 逐单位判定，
命中第一个即结束 ⇒ **每槽产出 ≤1 人**。

### 1.5 组装

```
0x1859931c8  rdx = 临时列表(收集器)
0x1859931cb  rcx = rdi
0x1859931ce  call 0x185F2B5D0   ; ★ List::RemoveAll(收集器, 谓词) ★
             ;   谓词 b__10_9: test rdx,rdx; sete al; ret   → 只删 null，不去重
0x1859931df  cmp dword [rax+0x88], 1     ; ★ save ★
0x1859931e6  jne 0x185993332             ; save != 1 → 直接返回收集器
0x185993220  ┌ 循环 i = 0..收集器.Count
0x185993235  │  mov rax, [rdi+0x10]
0x185993239  │  mov rax, [rax + rcx*8 + 0x20]   ; 收集器[i] = WorldUnitLuckBase
0x185993247  │  mov rax, [rax + 0x18]           ; +0x18 = luckConf
0x185993254  │  mov rdx, [rax + 0x10]           ; luckConf.id
0x185993278  │  call 0x185F27700                ; List::Add(结果列表, id)
0x185993281  └  jmp 0x185993220
0x185993332  ... 正常返回
```

**⇒ 组装语义：**
- `save=0`（我方当前）：结果列表 = 收集器 = **5 个槽位各 ≤1 人拼起来的并集**
- `save=1`：额外把每个人的 `luckConf.id` 写成一份 JSON 存进 `DataObjectData::SetString`（0x183A87440）

**⇒ 只删 null，不去重。槽位之间零交互。**

### 1.6 ★为什么 `_0/_1/_2` 会抽到重复的人（重要）★

`addBattleNPCData_<组>_0` / `_1` / `_2` 是 **3 条独立命令**，每条都完整重跑一次
`ConfNpcCondition::GetUnits(int组id, null)`（见 `BattleFunction::AddBattleNPCData` @0x1828FDEE0）。

而 `SortRandom` @0x185992DFB 在 `GetUnitsInID` **内部**，所以：

```
第1次 GetUnits → SortRandom 洗一次 → 槽1 取 [0] → 得 方成和
第2次 GetUnits → SortRandom 再洗 → 槽1 取 [0] → 得 温芸
第3次 GetUnits → SortRandom 再洗 → 槽1 取 [0] → 得 方成和   ← 重复！
```

**⇒ 三段相同 = 有放回抽样，重复概率就是 E11 算的 48%（5 人池抽 3 次互不相同 = 5·4·3/5³）。
E11 的日志证据 `[0]方成和 [1]温芸 [2]方成和` 与这个模型完全吻合，无需另找解释。**

**⇒ 这也是「三段不同」方案能结构防重的真正原因**：
第 N 次 `GetUnits` 里，槽 N 的条件是 `feature_1_<N号位气运>`，池里只有一个人有它，
洗牌怎么洗都只有那一个 ⇒ 不可能重复。

---

## 二、回答「每人一段」还是「所有条件对应一个人」

| 说法 | 对不对 | 依据 |
|---|---|---|
| 「所有筛选逻辑对应一个人」（= 交集/级联） | ❌ **错** | 5 个槽位各自 `Where` 同一个池、各自取 `[0]`；没有任何「把上一槽结果当下一槽输入」的代码。级联版在 `GetUnits(string[])` @0x1859934D0，**不在战斗链上** |
| 「每个条件段产出一个候选，拼成 N 个」 | ✅ **对** | 每槽 ≤1 人，N 段 ⇒ ≤N 人 |
| 「段与段之间会互相拖死」 | ❌ **错** | 空槽只是产出 null，被尾部 `RemoveAll(null)` 删掉，不影响其它槽 |

> ⚠ 但有一个**必须知道**的细节：`addBattleNPCData_<组>_N` 的 `N` 是**结果列表下标**，
> 而结果列表是「槽1命中的人, 槽2命中的人, 槽3命中的人」压紧后的列表。
> **如果槽1 空、槽2/3 命中，列表会变成 `[槽2的人, 槽3的人]`**，
> 于是 `_0` 拿到槽2的人、`_1` 拿到槽3的人、`_2` 越界静默跳过。
> ⇒ **人不会错位到别的槽，但「哪个槽的人站哪个位」会滑动。**
> 对「只想让 3 个队友都在场」这个需求无影响；对「1号位必须站1号位的人」有影响。

### 2.1 为什么「三段不同」在当前实现下确实会出人（不是 0）

- 槽1 `feature_1_MID&51` → 池里找挂 51 的人 → 命中则取该人
- 槽2 `feature_1_MID&55` → **同一个池**里找挂 55 的人 → 命中则取该人
- 槽3 `feature_1_MID&56` → 同上

三者互不影响。**理论上 3 段异气运 = 3 个不同的人，结构上不可能重复。**
这是设计正确性上的**最终答案：三段不同的思路是对的，不是它导致 0 人。**

### 2.2 那 0 人到底为什么

见第四节 —— **气运没进 `allLuck`**，池子里根本没有「挂 55/56 的人」，于是 3 个槽全空。

> ⚠ 注意：如果只有槽1 命中、槽2/3 空，`addBattleNPCData_<组>_1` / `_2` 会 `CommonTool::Index`
> 越界 → 静默跳过（`0x18338C9B0` 三重防护），**不报错**。
> 所以「出 1 人」和「出 0 人」在日志上都可能表现为「人不对/没人」，必须靠探针分辨。

---

## 三、方案一：回到「一个气运」+ 消掉重复 —— 能不能行

### 3.1 机制上「重复」的唯一来源（已定案）

**一次 `GetUnitsInID` 调用内，5 个槽位共用同一次 `SortRandom` 的结果 ⇒ 三段相同必得同一人。**

但 `addBattleNPCData_<组>_0 / _1 / _2` 是 **3 次独立的 `GetUnits` 调用**，
每次进 `GetUnitsInID` 都**重新** `SortRandom`（@0x185992DFB 在函数体内）：

```
第1次 GetUnits → 洗牌 → 槽1 取 [0] → 方成和
第2次 GetUnits → 洗牌 → 槽1 取 [0] → 温芸
第3次 GetUnits → 洗牌 → 槽1 取 [0] → 方成和    ← 重复
```

**⇒ 三段相同 = 有放回抽样。重复概率 = 5·4·3/5³ = 48%（E11 实测吻合）。**

> **自我更正**：我一度以为「一次洗牌 + 3 个槽位」⇒ 必然重复、48% 是错的。
> 核对 `BattleFunction::AddBattleNPCData` @0x1828FDEE0 后确认：
> 每个 `|` 分隔的 token 都**完整重跑一次** `GetUnits`，
> 所以 E11 的 48% 模型**是对的**，历史文档不用改。

### 3.2 方案一可行路径（按代价排序）

| # | 做法 | 能不能去重 | 代价/风险 |
|---|---|---|---|
| **A** | 单段 `condition1` + `save=1` + `npcConditionSave_<组>_N` | ✅ 天然 1 人 | 只能出 1 人；`save=1` 会写存档复用 |
| **B** | 3 段不同气运（现方案） | ✅ 结构不可能重复 | **必须先修好气运进 `allLuck`**（第四节） |
| **C** | 3 段相同 + C# 侧 `PurgeSlotExcept` 事后收敛 | ⚠️ 只治标 | 引擎先塞重复的人进 `npcUnits`，C# 再踢；战斗已开始，会露馅 |
| **D** | 用 `condition2ABC` 的「已选取单位」语义做互斥 | ❓ 未验证 | 官方表头：`0`=列表循环的当前单位，`1~5`=已选取的单位，`-1`=传入单位1（玩家），`11~15`=传入单位。**理论上 `condition2ABC=1` 可以把「条件1 已选出的人」当上下文**，但那是「拿他当 A/B/C 去判条件」，**不是「排除他」**。官方 427 行里三段全不同 = 0 行，无先例 |
| **E** | 换 `featureGroup_<type>_<group>`（`UnitCondition::FeatureGroup` @0x1825900E0） | ❓ | 官方**零使用**，风险未知 |

### 3.3 我的判断

**方案一（一个气运）在机制上无法优雅去重。** 引擎里没有任何「排除已选」的语义：
- `RemoveAll` 只删 null，不去重（0x1859931CE）
- 5 个槽位互不通信（0x185992E00–0x185993000）
- 官方 427 行里三段全不同 = 0 行、两段相同 = 4 行 ⇒ **官方自己也从没用多段做过「选多个人」**

**⇒ 方案二（3 段异气运）方向是对的，不要回退。**
真正要做的是**把气运以正确形态挂进 `allLuck`**（第四节），而不是改回一个气运。

### 3.4 但更该考虑：干脆绕开条件组

既然 `addBattleNPCData` 的全部作用只是
「`GetUnits(组id)` → `CommonTool::Index(list, N)` → `npcUnits.Add(unit)`」，
而我们**已经知道自己要哪 3 个人**（就是队伍里那 3 个 `WorldUnitBase`），
那完全可以**跳过条件组**，直接往容器里塞：

```csharp
var nu = g.world.battle.data.npcUnits;      // 容器 +0xB0
nu.Clear();                                 // 对齐 clearBattleNPCDatas_0
foreach (var u in TeamTool.ListMates())     // 已知的 3 个队友
    if (u != null) nu.Add(u);
// 然后照旧让 addBattleUnit_3/4/5 从容器按位取人
```

**优点**
- 不依赖气运、不依赖 `feature_`、不依赖 `GetLuck`、不依赖 `SortRandom` —— **彻底绕开第四节那个坑**
- 顺序 100% 确定：容器 `[0]/[1]/[2]` = 1/2/3 号位，不会「槽空导致滑动」
- 不可能重复（`List.Add` 前自己判重即可）

**要验证的两点**
1. `npcUnits` 的**写入时机** —— 必须在触发器 `tt=2` 跑完之后、`addBattleUnit_3/4/5` 之前
   （最稳的做法：把 `DungeonTrigger MID&001` 的 `battleFunction` 里的
   `addBattleNPCData_*` 三条**删掉**，只留 `addBattleUnit_3/4/5`，容器由 C# 填）
2. `battle.data` 在 `SceneBattle` 建好之前是否为 null ⇒ 需要挂 `SceneBattle::Init`
   或 `WorldBattleMgr::IntoBattleInit` 的 postfix

mod 里已经有 Harmony（`_harmony.PatchAll` @`ModMain.cs:127`），
`csharp/drama/BattleEndHook.cs` 就是手动挂 hook 的先例 ⇒ **这条路技术上没有障碍。**

> **建议顺序**：先按 5.3 的探针确认根因；若气运那条路仍然不稳，
> **直接上 3.4 的容器直塞**，比继续和条件组搏斗划算得多。

---

## 四、★真正的 0 人根因：`GetLuck` 的 null 守卫会**抛异常**，而 `NewCreateLuck` **补不了** `luckData`★

### 4.1 字段布局（Cpp2IL `[FieldOffset]`，`luck2.txt`）

```csharp
public class WorldUnitLuckBase {
    +0x10  WorldUnitBase unit;
    +0x18  ConfRoleCreateFeatureItem luckConf;
    +0x20  DataUnit.LuckData luckData;      // ★ GetLuck 读的是它 ★
    +0x28  Action onDieCall;
    +0x30  List<WorldUnitEffectBase> effects;
}

public class WorldUnitLuckDyn {             // ★ 注意：不继承 WorldUnitLuckBase ★
    +0x10  WorldUnitBase unit;
}
```

> **勘误**：`WorldUnitLuckDyn` 只有 `+0x10 unit` 一个字段，**它不是 `WorldUnitLuckBase` 的子类**。
> `allLuck` 的元素类型是 `WorldUnitLuckBase`。

### 4.2 `WorldUnitBase::GetLuck(int id)` @0x1832BC4C0（全 82 条）

```
0x1832bc4f7  mov rcx, [rsi+0x40]          ; allLuck
0x1832bc505  test rcx,rcx ; je 0x1832bc584  ★THROW
0x1832bc511  cmp eax, [rcx+0x18]          ; 循环 i < allLuck.Count
0x1832bc52e  mov rax, [rax + rcx*8 + 0x20]  ; allLuck[i]
0x1832bc533  test rax,rax ; je 0x1832bc584  ★THROW   ← 元素为 null 就抛
0x1832bc538  mov rax, [rax + 0x20]        ; ★ luckData (+0x20) ★
0x1832bc53c  test rax,rax ; je 0x1832bc584  ★THROW   ← luckData 为 null 就抛
0x1832bc545  cmp dword [rax+0x10], ebp    ; luckData.id == 目标 id ?
0x1832bc548  je  0x1832bc558              ; 命中 → 返回 allLuck[i]
0x1832bc580  xor eax,eax                  ; 遍历完没找到 → 返回 null
```

**⇒ 只要 `allLuck` 里存在一枚 `luckData == null` 的元素，`GetLuck` 就会抛异常（不是返回 false）。**

而 `UnitCondition::Feature` @0x182590500 是这样调的：

```
0x18259051b  mov rcx, [rbx+0x28]      ; args[1] = type
0x182590521  call 0x184FC4E00         ; Int32.Parse
0x18259052e  call 0x1825931A0         ; GetUnitInType(type) → 候选单位
0x182590533  test rax,rax ; je → 返回 false
0x182590547  call 0x184FC4E00         ; Int32.Parse(args[2] = luckId)
0x182590554  call 0x1832BC4C0         ; ★ GetLuck(luckId) ★  ← 没有 try/catch
0x182590559  test rax,rax ; setne al  ; 返回 != null
```

**⇒ 异常一路冒到 `ConfNpcCondition::GetUnitsInID`，那里 `je 0x18599341F` = `call 0x180279A90`（throw stub），
没有任何 catch。整条 `GetUnits` 抛异常 ⇒ `addBattleNPCData` 拿不到列表 ⇒ 静默不加人 ⇒ 副本 0 人。**

### 4.3 `WorldUnitLuckBase::Init` @0x183769280 会写 `luckData`（+0x20）

```
0x18376939d  mov [r12+0x10], rdi          ; 参数2(luckData) → 局部对象 +0x10
0x1837693b3  mov [rcx], r15               ; 参数1(unit)    → this+0x10
0x1837693c0  lea rcx, [r13+0x20]
0x1837693c4  mov [rcx], rdx               ; ★ rdx = [r12+0x10] = luckData → this+0x20 ★
0x183769412  call 0x183C39290             ; ConfRoleCreateFeature::GetItem(luckData.id)
0x18376941d  mov [r13+0x18], rax          ; this+0x18 = luckConf
```

**⇒ `Init` 一定会填 `this+0x20 = luckData`。**

### 4.4 `WorldUnitBase::CreateLuck` @0x1832BAC80 只调 `Init` + `Create`

```
0x1832bacee  call 0x1812F2A90             ; WorldUnitLuckDyn::.ctor
0x1832bad17  mov ecx, [rbp+0x10]          ; luckData.id
0x1832bad1c  call 0x18280A530             ; WorldFactory::CreateLuckInID(id)
0x1832bad24  mov [rbx+0x18], rax          ; 临时对象 +0x18 = 工厂产物
0x1832bad49  call 0x185F27700             ; allLuck.Add(临时对象)   ← 先加进列表
0x1832bad7b  mov r10, [r14]               ; r14 = 工厂产物
0x1832bad96  call qword ptr [r10+0x178]   ; Create()
0x1832badb0  call qword ptr [r8+0x188]    ; ★ Init(this, luckData, null) ★
0x1832badb7  mov rax, [rbx+0x18]          ; 返回工厂产物
```

**⇒ 如果 `CreateLuckInID` 返回 null，`Init` 不会被调，`+0x20` 永远是 null ⇒ `GetLuck` 必抛。**

### 4.5 `WorldUnitLuckBase::NewCreateLuck` @0x1837696A0 **补不了** `luckData`（勘误）

```
0x1837696e9  mov rax, [rsi+0x20]          ; ★ this.luckData ★
0x1837696f7  test rax,rax ; je 0x1837697bf  ★THROW（luckData 为 null 就抛）
0x183769700  mov rcx, [rax+0x20]          ; luckData.某个子对象
0x183769717  call 0x183A86CB0
0x183769731  cmp dword [rax+0x18], 0      ; 条件
0x183769751  call 0x183A873D0             ; 造 Action
0x183769756  mov rax, [rsi+0x30]          ; this.effects
0x183769797  call qword ptr [rax+0x1A8]   ; 逐 effect 执行
```

**⇒ `NewCreateLuck` 的**第一件事**就是读 `this+0x20`，null 直接抛。
它不能用来「补」`luckData`；它只是「把气运效果真正生效」。**

### 4.6 根因链（定案）

```
① TeamTool.AddLuck 直塞 propertyData.addLuck（存档层）
      → 本局 allLuck 里没有该气运 ⇒ GetLuck 找不到 ⇒ 0 人
② 或者 CreateLuck(data) 把元素塞进了 allLuck，
   但 CreateLuckInID 对 type=2 返回 null ⇒ Init 没跑 ⇒ +0x20 luckData = null
      → GetLuck 遍历到它就抛异常 ⇒ 整个 GetUnits 抛 ⇒ 0 人
③ 换局/读档时 WorldMgr::EnterWorldFixData 走原生 Init 序列
      ⇒ luckData 正确填好 ⇒ 这才有 10-18 晚的成功
```

**⇒ 「10-18 成功、10-19 失败」的真正变量不是「三段相同/不同」，
而是「这枚气运到底有没有以正确形态进入本局的 `allLuck`」。**

---

## 五、怎么一次性拿到全部数据（不再一条条反汇编）

### 5.1 ★ 已有现成的全符号表 ★

```
F:\_dt_dump\rvamap_all.txt      5.4 MB，13 万+ 条  "0x<RVA>\t<命名空间>::<方法名>"
F:\_dt_dump\rvamap.txt          3.6 MB，精简版
```

用法（本会话已验证）：

```python
import sys; sys.path.insert(0,'/home/zyh/re')
from sym import name
print(name(0x1859937F0))   # → ConfNpcCondition::GetUnits
```

`/home/zyh/re/` 下已建好工具链（全部可复用）：

| 文件 | 作用 |
|---|---|
| `pe.py` | PE 解析 + capstone 反汇编（`pe.dis(rva, n)`） |
| `sym.py` | RVA → 方法名（读 `rvamap_all.txt`，自动取最近前驱符号） |
| `full.py` | **一条命令出带符号注释的反汇编**：`python3 full.py 0x5991CB0 0x400 out.txt` |
| `dump.py` / `annot.py` / `dis1.py` | 早期版本，功能被 `full.py` 覆盖 |

### 5.2 ★ 现成的 IL 反编译器（拿字段布局/签名/类关系）★

```
F:\_dt_dump\dt.csproj + Prog.cs
cd /mnt/f/_dt_dump && "/mnt/c/Program Files/dotnet/dotnet.exe" run --project 'F:\_dt_dump\dt.csproj' -c Release
```

改 `Prog.cs` 的 `names` 数组即可。产物：`F:\_dt_dump\<你指定的>.txt`。
**字段偏移、类继承、方法签名、RVA 一次全出**（方法体是桩，但**布局是准的**）。
本会话用它拿到了 `WorldUnitLuckBase` / `WorldUnitLuckDyn` / `ConfNpcCondition` / `UnitCondition` /
`ConfNpcConditionItem` 的完整布局 —— **这一步替代了 90% 的盲扫反汇编**。

### 5.3 ★ 最快的路：直接 C# 运行时探针（不用反汇编）★

mod 本身就跑在引擎里。要问「条件组能选出几个人」，**直接调引擎**即可，
比反汇编快两个数量级。已有探针 `csharp/app/TeamMultiProbe.cs` 里的 `DoGetUnits` 就是这个思路。

**推荐的定案探针（一次跑完，全部答案）**：

```csharp
// ① 条件组直接求值 —— 引擎真值
var list = g.conf.npcCondition.GetUnits(condId, null);
Log($"GetUnits({condId}) = {list?.Count}");
for (int i = 0; i < list.Count; i++) Log($"  [{i}] {list[i].GetName()}");

// ② 逐段单独求值 —— 分辨「哪一段空」
foreach (var seg in new[]{"feature_1_<51>","feature_1_<55>","feature_1_<56>"}) {
    // 临时把该段写进一个空条件组再求值，或用 UnitCondition 直接判
}

// ③ 每个候选人的 allLuck 逐枚 dump —— 分辨「luckData 是否 null」
foreach (var u in g.world.worldUnitMgr.allUnit) {
    var all = u.allLuck;
    for (int i = 0; i < all.Count; i++) {
        var lk = all[i];
        Log($"{u.GetName()} luck[{i}] obj={(lk==null?"null":"ok")} " +
            $"id={(lk?.luckData?.id.ToString() ?? "<null>")}");
    }
}

// ④ 对每个队员单独调 GetLuck —— 会抛就说明有 null 元素
try { var r = u.GetLuck(luckId); Log($"GetLuck={r?.luckData?.id}"); }
catch (Exception e) { Log($"★ GetLuck 抛异常：{e.GetType().Name} {e.Message}"); }
```

**④ 是判定根因的一锤定音**：抛异常 = 命中 4.6 的路径②；返回 null 且 `allLuck` 里没有 = 路径①。

### 5.4 官方配置表（只读，最好的对照源）

```
GAME_ROOT/Mod/modFQA/配置修改教程/配置（只读）Json格式/*.json     ← 1310+ 张表
GAME_ROOT/Mod/modFQA/配置修改教程/配置表头/*.xlsx                 ← 表头注释（字段语义）
```

`NpcCondition` / `DungeonTrigger` 的官方全量形状统计直接在这里数，
不用再靠子代理逐行读文档。

---

## 六、下一步（按性价比排序）

1. **先跑 5.3 的探针 ④**（10 分钟，不用反汇编）—— 确定是「气运没进 allLuck」还是「进了但 luckData=null」。
   探针 ④ 会抛异常 = 路径②（有 null 元素）；返回 null 且 allLuck 里没有 = 路径①。
2. 若是路径②：**`NewCreateLuck` 救不了**（它第一件事就是读 `+0x20`，null 直接抛）。
   只能：`WorldFactory.CreateLuckInID(id)` 拿非 null 对象 → 手工调 `Init(unit, luckData, null)`
   → 再 `allLuck.Add`；或者**换局/读档**让 `EnterWorldFixData` 走原生序列。
3. **★推荐先试 3.4（容器直塞）★**：不依赖气运，10 行 C# 就能验证，
   而且能同时解决「重复」「槽空滑动」「气运没挂上」三个问题。
4. 气运那条路修好之后，**三段异气运（方案二）应当直接出 3 个不同的人**，无需回退方案一。

---

## 七、勘误表（推翻历史文档）

| # | 历史说法 | 现在结论 | 依据 |
|---|---|---|---|
| 1 | 「三段相同 ⇒ 48% 概率重复」 | ✅ **对**（我一度误判为 100%，已自我更正）。因为 `_0/_1/_2` 是**三次独立的 `GetUnits` 调用**，每次进 `GetUnitsInID` 都重新 `SortRandom` ⇒ 三次独立洗牌 ⇒ 有放回抽样 | `SortRandom` @0x185992DFB 在 `GetUnitsInID` 内部，每次调用各执行一次；`AddBattleNPCData` 每次 `|` 分隔的 token 都完整重跑一次 `GetUnits` |
| 2 | 「条件段可能级联过滤」 | ❌ 错。5 槽独立 `Where` 同一池 | 0x185992E00–0x185993000 |
| 3 | 「`GetUnitsInID` 空段会抛异常导致 0 人」 | ⚠️ 部分对：`0x18599341F` 确实是 throw stub，但那是 **null 守卫**，不是空段 | 0x185993400 起是 `pop/ret` 正常出口 |
| 4 | 「`WorldUnitLuckDyn` 是 `WorldUnitLuckBase` 子类」 | ❌ 错。独立类，只有 `+0x10 unit` | Cpp2IL `luck2.txt` |
| 5 | 「`NewCreateLuck(0)` 能补 `luckData`」 | ❌ 错。它**读** `+0x20`，null 直接抛 | 0x1837696E9 |
| 6 | 「`Init` 内部 vtable+0x188」 | ✅ 对，但 `Init` 是 `WorldUnitLuckBase::Init` @0x183769280，由 `CreateLuck` @0x1832BADB0 调用 | 0x1832BAD96 / 0x1832BADB0 |
