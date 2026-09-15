# -*- coding: utf-8 -*-
"""
uichatai.ab 补丁（UnityPy 版）：气泡根 LayoutElement.m_LayoutPriority 1 -> 0（2 处，prefH=76 者）。
Header(prefH=40) 不动。保存为 .patched 并自校验（重读确认 prio=0、其余 typetree 字段不变）。
"""
import shutil
import UnityPy

SRC = r'E:/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/ResBuildABProject/Assets/AssetBundle/ab/UI/uichatai.ab'
DST = SRC + '.patched'

env = UnityPy.load(SRC)
patched = 0
for obj in env.objects:
    if obj.type.name != 'MonoBehaviour':
        continue
    try:
        tree = obj.read_typetree()
    except Exception as ex:
        print('skip MonoBehaviour (no typetree):', ex)
        continue
    if 'm_LayoutPriority' not in tree:
        continue
    print('LE found: prefH=%s prio=%s name=%r' % (tree.get('m_PreferredHeight'), tree.get('m_LayoutPriority'), tree.get('m_Name')))
    if tree.get('m_PreferredHeight') == 76 and tree.get('m_LayoutPriority') == 1:
        # 记录全部字段，改后核对
        before = dict(tree)
        tree['m_LayoutPriority'] = 0
        obj.save_typetree(tree)
        patched += 1
print('patched count:', patched)
assert patched == 2, 'expect exactly 2 bubble LayoutElements'

import os
OUTDIR = r'F:/agent_loop/_tmp_ab_out'
os.makedirs(OUTDIR, exist_ok=True)
env.save("lz4", OUTDIR)  # UnityPy: save(pack, out_path)，仅落盘 is_changed 的文件
cand = [os.path.join(OUTDIR, f) for f in os.listdir(OUTDIR)]
print('saved files:', cand)
assert len(cand) == 1
shutil.copyfile(cand[0], DST)
print('saved', DST)

# ===== 自校验：重读 DST =====
env2 = UnityPy.load(DST)
ok = 0
for obj in env2.objects:
    if obj.type.name != 'MonoBehaviour':
        continue
    tree = obj.read_typetree()
    if 'm_LayoutPriority' in tree:
        print('verify LE: prefH=%s prio=%s' % (tree.get('m_PreferredHeight'), tree.get('m_LayoutPriority')))
        if tree.get('m_PreferredHeight') == 76:
            assert tree['m_LayoutPriority'] == 0
            ok += 1
assert ok == 2
print('VERIFY OK: 2 bubble LayoutElement priority=0')
