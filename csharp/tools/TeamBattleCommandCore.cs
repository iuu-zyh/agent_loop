using System;

namespace AgentLoopBridge
{
    internal static class TeamBattleCommandCore
    {
        // 以额外参数标记归属，原生命令仍留在同一个 CallQueue，延时与其他剧情命令不移位。
        private static string Marker(string condition) { return "@Team:" + condition; }

        internal static bool IsMarked(string[] args, string condition, int length)
        {
            return args != null && args.Length == length + 1 && args[length] == Marker(condition);
        }

        internal static int MemberIndex(string value)
        {
            int slot;
            return int.TryParse(value, out slot) && slot >= 3 && slot <= 5 ? slot - 3 : -1;
        }

        internal static string[] Remap(string[] args, int slot)
        {
            var result = new string[args.Length - 1];
            Array.Copy(args, result, result.Length);
            result[1] = slot.ToString(System.Globalization.CultureInfo.InvariantCulture);
            return result;
        }

        internal static string Rewrite(string function, string condition, string effect)
        {
            if (string.IsNullOrEmpty(function)) return function;
            var commands = function.Split('|');
            bool selection = false, changed = false;
            for (int i = 0; i < commands.Length; i++)
            {
                var args = commands[i].Trim().Split('_');
                if (args[0] == "addBattleNPCData")
                {
                    // 外部条件组会改变共享列表；不能把紧随其后的 NPC 生成也认作自己的队友。
                    selection = TeamBattleAdmissionCore.IsSelection(args, condition);
                    if (selection && commands[i] != string.Join("_", args))
                    {
                        commands[i] = string.Join("_", args);
                        changed = true;
                    }
                    continue;
                }
                bool spawn = selection && args.Length == 8 && args[0] == "addBattleUnit" && MemberIndex(args[1]) >= 0;
                bool flag = selection && args.Length == 3 && args[0] == "setBattleData" && args[1] == "alTeamIn" && args[2] == "ok";
                bool injury = args.Length == 3 && args[0] == "unitAddEffect" && args[2] == effect && MemberIndex(args[1]) >= 0;
                if (!spawn && !flag && !injury) continue;
                commands[i] = string.Join("_", args) + "_" + Marker(condition);
                changed = true;
            }
            return changed ? string.Join("|", commands) : function;
        }
    }
}
