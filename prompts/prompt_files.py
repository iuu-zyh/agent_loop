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

「自我人设」是列表里的**虚拟条目**（path = `config:player.persona`）：它不是 prompts/ 下的文件，
而是 config.json 的 `player.persona`（玩家自述，注入 system 的一段）。挂在"提示词"页是因为
玩家会在这里找它 —— 但**读写都不走本模块**（C# 侧分流到 get_config / set_config），
所以 read/write/delete 对它一律显式拒绝，免得有人以为 prompts/ 里能放一份"自我人设文件"。

热生效约定：所有文件 SystemPrompt 每步 mtime 自检，写完下个 turn 自动生效，UI 无需提示操作。

「重置为默认」（2026-09-16 加）：`reset_prompt(rel)` 把用户副本还原成**随包内置**的那一份
（`paths.bundled_prompts_root()`，见该函数注释）。要点三条：
- **内容只有一个来源**：内置副本。绝不拿用户目录当默认（那会退化成自我拷贝、静默失效）。
- **不套 `_allow_create` 门**：sections / default / _suffix 这类「系统件」不许新建，但**可以还原**；
  真正的闸门是「内置副本里得有这个文件」，而它是随包发行的、用户碰不到 → 无法借此写入任意内容。
- **接口不收 text 参数**：结构上就不可能变成「往 sections 里写任意内容」的旁路。
写入统一走 `textio.write_text`（UTF-8 无 BOM + LF），与手改保存同一条路径、同一个形态。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import paths as _paths
from ..config import textio as _textio

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

# 「自我人设」虚拟条目的 path 前缀与完整路径。**唯一定义处**（C# 侧按前缀分流，
# 故这个字符串是对外契约：改它要同时改 ConfigPresenter.IsVirtualConfigPath）。
VIRTUAL_PREFIX = "config:"
PLAYER_PERSONA_PATH = "config:player.persona"


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

    防护：禁绝对路径 / .. / 反斜杠 / 多级嵌套；组外一律拒绝；`config:` 虚拟条目显式拒绝。
    """
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("path 不能为空（形如 \"personas/林婉清.txt\"）")
    rel = rel.strip().replace("\\", "/")
    if rel.startswith(VIRTUAL_PREFIX):
        raise ValueError(
            f"「{rel}」是配置项不是文件（自我人设存在 config.json 的 player.persona），"
            "读写走 get_config / set_config，不经提示词文件接口")
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


def _defaults_root(defaults: Optional[Path] = None) -> Path:
    """内置副本根：显式传入优先（测试用），否则取 `paths.bundled_prompts_root()`。"""
    return Path(defaults) if defaults is not None else _paths.bundled_prompts_root()


def _default_source(rel: str, defaults_root: Path) -> Optional[Path]:
    """`rel` 在内置副本里的对应文件；没有（或路径非法）返回 None。

    复用 `_resolve` —— 路径穿越防护、`config:` 虚拟路径拒绝、两段式校验全都同一套，
    不新写一行路径逻辑（这也是「重置」唯一的安全边界所在）。
    """
    try:
        return _resolve(rel, defaults_root, must_exist=True)
    except Exception:
        return None


def _describe(group: str, name: str) -> str:
    """文件作用一句话（UI 悬停气泡用；新增文件类型记得补这里）。"""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if group == "sections":
        return {
            "harness_identity": "框架身份与对话基本规则（对所有 NPC 生效）",
            "world_basis": "世界观基础设定（通用背景）",
            "world_persona_rules": "世界内 NPC 人设写作通则",
            "world_rich_text": "回复的排版与着色说明（气泡支持的富文本标签）",
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
    if group == "player":
        return ("你（玩家）是谁：写在这里的设定会作为 system 的一段注入给**所有 NPC**"
                "（存在 config.json 的 player.persona，上限 4000 字）。空 = 不注入；"
                "改完下个回合生效。可写身份、来历、性情、希望被怎么称呼；"
                "与游戏内事实（修为/宗门/关系）冲突时以游戏事实为准")
    return "提示词文件"


def list_prompts(root: Optional[Path] = None, npc_id: Optional[str] = None,
                 defaults: Optional[Path] = None) -> List[Dict[str, Any]]:
    """枚举提示词文件。

    npc_id 为空 → 全量（F11 管理员视图）。
    npc_id 非空 → 仅 全局 + 该Npc 专属（对话内 ⚙ 视图）：
      sections: 全量 4 个（全局）
      personas: default.txt + _suffix.txt + {npc_id}.txt
      traits:   {npc_id}.json
      compaction: default.md + {npc_id}.md（工具结果压缩指令内置，不出现在此）

    每项带两个标记，语义**互不重叠**（UI 据此决定按钮可用性，权威判定仍在各接口内部）：
      deletable  —— 是不是"用户新增件"（能新建的才能删）
      resettable —— 内置副本里有没有同名文件（有才能"重置为默认"）
      注意二者可以同时为 False（sections：既是系统件、又确实有内置默认）。
    """
    base = Path(root) if root is not None else _prompts_root()
    defaults_root = _defaults_root(defaults)
    npc_id = npc_id.strip() if isinstance(npc_id, str) and npc_id.strip() else None
    out: List[Dict[str, Any]] = [{
        # 「自我人设」虚拟条目：**永远排第一**（两种视图都给 —— 它是全局的，不属于任何 NPC）。
        # label 由本模块下发，C# 侧有 label 就用它、没有才拼"分组/文件名"（老端兼容）。
        "group": "player",
        "name": "自我人设",
        "label": "自我人设（玩家）",
        "path": PLAYER_PERSONA_PATH,
        "desc": _describe("player", "player.persona"),
        "deletable": False,        # 它不是文件，删不掉（删 = 清空内容，把编辑框清空再保存）
        # 它有明确定义的"默认"= 空：C# 侧按 path 前缀分流到 set_config（**不调 reset_prompt**，
        # 那个接口对 `config:` 路径显式拒绝）。这里报 True 是为了让重置键在它上面可用。
        "resettable": True,
    }]
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
            rel = f"{group}/{name}"
            out.append({
                "group": group, "name": name, "path": rel,
                "desc": _describe(group, name),
                # 可删标记：与 _allow_create 同规约——sections/default/_suffix=false，
                # 新增文件=true。UI 据此置灰删除按钮；文件枚举自 iterdir，必然存在。
                "deletable": _allow_create(group, name),
                # 可重置标记：内置副本里有没有同名文件。sections 是 true（系统件不许新建，
                # 但**可以还原**）；用户新增的 personas/{npc}.txt / traits/*.json 是 false。
                "resettable": _default_source(rel, defaults_root) is not None,
            })
    return out


def read_prompt(rel: str, root: Optional[Path] = None) -> str:
    """读一个提示词文件全文（UTF-8）。"""
    target = _resolve(rel, root, must_exist=True)
    return _textio.read_text(target)


def write_prompt(rel: str, text: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """写一个提示词文件（全文覆盖，UTF-8 无 BOM + LF）。

    - 已有文件：直接覆盖（文本即真相，下个 turn 热生效）
    - 新文件：仅当分组允许新建且扩展名匹配（personas .txt / traits .json / compaction .md）
    统一走 textio.write_text：换行钉死 LF，否则 Windows 上「读→写」每轮多一层 \\r（见 textio 注释）。
    """
    if not isinstance(text, str):
        raise ValueError("text 必须是字符串")
    target = _resolve(rel, root, must_exist=False)
    if not target.is_file() and not _allow_create(rel.split("/")[0], target.name):
        raise ValueError(f"该分组不允许新建文件: {rel}（sections 只能改已有文件）")
    _textio.write_text(target, text)
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
        _textio.write_text(target, text)
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
    _textio.write_text(target, content)
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


# ---------------------------------------------------------------------------
# 重置为默认（把用户副本还原成随包内置的那一份）
# ---------------------------------------------------------------------------

def is_resettable(rel: str, defaults: Optional[Path] = None) -> bool:
    """该路径在内置副本里有没有对应文件（= 能不能「重置为默认」）。

    与 `is_deletable` **刻意不对称**，别照抄那边的规则：
    - `is_deletable` 是「能新建的才能删」→ sections 为 False；
    - `is_resettable` 问的是「出厂有没有这一份」→ sections 为 **True**：它们不许新建，
      但正是最需要「改坏了能还原」的那批文件。
    用户新增的 personas/{npc}.txt / traits/*.json / compaction/{npc}.md → False
    （内存里没有出厂版本，UI 应引导去用「删除文件」）。

    这里对非法路径（穿越/绝对路径/`config:`）一律返回 False 而不抛 —— 与 is_deletable 同规约。
    """
    return _default_source(rel, _defaults_root(defaults)) is not None


def reset_prompt(rel: str, root: Optional[Path] = None,
                 defaults: Optional[Path] = None) -> Dict[str, Any]:
    """把 `rel` 还原成**随包内置**的默认内容（用户副本被覆盖，不可恢复）。

    参数：`root` = 用户副本根（同其它接口；缺省走 paths.prompts_root()）；
          `defaults` = 内置副本根（缺省走 paths.bundled_prompts_root()；测试用）。

    契约要点（每一条都有测试盯着，别顺手改）：
    - **来源唯一**：只可能是内置副本里的同名文件。没有任何"从用户目录兜底"的分支 ——
      真兜底了就会变成自我拷贝，界面上完全看不出却永远无效。
    - **不套 `_allow_create`**：sections / default / _suffix 可以还原（见 is_resettable）。
    - **不收 text 参数**：结构上不可能被当成"往 sections 写任意内容"的旁路。
    - 目标已存在且**字节相同** → 不写盘（`changed=False`）：避免无谓的 mtime/内容变更
      触发 SystemPrompt 重新组装（每步按内容比对，见 system_prompt 的热更实现）。
    - 目标缺失/整组目录缺失 → 直接补回来（顺带成了"文件丢了"的修复手段）。
    - **写入恒为 UTF-8 无 BOM + LF**（textio.write_text），与手改保存同一形态；
      所以顺手能把 CRLF 污染的文件治好 —— 但若内置那份本身是 CRLF，字节比对会一直判"有变化"，
      故仓库内的提示词文件也一并归一到 LF（tests 有不变量测试守着）。

    返回：{"path","reset":True,"changed":bool,"text":<新内容>,"chars":N,"effective":"hot"}
    `text` 回传是为了让 C# 一次 RPC 就把编辑框刷新到位，不用再 read_prompt 一趟。
    """
    # 先解析目标：非法路径（穿越/绝对路径/`config:` 虚拟条目）在这里就抛出**准确**的错误，
    # 不会被下面"没有默认版本"的友好提示盖掉。
    target = _resolve(rel, root, must_exist=False)

    defaults_root = _defaults_root(defaults)
    src = _default_source(rel, defaults_root)
    if src is None:
        raise ValueError(
            f"该文件没有内置默认版本（不是随包发的文件）: {rel} —— "
            "它多半是你自己新建的（personas/{npc}.txt / traits/*.json / compaction/{npc}.md）；"
            "要清掉请用「删除文件」，删除后相关规则自动回落到全局默认")

    # 开发机布局（或数据根缺失退回包目录时）两个副本同体：重置 = 无操作，如实上报。
    # 不当成错误：源码树里那份 prompts/ 本身就是出厂真相，"重置它"本就该什么也不发生。
    if src.resolve() == target.resolve():
        text = _textio.read_text(src)
        return {"path": rel, "reset": True, "changed": False, "text": text,
                "chars": len(text), "effective": "hot",
                "note": "工作副本即内置副本（源码树/兜底布局），内容未变"}

    text = _textio.read_text(src)
    changed = True
    try:
        if target.is_file() and target.read_bytes() == src.read_bytes():
            changed = False
    except OSError:
        changed = True          # 读不动旧文件（占用/权限）→ 照写，让写入去报真正的错
    if changed:
        _textio.write_text(target, text)
    return {"path": rel, "reset": True, "changed": changed, "text": text,
            "chars": len(text), "effective": "hot"}
