/// <summary>
/// 自主交互候选排序键（11-13）—— 纯逻辑，零 Unity / 零 Newtonsoft。
///
/// ## 为什么值得单独一个文件
///
/// 用户 11-13 的需求是「被传话的 npc 进入一个优先队列，优先进行自主交互，优先队列比好感度
/// 同格之类的优先级都要高」，并明确了边界：「**只改变排序**（仍受一日一次限制），
/// 不会跳过 daily_chance」。
///
/// 这条需求的正确性全在**一个比较函数**上，而它出错的方式**全都不报错**：
///   · 优先没生效 → 收信人照样被好感排序压到后面，表现与"没做这个功能"一模一样；
///   · 优先过度   → 只要有人捎过话就天天触发，玩家体感是"这 NPC 抽风了"；
///   · 污染好感   → 若把加成加进好感数值本身，`PickIntent`（按好感选意图池）与
///                  `BuildReason`（按好感选措辞）就会读到假值：仇人走进正向池、
///                  话术变成"念及与你多日情谊"。
///
/// 所以判据从 `NpcInitiativeMonitor`（依赖 Unity，测不了）搬到这里，
/// 由 `InitiativeSortCoreTests` 直接钉住。监控器那边只做数据准备与副作用。
///
/// ## 判据（逐条对应上面的错法）
///
///   ① **先看 HasMail，再看好感** —— 有未读捎话的人排在所有无信者之前；
///   ② **有信者之间仍按好感降序** —— 优先只是"提前"，不是"重新洗牌"；
///   ③ **比较函数不修改任何输入** —— 尤其不碰 `Intim`（见上面"污染好感"那条）。
///
/// 洗牌（等值段内随机打散）**刻意留在这里**而不是调用方：它的分段判据必须与比较键
/// 同构，分开放就会漂移 —— 一旦洗牌段只按好感分段，同好感时"有信者"会被洗到后面，
/// 而这种失效只在**恰好同好感**时出现，极难复现。
/// </summary>
using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    /// <summary>候选行的**排序相关**字段（其余字段由监控器的 Row 自己持有）。</summary>
    public struct InitiativeSortKey
    {
        /// <summary>与玩家的好感（-300~300）。**只读，比较函数不许改它。**</summary>
        public int Intim;
        /// <summary>该 NPC 有未读捎话（信箱优先队列）。</summary>
        public bool HasMail;

        public InitiativeSortKey(int intim, bool hasMail)
        {
            Intim = intim;
            HasMail = hasMail;
        }
    }

    public static class InitiativeSortCore
    {
        /// <summary>
        /// 比较两行的排序先后：负数 = a 在前。
        ///
        /// ★ 判据顺序不可换 ★ `HasMail` 必须先于 `Intim` —— 这正是"优先队列比好感度
        /// 同格之类的优先级都要高"那一句的实现。反过来的话，一个 300 好感的道侣会
        /// 永远压住一个 0 好感的收信人，而**不会有任何报错**。
        /// </summary>
        public static int Compare(InitiativeSortKey a, InitiativeSortKey b)
        {
            if (a.HasMail != b.HasMail) return a.HasMail ? -1 : 1;
            return b.Intim.CompareTo(a.Intim);      // 好感降序
        }

        /// <summary>
        /// 排序 + 等值段内洗牌。**洗牌段的分段判据必须与 `Compare` 同构**
        /// （同好感 **且** 同 HasMail 才算一段）—— 否则优先序会在"恰好同好感"时被抹掉。
        ///
        /// `rand` 传 `UnityEngine.Random.Range(a, b+1)` 的等价物（[a, b] 闭区间整数），
        /// 单测传固定序列即可复现。返回排序后的下标数组（调用方按它重排自己的行）。
        /// </summary>
        public static int[] SortOrder(IList<InitiativeSortKey> keys, Func<int, int, int> rand)
        {
            if (keys == null || keys.Count == 0) return new int[0];
            var idx = new int[keys.Count];
            for (int i = 0; i < idx.Length; i++) idx[i] = i;
            Array.Sort(idx, (x, y) => Compare(keys[x], keys[y]));

            // Fisher-Yates，只在「好感与 HasMail 都相同」的连续段内交换。
            int s = 0;
            while (s < idx.Length)
            {
                int e = s;
                while (e + 1 < idx.Length
                       && keys[idx[e + 1]].Intim == keys[idx[s]].Intim
                       && keys[idx[e + 1]].HasMail == keys[idx[s]].HasMail) e++;
                if (e > s && rand != null)
                {
                    for (int k = e; k > s; k--)
                    {
                        int m = rand(s, k);
                        if (m < s) m = s;
                        if (m > k) m = k;
                        int t = idx[k]; idx[k] = idx[m]; idx[m] = t;
                    }
                }
                s = e + 1;
            }
            return idx;
        }
    }
}
