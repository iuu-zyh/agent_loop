# -*- coding: utf-8 -*-
"""HUD「传」钮未读红点 —— 样式方案示意图（非真实截图，仅供选型）。

现状（csharp/UI/MapMainContactButton.SetUnread）：
    new GameObject + Image(sprite=null) + sizeDelta(14,14) + anchor(1,1) + anchoredPosition(-4,-4)
Unity 的 Image 在 sprite==null 时退化为 Graphic.OnPopulateMesh → 画一个**纯色方块**，
所以屏幕上看到的是 14×14 的红方块，而不是圆点；又因为锚在按钮 RectTransform 的右上角，
而模板 G:btnEmail 是**圆钮**，圆钮的矩形角落本身是透明区 → 方块等于悬在圆环外面。
"""
from PIL import Image, ImageDraw, ImageFont
import math, os

D = 192                      # 按钮直径（3× 游戏内约 64px）
PAD = 26
LABEL_H = 108
COLS = 6
W = COLS * (D + PAD * 2)
H = D + PAD * 3 + LABEL_H

FONT = "/mnt/c/Windows/Fonts/msyh.ttc"
f_title = ImageFont.truetype(FONT, 26)
f_name  = ImageFont.truetype(FONT, 22)
f_note  = ImageFont.truetype(FONT, 17)
f_glyph = ImageFont.truetype(FONT, int(D * 0.40))
f_badge = ImageFont.truetype("/mnt/c/Windows/Fonts/msyhbd.ttc", 34)

INK_BG   = (26, 30, 33)
DISC_TOP = (58, 64, 68)
DISC_BOT = (34, 38, 42)
RING     = (150, 128, 86)
GLYPH    = (233, 226, 210)
RED      = (255, 59, 48)       # #FF3B30 = 面板/横幅统一色 UnreadRed
RED_OLD  = (242, 64, 64)       # 现状：new Color(0.95,0.25,0.25)
CINNABAR = (196, 62, 48)


def gradient_disc(d):
    """圆形按钮：上亮下暗的墨玉盘 + 铜环 + 内圈。"""
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    for y in range(d):
        t = y / max(1, d - 1)
        c = tuple(int(DISC_TOP[i] + (DISC_BOT[i] - DISC_TOP[i]) * t) for i in range(3))
        dr.line([(0, y), (d, y)], fill=c + (255,))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse([1, 1, d - 2, d - 2], fill=255)
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    dr = ImageDraw.Draw(out)
    dr.ellipse([2, 2, d - 3, d - 3], outline=RING + (215,), width=5)          # 铜环
    dr.ellipse([13, 13, d - 14, d - 14], outline=(120, 104, 72, 110), width=2)  # 内细圈
    return out


def draw_button(canvas, x, y):
    disc = gradient_disc(D)
    # 投影
    sh = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([4, 8, D - 4, D - 4], fill=(0, 0, 0, 120))
    canvas.alpha_composite(sh, (x, y))
    canvas.alpha_composite(disc, (x, y))
    dr = ImageDraw.Draw(canvas)
    tb = dr.textbbox((0, 0), "传", font=f_glyph)
    dr.text((x + D / 2 - (tb[2] - tb[0]) / 2 - tb[0],
             y + D / 2 - (tb[3] - tb[1]) / 2 - tb[1]), "传", font=f_glyph, fill=GLYPH)


def circle_badge(canvas, cx, cy, r, fill, ring=None, ring_w=5, num=None):
    dr = ImageDraw.Draw(canvas)
    if ring:
        dr.ellipse([cx - r - ring_w, cy - r - ring_w, cx + r + ring_w, cy + r + ring_w], fill=ring)
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    if num:
        tb = dr.textbbox((0, 0), num, font=f_badge)
        dr.text((cx - (tb[2] - tb[0]) / 2 - tb[0], cy - (tb[3] - tb[1]) / 2 - tb[1] - 1),
                num, font=f_badge, fill=(255, 255, 255))


def glow(canvas, cx, cy, r, color):
    """按钮整体高亮：环上叠一圈朱砂光晕。"""
    lay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(lay)
    for i in range(14, 0, -1):
        a = int(70 * (1 - i / 14.0) ** 1.4)
        dr.ellipse([cx - r - i, cy - r - i, cx + r + i, cy + r + i], outline=color + (a,), width=2)
    canvas.alpha_composite(lay)


canvas = Image.new("RGBA", (W, H), INK_BG + (255,))
# 底纹：中心微亮
grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(grad)
for i in range(60, 0, -1):
    gd.ellipse([W / 2 - i * 22, H / 2 - i * 12, W / 2 + i * 22, H / 2 + i * 12],
               fill=(70, 78, 84, max(0, 6 - i // 12)))
canvas.alpha_composite(grad)

dr = ImageDraw.Draw(canvas)
dr.text((PAD, 16), "主界面「传」钮 · 未读提示样式方案（示意图，非真实截图；按钮为游戏圆钮的近似）",
        font=f_title, fill=(226, 220, 206))

# 45° 环上落点（圆心压在铜环上）
R = D / 2.0
diag = R * (1 - math.cos(math.radians(45)))     # 距矩形右上角的内缩量
r_badge = D * 0.155                             # ≈0.31D 直径

cols = [
    ("①现状（方块）", "Image 未给 sprite\n→ 纯色方块挂矩形角上\n圆钮的角落是透明区\n= 红方块悬在环外", "cur"),
    ("②真圆 · 压环", "MakeCircleSprite 抗锯齿真圆\n圆心落在 45° 环上\n与面板红点同色 #FF3B30", "circle"),
    ("③真圆 + 白描边", "角标感最强\n深色图标上也看得清\n（iOS/微信角标式）", "ring"),
    ("④真圆 + 墨描边", "朱砂红 + 深墨外圈\n最贴水墨/青铜风 HUD\n不抢图标本身", "ink"),
    ("⑤数字角标", "显示未读条数\n信息量最大\n（HUD 上可能偏重）", "num"),
    ("⑥整体高亮", "不挂点：铜环变朱砂\n+ 微光呼吸\n最含蓄、最不像外挂件", "glow"),
]

for i, (name, note, kind) in enumerate(cols):
    x = i * (D + PAD * 2) + PAD
    y = PAD + 42
    draw_button(canvas, x, y)
    cx, cy = x + D / 2, y + D / 2
    if kind == "cur":
        s = 42                                  # 14px × 3
        bx = x + D - 12 - s / 2
        by = y + 12 - s / 2
        dr.rectangle([bx, by, bx + s, by + s], fill=RED_OLD + (255,))
        dr.line([(cx + R * 0.70, cy - R * 0.70), (bx + s / 2, by + s / 2)], fill=(255, 80, 80, 170), width=3)
    elif kind == "circle":
        circle_badge(canvas, x + D - diag, y + diag, r_badge, RED + (255,))
    elif kind == "ring":
        circle_badge(canvas, x + D - diag, y + diag, r_badge, RED + (255,), ring=(245, 244, 240, 255), ring_w=5)
    elif kind == "ink":
        circle_badge(canvas, x + D - diag, y + diag, r_badge, CINNABAR + (255,), ring=(28, 24, 20, 235), ring_w=5)
    elif kind == "num":
        circle_badge(canvas, x + D - diag, y + diag, r_badge * 1.16, RED + (255,),
                     ring=(245, 244, 240, 255), ring_w=5, num="3")
    elif kind == "glow":
        glow(canvas, cx, cy, R, CINNABAR)
    dr = ImageDraw.Draw(canvas)
    tb = dr.textbbox((0, 0), name, font=f_name)
    dr.text((x + D / 2 - (tb[2] - tb[0]) / 2, y + D + 16), name, font=f_name, fill=(232, 226, 212))
    dr.multiline_text((x + D / 2, y + D + 48), note, font=f_note, fill=(168, 176, 178),
                      align="center", anchor="ma", spacing=6)

out = "/mnt/f/agent_loop/docs/mockups/hud-unread-dot-options.png"
canvas.convert("RGB").save(out, quality=95)
print("saved", out, canvas.size)
