"""Isometric preview of a generated (or vanilla) map, drawn with the game's own sprites.

  python preview.py <maps folder> X0 Y0 X1 Y1 out.png [maxLevel] [scale]

X/Y are absolute world squares (cell*256 + local). maxLevel 0 = ground floor only (see inside buildings).
"""
import os, sys
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sprites import library
from lotreader import cell_squares


def render_iso(squares, x0, y0, x1, y1, out, zmax=3, scale=0.5):
    lib = library()
    cells = {}
    for x, y, z, t in squares:
        if x0 <= x < x1 and y0 <= y < y1 and z <= zmax:
            cells[(x, y, z)] = t
    w, h = x1 - x0, y1 - y0
    W = (w + h) * 64 + 256
    H = (w + h) * 32 + 64 + 192 * (zmax + 1) + 256
    img = Image.new('RGBA', (W, H), (30, 30, 30, 255))
    oxs = h * 64 + 64
    oys = 192 * zmax + 64 + 192
    for (x, y, z) in sorted(cells, key=lambda k: (k[2], k[0] + k[1], k[0])):
        lx, ly = x - x0, y - y0
        bx = oxs + (lx - ly) * 64
        by = oys + (lx + ly) * 32 + 64 - z * 192
        for n in cells[(x, y, z)]:
            s = lib.get(n)
            if not s:
                continue
            im, ox, oy, ow, oh = s
            px, py = int(bx - ow / 2 + ox), int(by - oh + oy)
            if px >= 0 and py >= 0:
                img.alpha_composite(im, (px, py))
    if scale != 1:
        img = img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
    img.convert('RGB').save(out)


def world_squares(mapdir, x0, y0, x1, y1):
    for cx in range(x0 // 256, (x1 - 1) // 256 + 1):
        for cy in range(y0 // 256, (y1 - 1) // 256 + 1):
            if os.path.exists(f'{mapdir}/{cx}_{cy}.lotheader'):
                for x, y, z, t in cell_squares(mapdir, cx, cy):
                    yield x + cx * 256, y + cy * 256, z, t


if __name__ == '__main__':
    mapdir = sys.argv[1]
    x0, y0, x1, y1 = map(int, sys.argv[2:6])
    out = sys.argv[6]
    zmax = int(sys.argv[7]) if len(sys.argv) > 7 else 3
    scale = float(sys.argv[8]) if len(sys.argv) > 8 else 0.5
    render_iso(world_squares(mapdir, x0, y0, x1, y1), x0, y0, x1, y1, out, zmax, scale)
    print('wrote', out)
