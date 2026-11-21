/// <summary>
/// 「自定义经历」模板的运行时注册 —— **全仓唯一一份**。
///
/// 【为什么要有它】
/// `RoleLogLocal` 里那些行（`MID&amp;904~907` 组队四条、`MID&amp;908~910` 决斗三条、宗门入/退两条）
/// 是运行时按需注册的：表里有就直接用，没有就**克隆 `MID&amp;1` 改 id/keyID/ch** 再 `AddItem`。
/// 这段"查表 → 克隆 → 设字段 → AddItem → 回读 → 补按键索引"原先有两份
/// （`tools/TeamBattleFollowup.EnsureLogTemplate` 与 `drama/NpcDuel.EnsureTpl`），
/// 并且**已经漂移**：只有 NpcDuel 那份调了 `PatchKeyIndex`。
///
/// 【漂移的后果（这不是风格问题）】
/// `ConfRoleLogLocal::AddItem`@0x4A21950 的尾部只有一句
/// `jmp System.Collections.Generic.List::Add`（往 `allConfList(+0x50)` 追加），
/// **没有**更新 `allItemInKey(+0x40)` —— 那个字典只在 `OnInit` 里建一次。反证：
/// `ConfRoleLogLocal.OnInit` = 209 字节（建索引），`AddItem` = **8 字节**（放不下建索引）。
/// 于是只走 `AddItem` 的行：`GetItem(id)` 查得到（走 `allConfDic`），但写经历是走**字符串 key**
/// 查 `allItemInKey` 的 —— 查不到就只打一行 `找不到日志：eventLog…` 然后**静默丢弃**，
/// 真机形态正是"模板注册成功、写经历全部未落库"。
/// 10-26 在 NpcDuel 那条路上踩出来并补了 `PatchKeyIndex`；`TeamBattleFollowup` 那条路一直没补，
/// 10-28 的审查已指出，11-19 复核仍未修 —— 而且调用面变大了（`ToolExecutor` 的宗门入/退也走它）。
///
/// 【`AddItem` 到底补不补 `allItemInKey`】
/// **不补**（反编区间如上）。旧注释里「`AddItem` 的尾部正是往 `allItemInKey` 里再塞一份」
/// 与反编事实相反 —— 那句话是 10-28 审查抓到的错，别再写回去。
///
/// 【线程】主线程（改配表对象）；全程 try/catch，坏掉最坏是"经历写不进去"，日志里看得见。
/// </summary>
using System;
using System.Collections.Generic;
using System.Reflection;

namespace AgentLoopBridge
{
    internal static class RoleLogTemplate
    {
        /// <summary>已确认可用的模板 id（查表命中 / 运行时注册成功，都进这里）。</summary>
        private static readonly HashSet<int> _ready = new HashSet<int>();

        /// <summary>
        /// 确保某条"自定义经历"的文案可用。
        /// </summary>
        /// <param name="id">`RoleLogLocal` 行 id（必须登记在 `ModIds`，ID 空间唯一事实来源）。</param>
        /// <param name="key">字符串 key（引擎 `AddLogData` 查的是它，不是 id）。</param>
        /// <param name="ch">正文模板（占位符 `{0}`/`{1}`… 由 `GameTool::StrPar` 替换）。</param>
        /// <param name="label">日志里给人看的名字（"副本见闻"/"决斗同行"…）。</param>
        /// <param name="tag">日志前缀（沿用调用方类自己的 Tag，保持 `Player.log` 里前缀稳定）。</param>
        public static bool EnsureLogTemplate(int id, string key, string ch, string label, string tag)
        {
            if (_ready.Contains(id)) return true;
            tag = tag ?? "";
            try
            {
                var conf = g.conf != null ? g.conf.roleLogLocal : null;
                if (conf == null) { ModMain.P(tag + "模板注册失败（" + label + "）：g.conf.roleLogLocal 为空"); return false; }

                // ① 表里已经有了（重新导出过 mod）⇒ 直接用；这一路不需要补索引（OnInit 建过）
                try
                {
                    var existing = conf.GetItem(id);
                    if (existing != null && !string.IsNullOrEmpty(existing.ch))
                    {
                        _ready.Add(id);
                        ModMain.P(tag + "模板[" + label + "]：用表里的 " + id);
                        return true;
                    }
                }
                catch { }

                // ② 运行时注册：克隆 `MID&1`（同行那条），只改 id/keyID/ch
                var proto = conf.GetItem(ModIds.LogTeamDungeonDrama - 903);
                if (proto == null) { ModMain.P(tag + "模板注册失败（" + label + "）：克隆源 MID&1 取不到"); return false; }

                var row = proto.Clone();
                if (row == null) { ModMain.P(tag + "模板注册失败（" + label + "）：Clone() 返回空"); return false; }
                row.id = id;
                row.keyID = key;
                row.ch = ch;
                row.tc = ch;
                row.en = ch;
                row.kr = ch;
                conf.AddItem(row);

                // ③ 回读确认（不确认就等于不知道）
                var back = conf.GetItem(id);
                if (back == null || string.IsNullOrEmpty(back.ch))
                {
                    ModMain.P(tag + "❌ 模板[" + label + "]注册后回读不到（id=" + id + "）⇒ 这条经历写不进去");
                    return false;
                }

                // ④ 补按键索引（**不是可选项**，理由见文件头）
                PatchKeyIndex(conf, row, label, tag);

                _ready.Add(id);
                ModMain.P(tag + "模板[" + label + "]：运行时注册成功 id=" + id + " key=" + key
                          + " ch=" + back.ch + "（建议后续固化进 RoleLogLocal.xlsx）");
                return true;
            }
            catch (Exception e)
            {
                ModMain.P(tag + "模板[" + label + "]注册异常: " + e.GetType().Name + " " + e.Message);
                return false;
            }
        }

        /// <summary>
        /// 把刚注册的那行补进 `ConfRoleLogLocal.allItemInKey`（`+0x40`）。
        ///
        /// 字典值类型是 `List&lt;ConfRoleLogLocalItem&gt;`，查的时候取列表首元素
        /// （`OnInit` 建出来的也是同一形状）⇒ **重复塞同一个实例无害**。
        /// 全程反射 + try/catch：这条链坏掉最坏是"经历写不进去"（日志里看得见），
        /// 绝不能把引擎的配置表弄脏，所以任何异常都只记一行、不外抛。
        /// </summary>
        private static void PatchKeyIndex(object conf, object row, string label, string tag)
        {
            try
            {
                var f = conf.GetType().GetField("allItemInKey",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (f == null) { ModMain.P(tag + "按键索引补登记失败（" + label + "）：找不到 allItemInKey 字段"); return; }
                object dict = f.GetValue(conf);
                if (dict == null) { ModMain.P(tag + "按键索引补登记失败（" + label + "）：allItemInKey 为空"); return; }

                string key = null;
                try { key = (string)row.GetType().GetProperty("keyID").GetValue(row); } catch { }
                if (string.IsNullOrEmpty(key)) { ModMain.P(tag + "按键索引补登记失败（" + label + "）：读不到 keyID"); return; }

                var dType = dict.GetType();
                var contains = dType.GetMethod("ContainsKey");
                var getItem = dType.GetMethod("get_Item");
                if (contains == null || getItem == null)
                {
                    ModMain.P(tag + "按键索引补登记失败（" + label + "）：字典接口不像 Dictionary");
                    return;
                }
                bool has = (bool)contains.Invoke(dict, new object[] { key });
                object list = has ? getItem.Invoke(dict, new object[] { key }) : null;
                if (list == null)
                {
                    var valueType = dType.GetGenericArguments()[1];
                    list = Activator.CreateInstance(valueType);
                    var setItem = dType.GetMethod("set_Item");
                    if (setItem != null) setItem.Invoke(dict, new object[] { key, list });
                    else dType.GetMethod("Add").Invoke(dict, new object[] { key, list });
                }
                var add = list.GetType().GetMethod("Add");
                if (add == null) { ModMain.P(tag + "按键索引补登记失败（" + label + "）：List 没有 Add"); return; }
                add.Invoke(list, new object[] { row });

                // 回读：现在这个 key 在字典里到底有没有（不依赖 LS，LS 会跑条件求值）
                bool hasNow = false;
                try
                {
                    object list2 = getItem.Invoke(dict, new object[] { key });
                    if (list2 != null) hasNow = (int)list2.GetType().GetProperty("Count").GetValue(list2) > 0;
                }
                catch { }
                ModMain.P(tag + "按键索引补登记（" + label + "）：key=" + key
                          + (has ? "（原先已有，追加一份同实例）" : "（原先没有，新建列表）")
                          + "｜回读=" + (hasNow ? "✅查得到" : "❌仍查不到"));
            }
            catch (Exception e)
            {
                ModMain.P(tag + "按键索引补登记异常（" + label + "）：" + e.GetType().Name + " " + e.Message);
            }
        }
    }
}