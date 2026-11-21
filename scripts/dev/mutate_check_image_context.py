#!/usr/bin/env python3
"""变异验证（10-18「图片进上下文」）：把每一条防线改回 bug 原样，确认对应测试变红。

本工程既定证明手法：绿色测试本身不证明什么，只有"改坏了会红"才证明测试真的在守着。
覆盖五组防线（落盘与引用 / 发前投影 / token 记账 / 回收 keep-set / 压缩材料 / 独立复核补强），共 26 项变异。

用法：python3 scripts/dev/mutate_check_image_context.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mutate_lib  # noqa: E402  （同进程跑测层 + 中断还原保险绳，见该文件顶部说明）

ROOT = Path(__file__).resolve().parent.parent.parent   # scripts/dev/ → 工程根
DIALOG = ROOT / "agent" / "dialogue_agent.py"
METER = ROOT / "compaction" / "meter.py"
IMAGES = ROOT / "storage" / "images.py"
OPS = ROOT / "session" / "history_ops.py"
COMPRESS = ROOT / "compaction" / "compress.py"
WS = ROOT / "transport" / "ws_channel.py"

TESTS = ["tests/test_image_context.py", "tests/test_image_attachment.py"]
PYTEST = [sys.executable, "-m", "pytest", *TESTS, "-q", "--no-header", "-p", "no:cacheprovider"]

# 源码里反复出现的三句（片段里逐字用，改一处就够）
_URI_RESOLVE = """                nb = dict(b)
                nb.pop("ref", None)
                nb["url"] = uri
                new_content.append(nb)"""
_GC_DELETE = """    try:
        from ..storage.images import gc_session
        gc_session(session, why=f"delete:{reason}")
    except Exception:
        # 回收失败**不能**让删除失败：删除已经落账，重试由玩家决定
        log.warning("删除后图片回收失败（删除本身已生效）", exc_info=True)"""
_GC_COMPACT = """        try:
            from ..storage.images import gc_session
            gc_session(session, why="compact")
        except Exception:
            # 回收失败不能让压缩失败：纪要已经落账，账本是权威
            log.warning("压缩后图片回收失败（压缩本身已生效）", exc_info=True)"""

MUTATIONS = [
    # ---------------- ① 落盘与引用：字节进仓、账本只记 ref ----------------
    (
        "A 收图只算 ref 不落盘（账本有引用、文件不存在 ⇒ 下一回合就解析不出来）",
        DIALOG,
        "                ref = store.put(data, mime)",
        "                ref = store.ref_for(data, mime)",
        "test_image_reaches_model_but_not_ledger",
    ),
    (
        "B 账本块里顺手塞 url（图像字节进账本 = 09-13 那条注释警告的事故）",
        DIALOG,
        '            block: Dict[str, Any] = {"type": "image", "ref": ref, "mime": mime, "tokens": tokens}',
        '            block: Dict[str, Any] = {"type": "image", "ref": ref, "url": url, "mime": mime, "tokens": tokens}',
        "test_ledger_holds_ref_only_never_bytes",
    ),
    (
        "C ref 形状与目录包含校验被删（`../` 可穿越到存档根之外）",
        IMAGES,
        """        s = str(ref or "")
        if not _REF_RE.match(s):
            return None
        path = os.path.join(self.root, *s.split("/"))
        # 双保险：realpath 必须仍在 root 之内（regex 已经拦了 `..`，但不靠单点防御）
        try:
            real_root = os.path.realpath(self.root)
            real = os.path.realpath(path)
            if os.path.commonpath([real_root, real]) != real_root:
                return None
        except Exception:
            return None""",
        """        s = str(ref or "")
        path = os.path.join(self.root, *s.split("/"))""",
        "test_store_put_dedupes_by_content_and_validates_ref",
    ),
    (
        "D 落盘不再按内容去重（同一张图发两次存两份）",
        IMAGES,
        "        digest = hashlib.sha1(data).hexdigest()",
        "        digest = hashlib.sha1(data + os.urandom(4)).hexdigest()",
        "test_store_put_dedupes_by_content_and_validates_ref",
    ),
    # ---------------- ② 发前投影：ref → data URI，且必须浅拷贝 ----------------
    (
        "E 投影把 ref 当 url 直接发（模型收到 \"images/...\" 这种假地址）",
        DIALOG,
        _URI_RESOLVE,
        """                nb = dict(b)
                nb.pop("ref", None)
                nb["url"] = ref
                new_content.append(nb)""",
        "test_image_reaches_model_but_not_ledger",
    ),
    (
        "F 投影原地改账本（不浅拷贝 ⇒ 下次存档 base64 永久写进 log）",
        DIALOG,
        _URI_RESOLVE,
        """                b.pop("ref", None)
                b["url"] = uri
                new_content.append(b)""",
        "test_inline_projection_is_pure_and_repeatable",
    ),
    (
        "G 文件读不出来时抛异常（一次手删就让整局对话全崩）",
        DIALOG,
        """                if not uri:
                    log.warning("图片读不出来：%s（本次请求丢弃这张图；占位文本仍在上下文里）", ref)
                    continue""",
        """                if not uri:
                    raise RuntimeError(f"图片读不出来：{ref}")""",
        "test_missing_file_degrades_to_placeholder",
    ),
    (
        "H 模型不支持图片也照发（纯文本模型每回合报错，不只是丢图）",
        DIALOG,
        '        allow = getattr(self.llm, "supports_image", None) is not False',
        "        allow = True",
        "test_text_only_model_drops_images_without_error",
    ),
    # ---------------- ③ token 记账 ----------------
    (
        "I image 块落回 else 分支（dump 整块：data URI 被估成几万 token）",
        METER,
        '        elif t in ("image", "image_url"):',
        "        elif False:",
        "test_image_tokens_recorded_and_counted_by_meter",
    ),
    (
        "J 有宽高也不用（图片恒按 85 下限计，估算看不见图）",
        METER,
        "    if wi > 0 and hi > 0:",
        "    if False:",
        "test_image_tokens_recorded_and_counted_by_meter",
    ),
    # ---------------- ④ 回收：keep-set = surface ∪ 待投递筐 ----------------
    (
        "K 回收的 keep-set 丢掉待投递筐（回合边界压缩 ⇒ 玩家刚发的图被删）",
        IMAGES,
        "    return refs | pend",
        "    return refs",
        "test_pending_inbox_image_is_not_garbage_collected",
    ),
    (
        "L 回收不认 keep（见到文件就删）",
        IMAGES,
        """            if ref in keep_set:
                continue""",
        """            if False:
                continue""",
        "test_pending_inbox_image_is_not_garbage_collected",
    ),
    (
        "M 删回合/群窗删消息之后不回收（图片永远占着存档目录）",
        OPS,
        _GC_DELETE,
        "",
        "test_delete_turn_reclaims_image_file",
    ),
    (
        "N 压缩折走之后不回收（纪要里已经没有它了，文件却留着）",
        COMPRESS,
        _GC_COMPACT,
        "",
        "test_compaction_reclaims_image_file",
    ),
    # ---------------- ⑤ 压缩材料 ----------------
    (
        "O 材料里重复补 [图片]（正文已有占位 ⇒ 摘要以为附了两张图）",
        COMPRESS,
        "                    if not _has_placeholder:",
        "                    if True:",
        "test_transcript_material_does_not_duplicate_image_marker",
    ),
    (
        "P 压缩材料直接 dump 图片块（base64 灌进摘要请求）",
        COMPRESS,
        """                elif btype in ("image", "image_url"):
                    # 图片永远**按文字形式**进材料（绝不读字节：账本里只有 ref，
                    # 这里也不去解 data URI —— 那是几万字符的噪音）
                    if not _has_placeholder:
                        lines.append("[图片]")""",
        """                elif btype in ("image", "image_url"):
                    lines.append(self._stringify(block))""",
        "test_transcript_material_does_not_duplicate_image_marker",
    ),
    # ---------------- ⑥ 独立复核补强（10-18 复核报告：这些防线当时没有任何测试） ----------------
    (
        "Q 回收的 fail-closed 被破坏（keep-set 拼不出来时当成'没有引用'⇒ 全删）",
        IMAGES,
        """    keep = live_refs(session, extra=extra)
    if keep is None:
        return []""",
        """    keep = live_refs(session, extra=extra)
    if keep is None:
        keep = set()""",
        "test_gc_aborts_when_surface_is_corrupt",
    ),
    (
        "R 待投递筐重放失败时返回空集（注释写着'绝不'，空集 = 把筐里的图全删）",
        IMAGES,
        """    try:
        from ..session.inbox import Inbox
        box = Inbox(session)
    except Exception:
        return None  # type: ignore[return-value]""",
        """    try:
        from ..session.inbox import Inbox
        box = Inbox(session)
    except Exception:
        return set()""",
        "test_gc_aborts_when_inbox_replay_fails",
    ),
    (
        "S 投影只处理一条消息里的第一张图（C# 一条消息最多 3 张 ⇒ 第 2/3 张永远看不到）",
        DIALOG,
        _URI_RESOLVE,
        _URI_RESOLVE + "\n                break",
        "test_multiple_images_in_one_message",
    ),
    (
        "S2 收图只收第一张（后面的图连盘都不落）",
        DIALOG,
        """            out.append(block)
        return out""",
        """            out.append(block)
            break
        return out""",
        "test_multiple_images_in_one_message",
    ),
    (
        "T 图片仓目录名退回无界百分号编码（长中文名的 NPC 图全废 —— 复核抓到的真 bug）",
        IMAGES,
        """    raw = str(scope or "").strip() or "_"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    head = urllib.parse.quote(raw, safe="")[:40].strip(".%") or "_"
    return f"{head}-{digest}\"""",
        """    return urllib.parse.quote(str(scope or "").strip() or "_", safe="")""",
        "test_image_store_scope_is_bounded_and_isolated",
    ),
    (
        "U 收图丢掉体积兜底（没有宽高的图恒按 85 下限估 ⇒ 估算看不见图）",
        DIALOG,
        "            tokens = meter.estimate_image_tokens(w=w, h=h, kb=kb if kb else len(data) / 1024.0)",
        "            tokens = meter.estimate_image_tokens(w=w, h=h, kb=kb)",
        "test_image_token_fallbacks",
    ),
    (
        "V 显式 supports_image=True 被当成'未声明'（声明支持图片的模型反而被丢图）",
        DIALOG,
        '        allow = getattr(self.llm, "supports_image", None) is not False',
        '        allow = getattr(self.llm, "supports_image", None) is None',
        "test_explicitly_supported_model_still_receives_images",
    ),
    (
        "W 取消/关窗时不回收筐里的孤儿图（玩家不删不压就永久滞留）",
        DIALOG,
        """        try:
            from ..storage.images import gc_session
            gc_session(self.session, why="cancel")
        except Exception:
            log.warning("取消后图片回收失败（不影响取消本身）", exc_info=True)
""",
        "",
        "test_cancel_reclaims_images_of_dropped_inbox",
    ),
    (
        "X tool-result 内嵌图片块又被 dump 进材料（几万字符的 JSON 灌进摘要请求）",
        COMPRESS,
        """                    _inner, _imgs = self._split_images(block.get("content"))
                    lines.append(f"[工具返回] {self._stringify(_inner).strip()}"
                                 + ("[图片]" if _imgs else ""))""",
        """                    lines.append(f"[工具返回] {self._stringify(block.get('content')).strip()}")""",
        "test_tool_result_image_is_not_dumped_into_material",
    ),
    (
        "Y 不兜 text=None（判据放宽后 'None + 有图' 可达 ⇒ 崩在 len(text)，整个回合失败）",
        WS,
        """        if text is None:
            text = ""
        if not npc_id or (not text and not images):""",
        """        if not npc_id or (not text and not images):""",
        "test_none_text_with_image_still_works",
    ),
]


def _adapt(snippet: str, text: str) -> str:
    """把片段的行尾适配成目标文件的行尾（本仓 `session/history_ops.py` 是 CRLF，其余是 LF）。"""
    if "\r\n" in text and "\r\n" not in snippet:
        return snippet.replace("\n", "\r\n")
    if "\r\n" not in text and "\r\n" in snippet:
        return snippet.replace("\r\n", "\n")
    return snippet


def _locate(text: str, snippet: str):
    """EOL 无关的定位 → (start, end) 或 None。"""
    cand = _adapt(snippet, text)
    i = text.find(cand)
    return None if i < 0 else (i, i + len(cand))


def _count(text: str, snippet: str) -> int:
    return text.count(_adapt(snippet, text))


def run_tests():
    """跑一次测试（子进程）。

    ★ 必须**绕开字节码缓存** ★：仓库在 Windows 盘（drvfs）上，还原源码后 mtime 与变异那一次
    可能落在同一秒粒度里，CPython 的 "mtime+size" 判据于是认为缓存仍有效 → 跑的是上一轮的变异版
    （或反过来：源码明明被改坏、跑的还是好的）。实测代价：一次真变异报"没捕获"、
    一次"还原后绿"却是假绿，并且**把变异留在源码树里**。`_mutate_lib` 对它自己那套同进程跑测
    做了同样的事（见其 docstring 第 3 条）；本脚本走子进程，必须自己来：
    每次给一个全新的 `PYTHONPYCACHEPREFIX`（等于不读也不写任何旧 pyc）。
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="agent_loop_mut_pyc_")
    p = subprocess.run(PYTEST, cwd=str(ROOT), capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    print("=" * 78)
    print("变异验证（图片进上下文）：改回 bug → 期望变红；还原 → 期望变绿")
    print("=" * 78)

    _mutate_lib.guard([DIALOG, METER, IMAGES, OPS, COMPRESS, WS])

    rc, out = run_tests()
    if rc != 0:
        print("✗ 基线不绿，先修好再跑变异\n" + out[-3000:])
        return 1
    print(f"✓ 基线绿：{out.strip().splitlines()[-1]}\n")

    snapshots = {p: p.read_bytes() for p in (DIALOG, METER, IMAGES, OPS, COMPRESS, WS)}

    failures = []
    for name, path, good, bad, expect in MUTATIONS:
        original = path.read_text(encoding="utf-8")
        hit = _locate(original, good)
        if hit is None:
            print(f"✗ {name}: 找不到待变异片段（源码变了？）")
            failures.append(name)
            continue
        if _count(original, good) != 1:
            print(f"✗ {name}: 待变异片段出现 {_count(original, good)} 次（不唯一，改错地方就会误判）")
            failures.append(name)
            continue
        start, end = hit
        mutated = original[:start] + _adapt(bad, original) + original[end:]
        try:
            path.write_text(mutated, encoding="utf-8")
            if path.read_text(encoding="utf-8") == original:
                print(f"✗ {name}: 变异**没施加成功**（写完读回来还是一样）")
                failures.append(name)
                continue
            rc, out = run_tests()
            caught = expect in out and rc != 0
            print(f"{'✓' if caught else '✗'} {name}")
            print(f"    期望 {expect} → {'RED（被捕获）' if caught else '没有变红！'}")
            if not caught:
                failures.append(name)
        finally:
            path.write_bytes(snapshots[path])
            if path.read_bytes() != snapshots[path]:
                print(f"✗ {name}: 还原失败！")
                failures.append(name)

    dirty = [p.name for p, data in snapshots.items() if p.read_bytes() != data]
    if dirty:
        print(f"✗ 这些文件没有逐字节还原：{dirty}")
        failures.append("restore:bytes")

    rc, out = run_tests()
    print()
    if rc == 0:
        print(f"✓ 全部还原后绿：{out.strip().splitlines()[-1]}")
    else:
        print("✗ 还原后没有恢复绿色！\n" + out[-2000:])
        failures.append("restore:green")

    print()
    print("=" * 78)
    if failures:
        print(f"✗ {len(failures)} 项未被捕获：{failures}")
        return 1
    print(f"✓ 全部 {len(MUTATIONS)} 项变异都被捕获，且还原后绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
