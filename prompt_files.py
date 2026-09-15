"""prompt_files — prompts/ 目录服务（UI 侧唯一读写口；各司其职：只碰文件，不懂屉）

分层分权：
- SystemPrompt 管「文件 → 屉 → system」（每步自检热加载）——不动。
- prompt_files 管「UI 的枚举/读/写/新建」：白名单校验 + 路径穿越防护 + 文件 IO。
- ChatHub 只路由（list_prompts / read_prompt / write_prompt / create_persona），
  装配层把本模块注入。

白名单语义（与 system_prompt 的加载规则严格对齐）：
- sections/：只有 _SECTION_MAP 四个文件会被加载（harness_identity / world_basis /
  world_persona_rules / tool_usage），故**只允许读写已有文件**，新建无意义即拒绝。
- personas/：default.txt / _suffix.txt / {npc_id}.txt（新建 = 新增 NPC 人设）
- traits/：{npc_id}.json（可选静态标签变量）
- compaction/：default.md / {npc_id}.md（**历史**压缩指令模板，作为 system 下发）

热生效约定：所有文件 SystemPrompt 每步 mtime 自检，写完下个 turn 自动生效，UI 无需提示操作。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths as _paths
from . import textio as _textio

# prompts 根目录：**每次现算**，不缓存成常量。
# 为什么不缓存：发行版里「<Mod根>/prompts」可能在本进程起来之后才被安装器铺好，
# 缓存常量会让本模块永远看不见它。现算只是一次 is_dir()，开销可忽略。
# 兼容保留 `_PROMPTS_ROOT` 这个名字（旧引用与测试可能直接取）。
def _prompts_root() -> Path:
    return _paths.prompts_root()


_PROMPTS_ROOT = _prompts_root()

# 允许的分组与该组可新建文件的扩展名（None = 只允许已有文件）
_GROUPS: Dict[str, Optional[str]] = {
    "sections": None,      # 只读改已有（新建不被 _SECTION_MAP 加载）
    "personas": ".txt",
    "traits": ".json",
    "compaction": ".md",
}
_GROUP_ORDER = ("sections", "personas", "traits", "compaction")

# npc_id / 文件名合法性：中英数字与部分安全符号；禁路径分隔/控制字符；禁下划线开头（避让 _suffix.txt）
_NAME_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]+$")


def _validate_name(name: str, *, kind: str) -> str:
    """npc_id 或文件名合法性校验，非法即拒（fail-closed）。"""
    if not isinstance(name, str):
        raise ValueError(f"{kind} 必须是字符串")
    name = name.strip()
    if not name or len(name) > 64:
        raise ValueError(f"{kind} 长度需在 1~64 之间")
    if name.startswith("_"):
        raise ValueError(f"{kind} 不能以下划线开头（避让系统文件 _suffix.txt 等）")
    if not _NAME_RE.match(name) or name in (".", ".."):
        raise ValueError(f"{kind} 含非法字符: {name!r}")
    return name


def _resolve(rel: str, root: Optional[Path] = None, *, must_exist: bool = True) -> Path:
    """rel（"<group>/<filename>"）→ 组内绝对路径。

    防护：禁绝对路径 / .. / 反斜杠 / 多级嵌套；组外一律拒绝。
    """
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("path 不能为空（形如 \"personas/林婉清.txt\"）")
    rel = rel.strip().replace("\\", "/")
    if rel.startswith("/") or ":" in rel.split("/")[0]:
        raise ValueError(f"拒绝绝对路径: {rel}")
    parts = [p for p in rel.split("/") if p != ""]
    if len(parts) != 2:
        raise ValueError(f"path 需为 <分组>/<文件名> 两段: {rel}")
    group, fname = parts
    if group not in _GROUPS:
        raise ValueError(f"未知分组: {group}（允许: {', '.join(_GROUP_ORDER)}）")
    if fname in (".", "..") or not _NAME_RE.match(fname):
        raise ValueError(f"文件名非法: {fname!r}")
    base = Path(root) if root is not None else _prompts_root()
    target = (base / group / fname).resolve()
    group_dir = (base / group).resolve()
    if group_dir not in target.parents:
        raise ValueError(f"路径越界（防穿越）: {rel}")
    if must_exist and not target.is_file():
        raise FileNotFoundError(f"文件不存在: {rel}")
    return target


def _allow_create(group: str, fname: str) -> bool:
    """该组是否允许新建此文件：扩展名须匹配该组的可建类型。"""
    ext = _GROUPS[group]
    if ext is None:
        return False
    return fname.endswith(ext) and fname != f"_suffix{ext}" and fname != f"default{ext}"


def _describe(group: str, name: str) -> str:
    """文件作用一句话（UI 悬停气泡用；新增文件类型记得补这里）。"""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if group == "sections":
        return {
            "harness_identity": "框架身份与对话基本规则（对所有 NPC 生效）",
            "world_basis": "世界观基础设定（通用背景）",
            "world_persona_rules": "世界内 NPC 人设写作通则",
            "tool_usage": "工具调用使用说明（模型何时用什么工具）",
        }.get(stem, "全局设定文件，对所有 NPC 生效")
    if group == "personas":
        if stem == "default":
            return "兜底人设：NPC 无专属人设文件时使用"
        if stem == "_suffix":
            return "人设尾部追加：所有 NPC 的人设末尾都会拼上这一段"
        return f"{stem} 的专属人设（存在则覆盖 default）"
    if group == "traits":
        return f"{stem} 的静态标签变量（气运/性格等，人设里用 {{变量}} 插值）"
    if group == "compaction":
        if stem == "default":
            return "历史压缩指令模板（全局，作为 system 下发）"
        return f"{stem} 的历史压缩指令模板（专属）"
    return "提示词文件"


def list_prompts(root: Optional[Path] = None, npc_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """枚举提示词文件。

    npc_id 为空 → 全量（F11 管理员视图）。
    npc_id 非空 → 仅 全局 + 该Npc 专属（对话内 ⚙ 视图）：
      sections: 全量 4 个（全局）
      personas: default.txt + _suffix.txt + {npc_id}.txt
      traits:   {npc_id}.json
      compaction: default.md + {npc_id}.md（工具结果压缩指令内置，不出现在此）
    """
    base = Path(root) if root is not None else _prompts_root()
    npc_id = npc_id.strip() if isinstance(npc_id, str) and npc_id.strip() else None
    out: List[Dict[str, Any]] = []
    for group in _GROUP_ORDER:
        gdir = base / group
        if not gdir.is_dir():
            continue
        names = sorted(p.name for p in gdir.iterdir() if p.is_file())
        if npc_id is None:
            filtered = names
        else:
            if group == "sections":
                filtered = names
            elif group == "personas":
                allowed = {f"{npc_id}.txt", "default.txt", "_suffix.txt"}
                filtered = [n for n in names if n in allowed]
            elif group == "traits":
                filtered = [n for n in names if n == f"{npc_id}.json"]
            elif group == "compaction":
                allowed = {f"{npc_id}.md", "default.md"}
                filtered = [n for n in names if n in allowed]
            else:
                filtered = names
        for name in filtered:
            out.append({
                "group": group, "name": name, "path": f"{group}/{name}",
                "desc": _describe(group, name),
                # 可删标记：与 _allow_create 同规约——sections/default/_suffix=false，
                # 新增文件=true。UI 据此置灰删除按钮；文件枚举自 iterdir，必然存在。
                "deletable": _allow_create(group, name),
            })
    return out


def read_prompt(rel: str, root: Optional[Path] = None) -> str:
    """读一个提示词文件全文（UTF-8）。"""
    target = _resolve(rel, root, must_exist=True)
    return _textio.read_text(target)


def write_prompt(rel: str, text: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """写一个提示词文件（全文覆盖，UTF-8）。

    - 已有文件：直接覆盖（文本即真相，下个 turn 热生效）
    - 新文件：仅当分组允许新建且扩展名匹配（personas .txt / traits .json / compaction .md）
    """
    if not isinstance(text, str):
        raise ValueError("text 必须是字符串")
    target = _resolve(rel, root, must_exist=False)
    if not target.is_file() and not _allow_create(rel.split("/")[0], target.name):
        raise ValueError(f"该分组不允许新建文件: {rel}（sections 只能改已有文件）")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {"path": rel, "saved": True}


def write_prompts(files: Dict[str, str], root: Optional[Path] = None) -> Dict[str, Any]:
    """批量写多个提示词文件（全量提交专用）。

    files: {rel: text} 字典，rel 同 write_prompt 约束。
    失败策略 fail-fast：首个非法即抛异常，上层转 ok:false，不出现半成功。
    成功返回 {"saved": [path,...], "count": N, "effective": "hot"}。
    """
    if not isinstance(files, dict) or not files:
        raise ValueError("files 需要非空字典 {path: text}")
    if len(files) > 64:
        raise ValueError(f"批量文件数过多: {len(files)}（上限 64）")
    for rel, text in list(files.items()):
        if not isinstance(text, str):
            raise ValueError(f"{rel} 的 text 必须是字符串")
        target = _resolve(rel, root, must_exist=False)
        if not target.is_file() and not _allow_create(rel.split("/")[0], target.name):
            raise ValueError(f"该分组不允许新建文件: {rel}")
    saved: List[str] = []
    for rel, text in files.items():
        target = _resolve(rel, root, must_exist=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        saved.append(rel)
    return {"saved": saved, "count": len(saved), "effective": "hot"}


def create_persona(npc_id: str, from_default: bool = True, root: Optional[Path] = None) -> Dict[str, Any]:
    """新建 NPC 人设 personas/{npc_id}.txt（已存在即拒）。

    from_default=True 时以 personas/default.txt 为底稿（缺文件用内置默认句），
    与 SystemPrompt._load_persona 的「NPC 专属 → default 兜底」规则呼应。
    """
    npc_id = _validate_name(npc_id, kind="npc_id")
    rel = f"personas/{npc_id}.txt"
    target = _resolve(rel, root, must_exist=False)
    if target.is_file():
        raise ValueError(f"人设文件已存在: {rel}")
    content = ""
    if from_default:
        default_path = target.parent / "default.txt"
        if default_path.is_file():
            content = _textio.read_text(default_path)
        if not content.strip():
            content = "你是{npc_name}，鬼谷八荒世界的一名修仙者。"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": rel, "created": True}


def is_deletable(rel: str, root: Optional[Path] = None) -> bool:
    """该文件是否可删（UI 预判用；delete_prompt 内部同样以此把关）。

    与 _allow_create 严格对齐——「能新建的才能删」：sections 全组、
    personas/default.*、personas/_suffix.*、compaction/default.* 为系统文件，
    其余（personas/{npc}.txt、traits/{npc}.json、compaction/{npc}.md）为新增文件。
    """
    try:
        target = _resolve(rel, root, must_exist=True)
    except Exception:
        return False
    return _allow_create(rel.split("/")[0], target.name)


def delete_prompt(rel: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """删除一个已存在的提示词文件（仅限新增文件，fail-closed）。

    删除即热生效（SystemPrompt 每步 mtime 自检，无需重启）：
    - personas/{npc}.txt 删除 → 该 NPC 回落 default.txt 兜底；
    - traits/{npc}.json / compaction/{npc}.md 删除 → 回落全局模板/无标签。
    """
    target = _resolve(rel, root, must_exist=True)
    if not _allow_create(rel.split("/")[0], target.name):
        raise ValueError(f"系统内置文件不可删除: {rel}")
    target.unlink()
    return {"path": rel, "deleted": True}
