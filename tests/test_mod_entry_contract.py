"""官方桥入口契约 —— 「缺了就全部静默失效」的那一层（2026-09-21 文件夹整理时补钉）。

加载链与敏感点
------------------------------------------------------------------------------------
    游戏 → ModExportData.cache → 枚举 ModCode/dll/ 加载 MOD_Jgmg5L.dll
        → 官方桥 GGBH_MOD 反射调用「{命名空间}.ModMain」
            → MOD_Jgmg5L.ModMain        ← 入口壳（ModMainEntry.cs，转发）
                → AgentLoopBridge.ModMain   ← 真实现（全部逻辑）

三个敏感点，任何一处破坏的**症状都是静默的**（Init/Destroy 永不执行，Harmony 补丁、
AB 面板、对话窗全部无声消失，没有任何报错）：
  ① 入口壳类必须叫 `MOD_Jgmg5L.ModMain`（namespace MOD_Jgmg5L + class ModMain）；
  ② 入口壳必须真的转发到 `AgentLoopBridge.ModMain`（转发丢了 = 壳是空壳）；
  ③ 程序集名必须保持 `MOD_Jgmg5L`（与官方模组命名空间一致）。

为什么现在钉：2026-09-21 按职责域整理 csharp/ 目录（38 个根下文件进 8 个域子目录），
ModMainEntry / ModMain 一并搬进 app/。文件夹与反射查找无关（反射认的是编译后类型全名，
与源码路径无关），但「文件存在、命名空间没被顺手改掉、还在参与编译」这三件事从此
由本文件守着 —— 以后任何人再动文件组织，碰到入口就会在测试阶段红，而不是在游戏里静默。

钉法刻意做到**文件夹无关**（rglob 查找）：本测试不许因为文件搬家而失效，
它钉的是契约本身，不是 2026-09-21 当天的目录布局。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSHARP = ROOT / "csharp"
CSPROJ = CSHARP / "AgentLoopBridge.csproj"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _find(name: str) -> Path:
    """按文件名在 csharp/ 下查找（排除生成物），找不到直接断言失败并说明加载后果。"""
    hits = [p for p in CSHARP.rglob(name)
            if not ({"tests", "obj", "bin"} & set(p.parts))]
    assert len(hits) == 1, (
        f"{name} 应当恰好存在一份（找到 {len(hits)} 份：{hits}）—— "
        "入口壳是官方桥反射的唯一入口，出现两份/零份都会静默失效"
    )
    return hits[0]


def test_entry_shell_exists_with_the_reflected_full_name():
    """① 入口壳必须仍然是 `MOD_Jgmg5L.ModMain` —— 官方桥按这个全名反射。

    namespace 一旦被"顺手规范化"成 AgentLoopBridge，反射就找不到入口，
    Init/Destroy 永不执行，全部功能静默失效（ModMainEntry.cs 头注释原话）。
    """
    p = _find("ModMainEntry.cs")
    src = _read(p)
    assert "namespace MOD_Jgmg5L" in src, (
        f"{p.relative_to(ROOT)} 的 namespace 不再是 MOD_Jgmg5L —— "
        "官方桥反射「{命名空间}.ModMain」会找不到入口，全部功能静默失效"
    )
    assert re.search(r"public\s+class\s+ModMain\b", src), (
        "入口壳类不再叫 ModMain —— 官方桥反射 {命名空间}.ModMain 将落空"
    )


def test_entry_shell_actually_forwards_to_the_real_modmain():
    """② 入口壳必须真的转发到 AgentLoopBridge.ModMain —— 防止壳被改成空壳。

    Init 与 Destroy 两条官方回调都要 _impl 转发；漏一条 = 该生命周期静默失效
    （进世界不装配 / 回主界面不清理 → 登记泄漏那类事故的温床）。
    """
    src = _read(_find("ModMainEntry.cs"))
    assert re.search(r"new\s+AgentLoopBridge\.ModMain\s*\(", src), (
        "入口壳没有构造 AgentLoopBridge.ModMain —— 转发断了，壳是空壳"
    )
    for method in ("Init", "Destroy"):
        m = re.search(r"public\s+void\s+" + method + r"\s*\(\)\s*\{(.*?)\}", src, re.S)
        assert m and "_impl." + method in m.group(1), (
            f"入口壳的 {method}() 没有转发到 _impl —— 该生命周期回调会静默失效"
        )


def test_real_modmain_still_exists_in_the_bridge_namespace():
    """真实现 AgentLoopBridge.ModMain 必须存在 —— 全部逻辑与 Harmony 补丁挂载都在它身上。"""
    p = _find("ModMain.cs")
    src = _read(p)
    assert "namespace AgentLoopBridge" in src, (
        f"{p.relative_to(ROOT)} 的 namespace 不再是 AgentLoopBridge —— "
        "入口壳 new AgentLoopBridge.ModMain() 会编译失败"
    )
    assert re.search(r"public\s+class\s+ModMain\b", src), "真实现类不再叫 ModMain"
    # Init/Destroy 是官方桥驱动的两个生命周期入口，缺一不可
    for method in ("public void Init()", "public void Destroy()"):
        assert method in src, f"真实现缺 {method} —— 官方桥没有可调的回调"


def test_assembly_name_matches_the_official_mod_namespace():
    """③ 程序集名必须保持 MOD_Jgmg5L —— 三者一致约定（程序集名/导出路径/命名空间）。"""
    src = _read(CSPROJ)
    m = re.search(r"<AssemblyName>\s*MOD_Jgmg5L\s*</AssemblyName>", src)
    assert m, "AssemblyName 不再是 MOD_Jgmg5L —— 官方桥按程序集名定位 mod，改了就静默失效"


def test_entry_files_are_part_of_the_compilation():
    """入口壳与真实现都必须参与编译 —— 通配收不全 = DLL 里没有入口 = 静默失效。

    钉「csproj 能罩住它们」的意图而不是钉文件路径：无论文件在哪个子目录，
    只要 csproj 的通配（或显式条目）能覆盖它就放行。
    """
    src = _read(CSPROJ)
    wildcard_dirs = re.findall(r'<Compile Include="([^"]+)"\s*/>', src)
    assert wildcard_dirs, "csproj 里没有任何 Compile 条目"
    for name in ("ModMainEntry.cs", "ModMain.cs"):
        p = _find(name)
        rel_posix = p.relative_to(CSHARP).as_posix()
        covered = any(
            (w.endswith("*.cs") and rel_posix.startswith(w[:-4].replace("\\", "/"))
             and "/" not in rel_posix[len(w[:-4].replace("\\", "/")):])
            or rel_posix.replace("\\", "/") == w.replace("\\", "/")
            for w in wildcard_dirs
        )
        assert covered, (
            f"{rel_posix} 不被 csproj 任何 Compile 条目覆盖 —— "
            "入口没编进 DLL，官方桥找不到入口，全部功能静默失效"
        )


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
