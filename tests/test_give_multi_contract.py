"""多件赠送契约（09-17 用户实测："只有第一个物体成功，其余没生效，但工具返回成功"）。

**两条独立的缺陷**（都要锁）：

1. **引擎限制**：原生 `UnitActionRoleGive` **一次只处理一件**。
   真机两次自证：`giveProps=4 → receiveProps=1 refuseProps=0`、`giveProps=3 → receiveProps=1`；
   签名里 `propsData` 是**单数**（列表只是账本，`OnEnd` 的闭包按下标找"这一件"）；
   配表剧情 21701 的文案就是单数：「我这里有一个{0}准备赠于你，你需要此物吗？」+「我收下了／我不需要这个」。
   ⇒ 多件必须走我们自己的窗 + 直写（1 件仍走原生，零风险）。

2. **报告失真**：结果里 `items` 是**我们拆栈时的愿望单**，而引擎真值在
   `accepted_count`（= `receiveProps.Count`）。旧渲染只在 `refused_count > 0` 时才提"部分收下"，
   否则一律写"收下了赠礼（全列）" ⇒ 1 件被写成 4 件，模型据此在游戏里说"都给你了"。

本文件锁住：
  · C#：多件分支存在且**没有**再建 `UnitActionRoleGive`；单件分支仍在；
    窗开不起来/入包失败一律 `RollbackGive`（拆出来的道具不能吞）；结果带引擎真值
    `accepted_count`/`refused_count`/`delivered_all`；副作用（好感 + 双份角色日志）复刻；
  · 渲染层：按引擎真值说话（未送达的那几件要点名），且单件/部分拒收/全拒收三条旧行为不回归；
  · 两侧都留了"好感探针"（原生公式不可见 ⇒ 真机核对，不许猜）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"

from agent_loop.tools import text_render as tr  # noqa: E402
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）


def _cs(name: str) -> str:
    return (CS / name).read_text(encoding="utf-8")


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


def _economy() -> str:
    return _body(_cs_tools.flat_src(), " JObject EconomyItem(JObject args)")


def _r(data: dict, args: dict | None = None) -> str:
    return tr.render("economy_item", args or {"initiator": "姜萌"}, {"success": True, "data": data})


BASE = {"target": "唐炎", "via": "direct", "same_grid": True}
FOUR = [{"item": "六品化瘀丹", "requested": 10, "actual": 10, "mode": "props"},
        {"item": "六品蓄力丹", "requested": 3, "actual": 3, "mode": "props"},
        {"item": "六品玉琼丹", "requested": 3, "actual": 3, "mode": "props"},
        {"item": "五品培元丹", "requested": 3, "actual": 3, "mode": "props"}]


# ---------------------------------------------------------------------------
# C# 接线
# ---------------------------------------------------------------------------

def test_multi_item_no_longer_goes_through_the_native_action():
    """≥2 件：不能再建 `UnitActionRoleGive`（它只处理第一件 —— 真机 receiveProps=1）。"""
    src = _economy()
    assert re.search(r"if \(giveList\.Count > 1\)", src), \
        "多件分流判据没了（多件会落回原生动作 ⇒ 只送出一件）"
    i = src.index("if (giveList.Count > 1)")
    j = src.index("var giveAction = new UnitActionRoleGive(")
    multi = src[i:j]
    assert "UnitActionRoleGive(" not in multi, "多件分支里还建了原生动作（只会送出一件）"
    assert "JObject opened = Claimed(wub, () => ShowDramaService.ShowConfirm(" in multi, \
        "多件分支没走自制确认窗（或没把窗的结果接住）"
    assert "ModIds.DramaEconomyItemBase" in multi, "多件窗没占 economy 段（与纯灵石窗同段）"


def test_multi_item_reject_puts_the_items_back():
    """★ 09-24 补的洞（用户选的方案 A）★ 多件窗被**拒绝**时必须把已拆出的道具放回。

    多件是"先拆栈、再问"（要列清单就得先把道具挪到手上），而 `ShowConfirm` 原先**只有 onOk**：
    玩家点「我不需要这个」只回一句 error、没有任何回调 ⇒ 那批已经离开背包的道具**凭空消失**。
    契约两半：
      ① 窗口侧：`ShowConfirm` 要有 `onNo` 参数，且在 `Resolve` **之前**执行（否则模型先收到
         "没送成"、游戏状态还没还原，两边对不上账）；
      ② 调用侧：多件分支必须传 `onNo: () => RollbackGive(wub, giveList)`。
    单件原生那条不受影响：它的退还是引擎自己的 `FeedbackAllProps()`（我们**不许**在那儿再放一次，
    那会造出重复道具）。
    """
    svc = _cs("UI/ShowDramaService.cs")
    assert "Action onNo = null" in svc, "ShowConfirm 没有 onNo 参数（拒绝路径又没人善后）"
    body = _body(svc, "public static JObject ShowConfirm(")
    i_no = body.index("onNo != null")
    i_resolve = body.index("DramaGate.Resolve(slot, ToolResult.Fail(noTip")
    assert i_no < i_resolve, "善后跑在 Resolve 之后 —— 模型先看到结果、游戏状态后还原"
    assert "try { if (onNo != null) onNo(); } catch { }" in body, \
        "善后没吞异常（拒绝路径炸掉会让槽位收不了口 = 永久占用）"

    multi = _economy()
    multi = multi[multi.index("if (giveList.Count > 1)"):]
    assert "onNo: () => RollbackGive(wub, giveList)" in multi, \
        "多件分支没传 onNo —— 玩家不收就是道具消失（本洞正是这么漏的）"
    # onOk / noTip / onNo 必须在**同一次** ShowConfirm 调用里（别写成事后补一次、条件又漏）
    i_open = multi.index("ShowDramaService.ShowConfirm(")
    call = multi[i_open: multi.index(")));", i_open) + 4]
    assert "onOk: () => GivePropsByWindow(" in call, "多件窗没接 onOk（收下时的入包）"
    assert "onNo: () => RollbackGive(wub, giveList)" in call, "onNo 不在这次调用里"
    assert 'noTip: "玩家没有收下赠礼"' in call, "拒绝文案不在这次调用里"


def test_single_item_still_uses_the_native_action():
    """1 件仍走原生：原生立绘/文案/副作用都是精确的，不为了统一而牺牲它。"""
    src = _economy()
    j = src.index("var giveAction = new UnitActionRoleGive(")
    tail = src[j:]
    assert "wub.CreateAction(giveAction, true)" in tail, "单件不再走原生动作了"
    assert "UnitActionPending.Register(giveAction.Pointer" in tail, "单件缺少完成挂起（结果会早发）"


def test_rolled_back_whenever_the_items_left_the_bag():
    """道具已经拆出背包 ⇒ 任何"没送成"的出口都必须 `RollbackGive`（吞道具是本仓最贵的 bug）。"""
    src = _economy()
    multi = src[src.index("if (giveList.Count > 1)"):src.index("var giveAction = new UnitActionRoleGive(")]
    b = _body(_cs_tools.flat_src(),
              " JObject GivePropsByWindow(\n            WorldUnitBase giver, WorldUnitBase receiver,")
    assert b.count("RollbackGive(") >= 2, "GivePropsByWindow 的两条失败路（背包不可用/入包异常）必须回滚"
    # 「窗没开起来」出口的回滚要**钉在该出口内**：multi 段里还有 onNo 一处 RollbackGive，
    # 早前 `"RollbackGive(" in multi` 被它顶真 ⇒ 变异③（删这处回滚）漏网（10-15 复核实测）。
    i_fail = multi.index("if (opened == null")
    assert "RollbackGive(" in multi[i_fail:i_fail + 320], \
        "窗没开起来的出口里没有回滚（道具会凭空消失；别用同段别处的 RollbackGive 顶真）"


def test_result_reports_engine_truth_and_full_delivery():
    """结果必须同时给：引擎真值计数 + "这次是不是全部到账"（渲染层据此说话）。"""
    b = _body(_cs_tools.flat_src(),
              " JObject GivePropsByWindow(\n            WorldUnitBase giver, WorldUnitBase receiver,")
    for key in ("accepted_count", "refused_count", "delivered_all"):
        assert f'["{key}"]' in b, f"多件结果缺 {key}"
    assert '["accepted_count"] = giveList.Count' in b, "多件的 accepted_count 不是实际送出的栈数"


def test_side_effects_are_replicated_with_native_keys():
    """我们自己落刀 ⇒ 必须自己补原生那两件副作用：好感 + 双份角色日志（key 取自配表）。"""
    b = _body(_cs_tools.flat_src(),
              " void GiveSideEffects(\n            WorldUnitBase giver, WorldUnitBase receiver,")
    # flat_src 已抹类名前缀，所以这里只写函数名
    assert "ApplyGiftIntim(receiver, giver" in b, "收礼方对送礼方的好感没加（收礼的语义：receiver→giver）"
    assert "eventLogRoleGive21311" in b and "eventLogRoleGive21312" in b, \
        "角色日志没照抄原生 key（近况里会看不到这次赠礼）"
    assert "UnitConditionData(giver, receiver)" in b, "日志缺 {name|A}/{name|B} 的单位上下文"


def test_intim_constant_comes_from_conf_and_is_probed():
    """好感值取自配表（`RoleGive.closeParaA`，回退 15）并以 `closeOnceAddMax` 封顶；

    写入走「**引擎换算**」层（`ApplyIntim`：只传请求值、回读实得 —— **不校正、不补差**，
    10-15 用户定案：`AddIntim` 的系数换算就是游戏本身的口径，我们只如实报出）；
    且**两侧都留探针**（原生公式不可见 ⇒ 真机核对）。"""
    src = _cs_tools.flat_src()
    assert "const int IntimStepDefault = 15;" in src, "配表基准常量没了"
    assert "g.conf.roleGive.closeParaA" in src, "好感值没从配表读（写死 15 会被官方改表打脸）"
    assert "closeOnceAddMax" in src, "没按配表单次上限封顶"
    assert re.search(r"(private|internal) static int ApplyIntim\(", src), \
        "好感写入层没了"
    assert "rel.SetIntim(" not in src, \
        "又出现了 SetIntim（10-15 已定案：换算交引擎、我们只回读；写绝对值会绕过 RoleClose 衰减）"
    assert "单件原生·补差" not in src, "'补差'该删（同上：不再校正，只留探针）"
    assert src.count('ModMain.P("[Give] 好感探针') >= 2, \
        "原生路径与多件直写路径都要有探针（注释不算）—— 只留一侧就没法回答「配表 15 对不对」"


# ---------------------------------------------------------------------------
# 渲染层：按引擎真值说话
# ---------------------------------------------------------------------------

def test_renderer_reports_only_what_the_engine_accepted():
    """真机同款（4 件只成 1 件）：必须点名"未送达"，不能再说全部收下。"""
    t = _r({**BASE, "items": FOUR, "accepted": True, "accepted_count": 1, "refused_count": 0})
    # 逐字钉"收下的那件"与"没送到的那几件"：只查"这两个名字出现过"是假的 ——
    # 把 got/miss 切反、或 got 按愿望单全列，两种错法都能让那种弱断言通过（变异⑬⑭ 实测）。
    assert "收下了赠礼（六品化瘀丹×10）" in t, t
    assert "未送达：六品蓄力丹×3、六品玉琼丹×3、五品培元丹×3" in t, t
    assert "引擎一次只处理一件" in t, t


def test_renderer_says_all_delivered_when_they_all_were():
    """多件直写（delivered_all）：accepted_count == 件数 ⇒ 正常"收下了赠礼（全列）"。"""
    t = _r({**BASE, "items": FOUR[:2], "accepted": True, "accepted_count": 2,
            "refused_count": 0, "delivered_all": True})
    assert "收下了赠礼（六品化瘀丹×10、六品蓄力丹×3）" in t, t
    assert "未送达" not in t, t


def test_old_shapes_do_not_regress():
    """单件原生 / 部分拒收 / 全拒收三条旧文案逐字不动（两侧升级不同步时不回归）。"""
    one = [{"item": "六品玉琼丹", "requested": 14, "actual": 14, "mode": "props"}]
    t = _r({**BASE, "items": one, "accepted": True, "refused_count": 0})
    assert "收下了赠礼（六品玉琼丹×14）" in t and "未送达" not in t, t
    t = _r({**BASE, "items": one, "accepted": False, "refused_count": 2, "accepted_count": 12})
    assert "收下了部分赠礼" in t and "拒收了其余2件" in t, t
    t = _r({**BASE, "items": one, "accepted": False, "refused_count": 14, "accepted_count": 0})
    assert "拒绝了赠礼" in t and "「我不需要这个」" in t, t


def test_no_accepted_count_means_old_payload():
    """旧 C# 载荷（无 accepted_count）保持旧文案 —— 缺字段时不做任何推断。"""
    t = _r({**BASE, "items": FOUR, "accepted": True, "refused_count": 0})
    assert "收下了赠礼（六品化瘀丹×10、六品蓄力丹×3、六品玉琼丹×3、五品培元丹×3）" in t, t
    assert "未送达" not in t, t
