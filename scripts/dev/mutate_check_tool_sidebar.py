#!/usr/bin/env python3
"""变异验证 —— 对话窗右侧「可做的事」侧边栏（10-19 用户需求）。

**为什么必须有**：这个功能每一处都**天生静默失败** ——
  · 目录写错一个 op → 点击发出一句"模型会去调、C# 报不支持"的话；
  · 台词混进元指令 → 那行字永久留在账本里，重开窗以玩家气泡回放；
  · `fill` 忘留空位 → 直接发出半句话，模型只能猜玩家要什么；
  · 面板挂错父级 → 位置整个算错或压根不显示；
  · 不随窗收起 → 下次开窗展示的是**上一个 NPC** 的上下文；
  · 群聊露出这颗钮 → 点了不知道在跟谁说话。
没有一条会在日志里留下痕迹。所以验收线是"把实现逐条回退，对应断言真的变红"。

覆盖：`tests/test_tool_sidebar_contract.py`（31 项）+ `ToolCatalogTests`（C# 26 断言）。

用法：
    python3 scripts/dev/mutate_check_tool_sidebar.py
    python3 scripts/dev/mutate_check_tool_sidebar.py py      # 只跑 Python 侧
    python3 scripts/dev/mutate_check_tool_sidebar.py cs      # 只跑 C# 侧

注意：会**临时改源码再还原**（备份在系统临时目录 + `_mutate_lib.guard` 中断钩子），
跑完末行必须是"全部变异都被捕获"。
"""

from __future__ import annotations
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层，见该文件顶部说明）


import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CATALOG = ROOT / "tools/catalog.py"
WS = ROOT / "transport/ws_channel.py"
PANEL = ROOT / "csharp/UI/ToolPanel.cs"
CORE = ROOT / "csharp/UI/ToolCatalogCore.cs"
WIN = ROOT / "csharp/UI/ChatWindow.cs"
PRES = ROOT / "csharp/UI/ChatPresenter.cs"
AB = ROOT / "csharp/UI/AbChatPanel.cs"
REFS = ROOT / "csharp/UI/ChatWindowRefs.cs"

CONTRACT = "tests/test_tool_sidebar_contract.py"

MUTATIONS = [
    # ── 目录 ↔ schemas 对账 ────────────────────────────────────
    (
        "① 目录里写一个不存在的 op（点击发出一句永远失败的话）",
        CATALOG,
        'argval="marry"',
        'argval="marry_me"',
        "test_ops_match_schema_enums_exactly",
    ),
    (
        "② 新增工具没进目录也不写理由（玩家永远看不见它）",
        CATALOG,
        '"search_units": (',
        '"search_units_x": (',
        "test_every_tool_is_covered_or_explained",
    ),
    (
        "③ 台词混进元指令（那行字永久躺在账本里）",
        CATALOG,
        '"这世间路远，你可愿与我结为道侣？"',
        '"请调用 social_relation 结为道侣"',
        "test_catalog_has_no_self_inconsistency",
    ),
    (
        "④ fill 型忘了留空位（直接发出半句话，模型只能猜）",
        CATALOG,
        '"你那儿可有" + BLANK + "？赠我一件罢。"',
        '"你那儿可有什么好东西？赠我一件罢。"',
        "test_economic_tools_are_all_fill",
    ),
    (
        "⑤ 经济工具改成 send 型（模型会自己猜要送什么）",
        CATALOG,
        '           "你那儿可有" + BLANK + "？赠我一件罢。", "economy_item",',
        '           "你那儿可有什么好东西？赠我一件罢。", "economy_item",',
        "test_economic_tools_are_all_fill",
    ),
    (
        "⑥ 空位标记改成半角空格（光标落在句尾/字中间）",
        CATALOG,
        'BLANK = "\\u3000"',
        'BLANK = " "',
        "test_blank_marker_matches_between_python_and_csharp",
    ),
    (
        "⑦ 元指令词表被清空（红线形同虚设）",
        CATALOG,
        'META_WORDS = (\n    "调用", "工具", "函数", "schema", "参数", "系统提示", "提示词",\n    "prompt", "tool", "assistant", "agent", "模型",\n    "执行", "触发", "帮我调",\n)',
        'META_WORDS = ()',
        "test_meta_word_redline_actually_fires",
    ),
    (
        "⑧ 目录 key 重复（两行撞键：C# 侧点哪一行都可能错）",
        CATALOG,
        '_i("jieyi", "义结金兰"',
        '_i("marry", "义结金兰"',
        "test_catalog_has_no_self_inconsistency",
    ),
    (
        "⑨ UNLISTED 的理由写成占位符（等于没说）",
        CATALOG,
        '        "必填参数 filters 是一个过滤字典（姓名/关系/境界/宗门/地区/种族/性别）。"\n'
        '        "没有任何一句预设台词能把它填对 —— 摆一个点下去只会返回「全图好感最高的十个人」的按钮，"\n'
        '        "等于教玩家「这个按钮没用」。留给人自己描述要找谁。"',
        '        "暂不。"',
        "test_unlisted_reasons_are_real_text",
    ),
    # ── Python 侧下发 ─────────────────────────────────────────
    (
        "⑩ list_tools 路由删掉（C# 拉不到目录，面板永远空）",
        WS,
        'if method == "list_tools":',
        'if method == "list_tools_dead":',
        "test_list_tools_rpc_exists_and_returns_the_catalog",
    ),
    (
        "⑪ RPC 自己拼一份形状（与目录双真相源）",
        WS,
        "            return _catalog.payload()",
        '            return {"groups": _catalog.payload()["groups"]}',
        "test_list_tools_rpc_exists_and_returns_the_catalog",
    ),
    (
        "⑫ 下发里混进 tool（诱导后来的人在 C# 里做路由判断）",
        CATALOG,
        '                        "sub": it["sub"],',
        '                        "sub": it["sub"], "tool": it["tool"],',
        "test_catalog_payload_shape",
    ),
    (
        "⑬ 目录被塞进提示词（每回合固定 token 白涨）",
        ROOT / "agent/system_prompt.py",
        "from ..tools.schemas import ALL_TOOL_SCHEMAS",
        "from ..tools.schemas import ALL_TOOL_SCHEMAS\n            from ..tools import catalog  # noqa",
        "test_catalog_is_not_in_the_prompt",
    ),
    # ── C# 接线 ───────────────────────────────────────────────
    (
        "⑭ 面板挂到根 Canvas（坐标基准错，位置整个偏掉）",
        WIN,
        "ToolPanel.Build(_r.Window, () => ToolsClicked?.Invoke());",
        "ToolPanel.Build(_r.Root.GetComponent<RectTransform>(), () => ToolsClicked?.Invoke());",
        "test_panel_is_parented_to_bg_not_root_canvas",
    ),
    (
        "⑮ AB 路径不填 refs.Window（面板静默不出现，零报错）",
        AB,
        '            r.Window = FindRt(root, "BG");',
        '            r.Window = null;',
        "test_ab_panel_actually_fills_the_bg_field",
    ),
    (
        "⑯ 工具钮改成新建而不是克隆（样式与隔壁不一致）",
        PANEL,
        '                var src = bg.Find("ConfigBtn");',
        '                var src = (Transform)null;',
        "test_tool_button_is_cloned_not_invented",
    ),
    (
        "⑰ 工具钮回到事故位置 -107.8（盖住删除键 —— 用户截图实证过的那个 bug）",
        PANEL,
        "private const float ToolBtnX = -249.8f;",
        "private const float ToolBtnX = -107.8f;",
        "test_tool_button_does_not_overlap_the_delete_button",
    ),
    (
        "⑰b 工具钮挪进踢人位（单聊不显示拉人/踢人所以测不出来，但群聊切换时会撞）",
        PANEL,
        "private const float ToolBtnX = -249.8f;",
        "private const float ToolBtnX = -321.8f;",
        "test_tool_button_does_not_overlap_the_delete_button",
    ),
    (
        "⑱ ★ 面板另写一份发送逻辑（绕过时间戳/死者拦截/TurnRegistry）",
        PRES,
        "            OnSubmitRequested();\n        }\n\n        /// <summary>把一句话填进输入框",
        "            _ws.SendPlayerMessage(_window.CurrentNpcId, text);\n        }\n\n        /// <summary>把一句话填进输入框",
        "test_click_reuses_the_single_send_path",
    ),
    (
        "⑲ fill 判定挪到发送之后（填空型被直接发出去）",
        PRES,
        "            if (ToolCatalogCore.IsFill(mode, text))",
        "            if (ToolCatalogCore.IsFill(mode, text) && false)",
        "test_the_fill_branch_is_not_disabled_and_never_sends",
    ),
    (
        "⑳ 填空型也直接发（半句话上屏，模型只能猜）",
        PRES,
        "                FillInput(text);\n                return;",
        "                FillInput(text);\n                OnSubmitRequested();\n                return;",
        "test_the_fill_branch_is_not_disabled_and_never_sends",
    ),
    (
        "㉑ 填空不设光标（玩家得自己找位置删字）",
        PRES,
        "            try { input.caretPosition = caret; } catch { }",
        "            try { } catch { }",
        "test_fill_puts_the_caret_on_the_blank",
    ),
    (
        "㉒ 填空不聚焦（玩家还得自己点一下输入框）",
        PRES,
        "            try { input.Select(); input.ActivateInputField(); } catch { }",
        "            try { } catch { }",
        "test_fill_puts_the_caret_on_the_blank",
    ),
    (
        "㉓ 填空后不给提示（玩家以为已经发出去了）",
        PRES,
        '            _window.AppendSystemNotice("把「" + BLANK_HINT + "」处补上要说的东西，再按回车发出");',
        "            // mutated: 不给提示",
        "test_fill_puts_the_caret_on_the_blank",
    ),
    (
        "㉔ ESC 不认面板（一次按键关掉面板+对话窗两层）",
        PRES,
        "                if (ToolPanel.IsOpen)\n                {\n                    ToolPanel.Hide();\n                    return;\n                }",
        "                if (false)\n                {\n                    ToolPanel.Hide();\n                    return;\n                }",
        "test_esc_closes_the_panel_before_the_window",
    ),
    (
        "㉕ ESC 关了面板但没 return（同一次按键继续关窗）",
        PRES,
        "                    ToolPanel.Hide();\n                    return;",
        "                    ToolPanel.Hide();",
        "test_esc_panel_branch_returns_immediately",
    ),
    (
        "㉖ 关窗不收面板（下次开窗它以「开着」的姿态出现）",
        PRES,
        """            // 工具侧边栏随窗收起（10-19）：面板挂在 BG 下，窗口一关就看不见了 —— 但它是
            // **独立于窗口显隐**的一棵树（ToolPanel 自己的 root），不收起的话下次开窗会直接
            // 以"开着"的姿态出现，而那时目录可能还是上一个 NPC 的上下文。
            ToolPanel.Hide();""",
        "            // mutated: 关窗不收面板",
        "test_panel_is_hidden_on_every_exit_path",
    ),
    (
        "㉗ 换 NPC 不收面板（拿上一个人的上下文摆在新人脸上）",
        PRES,
        '            // 换人先把侧边栏收起（10-19）：面板里每一条都是"对**这个人**说什么"，\n            // 换绑后还开着就等于把上一个人的上下文摆在新人脸上。\n            ToolPanel.Hide();',
        "            // mutated: 换 NPC 不收面板",
        "test_panel_is_hidden_on_every_exit_path",
    ),
    (
        "㉘ 进群不收面板",
        PRES,
        '            ToolPanel.Hide();\n            bool hasRoom',
        '            bool hasRoom',
        "test_panel_is_hidden_on_every_exit_path",
    ),
    (
        "㉙ 群聊里露出工具钮（点了不知道在跟谁说话）",
        WIN,
        "                var toolBtn = ToolPanel.ButtonGo;\n                if (toolBtn != null) toolBtn.SetActive(!show);",
        "                var toolBtn = ToolPanel.ButtonGo;\n                if (toolBtn != null) toolBtn.SetActive(true);",
        "test_button_visible_only_in_solo_chat",
    ),
    (
        "㉚ 删除模式里工具钮还亮着（选删除时另起一轮对话）",
        WIN,
        "            var toolBtnSel = ToolPanel.ButtonGo;\n            if (toolBtnSel != null) toolBtnSel.SetActive(false);",
        "            var toolBtnSel = ToolPanel.ButtonGo;",
        "test_button_visible_only_in_solo_chat",
    ),
    (
        "㉛ 退出删除模式无条件恢复（群聊里冒出工具钮）",
        WIN,
        "                if (toolBtnBack != null) toolBtnBack.SetActive(!IsRoomMode);",
        "                if (toolBtnBack != null) toolBtnBack.SetActive(true);",
        "test_button_visible_only_in_solo_chat",
    ),
    (
        "㉜ 订阅了不退订（宿主重建后一次点击触发多次）",
        PRES,
        "                ToolPanel.ItemChosen -= OnToolItemChosen;",
        "                // mutated: 不退订",
        "test_presenter_subscribes_and_unsubscribes",
    ),
    (
        "㉝ 首次打开不拉目录（面板空白且没有回音）",
        PRES,
        '            _ws.SendRequest("list_tools", new JObject(), resp =>',
        '            _ws.SendRequest("list_tools_dead", new JObject(), resp =>',
        "test_presenter_fetches_the_catalog_once_and_refreshes_lazily",
    ),
    (
        "㉞ 不走缓存（每次打开都发一次 RPC）",
        PRES,
        "            if (ToolPanel.HasData) { ToolPanel.Show(); return; }",
        "            if (false) { ToolPanel.Show(); return; }",
        "test_presenter_fetches_the_catalog_once_and_refreshes_lazily",
    ),
    (
        "㉟ 拉取失败静默（就是「点了没反应」）",
        PRES,
        '                        _window.AppendSystemNotice("工具目录没能取到（" + (string.IsNullOrEmpty(err) ? "无应答" : err) + "）");',
        "                        // mutated: 静默失败",
        "test_catalog_failure_is_never_silent",
    ),
    (
        "㊱ 滚动区透明层 alpha 写成 0（被剔除，滚轮进不来）",
        PANEL,
        "hit.color = new Color(0f, 0f, 0f, 1f / 255f);",
        "hit.color = new Color(0f, 0f, 0f, 0f);",
        "test_panel_scroll_area_has_a_raycast_target",
    ),
    (
        "㊲ 滚动区不吃射线（能开、节点全 True、就是滚不动）",
        PANEL,
        "            hit.raycastTarget = true;",
        "            hit.raycastTarget = false;",
        "test_panel_scroll_area_has_a_raycast_target",
    ),
    (
        "㊳ 面板宽度算错（不是右侧四分之一）",
        PANEL,
        "private const float PanelW = 300f;",
        "private const float PanelW = 520f;",
        "test_panel_dims_are_the_quarter_the_user_asked_for",
    ),
    (
        "㊴ 顶边压住顶栏（把 ✕ 盖住，玩家只能靠 ESC 退出）",
        PANEL,
        "private const float TopInset = 40f;",
        "private const float TopInset = 0f;",
        "test_panel_dims_are_the_quarter_the_user_asked_for",
    ),
    (
        "㊵ ChatWindow 不发事件（顶栏钮点了没反应）",
        WIN,
        "ToolPanel.Build(_r.Window, () => ToolsClicked?.Invoke());",
        "ToolPanel.Build(_r.Window, null);",
        "test_window_exposes_the_event_and_wires_it",
    ),
    (
        "㊼ 换存档只调 Reset 不调 ResetForWorld（新档里面板永远不重建，零日志）",
        ROOT / "csharp/app/ModMain.cs",
        "try { ToolPanel.ResetForWorld(); }",
        "try { ToolPanel.Reset(); }",
        "test_world_reload_uses_reset_for_world_not_reset",
    ),
    (
        "㊽ 换存档根本不重侧面板（僵尸引用留着）",
        ROOT / "csharp/app/ModMain.cs",
        "                try { ToolPanel.ResetForWorld(); }\n                catch (Exception tpe) { P(\"[AgentLoopBridge] 换存档后重置工具侧边栏失败: \" + tpe.Message); }",
        "                // mutated: 不复位",
        "test_world_reload_uses_reset_for_world_not_reset",
    ),
    (
        "㊾ HasPanel 裸判 != null（僵尸被当成「宿主还在」，永不重建）",
        PANEL,
        "public static bool HasPanel { get { return Alive(_root); } }",
        "public static bool HasPanel { get { return _root != null; } }",
        "test_panel_guards_against_zombie_references",
    ),
    (
        "㊿ Build 里裸判 _root != null（僵尸宿主被当成活着）",
        PANEL,
        "                if (Alive(_root))",
        "                if (_root != null)",
        "test_panel_guards_against_zombie_references",
    ),
    (
        "51 ButtonGo 裸返回 _btn（僵尸按钮 SetActive 会抛）",
        PANEL,
        "                if (Alive(_btn)) return _btn;",
        "                if (_btn != null) return _btn;",
        "test_panel_guards_against_zombie_references",
    ),
    # ── 判定核（C# 单测那一半）────────────────────────────────
    (
        "㊶ 空位判据改成半角空格（光标落在字中间）",
        CORE,
        "        public const char Blank = '\\u3000';",
        "        public const char Blank = ' ';",
        "test_blank_marker_matches_between_python_and_csharp",
    ),
]

#: C# 单测那一半单独跑（Python 契约测不到 C# 判定核的行为，只能靠 ToolCatalogTests）
CS_MUTATIONS = [
    (
        "㊷ （C#）FirstBlank 认半角空格（认错的字符 → 光标停在无关字中间）",
        CORE,
        "            return say.IndexOf(Blank);",
        "            return say.IndexOf(' ');",
    ),
    (
        "㊸ （C#）多空位取最后一个（光标该在第一个）",
        CORE,
        "            return say.IndexOf(Blank);",
        "            return say.LastIndexOf(Blank);",
    ),
    (
        "㊹ （C#）没空位时光标落在 0 而不是句尾",
        CORE,
        "            return i >= 0 ? i : (say ?? \"\").Length;",
        "            return i >= 0 ? i : 0;",
    ),
    (
        "㊺ （C#）BeforeSend 删掉空位（把「我漏填了」伪装成「我说了别的」）",
        CORE,
        "            return say ?? \"\";\n        }\n    }\n}",
        "            return (say ?? \"\").Replace(Blank.ToString(), \"\");\n        }\n    }\n}",
    ),
    (
        "㊻ （C#）IsFill 忽略 mode（台词没空位时也不算 fill）",
        CORE,
        "            return string.Equals(mode ?? \"\", ModeFill, System.StringComparison.OrdinalIgnoreCase);",
        "            return false;",
    ),
]

_BAK = Path(tempfile.gettempdir()) / "mutate_check_tool_sidebar"


def _run_contract(node=None):
    cmd = [sys.executable, "-m", "pytest",
           CONTRACT if node is None else f"{CONTRACT}::{node}", "-q", "--no-header"]
    p = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    return p.returncode, p.stdout + p.stderr


def _run_cs():
    cmd = [sys.executable, str(ROOT / "scripts/dev/run_csharp_tests.py"),
           "--only", "ToolCatalogTests", "--quiet"]
    p = _mutate_lib.run(cmd, cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    do_py = mode in ("all", "py")
    do_cs = mode in ("all", "cs")

    files = sorted({m[1] for m in MUTATIONS} | {m[1] for m in CS_MUTATIONS})
    print("=== 工具侧边栏（目录 / 下发 / C# 接线 / 判定核）===")
    _mutate_lib.guard(files)      # 被中断也要还原（见 _mutate_lib.guard）
    _BAK.mkdir(exist_ok=True)
    for f in files:
        shutil.copy2(f, _BAK / f.name)

    def restore():
        for f in files:
            shutil.copy2(_BAK / f.name, f)

    bad = 0
    total = 0

    if do_py:
        rc, out = _run_contract()
        print("Python 基线：exit=", rc)
        if rc != 0:
            print("★ 基线不是绿的，先修基线：\n", out[-1500:])
            restore()
            return 99
        for name, path, old, new, expect in MUTATIONS:
            total += 1
            restore()
            src = path.read_text(encoding="utf-8")
            if old not in src:
                print(f"✗ {name}: 找不到待替换文本（测试自身失效，需更新本脚本）")
                bad += 1
                continue
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
            rc, out = _run_contract(expect)
            ok = rc != 0 and expect in out
            print(f"{'✓' if ok else '✗'} {name} → exit={rc} 命中[{expect}]={expect in out}")
            if not ok:
                bad += 1
                print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])
        restore()

    if do_cs:
        rc, out = _run_cs()
        print("C# 基线：exit=", rc)
        if rc != 0:
            print("★ C# 基线不是绿的：\n", out[-1500:])
            restore()
            return 99
        for name, path, old, new in CS_MUTATIONS:
            total += 1
            restore()
            src = path.read_text(encoding="utf-8")
            if old not in src:
                print(f"✗ {name}: 找不到待替换文本")
                bad += 1
                continue
            path.write_text(src.replace(old, new, 1), encoding="utf-8")
            rc, out = _run_cs()
            ok = rc != 0
            print(f"{'✓' if ok else '✗'} {name} → exit={rc}")
            if not ok:
                bad += 1
                print("   尾部：", [l for l in out.strip().splitlines() if l.strip()][-4:])
        restore()

    # 还原后逐项复验
    if do_py:
        rc, out = _run_contract()
        print("还原后 Python：exit=", rc)
        if rc != 0:
            print(f"★★ 还原失败！从 {_BAK} 手工恢复")
            return total + 2
    if do_cs:
        rc, out = _run_cs()
        print("还原后 C#：exit=", rc)
        if rc != 0:
            print(f"★★ 还原失败！从 {_BAK} 手工恢复")
            return total + 2

    print()
    if bad == 0:
        print(f"全部变异都被捕获（{total}/{total}）")
        return 0
    print(f"有 {bad} 个变异没被捕获（共 {total} 个）—— 测试需要加强")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
