"""config_store + prompt_files + ChatHub 配置 RPC 测试：
set/get 往返 / 白名单拒绝 / 生效判定 / 路径穿越防护 / 新建人设 / ChatHub 路由与未注入报错
"""
from __future__ import annotations

import json
import os

import pytest

from agent_loop import config_store, prompt_files
from agent_loop.ws_channel import ChatHub


# ---------------------------------------------------------------------------
# config_store
# ---------------------------------------------------------------------------

def _write_cfg(tmp_dir, data) -> str:
    path = os.path.join(tmp_dir, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def test_set_get_roundtrip(tmp_path):
    """set_config 写回 → get_config 读回一致；数值键类型归一。"""
    path = _write_cfg(tmp_path, {"llm": {"base_url": "http://old", "api_key": "k", "model": "m"}})
    result = config_store.set_config(
        {"llm": {"base_url": "http://new", "model": "m2", "image": True},
         "network": {"port": "9999"}},
        config_path=path,
    )
    cfg = config_store.get_config(config_path=path)
    assert cfg["llm"]["base_url"] == "http://new"
    assert cfg["llm"]["model"] == "m2"
    assert cfg["llm"]["image"] == {"enabled": True}, "image 接受 bool 归一为 {enabled}"
    assert cfg["network"]["port"] == 9999, "字符串端口归一为 int"
    assert cfg["llm"]["api_key"] == "k", "未动键保留"
    assert result["effective"] == "mixed", "llm+network 同动 → mixed"
    assert sorted(result["updated"]) == ["llm.base_url", "llm.image", "llm.model", "network.port"]
    print("✓ set/get 往返一致 + 类型归一")


def test_set_config_preserves_other_keys(tmp_path):
    """白名单外的既有键（retries/concurrency 等）原样保留。"""
    path = _write_cfg(tmp_path, {"llm": {"base_url": "u", "retries": 3}, "concurrency": {"max_concurrent_turns": 4}})
    config_store.set_config({"llm": {"model": "m"}}, config_path=path)
    raw = json.loads(open(path, encoding="utf-8").read())
    assert raw["llm"]["retries"] == 3
    assert raw["concurrency"]["max_concurrent_turns"] == 4
    print("✓ 写回只动白名单键，其余保留")


def test_set_config_rejects_outside_whitelist(tmp_path):
    """白名单外的块/键一律拒绝（fail-closed）。

    ★09-14 换过例子★：原先拿 `llm.retries` 当"白名单外键"，而它已按用户要求进面板白名单，
    于是这条会静默变成"测了个白名单内的键"（DID NOT RAISE）。
    改用 `compaction.cool_down` —— 它在 config_loader 里是真实键，且**刻意**不进白名单
    （见 config_store.UI_WHITELIST 上方注释），是更稳的样本。
    """
    path = _write_cfg(tmp_path, {"llm": {}})
    with pytest.raises(ValueError):
        config_store.set_config({"compaction": {"cool_down": 5}}, config_path=path)   # 白名单外键
    with pytest.raises(ValueError):
        config_store.set_config({"storage": {"storage_root": "x"}}, config_path=path)  # 白名单外块
    with pytest.raises(ValueError):
        config_store.set_config({}, config_path=path)                            # 空
    print("✓ 白名单外键/块拒绝")


def test_llm_time_keys_are_writable_and_type_normalised(tmp_path):
    """09-14 新增的四个 llm 时间/重试键：可写、按类型归一、且 0 不被夹成别的值。

    0 的语义是「关掉这个上限」（config.example.json 里写明的后门），
    _RANGE_LIMITS 的下界必须是 0 而不是 1 —— 夹到 1 会把用户的显式选择
    悄悄改成"1 秒超时"，比不夹更糟。这条就是钉住那个下界。
    """
    path = _write_cfg(tmp_path, {"llm": {"base_url": "u", "api_key": "k"}})
    config_store.set_config(
        {"llm": {"timeout": "30", "timeout_nonstream": 240, "total_budget": "150.5", "retries": "2"}},
        config_path=path,
    )
    raw = json.loads(open(path, encoding="utf-8").read())["llm"]
    assert raw["timeout"] == 30.0 and isinstance(raw["timeout"], float), "字符串归一为 float"
    assert raw["timeout_nonstream"] == 240.0
    assert raw["total_budget"] == 150.5
    assert raw["retries"] == 2 and isinstance(raw["retries"], int), "retries 归一为 int"

    # 0 = 合法（关掉上限），不得被夹取
    config_store.set_config({"llm": {"timeout": 0, "total_budget": 0}}, config_path=path)
    raw2 = json.loads(open(path, encoding="utf-8").read())["llm"]
    assert raw2["timeout"] == 0, "timeout=0（关掉上限）是合法值，不该被夹到 1"
    assert raw2["total_budget"] == 0
    print("✓ 四个 llm 时间/重试键可写 + 类型归一 + 0 不被夹")


def test_set_config_rejects_corrupt_json(tmp_path):
    """原 JSON 非法 → 拒绝写入（防覆盖丢键），不抛意外类型。"""
    path = os.path.join(tmp_path, "config.json")
    open(path, "w", encoding="utf-8").write("{ bad json !!!")
    with pytest.raises(ValueError):
        config_store.set_config({"llm": {"model": "m"}}, config_path=path)
    print("✓ 坏 JSON 拒绝写入")


def test_effective_field(tmp_path):
    """生效判定：只动 llm → hot；只动装配期块 → restart。"""
    path = _write_cfg(tmp_path, {})
    r1 = config_store.set_config({"llm": {"model": "m"}}, config_path=path)
    # ：initiative.min_interval 改为热生效（ChatHub.min_initiative_interval 是普通属性、
    # 每次主动开口现读，ConfigService 保存时直接改活），故这里换用真正装配期一次性传参的
    # network.port 来钉住 restart 分支；「min_interval 单改 = hot」的断言在 tests/test_config_hot.py。
    r2 = config_store.set_config({"network": {"port": 9999}}, config_path=path)
    assert r1["effective"] == "hot"
    assert r2["effective"] == "restart"
    print("✓ effective: hot / restart / mixed 判定")


def test_set_config_no_change_reports_none(tmp_path):
    """全量提交但值都未变 → effective=none、不重写文件（UI 全量提交也能准确提示）。"""
    path = _write_cfg(tmp_path, {"llm": {"base_url": "u", "model": "m"}, "network": {"port": 8766}})
    before = open(path, encoding="utf-8").read()
    r = config_store.set_config(
        {"llm": {"base_url": "u", "model": "m", "image": False},
         "network": {"port": 8766},
         "initiative": {"min_interval": 30.0},
         "compaction": {"ctx_window": 32768}},
        config_path=path,
    )
    # image/min_interval/ctx_window 文件里原本没有：这些是新值 → 会写入；三者均可热生效 → hot
    assert r["effective"] == "hot"
    after_first = open(path, encoding="utf-8").read()
    # 但只提交与文件一致的键时必须为 none，且不重写文件
    r2 = config_store.set_config({"llm": {"base_url": "u", "model": "m"}, "network": {"port": 8766}}, config_path=path)
    assert r2["effective"] == "none" and r2["updated"] == []
    assert open(path, encoding="utf-8").read() == after_first, "无改动不重写文件"
    print("✓ 无改动 → effective=none 且不重写文件")


def test_hot_key_request_timeout(tmp_path):
    """逐键热生效（09-12）：network.request_timeout / compaction.ctx_window 单改即 hot
    （前者 WsServer 每次现读；后者装配层重建压缩器热挂回），与装配期键同改 → mixed。"""
    path = _write_cfg(tmp_path, {"network": {"request_timeout": 120.0}, "compaction": {"ctx_window": 32768}})

    r1 = config_store.set_config({"network": {"request_timeout": 60.0}}, config_path=path)
    assert r1["effective"] == "hot", "只改超时 → 无需重启"
    assert r1["hot_keys"] == ["network.request_timeout"]

    r2 = config_store.set_config({"network": {"request_timeout": 65.0, "port": 8767}}, config_path=path)
    assert r2["effective"] == "mixed", "超时(热) + 端口(装配期) 同改 → mixed"
    assert sorted(r2["hot_keys"]) == ["network.request_timeout"]

    r3 = config_store.set_config({"compaction": {"ctx_window": 64000}}, config_path=path)
    assert r3["effective"] == "hot" and r3["hot_keys"] == ["compaction.ctx_window"]
    print("✓ 逐键热生效：request_timeout=hot / +port=mixed / ctx_window=hot")


# ---------------------------------------------------------------------------
# prompt_files
# ---------------------------------------------------------------------------

def _make_root(tmp_path):
    """临时 prompts 根（带 sections/personas 的最小结构）。"""
    (tmp_path / "sections").mkdir()
    (tmp_path / "personas").mkdir()
    (tmp_path / "sections" / "world_basis.txt").write_text("旧世界观", encoding="utf-8")
    (tmp_path / "personas" / "default.txt").write_text("默认人设{npc_name}", encoding="utf-8")
    return tmp_path


def test_list_and_read_write_prompts(tmp_path):
    root = _make_root(tmp_path)
    items = prompt_files.list_prompts(root=root)
    paths = [i["path"] for i in items]
    assert "sections/world_basis.txt" in paths and "personas/default.txt" in paths
    assert prompt_files.read_prompt("sections/world_basis.txt", root=root) == "旧世界观"
    prompt_files.write_prompt("sections/world_basis.txt", "新世界观", root=root)
    assert prompt_files.read_prompt("sections/world_basis.txt", root=root) == "新世界观"
    print("✓ 枚举/读/写往返")


def test_write_prompt_create_rules(tmp_path):
    """新建仅限 personas .txt / traits .json / compaction .md；sections 与系统文件拒绝。"""
    root = _make_root(tmp_path)
    prompt_files.write_prompt("personas/张三.txt", "人设", root=root)       # 允许
    prompt_files.write_prompt("traits/张三.json", "{}", root=root)          # 允许
    prompt_files.write_prompt("compaction/张三.md", "模板", root=root)      # 允许
    with pytest.raises(ValueError):
        prompt_files.write_prompt("sections/新段.txt", "x", root=root)       # sections 禁新建
    with pytest.raises(ValueError):
        prompt_files.write_prompt("personas/_suffix.txt", "x", root=root)   # 系统文件禁新建
    with pytest.raises(ValueError):
        prompt_files.write_prompt("personas/李四.json", "x", root=root)      # 扩展名不符
    print("✓ 新建白名单（分组×扩展名）")


def test_path_traversal_rejected(tmp_path):
    """路径穿越/绝对路径/嵌套一律拒绝。"""
    root = _make_root(tmp_path)
    for bad in ("../config.json", "sections/../../x.txt", "/etc/passwd", "a/b/c.txt", "sections/..", ""):
        with pytest.raises((ValueError, FileNotFoundError)):
            prompt_files.read_prompt(bad, root=root)
        with pytest.raises((ValueError, FileNotFoundError)):
            prompt_files.write_prompt(bad, "x", root=root)
    print("✓ 路径穿越防护")


def test_create_persona(tmp_path):
    """新建 NPC 人设：default 底稿 / 重复拒 / 非法 npc_id 拒。"""
    root = _make_root(tmp_path)
    r = prompt_files.create_persona("林婉清", root=root)
    assert r["created"] is True
    content = prompt_files.read_prompt("personas/林婉清.txt", root=root)
    assert content == "默认人设{npc_name}", "以 default.txt 为底稿"
    with pytest.raises(ValueError):
        prompt_files.create_persona("林婉清", root=root)                     # 已存在
    for bad in ("", "_hidden", "a/b", "..", "x" * 100):
        with pytest.raises(ValueError):
            prompt_files.create_persona(bad, root=root)
    print("✓ create_persona：底稿复制 + 非法 npc_id 拒绝")


# ---------------------------------------------------------------------------
# ChatHub 配置 RPC 路由
# ---------------------------------------------------------------------------

class _FakeConfigService:
    def __init__(self):
        self.last_partial = None

    def get_config(self):
        return {"llm": {"base_url": "http://x"}}

    def set_config(self, partial):
        self.last_partial = partial
        return {"updated": ["llm.model"], "effective": "hot"}


class _FakePromptService:
    def list_prompts(self):
        return [{"group": "personas", "name": "default.txt", "path": "personas/default.txt"}]

    def read_prompt(self, rel):
        return f"text-of-{rel}"

    def write_prompt(self, rel, text):
        return {"path": rel, "saved": True}

    def create_persona(self, npc_id, from_default=True):
        return {"path": f"personas/{npc_id}.txt", "created": True}


def _hub(config_service=None, prompt_service=None) -> ChatHub:
    return ChatHub(None, None, config_service=config_service, prompt_service=prompt_service)


def test_hub_config_rpc_routing():
    """handle_request 路由六个新 method；set_config 兼容 {config:{...}} 与平铺两种参数。"""
    cfg, pr = _FakeConfigService(), _FakePromptService()
    hub = _hub(cfg, pr)
    assert hub.loop is None  # 配置 RPC 不依赖活体
    out = asyncio_run(hub.handle_request("get_config", {}))
    assert out == {"llm": {"base_url": "http://x"}}
    out = asyncio_run(hub.handle_request("set_config", {"config": {"llm": {"model": "m"}}}))
    assert out["effective"] == "hot" and cfg.last_partial == {"llm": {"model": "m"}}
    out = asyncio_run(hub.handle_request("set_config", {"llm": {"model": "m2"}}))  # 平铺参数
    assert cfg.last_partial == {"llm": {"model": "m2"}}
    out = asyncio_run(hub.handle_request("list_prompts", {}))
    assert out["groups"][0]["path"] == "personas/default.txt"
    out = asyncio_run(hub.handle_request("read_prompt", {"path": "personas/default.txt"}))
    assert out["text"] == "text-of-personas/default.txt"
    out = asyncio_run(hub.handle_request("write_prompt", {"path": "personas/a.txt", "text": "x"}))
    assert out["saved"] and out["effective"] == "hot"
    out = asyncio_run(hub.handle_request("create_persona", {"npc_id": "张三"}))
    assert out["created"] and out["effective"] == "hot"
    print("✓ ChatHub 路由六个配置 method")


def test_hub_rpc_without_services_raises():
    """服务未注入 → 明确报错（传输层转 ok:false），get_history 不受影响。"""
    hub = _hub()
    with pytest.raises(ValueError):
        asyncio_run(hub.handle_request("get_config", {}))
    with pytest.raises(ValueError):
        asyncio_run(hub.handle_request("list_prompts", {}))
    print("✓ 未注入服务时明确报错")


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        p = os.path.join(d, "a")
        os.makedirs(p)
        test_set_get_roundtrip(p)
    print("\nconfig RPC 测试（部分）通过；完整请用 pytest")

# ---------------------------------------------------------------------------
# ctx_window 热生效的落地点（AgentLoop.rebind_compactor）
# ---------------------------------------------------------------------------

class _FakeCompactor:
    def __init__(self, tag):
        self.tag = tag

    async def maybe(self, *a, **k):
        return None


def test_rebind_compactor_hot_swap(tmp_path):
    """压缩器热换必须同时覆盖「活动」与「冻结舱」两类 agent：
    冻结体复活是同一对象直接出舱（create 的冻结分支 return frozen），漏推就会留着旧压缩器。"""
    from agent_loop.agent_loop import AgentLoop
    from agent_loop.llm.echo_client import EchoLlmClient

    loop = AgentLoop.reset_for_test(storage_root=str(tmp_path), llm=EchoLlmClient(),
                                    compactor=_FakeCompactor("old"), context_window=1000)
    active = loop.create("热换甲")
    frozen = loop.create("热换乙")
    loop.dispose("热换乙")                       # 关窗 → 进冻结舱（同一对象）
    assert active.compactor.tag == "old" and active.stats.context_window == 1000
    assert frozen.compactor.tag == "old"

    new_c = _FakeCompactor("new")
    n = loop.rebind_compactor(new_c, context_window=2000)
    assert n == 2, "活动 + 冻结体都要同步"
    assert loop.compactor is new_c and loop.context_window == 2000
    assert active.compactor is new_c and active.stats.context_window == 2000
    assert frozen.compactor is new_c and frozen.stats.context_window == 2000, "冻结舱不能漏推"
    assert loop.create("热换乙") is frozen, "冻结体复活仍是同一对象（本测试的前提）"

    # 新建 agent 也取管家这份
    fresh = loop.create("热换丙")
    assert fresh.compactor is new_c and fresh.stats.context_window == 2000

    AgentLoop.reset_for_test(storage_root=str(tmp_path), llm=EchoLlmClient())   # 清单例
    print("✓ 压缩器热换：活动/冻结/新建 三类 agent 全部拿到新压缩器与新预算")
