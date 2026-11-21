"""textio — 读写「用户手写文本文件」的统一入口（BOM / 编码 / 换行容错）。

换行契约（2026-09-16 加，配「重置为默认」功能）：
    **读进来恒为 LF，写出去恒为 LF。**
    为什么必须钉死：Windows 上 `Path.write_text()`（text 模式，newline=None）会把 `\n`
    翻译成 `\r\n`，而本模块的 `read_text` 走的是 `read_bytes()` + decode，**不做换行翻译** ——
    两边不对称，于是一份 CRLF 文件每「读→写」一次就多一层 `\r`（实测 4 轮：13→15→17→19 字节）。
    对提示词文件这尤其致命：保存走的是**全量提交**，用户只是「点一下文件 + 点保存」
    （一个字都没编辑）就会中招，文件无限膨胀、system prompt 里塞满 `\r`。
    修法 = 读侧归一 + 写侧 `newline=""`（关掉平台翻译）→ 读写成为不动点，
    `prompts/` 单调收敛到「UTF-8 无 BOM + LF」，与仓库/发行包里那份一致。

为什么需要它（2026-09-13 发行版审计）：
    发行版里 config.json 与 prompts/ 都是**玩家拿记事本/Notepad++/VSCode/WPS 改**的文件，
    而这几家写出来的编码并不一致：
      · Windows 10/11 记事本          → UTF-8 无 BOM
      · 旧系统记事本 / 某些 WPS 导出   → UTF-8 **带 BOM**（或 GBK）
      · PowerShell 5.1 `>` 重定向      → UTF-16 带 BOM
      · Notepad++ 手选                 → 什么都可能
    旧代码一律 `read_text(encoding="utf-8")`：
      · 带 BOM 的 config.json → `json.loads` 直接抛
        `Expecting value: line 1 column 1`——玩家看到的是"mod 一装就崩"，
        而原因只是他在记事本里按了一次保存；
      · 带 BOM 的提示词文件 → BOM 作为不可见字符被拼进 system prompt（静默污染）。
    所以「读用户文件」这件事必须走同一个容错函数，而不是各处自己写 read_text。

各司其职：本模块只管「字节 ↔ 文本」，不解析内容、不知道 json/md/txt 的区别。
"""

from __future__ import annotations

from pathlib import Path

#: 依次尝试的编码。utf-8-sig 放首位：它能同时吃下「带 BOM」与「不带 BOM」的 UTF-8，
#: 且会把 BOM 剥掉（这正是我们要的）。GBK(936) 兜住中文 Windows 的老习惯。
_ENCODINGS = ("utf-8-sig", "gbk")


def normalize_newlines(text: str) -> str:
    """`\\r\\n` 与孤立 `\\r` 一律归一成 `\\n`（与 `Path.read_text()` 的通用换行同语义）。

    先换 `\\r\\n` 再换单个 `\\r`，顺序不能反：反了会把 `\\r\\n` 拆成两个 `\\n`（凭空多一个空行）。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path) -> str:
    """读文本文件，容错顺序 utf-8-sig → gbk → utf-8(替换非法字节)；换行归一为 LF。

    永不因编码抛错：最后一档用 errors="replace" 保证一定有返回值——
    用户文件读出一两个替换符，远好过整个 mod 起不来。
    """
    p = Path(path)
    raw = p.read_bytes()
    for enc in _ENCODINGS:
        try:
            return normalize_newlines(raw.decode(enc))
        except (UnicodeDecodeError, LookupError):
            continue
    return normalize_newlines(raw.decode("utf-8", errors="replace"))


def write_text(path, text: str) -> None:
    """写文本文件：UTF-8 **无 BOM** + 换行**恒为 LF**。

    `newline=""` 是这里的全部要点：关掉「写时把 `\\n` 翻成 `os.linesep`」的平台翻译。
    于是「读 → 写」成为不动点（read_text 也归一成 LF），并且写出来的形态与仓库/发行包里
    那份一致（git 友好、跨平台一致、system prompt 里不会混进 `\\r`）。

    不写 BOM：BOM 对 JSON/prompt 都是静默污染源（见模块头注释）。
    父目录不存在时补建（与 prompt_files.write_prompt 的既有行为一致）。
    """
    p = Path(path)
    if p.parent and not p.parent.is_dir():
        p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text if isinstance(text, str) else str(text))
