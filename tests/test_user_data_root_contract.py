# -*- coding: utf-8 -*-
"""test_user_data_root_contract — 用户数据根外置（方案 A）的防回潮闸门。

## 背景

创意工坊整包更新会同步 MOD 目录，而 `config.json` / `prompts/` / 窗口位置 / 公告水位
/ 剧情账本原本都住在 `<Mod根>\\ModAssets\\` 里 —— 更新一次，用户填的 Key、改过的提示词、
自建人设全被盖回去。方案 A 把数据根移到 `%USERPROFILE%\\.agent_loop`。

## 钉住什么

- 迁移必须**只补缺失、绝不覆盖**：`File.Exists(dst)` 早退 + `File.Copy(..., false)`。
  这条一旦被改掉，用户改过的人设会在一次更新后静默回退，且没有任何报错。
- 优先级必须是 env > 用户目录 > 旧 ModAssets。
- C# 与 Python 必须锚同一个数据根（C# 经 `AGENT_LOOP_DATA` 下发）。

## 不测什么

不测真实文件 IO —— 迁移语义由 `csharp/tests/UserDataMigrationCoreTests` 用临时目录覆盖。
这里只做源码级契约，风格与 `test_python_layout_contract.py` 一致。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CS = ROOT / "csharp"
MODPATHS = CS / "app" / "ModPaths.cs"
MIGRATION = CS / "app" / "UserDataMigrationCore.cs"
LAUNCHER = CS / "bridge" / "Launcher.cs"
PY_PATHS = ROOT / "config" / "paths.py"
TEST_CSPROJ = CS / "tests" / "UserDataMigrationCoreTests" / "UserDataMigrationCoreTests.csproj"


def src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def flat(p: Path) -> str:
    return re.sub(r"\s+", " ", src(p))


def test_migration_core_is_the_user_dir_source():
    t = src(MIGRATION)
    assert 'internal const string UserDataDirName = ".agent_loop";' in t
    assert "CopyTreeIfMissing" in t


def test_migration_never_overwrites():
    """删掉 `if (File.Exists(dst)) return;` 或把 false 改成 true，迁移就会开始回退用户改动。"""
    t = src(MIGRATION)
    assert re.search(r"if \(File\.Exists\(dst\)\)\s*return;", t)
    assert "File.Copy(src, dst, false)" in t
    assert "File.Copy(src, dst, true)" not in t


def test_migration_covers_user_state_files():
    t = src(MIGRATION)
    for name in ("window_layout.json", "notice_seen.json", "drama_ledger.jsonl"):
        assert name in t, name


def test_dataroot_precedence_env_then_user_then_legacy():
    t = src(MODPATHS)
    body = t[t.index("private static void ResolveDataRoot()"):]
    i_env = body.index("EnvDataRoot")
    i_user = body.index("UserDataMigrationCore.DefaultUserRoot")
    i_legacy = body.index('_dataRootSource = "ModAssets')
    assert i_env < i_user < i_legacy


def test_dataroot_uses_cached_resolved_field():
    """DataRoot 不能再现算（现算会绕过 ResolveDataRoot 的 env/迁移分支）。"""
    assert "get { Resolve(); return _dataRoot; }" in flat(MODPATHS)
    assert "Environment.SpecialFolder.UserProfile" in src(MODPATHS)


def test_launcher_passes_same_data_root_to_python():
    assert ("psi.EnvironmentVariables[ModPaths.EnvDataRoot] = ModPaths.DataRoot;"
            in flat(LAUNCHER))


def test_python_side_still_reads_env_data_root():
    t = src(PY_PATHS)
    assert 'ENV_DATA_ROOT = "AGENT_LOOP_DATA"' in t
    assert "os.environ.get(ENV_DATA_ROOT)" in t


def test_csharp_tests_link_the_core():
    assert r"..\..\app\UserDataMigrationCore.cs" in src(TEST_CSPROJ)
