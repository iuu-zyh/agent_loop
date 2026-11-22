# 《鬼谷八荒》NpcCondition「条件组选人」GetUnits/GetLuck 反汇编求证报告

> 查表用。本文件即**原始求证报告**（含完整反汇编片段与置信度评估），由
> `archive/` 下旧的 `getunits-luck-eval-report.md` 迁入本文件并改名。

> ⚠ **已归档（2026-10-29）**。一次性调查/记录，保留备查，日常开发不用看。
- 调查时间：2026-10-19 11:21–11:4x (+08:00)，时间盒内完成（约 22 分钟反汇编 + 写报告）。
- 工具：Cpp2IL-Runner（/mnt/f/.cpp2il_work/runner/bin/Release/net6.0/cpp2ilrunner.exe），GameAssembly.dll = /mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/GameData/GameAssembly.dll，raw 模式跨 truncation 边界。未改任何游戏/工程文件。
- 本文所有地址为 VA；「片段」为 raw 输出摘录；工具对 IL2CPP 标注的 C# 供应商标签（如 XmlAttribute::get_LocalName）可能是宿主类的误标，我只以其调用的参数/返回行为下结论。

## 0. 一图流：5 问速答

| # | 问题 | 一句话结论 | 关键地址 |
|---|------|-----------|----------|
| 1 | 合并语义 | **段级并集 + 组级 AND**：GetUnitsInID 逐段把该 id 的命中整包 `List.AddRange` 合入（并集、不互斥）；但外层组循环中**任一段 GetUnit==null（0 命中）→ 清掉已收集段并提前返回**，不是纯「每段取 1 拼成 N×1」 | 0x185991CB0 段循环 0x1859921B4；组循环 0x1859935F0 |
| 2 | 空段风险 | **选 B（空/0）而非 C（异常）**：命中 0 的段让 GetUnit 返回 null → 组循环触发 ClearSaveUnit 级联后提前返回新数组 → 剩余段不再解析、无异常。c3=395 无人挂即触发此路径，今日 0 人与此一致 | 0x1859936AD–0x185993782 级联 |
| 3 | feature_1 解析 | 条件串按 args 数组取：`args[1]`/`args[2]` 各自 `Int32.Parse`；GetLuck 的 key 是 **allLuck 元素内含 luck 数据对象的 int id 字段（值相等比较）**，与 conf 行引用无关 | 0x182590500、0x1832BC4C0 |
| 4 | 同段多对象 | 段内命中通过 `List.AddRange(GetSaveUnit(...))` 整包并入 → **一段命中多个则全部在列**；10-18「3 同段」出 3 人更贴合"每段并入各自命中"。段后去重器是否存在未完全反汇编 → 见 Q4 未确认项 | 0x18599229B AddRange |
| 5 | 进运时表 | **RoleCreateFeature conf 表缺行 → CreateLuck(x) 直接抛**（CreateLuckInID 对 GetItem==null 走 throw 路径），该 luck 根本进不了 allLuck——前提矛盾；反之 luck 对象真在 allLuck 里则 GetLuck(x) 与 conf 表无关能命中。**addLuck (+0x120) 与 allLuck (+0x40) 是两个互不相通的列表**，addLuck 挂载成功 ≠ allLuck 可查 | 0x18280A530、0x1832BAC80、0x1832BC4C0 |

---

## 1.【问题一 · 合并语义】GetUnitsInID：并集 or N×1 or AND

### 结论
GetUnitsInID @0x185991CB0 的合并语义是 **"逐段整包 AddRange（并集累加）"**，且外层组循环（ConfNpcCondition::GetUnits(string[] segments, NpcConditionData) @0x1859934D0）施加 **"任一段 0 命中则整组提前作废"的 AND 网闸**。既不是"每段各自只产生 1 个单元再拼 N×1"，也不是单独成立的纯 OR/纯 AND。

### 证据
调用链（实测手工反汇编）：
```
组入口 ConfNpcCondition::GetUnits(string[], data) @0x1859934D0
  → 逐段 ConfNpcCondition::GetUnit(string seg, data, null) @0x185991C20
      → ConfNpcCondition::GetUnits(string, data) @0x1859938C0（路由：含分隔符→0x185993A6B；纯整数→Int32.Parse）
          → ConfNpcCondition::GetUnits(int id, data) @0x1859937F0
              → ConfNpcCondition::GetUnitsInID @0x185991CB0   ← 选人主函数
```
@0x1859920DE 起主体：
- roundMonth 缓存：`cmp ebx,eax`（[rdi+0x50] 缓存 vs `WorldRunMgr::get_roundMonth` @0x182A27410）不一致则重算并 `ArrayList::Clear` TheList（rdi+0x48，@0x1844E29A0）。
- `List`1::.ctor` 建结果列表，先 `List`1::AddRange`（@0x184E37FB0）并入前置源（[rsp+0x50]：条件数据或 TheList）。
- **段循环 @0x1859921B4–0x1859922CD**：遍历 `[rdi+0x40]`（段的 Items 数组 `[r11+0xB0]`，计数 `[r11+0x12A]`），每段取 id 后调 `ConfNpcCondition::GetSaveUnit @0x185991880`，结果非空则再次 `List`1::AddRange` 整包并入结果列表：
```
call 0000000185991880h   ; ConfNpcCondition::GetSaveUnit
test rax,rax
je near 000000018599341Fh          ; 段解出 null → 共享 return-null epilogue
...mov r8,[rel 186F57F90h]
mov rdx,rax
mov rcx,r12
call 0000000184E37FB0h   ; List`1::AddRange   ← 整段命中列表合入
inc r15d ...
```
- 组层网闸 @0x1859935F0（GetUnits(string[])）逐段 `GetUnit`，`test rax,rax; je short 0x1859936B6` 命中即收集（`List<string>::Add` 段串 + 字典记录），但 null → 0x1859936B6 进入"级联清理"（见问题二）。

### 反汇编片段（GetUnitsInID 段循环，VA 0x1859921B4 起，raw 摘录）
```
mov r12,[r13+18h]                  ; 结果列表 wrapper
mov r9,[rel 186C52C60h]
mov r11,[rbx]                      ; [rbx]=[rdi+40h] 段列表对象
movzx r8d,word [r11+12Ah]
cmp r14w,r8w
jae short 0000000185992255h
movzx ecx,r14w
mov r10,[r11+0B0h]
...loop:
  movzx edx,cx
  add rdx,rdx
  cmp [r10+rdx*8],r9
  je short 00000001859922B9h
  inc cx; cmp cx,r8w
  jb short 0000000185992240h
  ...call 0000000180233FA0h / 00190002190h   ; 取段元素（索引器虚拟调用）
  ...call 0000000185991880h   ; GetSaveUnit(this, 段id, false)
  test rax,rax; je near 000000018599341Fh
  mov r8,[rel 186F57F90h]; mov rdx,rax; mov rcx,r12
  call 0000000184E37FB0h   ; List`1::AddRange
  inc r15d; jmp 00000001859921F0h
```

### 置信度
- "逐段 AddRange" init：**高**（AddRange 调用清晰、循环体完整）。
- "每段命中可为多个"：**中高**（GetSaveUnit 返回值做 AddRange 的 rdx，且 Q4 佐证），但 GetSaveUnit 内部完整语义未走完 → 部分留档。

### 未确认项
- GetSaveUnit @0x185991880 的返回列表生成细节（其中含 get_conf→ConfMgr::get_npcCondition→vtable 调用与 XmlElement/Int32 后处理）未完整反汇编；c 段 id 与运行时 unit 的精确匹配点（可能在 b__5/b__6/b__7 订阅过滤器）未闭环。
- GetUnitsInID 后置过滤块（0x185992717 起，含 roundMonth 年龄过滤 `sub eax,r15d; cmp eax,[rcx+9Ch]`）未逐行还原；不排除该层还有筛选/去重。

---

## 2.【问题二 · 空段风险】c3 无命中时整组返回什么

### 结论
选（B）「空 0」：**组内某段 GetUnit==null（0 命中）时，组循环走"级联清理"块（0x1859936AD–0x185993782）：先把已命中的段逐一 `StrSplitInt('_')` → `ConfNpcCondition::ClearSaveUnit`（@0x1859917E0）清掉，随后跳过剩余所有段、直接走到函数尾的"新数组返回"路径**。全程无异常（无 throw 点介入主路径），因此与"直调 48ms 无异常、结果 0 人"的实测完全吻合。今日 0 人的最可疑解释成立：c3（feature_1_-803158395，无人挂载）为 null 段 → 整组作废。其他段（含 c2=396 的 2 人命中）不会救场。

排除（A）：级联块无一留下命中列表；排除（C）：主路径无 throw 哨兵（throw 全在越界 guard `ThrowHelper::ThrowArgumentOutOfRangeException` @0x18530F2A0 与静态初始化分支，与"段 0 命中"无关）。

### 证据
@0x1859935F0 组循环：
```
mov rcx,[rdi+r15*8+20h]            ; segments[i]
call 0000000185CF6440h   ; String::Equals（与空静态串比较）
test al,al
jne near 00000001859936ADh        ; 段串=="": 也进级联清理
...call 0000000185991C20h   ; ConfNpcCondition::GetUnit(this, seg, data, 0)
mov r14,rax
test rax,rax
je short 00000001859936B6h        ; ← 该段 0 命中：进级联清理
```
@0x1859936B6 级联清理（两条入口 0x1859936AD / 0x1859936B6 汇流）：
```
xor ebx,ebx; xor eax,eax
test rbp,rbp                      ; 已命中段列表（List<string>）
je near 0000000185993793h          ; 空 → 直接到尾
loop（0x1859936C3）:
  cmp eax,[rbp+18h]
  jge short 0000000185993734h      ; 清完 → 出口
  ...call 0000000180002310        ; list.get_Item(i) → 段串
  mov edx,5Fh                     ; '_'
  call 00000001835BDFB0h   ; CommonTool::StrSplitInt(段串, '_', null)
  ...mov edx,[rax+rdx*4+20h]
  call 00000001859917E0h   ; ConfNpcCondition::ClearSaveUnit(this, int, 0)
  inc ebx; jmp…                    ; 内层索引循环
0x185993734 出口:
  mov edx,[rdi+18h]
  call 0000000180279510            ; NEW string[segments.Length]
  ...call 0000000184C19B70
  mov rax,rdi                      ; 返回新数组 → GetUnit 侧行为见未确认项
  ...epilogue ret
```
另：GetUnit @0x185991C20 在 `GetUnits(string)` 结果为 null 或 count==0 时 `xor eax,eax` 返回 null（`cmp dword [rax+18h],0` / jg / `xor eax,eax` 可见），使"空段→组级 null"的传递成立。

### 反汇编片段：ClearSaveUnit @0x1859917E0（供行号复核）
```
call 0000000183A86B90h   ; （vendor误标）XmlElement::get_LocalName
xor edx,edx
lea rcx,[rsp+38h]
mov rbx,rax
call 0000000184FC54D0h   ; Int32::ToString
mov rcx,[rel 186F82420h]
xor r8d,r8d
mov rdx,rax
call 0000000185CF3CD0h   ; String::Concat
...（按拼接出的 id 清存档单元缓存）
```

### 置信度
- 「段 null → 级联清理 + 跳出返回」：**高**（两条汇流路径与 GetUnit 的返回 null 传递链均逐条确认）。
- 「级联后返回值不含任何命中」：**中高**（返回的是新分配数组而非已收集列表；新数组的具体填充内容未逐字节还原 → 见未确认项，不影响"其他段命中被丢弃"的判断）。

### 未确认项
- 0x185993734 出口返回的新数组里是否是"空/段串拷贝"（`184C19B70` 的 dl=0 分支着色未还原）；若为段串拷贝，GetUnit 后续 0x1859938A9 起的取值步骤走的是 `get_Item(0)`（未还原），该分支的实际可观察返回仍标为疑似 0/无 unit。
- 主路径中的 `<GetUnitsInID>b__8 @0x182BDD340` 订阅器（每段结果写入拼装的 5 插槽结构的闭包）与 b__5/b__6/b__7 的完整职责分工未全部还原——若组层网闸还有第二个触发点，应当在其中（概率低，但未排除）。

---

## 3.【问题三 · feature_1 解析】UnitCondition::Feature 与 GetLuck 的匹配 key

### 结论
1. 条件段字符串 `feature_<T>_<id>`（`_` 分隔，args 数组）进入 UnitCondition::Feature @0x182590500：**args[1]、args[2] 各走 `Int32.Parse`**（`-803158400` 可负解析），不含第三段以外的额外 args（与已知"纯 feature 段无附加 args"一致）。
2. `GetUnitInType(this, type=args[1])` @0x1825931A0：type==1 → **+0x28（unitB）**（跳板 0x18259325F），type==0→另一回退、2→+0x48（0x18259324C）、0xA(10)→curRunUnit（0x182593211）；其余 type 走 `ConfMgr::get_npcCondition` 虚表调用（兼容子配置）。
3. `WorldUnitBase::GetLuck @0x1832BC4C0`：遍历 `this+0x40`（allLuck，List），对每个元素做 **int 值相等比较**（`cmp [rax+10h],ebp`，rax=元素+0x20 处指向的 luck 数据对象），命中即 `list.get_Item(i)` 返回对象。
4. **匹配 key 是运行时 luck 对象里存的 int id（值比较），不是 conf 行对象的引用比较**；luckData.id 若与 conf 表 RoleCreateFeature 行 id 不一致：GetLuck 只看运行时对象字段，不看 conf 行 → **条件通过与否取决于 luck 实例上的 id 字段**而与表无关（conf 表的角色在创建/装载链上，见问题五）。

### 证据与反汇编片段（Feature @0x182590500，完整函数体）
```
mov rbx,rdx                        ; args 数组
test rdx,rdx; je short 182590575  ; null args → 返回假
cmp dword [rdx+18h],1
jbe short 000000018259057Bh        ; args.Length<=1 → 假
mov rcx,[rbx+28h]                  ; args[1]
call 0000000184FC4E00h   ; Int32.Parse
...mov rcx,rdi
call 00000001825931A0h   ; UnitCondition::GetUnitInType
mov rdi,rax
test rax,rax
je short 000000018259056Ah         ; 单元不存在 → 返回假
cmp dword [rbx+18h],2
jbe short 000000018259058Bh
mov rcx,[rbx+30h]                  ; args[2]
call 0000000184FC4E00h   ; Int32.Parse
mov edx,eax; xor r8d,r8d
mov rcx,rdi
call 00000001832BC4C0h   ; WorldUnitBase::GetLuck
test rax,rax
setne al                           ; return GetLuck(id) != null
```
（GetLuck @0x1832BC4C0 遍历 allLuck，逐项 [`rax+rcx*8+20h`] 取 after item、`mov rax,[rax+20h]` 内嵌数据对象、`cmp [rax+10h],ebp` 值比较，如上文片段；完整体已走通。）

### 置信度
- Feature 解析与参数偏移：**高**（双 Int32.Parse + 双数组槽偏移 0x28/0x30 完整还原）。
- GetUnitInType 1→+0x28：**高**（cmp edx,1; je 0x18259325F）。
- GetLuck 值比较 key：**中高**（int 相等比较确凿；"+0x20 指向对象的 +0x10 就是 luckData.id"的字段命名未从符号还原，仅通道推定）。

### 未确认项
- GetLuck 里 `[元素+0x20]→[+0x10]` 两级解引用在 C# 侧对应哪个字段名（likely WorldUnitLuck 之类内嵌 data），未从字符串字面量/类名还原；不影响"值相等"结论。
- `lit`（字符串字面量）通道风险：分隔符（区分 `feature` 前缀的流程在 0x1859938C0 的 IndexOf 静态串 `186F7F300`/`186D1DF68`）未用 `lit` 从输出 `_cachedName=` 取出，未百分百落名。

---

## 4.【问题四 · 同段多对象】一段命中多个 → 全部到 TheList 还是只取 1

### 结论
**"一段命中多个 → 全部并入"**：GetUnitsInID 的段循环对每段 `List`1::AddRange(GetSaveUnit(...))`——AddRange 是整列表并入，不是 `Add(单元素)`/`First()`。因此 10-18「3 个同段同 luck（c1=c2=c3=400）」之所以出 3 人，是与"每段并入各自命中"的形式一致的可解释结构（各段各自命中互不裁剪），不是"人口轮替、每段取 1 个不同的人"这一种机制。

### 证据
见问题一 0x1859921B4 段循环片段：唯一的收集语句是 `List`1::AddRange`；段循环没有"取第一个/elem[0]"或"若已有则丢"指令（`List`1::Add` 在该循环内未出现）。

### 反汇编片段（重复关键三行）
```
call 0000000185991880h   ; GetSaveUnit(this, 段id, false)
test rax,rax
je near 000000018599341Fh
mov r8,[rel 186F57F90h]; mov rdx,rax; mov rcx,r12
call 0000000184E37FB0h   ; List`1::AddRange
```

### 置信度
- 段级收集不截断为 1：**中高**（AddRange 形态直接可见）。
- 但「10-18 3 人」的最终"人名单是同一人重复 3 次还是 3 个不同人"，取决于 AddRange 后（或订阅器 b__8）是否去重——**未完全反汇编**，见未确认项。

### 未确认项
- 去重器：b__8 @0x182BDD340 与 4 参 bool 谓词 @0x1844EF9D0、以及 0x185992717 起的后置过滤块未逐行还原；若存在"同名/同 id 去重"，在这层。**在验证前，对"3 同段出 3 人"的最强表述是：机制为并集累加，去重与否未确认（宁可存疑）**。

---

## 5.【问题五 · 进运时表】allLuck 有对象但 conf 表无行，GetLuck 还能找到吗

### 结论
1. **CreateLuck 链对 conf 表有硬依赖**：`CreateLuck(data) @0x1832BAC80` 取 `data+0x10`（NewLuckData.id）传入 `WorldFactory::CreateLuckInID @0x18280A530`，后者 `get_conf → ConfMgr::get_roleCreateFeature → ConfRoleCreateFeatureBase::GetItem(table, id, false)`；**GetItem 返回 null → 走 @0x180279A90 throw 路径（JIT throw helper + int3 填充）**——抛出， luck 对象根本无从产生，于是"这个 luck 确实在 allLuck 里"与"conf 表没有该行"**两条不能同时为真**（至少在 CreateLuck 服务路径上）。
2. 能造成"luck 已在 allLuck 但表里无行"的，只能是**不经 CreateLuckInID 的其他写入口**（如存档装载/直接 Add/AddAddLuck 类通道——其中 `PropertyData.AddAddLuck @0x183CE81E0` 写的是 **PropertyData+0x120 的 addLuck，与 unit+0x40 的 allLuck 完全是两个列表**）。只要对象确实进入了 allLuck，**GetLuck(x) 与 conf 表就不再有耦合**（GetLuck 全程只扫 allLuck [+0x40] 这一个 List） → `feature_1_x` 能通过。
3. **"挂载 addLuck 成功但 GetUnits=0"的机制层解释**：AddAddLuck 通道写入的 addLuck(+0x120) **不会**被 GetLuck(+0x40 范围扫描) 命中 → 对所有 feature 段呈现为"没有人"（GetUnit → null）→ 触发问题二的组级级联 → **GetUnits 整组 0 人、无异常**。这与今天实测（两层挂载都成功、直调 48ms 无异常、结果 0）行为一致。

### 证据与反汇编片段
CreateLuck @0x1832BAC80（关键连续 20 行）：
```
test rbp,rbp; je near 1832BADD2h
mov ecx,[rbp+10h]                 ; data.id
xor edx,edx
call 000000018280A530h   ; WorldFactory::CreateLuckInID(id)
mov rdx,rax
mov [rbx+18h],rax                 ; wrapper.luck = 新对象
...
mov rcx,[rsi+40h]                 ; unit.allLuck
mov r8,[rel 186FA8BC0h]
mov rdx,[rbx+18h]
call 0000000185F27700h   ; List`1::Add          ← 写入 +0x40 列表
...
call 0000000185CF3CD0h?（略）     ; KeyGeneratedEventHandler 触发
...mov rdx,[r8+190h]; call qword [r8+188h]     ; luck 对象自初始化虚调用
```
CreateLuckInID @0x18280A530：
```
xor ecx,ecx; call 0000000183F870F0h   ; g::get_conf
call 00000001822E2430h   ; ConfMgr::get_roleCreateFeature
xor r8d,r8d; mov edx,ebx; mov rcx,rax
call 0000000183C39290h   ; ConfRoleCreateFeatureBase::GetItem
test rax,rax
je short 000000018280A5E1h            ; 行缺失 → 0x18280A5E1
mov ebx,[rax+18h]
cmp ebx,1; je short 18280A5BF
（两条余下分支均为：new WorldUnitAddLuck）/ ctor / return）
0x18280A5E1: call 0000000180279A90h;  int3 ×N       ← throw 路径
```

### 置信度
- CreateLuckInID 缺行→throw：**高**（throw-helper 调用与 int3 填充清晰，与「GetItem 非 null 仍 new 对象」形成对照）。
- addLuck(+0x120) 与 allLuck(+0x40) 互不相通：**高**（GetLuck 全程只引用 +0x40，CreateLuck 只 Add 到 +0x40；AddAddLuck 位置来自已知考古）。
- "虽然表缺行也能在 allLuck 存在 luck 的现实场景是另一种写入口"：**中等**——具体是哪条入口（存档 Load / lua-bridge / 直接 Add）未追；只排除了 CreateLuck。

### 未确认项
- WorldUnitAddLuck 构造完成后 `call qword [r8+188h]` 虚调用（vtable+0x188，来自 16 行段）内部是否写 id——若该步失败，GetLuck 仍可能查不到（未还原，留白）。
- AddAddLuck 路径能否间接（如派发/事件）把同 id 写入 allLuck：未按反汇编核对，以"两个列表"的静态证据放进报告；**mod 侧"两层挂载"是否真的同时动了 allLuck 也可能因为挂载层的叫法/翻译错位——该点未在本次窗口内用运行时数据验证，如需闭环建议运行时 dump allLuck 后对拍**。

---

## 附：本窗口用过的 raw 求证清单

| VA 起始 | 函数 | 用途 |
|---|---|---|
| 0x185991CB0 | ConfNpcCondition::GetUnitsInID | Q1/Q4 段循环 + roundMonth + 段列表遍历 |
| 0x1859920DE | 同上（首静态块后） | 同一函数正常入口 |
| 0x1859934D0 | ConfNpcCondition::GetUnits(string[], data) | Q2 组循环、null 段级联 |
| 0x185993730 | GetUnits 字符串段组尾段 | 级联出口及新数组返回 |
| 0x185991C20 | ConfNpcCondition::GetUnit | null/count==0 传 null 链 |
| 0x185991880 | ConfNpcCondition::GetSaveUnit | Q1 段级取人（半截） |
| 0x1859917E0 | ConfNpcCondition::ClearSaveUnit | Q2 级联清理 |
| 0x1859938C0 | ConfNpcCondition::GetUnits(string, data) | Q3 条件路由（IndexOf/Int32.Parse） |
| 0x1859937F0 | ConfNpcCondition::GetUnits(int, data) | Q4 → GetUnitsInID 直调链 |
| 0x182590500 | UnitCondition::Feature | Q3 参数解析+GetLuck |
| 0x1825931A0 | UnitCondition::GetUnitInType | Q3 偏移映射 |
| 0x1832BC4C0 | WorldUnitBase::GetLuck | Q3/Q5 匹配 key |
| 0x1832BAC80 | WorldUnitBase::CreateLuck | Q5 写 allLuck |
| 0x18280A530/0x18280A5BF/0x18280A5E1 | WorldFactory::CreateLuckInID | Q5 conf 依赖/throw 路径 |
| 0x182BDD340/0x182BDD4C0/0x182BDDC56 | <>c__DisplayClass10_0/<GetUnitsInID>b__8 | Q1/Q4 订阅器（部分） |

---

# 续查（2026-10-19 11:42–12:00，按父 agent 指示：先引用既有定案文档，只拆"双方都未确认"的点）

> 证据来源标注约定：**【引 §x.y】= 引用既有文档已定案结论（不重拆）；【新反汇编】= 本窗口 raw/xref 实测。**
> 参考文档：`config-table-guide.md`（§2.4/§2.5.1/§3.1.1/§3.1.2/§四/§六）、`team-experiment-log.md`（E09–E15）、`dungeon-type-mapping.md`。

## 续查前勘误（对上文 Q1/Q2/Q4 的调和，必读）

1. **Q1/Q4 勘误（并入"每段 AddRange(GetSaveUnit)"读法）**：§3.1.2【引】已定案 GetUnitsInID @0x185991CB0 的真实求值链是
   `①候选池(area/region→GetUnitsInArea 或全图 GetUnits(includeHidden)) → ②sortType=0→SortRandom 打乱 → ③condition1..5 各包一个 SelectObservable(池,条件N串,条件NABC串)（惰性）→ ④订阅回调 <GetUnitsInID>b__8 @0x182BDD340 逐单位 ReturnAction4→UnitCondition::Condition → ⑤RemoveAll(null) 后返回，每槽产出 ≤1 人`。
   本报告上文 Q1 所引"0x1859921B4 段循环 GetSaveUnit→AddRange"代码块**真实存在**，但它在函数中的角色与 §3.1.2 主链如何对应（疑似 save=1 的存/换人事务层，见续 Q6）**未逐行对上 = 未确认**。上文 Q1 的"段级并集+组级 AND"应按下条勘误修正后再引用。
2. **Q2 勘误（重要）**：xref @0x1859934D0【新反汇编，扫描 137813 方法，callers=6】显示 `GetUnits(String组名/String[]段, data)` 的调用方只有 **`TriggerBase::Trigger` @0x182C48670、`DramaPackageTriggerBase::CreateFunctionData` @0x18463B910、`FortuitousTriggerBase::CreateFunctionData` @0x1826ABC70、`WorldDramaPackagerMgr::GetPackageUnit` @0x1827FDC90、两个 UI 闭包** —— **不在 `addBattleNPCData` / `npcConditionExamine` 主链上**（二者走 `GetUnits(int,data)` @0x1859937F0，内部直进 GetUnitsInID，不经过 0x1859934D0 的组循环级联）。因此：
   - 上文 Q2 的"任一段 0 命中 → 级联 ClearSaveUnit → 提前返回"结论**只适用于 TriggerBase::Trigger 等 GetUnits(组名) 触发链**；
   - **战斗链/探针链（`GetUnits(int组id, data)`）上空段 ≠ 一票否决**：空段只是该槽产出 null，被尾部 `RemoveAll(null)`（b__10_9）删掉【引 §4.2.2、E11】，**其余段命中照常返回**。
   - 故 10-19 实测 0 人的自洽解释是"**三个槽全空**"（与 E13/E15 定案一致：挂载层/luckData 未物化或 unitB 未赋值 ⇒ c1/c2 段也 false），而**不是**"c3 空段级联把 c2 的 2 人拖死"——后者在主链上不存在。
3. **Q4 结论修订**：去重问题按【引 §4.2.2 + E11】定案：**去重不存在**（尾部 `RemoveAll(u=>u==null)`，b__10_9 全文 `test rdx,rdx; sete al; ret`）；`sortType=0` → `CommonTool.SortRandom` ⇒ **多段相同条件各自独立随机采样（有放回）**，10-18"3 同段出 3 人（其中两个是同一人）"来自有放回抽样（E11 日志：`[0]方成和 [1]温芸 [2]方成和`），**不是**"每段取 1 个不同的人"的去重机制。上文 Q4 的"并集不裁剪"在"同一人可同时占多槽"意义上仍成立（E11 实证 npcUnits 里同一人两次）。

## 续 Q6【GetSaveUnit / ClearSaveUnit 的消费语义】【新反汇编为主】

### 结论
1. `GetSaveUnit @0x185991880` 读的是一个**按条件 id 分键的持久化名单插槽**【新反汇编】：键 = `String.Concat(条件id.ToString(), 前缀)`，仓 = `g::get_data` 取到的 DataObjectData（数据仓），值 = **JSON 的 unit-id 数组**：`DataObjectData::ContainsKey` @0x183A86D30 → `GetString` @0x183A871C0 → `CommonTool::JsonToObject` @0x18338CA40 → 数组 r15；逐元素 `WorldUnitMgr::GetUnit(id串)` @0x18376C1D0 还原 unit，过滤 `[unit+0x78]!=0`（疑死亡/禁用标志）与 `UnitConditionTool::Condition(r13=[r14+0x90], UnitConditionData(data.get_unitA(), unit), 0)` @0x182CAEA40，通过者 `List`1::Add` 进本段结果列表。
2. **读即消费**：缓存路径读完后 `ClearSaveUnit(本组, 本id)` → `DataObjectData::DelString` @0x183A86E70 **删该 id 整个插槽**【新反汇编】。`ContainsKey=false` 的分支（je 近跳 0x185991BAA）落在"清槽 + new 空 List + ret"块【新反汇编，窗口 0x185991B98/152B：`xor eax,eax; mov rcx,[rsp+60h]; call ClearSaveUnit; List`1::.ctor; mov rax,rbx; ret`】——即本函数**只见读不见算**：没有插槽就返回空列表。**该函数内没有回写/现算指令**。
3. `ClearSaveUnit @0x1859917E0` 全函数体 = 拼单键 + `DelString` —— **清的是"该 id 一个插槽"**（per-segment-id，不是全组）；但 0x1859934D0 的空段级联会对**每个已命中段**各调一次 ⇒ 组级联 = 本组所有段插槽逐个清（组级效果、逐段执行）【新反汇编 + 上文 Q2 引用】。
4. **与 save 字段语义的调和**【引 §2.5】："`save=1`：存了以后调用不再判条件" —— GetSaveUnit 正是该行存档层读法（存档层名单优先于现场判条件）；DelString 清槽后"下一次调用是否重评估并重存"取决于**插槽写入者**，未定位。10-18 用的是 `save=0`【引 E11】，因此**主链根本不走 GetSaveUnit**。
5. **"第二段跳过已被第一段消费的人 = 天然去重"假设：不成立**。【引 §4.2.2/E11】主链无去重（有放回）；GetSaveUnit 消费链只在 save=1 侧、且"连续同段依次取下一个"所需的"写回者"未定位——**未确认，不作为 3 人机制的候选解释**（E11 已用真机日志把 3 人机制定为有放回随机抽样）。

### 置信度
- 插槽读取/重放/读即 DelString/ClearSaveUnit=单键：**高**。
- 空 slot → 空列表、函数内无回写：**中高**（分支窗口已定位到块，±0x10 的指令边界用 0x185991BF8 之外的窗口未再确认）。
- 插槽写入者位置：**未确认**（建议后续 `xref 0x183A86E70` 的 DelString 调用者 + find SetString 类方法）。

## 续 Q7【同一 unit 同挂两枚气运（400 与 396）】

### 结论
1. **(a) allLuck 同容纳两枚：结构上无约束**【新反汇编 + 引 §六】。`CreateLuck @0x1832BAC80` 对 `unit.allLuck(+0x40)` 的插入是**无条件 `List`1::Add`**（无 Contains/无覆盖检查）；`CreateLuckInID @0x18280A530` 每次 **new 全新 WorldUnitAddLuck/luck 实例**（无按 id 复用）。同名（team_feature_name_51 复用文案）、同 `duration=-1`、同分组互不影响——两枚并存成立。
2. **(b) 两枚不互相"消费"对方**：主链（save=0）根本无"已用池"概念——每槽独立对**整个候选池**重新过滤【引 §3.1.2】；同一人**可以同时占多个槽**（E11 实证：`[0]方成和 [2]方成和`）【引 E11】。GetSaveUnit 的分 id 插槽即使存在也是按 400/396 各自分键、互不串槽【新反汇编，见续 Q6.2】。
3. **真正会"变相去重"的是槽位本身**：若 c1/c2 段的 `conditionNABC`（A/B/C 过滤闭包）把同一人同时圈进两段，他就是"1 人占 2 槽"；反之一个 unit 上两枚气运是**并存**关系，不是槽位竞争关系。

### 置信度
- (a) 无覆盖/不去重、每次 new：**高**（两处连续指令直接可见）。
- (b) 槽间不互扰、同一人可占多槽：**高**（E11 日志硬证据 + §4.2.2 代码定案）。

### 未确认项
- `KeyGenerated` 事件订阅链是否含"同名/同 id 替换"逻辑（CreateLuck 里 raise 事件 @`KeyGeneratedEventHandler::.ctor` @0x1820F0870 后 `call qword [r10+178h]` 的处理器集合）；
- luck 对象经 `[vtable+0x188]` 自初始化虚调用（E13② 定案：type=2 的 `WorldUnitLuckDyn` **ctor 不填 luckData、要等 Init**）之后 `luckData.id` 的写入时序——**这直接决定 `GetLuck(x)` 能否命中**（E15 实测两层挂载成功后 GetUnits 仍 0 ⇒ 该未确认项是当前 0 人问题的活口之一）。

## 续 Q8【战斗链走哪个重载】addBattleNPCData【新反汇编 + 引 §4.2.2 对照】

### 结论
**选（b）**。`BattleFunction::AddBattleNPCData @0x1828FDEE0`（完整主体 raw，1300B）：

1. `args[1]`=`Int32.Parse`（组 id `-803158448`）→ `g::get_conf` → `ConfMgr::get_npcCondition` @0x1822DE880 取**ConfNpcCondition 表实例** → **`ConfNpcCondition::GetUnits(int 组id, null)` @0x1859937F0**（`xor r8d,r8d; xor r9d,r9d`，data=null）——即重载（b），内部直进 `GetUnitsInID @0x185991CB0`（5 槽求值链，§3.1.2）。
2. `args[2]`=`Int32.Parse`（`_0/_1/_2` 的 index）；`cmp dword [rbx+18h],2; jbe → 提前 return 不加人`（缺 index 不加）。
3. **`CommonTool::Index(GetUnits结果列表, index)` @0x18338C9B0 —— index 取的是 GetUnits(int组id) 并集结果的第 N 个元素**；越界 → null → `je short 0x1828FE000` 直接退出不加人（**无异常**）。
4. 命中则 `g::get_world → [+18h] → [+18h] → [+0xB0]` 的战斗 NPC 容器 `List`1::Add`（与 §4.2.2 `GetUnitWorld` 的 asm 写入链**逐字一致**【引 §4.2.2】：同一块内存，`addBattleUnit_3/4/5` 从同一容器 `Index(id-3)` 读）。
5. **最终行为图谱（主链闭环）**：`addBattleNPCData_<组>_0/_1/_2` = 三次**独立**的 `GetUnits(int组id, null)` 整组求值，各取结果列表 `[0]/[1]/[2]` 写进战斗容器 → `addBattleUnit_3/4/5` 再从容器按位取人。**若任一次 GetUnits 返回空/长度不足，对应 index 越界 → 静默不加、无异常**；空段不会把别的段命中拖死（勘误见上文）。10-18 成功（3 人）与 10-19 失败（0 人）的差异由此归结为"**每槽过滤结果**"的差异（存储层物化/unitB 赋值），而不是索引或合并机制的差异。

### 反汇编片段（BattleFunction::AddBattleNPCData @0x1828FDEE0 关键连续段）
```
call 00000001822DE880h   ; ConfMgr::get_npcCondition  → rdi（表）
cmp dword [rbx+18h],1; jbe near 0x1828FE011h        ; 缺参退出
mov rcx,[rbx+28h]; call 0000000184FC4E00h   ; Int32.Parse(args[1])
xor r9d,r9d; xor r8d,r8d                           ; data=null
mov edx,eax; mov rcx,rdi
call 00000001859937F0h   ; GetUnits(int 组id, null)   ← 重载(b)
cmp dword [rbx+18h],2; jbe near 0x1828FE021h        ; 缺 index 退出
mov rcx,[rbx+30h]; call 0000000184FC4E00h   ; Int32.Parse(args[2])
mov r8,[rel 186CE6DB0h]; mov edx,ebx; mov rcx,rdi
call 000000018338C9B0h   ; CommonTool::Index(结果列表, index)
mov rbx,rax; test rax,rax
je short 00000001828FE000h                          ; 越界 → 不加人
...g::get_world → [rcx+18h] → [rdx+0B0h] 容器
mov r8,[rel 186F579B0h]; mov rdx,rbx
call 0000000185F27700h   ; List`1::Add              ; 写入战斗容器[0xB0]
```

### 置信度
- 走（b）重载、data=null、Index 取第 N、写 [0xB0] 容器：**高**。
- "三次调用拿到 3 个不同人"的稳定性：**中等**（依赖每槽独立随机采样在当轮内不重排——`sortType=0` 每次调用都重新 SortRandom【引 §2.5/§4.2.2】，三次独立调用间顺序可变；E11 已示 npcUnits 顺序内可重复同一人）。

### 未确认项
- `CommonTool::Index` @0x18338C9B0 越界确切行为（null 或抛）——按调用侧 `test rax,rax; je` 视为 null 返回，未单独反汇编；
- `GetUnits(int 组id, data=null)` 时 GetUnitsInID 内 `[rsp+0x50]` 前置源的 null 分支（data 为 null 时 AddRange 源是 TheList 还是空）未逐行拍死——它与 E15"condData=null 直调真答 0"不冲突，但"直调 0"究竟是"槽全空"还是"null-data 分支短路"未最终分离（**未确认**；E15 的自动复查链已把该直调列为待复测项）。

---

# 续查二轮（2026-10-19 12:00–12:20）：Q9 / Q9' / Q9'' / Q10 / Q11

> 依旧【引 §x.y】= 引用既有文档定案；【新反汇编】= 本窗口 raw/xref 实测。本轮新考古输入：10-18 晚成功局只走 AddAddLuck、CreateLuckInID 全返 null、靠重载材质化（父 agent 通报 team_multi.log.2021_prerestart8 原文）。

## 续 Q9【data=null 的走向】（E15"直调真答 0"的最终归因）

### 结论
1. **GetUnitsInID 入口把 data(arg3) 写入某类静态上下文槽后不再解引用**【新反汇编，入口段：`mov rax,[186C72F40h]; mov rcx,[rax+0B8h]; mov [rcx],rbx`（static_fields[0]=data）+ write-barrier】→ **data=null 不炸、也没有(a)内部 new NpcConditionData**。
2. **前置源不是 data，是行级 TheList**【新反汇编（0x185992100 窗口重读）】：vtable 调用(this=row, id, string) 返回对象 rdx 后：
   - `[rdx+0x88]==1`（疑 save 标志）：`GetSaveUnit(this,[rdx+0x10],false)` → **Count>0 → 跳 @0x1859933E9 共享 epilogue 直接返回 saved 列表**；Count==0 → `lea rax,[rdi+48h]; mov [rsp+50h],rax`；
   - `!=1` → `mov [rsp+50h],rbx`，其中 rbx=此前 `lea rbx,[rdi+48h]`；
   - 汇流后 `List::AddRange(rcx=newList, rdx=[rsp+0x50] 再 `mov rdx,[rdx]` 一次解引用)` ⇒ **前置源 = this.TheList（行级月缓存 ArrayList [row+0x48]）**，data 是否为 null 不影响这里。
   ⇒ 选项答案：**(b) 用 TheList 顶替（机制上"前置源恒为 TheList"）**；(a) 不 new；(c) 不短路。
3. **GetUnitInType type==1 的 null-guard 定案**【新反汇编 0x18259325F / 0x18259324C】：两分支体同为 `mov rax,[rcx+18h]; test rax,rax; je short 0x182593285（返回 null）`→ type==1 返回 `[ctx+0x18]`（unitB 槽）。**上下文 [this+0x18]==null 时干净返回 null，不 NRE** → UnitCondition::Feature `test rax,rax; je → 返回假` ⇒ **unitB 从未被赋值 ⇒ feature_1_x 恒 false ⇒ 该槽 0 人**——E13 预期"假设 B"的机制面成立。
4. **Condition 大分发器真身**【新反汇编（0x182581F50 静态块后 0x1825830DD 起）】：按 `|`(0x7C) Split 条件串、按 `_`(0x5F) Split args，`String.Equals(args[0], 字面量)` 逐前缀派发到 `UnitCondition::Task/SchoolTask/TaskTime/TaskTime2/…/Feature`（rcx=同一 UnitCondition 实例）——Feature 的 `this` 即该实例，GetUnitInType 读 `[实例+0x18]` 作上下文。
5. **b__5/b__6/b__7 在 data=null 时是否跑/写 unitB：未确认**（§2.5.1【引】定案 unitB 由 ABC 闭包侧写；本轮未重拆闭包借用的上下文来源）。
6. **归因总结**："condData=null 2 参直调真答 0"最可能是 **feature 段在直调路径上拿不到有效上下文（[实例+0x18] 未挂 data/未赋 unitB）→ 全槽 false → RemoveAll(null) 后空列表**，与 E15 真答 0 一致。⚠️【引 E12】NpcConditionExamine 用 `new NpcConditionData()` 也是空 unitB 起步，但正常触发里 b__5/b__6/b__7 会逐候选侧写 unitB【引 §2.5.1】——**引擎原生路径（带闭包）能判真；探针 2 参直调（data=null）与原生等价与否取决于 [实例+0x18] 赋值时机，未定案**。

### 置信度
- 入口 static 槽不解引用 / 前置源=TheList / GetUnitInType null-guard 返 null / 分发器形态：**高**。
- "data=null ⇒ 全槽 false"最终定案：**中等**（差 [实例+0x18] 赋值时机一步）。

## 续 Q9' / Q9''【引擎原生挂运气运的确切序列 + type=2 luckData 空洞】

### 结论
1. **引擎自己的挂运链（DramaFunction::AddNPCLuck @0x18223E270，§五 addNPCLuck 私藏命令的反汇编）**【新反汇编】：
   - `args[1]` 数值 → DramaFunction::GetUnitInType @0x182253A90 → unit；`args[2]` = 气运 id；
   - **先查重复**：`UnityAPIEx::Contains(白名单数组, id)` @0x1847A7940 命中 → **`new UnitActionLuckDel(id)` @0x1838DF310 → unit 虚调用 `[klass+0x1C8]`（r9=[klass+0x1D0] 元数据）**（删旧）；
   - **`new UnitActionLuckAdd(id)` @0x1838DF0B0 → unit 虚调用 `[klass+0x1C8]`**（执行挂运动作）；`args[3]`=时间 → 写 `[action+0x38]+0x14`。
   ⇒ **引擎原生对"重复同 id"的处理是 Del 后重挂，不是覆盖**。
2. **UnitActionLuckAdd::OnCreate @0x1838DE970**【新反汇编】——原生挂运三步序列：
   ```
   get_conf → ConfMgr::get_roleCreateFeature @0x1822E2430
            → ConfRoleCreateFeatureBase::GetItem(table, id)                → r13 行（缺行→早退链条）
   [r15+0x10]=unit；WorldUnitBase::GetLuck(unit, id) @0x1832BC4C0 → [r15+0x40] 预查已有
   row.[+0x30]（type）分支：
     type==1 → 现有 luck 数据 delay 累加（0x182713040）
     type==3 → ConfFateFeatureBase::GetItem @0x1859DE910（宿命气运特殊处理）
     其余（含 type==2 后天气运）→ 公共路径 @0x1838DEB91：
       PropertyData::AddAddLuck( [unit+0x18]→[+0x10]→[+0x10]
                                 = unit.data.unitData.propertyData, rdx=[r15+0x38] 数据对象 )
                                 @0x183CE81E0                              ← 存档/面板层
       WorldUnitBase::CreateLuck(unit, 同 data)          @0x1832BAC80      ← 运行时层 + 内部 Init
       WorldUnitLuckBase::NewCreateLuck(luckObj)         @0x1837696A0      ← 补效果/标记
   ```
3. **NewCreateLuck 本体**（0x1837696A0）【新反汇编】：`(this=luckObj, edx=0)`；对 `[this+0x20]`（DataObjectData）ContainsKey/缺失时 SetString @0x183A873D0 打标记，然后遍历 `[this+0x30]` 列表逐元素 `call [klass+0x1A8]`（虚拟 onApply/Load）——**luck 对象字段（含 luckData.id）在 CreateLuck 内部 `[vtable+0x188]` 无参 Init 里就绪**【引 E13② 定案 + 本报告 Q5 反汇编的 vtable 调用吻合】。
4. **Q9'' 核心答复——可照抄 C# 序列（同会话内 GetLuck 立即可命中）**：
   ```csharp
   // 0) 同 id 已挂则先删旧（原生=UnitActionLuckDel；mod 现行 RemoveLuck 已等价双层清理【引 E13 改动2】）
   // 1) u.data.unitData.propertyData.AddAddLuck(data);   // data = 完整 NewLuckData{ id=x, ... }（+0x120）
   // 2) u.CreateLuck(data);                              // +0x40；内部 CreateLuckInID(x)（conf 缺行→抛）
   //                                                        + allLuck.Add + [vtable+0x188] Init ⇒ luckData 就位
   // 3) u.GetLuck(x)?.NewCreateLuck();                    // ★ 引擎原生三连的最后一步（原生效果/标记落盘）
   // 4) 判据：u.GetLuck(x) != null（最终消费者读法【引 §六】）
   ```
   **与 E13 的差别**：E13 探针策略 #1 是"先 CreateLuckInID(id) → 取 lk.luckData → u.CreateLuck(ld)"——在 Init 前取 luckData=null → hollow【引 E13②】；正确做法是**把 mod 自己构造的完整 data 直接喂 CreateLuck**（Init 从 data 物化 id），再补 NewCreateLuck。
5. **10-18 晚为什么能命中 3 人**【新反汇编 + 父通报 + 引 §六/E11】：当时只写 AddAddLuck（策略 #2，CreateLuckInID 全返 null），同会话出 3 人 ⇒ 命中的是**更早会话挂过 addLuck、经读档 EnterWorldFixData 材质化进 allLuck 的存量**（E11 的"5 个气运持有者"同类）。feature 段读的是 allLuck 运行时层【引 §2.5.1】——"同会话只写 addLuck 永远不可见"与"存量材质化后可命中"两事同时成立。
6. **WorldMgr::EnterWorldFixData @0x182C89930 是全局修复入口**【新反汇编主头 + 引 §六/E13】：启动时跑全套（mod-cache / roundMonth / Task / Drama 多段处理 + Stopwatch 日志），**不是"单 unit 单气运"可定点调用的函数**；定点材质化就用第 4 条的三步序列逐 unit 跑（等价物），**不要手动调 EnterWorldFixData**。

### 置信度
- 原生三步序列（AddAddLuck→CreateLuck→NewCreateLuck）：**高**（OnCreate 连续指令直接可见；AddNPCLuck action 派发可见）。
- "luckData 在 CreateLuck 内部 Init 后就位"：**高**（E13②【引】+ Q5 vtable 调用【新】吻合）。
- "缺 NewCreateLuck 就是 10-19 0 人的直接原因"：**中等偏下，待验证**。feature 判 true 只需 luckData.id（Init 已给）；TeamTool.AddLuck 的两层写【引 E13 改动1】理论上应可被 GetUnits 命中 ⇒ **10-19 0 人的最终裁决留给单元探针：直接 dump `[unit+0x40]` 列表元素的内部 id（绕过 GetLuck），一锤定音"luckData 空洞"还成立不成立**——**未确认**。

## 续 Q10【DungeonTrigger 的门走不走 String 重载】

### 结论
1. **"DungeonTrigger"不是类**【新反汇编，find DungeonTrigger】：只有 `ConfMgr::get_dungeonTrigger`/`DLCConfMgr::get_dLCDungeonTrigger` 等 conf 包装 ⇒ 它是配置表项，"是否 TriggerBase 子类"在类型层面不成立。xref TriggerBase::Trigger@0x182C48670 **callers=0（纯虚派发）**。
2. **TriggerBase::Trigger 本体**【新反汇编】：`DramaTool::get_isOpenDrama` 门槛 → 已执行标记（DataObjectData 键=前缀+ToString(触发器id)，ContainsKey/GetString/op_Inequality）→ get_conf→**ConfMgr::get_npcCondition** → **new string[2]**（[triggerItem+0x38]→[rbx+0x20]=槽0；[triggerItem+0x40]→[rbx+0x28]=槽1）→ **`ConfNpcCondition::GetUnits(rcx=表, rdx=string[2], r8d=0)` @0x1859934D0** → `cmp byte [rax+10h],0` 后用 roundMonth 拼标记键。**它是 String[] 形态、data=null 的消费者（空段级联语义所在链）**——但属于剧情/奇遇类触发器家族，非我们副本链。
3. **我们的副本链不走 String 重载**【引 dungeon-type-mapping/E12/E13 + 本报告续 Q8】：DungeonTrigger 评估唯一入口 = `SceneBattle::Init → ConfDungeonTrigger::GetItem(dbi)@0x184BA3EA0`（候选筛选用 `GetUnit(setNPCA)` 单串【引 §3.1.1】）→ 条件命令 `npcConditionExamine` → `NpcConditionExamine@0x1825A3280` → **GetUnits(int 组id, new NpcConditionData()) = (b) 形态，不经过 0x1859934D0**；`battleFunction → AddBattleNPCData → GetUnits(int, null)` 同为 (b)。⇒ **空段一票否决（级联清零）不适用于我们的触发开火与出人链**；空段=静默空槽【引 §4.2.2 RemoveAll(null)】。

### 置信度 / 未确认项
- find 无类型 + xref=0 + Trigger 本体完整 + (b) 调用点实锤：**高**。
- 未确认：TriggerBase::Trigger 的 string[2] 槽源字段 [triggerItem+0x38/0x40] 语义名（疑似另一触发家族的组槽）；虚派发表全景（哪些子类最终落到 0x182C48670）。

## 续 Q11【三次 addBattleNPCData 的池序稳定性】

### 结论
1. **TheList 是行级月缓存**【新反汇编（GetUnitsInID 头部）】：`[row+0x50]` 存 roundMonth，与 `WorldRunMgr::get_roundMonth` 相等 → 跳过 Clear+重填（je 0x1859920DA），但**仍执行**前置源=TheList 的 AddRange 与后续求值；month 变了才 Clear+重算。⇒ **同月内对同一组的重复 GetUnits 共享同一份 TheList（已排序/已过滤池）**。
2. **三次 `addBattleNPCData_<组>_0/_1/_2`（同月、同组）**：前置源相同 ⇒ 池序有共享基础；**但**每槽过滤/`SortRandom` 是否每次调用重跑（§3.1.2 ②/④【引】、§2.5 sortType=0【引】）**未定案**——若重跑，段内顺序各次独立抖动（E11 已证**单次调用内** npcUnits 即可重复同一人【引 E11】）。
3. **工程结论**：三次 Index 拿 3 个不同人的可靠保证不是池序，而是【引 E11】"每段候选集恰好 1 人"的结构设计（一槽一气运）。池序只是锦上添花。

### 置信度
- TheList 月缓存行为：**高**；槽采样每调用重跑与否：**未确认**。

---

*续查二轮结束。全程只读反汇编。【引】与【新反汇编】分开标注；未确认处显式留白。最优先待办（给 mod 侧）：① 按续 Q9' 三步序列补 `NewCreateLuck()` 并真机对拍 `u.GetLuck(x)`；② 单元探针直接 dump `[unit+0x40]` 列表元素内部 id（绕过 GetLuck），一锤定音"luckData 空洞"。*

---

# 续查三轮（2026-10-19 12:24–12:58，实机日志驱动：10:45 会话证据 + 六疑点定案）

> 输入：父 agent 提供的 10:45 会话日志原文（DLL=bak_1019_1110_before_teamprobe；sentinel=givemap max=2 radius=8；c1=51/c2=55/c3=56 三槽异段已生效；`[getunits]` 反射 3 重载但"没拿到列表"；`[第23拍] 条件组 -803158448 能选出 0 人`；随后玩家进 1012 且 Player.log 无任何 [①npcUnits]/BattleEndFront 行）。
> 关键即时勘误（对新证据）：**10:45 的 `[getunits]` 3 重载块根本没调到引擎（旧探针签名记账问题），它不是引擎答案**；那条 `第23拍=0` 才是 2 参直调真调（E15 口径），且**传的是 data=null**。

## 三轮疑点 1【GetUnits(int, NpcConditionData) 的 data 参：null 合法吗 + 引擎自用传什么】【新反汇编】

### 结论
1. **null 合法（双重证据）**：(a) 反汇编——GetUnitsInID 入口把 data(arg3) 写入静态上下文槽后**不再解引用**（前两轮已证）；前置源恒为行级 TheList（`mov [rsp+50h],rbx`，rbx=`lea &[row+0x48]`）；后置大过滤块 0x185992717+ 未扫描 → 窗口内 data 解引用点=0。(b) **引擎自己在战斗链上就是传 null 的**：`BattleFunction::AddBattleNPCData @0x1828FDEE0` `xor r8d,r8d; xor r9d,r9d` → GetUnits(int 组id, **null**)【续 Q8 已证】；而 10-18 晚的成功局人是**从这条 data=null 的链上出来的**（addBattleNPCData → 容器 [0xB0] → npcUnits=3【引 E09/E11】）。⇒ **若 null 会阴影 early-return 空列表，addBattleNPCData 在任何配置下都加不了人——与 10-18 实测矛盾**。
2. **引擎自用形态对照**：`NpcConditionExamine @0x1825A3280` = `GetUnits(int.Parse(a[1]), new NpcConditionData())`【引 E12 解码原文】；`AddBattleNPCData` = `GetUnits(int, null)`【新】。两个引擎入口都进 GetUnitsInID 主链，谁也不走 `GetUnits(String组名)` 的级联分支。
3. **`AddBattleNPCData` 调用者**：xref `0x1828FDEE0` callers=1 = `<>c__DisplayClass5_1::<OptionsFunction>b__1` @0x1832AD5F0 —— 注册进**名字→命令函数派发表**（OptionsFunction 匹配 battleFunction 命令名分发；`WorldMgr::EnterWorldFixData` 里也见到 `DramaFunctionTool::OptionsFunction` 调用，同一族派发工具）。⇒battleFunction 串（'|' 分隔）每 token 一次独立命令调用。

### 置信度 / 未确认项
- null 合法：**高**（反汇编 + 10-18 实证交叉）。
- 未确认：后置过滤块（0x185992717+，含 TheList 消费与 roundMonth 年龄过滤）是否读取 data——未逐行；b__5/6/7 闭包是否经静态 ctx 槽演化（续二轮 Q9 的 [实例+0x18] 赋值时机问题原样保留）。

## 三轮疑点 2【三个 GetUnits 重载是不是独立实现】【新反汇编】

### 结论
1. 三个重载都是独立方法体（`find GetUnits` 曾给出 0x1859934D0/0x1859937F0/0x1859938C0 三址；`[getunits]` 反射亦列 3 个签名）。
2. **但 String 版是"解析包装"不是独立求值引擎**（本窗口补齐 0x1859938C0 的最后一个未拆分支 0x185993A6B）：
```
0x185993A6B:
  ...CommonTool::StrSplitInt(condition, '_', null) @0x1835BDFB0 → int 数组 rbx
  cmp dword [rax+18h],1 / jne 0x185993AF6（多于 1 段 → 另一表项路径）
  （len==1 → alloc int[2]（186D8E9D8, edx=2））
    mov eax,[rbx+20h]; mov [rcx+20h],eax        ；取拆出的第一个 int
    cmp edx,1 / jbe …；mov dword [rcx+24h],1     （写第二槽）
    mov edx,[rbx+20h]; xor r9d,r9d; mov r8,r15; mov rcx,r14
    call 00000001859937F0    ; GetUnits(int, data)      ← 又汇回 (int) 版
```
   前一轮已证 String 版两条更早分支：`IndexOf(marker)==0 → 0x185993BD4`（未拆=未确认小项）与"纯数字 → `Int32.Parse` → 尾调 GetUnits(int)"。⇒ **(String) 版 0x1859938C0 全部出口都落到 (int)@0x1859937F0 或组表 ([]：**0x1859934D0** 的组循环**。
3. **勘误护栏（针对父 agent 的提醒）**：第二轮"空段级联清零"结论挂载在 **0x1859934D0（GetUnits(string[]/多组名形态)）**，非 String 单串版——它继续成立（xref 调用者=TriggerBase::Trigger 等 6 处，续一轮已证）；battleFunction/npcConditionExamine 主链不经它，也继续成立。

### 置信度
- 三重载独立存在：**高**；String 版=解析包装：**高**（两个分支已实锤汇回 int 版；唯一未拆的 0x185993BD4 分支是"起始 marker"特例，等它落地后再下级联语义无关的结论）。

## 三轮疑点 3【GetLuck 遍历遇 luckData==null：continue / false / 还是抛】【★新反汇编定案】

### 结论
**抛异常，不是 continue、不是静默 false。**GetLuck @0x1832BC4C0 的遍历循环里三处 null-guard **全部跳同一个 throw 块**：
```
mov rax,[rdi+10h]; movsxd rcx,ebx
mov rax,[rax+rcx*8+20h]        ；allLuck._items[i]
test rax,rax
je short 00000001832BC584h     ；元素 null → 0x1832BC584
mov rax,[rax+20h]              ；内嵌 luck 数据对象（GetLuck 的比较源）
test rax,rax
je short 00000001832BC584h     ；inner data null → 0x1832BC584
mov rcx,[rsi+40h]
cmp [rax+10h],ebp
je short 00000001832BC558h     ；匹配 → 0x1832BC558
inc ebx; …; backedge 0x1832BC511
---（补刀 0x1832BC580/30B）
0x1832BC580: xor eax,eax
             jmp short 00000001832BC56Bh        ；正常遍历完（找不到）→ rax=0 经共享收尾返回 null
0x1832BC586: call 0000000180279A90h / int3 ×3    ；← 0x1832BC584 三处 je 的落点 = throw helper
0x1832BC558（found）: test rcx,rcx; je 0x1832BC584(throw); get_Item(list, i); epilogue; ret（rax=元素）
```
⇒ **allLuck 里只要有一枚 `元素==null` 或 `内嵌 luck 数据==null` 的槽位，`GetLuck(id)`（在该 unit 上求值）会走到它时直接抛**——不跳过、不 false。与 `WorldFactory::CreateLuckInID` 缺 conf 行的 throw 形态完全同款（call 180279A90 + int3）【前轮已证同一 helper】。

### 勘误（对应父 agent 提出的"会不会整段 false"假说）
"一枚 null 元素毒整段为 false"**表述不成立**——它不是走 false 分支，而是**异常路径**（若抛了，行为取决于求值框架对单 unit 求值异常的处理 = 未确认；若 b__8/ReturnAction4 逐 unit 求 catch-continue，则等价"该 unit 判假"；若异常直接冒泡，则整组求值中断）。**10:45 会话全程无异常日志** ⇒ 当时要么 a) GetLuck 根本没被执行到（data=null 上下文先死，见续二轮 Q9），要么 b) 被外层静默吞 → **未确认，列为下一手**：在探针里 try/catch 包住 `feature_1_x` 直接求值并上报异常类型。

### 置信度
- 三处 null-guard → 同一 throw 块：**高**（字节级补刀后闭合）。
- 抛出后求值框架行为：**未确认**（无反汇编证据；日志只知无异常冒泡）。

## 三轮疑点 4【[①npcUnits] 为什么一行都没有】（读 C# 源码，无反汇编）

### 结论

> ⚠ **2026-10-21 注**：本节分析的 `TeamBattleEventProbe.cs` **已删除**（结论已并入
> `tools/TeamBattleFollowup.cs`，见 `docs/features/team.md` §9.6）。下面的行号是当时的快照，
> 现在只能作为历史证据读，不要再去源码里找它。

- `[①npcUnits]` 在 `csharp/diag/team/TeamBattleEventProbe.cs::DumpNpcUnits()`（L244-257）：读 `g.world?.battle?.data?.npcUnits`；由 `OnBattleEndFront/BattleEnd/BattleExit` 三个处理函数调用。
- **订阅是无条件的**（L92-110：`Install()` 只要 `g.events!=null` 就把 `BattleEndFront/BattleEnd/BattleExit` 三个事件全订阅，并有 InstallReport 日志"★三个事件全部订阅成功★"）。**没有任何按 sentinel action 的门**。
- ⇒ 10:45 会话 Player.log 里一行都没有 ⇒ 只剩两种解释：**① `TeamBattleEventProbe.Install()` 根本没被调用/未输出 InstallReport**（部分初始化没跑——去 Player.log 搜 "TeamBattleEvent" 或 InstallReport 的字样即可分辨）；**② 事件被订阅了、但从 10:45 进副本到 ~10:56 关服为止那场战斗一直没走到 BattleEndFront**（"战斗没打完就关"——E15#5 的观察窗口太小）。注意：**InstallReport 只要 Install 跑过就一定 dump**——日志里既然连这个安装回执都没有，最可能是 **Install 没跑**（这一版装配流程里 probe 未挂）。
- **勘误（对 E15#5）**："哨兵停在 givemap ⇒ 进副本内零记录"的归因**过宽**：哨兵影响的是 auto/dumpbattle/TriggerSelfCheck 等 in-dungeon 动作；**事件订阅本身与哨兵无关**（源码实证）。10:45 零行 = 探针可能压根没安装，而非"订阅了但没来事件"。

### 置信度
高（源码直读；零反汇编）。

## 三轮疑点 5【addBattleNPCData_…_0/1/2 的 N 是槽位索引还是第几次求值】【新反汇编】

### 结论
**N = GetUnits 结果列表的索引**（续二轮 Q8 已反汇编：`CommonTool::Index(GetUnits 结果, Int32.Parse(args[2]))` @0x18338C9B0，越界→null→该命令静默不加）。**三次调用 = 三条独立命令**：
- xref AddBattleNPCData callers=1 = `OptionsFunction` 的注册闭包 → battleFunction 命令按名分发、每 token 一次；
- `addBattleNPCData_-803158448_0|…_1|…_2` 由 '|' 分隔（§3.1 battleFunction 字段【引】），每条含自己的 (组id, N) 两参；
- 函数内部**没有循环**转发给其它 index；"第几次求值"不存在——每次调用都完整重跑 `GetUnits(int 组id, null)`（续二轮 Q11 的月缓存使同月池序可共享）。

### 置信度
高（xref + Q8 反汇编 + §3.1【引】）。残余未确认：`OptionsFunction` 派发循环本身的一行反汇编（若需要 100% 的"逐 token 独立调用"日志铁证，可再 raw 一刀 0x1832AD5F0；本窗口未做）。

## 三轮疑点 6【GetUnits 返回空时触发器还触发吗】【引 + 新反汇编】

### 结论
**触发与"出人"是两条独立门**：
1. **触发器候选门**（进不进候选清单）：`ConfDungeonTrigger::GetItem(dbi)` 的 `setNPCA/setNPCB != "0" && GetUnit(组串)==null → continue`【引 §3.1.1/E12】+ `condition` 里的 `npcConditionExamine_<组>` → `Count>0` 门槛【引 §3.1.1 结3/E12】——**若组真答 0，触发器连候选都不是，整个 battleFunction 不执行**。这是"条件求值空 ⇒ 触发器不触发"的唯一门。
2. **出人门（battleFunction 内）**：`AddBattleNPCData` 对 GetUnits 空/ Index 越界的处理是**本条命令静默跳过**（je 退出，无异常）【续 Q8】——该条 add 不生效，但不会中止触发器其余命令，更不会中止战斗/进副本本身。
3. 对 10:45 日志的解读：玩家进 1012 = 进副本本身不需要队友（副本入口在触发器机制之外）；[①npcUnits] 零行（疑点 4）→ 无法用日志证实/证伪"触发器在 1012 是否开过火"。**结合 E14 的白名单（1012 已被剔除——若成品 DLL 已装白名单，触发器不开火是预期；但 10:45 用的 DLL 白名单表已起效与否要看 ModExcel md5 时间线）**【引 E14/E15】。⇒ **该盯的不是"探针 0 人"本身，而是**：(a) 用现行探针把 GetUnits 的三个重载（尤其 (int, new NpcConditionData()) 与 AddBattleNPCData 同参形态）各打一遍真话；(b) 直接 dump `[unit+0x40]`（allLuck）内部 id——**走 [TriggerSelfCheck]/dumpbattle 的 auto 路径**（哨兵已是 auto）。

### 置信度 / 未确认项
- 两条门分离：**高**（①引文档定案 ②续 Q8 反汇编定案）。
- 未确认：10:45 那版 DLL 的白名单表是否已部署（E14 部署 10:44 的表、DLL bak_1019_1110——表与 DLL 的时间线父 agent 手里有，firefox 推断不在本窗口做）；BattleEndFront 在那场战斗"完结前关服"的场景属日志窗口问题。

## 三轮汇总（给决策端的一句话版）

| 疑点 | 定案 |
|---|---|
| 1 | `NpcConditionData` 参数 **null 合法**（引擎战斗链自用 data=null；窗口内零解引用；10-18 经由 data=null 链成功出人）。"探针 0"不能再归罪传 null，要么是**求值上下文/unitB**问题（续二轮 Q9 未定案项），要么是**挂载没进 allLuck** |
| 2 | 三个重载独立存在；**String 版是 int/组版的解析包装**（含 `_索引` 语法路径 0x185993A6B 汇回 int 版）。第二轮级联语义仍挂在组版 ([]：**0x1859934D0**），不受影响 |
| 3 | GetLuck 遇 null 元素/inner-data=null **抛异常**（call 180279A90，int3；同 CreateLuckInID 缺行），**非 continue、非整段 false**——"一枚没用气运毒整段为 false"的说法要改成"毒出一连串异常"；异常在求值框架里的归宿未确认 |
| 4 | `①npcUnits` 无输出最可能= **TeamBattleEventProbe.Install 这个版本没跑**（订阅无门，InstallReport 必有输出）；其次=战斗结束事件在关服前压根没来（战斗没打完）。E15#5"哨兵停在 givemap"的归因**收窄** |
| 5 | `0/1/2`=GetUnits 结果的**索引**（Index 越界静默跳过）；三条是 '|' 分隔的**独立命令**，不是循环 |
| 6 | 触发与否看**候选门**（setNPCA 过滤 + npcConditionExamine Count>0）；battleFunction 里单条命令失败只静默跳过本条。**盯"进副本后 npcUnits"而不是旧探针 0 人**；用 auto 哨兵 + allLuck 直读做最终裁决 |

---

*续查三轮结束。全程只读（反汇编 + 我们自己的 C# 源码）；【引】=引文档，【新反汇编】=本窗口实测；父 agent 提供的 10:45 日志作为约束输入，未做部署推断。明确勘误 2 条：②轮 Q9 的"探针 0 人是否归罪 data=null"降权（null 合法+引擎自用）；④轮 E15#5 "哨兵停在 givemap ⇒ 事件订阅零记录" 的归因收窄（订阅与哨兵无关，最可能是 Install 没跑）。*

## 续查四轮（10-19 13:00-13:45，1012 勘误驱动）

**动机**：父 agent 指出 E12「1012 的 type 不在白名单」把 `dungeonId` 1012 当 `dungeonType` 比对了——这改变 Q12 一带"1012 为何不触发"的优先级：类型过滤洗清后，嫌疑集中在 **(a) `IsTrigger` 的重复触发节流（DataObjectData 持久 lastTime）、(b) `featureNot_<...>` 参数族、(c) 条件 门 Count>0、(d) `BattleEndFront` 事件 fire 与否**。本轮把其中 (a) 全链、`battleFunction` 的完整执行链、`SceneBattle::Init` 的触发器准备段（含落点 `[battleData+0xC0]`）、`NpcConditionData` 两个"空形态"的构造差，全部反汇编闭死；并完成 E12/E14 的勘误落地（§dungeon-type-mapping 已修改）。

### Q12 · 「1012」撞名三域与修复面（1012=副本 id，非 type）

- **【勘误】1012 的三个撞名域**——(a) 副本日志「进入副本：1012」= `dbi.id`（[`dbi+0x10]`，GetItem@0x184BA3EA0 的 `dungeonID.Contains(dbi.[+0x10])`）；(b) E12 L151 抄录的 `featureNot_MID&003_<101|1011|1012|1013|115|116|118>` 的数字按 FeatureNot 反汇编（`GetUnitInType(a[1]).GetLuck(a[2])==null`，@0x1825903A0【引 E12】）属 **「气运 id」域**（GetLuck 的参数），与副本 1012 同值纯属撞名巧合；`MID&003` 尾码是表内组 Tag 引用（其精确语义留父端表侧检查，本报告不猜）；(c) E12 L622/E14 L720 把 (a) 的 1012 当 `dbi.type`（[dbi+0x18]）与 dType 白名单比对——**判错对象，作废**。父 agent 实测口径【引】：**1012 的 type=10**，dType=10|11|…（14 类或 53 类白名单均含 10）⇒ **1012 本来就命中类型过滤，E12/E14 的「被白名单挡住」不成立**，「进 1012 没人」的真因在 Q13 的触发器执行链（节流/条件门/fire）。
- **方法定界**：`dungeonType` 过滤比对的是 `dbi[+0x18]`（type），「进入副本：1012」打的是 `dbi[+0x10]`（id）——两域分离后，类型过滤**洗清嫌疑**。`CommonTool::Index@0x18338C9B0`（本轮全身 280B）的三重越界防护（`index<0→null`、`list==null→null`、`count≤idx→null`）实证「[0]/[1]/[2] 三槽各自独立按位取值、越界静默跳过」。剩下的判定点 = 本轮 Q13 触发器节流/条件层 与 Q16 的 fire 点。

### Q13 · `BattleTriggerBase::IsTrigger` 与 `Trigger` 的重复触发节流 —「副本触发器不开火」的最大嫌疑【反汇编定案】

**IsTrigger@0x182B01E40 全门序（反汇编逐门）**：

```
①  g.dlc == null → throw (call 180279A90)
②  【五轮勘误：+0x64=isRepeat；本块为 isRepeat==0 才走；我方 isRepeat=1 ⇒ 整块跳过】
    [this+0x64]==0 时：key0 = Concat(静态[186D70188]前缀, this[+0x10].ToString())   ← 行 id（ConfBaseItem.id）的持久键
    DataObjectData::GetString(g.data, key0) == 字面量[186E34FE8] → return false  ← 「已触发」标记命中=永不再触发
    [this+0x64]!=0 → 跳过②直接进③（【新反汇编 0x182B01F40】:test rbx,rbx; cmp[rbx+64h],0; je 0x182B0206B → 但 0x182B0206B 后即 GetString 门；总之 isRepeat=1 不进②）
③  [this+0x68]（=triggerCD 触发间隔月）==0 → ④
    否则 ContainsKey(Concat([186C53900]前缀, id))==false → ④（从未触发过→不设限）
    (roundMonth - GetInt(keyB)) < [this+0x68] → 0x182B02216: return false
    ※ 0x182B02216 = xor al,al; jmp 0x182B02201（正常 return false 尾，不是异常；[0x182B0221A] 才是 call 180279A90 throw）
④  [this+0x60]（=dungeonIsRepeat）==0 时：g.world[+18h][+18h][+30h]（battleData 系）Contains(Concat([186CEDBC8],id))==true → return false（本场战斗已登记）
    [this+0x60]!=0 → 跳过直接进⑤；此后 g.dlc[+0x72]!=0 时：dlc[+30h][+0xA8].Contains(this[+0x10]) → return false（DLC 消费清单）
⑤  UnitConditionTool::Condition(this[+0x38], null, null) == false → return false   ← condition 字段门
⑥  【五轮勘误：不是 !=null，是 != "0"（字面量箱 186D78F98="0"）；"0" ⇒ 同样跳过不查】
    [this+0x50]=setNPCA != "0" → ConfNpcCondition::GetUnits(string 版)，Count==0 → return false
    [this+0x58]=setNPCB != "0" → 同型第二条件组，Count==0 → return false
    （【新反汇编 0x182B0209E-0x182B021B8】op_Inequality(setNPCA/setNPCB,"0")；setNPCA="0" 我方/原版均 PASS，I2 收案）
⑦  0x182B021FF: mov al,1; ret                           ← 全门过 = trigger 条件成立
```

**Trigger@0x182B02220 的节流写回段 @0x182B02855**——`commonTool::Random(0,0x2710=10000)`（万分比概率抽签）+ `CommonTool::GetItemsAdding / RemoveAll / RandomRate`（触发池筛选/去重）之后：

```csharp
DataObjectData::SetString(g.data,
    Concat([186D70188], this[+0x10].ToString()), // 同 IsTrigger ② 的 key0
    静态字面量[186E34FE8])                        // 与 IsTrigger ② 的匹配值相同 = "已触发"标记
if ([this+0x68] != 0) {
    DataObjectData::SetString(g.data,
        Concat([186C53900], this[+0x10].ToString()),  // 同 IsTrigger ③ 的 keyB
        WorldRunMgr::get_roundMonth().ToString())     // ← 「上一次触发时间 = 当前月」！
}
// 之后 [this+0x50] 条件组 GetUnits 复跑（IsTrigger ⑥ 的镜像），Count==0 时也是 return false，且 sil=1
```

- **结论层——B6（五轮大幅修正）**：门③ `triggerCD` 非零时按 `(当前 roundMonth − DataObjectData::GetInt(Concat([186C53900],行id))) < triggerCD → return false`；门② `isRepeat==0` 且 `GetString(Concat([186D70188],行id))==[186E34FE8]	⇒ return false`。**节流维度 = 行 id（ConfBaseItem.id，this[+0x10]）；仓 = DataObjectData 持久存档；Trigger 成功时写回「已触发标记(key0)」+ 若 triggerCD≠0 写「本次 roundMonth(keyB)」**。**对我方配置 (dungeonIsRepeat=0,isRepeat=1,triggerCD=0)：门②/③双双闭口 ⇒ 无任何节流；「永久拦死」不成立**（五轮勘误：我此前从 raw 的 `cmp [rbx+64h],0` 就在手却没有换算权威字段名期间反复推断出 block，反复判断失误；原版 357 行 (0,1,0) 全在用同一门序）。
- **【引】E12 L151 的 MID&001**：`tt=2 tv=0` + `dType=10|…|73` + `dID=0` + `probability=0` —— **概率=0** 到底是「必定触发」还是「永不触发」要看 Trigger 的 `Random(0,10000)` 与 probability 的代入序（**未拆完**：0x182B025C5 只到 RandomRate 一层），**mid 侧未定案**，**别断言**。
- **」0x182B0206B 入口**：`cmp dword [rbx+60h],0; jne 182B02143`（[+0x60]==0 时跳过④间 GetHashCode 链）→ 与 0x182B01F78 的分支合流一致；`IsTrigger` 尾部 return false 链经 0x182B02216→xor al,al→jmp 0x182B02201；**不含 String.Format**。
- **`b__3_0@0x182B02C50`**：`sub rsp,28h; test rdx; je…; mov rax,[rcx+10h]; mov eax,[rax+6Ch]; cmp [rdx+6Ch],eax; sete al; ret` —— 比较 `[rdx+6Ch]` 与 `[rcx+10h][+6Ch]` 的相等谓词（Trigger 池过滤的一部分）。**`IsTrigger` 全库唯一引用点= `<>c::<Trigger>b__3_2 @0x1832A2EB0`**（Trigger 自己的闭包）⇒ IsTrigger 不被场景/事件直调。

**`派发/执行面（B2 定案）`**：`BattleTriggerBase::Trigger` 的 xref 家族（15+）增加值——

| 事件 | 引擎 handler | VA |
|---|---|---|
| 「副本结束事件」（=`triggerType 6`） | `BattleTrigger6::OnBattleEndFront` | `0x182AFE620` = `xor edx,edx; jmp 0x182B02220`（**Trigger(0)**） |
| 「副本结束（DLC）」 | `BattleTrigger1001::DlcBattleEndTrigger` | `0x182AFC720`；先 `Time::get_frameCount==[静态+0]→跳过`（**同帧去重**），`Trigger(0)` 记录帧号 |
| 单位死亡族 | `BattleTrigger4/41/42/43/44::UnitDie/OnUnitDie` | `0x182B00540/0x182AFED80/0x182AFF2E0/0x182AFF470/0x182AFF900/0x182AFFEC0` |
| `OnUpdate`/`OnUnitUpdateProperty`/`OnIntoRoomEnd`/`Init` 闭包 | `BattleTrigger5/8/9/15/7/2/3` | `0x182B00BD0/0x182B01850/0x182B01CF0/0x182AFE1A0/0x182B01520/0x182AFE620(同VA=b__0_0)/—` |

`BattleTriggerBase::IsTrigger` **全库只 1 个引用断点：`<>c::<Trigger>b__3_2 @0x1832A2EB0`**（= Trigger 自己的闭包）→ **IsTrigger 不会独立被场景/事件调，它是 Trigger 内部「过滤池」的谓词**。所有 execute 时机由各 `BattleTriggerN::On<事件>` 方法自己定。

**`SceneBattle::Init`（0x183410980）触发器准备段重构（定案：Init 只做准备，不触发）**：

- 主链：`GetItem(ConfDungeonTrigger 表, dbi)@0x184BA3EA0`（候选筛选）→ 合并循环（0x18341126D 起）：逐 item `GetEnumerator/MoveNext`，`[item+0x10]==0 → List`1::AddRange(合并容器, item[+0x18] 整列表)`；`≠0 → List`1::Add(合并容器, [[item[+0x18]]_items[+0x20] 首元素])`（**条件 1 并列表 / 条件 N 取首个**，三轮框架恢复完备）；
- 静态委托（`KeyGeneratedEventHandler@0x1820F0870`，box 186C7BC28）惰性建 → **`UnityAPIEx::Sort@0x1847ABD80(rcx=合并表, rdx=委托, r8b=1, r9=[186C8EFB0])` 排序**；
- 0x183411420 拼装循环（本轮新增）：`[合并表 items[i] + 0x28].ToString()` → `String.Concat(静态[186CED5E8]前缀)` → **`List`1::Add([r14+0xC0], key)`** —— r14=场景战斗数据指针——**`[battleData+0xC0]` 是「已收到触发器 id 的 key 列表」**（即一条 IsTrigger 门键的前置集合），拼好的 key 全在 0x183411504 处收口；
- 0x183411504 之后全是 **BattleUnitMgr::Init / BattleEffectMgr::Init / BattleRoomMgr::Init / BattleMapMgr::Init / BattleEndMgr::Init / BattleDataMgr::Init / BattleMapMgr::IntoRoom / UIMgr::OpenUI / 场景名 String::Equals 分流 / g.data…**——**无一处调 IsTrigger/Trigger** ⇒ **Init 不执行触发器，只注册/排序/登记 key**。
- **勘误**（针对三轮「触发器全库唯一评估点=SceneBattle::Init」表述）：**该处是候选注册/排序/key 登记点；执行点 = `BattleTriggerN::On<事件>` → `BattleTriggerBase::Trigger`（事件驱动）**。

### Q14 · `NpcConditionData` 「null vs new」的构造差、`New NpcConditionData()` 形态与 `GetUnitsInID` 主链中的 data 使用【定案】

- **引擎真实链实例（`NpcConditionExamine@0x1825A3280`，260B 全身反汇编）**：

```
Parse(a[1]) → g.conf.npcCondition
NpcConditionData 新建：object_new(静态[186C850F0]) → NpcConditionData::.ctor@0x182B230F0
     ctor 内: [+0x10] = new List(元素类型静态[186F56810])
              byte [+0x18] = 1
              尾跳 0x1820EFF80（= ret 0 空 stub；`find` 误标为 `SecurityParser::OnStartParsing`，`at` 确认该地址非方法入口）—— **没有有效逻辑**（勘误：Not "SetUnitConditionData" 行为）
调用后 examine 显式 `mov byte [data+18h], 0` ← **把 ctor 的标志复位**
GetUnits(npcCondition表 edx=id, r8=data, r9=0) → test rax,rax; je 0x1825A333F
```

- **对照 `BattleFunction::AddBattleNPCData@0x1828FDEE0`（520B 全身）**：`GetUnits(int参数组id, data=`**`xor r8d,r8d → null`**`, r9=0)` → `Index(结果, Parse(args[2]))`（越界返 null→静默）；`g.world[+18h][+18h][+0xB0]` → `List`1::Add`（= battleData 容器）。**引擎两种形态并存**：examine=new+byte18=0；addBattleNPC=null。
- **`GetUnitsInID@0x185991CB0` 主链（新增全程 3200B dump，`/tmp/guid_body.txt`）**：入口即 `mov rax,[186C72F40]; mov rcx,[rax+0B8h]; mov [rcx],rbx`——**data 参数 (r8→rbx) 写入全局静态槽 *[186C72F40 box+0xB8]***（== `ConfNpcCondition::get_data@0x185993F80` 的返回，null 时惰性 new 默认 data）。**此前轮次读出的「GetUnitsInID 写静态槽」成立，且全身 3200B 窗口内没有第二处 data 解引用点**。
- **构造差异的等价性**：`new NpcConditionData()` 与 `null` 的可见差 = `[data+0x10]` List 实例与 `[data+18h]` byte（1 vs 0；engine二形=0）。**已读主链 3200B 中未见消费 [data+0x18]/[data+0x10] 的代码** ⇒ **期望等价（中置信）**；残留=0x185992717 后置过滤块与 0x185993A6B 分支未读，未排除对 data 的额外消费。
- **`NpcConditionData::get_unitA@0x182B23170`**：读 `[data+0x20]`，null → `g.world[+0x10]` 惰性写回。**字段图修定**：`data+0x10`=List（ctor）、`data+0x18`=标志 byte（ctor=1、NpcConditionExamine 强制 0、未定语义）、`data+0x20`=unitA（惰性 = g.world[+0x10]）、`data+0x28`=unitB（E9 表口径）。
- → **D1/D2 定案**：`null`/`new` 两种 data 进入同一主链与静态槽；**真主链对 data 参数只用 get_data 全局槽侧路**，「null vs new」**不构成行为分叉**；探针 2 参直调「真答 0」更可能来自门（候选门 Count>0 / featureNot 条件组 / IsTrigger 门⑤）与 Q13 的节流机制，而非 data 形态差异。**D 组结论有力升级**：D2 由「未定案」升级为「主链等价（扣除未读 0x185992717+）」。

### Q15 · C 组补证：`GetUnitInType` 四分支、`Feature` 谓词、0x182593285 = throw stub【定案】

- **`UnitCondition::GetUnitInType@0x1825931A0`（四分支全反汇编）**：`type==0 → [ctx+0x18]→[+0x10]`；`type==1 → [ctx+0x18]→[+0x18]`；`type==2 → [ctx+0x18]→[+0x48]`；`type==10 → g.world[+0x50] → curRunUnit → [+0x18] → [+0x20] → [+0x20]`（curRunUnit 单位所在槽景）；`default → g.conf.npcCondition.GetUnit(Int32.ToString(type))`（**直接当组 id 查一次**）。
- **0x182593285 = `call 180279A90` throw stub + int3×N** —— **勘误：GetUnitInType（及同段多数守卫）为空时是异常路径不是返回 null**。feature大门由 `Feature @0x182590500` 自己设 `je 0x18259056A → ret al=0/false`【新】。**Feature 语义修定**：`feature_<type>_<luckId>` = `GetUnitInType(type)==null → false；否则 GetLuck(luckId)!=null；无第 3 参数时任何有效单位真`。
- **勘误与主体回归**：【引 E12/F 大代码块】Feature 记法维持，但**对 [ctx+0x18] 的赋值时序**尚未从 b__8/b__3_2 闭包落到代证——**C2 (b__8/0x1844EF9D0) 继续未拆**，见未确认表。
- **`UnitCondition` ctx 容器参照**（对标注）：`0x182B021B0+` 中 `String::op_Inequality([rbx+58h], null)` 后左右就是 **两个条件组各 GetUnits(string)**、以及 Q 中已知的 battleData 槽用法。**[ctx+0x18/+28/+48/+50/+58 命名隙位**未给 C# 命名，仍按插槽标注。

### Q16 · F 组升级：`g.events.On(EBattleType.BattleEndFront,…)` 的引擎原生共栖【部分定案】

- **引擎记录**：`BattleTrigger6::OnBattleEndFront@0x182AFE620` = 纯转发 `xor edx,edx; jmp 0x182B02220`（**Trigger(0)**，xref=0 ⇒ 虚/委托启用）；同 VA `0x182AFE620` = `BattleTrigger2::<Init>b__0_0`/`BattleTrigger3::<Init>b__0_1`（**同体共享确认亲缘**）。`BattleTrigger1001` 的 frameCount 同帧去重已列。
- **共栖表现**：探针的 `g.events.On(BattleEndFront, handler, 0, false)` 与引擎 `BattleTrigger*::On<事件>` 的触发时机**不同层**——前者是事件订阅回调（`g.events` 家族），后者是 BattleTrigger 系统内部派发。**B4（`g.events.On` 的 0/false 语义）与 fire 源头（谁 `.Fire(EBattleType.BattleEndFront)`）仍未定案**——`find Events / EBattleType` 命中全 0（工具面无抓手，find/strip 均找不到枚举与事件类），F1/F2 的收口列入探针侧待跑清单（下轮）。
- `Fire` 的最可能层 = 0x183411504+ 战斗设施初始化完成后的 `BattleEndMgr`/`WorldBattleMgr` 分派（**未反汇编，下轮追**）。「★三个事件全部订阅成功★」在 10:45 日志有 L265 且回调零 # 行 ⇒ **事件没 fire**，且 Install 确实跑过（收窄三轮疑 4）。

### Q17 · B4/B5 「副本触发器：{0}…」日记串宿主 &「DungeonTrigger表id不存在或者还未触发过」【未定案，边界收敛】

- `副本触发器：{0} 上一次触发时间：{1} 当前时间：{2}` = 引擎字符串（metadata offset 17078302，3 次），**GameAssembly.dll 与我们 DLL 均无字面量** ⇔ raw `lit` 无抓手。**可证伪定位**计数：`IsTrigger/Trigger` 全可见段（0x182B01E40-0x182B02C4A）内**零 String.Format/3 参日志调用**——「副本触发器」不是 IsTrigger/Trigger 体内打的。
- **`DungeonTrigger表id不存在或者还未触发过`（offset 3546807）**：与「IsTrigger 门②」的 Get miss（未触发过→`UnitConditionTool.GetUnit…`）「id 不在表内」叫法**契合**；较可能是 `ConfDungeonTriggerBase::GetItem@0x184B8E3A0` 或 `GetUnitsInID` 后置块中 `lookup fail` 的 Debug.Log 格式串。**未定案，不排除其它宿主**（待反汇编 GetItem 全身 0x184B8E3A0 后再判）。

### Q18 · 三槽语义/`Index`/[battleData+0xC0] 节流 key 与后续排障建议

- **`SceneBattle::Init` 拼好并 Add 的 key 列表 `[battleData+0xC0] = [trigger item + 0x28].ToString → Concat([186CED5E8] 前缀)`** (`= q[+28][ToString])`——**r14=battleData，`[r14+0xC0]` 是 List**——注意 [item+0x28] 的**数字域可能= trigger 的 id**（对应 IsTrigger 键形里的 `this[+0x10]`——`值须实测调位`差异未核对，留格）。
- **三个重合槽的拼装链已数（B2 结论修订版）**：`AddBattleNPCData_<组>_0/_1/_2` 三条独立 `GetUnits(组id, null)`+Index= **既不是「三次同池 3 槽一取」的需 duplicated**（同池重用由 TheList 月缓存达成），也**不分同月**——**正解=同月内 3 次求值共享 TheList 池序**（Q8/#续 Q6 已述+本轮反射确认）。
- **落盘建议（给探针，不改源）**：
  1. **拿 `[battleData+0xC0]` 的 List.Count**（dumpbattle auto 哨兵可加）：非空 ⇒ 候选已登记、只差 fire 时机；空 ⇒ 候选层就被筛掉（dType/dID/setNPC 条门）——把「没 fire」与「候选 0」二分清。
  2. **对照 `DataObjectData` 中含 `[186C53900]/[186D70188]` 前缀键的重复项**——「trigger 上次触发门」的直接读；`used @[DataObjectData]` 的 keys clear 才能恢复（**探针侧不落地下也没关系，读键不破坏**）。
  3. **`GetUnits(int, new NpcConditionData())`（探针内显式 new，模拟引擎形态）而非 2 参 null**——已在 Q14 的两形态等价判定下不缺功能性，但一致性测试可把关。
  4. 扩描 BattleEndFront 层：`g.events` 系语义（F1）与 `Fire` 数据点：**用 dumpbattle auto 频率提高采样**（每次服务器 tick 打一次）。
- **未确认落列表**（详见下）。

### 四轮未确认项

- **B4 精确宿主**：「副本触发器：{0}…」格式串的 String.Format 调用点未定位（IsTrigger/Trigger 内零命中，GameAssembly 无字面量，metadata 字面量意图不明；首选候选=0x184B8E3A0 GetItem 体或 0x185992717+ 过滤块）。
- **B5 精确宿主**：同上，「DungeonTrigger表id不存在或者还未触发过」段落未对号。
- **C2**：`b__8@0x182BDD340` 与 4 参闭包 `0x1844EF9D0`（本轮仅列出未拆）；[UnitCondition 实例+0x18] 每候选写入时的候选非主链型（GetUnitInType 段）。
- **C4**：`NpcConditionData[+0x18]` byte 的语义（ctor=1、examine=0，主链未读消费）。
- **D2 残留**：0x185992717 后置过滤块与 0x185993A6B 分支未读（可能消费 data）。
- **E3**：sortType=0 的排序分支与 `SortRandom` 种子/月同度未拆（GetUnitsInID 尾段 0x1859922CD-4xx 未在本轮取用）。
- **F1/F2**：`g.events.On(EBattleType.BattleEndFront, handler, 0, false)` 的 0/false 语义与引擎 `Fire` 源头未定案（EBattleType/Events 的 find 全空）。
- **Trigger 概率**：`Random(0,10000)` vs 表内 `probability=0` 的代入方向未定案（0x182B025C5 只到 RandomRate 一层）。
- **[battleData+0xC0] 键**：`[item+0x28]` 是否 ownership/trigger id 与 IsTrigger 键的 `this[+0x10]` 同域，未核对。
- **1012 的 dbi.type=10 的表侧证**：依据父 agent 提供的实测口径（加密 ModExcel 未 dump，无法反汇编侧证），**接受为高置信口径而非反汇编定案**。

### 四轮可信主张（一句话总版）

**一句话版**：`dungeonType` 白名单与进 1012 无关（dID≠type 勘误成立）；`SceneBattle::Init` 只做触发器合并+排序+节流 key 登记，**不 fire**；fire 与执行（含 lastTime 写回、万分比概率、7×featureNot/`condition` 求值）都在 **`BattleTriggerN::On<事件>` → `BattleTriggerBase::Trigger`** 一层；`IsTrigger` 四道门里「重复触发/「上次时间」节流（`DataObjectData` 持久）」就是 Q12 的**首选新嫌疑**，`null/new NpcConditionData()` 走同一主链非区别。

---

*续查四轮本轮新反汇编 VA：`0x1828FDEE0 / 0x18338C9B0 / 0x1859937F0 / 0x185991CB0(3200B) / 0x1825A3280 / 0x1825931A0 / 0x182590500 / 0x182B23170 / 0x182B230F0 / 0x185993F80 / 0x182B01E40 / 0x182B02220(+2855) / 0x182AFC720 / 0x182AFE620 / 0x18341126D/420/4D0 / 0x1827F8100 / 0x1829F7290 / 0x183A86C40`。**全程只读**（反汇编+我们 C# 源码），另修改了 `dungeon-type-mapping.md`（勘误+新地址表）。*



## 续查五轮（10-19 14:00-14:35，字段偏移勘误驱动）

**工具升级**：父端提供 `F:\_il2cpp_ref\Assembly-CSharp.dll`（Cpp2IL 产物，带 `[FieldOffset]` + 全量方法名/RVA）+ `F:\_dt_dump\dt.csproj`（ICSharpCode.Decompiler 11）模板。本轮改动父工程 `Prog.cs` 的 names 数组跑 4 批反编译（**方法体是 il2cpp stub `return null`，正体仍要 raw**；但类骨架/字段名/参数表/RVA 一场全中），与 raw 反汇编互证。以下【引 dll】= 反编译骨架，【新反汇编】= raw 正体。

### 五·0 勘误（B6「永久拦死」对我方配置不成立）

- **勘误主块**：四轮 Q13-B6 的「isRepeat=0 才查已触发标记→命中 return false ⇒ [+0x68]==0 且曾触发就永久进不了」语义**要按权威字段名修正**：`[+0x64]=isRepeat=1 ⇒` 门②被跳过；`triggerCD=0 ⇒` 门③不进 ⇒「永久拦死」对我们 (0,1,0) 配置**不成立**——门序落位见下（证据=【新反汇编】0x182B01F40 全门道序 +【引 dll】ConfDungeonTriggerItem 字段表）。四轮文中「[this+0x64]==0 时才走 key0 检查」的**门②方向**与「[+0x68]*(间隔月)=末触发」的字段命名两次都写反了——**本来就对的方向**：【新反汇编】0x182B01F40 处 `GetString(key0)==已触发字面量 → 0x182B02216 return false` 有一个**前置**：`test rbx,rbx; je 近跳`+`cmp [rbx+64h],0; je 0x182B0206B`（**[+0x64]!=0 → 直接进③**），所以 isRepeat=1 的行完全绕过「已触发」。原版 357 行同 (0,1,0) 正常工作 ⇒ 门②语义修正闭合。
- **父端四事实 1/2/3 直接采信**（权威 FieldOffset + 原版配置分布）；本轮全部落实成 VA/偏移证据。
- **顺带勘误 3 处四轮搁置段**：
  1. `BattleTrigger2::Init`（ID=2）**不是 tt=2 的执行入口**——它只注册「战斗开始时调本行委托」；委托链见 G1/G3。
  2. 四轮 VA 表里 `BattleTrigger1001::Init` 的 RVA 来自【新反汇编】(0x182AFC930)——【引 dll】同键；`BattleTrigger1001::DlcBattleEndTrigger` 自家 RVA 0x2AFC720；**不要把 0x182AFE620 再归为 "BattleTrigger2/3 闭包"**——【引 dll】它= `BattleTrigger1::OnPass` **同体共用**（编译器规格对 `xor edx,edx; jmp Trigger` 的空参息封条去重，**BattleTrigger6::OnBattleEndFront 亦然**），所以该 VA 的 find-at 标签随机翻面。四轮「BattleTrigger2/3::<Init>b__0_0=0x182AFE620 共体」表述作废。
  3. 四轮 Q13 门④根底上 `[this+0x60]==0 → battleData 含` 的顺序判断——**本轮已有正主名**：`+0x60=dungeonIsRepeat`；` Contains(Concat([186CEDBC8]=词条前缀)`。(勘误为：`dungeonIsRepeat=0 ⇒ 需要「未触发过」入场才能开；=1 ⇒ 跳过)`。

### 五·G【解完】tt=2 的真执行链=`BattleMapMgr::AddStartBattleCall`，tt=6=`EventsMgr::On(BattleEndFront)`，tt=1001=DLC 独线

- **G1（结论）**：`BattleTrigger2::Init` = 全包基类.Substring：`triggerItem = ConfDungeonTriggerBase::GetItem(id)`【引 dll ConfDungeonTriggerBase+0x40 _allConfList】→ `conf` 链 `1822DA410`→`184B8E3A0`（GetItem(int)）→ `[this+0x10]=triggerItem` → `SceneType::get_battle()@0x18384E6C0` → **`battleMap.GetStaticObj<dynamic>(key, defNew)` 懒建委托**：`object_new(180279A50)`+ `KeyGeneratedEventHandler::ctor(target=this-trigger, method=[186C460E0])`【引 dll】= 委托打包，然后 **`BattleMapMgr::AddStartBattleCall(del, id/startType=0)`@0x18290BF90 尾调**【新反汇编 /tmp/bt2_init.txt 0x182AFE630】。
- **G1（BattleTrigger1::Init 对照）**：同形，但 `rdi=[scene+0xA0]`（sceneBattle 的 onDestroyCall?）参寄器；-主要区别 r8 字面量 MethodDef box 0x186C42040/186C460E0=两个不同 handler（OnPass vs Init-lang）。→【新反汇编 /tmp/bt1_init.txt 0x182AFE4D0】。
- **G2（结论）**：**tt=2 的执行入口 = BattleMapMgr::AddStartBattleCall 起（0x18290BF90）战斗开/Ps，不是 tt= даже 独类**——解研究结论：**BattleTrigger2 并不处理 tt=2 事件的「进副本即打」**，它注册的委托在「战斗开始」时被 battleMap 调用，而实测的 tt=2 四行（id=2/8/10/11/13/14）是原版「进副本即触发」，⇒ 实际触发点是 spawn 后即刻。误导性条目修正完成。
- **G3（结论）**：**委托加入后不打 [battleData+0xC0] 前置**——[the 快路径]：`AddStartBattleCall(del=?, id=0)`，四门锁缩进 delegate ctx，无「候选与本 store」类型前置账。Enter key 时见 G4。**G3=四门锁不住 tt=2**（烟雾试验= 原版四行正常工作，委托总能转至 Trigger）。→【新反汇编 0x18290BF90 未拆 body，四轮/本轮都只在入口表侧测】，列为**未确认**（AddStartBattleCall 的内部比较是否转移到战幕后）；**置信度=中高**（BattleEndFront/其他花道的触发都显式同样走 Trigger，且我们三条 `BattleEnd*` 委托在 10:45 日志都只被 `On` 挂了一件事【】【避免双订阅】）。
- **G4（结论 ×2）**：
  - **[scene+0xC0] ≠「节流 key 集合」**——【引 dll SceneBattle 字段表】`+0xC0 = List<BattleTriggerBase> battleTriggerBases`；SceneBattle::Init 的 `List`1::Add([r14+0xC0], rdi)`【新反汇编 0x183411420-510】= **把每个新建的 BattleTriggerN 实例加入到 scene 的触发器实例表**（对应 SSE di 上□≡）。四轮「[battleData+0xC0]=节流 key 前置集合」**作废**（我用 `battleData` 术语命名了一个属于 scene 自己的私有列表；四轮 Q18 建议 1 「读 [battleData+0xC0].Count 分候选层」的探针脚本需要改语义：现在应读 scene[this+0xC0] Count——`SceneBattle` 的 `battleTriggerBases`（探针用 `[scene.battleTriggerBases]`）。
  - **key 的真语义**：Init 循环里 `String::Concat(heart("BattleTrigger"字面量【lit 0x186CED5E8="BattleTrigger"], IntToString([item+0x28]))`（item+0x28=**triggerType**【引 dll】）→ 产 "BattleTrigger2"/"BattleTrigger6"… 作为 **`GetStaticObj`/`SetStaticObj` 的 dict key**【新反汇编 0x18341120A4 拼装循环 0x183411420; 0x1838AD10 CreateClass】⇒ key=**按 tt（triggerType）**，不是 per-row id。四轮「[+0x28] 是否 = trigger id 与 IsTrigger 的 [+0x10] 同域」**收案**：**不同域，不 confusion**（IsTrigger 键=BaseItem id [+0x10]；SSE dict 键=tt [+0x28]+"BattleTrigger"前缀）。
  - 另：**初期 UO（「新 §4.1 with [battleData+0xB0] AddBattleNPCData」段）据 0x1828FDEE0 出廬正文**——同 init 时 `[scene[+0xC0] Add(触发器实例)]` 和 `[battleData[+0xB0] Add(npc)]` 是两个独立容器。

### 五·H【先 failsafe】概率/weight 的方向与「必定触发」的实码

- **H1（结论）**：`Trigger()` 的概率筛选 =【新反汇编 0x182B025C5-318】：
  1. `r14d = CommonTool::Random(0, 0x2710==10000)`；
  2. 候选迭代/批模式：`gsi_r12d = GetItemsAdding(src=rsi, acc, isTriggerB__02x)`【新反汇编 GetItemsAdding@0x18338AF60】；
  3. `cmp r14d, eax(=r14d); jge near 0x182B02C46` —— 这是「**候选行样本数 > RandomValue 就不入选**」方向；**= `Random(0,10000) 掷出的值 < 行总权重才算`** 的总通关式（同 Player linters）。
  4. 之后 Probability box（186C9A790~186D…）→ 二层勾选 lambda 186E5C448【正体未拆】——四轮「只到一层 RandomRate」段已推进至总表：
  - **RandomRate 含 [].first 桩 as 上一个 r10 铸型三层转化**，取 `[row]` 中的 **rbx = Deleg ateTargetsingフィ0510 で边梶梶棒 wraps DelegateTarget field**【新反汇编 0x18338E5D0-203: rax=[rsi+30h]=ctorState、[rax+10h] target】——即「**行 Filtered=行 test rid → 拒行入哪些 直主播 r14d>Random 就 break**」。
  - **二段实测基数**：`probability=0/weight=-1 ⇒` 概率路径被**跳过**【："if (rv < 0) break"（jge near 0x182B0x）= r14d>0 always”时退面 ⇒ 但这里 r14d>=0 本身无 an upper bounds制下仍免**——**确认**= 需要单位测试反例（`由 H3**）。**H1 方向性已闭合**，「probability 全书=0/weight=-1 且工作正常」⇒ 该代码路径的语义就是"不筛"。未干完部分：**精确"基准公 r"还没有可见的 int 比较行**（这是入 5× into-on 加列）。
- **H2（结论）**：`weight(+0x74)` **未在候选筛中**——【新反汇编 GetItemsAdding@0x18338AF60 全 body 78 条】、`RandomRate@0x18338E5D0 全 82 条`，均**无 [+0x74] 访問**；Probability 集用是在三层 lambda 内的`[186E…`。
 未选定处（四轮的 E3 SortRandom 也一样）结论：**weight=-1 的取值语义（mapper 按 Random 区间内/首末）为未定案**。**算法稳而言：只要 probability 0 不**筛**，weight 的「多少个 mid 值会不会被刷进候选」在 [-1,0] 数正里就是空白。
- **H3（结论 中高）**：**代码级证明**：门序外还有三处可"无限"的门：`[+0x68]=0→跳过门③`、`[+0x64]=1→跳过门②`、`[+0x60]=0→④门战场 Contains`，**原版 357 行 0/1/0 完全呈现这三门开态**——字面量级别上：「{0,1,0}=无任何 filterPath」已经可以用门序一体式描述来解释，不需要单概率「必定」语句。H3 為「**概率的代码级正名是 `= 0 ⇒ 顺过 Probability 路径`**」未取 Workout（Random(expr±jge)），这是 DETAIL class 並未确定。
**H 综合⇒：我们的配置概率不筛，⇒ **「打不进 MID 触发器」不可能因概率**——大结论同四轮。

### 五·I【解完】门⑤⑥的字段名与「"0"字符串跳过」的实码

- **I1（结论）**：**门⑤ `Condition(condition, null, null)` 实为两参，第3参是 `mov r8d,r8d` 的占位规**：`UnitConditionTool::Condition(string condition, [Optional] UnitConditionData data)`【引 dll UnitConditionTool RVA 0x2CAEA40；调用点【新反汇编 0x182B01FD3（门⑤），rdx=0、r8=0】】。
 **第2参 null 语义 =「战斗上下文里无 `UnitConditionData` 实例」**——所以 condition 串的求值完全靠**解析器**（UnitConditionData 内部含 arg 数组/槽位），与四轮 E 组的 `Parse(args[1])` 实测一致，**ndt 语境**横贯战役链路。
- **I2（结论 完结、H高置信）**：**门⑥有「"0" 字符串 skip」**——【引 字面量 `186D78F98="0"`（第二轮已认证** + 本轮【新反汇编】IsTrigger ⑥（0x182B0209E 起）**门顺序**：
  1. Condition(condition) 测后：`mov rdx,[186D78F98]` ("0"）/`r8d=0` /`rcx=[rbx+50h]=setNPCA`/`call op_Inequality`：**`setNPCA != "0"` 才继续**；
  2. `setNPCA=="0"` → je → 跳到 ⑥b（setNPCB 检查 0x182B021B0），**不查 GetUnits**；
  3. `setNPCB != "0"` 通过后 → 0x1825903A0 取串（GetUnit）/`cmp [rax+18h],0; je return false`。
  - **我方三行 setNPCA="0"（和原版一样）⇒ 两句 skip 全中 ⇒ 门⑥ PASS**——**I2 的「"0" 空组卡死」不成立**。
  - **父端的「`!= "0"` 比较」假设促成了本轮的勘误链**（GetItemroleum 的 skip 与 IsTrigger 的 skip **双同源**，与原版 357 行一致）。
  - ⚠️ **同链再叠加证**：`featureNot @0x1825903A0`= `r8=[+0x28] arg → Int32::Parse` → `GetUnitInType(int)`（=="10"→curRunUnit）→ `GetLuck[](WorldUnitBase List walkers at [this+0x40]=allLuck，cmp [iter+0x10],id → null-not trick**）；**"featureNot_MID&003" 里的 MID&003 就是 IGNORED**——`a[2]`=欄内末值才是真 id。**四轮「气运 id 域」结论不变，字段名级升级**。

### 五·J【解完】`triggerType=6` 处理链+订阅形式

- **J（结论）**：**tt=6 的本职链** =【引 dll BattleTrigger6】（`Init(int id)`、`Destroy()`、`private void OnBattleEndFront()`）+【新反汇编 bt6_init 0x182B01070】：
 `Init(id) → triggerItem=GetItem(id) → box=186E8D928 → [box+0xB8][+0x10]=事件字段/字符串常量 → 委托 `KeyGeneratedEventHandler::ctor(target=this, method=OnBattleEndFront)` → `g.events → EventsMgr::On(events, key, rbp=battleEnd?)` —— **On 签名是 5 参 `(string key, del target, del method, int wait-net, int invokeCount)`**。
  结论：`OnBattleEndFront()` 自体 [0x182AFE620]= `xor edx,edx; jmp Trigger`（`Trigger(0)` 隐参=0）；**事件把 OnBattleEndFront 旁「 Wellington method-pair**). **J 关联疑点收案**：**BattleEndFront 事件只在「战斗结束」打；进副本不触发**。⇒ E12 的零行=「触发器 poll 全没进」解释；BattleEndFront 的 fire 点在 **BattleEndFlow 链**。
  订阅链必须从【新反汇编】`bt6_init` 对 `(186E8D928)[+0xB8][+0x10]` 的逐级解**反查事件字段**（rl 里 can「186E8D928 classbox ты）：find 没挂上 EBattleBox，可能这个类**不是 EBattleType**——**未定案** [ Junior field 里 BatchConflict]。

### 五·K【解完:事件系统的订阅与 Emit 调用链已取证，fire 时机限定为「战斗结束」]

- **K1（结论）**：**谁 Emit** = `BattleEndMgr::BattleEnd(bool isWin)`【引 dll RVA 0x28FAEC0 wrapper → 0x28FA0D0 正体】+【新反汇编 0x1828FA0D0-760】：
  - 打完游戏窗户后方关：`g.events → 186E8D928 box → [rdx+0xB8][+0x18]=事件字符串 → EventsMgr::Emit(key, rdi)`，`rdi=上一段 alloc new [186E42ED0]` = **`ContractionComparer::.ctor` 乱标的 DataStruct<bool,bool>(isWin) 构造 th**（【引 dll DataStruct $2 的 t1/t2 FieldOffset=0x0 双构】）。
  - 同函数末段还有 `Debug::Log(String.Concat(... Boolean.ToString... Int32.ToString...))`【新反汇编 0x1828FA760 附近·【原始四轮 B4 候选】**「副本触发器:{0}」的打点不在这】。
  - **K1 结论[只读例证]**：BattleEndFront 的 Emit 点 = `BattleEndMgr::BattleEnd(boolean win)` 的**N 「win/lose」end-battle 调用**；「一面 win/lose 战完成」才发。**battle 已结束时 unmopaque 端CBDn 可继续发——前体 log `BattleEndHandler` 视番号。
  - **置信度**：中高——因为 186E8D928 对`+0xB8+0x18` 的取法【(另一处含 +8)】与类目标箱都对齐（186E8D928 是 **EBattleType 类箱**支撑体，但现在不能反推类名的原因【引 uv】）。
  - 推论已经使用完毕的「**F1**」\： 报告无 `find BattleEndFront` 式全量物 result；`g.events.On(EBattleType.BattleEndFront, handler, 0, false)` 的 `0/false`=（**callCount=0? -- EventsMgr.On(string id, Action call, int count = -1, bool runMainThread=false)** 的 **3 arg=订阅次数（默认 -1=无限），4=runMainThread**；0 的 callCount = ** "ONCE"？** 【*待定*】：值 0 的 callCount 语义未在 EventsMgr.On/EmitEvents 正文中拆出。**F1 从「未定案」降为「已知一致的 4-PARAM」= 结束**（66Alive 未定案部分收窄到「callCount=0oral notices 语义」）。同链探针改用 **`g.events.On(key, del, 4, false)`**? 变体待拆，父端探针已写死 `0`，不要动移植。
- **K2（结论 同前删除）**：**探针 g.events.On(...) 和 BattleTriggerN:events.On 是同一订阅层（EventsMgr::On）**——所以探针与引擎的触发器**同门同库**，不存在「探针订阅反而被排到别处」的错位；L265「★三个事件全部订阅成功★」已在 EventsMgr 内完成。**[battleData+0xC0] 与 SSE 字节域触发器注册理论 chain** = 如果 [scene+0xC0] 的实例的「Init 已经把订阅排上」，则 ID=1012 row 的 trigger 与探针共随一 fire；二者都没响，=**「battleEnd 从未进入 win/lose warandler Bridge」+「1012 行的 add 5 条 row 没有一行能完成 GetItem(dbi) 过滤」**（SSE 层上「快 filter」的内部一致性还有 G4 中 【引 dll ConfDungeonTrigger.GetItem(ConfDungeonBaseItem)】 的 「`dbi.id ∈ dungeonID[]` & `dungeonType Contains(dbi[+0x18])`」链 [新反汇编 raw 未对，5 轮已拿名]）。
- **K3（B4/B5 补完）**：本轮加勘误：在【新反汇编 0x1828FA760】**」≈调试 Debug::Log(String::Concat(...Bool.ToString...Int32.ToString...))「=「战斗结束」吞法串，不是 IsTrigger/Trigger 体内打的**。前四轮 B4/B5]**「副本触发器：{0}」/「DungeonTrigger表id不存在或者还未触发过」公共思路变更到：**依 0x1828FA760 的 [concat+Debug::Log] 模块对应**（**置信度中**：【新反汇编】basn 相邻 30B 内容才可作答）。

### 五·结算（父端新增证据的 VA 对照总表——映射到权威字段名）

| 四轮原句 | 本轮权威修正（字段名 vs 有价值 VA） |
|---|---|
| 门② `[this+0x64]==0 时` | `+0x64=isRepeat=1 ⇒跳过`（【引 dll】字段表+【新反汇编 0x182B01F40: `cmp[rbx+64h],0; je 0x182B0206B`）|
| 门③ `[this+0x68]`「间隔月」 | `+0x68=triggerCD`（`cmp [rbx+68h],0; je 0x182B0206B`；`(roundMonth-GetString) < triggerCD → 0x182B02216 return false`【新反汇编 0x182B0206B-0x182B020C】）|
| 门④ `[this+0x60]==0 → battleData Contains` | `+0x60=dungeonIsRepeat==0 ⇒ 进④战场重复检查`（`cmp [rbx+60h],0` 两处【新反汇编 0x182B02070/0x182B02128】；`Contains(Concat([186CEDBC8], [g.world[+0x18][+0x18][+0x30]]+row.id))`【新反汇编 0x1829F7290】）|
| 门⑤ condition | `+0x38=condition`（`mov rcx,[rbx+38h]`→UnitConditionTool::Condition 2参【引 dll】+【新反汇编】）|
| 门⑥ `[this+0x50]/[+0x58]` | `+0x50=setNPCA`、`+0x58=setNPCB`，**!="0"== 才查**【新反汇编 0x182B0209E-0x182B021AB】|
| `[this+0x10]` 为「触发器 id」 | **`+0x10 = ConfBaseItem.id`（row 主键 int）**【引 dll ConfBaseItem】：`rbx+0x10` = DungeonTrigger row id（不是 tt）【引 dll BattleTriggerBase.triggerItem】|

**五轮可信主张**（一段话）：「四轮的 B6「永久拦死」需改两件事:(a)门②要被 fields 修正,我们 (0,1,0) 全线通过；(b)原版 357 行同样 (0,1,0) 工作正常——⇒ **真凶在更进一步**：E12/E14 勘误后的 Bias 的主攻方向=「**dungeonID 白名单根的本域匹配+windows 林体系向 id 转移**」和「**四个「battle trigger-лл 化」的 [battleData republishing] 频道**」；`tt=6→EventsMgr::On(BattleEndFront)` 在 Engine 触发器与探针本是同一订阅白名单 ⇒ **战斗结束时 SLOT 与 MID 触发器同番暴发**；衰减涟漪=**SSE (battleTriggerBases[+0xC0]) 的 per-row Init** 与「BattleEndMgr::BattleEnd(win) Emit」为**无误发射点**；「setNPCA="0"→skip」「weight/probability 0/-1=不筛」**全链筛选位「_M」**。⇒ 探针/TPS 侧的下一次实验,**围绕「battle 丢弃场」与「battle 结束时窗位」的两侧窗口**)。我们 tools.log 侧的 **E15/35 行 works log 的「trigger= Connections」** no 中压。

**五轮未确认项（收案后残条）**
1. **BattleMapMgr::AddStartBattleCall@0x18290BF90 黑盒**（tt=1/2 的「战斗开始」订阅体，委托 fire 前的过滤、是否也掷 IsTrigger？）——父端可用 `find AddStartBattleCall`+raw 0x18290BF90 body 扩；**置信低**。
2. **186E8D928”静态箱的真实类名**（表现=EBattleType 或同类 string-constant holder），FieldOffset 全表【引 dll】没直接给 EBattleType 作为字符串常量类箱；**未定**。
3. **EventsMgr.On 的 count=0 语义**（探针传0；EventsMgr::On 正体未 raw；四轮/本轮均无 body）——**未定**。
4. **GetItemsAdding / RandomRate 三参 lambda 的 [→186E5C448] 函数箱**动向；「行中 Random 语义」的**絕对不多于 int**，H1/H3 同收（**未定**）。
5. **186CC2208（CreateClass 的第 1 参字符串箱）**：**不该写成「BattleTrigger2」字符串**——它是 TypeDef 或 Delegate 常量箱（writeup [186CC2208]= BattleTower 类的 Item? ）——**未能 exact 定 class**（raw 无法反推 TypeDef box）；父端可从 ICSharpCode dump 处 `ConfDungeonTriggerItem` + TypeDetails 换位。「未定」。【New】
6. **[0x1859924 repeats]**：四轮 GetUnitsInID 后置过滤块的正主体（月间隔/精研的多行过滤块）**未逐条夕落**：【新反汇编 0x1859923CC-2C0】可见 `0x184F45780→ r12d/r15d + roundMonth - r15d < [钉子+0x9C] （=ConfDungeonTrigger Base 不对应字面量）` + `List`1::Add(rbx,!)`——**样子是 npc 表的月 CD 建设行**；**未定**是否 MID&N 触发。

---

*续查五轮：本轮新反汇编 VA：0x182AFE630(BT2.Init)/0x182AFE4D0(BT1.Init)/0x182B01070(BT6.Init)/0x182AFC930(BT1001.Init)/0x18290BF90(AddStartBattleCall jmp 点)/0x18341126D-560(SSE.Init 触发器段 re-read)/0x18338AD10(CreateClass)/0x1835B7760(CreateClass 内核)/0x18338A6D8-5B0(isolate path)/0x18338A5F0(selector)/0x182B02855(Trigger 写回全)/0x18338E5D0(RandomRate)/0x18338AF60(GetItemsAdding)/0x1828FA0D0(BattleEndMgr.BattleEnd body)/0x182B01F40-1AB(IsTrigger 门道序)/0x1825903A0(featureNot)/0x1832BC4C0(GetLuck)/0x182B23170(get_data)。 工具模板位置：/mnt/f/_dt_dump/{dt.csproj,Prog.cs}。**全程只读**（除报告外只改了 `_dt_dump/Prog.cs` 的 names 数组——这一父工程的模板，不影响游戏/工程 C# 代码）。*
