#!/usr/bin/env python3
"""归并后的两步收尾（一次性）：① 头注释写清"Python 对应" ② 六个动作工具的开场收口到 ToolArgs.BindActor。"""
import pathlib, re, sys

TOOLS = pathlib.Path(__file__).resolve().parents[2] / "csharp" / "tools"

PYMAP = {
 "QueryTool":   "查询域（`tools/schemas.py` 的「查询域」一段；`READONLY_TOOLS` 三件）",
 "RelationTool":"动作域·关系（`social_relation`）",
 "MovementTool":"动作域·位移（`movement`）",
 "ActionTool":  "动作域·互动（`world_ai_action`，含 join_sect / leave_sect）",
 "EconomyTool": "动作域·财物：赠送 → 买卖（`economy_item` / `trade`）",
 "AcquireTool": "动作域·财物：讨要（`item_acquire`）",
 "TeamTool":    "动作域·组队（`team`）",
}

BIND = '''
        // ---------------------------------------------------------------- 动作工具的共同开场

        /// <summary>
        /// 动作工具的共同开场之一：取发起方（`initiator` 名字 + `initiator_id` 精确 id）。
        /// 失败帧放进 <paramref name="fail"/> —— 调用方一句 `if (bf != null) return bf;` 即可。
        ///
        /// 【为什么收在这里】六个动作工具（关系/位移/互动/赠予/取得/组队）开头这七行**逐字相同**
        /// （11-20 归并时对着六份比对过）：取发起方 → 已故就带死亡事实报错 → 否则报"未找到发起方 X"。
        /// 以前改其中一处的文案，另外五处不会跟着改；而这句话是**给模型看的**，措辞不一致它就会
        /// 按不同的错去重试（"名字打错"和"人已死"是两种完全不同的自纠路径）。
        /// </summary>
        internal static WorldUnitBase BindActor(JObject args, out string initiator, out JObject fail)
        {
            initiator = (string)args["initiator"] ?? "";
            var wub = ResolveActor(args);
            if (wub != null) { fail = null; return wub; }
            // 死者兜底（10-15 待办②）：发起方已故 ⇒ 带死亡事实报错，别让模型当成名字打错
            string dn = UnitSnapshot.DeadNotice((string)args["initiator_id"] ?? "", initiator);
            fail = dn != null ? Fail(dn) : Fail("未找到发起方 " + initiator);
            return null;
        }
'''

START = 'string initiator = (string)args["initiator"] ?? "";'
END = 'return Fail("未找到发起方 " + initiator);'
NEW = ('var wub = ToolArgs.BindActor(args, out string initiator, out JObject bindFail);\n'
       '            if (bindFail != null) return bindFail;')


def main():
    for cls, where in PYMAP.items():
        p = TOOLS / f"{cls}.cs"; s = p.read_text(encoding="utf-8")
        line = f"/// 【Python 对应】{where} —— 划分以 Python 侧为准，别在这里另立一套分类。\n"
        if "【Python 对应】" not in s:
            i = s.index("/// </summary>")
            p.write_text(s[:i] + line + s[i:], encoding="utf-8")

    p = TOOLS / "ToolArgs.cs"; s = p.read_text(encoding="utf-8")
    anchor = "        /// <summary>发起方（`initiator`）"
    if "BindActor" not in s:
        assert anchor in s
        s = s.replace(anchor, BIND.strip("\n") + "\n\n" + anchor, 1)
        p.write_text(s, encoding="utf-8")

    # 分发表里 Movement 属于 MovementTool（归并时它保持独立），修掉前缀重写的误伤
    p = TOOLS / "ToolExecutor.cs"; s2 = p.read_text(encoding="utf-8")
    s2 = s2.replace("ActionTool.Movement(args)", "MovementTool.Movement(args)")
    p.write_text(s2, encoding="utf-8")

    for f in ("RelationTool.cs", "MovementTool.cs", "ActionTool.cs",
              "EconomyTool.cs", "AcquireTool.cs", "TeamTool.cs"):
        p = TOOLS / f; s = p.read_text(encoding="utf-8"); n = 0
        while True:
            i = s.find(START)
            if i < 0:
                break
            j = s.find(END, i)
            assert j > 0, f
            k = s.find("\n", j) + 1
            m = re.match(r"[ \t]*\}[ \t]*\n", s[k:])
            assert m, f
            k += m.end()
            s = s[:i] + NEW + "\n" + s[k:]
            n += 1
        p.write_text(s, encoding="utf-8")
        print(f"  {f}: 开场收口 {n} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
