"""The picture the game's Mods screen shows for a map: an isometric slice of its busiest spot, drawn with the
game's own sprites (preview.py), as poster.png (512 x 512) and icon.png (64 x 64) beside each mod.info.

  python poster.py <mod folder>          e.g. output/Austin6thStreet

The spot is the heart of the town: where the most shop, cafe, bar and restaurant floor is at street level
(the downtown strip); a town with hardly any shops shows its middle, the place it was made around. It is
drawn at street level with the roofs off, so the shops can be seen. Replace poster.png with an in-game
screenshot any time: the game shows whatever is there.
"""
import glob, os, sys, tempfile
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preview
from lotreader import rd_header

CELL = 256
BG = (30, 30, 30)


STREET = ('store', 'shop', 'cafe', 'bar', 'dining', 'restaurant', 'diner', 'bakery', 'pharmacy', 'grocery',
          'aesthetic', 'pizza', 'burger', 'jays', 'spiffo', 'bank', 'theatre')


def busiest(mapdir, span):
    """World square at the middle of the span x span block with the most shop floor at street level, or the
    middle of the map when there is little of it."""
    cells = []
    for hp in glob.glob(os.path.join(mapdir, '*.lotheader')):
        cx, cy = map(int, os.path.basename(hp)[:-10].split('_'))
        cells.append((cx, cy, rd_header(hp)))
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    X0, Y0 = min(xs), min(ys)
    floor = np.zeros(((max(ys) - Y0 + 1) * CELL, (max(xs) - X0 + 1) * CELL), np.float32)
    for cx, cy, h in cells:
        for name, lvl, rects, _ in h['rooms']:
            if lvl != 0 or not any(k in name for k in STREET) or 'storage' in name or 'kitchen' in name:
                continue
            for (x, y, w, hh) in rects:
                ox, oy = (cx - X0) * CELL + x, (cy - Y0) * CELL + y
                floor[oy:oy + hh, ox:ox + w] += 1.0
    score = ndi.uniform_filter(floor, span)
    if score.max() < 0.08:                           # hardly a shop: the middle of the map
        return (X0 + (max(xs) - X0 + 1) / 2.0) * CELL, (Y0 + (max(ys) - Y0 + 1) / 2.0) * CELL
    m = span // 2 + 4                                # keep the slice inside the map
    if score.shape[0] > 2 * m and score.shape[1] > 2 * m:
        inner = score[m:-m, m:-m]
        y, x = np.unravel_index(int(np.argmax(inner)), inner.shape)
        y, x = y + m, x + m
    else:
        y, x = score.shape[0] // 2, score.shape[1] // 2
    return X0 * CELL + int(x), Y0 * CELL + int(y)


def make(mod_dir, span=56, zmax=0):
    mapdirs = glob.glob(os.path.join(mod_dir, 'common', 'media', 'maps', '*'))
    if not mapdirs:
        raise SystemExit('no map under %s/common/media/maps' % mod_dir)
    mapdir = mapdirs[0]
    cx, cy = map(int, busiest(mapdir, span))
    x0, y0, x1, y1 = cx - span // 2, cy - span // 2, cx + span // 2, cy + span // 2
    tmp = os.path.join(tempfile.gettempdir(), 'osm2zomboid_poster.png')
    # a building is stored whole in the cell of its north-west corner: read a cell further back too
    squares = preview.world_squares(mapdir, x0 - CELL, y0 - CELL, x1, y1)
    preview.render_iso(squares, x0, y0, x1, y1, tmp, zmax, 0.35)
    im = Image.open(tmp).convert('RGB')
    a = np.asarray(im).astype(int)
    fg = np.abs(a - np.array(BG)).sum(axis=2) > 12
    ys, xs = np.nonzero(fg)
    if len(xs):
        im = im.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    # the middle of the slice, square: the street corner rather than empty sky above the towers
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2 if h > side else 0
    sq = im.crop((left, top, left + side, top + side))
    out = []
    for d in [mod_dir] + [p for p in (os.path.join(mod_dir, '42'),) if os.path.isdir(p)]:
        sq.resize((512, 512), Image.LANCZOS).save(os.path.join(d, 'poster.png'))
        sq.resize((64, 64), Image.LANCZOS).save(os.path.join(d, 'icon.png'))
        mi = os.path.join(d, 'mod.info')
        if os.path.isfile(mi):
            lines = [l for l in open(mi, encoding='utf8').read().splitlines() if not l.startswith(('poster=', 'icon='))]
            open(mi, 'w', encoding='utf8').write('\n'.join(lines + ['poster=poster.png', 'icon=icon.png']) + '\n')
        out.append(d)
    print('poster: slice around %d,%d -> %s' % (cx, cy, ', '.join(out)))


if __name__ == '__main__':
    make(sys.argv[1])
