"""工具层「改动是否生效」的现场证据契约（11-20，用户要求"改动的部分加日志"）。

## 为什么这一层必须有日志

11-19/11-20 把 6711 行的 `ToolExecutor.cs` 拆成 13 个类。拆分**本身不报错**：
旧 DLL 能跑、新 DLL 也能跑，日志里长得几乎一样。真正的风险是三种：

  ① **部署的还是旧 DLL**（路径对但文件没换、或换了没重启）——
     现象是"新功能没反应"，而所有旧日志照常打印；
  ② **编译没带上新类**（csproj 漏了、条件编译把它排除了）——
     现象和 ① 一模一样，靠 `[Build]` 那种**手写字符串**永远分不出来；
  ③ **拆完之后日志被打散**：同一件事的几行落到不同文件，排障时按前缀 grep 会漏。

所以这一层要两行**运行时事实**，不是又一句手写文案：

    [ToolLayer] 工具层 13 类（13 在册）：ToolExecutor ToolArgs … NativeActionService｜分发 10 个工具名
    [Tool] inspect_unit 3ms ok → QueryTool.cs

第一行用**反射**查类型（`Assembly.GetType`），回答"加载进来的这份 DLL 里到底有没有这 13 个类"；
第二行每次调用都打，带**落点文件名**，回答"这次调用真的走的是新分层，而不是老的单一实现"。

## 本文件钉住什么

  · 自检行确实由反射产出（不是又一个拼死的字符串）；
  · 自检的类清单 == `tests/_cs_tools.SPLIT_CLASSES` == 磁盘上真实存在的类（三处同源）；
  · 路由表 `_ToolHome` 的键 == `schemas.TOOL_ORDER`（少一个 = 有工具调用时不打落点行）；
  · 落点行在 `finally` 里发（异常/模态挂起/正常返回三条路都不能漏）；
  · 每一行都有 `[Tag]` 前缀（仓库日志约定，`test_no_ai_wording_contract` 依赖它区分日志与界面文案）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import _cs_tools

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"
EXEC = CS / "tools" / "ToolExecutor.cs"
MAIN = CS / "app" / "ModMain.cs"


def _schemas():
    import importlib

    parent = str(ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    return importlib.import_module(f"{ROOT.name}.tools.schemas")


def _code(src: str) -> str:
    """剥掉 `//` 行注释（注释里写 `[Tool]` 字样是合法的，不该被当实现）。"""
    out = []
    for ln in src.splitlines():
        i = ln.find("//")
        if i >= 0 and not re.search(r'"\s*//', ln[:i]):
            ln = ln[:i]
        out.append(ln)
    return "\n".join(out)


def _body(src: str, sig: str) -> str:
    i = src.index(sig)
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
    raise AssertionError(f"方法体未闭合：{sig}")


# ══════════════════════════════════════════════════════════════════
# ① 启动自检：必须是运行时事实，不是手写字符串
# ══════════════════════════════════════════════════════════════════

def test_layout_selfcheck_uses_reflection_not_a_hardcoded_string():
    """`DescribeLayout` 必须**真的去问程序集**，不许返回一句写死的话。

    判据：函数体里同时出现 `GetType`（真查）与 `Assembly`（查的是哪份程序集）。
    只有字符串拼接、没有 `GetType` 的版本，在"部署了旧 DLL"时照样打印"13 类都在"——
    那正是要避免的假阳性。
    """
    code = _code(EXEC.read_text(encoding="utf-8"))
    b = _body(code, "internal static string DescribeLayout()")
    assert "GetType(" in b, "自检没有真去查类型（返回写死字符串 = 分不出旧 DLL）"
    assert "Assembly" in b, "自检没说明查的是哪份程序集"
    # 必须区分"在册"与"缺失"，否则缺了也是同一句话
    assert "miss" in b, "自检没有记录缺失项（缺类时应当明说）"
    assert "旧 DLL" in b, "缺类时没点名最可能的原因（部署了旧 DLL / 编译没带上）"


def test_selfcheck_class_list_matches_the_split():
    """自检里的类清单 == `SPLIT_CLASSES` == 磁盘上的真实文件。

    三处同源的理由：类名清单是本轮拆分的**唯一事实**。
      · 自检少写一个 → 那个类就算真没进 DLL 也查不出来（自检失效）；
      · `SPLIT_CLASSES` 少写一个 → `flat_src()` 不抹它的前缀，几十条契约测试集体红；
      · 磁盘上少一个 → 编译就过不去（这条是兜底，不属于日志面）。
    """
    code = _code(EXEC.read_text(encoding="utf-8"))
    b = _body(code, "internal static string DescribeLayout()")
    # 取函数里那个 string[] 字面量块
    arr = re.search(r"string\[\]\s+names\s*=\s*\{(.*?)\};", b, re.S)
    assert arr, "自检里没有类名数组 names"
    names = re.findall(r'"(\w+)"', arr.group(1))
    assert sorted(names) == sorted(_cs_tools.SPLIT_CLASSES), (
        f"自检的类清单与 SPLIT_CLASSES 不一致：\n"
        f"  自检独有 {sorted(set(names) - set(_cs_tools.SPLIT_CLASSES))}\n"
        f"  SPLIT_CLASSES 独有 {sorted(set(_cs_tools.SPLIT_CLASSES) - set(names))}"
    )
    # 每个都真的存在于磁盘（改名/删类时别忘同步）
    on_disk = {p.stem for p in (CS / "tools").glob("*.cs")}
    missing = [n for n in names if n not in on_disk]
    assert not missing, f"自检里点名了不存在的类：{missing}"


def test_selfcheck_is_called_from_init():
    """自检必须在 `ModMain.Init` 打一行 —— 那是唯一"游戏一启动必执行"的时机。"""
    main = _code(MAIN.read_text(encoding="utf-8"))
    assert "ToolExecutor.DescribeLayout()" in main, "Init 里没有调工具层自检"
    assert 'P("[ToolLayer] ' in main, "自检那行没有 [ToolLayer] 前缀（会被当成界面文案）"
    # 包了 try/catch：自检本身绝不能把 Init 拖垮
    i = main.index("ToolExecutor.DescribeLayout()")
    assert "catch" in main[i : i + 400], "自检没包 try/catch（异常会中断 Init）"


# ══════════════════════════════════════════════════════════════════
# ② 落点行：每次调用都打，且带落点文件
# ══════════════════════════════════════════════════════════════════

def test_tool_home_covers_every_dispatched_tool():
    """`_ToolHome` 的键 == `schemas.TOOL_ORDER`。

    少一个键的后果不是报错，是那个工具调用时打 `→ (未登记)` ——
    而这恰恰发生在"最想知道它落到哪个文件"的时候。
    """
    code = _code(EXEC.read_text(encoding="utf-8"))
    keys = set(re.findall(r'\{\s*"(\w+)",\s*"\w+Tool\.cs"\s*\}', code))
    order = set(_schemas().TOOL_ORDER)
    assert keys == order, (
        f"_ToolHome 与 TOOL_ORDER 不一致：\n"
        f"  路由表独有 {sorted(keys - order)}\n"
        f"  TOOL_ORDER 独有 {sorted(order - keys)}"
    )


def test_tool_home_points_at_real_files():
    """路由表的每个目标文件都真的存在（拆分的落点写错 = 日志骗人）。"""
    code = _code(EXEC.read_text(encoding="utf-8"))
    homes = set(re.findall(r'"\w+",\s*"(\w+Tool\.cs)"', code))
    on_disk = {p.name for p in (CS / "tools").glob("*.cs")}
    # NativeActionService 等不带 Tool 后缀的类不在路由表里（它们不是工具入口）
    bad = sorted(h for h in homes if h not in on_disk)
    assert not bad, f"路由表指向不存在的文件：{bad}"


def test_dispatch_log_is_emitted_in_finally():
    """落点行必须在 `finally` 里发 —— 三条出口（正常 / 异常 / `__pending__`）都要走到。

    写在 `return res` 之前的版本会漏掉异常路；写在 `catch` 里的版本会漏掉正常路。
    `finally` 是这里唯一不用逐条枚举出口的位置。
    """
    code = _code(EXEC.read_text(encoding="utf-8"))
    b = _body(code, "public JObject Execute(string name, JObject args)")
    i_finally = b.index("finally")
    assert "LogDispatch(" in b[i_finally:], "落点行不在 finally 里（会漏掉某条出口）"
    assert "LogDispatch(" not in b[:i_finally], "落点行被写在了 finally 之外（那会重复打）"
    # 计时器要在 try 之前起，否则 catch 路没有耗时
    assert b.index("Stopwatch") < b.index("try"), "计时器在 try 之内，异常路的耗时拿不到"


def test_dispatch_log_shape_is_judgeable():
    """落点行的形状：工具名 + 耗时 + 成败 + 失败原文 + 落点文件。

    "ok / FAIL" 用英文：日志里好 grep，也不会与中文文案混在一起看错。
    第三种状态 `pending` 必须单独区分（模态窗已弹、等玩家点选）——
    混进 `ok` 就看不出"玩家点了取消"与"工具自己报错"的区别。
    """
    code = _code(EXEC.read_text(encoding="utf-8"))
    b = _body(code, "private static void LogDispatch(")
    assert '"[Tool] "' in b, "落点行没有 [Tool] 前缀"
    assert "ms " in b, "落点行没有耗时"
    assert '"ok"' in b and '"FAIL"' in b, "落点行没有明确的成败标记"
    assert "pending（等玩家点选）" in b, "挂起态没和 ok/FAIL 分开（模态窗已弹 ≠ 办完了）"
    assert "__pending__" in b, "挂起态没读 __pending__ 标记"
    assert "→ " in b, "落点行没有 → 落点文件"
    # 失败原文要截断：一条长报错会把一行日志撑成十行
    assert "Substring(0, 120)" in b, "失败原文没截断"


# ══════════════════════════════════════════════════════════════════
# ③ 新增的分层日志：前缀合规 + 关键路径确实留痕
# ══════════════════════════════════════════════════════════════════

def test_new_layer_logs_carry_tag_prefix():
    """本轮新加的日志都要 `[Tag]` 前缀（仓库约定；`test_no_ai_wording_contract` 靠它区分日志/界面）。"""
    targets = {
        "tools/ToolExecutor.cs": ("[Tool] ",),
        "tools/ToolArgs.cs": ("[ToolArgs] ",),
        "tools/PropsService.cs": ("[Props] ",),
        "tools/SectQueryTool.cs": ("[Sect] ",),
        "UI/AbPanelPreflight.cs": ("",),          # 沿用各面板自己的 [XxxPanel] 前缀
        "UI/AbPanelLifecycle.cs": ("[AbPanelLifecycle] ",),
    }
    for rel, prefixes in targets.items():
        src = (CS / rel).read_text(encoding="utf-8")
        for p in prefixes:
            assert p in src, f"{rel} 里没有 {p!r} 前缀的日志"
    # `[ToolLayer]` 是**调用方**打的前缀（自检结果由 ModMain 拼进日志行），不在 ToolExecutor 里
    assert 'P("[ToolLayer] ' in (CS / "app" / "ModMain.cs").read_text(encoding="utf-8"), \
        "ModMain 里没有 [ToolLayer] 前缀的自检行"


def test_props_service_logs_the_three_money_moments():
    """灵石转移 / 拆栈 / 回滚 —— 三处原先在**成功路径上完全静默**，现在必须各有一行。

    为什么这三处：它们是"东西真的动了"的唯一时刻。旧实现只在失败时说一句，
    于是"到底动没动、动了多少"只能靠玩家数背包，或者靠余额差反推。
    """
    src = _code((CS / "tools" / "PropsService.cs").read_text(encoding="utf-8"))
    b_transfer = _body(src, "internal static int TransferLingshi(")
    assert 'ModMain.P("[Props] 灵石转移' in b_transfer, "灵石转移成功路径没留痕"
    b_take = _body(src, "internal static int TakePropsFromBag(")
    assert 'ModMain.P("[Props] 拆栈' in b_take, "拆栈成功路径没留痕"
    b_roll = _body(src, "internal static void RollbackGive(")
    assert 'ModMain.P("[Props] 回滚' in b_roll, "回滚没留痕（最该留痕的一步）"
    # 回滚的条数必须取 Clear **之前**的快照，否则恒为 0
    # （⚠ 只断言 "int n = giveList.Count;" 存在是不够的：把它挪到 Clear 之后照样存在 ——
    #   变异②实测漏网过一次，所以这里钉**顺序**）
    i_n = b_roll.index("int n = giveList.Count;")
    i_clear = b_roll.index("giveList.Clear();")
    assert i_n < i_clear, "回滚条数没在 Clear 之前取快照（Clear 之后就恒为 0，日志永远说'回滚 0 件'）"


def test_bind_actor_reports_which_resolution_path_won():
    """发起方解析要区分"id 精确命中"与"按名解析"。

    两者在**没出问题时**长得一模一样；出事时（同名 / 换存档导致 id 失效）
    才分得出责任 —— 而那时日志已经写完了。
    """
    src = _code((CS / "tools" / "ToolArgs.cs").read_text(encoding="utf-8"))
    b = _body(src, "internal static WorldUnitBase BindActor(")
    assert "带 id 精确命中" in b, "成功路径没区分 id 精确 / 按名"
    assert "按名解析" in b, "同上"
    assert "发起方解析失败" in b, "失败路径没留痕"
    assert "已身故" in b, "失败路径没区分'查无此人'与'已身故'（两种自纠路径不同）"


def test_preflight_logs_the_four_passes():
    """前置检查四项全过要打一行 —— 这一层是**收拢后的共用件**，不打卡不出它有没有被走到。

    旧代码也能把面板建出来。有这一行才能确认三面板走的确实是同一份前置检查。
    """
    src = _code((CS / "UI" / "AbPanelPreflight.cs").read_text(encoding="utf-8"))
    b = _body(src, "public static string Check<T>(string prefabKey, bool requireWs)")
    assert "前置四项通过" in b, "四项全过时没留痕"
    assert "无需在线" in b and "在线已连接" in b, \
        "没把 requireWs 的实际取值打出来（配置面板与另两个的区别就在这里）"


def test_lifecycle_logs_zombie_discard():
    """僵尸引用丢弃要留痕 —— "面板忽然再也开不出来"的第一现场就在这里，旧实现全静默。"""
    src = _code((CS / "UI" / "AbPanelLifecycle.cs").read_text(encoding="utf-8"))
    b = _body(src, "public static bool Alive<T>(ref T resident)")
    assert b.count("[AbPanelLifecycle]") >= 2, "两条僵尸路（fake-null / 判空即抛）都要留痕"
