"""自我人设（config.player.persona → system 的一段）行为测试（09-16）。

**这是什么**：玩家为自己写的人物设定，作为 system 的一段注入（段名 `config:player_persona`，
标签「自我人设」）。与 L1 上下文里的「玩家」段**分工不同**：那是 C# 从存档读出来的游戏事实
（姓名/修为/宗门/关系/位置），这是玩家自述。

**本文件锁四件事**：
  A. 配置层：默认空 / 写回 / **空串照写回**（不是删键）/ 类型与 4000 字上限 fail-closed / 热生效
  B. 提示词层：空 = 段**根本不存在**（零回归：与不启用本功能时逐字节相同）/ 模板包裹 / order=-70 /
     清空 = 移除 / 「系统组装」标签
  C. 差分：真跑一回合，看**上行给 UI 的 sys 帧** —— 首步列一次、改一次只上行这一段、清空后不再出现
  D. 提示词页的虚拟条目（`config:player.persona`）：排第一、不可删、文件接口显式拒绝

C 段为什么值得跑真回合：差分是"用户看得见的行为"（「系统组装」折叠块里出现/不出现那一行），
只断言 `assemble()` 的段清单测不到它 —— 得让 `dialogue_agent._step` 真的吐一次 sys 帧。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from agent_loop.config import config_loader, config_store

from agent_loop.prompts import prompt_files
from agent_loop.agent_loop import AgentLoop
from agent_loop.transport.bridge import StubGameBridge
from agent_loop.llm.echo_client import EchoLlmClient
from agent_loop.agent.system_prompt import SystemPrompt

NPC = "自我人设测试NPC"
PERSONA = "我是青云剑宗外门弟子，性情孤僻，不喜寒暄；称呼我「道友」即可。"
PERSONA2 = "我是白帝城的一名散修，嗜酒，说话直。"


def _cfg_path(tmp_path) -> Path:
    return tmp_path / "config.json"


def _tmp_prompts(tmp_path) -> Path:
    """最小 prompts 结构（prompt_files 只枚举已存在的目录）。"""
    root = tmp_path / "prompts"
    (root / "sections").mkdir(parents=True, exist_ok=True)
    (root / "sections" / "world_basis.txt").write_text("旧世界观", encoding="utf-8")
    (root / "personas").mkdir(exist_ok=True)
    (root / "personas" / "default.txt").write_text("你是{npc_name}。", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _fresh_singleton():
    """每个测试都从"没有人设"的干净单例开始（SystemPrompt 是进程单例）。"""
    SystemPrompt.reset_for_test()
    yield
    SystemPrompt.reset_for_test()


# ---------------------------------------------------------------------------
# A. 配置层
# ---------------------------------------------------------------------------

def test_default_block_is_empty():
    """默认空 = 不注入。这是"零回归"的前提：模板与老 config 都没有这个块。"""
    assert config_loader.DEFAULT_CONFIG["player"]["persona"] == ""
    assert config_loader.load_config(config_path="/nonexistent/config.json")["player"]["persona"] == ""
    print("✓ 默认 player.persona 为空")


def test_set_get_roundtrip_and_hot(tmp_path):
    """写回 → 读回一致；`player` 是热生效块（改完不必重启 Python/游戏）。"""
    path = _cfg_path(tmp_path)
    r = config_store.set_config({"player": {"persona": PERSONA}}, config_path=path)
    assert r["updated"] == ["player.persona"], r
    assert r["effective"] == "hot", "自我人设必须热生效（下个回合的 system 就变）"
    assert r["hot_keys"] == ["player.persona"], r
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["player"]["persona"] == PERSONA
    assert config_store.get_config(config_path=path)["player"]["persona"] == PERSONA
    print("✓ 写回/读回一致 + hot")


def test_empty_string_is_written_back_not_deleted(tmp_path):
    """空串是**合法值**（= 不注入），照原样写回 —— 与 llm.headers 的"空 = 删键"刻意相反：
    删键会让玩家在 config.json 里看不出自己设过什么；而"清空"必须真的能存下来。"""
    path = _cfg_path(tmp_path)
    config_store.set_config({"player": {"persona": PERSONA}}, config_path=path)
    r = config_store.set_config({"player": {"persona": ""}}, config_path=path)
    assert r["updated"] == ["player.persona"], "空串必须算一次真改动（否则 UI 会以为没保存）"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "persona" in raw["player"], "空串不是删键"
    assert raw["player"]["persona"] == ""
    print("✓ 空串照写回（键仍在）")


def test_whitespace_normalized(tmp_path):
    """CRLF/CR → LF、首尾去净；纯空白 = 空（不注入）。"""
    path = _cfg_path(tmp_path)
    config_store.set_config({"player": {"persona": "  甲\r\n乙\r丙  "}}, config_path=path)
    assert json.loads(path.read_text(encoding="utf-8"))["player"]["persona"] == "甲\n乙\n丙"
    config_store.set_config({"player": {"persona": "   \n\t "}}, config_path=path)
    assert json.loads(path.read_text(encoding="utf-8"))["player"]["persona"] == ""
    print("✓ 换行归一 + 纯空白 → 空")


def test_type_and_length_fail_closed(tmp_path):
    """非字符串 / 超 4000 字一律拒收（**不静默截断**：截断会把玩家写的人设从中间切断）。"""
    path = _cfg_path(tmp_path)
    with pytest.raises(ValueError):
        config_store.set_config({"player": {"persona": 123}}, config_path=path)
    with pytest.raises(ValueError) as ei:
        config_store.set_config({"player": {"persona": "字" * (config_store._PERSONA_MAX + 1)}}, config_path=path)
    assert "4000" in str(ei.value), "超长报错要点明上限"
    config_store.set_config({"player": {"persona": "字" * config_store._PERSONA_MAX}}, config_path=path)  # 边界内通过
    assert len(config_store.get_config(config_path=path)["player"]["persona"]) == 4000
    with pytest.raises(ValueError):
        config_store.set_config({"player": {"nickname": "x"}}, config_path=path)   # 白名单外键
    print("✓ 类型/长度/白名单 fail-closed")


# ---------------------------------------------------------------------------
# B. 提示词层
# ---------------------------------------------------------------------------

def test_not_injected_by_default_is_byte_identical():
    """★ 零回归：没人设时 system 里根本没有这一段 —— 与"从不启用本功能"逐字节相同。"""
    sp = SystemPrompt.instance()
    a = sp.assemble()
    assert SystemPrompt.PLAYER_PERSONA_SECTION not in {s["name"] for s in a["sections"]}
    baseline = sp.render_prompt(a)
    sp.set_player_persona("")                      # 显式设成空，也不该改变任何东西
    assert sp.render_prompt(sp.assemble()) == baseline, "空人设不得在 system 里多出空行/空段"
    print("✓ 默认不注入，且与基线逐字节相同")


def test_injected_text_and_order():
    """注入：模板包裹 + 原文在内 + order=-70（排在 NPC 专属人设**之前**、人设规则**之后**）。"""
    sp = SystemPrompt.instance()
    sp.ensure_agent_layer(NPC)                     # 让 deployment:persona(0) 也在场，检验相对位置
    assert sp.set_player_persona("  " + PERSONA + "  ") is True
    a = sp.assemble(scope=NPC)
    secs = [s["name"] for s in a["sections"]]
    assert SystemPrompt.PLAYER_PERSONA_SECTION in secs
    order = {s["name"]: s["order"] for s in a["sections"]}
    assert order["world:persona_rules"] < order[SystemPrompt.PLAYER_PERSONA_SECTION] < order["deployment:persona"], order
    text = sp.render_prompt(a)
    assert "[玩家自述]" in text and PERSONA in text, "模板与原文都要在"
    assert "以「玩家」段为准" in text, "冲突裁决规则必须写进去（自封身份 vs 游戏事实）"
    assert sp.player_persona() == PERSONA, "原文首尾已去净"
    print("✓ 注入：模板 + 原文 + order -70")


def test_clear_removes_section():
    """清空 = 移除段（而不是留一段空文本）。"""
    sp = SystemPrompt.instance()
    sp.set_player_persona(PERSONA)
    assert SystemPrompt.PLAYER_PERSONA_SECTION in sp.global_layer.sections
    assert sp.set_player_persona("   ") is False
    assert SystemPrompt.PLAYER_PERSONA_SECTION not in sp.global_layer.sections
    assert sp.player_persona() == ""
    print("✓ 清空即移除")


def test_reload_global_sections_keeps_persona():
    """重载 prompts/ 文件（reload_global_sections）不该顺手抹掉玩家自述 —— 它不是文件段。"""
    sp = SystemPrompt.instance()
    sp.set_player_persona(PERSONA)
    sp.reload_global_sections()
    assert SystemPrompt.PLAYER_PERSONA_SECTION in sp.global_layer.sections
    print("✓ 文件段重载不影响自我人设")


def test_sys_label_present():
    """「系统组装」折叠块里的那一行标题。缺了会显示裸段名 `config:player_persona`。"""
    assert SystemPrompt._SYS_LABELS.get(SystemPrompt.PLAYER_PERSONA_SECTION) == "自我人设"
    print("✓ 标签「自我人设」")


def test_variables_usable_inside_persona():
    """自述里可以用 `{变量}`（如 {npc_name}）—— 走 render_prompt 的既有插值，不额外做任何事；
    未定义的 `{...}` 原样保留（不会被吃掉）。"""
    sp = SystemPrompt.instance()
    sp.ensure_agent_layer(NPC)
    sp.set_player_persona("我是{npc_name}的旧识。暗号{未定义变量}")
    text = sp.render_prompt(sp.assemble(scope=NPC))
    assert f"我是{NPC}的旧识" in text
    assert "{未定义变量}" in text
    print("✓ 变量插值可用，未定义变量原样保留")


# ---------------------------------------------------------------------------
# C. 差分（真跑一回合，看上行给 UI 的 sys 帧）
# ---------------------------------------------------------------------------

def _make_agent(tmp_path, npc):
    """建一个能真跑回合、收集 on_step 帧的 agent（EchoLlmClient：单步、无工具）。

    ⚠ 顺序要紧：`AgentLoop.reset_for_test` 会**重置 SystemPrompt 单例**（连带抹掉自我人设），
    所以人设必须在它之后注入 —— 这正是真实装配期的顺序（server.get_loop 先建 AgentLoop，
    再 install_player_persona）。"""
    bridge = StubGameBridge()
    bridge.seed_unit(npc, {"realm": "金丹", "sect": "化神殿", "pos": "永宁州", "mood": 70,
                           "player": {"name": "韩立", "realm": "筑基", "same_grid": True},
                           "now": {"year": 1, "month": 3, "day": 5, "text": "1年3月5日"}})
    loop = AgentLoop.reset_for_test(storage_root=str(tmp_path / "sess"), bridge=bridge, llm=EchoLlmClient())
    return loop.create(npc)


def _turn(agent, text) -> list:
    """跑一个回合并返回该回合的 on_step 帧（同一个 agent 可反复跑：差分状态累积在它身上）。"""
    frames = []
    agent.on_step = lambda info: frames.append(info)
    agent.send(text)

    async def _go():
        await agent.run_until_idle()

    asyncio.run(_go())
    return frames


def _sys_parts(frames, turn=None) -> list:
    """一回合里所有 sys 帧的 parts（可只看某一回合）。"""
    out = []
    for f in frames:
        if f.get("kind") != "sys":
            continue
        if turn is not None and f.get("turn") != turn:
            continue
        out.extend(f.get("parts") or [])
    return out


def test_diff_persona_appears_then_only_it_changes(tmp_path):
    """★ 差分三连（用户问的就是这件事）：
      ① 首个回合：自我人设作为**一段**被列出来（标签「自我人设」）；
      ② 改了人设：下一个回合**只上行这一段**（其它段没变就不发，不刷屏）；
      ③ 清空：下一个回合**不再出现**（sys 没有 ctx 那种「（已移除）」行）。
    """
    agent = _make_agent(tmp_path, NPC)
    SystemPrompt.instance().set_player_persona(PERSONA)
    p1 = _sys_parts(_turn(agent, "你好"))
    mine = [p for p in p1 if p["name"] == SystemPrompt.PLAYER_PERSONA_SECTION]
    assert len(mine) == 1, f"首个回合应恰好列一次自我人设，实际={[p['name'] for p in p1]}"
    assert mine[0]["label"] == "自我人设", mine[0]
    assert mine[0]["text"].startswith("[玩家自述]") and PERSONA in mine[0]["text"]
    assert len(p1) > 1, "首个回合应当把其它段也列全（这是既有语义）"

    # ② 改人设 → 新回合只上行自我人设
    SystemPrompt.instance().set_player_persona(PERSONA2)
    p2 = _sys_parts(_turn(agent, "再说一句"))
    assert [p["name"] for p in p2] == [SystemPrompt.PLAYER_PERSONA_SECTION], \
        f"改人设后应当只有这一段上行，实际={[p['name'] for p in p2]}"
    assert PERSONA2 in p2[0]["text"]

    # ③ 清空 → 不再出现
    SystemPrompt.instance().set_player_persona("")
    p3 = _sys_parts(_turn(agent, "第三句"))
    assert SystemPrompt.PLAYER_PERSONA_SECTION not in {p["name"] for p in p3}, p3
    print("✓ 差分：首回合列一次 / 改后只上行这一段 / 清空后不再出现")


# ---------------------------------------------------------------------------
# D. 提示词页的虚拟条目
# ---------------------------------------------------------------------------

def test_virtual_entry_first_and_not_deletable(tmp_path):
    """列表第一条是「自我人设（玩家）」，不可删，且带 label（C# 侧优先用它当显示名）。"""
    items = prompt_files.list_prompts(root=_tmp_prompts(tmp_path))
    first = items[0]
    assert first["path"] == prompt_files.PLAYER_PERSONA_PATH
    assert first["group"] == "player" and first["label"] == "自我人设（玩家）"
    assert first["deletable"] is False, "它不是文件，删不掉（清空 = 清空编辑框再保存）"
    assert "player.persona" in first["desc"] and "4000" in first["desc"], "说明要交代存哪、上限多少"
    # 两种视图都给（它是全局的，不属于任何 NPC）
    assert prompt_files.list_prompts(root=_tmp_prompts(tmp_path), npc_id=NPC)[0]["path"] \
        == prompt_files.PLAYER_PERSONA_PATH
    print("✓ 虚拟条目：排第一 / 不可删 / 两种视图都在")


def test_virtual_path_rejected_by_file_apis(tmp_path):
    """文件接口对 `config:` 路径**显式拒绝**并说清该走哪条路 —— 免得有人以为
    prompts/ 里能放一份"自我人设文件"，或者 C# 侧把虚拟路径混进批量保存。"""
    root = _tmp_prompts(tmp_path)
    rel = prompt_files.PLAYER_PERSONA_PATH
    for fn in (lambda: prompt_files.read_prompt(rel, root=root),
               lambda: prompt_files.write_prompt(rel, "x", root=root)):
        with pytest.raises(ValueError) as ei:
            fn()
        assert "config.json" in str(ei.value) and "player.persona" in str(ei.value), ei.value
    assert prompt_files.is_deletable(rel, root=root) is False
    with pytest.raises(ValueError):
        prompt_files.delete_prompt(rel, root=root)
    print("✓ 文件接口对虚拟路径显式拒绝")


def test_virtual_prefix_is_a_cross_language_contract():
    """`config:` 前缀是 C#（ConfigPresenter.VirtualConfigPrefix）与 Python 的**对外契约**：
    两边必须逐字相同，否则虚拟条目会被当成文件发给 read_prompt → 面板报错。"""
    cs = (Path(__file__).resolve().parent.parent / "csharp" / "UI" / "ConfigPresenter.cs").read_text(encoding="utf-8")
    assert f'VirtualConfigPrefix = "{prompt_files.VIRTUAL_PREFIX}"' in cs, "C# 侧前缀与 Python 不一致"
    print("✓ 前缀契约两端一致")


# ---------------------------------------------------------------------------
# E. 装配期注入 + 配置保存热落地（走真实 ConfigService，不是源码断言）
# ---------------------------------------------------------------------------

import importlib.util  # noqa: E402

_SERVER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "server.py"


@pytest.fixture(scope="module")
def server_mod():
    """按文件路径加载 scripts/server.py（scripts/ 不是包，沿用 test_config_hot 的做法）。"""
    spec = importlib.util.spec_from_file_location("_server_under_test_persona", _SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_configservice_hot_installs_and_clears_persona(server_mod, tmp_path, monkeypatch):
    """★ 保存即生效：`set_config` 的 `player.*` 热落地分支把新文本**重新注入** SystemPrompt
    （不必重启 Python，更不必重启游戏）；清空则移除该段。

    只把读/写盘重定向到临时 config.json，热落地逻辑走原实现；日志重配关掉
    （否则真跑会把全局日志管道切到仓库 logs/）。
    """
    path = tmp_path / "config.json"
    real_set = config_store.set_config
    monkeypatch.setattr(server_mod, "load_config", lambda *a, **k: config_loader.load_config(config_path=path))
    monkeypatch.setattr(config_store, "set_config", lambda partial, *a, **k: real_set(partial, config_path=path))
    monkeypatch.setattr(server_mod.log_setup, "reconfigure", lambda *a, **k: {})

    svc = server_mod.ConfigService()
    r = svc.set_config({"player": {"persona": PERSONA}})
    assert r["effective"] == "hot", r
    sp = SystemPrompt.instance()
    assert sp.player_persona() == PERSONA, "保存后单例里就是新文本"
    assert SystemPrompt.PLAYER_PERSONA_SECTION in sp.global_layer.sections

    svc.set_config({"player": {"persona": ""}})
    assert sp.player_persona() == ""
    assert SystemPrompt.PLAYER_PERSONA_SECTION not in sp.global_layer.sections, "清空要移除段"

    # 装配期注入口径一致：install_player_persona 直接读 config.json
    real_set({"player": {"persona": PERSONA}}, config_path=path)
    assert server_mod.install_player_persona() is True
    assert SystemPrompt.instance().player_persona() == PERSONA
    print("✓ ConfigService 热落地 + 装配期注入")
