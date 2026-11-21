/// <summary>
/// 通讯录「最近」Tab 行右侧**相对时间**的判档（纯逻辑：零 Unity ⇒ 可编成 net8.0 直接单测）。
///
/// ── 需求（用户 10-22 UI 优化批次）──────────────────────────────────────────
/// 「最近」Tab 一直按最近互动**排序**，但行上没有任何时间——列表顺序对了，信息却没给到
/// （微信会话列表右侧那一列就是干这个的）。补一行右上小灰字：
///
///   · 一分钟内 → `刚刚`
///   · 今天     → `14:22`（时分，微信同款）
///   · 昨天     → `昨天`
///   · 七天内   → `3天前`
///   · 更久     → `10月6日`；跨年 → `2025年3月6日`
///
/// ── 为什么是**现实时间**而不是游戏历 ──────────────────────────────────────
/// 排序键 `ContactEntry.LastInteract` 本来就是 unix 秒（会话文件 mtime ∪ 本地回复时刻，
/// 全是现实钟）。聊天窗的时间分隔行走的是**游戏历**（账本行首戳）——两套钟各管各的：
/// 会话列表回答"我现实里多久没理这个人"，聊天里回答"剧情上这句话是哪天说的"。
/// 硬把列表换成游戏历要往账本塞回合级日期（协议改动），不值。
///
/// 时钟源由调用方注入（`nowSec`）——纯函数才可测；跨天判定用**本地日历日**
/// （`LocalDateTime.Date`），与玩家肉眼"昨天"的直觉一致。
/// </summary>
using System;

namespace AgentLoopBridge
{
    internal static class ContactTimeCore
    {
        /// <summary>把 thenSec（上次互动，unix 秒）相对 nowSec 渲染成列表右侧短标签。
        /// 无记录（thenSec&lt;=0）或时钟倒挂（now&lt;then，改系统时间/跨存档）→ 空串 = 不打。</summary>
        public static string Format(long nowSec, long thenSec)
        {
            if (thenSec <= 0 || nowSec < thenSec) return "";
            if (nowSec - thenSec < 60) return "刚刚";

            var now = DateTimeOffset.FromUnixTimeSeconds(nowSec).LocalDateTime;
            var then = DateTimeOffset.FromUnixTimeSeconds(thenSec).LocalDateTime;
            int dayDiff = (int)(now.Date - then.Date).TotalDays;

            if (dayDiff <= 0) return then.ToString("HH:mm");     // 今天：时分
            if (dayDiff == 1) return "昨天";
            if (dayDiff < 7) return dayDiff + "天前";
            if (then.Year == now.Year) return then.Month + "月" + then.Day + "日";
            return then.Year + "年" + then.Month + "月" + then.Day + "日";
        }
    }
}
