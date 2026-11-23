"""发布前体检：仓库（按 `git ls-files`）里有没有不该外发的凭据 / 本机路径 / 具体厂商端点。

用法： python scripts/dev/check_no_secrets.py
退出码 0 = 干净；1 = 有命中（逐行打印文件:行号）。

为什么按 `git ls-files` 而不是遍历目录：遍历会把 `.venv/`、`.ruff_cache/`、
`config.json` 这些本地产物一起扫进来，噪声淹没有效信号。发布包是按 git 清单打的，
所以体检也应该盯同一份清单。

为什么用 Python 而不是 `grep`：git 对非 ASCII 路径会输出八进制转义，
shell 侧 `Select-String` / `while read` 拿到的是转义串而不是真路径 —— 中文目录会
全报"找不到文件"，看起来像"扫描通过"，其实是没扫。
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys

PATTERNS = {
    # 各家常见的 key 形态
    "疑似 API key（user_ 前缀）": re.compile(r"\buser_[A-Za-z0-9]{20,}"),
    "疑似 API key（sk- 前缀）": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "疑似 GitHub token": re.compile(r"\b(ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    "疑似 AWS key": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "Bearer 字面值": re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{24,}"),
    # 本机痕迹。注意排除 `<你的用户名>` 这类占位符写法（文档里本来就该这么写），
    # 只抓"后面跟了一段真名字"的形态。
    "本机绝对路径": re.compile(r"[A-Za-z]:\\+Users\\+(?!<)[^\\/\s\"'<>]+"),
    # 具体厂商端点（发布版只该出现 <gateway> 占位符）
    "具体厂商端点": re.compile(r"[a-z0-9-]+\.(?:ai|com|io|dev)/provider"),
}

# 允许的例外（占位符与示例值）。命中这些就不算问题。
# 后三条是文档里**故意**写的占位（`C:\\Users\\<你的用户名>`）与
# `scripts/export_public.py` 里那条正则自己的源码文本 —— 它们不是真路径。
ALLOW = re.compile(
    r"<gateway>|<model-id>|<provider>|<api_key>|sk-verysecret-0123456789|"
    r"config\.example\.json|你的用户名|\[A-Za-z0-9_\]+|Users\\\\\.\.\."
)

# 不参与体检的路径：文档截图是二进制、示例配置本来就要写字段名
SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ab", ".dll", ".exe")


def main() -> int:
    out = subprocess.run(["git", "ls-files"], capture_output=True)
    if out.returncode != 0:
        print("不是 git 仓库，或 git 不可用", file=sys.stderr)
        return 2
    files = [f for f in out.stdout.decode("utf-8").split("\n") if f.strip()]

    total = 0
    for name, rx in PATTERNS.items():
        hits = []
        for rel in files:
            if rel.endswith(SKIP_SUFFIX) or not os.path.isfile(rel):
                continue
            try:
                s = io.open(rel, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in rx.finditer(s):
                if ALLOW.search(m.group(0)):
                    continue
                hits.append("%s:%d  %s" % (rel.replace(os.sep, "/"),
                                           s[:m.start()].count("\n") + 1,
                                           m.group(0)[:70]))
        print("[%s] %d 处" % (name, len(hits)))
        for h in hits:
            print("    " + h)
        total += len(hits)

    print("-" * 60)
    print("合计 %d 处" % total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
