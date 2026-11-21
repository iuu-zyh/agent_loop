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
    # top（2026-09-21）：C# 回传 top，头部动态反映实际列出数
    t2 = _r("search_units", {"filters": {}, "top": 50},
            {"filters": {}, "total": 15, "top": 15,
             "items": [{"name": f"弟子{i}", "relation": "陌生", "intim": i} for i in range(15)]})
    assert "命中15人，按好感降序，列前15" in t2, t2
    assert t2.count("弟子") == 15, t2   # 无截断：top 放开时 15 条全出
    print("✓ search 全字段 + 筛选回显 + top 动态头部")


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
    # name 精准查族系（2026-09-21）：标题用族系口径（主宗+分舵都在表里）
    t3 = _r("query_world", {"topic": "sects", "name": "北斗剑宗"}, data)
    assert "「北斗剑宗」宗门族系（主宗+分舵共2处）" in t3, t3
    assert "白源区·北斗剑宗" in t3 and "永宁州·化神殿" in t3, t3   # 主宗与外州分舵都在
    # name 未命中：转述 C# 的 note，不落 region 分支的措辞
    t4 = _r("query_world", {"topic": "sects", "name": "不存在的宗"},
            {"sects": [], "total": 0, "name": "不存在的宗", "note": "未找到名为「不存在的宗」的宗门（天下未闻此名，或已灭除籍）"})
    assert t4 == "未找到名为「不存在的宗」的宗门（天下未闻此名，或已灭除籍）", t4
    t5 = _r("query_world", {"topic": "sects", "name": "不存在的宗"}, {"sects": [], "total": 0})
    assert "「不存在的宗」宗门查询：未找到同名宗门" in t5, t5   # note 缺席时的兜底措辞
    print("✓ sects 裁剪后字段（无 派/立场值/名词组/id）+ region 过滤标题 + name 族系/未命中")


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
    # name 包含匹配（2026-09-21）：命中带"名含="回显；空表转述 C# note；note 缺席时兜底措辞
    t3 = _r("query_world", {"topic": "places", "name": "丹阳谷"},
            {"places": [{"name": "丹阳谷", "cat": "宗门", "region": "永宁州", "point": {"x": 1, "y": 2}},
                        {"name": "丹阳谷水宁州分舵", "cat": "宗门", "region": "水宁州", "point": {"x": 3, "y": 4}}],
             "total": 2, "name": "丹阳谷"})
    assert "名含=丹阳谷可去地点共2处（宗门2）" in t3, t3
    assert "[宗门] 永宁州·丹阳谷(1,2)" in t3 and "[宗门] 水宁州·丹阳谷水宁州分舵(3,4)" in t3, t3
    t4 = _r("query_world", {"topic": "places", "name": "不存在的地点"},
            {"places": [], "total": 0, "name": "不存在的地点", "note": "未找到名字含「不存在的地点」的地点"})
    assert t4 == "未找到名字含「不存在的地点」的地点", t4
    t5 = _r("query_world", {"topic": "places", "name": "不存在的地点"}, {"places": [], "total": 0})
    assert "未找到名字含「不存在的地点」的地点" in t5, t5
    print("✓ places cat/region 筛选回显 + 资源点分类 + 空结果 + name 包含匹配")


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


def test_npc_solo_and_together_render():
    """`mode` 的两个值必须走**不同**的渲染分支（10-27 改名后钉住）。

    事故形态：工具参数改叫 `solo`/`together`，而渲染层还只认旧的 `watch`——
    于是 `solo` 的观战结果落进"你向某人发起攻击（等他应战）"那条文案，
    模型读到的是一场"还没打"的攻击，而玩家那边整场已经打完了。
    `together` 更不能落进观战：那一场玩家是**参战方**，不是旁观者。
    """
    solo = {"op": "attack", "target": "姜萌", "mode": "solo",
            "battle_seen": True, "battle_finished": True, "spectator": "唐炎",
            "left": {"name": "江宇文", "alive": True, "hp": 120, "hp_max": 200},
            "right": {"name": "姜萌", "alive": False, "hp": 0, "hp_max": 180},
            "winner": "江宇文"}
    out = _r("world_ai_action", {"op": "attack"}, solo)
    assert "唐炎在旁看着" in out, out
    # together：玩家参战，绝不能出现"旁观"字样
    tog = {"op": "attack", "target": "姜萌", "mode": "together",
           "battle_seen": True, "battle_finished": True}
    tout = _r("world_ai_action", {"op": "attack"}, tog)
    assert "在旁看着" not in tout, tout


def test_npc_watch_render():
    """NPC 对 NPC 观战（10-26）：`mode=watch` 走独立分支，且**不许编胜负**。

    （`watch` 是 10-27 改名前的开发名，渲染层仍认它，见上面的 `solo`/`together` 测试。）

    三档事实各自对应一句话：打起来了且有一方倒下 / 打起来了但谁都没倒 / 根本没打起来。
    第二条是本测试的重点 —— `winner` 缺失时必须显式说"未定"，否则模型会自己补一个赢家。
    """
    watch = {"op": "attack", "target": "姜萌", "mode": "watch",
             "battle_seen": True, "battle_finished": True, "spectator": "唐炎",
             "left": {"name": "江宇文", "alive": True, "hp": 120, "hp_max": 200},
             "right": {"name": "姜萌", "alive": False, "hp": 0, "hp_max": 180},
             "winner": "江宇文"}
    out = _r("world_ai_action", {"op": "attack"}, watch)
    assert "江宇文" in out and "占了上风" in out and "姜萌" in out
    # 玩家是**旁观的第三人**（`spectator`），不是参战方 —— 这句话必须有，
    # 否则 NPC 会把"打了一场"讲成私斗，忘了玩家就在旁边看着。
    assert "唐炎在旁看着" in out, out
    assert "唐炎占了上风" not in out and "唐炎落败" not in out, out
    base_out = out          # 无 judgement 的基线：下面钉"一个字都不许多"

    watch_no_winner = dict(watch)
    watch_no_winner.pop("winner")
    watch_no_winner["right"] = {"name": "姜萌", "alive": True, "hp": 80, "hp_max": 180}
    out = _r("world_ai_action", {"op": "attack"}, watch_no_winner)
    assert "胜负未定" in out and "都没有倒下" in out
    assert "占了上风" not in out

    # `spectator` 缺失 = 读不到玩家名字 ⇒ 不提"谁在看"，也不许自己编一个
    watch_blind = dict(watch_no_winner)
    watch_blind.pop("spectator")
    out = _r("world_ai_action", {"op": "attack"}, watch_blind)
    assert "在旁看着" not in out, out
    assert "胜负未定" in out

    # `alive` 整个缺失（C# 两处证据都读不到）= 事实缺失，照样只能说"未定"
    watch_bare = {"op": "attack", "target": "姜萌", "mode": "watch", "battle_seen": True,
                  "left": {"name": "江宇文"}, "right": {"name": "姜萌"}}
    out = _r("world_ai_action", {"op": "attack"}, watch_bare)
    assert "胜负未定" in out and "占了上风" not in out, out

    # 战后处置页（10-26）：`judgement` = C# 在**真的拦住 BattleEnd、弹了原版 20403、
    # 拿到玩家的选择**之后才给的键。有它 ⇒ 补一句"你上前处置了谁"。
    watch_kill = dict(watch)
    watch_kill["judgement"] = {"loser": "姜萌", "outcome": "kill", "label": "击杀此人"}
    out = _r("world_ai_action", {"op": "attack"}, watch_kill)
    assert "你上前处置了姜萌：击杀此人。" in out, out
    assert out.startswith("你与姜萌当场动手"), out      # 原有那句必须原样在前面

    # `label` 缺失 ⇒ 用 `outcome` 兜底（键表与 C# `NpcDuel.SetOutcome` 一一对应）。
    # **不许**把英文 slug 原样吐给模型。
    watch_no_label = dict(watch)
    watch_no_label["judgement"] = {"loser": "姜萌", "outcome": "spare"}
    out = _r("world_ai_action", {"op": "attack"}, watch_no_label)
    assert "算了，东西不要了，你走吧" in out, out
    assert "spare" not in out, out

    # 玩家把窗关掉（ESC/点窗外）= 放过：C# 给 outcome="closed"，文案照"放过"说
    watch_closed = dict(watch)
    watch_closed["judgement"] = {"loser": "姜萌", "outcome": "closed"}
    out = _r("world_ai_action", {"op": "attack"}, watch_closed)
    assert "姜萌倒在你面前" in out and "由他去了" in out, out

    # 败者是**读这段的 NPC 自己**（观战里发起方也可能输）：不许写成"你处置了你自己"
    watch_me = {"op": "attack", "target": "姜萌", "mode": "watch", "battle_seen": True,
                "left": {"name": "江宇文", "alive": False, "hp": 0, "hp_max": 200},
                "right": {"name": "姜萌", "alive": True, "hp": 80, "hp_max": 180},
                "winner": "姜萌",
                "judgement": {"loser": "江宇文", "outcome": "spare", "label": "算了，东西不要了，你走吧"}}
    out = _r("world_ai_action", {"op": "attack"}, watch_me)
    assert "你上前处置了江宇文" not in out, out
    assert "你倒在姜萌手边" in out and "算了，东西不要了，你走吧" in out, out

    # 没有 `judgement`（没人倒下 / 页没开成 / 玩家还没选）⇒ **一个字都不许多**
    out = _r("world_ai_action", {"op": "attack"}, dict(watch))
    assert out == base_out, out
    assert "处置" not in out, out

    # 战败重伤（10-26 用户要求）：除击杀外，败者都要挂「重伤」。
    # C# 把**实际结果**放 `judgement.hurt`（自由文本），渲染层只认"挂载成功 / 已在身"两个词。
    watch_hurt = dict(watch)
    watch_hurt["judgement"] = {"loser": "姜萌", "outcome": "spare", "label": "算了，东西不要了，你走吧",
                               "hurt": "战败重伤=气运 5011（表 RoleBattle.hurtFeatureID）挂载成功"}
    out = _r("world_ai_action", {"op": "attack"}, watch_hurt)
    assert "姜萌伤势不轻" in out and "「重伤」在身" in out, out

    # 击杀那一支：`hurt` 写的是"不需要（已击杀）" ⇒ 不许再说"重伤"
    watch_killed = dict(watch)
    watch_killed["judgement"] = {"loser": "姜萌", "outcome": "kill", "label": "击杀此人",
                                 "hurt": "战败重伤=不需要（已击杀）"}
    out = _r("world_ai_action", {"op": "attack"}, watch_killed)
    assert "伤势不轻" not in out and "重伤" not in out, out

    # 没挂上（读表失败/单位认不出）⇒ 同样不许替 C# 说"他重伤了"
    watch_nohurt = dict(watch)
    watch_nohurt["judgement"] = {"loser": "姜萌", "outcome": "spare", "label": "算了，东西不要了，你走吧",
                                 "hurt": "战败重伤=未落账（认不出败者的世界单位）"}
    out = _r("world_ai_action", {"op": "attack"}, watch_nohurt)
    assert "伤势不轻" not in out, out

    not_started = {"op": "attack", "target": "姜萌", "mode": "watch", "battle_seen": False}
    out = _r("world_ai_action", {"op": "attack"}, not_started)
    assert "没能开起来" in out


def test_world_ai_faithful():
    # 切磋：原生 21204 确认窗「好，就让我和你切磋一下 | 我现在没有空」，
    # C# 挂起等选项落定后才回 —— 三条分支必须给模型**不同**的话，
    # 否则玩家婉拒了 NPC 还照着"双方已开始切磋"演（用户报的"都是一样的"）。
    # 应战之后 C# 要跨过整场战斗才回（09-22）：`accepted:true` 只是入场券，结局看
    # battle_finished / lines。裸 accepted:true（没有任何战斗字段）= 没等到下文，
    # 必须明说"未知"，不许让模型以为打完了。
    # ★视角：读这段的是**发起方的 NPC**（`Agent.id`），所以"他应下 / 他婉拒 / 他说"，
    #   旧文案那句"你应下了姜萌的切磋"（姜萌就是读的人）等于告诉 NPC 应战的是它自己。
    P = {"target": "姜萌", "player": "唐炎"}
    bare = _r("world_ai_action", {}, {"op": "spar", **P, "accepted": True})
    assert bare.startswith("唐炎应下了你的切磋。") and "别据此推断" in bare, bare
    won = _r("world_ai_action", {}, {"op": "spar", **P, "accepted": True,
                                     "battle_seen": True, "battle_finished": True,
                                     "lines": [{"who": "player", "name": "唐炎", "text": "厉害，我输了。"},
                                               {"who": "npc", "name": "姜萌", "text": "看来是我赢了！"}]})
    assert won.startswith("唐炎应下了你的切磋，两人动起手来。"), won
    assert "唐炎说：厉害，我输了。" in won and "你说：看来是我赢了！" in won, won
    assert "未知" not in won, won          # 有原话就不该再补一句"结果未知"
    # 判不出说话人 → 写"有人"，绝不猜（猜反了归属，胜负就安错了人头）
    blind = _r("world_ai_action", {}, {"op": "spar", **P, "accepted": True,
                                       "battle_finished": True,
                                       "lines": [{"who": "", "name": "", "text": "承让。"}]})
    assert "有人说：承让。" in blind, blind
    # 打完了但一句词都没抓到
    quiet = _r("world_ai_action", {}, {"op": "spar", **P, "accepted": True,
                                       "battle_finished": True})
    assert "没拿到战后的话" in quiet and "别自行推断" in quiet, quiet
    assert "婉拒" not in bare and "婉拒" in _r("world_ai_action", {}, {"op": "spar", **P, "accepted": False})
    assert "唐炎婉拒了你的切磋。" == _r("world_ai_action", {}, {"op": "spar", **P, "accepted": False})
    assert "等待他在原版剧情中回应" in _r("world_ai_action", {}, {"op": "spar", **P, "pending": True})
    # 字段缺失 = 玩家没选就关掉了窗（OnEnd 兜底）→ **不许**替玩家编一个"婉拒"
    miss = _r("world_ai_action", {}, {"op": "spar", **P})
    assert "婉拒" not in miss and "应下" not in miss and "未作答复" in miss, miss
    assert "未作答复" in _r("world_ai_action", {}, {"op": "spar", **P, "answered": False})
    # 攻击（09-22 也跨过战斗了）：没结局不许编，收在处置页必须写"生死未定"
    a0 = _r("world_ai_action", {}, {"op": "attack", "target": "郑南", "player": "唐炎"})
    assert "别据此推断" in a0 and "婉拒" not in a0, a0
    a1 = _r("world_ai_action", {}, {"op": "attack", "target": "郑南", "player": "唐炎",
                                    "battle_seen": True, "verdict_page": True,
                                    "lines": [{"who": "npc", "name": "郑南", "text": "饶我一条性命…"}]})
    assert "生死未定" in a1 and "你说：饶我一条性命…" in a1, a1
    # attack 的裸结果（09-22 起也跨过战斗了）：不得再是那句写死的"即将进入战斗"
    a_bare = _r("world_ai_action", {}, {"op": "attack", "target": "姜萌", "player": "唐炎"})
    assert "即将进入战斗" not in a_bare and "别据此推断" in a_bare, a_bare

    # ★`mode=together` 的视角必须与 solo / 打玩家**分开**（10-27）★
    #   三者走的是同一个 `_render_battle_result`，但"谁动手、谁被处置"完全不同：
    #     solo     = 你与 B 单挑、玩家旁观（另有 `_render_npc_watch`，不到这里）；
    #     打玩家   = 你出刀 ⇒ 处置页 = 玩家战败，玩家处置你；
    #     together = **玩家当发起方**打 B ⇒ 同一个处置页 = B 战败，玩家处置他。
    #   写反了模型就会替玩家做决定（"我放你一马"），而玩家手还没动。
    #
    # ★字段语义（10-27 复核确认，别改错）★ 两条链里 `target` 指的不是同一个人：
    #   · 打玩家 / spar：`Arm(…, target, …)` 传的是**发起方 NPC 自己的名字**，
    #     所以 `target` 不能当对手名；对手（= 玩家）在 `player`。
    #   · together：`Arm(…, foeName, …)` 传的是**对手 B 的名字**，`player` 则是玩家自己。
    #   因此本节的 fixture 按两条链各自的真实形状给，不是同一份换 mode。
    PLAYER = "唐炎"          # 玩家显示名（`PlayerName()`）
    # 打玩家：你出刀、对手是玩家 ⇒ 处置页 = 玩家战败，玩家处置你
    to_player = {"op": "attack", "target": "管星贤", "player": PLAYER,
                 "battle_seen": True, "battle_finished": True, "verdict_page": True}
    r1 = _r("world_ai_action", {}, to_player)
    assert f"你向{PLAYER}动了手" in r1, r1
    assert "玩家带着你和队友" not in r1, r1
    assert "你落在下风" in r1, r1
    # together：玩家当发起方打 B ⇒ 同一个处置页 = B 战败，玩家处置他
    to_together = {"op": "attack", "target": "姜萌", "player": PLAYER, "mode": "together",
                   "battle_seen": True, "battle_finished": True, "verdict_page": True}
    r2 = _r("world_ai_action", {}, to_together)
    assert "你和唐炎一起向姜萌动了手" in r2, r2
    assert "正由**玩家**处置他" in r2, r2
    # 绝不能再写成"你落了下风"（那会把玩家赢说成你输）
    assert "你落在下风" not in r2, r2

    # ★"没打起来"要如实说，不能报成"两边打起来"★（10-28 真机：姜萌逃跑那场）
    #   引擎走的是 `UnitActionFeedback101` 逃跑判定，弹页 20102（只有一个「确定」），
    #   链上**有台词**但**没进过战斗**。旧判据把"有台词"也算成"打过"。
    esc = {"op": "attack", "target": "姜萌", "player": "唐炎", "mode": "together",
           "battle_seen": False, "battle_finished": False, "no_battle": True,
           "lines": [{"kind": "page", "who": "player", "text": "哼！逃得倒是挺快！"},
                     {"kind": "option", "text": "确定"}]}
    r3 = _r("world_ai_action", {}, esc)
    assert "两边打起来" not in r3, "逃跑那场被报成\"打起来\"了：" + r3
    assert "没能打起来" in r3, r3
    # 台词照给（玩家确实说了那句话），但归属不能被改
    assert "唐炎说：哼！逃得倒是挺快！" in r3, r3
    # 对手名是 target（姜萌），不能是玩家名
    assert "向姜萌动了手" in r3, r3
    # 也绝不能把玩家名当对手名（那是打玩家那条链的形状，串过来就是"向自己动手"）
    assert f"向{PLAYER}动了手" not in r2, r2
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


def test_player_choices_are_rendered():
    """玩家在交互里点过的选项必须出现在结果正文里，**且落在它回答的那一页之后**（时序）。

    真机现场（切磋）：C# 侧 `TalkOutcome` 收了开窗页 + 两条选择（接受 / 离开）、`BattleOutcome`
    收了两句战后台词，但模型只看到"应下了" + 一句开窗语。三处缺陷：C# 侧 `FillResult` 覆盖了
    `lines`（改成合并，见契约 `test_talk_outcome_wiring`）；渲染层没有 `choices` 出口；
    没有出口时选择若简单堆在末尾，**战前**点的「接受切磋」会排到战后台词后面（用户当场指出）。
    这里钉住渲染层这一半 —— 精确到行序。
    """
    P = {"target": "江宇文", "player": "唐炎"}
    t = _r("world_ai_action", {}, {"op": "spar", **P, "accepted": True, "battle_seen": True,
                                   "battle_finished": True,
                                   # C# 合并后的顺序：台账的开窗页在前，战斗链的两句在后
                                   "lines": [{"who": "npc", "name": "江宇文", "drama_id": 21204,
                                              "text": "前辈修为如此高深，能否与我切磋一番？"},
                                             {"who": "player", "name": "唐炎", "drama_id": 21301,
                                              "text": "承让！"},
                                             {"who": "npc", "name": "江宇文", "drama_id": 21302,
                                              "text": "唐兄，你果然厉害！"}],
                                   "choices": [{"text": "好，就让我和你切磋一下", "after_drama_id": 21204},
                                               {"text": "离开", "after_drama_id": 21302}]})
    assert t.splitlines() == [
        "唐炎应下了你的切磋，两人动起手来。",
        "你说：前辈修为如此高深，能否与我切磋一番？",
        "唐炎选择了：「好，就让我和你切磋一下」",     # 战前的应答，紧跟发问那一页
        "唐炎说：承让！",
        "你说：唐兄，你果然厉害！",
        "唐炎选择了：「离开」",                       # 战后那页的应答，跟在它后面
    ], t
    # 论道/双修：结果要带交互原文与玩家选择（用户原话："返回论道期间的 UI 文本，以及选择"）
    t2 = _r("world_ai_action", {}, {"op": "lun_dao", "target": "姜萌", "player": "唐炎",
                                    "completed": True,
                                    "lines": [{"who": "npc", "name": "姜萌", "drama_id": 22834,
                                               "text": "若二者不可兼得，你当如何？"},
                                              {"who": "npc", "name": "姜萌", "drama_id": 22836,
                                               "text": "那你打算从何处着手？"}],
                                    "choices": [{"text": "尽力二者皆救", "after_drama_id": 22834},
                                                {"text": "非礼勿视", "after_drama_id": 22836}]})
    assert t2.splitlines() == [
        "你与姜萌的论道已结束。",
        "你说：若二者不可兼得，你当如何？",
        "唐炎选择了：「尽力二者皆救」",
        "你说：那你打算从何处着手？",
        "唐炎选择了：「非礼勿视」",
    ], t2
    # 没有内容时保持旧文案逐字不变（不给"论道已结束"多加一行）
    assert _r("world_ai_action", {}, {"op": "lun_dao", "target": "姜萌", "completed": True}) \
        == "你与姜萌的论道已结束。"
    # 选择对不上任何一页（那页没记上台词）→ 追加在末尾，绝不丢；判不出玩家名写"玩家"
    t3 = _r("world_ai_action", {}, {"op": "lun_dao", "target": "姜萌", "completed": True,
                                    "choices": [{"text": "一同论道"}]})
    assert "玩家选择了：「一同论道」" in t3, t3

    # ★新形状（09-23 单流）：`lines` 自己就是时序，每行带 `kind` —— 渲染层只按顺序翻译，
    #   不再做任何"按页号交错"。这正是切磋结果顺序错位的根治点。
    t4 = _r("world_ai_action", {}, {
        "op": "spar", "target": "江宇文", "player": "唐炎",
        "accepted": True, "battle_seen": True, "battle_finished": True,
        "lines": [
            {"kind": "page", "who": "npc", "name": "江宇文", "drama_id": 21204,
             "text": "前辈修为如此高深，能否与我切磋一番？"},
            {"kind": "option", "who": "choice", "name": "唐炎", "drama_id": 21204,
             "text": "好，就让我和你切磋一下"},
            {"kind": "page", "who": "player", "name": "唐炎", "drama_id": 21301,
             "text": "承让！"},
            {"kind": "option", "who": "choice", "name": "唐炎", "drama_id": 21302,
             "text": "离开"},
        ]})
    assert t4.splitlines() == [
        "唐炎应下了你的切磋，两人动起手来。",
        "你说：前辈修为如此高深，能否与我切磋一番？",
        "唐炎选择了：「好，就让我和你切磋一下」",
        "唐炎说：承让！",
        "唐炎选择了：「离开」",
    ], t4

    # 选择**不算**"链上原话"：只有选择、没有任何台词/战斗信号时，不许据此判成"打过了"
    t5 = _r("world_ai_action", {}, {
        "op": "spar", "target": "江宇文", "player": "唐炎", "accepted": True,
        "lines": [{"kind": "option", "who": "choice", "name": "唐炎",
                   "text": "好，就让我和你切磋一下"}]})
    assert t5.splitlines() == ["唐炎应下了你的切磋。", "唐炎选择了：「好，就让我和你切磋一下」"], t5
    print("✓ 玩家选择渲染（新旧两种形状：单流顺序 / 旧交错）")


def test_screen_note_is_split_and_repointed():
    """屏幕括注里的「你」必须换成玩家名，且**只动括注**（09-24 真机：胜负被读反）。

    真机现场（attack 战败逃跑那页）：屏幕成品是**一整串**——
      「别跑！…下次别让我遇见你！！！（对方战败后，趁你放松的时刻拔腿就跑，你连忙追上去却不见了踪影）」
    括注是**游戏写给玩家屏幕的叙述**（其中的「你」=玩家），却跟着台词一起被采集；模型把括注里的
    「你」接到自己身上，把"我方战败逃走"读成了"玩家逃走"（会话记录里它的推理是
    "NPC 获胜、唐炎 fled"，回复成了"我管星贤的拳，你躲不过第二次"）。

    规则（见 `text_render._NOTE_RE`）：括注含「你」→ 摘成独立「叙述：」行、其中的「你」换成
    玩家名；**台词里的「你」不许动**（玩家对 NPC 说的话）；不含「你」的括注原样保留。
    """
    P = {"op": "attack", "target": "管星贤", "player": "唐炎"}
    t = _r("world_ai_action", {}, {**P, "battle_seen": True, "battle_finished": True, "lines": [
        {"kind": "page", "who": "npc", "name": "管星贤", "drama_id": 1,
         "text": "站住！与我决一死战吧！"},
        {"kind": "page", "who": "player", "name": "唐炎", "drama_id": 2,
         "text": "别跑！打不过就跑，比兔子还快，下次别让我遇见你！！！"
                 "（对方战败后，趁你放松的时刻拔腿就跑，你连忙追上去却不见了踪影）"},
    ]})
    assert t.splitlines() == [
        "你向唐炎动了手，两人打起来。",
        "你说：站住！与我决一死战吧！",
        "唐炎说：别跑！打不过就跑，比兔子还快，下次别让我遇见你！！！",   # 台词里的「你」=NPC，不动
        "（叙述：对方战败后，趁唐炎放松的时刻拔腿就跑，唐炎连忙追上去却不见了踪影）",
    ], t

    # 括注与 who 无关（叙述是屏幕的，不是说话人的）：NPC 那一行带着也照摘
    t2 = _r("world_ai_action", {}, {**P, "battle_seen": True, "battle_finished": True, "lines": [
        {"kind": "page", "who": "npc", "name": "管星贤", "drama_id": 1,
         "text": "哼（趁你分神，他拔腿就跑）"},
    ]})
    assert t2.splitlines() == ["你向唐炎动了手，两人打起来。", "你说：哼",
                               "（叙述：趁唐炎分神，他拔腿就跑）"], t2

    # 不含「你」的括注：原样留在台词里（没有指代冲突，不动阅读体验）
    t3 = _r("world_ai_action", {}, {**P, "battle_seen": True, "battle_finished": True, "lines": [
        {"kind": "page", "who": "player", "name": "唐炎", "drama_id": 1,
         "text": "哼（冷笑），我记住你了。"},
    ]})
    assert "唐炎说：哼（冷笑），我记住你了。" in t3.splitlines(), t3

    # 「你们」避让：那是冲着人说的话，归属不定，不许替换成"唐炎们"
    body, notes = tr._split_note("（你们两个一起上吧）", "唐炎")
    assert body == "" and notes == ["（叙述：你们两个一起上吧）"], (body, notes)

    # 旧形状（不带 kind，走 after_drama_id 交错兜底）同样摘行
    t4 = _r("world_ai_action", {}, {**P, "battle_seen": True, "battle_finished": True,
                                    "lines": [{"who": "player", "name": "唐炎", "drama_id": 9,
                                               "text": "走！（你追了上去，却不见踪影）"}],
                                    "choices": [{"text": "离开", "after_drama_id": 9}]})
    assert t4.splitlines() == [
        "你向唐炎动了手，两人打起来。",
        "唐炎说：走！",
        "（叙述：唐炎追了上去，却不见踪影）",
        "唐炎选择了：「离开」",
    ], t4
    print("✓ 屏幕括注：摘成「叙述」行 + 括注内「你」→ 玩家名（台词里的「你」不动）")


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
    # 灵石讨要对玩家（2026-09-21 确认窗链路）：Ok 帧通用形态；Err 帧（玩家点"不给"/足额不足）
    # 必须短路交回 json.dumps —— 绝不能渲染成"等待回应"
    t = _r("item_acquire", {"op": "ask_for", "item_name": "灵石", "count": 500},
           {"op": "ask_for", "target": "姜萌", "item_name": "灵石", "count": 500, "accepted": True})
    assert "你把「灵石」×500交给了姜萌。" == t, t
    err = {"success": False, "error": "你没有把灵石给姜萌"}
    assert _r("item_acquire", {"op": "ask_for", "item_name": "灵石", "count": 500}, err) is None, "失败帧必须短路"
    assert _r("item_acquire", {"op": "ask_for", "item_name": "六品玉琼丹", "count": 6},
              {"success": False, "error": "姜萌的「六品玉琼丹」单栈仅 2 个，不足 6 个"}) is None, "既有 Fail 帧同样短路"
    print("✓ item_acquire 全字段 + 灵石确认窗帧 + 失败帧短路")


# ---------------------------------------------------------------------------
# 边界铁律：截断只允许在 C 端（用户定调）
# ---------------------------------------------------------------------------

def test_counterparty_third_person():
    """对手方不是玩家（`counterparty`）时的第三人称措辞（09-16）。

    为什么必须区分：这些 tool_result 是**给 NPC 读的世界事实**，旧文案里的「你」指玩家 ——
    对手方换成另一个 NPC 后再写"你"会读成"对方对玩家"，人称反了。
    缺省（无 counterparty）必须逐字节保持旧措辞（上面各条测试就是这条的哨兵）。
    """
    t = _r("social_relation", {}, {"op": "add_intim", "target": "张三", "counterparty": "李四",
                                   "requested": 3, "actual_delta": 3, "intim_before": 10, "current_intim": 13})
    assert "张三对李四的好感提升了3点" in t and "10→13" in t, t
    assert _r("social_relation", {}, {"op": "jie_yuan", "target": "张三", "counterparty": "李四",
                                      "relation": "道侣", "verified": True}) == "张三与李四结为道侣。"
    t = _r("social_relation", {}, {"op": "jie_chu_jie_yuan", "target": "张三", "counterparty": "李四",
                                   "relation": "道侣", "readback_still_related": False})
    assert "张三与李四解除了道侣关系" in t and "读回确认关系已解除" in t, t

    assert _r("movement", {}, {"op": "teleport", "target": "张三", "counterparty": "李四",
                               "moved": True}) == "张三已动身前往李四身边（张三移动到李四那处）。"
    assert _r("movement", {}, {"op": "summon", "target": "张三", "counterparty": "李四",
                               "moved": True}) == "李四已被张三召到身边（李四移动到张三所在处）。"
    assert _r("movement", {}, {"op": "teleport", "target": "张三", "counterparty": "李四"}) \
        == "张三与李四已在同一处，无需传送。"

    t = _r("economy_item", {"initiator": "张三"},
           {"op": "give_item", "target": "李四", "counterparty": "李四",
            "items": [{"item": "丹药", "requested": 2, "actual": 2, "mode": "props"}],
            "accepted": True, "refused_count": 0, "same_grid": True})
    assert t.startswith("张三赠予李四：") and "李四收下了赠礼（丹药×2）" in t, t

    assert _r("item_acquire", {}, {"op": "steal_item", "target": "张三", "counterparty": "李四",
                                   "item_name": "丹药", "stack_count": 3}) \
        == "张三偷取了李四的「丹药」，整栈共3个。"
    assert _r("item_acquire", {}, {"op": "ask_for", "target": "张三", "counterparty": "李四",
                                   "item_name": "丹药", "count": 2, "accepted": True}) \
        == "李四把「丹药」×2交给了张三。"
    t = _r("item_acquire", {}, {"op": "ask_for", "target": "张三", "counterparty": "李四",
                                "item_name": "丹药", "count": 2, "accepted": False, "upset": True})
    assert "李四拒绝了张三的讨要" in t and "对方似有不悦" in t, t
    print("✓ counterparty：非玩家对手方走第三人称措辞（含社交/位移/赠送/偷讨）")


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
