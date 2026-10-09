"""Generate ribbon icons for the toolbox commands (one-off dev script).

Pure stdlib (zlib + struct): draws simple line-art glyphs on a transparent
64x64 canvas, box-downsamples to 32 and 16, and writes PNGs named the way the
existing weldment command does (16x16/32x32/64x64.png).

Glyphs (consistent square-member line art, accent = the cut/face):
  weldmentCope  -- a tube with a rectangular saddle notch cut in its foot
  weldmentMiter -- an L frame joined on a 45-degree miter face
  weldmentButt  -- a T: a backing bar butting flush onto a through bar
  weldmentBom   -- a document list: page outline, header bar, rows
"""

import os
import struct
import zlib

INK = (70, 70, 70, 255)           # dark gray line art
ACCENT = (226, 118, 32, 255)      # Fusion-orange highlight

S = 64  # canvas size


def canvas():
    return [[(0, 0, 0, 0) for _ in range(S)] for _ in range(S)]


def px(img, x, y, c):
    x, y = int(round(x)), int(round(y))
    if 0 <= x < S and 0 <= y < S:
        img[y][x] = c


def line(img, p0, p1, c, w=3):
    (x0, y0), (x1, y1) = p0, p1
    n = int(max(abs(x1 - x0), abs(y1 - y0))) * 2 + 1
    r = w / 2.0
    for i in range(n + 1):
        t = i / n
        x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        for dy in range(-int(r) - 1, int(r) + 2):
            for dx in range(-int(r) - 1, int(r) + 2):
                if dx * dx + dy * dy <= r * r + 0.5:
                    px(img, x + dx, y + dy, c)


def poly(img, pts, c, w=3):
    for j in range(len(pts)):
        line(img, pts[j], pts[(j + 1) % len(pts)], c, w)


def rect(img, x0, y0, x1, y1, c, w=3):
    poly(img, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], c, w)


def fill(img, x0, y0, x1, y1, c):
    for y in range(int(y0), int(y1) + 1):
        for x in range(int(x0), int(x1) + 1):
            px(img, x, y, c)


def arc(img, cx, cy, r, a0, a1, c, w=3):
    # circular arc, angles in degrees, sampled densely
    import math as _m
    n = max(int(abs(a1 - a0) * r * 0.06), 24)
    pts = [(cx + r * _m.cos(_m.radians(a0 + (a1 - a0) * i / n)),
            cy + r * _m.sin(_m.radians(a0 + (a1 - a0) * i / n)))
           for i in range(n + 1)]
    for j in range(len(pts) - 1):
        line(img, pts[j], pts[j + 1], c, w)


def down(img, k):
    out = [[(0, 0, 0, 0) for _ in range(S // k)] for _ in range(S // k)]
    for y in range(S // k):
        for x in range(S // k):
            acc = [0, 0, 0, 0]
            for dy in range(k):
                for dx in range(k):
                    p = img[y * k + dy][x * k + dx]
                    for i in range(4):
                        acc[i] += p[i]
            a = acc[3] // (k * k)
            if a == 0:
                out[y][x] = (0, 0, 0, 0)
            else:
                out[y][x] = tuple(v * (k * k) // max(acc[3], 1) for v in acc[:3]) + (a,)
    return out


def write_png(path, img):
    h = len(img)
    w = len(img[0])
    raw = b''.join(b'\x00' + b''.join(struct.pack('4B', *p) for p in row)
                   for row in img)

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(raw, 9))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(png)


def glyph_cope():
    img = canvas()
    # vertical tube with a rectangular saddle notch removed at the foot
    line(img, (20, 8), (44, 8), INK)          # top
    line(img, (20, 8), (20, 52), INK)         # left side
    line(img, (44, 8), (44, 52), INK)         # right side
    line(img, (20, 52), (26, 52), INK)        # foot left
    line(img, (26, 52), (26, 40), INK)        # notch down
    line(img, (26, 40), (38, 40), ACCENT)     # notch floor (the cut)
    line(img, (38, 40), (38, 52), INK)        # notch up
    line(img, (38, 52), (44, 52), INK)        # foot right
    return img


def glyph_miter():
    img = canvas()
    # an L frame whose corner is joined on a 45-degree miter face
    outline = [(6, 10), (38, 10), (38, 58), (22, 58), (22, 26), (6, 26)]
    poly(img, outline, INK)
    line(img, (22, 26), (38, 10), ACCENT)     # the miter face
    return img


def glyph_butt():
    img = canvas()
    # through bar along the bottom, backing bar butting flush onto it
    rect(img, 6, 38, 58, 54, INK)             # through member
    line(img, (26, 8), (26, 38), INK)         # backing left
    line(img, (38, 8), (38, 38), INK)         # backing right
    line(img, (26, 8), (38, 8), INK)          # backing top
    line(img, (26, 38), (38, 38), ACCENT)     # flush butt face (the cut)
    return img


def glyph_bom():
    img = canvas()
    rect(img, 14, 6, 50, 58, INK)             # page
    fill(img, 16, 8, 48, 17, ACCENT)          # header bar
    line(img, (14, 18), (50, 18), INK)         # header rule
    for y in (28, 36, 44, 52):                # list rows
        line(img, (20, y), (44, y), INK, 2)
    return img


def glyph_weldment():
    img = canvas()
    # a hollow structural profile (the member being created)
    rect(img, 12, 20, 52, 44, INK)            # outer section
    rect(img, 20, 27, 44, 37, ACCENT)          # hollow interior
    return img


def glyph_bend():
    img = canvas()
    # a tube bent through a smooth 90-degree curve (distinct from the L frames)
    cx, cy = 14, 50
    arc(img, cx, cy, 34, -90, 0, INK)                # outer wall
    arc(img, cx, cy, 22, -90, 0, INK)                # inner wall
    line(img, (cx, cy - 34), (cx, cy - 22), INK)     # top cap
    line(img, (cx + 34, cy), (cx + 22, cy), INK)     # right cap
    arc(img, cx, cy, 28, -80, -10, ACCENT, 3)        # bend centreline accent
    return img


def glyph_bend_table():
    img = canvas()
    # a bent tube carrying two scribed mark ticks -- the bend instruction sheet
    cx, cy = 10, 46
    arc(img, cx, cy, 30, -90, 0, INK)               # outer wall
    arc(img, cx, cy, 20, -90, 0, INK)               # inner wall
    line(img, (cx, cy - 30), (cx, cy - 20), INK)    # top cap
    line(img, (cx + 30, cy), (cx + 20, cy), INK)    # right cap
    # tick marks across the tube: start/end marks on the straight legs
    line(img, (cx + 22, cy - 26), (cx + 32, cy - 20), ACCENT, 3)
    line(img, (cx + 24, cy - 2), (cx + 24, cy + 8), ACCENT, 3)
    # a small table grid at the lower right (the instruction list)
    rect(img, 38, 34, 58, 56, INK)
    line(img, (38, 42), (58, 42), INK)
    line(img, (48, 34), (48, 56), INK)
    return img


def glyph_gusset():
    img = canvas()
    # an L corner with a triangular gusset plate in the joint
    poly(img, [(10, 12), (24, 12), (24, 40), (52, 40),
               (52, 54), (10, 54)], INK)
    poly(img, [(24, 26), (24, 40), (38, 40)], ACCENT)
    return img


def glyph_tube():
    img = canvas()
    # a straight tube (two parallel walls + end cap) with a rotation arrow --
    # profile creation only, no joint
    line(img, (10, 22), (46, 22), INK)             # top wall
    line(img, (10, 42), (46, 42), INK)             # bottom wall
    line(img, (10, 22), (10, 42), INK)             # start cap
    arc(img, 46, 32, 10, -90, 90, INK)             # rounded end cap
    arc(img, 28, 52, 8, 200, 340, ACCENT)           # rotation arrow
    line(img, (35, 49), (38, 52), ACCENT, 2)        # arrow head
    line(img, (35, 55), (38, 52), ACCENT, 2)
    return img


def glyph_gusset_profile():
    img = canvas()
    # an I-beam cross-section with a gusset inside the flanges
    rect(img, 14, 12, 50, 22, INK)            # top flange
    rect(img, 14, 42, 50, 52, INK)            # bottom flange
    rect(img, 28, 22, 36, 42, INK)            # web
    poly(img, [(36, 28), (36, 40), (48, 40)], ACCENT)  # gusset
    return img


GLYPHS = {'weldment': glyph_weldment,
          'weldmentCope': glyph_cope, 'weldmentMiter': glyph_miter,
          'weldmentButt': glyph_butt, 'weldmentBom': glyph_bom,
          'weldmentBend': glyph_bend,
          'weldmentBendTable': glyph_bend_table,
          'weldmentGusset': glyph_gusset, 'weldmentTube': glyph_tube,
          'weldmentGussetProfile': glyph_gusset_profile}


def contact_sheet(imgs):
    # lay the 64x64 glyphs out in a row over a mid-gray (theme-agnostic) bg
    n = len(imgs)
    pad = 6
    w = n * (S + pad) + pad
    h = S + 2 * pad
    sheet = [[(128, 128, 128, 255) for _ in range(w)] for _ in range(h)]
    for idx, img in enumerate(imgs):
        ox = pad + idx * (S + pad)
        for y in range(S):
            for x in range(S):
                r, g, b, a = img[y][x]
                if a:
                    bg = sheet[y + pad][ox + x]
                    sheet[y + pad][ox + x] = (
                        (r * a + bg[0] * (255 - a)) // 255,
                        (g * a + bg[1] * (255 - a)) // 255,
                        (b * a + bg[2] * (255 - a)) // 255, 255)
    return sheet


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    imgs = []
    for name, fn in GLYPHS.items():
        img = fn()
        imgs.append(img)
        out = os.path.join(base, 'commands', name, 'resources')
        os.makedirs(out, exist_ok=True)
        write_png(os.path.join(out, '64x64.png'), img)
        write_png(os.path.join(out, '32x32.png'), down(img, 2))
        write_png(os.path.join(out, '16x16.png'), down(img, 4))
        print('wrote', out)
    sheet = contact_sheet(imgs)
    write_png(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'contact_sheet.png'), sheet)
    print('contact sheet written')


if __name__ == '__main__':
    main()
