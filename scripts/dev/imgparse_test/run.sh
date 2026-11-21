#!/usr/bin/env bash
# ImageInput 路径解析回归测试 —— 直接编译**真实源码**跑，不是移植副本。
#
# 为什么用「抽取」而不是「复制」：这些函数是纯 .NET（只碰 File/Path/StringBuilder），
# 而 ImageInput.cs 整体依赖 UnityEngine 编译不进来。故按花括号配平把函数原文抽出来，
# 拼成一个可独立编译的类 —— 被测代码与仓库里逐字一致，改了仓库这里就会跟着变。
#
# 用法：bash scripts/dev/imgparse_test/run.sh
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../../.. && pwd)"
DOTNET="${DOTNET:-/mnt/c/Program Files/dotnet/dotnet.exe}"
[ -x "$DOTNET" ] || DOTNET=dotnet

python3 - "$ROOT/csharp/UI/ImageInput.cs" <<'PY'
import pathlib, re, sys
src = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
lines = src.split('\n')

def find(sig):
    for i, l in enumerate(lines):
        if l.strip().startswith(sig):
            return i
    raise SystemExit('找不到函数: ' + sig)

def extract(start):
    depth, started = 0, False
    for j in range(start, len(lines)):
        for ch in lines[j]:
            if ch == '{': depth += 1; started = True
            elif ch == '}':
                depth -= 1
                if started and depth == 0:
                    return '\n'.join(lines[start:j+1])
    raise SystemExit('花括号不配平 @' + str(start))

names = ['public static void SplitPaths', 'private static string TakeQuotedPaths',
         'public static string QuotePath', 'private static bool TryAsImagePath',
         'private static string DriveTail', 'private static bool IsImagePath',
         'private static void AddUnique']
exts = next(l.strip() for l in lines if 'string[] Exts' in l)

out = ['using System;','using System.Collections.Generic;','using System.IO;','using System.Text;','',
       'public static class P {', '    ' + exts, '']
for n in names:
    out.append(extract(find(n))); out.append('')
out.append('}')
pathlib.Path('P.cs').write_text('\n'.join(out), encoding='utf-8')
print(f'抽取 {len(names)} 个函数 + Exts → P.cs（{len(out)} 行，源码逐字）')
PY

"$DOTNET" run --project imgparse_test.csproj 2>&1 | grep -v "warning CS1668"
