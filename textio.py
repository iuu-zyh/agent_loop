"""textio — 读「用户手写文本文件」的统一入口（BOM / 编码容错）。

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

各司其职：本模块只管「字节 → 文本」，不解析内容、不知道 json/md/txt 的区别。
"""

from __future__ import annotations

from pathlib import Path

#: 依次尝试的编码。utf-8-sig 放首位：它能同时吃下「带 BOM」与「不带 BOM」的 UTF-8，
#: 且会把 BOM 剥掉（这正是我们要的）。GBK(936) 兜住中文 Windows 的老习惯。
_ENCODINGS = ("utf-8-sig", "gbk")


def read_text(path) -> str:
    """读文本文件，容错顺序 utf-8-sig → gbk → utf-8(替换非法字节)。

    永不因编码抛错：最后一档用 errors="replace" 保证一定有返回值——
    用户文件读出一两个替换符，远好过整个 mod 起不来。
    """
    p = Path(path)
    raw = p.read_bytes()
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")
