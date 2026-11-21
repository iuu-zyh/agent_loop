# -*- coding: utf-8 -*-
"""「重置为默认」提示词文件（09-16）：
- prompt_files.reset_prompt / is_resettable：**来源唯一 = 内置副本**、系统件可还原、
  用户新增件拒绝、路径穿越拒绝、缺失文件/缺失分组可补回、字节幂等（changed 标记）；
- textio 读写换行契约（LF 不动点）—— 这是"重置"能字节幂等的前提；
- list_prompts 下发 resettable 标记；
- ChatHub reset_prompt RPC 路由（含旧 mock 服务不支持的明确报错）。

测试要点：**用户副本与内置副本必须是两个不同内容的目录**（生产就是如此），
否则测不出"来源拿错"这类静默失效（拿错时重置会退化成自我拷贝，一切看起来都正常）。
"""

import inspect
import pathlib
import shutil

import pytest

from agent_loop.config import paths, textio
from agent_loop.prompts import prompt_files
from agent_loop.prompts.prompt_files import is_resettable, list_prompts, reset_prompt


def _defaults(tmp_path):
    """造一个"内置副本"（内容与用户副本刻意不同）。

    ★ `newline="\\n"` 必须显式给：本功能的核心契约是 **LF 不动点**（"重置 = 与内置副本
    逐字节一致"，见 `reset_prompt` 的字节幂等断言）。而 `Path.write_text` 在 Windows 上
    默认 `newline=None` ⇒ 写入的 `\\n` 会被翻译成 `os.linesep`（`\\r\\n`），于是"内置副本"
    变成 CRLF、与 reset 写出的 LF 逐字节不等 —— 测试**在自己的造数上**失败，
    而生产的内置副本（打包文件）本来就是 LF。造数失真，不是功能失真。
    """
    d = tmp_path / "bundled" / "prompts"
    (d / "sections").mkdir(parents=True)
    (d / "personas").mkdir()
    (d / "traits").mkdir()
    (d / "compaction").mkdir()
    (d / "sections" / "world_basis.txt").write_text("出厂世界观\n第二行\n", encoding="utf-8", newline="\n")
    (d / "sections" / "harness_identity.txt").write_text("出厂框架身份", encoding="utf-8", newline="\n")
    (d / "personas" / "default.txt").write_text("出厂默认人设{npc_name}", encoding="utf-8", newline="\n")
    (d / "personas" / "_suffix.txt").write_text("出厂尾部追加", encoding="utf-8", newline="\n")
    (d / "compaction" / "default.md").write_text("出厂压缩指令", encoding="utf-8", newline="\n")
    return d


def _root(tmp_path):
    """造一个"用户副本"：同名文件内容被改坏 + 一个用户新增件。"""
    r = tmp_path / "user" / "prompts"
    (r / "sections").mkdir(parents=True)
    (r / "personas").mkdir()
    (r / "traits").mkdir()
    (r / "compaction").mkdir()
    (r / "sections" / "world_basis.txt").write_text("被玩家改坏的世界观", encoding="utf-8")
    (r / "sections" / "harness_identity.txt").write_text("被玩家人工改过", encoding="utf-8")
    (r / "personas" / "default.txt").write_text("改过的默认人设", encoding="utf-8")
    (r / "personas" / "_suffix.txt").write_text("改过的尾部", encoding="utf-8")
    (r / "compaction" / "default.md").write_text("改过的压缩指令", encoding="utf-8")
    (r / "personas" / "林婉清.txt").write_text("玩家自己新建的人设", encoding="utf-8")
    return r


# ---------------------------------------------------------------------------
# 核心：来源唯一 + 还原
# ---------------------------------------------------------------------------

def test_reset_restores_content_and_reports_change(tmp_path):
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    r = reset_prompt("sections/world_basis.txt", root=root, defaults=dflt)
    assert r["path"] == "sections/world_basis.txt"
    assert r["reset"] is True and r["changed"] is True
    assert r["text"] == "出厂世界观\n第二行\n"
    assert r["chars"] == len(r["text"])
    assert (root / "sections" / "world_basis.txt").read_text(encoding="utf-8") == r["text"]
    # 目标字节 == 内置副本字节（这是"重置为默认"的字面含义，必须逐字节成立）
    assert (root / "sections" / "world_basis.txt").read_bytes() \
        == (dflt / "sections" / "world_basis.txt").read_bytes()
    print("✓ reset_prompt：内容还原 + text 回传 + 字节一致")


def test_reset_source_is_bundled_not_user_tree(tmp_path, monkeypatch):
    """**来源唯一**：绝不能从用户副本读默认（那会变成自我拷贝、静默无效）。

    这条测试走**生产形态**：不传 `defaults`（→ `paths.bundled_prompts_root()`），
    并把两个根分别指到内容不同的目录上。只有这样才能抓住"来源拿错"——
    拿错时重置会退化成"自己拷自己"或"用户文件覆盖用户文件"，界面上完全看不出异常。
    （第一版测试显式传了 defaults，于是变异①⑩都抓不住：测试替被测代码兜了底。)
    """
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    (dflt / "sections" / "world_basis.txt").write_text("出厂版本A", encoding="utf-8")
    (root / "sections" / "world_basis.txt").write_text("用户版本B", encoding="utf-8")
    monkeypatch.setattr(paths, "prompts_root", lambda: root)
    monkeypatch.setattr(paths, "bundled_prompts_root", lambda: dflt)
    assert prompt_files._defaults_root(None) == dflt, "缺省来源必须是内置副本根"
    reset_prompt("sections/world_basis.txt", root=root)     # ← 刻意不传 defaults
    assert (root / "sections" / "world_basis.txt").read_text(encoding="utf-8") == "出厂版本A", \
        "重置必须写入内置副本的内容；写成用户自己的内容 = 自我拷贝（静默失效）"
    print("✓ 来源唯一：缺省走内置副本根，不是用户副本")


def test_bundled_root_does_not_follow_data_root(tmp_path, monkeypatch):
    """`paths.bundled_prompts_root()` 锚**包目录**，不许跟着 `AGENT_LOOP_DATA` 跑。

    为什么单测这一条：发行版里 `data_root()/prompts` 就是**用户副本**，内置副本根一旦"退回数据根"，
    重置就变成自我拷贝 —— 而两种写法在开发机上（数据根 == 包目录）**结果完全相同**，
    只有把数据根指到别处才分得出来。
    """
    fake = tmp_path / "别的地方"
    fake.mkdir()
    monkeypatch.setattr(paths, "data_root", lambda: fake)
    assert paths.bundled_prompts_root() == paths.PKG_DIR / "prompts", \
        "内置副本根必须锚包目录（exe 里那份）；跟着数据根跑 = 拿用户副本当默认"
    print("✓ 内置副本根不随 AGENT_LOOP_DATA 漂移")


def test_reset_twice_is_byte_idempotent(tmp_path):
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    first = reset_prompt("personas/default.txt", root=root, defaults=dflt)
    assert first["changed"] is True
    before = (root / "personas" / "default.txt").read_bytes()
    second = reset_prompt("personas/default.txt", root=root, defaults=dflt)
    assert second["changed"] is False, "已经是默认内容就不该再写盘（免得白发一次 SystemPrompt 重载）"
    assert (root / "personas" / "default.txt").read_bytes() == before
    print("✓ changed 标记：第二次重置不写盘")


def test_reset_heals_crlf_pollution(tmp_path):
    """被 CRLF 污染（旧版保存路径的副作用）的文件，重置顺手治好（写侧恒 LF）。"""
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    (root / "personas" / "default.txt").write_bytes(b"\xe7\x94\xb2\r\n\xe4\xb9\x99\r\n")   # 甲 CRLF 乙 CRLF
    r = reset_prompt("personas/default.txt", root=root, defaults=dflt)
    assert r["changed"] is True
    assert b"\r" not in (root / "personas" / "default.txt").read_bytes()
    assert (root / "personas" / "default.txt").read_bytes() \
        == (dflt / "personas" / "default.txt").read_bytes()
    print("✓ CRLF 污染件被重置治好（写侧恒 LF）")


def test_reset_does_not_touch_defaults_tree(tmp_path):
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    src = dflt / "sections" / "world_basis.txt"
    before = (src.read_bytes(), src.stat().st_mtime_ns)
    reset_prompt("sections/world_basis.txt", root=root, defaults=dflt)
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before, "内置副本是只读来源，一个字节都不许动"
    print("✓ 内置副本只读（字节/mtime 均未变）")


# ---------------------------------------------------------------------------
# 白名单：系统件可还原、用户新增件拒绝
# ---------------------------------------------------------------------------

def test_reset_allows_system_files(tmp_path):
    """sections / default / _suffix 都不许新建，但**可以**还原 —— 不套 _allow_create 门。"""
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    for rel in ("sections/harness_identity.txt", "personas/default.txt",
                "personas/_suffix.txt", "compaction/default.md"):
        assert is_resettable(rel, defaults=dflt), rel
        r = reset_prompt(rel, root=root, defaults=dflt)
        assert r["changed"] is True and r["text"].startswith("出厂")
    assert (root / "sections" / "harness_identity.txt").read_text(encoding="utf-8") == "出厂框架身份"
    print("✓ 系统件可还原（sections/default/_suffix）")


def test_reset_refuses_user_created_file(tmp_path):
    """用户新增件没有出厂版本 → 拒绝，并把用户引导到「删除文件」。"""
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    assert is_resettable("personas/林婉清.txt", defaults=dflt) is False
    with pytest.raises(ValueError) as ei:
        reset_prompt("personas/林婉清.txt", root=root, defaults=dflt)
    msg = str(ei.value)
    assert "没有内置默认版本" in msg and "删除文件" in msg
    assert (root / "personas" / "林婉清.txt").read_text(encoding="utf-8") == "玩家自己新建的人设", \
        "拒绝时必须原样保留，不能顺手删掉"
    print("✓ 用户新增件被拒（内容原样保留 + 引导删除）")


def test_reset_refuses_virtual_config_path(tmp_path):
    """自我人设不是文件：文件接口显式拒绝（C# 侧按前缀分流到 set_config 清空）。

    断言里点名 `set_config` 而不是只找 "player.persona"：`config:player.persona` 这种路径
    后面还有「拒绝绝对路径」「需两段」两道通用防护会兜住（都含 `:`、都没有 `/`），
    所以**只有专用那条**会告诉用户"该走 get_config / set_config" —— 那才是这条守卫的存在意义。
    """
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    assert is_resettable(prompt_files.PLAYER_PERSONA_PATH, defaults=dflt) is False
    with pytest.raises(ValueError) as ei:
        reset_prompt(prompt_files.PLAYER_PERSONA_PATH, root=root, defaults=dflt)
    msg = str(ei.value)
    assert "set_config" in msg and "不是文件" in msg, msg
    print("✓ `config:` 虚拟路径被拒（并指明该走 set_config）")


def test_reset_rejects_bad_paths(tmp_path):
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    for bad in ("", "../../config.json", "/etc/passwd", "sections", "nope/x.txt",
                "sections/../../x.txt", "sections/a/b.txt"):
        with pytest.raises(ValueError):
            reset_prompt(bad, root=root, defaults=dflt)
        assert is_resettable(bad, defaults=dflt) is False
    # 反斜杠是**归一**不是拒绝（_resolve 既有契约：Windows 用户会顺手写反斜杠）
    assert is_resettable("personas\\default.txt", defaults=dflt) is True
    print("✓ 非法路径一律拒绝（含穿越 / 绝对路径 / 段数不对）；反斜杠归一放行")


def test_reset_has_no_text_parameter():
    """**结构性**约束：接口不收 text —— 结构上就不可能变成写任意内容的旁路。"""
    params = list(inspect.signature(reset_prompt).parameters)
    assert params == ["rel", "root", "defaults"], params
    assert not any(p in ("text", "content", "body") for p in params)
    print("✓ reset_prompt 签名不含 text（无法被当成写入旁路）")


# ---------------------------------------------------------------------------
# 修复能力：文件/分组丢了也能补回
# ---------------------------------------------------------------------------

def test_reset_restores_missing_file(tmp_path):
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    (root / "compaction" / "default.md").unlink()
    r = reset_prompt("compaction/default.md", root=root, defaults=dflt)
    assert r["changed"] is True
    assert (root / "compaction" / "default.md").read_text(encoding="utf-8") == "出厂压缩指令"
    print("✓ 丢失的文件可补回")


def test_reset_restores_missing_group_dir(tmp_path):
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    shutil.rmtree(root / "sections")
    r = reset_prompt("sections/world_basis.txt", root=root, defaults=dflt)
    assert r["changed"] is True
    assert (root / "sections" / "world_basis.txt").read_text(encoding="utf-8") == "出厂世界观\n第二行\n"
    print("✓ 整个分组目录丢失也能补回")


def test_reset_in_dev_layout_is_honest_noop(tmp_path):
    """开发机布局（用户副本 == 内置副本）：如实回 changed=False，不假装写了盘。"""
    d = _defaults(tmp_path)
    r = reset_prompt("sections/world_basis.txt", root=d, defaults=d)
    assert r["changed"] is False
    assert r["text"] == "出厂世界观\n第二行\n"
    assert "note" in r, "同体布局要在返回值里说清，便于日志/排障"
    assert (d / "sections" / "world_basis.txt").read_text(encoding="utf-8") == "出厂世界观\n第二行\n"
    print("✓ 同体布局：no-op 且如实上报")


# ---------------------------------------------------------------------------
# list_prompts 标记
# ---------------------------------------------------------------------------

def test_list_prompts_resettable_flag(tmp_path):
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    items = {i["path"]: i for i in list_prompts(root=root, defaults=dflt)}
    assert items["sections/world_basis.txt"]["resettable"] is True
    assert items["personas/default.txt"]["resettable"] is True
    assert items["personas/_suffix.txt"]["resettable"] is True
    assert items["personas/林婉清.txt"]["resettable"] is False
    # 虚拟条目：有明确定义的"默认"= 空（C# 侧走 set_config，不走 reset_prompt）
    assert items[prompt_files.PLAYER_PERSONA_PATH]["resettable"] is True
    # deletable / resettable 语义互不重叠：sections 是「不可删但可重置」
    assert items["sections/world_basis.txt"]["deletable"] is False
    print("✓ list_prompts.resettable 标记（含与 deletable 的正交性）")


def test_list_prompts_without_defaults_uses_bundled(tmp_path):
    """不传 defaults 时走 paths.bundled_prompts_root()（生产装配形态）。"""
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    assert paths.bundled_prompts_root() == prompt_files._defaults_root(None)
    items = {i["path"]: i["resettable"] for i in list_prompts(root=root)}
    # 仓库里真实存在的 sections 文件 → True；测试造的"林婉清"仓库里没有 → False
    assert items["sections/world_basis.txt"] is True
    assert items["personas/林婉清.txt"] is False
    print("✓ 缺省 defaults = 内置副本根")


# ---------------------------------------------------------------------------
# textio 换行契约（重置能字节幂等的前提）
# ---------------------------------------------------------------------------

def test_textio_lf_fixed_point(tmp_path):
    """读→写→读 必须收敛到 LF 且**不再膨胀** —— "每存一次多一层 \\r" 那个 bug 的回归测试。

    起点刻意用 **CRLF** 字节（而不是 LF）：那个 bug 的现场就是"磁盘上已经是 CRLF"
    （旧版写盘留下 / 记事本存过 / 随包那 3 个文件），此时
      · 读侧不归一 → 字符串里带着 \\r → 写侧若做平台翻译 → `\\r\\n` 变 `\\r\\r\\n`（实测 13→15→17→19 字节）；
      · 只要读侧归一**或**写侧不翻译，任一条成立膨胀就停。
    所以这条测试同时盯住两个方向：跑完必须是 LF（`\\r` 一个都不剩）且长度恒定。
    """
    p = tmp_path / "x.txt"
    p.write_bytes("甲\r\n乙\r\n丙\r\n".encode("utf-8"))
    sizes = []
    for _ in range(4):
        textio.write_text(p, textio.read_text(p))
        sizes.append(len(p.read_bytes()))
    assert len(set(sizes)) == 1, "每轮读写后字节数必须恒定（膨胀就是 bug）: %s" % sizes
    assert b"\r" not in p.read_bytes(), "读写必须收敛到 LF（残留 \\r 会进 system prompt，也会让字节比对永远判'有变化'）"
    assert p.read_bytes() == "甲\n乙\n丙\n".encode("utf-8")
    print("✓ textio 读写：CRLF 起点也收敛到 LF，且字节数不再膨胀")


def test_textio_normalizes_crlf_and_cr(tmp_path):
    p = tmp_path / "x.txt"
    p.write_bytes(b"a\r\nb\rc\nd")            # CRLF / 孤立 CR / LF 混合
    assert textio.read_text(p) == "a\nb\nc\nd"
    p.write_bytes("甲\r\n乙".encode("gbk"))     # GBK + CRLF：编码兜底与换行归一必须同时生效
    assert textio.read_text(p) == "甲\n乙"
    print("✓ read_text：CRLF/CR 归一 + GBK 兜底")


def test_textio_write_is_utf8_no_bom(tmp_path):
    p = tmp_path / "x.txt"
    textio.write_text(p, "甲\n乙")
    assert p.read_bytes() == "甲\n乙".encode("utf-8"), "写侧不许做平台换行翻译（Windows 上会变 CRLF）"
    assert not p.read_bytes().startswith(b"\xef\xbb\xbf")
    print("✓ write_text：UTF-8 无 BOM + 换行恒 LF")


def test_shipped_prompts_are_lf_utf8_no_bom():
    """仓库里随包的提示词必须全是 LF + UTF-8 无 BOM。

    为什么这是一条不变量而不是洁癖：重置是**逐字节**比对/复制的，内置那份要是 CRLF，
    "重置后再重置"会永远判 `changed=True`（LF 写入 vs CRLF 源），幂等性就没了。
    """
    root = paths.bundled_prompts_root()
    assert root.is_dir(), root
    bad = []
    for p in sorted(root.rglob("*")):
        # 该不变量针对提示词资产；prompts/ 里的 .py（prompt_files 等）与 __pycache__ 不在此列
        if not p.is_file() or p.suffix == ".py" or "__pycache__" in p.parts:
            continue
        b = p.read_bytes()
        if b"\r" in b or b.startswith(b"\xef\xbb\xbf"):
            bad.append(p.relative_to(root).as_posix())
    assert not bad, "随包提示词必须 LF + 无 BOM，违规: %s" % bad
    print("✓ 随包提示词：全部 LF + 无 BOM")


# ---------------------------------------------------------------------------
# ChatHub reset_prompt RPC 路由
# ---------------------------------------------------------------------------

class _RealishPromptService:
    """薄封装真实 prompt_files（生产装配即此形态）。"""

    def __init__(self, root, defaults):
        self._root = root
        self._defaults = defaults

    def reset_prompt(self, rel):
        return reset_prompt(rel, root=self._root, defaults=self._defaults)


class _OldMockPromptService:
    """旧版 mock：没有 reset_prompt（验证 fail-closed 而非静默成功）。"""

    def list_prompts(self):
        return []


def _hub(prompt_service=None):
    from agent_loop.transport.ws_channel import ChatHub
    return ChatHub(None, None, prompt_service=prompt_service)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_hub_reset_prompt_routing(tmp_path):
    root, dflt = _root(tmp_path), _defaults(tmp_path)
    hub = _hub(_RealishPromptService(root, dflt))
    out = _run(hub.handle_request("reset_prompt", {"path": "sections/world_basis.txt"}))
    assert out["reset"] is True and out["effective"] == "hot" and out["changed"] is True
    assert (root / "sections" / "world_basis.txt").read_text(encoding="utf-8") == "出厂世界观\n第二行\n"
    with pytest.raises(ValueError):    # 用户新增件 → ValueError → 传输层 ok:false
        _run(hub.handle_request("reset_prompt", {"path": "personas/林婉清.txt"}))
    print("✓ ChatHub reset_prompt 路由（hot 标记 + 无默认版本拒绝）")


def test_hub_reset_prompt_old_service_fails_closed():
    """旧 mock 无 reset_prompt → 明确报错，绝不静默成功。"""
    hub = _hub(_OldMockPromptService())
    with pytest.raises(ValueError) as ei:
        _run(hub.handle_request("reset_prompt", {"path": "sections/world_basis.txt"}))
    assert "不支持重置" in str(ei.value)
    print("✓ 旧服务缺 reset_prompt 时 fail-closed")
