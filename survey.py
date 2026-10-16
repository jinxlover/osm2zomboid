"""Survey vanilla map cells before placing a map mod.

  python survey.py CX0 CY0 CX1 CY1 out.png [pixels_per_square]

Draws cells CX0..CX1 x CY0..CY1 (inclusive) of the vanilla map with the cell grid: roads dark grey,
other paving light grey, gravel tan, dirt tracks brown, railway orange, grass green, trees dark green,
water blue, buildings red.
Prints one line per cell with what a mod placed there would replace (rooms, road squares, trees).
"""
import os, sys
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog
from lotreader import rd_header, rd_pack, iter_squares

CELL = 256
ROAD_BLOCKS = {0, 4, 5, 6}         # blends_street_01 16-tile blocks that are asphalt / concrete road surface
C_VOID, C_GRASS, C_TREE, C_ROAD, C_PAVE, C_TRACK, C_WATER, C_BLD, C_DIRT, C_OTHER, C_GRAVEL, C_RAIL = range(12)
COLORS = np.array([(0, 0, 0), (92, 122, 60), (38, 74, 34), (70, 70, 74), (176, 172, 160), (140, 112, 70),
                   (60, 110, 170), (200, 60, 50), (150, 120, 80), (120, 120, 120), (190, 160, 120), (230, 130, 30)], np.uint8)


def classify(tiles):
    t = tiles[0]
    sheet, _, idx = t.rpartition('_')
    i = int(idx) if idx.isdigit() else 0
    c = C_OTHER
    if sheet == 'blends_street_01':
        b = i // 16
        c = C_ROAD if b in ROAD_BLOCKS else (C_TRACK if b == 1 else (C_GRAVEL if b == 2 else C_PAVE))
    elif sheet == 'blends_natural_02':
        c = C_WATER
    elif sheet == 'blends_natural_01':
        c = C_GRASS if 16 <= i < 64 else C_DIRT
    elif sheet.startswith('floors_exterior'):
        c = C_PAVE
    for o in tiles[1:]:
        if o.startswith('industry_railroad'):
            return C_RAIL
        if c in (C_GRASS, C_DIRT) and o.startswith(('vegetation_trees', 'jumbo_tree')):
            return C_TREE
    return c


def read_cell(vdir, cx, cy):
    """-> (class array 256x256 [y, x], number of rooms) or (None, 0) when the cell does not exist."""
    hp = os.path.join(vdir, f'{cx}_{cy}.lotheader')
    if not os.path.isfile(hp):
        return None, 0
    h = rd_header(hp)
    ver, n, offs, pack = rd_pack(os.path.join(vdir, f'world_{cx}_{cy}.lotpack'))
    a = np.zeros((CELL, CELL), np.uint8)
    for x, y, z, tiles in iter_squares(h, pack, offs, n):
        if z == 0 and tiles:
            a[y, x] = classify(tiles)
    for name, lvl, rects, _ in h['rooms']:
        if lvl == 0:
            for (x, y, w, hh) in rects:
                a[y:y + hh, x:x + w] = C_BLD
    return a, len(h['rooms'])


def survey(cx0, cy0, cx1, cy1, vdir=None):
    vdir = vdir or os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
    nx, ny = cx1 - cx0 + 1, cy1 - cy0 + 1
    A = np.zeros((ny * CELL, nx * CELL), np.uint8)
    info = {}
    for cy in range(cy0, cy1 + 1):
        for cx in range(cx0, cx1 + 1):
            a, rooms = read_cell(vdir, cx, cy)
            if a is None:
                info[(cx, cy)] = None
                continue
            A[(cy - cy0) * CELL:(cy - cy0 + 1) * CELL, (cx - cx0) * CELL:(cx - cx0 + 1) * CELL] = a
            info[(cx, cy)] = dict(rooms=rooms, road=int((a == C_ROAD).sum()), track=int((a == C_TRACK).sum()),
                                  rail=int((a == C_RAIL).sum()), gravel=int((a == C_GRAVEL).sum()),
                                  pave=int((a == C_PAVE).sum()), tree=int((a == C_TREE).sum()),
                                  water=int((a == C_WATER).sum()), bld=int((a == C_BLD).sum()))
    return A, info


def render(A, cx0, cy0, out, scale=0.5):
    img = Image.fromarray(COLORS[A])
    if scale != 1:
        img = img.resize((int(A.shape[1] * scale), int(A.shape[0] * scale)), Image.NEAREST)
    d = ImageDraw.Draw(img)
    step = CELL * scale
    for i in range(A.shape[1] // CELL + 1):
        d.line([(i * step, 0), (i * step, img.height)], fill=(255, 255, 0), width=1)
    for j in range(A.shape[0] // CELL + 1):
        d.line([(0, j * step), (img.width, j * step)], fill=(255, 255, 0), width=1)
    for j in range(A.shape[0] // CELL):
        for i in range(A.shape[1] // CELL):
            d.text((i * step + 3, j * step + 2), f'{cx0 + i},{cy0 + j}', fill=(255, 255, 255))
    img.save(out)


if __name__ == '__main__':
    cx0, cy0, cx1, cy1 = map(int, sys.argv[1:5])
    out = sys.argv[5]
    scale = float(sys.argv[6]) if len(sys.argv) > 6 else 0.5
    A, info = survey(cx0, cy0, cx1, cy1)
    render(A, cx0, cy0, out, scale)
    for (cx, cy), v in sorted(info.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if v is None:
            print(f'{cx},{cy}: no vanilla cell')
        else:
            kind = 'forest' if not (v['rooms'] or v['road'] or v['pave'] or v['bld'] or v['rail'] or v['gravel']) else ''
            print(f'{cx},{cy}: rooms {v["rooms"]:4d} road {v["road"]:6d} rail {v["rail"]:5d} gravel {v["gravel"]:5d} '
                  f'track {v["track"]:5d} paving {v["pave"]:5d} trees {v["tree"]:6d} water {v["water"]:6d} {kind}')
    print('wrote', out)
