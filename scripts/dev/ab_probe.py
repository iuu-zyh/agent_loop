#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AB 内容探针：解压 UnityFS(AssetBundle) 并抽取其中的字符串，用于核对
某个 .ab 到底是哪次构建的（节点名 / TMP 文案 / 组件类型是否齐全）。

UnityFS 头部：
    "UnityFS\\0" u32 version "ver\\0" "rev\\0" i64 size
    u32 compressedBlocksInfoSize u32 uncompressedBlocksInfoSize u32 flags
    [version>=7 时 16 字节对齐] blocksInfo [data blocks] [blocksInfo(若 flags&0x80)]

压缩方式 = flags & 0x3F: 0=none 1=LZMA 2=LZ4 3=LZ4HC
blocksInfo / 每个 data block 都是独立的压缩流（LZ4 raw block 或 .lzma 流）。

用法:
    python3 scripts/dev/ab_probe.py <file.ab> [--grep 关键字 ...] [--dump-strings 输出.txt]
"""
from __future__ import annotations

import argparse
import io
import lzma
import re
import struct
import sys

try:
    import lz4.block as lz4block
except Exception:  # pragma: no cover
    lz4block = None

_COMP = {0: "none", 1: "LZMA", 2: "LZ4", 3: "LZ4HC"}


def _read_cstr(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("utf-8", "replace"), end + 1


def _lzma_raw(blob: bytes, raw_size: int) -> bytes:
    """Unity 的 LZMA 块 = 5 字节 props + 裸 LZMA1 流（**不含** 8 字节长度字段，
    长度在上层 blocksInfo 表里），所以只能按 FORMAT_RAW 解。"""
    props = blob[0]
    lc, rest = props % 9, props // 9
    lp, pb = rest % 5, rest // 5
    (dict_size,) = struct.unpack("<I", blob[1:5])
    filt = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
             "lc": lc, "lp": lp, "pb": pb}]
    return lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filt).decompress(blob[5:])


def _decompress(kind: int, blob: bytes, raw_size: int) -> bytes:
    if kind == 0:
        return blob
    if kind == 1:
        try:
            return _lzma_raw(blob, raw_size)
        except lzma.LZMAError:
            # 兜底：标准 .lzma(alone) 流
            return lzma.decompress(blob, format=lzma.FORMAT_ALONE)
    if lz4block is None:
        raise RuntimeError("需要 lz4 模块：pip install lz4")
    return lz4block.decompress(blob, uncompressed_size=raw_size)


def parse_blocks_info(blob: bytes):
    """blocksInfo 里 blocks 表 + 目录(节点)表；兼容带/不带 16 字节哈希头。"""
    for skip in (16, 0):
        try:
            r = io.BytesIO(blob[skip:])
            (nblocks,) = struct.unpack(">I", r.read(4))
            if not 0 < nblocks <= 4096:
                continue
            blocks = []
            for _ in range(nblocks):
                u, c, f = struct.unpack(">IIH", r.read(10))
                blocks.append((u, c, f))
            (nnodes,) = struct.unpack(">I", r.read(4))
            if not 0 < nnodes <= 4096:
                continue
            nodes = []
            for _ in range(nnodes):
                off, size, flags = struct.unpack(">qqI", r.read(20))
                path, p = _read_cstr(r.getvalue(), r.tell())
                r.seek(p)
                nodes.append((off, size, flags, path))
            return blocks, nodes
        except Exception:
            continue
    raise RuntimeError("blocksInfo 解析失败")


def extract(path: str) -> dict:
    buf = open(path, "rb").read()
    if buf[:7] != b"UnityFS":
        raise RuntimeError("不是 UnityFS 包")
    pos = 8
    (version,) = struct.unpack(">I", buf[pos:pos + 4]); pos += 4
    unity_ver, pos = _read_cstr(buf, pos)
    unity_rev, pos = _read_cstr(buf, pos)
    (size,) = struct.unpack(">q", buf[pos:pos + 8]); pos += 8
    cb, ub, flags = struct.unpack(">III", buf[pos:pos + 12]); pos += 12
    comp = flags & 0x3F
    if version >= 7:
        pos = (pos + 15) & ~15
    if flags & 0x80:
        raise RuntimeError("blocksInfo 在文件尾（未实现）")
    info_blob = buf[pos:pos + cb]; pos += cb
    info = _decompress(comp, info_blob, ub)
    blocks, nodes = parse_blocks_info(info)

    data, cur = bytearray(), pos
    for u, c, f in blocks:
        # 每块的压缩方式看块自身 flags 低位（为 0 时退回归档级 flags）——
        # 实测本工程：归档 flags=0x43(LZ4HC) 但数据块 flags=0x41 → LZMA
        blk = (f & 0x3F) or comp
        data += _decompress(blk, buf[cur:cur + c], u)
        cur += c

    return {
        "file_size": len(buf), "declared_size": size, "unity": f"{unity_ver} ({unity_rev})",
        "version": version, "compression": _COMP.get(comp, comp),
        "blocks": [{"uncompressed": u, "compressed": c} for u, c, _ in blocks],
        "nodes": [{"path": p, "size": s} for _o, s, _f, p in nodes],
        "raw": bytes(data),
    }


_CJK = re.compile(rb"(?:[\xe4-\xe9][\x80-\xbf]{2}){2,}")


def strings(raw: bytes, min_cjk: int = 2, min_ascii: int = 4):
    out = set()
    for m in _CJK.finditer(raw):
        s = m.group().decode("utf-8", "ignore")
        if len(s) >= min_cjk:
            out.add(s)
    for m in re.finditer(rb"[ -~]{%d,}" % min_ascii, raw):
        out.add(m.group().decode("ascii", "ignore"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ab")
    ap.add_argument("--grep", nargs="*", default=[])
    ap.add_argument("--dump-strings")
    ap.add_argument("--list-strings", action="store_true")
    a = ap.parse_args()

    info = extract(a.ab)
    print(f"文件      : {a.ab}")
    print(f"大小      : {info['file_size']} bytes (头部声明 {info['declared_size']})")
    print(f"Unity     : {info['unity']}  UnityFS v{info['version']}  压缩={info['compression']}")
    print(f"包内资源  : {', '.join(n['path'] for n in info['nodes'])}")
    print(f"数据块    : {len(info['blocks'])} 块，解压后 {len(info['raw'])} bytes")
    for i, b in enumerate(info["blocks"]):
        print(f"  #{i}: {b['uncompressed']} -> {b['compressed']}")

    ss = strings(info["raw"])
    print(f"字符串数  : {len(ss)}")
    if a.dump_strings:
        with open(a.dump_strings, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(ss)))
        print(f"已写出    : {a.dump_strings}")
    if a.list_strings:
        for s in sorted(ss):
            print("   ", s)
    if a.grep:
        print("--- 关键字核对 ---")
        for k in a.grep:
            print(f"  {'OK ' if k in ss else 'MISS'} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
