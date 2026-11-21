"""「重置为默认」按钮的 **C# 接线 + 预制件契约**（09-16）。

规则本身（来源唯一、系统件可还原、幂等、换行不动点）在 Python 侧，由
`tests/test_prompt_reset.py` 跑真实行为；两段确认的判定表由 `csharp/tests/TwoStepConfirmTests`
跑净逻辑；本文件锁**接线还接着没有** —— 这些点在 Unity 里跑不了，只能靠源码断言，
而且每一条都能被"回退实现"打红（见 `scripts/dev/mutate_check_prompt_reset.py`）。

三条最容易静默失效的接线（每条都对应一次真踩过的坑）：

  ① **重置成功后必须同步 `_promptCache`**：保存走的是全量提交（`SavePromptFile` 把整个缓存
     write_prompts 上去），不写回缓存 → 用户下次点「保存文件」会把重置**之前**的内容原样写回，
     现象是"重置自己反弹了"。删除路径的 `_promptCache.Remove` 是同一个坑的另一半。
  ② **自我人设（`config:` 虚拟条目）必须走 set_config 清空，不许发 reset_prompt**：
     Python 对 `config:` 路径显式拒绝，发过去只会得到一句看不懂的报错。
  ③ **列宽四份定义必须一致**：预制件（进游戏的唯一真相）、ConfigUiBuilder（参考实现）、
     补丁脚本的常量、`DisarmReset` 的按钮文案 —— 任何一处走偏，实机上只是"按钮挤在一起/
     名字不对"，没有异常日志可查。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PRESENTER = ROOT / "csharp" / "UI" / "ConfigPresenter.cs"
REFS = ROOT / "csharp" / "UI" / "ConfigPanelRefs.cs"
AB_PANEL = ROOT / "csharp" / "UI" / "AbConfigPanel.cs"
BUILDER = ROOT / "csharp" / "UI" / "ConfigUiBuilder.cs"
TWO_STEP = ROOT / "csharp" / "UI" / "TwoStepConfirm.cs"
PATCH_SCRIPT = ROOT / "scripts" / "dev" / "prefab_patch_prompt_reset.py"
PREFAB = Path("/mnt/e/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/"
              "资源修改教程/ResBuildABProject/Assets/Resources/UI/UIConfigAi.prefab")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """剥 C# 注释：注释里会**引用**被禁止的写法，不能把它们当成代码。"""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _method(src: str, signature: str) -> str:
    """按大括号配对取一个 C# 成员的正文（含花括号）。签名必须唯一。"""
    assert src.count(signature) == 1, f"签名不唯一（{src.count(signature)} 次）：{signature}"
    i = src.index(signature)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j : k + 1]
    raise AssertionError("大括号不配对：" + signature)


# ----------------------------------------------------------------------
# ① 缓存同步 + 虚拟条目分流
# ----------------------------------------------------------------------

def test_reset_syncs_prompt_cache():
    """重置成功后必须把新文本写回 `_promptCache`（否则下次全量保存把旧内容写回）。"""
    body = _strip_comments(_method(_read(PRESENTER), "private void ResetCurrentFile()"))
    assert "_promptCache[path] = txt" in body, \
        "重置成功分支必须同步缓存 —— 保存是全量提交，不同步就会把重置前的内容写回去"
    assert "SetPromptEditorText(txt)" in body, "编辑框要立刻显示重置后的文本（response 里带回来了，不用再读一趟）"
    # 结构：同步缓存必须与"取回 text"发生在同一段（成功分支），不许只在别处出现
    ok_idx = body.index("_ws.SendRequest(\"reset_prompt\"")
    assert body.index("_promptCache[path] = txt") > ok_idx, "缓存同步必须在响应回调里"


def test_virtual_item_clears_via_set_config_not_reset_prompt():
    """自我人设不是文件：重置 = 清空 → 走 set_config；**绝不**发 reset_prompt。"""
    body = _strip_comments(_method(_read(PRESENTER), "private void ResetCurrentFile()"))
    assert "IsVirtualConfigPath(path)" in body
    assert 'SavePersona("", -1)' in body, "虚拟条目要复用既有的 set_config 人设保存路径（空串照提交那条约定）"
    # 虚拟分支必须在发 RPC 之前 return，且该分支内不得出现 reset_prompt
    v_idx = body.index("if (virtualItem)")
    ret_idx = body.index("return;", v_idx)
    rpc_idx = body.index('_ws.SendRequest("reset_prompt"')
    assert ret_idx < rpc_idx, "虚拟条目必须在发 reset_prompt 之前就返回"
    assert "reset_prompt" not in body[v_idx:ret_idx], "虚拟条目分支里不许出现 reset_prompt"
    assert "SetPromptEditorText(\"\")" in body[v_idx:ret_idx], "清空时编辑框也要跟着空"
    assert "_promptCache[path] = \"\"" in body[v_idx:ret_idx], "清空也要同步缓存"


def test_reset_checks_resettable_flag_before_arming():
    """没有内置默认版本的文件（用户新建件）在本地就拦下并给出可照做的提示。"""
    body = _strip_comments(_method(_read(PRESENTER), "private void ResetCurrentFile()"))
    assert "_resettable.TryGetValue(path, out can)" in body
    assert "can = true" in body, "缺字段时乐观为 true（权威判定在 Python；误判成不可用会灰掉本来能重置的文件）"
    assert "删除文件" in body, "拒绝时要引导用户去用删除键"


def test_resettable_flag_read_is_null_safe():
    """`?.Value<bool>()` 遇到显式 JSON null 会抛 InvalidCastException（本仓库踩过），
    所以 resettable 一律经 ToString 判，且缺键回落乐观 true。"""
    src = _strip_comments(_read(PRESENTER))
    m = re.search(r'_resettable\[path\] = ([^;]+);', src)
    assert m, "找不到 _resettable 赋值"
    expr = m.group(1)
    assert "Value<bool>" not in expr, "不许用 ?.Value<bool>()（JSON null 会抛）"
    assert '.ToString() != "False"' in expr
    assert "rtToken == null" in expr


# ----------------------------------------------------------------------
# ② 两段确认（复用 TwoStepConfirm，与删除同款）
# ----------------------------------------------------------------------

def test_both_destructive_buttons_use_two_step_confirm():
    src = _strip_comments(_read(PRESENTER))
    assert "readonly TwoStepConfirm _deleteConfirm" in src and "readonly TwoStepConfirm _resetConfirm" in src
    assert "_deleteConfirm.Click(Time.time)" in src, "删除也必须走同一个判定类（别退回内联）"
    assert "_resetConfirm.Click(Time.time)" in src
    # 旧的散装状态不许复活
    assert "_deleteArmed" not in src and "_deleteArmTime" not in src, "旧的内联武装状态应已删除"


def test_two_step_confirm_class_is_unity_free():
    """判定类必须零 Unity 依赖，否则 net8.0 单测编不进去（那 45 项断言就白写了）。"""
    src = _read(TWO_STEP)
    # 按**真实 using 指令**判，不按全文找字符串 —— 类头注释里正写着"刻意不 using UnityEngine"
    assert not re.search(r"(?m)^\s*using\s+UnityEngine", src), "TwoStepConfirm 不许引 Unity"
    assert "TwoStepConfirm" in src and "internal enum Step" in src
    assert "Step.Expired" in src and "Step.Second" in src and "Step.First" in src


def test_select_file_disarms_reset():
    body = _strip_comments(_method(_read(PRESENTER), "private void SelectFile(string path)"))
    assert "DisarmReset()" in body, "换文件必须作废重置武装（防'在 A 上武装、在 B 上执行'）"
    assert "DisarmDelete()" in body


def test_disarm_reset_restores_idle_label():
    body = _strip_comments(_method(_read(PRESENTER), "private void DisarmReset()"))
    assert "_resetConfirm.Disarm()" in body
    assert '"重置为默认"' in body, "空闲态文案必须与预制件里的 m_Text 一致"


def test_reset_button_attach_is_null_safe():
    """老 AB 没有 ResetPromptBtn 时，只应"没有这个功能"，不许影响其它接线。"""
    body = _strip_comments(_method(_read(PRESENTER), "public void Init(ConfigPanelRefs refs, WsClient ws)"))
    assert "if (_refs?.ResetPromptButton != null)" in body
    assert "ClickUtils.Attach(GetButton(_refs.ResetPromptButton), ResetCurrentFile)" in body


def test_unknown_method_is_translated_for_upgrade_skew():
    """新 DLL + 旧 exe：Python 抛 unknown method，要翻成"该升级 exe"这种能照做的话。"""
    body = _strip_comments(_method(_read(PRESENTER), "private void ResetCurrentFile()"))
    assert "unknown method" in body and "AgentLoopServer.exe" in body


def test_tooltip_registered_after_list_rebuild_clear():
    """气泡登记必须写在 RegisterFormTooltips 里 —— RebuildFileList 会 _tip.Clear() 后重登记。"""
    src = _strip_comments(_read(PRESENTER))
    body = _method(src, "private void RegisterFormTooltips()")
    assert "_refs?.ResetPromptButton" in body and "_tip?.Register(UiRects.Of(_refs.ResetPromptButton.transform)" in body


# ----------------------------------------------------------------------
# ③ 路径 / 列宽 / 文案：四份定义必须一致
# ----------------------------------------------------------------------

def test_node_path_contract_matches_everywhere():
    refs_src = _strip_comments(_read(REFS))
    ab_src = _strip_comments(_read(AB_PANEL))
    builder_src = _strip_comments(_read(BUILDER))
    patch_src = _read(PATCH_SCRIPT)
    assert "public GameObject ResetPromptButton;" in refs_src
    assert '"BG/PagePrompt/ResetPromptBtn"' in ab_src
    assert '"ResetPromptBtn"' in builder_src
    assert 'TARGET_GO = "ResetPromptBtn"' in patch_src


def test_column_layout_matches_prefab_patch_script():
    """列宽四份定义一致：预制件补丁 / 代码构建器 / 文案常量。"""
    builder = _strip_comments(_read(BUILDER))
    patch = _read(PATCH_SCRIPT)
    # 关键点：右半的三分之一 = 全宽的 1/6（写成 1/3 会把删除键推到面板外）
    assert "const float ThirdOfRight = 1f / 6f;" in builder
    for token in ('ANCHOR_NEW_MAX_X = "{x: 0.6666667, y: 0}"',
                  'ANCHOR_RESET_MIN_X = "{x: 0.6666667, y: 0}"',
                  'ANCHOR_RESET_MAX_X = "{x: 0.8333333, y: 0}"',
                  'ANCHOR_DELETE_MIN_X = "{x: 0.8333333, y: 0}"',
                  'ANCHOR_SAVE_MAX_X = "{x: 0.5, y: 0}"'):
        assert token in patch, "补丁脚本缺常量：" + token
    assert "0.5f + ThirdOfRight" in builder and "0.5f + 2f * ThirdOfRight" in builder
    # 保存键独占左半（它的文案长），删除键顶到最右
    assert "spRt.anchorMax = new Vector2(0.5f, 0f);" in builder
    assert "dbRt.anchorMax = new Vector2(1f, 0f);" in builder


def test_button_text_and_color_match():
    builder = _strip_comments(_read(BUILDER))
    presenter = _strip_comments(_read(PRESENTER))
    patch = _read(PATCH_SCRIPT)
    assert "重置为默认" in builder, "代码构建器要给出同样的按钮文案"
    assert "重置为默认" in presenter
    assert 'TARGET_TEXT = "重置为默认"' in patch
    assert 'TARGET_COLOR = "{r: 0.55, g: 0.33, b: 0.05, a: 1}"' in patch
    assert "new Color(0.55f, 0.33f, 0.05f, 1f)" in builder, "暗琥珀：与保存的墨黑、删除的警示红区分开"


def test_diag_line_reports_optional_prompt_nodes():
    """可选节点存在性要进日志：'预制件没打好'与'代码路径接错'在实机上表现一样（按钮不出现）。"""
    src = _strip_comments(_read(AB_PANEL))
    assert "ResetPromptBtn=" in src, "AbConfigPanel 的装配日志要点出这个可选节点"


# ----------------------------------------------------------------------
# ④ 预制件真实产物（AB 工程不在本机时跳过）
# ----------------------------------------------------------------------

@pytest.mark.skipif(not PREFAB.is_file(), reason="AB 打包工程不在这台机器上")
def test_patched_prefab_has_node_and_verifies():
    """真预制件里节点在、几何对、自校验过 —— 这是"重打 AB 之后按钮会出现"的唯一凭据。"""
    import sys
    sys.path.insert(0, str(ROOT / "scripts" / "dev"))
    import prefab_patch_prompt_reset as M

    text = _read(PREFAB)
    assert M.verify(text) is True, "补丁脚本的自校验没过"
    pf = M.Prefab(text)
    go = pf.find_go("ResetPromptBtn")
    assert go, "预制件里没有 ResetPromptBtn —— 补丁没生效？"
    assert pf.path_of(go) == "/UIConfigAi/BG/PagePrompt/ResetPromptBtn"
    # 幂等：再补一次必须一个字节都不变
    assert M.patch(text) == text, "补丁不幂等"
    # 文案/颜色（真预制件里是 Unity 的 \\uXXXX 转义）
    label_go = re.search(r"(?m)^  m_GameObject: \{fileID: (\d+)\}",
                         pf.doc[pf.children(pf.rt_of_go(go))[0]]).group(1)
    txt = None
    for c in M.RE_COMPONENT_REF.findall(pf.doc[label_go]):
        if M.is_text_comp(pf, c):
            txt = c
    assert pf.field(txt, "m_Text") == M.yaml_str("重置为默认")
    assert pf.field(txt, "m_Color") == M.TARGET_COLOR
