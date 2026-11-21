# -*- coding: utf-8 -*-
"""BusyLabel 飘窗中央修复：AB 预制件里锚点是中心(0,0)，改回代码 builder 的本意位置
（BG 右上角、标题栏下方：锚(1,1)/pivot(1,1)/pos(-16,-58)/size(300,24)），
Text 对齐 0(UpperLeft)→2(UpperRight)。AB 工程 + ui_preview 双份，幂等。"""
import io, sys, re, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

TARGETS = [
    (r"E:\SteamLibrary\steamapps\common\鬼谷八荒\Mod\modFQA\资源修改教程\ResBuildABProject\Assets\Resources\UI\UIChatAi.prefab",
     r"F:\agent_loop\backup\UIChatAi.prefab.bak_20260908"),
    (r"F:\agent_loop\ui_preview\Assets\Resources\UI\UIChatAi.prefab",
     r"F:\agent_loop\backup\UIChatAi.preview.bak_20260908"),
]

RT_FID = "2383907941086270437"     # BusyLabel RectTransform
TEXT_FID = "3570985077533490413"   # BusyLabel Text

RT_FIELDS = {
    "m_AnchorMin": "{x: 1, y: 1}",
    "m_AnchorMax": "{x: 1, y: 1}",
    "m_AnchoredPosition": "{x: -16, y: -58}",
    "m_SizeDelta": "{x: 300, y: 24}",
    "m_Pivot": "{x: 1, y: 1}",
}

for path, backup in TARGETS:
    text = open(path, encoding="utf-8").read()
    shutil.copyfile(path, backup)

    def patch_doc(text, fid, replacer, label):
        pat = re.compile(r"(?s)(--- !u!\d+ &" + fid + r"\n.*?)(?=^--- !u!|\Z)", re.M)
        m = pat.search(text)
        assert m, path + " : doc " + fid + " (" + label + ") 未找到"
        doc = m.group(1)
        new_doc = replacer(doc)
        return text[:m.start(1)] + new_doc + text[m.end(1):], new_doc != doc

    def fix_rt(doc):
        for key, val in RT_FIELDS.items():
            doc, n = re.subn(re.escape(key) + r": \{x: -?[\d.eE+]+, y: -?[\d.eE+]+\}",
                             key + ": " + val, doc, count=1)
            assert n == 1, key + " 替换失败"
        return doc

    def fix_text(doc):
        doc, n = re.subn(r"m_Alignment: \d+", "m_Alignment: 2", doc, count=1)
        assert n == 1, "m_Alignment 替换失败"
        return doc

    text, ch1 = patch_doc(text, RT_FID, fix_rt, "RectTransform")
    text, ch2 = patch_doc(text, TEXT_FID, fix_text, "Text")
    open(path, "w", encoding="utf-8", newline="").write(text)
    print(path.split(chr(92))[-1], "→", "RectTransform 改动" if ch1 else "RT 已是目标值",
          "/", "Text 对齐改动" if ch2 else "Text 已是目标值")
print("备份：", backup)
