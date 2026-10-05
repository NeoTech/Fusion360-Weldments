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


GLYPHS = {'weldmentCope': glyph_cope, 'weldmentMiter': glyph_miter,
          'weldmentButt': glyph_butt, 'weldmentBom': glyph_bom}


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name, fn in GLYPHS.items():
        img = fn()
        out = os.path.join(base, 'commands', name, 'resources')
        os.makedirs(out, exist_ok=True)
        write_png(os.path.join(out, '64x64.png'), img)
        write_png(os.path.join(out, '32x32.png'), down(img, 2))
        write_png(os.path.join(out, '16x16.png'), down(img, 4))
        print('wrote', out)


if __name__ == '__main__':
    main()
