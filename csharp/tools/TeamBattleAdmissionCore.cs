using System;
using System.Collections.Generic;

namespace AgentLoopBridge
{
    internal sealed class TeamBattleAdmissionCore
    {
        private long _battle;
        private long _generation;
        private readonly Dictionary<long, string> _units = new Dictionary<long, string>();
        private readonly Dictionary<int, string> _slots = new Dictionary<int, string>();

        internal bool HasTeam(long battle)
        {
            return battle != 0 && battle == _battle && _slots.Count > 0;
        }

        internal bool HasSpawned(long battle)
        {
            return HasTeam(battle) && _units.Count > 0;
        }

        internal void Register(long battle, IEnumerable<string> ids, int firstSlot = 3)
        {
            Reset();
            if (battle == 0 || ids == null) return;
            _battle = battle;
            var seen = new HashSet<string>(StringComparer.Ordinal);
            int slot = firstSlot;
            foreach (var id in ids)
            {
                // 缺失身份的槽位仍占原位置，不能把后面的队友错配到前一个槽。
                if (slot > 10) break;
                if (!string.IsNullOrEmpty(id) && seen.Add(id)) _slots[slot] = id;
                slot++;
            }
        }

        internal bool CanUseSlot(long battle, int slot, string actualId)
        {
            string registered;
            return HasTeam(battle) && !string.IsNullOrEmpty(actualId)
                && _slots.TryGetValue(slot, out registered)
                && string.Equals(registered, actualId, StringComparison.Ordinal);
        }

        internal int[] Slots { get { var result = new int[_slots.Count]; _slots.Keys.CopyTo(result, 0); Array.Sort(result); return result; } }

        internal bool Contains(string id)
        {
            return !string.IsNullOrEmpty(id) && _slots.ContainsValue(id);
        }

        internal bool CaptureUnit(long battle, long pointer, string id)
        {
            if (!HasTeam(battle) || pointer == 0 || !Contains(id)) return false;
            string previous;
            if (_units.TryGetValue(pointer, out previous)) return previous == id;
            if (_units.ContainsValue(id)) return false;
            _units[pointer] = id;
            return true;
        }

        internal TeamBattleSpawnScopeCore BeginSpawn(long battle, int slot, long worldData, string id)
        {
            return worldData != 0 && CanUseSlot(battle, slot, id)
                ? new TeamBattleSpawnScopeCore(this, _generation, battle, slot, worldData, id) : null;
        }

        internal bool IsCurrentSpawn(long generation, long battle, int slot, string id)
        {
            return generation == _generation && CanUseSlot(battle, slot, id);
        }

        internal bool ResolveUnit(long battle, long pointer, out string id)
        {
            id = null;
            return HasTeam(battle) && _units.TryGetValue(pointer, out id) && Contains(id);
        }

        internal bool Participated(string id) { return Contains(id) && _units.ContainsValue(id); }

        internal bool CanHurt(string id, bool player, bool dead, bool used)
        {
            return Participated(id) && !player && dead && !used;
        }

        internal void Reset()
        {
            _generation++;
            _battle = 0;
            _slots.Clear();
            _units.Clear();
        }

        internal static bool IsSelection(string[] args, string condition)
        {
            return args != null && args.Length == 3 && args[0] == "addBattleNPCData"
                && args[1] == condition && (args[2] == "0" || args[2] == "1" || args[2] == "2");
        }

        internal static bool CanInstallHurtGuards(bool world, bool player, bool map)
        {
            return world && player && map;
        }

        internal static bool CanStart(bool hasMembers, bool disabled, bool duel, bool consent)
        {
            return hasMembers && !disabled && (!duel || consent);
        }
    }

    // 初始化回调只提供候选；整个原生生成命令正常结束才授予参战身份。
    internal sealed class TeamBattleSpawnScopeCore
    {
        private readonly TeamBattleAdmissionCore _owner;
        private readonly long _generation, _battle, _worldData;
        private readonly int _slot;
        private bool _closed;
        internal readonly string Id;
        internal long Unit { get; private set; }

        internal TeamBattleSpawnScopeCore(TeamBattleAdmissionCore owner, long generation,
            long battle, int slot, long worldData, string id)
        {
            _owner = owner; _generation = generation; _battle = battle;
            _slot = slot; _worldData = worldData; Id = id;
        }

        internal bool IsCurrent(long battle)
        {
            return !_closed && battle == _battle && _owner.IsCurrentSpawn(_generation, battle, _slot, Id);
        }

        internal bool Observe(long battle, long worldData, string id, long unit)
        {
            if (!IsCurrent(battle) || worldData != _worldData || id != Id || unit == 0
                || (Unit != 0 && Unit != unit)) return false;
            Unit = unit;
            return true;
        }

        internal bool Complete(long battle, bool success)
        {
            bool valid = IsCurrent(battle);
            _closed = true;
            return success && valid && Unit != 0 && _owner.CaptureUnit(battle, Unit, Id);
        }
    }
}
