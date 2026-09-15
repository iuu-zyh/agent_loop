import re, io, sys

# class-id -> name for the ones we care about
CLS = {'1':'GameObject','4':'Transform','224':'RectTransform','114':'MonoBehaviour',
       '222':'CanvasRenderer','33':'Texture2D','28':'Sprite','120':'SkinnedMeshRenderer'}

def parse(path):
    lines = io.open(path, encoding='utf-8').read().splitlines()
    head = re.compile(r'^--- !u!(\d+) &(\d+)')
    docs = []
    cur = None
    for i, ln in enumerate(lines):
        m = head.match(ln)
        if m:
            if cur: docs.append(cur + (i,))
            cur = (m.group(2), m.group(1), i)
    if cur: docs.append(cur + (len(lines),))
    return lines, docs

def block(lines, s, e):
    return lines[s:e]

def get(docmap, lines, fid):
    cls, s, e = docmap[fid]
    return cls, block(lines, s, e)

path = sys.argv[1] if len(sys.argv) > 1 else r'E:/SteamLibrary/steamapps/common/鬼谷八荒/Mod/modFQA/资源修改教程/ResBuildABProject/Assets/Resources/UI/UIChatAi.prefab'
lines, docs = parse(path)
docmap = {d[0]: (d[1], d[2], d[3]) for d in docs}

gos = {}       # fid -> name
go_comps = {}  # go_fid -> [(comp_fid, cls)]
trans = {}     # tf_fid -> (go_fid, father)
scripts = {}   # mono_fid -> (go_fid, script_guid, raw)

for fid, cls, s, e in docs:
    if cls == '1':
        name = None; comps = []
        for ln in lines[s:e]:
            m = re.match(r'^  m_Name: (.*)$', ln)
            if m and name is None: name = m.group(1).strip()
            m = re.match(r'^  m_Component:', ln)
            if m:
                pass
        # second pass for component list
        incomp = False
        for ln in lines[s:e]:
            if re.match(r'^  m_Component:', ln):
                incomp = True; continue
            if incomp:
                m = re.match(r'^  - component: \{fileID: (\d+)\}', ln)
                if m: comps.append(m.group(1))
                elif not ln.startswith('  -'): incomp = False
        gos[fid] = name or '?'
        go_comps[fid] = comps
    elif cls in ('4','224'):
        gof, fath = '0','0'
        for ln in lines[s:e]:
            m = re.match(r'^  m_GameObject: \{fileID: (\d+)\}', ln)
            if m: gof = m.group(1)
            m = re.match(r'^  m_Father: \{fileID: (\d+)\}', ln)
            if m: fath = m.group(1)
        trans[fid] = (gof, fath)
    elif cls == '114':
        gof = None
        for ln in lines[s:e]:
            m = re.match(r'^  m_GameObject: \{fileID: (\d+)\}', ln)
            if m: gof = m.group(1); break
        scripts[fid] = gof

tf_of_go = {gof: tf for tf, (gof, f) in trans.items()}
def path_of(go):
    parts = []
    tf = tf_of_go.get(go)
    while tf:
        gof, fath = trans[tf]
        parts.append(gos.get(gof, '?'))
        tf = fath if fath != '0' else None
    return '/' + '/'.join(reversed(parts))

INTEREST = ['LayoutElement','ContentSizeFitter','VerticalLayoutGroup','HorizontalLayoutGroup',
            'LayoutGroup','Image','Text','InputField','Button','Mask','RectMask2D']
FIELDS = {
 'LayoutElement': ['m_IgnoreLayout','m_MinWidth','m_MinHeight','m_PreferredWidth','m_PreferredHeight','m_FlexibleWidth','m_FlexibleHeight','m_LayoutPriority'],
 'ContentSizeFitter': ['m_HorizontalFit','m_VerticalFit'],
 'VerticalLayoutGroup': ['m_Padding','m_Spacing','m_ChildAlignment','m_ControlChildSize','m_ControlChildScale','m_ChildForceExpand'],
 'HorizontalLayoutGroup': ['m_Padding','m_Spacing','m_ChildAlignment','m_ControlChildSize','m_ControlChildScale','m_ChildForceExpand'],
 'Image': ['m_Sprite','m_Type','m_PreserveAspect','m_FillCenter','m_Sliced','m_PixelsPerUnitMultiplier'],
 'Text': ['m_Text','m_FontData','m_Alignment','m_Resize','m_Wrap'],
}

def dump_target(go_name, with_tf=True):
    print('='*100)
    for gof, name in gos.items():
        if name == go_name:
            print(f'### {go_name}  [{path_of(gof)}]  go={gof}')
            if with_tf:
                tf = tf_of_go.get(gof)
                if tf:
                    cls, b = get(docmap, lines, tf)
                    keys = {}
                    for ln in b:
                        m = re.match(r'^  (m_AnchorMin|m_AnchorMax|m_AnchoredPosition|m_SizeDelta|m_Pivot|m_LocalScale): (.*)$', ln)
                        if m: keys[m.group(1)] = m.group(2)
                    print('  RT:', keys)
            for cf in go_comps.get(gof, []):
                if cf not in docmap: continue
                cls, b = get(docmap, lines, cf)
                if cls == '114':
                    guid = None
                    for ln in b:
                        m = re.search(r'guid: ([0-9a-f]{32})', ln)
                        if m: guid = m.group(1); break
                    # collect ALL m_ key: value lines (compact)
                    kv = []
                    for ln in b:
                        m = re.match(r'^  (m_\w+): (.*)$', ln)
                        if m and m.group(1) != 'm_GameObject':
                            v = m.group(2)
                            if len(v) > 60: v = v[:60] + '…'
                            kv.append(f'{m.group(1)}={v}')
                    print(f'  [Mono guid={guid}]')
                    for k in kv: print('    ', k)
                elif cls == '33':
                    kv = {}
                    for ln in b:
                        m = re.match(r'^  (m_Sprite|m_Type|m_PreserveAspect|m_FillCenter): (.*)$', ln)
                        if m: kv[m.group(1)] = m.group(2)
                    print(f'  [Image] {kv}')
            # children monos that are behaviour on child Text GO? no, keep GO only

for t in sys.argv[2:] if len(sys.argv) > 2 else ['UserBubble','NpcBubble','Input','ChatInput','Button','SystemLine','StepGroup','Content']:
    dump_target(t)
