"""Pitched roofs the way the vanilla map has them.

  python pitched.py        cut the roofs off vanilla houses -> cache/vanilla_pitched.json

Vanilla houses (276 of 277 sampled) have pitched roofs drawn with BuildingEd: slope tiles (roofs_0N) on the
levels above the top floor, gable ends (walls_exterior_roofs_0N), gutters and trims (roofs_accents_01).
The game does not ship the editor's roof rules, so this module copies whole vanilla roofs: for every vanilla
house whose top floor is a plain rectangle and that stands clear of its neighbours, every roof tile around
it is stored relative to the footprint's corner and the top floor. A generated house of the same size gets
one of those roofs; a longer or shorter one gets a roof of the same width stretched along its ridge, by
repeating or dropping a row where the roof is the same all along.
"""
import collections, glob, json, os, random, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cache', 'vanilla_pitched.json')
ROOFISH = ('roofs_0', 'roofs_accents', 'walls_exterior_roofs', 'roofs_burnt')
RES = {'bedroom', 'livingroom', 'kitchen', 'bathroom', 'hall', 'closet', 'kidsbedroom', 'diningroom', 'laundry',
       'empty', 'storage', 'attic', 'garage', 'hallway', 'shed'}
M = 2                                   # margin around the footprint searched for roof pieces


_lib = None


def coverage(tiles, W, H):
    """How much of the house's ceiling the roof's sprites cover when drawn the way the game draws them
    (each level 192 px up; a square's diamond 128 x 64 px). 1.0 = closed."""
    global _lib
    import numpy as np
    from PIL import Image
    if _lib is None:
        from sprites import library
        _lib = library()
    size = (W + H + 12) * 64
    oxs, oys = (H + 6) * 64, 192 * 7 + 64

    def place(img, name, x, y, z):
        s_ = _lib.get(name)
        if not s_:
            return
        im, ox, oy, ow, oh = s_
        bx = oxs + (x - y) * 64
        by = oys + (x + y) * 32 + 64 - z * 192
        img.alpha_composite(im, (int(bx - ow / 2 + ox), int(by - oh + oy)))
    ceil = Image.new('RGBA', (size, size + 1400), (0, 0, 0, 0))
    for x in range(W):
        for y in range(H):
            place(ceil, 'floors_interior_tilesandwood_01_42', x, y, 1)
    roof = Image.new('RGBA', ceil.size, (0, 0, 0, 0))
    for dx, dy, dz, tl in tiles:
        for t in tl:
            place(roof, t, dx, dy, dz)
    bb = ceil.getbbox()                                # only the ceiling's pixels count
    c = np.array(ceil.crop(bb).getchannel('A')) > 0
    r = np.array(roof.crop(bb).getchannel('A')) > 0
    return float((c & r).sum()) / max(1, int(c.sum()))


def harvest(vanilla_dir=None, out=OUT):
    from lotreader import rd_header, rd_pack, iter_squares
    vanilla_dir = vanilla_dir or os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
    stamps = collections.defaultdict(collections.Counter)
    for hp in sorted(glob.glob(os.path.join(vanilla_dir, '*.lotheader'))):
        h = rd_header(hp)
        if not h['rooms']:
            continue
        cell = os.path.basename(hp)[:-10]
        try:
            ver, n, offs, pack = rd_pack(os.path.join(vanilla_dir, 'world_%s.lotpack' % cell))
        except Exception:
            continue
        sq = {(x, y, z): t for x, y, z, t in iter_squares(h, pack, offs, n)}
        rooms = h['rooms']
        foot = []
        for bl in h['blds']:
            rs = [rooms[i] for i in bl if rooms[i][0] != 'emptyoutside']     # the porch-roof zones on the level above
            if not rs or not all(r[0] in RES for r in rs):
                continue
            top = max(r[1] for r in rs)
            if min(r[1] for r in rs) < 0:
                continue
            cells = {(x + i, y + j) for r in rs if r[1] == top for (x, y, w, hh) in r[2] for i in range(w) for j in range(hh)}
            allc = {(x + i, y + j) for r in rs for (x, y, w, hh) in r[2] for i in range(w) for j in range(hh)}
            if not cells:
                continue
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            foot.append((x0, x1, y0, y1, top, cells, allc))
        for i, (x0, x1, y0, y1, top, cells, allc) in enumerate(foot):
            W, H = x1 - x0 + 1, y1 - y0 + 1
            if len(cells) != W * H or allc != cells and top == 0:
                continue                        # an L-shaped house, or a porch room sticking out
            if x0 - M < 0 or y0 - M < 0 or x1 + M + 9 > 255 or y1 + M + 9 > 255:
                continue
            clear = all(ox1 < x0 - M - 2 or ox0 > x1 + M + 2 or oy1 < y0 - M - 2 or oy0 > y1 + M + 2
                        for j, (ox0, ox1, oy0, oy1, *_r) in enumerate(foot) if j != i)
            if not clear:
                continue
            tiles = []
            for dz in range(0, 7):
                z = top + dz
                for x in range(x0 - M, x1 + M + 1):
                    for y in range(y0 - M, y1 + M + 1):
                        tl = [t for t in sq.get((x, y, z), ()) if t.startswith(ROOFISH)]
                        if tl:
                            tiles.append((x - x0, y - y0, dz, tuple(tl)))
            if not any(t[2] >= 1 for t in tiles):
                continue                        # no roof above the top floor: a flat one, or roofless
            if any(not (-1 <= t[0] <= W and -1 <= t[1] <= H) for t in tiles):
                continue                        # roofs over a porch or a garage beside the house: not this footprint's
            slopes = sum(1 for t in tiles if t[2] >= 1 and any(x.startswith('roofs_0') for x in t[3]))
            if slopes < 0.4 * W * H:
                continue                        # a scrap of roof, not a roof
            if coverage(tiles, W, H) < 0.97:
                continue                        # drawn, it leaves part of the house open to the sky
            stamps['%d,%d' % (W, H)][tuple(sorted(tiles))] += 1
    data = {k: [dict(n=n, t=[[a, b, c, list(d)] for a, b, c, d in key]) for key, n in v.most_common(4)]
            for k, v in stamps.items()}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(data, open(out, 'w'), separators=(',', ':'))
    print('%d footprint sizes with a vanilla roof, from %d houses' % (len(data), sum(sum(v.values()) for v in stamps.values())))
    return data


_data = None


def load():
    global _data
    if _data is None:
        if not os.path.isfile(OUT):
            harvest()
        _data = {k: [c for c in lst if not _patched(c['t'])] for k, lst in json.load(open(OUT)).items()}
        _data = {k: v for k, v in _data.items() if v}
    return _data


# roof pieces that mean the same in every roof sheet (vents, the flat top): a roof may borrow them from another
SHARED = set(range(16)) | set(range(20, 32)) | {70, 71, 90, 91, 92, 93}


def _patched(tiles):
    """A vanilla shack roof patched with boards of another sheet (corrugated iron with wooden planks): on a
    suburban house it reads as a glitch, so houses do not get these few."""
    sheets = collections.Counter(t.rsplit('_', 1)[0] for _, _, _, tl in tiles for t in tl if t.startswith('roofs_0'))
    if len(sheets) < 2:
        return False
    main = sheets.most_common(1)[0][0]
    return any(not t.startswith(main + '_') and int(t.rsplit('_', 1)[1]) not in SHARED
               for _, _, _, tl in tiles for t in tl if t.startswith('roofs_0'))


def _stretch(tiles, W0, H0, W, H):
    """A roof for a W0 x H0 footprint made to fit W x H by repeating or dropping a row (column) that is
    the same as the one after it, i.e. a stretch of plain roof along the ridge. -> tiles or None"""
    out = [tuple(t[:3]) + (tuple(t[3]),) for t in tiles]
    for axis, have, want in ((1, H0, H), (0, W0, W)):
        if want == have:
            continue
        rows = collections.defaultdict(set)
        for t in out:
            rows[t[axis]].add(t[:axis] + t[axis + 1:])
        same = [c for c in range(1, have - 1) if rows[c] and rows[c] == rows[c + 1]]
        if not same:
            return None
        mid = same[len(same) // 2]
        if want < have:                                  # shorten only within a run of plain rows
            run = mid
            while run + 1 in same or (run in same and rows[run + 1] == rows[mid]):
                run += 1
            if run - mid + 1 < have - want:
                return None
        new = []
        for t in out:
            c = t[axis]
            if want > have:
                if c > mid:
                    new.append(t[:axis] + (c + want - have,) + t[axis + 1:])
                else:
                    new.append(t)
                    if c == mid:
                        new += [t[:axis] + (c + k,) + t[axis + 1:] for k in range(1, want - have + 1)]
            else:
                cut = have - want
                if mid <= c < mid + cut:
                    continue
                new.append(t[:axis] + (c - cut,) + t[axis + 1:] if c >= mid + cut else t)
        out = new
    return out


def paintable(tiles):
    """Every roof piece means the same in every roof sheet, so the roof can take any colour (a house under
    several roofs, a row of homes: one colour; a plank roof cannot be painted shingle)."""
    return all(int(t.rsplit('_', 1)[1]) in SHARED for _, _, _, tl in tiles for t in tl if t.startswith('roofs_0'))


def roof_for(W, H, rng, paint=False):
    """-> list of (dx, dy, dz, [tiles]) for a W x H rectangular top floor, or None.
    paint: only roofs that can be recoloured (falls back to any when no such roof fits)."""
    if paint is True:
        r = roof_for(W, H, rng, 'only')
        return r if r is not None else roof_for(W, H, rng)
    d = load()
    if paint == 'only':
        d = _paintable_data()
    k = '%d,%d' % (W, H)
    if k in d:
        c = rng.choices(d[k], weights=[s['n'] for s in d[k]])[0]
        return [tuple(t) for t in c['t']]
    k = k + (':paint' if paint else '')
    # the same width or length, stretched along the ridge from the nearest size that allows it (the answer
    # depends only on the size, and checking a stretched roof means drawing it: once per size)
    if k in _stretched:
        r = _stretched[k]
        return list(r) if r is not None else None
    _stretched[k] = None
    cands = []
    for key, lst in d.items():
        w0, h0 = map(int, key.split(','))
        if w0 == W or h0 == H:                         # stretched along the ridge only, any length
            cands.append((abs(w0 - W) + abs(h0 - H), w0, h0, lst))
    for _, w0, h0, lst in sorted(cands, key=lambda c: c[0]):
        for c in sorted(lst, key=lambda c: -c['n']):
            r = _stretch(c['t'], w0, h0, W, H)
            if r and coverage(r, W, H) >= 0.97:     # still closed once stretched
                _stretched[k] = [tuple(t) for t in r]
                return list(_stretched[k])
    return None


_stretched = {}
_paint = None


def _paintable_data():
    global _paint
    if _paint is None:
        _paint = {k: [c for c in lst if paintable(c['t'])] for k, lst in load().items()}
        _paint = {k: v for k, v in _paint.items() if v}
    return _paint

if __name__ == '__main__':
    d = harvest()
    print(sorted(d, key=lambda k: -sum(s['n'] for s in d[k]))[:40])
