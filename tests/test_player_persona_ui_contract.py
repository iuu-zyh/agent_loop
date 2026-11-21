"""自我人设的 **C# 接线契约**（09-16）。

规则本身（配置归一、注入、差分）在 Python 侧，由 `tests/test_player_persona.py` 跑真实行为；
本文件锁 **C# 面板那条路**是否还按约定接着 —— 这些点在 Unity 里跑不了，只能靠源码断言，
而且每一条都能被"回退实现"打红（见 `scripts/dev/mutate_check_player_persona.py`）。

背景：自我人设挂在**提示词页**（复用现成的多行编辑器 + 保存键），
所以它是一条 **path 前缀 `config:` 的虚拟条目** —— 背后是 config.json 的 `player.persona`，
不是 prompts/ 下的文件。C# 侧三处分流缺一不可：

  ① `SelectFile`  → 虚拟条目**不许**发 `read_prompt`（Python 对 config: 路径 fail-closed 拒绝）
  ② `SavePromptFile` → 虚拟条目**必须**从批量 `write_prompts` 的 files 里剔除
     （Python 的批量写是 fail-fast：混进去会让整页"保存提示词"一起失败）
  ③ `SavePersona` → 走 `set_config`，且**空串照提交**（空 = 清空，是合法表达；
     与数值行的"空 = 不提交"惯例刻意相反，否则用户永远清不掉自己的人设）
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESENTER = ROOT / "csharp" / "UI" / "ConfigPresenter.cs"
SERVER = ROOT / "scripts" / "server.py"
PROMPT_FILES = ROOT / "prompts" / "prompt_files.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """剥 C# 注释：注释里会**引用**被禁止的写法（如 `?.Value<string>()`），不能把它们当成代码。"""
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
# ① 前缀是对外契约：两端必须逐字相同
# ----------------------------------------------------------------------

def test_prefix_matches_python_side():
    from agent_loop.prompts import prompt_files as pf
    cs = _read(PRESENTER)
    m = re.search(r'VirtualConfigPrefix\s*=\s*"([^"]+)"', cs)
    assert m, "ConfigPresenter 里没有 VirtualConfigPrefix 常量"
    assert m.group(1) == pf.VIRTUAL_PREFIX, f"C#/Python 前缀不一致：{m.group(1)!r} vs {pf.VIRTUAL_PREFIX!r}"
    # 虚拟条目的完整 path = 前缀 + 配置键路径（C# 只认前缀，其余原样回传）
    assert pf.PLAYER_PERSONA_PATH == pf.VIRTUAL_PREFIX + "player.persona"


def test_virtual_path_helper_exists_and_is_ordinal():
    body = _method(_read(PRESENTER), "private static bool IsVirtualConfigPath(string path)")
    assert "StartsWith(VirtualConfigPrefix" in body
    assert "StringComparison.Ordinal" in body, "路径前缀比较必须 Ordinal（别用文化相关比较）"
    assert "IsNullOrEmpty" in body, "null/空路径要先挡住（否则 StartsWith 抛）"


# ----------------------------------------------------------------------
# ② 选中虚拟条目：绝不发 read_prompt
# ----------------------------------------------------------------------

def test_select_file_routes_virtual_before_read_prompt():
    body = _method(_read(PRESENTER), "private void SelectFile(string path)")
    assert "IsVirtualConfigPath(path)" in body, "SelectFile 必须认出虚拟条目"
    virtual_at = body.index("if (virtualItem)")
    read_at = body.index('_ws.SendRequest("read_prompt"')
    assert virtual_at < read_at, "虚拟条目的分支必须在 read_prompt **之前**"
    branch = body[virtual_at:read_at]
    assert "FillPersonaEditor();" in branch, "虚拟条目要用缓存值填编辑框"
    assert "return;" in branch, "虚拟条目必须直接返回（不能往下走到 read_prompt）"


def test_persona_editor_prefers_dirty_buffer():
    """脏值优先：编辑框里的未保存内容不能被 get_config 的刷新冲掉。"""
    body = _method(_read(PRESENTER), "private void FillPersonaEditor()")
    assert "_promptCache.TryGetValue(_currentPath, out txt)" in body, "先看未保存的编辑"
    assert "_playerPersona" in body, "回落才是 get_config 的文件真相"


def test_editor_fill_is_shared_helper():
    """两条路（选文件 / 刷新人设）共用同一个收尾：摘监听防回灌、撑高度、回顶部。"""
    body = _method(_read(PRESENTER), "private void SetPromptEditorText(string txt)")
    for needle in ("onValueChanged.RemoveListener", "UpdatePromptLayout()", "PromptScroll"):
        assert needle in body, f"SetPromptEditorText 少了收尾步骤：{needle}"
    assert "SetPromptEditorText(txt);" in _method(_read(PRESENTER), "private void SelectFile(string path)")


# ----------------------------------------------------------------------
# ③ 保存：虚拟条目从批量写里剔除，单独走 set_config
# ----------------------------------------------------------------------

def test_save_prompt_excludes_virtual_from_bulk():
    body = _method(_read(PRESENTER), "private void SavePromptFile()")
    assert "IsVirtualConfigPath(kv.Key)" in body, "批量提交前必须分流"
    loop_at = body.index("foreach (var kv in _promptCache)")
    loop_end = body.index("if (files.Count == 0)")
    loop_block = body[loop_at:loop_end]
    assert "continue;" in loop_block, "虚拟条目要 continue 掉，不能进 files"
    assert "files[kv.Key] = kv.Value" in loop_block, "其余文件照旧批量提交"
    assert '["files"] = files' in body
    # 只改了人设时不能发空的 write_prompts（后端会拒：files 需要非空字典）
    assert "if (files.Count == 0)" in body and "SavePersona(persona, -1)" in body


def test_save_persona_submits_empty_string():
    body = _strip_comments(_method(_read(PRESENTER), "private void SavePersona(string persona, int filesSaved)"))
    assert '["player"]' in body and '["persona"] = persona ?? ""' in body, "空串也要提交（= 清空）"
    assert '"set_config"' in body
    # ★ 「空串照提交」的**结构性**判据：载荷构造 → 发送之间不许有分支/提前返回。
    #   行为层面（真的发出去了）在 WSL 里验不了（要 Unity），所以退而锁"没有任何闸门"：
    #   变异验证里那条"空串 return 掉"就是被这一行抓住的。
    payload_at = body.index('var payload = new JObject { ["player"]')
    send_at = body.index('_ws.SendRequest("set_config"')
    between = body[payload_at:send_at]
    assert "return" not in between and "if (" not in between, (
        "构造载荷到发送之间不许有分支 —— 空串是合法值（= 清空），必须无条件提交：\n" + between)
    for guard in ("persona.Length > 0", "IsNullOrEmpty(persona)", 'persona == ""'):
        assert guard not in body, f"不许对空串设闸（那会让用户永远清不掉人设）：{guard}"
    assert "_promptCache[_currentPath] = _playerPersona;" in body, "保存成功后同步脏值缓存"
    assert "ReportRestartIfNeeded" not in body, "player 是热生效块，不该触发重启编排"


def test_request_config_reads_persona_null_safe():
    """`?.Value<string>()` 在「键存在但值为 JSON null」时会抛 InvalidCastException（本仓库踩过），
    字符串一律用 `.ToString()`（JValue(null).ToString() == ""）。"""
    body = _strip_comments(_method(_read(PRESENTER), "private void RequestConfig()"))
    assert 'data["player"]?["persona"]?.ToString()' in body
    assert "Value<string>()" not in body


# ----------------------------------------------------------------------
# ④ 列表与不可删
# ----------------------------------------------------------------------

def test_file_list_prefers_python_label():
    """虚拟条目的显示名由 Python 下发（`label` 字段）；缺失才回落"分组/文件名"。"""
    body = _method(_read(PRESENTER), "private void RebuildFileList(JArray groups)")
    assert 'item?["label"]?.ToString()' in body
    assert "GroupCn(group)" in body, "回落路径要保留（老端兼容）"


def test_virtual_entry_not_deletable_locally():
    body = _method(_read(PRESENTER), "private static bool IsDeletableLocal(string group, string name)")
    assert 'group == "player"' in body, "本地兜底也要认「虚拟条目不可删」"
    assert "return false" in body


def test_group_and_tip_fallbacks_exist():
    src = _read(PRESENTER)
    assert '"player": return "自我人设"' in src or 'case "player": return "自我人设"' in src, "分组中文名兜底"
    assert 'case "player":' in src, "悬停说明兜底（Python desc 缺失时用）"


# ----------------------------------------------------------------------
# ⑤ 装配期注入 + 配置保存热落地（Python 侧接线）
# ----------------------------------------------------------------------

def test_server_hot_branch_reinstalls_persona():
    src = _read(SERVER)
    assert 'if any(k.startswith("player.") for k in hot):' in src, "set_config 热落地必须认 player.*"
    branch_at = src.index('if any(k.startswith("player.") for k in hot):')
    branch = src[branch_at : branch_at + 900]
    assert "install_player_persona()" in branch, "热落地要重新注入（否则要重启才生效）"
    assert "player_persona()" in branch, "日志要能说清从什么变成什么"


def test_server_injects_at_assembly():
    src = _read(SERVER)
    assert "install_player_persona()" in src
    get_loop_at = src.index("def get_loop()")
    call_at = src.index("install_player_persona()", get_loop_at)
    assert call_at > get_loop_at, "装配期（get_loop 内）要注入一次"
    assert "from agent_loop.agent.system_prompt import SystemPrompt" in src, "server 顶部要导入 SystemPrompt"
