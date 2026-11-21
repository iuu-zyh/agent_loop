"""team 组队工具契约（10-17 新增）

**为什么单独一个文件**：组队是**唯一**会往单位身上写「气运」的工具，而气运 ID 是
**导出时才展开**的（`MID&51` → `excelMID + 51`），excelMID 又由官方编辑器随机分配。
这条链上任何一处写死或写错都不会在编译期/测试期报错，只会让组队静默失效 ——
与 `test_team_multi_probe_contract.py`（探针）是同一类风险，但对象是**产品代码**。

钉住这几件事：

  A. 两处接线都在：`switch` 分发 + `ActionWatcher` 动作清单
  B. 气运 ID **绝不硬编码**（必须按名反查）
  C. 反查走的是**已验证**的那条路（`GetItem(id)` 区间探测），不是已知失败的枚举路径
  D. 三条挂载策略 + 读回复查（"没抛异常"不算成功）
  E. 摘除用 `DestroyLuck`，不许出现 `RemoveLuck`（引擎没有这个 API）
  F. 玩家判定用 `unitID` 相等（引用判等在 IL2CPP 上不可靠 —— 实测把玩家挂上过气运）
  G. 队友上限与配置表里的容器槽数一致
  H. schema ↔ 渲染层键对账
  I. 邀请/解散必须弹窗（玩家有拒绝权），status 不弹窗
"""
from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"
TEAM = CS / "tools" / "TeamTool.cs"
EXEC = _cs_tools.TOOL_SRC
WATCHER = CS / "tools" / "ActionWatcher.cs"
MODIDS = CS / "game" / "ModIds.cs"
SCHEMAS = ROOT / "tools" / "schemas.py"
RENDER = ROOT / "tools" / "text_render.py"
UNIT_REFS = ROOT / "tools" / "unit_refs.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(p: Path) -> str:
    """剥掉 `//` 行注释。

    注释里**允许**提到已被删除的 API 名（那是留给后来者的证据与警告），
    所以"某符号不许存在"这类扫描必须只看真代码 —— 否则就把注释留痕也一起禁了，
    结果是没人敢写"这里原来是什么、为什么删"。
    """
    src = _read(p)
    return "\n".join(ln[: ln.find("//")] if "//" in ln else ln for ln in src.splitlines())


# ---------------------------------------------------------------- A. 接线

def test_tool_is_dispatched():
    """`switch` 里没有它 = 模型调了直接吃 `unknown tool`。"""
    src = _read(EXEC)
    assert re.search(r'case\s+"team"\s*:\s*res\s*=\s*Team\s*\(\s*args\s*\)', src), \
        "分发表没接 team（模型调用会返回 unknown tool）"
    assert re.search(r"\sJObject\s+Team\s*\(\s*JObject\s+args\s*\)", src), \
        "Team(args) 方法不存在"


def test_tool_is_in_action_watcher():
    """漏在 `ActionWatcher.ActionTools` 之外 = 组队成功后不自动开对话窗（玩家屏幕上没动静）。

    这是 `trade` 踩过的同类错误（见 test_tool_group_consistency.py 文件头）。
    """
    src = _read(WATCHER)
    m = re.search(r"ActionTools\s*=\s*new\s+HashSet<string>\([^)]*\)\s*\{(.*?)\}", src, re.S)
    assert m, "找不到 ActionWatcher.ActionTools 定义"
    assert '"team"' in m.group(1), \
        "ActionWatcher.ActionTools 没登记 team —— 组队后不会自动打开对话 UI"


def test_schema_registered():
    src = _read(SCHEMAS)
    assert '"team"' in src, "schemas.py 里没有 team"
    for op in ("invite", "dismiss", "status"):
        assert f'"{op}"' in src, f"schema 缺 op 取值 {op}"


# ---------------------------------------------------------------- B/C. 气运 ID

def test_luck_id_is_not_hardcoded():
    """气运 ID = `excelMID + 偏移`，excelMID 由编辑器随机分配 ⇒ 写死就是错。

    注意：`ModIds.Mod` 是**允许**的（那是本 mod 的私有段基址，代码里必须有一份），
    但不许出现「展开后的具体气运整数」（形如 -803158450 这种算出来的值）。
    """
    src = _read(TEAM)
    # 不许出现把气运偏移算成字面量的痕迹
    assert not re.search(r"-\s*803\s*158\s*4[5-9]\d", src), \
        "TeamTool 里出现了展开后的气运整数 —— 必须按名反查，不许硬编码"
    # 11-16 用户拍板：删掉按名反查（"ID 不可能变，没必要遍历气运表"）。
    # 判据改成"按已知偏移取"，防线在 test_luck_offsets_match_config_table（开发期对 xlsx）。
    assert "FindLuckIdByName" not in src, "按名反查又回来了（11-16 已删，理由见 SlotLuckId 注释）"
    assert "ModIds.LuckTeam" in src, "没有走 ModIds 的已知偏移"
    assert "LuckMateName" in src, "队友气运名常量缺失"


def test_luck_lookup_uses_verified_path():
    """反查必须走 `GetItem(id)` 区间探测。

    **不能用枚举 `_allConfList`**：Il2Cpp 的 `List<T>` 不实现 `IList<object>`，
    探针实测能读到 `Count=1372` 却取不到元素（主路径至今失败，靠次路径救场）。
    这条测试防的是"以后有人觉得枚举更优雅又改回去"。
    """
    src = _read(TEAM)
    # 按名反查已删（11-16）：现在解析走 `SlotLuckId` 的 `GetItem(已知偏移)`。
    # 这条守住"不许退回枚举 `_allConfList`"（Il2Cpp 集合索引在真机取不到元素）。
    body = src[src.index("internal static int SlotLuckId("):]
    body = body[: body.index("\n        /// <summary>")] if "\n        /// <summary>" in body else body
    assert "GetItem(" in body, "解析没有用 GetItem(int)"
    assert "_allConfList" not in body, \
        "反查改回了枚举 _allConfList —— 那条路在真机上失败过（Il2Cpp 集合索引不可用）"


def test_luck_name_is_expanded_before_translate():
    """mod 条目的 name 是**未展开**的 key（`xxxMID&001`），必须展开偏移后过 `GameTool.LS`。"""
    src = _read(TEAM)
    # 按名反查删掉后，"把未展开的 key 翻成中文"整条链（ExpandMid + GameTool.LS）一并退役
    # —— 那正是 75ms 卡顿的来源。判据改成"气运 id 全部来自 ModIds 的偏移常量"。
    assert "ExpandMid" not in src, "按名反查的 MID& 展开又回来了（11-16 已删）"
    for k in ("ModIds.LuckTeam", "ModIds.LuckHurt", "ModIds.LuckSelf"):
        assert k in src, "气运 id 没有走 " + k


# ---------------------------------------------------------------- D. 挂载策略

def test_luck_creation_tries_multiple_strategies():
    """三条策略都要在：引擎入口 → 属性层待加队列 → 直塞 addLuck。

    历史：早期 #1 走 `CreateLuckInID(...).luckData`，实测是 null（NullReferenceException），
    只有 #2 能"读回来"—— 但 #2 只建运行时对象，**面板看不见**（见下一条）。
    现在三条都指向"能进 addLuck"的路径。
    """
    src = _read(TEAM)
    assert "u.CreateLuck(" in src, "缺策略 #1（引擎正规入口）"
    assert "AddAddLuck" in src, "缺策略 #2（属性层待加队列，面板读的就是它）"
    assert "propertyData.addLuck" in src, "缺策略 #3（直塞 addLuck 兜底）"


def test_luck_creation_verifies_both_layers():
    """★ 成功判据必须**两层都过**：`addLuck`（面板可见）+ `GetLuck`（条件组可见）。★

    时间线：
    · 10-18 面板事故：旧版拿 `GetLuck(id) != null` 当判据 —— 而 `CreateLuck(data)`
      **只建运行时对象、不写 addLuck**。于是工具报成功、NPC 面板一片空白、读档就丢。
    · 10-19 反向事故：AddLuck 旧判据只看 `InAddLuck`（存档/面板层），任何一条策略把
      addLuck 塞进去就算成功 —— 运行时层是否真进**从未被验证**。用户当场抓到的就是
      "面板里（张票/方成和）看得见气运，进副本（GetUnits 读运行时层）却 0 人"。

    所以现在的正解是：二层都要验证（用户要求"不确定就查清楚"，两层口径写死）
    —— 缺任何一层都不许报成功，err 里明说哪层缺。
    """
    src = _read(TEAM)
    body = src[src.index("internal static bool AddLuck"):]
    body = body[: body.index("internal static bool RemoveLuck")]
    assert "InAddLuck" in body, "AddLuck 没有用 InAddLuck 验存档/面板层"
    assert body.count("u.GetLuck(luckId)") >= 2, \
        "AddLuck 的新口径要求运行时层(GetLuck)也验证（先前只验面板层打了假成功）"
    assert "两层验证失败" in body, "失败时要明说缺的是哪一层"
    assert "runtimeOk && panelOk" in body, "两层都进才算成功"
    # 判据本体必须真的去读 addLuck 列表（切片：InAddLuck 的方法身——从偏移 10 起找到
    # 下一个方法自己的 `/// <summary>` 注释头为止，RemoveLuck 的注释不许裹进来）
    helper = src[src.index("internal static bool InAddLuck"):]
    helper = helper[: 10 + helper.index("/// <summary>", 10)]
    assert "propertyData.addLuck" in helper, "InAddLuck 没有读 propertyData.addLuck"
    assert "GetLuck" not in helper, "InAddLuck 退回了 GetLuck —— 那正是踩过的坑"


def test_luck_layer_state_helper_exists():
    """逐人分层诊断（面板=✅ 但运行时=❌ 的假成功必须能被一眼看出）。"""
    src = _read(TEAM)
    assert "internal static string LuckLayerState(" in src, "缺 LuckLayerState 助手"


def test_luck_removal_uses_destroy():
    """引擎里是 `DestroyLuck(WorldUnitLuckBase)`，**没有** `RemoveLuck`（早期按猜的名字调，编译期就挂）。"""
    src = _read(TEAM)
    assert "DestroyLuck" in src, "摘气运用错了 API"
    assert not re.search(r"\.RemoveLuck\s*\(", src), \
        "出现了 RemoveLuck —— 引擎没有这个 API（会编译失败）"


# ---------------------------------------------------------------- F. 玩家判定

def test_player_detection_uses_unit_id():
    """判据必须是 `unitID` 相等。

    引用判等（`u == player`）在 IL2CPP 包装器上**不可靠** —— 探针实测把「结伴而行」
    挂到了玩家自己身上（日志 `已挂气运=1 [唐炎(15,2)]`，唐炎就是玩家）。
    `UnitSnapshot` 文件头写明：解析同一个人的判据是 unitID 相等。
    """
    src = _read(TEAM)
    body = src[src.index("internal static bool IsPlayer"):]
    body = body[: body.index("internal static string NameOf")]
    assert "unitID" in body, "IsPlayer 没有用 unitID 判等"
    assert "Pointer" in body, "缺少 Pointer 兜底"


# ---------------------------------------------------------------- G. 上限一致

def test_max_members_matches_config_table():
    """代码里的上限必须与 `DungeonTrigger` 里写的容器槽数一致。

    多写了槽位 = 配置表引用了不存在的队友（可能报错）；
    少写了 = 拉了 3 个人却只进来 2 个（静默，最难查）。
    """
    src = _read(TEAM)
    m = re.search(r"MaxMembers\s*=\s*(\d+)", src)
    assert m, "找不到 MaxMembers"
    n = int(m.group(1))
    assert n == 3, f"当前定为 3 人（与 DungeonTrigger 的槽 0/1/2 对齐），实际 {n}"

    # 配置表里确实写了 0/1/2 三个槽
    from openpyxl import load_workbook
    p = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/DungeonTrigger.xlsx")
    if not p.exists():
        return                      # 非作者机（没有 mod 工程）时跳过
    ws = load_workbook(p, data_only=True)["DungeonTrigger"]
    rows = list(ws.iter_rows(values_only=True))
    cn = list(rows[0])
    idx = cn.index("触发副本作用")
    slots = set()
    for r in rows[3:]:
        if not r or not r[0] or str(r[0]) == "注释":
            continue
        for part in str(r[idx] or "").split("|"):
            mm = re.search(r"addBattleNPCData_\S+_(\d+)$", part)
            if mm:
                slots.add(int(mm.group(1)))
    # ── 出人方式：`addBattleUnit_<NpcConditionID>&N`（原版 22201 的多人生成写法）──
    #
    # ★ 10-18 更正 ★ 本测试原来断言"必须用 setNPCA + addBattleUnit_3/4/5"，
    #   那是基于一次**归因错误**的结论（把克隆人怪到 addBattleUnit 没索引，实际是
    #   条件组 save=0）。事实是：
    #     · `setNPCA` 在全部已装 mod 里使用次数 = 0；原版 76 条全是纯数字 ID，
    #       且原版没有任何 `tt=13 + dungeonType=0 + dungeonID=0` 的条目 ⇒ 该组合零先例；
    #     · 真正有先例且**真的能跑**的是 `addBattleNPCData_<组>_<槽>`
    #       + `addBattleUnit_<组ID>[&N]_...`（原版 133/134 与组队 mod 同款）；
    #     · 一次出多人的范本是原版 `id=22201`：`addBattleUnit_100801&0..&4`，
    #       配合 `NpcCondition 100801`（5 段 + save=1）。
    #   所以这里改为校验**真正决定人数的那件事**：索引/位置覆盖到 MaxMembers。
    #
    # ★ 10-18 晚 最终方案（实测通过）★
    #   出人方式是 **`addBattleNPCData_<组>_0/_1/_2` + `addBattleUnit_3/4/5`**，
    #   完全不用双 `&`。链路（反汇编确认是同一块内存）：
    #     写入 g.world→[+18h]→[+18h]→[+0B0h] → List::Add   （addBattleNPCData）
    #     读取 g.world→[+18h]→[+18h]→[+0B0h] → Index(id-3)（GetUnitWorld 的 3..10 分支）
    #   之所以不用 `addBattleUnit_MID&003&0`：`MID&003` 导出后是**纯整数
    #   `-803158448`，不含 `&`**（运行时快照实测），双 `&` 在导出时如何切分未知，
    #   原版 386 条里双 `&` 出现 0 次。
    # 有两条互斥的出人写法，各自有各自的「从 0 开始」规则：
    #   ① `addBattleUnit_<组ID>&N` —— `&N` 是条件组列表下标，必须从 0 起（原版 22201）
    #   ② `addBattleUnit_<3..10>`  —— 裸数字是「列表位置 id-3」，3 等价于下标 0
    #      （反汇编 `GetUnitWorld`：`lea edx,[rdi-3]` → `Index(arr, id-3)`）
    idx_units = set()    # 写法①：&N
    pos_units = set()    # 写法②：裸数字位置
    for r in rows[3:]:
        if not r or not r[0] or str(r[0]) == "注释":
            continue
        for part in str(r[idx] or "").split("|"):
            mu = re.search(r"addBattleUnit_\S+?&(\d+)_", part)   # 带索引的
            if mu:
                idx_units.add(int(mu.group(1)))
                continue
            mu2 = re.search(r"addBattleUnit_([3-9]|10)_", part)    # 裸数字位置写法
            if mu2:
                pos_units.add(int(mu2.group(1)))
    assert idx_units or pos_units, "DungeonTrigger 里没有 addBattleUnit —— 队友进不了副本"
    if idx_units:
        assert len(idx_units) >= n, (
            f"`&N` 只覆盖了索引 {sorted(idx_units)}，但 MaxMembers={n}")
        assert 0 in idx_units, "写 `&N` 时缺 `&0`（第 1 个队友）—— 下标应从 0 开始"
    else:
        assert len(pos_units) >= n, (
            f"裸数字位置只覆盖了 {sorted(pos_units)}，但 MaxMembers={n} "
            f"—— 第 {n} 个队友不会上场")
        assert 3 in pos_units, (
            "裸数字位置写法缺 `3`（= 列表下标 0，第 1 个队友） "
            "—— GetUnitWorld 里是 Index(arr, id-3)")

    # ── 条件组必须够段数（save 只影响存档复用，不影响人数）──
    nc = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/NpcCondition.xlsx")
    if nc.exists():
        ws2 = load_workbook(nc, data_only=True)["NpcCondition"]
        r2 = list(ws2.iter_rows(values_only=True))
        cn2 = [str(c) if c is not None else "" for c in r2[0]]
        ci = {h: k for k, h in enumerate(cn2)}
        best_segs, best_save = 0, None
        for row in r2[3:]:
            if not row or not row[0] or str(row[0]) == "注释":
                continue
            segs = sum(
                1 for k in range(1, 6)
                if ci.get(f"条件{k}") is not None
                and str(row[ci[f"条件{k}"]] or "0") not in ("0", "", "None"))
            if segs > best_segs:
                best_segs = segs
                best_save = str(row[ci["是否储存"]] or "0")
        assert best_segs >= n, (
            f"最长条件组只有 {best_segs} 段，但 MaxMembers={n} "
            f"—— 一段产出一个人，段数不够就抓不到 {n} 个人")
        # ★ 10-18 深夜 真机日志定案 ★
        #   本测试曾断言「多人条件组必须 save=1」（原版 100801/100821/100401 都是 save=1），
        #   后来真机跑出 3 个队友就以为 save=0 也行 —— **那个结论是错的**，
        #   当时只是"碰巧 3 段抽中了 3 个不同的人"。
        #
        #   真机日志 `[TeamBattleEvent] 名单三对比`：
        #       ② 气运持有者 = 方成和、温芸、祁曼荷、张票、江宇文   （5 人）
        #       ① 实际进副本 = 方成和、温芸、方成和                   （有人重复！）
        #
        #   反汇编 `ConfNpcCondition::GetUnitsInID` 确证两件事：
        #     · **没有去重** —— 尾部只有 `list.RemoveAll(u => u == null)`；
        #     · `sortType=0` 走 `CommonTool.SortRandom` ⇒ 每段独立随机采样。
        #   所以「三个一模一样的条件」= 从同一个池子里**有放回**抽三次，
        #   5 人池里抽 3 次互不相同的概率只有 48%。
        #   ⇒ 唯一稳的办法：**每段筛一枚不同的气运**（一槽一枚），
        #     每段候选集恰好 1 人，引擎怎么随机都只能是那一个。
        #
        #   save 只决定「结果是否存进存档复用」，与人数、重复都无关。
        assert best_save in ("0", "1"), (
            f"条件组 save={best_save} 不是合法值（应为 0 或 1）")


def test_npc_condition_segments_use_distinct_lucks():
    """`NpcCondition` 每段必须筛**不同**的槽位气运 —— 10-19 续查二轮定案恢复。

    证据链（docs/archive/getunits-luck-eval-report.md 续查二轮）：
    · battle 主链（addBattleNPCData → GetUnits(int,·)）**空槽=静默跳槽、不清零**
      ⇒ 异段结构"漏挂一枚"只会少带人，不会全灭（上午的 A/B 回退基于错误归纳，已撤）；
    · 去重不存在（E11：`[0]方成和 [1]温芸 [2]方成和` 有放回）⇒ 只有异段
      "每段候选集恰好 1 人"才结构防重；三段同 luck 会重现 E11 型重复。
    """
    p = Path("/mnt/f/mod/ModProject_Jgmg5L/Project/ModExcel/NpcCondition.xlsx")
    if not p.exists():                                    # 兼容真实路径
        p = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/NpcCondition.xlsx")
    if not p.exists():
        return
    from openpyxl import load_workbook
    src = _read(TEAM)
    m = re.search(r"MaxMembers\s*=\s*(\d+)", src)
    n = int(m.group(1))

    ws = load_workbook(p, data_only=True)["NpcCondition"]
    rows = list(ws.iter_rows(values_only=True))
    ci = {str(h): k for k, h in enumerate(rows[0]) if h is not None}
    best, best_row = 0, None
    for r in rows[3:]:
        if not r or not r[0] or str(r[0]) == "注释":
            continue
        segs = [str(r[ci[f"条件{k}"]] or "0") for k in range(1, 6)]
        segs = [s for s in segs if s not in ("0", "", "None")]
        if len(segs) > best:
            best, best_row = len(segs), (str(r[0]), segs)
    assert best >= n, f"最长条件组只有 {best} 段，不够 {n} 人"

    rid, segs = best_row
    # 排除 `feature_0_`（筛 unitA=玩家）之类写法：真正决定"谁进队"的是 feature_1_<luckId>
    luck_ids = []
    for s in segs[:n]:
        mm = re.match(r"feature_1_(\S+)$", s)
        assert mm, (
            f"`NpcCondition {rid}` 的某一段是「{s}」，不是 `feature_1_<气运id>`。\n"
            f"  `feature_1_` 的 `1` = GetUnitInType(1) = **unitB**（列表循环的当前候选），\n"
            f"  后面那个数字是**气运 id**（UnitCondition::Feature → unit.GetLuck(id)）。")
        luck_ids.append(mm.group(1))
    assert len(luck_ids) >= n and len(set(luck_ids)) == len(luck_ids), (
        f"`NpcCondition {rid}` 前 {n} 段是 {luck_ids} —— 必须每段一枚**不同**槽位气运"
        f"（MID&51/55/56），三段同款会重现 E11 型有放回重复（方成和×2）；")
    assert set(luck_ids) == {"MID&51", "MID&55", "MID&56"}, (
        f"终版应回到 feature_1_MID&51/55/56 三异段，实际 {luck_ids}。")


def test_slot_lucks_all_exist_in_config_table():
    """三个号位的气运都必须在 `RoleCreateFeature` 里真有行，否则那一槽永远抓不到人。"""
    p = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/RoleCreateFeature.xlsx")
    if not p.exists():
        return
    from openpyxl import load_workbook
    ws = load_workbook(p, data_only=True)["RoleCreateFeature"]
    ids = {str(r[0]) for r in ws.iter_rows(min_row=4, values_only=True)
           if r and r[0] and str(r[0]).startswith("MID&")}
    modids = _read(MODIDS)
    for name in ("LuckTeam", "LuckTeam2", "LuckTeam3"):
        off = int(re.search(rf"{name}\s*=\s*Mod\s*\+\s*(\d+)", modids).group(1))
        assert f"MID&{off}" in ids, f"{name} = MID&{off}，RoleCreateFeature 里没有这一行"


def test_team_tool_has_slot_machinery():
    """C# 侧必须有槽位机制：一槽一枚、同槽去重、跨槽判重。

    没有这些，"一槽一枚"只是配表约定；代码里只要有一处按"有没有 1 号位气运"判重，
    就会出现同一个人被挂两枚气运（占两个槽 ⇒ 副本里同一个人出场两次）。
    """
    src = _read(TEAM)
    for sym in ("SlotKnownIds", "SlotLuckId", "SlotOf", "SlotHolders",
                "PurgeSlotExcept", "RemoveAllSlotLucks", "HoldsAnyMateLuck"):
        assert sym in src, f"TeamTool 缺槽位机制：{sym}"

    # 判重必须跨槽（不能只看 1 号位）
    body = src[src.index("private static JObject Invite("):]
    body = body[: body.index("private static JObject Dismiss(")] if "private static JObject Dismiss(" in body else body
    assert "SlotOf(mate)" in body, "invite 判重没走 SlotOf（只看 1 号位会漏）"
    assert "PurgeSlotExcept" in body, "invite 没有清同槽残留 ⇒ 老数据会让这一槽变成多人随机抽"

    # 槽位数必须等于 MaxMembers，且三枚 id 互不相同
    m = re.search(r"SlotKnownIds\s*=\s*\{([^}]*)\}", src)
    assert m, "找不到 SlotKnownIds"
    names = [x.strip() for x in m.group(1).split(",") if x.strip()]
    assert len(names) == 3, f"SlotKnownIds 应有 3 项，实际 {names}"
    assert len(set(names)) == 3, f"SlotKnownIds 有重复项：{names}"


def test_team_tool_migrates_old_single_luck():
    """老版本三个槽共用一枚气运，真机留下 5 个持有者 ⇒ 必须有一次性的清空迁移。

    从气运本身推不出"谁才是现任 3 人"（气运里没有槽位字段），所以只能清空重来；
    猜一个必然会把陌生人带进副本。标记文件保证只跑一次。
    """
    src = _read(TEAM)
    assert "EnsureSlotsMigrated" in src, "TeamTool 没有旧数据迁移入口"
    assert "_team_slots_v2.migrated" in src, "迁移没有标记文件 ⇒ 每次都会清空队伍"
    body = src[src.index("internal static JObject Execute("):]
    assert "EnsureSlotsMigrated()" in body, "Execute 没有触发迁移"


def test_dungeon_trigger_type_filter_is_curated_whitelist():
    """`DungeonTrigger.dungeonType` 必须是**显式白名单**，且必须剔除三类副本。

    ★ 演变史（都踩过）★
      ① 照抄工坊 mod 的 `10|11|…|73` ⇒ 1012 副本不在列表，队友一个不来（E12）。
      ② 改成 `0`（全类型）⇒ 子代理反汇编+官方表头确认（docs/reference/dungeon-type-mapping.md）：
         纯切磋确实不进触发器（`UnitActionRoleBattle` 无 dbi，`SceneBattle::Init` 空槽守卫），
         但**演武会开真副本（type=41）**，`type=0` 会让队友被拉进演武/剧情决斗/约会。
    ★ 现行口径 ★ 白名单 = 全部 46 类官方语义，剔除：
      · 决斗/切磋：40（NPC决斗） 41（NPC切磋——演武走此型） 78（兽潮NPC决斗） 79（剧情NPC对战）
      · 剧情演出：20~29 44（开局） 52（何罗鱼） 83~88（李四/苍龙/女娲…） 89 90 91（约会）
      · 测试/搞怪：45（DPS测试） 70（不死） 76（斗鸡） 81（伤害测试）
      · 渡劫仪式：42（渡劫失败）
    注意：type=30/60/80 的官方注释缺失（未确证），保留在白名单里——宁可多触发，
    也不要像 ① 那样静默不触发；真发现乱入再收。

    ★ 10-25 用户拍板的唯一例外 ★ 决斗 40 要能**组队参战**（攻击/寻仇时弹确认窗）。
      例外必须窄到"单独一行、只含 40"，所以 `MID&004` 允许出现在这里；它若被并进
      通用白名单行（和其他类型混一行），本测试立刻失败 —— 那才是"顺手把演武/约会也带进来"的形状。
    """
    p = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/DungeonTrigger.xlsx")
    if not p.exists():
        return
    from openpyxl import load_workbook
    ws = load_workbook(p, data_only=True)["DungeonTrigger"]
    rows = list(ws.iter_rows(values_only=True))
    ci = {str(h): k for k, h in enumerate(rows[0]) if h is not None}
    col = ci["副本类型"]

    MUST_EXCLUDE = {40, 41, 78, 79,          # 决斗/切磋
                    44, 91,                   # 开局剧情 / 约会
                    76, 81, 45, 70,           # 斗鸡/伤害测试/DPS测试/不死
                    42,                       # 渡劫
                    83, 84, 85, 86, 87, 88,   # 剧情 Boss
                    89, 90,                   # 剧情演出
                    52}                       # 何罗鱼第一场剧情
    # 20~29 剧情副本段
    MUST_EXCLUDE |= set(range(20, 30))

    # 例外：决斗相关的**两行**（10-25 注入 / 10-26 重伤链），见上边 ★ 10-25 ★ 与 ★ 10-26 ★
    #
    #   MID&004 —— 只含 40：把队友塞进容器（注入）
    #   MID&002 —— 含 40，但它是**白名单行**（53 类 + 40）：只挂死亡回调 unitAddEffect_3/4/5，
    #              不碰容器。决斗里必须能进来，否则队友倒下不开 190、表也不挂重伤。
    #   两行都不许把 40 并进 MID&001 / MID&003：
    #      MID&001 的 setDramaUnitData 会改剧情单位表（换掉决斗处置页 20403）；
    #      MID&003 是 clearBattleNPCDatas（会清掉刚注入的队友）。
    DUEL_EXEMPT = {
        "MID&004": {40},                 # 注入行：只许 40
    }
    DUEL_MAY_INCLUDE = {"MID&002"}       # 白名单行：允许含 40，其余类型照旧

    bad, missing_checked = [], []
    duel_row_ok = False
    n_rows = 0
    for r in rows[3:]:
        if not r or not r[0] or str(r[0]) == "注释":
            continue
        n_rows += 1
        v = str(r[col] if r[col] is not None else "").strip()
        if v in ("0", "0.0", ""):
            bad.append((str(r[0]), v, "写 0 = 全类型，演武/约会也会拉队友"))
            continue
        got = set()
        for x in v.split("|"):
            x = x.strip()
            if x.isdigit():
                got.add(int(x))
        hit = sorted(got & MUST_EXCLUDE)
        if hit:
            rid = str(r[0])
            if rid in DUEL_EXEMPT and got == DUEL_EXEMPT[rid]:
                duel_row_ok = True
            elif rid in DUEL_MAY_INCLUDE and got - {40}:
                # 白名单行：除了 40 之外还有其他类型 ⇒ 它本来就是通用白名单，放行
                duel_row_ok = True
            else:
                bad.append((rid, hit, "官方语义禁止组队的类型"))
        if got & set(range(10, 20)):
            missing_checked.append(str(r[0]))
    assert n_rows >= 3, "DungeonTrigger 行数异常"
    assert not bad, f"副本类型白名单混入了不该组队的类型：{bad}"
    assert missing_checked, "常规副本段（10~19，组队主力）不在白名单里"
    assert duel_row_ok, ("决斗例外行（MID&004 只含 40 / MID&002 白名单含 40）不在表里 —— "
                         "攻击/寻仇的组队参战就没了注入入口或重伤链")


# ---------------------------------------------------------------- H. 键对账

def test_renderer_keys_are_all_written_by_csharp():
    """渲染层读的每个键，C# 必须真的写 —— 否则模型永远看不到（已踩多次的形态）。"""
    import sys
    sys.path.insert(0, str(ROOT))
    import inspect
    import tools.text_render as tr

    fn = tr._RENDERERS.get("team")
    assert fn is not None, "text_render 没有 team 渲染器"
    reads = set(re.findall(r'data\.get\(\s*["\']([^"\']+)', inspect.getsource(fn)))
    writes = set(re.findall(r'\["([^"]+)"\]\s*=', _read(TEAM)))
    missing = reads - writes
    assert not missing, f"渲染层读了 C# 从不写的键：{sorted(missing)}"


def test_target_id_is_injected():
    """`team.target` 要按名字注入精确 id（与其它指人工具同款）。"""
    src = _read(UNIT_REFS)
    assert re.search(r'"team"\s*:\s*\(\(\s*"target"\s*,\s*"target_id"\s*\)', src), \
        "unit_refs.PERSON_ARGS 没登记 team.target"


# ---------------------------------------------------------------- I. 弹窗

def test_invite_and_dismiss_show_confirm():
    """邀请/解散必须弹确认窗（玩家有拒绝权），且用 team 自己的段 ID。"""
    # `Team(args)` 在 11-20 归并后位于 `TeamTool.cs` **末尾**（入口与领域同一个文件），
    # 另一半 `ResolveCounterparty` 在 `ToolArgs.cs` —— 所以边界取"文件内到结尾"。
    src = _cs_tools.tool_file("TeamTool.cs")
    body = src[src.index(" JObject Team(JObject args)"):]
    assert body.count("ShowDramaService.ShowConfirm") >= 2, \
        "invite 与 dismiss 都要弹确认窗"
    assert "DramaTeamInviteBase" in body, "没有用 team 专属段 ID"


def test_status_does_not_show_confirm():
    """status 是只读查询，弹窗只会平白打扰玩家。"""
    src = _cs_tools.tool_file("TeamTool.cs")
    body = src[src.index(" JObject Team(JObject args)"):]
    m = re.search(r'if\s*\(\s*op\s*==\s*"status"\s*\)(.*?)\n\s{12}', body, re.S)
    assert m, "找不到 status 分支"
    assert "ShowConfirm" not in m.group(1), "status 不该弹窗"
    # status 必须在弹窗代码**之前**返回
    assert body.index('op == "status"') < body.index("ShowConfirm"), \
        "status 分支必须在弹窗之前 return，否则会跟着弹"


def test_drama_ids_registered():
    """新增段必须在 ModIds 登记（那是 ID 空间的唯一事实来源）。"""
    src = _read(MODIDS)
    assert "DramaTeamInviteBase" in src, "ModIds 没登记 team 邀请窗段"
    assert re.search(r"DramaTeamInviteBase\s*=\s*Mod\s*\+\s*180", src), \
        "team 邀请窗段应为 +180（+180~+189，每段 10 个）"
    assert re.search(r"DramaTeamHurtBase\s*=\s*Mod\s*\+\s*190", src), \
        "team 重伤剧情段应为 +190"


# ---------------------------------------------------------------- 契约表

def test_drama_table_ids_match_modids():
    """配置表里的重伤剧情 ID 必须与 `ModIds.DramaTeamHurtBase` 一致。

    `BattleEffect.value1` 指向剧情 ID；两边错一个数，队友阵亡就不会挂重伤
    （而且**完全静默** —— 战斗正常结束，只是气运没加上）。
    """
    modids = _read(MODIDS)
    m = re.search(r"DramaTeamHurtBase\s*=\s*Mod\s*\+\s*(\d+)", modids)
    assert m, "找不到 DramaTeamHurtBase"
    base = int(m.group(1))

    from openpyxl import load_workbook
    p = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/BattleEffect.xlsx")
    if not p.exists():
        return
    ws = load_workbook(p, data_only=True)["BattleEffect"]
    rows = list(ws.iter_rows(values_only=True))
    fn = list(rows[1])
    i_val1 = fn.index("value1")
    found = []
    for r in rows[3:]:
        if not r or not r[0] or str(r[0]) == "注释":
            continue
        v = str(r[i_val1] or "")
        mm = re.search(r"MID&(\d+)", v)
        if mm:
            found.append(int(mm.group(1)))
    assert found, "BattleEffect 里没有指向剧情的行 —— 阵亡不会挂重伤"
    assert base in found, \
        f"BattleEffect 指向 MID&{found}，但 ModIds.DramaTeamHurtBase = +{base} —— 两者必须一致"


# ---------------------------------------------------------------- 配置表完整性（10-17 真机前查出的两个致命洞）

def _mod_excel():
    """本机 mod 工程目录（非作者机返回 None，测试跳过）。"""
    p = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel")
    return p if p.exists() else None


def test_dialog_shell_exists_for_every_window_id():
    """★每个要开的窗，`DramaDialogue` 必须有壳条目★

    `ShowDramaService` 文件头写着这条事故（"赠送灵石不弹窗"）：
    缺条目时 `OpenUI → DramaTool.OpenDrama → RandomDramaID` 查表失败，
    打一句"找不到剧情ID：xxx"后**静默不开窗**、不抛异常 ⇒ `DramaGate` 120s 超时。
    真机表现 = 玩家点了工具、屏幕上什么都没发生、120 秒后模型收到超时。

    10-17 实查发现 `team` 的 +180 段**只登记了常量、没补表条目** —— 正是这条洞。
    """
    d = _mod_excel()
    if d is None:
        return
    from openpyxl import load_workbook

    def ids(fn):
        ws = load_workbook(d / f"{fn}.xlsx", data_only=True)[fn]
        return {str(r[0]) for r in ws.iter_rows(min_row=4, values_only=True)
                if r and r[0] and str(r[0]) != "注释"}

    dd, do = ids("DramaDialogue"), ids("DramaOptions")

    src = _read(MODIDS)
    bases = {m.group(1): int(m.group(2))
             for m in re.finditer(r"const int (\w+)\s*=\s*Mod\s*\+\s*(\d+)", src)}
    missing = []
    for name, off in bases.items():
        if off < 100:
            continue                      # +0~+99 是系统保留段，不是工具窗
        # ★ 只有**剧情窗基址**才该配 DramaDialogue/DramaOptions 条目 ★（10-25 收紧）
        #   原判据是"`Mod + N` 且 N>=100 就算窗"，但 `ModIds` 是**全部 ID 空间**的登记表：
        #   同一张表的 id 也在这里登记（如 `LogTeamDungeonDrama = Mod + 904`，那是
        #   `RoleLogLocal` 的经历模板 id，根本不开窗）。用"谁在这个文件里"推"谁是窗"
        #   必然误报 —— 而误报的后果是有人为了让它变绿去给经历 id 编一个假剧情窗。
        #   命名约定即判据：本仓所有剧情窗基址都叫 `Drama*Base`（十个，见下方断言）。
        if not (name.startswith("Drama") and name.endswith("Base")):
            continue
        if f"MID&{off}" not in dd:
            missing.append(f"{name}(MID&{off}) 缺 DramaDialogue 条目")
        # 重伤剧情是无选项的纯展示窗，不该有选项条目
        if name != "DramaTeamHurtBase":
            for o in (off + 1, off + 2):
                if f"MID&{o}" not in do:
                    missing.append(f"{name}(MID&{o}) 缺 DramaOptions 条目")
    # 约定本身要有牙：真的窗一个都不能漏（漏了 = 它不配表条目也查不出来）
    drama_bases = {n: o for n, o in bases.items()
                   if n.startswith("Drama") and n.endswith("Base") and o >= 100}
    assert len(drama_bases) >= 9, \
        f"按 `Drama*Base` 只找到 {len(drama_bases)} 个窗基址 —— 命名约定被破坏，本测试会漏检"

    assert not missing, "确认窗会静默不开（120s 超时）：\n  - " + "\n  - ".join(missing)


def test_luck_name_keys_exist_in_localtext():
    """★气运表的 name/tips 必须指向 LocalText 里真实存在的 key★

    写法看着对（`role_feature_postnatal_nameMID&001` 这种），但那是**别的 mod 的 key**
    —— 抄过来后经 `MID&` 展开 + `GameTool.LS` 翻译，查不到就返回 key 本身，
    真机表现 = 气运名显示成一串 `role_feature_postnatal_name-803158450` 乱码。

    而且这条链更隐蔽：`TeamTool.FindLuckIdByName` **按名字**比对，
    翻译不出来时比对的是展开后的 key，与代码里的中文常量「结伴而行」永远不等
    ⇒ 反查返回 0 ⇒ `team` 工具直接失败「气运表里找不到『结伴而行』」。
    """
    d = _mod_excel()
    if d is None:
        return
    from openpyxl import load_workbook

    ws = load_workbook(d / "LocalText.xlsx", data_only=True)["LocalText"]
    keys = {str(r[1]) for r in ws.iter_rows(min_row=4, values_only=True) if r and r[1]}

    ws = load_workbook(d / "RoleCreateFeature.xlsx", data_only=True)["RoleCreateFeature"]
    rows = list(ws.iter_rows(values_only=True))
    fn = list(rows[1])
    bad = []
    for col in ("name", "tips"):
        i = fn.index(col)
        for r in rows[3:]:
            if not r or not r[0] or str(r[0]) == "注释":
                continue
            v = str(r[i] or "")
            if not v or v == "0":
                continue
            if "MID&" in v:
                bad.append(f"{r[0]}.{col} = {v}（仍是未展开的 MID& key，翻译不出中文）")
            elif v not in keys:
                bad.append(f"{r[0]}.{col} = {v}（LocalText 里没有这个 key）")
    assert not bad, "气运名会显示为乱码 / 按名反查失败：\n  - " + "\n  - ".join(bad)


# ══════════════════════════════════════════════════════════════
#  动态气运描述（10-17 用户要求）
#
#  NPC 的气运要写"正和玩家组队中"；玩家自己也要有一条，写"和谁组队"（多人列全）。
#  描述随每次邀请/解散更新。
# ══════════════════════════════════════════════════════════════

DESC = CS / "tools" / "TeamDescService.cs"


def test_desc_service_exists_and_uses_localtext():
    """动态描述的唯一实现路径：改 `ConfLocalText.allText[key]` 的 ch/tc/en/kr。

    反射实证（10-17）：
        ConfLocalText { Dictionary<String, ConfLocalTextItem> allText; static String GetText(key) }
        ConfLocalTextItem { String key, ch, tc, en, kr }
    `GameTool.LS` 读的就是这个字典 ⇒ 运行时改写即生效。
    """
    src = _read(DESC)
    assert "allText" in src, "没有用 allText 字典（动态描述无从实现）"
    assert "ConfLocalTextItem" in src, "没有拿到文案条目类型"
    # 四种语言都要写：只写 ch 会让繁体/英文/韩文露出旧文案
    for lang in ("ch", "tc", "en", "kr"):
        assert re.search(rf"item\.{lang}\s*=", src), f"没有写 {lang} 字段（切语言会露旧文案）"


def test_desc_never_throws_into_business_path():
    """文案是展示层：写失败必须降级，不许把组队本身搞失败。"""
    src = _read(TEAM)
    body = src[src.index("internal static void SyncTeamState"):]
    # 区段终点用 `dismiss` 段头（10-19 删掉周期心跳后，"心跳监听" 段已不存在）。
    body = body[: body.index("//  dismiss —— 解散 / 请离")]
    assert "try" in body and "catch" in body, "SyncTeamState 没有兜住异常"
    assert "return Fail" not in body, \
        "SyncTeamState 里出现了 Fail —— 文案失败不该让组队失败"
    assert "TeamDescService.RefreshAll" in body, "SyncTeamState 没刷文案（等于没同步）"


def test_invite_and_dismiss_both_sync_desc():
    """两个动作的**成功路径**都必须刷描述（漏一个 = 描述长期过期）。"""
    src = _read(TEAM)
    inv = src[src.index("private static JObject Invite"):]
    inv = inv[: inv.index("// ══════════════════════════════════════════════════════════\n        //  组队状态的")]
    assert "SyncTeamState" in inv, "invite 成功后没有刷组队状态"

    dis = src[src.index("private static JObject Dismiss"):]
    dis = dis[: dis.index("//  status —— 查队伍")]
    assert dis.count("SyncTeamState") >= 2, \
        "dismiss 的两条路径（解散整队 / 请离单人）都要刷描述"


def test_player_gets_own_team_luck():
    """玩家身上要有一条独立气运表达"我在组队中"，与 NPC 的队友标记分开。"""
    src = _read(TEAM)
    # 玩家侧气运 id 走 `ModIds.LuckSelf`（名字常量随按名反查一起删了，11-16）
    assert "ModIds.LuckSelf" in src, "没有玩家侧气运 id"
    body = src[src.index("internal static void SyncTeamState"):]
    assert "playerUnit" in body, "SyncTeamState 没动玩家单位"
    assert "AddLuck(player" in body, "没有给玩家挂「组队中」"
    assert "RemoveLuck(player" in body, "队伍空了之后没有摘掉玩家的「组队中」"


def test_mate_luck_goes_on_npc_not_player():
    """★关键★ `addBattleNPCData` 挑的是带「结伴而行」的 NPC。

    把它挂到玩家身上会让条件组把玩家算成队员，容器槽里塞进玩家 —— 真机必炸。
    所以 target=玩家时，队员必须是**发起方 NPC 自己**。
    """
    src = _read(TEAM)
    inv = src[src.index("private static JObject Invite"):]
    inv = inv[: inv.index("// ══════════════════════════════════════════════════════════\n        //  组队状态的")]
    assert "targetIsPlayer ? self : who" in inv, \
        "队员没按「target 是玩家则挂自己」分流 —— 气运会挂错人"
    assert re.search(r'if\s*\(\s*IsPlayer\(mate\)\s*\)', inv), \
        "没有拦住「玩家当队员」这种情况"


def test_tick_is_event_driven_not_polling():
    """心跳**必须是事件驱动**的，不许恢复周期全图扫描（10-19 用户拍板）。

    旧实现每 300 帧（≈4~5s）无条件全图扫描一次判断"成员签名变没变"。
    真机 `[TickProfiler]` 铁证它的代价：
      · `diag` 的 max 恒定 ~115ms，周期**精确等于 300 帧**（176fps→1.69s，70fps→4.19s）；
      · `diag` 整个窗口总耗时 ≈ 那一次尖峰，占比 103% ⇒ 开销几乎全部来自这一次扫描；
      · 70fps 下等于每 4.2 秒硬卡 8 帧 —— 这是用户报"很卡"的主因。
    而收益是 **0 次触发**：整场 ~140 次心跳，`SyncTeamState` 因"成员变了"触发 0 次。

    ⇒ 现在只有真的发生"会让描述过期"的事件才排一次同步，执行完归零，
      常态每帧只做两次 int 比较，**不碰引擎、不扫全图**。

    契约（防止有人又把心跳加回来）：
      ① 有 `Tick()` 且挂在 `DiagSwitches.FrameTick`、包了 try/catch；
      ② `Tick()` 里**不许**出现全图遍历（`GetUnits(true)` / `ListMates`）；
      ③ 倒计时字段存在且执行完归 -1，不许是"每 N 帧无条件跑"。
    """
    src = _read(TEAM)
    assert "internal static void Tick()" in src, "没有 Tick 入口（FrameTick 会调空）"
    tk = src[src.index("internal static void Tick()"):]
    tk = tk[: tk.index("private static void DoDeferredSync")]
    # 剥掉注释再查：`ListMates()` 在 Tick 的**说明文字**里出现过（讲它为什么不能用），
    # 那是文档不是调用 —— 判据是"代码里不许调它"。
    tk_code = "\n".join(l[: l.find("//")] if "//" in l else l for l in tk.splitlines())
    assert "GetUnits(true)" not in tk_code, "Tick 里又出现了全图扫描（115ms/次，会卡顿）"
    assert "ListMates" not in tk_code, "Tick 里又调了 ListMates（同样是全图扫描）"
    assert "_syncFrame" in tk and "_retryFrame" in tk, "Tick 没走倒计时待办"

    # 待办字段必须是 -1 表示"无待办"，执行完归零
    body = src[src.index("private static void DoDeferredSync()"):]
    body = body[: body.index("private static void DoRetryText()")]
    assert "_syncFrame = -1" in body, "执行完没把待办清零 —— 会每帧重复同步"

    diag = _read(CS / "app" / "DiagSwitches.cs")
    assert "TeamTool.Tick()" in diag, "FrameTick 没接 TeamTool.Tick —— 待办永远不会被执行"
    assert re.search(r"try\s*\{\s*TeamTool\.Tick\(\);\s*\}\s*catch", diag), \
        "Tick 没包 try/catch（异常会打断主循环）"


def test_player_luck_row_exists_in_table():
    """玩家侧气运在 `RoleCreateFeature` 里必须有定义（代码只能挂已定义的气运）。"""
    d = _mod_excel()
    if d is None:
        return
    from openpyxl import load_workbook
    ws = load_workbook(d / "RoleCreateFeature.xlsx", data_only=True)["RoleCreateFeature"]
    rows = list(ws.iter_rows(values_only=True))
    fn = list(rows[1])
    i_name = fn.index("name")
    names = []
    for r in rows[3:]:
        if not r or not r[0] or str(r[0]) == "注释":
            continue
        v = str(r[i_name] or "")
        if v:
            names.append(v)
    # LocalText 里要能找到这些 key，且翻译出来是代码里的中文名
    lt = load_workbook(d / "LocalText.xlsx", data_only=True)["LocalText"]
    zh = {str(r[1]): str(r[2]) for r in lt.iter_rows(min_row=4, values_only=True) if r and r[1]}
    missing = [n for n in names if n not in zh]
    assert not missing, f"气运名在 LocalText 里查不到：{missing}"

    assert "组队中" in zh.values(), "LocalText 里没有「组队中」这条（玩家侧气运名）"
    assert "结伴而行" in zh.values(), "LocalText 里没有「结伴而行」这条（NPC 侧气运名）"


def test_world_reload_resets_desc_state():
    """读档后必须复位，让描述重刷一次。

    为什么：文案表 `allText` 随世界重载从配置**重新初始化**，运行时覆盖全丢；
    而现在的同步是**事件驱动**（成员签名不变就不刷）—— 同一存档同一批队友时
    什么都不会触发 ⇒ 永远不重刷 ⇒ 气运描述退回配置表那句静态话。
    `OnIntoWorldEvent` 里的 `ResetTickState()` 就是兜这件事的**唯一**入口。

    契约：复位要 ① 清掉缓存的名单（否则重试会拿旧名单刷）；
          ② 丢掉上个存档的队员缓存（新存档的人可能完全不同）；
          ③ 排一次同步。
    """
    src = _read(TEAM)
    assert "internal static void ResetTickState()" in src, "没有复位入口"
    body = src[src.index("internal static void ResetTickState()"):]
    body = body[: body.index("\n        }") + 10]
    assert "_lastNames = null" in body, "复位没清缓存的名单（重试会刷旧名字）"
    assert "ResetMemberCache()" in body, "复位没丢上个存档的队员缓存（新存档会认错人）"
    assert "RequestSync()" in body, "复位没排同步 —— 读档后描述不会重刷"

    main = _read(CS / "app" / "ModMain.cs")
    assert "TeamTool.ResetTickState()" in main, \
        "OnIntoWorldEvent 没接复位 —— 读档后气运描述会退回静态文案"


# ══════════════════════════════════════════════════════════════
#  解散扣好感（10-18 用户口径：**单边**、**代码直接扣**）
# ══════════════════════════════════════════════════════════════

def test_dismiss_deducts_intim():
    """解散必须真的扣好感，走的是与赠礼/讨要同一条写入层（`IntimService.ApplyIntim`）。

    为什么不走配表动作：解散本身是**代码摘气运**实现的（`RemoveLuck`），
    没有原生动作可挂钩子 —— 配表的 `abClose` 只在剧情命令里有意义。
    所以直接调写入层，语义仍是"只传请求值、引擎按 RoleClose 换算、回读实得"。
    """
    src = _read(TEAM)
    assert "ApplyDismissIntim" in src, "没有扣好感的实现"
    body = src[src.index("private static JObject ApplyDismissIntim"):]
    body = body[: body.index("private static void AttachIntim")]
    # 写入层 11-20 从 `ToolExecutor` 搬到独立类 `IntimService`（全仓唯一写入口）
    assert "IntimService.ApplyIntim" in body, \
        "没有复用 IntimService.ApplyIntim（会绕开统一的好感写入语义）"

    # 写入层必须对外开放
    ex = _read(EXEC)
    assert re.search(r"internal static int ApplyIntim\(", ex), \
        "ApplyIntim 还是 private，TeamTool 调不到"


def test_dismiss_intim_is_unilateral():
    """★单边★ 只有被请离的 NPC「对玩家」掉好感，反向不动。

    写反的形态：对玩家扣"对 NPC"的好感，或两边都扣 —— 前者没有任何游戏效果
    （玩家面板不显示他对 NPC 的私下看法），后者等于玩家自己也记恨，语义错。
    """
    src = _read(TEAM)
    body = src[src.index("private static JObject ApplyDismissIntim"):]
    body = body[: body.index("private static void AttachIntim")]
    # holder = NPC(mate)，other = 玩家 ⇒ 掉的是"他对玩家"的那一条
    assert re.search(r"ApplyIntim\(\s*mate\s*,\s*player\s*,", body), \
        "方向错了：必须是 ApplyIntim(被请离的NPC, 玩家, 负值)"
    # 负值
    assert re.search(r"ApplyIntim\([^)]*,\s*-loss\s*,", body), \
        "扣好感必须传负值"
    # 不许出现第二次写入（双边会有第二次）
    assert body.count("ApplyIntim(") == 1, \
        "出现了多处 ApplyIntim —— 单边只该扣一次（被请离者对玩家）"


def test_dismiss_intim_range_matches_mod():
    """区间跟组队 mod 的 `abClose_1_0_-10_-6` 对齐：6~10。

    参数1=B(对话对象=该 NPC)、参数2=A(玩家) ⇒ 正是"他对玩家"。
    """
    src = _read(TEAM)
    m1 = re.search(r"DismissIntimMin\s*=\s*(\d+)", src)
    m2 = re.search(r"DismissIntimMax\s*=\s*(\d+)", src)
    assert m1 and m2, "缺少区间常量"
    lo, hi = int(m1.group(1)), int(m2.group(1))
    assert (lo, hi) == (6, 10), f"区间应为 6~10（mod 的 -10_-6），实际 {lo}~{hi}"


def test_dismiss_intim_runs_before_luck_removal():
    """**先扣好感、再摘气运**。

    反过来的坏形态：先摘气运、再扣好感 —— 中间若异常就是"人踢走了却一点代价没付"；
    而"好感掉了但人还在队里"是玩家能看见、能再试一次的小问题。
    """
    src = _read(TEAM)
    body = src[src.index("private static JObject Dismiss"):]
    body = body[: body.index("//  解散的好感代价")]

    i_intim = body.index("ApplyDismissIntim")
    # 摘气运的入口叫 `RemoveAllSlotLucks`（一槽一枚气运后要整组摘，
    # 不能只摘 1 号位那枚 —— 否则人会留在 2/3 号位上）。
    i_remove = min(body.index(x) for x in ("RemoveAllSlotLucks", "RemoveLuck")
                   if x in body)
    assert i_intim < i_remove, "顺序错了：必须先扣好感再摘气运"

    # 两条路径（解散整队 / 请离单人）都要在摘气运之前扣
    assert body.count("ApplyDismissIntim") >= 2, \
        "解散整队与请离单人两条路径都要扣好感"


def test_dismiss_intim_failure_does_not_block():
    """好感是副作用，不许因为它失败就让解散失败。"""
    src = _read(TEAM)
    body = src[src.index("private static JObject ApplyDismissIntim"):]
    body = body[: body.index("private static void AttachIntim")]
    assert "catch" in body, "扣好感没兜异常"
    assert "return Fail" not in body, "扣好感失败不该让解散失败"


def test_intim_result_is_reported():
    """实得必须报出来（引擎会按 RoleClose 衰减，请求值≠实得）。

    `intim_lost` 缺失 = 读不到起点好感，那就不报数字，绝不发 int.MinValue 当值。
    """
    src = _read(TEAM)
    assert "intim_lost" in src, "结果里没有 intim_lost"
    assert "intim_detail" in src, "结果里没有逐人明细 intim_detail"
    # 计算总数时跳过"读不到"的项（int.MinValue 加进去会把总和变成垃圾大数）
    dis = src[src.index("private static JObject Dismiss"):]
    dis = dis[: dis.index("//  解散的好感代价")]
    assert "int.MinValue" in dis, \
        "汇总 intim_total 时没有排除 int.MinValue（会把总和变成 -21 亿）"


def test_renderer_speaks_the_intim_cost():
    """渲染层要说出来 —— 玩家不翻关系面板就看不到这个代价。"""
    src = _read(RENDER)
    fn = src[src.index("def _intim_tail"):]
    fn = fn[: fn.index('@renderer("team")')]
    assert "intim_lost" in fn and "intim_detail" in fn, "好感尾巴没读这两个键"
    body = src[src.index('@renderer("team")'):]
    body = body[: body.index("def _team_", 0) if "def _team_" in body else len(body)]
    assert "_intim_tail" in body, "render_team 没调好感尾巴"


def test_schema_warns_about_intim_cost():
    """模型必须知道"解散会得罪人"，否则下一轮会把人当还热络。

    ★ 10-18 搬迁 ★ 这条政策**不在 schema 里了**，在 `tool_usage.txt`。
      原因是 schema 散文有长度预算（参数名/枚举属于 schema，"政策"属于 tool_usage），
      而 `team` 加进来后预算已经撑满 —— 把"重伤 4 个月 / dismiss 掉好感"搬过去，
      两处口径都写清楚，也不重复付费。所以两边任意一处有"好感"即可，但**必须有一处**。
    """
    from pathlib import Path as _P
    usage = (_P(__file__).resolve().parent.parent / "prompts" / "sections" / "tool_usage.txt")
    schema_src = _read(SCHEMAS)
    i = schema_src.index('"name": "team"')
    seg = schema_src[i: i + 3000]
    usage_src = usage.read_text(encoding="utf-8") if usage.exists() else ""
    assert ("好感" in seg) or ("好感" in usage_src), \
        "team 的描述与工具使用规则都没提「好感」代价（模型会把散了伙的人当还热络）"
    assert ("重伤" in seg) or ("重伤" in usage_src), \
        "没提「内外重伤」代价（模型会把重伤的人反复往副本里推）"


# ══════════════════════════════════════════════════════════════
#  同名气运：识别一律用已知偏移（10-18 真机事故）
#
#  事故经过：工坊里另有一个组队 mod（excelMID -2004589739），它的气运也叫
#  「结伴而行」。原先全靠按名反查，命中谁取决于扫描顺序 —— 真机日志里命中的是
#  工坊的 -2004589688，于是 NPC 身上挂的是**别人的**气运：
#    · 我们的 `NpcCondition MID&003`（`feature_1_MID&51`）匹配不到它
#    · 我们的 `DungeonTrigger` 不触发 ⇒ **队友不上场，工具却报成功**
#  这种失效完全静默（工具返 Ok、气运看得见、名字也对），只能靠契约测试钉住。
# ══════════════════════════════════════════════════════════════

def test_luck_offsets_registered_in_modids():
    """气运偏移必须在 ModIds 登记 —— 那是 ID 空间的唯一事实来源。"""
    src = _read(MODIDS)
    for name, off in (("LuckTeam", 51), ("LuckHurt", 52), ("LuckTalk", 53), ("LuckSelf", 54),
                      ("LuckTeam2", 55), ("LuckTeam3", 56)):
        assert name in src, f"ModIds 没登记 {name}"
        assert re.search(rf"{name}\s*=\s*Mod\s*\+\s*{off}\b", src), \
            f"{name} 应为 Mod + {off}（与 RoleCreateFeature.xlsx 的 MID&{off} 对应）"


def test_luck_offsets_match_config_table():
    """ModIds 里的偏移必须与 `RoleCreateFeature.xlsx` 的 `MID&NNN` 逐一对上。

    两边错一个数 = 代码去挂一枚不存在的气运（或挂到别人的编号上），
    而 `GetItem` 返回 null 后会静默退回按名反查 —— 于是又回到撞车状态。
    """
    p = Path("/mnt/f/mod/ModProject_Jgmg5L/ModProject/ModExcel/RoleCreateFeature.xlsx")
    if not p.exists():
        return
    from openpyxl import load_workbook
    ws = load_workbook(p, data_only=True)["RoleCreateFeature"]
    ids = set()
    for r in ws.iter_rows(min_row=4, values_only=True):
        if r and r[0] and str(r[0]).startswith("MID&"):
            ids.add(str(r[0]))
    modids = _read(MODIDS)
    for name in ("LuckTeam", "LuckHurt", "LuckTalk", "LuckSelf", "LuckTeam2", "LuckTeam3"):
        off = int(re.search(rf"{name}\s*=\s*Mod\s*\+\s*(\d+)", modids).group(1))
        assert f"MID&{off}" in ids, f"{name} = MID&{off}，但 RoleCreateFeature 里没有这一行"


def test_team_tool_prefers_known_offset_over_name():
    """识别必须**先查已知偏移**，按名反查只能兜底。

    顺序反了就等于回到撞车状态：工坊那枚同名气运会先被命中。
    """
    src = _read(TEAM)
    # 按名反查删掉后，"先偏移、后按名"的顺序问题不复存在 —— 现在只有偏移这一条路。
    assert "ResolveLuckById" in src, "没有按已知 id 的解析入口"
    assert "FindLuckIdByName" not in src, "按名反查又回来了"

    # 三个访问器都应走 ResolveLuck 并传入 ModIds 的已知偏移
    # 按名反查删掉后只剩一条路：`ResolveLuckById(ModIds.LuckX)`。
    # 三枚气运都必须走它（缺一个就意味着有人在别处另开了一条解析路径）。
    for cid in ("ModIds.LuckTeam", "ModIds.LuckHurt", "ModIds.LuckSelf"):
        assert f"ResolveLuckById({cid})" in src or f"SlotKnownIds" in src, \
            f"没有用 {cid} 走已知偏移"


def test_probe_prefers_known_luck_ids():
    """探针也要先查已知偏移 —— 否则 `givemap` 挂的是工坊的气运，测的就不是我们的配置。"""
    probe = _read(CS / "diag" / "team" / "TeamMultiProbe.cs")
    assert "TryKnownLuckIds" in probe, "探针没有已知偏移优先的入口"
    call = probe.index("TryKnownLuckIds();")
    scan = probe.index("ScanLuckByList();")
    assert call < scan, "TryKnownLuckIds 必须在 ScanLuckByList 之前调用"
    assert "ModIds.LuckTeam" in probe and "ModIds.LuckTalk" in probe, \
        "探针没引用 ModIds 的气运偏移"



def test_addluck_flags_dynamic_null_trap():
    """动态气运陷阱必须有专门出口（10-19 续查 Q9'' 链）：
    type=2 气运 u.CreateLuck → allLuck 里对象 luckData=null ⇒ GetLuck 恒不命中，
    本局条件组看不见（10-18 晚靠换局材质化才成功）。报错要说清，不许笼统失败。"""
    src = _read(TEAM)
    assert "IsDataNullTrap" in src, "缺陷阱判定助手"
    assert "动态气运陷阱" in src, "AddLuck 失败信息必须点名陷阱"
    assert "luckData=null" in src, "要说明对象 luckData=null"
    assert "换局材质化后生效" in src, "要说明跨局才生效"


def test_addluck_uses_engine_native_sequence():
    """★10-19 续查二轮 Q9' 定案：挂运必须复刻引擎原生序列
    UnitActionLuckAdd::OnCreate@0x1838DE970 —— ①AddAddLuck ②CreateLuck(guarded)
    ③NewCreateLuck，④直塞 addLuck 只剩兜底；顺序反了等于回到 hollow/E13 老坑。"""
    src = _read(TEAM)
    i_sig = src.index("internal static bool AddLuck(WorldUnitBase u, int luckId")
    i_end = src.index("internal static bool IsDataNullTrap(")
    body = src[i_sig:i_end]
    i_add = body.index("AddAddLuck(data)")
    i_cr = body.index("luckObj = u.CreateLuck(data)")
    i_new = body.index("NewCreateLuck(0)")
    i_fb = body.index("NewCreateLuck()")
    i_in = body.index("InAddLuck(u, luckId)")
    assert i_add < i_cr, "①AddAddLuck 必须在 ②CreateLuck 之前（引擎 OnCreate 序）"
    assert i_cr < i_new, "②CreateLuck 必须在 ③NewCreateLuck 之前"
    assert i_new < i_fb, "③NewCreateLuck(0) 失败才试无参兜底"
    assert i_fb < i_in, "④直塞 addLuck 兜底留在最后"
    assert "if (u.GetLuck(luckId) == null)" in body, "② 必须 guard 幂等（GetLuck 命中即跳过）"
    assert "allLuck" in body, "luckObj 拿不到时要按 allLuck 末位兜取"
    assert "两层验证失败" in body and "runtimeOk && panelOk" in body, "成败判据仍是两层都真进"


def test_rescue_guard_stays_deleted():
    """救助战斗守卫（方案 A）**已删除**，10-21 用户拍板。

    ## 它原来做什么

    `IsRescueBattle()` 读 `battleCustomData` 的 `rescue=1` / `NPC=300001~300004`；
    命中就放行原 `addBattleNPCData`、跳过队伍注入、拦掉队伍 `addBattleUnit_3/4/5`，
    把**战斗位 3** 留给救助 NPC。理由是官方 `DungeonSpecialRoom31` 的
    `unitAnim_3_Die_1` 打的正是战斗位 3，而我们的第一个队友就占在那里
    ⇒ 救助 NPC 站着能打、队友反而"腿直贴地"。

    ## 为什么删（证据，不是印象）

    · **编译期**：该分支在 `bak_1019_2214` … `bak_1021_0030` 的 DLL 里确实存在
      （UTF-16 `救助战斗` 计数 = 2），`bak_1021_0045` 之后消失；
    · **运行期**：这几局 `runs/{2026-10-19_222559, 10-20_204755, 10-20_230705,
      10-21_001653}` 的 `Player.log` 里 `救助` **0 命中**，
      而同一批日志里 `Clear 前 npcUnits` 有命中
      ⇒ Prefix 确实跑了，是守卫自己从没命中 ⇒ `battleCustomData` 里没这两个 key。

    ## 契约

    ① 代码里不许再有 `IsRescueBattle()` / `AddBattleUnitPrefix` / 救助横幅；
    ② **注释必须留痕**：说明"删于何时、凭什么证据删、将来反馈来了先查什么"。
       否则下一次有人看到"队友贴地"会凭印象把它加回来，又从零踩一遍。
    """
    src = _read(CS / "tools" / "TeamContainerInject.cs")
    code = _code(CS / "tools" / "TeamContainerInject.cs")
    assert "IsRescueBattle()" not in code, "救助判据又被加回来了"
    assert "AddBattleUnitPrefix" not in code, "addBattleUnit 守卫又被加回来了"
    assert "救助剧情：本场队伍暂不参战" not in code, "救助横幅文案又被加回来了"
    # 注释留痕（防止无脑回滚）—— 这两条查的是**注释**，所以用原文
    assert "删除「救助剧情不注入」分支" in src, "删掉救助守卫时没有在注释里留痕"
    assert "battleCustomData" in src and "rescue" in src, \
        "注释里没写清当初的判据来源，将来无法判断该不该恢复"
    # 容器直塞本身必须在（这是组队上场的唯一路径，删了等于组队失效）
    assert "★容器直塞=已挂★" in code, "容器直塞的安装回执没了"
    assert "npcUnits" in code, "容器直塞没写 npcUnits（队友上不了场）"
