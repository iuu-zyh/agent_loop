"""脱离游戏的模拟：关窗销毁活体留账，再开自动续聊 + 并发建锁 + 半截 turn 补 + 屉归 SystemPrompt"""

import asyncio
import pathlib
import threading

from agent_loop.agent_loop import AgentLoop
from agent_loop.system_prompt import SystemPrompt
from agent_loop.llm.echo_client import EchoLlmClient

ROOT = "/tmp/agent_loop_test_grill"


def reset():
    # 清残留：ROOT 固定复用，上轮运行留下的 jsonl 会污染「冻结不落盘」断言
    import shutil
    shutil.rmtree(ROOT, ignore_errors=True)
    # llm 显式 Echo：脱离 LLM 回显收口，避免读 env 撞真机
    return AgentLoop.reset_for_test(storage_root=ROOT, llm=EchoLlmClient())


def _write_persona(npc_id: str, text: str):
    """测试辅助：按新责权写法，经文件提供人设（AgentLoop 不再传参）"""
    # 直接写 prompts/personas/{npc_id}.txt，ensure_agent_layer 会读
    # 若 text 为空则删文件走 default.txt
    from pathlib import Path

    p = Path(__file__).parent.parent / "prompts" / "personas" / f"{npc_id}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    if text:
        p.write_text(text, encoding="utf-8")
    else:
        if p.exists():
            p.unlink()


def test_create_and_reuse_same_object():
    loop = reset()
    # 新 API：只递 npc_id，人设由文件提供（此处走 default.txt 的 {npc_name}）
    a1 = loop.create("林婉清")
    try:
        loop.create("林婉清")
        assert False, "should have raised duplicate"
    except RuntimeError as e:
        assert "already registered" in str(e)
    assert loop.get("林婉清") is a1
    print("✓ 同号并发建互斥：同一对象")


def test_close_dispose_and_resume():
    loop = reset()
    agent = loop.create("林婉清")
    agent.send("你好")
    asyncio.run(agent.run_until_idle())
    assert len(agent.session.derive_messages()) >= 2
    loop.dispose("林婉清")
    assert loop.get("林婉清") is None
    assert not loop.path_for("林婉清").exists(), "09-11 冻结语义：未存档不落盘"
    agent2 = loop.create("林婉清")   # 冻结舱复活（内存历史比磁盘新）
    msgs = agent2.session.derive_messages()
    assert any("你好" in str(m) for m in msgs)
    print("✓ 关窗冻结活体（不落盘），再开从冻结舱自动续聊")


def test_concurrent_create_with_lock():
    loop = reset()
    errors = []
    created = []

    def try_create():
        try:
            ag = loop.create("林婉清")
            created.append(ag)
        except RuntimeError as e:
            errors.append(str(e))

    t1 = threading.Thread(target=try_create)
    t2 = threading.Thread(target=try_create)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(created) == 1, f"created {len(created)}"
    assert len(errors) == 1 and "already registered" in errors[0]
    print("✓ 并发建 with lock：只成一个分身")


def test_interrupted_turn_closer():
    loop = reset()
    from agent_loop.session import Session
    from agent_loop.persistence import save_session, load_session

    sess = Session(id="半截子", header={"id": "半截子", "cwd": "/tmp"})
    sess.append("turn/start", {"turn": 1})
    sess.append("step/start", {"turn": 1, "step": 1})
    save_session(sess, ROOT)
    loaded = load_session("半截子", ROOT)
    assert loaded is not None
    assert any(ev["type"] == "turn/end" and ev["data"]["reason"]["kind"] == "interrupted" for ev in loaded.log)
    print("✓ 半截 turn 崩后补 interrupted")


def test_system_prompt_layered():
    loop = reset()
    SystemPrompt.reset_for_test()
    loop2 = AgentLoop.reset_for_test(storage_root=ROOT)
    # 新责权：人设不在 create 参，经文件
    _write_persona("林婉清", "你是林婉清，外冷内热")
    _write_persona("张三", "你是张三，豪爽侠客")
    # 确保新文件被读到（reset 已清 scoped，下次 create 会 ensure）
    a = loop2.create("林婉清")
    b = loop2.create("张三")
    sys_a = a.system_prompt.assemble(scope="林婉清")
    sys_b = b.system_prompt.assemble(scope="张三")
    assert any("林婉清" in s["text"] for s in sys_a["sections"])
    assert any("张三" in s["text"] for s in sys_b["sections"])
    # 改林婉清不影响张三：经 SystemPrompt 侧改屉
    a.system_prompt.dispose_scope("林婉清")
    a.system_prompt.section(name="deployment:persona", text="新林婉清", order=0, scope="林婉清")
    sys_a2 = a.system_prompt.assemble(scope="林婉清")
    sys_b2 = b.system_prompt.assemble(scope="张三")
    assert any("新林婉清" in s["text"] for s in sys_a2["sections"])
    assert any("张三" in s["text"] for s in sys_b2["sections"])
    # 清理 per-NPC 文件
    _write_persona("林婉清", "")
    _write_persona("张三", "")
    print("✓ SystemPrompt 单例分层：各 NPC 屉隔离，热改不串台（屉归 SystemPrompt）")


def test_per_npc_file_and_lazy_assemble():
    """新增：per-NPC 文件 + 懒建 + 变量插值 + 后缀保留"""
    import json

    SystemPrompt.reset_for_test()
    loop = AgentLoop.reset_for_test(storage_root=ROOT)
    _write_persona("萧炎", "你是{character}的萧炎，爱好{hobby}，名叫{npc_name}")
    # traits 文件
    from pathlib import Path

    p = Path(__file__).parent.parent / "prompts" / "traits" / "萧炎.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"character": "冷峻孤高", "hobby": "炼丹"}, ensure_ascii=False), encoding="utf-8")

    agent = loop.create("萧炎")
    prompt = agent.system_prompt.render_prompt(agent.system_prompt.assemble(scope="萧炎"))
    assert "冷峻孤高" in prompt and "炼丹" in prompt and "[输出底线]" in prompt

    # 懒建：不经 create，直接 assemble 也能长屉
    SystemPrompt.reset_for_test()
    sp = SystemPrompt.instance()
    assem = sp.assemble(scope="懒建NPC")
    assert any(s["name"] == "deployment:persona" for s in assem["sections"])
    assert "懒建NPC" in sp.render_prompt(assem)

    # 清理
    _write_persona("萧炎", "")
    if p.exists():
        p.unlink()
    print("✓ per-NPC 文件 + 懒建 + 变量/provider + 后缀保留")


if __name__ == "__main__":
    test_create_and_reuse_same_object()
    test_close_dispose_and_resume()
    test_concurrent_create_with_lock()
    test_interrupted_turn_closer()
    test_system_prompt_layered()
    test_per_npc_file_and_lazy_assemble()
    print("\n全部脱离游戏模拟通过")
