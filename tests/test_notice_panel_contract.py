"""「须知」面板（注意事项 + 更新公告）的接线契约（10-23 用户需求）。

用户的原话是三件事，本文件逐条钉住：

1. **入口**：对话窗标题栏 ⓘ / 设置窗标题栏「须知」+ 版本更新后自动弹一次（同版本不再弹）。
2. **"打开过没有"要真的记得住**：判据是 `notice_seen.json` 里的**单调递增水位**（`seq`），
   **不是**布尔开关、**不是**版本字符串比较 —— 后者在 `1.0.10 vs 1.0.9` 会判错，
   且"改了内容忘改版本号"时会静默不再弹（最坏的失败：公告消失且无报错）。
3. **文案只在一个地方**：面板里不许出现任何一句公告原文，问文案只能问 `NoticeContentCore`。

为什么必须钉在源码上（而不是只跑单测）：
`NoticeCoreTests` 只证明"判据函数是对的"；**判据有没有被接线**是另一件事，而且失败时
完全静默 —— 水位没人读 ⇒ 每次都弹；Panel 里硬编码了一句公告 ⇒ 内容核改了它也不变。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"
UI = CS / "UI"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """剥掉注释（`//` 行注释、`/* */` 块注释），只留可执行代码。

    为什么必须剥：本仓注释里大量**引用别的符号**（ESC 那段注释就写着
    `BigPortrait.ConsumesEscape()`），不剥的话"比调用先后"会拿到注释的位置，
    契约测试就会在注释搬家时莫名其妙变红（或更糟：变绿）。
    字符串里的 `//`（例如 URL）要保留，故用一个小扫描器而不是 `split("//")`。
    """
    out = []
    i = 0
    n = len(src)
    in_block = False
    while i < n:
        if in_block:
            if src.startswith("*/", i):
                in_block = False
                i += 2
            else:
                if src[i] == "\n":
                    out.append("\n")
                i += 1
            continue
        if src.startswith("/*", i):
            in_block = True
            i += 2
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src[i] == '"':
            out.append(src[i])
            i += 1
            while i < n:
                out.append(src[i])
                if src[i] == "\\":
                    i += 1
                    if i < n:
                        out.append(src[i])
                        i += 1
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def _method_body(src: str, sig_fragment: str) -> str:
    """从 `sig_fragment` 起截到配对的大括号（源码契约测试的老手法，见 test_bubble_text_contract）。"""
    i = src.index(sig_fragment)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError("大括号不配对：" + sig_fragment)


# ---------------------------------------------------------------- 1. 文件与唯一真相源

def test_three_files_exist_and_split_roles():
    """内容 / 判据 / 视图 三分：改文案不动布局，改判据不动文案。"""
    for name in ("NoticeContentCore.cs", "NoticeCore.cs", "NoticeStore.cs", "NoticePanel.cs"):
        assert (UI / name).is_file(), f"缺少 {name}"
    # 内容核必须是纯数据（零 Unity 依赖），否则单测编不进去
    content = _read(UI / "NoticeContentCore.cs")
    assert "using UnityEngine" not in content, "NoticeContentCore 必须零 Unity 依赖（要编进 net8 单测）"
    assert "using Newtonsoft" not in content, "NoticeContentCore 不许依赖 Newtonsoft"
    # 判据核同样纯逻辑
    core = _read(UI / "NoticeCore.cs")
    assert "using UnityEngine" not in core, "NoticeCore 必须零 Unity 依赖（单测链接它）"
    assert "System.IO" not in core, "NoticeCore 不许碰文件（落盘归 NoticeStore）"


def test_panel_contains_no_hardcoded_notice_text():
    """★ 主判据：面板里一句公告原文都不许有 —— 文案只能从内容核取。

    反例（曾经差点发生）：图省事在 `MakeSectionCard` 里写死一句"记得点测试连接"。
    那样内容核改了它也不变，两处文案迟早分家。

    判据用**内容核里的实际字串**反查面板源码（而不是维护一份"允许的界面字"白名单：
    白名单会随界面改名天天变红，且永远不完整）。凡是内容核里的标题或正文出现在面板里
    ⇒ 说明有人把文案抄了一份过去。
    """
    content = _read(UI / "NoticeContentCore.cs")
    panel = _code_only(_read(UI / "NoticePanel.cs"))

    # 内容核里所有"正文级"字串（标题 / 每行正文 / 条目说明）。短的（<=6 字）跳过：
    # 例如键帽 "C"、标题「修复」这类短词有可能与界面固定字巧合。
    literals = re.findall(r'(?:Title|Text)\s*=\s*"((?:[^"\\\n]|\\.)*)"', content)
    literals += [m for m in re.findall(r'^\s{20,}"((?:[^"\\\n]|\\.)*)"', content, re.M)]
    big = [x for x in literals if len(x) > 6]
    assert len(big) >= 15, f"内容核里取到的正文太少（{len(big)} 条），正则可能没匹配上"

    leaked = [x for x in big if x in panel]
    assert not leaked, (
        "NoticePanel 里抄了内容核的原文（文案必须只有一份）：\n  "
        + "\n  ".join(repr(x[:40]) for x in leaked[:5])
    )


# ---------------------------------------------------------------- 2. 内容自检

def test_release_seq_strictly_increasing_and_unique():
    """序号必须严格递增且唯一 —— 水位判据完全建立在它上面。"""
    src = _read(UI / "NoticeContentCore.cs")
    seqs = [int(m) for m in re.findall(r"Seq\s*=\s*(\d+)\s*,", src)]
    assert len(seqs) >= 2, "至少要有首发与本次两条公告，否则判据跑不起来"
    assert len(set(seqs)) == len(seqs), f"公告序号重复：{seqs}"
    assert seqs[0] == max(seqs), f"最新一条必须排在最前且序号最大，实际：{seqs}"
    # `Releases` 数组里的顺序必须是"序号从大到小"（面板直接按数组顺序画）
    assert seqs == sorted(seqs, reverse=True), \
        "Releases 必须最新在最前（面板按数组顺序画，不倒序）"


def test_latest_release_version_matches_about():
    """★ 防"加了公告忘了提版本"★ —— 这是本设计的核心保险。"""
    about = _read(CS / "app" / "About.cs")
    m = re.search(r'const string Version = "([^"]+)"', about)
    assert m, "About.cs 里找不到 Version 常量"
    ver_about = m.group(1)

    content = _read(UI / "NoticeContentCore.cs")
    m2 = re.search(r"Seq\s*=\s*\d+\s*,\s*\n\s*Version\s*=\s*\"([^\"]+)\"", content)
    assert m2, "NoticeContentCore.Releases[0] 里找不到 Version"
    assert m2.group(1) == ver_about, (
        f"About.Version={ver_about} 与最新公告 Version={m2.group(1)} 不一致 —— "
        "加公告必须同时提版本，否则玩家看到的版本号与公告对不上"
    )


def test_guide_covers_the_four_must_know_items():
    """用户点名要的四件事必须在「使用须知」里，一件都不能漏。"""
    content = _read(UI / "NoticeContentCore.cs")
    must = {
        "杀毒隔离区": "第一条：连不上先查安全中心的隔离记录（用户 10-23 点名要的）",
        "保护历史记录": "要写出具体路径，不能只说'杀毒软件'",
        "还原": "必须给出「还原」这个动作",
        "在设备上允许": "只还原不加白名单，下次还会被杀 —— 这步是关键",
        "排除项": "建议把 ModAssets 加进排除项",
        "C": "第二条：通讯录快捷键",
        "测试连接": "第三条：填完 Key 必须点测试连接",
        "思考": "第三条：建议用支持思考模式的模型 / 自动填 low",
    }
    for needle, why in must.items():
        assert needle in content, f"「使用须知」缺少「{needle}」—— {why}"


def test_guide_says_no_firewall_and_correct_antivirus_path():
    """★ 事实正确性（10-23 核查结论）★

    用户原话是"防火墙的记录部分"，核查后确定：**防火墙不处理本机回环流量**
    （127.0.0.1 自己连自己不经过防火墙），真正隔离 exe 的是 Defender 的隔离区。
    文案必须写「保护历史记录 / 隔离」，不许误导玩家去关防火墙。
    """
    content = _read(UI / "NoticeContentCore.cs")
    guide = content[content.index("NoticeSection[] Guide"):content.index("NoticeRelease[] Releases")]
    assert "隔离" in guide, "必须讲隔离区（不是防火墙）"
    # 允许提"防火墙"唯一一种用法：明确说不是它（本仓的写作口径：把证伪的假说也写清楚）
    if "防火墙" in guide:
        assert "不是防火墙" in guide or "并非防火墙" in guide, \
            "提到防火墙就必须说清「不是防火墙的问题」，否则玩家会去关防火墙（无效且危险）"


def test_guide_has_no_invented_counts():
    """★ 文案里不许出现没核过的数字 ★

    第一版写了"游戏那十二个快捷键"——实际 `DefaultKeys.json` 是 33 条（两组 17+16），
    而且分组是按场景切的，面板开着时有效的未必是全部。"十二"是我编的。
    玩家会拿这个数字去数，数不对就是"这 mod 连自己都说不清"。
    规矩：**要么一个数字都不写，要么数字能在仓库里被指认出来**。
    """
    content = _read(UI / "NoticeContentCore.cs")
    guide = content[content.index("NoticeSection[] Guide"):content.index("NoticeRelease[] Releases")]
    # 「那一条」是指示代词不是计数，放行；「一」以外的数字一律要能指认来源。
    for m in re.finditer(r"([二三四五六七八九十百]+|[2-9]\d*)\s*(个|条|项|种|步|遍)", guide):
        raise AssertionError(
            "须知里出现了未核实的计数「" + m.group(0) + "」——"
            + "写成「游戏自己的快捷键」就够了，玩家不需要数"
        )


def test_release_items_are_single_sentences_numbered():
    """公告条目 = 编号 + **一句话**（10-23 改版，照工坊更新日志的写法）。

    旧版是「短标题 + 正文」两段式，用户判定"味儿太浓"。工坊那边每条就是一句话，
    玩家是**扫**公告不是读公告：自己编一句短标题反而要求他读两遍才明白改了什么。

    因此这里钉住的不是"标题长度"，而是**句子形态**：
      · 每条都在 8~40 字之间（太短说不清，太长就该拆成两条）；
      · 不以"了"结尾的残句（"修复了。"这种）；
      · 不用「！」「~」这类卖萌标点；
      · 全篇条目数够（本次更新有十来件事，写成 3 条说明又合并回"修复已知问题"了）。
    """
    content = _read(UI / "NoticeContentCore.cs")
    rel = content[content.index("NoticeRelease[] Releases"):]
    # 注意用 [^"\n] 而不是 [^"]：C# 字符串不能跨行，`[^"]` 会把 "…",\n Date = "…" 整段吃进去
    items = re.findall(r'"([^"\n]{8,})"', rel)
    # 过滤掉 Date/Author/Version 那三类字符串
    items = [t for t in items
             if not re.match(r"^\d+ 月", t) and t != "iuuuuuu" and not re.match(r"^\d+\.\d+", t)]
    assert len(items) >= 8, f"公告条目太少（{len(items)} 条），本次更新有十来件事要写"
    assert len(set(items)) == len(items), "公告条目有重复（复制粘贴没改？）"
    for t in items:
        assert 8 <= len(t) <= 40, f"条目长度不合适（{len(t)} 字）：{t}"
        assert not t.endswith("。"), f"条目不该带句号（编号本身就是分隔）：{t}"
        assert "！" not in t and "~" not in t, f"条目里不许卖萌标点：{t}"
        assert not re.search(r"(更加|进一步|极大地|完美地)", t), f"空话套话：{t}"


# ---------------------------------------------------------------- 3. 入口与接线

def test_chat_title_bar_has_notice_button():
    """对话窗顶栏那一枚：必须在 `ChatWindow.Init` 里造（每次开窗都在）。"""
    w = _read(UI / "ChatWindow.cs")
    assert "NoticeButton.EnsureChatButton(_r.Window)" in w, \
        "对话窗 Init 里没有造「须知」钮"
    btn = _read(UI / "NoticeButton.cs")
    assert 'ChatButtonName = "NoticeBtn"' in btn
    # 位置：必须避开配置/删除之间那 6px 缝隙，且与踢人（群聊）互斥
    assert "ChatBtnX = -321.8f" in btn, "须知钮的 x 漂了（应为工具钮左 8px = -321.8）"
    assert "SetChatButtonVisible" in w, "群聊切单聊时没有收起/恢复须知钮（会与踢人叠在一起）"


def test_config_title_bar_has_notice_button():
    """设置窗标题栏那一枚：AB 版运行期克隆 + 代码版兜底，两条路都要有。"""
    ab = _read(UI / "AbConfigPanel.cs")
    assert "NoticeButton.EnsureConfigButton(" in ab, "AB 设置窗没有造「须知」钮"
    code = _read(UI / "ConfigUiBuilder.cs")
    assert '"NoticeBtn"' in code, "代码版设置窗没有「须知」钮"


def test_notice_panel_is_registered_in_hotkey_gate():
    """★ 面板开着必须屏蔽游戏原生快捷键（否则手一抖按 B，背包从背后弹出来）★"""
    probe = _read(UI / "OwnUiProbe.cs")
    assert "NoticePanel.IsShowing" in probe, "OwnUiProbe 没有把须知面板算进「我们的界面开着」"
    # 顺序：须知是层顶，必须排在三个面板之前（报错名会误导排障）
    i_notice = probe.index("NoticePanel.IsShowing")
    i_cfg = probe.index("AbConfigPanel.IsShowing")
    assert i_notice < i_cfg, "须知应排在配置面板之前（它是最上层）"


def test_escape_chain_asks_notice_first():
    """★ 一次 ESC 只关一层 ★ 须知在最上层，对话窗那条路必须先问它。"""
    cp = _code_only(_read(UI / "ChatPresenter.cs"))
    body = _method_body(cp, "private void Update()")
    esc = body[body.index("if (Input.GetKeyDown(KeyCode.Escape))"):]
    assert "NoticePanel.ConsumesEscape()" in esc, "对话窗 ESC 分支没有先问须知面板"
    i_notice = esc.index("NoticePanel.ConsumesEscape()")
    i_portrait = esc.index("BigPortrait.ConsumesEscape()")
    i_cfg = esc.index("AbConfigPanel.IsOpen")
    assert i_notice < i_cfg and i_notice < i_portrait, \
        "须知必须排在配置窗与放大立绘**之前**判断（它是最上层）"


def test_panel_tick_and_auto_show_are_wired():
    """自动弹出必须挂在**常驻**帧回调上（三个面板关闭时它们的 Update 不跑）。"""
    hot = _read(UI / "AbHotkeys.cs")
    assert "NoticePanel.Tick();" in hot, "AbHotkeys.OnFrame 没有推进须知面板"
    main = _read(CS / "app" / "ModMain.cs")
    assert "NoticePanel.ScheduleAutoShow(" in main, "进世界没有安排自动弹出"
    assert "NoticePanel.OnIntoWorld();" in main, "进世界没有复位须知面板状态"


def test_seen_watermark_is_persisted_under_data_root():
    """水位文件必须落在**数据根**（换存档不重弹），且只前进不后退。"""
    store = _read(UI / "NoticeStore.cs")
    assert "ModPaths.DataRoot" in store, "水位文件没落在数据根（会随存档/工作目录漂）"
    assert "NoticeCore.NeedsWrite(" in store, "写盘前没有判「有没有前进」（会导致每次开关都写盘）"
    assert "NoticeCore.NextSeen(" in store, "写盘没有走 NextSeen（会有回退风险）"
    # 原子写（UnitIdRegistry 同款）
    assert "File.Replace" in store, "没有原子写（崩溃会留半截 JSON）"
    # 读失败必须当"没看过"，不能当"看过"
    assert "按「没看过」处理" in store, "读盘失败时的降级语义没写清楚"


def test_watermark_advances_only_when_panel_really_shows():
    """水位推进的唯一调用点 = 面板真的显示出来那一刻（EnsureBuilt 失败时不算看过）。"""
    panel = _read(UI / "NoticePanel.cs")
    body = _method_body(panel, "internal static void Show(")
    assert "NoticeStore.MarkSeen();" in body, "Show 里没有推进水位"
    assert body.index("if (!EnsureResident()) return;") < body.index("NoticeStore.MarkSeen();"), \
        "必须先确保宿主建成再记「看过」（建失败却记了 = 公告永远不再出现）"
    # 全文件只能有一处 MarkSeen 调用
    assert panel.count("NoticeStore.MarkSeen()") == 1, \
        "MarkSeen 只许有一个调用点（多个点会让「打开才记」的语义失守）"

def test_new_tags_use_open_time_snapshot_not_live_value():
    """★ 「新」标记与"还剩几条"必须用 Show 时刻的快照 ★

    实时值会在 `MarkSeen` 之后立刻变成"全都看过"⇒ 玩家点开更新公告、只看到标题还没读，
    切一下页签再切回来，「新」标签就没了；页脚也会在同一屏里自己闪一下
    （"有 3 条新公告" → "已是最新"）。
    """
    panel = _read(UI / "NoticePanel.cs")
    body = _method_body(panel, "internal static void Show(")
    # 快照来自局部 `snap`（Seq 与 Version 必须同源：分别去问 NoticeStore 会拿到两代状态）
    i_snap = body.index("var snap = NoticeStore.Seen;")
    i_mark = body.index("NoticeStore.MarkSeen()")
    assert i_snap < i_mark, "水位快照必须在 MarkSeen 之前抓（否则全被算成已看过）"
    assert "_renderSeenSeq = snap.Seq;" in body and "_renderSeenVersion = snap.Version;" in body, \
        "快照没同时抓 Seq 与 Version（页脚两侧会不同步）"

    build = _method_body(panel, "private static void BuildReleases(")
    assert "_renderSeenSeq" in build, "公告页的「新」标记没有用快照"
    assert "NoticeStore.Seen.Seq" not in build, "公告页还在用实时水位（会导致「新」标签闪没）"

    foot = _method_body(panel, "private static void UpdateFooter(")
    assert "NoticeCore.FooterUnread(_renderSeenSeq," in foot, \
        "页脚「还剩几条」没有用快照"
    # 左半边（上次看过哪个版本）同样必须走快照 —— 否则同一屏内 MarkSeen 之后它会跳到新版本
    assert "NoticeCore.FooterSeen(_renderSeenSeq, _renderSeenVersion)" in foot, \
        "页脚「上次看过」用了实时值（会在同一屏里跳版本号）"
    assert "NoticeStore.Seen" not in foot, "页脚还在直接读实时水位"


def test_body_paragraphs_are_separated():
    """多段正文之间要空一行 —— 五条并列步骤挤成连续折行时读起来像一句话没说完。"""
    panel = _read(UI / "NoticePanel.cs")
    body = _method_body(panel, "private static Text MakeBodyText(")
    assert 's += "\\n\\n";' in body, "段落之间没有空行（步骤会挤成一坨）"


def test_panel_activates_before_building_content():
    """★ 先点亮再生内容 ★

    `LayoutRebuilder.ForceRebuildLayoutImmediate` 在**未激活**的节点上量到的高度是 0
    （ContentSizeFitter 不参与），先建后亮 ⇒ 首屏所有卡片挤成一行高。这条在实机上
    只在"第一次打开"时出现，第二次就好了（已经亮了），所以最容易漏测。
    """
    panel = _read(UI / "NoticePanel.cs")
    body = _method_body(panel, "internal static void Show(")
    i_active = body.index("r.Window.gameObject.SetActive(true)")
    i_build = body.index("BuildTab(_tab)")
    assert i_active < i_build, "必须先激活窗口再建内容（否则首屏高度算成 0）"


def test_paper_sprite_bundle_name_matches_deployed_file():
    """底图取的 bundle 名必须与部署目录里的文件名（去 .ab、小写）逐字一致。"""
    import os
    name = "panel_bg.png.png"
    panel = _read(UI / "NoticePanel.cs")
    assert f'"{name}"' in panel, "面板里取底图用的 bundle 名变了"
    # 部署目录里真有这个 bundle（本机才有；换机器跳过）
    ab_dir = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/ModExportData/"
                  "Mod_Jgmg5L/ModRes/AssetBundle")
    if not ab_dir.is_dir():
        print("· 跳过：本机没有游戏目录")
        return
    found = [p for p in ab_dir.rglob("*.ab") if p.name == name + ".ab"]
    assert found, f"部署目录里找不到 {name}.ab（底图会退纯色，面板仍可用但少了纸纹）"


def test_panel_goes_through_ui_manager_not_self_made_canvas():
    """★ 架构红线（10-23 实机返工）★ 必须 `g.ui.OpenUI` 登记进游戏 UI 管理器。

    用户报的三个症状（面板开着时滚轮还能缩放地图、ESC 同时弹出游戏菜单、
    关窗后黑蒙版不退）根因是同一条：**自建 Canvas 不在 UIMgr 的登记表里，
    游戏压根不知道"有界面开着"**，于是世界输入照常放行。
    `AbChatPanel`/`AbConfigPanel`/`AbContactPanel` 走 OpenUI 被当模态，所以挡得住。

    这条测试是防回退用的：谁再想"省事，直接在 g.root 上搭一个 Canvas"，先看它。
    """
    panel = _code_only(_read(UI / "NoticePanel.cs"))
    # 必须走管理器开窗
    assert "g.ui.OpenUI(" in panel, "没有走 g.ui.OpenUI（世界输入不会被门控 —— 实机返工过一次）"
    assert "g.ui.CloseUI(" in panel, "关窗没走 CloseUI（登记项泄漏 = 世界输入被永久门控，09-10 事故）"
    assert "g.ui.GetUI(" in panel, "没有复用管理器保活的实例（重复 OpenUI 会撞登记表返回 null）"
    # 绝不许自己建 Canvas / 自挂到 g.root
    assert "AddComponent<Canvas>()" not in panel, "不许自建 Canvas（绕过 UIMgr ⇒ 输入不门控）"
    assert "AddComponent<GraphicRaycaster>" not in panel, "射线器由 UIMgr 挂，不要自己加"
    assert "AddComponent<CanvasScaler>" not in panel, "缩放由 UIMgr 挂，不要自己加"
    assert "g.root" not in panel, "不许把面板挂到 g.root（那是自建画布的老路）"
    # 关窗绝不自己 SetActive(false) 根节点
    hide = _method_body(panel, "internal static void Hide(")
    assert "SetActive(false)" not in hide, \
        "Hide 里不许自己 SetActive(false)（绕过关窗契约会让登记项泄漏，世界输入被永久门控）"
    assert "CloseViaManager()" in hide, "Hide 必须交给管理器"


def test_dim_mask_lives_inside_the_instance_so_it_closes_with_it():
    """★ 关窗后黑蒙版不退（10-23 用户报的第 3 个 bug）★

    旧版 Dim 与 Window 是**兄弟节点**，`Hide()` 只关 Window ⇒ 蒙版留在屏幕上。
    现在整棵（含 Dim）是同一个 OpenUI 实例，关窗由管理器整份收走，结构上不可能再残留——
    这条钉住"Dim 必须与 Window 同根"，防止有人把它挪到别处。
    """
    panel = _read(UI / "NoticePanel.cs")
    templ = _method_body(panel, "private static GameObject BuildTemplate(")
    # Dim 与 Window 都在模板根下（同一个 parent 参数 = root.transform）
    assert 'MakeRect("Dim", root.transform)' in templ, "Dim 没挂在模板根下"
    assert 'MakeRect("Window", root.transform)' in templ, "Window 没挂在模板根下"
    # 两者都不许在关窗路径里被单独留下
    assert "Dim.SetActive" not in _method_body(panel, "internal static void Hide("), \
        "Hide 里去动 Dim 说明它没跟着实例一起关（结构又退回旧样子了）"


def test_template_has_no_canvas_and_keeps_children_off():
    """模板是**运行时树**，不是 .ab 预制体：两条硬约束。

    ① 不许带 Canvas 三件套 —— UIMgr.CreateUI 会自己挂，自带会撞
       "Can't add component ... already added" → OpenUI 内部 NRE（真机实证）。
       与 `ModAbRes.Inject(stripCanvas: true)` 是同一条约束。
    ② 模板根必须**保持激活**、只关 Dim/Window ——
       `new GameObject()` 建的是真场景对象，根关掉的话克隆出来也是关的，
       而 Show 只点亮子节点 ⇒ activeInHierarchy 恒 false ⇒ 面板永远不显示。
    """
    panel = _read(UI / "NoticePanel.cs")
    templ = _method_body(panel, "private static GameObject BuildTemplate(")
    assert "AddComponent<Canvas>()" not in templ
    assert "AddComponent<CanvasScaler>()" not in templ
    assert "AddComponent<GraphicRaycaster>()" not in templ
    assert "root.SetActive(false)" not in templ, \
        "模板根不许关（克隆出来会是关的，面板永远不显示）"
    assert "dim.gameObject.SetActive(false)" in templ and "win.gameObject.SetActive(false)" in templ, \
        "模板里 Dim/Window 出厂必须是关的（否则模板本身在场景里显示出来）"


def test_escape_hides_one_frame_later_so_the_game_never_sees_an_empty_top():
    """★ ESC 必须推迟一帧才关（10-23 用户报的第二个 bug）★

    游戏自己也在读 ESC，Update 顺序我们控制不了。若我们先关窗：
    面板一没，游戏那边"层顶还有没有 UI"就到手为空 ⇒ 同一次 ESC 顺手弹出游戏菜单。
    推迟一帧则两种顺序都安全 —— 本帧面板始终还开着，游戏无论何时来看都让给我们；
    下一帧 `GetKeyDown` 已为 false，谁也不会再动手。

    ⚠ 这条与"ESC 只关一层"（`_escFrame`）是两件事：
      · `_closeAtFrame` 保证"游戏不知道我们关了"；
      · `_escFrame` 保证"同一次 ESC 不关第二个窗"。
    """
    panel = _read(UI / "NoticePanel.cs")
    tick = _method_body(panel, "internal static void Tick(")
    assert "_closeAtFrame = Time.frameCount + 1" in tick, \
        "ESC 没有推迟一帧（游戏会在同一次 ESC 里弹出自己的菜单）"
    assert "Hide();" in tick, "推迟到点后没有真的关"
    # 帧号必须记在**检测到 ESC 的帧**上：记在延迟后那一帧等于死字段
    # （那一刻 GetKeyDown 已是 false，没有任何调用方还会来问「这次 ESC 归谁」）
    i_mark = tick.index("_escFrame = Time.frameCount;")
    i_defer = tick.index("_closeAtFrame = Time.frameCount + 1")
    i_hide = tick.index("Hide();")
    assert i_mark < i_defer, "`_escFrame` 必须记在检测到 ESC 的同一帧（不是延迟之后那一帧）"
    assert i_mark < i_hide


def test_hide_is_idempotent_because_the_game_may_close_it_first():
    """游戏自己会关我们（面板已登记在 UIMgr，ESC 落到层顶 UI 时由管理器走 CloseUI）。

    关掉之后再调 Hide() 不能"拿已关的 UI 走关闭流程"——那是本仓反复踩的坑。
    """
    panel = _read(UI / "NoticePanel.cs")
    hide = _method_body(panel, "internal static void Hide(")
    assert "if (!IsShowing && _instance == null) return;" in hide, \
        "Hide 没有幂等守卫（游戏先关掉时这里会再走一次 CloseUI）"


def test_auto_show_marks_done_only_after_panel_really_appeared():
    """★ "每进程一次"的额度必须在**面板真的弹出来之后**才花掉 ★（10-23 复查修）

    旧写法：
        _autoDoneThisRun = true;          // ← 先花额度
        Show(...);                        // ← 可能因 GateReason 直接失败

    `Show → EnsureResident` 在"游戏日历 / 存档世界 / 世界界面"未就绪时会 return false，
    那一刻面板根本没出现，但额度已经花光 ⇒ **首次玩家永远看不到公告**，
    而且日志里连一句失败都没有（这是最容易漏测的一条：只有全新安装 + 进世界慢了才复现）。

    首次打开这条路径在开发机上几乎测不到：水位文件一旦写过就是 seq=3，
    之后 `ShouldAutoShow(3,3)=false`，自动弹出分支再也不执行。
    所以必须靠这条契约把次序钉死。
    """
    panel = _code_only(_read(UI / "NoticePanel.cs"))
    tick = _method_body(panel, "internal static void Tick(")
    i_show = tick.index("Show(wantTab);")
    i_done = tick.index("_autoDoneThisRun = true;")
    assert i_show < i_done, \
        "`_autoDoneThisRun` 在 Show 之前置位 —— 面板没弹出来也会被算作「本次已弹过」"
    # 必须按"真的显示出来了"判定，而不是 Show 一返回就当成功
    assert "if (IsShowing)" in tick, "没有按 IsShowing 判定弹没弹出来"
    # 失败要能重试，不能一次就放弃（除非超时）
    assert "_autoAt = Now() + 0.5f;" in tick, "没弹出来时没有安排重试"
    assert "_autoDeadline" in tick, "没有等待上限（永远不就绪的场景里会无限重试）"


def test_auto_show_timeout_does_not_burn_the_once_per_process_quota():
    """★ 超时只能放弃**这一次**，不许花掉"每进程一次"的额度 ★（10-28 修）

    真机实证（10-23 15:47 那次日志）：进世界后世界界面整整一分钟没就绪，
    `当前不宜创建（存档世界尚未加载）` 连打 80 行，然后
    `自动弹出等待超时，本次放弃` —— 而它**顺手把 `_autoDoneThisRun` 置了 true**
    ⇒ 本进程后面再进世界也不弹 ⇒ 玩家看到的就是"更新了但没弹出来"。

    额度的语义只有一条：**弹出来了才花**。世界没就绪属于"这次没轮到"，不是"看过了"。
    超时分支若再出现 `_autoDoneThisRun = true`，这条立刻变红。
    """
    panel = _code_only(_read(UI / "NoticePanel.cs"))
    tick = _method_body(panel, "internal static void Tick(")
    # 超时分支：`_autoPending = false;` 之后紧跟的那段里不许再置 done
    i_timeout = tick.index("自动弹出等待超时")
    tail = tick[i_timeout:tick.index("else", i_timeout)]
    assert "_autoDoneThisRun" not in tail, \
        "超时分支把「每进程一次」的额度也花掉了 —— 世界没就绪不等于玩家看过了"
    # done 只许出现一次（真的弹出来的那一支）
    assert tick.count("_autoDoneThisRun = true;") == 1, \
        "`_autoDoneThisRun = true;` 只该有一个置位点（Show 成功之后）"


def test_auto_show_always_leaves_a_trace_either_way():
    """★ 弹 / 不弹 / 待命 / 超时，四条路都必须留一行日志 ★（10-28 用户报「更新了没弹」）

    这条不是洁癖：`ShouldAutoShow` 静默 return 的样子，与"自动弹出整个没接线"**完全一样**。
    上一次排查只能翻 `notice_seen.json` 反推水位，因为日志里一个字都没有。

    钉住三件事：
      · 判据为假时那行必须带上**水位与最新值**（区分"已看过"与"内容核没加条目"）；
      · 安排成功时那行必须带上**落页**（老玩家该落到「更新公告」而不是重读入门）；
      · 等世界就绪期间有节流日志，之后必然出现「已弹出」或「超时」，中间不留空白。
    """
    panel = _read(UI / "NoticePanel.cs")
    sched = _method_body(_code_only(panel), "internal static void ScheduleAutoShow(")
    assert "不自动弹：已看过最新" in sched, "判据为假时没有任何日志（排障时无从下手）"
    assert "seen.Seq" in sched and "max" in sched, "那行没带水位/最新值（分不清是哪一种原因）"
    assert "已安排自动弹出" in sched, "安排成功时没有日志"
    assert "落页=" in sched, "安排那行没写落页（看不出会弹哪一页）"

    tick = _method_body(_code_only(panel), "internal static void Tick(")
    assert "继续等待" in tick, "等世界就绪期间没有日志（`已安排` 之后会是一片空白）"
    assert "自动弹出等待超时" in tick, "超时没有日志"
    assert "自动弹出成功" in tick, "弹出来了却没日志（四条路必须都能在日志里被单独确认）"
    # 节流：不能每 0.5s 一行
    assert "5f" in tick, "待命日志没有节流（会像闸门那条一样刷屏）"


def test_first_install_path_is_covered_by_logic_tests():
    """首次安装（水位文件不存在 → seq=0）的核心判定，必须在纯逻辑测试里有覆盖。

    这条守住"首次打开"这件事本身：真机上删水位才能复现，而单测里随时可跑。
    """
    prog = (Path(__file__).resolve().parents[1]
            / "csharp/tests/NoticeCoreTests/Program.cs").read_text(encoding="utf-8")
    assert "首次安装（0 < 3）⇒ 弹" in prog, "没有「首次安装应该弹」的断言"
    assert "从没看过（seq=0）⇒ 使用须知" in prog, \
        "首次打开必须落在「使用须知」页（新玩家先要知道怎么用，不是先看更新日志）"


def test_transparent_hit_areas_are_almost_transparent_not_zero():
    """★ 透明按钮不许用 `Color.clear(α=0)` ★（10-23 用户报「更新公告点不动」的根因）

    `alpha=0` 的 Graphic 会被 canvas 判为剔除（`CanvasRenderer.cull = true`），
    而 `GraphicRaycaster` **会跳过被剔除的图形** ⇒ 点击区"看不见、也点不到"。
    症状是"点了完全没反应"，且日志一个字都没有（射线压根没打中）。

    正确写法：`new Color(0,0,0, 1f/255f)` —— 屏幕上肉眼不可见，但足以让 raycaster 认它。
    本仓已有两处同样处理：`ToolPanel:342`（滚动命中区）、`BigPortrait.PickTint`（模态拦截层）。
    """
    # 必须剥注释再查：本文件头上正大光明写着「绝不能是 Color.clear(α=0)」这句复盘，
    # 不剥的话那条注释自己就会把测试判红（已经踩过一次）。
    panel = _code_only(_read(UI / "NoticePanel.cs"))
    body = _method_body(panel, "private static Text MakeTab(")
    assert "Color.clear" not in body, \
        "页签点击区用了 Color.clear（α=0 会被剔除，射线打不中 —— 已实机翻车一次）"
    assert "1f / 255f" in body, "页签点击区不是 α=1/255（看不见但可点）"
    close = _method_body(panel, "private static void BuildTitleBar(")
    assert "Color.clear" not in close, "✕ 关闭钮的底也是 α=0（同一个坑）"
    assert "1f / 255f" in close, "✕ 关闭钮的底不是 α=1/255"
    # 看不见的按钮不要 hover 变色（否则凭空浮出灰块）
    nb = _method_body(panel, "private static GameObject NewButton(")
    assert "bg.a < 0.02f" in nb, "透明按钮的 Transition 判据太窄（1/255 会被当成有色底走 ColorTint）"


def test_never_uses_transform_find_because_template_children_are_inactive():
    """★ `Transform.Find` 只找激活路径 —— 用了它「须知」永远打不开（10-23 实机，已定案）★

    事故形状（实机日志逐字）：
        [Notice] g.ui.OpenUI("UINoticeAi") 创建宿主...
        [Notice] 实例节点缺失，关闭本次实例        ← 点一次按钮循环一次，屏幕毫无反应

    根因：模板里 `Dim`/`Window` 出厂 `SetActive(false)`（模板躺在资源表里不该渲染），
    而 `Transform.Find("Window")` 遇到未激活的节点直接返回 null、**不报错不打日志**
    ⇒ `CollectRefs` 判"节点缺失" ⇒ OpenUI 成功建好又立刻被关掉。

    规矩：**本文件里一句 `root.Find(` 都不许有**，一律走 `FindByPath`（自己遍历 childCount 比名字）。
    """
    panel = _code_only(_read(UI / "NoticePanel.cs"))
    for m in re.finditer(r"\.Find\s*\(", panel):
        line_no = panel[:m.start()].count("\n") + 1
        raise AssertionError(
            f"NoticePanel.cs:{line_no} 用了 Transform.Find —— 它会跳过未激活节点，"
            "模板里 Dim/Window 出厂是关的，用了必然「节点缺失」（已实机翻车一次）"
        )
    # 11-19 起 FindByPath 收进 `UI/UiKit.cs`（全仓唯一一份）—— 面板里只要**调**它即可。
    # 判据仍是"不许用 Transform.Find"（上面那条）+ "取节点走那个与 active 无关的实现"。
    assert "FindByPath(" in panel, "没有用与 active 无关的 FindByPath（UiKit）"
    # 也不许 GetComponentInChildren 那种"是否含未激活"行为不稳的重载
    assert "GetComponentInChildren<ScrollRect>(true)" not in panel, \
        "ScrollRect 改走固定路径取（includeInactive 重载在 il2cpp 下行为不稳）"


def test_every_node_gets_the_ui_layer_before_the_template_is_done():
    """★ 整屏不可见且零报错的那类 bug ★ `new GameObject()` 的 layer 是 0(Default)。

    Unity 里 layer **不继承**：爸爸是 UI(5)、`new GameObject()` 建出来的儿子仍是 0。
    游戏 UI 相机只渲染 UI 层 ⇒ 儿子全都不画，但 `activeSelf` 全是 true、日志一个字没有，
    表现就是"按钮能点、屏幕全空"（比崩溃难查十倍）。

    所以刷 layer 必须**在所有子节点建完之后**（建 Dim/Window 之前刷等于没刷）。
    本仓三个 AB 面板没这个问题，因为它们的节点来自 .ab 预制体（layer 已在文件里）。
    """
    panel = _read(UI / "NoticePanel.cs")
    templ = _method_body(panel, "private static GameObject BuildTemplate(")
    assert "SetLayerRecursive(root, layer)" in templ, "模板没刷 UI 层"
    i_layer = templ.index("SetLayerRecursive(root, layer)")
    for child in ('MakeRect("Dim"', 'MakeRect("Window"', "BuildTitleBar(", "BuildScroll("):
        i_child = templ.index(child)
        assert i_child < i_layer, f"刷 layer 在 {child} 之前 —— 该节点会留在 Default 层（看不见）"


def test_runtime_prefab_injection_is_the_non_ab_path():
    """运行时树当预制体注入 `g.res.allRes`（不出图、不重打 .ab）。

    `OpenUI` 的入参只能是**资源名**，所以树必须先进资源表。这条也钉住"双 key 写入"
    （`ModAbRes.Inject` 同款：`UI/名` 与 `名` 各写一份，游戏两条路都可能查）。
    """
    res = _read(Path(__file__).resolve().parents[1] / "csharp/app/ModAbRes.cs")
    body = _method_body(res, "internal static bool PutRuntimePrefab(")
    assert 'g.res.allRes["UI/" + key]' in body, "没写 UI/ 前缀那个 key"
    assert 'g.res.allRes[key]' in body, "没写裸名那个 key"
    panel = _read(UI / "NoticePanel.cs")
    assert "ModAbRes.PutRuntimePrefab(PrefabKey" in panel, "面板没把模板注入资源表"


# ---------------------------------------------------------------- 4. 视觉约束

def test_paper_theme_is_used_and_pinned():
    """宣纸配色必须来自 Theme（值钉在位），面板不许自己抄一份。"""
    theme = _read(UI / "Theme.cs")
    for name in ("PaperPageBg", "PaperCardBg", "PaperHairline", "PaperInk",
                 "PaperInkDim", "PaperAccent", "PaperKeycapBg"):
        assert name in theme, f"Theme 缺少 {name}"
    panel = _read(UI / "NoticePanel.cs")
    assert "Theme.PaperAccent" in panel and "Theme.PaperInk" in panel
    # 面板里不许出现裸 Color(0.2x, 0.2x, 0x) 这类自造墨色（除全透明/白/黑蒙版）
    for m in re.finditer(r"new Color\(([^)]+)\)", panel):
        vals = [v.strip() for v in m.group(1).split(",")]
        if len(vals) < 3:
            continue
        rgb = vals[:3]
        # 允许：纯黑蒙版 / 纯白 / 灰度（按钮 highlighted/pressed 的中性反馈色 —— 它们不是"墨色"，
        # 换主题时也不该跟着变）。带色相的（例如自造一个墨色/青瓷色）一律判红。
        if len(set(rgb)) == 1:
            continue
        raise AssertionError(f"NoticePanel 自造了带色相的颜色 new Color({m.group(1)})，应走 Theme.Paper*")


def test_panel_does_not_add_monobehaviour():
    """IL2CPP 铁律：不新增 MonoBehaviour（免 RegisterTypeInIl2Cpp 那一串坑）。"""
    panel = _code_only(_read(UI / "NoticePanel.cs"))
    assert ": MonoBehaviour" not in panel, "NoticePanel 不许是 MonoBehaviour（关窗/ESC 走 AbHotkeys.Tick）"
    assert "RegisterTypeInIl2Cpp" not in panel


def test_panel_has_scroll_hit_target():
    """滚动区必须自带可命中的 Graphic（否则滚轮/拖动一点都进不来 —— 踩过三次）。"""
    panel = _read(UI / "NoticePanel.cs")
    body = _method_body(panel, "private static void BuildScroll(")
    assert "raycastTarget = true" in body, "滚动区没有可命中的 Graphic"
    assert "RectMask2D" in body, "滚动区没有裁剪（长文会溢出到页脚上）"


def test_release_item_number_and_text_have_visual_hierarchy():
    """公告卡里**序号弱墨、正文墨色** —— 一句话条目也得让眼睛先抓到"共几条"。

    10-23 改版去掉了每条的独立小标题（工坊就是"编号 + 一句话"），层次感只能靠
    序号/正文的墨色差来拉开：同色时一整块文字看着像一段连续段落，数不出有几条。
    """
    panel = _read(UI / "NoticePanel.cs")
    body = _method_body(panel, "private static void MakeItemCard(")
    assert "body.color = Theme.PaperInk;" in body, "公告正文没有用墨色（它本身就是「改了什么」）"
    assert re.search(r'MakeText\("Index",[^;]*Theme\.PaperInkDim', body, re.S), \
        "序号没有用弱墨（与正文拉不开层次）"
