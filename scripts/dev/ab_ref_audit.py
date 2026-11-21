# -*- coding: utf-8 -*-
"""全面核查：三个 UI prefab 引用的全部工程内资源，是否都被 AB 覆盖。
判定层级：
  [并包]   被 runner 显式分配进某 bundle
  [旧包兜底] 游戏侧 assets/art/ 有 09-06 依赖包（小写同名）
  [缺失]   两边都没有 → 必炸
另外列出 Unity 内置/游戏内资源（不进 AB，运行时引擎解析，正常）。"""
import io, os, re, glob

PROJ = r"E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject\Assets"
GAME_AB = r"E:\SteamLibrary\steamapps\common\鬼谷八荒\ModExportData\Mod_Jgmg5L\ModRes\AssetBundle"

PREFABS = {
    "UIChatAi": "uichatai.ab",
    "UIConfigAi": "uiconfigai.ab",
    "UIContactAi": "uicontactai.ab",
}

# runner 当前显式分配（读源码为准；支持字面量与常量两种 GetAtPath 形式）
runner = io.open(os.path.join(PROJ, "Scripts", "ABBuild", "Editor", "ABBuildRunner.cs"), encoding="utf-8").read()
consts = dict(re.findall(r'const string (\w+) = "([^"]+)"', runner))   # 常量名 -> 资源路径
bundle_of_asset = {}   # 资源文件名 -> bundle
for line in runner.splitlines():
    m = re.search(r'GetAtPath\((?:"([^"]+)"|(\w+))\)\.assetBundleName = (\w+);', line)
    if not m:
        continue
    literal, cname, bname = m.group(1), m.group(2), m.group(3)
    path = literal or consts.get(cname)
    if not path:
        continue
    bm = re.search(r'const string %s = "([^"]+)"' % bname, runner)
    bundle_of_asset[os.path.basename(path)] = bm.group(1) if bm else bname

# 游戏侧旧依赖包清单
old_deps = set()
for p in glob.glob(os.path.join(GAME_AB, "assets", "art", "*.ab")):
    old_deps.add(os.path.basename(p).lower())

# 工程内全部资源 guid -> 文件（Assets 全树）
guid2file = {}
for meta in glob.glob(os.path.join(PROJ, "**", "*.meta"), recursive=True):
    if os.path.basename(meta).endswith(".prefab.meta") or os.path.basename(meta).endswith(".cs.meta"):
        continue
    try:
        head = io.open(meta, encoding="utf-8", errors="ignore").read(400)
    except Exception:
        continue
    m = re.search(r"guid: ([0-9a-f]{32})", head)
    if m:
        guid2file[m.group(1)] = meta[:-5]  # 去 .meta

ok = True
for name, abfile in PREFABS.items():
    t = io.open(os.path.join(PROJ, "Resources", "UI", name + ".prefab"), encoding="utf-8").read()
    gids = sorted(set(re.findall(r"guid: ([0-9a-f]{32})", t)))
    print("== %s (%s) ==" % (name, abfile))
    for gid in gids:
        f = guid2file.get(gid)
        if f is None:
            continue  # Unity 内置/游戏内资源，引擎运行时解析
        rel = os.path.relpath(f, PROJ)
        base = os.path.basename(f)
        b = bundle_of_asset.get(base)
        if b:
            print("  [并包] %-52s -> %s" % (rel, b))
            continue
        # 未并包 → 查旧依赖包（AB 名规则：全小写；.png -> .png.ab）
        cand = (base.lower() + ".ab")
        if cand in old_deps:
            print("  [旧包兜底] %-49s -> assets/art/%s" % (rel, cand))
        else:
            print("  [!!!缺失!!!] %-47s -> 无并包、无旧包" % rel)
            ok = False
print()
print("结论:", "全部覆盖 ✓" if ok else "存在缺失，必须补并包！")
