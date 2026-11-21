"""好感机制接线契约（10-12 建；**10-15 改口径**）。

**为什么值得有**（这四件事都曾经是静默的）：

  ① 「引擎换算、我们只回读」——`AddIntim/AddHate` 传请求值即可，换算（含 `RoleClose` 系数衰减：
     0~60 全额 / 60~120 五折 / 120~180 25% / 180~240 12.5% / 240~300 6.25%）由**引擎**决定；
     我们**不校正、不补差**（10-12 版曾用 `SetIntim` 写绝对值，10-15 按用户意见删除），
     只回读实得并在结果里说明衰减（渲染层 `_intim_decay_note`）；
  ② 「唯一写入点」——好感写入散到各处就会互相漂移；本契约把它钉在 `ApplyIntim` 一处；
  ③ 「我们落刀的路径才写入」——凡走原生动作的路径（单件赠礼/原生讨要/双修/切磋/邀约/传功/偷窃）
     **只探针不落刀**，否则一笔交互记两遍；
  ④ 「按 `accepting_gift` 分流」——收下赠礼＝**单边**（收礼方→送礼方 +`RoleGive`）；
     讨要达成＝**双边**（取得方→交付方 +`RoleAskfor.closeParaA`、交付方→取得方 −`closeParaB`）。

断言读 `csharp/tools/IntimService.cs`（好感写入层）与工具层的**源码文本**（这些接线没有可运行的 C# 测试宿主，
与 `test_give_multi_contract.py` 同款做法）。
"""

from __future__ import annotations

import re
from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _src() -> str:
    """工具层源码，**类名前缀已抹平**：拆分前这些是同类内裸调用，现在跨类要带前缀。
    契约断言的是"调没调、参数对不对"，不是"写没写类名"（见 tests/_cs_tools.flat_src）。"""
    return _cs_tools.flat_src()


# `ApplyIntim` 的可见性从 private 改成 internal 了（`TeamTool` 解散时要扣好感，
# 必须跨文件调 —— 10-18）。契约盯的是"有没有这一层、层里做了什么"，
# 不是修饰符，所以按 (private|internal) 匹配。
_APPLY_INTIM_RE = re.compile(r"(private|internal) static int ApplyIntim\(")


def _apply_intim_at(src: str) -> int:
    m = _APPLY_INTIM_RE.search(src)
    assert m, "好感写入层 ApplyIntim 没了"
    return m.start()


def _apply_intim_head(src: str) -> str:
    m = _APPLY_INTIM_RE.search(src)
    assert m, "好感写入层 ApplyIntim 没了"
    return m.group(0)


def _body(src: str, head: str, tail: str = "\n        }") -> str:
    """从 `head` 起到 `tail` 为止（够钉住一个方法体）。"""
    i = src.index(head)
    j = src.index(tail, i)
    return src[i:j]


# ══════════════════════════════════════════════════════════════════
# ① 唯一写入点 + 引擎换算（不校正）
# ══════════════════════════════════════════════════════════════════

def test_only_the_write_layer_touches_intimacy():
    """全仓**只有** `ApplyIntim` 调 AddIntim/AddHate，且**任何地方都不再用 SetIntim**。

    散开写 = 各路径自己发明方向与值；收在一处才谈得上"只回读、如实报"。
    SetIntim 写的是绝对值（绕过引擎的 RoleClose 衰减）—— 10-15 已定案不用，回来就是回归。
    扫描前**剥掉 `//` 行注释**：API 签名清单写在注释里是合法的（写入层头部就有）。
    """
    hits = []
    for f in CS.rglob("*.cs"):
        p = f.as_posix()
        if "/obj/" in p or "/bin/" in p or "/tests/" in p or "/reference/" in p:
            continue
        raw = f.read_text(encoding="utf-8", errors="replace")
        code = "\n".join(ln[:ln.find("//")] if "//" in ln else ln for ln in raw.splitlines())
        for pat in ("AddIntim(", "AddHate(", "SetIntim("):
            if pat in code:
                hits.append((f.name, pat, code.count(pat)))
    assert sorted({h[0] for h in hits}) == ["IntimService.cs"], \
        f"好感写入漏到别的文件了：{hits}"
    assert not [h for h in hits if "SetIntim" in h[1]], \
        f"SetIntim 又出现了（10-15 定案：换算交引擎，我们只回读）：{hits}"
    # 且这两个调用只在写入层体内（别处出现 = 有人绕开写入层自己写）。
    # 计数同样走**剥注释后的代码**（头部 API 签名清单里那两行注释不算）。
    raw = _src()
    code = "\n".join(ln[:ln.find("//")] if "//" in ln else ln for ln in raw.splitlines())
    _written = _cs_tools.tool_file("IntimService.cs")      # 写入层就是这一个文件
    b = _written[_apply_intim_at(_written):]
    assert "AddIntim(" in b and "AddHate(" in b, "写入层里没有写入调用"
    assert code.count("AddIntim(") == 1 and code.count("AddHate(") == 1, \
        f"写入层之外还有写入调用（AddIntim×{code.count('AddIntim(')} / AddHate×{code.count('AddHate(')}）"
    assert code.count("SetIntim(") == 0, "代码里出现了 SetIntim（应只存在于注释说明里）"


def test_write_layer_is_native_only_and_reads_back():
    """写入层：只 AddIntim/AddHate + 回读实得 + 探针；**没有校正/补差/封顶写回**。"""
    b = _body(_src(), _apply_intim_head(_src()))
    assert "rel.AddIntim(oid, delta, 0, \"\", true)" in b, "正向没走原生 AddIntim（原生 tip/账本会丢）"
    assert "rel.AddHate(oid, -delta, 0, \"\", true)" in b, "负向没走原生 AddHate"
    assert "int before = IntimBetween(holder, other);" in b and "int after = IntimBetween(holder, other);" in b, \
        "没有前后回读（结果里的实得 Δ 就没有出处）"
    assert "int actual = after - before;" in b and "return actual;" in b, "没把实得当返回值"
    assert 'ModMain.P("[Intim] ' in b, "写入层没有探针行（真机没法核对配表）"
    assert "SetIntim" not in b, "写入层里出现了 SetIntim（校正口径已废弃）"
    assert "if (before < 0) return int.MinValue;" in b, "起点读不到时没按「未知」处理（会拿 0 冒充真值算 Δ）"


def test_step_constants_live_in_one_block():
    """量级常量集中在写入层顶部（要调只动这四个 + 配表值）。"""
    src = _src()
    blk = src[src.index("// ============ 好感写入层"):]
    blk = blk[:blk.index("/// <summary>读配表整数值")]
    for line in ("const int IntimStepDefault = 15;",
                 "const int IntimStepMax = 120;",
                 "const int LunDaoIntimEach = 10;",
                 "const int TradeIntimEach = 5;"):
        assert line in blk, f"量级常量没在写入层顶部：{line}"


# ══════════════════════════════════════════════════════════════════
# ② 赠礼：收礼方 → 送礼方（单边）
# ══════════════════════════════════════════════════════════════════

def test_gift_paths_wire_receiver_to_giver():
    """赠礼：**收礼方对送礼方** + 配表值。自制路（多件窗/纯灵石窗/直通/NPC 免窗直转）都要有；
    单件原生路径**只探针不落刀**（原生自己算，补差已删）。"""
    src = _src()
    assert 'int got = ApplyGiftIntim(receiver, giver, "多件窗");' in src, "多件赠礼窗没加好感"
    assert 'int intimWindow = ApplyGiftIntim(player, npc, "灵石窗");' in src and \
           'PutIntimWithAfter(okWindow, "intim", intimWindow, player, npc);' in src, "纯灵石赠礼窗没加好感"
    # `PutIntim` 后来升级成 `PutIntimWithAfter`（多回写一个 `intim_after` 真值供模型读）；
    # 判据仍是"直通路径也落好感"，不锁用哪个收口函数。
    assert 'int intimDirect = ApplyGiftIntim(player, npc, "灵石直通");' in src and \
           'PutIntimWithAfter(okDirect, "intim", intimDirect, player, npc);' in src, \
        "纯灵石 AutoConfirm 直通没加好感"
    assert 'ApplyGiftIntim(player, wub, "道具·NPC直转");' in src, "NPC 免窗直转赠礼没加好感"
    assert 'ApplyGiftIntim(player, wub, "灵石·NPC直转");' in src, "NPC 免窗直转灵石没加好感"
    assert "ApplyGiftIntim(player, wub, \"单件原生·补差\")" not in src, "单件原生路径又在补差了（已定案不校正）"


# ══════════════════════════════════════════════════════════════════
# ③ 取得：按 accepting_gift 分流（收下=单边；讨要=双边）
# ══════════════════════════════════════════════════════════════════

def test_receiving_paths_are_single_sided_gift():
    """`accepting_gift`（收下对方赠予）⇒ 单边：收礼方（你）对送礼方 +`RoleGive.closeParaA`。"""
    src = _src()
    assert 'ApplyGiftIntim(wub, player, "收下赠礼");' in src, "收下玩家赠礼（道具）没加好感"
    assert 'ApplyGiftIntim(wub, player, "灵石·收下")' in src, "收下玩家赠礼（灵石）没加好感"
    assert 'ApplyGiftIntim(wub, player, "NPC收下")' in src, "NPC↔NPC 收下赠予没加好感"
    assert 'ApplyGiftIntim(wub, holder, "多件·收下")' in src, "多件收下赠礼没加好感"


def test_ask_paths_are_double_sided():
    """讨要达成 ⇒ **双边**：取得方 +（`closeParaA`）/ 交付方 −（`closeParaB`）。

    四处自制路都要成对出现（灵石窗 / 灵石·NPC 直转 / NPC 取得 / 多件窗）——
    少写一侧就是"被搜刮的人还越发喜欢他"（与原生 `closeParaB=-8` 相反）。
    """
    src = _src()
    # 收口函数已升级为 `PutIntimWithAfter(obj, key, Δ, holder, other)`：除了写 `intim`，
    # 还会回写 `intim_after` 真值（引擎换算后的实际值）。键名与方向判据保持不变。
    pairs = [
        ('PutIntimWithAfter(joLs, "intim", ApplyAskforIntim(wub, player, "灵石窗"), wub, player);',
         'PutIntimWithAfter(joLs, "intim_upset", ApplyAskforUpset(player, wub, "灵石窗"), player, wub);'),
        ('PutIntimWithAfter(joNpcLs, "intim", ApplyAskforIntim(wub, player, "灵石·NPC直转"), wub, player);',
         'PutIntimWithAfter(joNpcLs, "intim_upset", ApplyAskforUpset(player, wub, "灵石·NPC直转"), player, wub);'),
        ('PutIntimWithAfter(joNpcItem, "intim", ApplyAskforIntim(wub, player, "NPC取得"), wub, player);',
         'PutIntimWithAfter(joNpcItem, "intim_upset", ApplyAskforUpset(player, wub, "NPC取得"), player, wub);'),
        ('PutIntimWithAfter(jo, "intim", ApplyAskforIntim(wub, holder, "多件窗"), wub, holder);',
         'PutIntimWithAfter(jo, "intim_upset", ApplyAskforUpset(holder, wub, "多件窗"), holder, wub);'),
    ]
    for plus, minus in pairs:
        assert plus in src, f"讨要正向缺失：{plus}"
        assert minus in src, f"讨要反向缺失（交付方的不满）：{minus}"
    # 值取自配表（成对的两个键）
    assert "g.conf.roleAskfor.closeParaA" in src, "讨要加值没读配表 closeParaA"
    assert "-g.conf.roleAskfor.closeParaB" in src, "讨要减项没读配表 closeParaB（原值 -8）"
    b = _body(src, " int AskforIntimReduceValue()")
    assert "ConfIntim(-g.conf.roleAskfor.closeParaB, 8)" in b, "交付方减项的回退值不是 8"


def test_native_ask_path_does_not_double_write():
    """**单件道具 + 对玩家**仍走原生窗 ⇒ 好感由引擎自己算，我们**不落刀**（只读结局标记）。"""
    src = _src()
    i = src.index("var ask = new UnitActionRoleAskfor(player, stack.SoleID, count);")
    seg = src[i:i + 1400]
    assert "ApplyAskforIntim" not in seg and "ApplyGiftIntim" not in seg, \
        "原生讨要路径上又写了一次好感（引擎自己会算 ±8，会记两遍）"
    assert "isAskforComplete" in seg and "isNPCReduceIntim" in seg, "原生路径的结局标记没读（模型不知成败）"


# ══════════════════════════════════════════════════════════════════
# ④ 多件讨要：自制窗（原生一次只吃一件）
# ══════════════════════════════════════════════════════════════════

def test_multi_ask_goes_through_our_window():
    """`items` ≥2 ⇒ 自制确认窗；原生 `UnitActionRoleAskfor` 一次只吃一个 soleID（签名实证）。"""
    src = _src()
    assert "var wants = ParseAcquireItems(args);" in src, "items 没被解析（多件死参数）"
    assert "if (wants.Count > 1)" in src and "return AskMultiple(wub, player, wants, receiving, toNpc, victimName, target, cpName);" in src, \
        "多件没转自制窗（会退回原生 = 只处理一件）"
    b = _body(src, " JObject AskMultiple(WorldUnitBase wub, WorldUnitBase holder, List<AcquireItem> wants,")
    assert "ModIds.DramaItemAcquireBase" in b and "ShowDramaService.ShowConfirm(" in b, "多件没走自制确认窗"
    assert "Claimed(wub," in b, "多件窗没走 Claimed（闸 1.5 归因凭证会漏）"
    assert "if (toNpc)\n                return WithCounterparty(doTake(), cpName);" in b, \
        "NPC↔NPC 多件没走免窗直转（会弹一扇没人点的窗，挂到超时）"
    # 整笔 + 回滚：任一不足/入包失败都要把已取出的放回
    assert b.count("RollbackGive(holder, taken);") >= 3, "多件的三条失败路没各自回滚（会吞道具）"
    assert "TransferLingshi(holder, wub, totalMoney," in b, "多件没处理混在里面的灵石"
    assert "TransferLingshi(wub, holder, paid," in b, "入包失败时灵石没退回"


def test_multi_ask_result_shape_and_renderer():
    """多件结果带 `items` 数组（渲染层按它列清单）；单件旧形状逐字不变。"""
    import importlib.util
    import sys

    sys.path.insert(0, str(ROOT.parent))
    tr = importlib.import_module(f"{ROOT.name}.tools.text_render")
    multi = {"op": "ask_for", "target": "李四", "items": [{"item": "丹药", "requested": 2},
                                                          {"item": "灵石", "requested": 300}],
             "count": 2, "accepted": True}
    t = tr.render("item_acquire", {}, {"success": True, "data": multi})
    assert t == "你把「丹药」×2、「灵石」×300交给了李四。", t
    one = {"op": "ask_for", "target": "李四", "item_name": "丹药", "count": 4, "accepted": True}
    assert tr.render("item_acquire", {}, {"success": True, "data": one}) == "你把「丹药」×4交给了李四。"


# ══════════════════════════════════════════════════════════════════
# ⑤ 本项目规则（游戏无配表）+ 原生路径只探针
# ══════════════════════════════════════════════════════════════════

def test_trade_and_lun_dao_have_project_rules():
    """交易与论道：游戏**没有**原生钩子（ConfRole* 无 Trade/lun_dao 键）⇒ 按本项目规则补，且可核对。"""
    src = _src()
    assert 'ApplyIntim(seller, buyer, TradeIntimEach, "交易·卖方对买方")' in src, "有价成交：卖方对买方没加"
    assert 'ApplyIntim(buyer, seller, TradeIntimEach, "交易·买方对卖方")' in src, "有价成交：买方对卖方没加"
    assert 'PutIntimWithAfter(intimPair, "buyer_to_seller",' in src and \
           'ApplyGiftIntim(buyer, seller, "交易赠予")' in src, "price=0 的赠予语义没接"
    assert "ApplyLunDaoIntim(npc, out int dNpcToPlayer, out int dPlayerToNpc);" in src, "论道没做双方好感结算"
    assert "LunDaoIntimTopUp" not in src, "论道还在 top-up（已定案：直接各 +10，让引擎换算）"
    b = _body(src, " void ApplyLunDaoIntim(WorldUnitBase npc,")
    assert "ApplyIntim(npc, player, LunDaoIntimEach," in b and "ApplyIntim(player, npc, LunDaoIntimEach," in b, \
        "论道不是双向各 +10"


def test_native_paths_are_probed_not_written():
    """原生动作路径只观察不落刀：单件赠礼留探针；双修留探针（ConfRoleTrains 自算）。"""
    src = _src()
    assert 'ModMain.P("[Give] 好感探针 原生赠礼' in src, "原生单件赠礼的探针没了（配表值没法核对）"
    assert 'ModMain.P("[Intim] 双修探针' in src, "双修探针没了（原生的 Δ 无从核对）"


# ══════════════════════════════════════════════════════════════════
# ⑥ 配表读值（不写死）
# ══════════════════════════════════════════════════════════════════

def test_values_come_from_conf_with_fallback():
    """赠礼/讨要的加值读配表（读不到才回退），并按配表单次上限封顶。"""
    src = _src()
    assert "g.conf.roleGive.closeParaA" in src and "g.conf.roleGive.closeOnceAddMax" in src, \
        "赠礼值没读配表 RoleGive（写死会被官方调表打脸）"
    assert "g.conf.roleAskfor.closeParaA" in src and "g.conf.roleAskfor.closeAddMax" in src, \
        "讨要值没读配表 RoleAskfor"
    gb = _body(src, " int GiftIntimValue()")
    assert "ConfIntim(g.conf.roleGive.closeParaA, IntimStepDefault)" in gb, "赠礼回退值不是基准 15"
    ab = _body(src, " int AskforIntimValue()")
    assert "ConfIntim(g.conf.roleAskfor.closeParaA, IntimStepDefault)" in ab, "讨要回退值不是基准 15"


# ══════════════════════════════════════════════════════════════════
# ⑦ 衰减说明（渲染层）
# ══════════════════════════════════════════════════════════════════

def test_renderer_explains_attenuation():
    """实得 ≠ 请求时，渲染层必须说明"这是引擎的系数换算，不是失败"（否则模型会重试）。"""
    import importlib.util
    import sys

    sys.path.insert(0, str(ROOT.parent))
    tr = importlib.import_module(f"{ROOT.name}.tools.text_render")
    t = tr.render("social_relation", {}, {"success": True, "data": {
        "op": "add_intim", "target": "李四", "requested": 30, "actual_delta": 8,
        "intim_before": 150, "current_intim": 158}})
    assert "好感提升了8点" in t and "请求30点" in t and "150→158" in t, t
    assert "系数" in t and "25" in t and "不是失败" in t, f"没说明衰减：{t}"
    # 实得 == 请求时不加废话（旧文案逐字不变）
    ok = tr.render("social_relation", {}, {"success": True, "data": {
        "op": "add_intim", "target": "李四", "requested": 15, "actual_delta": 15,
        "intim_before": 20, "current_intim": 35}})
    assert ok == "李四对你的好感提升了15点（请求15点，20→35）", ok


# ══════════════════════════════════════════════════════════════════
# ⑧ 未知实得不许漏进结果字段（10-15 复核新增）
# ══════════════════════════════════════════════════════════════════

def test_unknown_delta_never_leaks_into_result_fields():
    """读不到起点（`int.MinValue`）时结果字段必须**缺失**——不许把 -2147483648 报给模型/日志。

    口径与 trade 余额同源（"读得到才写；渲染层对缺失字段是容忍的"）。所有
    「写入层返回值 → 结果字段」都必须经 `PutIntim`（唯一过滤点）；裸赋值 = 漏网。
    """
    import re

    src = _src()
    assert " void PutIntim(JObject o, string key, int delta)" in src, "缺 PutIntim 收口 helper"
    assert "delta != int.MinValue" in src, "PutIntim 没过滤 int.MinValue（未知实得会漏成垃圾大数）"
    # 裸赋值扫描：任何 `["intim"] = Apply...` / `["intim_upset"] = Apply...` / `intimPair[...] = Apply...`
    bad = re.findall(r'(?:\["intim(?:_upset)?"\]|intimPair\["\w+"\])\s*=\s*Apply', src)
    assert not bad, f"有结果字段直接接写入层返回值（MinValue 会漏进结果）：{bad}"
    # 已知消费点必须真的在 PutIntim 后面（防"定义了不用"）——抽 4 处代表
    for anchor in ('PutIntimWithAfter(okWindow, "intim", intimWindow, player, npc);',
                   'PutIntim(okMoneyNpc, "intim", intimMoneyNpc);',
                   'PutIntim(okRcv, "intim", intimRcv);',
                   'if (intimPair.Count > 0) okObj["intim"] = intimPair;'):
        assert anchor in src, f"收口点缺失：{anchor}"


# ══════════════════════════════════════════════════════════════════
# ⑨ 好感变化渲染给模型（10-15 用户定案补显示；原生路径无字段=天然不显示）
# ══════════════════════════════════════════════════════════════════

def test_renderers_surface_intim():
    """我们自己落刀的路径透出的 `intim`/`intim_upset` 必须渲染成一句话（模型要"知道"）；
    字段缺失（原生路径 / 读不到被滤）时输出**逐字回到旧文案**。各渲染器的"你"指向不同：
    economy/trade 全名互称；item_acquire 的"你"=玩家；world_ai 的"你"=AI 发起方。"""
    import importlib.util
    import sys

    sys.path.insert(0, str(ROOT.parent))
    tr = importlib.import_module(f"{ROOT.name}.tools.text_render")

    def r(tool, args, data):
        return tr.render(tool, args, {"success": True, "data": data})

    # 赠礼（economy_item）：收礼方对送礼方 +Δ
    t = r("economy_item", {"initiator": "李四"}, {
        "op": "give_item", "target": "王五",
        "items": [{"item": "丹药", "requested": 1, "actual": 1}],
        "accepted": True, "intim": 15, "same_grid": True})
    assert "王五对李四的好感提升15点" in t, t
    # 字段缺失 → 旧文案逐字不变（无好感句）
    t0 = r("economy_item", {"initiator": "李四"}, {
        "op": "give_item", "target": "王五",
        "items": [{"item": "丹药", "requested": 1, "actual": 1}], "accepted": True})
    assert "好感" not in t0 and "丹药×1" in t0, t0

    # 交易有价：双向各 +5；赠予（price=0）：只有买方对卖方
    t = r("trade", {}, {"seller": "李四", "buyer": "王五", "item": "丹药", "count": 2,
                        "price": 100, "item_moved": True,
                        "intim": {"seller_to_buyer": 5, "buyer_to_seller": 5}})
    assert "李四对王五的好感提升5点，王五对李四的好感提升5点" in t, t
    t = r("trade", {}, {"seller": "李四", "buyer": "王五", "item": "丹药", "count": 2,
                        "price": 0, "item_moved": True, "intim": {"buyer_to_seller": 15}})
    assert "王五对李四的好感提升15点" in t and "李四对王五" not in t, t

    # 讨要达成（对玩家）= 双边：取得方（AI）对"你"+；你对它 −
    t = r("item_acquire", {}, {"op": "ask_for", "target": "李四", "item_name": "丹药",
                               "count": 4, "accepted": True, "intim": 8, "intim_upset": -8})
    assert "李四对你的好感提升8点，你对李四的好感降低8点" in t, t
    # 旧形态（无字段）逐字不变
    t0 = r("item_acquire", {}, {"op": "ask_for", "target": "李四", "item_name": "丹药",
                                "count": 4, "accepted": True})
    assert t0 == "你把「丹药」×4交给了李四。", t0
    # 收下赠礼（receiving）= 单边：收礼方（AI）对送礼方（你）+
    t = r("item_acquire", {}, {"op": "ask_for", "target": "李四", "item_name": "丹药",
                               "count": 1, "accepted": True, "receiving": True, "intim": 15})
    assert "李四对你的好感提升15点" in t, t
    # NPC↔NPC 讨要：全名互称
    t = r("item_acquire", {}, {"op": "ask_for", "target": "李四", "item_name": "丹药",
                               "count": 2, "accepted": True, "counterparty": "王五",
                               "intim": 8, "intim_upset": -8})
    assert "李四对王五的好感提升8点，王五对李四的好感降低8点" in t, t

    # 论道：双向 +10（本项目规则）；双修原生自算 ⇒ 无字段 ⇒ 不显示
    t = r("world_ai_action", {}, {"op": "lun_dao", "target": "李四", "completed": True,
                                  "intim": {"npc_to_player": 10, "player_to_npc": 10}})
    assert "你对李四的好感提升10点，李四对你的好感提升10点" in t, t
    t = r("world_ai_action", {}, {"op": "shuang_xiu", "target": "李四", "completed": True})
    assert "好感" not in t and "已结束" in t, t
