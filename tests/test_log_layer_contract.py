"""经历日志「分层」源码契约（09-13）

事故：`inspect_unit(classes=["logs"])` 每条都返回 `(无文本)`，而日志里**一条错误都没有**。
根因是 C# 侧取错了层——两层的真实类型（读编译目标 `MelonLoader\\Managed\\Assembly-CSharp.dll` 实证）：

    DataUnitLog/LogData:
      P List<Il2CppStringArray> allLog / allVitalLog          ← 原始层，一行 = 编码串切段
      P List<LogItemData>       allLogData / allVitalLogData  ← 解码层，唯一能出人话
      P List<LogItemData>       _allLogData / _allVitalLogData

`UnitSnapshot.LogsArr` 只认 `LogItemData`（读 `month` / `logs` / `subLogs` / `DataToString()`）。
喂它原始层的 `string[]` 时，这三处取值**全部抛异常**，而三处都裹在 `catch { }` 里 →
整条静默退化成 `(无文本)`。真机铁证：`PageLogs 输出={"filter":"all","items":[{"text":"(无文本)"}×3]}`
——连 `month` 字段都整个缺失，只有「赋值抛异常」才会缺字段。

C# 侧的 dynamic 取数没法离线跑（要游戏运行时 + IL2CPP 代理 + 真实存档），所以这里钉住
**源码顺序契约**：解码层必须先于原始层被尝试，且原始层必须走专属解码函数。
这条契约正是本次事故的反面——把顺序倒回去，测试立刻红。
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "csharp" / "UnitSnapshot.cs"


def _method_body(src: str, signature: str) -> str:
    """按大括号配平从签名处截出方法体（够用：这两个方法里没有含花括号的字面量）。"""
    i = src.index(signature)
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
    raise AssertionError(f"方法体未闭合：{signature}")


def test_getloglist_prefers_decoded_layer():
    """解码层（allLogData/allVitalLogData）必须先于原始层（allLog/allVitalLog）被读取。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static object GetLogList")

    # \\b 天然把 allLog 与 allLogData 区分开（D 是词字符，allLog 后无边界）
    dec_v = re.search(r"\.allVitalLogData\b", body)
    dec_r = re.search(r"\.allLogData\b", body)
    raw_v = re.search(r"\.allVitalLog\b", body)
    raw_r = re.search(r"\.allLog\b", body)
    assert dec_v and dec_r, "GetLogList 没有读解码层属性 allLogData/allVitalLogData"
    assert raw_v and raw_r, "GetLogList 没有读原始层 allLog/allVitalLog（兜底路径被删了？）"

    assert dec_v.start() < raw_v.start(), (
        "重要层顺序错了：allVitalLog（原始层 List<string[]>）被排在 allVitalLogData（解码层）之前——"
        "这正是 09-13 『(无文本)』事故的成因"
    )
    assert dec_r.start() < raw_r.start(), (
        "常规层顺序错了：allLog（原始层 List<string[]>）被排在 allLogData（解码层）之前——"
        "这正是 09-13 『(无文本)』事故的成因"
    )
    # 解码层空时必须能落到原始层兜底：返回前要判 Count>0，不能 `if (v != null) return v;`
    assert body.count("Count > 0") >= 4, (
        "GetLogList 每一层都应判 Count>0 才返回；否则空解码层会把兜底路径短路掉"
    )
    print("✓ GetLogList：解码层优先 + 每层 Count>0 才返回（原始层可兜底）")


def test_logsarr_is_shape_aware():
    """LogsArr 必须知道元素是 LogItemData 还是 string[]，原始层走 DecodeRawLogRow。"""
    src = SRC.read_text(encoding="utf-8")
    sig = "private static JArray LogsArr(object list, bool raw = false)"
    assert sig in src, "LogsArr 丢了 raw 形参——元素形状信息传不进去了"
    body = _method_body(src, sig)
    assert "DecodeRawLogRow" in body, "LogsArr 在 raw 分支没有调用 DecodeRawLogRow"
    assert re.search(r"if\s*\(\s*raw\s*\)", body), "LogsArr 没有按 raw 分流"
    print("✓ LogsArr：按 raw 分流，原始层行交给 DecodeRawLogRow")


def test_raw_row_decoded_by_game_itself():
    """原始层一行靠游戏自己的 LogItemData.StringToData 还原，且只认含汉字的结果。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static string DecodeRawLogRow")
    assert "StringToData" in body, "没有用游戏自己的 StringToData 还原编码行"
    assert "new DataUnitLog.LogData.LogItemData()" in body, "没有构造 LogItemData 承接还原结果"
    assert "HasCjk" in body, (
        "缺少中文兜底校验：拼错分隔符时 StringToData 可能『成功』吐出编码残渣，"
        "那东西进模型上下文比 (无文本) 更糟"
    )
    print("✓ DecodeRawLogRow：StringToData 还原 + 汉字校验")


def test_logsarr_emits_newest_first():
    """LogsArr 必须**倒序**输出（新→旧），否则工具第 1 页与 L1「近况」段同时退回陈年旧事。

    游戏库里是升序（旧→新）——真机实证：`regular` 桶里「初入八荒。」（创角）排在
    「与云含进行交谈…」之前，而后者逻辑上必然更晚。两个下游消费者却都按【头=最新】写：
      · `ToolExecutor.PageLogs` 按 `(page-1)*5` 从头切片，`log_page` 默认 1；
      · `RecentTexts` 取 `arr[0..2]`/`arr[0..1]` 喂 L1 上下文的「近况」段。
    所以反转必须发生在 LogsArr（一处），且方向要有自检（OrderInfo 期望打出「降」）。
    """
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static JArray LogsArr")
    assert re.search(r"for\s*\(\s*int\s+i\s*=\s*cnt\s*-\s*1\s*;\s*i\s*>=\s*0\s*;\s*i--\s*\)", body), (
        "LogsArr 没有倒序遍历——库里是旧→新，不倒序则第 1 页=最旧、L1「近况」喂的是开局那几条"
    )
    assert "OrderInfo" in src, "缺少顺序自检：游戏若改了写入方向，只能靠人发现「近况怎么是陈年旧事」"
    d = _method_body(src, "private static string Dir")
    assert "升!" in d and "降" in d, "Dir 没有把异常方向显式标出来"
    print("✓ LogsArr：倒序输出（新→旧）+ 方向自检 OrderInfo/Dir")


def test_layer_diagnostics_present():
    """命中行必须带分层证据（两层条数），否则『两层不等=丢条目』永远看不见。"""
    src = SRC.read_text(encoding="utf-8")
    assert "LayerInfo(ld)" in src and "LayerInfo(ld2)" in src, "主路/回退命中行没有输出分层证据"
    body = _method_body(src, "private static string LayerInfo")
    assert "allLogData" in body and "allLog" in body, "LayerInfo 没有同时读两层"
    assert "两层不等" in body, "LayerInfo 没有把『两层条数不等』显式标出来"
    print("✓ LayerInfo：主路/回退命中行带分层条数，两层不等会打 ★")


if __name__ == "__main__":
    test_getloglist_prefers_decoded_layer()
    test_logsarr_is_shape_aware()
    test_raw_row_decoded_by_game_itself()
    test_logsarr_emits_newest_first()
    test_layer_diagnostics_present()
    print("\n经历日志分层契约：5/5 通过")

# ---------------------------------------------------------------------------
# @ 引用标记 → 中文名（第二起「静默降级」）
#
# 事故：L1 近况里出现 `在溪望镇的坊市中购买了zk8xkN。`，而游戏经历面板同一句显示的是
# 绿色的 `五品培元丹`。`zk8xkN` 是道具的 soleID —— 旧正则
#     Regex.Replace(s, "@[a-zA-Z]_([^|@]*)(?:\|[^@]*)?@", "$1")
# 把整个标记替换成了**字段0**，而真正的道具表 id 在**中间的数字字段**里。
#
# 标记真实格式（真机铁证，Player.log 里游戏自己的剧情文本）：
#     @w_rIsZH3|1|5031101|48|@
# 字段2 `5031101` 在 ItemProps.json 里存在（name=item_name5031101 → LocalText `青须藤`），
# 即「中间的数字字段 = 道具表 id」。ItemProps 全表 id 从 10001 起、无 <1000 的 id，
# 所以 1/48 这类序号字段查不到行 —— 「逐字段试解」不会误命中。
# ---------------------------------------------------------------------------

def test_at_tag_resolves_to_chinese_name():
    src = SRC.read_text(encoding="utf-8")
    # 不能再用 "$1"（只取字段0 = soleID）那种替换
    assert '"@[a-zA-Z]_([^|@]*)(?:\\\\|[^@]*)?@"' not in src, \
        "旧的 $1 替换又回来了：它只会输出 soleID（zk8xkN），不是道具中文名"
    assert re.search(r'Regex\.Replace\(s,\s*"@\(\[a-zA-Z\]\)_\(\[\^@\]\*\)@"\s*,\s*RenderAtTag\)', src), \
        "必须用 MatchEvaluator（RenderAtTag）逐字段解析标记"
    body = _method_body(src, "private static string RenderAtTag")
    assert "Split('|')" in body, "必须按 | 切字段（道具 id 在中间字段里，不是字段0）"
    assert "PropsName(id)" in body, "数字字段必须拿去查道具表"
    assert "AbilityName(id)" in body, "长形态 @w_ 的 id 落在 BattleAbilityBase（战斗能力表），必须也试"
    assert re.search(r"return\s+field0", body), "未知类型必须退回字段0（= 旧行为，不许更差）"
    print("✓ @ 标记：按 | 切字段 → 道具表/战斗能力表 → 中文名；未知类型退回字段0")


def test_at_tag_type_letter_decides_meaning():
    """★类型字母决定字段含义★ —— 实测 221/221 的 `@q_` 字段0 **就是显示名**（不是 ID）。

    真机全量统计（两个会话的 Player.log）：
      · `@q_` 221 次，**全部 2 字段**，形如 `@q_寇炫明(好友)|cPoLMG@` —— 字段0 = 人名(关系)，字段1 = unitID
      · `@w_` 14 次：12 次 5 字段（`@w_zcW8xn|1|1011111|13|@`，字段2=propsID → 六品培元丹）
        + 2 次 19 字段（`@w_gdZZA1|2|0|0|4|88003|…|@`，无 propsID，`88003` 命中 BattleAbilityBase → 大法）
    旧实现一律按"字段0 是 ID"处理，于是把 `q` 的**正确结果**打成"未解"（198/221 是误报），
    又把 `w` 长形态的裸 soleID 漏给模型。
    """
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static string RenderAtTag")
    assert re.search(r'letter\s*==\s*"q"', body), "必须按类型字母分流：q=人物引用（字段0 即显示名）"
    assert re.search(r'letter\s*==\s*"w"', body), "w=物品/能力引用：解不出时不许回裸 soleID"
    assert "（未知道具）" in body, "查不到名字时要给明确占位，而不是把 gdZZA1 这种串丢给模型"
    ab = _method_body(src, "private static string AbilityName")
    assert "g.conf.battleAbilityBase.GetItem(abilityId)" in ab, \
        "必须查 ConfBattleAbilityBase（ConfMgr.battleAbilityBase，反编实证 GetItem(Int32)）"
    assert "GameTool.LS(" in ab, "name 是本地化 key（ability_name_last8 → 大法），不过 LS 拿不到中文"
    print("✓ @ 标记按类型分流：q=人物引用 / w=道具·能力，未知给占位不留裸 ID")


def test_propsid_lookup_uses_conf_and_localization():
    """propsID → 中文名的链路：g.conf.itemProps.GetItem(id).name（本地化 key）→ GameTool.LS。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static string PropsName")
    assert "g.conf.itemProps.GetItem(propsId)" in body, "必须查 ConfItemProps（ConfMgr.itemProps，反编实证）"
    assert "GameTool.LS(" in body, "name 是本地化 key（item_name5031101），不过 LS 拿不到中文"
    print("✓ PropsName：ConfItemProps.GetItem → name → GameTool.LS")


def test_ampersand_junk_is_dropped():
    """`0&A&A` 这类 DataToString 编码残渣必须丢（游戏面板也不显示）。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static bool KeepLogPart")
    assert "HasCjk(s)" in body, "判据必须依赖「有没有中日韩字符」"
    assert "IndexOf('&')" in body, "只对有 & 的串做判定（纯数字/符号的 subLog 要保留）"
    assert "KeepLogPart(t)" in src, "LogItemText 没有接上过滤"
    print("✓ KeepLogPart：& 编码残渣（无 CJK）丢弃，正常文本与纯数字保留")


def test_recent_texts_merges_and_takes_newest():
    """近况取数：不再按桶配额（真机 vital 恒 0 → 永远只剩 2 条），改为合并后取最近 N 条。"""
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "public static JArray RecentTexts")
    assert "int[] limits" not in body, "按桶配额已废（vital 恒空 → 退化成恒 2 条）"
    assert "RecentCap" in body, "必须用命名常量，别把条数散在代码里"
    assert re.search(r"merged\.Sort\(\(a,\s*b\)\s*=>\s*\(\(int\)b\[\"m\"\]\)\.CompareTo", body), \
        "必须按账面月**降序**排（新→旧），否则近况又变陈年旧事"
    assert "seen.Add(" in body, "两桶可能各存一条同月同文，必须去重"
    cap = re.search(r"private const int RecentCap = (\d+);", src)
    assert cap and int(cap.group(1)) >= 3, "RecentCap 常量缺失或过小"
    print(f"✓ RecentTexts：合并两桶 → 月降序 → 去重 → 取最近 {cap.group(1)} 条")


def test_logitemtext_concatenates_sentence_fragments():
    """`logs[]` 是**同一句的片段**（原文自带标点）→ 必须直连，不能插分隔符。

    真机铁证：游戏经历面板显示 `基于自我成长的需求，认为当前的丹药不足够，想获得更多的丹药。在远望镇的坊市中购买了五品培元丹。`
    —— 片段之间**没有任何分隔符**；而旧实现 `string.Join("；", parts)` 会插出
    `基于自我成长的需求，；认为…` 这种顿挫，与面板逐字不一致。
    `subLogs[]` 是**附加明细**（不是主句的一部分），跟在主句后用 `；`。
    """
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static string LogItemText")
    assert "string.Concat(main)" in body, "句内片段必须直连（原文自带标点，游戏面板就是这样）"
    assert re.search(r'parts', body) is None, "旧的单一 parts 列表（统一 ； 连接）已被拆成 main/sub"
    assert 'string.Join("；", sub)' in body, "subLogs 是附加明细，用「；」跟在主句后"
    print("✓ LogItemText：logs 直连（= 游戏面板），subLogs 用「；」跟在后面")


def test_recent_segment_has_no_duplicate_label():
    """段标签已是「近况」，正文不许再写一遍「近况经历：」。"""
    sp = (SRC.parent.parent / "system_prompt.py").read_text(encoding="utf-8")
    i = sp.index("def format_l1_context")
    body = sp[i: sp.index("def render_context_segment")]
    # 只看代码行（注释里正当地提到"去掉「近况经历：」前缀"不算违规）
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    assert '"近况经历："' not in code, "正文前缀「近况经历：」与段标签「近况」重复，是纯噪音"
    assert re.search(r'recent_text = "；"\.join\(', code), "近况成文应直接是编号列表"
    print("✓ 近况段：渲染为 `Current runtime context —— 近况：1. …`，无重复标签")


# ---------------------------------------------------------------------------
# 兜底清理（用户拍板"链路确定了就别再兜底"）
# ---------------------------------------------------------------------------

def test_logitemtext_has_no_datatostring_fallback():
    """`DataToString()` 是**序列化格式**（`StringToData` 的逆），不能拿来冒充正文。

    它唯一产出过的东西就是 L1 近况里的 `0&A&A`。`GetLogString()` 已是实证主路
    （9/9 `命中主路`），片段全空时应当**如实返回空串**，而不是拿编码残渣填。
    """
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static string LogItemText")
    assert "DataToString()" not in body, \
        "DataToString 兜底又回来了 —— 它是 0&A&A 的来源，序列化格式不是人话"
    assert "GetLogString" in src, "主路必须是 GetLogString（TryDataString 里）"
    print("✓ LogItemText：只走 GetLogString，不再用 DataToString 填残渣")


def test_setattr_is_dynint_only():
    """属性只认面板口径的动态层 `DynInt`；裸字段兜底已删（它会给出与面板不一致的数）。

    这是 09-13「境界/声望取错」那条 bug 的**同一形态**：面板读 DynInt（含加成 + 上下限钳制），
    裸字段是未加成值。降级成"兜底"只是把同一个错藏得更深 —— 一旦命中，模型看到的属性
    与面板不一致且**没有任何信号**。
    """
    src = SRC.read_text(encoding="utf-8")
    body = _method_body(src, "private static void SetAttr")
    assert "DynOf(dyn, prop)" in body, "必须走动态层 DynOf"
    assert "PdOf(" not in body, "裸字段兜底又回来了（值与面板不一致且无信号）"
    assert "_dynMiss" in body, "缺失必须留痕（★DynInt 缺失 日志），否则退化成静默少字段"
    print("✓ SetAttr：只认 DynInt，缺失即不落键 + ★DynInt 缺失 日志")
