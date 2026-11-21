# 重打 AB 清单（10-31）

## 一、重打前：Unity 许可

根因不是"过期"，是**联不上配置源**：

    Unable to download configuration from remote source at:
      https://public-cdn.cloud.unity3d.com/config/production → timed out
    Licensing service disabled: Invalid configuration for licensing.
    Failed to acquire Licensing Service URL from configuration key: licensingServiceBaseUrl

而 CDN 现在返回的是一份 **unity.cn 国内版配置**（`license: https://license.unity.cn`），
里面没有国际版编辑器要的 `licensingServiceBaseUrl` 键。

做法：打开 Unity Hub → 登录 → 启动一次编辑器（能进就说明许可恢复了）。
Batchmode 与 Hub 走同一套许可，Hub 能进 batch 就能跑。

## 二、重打前：资产侧已就位（已核对）

| 项 | 状态 |
|---|---|
| `UIChatAi.prefab` 两个气泡 `m_LineSpacing` | 1.35 ✓ |
| 同上 `m_RichText` | 1 ✓ |
| 同上 `m_Alignment` | 0（左上）✓ |
| `npc气泡.png` spriteBorder | 38, 17, 35, 17 ✓ |
| `玩家气泡.png` spriteBorder | 10, 19, 40, 19 ✓ |
| ABBuildRunner 登记两张气泡图 | ✓（本轮补的，之前漏了） |
| 8 个登记路径磁盘存在 | ✓ |

## 三、重打

菜单：**游戏工具 → 更新 → 更新AB(自包含)**

或命令行（含中文路径，脚本须 UTF-8 带 BOM）：

    "C:\Program Files\Unity\Hub\Editor\2020.3.9f1\Editor\Unity.exe" -batchmode -quit \
      -projectPath "E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject" \
      -executeMethod ABBuildRunner.Run -logFile "F:\agent_loop\_ab_build.txt"

判据：日志出现 `[ABBuildRunner] self-contained AB build done -> ...`

## 四、重打后：核对

产物在 `ResBuildABProject\Assets\AssetBundle\ab\ui\`，**三个都要比时间戳**：

    uichatai.ab      （应变成今天）
    uiconfigai.ab
    uicontactai.ab

★ 若某个没变 = 那边没改，属正常。但 `uichatai.ab` 必须变。

## 五、重打后：部署

    python3 scripts/dev/check_ab_fallback.py          # 先确认新包结构齐
    # 然后（只拷 ab/，assets/ 是依赖残留，别拷）：
    cp ResBuildABProject/Assets/AssetBundle/ab/ui/*.ab \
       <游戏>/ModExportData/Mod_Jgmg5L/ModRes/AssetBundle/ab/ui/

三处都要（E: 游戏目录、F: 编辑器工程、F: debug）。

## 六、验证生效

重启游戏，看 `Player.log`：

    [ChatWindow] 气泡文字版式兜底：...   ← 有这行 = 运行期兜底动过（新 AB 上应该消失或减少）
    [ChatUiBuilder] 共享字体 = 「...」 ...  ← 确认字体与行盒

新 AB 生效后，运行期兜底应该**不再需要改** padding/行距/border（预制件已经是目标值），
所以那行日志会消失 —— 这是"资产真的进去了"的反向证据。
