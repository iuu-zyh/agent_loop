"""通讯录「临时」页契约（10-24：说过话但非好友、非社交关系的人）。

## 为什么必须有这一页（用户报障）

现象：NPC 主动传音 → 横幅弹出、HUD「传」钮亮红点 → 玩家打开通讯录，**找不到这个人**。

根因是**成员来源只有两层**，而这声传音两层都不记：

  ① 关系层 `RelationNetwork.CollectKnownUnits` —— 只由**游戏原生交互**写入
     （结拜/论道/赠礼/拜师…）；
  ② 手动层 `contacts.json` —— 只有玩家点过「加好友」。

而本项目所谓的"传音"是 MOD 抓时机发 `npc_initiative` 给 Python、由 LLM 生成一句话推回来，
**游戏侧一个字节都没写**。于是"他跟我说过话"这个事实在①②里都不存在 ⇒ 列表里没有他。

本页 = 第三层成员来源，**纯派生、单向只读**：不写 `contacts.json`、不发 RPC。

## 这个文件钉什么

1. **成员来源**：`npc_reply` 收口帧要记"说过话"；位置必须在 `isError` 之后（失败兜底帧
   不是他说的话）、`!initiative` 之前（玩家主动聊的也算）；
2. **不自动加好友**：`ContactStore` 不许有写 `contacts.json` 的路径；`MarkHeard` 只进
   `_heard` 集合，绝不碰 `_manualNames`（一碰就会触发"异地传音候选 / 面板按钮变移除好友 /
   隐藏者被好友优先放行"三个**行为**变化）；
3. **三层合并优先级**：关系层/手动层优先，本层只补缺（同一个人不许出两行）；
4. **只在「临时」页出现**：好友页必须滤掉本层行，否则好友簿失去"我认识谁"的语义；
5. **换档整表作废**：`ResetForWorld` 必须清 `_heard`，否则新档会显示上一档的人；
6. **不需要存档**：本地即时表是主力（用户指出的洞：没存档时账本没落盘）；
7. **Tab 是运行时克隆**，不在预制件里 —— 旧 `.ab` 也能用（不必重打 AB）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _read(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _method_body(src: str, sig: str) -> str:
    body = src[src.index(sig):]
    depth, started = 0, False
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                return body[: i + 1]
    raise AssertionError(f"方法体没闭合：{sig}")


# ---------------------------------------------------------------- ① 成员来源 / 位置

def test_mark_heard_sits_between_error_and_initiative():
    """`MarkHeard` 的位置是这次改动的**整个正确性**所在，两个方向都错不得。

    · 早于 `isError`：失败兜底帧（"他这一阵正忙着，没接上你的话"）会把一个**没说话的人**
      拉进名单；而且之后永不撤销（`_heard` 只增不减）⇒ 通讯录里长期挂着一个幽灵。
    · 晚于 `!initiative`：玩家自己发起的那一轮不会记 ⇒ "我点开他聊了半天，通讯录里还是没他"，
      正好是本次要修的半边。

    实现上 `isError` 的**求值**必须早于 `MarkHeard`，而失败帧的早退仍在后面（那里才该
    结束整个分流）—— 所以判据是"求值顺序 + MarkHeard 自己带 `!isError` 守卫"，
    不是"早退语句在它前面"。
    """
    duty = _read("contacts/ContactDuty.cs")
    body = _method_body(duty, "private static void OnUiEvent(JObject payload)")
    assert "if (!isError) ContactStore.MarkHeard(npc);" in body, (
        "「说过话」的记录要么没接线、要么没带 `!isError` 守卫"
        "（失败兜底帧不是他说的话，会把人凭空拉进「临时」页且永不撤销）"
    )

    i_read_err = body.index('bool isError = payload["error"]')
    i_heard = body.index("ContactStore.MarkHeard(npc)")
    i_ini = body.index("if (!initiative)")
    assert i_read_err < i_heard, "`isError` 必须先求值，`MarkHeard` 才能据它守卫"
    assert i_heard < i_ini, (
        "`MarkHeard` 排在 `!initiative` 早退**之后** —— 玩家主动发起的那一轮不算"
        "「说过话」，通讯录里仍然没有他（本次报障的另一半）"
    )


def test_mark_local_position_untouched():
    """反面钉子：**不许**为了省事把 `MarkLocal` 也挪到 `isError` 之后。

    那份时间戳是「最近」排序的源，写入点必须留在一切早退之前（`test_busy_label_contract`
    另有一条钉着它）。两件事刻意分开：`MarkLocal` = "这一帧到过"，`MarkHeard` = "他说了话"。
    """
    duty = _read("contacts/ContactDuty.cs")
    body = _method_body(duty, "private static void OnUiEvent(JObject payload)")
    assert "ContactStore.MarkLocal(npc, now)" in body, "「最近」的时间戳记录点不见了"
    assert body.index("ContactStore.MarkLocal(npc, now)") < body.index("if (isError) return;"), \
        "`MarkLocal` 被挪到失败帧早退之后了 —— 「最近」排序会少记失败回合（原语义是全部记）"


# ---------------------------------------------------------------- ② 不自动加好友

def test_heard_layer_never_writes_manual_contacts():
    """`MarkHeard` / `HeardNames` 一个字节都不许碰手动好友镜像。

    一碰就会触发三个**行为**变化（不是显示变化），三条都不该由一次显示修复带来：
      ① 他变成合法的"异地传音"候选（`NpcInitiativeMonitor` 的门槛是"非通讯录成员仅同格"）；
      ② NPC 面板的按钮从「加好友」变成「移除好友」；
      ③ 被玩家隐藏过的人被"好友优先放行"强制冒出来。
    """
    store = _read("contacts/ContactStore.cs")
    for sig in ("public static void MarkHeard(string npc)",
                "public static List<string> HeardNames()"):
        body = _method_body(store, sig)
        for forbidden in ("_manualNames", "_manualUnitIds", "SetManualNames", "SetManualContact"):
            assert forbidden not in body, (
                f"`{sig}` 里出现了 `{forbidden}` —— 「说过话」绝不能变成「加好友」（见文件头三个副作用）"
            )


def test_reset_for_world_clears_heard():
    """换档必须整表作废：上一档说过话的人绝不能出现在新档的「临时」页。"""
    store = _read("contacts/ContactStore.cs")
    body = _method_body(store, "public static void ResetForWorld()")
    assert "_heard.Clear();" in body, "换档没清「说过话」集合 → 新档显示上一档的人"
    assert "_heardLoaded = false;" in body, "换档没复位 loaded 标志 → 「临时」页会一直显示'已加载'"


# ---------------------------------------------------------------- ③ 三层合并

def test_three_layer_merge_priority():
    """关系层/手动层优先：同一个人不许在 `_contacts` 里出两行。

    判据是"补入前先 `Exists` 查重"——漏掉就会出现同名两行（一行带真身、一行没真身），
    点哪行的行为还不一样（一个有精确身份、一个按名猜）。
    """
    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private void MergeHeardIntoContacts()")
    assert "_contacts.Exists" in body, "补入前没查重 → 同一个人会出两行"
    i_exists = body.index("_contacts.Exists")
    i_add = body.index("_contacts.Add(e)")
    assert i_exists < i_add, "查重必须在 `Add` 之前"
    # 三层合并必须在两个入口都被调用（Show / OnStoreChanged），漏一个就有一条路看不到新人。
    # 计数口径：`MergeHeardIntoContacts();` 带分号 ⇒ **只计调用点**（定义行不带分号）。
    calls = pres.count("MergeHeardIntoContacts();")
    assert calls >= 2, (
        f"`MergeHeardIntoContacts();` 只有 {calls} 处调用 —— "
        "必须覆盖 Show（首次打开）与 OnStoreChanged（数据变更）两个入口"
    )


def test_temp_layer_does_not_resurrect_dead_or_self():
    """死者与玩家自身不进「临时」页（两处都是既有判据的复用，不是新逻辑）。"""
    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private void MergeHeardIntoContacts()")
    assert "UnitSnapshot.IsDead(" in body, "死者会出现在「临时」页（点开是个空窗）"
    assert "PlayerUnitId()" in body, "没挡玩家自身（账本不该有自己，但这是与来源无关的兜底）"


# ---------------------------------------------------------------- ④ 只在「临时」页

def test_friend_tab_excludes_temp_rows():
    """「好友」页必须滤掉本层行：那页回答"我认识谁"，一次擦肩不该算认识。"""
    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private void RenderCurrent()")
    assert "IsTempOnly" in body, "渲染没有区分「临时」层 → 好友页会混进陌生人"
    # 好友页那条 else 分支必须带过滤（直接 `rows = _contacts` 就是漏了）
    assert re.search(r"rows\s*=\s*_contacts\.FindAll\(c\s*=>\s*!IsTempOnly\(c\)\)", body), \
        "好友页的列表源必须是「排除临时层」的全量（直接赋值 = 陌生人混进好友簿）"
    assert not re.search(r"rows\s*=\s*_contacts\s*;", body), \
        "好友页仍然直接 `rows = _contacts`（没滤掉「临时」层）"


def test_recent_tab_excludes_temp_rows():
    """「最近」页同样排除本层：它回答"我认识的人里谁最近说过话"。

    只在一页里排掉是不够的——两页都渲染同一份 `_contacts`，漏一页那个人就换个页签又冒出来。
    """
    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private void RenderCurrent()")
    assert "c.LastInteract > 0 && !IsTempOnly(c)" in body, \
        "「最近」页没滤掉「临时」层（换页签他又出现）"


def test_temp_only_ignores_resolved_unit():
    """`IsTempOnly` 不许拿 `c.Unit` 当判据。

    事故形态：玩家点开「临时」里的某个人 → `OpenChatFor` → `ResolveEntry` 把 `e.Unit`
    填上了 → 下一次渲染这个行**当场从「临时」页消失**（而他确实跟自己说过话）。
    这一页的语义是"我们说过话"，与"此刻能不能解析到真身"无关。
    """
    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private static bool IsTempOnly(ContactEntry c)")
    assert "c.Unit" not in body, (
        "`IsTempOnly` 用了 `c.Unit` —— 点开一个人他就会从「临时」页蒸发（见本测试注释）"
    )
    assert "HeardOnly" in body, "判据必须看「说过话」标记"
    assert "IsManualContact" in body, "判据必须看好友镜像（刚加好友/刚移除都要立刻正确）"


def test_temp_merge_keeps_rows_of_fresh_friends():
    """反面钉子：`MergeHeardIntoContacts` **不许**无条件 `RemoveAll(HeardOnly)`。

    事故形态：玩家刚把某人加为好友 → `MergeManualIntoContacts` 给这条**已有**条目置
    `Manual=true`（它不会重加一遍）→ 这里若按标记整片删掉，本次渲染这个人**两页都没有**。
    """
    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private void MergeHeardIntoContacts()")
    m = re.search(r"_contacts\.RemoveAll\(([^;]+)\);", body)
    assert m, "找不到清理语句"
    cond = m.group(1)
    assert "HeardOnly" in cond and "IsManualContact" in cond, (
        "清理条件必须同时看「说过话」与「已不是好友」——只按 `HeardOnly` 删会让刚加好友的人消失"
    )


# ---------------------------------------------------------------- ⑤ 不需要存档（用户指出的洞）

def test_heard_source_includes_local_memory():
    """「临时」层的名字来源必须含**本地即时**那份。

    用户指出的洞：账本懒落盘，**不存档就一个字都没有**。只靠 `list_sessions` 回包的话，
    "今天聊了一整晚、一次没存"这个窗口里通讯录照样空着 —— 那正是本次要修的现象。

    本平台上的即时证据就是 `MarkHeard` 自己那张表（`npc_reply` 到达当帧写入，不等存档），
    所以 `HeardNames` 必须读它、而且**换档之外不许被清**。
    """
    store = _read("contacts/ContactStore.cs")
    body = _method_body(store, "public static List<string> HeardNames()")
    assert "_heard" in body, "`HeardNames` 没读本地即时表"

    setter = _method_body(store, "public static void SetSessionIndex(")
    assert "_heard.Clear()" not in setter, (
        "`SetSessionIndex` 把 `_heard` 清了 —— 刚说完话的人会在下次拉索引时从「临时」页消失"
    )
    assert "_heard.Add" in setter, "索引回包没把账本键并进「说过话」集合（老存档会缺人）"


def test_heard_loaded_flag_exists():
    """「加载中」与「确实没有」必须分得开（同 `ManualLoaded` 受过的教训）。

    Python 的未定档回包被 `RequestSessions` 拦掉了，但"还没拉到"与"拉到了、确实是空"
    仍然是两个不同的事实；混用会让面板第一帧先说"还没有"、再突然冒出一堆人。
    """
    store = _read("contacts/ContactStore.cs")
    assert "public static bool HeardLoaded" in store, "缺 `HeardLoaded` 标志"
    pres = _read("UI/ContactPresenter.cs")
    assert "ContactStore.HeardLoaded" in pres, "状态条没用到它（空名单会说成'还没有'）"


# ---------------------------------------------------------------- ⑥ Tab 运行时克隆

def test_temp_tab_is_runtime_built_not_in_prefab():
    """「临时」Tab 走运行时克隆 —— 必须在 C# 里建，**不许**指望预制件里有。

    这条钉的是"旧 `.ab` 也能用"：预制件不由本仓库维护（在 AB 打包工程里），
    若改成"必须在预制件里多一个节点"，旧包装配时会缺节点 → 而且失败是**静默**的
    （`Find` 拿不到只返回 null）。运行时克隆则最坏只是少一个页签。
    """
    builder = _read("UI/ContactUiBuilder.cs")
    assert "public static GameObject BuildTabPill(" in builder, "缺运行时克隆 Tab 的工厂"

    pres = _read("UI/ContactPresenter.cs")
    body = _method_body(pres, "private void EnsureTempTab()")
    assert "ContactUiBuilder.BuildTabPill(" in body, "「临时」Tab 没走运行时克隆"
    assert "LayoutTabsEqually" in body, (
        "加了第四个 Tab 却没重排 —— 新胶囊会叠在「群聊」上，"
        "uGUI 只给最上层 raycast ⇒ 表现为某个 Tab 点不动，且零报错"
    )
    # 克隆不带走运行期 AddListener，必须补挂
    assert "ClickUtils.Attach(GetButton(pill)" in body, \
        "克隆出来的 Tab 没补挂点击（Instantiate 只复制序列化状态，Init 里那次 Attach 早跑了）"


def test_tab_layout_is_equal_split_formula():
    """四个 Tab 的几何必须是**段公式**，不许出现写死的分段锚点。"""
    builder = _read("UI/ContactUiBuilder.cs")
    body = _method_body(builder, "public static void LayoutTabsEqually(Transform tabs, GameObject[] pills, float width)")
    assert "(float)seg / n" in body and "(float)(seg + 1) / n" in body, "锚点必须是段公式"
    # 只查"写死的段边界"（0.25/0.5/0.75），不查 0.5 —— pivot 本来就该是 0.5 居中。
    for hard in ("0.25f", "0.75f"):
        assert hard not in body, f"写死了分段锚点 {hard} —— 段数一变就会漂移"
    assert "anchorMin = new Vector2(a0, 0f)" in body and "anchorMax = new Vector2(a1, 1f)" in body, \
        "锚点必须用算出来的 a0/a1"
