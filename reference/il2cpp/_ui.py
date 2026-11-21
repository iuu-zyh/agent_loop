# -*- coding: utf-8 -*-
"""临时脚本：UINPCInfo 成员 + UIType.NPCInfo + OpenNPCInfoUI + UILayer。"""
import re

HHC = r'C:\Users\<用户名>\AppData\Local\Temp\GGBH_API_html\GGBH_API.hhc'
D = r'C:\Users\<用户名>\AppData\Local\Temp\GGBH_API_html\html'
txt = open(HHC, 'rb').read().decode('gbk', errors='replace')
names = re.findall(r'<param name="Name" value="([^"]+)"\s*>', txt)
locals_ = re.findall(r'<param name="Local" value="([^"]+)"\s*>', txt)

def page_of(exact):
    for i, n in enumerate(names):
        if n.strip() == exact:
            ln = locals_[i].strip()
            return ln.split('/')[-1] if ln else None
    return None

def extract(fname):
    p = D + '\\' + fname
    raw = open(p, 'rb').read()
    t = None
    for enc in ('utf-8', 'gbk'):
        try:
            s = raw.decode(enc)
            if '<' in s:
                t = s
                break
        except Exception:
            continue
    if t is None:
        t = raw.decode('utf-8', 'ignore')
    return t

def members(fname):
    html = extract(fname)
    links = re.findall(r'memberNameLink[^>]*>\s*<a[^>]*>([^<]+)</a>', html)
    return ', '.join(dict.fromkeys(links))[:600]

def plain(fname, maxlen=1200):
    html = extract(fname)
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'[ \t\xa0]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text[:maxlen]

for c in ['UINPCInfo 类', 'UINPCInfoBase 类', 'UIType 类', 'OpenNPCInfoUI 类',
          'UINPCInfoPreview 类', 'UILayer 枚举']:
    f = page_of(c)
    print('##### %s [%s]\n成员: %s\n' % (c, f, members(f) if f else '(无页)'))

print('##### OpenNPCInfoUI 字段详情')
f = page_of('OpenNPCInfoUI 类')
if f:
    print(plain(f))

print('\n##### UIType 中 NPCInfo / OpenUI 静态字段（含 NpcInfo 的条目）')
for n, l in zip(names, locals_):
    s = n.strip()
    if 'UIType' in s and ('Npc' in s or 'NPC' in s or 'Info' in s):
        print('  ', s[:70])
print('\n== UILayer 枚举候选 ==')
for n in names:
    if 'UILayer' in n:
        print('  ', n.strip()[:60])