# -*- coding: utf-8 -*-
"""气泡**富文本 + 行距**（10-31 用户需求）的跨语言契约。

## 需求原话
「文字格式不好，主要是没有行间距，当文字很多的时候读起来密密麻麻」＋
「希望可以使用富文本，即模型输出富文本，然后去进行渲染使得文本更加多样（像是不同颜色之类的）」。

## 为什么这个改动**必须**两侧同时成立

它天生是"改一半也看不出坏"的形状，而且坏法都很安静：

| 只做了一半 | 屏幕上的样子 | 有没有报错 |
|---|---|---|
| C# 清白了、提示词没教 | 永远无色 —— 模型不知道能写标签 | 无 |
| 提示词教了、C# 没白名单 | 模型写错一个字，满屏 `<color=红>` 原文 | 无 |
| C# 白名单、但没配平 | 流式期半句话染色闪一下；孤立 `</color>` 让后面整段错色 | 无 |
| 白色 `#FFFFFF` 照原样渲染 | 浅灰/浅绿气泡底上**完全看不见** | 无 |
| 预制件改了行距、C# 兜底没改 | "重打 AB 之前 / 之后观感不一样" | 无 |

所以这里钉的是**语义与一致性**，不是"有没有改过"：

  ① 四个写泡点全部走 `Render`（漏一个 = 某条路露裸标签）；
  ② C# 调色板的名字与值与 `prompts/sections/world_rich_text.txt` 教模型的**逐项相等**
     （模型写对名字而 C# 不认 = 静默无色）；
  ③ `BubbleTextCore.BubbleLineSpacing` 与 prefab 补丁脚本的 `LINE_SPACING` **逐值相等**
     （两条腿说的是同一件事）；
  ④ 运行期兜底真的挂在 `Init` 上，且方向是"开富文本 + 设行距"。

配对：
  · 规则本身的判定表在 `csharp/tests/BubbleTextTests/Program.cs`（122 项断言，含流式半截标签）；
  · 版式（padding/对齐）在 `tests/test_chat_bubble_layout_contract.py`（另一半，互不依赖）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CORE = ROOT / "csharp" / "UI" / "BubbleTextCore.cs"
WIN = ROOT / "csharp" / "UI" / "ChatWindow.cs"
ITEMS = ROOT / "csharp" / "UI" / "ChatItemViews.cs"
BANNER = ROOT / "csharp" / "UI" / "UnreadBanner.cs"
PROMPT = ROOT / "prompts" / "sections" / "world_rich_text.txt"
SECTION_MAP = ROOT / "agent" / "system_prompt.py"
PATCH = ROOT / "scripts" / "dev" / "prefab_patch_chat_bubble_style.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


# ----------------------------------------------------------------------
# ① 核：存在、纯逻辑、四个写泡点全走它
# ----------------------------------------------------------------------

def test_core_exposes_render_and_stays_unity_free():
    src = _read(CORE)
    assert "public static string Render(string s)" in src, "找不到 Render 入口"
    assert "public static string StripTags(string s)" in src, "找不到 StripTags 入口"
    assert "public const float BubbleLineSpacing" in src, "找不到行距常量"
    # 纯逻辑是它能被 net8.0 单测的前提；混进 UnityEngine 就编不进测试工程
    assert "UnityEngine" not in src, "BubbleTextCore 必须零 Unity 依赖"


def test_render_is_tighten_plus_rich():
    """`Render` 必须是「先折空行、再洗富文本」—— 顺序反了会把 `<color=…>` 里的
    换行/空白当成正文行结构去折（标签内部是不允许出现换行的）。"""
    src = _strip_comments(_read(CORE))
    i = src.index("public static string Render(string s)")
    body = src[i: src.index("}", i)]
    assert "Rich(Tighten(s))" in body, "Render 必须是 Rich(Tighten(s))（先折空行再洗标签）"


def test_named_palette_and_hex_both_resolve():
    """★ 模型会给**名字**（`<color=红>`），也会给 hex。两条路都要通。
    名字那条尤其重要：提示词教的是名字，认不出来就是"说好的颜色呢"。"""
    src = _read(CORE)
    i = src.index("private static Dictionary<string, string> BuildPalette()")
    body = src[i: src.index("\n        }", i)]
    # 7 色（无「橙」——理由见 test_orange_is_intentionally_absent）
    for name in ("红", "黄", "绿", "青", "蓝", "紫", "灰"):
        assert '"{ %s"' % name in body or '"%s"' % name in body, \
            "调色板缺中文颜色名 %s" % name
    # ResolveColor 必须同时接受名字与 #hex
    j = _read(CORE).index("private static string ResolveColor(string arg)")
    rc = _read(CORE)[j: j + 900]
    assert "Palette.TryGetValue" in rc, "ResolveColor 不认名字"
    assert "lower[0] != '#'" in rc, "ResolveColor 不认 #hex"


def _method(src: str, signature: str) -> str:
    src = _strip_comments(src)
    assert src.count(signature) == 1, "签名不唯一（%d 次）：%s" % (src.count(signature), signature)
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j: k + 1]
    raise AssertionError("大括号不配对：" + signature)


def test_all_four_bubble_write_sites_render():
    """四个写泡点（流式刷新 / 终稿覆盖 / 行覆盖 / 新建气泡）。
    漏一个 = "同一段话，露不露裸标签取决于走哪条判重分支"——而那四条路**肉眼分不出来**。"""
    # ① 流式刷新
    lu = _method(_read(WIN), "private void LateUpdate()")
    assert "BubbleTextCore.Render(_delta.ToString())" in lu, "流式刷新没走 Render"
    assert "BubbleTextCore.Tighten(_delta" not in lu, "流式刷新还留着只折空行的老调用"

    # ② 终稿覆盖活跃流式泡
    fin = _method(_read(WIN), "public void FinishNpcBubble(string fullText, bool initiative = false,"
                              " string speaker = null, string speakerId = null)")
    assert "BubbleTextCore.Render(fullText)" in fin, "终稿覆盖没走 Render"

    # ③ 行覆盖（OverwriteLastRow 走 SetRowText，不经过 ChatBubble.SetText）
    row = _method(_read(WIN), "private bool SetRowText(GameObject row, string text)")
    assert "BubbleTextCore.Render(text)" in row, "SetRowText 没走 Render"

    # ④ 新建气泡
    st = _method(_read(ITEMS), "public void SetText(string text)")
    assert "BubbleTextCore.Render(text)" in st, "ChatBubble.SetText 没走 Render"

    # 全仓不许再有人直连 Tighten 写屏（那是"只折空行、不洗标签"的老路）
    for p in (WIN, ITEMS):
        src = _strip_comments(_read(p))
        bad = [m for m in re.findall(r"BubbleTextCore\.Tighten\([^)]*\)", src)]
        assert not bad, "%s 里还有直连 Tighten 的写屏点：%s" % (p.name, bad)


# ----------------------------------------------------------------------
# ② 提示词与 C# 调色板**逐项相等**（模型写对名字而 C# 不认 = 静默无色）
# ----------------------------------------------------------------------

def _cs_palette() -> dict:
    """从 C# 解析出 {中文名: #RRGGBB}（只取中文键，英文是同义别名）。"""
    src = _read(CORE)
    i = src.index("BuildPalette()")
    body = src[i: src.index("return d;", i)]
    out = {}
    for name, hexv in re.findall(r'\{\s*"([^"]+)"\s*,\s*"(#[0-9A-Fa-f]{6})"\s*\}', body):
        if re.search(r"[\u4e00-\u9fff]", name):
            out[name] = hexv.upper()
    return out


def test_prompt_teaches_exactly_the_cs_palette_names():
    """提示词里列的颜色名集合 == C# 调色板的中文键集合。**双向**卡：
    少一个（提示词教了 C# 不认）→ 静默无色；多一个（C# 有提示词没提）→ 死代码。"""
    pal = _cs_palette()
    assert pal, "没从 C# 解析出调色板（格式变了？）"
    prompt = _read(PROMPT)
    # 提示词里那一行：`颜色名只有这几个：红、橙、黄、绿、青、蓝、紫、灰（...）`
    m = re.search(r"颜色名只有这几个：([^（(\n]+)", prompt)
    assert m, "提示词里找不到「颜色名只有这几个：」那一行"
    taught = {x.strip() for x in re.split(r"[、,，/]", m.group(1)) if x.strip()}
    assert taught == set(pal), (
        "提示词教的颜色名与 C# 调色板不一致\n  提示词=%s\n  C#=%s\n"
        "  提示词有而 C# 没有 → 模型写对了却不显示颜色（静默无色）" % (sorted(taught), sorted(pal)))


def test_prompt_lists_the_whitelisted_tags_only():
    """提示词只许教白名单里的标签 —— 现在是**只有 `color`**。

    ★ `b` / `i` 是 10-31 删掉的（实测依据）★
    8 个场景跑真实模型：`<color>` 用了 6 次，`<b>` **0 次**，`<i>` **0 次**；
    再写一句"每次回复都必须用一次 `<b>`"，模型照样一次都不用。
    技术上是能用的（uGUI 的伪粗体实测墨迹 +77~80%），但**模型不用** ——
    教它就是在提示词里塞两条永不触发的语法噪音，还会占掉模型对 `<color>` 的注意力。
    C# 白名单同步删掉，两边一致（否则"说不认却会变粗"是另一种不一致）。

    ★ `size` 刻意**不教** —— 它虽然被 C# 放行（8..30），但字号在气泡里最容易撑破版式；
    教了模型就会写，写了就有人用 28px 把整段说完。宁可留成"只有我们知道能开"。"""
    prompt = _read(PROMPT)
    for tag in ("<color=", "</color>"):
        assert tag in prompt, "提示词没教 %s（模型不会用）" % tag
    for gone in ("<b>", "</b>", "<i>", "</i>"):
        assert gone not in prompt, (
            "提示词还在教 %s —— 实测模型 8/8 场景不用、强制要求也不用，"
            "留着只是语法噪音（见 docs §E.4.2）" % gone)
    assert "<size=" not in prompt, "提示词不该教 <size>（能被用来撑破气泡版式）"
    # 明确写出"别的标签不认"，否则模型会顺手用 <g>/<y>（游戏调色板标签）
    #
    # ★ `<size>` 刻意**不列进这个名单**（10-31 修正）：C# 的白名单其实是**放行** size(8..30) 的，
    #   把它写成"不认"就是在提示词里说假话 —— 模型真写了，屏幕上会有反应，与说明不符。
    #   正确的做法是"不教、也不说不认"：模型不知道能写就不会写，写了也不至于撑破版式
    #   （有 8..30 的夹取），而说明本身保持诚实。
    # 明确写出"别的标签不认"（10-31 改成中文列举：加粗/斜体/字号/图标）
    assert re.search(r"(加粗|斜体|字号|图标)", prompt), \
        "提示词没列出不认的标签种类（模型会照游戏内文本的写法用 <g>/<y>/<size>）"
    # 但**必须**说清"会被删掉只剩字"，而不是含糊的"不支持"
    assert "只剩" in prompt or "删掉" in prompt, \
        "提示词没说清不认的标签会怎样（模型不知道怎么兜底）"


def test_section_is_registered_in_the_section_map():
    """没登记 = 文件放了也不进 system（`prompt_files` 只允许改**已有**的 section 文件，
    新建无意义）。这条把"加了文件忘了登记"钉住。"""
    src = _read(SECTION_MAP)
    assert '"world_rich_text.txt"' in src, "world_rich_text.txt 没登记进 _SECTION_MAP"
    m = re.search(r'"world_rich_text\.txt":\s*\("world:rich_text",\s*(-?\d+)\)', src)
    assert m, "_SECTION_MAP 里 world_rich_text 的条目形状不对"
    order = int(m.group(1))
    # ★ 必须在 deployment:persona(0) **之前**：那段每个 NPC 都不同，
    #   稳定段落在它后面 ⇒ 每换一个 NPC 都要重算整段前缀，provider 的前缀缓存全废。
    #   第一版写成 15，实测确实排到了人设后面（`assemble(scope)` 里能直接看到）。
    assert order < 0, (
        "world_rich_text 的 order 必须 < 0（排在 deployment:persona(0) 之前）；"
        "现在是 %d —— 它会被排到每个 NPC 不同的人设后面，废掉前缀缓存" % order
    )
    assert order > -80, "排版说明要排在 world_persona_rules(-80) 之后"


# ----------------------------------------------------------------------
# ③ 行距：两条腿同一个数
# ----------------------------------------------------------------------

def test_line_spacing_matches_the_prefab_patch_script():
    """C# 运行期兜底的常量 vs prefab 补丁脚本的目标值 —— 逐值相等。
    不一致就会出现"重打 AB 之前 / 之后行距不一样"，是这类"两条腿"改动最典型的坑。"""
    cs = re.search(r"public const float BubbleLineSpacing = ([\d.]+)f;", _read(CORE))
    assert cs, "找不到 BubbleLineSpacing 常量"
    sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    import prefab_patch_chat_bubble_style as M
    assert float(cs.group(1)) == float(M.LINE_SPACING), (
        "行距两条腿不一致：C#=%s prefab=%s" % (cs.group(1), M.LINE_SPACING))
    # 1 = 紧贴（正是被投诉的那种），必须真的改过
    assert float(M.LINE_SPACING) > 1.0, "行距还是 1（= 两行墨迹紧贴，等于没改）"
    assert float(M.LINE_SPACING) <= 1.6, "行距过大（>1.6）会让气泡高一倍，滚动变累"


def test_prefab_patch_writes_and_verifies_line_spacing():
    """补丁脚本必须**真写** `m_LineSpacing`，且 verify 里**独立复核**（不是只信 patch）。
    ★ `m_LineSpacing` 是 float，用 `_set_nested` 的 `-?\\d+` 正则匹配不到 —— 会静默 0 命中。"""
    src = _read(PATCH)
    assert '_set_font_field(pf, txt, "m_LineSpacing", LINE_SPACING)' in src, \
        "补丁没写 m_LineSpacing"
    assert "read_line_spacing(" in src, "补丁没有独立读回 m_LineSpacing"
    assert "_RE_FLOAT" in src, "缺 float 字段的正则（m_LineSpacing 不是 int）"
    assert "m_RichText" in src, "补丁没管 m_RichText"


# ----------------------------------------------------------------------
# ④ C# 运行期兜底：真的挂上、方向正确
# ----------------------------------------------------------------------

def test_runtime_leg_sets_line_spacing_and_rich_text():
    src = _read(WIN)
    fb = _method(src, "private static bool FixBubbleLayout(GameObject rowTemplate,"
                       " string bubbleName, bool tailOnLeft)")
    assert "text.lineSpacing = BubbleTextCore.BubbleLineSpacing" in fb, \
        "运行期兜底没设行距（老 AB 就还是密密麻麻）"
    assert "text.supportRichText = true" in fb, \
        "运行期兜底没开富文本（<color> 会原样上屏）"
    # 两个模板都兜（漏一个 = 那一侧还是老样子）
    ap = _method(src, "private void ApplyBubbleTextLayoutFloor()")
    for name in ("NpcBubble", "UserBubble"):
        assert '"%s"' % name in ap, "兜底没覆盖 %s" % name
    # 必须挂在 Init 上
    init = _method(src, "public void Init(ChatWindowRefs refs)")
    assert "ApplyBubbleTextLayoutFloor();" in init, "兜底没在 Init 里调用 = 老 AB 不会自动修好"


def test_action_rows_and_summary_share_the_line_spacing():
    """工具结果 / 行动记录 / 内心思量 / 压缩纪要这些**长文本块**也要行距。
    它们不是气泡（走 `AttachAutoText` / 自建 `Text`），漏掉就是"气泡好读了、行动记录还是一坨"。"""
    src = _read(ITEMS)
    n = src.count("lineSpacing = BubbleTextCore.BubbleLineSpacing")
    assert n >= 3, "长文本块的行距只设了 %d 处（应为 气泡行/思量/纪要 至少 3 处）" % n


def test_unread_banner_strips_tags_before_truncating():
    """横幅是单行小字，放不下颜色。必须**先剥标签再截断** —— 顺序反了会切出 `<col…`，
    屏幕上就是一串尖括号（Python 侧同一个坑，见 `test_rich_text_tag_contract.py`）。"""
    src = _read(BANNER)
    i = src.index("private static string Preview(string npc, string raw, int count)")
    body = src[i: src.index("\n        }", i)]
    assert "BubbleTextCore.StripTags(" in body, "横幅预览没剥标签（会露 <color=…>）"
    # 剥必须发生在截断之前
    assert body.index("StripTags(") < body.index("Substring(0, budget)"), \
        "横幅先截断再剥标签 —— 会切出半截标签"


# ----------------------------------------------------------------------
# ⑤ 亮度：浅色气泡底上的可读性（这是"富文本看起来什么都没变"的头号原因）
# ----------------------------------------------------------------------

def test_luminance_cap_exists_and_is_conservative():
    src = _read(CORE)
    assert "LumCap" in src, "没有亮度上限：模型写 #FFFFFF 在浅色气泡上等于隐形"
    m = re.search(r"private const double LumCap = ([\d.]+);", src)
    assert m, "LumCap 不是常量（改不了、测不了）"
    cap = float(m.group(1))
    assert 0.35 <= cap <= 0.60, "亮度上限 %s 不在合理区间（3.5~6.0 成）" % cap
    # 两种气泡底的感知亮度（NPC #E9E9E9 / 玩家 #9BE375）都远高于上限
    for name, hexv in (("NPC", "#E9E9E9"), ("玩家", "#9BE375")):
        r, g, b = int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        assert lum > cap + 0.15, "%s 气泡底的亮度 %0.2f 与上限 %s 差得不够（对比不足）" % (name, lum, cap)


def test_palette_values_are_all_under_the_cap_themselves():
    """调色板里的**每个**值都必须自己就够暗 —— 否则模型一写「黄」就被压成灰，
    玩家看到的是"颜色不对"而不是"颜色被保护了"。"""
    pal = _cs_palette()
    src = _read(CORE)
    cap = float(re.search(r"private const double LumCap = ([\d.]+);", src).group(1))
    for name, hexv in pal.items():
        r, g, b = int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        assert lum <= cap + 0.02, "调色板「%s」=%s 亮度 %0.2f 超过上限 %s（会被压暗，颜色就走样了）" % (
            name, hexv, lum, cap)


# ----------------------------------------------------------------------
# ⑧ 对比度：颜色必须**在两种气泡底上都看得见**
# ----------------------------------------------------------------------

#: 真实的两种气泡底色（从 AB 工程的 PNG 上量的，见 test_chat_bubble_layout_contract）。
#: ★ 玩家气泡是**浅绿**不是白 —— 第一版调色板只按灰底挑，绿底上对比度掉到 2.97。
BUBBLE_BG = {
    "NPC(#E9E9E9)": (0xE9, 0xE9, 0xE9),
    "玩家(#9BE375)": (0x9B, 0xE3, 0x75),
}

#: WCAG 2.1 对正文的要求。1.4.3 是 4.5；这里用 4.5 而不是 3.0（大字标准），
#: 因为气泡正文 19px 在游戏里会被缩放到更小，且玩家常在移动中扫读。
MIN_CONTRAST = 4.5


def _rel_lum(rgb):
    def f(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = _rel_lum(a), _rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_every_palette_color_is_readable_on_both_bubble_backgrounds():
    """★ 这是"富文本做完了但玩家看不清"的唯一护栏。

    第一版按灰底挑了 8 个明快的色：在 NPC 气泡上 4.05~6.11（勉强），
    但**玩家气泡是浅绿 `#9BE375`**，橙 `#C05621` 在上面只有 **2.97** ——
    绿底橙字，等于没标。而这件事**完全不报错**，只有真机才看得出来。
    """
    pal = _cs_palette()
    bad = []
    for name, hexv in pal.items():
        rgb = (int(hexv[1:3], 16), int(hexv[3:5], 16), int(hexv[5:7], 16))
        for bg_name, bg in BUBBLE_BG.items():
            c = _contrast(rgb, bg)
            if c < MIN_CONTRAST:
                bad.append("「%s」=%s 在 %s 上对比度只有 %.2f（要求 ≥%.1f）"
                           % (name, hexv, bg_name, c, MIN_CONTRAST))
    assert not bad, "调色板有颜色在气泡底上看不清：\n  " + "\n  ".join(bad)


def test_palette_colors_are_distinguishable_from_each_other():
    """色与色之间也要分得出来 —— 玩家看到的不是"一个是 #B2121D 一个是 #705600"，
    而是"这两个是不是同一个色"。深色区里**红/橙/黄挤不下三个**（距离 45~50），
    所以调色板只留 7 个；这条防止有人又把"橙"加回来。"""
    pal = _cs_palette()
    def rgb(h):
        return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))
    names = sorted(pal)
    too_close = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            d = sum((a - b) ** 2 for a, b in zip(rgb(pal[names[i]]), rgb(pal[names[j]]))) ** 0.5
            if d < 55:
                too_close.append("%s vs %s 距离 %.0f" % (names[i], names[j], d))
    assert not too_close, "调色板里有分不出来的颜色对：\n  " + "\n  ".join(too_close)


def test_orange_is_intentionally_absent():
    """「橙」被**刻意**去掉：深色区物理上塞不下红/橙/黄三个可区分的暖色。

    模型写「橙」会静默降级成白字（不报错）。这是已知取舍，不是漏了 ——
    提示词也只教 7 个，两边一致。这条把"有人好心把橙加回来"挡住。
    """
    pal = _cs_palette()
    assert "橙" not in pal, (
        "「橙」又被加回调色板了 —— 它与红/黄在深色区距离只有 45~50，玩家分不出。"
        "要加请先确认三种气泡底上对比度都 ≥4.5 且与现有 7 色距离都 ≥55。"
    )
    assert len(pal) == 7, "调色板应为 7 色（红黄绿青蓝紫灰），实际 %d 色" % len(pal)

# ----------------------------------------------------------------------
# ⑥ 两条"少写一行就静默坏掉"的判据（10-31 端到端实测各抓到一个真 bug）
# ----------------------------------------------------------------------

def test_partial_tag_detection_knows_cjk_color_names():
    """★ 半截标签判据必须认**中文色名**。

    第一版只按"出现中文/标点就当正文"判断，而本项目提示词教的就是 `<color=红>` 这种写法
    ⇒ 流式到 `=红` 那一帧时 `见过<color=红` **原样画上屏**，屏幕上闪一串尖括号。
    这不是"边界情况"：**每一句带颜色的台词流式时都会经过这一帧**。

    修法 = 半截判据除"字符集"之外，还要求**以某个白名单标签名开头、或是它的前缀**。
    """
    src = _read(CORE)
    i = src.index("private static bool IsPartialTagTail(string s, int open)")
    body = src[i: src.index("\n        }", i)]
    assert "StartsLikeTagName(" in body, (
        "半截标签判据丢了「按标签名前缀认」这一步 ⇒ 中文色名的半截形态（`<color=红`）"
        "会原样上屏。这是实测抓到的 bug，别简化回去。"
    )
    # 字符集必须含 CJK（颜色名是中文）
    j = src.index("private static bool IsTagChar(char c)")
    tagchar = src[j: src.index("\n        }", j)]
    assert "\\u4e00" in tagchar and "\\u9fff" in tagchar, (
        "IsTagChar 不许把中文排除 —— 颜色名就是中文，排除它半截标签就认不出来了"
    )
    # 前缀判据必须覆盖全部白名单标签名
    k = src.index("private static bool StartsLikeTagName(string tail)")
    names = src[k: src.index("\n        }", k)]
    # 与 Canonical 的白名单一致（b/i 已删，见 test_csharp_whitelist_has_no_bold_or_italic）
    for nm in ('"color"', '"size"'):
        assert nm in names, "StartsLikeTagName 漏了 %s" % nm


def test_bare_angle_brackets_survive_in_prose():
    """★ 正文里的裸尖括号不许被当成标签吃掉。

    "形状像 < 这个，也像 > 那个" —— 只按"有没有 `>`"判断会把它当合法标签 ⇒
    白名单外 ⇒ **连里面的字一起吃掉**，玩家看到的句子缺一块。
    真实 Unity 标签全部**字母开头**，所以 `FindTagEnd` 必须加这一条否决。
    """
    src = _read(CORE)
    i = src.index("private static int FindTagEnd(string s, int open)")
    body = src[i: src.index("\n        }", i)]
    assert "LooksLikeTagBody(" in body, (
        "FindTagEnd 少了「标签体必须字母开头」这条否决 ⇒ 正文里的 `< … >` 会被当标签吃掉"
    )
    j = src.index("private static bool LooksLikeTagBody(")
    lb = src[j: src.index("\n        }", j)]
    assert "'/'" in lb, "LooksLikeTagBody 必须跳过前导 `/`（闭合标签也一样要认）"
    assert ">=' a'" in lb or ">='a'" in lb or "c >= 'a'" in lb, "LooksLikeTagBody 没检查字母开头"

def test_prompt_actually_encourages_using_tags():
    """★ 提示词不许**压制**用标签。

    第一版写的是「默认就是普通浅色字，**不需要为了好看而加格式**」—— 那句话在
    system 里比"可以用这些标签"更响，模型的合理反应就是**一个字都不标**，
    于是功能全链路正确、屏幕上却一点颜色都没有。这正是本轮需求的反面。

    约束三条：
      ① 不许出现"不需要…加格式"这类否定措辞（那是在劝模型别用）；
      ② 必须给出**具体该标什么**的例子（"挑一两个真正要重的词"），
         只列语法不给场景，模型不会自己找地方用；
      ③ 必须说清"整段上色等于没重点"（否则另一头会全篇染色，同样难看）。
    """
    prompt = _read(PROMPT)
    for bad in ("不需要为了好看", "不必加格式", "不要加格式", "无需加格式", "保持纯文本"):
        assert bad not in prompt, (
            "提示词里有压制措辞「%s」—— 模型会照它做，结果是全篇无色，"
            "功能做了等于没做" % bad)
    # 钉**语义**不钉字面（措辞会随实测反复调，钉字面就变成每改一次都要改测试）：
    #   ① 必须有"挑哪处"这类**选择指引**；
    #   ② 必须给出**具体场景/示例**（带 `<color=` 的例句，且句里有实际内容）。
    assert re.search(r"挑", prompt), (
        "提示词没给「该挑哪处」的指引 —— 只说'可以用标签'，模型不会自己找地方用"
    )
    examples = re.findall(r"`[^`]*<color=[^`]+`", prompt)
    # 至少一条例句含实际台词（不是只有语法骨架 `…`）
    concrete = [e for e in examples if "…" not in e and len(e) > 20]
    assert concrete, (
        "提示词里没有一条**带实际内容**的例句（只有 `…` 语法骨架）。"
        "实测：给场景清单能到 3/6，给「正常说话就挑一处」这类正面要求能到 5/6 —— "
        "但只列语法时模型几乎不标。"
    )
    assert re.search(r"一(到两|两)处", prompt) or "一处" in prompt, (
        "提示词没说清「标几处」（不说就会全篇染色，同样难看）"
    )
    assert "整段上色" in prompt or "全篇" in prompt, (
        "提示词没说「整段上色等于没重点」—— 另一头会变成全篇染色，同样难看"
    )

# ----------------------------------------------------------------------
# ⑦ 真实模型命中率（记录实测数据，防止后续改动把效果改回"几乎不标"）
# ----------------------------------------------------------------------

def test_prompt_has_the_measured_effective_shape():
    """这份提示词的形状来自**真实模型 A/B 实测**（10-31，本地网关 + 真 system + 6 个场景）：

    | 版本 | 命中 |
    |---|---|
    | 只列语法（"可以用这些标签"） | 0~1/6 |
    | 场景清单（威胁标红、数目标黄…） | 3/6 |
    | 强制"每条回复都要标" | 5/6，但**模型开始写崩**（`<>`、`</>`、`<color:黄>`） |
    | ★ 定稿：正面要求 + 挑哪处 + 允许白字 | 3/6，零畸形 |

    定稿刻意**没有**选 5/6 那一版：多出来的命中落在"闲聊/应答"上（给「今天天气不错」标个颜色），
    那不是玩家要的"更多样"，是噪音；而代价是模型被逼出畸形标签。
    规律很清楚：**有冲突或有硬信息的场景会标，纯寒暄和短应答不标** —— 正是想要的形状。

    这条钉住三件事（改提示词时别退回去）：
      ① 有正面要求（"正常说话时挑一处"），不是"不需要加格式"；
      ② 有"挑哪处"的具体指引与带内容的例句；
      ③ **保留白字出口**（"只是应答一声…白字就行"）—— 没有它，模型要么不标、要么乱标。
    """
    prompt = _read(PROMPT)
    assert "正常说话时" in prompt or "挑一处" in prompt, "① 缺正面要求"
    # ★ 两者**都**要在（第一版写成 `or`，去掉"最该让对方记住"那半句仍通过 = 假绿）：
    #   "挑哪处"是小标题，"最该让对方记住"才是**判据**。只留标题等于没说。
    assert "挑哪处" in prompt, "② 缺「挑哪处」小标题"
    assert "最该让对方记住" in prompt, (
        "② 缺「挑哪处」的**判据**（只给标题不给依据，模型还是不知道挑什么）"
    )
    assert "白字就行" in prompt or "不必硬凑" in prompt, (
        "③ 缺白字出口 —— 实测加「每条都必须标」会涨到 5/6，但模型会写出 `<>`/`<color:黄>` 这类"
        "畸形标签，且把颜色标到闲聊上（噪音）。定稿刻意不选那一版。"
    )
    # 畸形写法的提醒必须还在（模型写过：漏等号、冒号、空标签）
    assert "等号" in prompt, "提示词没提醒「用等号不用冒号」（模型实测会写 `<color:黄>`）"
    assert "<>" in prompt, "提示词没提醒「不许写空标签」（模型实测会写 `<>`）"

def test_csharp_whitelist_has_no_bold_or_italic():
    """C# 的白名单与提示词**必须**同进同退。

    如果提示词说"不认"而 C# 还放行 `<b>`，就会出现"模型偶然写了 → 屏幕上突然变粗"，
    与说明自相矛盾；反过来（提示词教、C# 不认）则是静默无效。
    """
    src = _read(CORE)
    i = src.index("private static string Canonical(string name)")
    body = src[i: src.index("\n        }", i)]
    assert '"b"' not in body and '"i"' not in body, (
        "C# 白名单还有 b/i —— 提示词已经不教了，两边必须一致"
    )
    assert '"color"' in body, "白名单必须有 color"
    # StartsLikeTagName 也要同步（否则半截标签判据还认得 <b）
    j = src.index("private static bool StartsLikeTagName(string tail)")
    names = src[j: src.index("\n        }", j)]
    assert '"b"' not in names and '"i"' not in names, "StartsLikeTagName 还有 b/i"


# ----------------------------------------------------------------------
# ⑨ 中英颜色名必须指同一个值（模型实际会混着用）
# ----------------------------------------------------------------------

#: 中文名 → 英文名。实测里英文名的使用率很高（8 个场景里出现过
#: `<color=red>` / `<color=yellow>` / `<color=gray>`），所以这不是"备用通道"，
#: 是**同等主要**的一条路。两边指的值不一致 = 同一个"红"有时深有时浅。
EN_OF_ZH = {"红": "red", "黄": "yellow", "绿": "green", "青": "cyan", "蓝": "blue", "紫": "purple", "灰": "gray"}


def test_english_and_chinese_names_resolve_to_the_same_color():
    """中英同名必须同值。

    模型会混用（实测样本：`<color=red>要东西</color>`、`<color=yellow>未必非…</color>`、
    `<color=gray>我在这山脚下…</color>`）。两边指不同的值，玩家看到的就是
    "同样是红，这条深那条浅" —— 而且完全查不出原因（两个名字都对）。
    """
    pal = _cs_palette()                     # 只取中文键
    src = _read(CORE)
    i = src.index("BuildPalette()")
    body = src[i: src.index("return d;", i)]
    all_pairs = dict(re.findall(r'\{\s*"([^"]+)"\s*,\s*"(#[0-9A-Fa-f]{6})"\s*\}', body))
    for zh, en in EN_OF_ZH.items():
        assert zh in all_pairs, "调色板缺中文名 %s" % zh
        assert en in all_pairs, "调色板缺英文名 %s" % en
        assert all_pairs[zh] == all_pairs[en], (
            "「%s」=%s 与「%s」=%s 不一致 —— 模型混用时同一色会深浅不一"
            % (zh, all_pairs[zh], en, all_pairs[en]))
    # 提示词教的英文名集合也必须与表一致（少教一个 = 模型写了它静默无色）
    prompt = _read(PROMPT)
    m = re.search(r"英文\s*([a-z/]+)", prompt)
    assert m, "提示词没列英文颜色名"
    taught = set(m.group(1).split("/"))
    assert taught == set(EN_OF_ZH.values()), (
        "提示词教的英文名与调色板不一致\n  提示词=%s\n  调色板=%s"
        % (sorted(taught), sorted(EN_OF_ZH.values())))


# ----------------------------------------------------------------------
# ⑩ 亮度压制必须按**对比度**判，不能按亮度判（10-31 第二次重做的盲区）
# ----------------------------------------------------------------------

def test_cap_uses_wcag_contrast_not_a_luminance_threshold():
    """★ 第一版用「感知亮度 ≤0.50」当判据，扫描色空间后发现**34% 的过判颜色对比度不足**。

    典型：#7F7F7F 亮度 0.498 过判，但玩家浅绿气泡上对比度只有 **2.60** —— 等于隐形。
    灰阶 #5C5C5C~#7C7C7C 整段都落在这个盲区里。

    根因：亮度判的是"够不够暗"，而"读不读得出来"是两个**颜色之间**的关系。
    现在必须是 WCAG 对比度（相对亮度比），且**两个气泡底都过**。
    """
    src = _read(CORE)
    i = src.index("private static void CapLum(ref int r, ref int g, ref int b)")
    body = src[i: src.index("\n        }", i)]

    assert "PassesContrast(" in body, (
        "CapLum 没用对比度判据 —— 回去用亮度阈值会留出 34% 的盲区"
    )
    assert "lum <= LumCap" not in body, (
        "CapLum 还在用 `lum <= LumCap` 这个旧判据（它判不出「够暗但看不清」）"
    )
    # 必须同时过两种底
    j = src.index("private static bool PassesContrast(int r, int g, int b)")
    pb = src[j: src.index("\n        }", j)]
    assert "BubbleBgNpc" in pb and "BubbleBgUser" in pb, (
        "PassesContrast 必须**同时**检查两种气泡底（漏掉浅绿底就是第一版的错误）"
    )
    # 常量本身也要在
    assert "MinContrast = 4.5" in src or "MinContrast = 4.50" in src, (
        "MinContrast 应为 4.5（WCAG 2.1 AA 正文标准）"
    )


def test_cap_preserves_hue_by_scaling_proportionally():
    """压暗必须**等比缩放**（保色相）。

    早期想用"换成同亮度的灰"，那会把「红」压成灰 —— 玩家看到的是"颜色没了"，
    而不是"颜色被保护了"。等比缩放只降明度，色相与饱和度关系保持不变。
    """
    src = _read(CORE)
    i = src.index("private static void CapLum(ref int r, ref int g, ref int b)")
    body = src[i: src.index("\n        }", i)]
    assert re.search(r"Clamp255\(r \* k\)", body), "没按比例缩放 r"
    assert re.search(r"Clamp255\(g \* k\)", body), "没按比例缩放 g"
    assert re.search(r"Clamp255\(b \* k\)", body), "没按比例缩放 b"


# ----------------------------------------------------------------------
# ⑪ 行距的实际观感**取决于字体**（同一 1.35 在两种字体上差一倍）
# ----------------------------------------------------------------------

def test_line_spacing_depends_on_font_metrics_and_that_is_logged():
    """★ `lineSpacing` 是**乘数**（Unity 文档原话 "The line spacing multiplier"），
    乘的是**字体基础行高（ascent+descent）**，而它**不等于 fontSize**。

    实测（fontSize=19，Pillow 读字体度量）：

    | 字体 | 基础行高 | 比值 | ls=1.35 时行盒 | 墨迹空隙占字号 |
    |---|---|---|---|---|
    | SimHei  | 20 | 1.05× | 27.0 | 53% |
    | MSYaHei | 26 | 1.37× | 35.1 | **95%** |

    也就是说同一个 1.35，在 MSYaHei 上比 SimHei 上**松一倍**。
    而字体来自三级兜底链（① 捞游戏现成 Text 的字体 ② `SimHei` ③ 内置 LegacyRuntime），
    **每台机器拿到的可能不是同一款** —— 所以"玩家实际看到多松"必须能从日志读出来，
    否则下次觉得行距不对只能盲调。

    这条钉住那行日志存在（不钉具体数值 —— 那取决于玩家机器上的字体）。
    """
    src = (ROOT / "csharp" / "UI" / "ChatUiBuilder.cs").read_text(encoding="utf-8")
    i = src.index("public static Font ResolveSharedFont()")
    body = src[i: i + 2200]
    assert "ModMain.P(" in body, (
        "ResolveSharedFont 没留下「用了哪款字体」的日志 —— 行距观感取决于字体度量，"
        "不记下来就无法从玩家日志判断他那边松不松"
    )
    assert "GetCharacterInfo(" in body, (
        "日志里没有实际度量（GetCharacterInfo）—— 只报字体名不足以判断行高"
    )
    assert "BubbleTextCore.BubbleLineSpacing" in body, (
        "日志应带上当前行距常量，方便和字体度量一起看"
    )


def test_line_spacing_is_a_multiplier_not_pixels():
    """行距常量必须是**乘数**语义（1.0 = 默认），不能写成像素。

    证据：Unity `TextGenerationSettings.lineSpacing` 的官方注释是
    "The line spacing multiplier."；预制件里的字段 `m_LineSpacing: 1` 也是乘数形态。
    如果有人把它当像素（比如填 20），行高会变成字体的 20 倍。
    """
    src = _read(CORE)
    m = re.search(r"public const float BubbleLineSpacing = ([\d.]+)f;", src)
    assert m, "找不到 BubbleLineSpacing"
    v = float(m.group(1))
    assert 1.0 <= v <= 2.0, (
        "行距 %s 不像乘数（合理区间 1.0~2.0）。它是**乘数**不是像素 —— "
        "填 20 会让行高变成字体基础行高的 20 倍" % v
    )
