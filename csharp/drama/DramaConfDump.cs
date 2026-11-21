/// <summary>
/// DramaConfDump —— 剧情窗「选项到底从哪来」的取证探针（10-18，一次性诊断）。
///
/// 现场（用户截图 + `Player.log` 实证）
/// ------------------------------------------------------------------------------------
/// 自主交互确认窗（`dramaId = Mod+170 = -803158281`）实机建出 **4 枚真按钮**：
///
///     [同意] [不同意] [确定（占位，运行时覆盖）] [取消（占位，运行时覆盖）]
///
/// 后一对是本 mod `ModExcel/LocalText` 里的**壳占位文案**（`agentloop_opt_1x1/1x2`
/// =「确定（占位，运行时覆盖）」/「取消（占位，运行时覆盖）」），设计上必须被
/// `ShowDramaService` 的 `dialogueOptions[win+1]/[win+2]` 覆盖掉 —— 它俩露出来，
/// 说明「我们写进去的那对 id」和「窗口真正渲染的那对 id」不是同一对（或覆盖机制另有要求）。
///
/// 本探针把**三侧真值**一次打全，一条日志即可定案
/// ------------------------------------------------------------------------------------
///   ① 表侧：本 mod 全部剧情段（+100…+190）的 `ConfDramaDialogue` 行 —— `options` 原文 +
///      逐项 `ConfDramaOptions.text`（LocalText 键）。判据：`win` 那行解析出的 id 若不等于
///      `[win+1, win+2]` ⇒ **表/代码不同源**（改表，或重导出）。
///   ② 窗口侧：本页 `item.options` / `activeOptionsData`（游戏真正在渲染的选项清单）/
///      容器里每枚按钮的**物理状态**（激活、父级、同级序号、锚点坐标、尺寸、文本）。
///      判据：`activeOptionsData` 有几项、分别是什么 id ⇒ 多出来的按钮是谁生的；
///      「啊啊啊…」那枚若 `activeInHierarchy=False` 或坐标停在屏外 ⇒ 是**模板/占位节点**，
///      不是真选项（用户截图里没有它，可交叉验证）。
///   ③ 写入侧：`dramaData.dialogueOptions` 里**实际存在哪些键**（按 key 探针式查询，
///      不依赖字典枚举 API）⇒ 证明我们写的两条到底进没进这个对象。
///
/// 为什么用「按 key 探」而不是枚举：`Il2CppSystem.Collections.Generic.Dictionary` 的
/// 枚举器在 Unhollower 下不保证可用（枚举失败就白跑一趟）；`ContainsKey` + 索引器是稳的。
///
/// 全程 try/catch + 只读：任何一步炸了都只打一行日志，绝不影响开窗与点击链。
/// </summary>
using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.UI;

namespace AgentLoopBridge
{
    internal static class DramaConfDump
    {
        /// <summary>本 mod 的剧情段基址偏移（与 `ModIds` 分段总表同源；探针只读，故意冗余写死）。
        /// `+190` 是 team 重伤剧情（无选项），一并看。</summary>
        private static readonly int[] SegOffsets = { 100, 110, 120, 130, 140, 150, 160, 170, 180, 190 };

        /// <summary>表侧真值只打一次（每局第一次开剧情窗时）——它不以窗口为转移。</summary>
        private static bool _tablesDone;

        /// <summary>`UIDramaBase.CreateOptions` postfix 调（`__instance` = 本页窗口）。</summary>
        internal static void NoteWindow(UIDramaBase ui, GameObject p0, GameObject p1)
        {
            try
            {
                if (!_tablesDone) { _tablesDone = true; DumpTables(); }
            }
            catch (Exception e) { Log("表侧取证抛异常: " + e.Message); }

            try { DumpWindow(ui, p0, p1); }
            catch (Exception e) { Log("窗口侧取证抛异常: " + e.Message); }
        }

        // ------------------------------------------------------------------
        // ① 表侧：本 mod 全部剧情段
        // ------------------------------------------------------------------

        private static void DumpTables()
        {
            Log("========== 表侧真值（ModIds.Mod=" + ModIds.Mod + "）==========");
            foreach (int off in SegOffsets)
            {
                int win = ModIds.Mod + off;
                ConfDramaDialogueItem it = null;
                try { it = g.conf.dramaDialogue.GetItem(win); }
                catch (Exception e) { Log("段+" + off + " win=" + win + " GetItem 抛异常: " + e.Message); }

                if (it == null)
                {
                    Log("段+" + off + " win=" + win + " ⇒ ⚠ 配置表**没有这一行**（壳条目缺失，开窗会静默失败）");
                    continue;
                }

                string raw = Str(() => it.options);
                Log("段+" + off + " win=" + win
                    + " dialogue=" + Str(() => it.dialogue)
                    + " options原文=\"" + raw + "\"");

                int[] ids = ParseIds(raw);
                var sb = new StringBuilder();
                foreach (int id in ids) sb.Append(id).Append(' ');
                Log("      解析出 " + ids.Length + " 个选项 id=[" + sb.ToString().Trim() + "]"
                    + "｜本 mod 约定=[win+1, win+2]=[" + (win + 1) + ", " + (win + 2) + "]"
                    + (ids.Length == 2 && ids[0] == win + 1 && ids[1] == win + 2 ? " ✓一致" : " ⚠不一致"));

                foreach (int id in ids)
                {
                    ConfDramaOptionsItem oi = null;
                    try { oi = g.conf.dramaOptions.GetItem(id); } catch { }
                    if (oi == null)
                    {
                        Log("      └ 选项 " + id + " ⇒ ⚠ DramaOptions 无此行（按钮文本取不到）");
                        continue;
                    }
                    Log("      └ 选项 " + id + " text=" + Str(() => oi.text) + "（LocalText 键）"
                        + " optionsHide=" + Num(() => oi.optionsHide)
                        + " condition=" + Str(() => oi.condition)
                        + " function=" + Str(() => oi.function));
                }
            }
        }

        // ------------------------------------------------------------------
        // ② 窗口侧 + ③ 写入侧：本页
        // ------------------------------------------------------------------

        private static void DumpWindow(UIDramaBase ui, GameObject p0, GameObject p1)
        {
            Log("========== 窗口侧真值 ==========");

            ConfDramaDialogueItem item = null;
            try { item = ui == null ? null : ui.item; } catch { }
            if (item == null) Log("本页 ui.item 读不到（null）");
            else
            {
                string raw = Str(() => item.options);
                int[] ids = ParseIds(raw);
                var sb = new StringBuilder();
                foreach (int id in ids) sb.Append(id).Append(' ');
                Log("本页 item.id=" + Num(() => item.id) + " dialogue=" + Str(() => item.dialogue)
                    + " options原文=\"" + raw + "\" 解析=[" + sb.ToString().Trim() + "]");
            }

            // 游戏真正在渲染的选项清单（权威）
            try
            {
                var list = ui == null ? null : ui.activeOptionsData;
                if (list == null) Log("activeOptionsData = null（读不到）");
                else
                {
                    Log("activeOptionsData 共 " + list.Count + " 项：");
                    for (int i = 0; i < list.Count; i++)
                    {
                        ConfDramaOptionsItem oi = null;
                        try { oi = list[i]; } catch (Exception e) { Log("  #" + i + " 取项抛异常: " + e.Message); continue; }
                        if (oi == null) { Log("  #" + i + " = null"); continue; }
                        Log("  #" + i + " id=" + Num(() => oi.id) + " text=" + Str(() => oi.text)
                            + " optionsHide=" + Num(() => oi.optionsHide)
                            + " condition=" + Str(() => oi.condition));
                    }
                    // ★ 不变量（10-18 清壳修复的守门行）★
                    //   自制窗的选项数必须 == 我们字典里写进去的条数。多出来 = 表侧壳选项又长回来了
                    //   （LocalText 补全了文案 / `options` 没被清成 "0"）—— 用户看到的正是
                    //   「确定（占位，运行时覆盖）」那对多出来的按钮（10-18 截图）。
                    //   本行只告警、不修；真正的修在 `ShowDramaService.ClearShellOptions`。
                    int mine = 0;
                    try { mine = ui == null || ui.dramaData == null ? 0 : ui.dramaData.dialogueOptions.Count; } catch { }
                    if (mine > 0 && list.Count != mine)
                        Log("⚠ 选项数不符：activeOptionsData=" + list.Count + " vs dialogueOptions=" + mine
                            + " ⇒ 表侧壳选项没被清掉（查 ShowDramaService.ClearShellOptions / LocalText 是否补全）");
                    else if (mine > 0)
                        Log("✓ 选项数一致（" + mine + " 枚）—— 表侧壳已清，只渲染我们写的那对");
                }
            }
            catch (Exception e) { Log("activeOptionsData 取证失败: " + e.Message); }

            // ③ 写入侧：dramaData.dialogueOptions 里实际有哪些键（按 key 探，不依赖枚举）
            try
            {
                var dd = ui == null ? null : ui.dramaData;
                if (dd == null) Log("ui.dramaData = null（读不到）");
                else
                {
                    int winId = 0;
                    try { winId = item == null ? 0 : item.id; } catch { }
                    Log("本页 win=" + winId + "（按 key 探询，不打全字典）");
                    Log("dramaData.dialogueText 键=" + ProbeKeys(KeysOf(() => dd.dialogueText), winId));
                    Log("dramaData.dialogueOptions 命中=" + ProbeKeys(KeysOf(() => dd.dialogueOptions), winId));
                }
            }
            catch (Exception e) { Log("dramaData 取证失败: " + e.Message); }

            // ② 容器实物：每枚按钮的物理状态与文本
            try
            {
                var stat = new List<string>();
                Collect(p0, stat);
                Collect(p1, stat);
                Log("容器按钮 " + stat.Count + " 枚（p0=" + NodeName(p0) + " / p1=" + NodeName(p1) + "）：");
                for (int i = 0; i < stat.Count; i++) Log("  #" + i + " " + stat[i]);
            }
            catch (Exception e) { Log("容器按钮取证失败: " + e.Message); }
        }

        /// <summary>候选键集合：本段 +1..+9（本 mod 约定）∪ 全部段的 +1/+2（判"是不是写了别段的选项"）。</summary>
        private static List<int> CandidateKeys(int win)
        {
            var keys = new List<int>();
            if (win != 0)
            {
                for (int k = 1; k <= 9; k++) keys.Add(win + k);
                keys.Add(win + 50); keys.Add(win + 51); keys.Add(win + 52);
            }
            foreach (int off in SegOffsets)
            {
                keys.Add(ModIds.Mod + off + 1);
                keys.Add(ModIds.Mod + off + 2);
            }
            return keys;
        }

        /// <summary>把字典里**命中的键值**打出来（`ContainsKey` + 索引器；枚举 API 不可靠，故意不用）。
        /// 顺带把这些键"归属哪个段"标出来，一眼看出是不是写错了段。</summary>
        private static string ProbeKeys(object dict, int win)
        {
            if (dict == null) return "(字典 null)";
            var hit = new StringBuilder();
            int n = 0;
            foreach (int k in CandidateKeys(win))
            {
                bool has = false;
                try { has = ((dynamic)dict).ContainsKey(k); } catch { continue; }
                if (!has) continue;
                string v = "?";
                try { v = (string)((dynamic)dict)[k]; } catch { }
                hit.Append('[').Append(k).Append("]=\"").Append(Short(v)).Append("\"(").Append(SegOf(k, win)).Append(") ");
                n++;
            }
            return n == 0 ? "0 项（⚠ 一个候选键都不在）" : (n + " 项：" + hit.ToString().Trim());
        }

        /// <summary>键的归属说明：`本段+1` / `+100段+2` / `其它`。</summary>
        private static string SegOf(int key, int win)
        {
            if (win != 0 && key > win && key <= win + 9) return "本段+" + (key - win);
            foreach (int off in SegOffsets)
            {
                if (key == ModIds.Mod + off + 1) return "+" + off + "段+1";
                if (key == ModIds.Mod + off + 2) return "+" + off + "段+2";
            }
            return "其它";
        }

        /// <summary>收集容器下所有"带文本的 Button"，输出一行物理状态（激活/父级/同级/坐标/尺寸/文本）。</summary>
        private static void Collect(GameObject root, List<string> outRows)
        {
            if (root == null) return;
            try
            {
                var btn = root.GetComponent<Button>();
                if (btn != null)
                {
                    string label = ReadLabel(root);
                    if (!string.IsNullOrEmpty(label)) outRows.Add(Describe(btn, label));
                }
            }
            catch { }
            try
            {
                int c = root.transform.childCount;
                for (int i = 0; i < c; i++) Collect(root.transform.GetChild(i).gameObject, outRows);
            }
            catch { }
        }

        private static string Describe(Button btn, string label)
        {
            var go = btn.gameObject;
            string name = NodeName(go);
            string parent = "?";
            try { parent = go.transform.parent == null ? "(无父)" : go.transform.parent.name; } catch { }
            string sibling = "?";
            try { sibling = go.transform.GetSiblingIndex().ToString(); } catch { }
            string active = "?", inHier = "?";
            try { active = go.activeSelf ? "True" : "False"; } catch { }
            try { inHier = go.activeInHierarchy ? "True" : "False"; } catch { }
            string pos = "?", size = "?";
            try
            {
                var rt = go.GetComponent<RectTransform>();
                if (rt != null)
                {
                    pos = "(" + rt.anchoredPosition.x.ToString("0.#") + "," + rt.anchoredPosition.y.ToString("0.#") + ")";
                    size = "(" + rt.sizeDelta.x.ToString("0.#") + "," + rt.sizeDelta.y.ToString("0.#") + ")";
                }
            }
            catch { }
            string interact = "?";
            try { interact = btn.interactable ? "可点" : "不可点"; } catch { }
            return "name=" + name + " active=" + active + " inHierarchy=" + inHier
                 + " parent=" + parent + " sibling=" + sibling
                 + " pos=" + pos + " size=" + size + " " + interact
                 + " text=\"" + Short(label) + "\"";
        }

        /// <summary>文本两级读法（激活优先，再含非激活）——与 `DramaAiOption.ReadButtonLabel` 同源。</summary>
        private static string ReadLabel(GameObject go)
        {
            try { var t = go.GetComponentInChildren<Text>(); if (t != null && !string.IsNullOrEmpty(t.text)) return t.text.Trim(); } catch { }
            try { var p = go.GetComponentInChildren<TMPro.TextMeshProUGUI>(); if (p != null && !string.IsNullOrEmpty(p.text)) return p.text.Trim(); } catch { }
            try { var t = go.GetComponentInChildren<Text>(true); if (t != null && !string.IsNullOrEmpty(t.text)) return t.text.Trim(); } catch { }
            try { var p = go.GetComponentInChildren<TMPro.TextMeshProUGUI>(true); if (p != null && !string.IsNullOrEmpty(p.text)) return p.text.Trim(); } catch { }
            return "";
        }

        // ------------------------------------------------------------------
        // 小工具
        // ------------------------------------------------------------------

        /// <summary>把 `options` 列拆成 id 列表。三种写法都吃：`171|172`（本 mod 表内写法）、
        /// `-803158280|-803158279`（导出展开后的真值）、`MID&amp;171|MID&amp;172`（未展开）。</summary>
        private static int[] ParseIds(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return new int[0];
            var list = new List<int>();
            foreach (string tok0 in raw.Split('|'))
            {
                string tok = tok0.Trim();
                if (tok.Length == 0 || tok == "0") continue;
                int v;
                if (tok.StartsWith("MID&", StringComparison.OrdinalIgnoreCase))
                {
                    if (int.TryParse(tok.Substring(4), out v)) { list.Add(ModIds.Mod + v); continue; }
                    continue;
                }
                if (int.TryParse(tok, out v)) list.Add(v);
            }
            return list.ToArray();
        }

        private static string NodeName(GameObject go)
        {
            try { return go == null ? "(null)" : go.name; } catch { return "(?)"; }
        }

        private static string Short(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Length > 40 ? s.Substring(0, 40) + "…(" + s.Length + "字)" : s;
        }

        private static string Str(Func<string> f)
        {
            try { return f() ?? ""; } catch { return "(读不到)"; }
        }

        private static string Num(Func<int> f)
        {
            try { return f().ToString(); } catch { return "(读不到)"; }
        }

        /// <summary>`() =&gt; (object)dict` 形式的取值（泛型字典的静态类型不好写死，统一走 object）。</summary>
        private static object KeysOf(Func<object> f)
        {
            try { return f(); } catch { return null; }
        }

        private static void Log(string msg)
        {
            try { ModMain.P("[DramaConfDump] " + msg); } catch { }
        }
    }
}
