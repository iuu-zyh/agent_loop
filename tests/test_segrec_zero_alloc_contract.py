"""segrec 零分配改造契约（10-21）：层顶"名字"跨语言读取必须走指针缓存，稳态帧零字符串。

**症状来源**：真机 TickProfiler 显示剧情一出现，segrec 每帧 0.37~0.45ms、gc0 从 +1~2/5s 涨到
+8~10/5s，散落 12~28ms GC 停顿 = 用户体感"走路时走几步顿一下"。
根因：`DramaWindowName` 与 `ActionShowing` 各在每帧读一次层顶窗的 `.name`（每次跨引擎边界
造一个**新字符串**），再加确认窗查询键每帧 `new` 一次。

**修法**（改的是判据内部，**调用点一字不动**）：
层顶按"原生指针"缓存名字（窗口活着名字就不变），指针没动就复用；可见性不缓存（每帧照读，
无分配）；确认窗查询键整局建一次。防伪＝地址复用（旧窗销毁、新窗同址）⇒ TTL 每 20 帧强制
重读对账，最坏电平错 0.15s 且自愈，远小于核的交接判据粒度（A↑ 边沿实测 0.5s 级）。

钉六件事：
  A. `TopLayerState` 存在且形态正确：按 `Pointer` 判等缓存、TTL 强制重读、空层顶不缓存名字、
     ui 空与异常都复位缓存、可见性每帧真读（不许被缓存短路）
  B. `DramaWindowName` 体内：确认窗键只在 `_dynKeyInit` 闸后建**一次**；不再直接 `UiName`/
     `TopOfLayer`（那是逐帧字符串的来源）；家族判据语义保留（可见 && 含 Drama）
  C. `ActionShowing` / `BattlePreShowing` 同走 `TopLayerState`，且保留"可见 && 名字族"组合
  D. segrec 的调用点**不许动**（契约 C 组"采样四件套+唯一实现"的既有锁定继续成立）：
     Tick 仍问 `DramaWindowName()` 与 `ActionShowing()`，注册形态仍是 `Frame(new Action(Tick), 1, true)`
  E. TTL 常数在 1..30 帧（太短＝白缓存；太长＝地址复用窗口超出核的判据粒度）
  F. BusyReason 的 Mask/Window 层读名（事件路径）不许被顺手改成缓存外漏——它仍走 `UiName`
     （低频才跑，保持"日志里是人话"即可；这条防的是"顺手缓存所有层"把低频语义也改错）
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "csharp"
NA = CS / "npc" / "NpcInitiativeMonitor.cs"
REC = CS / "drama" / "DramaSegmentRecorder.cs"
# ★10-25 第二步★ 状态判据整段搬到 `state/`（`NpcInitiativeMonitor` 只留同名转发）。
# 本文件钉的是**零分配与语义**，那些实现现在住在下面两个文件里；
# 断言改成盯新址，并额外要求那个转发必须仍是**一行直通**（防止判据回流）。
FACTS = CS / "state" / "WorldStateFacts.cs"
CORE = CS / "state" / "WorldStateCore.cs"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """去掉 /// 文档注释与 // 行注释 —— 断言"不许出现某实现"时，注释里提到它不算。"""
    src = re.sub(r"///.*", "", src)
    return re.sub(r"//.*", "", src)


def _method(src: str, sig: str) -> str:
    """从签名起做花括号配对，截出完整方法体（含签名行）。"""
    i = src.index(sig)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError(f"方法体没闭合: {sig}")


def _forward_line(src: str, sig: str):
    """取表达式体方法的那一行（`sig => expr;`）。找不到返回 None。

    为什么要单独一个助手：转发写成 `=> expr;` 时**没有花括号**，`_method`（按花括号配对）
    会一路切到文件里下一个 `{`，把整段别的代码当成"方法体"——断言于是既假绿又荒唐。
    """
    return re.search(re.escape(sig) + r"\s*=>\s*([^;]+);", src)


CODE = _code_only(_read(NA))
REC_CODE = _code_only(_read(REC))
FACT_CODE = _code_only(_read(FACTS))
CORE_CODE = _code_only(_read(CORE))


# ---------- A. 层顶电平缓存本体 ----------

def test_top_layer_state_caches_by_pointer_with_ttl():
    body = _method(FACT_CODE, "internal static bool TopLayerState(out string name)")
    assert "top.Pointer" in body, "没按原生指针判等（缓存没建立，逐帧字符串依旧）"
    assert "p != _topPtr" in body, "指针变化没有触发重读（层顶换人会看不见）"
    assert "--_topVerifyCd <= 0" in body, "TTL 强制重读没了（地址复用骗过缓存将无法自愈）"
    assert "_topNameValid = p != IntPtr.Zero" in body, "空层顶不许缓存名字这条丢了"
    assert "return UiVisible(top);" in body, "可见性必须每帧真读（不许缓存短路）"


def test_top_cache_resets_on_no_world_and_on_error():
    body = _method(FACT_CODE, "internal static bool TopLayerState(out string name)")
    null_branch = body.split("if (ui == null)")[1].split(";")[0]
    assert "ResetTopCache()" in null_branch, "ui 空（换世界/未就绪）必须复位缓存"
    assert "catch" in body and "ResetTopCache()" in body.split("catch")[-1], \
        "异常路径必须复位缓存（坏状态不许带出这一帧）"


def test_ttl_window_is_small():
    m = re.search(r"TopVerifyFrames\s*=\s*(\d+)", FACT_CODE)
    assert m, "TTL 常数没定义"
    n = int(m.group(1))
    assert 1 <= n <= 30, f"TTL={n} 超出 1..30：太长则地址复用骗过缓存的窗口超过核判据粒度"


# ---------- B. DramaWindowName：键一次、名走缓存 ----------

def test_drama_window_name_allocates_nothing_per_frame():
    """逐帧路径"零额外分配"这条保证，在 10-25 第二步之后由**两个 helper** 承担。

    重构把原来一坨的 `DramaWindowName` 拆成"读两个电平"（Facts）+ "组合"（Core），
    每帧走的是 Facts 那两个 helper。所以断言跟着落到它们身上 —— **保证不变，只是分居两处**：

      · 确认窗键：`OwnDramaWindowVisible` 里必须还有"只建一次"的闸；
      · 层顶名字：`UiLayerTopName` 必须走 `TopLayerState` 的指针缓存；
      · 组合器：`WorldStateCore.DramaWindowReason` 是**纯函数**（不碰 UI，因此零分配）。

    另外要求转发仍是**一行直通**：判据一旦回流到 `NpcInitiativeMonitor`，
    "每帧分配"就会真的回来（那边没有再走缓存的可能）。
    """
    own = _method(FACT_CODE, "internal static bool OwnDramaWindowVisible()")
    assert "if (!_dynKeyInit)" in own and "_dynKeyInit = true;" in own, \
        "确认窗查询键没有'只建一次'的闸（每帧 new 的垃圾回来了）"
    assert own.count("new UIType.UITypeBase") == 1, \
        "OwnDramaWindowVisible 里出现第二个 new UITypeBase（查询键必须只剩初始化那一个）"
    assert "ui.GetUI(_dynKey)" in own and "UiVisible(" in own, \
        "确认窗仍须逐帧判**可见**（键缓存后判据形态不变，`test_drama_gate_contract` 同源锚定）"

    top = _method(FACT_CODE, "internal static string UiLayerTopName()")
    assert "TopLayerState(out" in top, "层顶名字必须走电平缓存，不许逐帧跨语言读名"
    assert "UiName(" not in top, "逐帧直读层顶名的旧路径必须绝迹"

    # 组合器必须是纯函数（不碰 g.ui / UiName），否则零分配保证就漏一处
    comb = _method(CORE_CODE, "public static string DramaWindowReason(bool ownDramaWindowVisible, string uiLayerTopName)")
    assert "g.ui" not in comb and "UiName" not in comb and "GetUI" not in comb, \
        "组合器碰了 UI —— 它必须保持纯函数（每帧走它，碰 UI 就绕过了缓存）"

    # 转发必须一行直通。⚠ 表达式体方法（`=> expr;`）没有花括号，不能用 `_method`
    #   （那个助手按花括号配对，会一路切到文件里下一个 `{`，得出荒谬的结论）
    m = _forward_line(CODE, "internal static string DramaWindowName()")
    assert m, "DramaWindowName 不再是表达式体转发（判据可能回流了）"
    assert "WorldStateFacts.DramaWindowReason()" in m.group(1), \
        "DramaWindowName 没转发到事实层"


def test_drama_window_name_keeps_semantics():
    """语义（两族窗口 + 两句原因文案）现在住在纯逻辑核里 —— 文案逐字保留，别改。"""
    body = _method(CORE_CODE, "public static string DramaWindowReason(bool ownDramaWindowVisible, string uiLayerTopName)")
    assert 'return "我方确认窗 UICustomDramaDyn 可见";' in body
    assert 'return "剧情窗开着（" + uiLayerTopName + "）";' in body
    assert 'uiLayerTopName.IndexOf("Drama", StringComparison.OrdinalIgnoreCase) >= 0' in body


# ---------- C. 两个名字族判据同走缓存 ----------

def test_action_showing_uses_shared_level_cache():
    body = _method(FACT_CODE, "internal static bool ActionShowing()")
    assert "TopLayerState(out n)" in body, "ActionShowing 没走层顶缓存（每帧读名的垃圾还在）"
    assert 'n.IndexOf("ActionCourseInfo"' in body and "n.Length > 0" in body, \
        "名字族判据变了（必须仍是 可见 && 含 ActionCourseInfo）"
    assert "TopOfLayer(UILayer.UI)" not in body and "UiName(" not in body, \
        "旧的两步直读必须绝迹"


def test_battle_pre_showing_uses_shared_level_cache():
    body = _method(FACT_CODE, "internal static bool BattlePreShowing()")
    assert "TopLayerState(out n)" in body
    assert 'n.IndexOf("MapBattlePre", StringComparison.OrdinalIgnoreCase) >= 0' in body


# ---------- D. 调用点不许动（既有"唯一实现"契约的延伸） ----------

def test_recorder_call_sites_unchanged():
    assert "NpcInitiativeMonitor.DramaWindowName()" in REC_CODE, \
        "segrec 的 W 不再复用 DramaWindowName —— '唯一实现'契约被破"
    assert "NpcInitiativeMonitor.ActionShowing()" in REC_CODE, \
        "segrec 的 A 不再复用 ActionShowing"
    assert "Core.Tick(w, b1, b2, a, Now())" in REC_CODE
    # 11-19 起常驻回调统一走 `FrameTicker`（本文件注释也已写明契约改成这条）——
    # 判据仍是"常驻 + 每帧一次"，只是换了个 API。
    assert "FrameTicker.Ensure(FrameTicker.KSegRec" in REC_CODE, \
        "注册形态变了（常驻由既有契约锁定，零分配改造不碰它）"


# ---------- F. 低频路径没被顺手改坏 ----------

def test_mask_window_reason_still_reads_names_directly():
    """BusyReason 的 Mask/Window 层（低频）仍用 UiName 现读现拼 —— 那是日志文本，不是逐帧热路径。"""
    # 现在由两个 explicit helper 承担：`MaskLayerName` / `WindowLayerName`（事实层，现读现拼），
    # 具体拼名字的那一句收在共用助手 `VisibleLayerName` 里（两个层判据共用一处，
    # 免得一边加了"可见但没名字"的占位符、一边忘了）。所以检查点分两处：
    #   · 两个入口必须**各自直接取自己那一层**（Mask / Window），不许走 UI 层的层顶缓存；
    #   · 真正读名字的那一句在助手里，仍是现读现拼。
    for m, layer in (("internal static string MaskLayerName()", "UILayer.Mask"),
                     ("internal static string WindowLayerName()", "UILayer.Window")):
        body = _method(FACT_CODE, m)
        assert layer in body, f"{m} 没取自己那一层（取错层 = 判据看错界面）"
        assert "TopLayerState" not in body, f"{m} 误用了层顶缓存（那是 UI 层的名字族判据专用）"
        assert "VisibleLayerName(" in body, f"{m} 没走共用助手（两处会分叉）"
    helper = _method(FACT_CODE, "private static string VisibleLayerName(UIBase top)")
    assert "UiName(" in helper, "VisibleLayerName 没现读现拼 UiName（层顶缓存只服务 UI 层的名字族判据）"
    assert "TopLayerState" not in helper, "VisibleLayerName 误用了层顶缓存"
