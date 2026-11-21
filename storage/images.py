"""storage/images — 图片字节的存档内仓库（session.log 里只记 ref）

背景与取舍见 `docs/archive/2026-10-18-图片进上下文.md`。一句话：

  · 账本是**只追加**的（删除 = 追一条 `history/delete` 注记、压缩 = 追一条 replace），
    内联进消息块的 base64 **永远回收不了**；而 `save_session` 是**全量重写**，
    每固化一次都要把整份 log 序列化一遍 —— 图越多越慢。
  · 所以字节落 `<sessions_root>/images/<scope_dir(npc)>/<sha1>.<ext>`（目录名定长安全，
    见 `scope_dir()`：长中文名/`.`/`..` 都不会把图写丢或写出去），账本里只留相对路径 `ref`。
    文件是**可删对象**：消息被删 / 被压缩折走之后，`collect()` 把它回收（§二-⑤）。

三件套：

    put(data, mime)  -> ref        写盘（同内容同 ref，天然去重）
    data_uri(ref)    -> str|None   投影给模型的那一份（读盘 + 内存缓存）
    collect(keep)    -> [ref]      GC：删除/压缩之后调用

★ **权威方向**：账本里的 ref 是权威，文件缺失即降级（丢图 + WARNING，绝不崩）。
  反过来（以文件为准去改账本）会让一次误删变成"历史被改写"。

★ 为什么 GC 的 keep-set 还要算上**待投递筐**：压缩发生在回合边界，此刻玩家刚发的那条
  带图消息还躺在 `inbox`（`agent/inbox/spliced` 事件，尚未落 surface）。只看 surface
  会把它的图删掉 ⇒ 图片静默丢失。`live_refs()` 用 `Inbox` 自身的重放逻辑算筐，
  不另写一份，免得两边漂移。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import threading
import urllib.parse
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .. import log_setup

log = log_setup.get_logger(__name__)

IMAGES_DIR = "images"

# 只认「本仓自己写出来的」ref 形状：images/<scope>/<16~64 位十六进制>.<ext>。
# 宁可窄一点（认不出 ⇒ 丢图 + 警告），也不接受任意相对路径 —— 那是 `../` 穿越的口子。
_REF_RE = re.compile(r"^images/[A-Za-z0-9._%+-]{1,64}/[0-9a-f]{16,64}\.[a-z0-9]{1,8}$")

_MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}
_EXT_TO_MIME = {v: k for k, v in _MIME_TO_EXT.items()}
# jpg 的规范 mime 是 image/jpeg（_EXT_TO_MIME 反查出来的是 image/jpg，这里纠正）
_EXT_TO_MIME[".jpg"] = "image/jpeg"

# data URI 缓存条数：每条约 1–2MB。只缓存**最近用到的**，按 FIFO 淘汰。
# 为什么值得缓存：每回合都要把上下文里的所有图重新转一遍 data URI，读盘 + base64
# 是纯重复劳动；而玩家发出的图条数天然很少（一条消息 ≤3 张）。
_MAX_CACHE = 24


def parse_data_uri(url: Any) -> Optional[Tuple[str, bytes]]:
    """`data:image/png;base64,xxx` → `(mime, bytes)`；认不出（http(s)/空/坏 base64）返回 None。"""
    if not isinstance(url, str):
        return None
    head, sep, payload = url.partition(",")
    if not sep or not head.startswith("data:") or "base64" not in head:
        return None
    mime = head[len("data:"):].split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        return None
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception:
        return None
    return (mime, raw) if raw else None


def scope_dir(scope: Any) -> str:
    """NPC id → **定长、安全**的图片仓目录名（`images/<scope_dir>/`）。

    为什么不是直接 `quote(id)`（10-18 复核抓到的真 bug）：汉字百分号编码后会**膨胀**
    （1 个汉字 → `%E4%BA%91` 9 个字符），名字一长（≥8 个汉字，编码后 >64 字符）就撑破
    `_REF_RE` 的 scope 上限 ⇒ `put()` 抛错 ⇒ 该 NPC 的图**每一张都被丢弃**
    （功能整个静默失效，只有 WARNING）。
    反过来"直接截断"又会撞名（两个 NPC 共用一个目录 = 互相删图）。

    所以：**可读前缀（百分号编码后截断）+ 短哈希（去重）**，长度恒为 ≤49 字符：
      · 去掉首尾的 `.` 与 `%`（截断可能切在 `%XX` 中间；也避免目录名以 `.` 开头）；
      · 哈希用**原始 id**（不是截断后的前缀），所以"前缀相同、后半不同"的两个名字不会撞；
      · 目录名恒不是 `.`/`..` ⇒ 不可能把图片写到 `images/` 之外。
    """
    raw = str(scope or "").strip() or "_"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    head = urllib.parse.quote(raw, safe="")[:40].strip(".%") or "_"
    return f"{head}-{digest}"


class ImageStore:
    """一个会话的图片仓（目录 = `<root>/images/<scope_dir(npc_id)>/`）。"""

    def __init__(self, root: str, scope: str):
        self.root = os.path.expanduser(str(root or ""))
        self.scope = scope_dir(scope)
        self.base = os.path.join(self.root, IMAGES_DIR, self.scope)
        self._cache: "OrderedDict[str, str]" = OrderedDict()
        self._lock = threading.Lock()

    # ---------- 写 ----------
    def ref_for(self, data: bytes, mime: str) -> str:
        """内容 → ref（不写盘）。同内容同 ref —— 去重是免费的。"""
        ext = _MIME_TO_EXT.get(str(mime or "").lower(), ".bin")
        digest = hashlib.sha1(data).hexdigest()
        return f"{IMAGES_DIR}/{self.scope}/{digest}{ext}"

    def put(self, data: bytes, mime: str) -> str:
        """写盘并返回 ref。已存在则不重写（同图再发一次零成本）。"""
        ref = self.ref_for(data, mime)
        path = self._path_of_ref(ref)
        if path is None:
            raise ValueError(f"非法 ref: {ref!r}")
        if path.exists() and path.stat().st_size == len(data):
            return ref
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时名再 rename：崩在写一半时留下的是 *.tmp，而不是一张**看起来存在**的坏图
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return ref

    # ---------- 读 ----------
    def _path_of_ref(self, ref: Any) -> Optional[Any]:
        s = str(ref or "")
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
            return None
        from pathlib import Path
        return Path(path)

    def data_uri(self, ref: Any, mime: Any = None) -> Optional[str]:
        """ref → data URI（给模型的那一份）。文件不在/ref 非法 ⇒ None（调用方降级）。"""
        s = str(ref or "")
        with self._lock:
            hit = self._cache.get(s)
            if hit is not None:
                self._cache.move_to_end(s)
                return hit
        path = self._path_of_ref(s)
        if path is None or not path.is_file():
            return None
        try:
            raw = path.read_bytes()
        except OSError:
            log.warning("图片读取失败：%s（本次请求丢弃这张图）", s, exc_info=True)
            return None
        m = str(mime or "").lower() or _EXT_TO_MIME.get(path.suffix.lower(), "image/png")
        uri = f"data:{m};base64,{base64.b64encode(raw).decode('ascii')}"
        with self._lock:
            self._cache[s] = uri
            self._cache.move_to_end(s)
            while len(self._cache) > _MAX_CACHE:
                self._cache.popitem(last=False)
        return uri

    # ---------- 回收 ----------
    def collect(self, keep: Iterable[str]) -> List[str]:
        """删掉 `keep` 之外的图片文件，返回被删的 ref 列表（幂等、失败只记日志）。"""
        keep_set = {str(k) for k in (keep or ())}
        removed: List[str] = []
        if not os.path.isdir(self.base):
            return removed
        try:
            names = sorted(os.listdir(self.base))
        except OSError:
            log.warning("图片目录读取失败：%s（本次不回收）", self.base, exc_info=True)
            return removed
        for name in names:
            ref = f"{IMAGES_DIR}/{self.scope}/{name}"
            if ref in keep_set:
                continue
            if name.endswith(".tmp"):
                # 写一半的残渣：无论 keep 如何都可以删
                try:
                    os.unlink(os.path.join(self.base, name))
                except OSError:
                    pass
                continue
            if not _REF_RE.match(ref):
                continue            # 不是本仓的形状（手放的杂物）：不碰
            path = os.path.join(self.base, name)
            try:
                size = os.path.getsize(path)
                os.unlink(path)
                removed.append(ref)
                log.info("图片回收：%s（%.0f KB）", ref, size / 1024.0)
            except OSError:
                log.warning("图片回收失败：%s", ref, exc_info=True)
        # 顺手清掉空的 scope 目录（删光之后不留空壳）
        try:
            if not os.listdir(self.base):
                os.rmdir(self.base)
        except OSError:
            pass
        return removed


# ══════════════════════════════════════════════════════════════════════
# 引用集合：surface ∪ 待投递筐
# ══════════════════════════════════════════════════════════════════════

def message_refs(content: Any) -> Set[str]:
    """一条消息的 content 里所有 image 块的 ref。"""
    out: Set[str] = set()
    if not isinstance(content, list):
        return out
    for b in content:
        if isinstance(b, dict) and b.get("type") in ("image", "image_url"):
            ref = b.get("ref")
            if isinstance(ref, str) and ref:
                out.add(ref)
    return out


def event_refs(ev: Dict[str, Any]) -> Set[str]:
    """一个账本事件里所有图片 ref（用户消息 / 助手消息 / 工具结果三种形状）。"""
    data = ev.get("data") if isinstance(ev, dict) else None
    if not isinstance(data, dict):
        return set()
    t = ev.get("type")
    if t == "user/message":
        return message_refs(data.get("content"))
    if t == "assistant/message":
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        return message_refs(msg.get("content"))
    if t == "tool/result":
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        return message_refs(msg.get("content"))
    return set()


def _all_log_refs(log_events: List[Dict[str, Any]]) -> Set[str]:
    refs: Set[str] = set()
    for ev in log_events:
        refs |= event_refs(ev)
    return refs


def pending_refs(session) -> Set[str]:
    """待投递筐里的 ref（`agent/inbox/spliced` 重放）。

    ★ 用 `Inbox` 自己的重放逻辑，不在这里另写一份 splice 语义 —— 两份实现必然漂移，
      而漂移的形状是"某些图被静默删掉"。
    ★ 重放失败 ⇒ 返回 None（调用方转成"保守：不回收"），绝不返回空集（空集 = 全删）。
    """
    try:
        from ..session.inbox import Inbox
        box = Inbox(session)
    except Exception:
        return None  # type: ignore[return-value]
    refs: Set[str] = set()
    try:
        for queue in box.state.values():
            for m in queue:
                if isinstance(m, dict):
                    refs |= message_refs(m.get("content"))
    except Exception:
        return None  # type: ignore[return-value]
    return refs


def live_refs(session, extra: Iterable[str] = ()) -> Optional[Set[str]]:
    """**仍然需要**的图片 ref 集合 = surface 节点 ∪ 待投递筐 ∪ extra。

    返回 None = 拼不出来（账本投影异常）⇒ 调用方必须**放弃回收**（宁留孤儿，不误删）。
    """
    refs: Set[str] = {str(x) for x in (extra or ())}
    log_events = getattr(session, "log", None)
    if not isinstance(log_events, list):
        return None
    try:
        for seq in list(session.surface.nodes):
            ev = log_events[seq]
            refs |= event_refs(ev)
    except Exception:
        log.warning("图片引用集合计算失败（本次不回收）", exc_info=True)
        return None
    pend = pending_refs(session)
    if pend is None:
        log.warning("待投递筐重放失败（本次不回收）")
        return None
    return refs | pend


def gc_session(session, why: str = "", extra: Iterable[str] = ()) -> List[str]:
    """回收该会话**不再需要**的图片文件（删除/压缩之后调用）。返回被删 ref。

    无图片仓（裸 Session：单测 / 脚本）⇒ 直接返回空 —— 不报错、不猜测路径。
    """
    store = getattr(session, "image_store", None)
    if store is None or not getattr(store, "root", ""):
        return []
    keep = live_refs(session, extra=extra)
    if keep is None:
        return []
    try:
        removed = store.collect(keep)
    except Exception:
        log.warning("图片回收异常（不影响账本）", exc_info=True)
        return []
    if removed:
        log.info("图片回收：删 %d 张，保留 %d 张（原因：%s）", len(removed), len(keep), why or "-")
    return removed
