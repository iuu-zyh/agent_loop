"""inspect_unit 死者回退契约（10-15）。

背景：探针实锤（DeathProbe）——活单位容器对死者**不可解析**（GetUnit 三重载+全量 1548 均
不含），死者数据只在死亡账本（`g.data.unitDie`，`allUnitDie=Dictionary<string,DieData>`）。
本契约钉住：
  ① inspect_unit 活人解析失败 → **死亡账本回退**（unit_id 精准 O(1) / 按名遍历兜底）；
  ② 死者结果 `dead=true` + **unit_id 无条件回传**（Python `_harvest_ids` 学进映射表，
     之后全程精准——"首次兜底遍历只发生一次"）；
  ③ 拿不到的块（stats/abilities/inventory）走 `dead_unavailable` 通用语句，不静默缺块；
  ④ 死者能给的块要真给：brief（境界/宗门/殁于X年X月/卒于/凶手/关系好感**冻结值**）+
     relationships（快照十容器，关系人活死两路拿名）+ logs（死亡瞬间经历快照）。
渲染侧（`render_inspect` 的 dead 段）：死亡事实顶在最前 + 不可查考清单；无 dead 键的
活人档案**逐字回到旧文案**。
断言读 C#/Python **源码文本** + 渲染函数行为（与本仓既有契约同款做法）。
"""

from __future__ import annotations

from pathlib import Path
import _cs_tools  # 工具层多文件聚合（见 tests/_cs_tools.py）

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"


def _src(rel: str) -> str:
    return (CS / rel).read_text(encoding="utf-8")


def _body(src: str, head: str) -> str:
    i = src.index(head)
    j = src.index("\n        }", i)
    return src[i:j]


# ══════════════════════════════════════════════════════════════════
# ① C# 侧：账本回退 + 死者档案采集
# ══════════════════════════════════════════════════════════════════

def test_dead_fallback_wired_into_inspect():
    """inspect_unit 活人解析失败的分支里必须先走死亡账本回退，命中才回落旧报错。"""
    src = _cs_tools.tool_src()
    assert "UnitSnapshot.FindDead(" in src, "inspect_unit 没接死亡账本回退"
    assert "UnitSnapshot.BuildDead(" in src, "死者档案采集没被调用"
    # 回退必须在 wub == null 分支内（活人命中绝不进账本）
    i = src.index("if (wub == null)")
    seg = src[i:src.index("ModMain.P(\"[Inspect] args=", i)]
    assert "FindDead" in seg and "BuildDead" in seg, "死者回退不在活人失败的分支里"
    assert "return Ok(dead);" in seg, "死者命中必须 Ok 返回（不是 Fail）"


def test_dead_result_carries_unit_id_and_dead_flag():
    """死者结果必须 `dead=true` + **unit_id 无条件回传**——后者是 Python `_harvest_ids`
    学进映射表的唯一入口，少了它"首次兜底遍历"就会每次发生（永远精准不起来）。"""
    b = _body(_src("game/UnitSnapshot.cs"), "public static JObject BuildDead(")
    assert 'data["dead"] = true;' in b, "dead 标记没了（渲染层/Python 判定总开关）"
    assert 'data["unit_id"]' in b, "死者结果没回传 unit_id（映射表学不到，之后每次都兜底遍历）"
    assert 'data["name"] = DeadName(dieData);' in b, "死者名字没采（name 数组 join）"


def test_dead_uses_ledger_probe_proven_path():
    """账本入口与定位必须走探针实锤的路：`g.data.unitDie` 双路兜底；
    定位 = ID 精准（O(1)）优先、按名遍历兜底（死者被 ClearDieUnit 清出关系簿，
    反查不出 ID——探针 ⑦ 实锤）；活人容器绝不出现在死者路径上。"""
    src = _src("game/UnitSnapshot.cs")
    assert "g.data.unitDie" in src and "g.data.dataUnitDie" in src, "账本入口双路兜底没了"
    b = _body(src, "public static bool FindDead(")
    assert "allUnitDie" in b, "定位没走 allUnitDie"
    # 取值与遍历必须走**已验证路径**（10-16 ⑦ 探针实证：单路 (dynamic)all[id] 索引器真机
    # 可能炸/返 null、Keys 索引静默返 null → "账本无死者"假象；三分列表 + get_Item 才是
    # 跑通过 75 位详探的路）。
    assert "GetDieData(" in b, "取值没走 GetDieData 双路降级（单路索引器真机不可信）"
    assert "DeadIds()" in b, "按名遍历没走 DeadIds（三分列表优先，Keys 只配降级）"
    # 定位路径上绝不允许活人容器调用（探针实锤：GetUnit 三重载对死者全 null，
    # GetUnits 全量遍历也不含 —— 调了必空转，还会误导后来者以为这是备用数据源）
    assert "GetUnit(" not in b, "死者定位路径出现活人容器 GetUnit（对死者必 null，纯空转）"
    # 采集体里冻结关系走快照
    bd = _body(src, "public static JObject BuildDead(")
    assert "GetIntim(pid)" in bd, "死者好感没走档案快照（冻结值）"
    assert "GetRelation(g.world.playerUnit)" in bd, "死者关系枚举没走档案快照"
    assert "killUnitID" in bd, "凶手没采（DieData 实锤字段）"
    assert "logData" in bd, "死者经历快照没采（UnitDieData.logData）"


def test_dead_unavailable_blocks_reported():
    """死者拿不到的块（stats/abilities/inventory）必须进 `dead_unavailable`
    （渲染层拼通用语句）——静默缺块 = 模型不知道"为什么没有这项"。"""
    src = _cs_tools.tool_src()
    assert 'dead["dead_unavailable"] = unavail;' in src, "不可查考清单没落"
    for k in ("stats", "abilities", "inventory"):
        assert f'cs == "{k}"' in src, f"{k} 没进不可查考清单"


# ══════════════════════════════════════════════════════════════════
# ③ 交互工具对死者的"已身故"文案（10-15 待办②）
# ══════════════════════════════════════════════════════════════════

def test_interact_tools_dead_notice_fallback():
    """活人解析失败的报错分支必须先问死亡账本（`DeadNotice` 兜底）——人死了要告诉
    模型**为什么**，不然它会当成名字打错去重试 search_units（活人工具，永远查不到）。
    8 处收口：发起方 ×5 + 对手方 ×1（ResolveCounterparty）+ 交易卖/买方 ×2。
    原报错文案必须**逐字保留**（未命中时落回，编造的名字照旧报"未找到"）。"""
    src = _cs_tools.tool_src()
    # 发起方那 5 处 11-20 起收口到 `ToolArgs.BindActor`（里面调一次 DeadNotice），
    # 所以"发起方覆盖率"看 BindActor 的调用点数，其余（对手方×1 / 交易×2）仍直接调 DeadNotice。
    n_actor = src.count("BindActor(args,")
    n_dead = src.count("DeadNotice(")
    assert n_actor >= 6, f"发起方死者兜底收口点不足（现 {n_actor} 处，应 6 个动作工具全走 BindActor）"
    assert n_dead >= 4, f"死者兜底接线不足（现 {n_dead} 处：BindActor 内 1 + 对手方 1 + 交易 2）"
    # 五处发起方 + 交易两处 + 对手方，各自带正确的 `_id` 键（拼错键 = 精准注入永远失效）
    for key in ("initiator_id", "target_id", "seller_id", "buyer_id"):
        assert f'args["{key}"]' in src, f"死者兜底没读 {key}（O(1) 精准定位失效，每次都按名全账本遍历）"
    # 未命中必须落回原文案（不许把"已身故"当万能报错）
    assert 'Fail("未找到发起方 " + initiator)' in src, "发起方原报错丢了"
    assert '「" + targetName + "」（target 要填对方真名' in src, "对手方原报错丢了"
    assert 'return Fail("未找到卖方 " + sellerId);' in src, "卖方原报错丢了"
    assert 'return Fail("未找到买方 " + buyerId);' in src, "买方原报错丢了"

    us = _src("game/UnitSnapshot.cs")
    b = _body(us, "public static string DeadNotice(")
    assert "FindDead(" in b, "DeadNotice 没复用 FindDead（重复实现定位 = 两处漂移）"
    # ★ 文案里绝不出现 unit_id（09-17 先例：错误帧原样进模型上下文，模型会照念/编造）★
    assert "unit_id" not in b, "DeadNotice 文案里泄漏 unit_id 键名"
    assert 'unitId + ' not in b and '+ unitId' not in b, "DeadNotice 文案拼了 unitId 值"
    assert "已身故" in b and "inspect_unit 查阅" in b, "死亡事实/自纠指引文案没了"
    # 未命中路径：返回 null（调用方落回原报错），绝不给空串（空串会被当命中）
    assert "return null;" in b, "未命中必须返回 null（调用方据此落回原报错）"


def test_dead_notice_reuses_shared_month_conversion():
    """`DeadNotice` 的"殁于N年M月"必须调**既有单一来源** `CnYearMonth`（算式唯一落点
    `SplitAccountMonth`）——全仓只许一处 `(x-1)/12+1`（test_game_time_stamp 在守）。
    死者报错里的日期与 inspect 档案/时间戳漂移 = 模型自相矛盾。"""
    us = _src("game/UnitSnapshot.cs")
    b = _body(us, "public static string DeadNotice(")
    assert "CnYearMonth(" in b, "殁于月份没走单一来源 CnYearMonth（又内联一份换算就会漂移）"
    assert "/ 12" not in b and "% 12" not in b, "DeadNotice 里内联了月份算式（破坏单一来源）"



# ══════════════════════════════════════════════════════════════════
# ② 渲染侧：死亡事实 + 通用语句；活人档案逐字不变
# ══════════════════════════════════════════════════════════════════

def _render(data):
    import importlib.util
    import sys

    sys.path.insert(0, str(ROOT.parent))
    tr = importlib.import_module(f"{ROOT.name}.tools.text_render")
    return tr.render("inspect_unit", {}, {"success": True, "data": data})


def test_renderer_surfaces_death_facts():
    """死亡事实（殁于/卒于/凶手）顶在最前；不可查考清单给通用语句。"""
    t = _render({
        "name": "宁凯旋", "unit_id": "LHbosH", "dead": True,
        "sex": "男", "realm": "结晶中期", "sect": "化神殿", "race": "人族",
        "reputation": 242, "age": 256, "life": 1128, "relation": "陌生", "intim": 0,
        "died_month": 15, "die_point": "64,0", "killer": "曾嘉泽",
        "relationships": {"parent": [{"name": "宁铁山", "unit_id": "abc"}]},
        # logs = PageLogs 产出形态（{filter,page,items,total,has_more}）——渲染层 _logs 只认它
        "logs": {"filter": "all", "page": 1, "total": 1, "has_more": False,
                 "items": [{"month": 14, "text": "在秘境中被曾嘉泽所杀", "tier": "important"}]},
        "dead_unavailable": ["stats", "abilities", "inventory"],
    })
    assert "☠ 宁凯旋已身故（殁于2年3月，卒于（64,0），死于曾嘉泽之手）。" in t, t
    assert "已不可查考：属性、功法、储物。" in t, t
    assert "结晶中期" in t and "化神殿" in t, t          # brief 静态字段照常渲染
    assert "宁铁山" in t, t                              # relationships 快照渲染
    assert "在秘境中被曾嘉泽所杀" in t, t                # logs 快照渲染（PageLogs 形态）


def test_renderer_without_dead_key_is_unchanged():
    """活人档案（无 dead 键）渲染**逐字回到旧文案**——死者段绝不泄漏进活人结果。"""
    t = _render({
        "name": "李四", "sex": "男", "realm": "结晶后期", "sect": "化神殿",
        "race": "人族", "reputation": 100, "relation": "陌生", "intim": 0,
    })
    assert "☠" not in t and "已身故" not in t and "已不可查考" not in t, t
    t2 = _render({
        "name": "李四", "dead": False, "sex": "男", "realm": "结晶后期",
        "relation": "陌生", "intim": 0,
    })
    assert "☠" not in t2, t2        # dead=False 与缺键同效（不做真值特判）


# ══════════════════════════════════════════════════════════════════
# ④ 传音落账不回复 + 自主交互排除（10-15 待办③）
# ══════════════════════════════════════════════════════════════════

def test_is_dead_is_a_thin_wrapper():
    """`IsDead` 必须是 `FindDead` 的布尔轻封装——第三套定位逻辑 = 三处漂移。"""
    us = _src("game/UnitSnapshot.cs")
    b = _body(us, "public static bool IsDead(")
    assert "FindDead(" in b, "IsDead 没走 FindDead（又一套定位 = 漂移源）"


def test_death_silences_outbound_relay():
    """玩家→死者的传音：消息**照发照记**（气泡 AppendUserMessage + 账本 OfferSolo 先落，
    回放可见），但**不进 LLM**——`IsDead` 判定必须钉在 OfferSolo **之后**、
    `SendPlayerMessage` **之前**；系统提示用定案原话；不进 busy（没有回帧会来）。"""
    src = _src("UI/ChatPresenter.cs")
    i_solo = src.index("ChatFrameQueue.OfferSolo(npc")
    i_send = src.index("_ws.SendPlayerMessage(npc, clean, new JArray(attachments))")
    seg = src[i_solo:i_send]
    assert "IsDead(" in seg, "死者判定不在 OfferSolo 之后、SendPlayerMessage 之前（进早了消息落不了账，进晚了已经发出去）"
    assert "许久没有回复，也许是已经遭遇了不测" in seg, "系统提示没用定案原话"
    assert "AppendUserMessage" not in seg, "拦截分支又画了一遍气泡（会双份）"
    # SetBusy 在 seg 里出现是正常的（SendPlayerMessage 前的正常路径）；要钉的是**拦截块内部**
    # 没有 busy——没有回帧会来，进 busy 就永远转圈。
    i_dead = seg.index("UnitSnapshot.IsDead(deadUid, npc)")
    i_ret = seg.index("return;", i_dead)
    assert "SetBusy" not in seg[i_dead:i_ret], "拦截分支进了 busy（没有回帧会来，永远转圈）"
    # 拦截分支必须清待附图（图片没随消息发走，留着骑到下一句别人的发言上）
    assert "_pendingImages.Clear()" in seg, "拦截分支没清待附图"
    # /compact 对死者也要本地拦（逝者的记忆无从压缩）
    i_cmp = src.index('msg.Equals("/compact", StringComparison.OrdinalIgnoreCase)')
    seg_cmp = src[i_cmp:src.index("StartManualCompact(npc)", i_cmp)]
    assert "IsDead(" in seg_cmp and "无从压缩" in seg_cmp, "/compact 没拦死者"


def test_initiative_never_fires_at_the_dead():
    """自主交互排除（两层）：`ReadRows` 构建候选时剔除死者（统一覆盖两个 FireOne 源）；
    `FireOne` 入口末道断言（与 IsPlayerUnit 同款，防未来候选源混入）。"""
    src = _src("npc/NpcInitiativeMonitor.cs")
    # 修饰符不锁（11-13 优先队列改造后它读 `_mailPending`，实例方法才够用）——
    # 钉的是"候选源里剔除死者"这条判据本身。
    b_rows = _body(src, " List<Row> ReadRows(")
    assert "IsDead(" in b_rows, "ReadRows 没剔除死者（候选源的第一道闸）"
    i_fire = src.index("private void FireOne(")
    seg_fire = src[i_fire:src.index("bool isBusy = busy.Length > 0;", i_fire)]
    assert "IsDead(" in seg_fire, "FireOne 没有死者末道断言（防未来候选源混入）"
    assert "IsPlayerUnit" in seg_fire, "玩家自身断言没了（回归检查）"


def test_packaged_exe_is_not_stale():
    """★构建产物新鲜度★：Python 改动必须重新打包（Nuitka exe）才算交付——
    10-15 事故：改了 text_render 却只部署 DLL，游戏里 exe 还是 16 天前的，
    渲染层死者段/好感句全不生效（且 deploy 的 `-Last N` 截断把 exe 段输出滤掉了）。
    本测试：exe 存在时，其 mtime 不得早于任何 `tools/`+`agent/` 源码；exe 不存在
    （没打包环境/纯源码仓）则跳过——不强迫每个开发者都打包。"""
    import datetime

    exe = Path(r"F:\.agent_loop_build\nuitka\AgentLoopServer.exe")
    if not exe.is_file():
        print("skip：本机没有 Nuitka 产物（不检查新鲜度）")
        return
    exe_mt = exe.stat().st_mtime
    stale = []
    for d in ("tools", "agent"):
        for p in (ROOT / d).rglob("*.py"):
            if p.stat().st_mtime > exe_mt:
                stale.append(f"{p.relative_to(ROOT)} ({datetime.datetime.fromtimestamp(p.stat().st_mtime):%m-%d %H:%M})")
    if stale:
        assert False, (
            "Nuitka exe 落后于 Python 源码（部署的是旧包！）：\n  " + "\n  ".join(stale[:8])
            + "\n重新打包：F:\\.agent_loop_build\\pybuild\\venv-nuitka\\Scripts\\python.exe -X utf8 "
              "F:\\agent_loop\\scripts\\build_exe_nuitka.py --jobs 12，然后 deploy_runtime.py --only exe"
        )
    print(f"✓ exe（{datetime.datetime.fromtimestamp(exe_mt):%m-%d %H:%M}）不落后于全部 Python 源码")