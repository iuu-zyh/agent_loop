"""tools/text_render — 工具结果润色层单测（2026-09-10 重写：忠实优先）

覆盖：8 工具渲染器（全字段忠实呈现）+ 兜底（错误帧/无渲染器 → None → 调用方回退全量 JSON）。
"""
from __future__ import annotations

from agent_loop.tools import text_render as tr


def _r(name, args, data, success=True):
    res = {"success": success}
    if data is not None:
        res["data"] = data
    return tr.render(name, args or {}, res)


# ---------------------------------------------------------------------------
# 兜底
# ---------------------------------------------------------------------------

def test_fallback_paths():
    assert _r("inspect_unit", {}, None, success=False) is None   # 错误帧
    assert _r("inspect_unit", {}, None) is None                  # 无 data
    assert _r("unknown_tool", {}, {"a": 1}) is None              # 无渲染器
    print("✓ 兜底：错误帧/无 data/未知工具 → None")


# ---------------------------------------------------------------------------
# inspect_unit
# ---------------------------------------------------------------------------

def test_inspect_inventory_faithful():
    """背包全列：类别标题 + 条目分行（名称×数量、单价、小计、**介绍 desc**）、杂项聚合、装备价值/介绍、灵石"""
    data = {
        "name": "姜萌",
        "inventory": {
            "props": [
                {"cat": "丹符", "items": [
                    {"name": "一品筑基丹", "count": 1, "worth": 800, "total": 800},
                    {"name": "六品蓄力丹", "count": 6, "worth": 8, "total": 48},
                    {"name": "六品玉琼丹", "count": 14, "worth": 24, "total": 336},
                    {"name": "六品培元丹", "count": 6, "worth": 36, "total": 216},
                    {"name": "六品化瘀丹", "count": 14, "worth": 24, "total": 336,
                     "desc": "最为常见的疗伤丹药，使用后能够恢复少量的体力值。"},
                    {"name": "六品培元丹", "count": 8, "worth": 24, "total": 192},
                ]},
                {"cat": "其他", "items": [
                    {"name": "青色妖丹", "count": 22, "worth": 20, "total": 440},
                ], "misc": {"kinds": 2, "pieces": 31, "worth": 310}},
            ],
            "equips": [{"name": "青纹袍", "worth": 180, "desc": "制式道袍，防御+8"}],
            "money": 381,
        },
    }
    text = _r("inspect_unit", {"classes": ["inventory"]}, data)
    assert "背包（约102件）" in text, text          # 1+6+14+6+14+8+22+31 = 102
    for nm in ("一品筑基丹×1", "六品化瘀丹×14", "六品培元丹×8"):
        assert nm in text, (nm, text)              # 全列，无截断
    assert "单价800" in text and "小计336" in text, text
    assert "另有2种共31件约值310" in text, text
    assert "六品化瘀丹×14（单价24，小计336）——最为常见的疗伤丹药，使用后能够恢复少量的体力值。" in text, text
    assert "身着：" in text and "青纹袍（值180）——制式道袍，防御+8" in text, text
    assert "灵石：381" in text, text
    print("✓ inspect 背包全字段（条目分行/介绍/单价小计/杂项/装备/灵石）")


def test_inspect_blocks_faithful():
    """性格/魅力声望（档位+数值）/道号/坐标/气运 desc/属性全字段/功法 id/关系簿全列/经历分页"""
    data = {
        "name": "姜萌", "sex": "女", "realm": "筑基境", "sect": "散修", "race": "人族",
        "title": "玉罗刹", "beauty": 375, "beauty_label": "仙姿",
        "reputation": 12, "reputation_label": "初出茅庐", "hobby": ["饰品", "琴"],
        "personality": {"inner": "狂邪", "outer": ["护短", "名声"]},
        "relation": "道侣", "intim": 200, "same_grid": True, "point": {"x": 31, "y": 78},
        "luck": {"born": [{"id": 1, "name": "植树成林", "desc": "木灵根亲和"}],
                 "added": [{"id": 2, "name": "陷入瓶颈", "desc": ""}]},
        "stats": {"power": 1200, "defense": 800, "hp": 1500, "hp_max": 1800, "energy": 90,
                  "mood": 72, "talent": 66, "beauty": 375, "reputation": 12,
                  "heart": "坚定不移", "heart_state": "Stable"},
        "abilities": {"skill_left": {"type": "灵技", "id": "M1", "name": "青木箭",
                                     "desc": "射出一支青木箭，威力 120。"},
                      "abilitys": [{"type": "心法", "id": "M2", "name": "青元诀"}]},
        "relationships": {"married": "唐炎", "master": ["李四", "王五"],
                          "friend_units": ["甲", "乙", "丙", "丁"], "human_value": 12},
        "logs": {"filter": "important", "page": 2, "total": 7, "has_more": False,
                 "items": [{"month": 1, "text": "与唐炎论道"}, {"month": 2, "text": "突破筑基"}]},
    }
    t = _r("inspect_unit", {}, data)
    assert "道号：玉罗刹" in t, t
    assert "魅力仙姿（375）" in t and "声名初出茅庐（12）" in t, t
    assert "性格：内狂邪·外护短·外名声" in t and "坐标(31,78)" in t, t
    assert "气运：先天植树成林（木灵根亲和）；后天陷入瓶颈" in t, t
    assert "攻击1200" in t and "防御800" in t and "精力90" in t and "资质66" in t, t
    assert "气血1500/1800" in t and "道心坚定不移（Stable）" in t, t
    assert "灵技「青木箭」(id=M1)" in t and "心法「青元诀」(id=M2)" in t, t
    # 技能说明：C# GetDesc 给的 desc 必须带出来；没有 desc 的槽不出现空破折号
    assert "灵技「青木箭」(id=M1)——射出一支青木箭，威力 120。" in t, t
    assert "心法「青元诀」(id=M2)——" not in t, t
    assert "功法：\n· " in t, f"功法应逐槽一行：{t}"
    assert "道侣：唐炎" in t and "师尊：李四、王五" in t, t
    assert "好友：甲、乙、丙、丁" in t and "人情12" in t, t      # 不再限 3 名
    assert "经历（重要｜第2页，共7条，已到末页）" in t, t
    assert "（1年1月）与唐炎论道" in t and "（1年2月）突破筑基" in t, t
    print("✓ inspect 各块全字段（性格/道号/坐标/气运/属性/功法id/关系簿/经历）")


def test_inspect_logs_month_range():
    """经历时间范围：since/until 账面月回显 N年M月 + 过滤后计数"""
    data = {"name": "姜萌",
            "logs": {"filter": "all", "page": 1, "total": 2, "has_more": False,
                     "since_month": 13, "until_month": 24,
                     "items": [{"month": 13, "text": "突破筑基"}, {"month": 14, "text": "获赠丹药"}]}}
    t = _r("inspect_unit", {"classes": ["logs"]}, data)
    assert "经历（全部｜范围 2年1月～2年12月｜第1页，共2条，已到末页）" in t, t
    assert "（2年1月）突破筑基" in t and "（2年2月）获赠丹药" in t, t
    print("✓ inspect 经历时间范围回显 + 月份换算")


def test_inspect_logs_tier_marks():
    """经历分层：filter=all 时重要件带 ★ + 表头图例；单选层时不加标记（同质无需区分）。

    真机现象（09-13）：玩家开局首月「初入八荒。」在重要/常规两桶各有一条，`all` 取并集后
    渲染成两行一模一样的文本——看起来像 bug，其实是两条独立的流（见 UnitSnapshot.LayerOverlap）。
    加 ★ 后至少能一眼分辨哪条是大事；C# 未升级（无 tier 字段）时不得回归。
    """
    mixed = {"name": "缪嘉歆",
             "logs": {"filter": "all", "page": 1, "total": 3, "has_more": False,
                      "items": [{"month": 1, "text": "初入八荒。", "tier": "important"},
                                {"month": 1, "text": "初入八荒。", "tier": "regular"},
                                {"month": 1, "text": "与云含进行交谈，气氛融洽，与对方的亲密度提升了！",
                                 "tier": "regular"}]}}
    t = _r("inspect_unit", {"classes": ["logs"]}, mixed)
    assert "经历（全部｜第1页，共3条，已到末页；★=重要）：" in t, t
    assert "★（1年1月）初入八荒。" in t, t                       # 重要件带星
    assert "\n（1年1月）初入八荒。" in t, t                        # 常规件同文但不带星 → 两行可分辨
    assert "★（1年1月）与云含进行交谈" not in t, t                 # 常规件不加星

    only = {"name": "缪嘉歆",
            "logs": {"filter": "important", "page": 1, "total": 1, "has_more": False,
                     "items": [{"month": 1, "text": "初入八荒。", "tier": "important"}]}}
    t = _r("inspect_unit", {"classes": ["logs"]}, only)
    assert "★" not in t and "；★=重要" not in t, t                # 单选层：同质，不加标记

    # 旧 C# 载荷（无 tier）：不得回归成带星/带图例
    legacy = {"name": "缪嘉歆",
              "logs": {"filter": "all", "page": 1, "total": 2, "has_more": False,
                       "items": [{"month": 13, "text": "突破筑基"}, {"month": 14, "text": "获赠丹药"}]}}
    t = _r("inspect_unit", {"classes": ["logs"]}, legacy)
    assert "★" not in t, t
    assert "经历（全部｜第1页，共2条，已到末页）：" in t, t
    assert "（2年1月）突破筑基" in t and "（2年2月）获赠丹药" in t, t
    print("✓ inspect 经历分层标记（all 带★+图例 / 单选层不加 / 旧载荷不回归）")


def test_search_units_sex():
    """找人结果行带性别（09-13）：filters 早就支持按 sex 筛人，结果行却不带性别——筛完仍不知谁是谁。"""
    data = {"total": 2, "filters": {"sex": "女"},
            "items": [{"name": "姜萌", "sex": "女", "relation": "道侣", "intim": 200,
                       "realm": "筑基", "sect": "散修"},
                      {"name": "唐炎", "sex": "男", "relation": "好友", "intim": 120,
                       "realm": "筑基", "sect": "青云宗", "region": "永宁州"}]}
    t = _r("search_units", {}, data)
    assert "姜萌（女，关系道侣，好感200，筑基/散修）" in t, t
    assert "唐炎（男，关系好友，好感120，筑基/青云宗，永宁州）" in t, t
    # 旧 C# 载荷（无 sex）：行格式不得回归
    old = {"total": 1, "items": [{"name": "姜萌", "relation": "道侣", "intim": 200,
                                  "realm": "筑基", "sect": "散修"}]}
    t2 = _r("search_units", {}, old)
    assert "姜萌（关系道侣，好感200，筑基/散修）" in t2, t2
    print("✓ search_units 结果行带性别（旧载荷不回归）")


def test_trade_render():
    """trade 渲染：谁把什么卖给谁 + 成交价 + 双方灵石余额变化 + 交割方式；0 价不得渲染成"价 0 灵石"。"""
    ok = {"success": True, "data": {
        "op": "trade", "seller": "云含", "buyer": "缪嘉歆", "item": "青木箭", "count": 2,
        "price": 3000, "item_moved": True,
        "buyer_item_before": 0, "buyer_item_after": 2,
        "seller_money_before": 0, "seller_money_after": 3000,
        "buyer_money_before": 5000, "buyer_money_after": 2000, "same_grid": True}}
    t = _r("trade", {"seller": "云含", "buyer": "缪嘉歆", "item": "青木箭", "count": 2, "price": 3000}, ok["data"])
    assert "云含把「青木箭」×2卖给了缪嘉歆，价 3000 灵石" in t, t
    assert "云含灵石 0→3000" in t and "缪嘉歆灵石 5000→2000" in t, t
    # 到货实证（审计补渲染）：C# 一直在发 buyer_item_before/after，此前没人读 ——
    # 模型只能看到一个 item_moved 布尔，看不到「0→2」这个唯一可核对的事实
    assert "缪嘉歆的「青木箭」0→2" in t, t
    assert "当面交割" in t, t

    free = {"op": "trade", "seller": "云含", "buyer": "缪嘉歆", "item": "丹药", "count": 1,
            "price": 0, "item_moved": True, "same_grid": False}
    t2 = _r("trade", {"seller": "云含", "buyer": "缪嘉歆", "item": "丹药", "count": 1, "price": 0}, free)
    assert "价 0 灵石" not in t2 and "不取分文" in t2, t2
    assert "异地交割" in t2, t2

    # 余额对不上（C# 核对失败会自己回滚，正常到不了这里）——照实说，不粉饰
    bad = dict(free, item_moved=False)
    t3 = _r("trade", {}, bad)
    assert "实收件数对不上" in t3, t3
    print("✓ trade 渲染（成交价/双方余额/0 价措辞/交割方式/核对失败照实说）")


def test_inspect_personality_desc():
    """性格描述：性格标签行不变 + 性格注解行（名字——面板描述）；无 desc 的旧数据不回归"""
    data = {
        "name": "姜萌",
        "personality": {"inner": "邪恶", "inner_desc": "邪恶，唯已所欲，从来不管其它人的感受。",
                        "outer": ["护短", "名声"], "outer_desc": ["护短的描述", ""]},
    }
    t = _r("inspect_unit", {"classes": ["brief"]}, data)
    assert "性格：内邪恶·外护短·外名声" in t, t                     # 标签行原样保留
    assert "性格注解：邪恶——邪恶，唯已所欲，从来不管其它人的感受。；护短——护短的描述" in t, t
    assert "名声——" not in t, t                                     # 空描述不出现
    old = _r("inspect_unit", {"classes": ["brief"]}, {"name": "姜萌", "personality": {"inner": "狂邪", "outer": ["护短"]}})
    assert "性格：内狂邪·外护短" in old and "性格注解" not in old, old
    print("✓ inspect 性格描述（注解行/空描述跳过/旧数据不回归）")


def test_inspect_attrs_faithful():
    """stats.attrs 动态全量：三组按面板分组渲染、缺失键跳过、root 重复键不双列、魅力/声望补齐、旧数据回退"""
    data = {
        "name": "姜萌",
        "stats": {
            "power": 22, "attack": 22, "defense": 8, "hp": 344, "hp_max": 344,
            "beauty": 375, "reputation": 12,
            "heart": "道种",
            "attrs": {
                "personal": {"age": 23, "life": 108, "mood": 120, "mood_max": 120,
                             "health": 100, "health_max": 100, "energy": 70, "energy_max": 80,
                             "hp": 344, "hp_max": 344, "mp": 168, "mp_max": 168,
                             "sp": 127, "sp_max": 127, "luck": 70, "talent": 136},
                "combat": {"attack": 22, "defense": 8, "foot_speed": 500, "move_speed": 380,
                           "physical_free": 0, "magic_free": 0, "crit": 34, "crit_value": 200,
                           "guard": 27, "guard_value": 0},
                "aptitudes": {"blade": 9, "spear": 3, "sword": 1, "fist": 16, "palm": 15, "finger": 2,
                              "fire": 2, "froze": 12, "thunder": 22, "wind": 3, "earth": 20, "wood": 1,
                              "refine_elixir": 8, "refine_weapon": 10, "geomancy": 5,
                              "symbol": 24, "herbal": 9, "mine": 2},
            },
        },
    }
    t = _r("inspect_unit", {"classes": ["stats"]}, data)
    assert "个人属性：寿命23/108，心情120/120，健康100/100，精力70/80，体力344/344，灵力168/168，念力127/127，幸运70，悟性136" in t, t
    assert "战斗属性：攻击22，防御8，脚力500，移速380，功法抗性0，灵根抗性0，会心34，护心27，暴击倍数200，抗暴倍数0" in t, t
    assert "刀法9，枪法3，剑法1，拳法16，掌法15，指法2" in t, t
    assert "火灵根2，水灵根12，雷灵根22，风灵根3，土灵根20，木灵根1" in t, t
    assert "炼丹8，炼器10，风水5，画符24，药材9，矿材2" in t, t
    assert "魅力375" in t and "声望12" in t and "道心道种" in t, t
    assert t.count("攻击22") == 1, t        # root power/attack 与 attrs.attack 不双列
    assert t.count("体力344/344") == 1, t   # root hp 与 attrs.hp 不双列
    # 旧 C#（无 attrs）回退路径不回归
    old = _r("inspect_unit", {"classes": ["stats"]}, {
        "name": "姜萌", "stats": {"power": 1200, "hp": 1500, "hp_max": 1800, "mood": 72}})
    assert "气血1500/1800" in old and "攻击1200" in old and "心情72" in old, old
    print("✓ inspect attrs 三组全字段（面板分组/缺失跳过/不双列/旧数据回退）")


# ---------------------------------------------------------------------------
# search_units
# ---------------------------------------------------------------------------

def test_search_faithful():
    data = {"filters": {"sect": "北斗剑宗"}, "total": 2, "items": [
        {"name": "林婉清", "relation": "好友", "intim": 72, "realm": "金丹后期", "sect": "北斗剑宗", "region": "白源区"},
        {"name": "张三", "relation": "相识", "intim": 40},
    ]}
    t = _r("search_units", {}, data)
    assert "命中2人" in t and "筛选：sect=北斗剑宗" in t, t
    assert "林婉清（关系好友，好感72，金丹后期/北斗剑宗，白源区）" in t, t
    assert "张三（关系相识，好感40）" in t, t
    print("✓ search 全字段 + 筛选回显")


# ---------------------------------------------------------------------------
# query_world
# ---------------------------------------------------------------------------

def test_qw_sects_faithful():
    """宗门输出（2026-09-11 裁剪后）：不再含 类型+立场值/名词组1/2/id；含 名号主支源/层级/存亡被占/宗主弟子声望敌对/宗旨/气运/坐标。"""
    data = {"sects": [
        {"name": "北斗剑宗", "region": "白源区", "point": {"x": 10, "y": 20},
         "main_name": "北斗", "branch_name": "剑宗", "name_origin": "北斗剑宗",
         "name_part1": ["北斗", "剑"], "is_top": True, "sub_schools": ["化神殿"],
         "is_hold": False, "type": "剑宗", "stand": 1,
         "slogans": [{"slogan": "以剑证道", "desc": "剑修至上"}], "fate": "紫气东来",
         "member_count": 230, "reputation": 1200, "leader": "玄阳子", "enemy": "天魔宗"},
        {"name": "化神殿", "region": "永宁州", "top_school": "北斗剑宗", "is_top": False,
         "is_hold": True, "hold_by": "天魔宗", "member_count": 80},
    ], "total": 2}
    t = _r("query_world", {"topic": "sects"}, data)
    assert "宗门概览（共2个）" in t, t
    assert "白源区·北斗剑宗" in t and "剑宗，立场值" not in t, t     # 类型/立场值已删
    assert "名号：主名北斗，支名剑宗，源名北斗剑宗" in t and "名词组" not in t, t   # 名词组1/2 已删
    assert "层级：主宗，下辖1宗（化神殿）" in t and "状况：未被占据" in t, t
    assert "宗主玄阳子，弟子230人，声望1200，敌对天魔宗" in t, t
    assert "宗旨：以剑证道（剑修至上）" in t and "气运：紫气东来" in t, t
    assert "坐标(10,20)" in t and "id=S1" not in t, t              # id 已删
    assert "层级：分宗，隶属北斗剑宗" in t and "状况：被占据（持有方天魔宗）" in t, t
    # region 过滤：只回该州，标题带州名
    t2 = _r("query_world", {"topic": "sects", "region": "华封州"},
            {"sects": [{"name": "丹阁", "region": "华封州", "is_top": True, "is_hold": False}], "total": 1})
    assert "华封州宗门概览（共1个）" in t2 and "华封州·丹阁" in t2, t2
    print("✓ sects 裁剪后字段（无 派/立场值/名词组/id）+ region 过滤标题")


def test_qw_places_faithful():
    """地点全列出（不再 12/类截断）+ 分类计数 + 坐标"""
    places = [{"name": f"城{i}", "cat": "城镇", "region": "白源区", "point": {"x": i, "y": i}} for i in range(14)]
    places += [{"name": f"宗{i}", "cat": "宗门", "region": "永宁州", "point": {"x": i, "y": i}} for i in range(14)]
    t = _r("query_world", {"topic": "places"}, {"places": places, "total": 28})
    assert "可去地点共28处（城镇14、宗门14）" in t, t
    assert t.count("[城镇]") == 14 and t.count("[宗门]") == 14, t     # 无截断
    assert "[城镇] 白源区·城0(0,0)" in t, t
    print("✓ places 全列出 + 分类计数 + 坐标")


def test_qw_places_cat_filter():
    """cat/region 筛选回显：分类限定 + 空结果提示 + 资源点分类渲染"""
    places = [{"name": "天机阁藏经洞", "cat": "突破材料", "region": "永宁州", "point": {"x": 150, "y": 260}},
              {"name": "器灵残骸", "cat": "器灵材料", "region": "雷泽", "point": {"x": 540, "y": 120}}]
    t = _r("query_world", {"topic": "places", "cat": "突破材料", "region": "永宁州"},
           {"places": [places[0]], "total": 1, "cat": "突破材料"})
    assert "分类=突破材料、州=永宁州可去地点共1处（突破材料1）" in t, t
    assert "[突破材料] 永宁州·天机阁藏经洞(150,260)" in t, t
    t2 = _r("query_world", {"topic": "places", "cat": "器灵材料"}, {"places": [], "total": 0, "cat": "器灵材料"})
    assert "可去地点：无（该筛选条件下没有匹配的地点）" in t2, t2
    print("✓ places cat/region 筛选回显 + 资源点分类 + 空结果")


def test_qw_rankings_and_events():
    t = _r("query_world", {"topic": "rankings", "board": "power"},
           {"board": "power", "total": 120,
            "items": [{"name": "林婉清", "score": 8900, "realm": "金丹后期", "sect": "北斗剑宗"}]})
    assert "战力榜（列出1人，榜内共120人）" in t and "1. 林婉清 8900（金丹后期/北斗剑宗）" in t, t
    t = _r("query_world", {"topic": "events"},
           {"count": 2, "events": [{"month": 3, "text": "魔修袭扰"}, {"month": 4, "text": "剑宗收徒"}]})
    assert "近期天下大事（2条，时间倒序）" in t, t
    assert "（3月）魔修袭扰" in t and "（4月）剑宗收徒" in t, t
    print("✓ rankings/events 全条目 + 月份")


# ---------------------------------------------------------------------------
# social / movement / world_ai
# ---------------------------------------------------------------------------

def test_social_faithful():
    t = _r("social_relation", {}, {"op": "add_intim", "target": "姜萌", "requested": 3,
                                   "actual_delta": 3, "intim_before": 59, "current_intim": 62})
    assert "姜萌对你的好感提升了3点" in t and "59→62" in t and "请求3" in t, t
    t = _r("social_relation", {}, {"op": "reduce_intim", "target": "姜萌", "requested": 2,
                                   "actual_delta": 0, "intim_before": 62, "current_intim": 62})
    assert "好感未变化" in t, t
    assert _r("social_relation", {}, {"op": "jie_yuan", "target": "姜萌", "relation": "道侣",
                                      "verified": True}) == "你与姜萌结为道侣。"
    t = _r("social_relation", {}, {"op": "divorce", "target": "姜萌", "relation": "夫妻",
                                   "readback_still_related": False})
    assert "解除夫妻关系" in t and "读回确认关系已解除" in t, t
    t = _r("social_relation", {}, {"op": "jie_yi", "target": "姜萌"})     # 直写兜底：不退回 JSON
    assert "结为结义" in t and "未返回状态校验" in t, t
    print("✓ social 全字段（含差额/读回/兜底）")


def test_movement_faithful():
    assert "已在同一处，无需召唤" in _r("movement", {}, {"op": "summon", "target": "姜萌"})
    assert "你已被召唤到姜萌身边" in _r("movement", {}, {"op": "summon", "target": "姜萌", "moved": True})
    assert "姜萌已传送到你身边" in _r("movement", {}, {"op": "teleport", "target": "姜萌", "moved": True})
    t = _r("movement", {}, {"op": "travel", "target": "姜萌", "destination": "青城", "region": "白源区",
                            "cat": "城镇", "point": {"x": 3, "y": 4}, "moved": True})
    assert "姜萌已动身前往青城（白源区）[城镇]，并在此地等候。" in t and "目的地坐标3,4" in t, t
    assert "此刻就在" in _r("movement", {}, {"op": "travel", "target": "姜萌",
                                            "destination": "青城", "already_there": True})
    print("✓ movement 全字段")


def test_world_ai_faithful():
    # 切磋：原生 21204 确认窗「好，就让我和你切磋一下 | 我现在没有空」，
    # C# 挂起等选项落定后才回 —— 三条分支必须给模型**不同**的话，
    # 否则玩家婉拒了 NPC 还照着"双方已开始切磋"演（用户报的"都是一样的"）。
    assert "你应下了姜萌的切磋。" == _r("world_ai_action", {}, {"op": "spar", "target": "姜萌", "accepted": True})
    assert "你婉拒了姜萌的切磋。" == _r("world_ai_action", {}, {"op": "spar", "target": "姜萌", "accepted": False})
    assert "等待你在原版剧情中回应" in _r("world_ai_action", {}, {"op": "spar", "target": "姜萌", "pending": True})
    # 字段缺失 = 玩家没选就关掉了窗（OnEnd 兜底）→ **不许**替玩家编一个"婉拒"
    miss = _r("world_ai_action", {}, {"op": "spar", "target": "姜萌"})
    assert "婉拒" not in miss and "应下" not in miss and "未作答复" in miss, miss
    assert "未作答复" in _r("world_ai_action", {}, {"op": "spar", "target": "姜萌", "answered": False})
    # attack 仍是立即返回（UnitActionRoleAttack 连 OnEnd 都没有，接不了选择链）
    assert "已向你发起攻击，即将进入战斗/切磋界面" in _r("world_ai_action", {}, {"op": "attack", "target": "姜萌"})
    assert "你与姜萌的论道已结束。" == _r("world_ai_action", {}, {"op": "lun_dao", "target": "姜萌", "completed": True})
    assert "未完成" in _r("world_ai_action", {}, {"op": "lun_dao", "target": "姜萌", "completed": False})
    # 字段缺失（非挂起形态）不得误判为未完成/被拒
    assert "等待完成" in _r("world_ai_action", {}, {"op": "lun_dao", "target": "姜萌"})
    assert "等待你在原版剧情中回应" in _r("world_ai_action", {}, {"op": "yao_yue", "target": "姜萌"})
    # 字段缺失（非挂起形态）不得误判为未完成/被拒
    assert "等待完成" in _r("world_ai_action", {}, {"op": "lun_dao", "target": "姜萌"})
    assert "等待你在原版剧情中回应" in _r("world_ai_action", {}, {"op": "yao_yue", "target": "姜萌"})
    assert "等待你在原版剧情中回应" in _r("world_ai_action", {}, {"op": "yao_yue", "target": "姜萌", "pending": True})
    assert "你接受了姜萌的邀约（将按剧情赴约）。" in _r("world_ai_action", {}, {"op": "yao_yue", "target": "姜萌", "accepted": True})
    assert "对方似有不悦" in _r("world_ai_action", {}, {"op": "chuan_gong", "target": "姜萌",
                                                       "skill": "青元诀", "accepted": False, "upset": True})
    assert "传授于你" in _r("world_ai_action", {}, {"op": "chuan_gong", "target": "姜萌",
                                                   "skill": "青元诀", "pending": True})
    # 功法来源（审计补渲染）：gainSkill=玩家在原生功法面板自选，seed=用 NPC 功法槽播种。
    # 不区分的话，玩家自选的功法会被模型说成"我传你这部"，语气反了。字段缺失不得当成 seed。
    g = _r("world_ai_action", {}, {"op": "chuan_gong", "target": "姜萌", "skill": "青元诀",
                                   "accepted": True, "skill_source": "gainSkill"})
    assert "你选定要学的" in g, g
    seed = _r("world_ai_action", {}, {"op": "chuan_gong", "target": "姜萌", "skill": "青元诀",
                                      "accepted": True, "skill_source": "seed"})
    assert "你选定要学的" not in seed and "你接受了姜萌传授的功法「青元诀」。" == seed, seed
    missing = _r("world_ai_action", {}, {"op": "chuan_gong", "target": "姜萌", "skill": "青元诀",
                                         "accepted": True})
    assert missing == seed, "字段缺失不得被当成 seed（契约：缺失 ≠ 否定）"
    print("✓ world_ai 全 op")


def test_yao_yue_invite_text():
    """邀约接受后带上第二层原生剧情的原句（C# DramaTextCapture 从 GetDialogueText 抓的成品句，
    地点就在句子里的「在××附近」）。抓不到则退回旧文案——契约铁律：字段缺失 ≠ 否定，不许编地点。
    真机样本（2026-09-12 云含→新达镇）用真句，防渲染层偷偷改写原文。"""
    said = "我先前在新达镇附近发现了一处幽静之地，不如我们到那边去吧。我会在新达镇附近等你三个月。"
    got = _r("world_ai_action", {}, {"op": "yao_yue", "target": "云含",
                                     "accepted": True, "invite_text": said})
    assert said in got, got                      # 原文逐字保留（含地点）
    assert "新达镇" in got, got
    assert "勿复述" in got, got
    assert got.startswith("你接受了云含的邀约。云含说：「"), got
    assert "invite_place" not in got and "约定地点" not in got, got   # 已不再有独立地点字段

    # 抓不到（C# 没透出字段）→ 完全退回改动前文案
    plain = _r("world_ai_action", {}, {"op": "yao_yue", "target": "云含", "accepted": True})
    assert plain == "你接受了云含的邀约（将按剧情赴约）。", plain

    # 拒绝路径：即便 C# 误带了字段，渲染层也不透出
    refused = _r("world_ai_action", {}, {"op": "yao_yue", "target": "云含",
                                         "accepted": False, "invite_text": said})
    assert "新达镇" not in refused and "拒绝了" in refused, refused
    print("✓ yao_yue 邀约原句渲染")


# ---------------------------------------------------------------------------
# economy / item_acquire
# ---------------------------------------------------------------------------

def test_economy_faithful():
    """逐项 请求/实得、部分收下、全拒收、灵石、失败项、送达方式——全部呈现"""
    base = {"target": "唐炎", "via": "direct", "same_grid": True}
    items = [{"item": "六品玉琼丹", "requested": 14, "actual": 14, "mode": "props"}]
    t = _r("economy_item", {"initiator": "姜萌"}, {**base, "items": items, "accepted": True, "refused_count": 0})
    assert t.startswith("姜萌赠予唐炎：") and "收下了赠礼（六品玉琼丹×14）" in t and "当面送达" in t, t
    items2 = [{"item": "六品玉琼丹", "requested": 20, "actual": 14, "mode": "props"},
              {"item": "九转金丹", "requested": 1, "actual": 0, "error": "背包中没有"}]
    t = _r("economy_item", {"initiator": "姜萌"}, {**base, "items": items2, "accepted": True, "refused_count": 0})
    assert "请求20，实得14" in t and "九转金丹（请求1，未达成：背包中没有）" in t, t
    t = _r("economy_item", {"initiator": "姜萌"},
           {**base, "items": items, "accepted": False, "refused_count": 2, "accepted_count": 12})
    assert "收下了部分赠礼" in t and "拒收了其余2件" in t, t
    t = _r("economy_item", {"initiator": "姜萌"},
           {**base, "items": items, "accepted": False, "refused_count": 14, "accepted_count": 0})
    assert "拒绝了赠礼" in t and "「我不需要这个」" in t, t
    t = _r("economy_item", {"initiator": "姜萌"},
           {**base, "same_grid": False,
            "items": [{"item": "灵石", "requested": 50, "actual": 50, "mode": "money"}],
            "accepted": True, "refused_count": 0})
    assert "收下了灵石×50" in t and "异地以传讯方式送达" in t, t
    print("✓ economy 全字段（差额/失败项/部分拒收/全拒收/送达方式）")


def test_item_acquire_faithful():
    t = _r("item_acquire", {}, {"op": "steal_item", "target": "姜萌", "item_name": "风玄丝", "stack_count": 16})
    assert "姜萌偷取了你的「风玄丝」，整栈共16个。" == t, t
    t = _r("item_acquire", {}, {"op": "ask_for", "target": "姜萌", "item_name": "六品玉琼丹",
                                "count": 6, "accepted": True})
    assert "你把「六品玉琼丹」×6交给了姜萌。" == t, t
    t = _r("item_acquire", {}, {"op": "ask_for", "target": "姜萌", "item_name": "六品玉琼丹",
                                "count": 6, "accepted": False, "upset": True})
    assert "你拒绝了姜萌的讨要（「六品玉琼丹」×6）" in t and "对方似有不悦" in t, t
    t = _r("item_acquire", {}, {"op": "ask_for", "target": "姜萌", "item_name": "六品玉琼丹",
                                "count": 6, "pending": True})
    assert "等待你在原版剧情中回应" in t, t
    print("✓ item_acquire 全字段")


# ---------------------------------------------------------------------------
# 边界铁律：截断只允许在 C 端（用户定调）
# ---------------------------------------------------------------------------

def test_no_truncation_anywhere():
    """渲染层不得截断：**N 条进 → N 条出**（截断/上限一律在 C# 决定，Python 忠实照渲染）。
    本测试是该铁律的防回退哨兵——若有人再给渲染器加 [:N]/上限，这里立刻红。"""
    def R(tool, args, data):
        out = tr.render(tool, args, {"success": True, "data": data})
        assert out, (tool, data)
        return out

    assert R("query_world", {"topic": "events"},
             {"count": 60, "events": [{"month": i, "text": f"事件{i}"} for i in range(60)]}).count("事件") == 60

    assert R("query_world", {"topic": "places"},
             {"total": 80, "places": [{"name": f"城{i}", "cat": "城镇", "region": "白源区"} for i in range(80)]}
             ).count("[城镇]") == 80

    assert R("query_world", {"topic": "sects"},
             {"total": 40, "sects": [{"name": f"宗{i}", "region": "白源区"} for i in range(40)]}
             ).count("· 白源区·宗") == 40

    assert R("inspect_unit", {},
             {"logs": {"filter": "all", "page": 1, "total": 25, "has_more": True,
                       "items": [{"month": 1, "text": f"事{i}"} for i in range(25)]}}
             ).count("（1年1月）") == 25

    assert R("search_units", {},
             {"total": 30, "items": [{"name": f"人{i}", "relation": "好友", "intim": i} for i in range(30)]}
             ).count("关系好友") == 30

    assert R("inspect_unit", {},
             {"inventory": {"props": [{"cat": "丹符", "items": [{"name": f"丹{i}", "count": 1} for i in range(12)]}]}}
             ).count("丹") >= 12                       # 单类超 6 种也不再截断

    rb = R("inspect_unit", {}, {"relationships": {"friend_units": [f"友{i}" for i in range(9)]}})
    assert "友8" in rb and rb.count("、") == 8         # 关系簿 9 名全列（不再限 3 名、不限总数）

    assert R("query_world", {"topic": "rankings", "board": "power"},
             {"board": "power", "total": 500,
              "items": [{"name": f"侠{i}", "score": 1000 - i} for i in range(120)]}
             ).count("侠") == 120                       # 榜单条数由 C# 决定，渲染不设上限
    print("✓ 无截断哨兵：events/places/sects/logs/search/inventory/关系簿/rankings N进N出")
