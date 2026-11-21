# -*- coding: utf-8 -*-
"""prompt_lab.empty_promises — 扫「话说满了、工具一次没调」的回合。

判的不是"该不该调"（决定权在模型：不送、讲条件、反问都算正常演绎），
判的是**言行一致**：台词里已经把事说成做了，这一轮却没有任何对应的工具调用。

用法：
    python scripts/dev/prompt_lab/empty_promises.py F:/.agent_loop_build/prompt_lab/*.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 类别 → (说成做了的措辞, 能兑现它的工具)。
# ★ 措辞一律取**当场承诺式/完成式**：`送你`/`赠你`/`依你` 这类太宽（回忆往事、讲条件、
#   许未来都会命中），命中的也只是"疑似"，报告里连原句一起出，由人过一眼再算数。
_CLAIMS: List[Tuple[str, List[str], re.Pattern]] = [
    ("当场给出", ["economy_item", "item_acquire", "trade"],
     re.compile(r"(给你了|收好吧|收下来吧|拿去用|这就给你|已然给你|送你了|便依你|既开了口，便依你|成交了|就这么定了)")),
    ("当场动身", ["movement"],
     re.compile(r"(这就动身|即刻动身|我这就过去|马上过去|已经到了|我赶过来)")),
    ("当场定名分", ["social_relation", "world_ai_action"],
     re.compile(r"(好，?我收你为徒|我应下这门师徒|我与你结为道侣|这门亲我应了)")),
    # 单独一类：不是"已经办了"，是"回头办" —— 算不算违规由玩家口径定，所以分开计数
    ("承诺将来给", ["economy_item", "item_acquire", "trade"],
     re.compile(r"(托人送去|让人送去|改日送|亲自送来|给你捎|回头.*送|替你留着)")),
    ("当场施为", ["world_ai_action"],
     re.compile(r"(接好了，这是|功法传给你了|我替你疗伤|来与我一论)")),
]


def violations(run: Dict[str, Any]) -> List[str]:
    """逐类判：话说到"这件事已经办成"，这一轮里就得有**那件**工具的调用。

    调过别的工具不算数 —— 查完背包说"便给你两枚"却没调 economy_item，正是要抓的形态。
    """
    called = {c.get("name") for c in run.get("calls") or []}
    text = run.get("final_text") or " ".join(
        s.get("text", "") for s in run.get("steps") or [])
    if not text.strip():
        return []
    return [name for name, tools, pat in _CLAIMS
            if pat.search(text) and not (called & set(tools))]


def main() -> int:
    files = [Path(a) for a in sys.argv[1:] if a.endswith(".json")] or \
            sorted(Path("F:/.agent_loop_build/prompt_lab").glob("*.json"))
    print(f"{'结果文件':<26}{'样本':<7}{'疑似空口':<10}分类")
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        runs = data.get("results")
        if not isinstance(runs, list):
            continue
        if runs and "runs" in runs[0]:            # 合成套件的报告形状
            runs = [dict(r, player=x.get("id"), **{k: v for k, v in r.items() if k != "final_text"})
                    for x in runs for r in (x.get("runs") or []) if isinstance(r, dict)]
        bad = [(r["player"], c) for r in runs for c in violations(r)]
        n = len(runs)
        if not n:
            continue
        tag = f.name.replace(".json", "")[:25]
        cats = Counter(c for _, c in bad)
        print(f"{tag:<26}{n:<7}{len(bad):<10}{dict(cats) or '-'}")
        for r in runs:
            for name, tools, pat in _CLAIMS:
                m = pat.search(r.get("final_text") or " ".join(
                    s.get("text", "") for s in r.get("steps") or []))
                if m and not ({c.get("name") for c in r.get("calls") or []} & set(tools)):
                    txt = r.get("final_text") or ""
                    i = max(0, m.start() - 22)
                    print(f'      · 「{c if (c:=name) else ""}」{r["player"][:16]} …'
                          f'{txt[i:m.end() + 20].replace(chr(10), " ")}…')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
