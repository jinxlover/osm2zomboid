"""Whole vanilla rooms as templates: furnish a generated room by copying a furnished vanilla room of the same kind.

  python interiors.py        cut the furnished rooms out of the vanilla map -> cache/vanilla_interiors.json

furnish.py rolls a room from statistics: the right things, but not vanilla's arrangement (a checkout by the
door, shelves in aisles, fridges along the back wall). This module copies arrangements whole. For every
vanilla room it keeps the room's shape, its doors, its floor tile, the paint of its walls and every piece of
furniture in it as a piece (sheet, name, group, facing, which variant, where), so that a template can be
turned to face a generated room's door: each piece is swapped for the same piece facing the new way
(catalog.variants), and a piece that does not come in that facing rules the turn out.
"""
import collections, glob, json, os, random, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cache', 'vanilla_interiors.json')
PER_NAME = 250                     # templates kept per room type
STRUCT = ('WallN', 'WallW', 'WallNW', 'WallSE', 'WallNTrans', 'WallWTrans', 'WallNWTrans', 'DoorWallN', 'DoorWallW',
          'doorN', 'doorW', 'doorFrN', 'doorFrW', 'WindowN', 'WindowW', 'windowN', 'windowW', 'lightswitch',
          'stairsTN', 'stairsMN', 'stairsBN', 'stairsTW', 'stairsMW', 'stairsBW', 'solidfloor')
ROT = {'N': 'E', 'E': 'S', 'S': 'W', 'W': 'N'}
_split = re.compile(r'^(.*)_(\d+)$')
_var = {}


def _is_object(t, p):
    if not p or any(k in p for k in STRUCT):
        return False
    if t.startswith(('floors_', 'blends_', 'overlay_', 'walls_', 'roofs_', 'street_', 'd_', 'e_', 'f_', 'trash_')):
        return False
    return 'CustomName' in p or 'container' in p or 'solid' in p or 'solidtrans' in p or 'Facing' in p


def piece_of(t, p):
    """-> (sheet, custom, group, facing, variant index, gx, gy) or None when the catalog has no variants."""
    m = _split.match(t)
    if not m or 'CustomName' not in p:
        return None
    sheet, custom, group = m.group(1), p.get('CustomName'), p.get('GroupName') or ''
    key = (sheet, custom, group)
    if key not in _var:
        _var[key] = _variants(sheet, custom, group)
    f = p.get('Facing', '')
    for i, v in enumerate(_var[key].get(f, [])):
        for dx, dy, tt in v:
            if tt == t:
                return sheet, custom, group, f, i, dx, dy
    return None


def harvest(vanilla_dir=None, out=OUT):
    from lotreader import rd_header, rd_pack, iter_squares
    cat = catalog.load(os.path.join(HERE, 'cache', 'catalog.json'))
    vanilla_dir = vanilla_dir or os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
    found = collections.defaultdict(list)
    counts = collections.defaultdict(list)                  # name -> [(area, containers, lamps)]
    shop_blds = collections.Counter()                       # buildings that have a room of the kind
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
        room_of = {}
        for ri, (name, lvl, rects, _) in enumerate(rooms):
            for (x, y, w, hh) in rects:
                for a in range(w):
                    for b in range(hh):
                        room_of[(x + a, y + b, lvl)] = ri
        for bl in h['blds']:
            for nm in {rooms[ri][0] for ri in bl}:
                shop_blds[nm] += 1
        for ri, (name, lvl, rects, _) in enumerate(rooms):
            cells = [(x + a, y + b) for (x, y, w, hh) in rects for a in range(w) for b in range(hh)]
            if not cells or len(cells) > 500 or any(not (0 <= x < 256 and 0 <= y < 256) for x, y in cells):
                continue
            cs = set(cells)
            x0, y0 = min(c[0] for c in cells), min(c[1] for c in cells)
            W, H = max(c[0] for c in cells) - x0 + 1, max(c[1] for c in cells) - y0 + 1
            objs, seen, floor, walls = [], set(), collections.Counter(), collections.Counter()
            nc = nl = 0
            for (x, y) in cells:
                tl = sq.get((x, y, lvl), ())
                if tl:
                    floor[tl[0]] += 1
                for t in tl:
                    p = cat.get(t, {})
                    if any(k in p for k in ('WallN', 'WallW', 'WallNW')) and not any(k in p for k in ('DoorWallN', 'DoorWallW', 'WindowN', 'WindowW')):
                        m = _split.match(t)
                        if m:
                            j = int(m.group(2))
                            walls['%s:%d' % (m.group(1), j - j % 4 if j % 16 < 8 else j)] += 1
                    if not _is_object(t, p):
                        continue
                    if 'container' in p:
                        nc += 1
                    if 'LightRadius' in p:
                        nl += 1
                    pc = piece_of(t, p)
                    if pc:
                        sheet, custom, group, f, i, gx, gy = pc
                        key = (sheet, custom, group, f, i, x - gx, y - gy)
                        if key in seen:
                            continue
                        seen.add(key)
                        objs.append([sheet, custom, group, f, i, x - gx - x0, y - gy - y0])
                    else:
                        objs.append(['', '', '', p.get('Facing', ''), -1, x - x0, y - y0, t])
            counts[name].append((len(cells), nc, nl))
            if len(objs) < 2:
                continue
            doors = []
            for (x, y) in cells:
                for side, nb, sqk, keys in (('N', (x, y - 1), (x, y), ('doorN', 'DoorWallN', 'doorFrN')),
                                            ('W', (x - 1, y), (x, y), ('doorW', 'DoorWallW', 'doorFrW')),
                                            ('S', (x, y + 1), (x, y + 1), ('doorN', 'DoorWallN', 'doorFrN')),
                                            ('E', (x + 1, y), (x + 1, y), ('doorW', 'DoorWallW', 'doorFrW'))):
                    if nb in cs:
                        continue
                    if any(k in cat.get(t, {}) for t in sq.get(sqk + (lvl,), ()) for k in keys):
                        doors.append([x - x0, y - y0, side, 1 if (nb[0], nb[1], lvl) not in room_of else 0])
            found[name].append(dict(w=W, h=H, cells=sorted([x - x0, y - y0] for x, y in cells), objs=objs, doors=doors,
                                    floor=floor.most_common(1)[0][0] if floor else None,
                                    wall=walls.most_common(1)[0][0] if walls else None, z=lvl))
    rng = random.Random(1)
    data = {'rooms': {}, 'caps': {}, 'buildings_with': dict(shop_blds)}
    for name, lst in found.items():
        rng.shuffle(lst)
        data['rooms'][name] = lst[:PER_NAME]
    for name, v in counts.items():
        a = sorted(v, key=lambda t: t[1])
        per = sorted(c / float(max(1, ar)) for ar, c, l in v)
        lam = sorted(l for ar, l2, l in [(t[0], t[1], t[2]) for t in v])
        data['caps'][name] = dict(n=len(v), c90=a[int(0.9 * (len(a) - 1))][1], cpa=per[int(0.9 * (len(per) - 1))],
                                  l90=lam[int(0.9 * (len(lam) - 1))])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(data, open(out, 'w'), separators=(',', ':'))
    print('%d room types, %d templates' % (len(data['rooms']), sum(len(v) for v in data['rooms'].values())))
    return data


_data = None


def load():
    global _data
    if _data is None:
        if not os.path.isfile(OUT):
            harvest()
        _data = json.load(open(OUT))
    return _data


# ------------------------------------------------------------------------------------------------- fitting
DIRV = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
ATTACH = {'attachedN': 'N', 'attachedS': 'S', 'attachedW': 'W', 'attachedE': 'E'}
OPPV = {'N': 'S', 'S': 'N', 'W': 'E', 'E': 'W'}
TURN = {'N': 'E', 'E': 'S', 'S': 'W', 'W': 'N'}
MIN_COVER = 0.45
# vanilla rooms that may furnish a room of another name: the same kind of shop under a chain's name, or a
# close cousin where vanilla has few of the kind (loot still follows the room's own name)
COUSINS = {'conveniencestore': ['cornerstore', 'zippeestore', 'generalstore', 'gigamart'],
           'cornerstore': ['conveniencestore', 'zippeestore', 'generalstore'],
           'generalstore': ['cornerstore', 'conveniencestore'],
           'liquorstore': ['cornerstore', 'conveniencestore'],
           'grocery': ['gigamart', 'generalstore'],
           'restaurantdining': ['restaurant', 'dining', 'diner', 'westerndining', 'italianrestaurant', 'chineserestaurant'],
           'westerndining': ['restaurantdining', 'restaurant'], 'italianrestaurant': ['restaurantdining', 'restaurant'],
           'burgerdining': ['restaurantdining', 'diner'], 'jayschicken_dining': ['restaurantdining', 'diner'],
           'pizzawhirled': ['restaurantdining', 'diner'], 'sushidining': ['restaurantdining'],
           'cafe': ['diner', 'donut_dining'], 'bar': ['ww_bar'], 'clothesstore': ['clothingstore', 'leatherclothesstore'],
           'giftstore': ['generalstore'], 'electronicsstore': ['electronicstore'], 'bookstore': ['comicstore'],
           'motelroom': ['motelroomoccupied']}
# a sales floor has a till; vanilla also gives the shop's name to its stock rooms, so templates for these
# rooms must have a register
TILL = {'conveniencestore', 'cornerstore', 'generalstore', 'liquorstore', 'grocery', 'giftstore', 'clothesstore',
        'bookstore', 'electronicsstore', 'toolstore', 'pharmacy', 'jewelrystore', 'musicstore', 'shoestore',
        'sportstore', 'toystore', 'furniturestore', 'movierental', 'gunstore', 'pawnshop', 'artstore', 'bakery',
        'candystore', 'comicstore', 'camerastore', 'departmentstore', 'aesthetic', 'gasstore', 'cafe'}


_vmemo = {}


def _variants(sheet, custom, group):
    """catalog.variants, remembered (it reads the whole catalog each call)."""
    k = (sheet, custom, group or '')
    if k not in _vmemo:
        _vmemo[k] = catalog.variants(sheet, custom, group or None)
    return _vmemo[k]


def _rot_xy(x, y, W, H):
    """A quarter turn clockwise of square (x, y) of a W x H block (north becomes east)."""
    return H - 1 - y, x


_density = None
DENSITY = os.path.join(HERE, 'cache', 'vanilla_density.json')


def density():
    """Median furniture tiles per square of vanilla rooms of each kind (rooms of 30+ squares, Louisville and
    the towns): how full a room should be. -> {room name: tiles per square}"""
    global _density
    if _density is None:
        if not os.path.isfile(DENSITY):
            import audit, statistics
            vdir = os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
            cat = catalog.load()
            got = collections.defaultdict(list)
            for box in ([44, 2, 56, 14], [35, 22, 46, 42], [20, 30, 34, 44]):
                sq, rooms, blds, fl, names = audit.load(vdir, box)
                for nm, z, cells, b in rooms:
                    if len(cells) >= 30:
                        n = sum(1 for c in cells for t in sq.get(c, ()) if _is_object(t, cat.get(t, {}))
                                and not t.startswith('lighting_indoor'))
                        got[nm].append(n / float(len(cells)))
            json.dump({k: statistics.median(v) for k, v in got.items() if len(v) >= 5}, open(DENSITY, 'w'))
        _density = json.load(open(DENSITY))
    return _density


_face_in = None


def face_in_kinds():
    """Kinds of furniture that vanilla (nearly) never turns to face the edge of its room: chairs, beds, desks,
    fridges, ovens, toilets, registers... (under 3% of 80 or more, measured over every vanilla room)."""
    global _face_in
    if _face_in is None:
        tot, out = collections.Counter(), collections.Counter()
        for lst in load()['rooms'].values():
            for t in lst:
                cells = {tuple(c) for c in t['cells']}
                for o in t['objs']:
                    if o[3] in DIRV and o[1]:
                        dx, dy = DIRV[o[3]]
                        tot[o[1]] += 1
                        out[o[1]] += (o[5] + dx, o[6] + dy) not in cells
        _face_in = {k for k in tot if tot[k] >= 80 and out[k] < 0.03 * tot[k]}
    return _face_in


_back_wall = None


def back_wall_kinds():
    """Kinds of furniture that vanilla stands with their back to a wall (90% or more of 60+): a back bar,
    a fridge, a sink, an oven, a toilet, lockers, drawers, and everything hung on a wall."""
    global _back_wall
    if _back_wall is None:
        tot, wall = collections.Counter(), collections.Counter()
        for lst in load()['rooms'].values():
            for t in lst:
                cells = {tuple(c) for c in t['cells']}
                for o in t['objs']:
                    if o[3] in DIRV and o[1]:
                        dx, dy = DIRV[o[3]]
                        tot[o[1]] += 1
                        wall[o[1]] += (o[5] - dx, o[6] - dy) not in cells
        _back_wall = {k for k in tot if tot[k] >= 60 and wall[k] >= 0.9 * tot[k]}
    return _back_wall


class Fitter:
    """Chooses and places vanilla room templates."""

    def __init__(self, seed=1):
        self.d = load()
        self.cat = catalog.load()
        self.rng = random.Random(seed * 31 + 7)
        self.prep = {}
        self.stats = collections.Counter()
        self.recent = collections.defaultdict(lambda: collections.deque(maxlen=6))   # vanilla rooms used lately
        self._search = {}                                # room shape -> the vanilla rooms that fit it

    def _prepare(self, name):
        if name not in self.prep:
            out = []
            src = list(self.d['rooms'].get(name, []))
            for c in COUSINS.get(name, []):
                src += self.d['rooms'].get(c, [])
            if name in TILL:
                src = [t for t in src if any(o[1] == 'Register' for o in t['objs'])]
            for t in src:
                for k in range(4):
                    r = self._rotate(t, k)
                    if r:
                        r['src'] = id(t)
                        out.append(r)
            self.prep[name] = out
        return self.prep[name]

    def _rotate(self, t, k):
        """The template turned k quarter turns, every piece swapped for the same piece facing the new way.
        -> None when a piece does not come in the facing it would need."""
        W, H = t['w'], t['h']
        cells = [tuple(c) for c in t['cells']]
        doors = [tuple(d) for d in t['doors']]
        pieces = []
        for o in t['objs']:
            if o[4] < 0:                                   # a lone tile the catalog has no turns for
                if k and o[3]:
                    continue
                pieces.append((None, [(o[5], o[6], o[7])]))
                continue
            sheet, custom, group, f, i, ox, oy = o
            shape = _variants(sheet, custom, group).get(f, [])
            if i >= len(shape):
                continue
            pieces.append(((sheet, custom, group, f, i), [(ox + dx, oy + dy, tt) for dx, dy, tt in shape[i]]))
        w, h = W, H
        for _ in range(k):
            cells = [_rot_xy(x, y, w, h) for x, y in cells]
            doors = [_rot_xy(x, y, w, h) + (TURN[s], e) for x, y, s, e in doors]
            newp = []
            for meta, tiles in pieces:
                rc = [_rot_xy(x, y, w, h) for x, y, _ in tiles]
                if meta is None:
                    newp.append((None, [(rc[0][0], rc[0][1], tiles[0][2])]))
                    continue
                sheet, custom, group, f, i = meta
                nf = TURN.get(f, f)
                shape = _variants(sheet, custom, group).get(nf, [])
                if i >= len(shape):
                    return None
                sw = max(dx for dx, _, _ in shape[i]) + 1
                sh = max(dy for _, dy, _ in shape[i]) + 1
                x0, y0 = min(x for x, _ in rc), min(y for _, y in rc)
                if (max(x for x, _ in rc) - x0 + 1, max(y for _, y in rc) - y0 + 1) != (sw, sh):
                    return None
                newp.append(((sheet, custom, group, nf, i), [(x0 + dx, y0 + dy, tt) for dx, dy, tt in shape[i]]))
            pieces = newp
            w, h = h, w
        return dict(w=w, h=h, cells=frozenset(cells), doors=doors, pieces=pieces, floor=t['floor'], wall=t['wall'], k=k)

    @staticmethod
    def _cut_row(t, axis, n):
        """The plain row of a template that may be repeated along `axis` (the one nearest the middle), or None."""
        rows = collections.defaultdict(set)
        cells = collections.defaultdict(set)
        for c in t['cells']:
            cells[c[axis]].add(c[1 - axis])
        spans = set()
        for meta, tiles in t['pieces']:
            cs = {p[axis] for p in tiles}
            if len(cs) > 1:
                spans.update(range(min(cs), max(cs)))      # a piece across rows r..r+1 forbids cutting there
            for p in tiles:
                rows[p[axis]].add((p[1 - axis], p[2]))
        doors_at = {d[axis] for d in t['doors']}
        ok = [r for r in range(1, n - 1) if r not in spans and r - 1 not in spans and cells[r] == cells[r + 1]
              and rows[r] == rows[r + 1] and r not in doors_at]
        return min(ok, key=lambda v: abs(v - n / 2.0)) if ok else None

    @staticmethod
    def _stretch(t, axis, extra):
        """Make a template `extra` squares longer along axis 0 (x) or 1 (y) by repeating a plain row: one the
        same as the row after it, crossed by no piece (an aisle of shelves, a row of tables). -> t or None"""
        if extra <= 0:
            return t
        cut = t.get('_cut')                                # where it can be cut depends only on the template
        if cut is None:
            cut = t['_cut'] = {}
        if axis not in cut:
            cut[axis] = Fitter._cut_row(t, axis, t['w'] if axis == 0 else t['h'])
        r = cut[axis]
        if r is None:
            return None

        def move(c):
            if c[axis] > r:
                c = list(c)
                c[axis] += extra
                return tuple(c)
            return c
        newcells = set(move(c) for c in t['cells'])
        for k in range(1, extra + 1):
            newcells |= {(c[0] + k, c[1]) if axis == 0 else (c[0], c[1] + k) for c in t['cells'] if c[axis] == r}
        pieces = []
        for meta, tiles in t['pieces']:
            if all(p[axis] == r for p in tiles):
                for k in range(0, extra + 1):
                    pieces.append((meta, [((x + k, y, tt) if axis == 0 else (x, y + k, tt)) for x, y, tt in tiles]))
            else:
                pieces.append((meta, [move((x, y)) + (tt,) for x, y, tt in tiles]))
        doors = [move((x, y)) + (s_, e) for x, y, s_, e in t['doors']]
        out = dict(t)
        out.update(cells=frozenset(newcells), pieces=pieces, doors=doors, _cut={},
                   w=t['w'] + (extra if axis == 0 else 0), h=t['h'] + (extra if axis == 1 else 0))
        return out

    def fit(self, name, squares, doors, clear=(), windows=(), prefer=(), walls=None, edge=None):
        """squares: set of (x, y) of a room; doors: [(x, y, side, exterior)] the room's door squares and the
        side each door is on; windows: squares with a window on one of their sides.
        -> (placements [(x, y, tile)], floor tile, wall family 'sheet:base') or None"""
        self._windows = set(windows)
        self._walls = walls                              # the real room, when fitting into part of it
        self._edge = edge                                # edge(side, x, y): a real wall, door or window there
        cands = self._prepare(name)
        if prefer:                                       # e.g. an office lobby: the vanilla lobbies with a front desk
            want = [t for t in cands if any(p[0] and self.cat.get(p[1][0][2], {}).get('CustomName') in prefer
                                            for p in t['pieces'] if p[1])]
            cands = want or cands
        if not cands or not doors:
            self.stats['no template: ' + ('no vanilla room of the kind' if not cands else 'room without a door')] += 1
            return None
        xs = [x for x, _ in squares]
        ys = [y for _, y in squares]
        X0, Y0 = min(xs), min(ys)
        C = {(x - X0, y - Y0) for x, y in squares}
        CW, CH = max(xs) - X0 + 1, max(ys) - Y0 + 1
        D = [(x - X0, y - Y0, s, e) for x, y, s, e in doors]
        main = sorted(D, key=lambda d: -d[3])
        rect = len(C) == CW * CH                            # a plain rectangle: fitting is a bounds check
        # the search depends only on the room's shape, its doors and its kind: an identical room (the same
        # office on the next floor, the next home of a row) reuses it, and the pick below still varies
        key = (name, tuple(sorted(prefer)), frozenset(C), tuple(sorted(D)))
        if key in self._search:
            found = self._search[key]
            self.stats['room searches reused'] += 1
            if not found:
                self.stats['no template fits'] += 1
                return None
        else:
            best = None
            found = []
            order = list(range(len(cands)))
            self.rng.shuffle(order)
            tried = 0
            for ci in order:
                t = cands[ci]
                if t['w'] > CW or t['h'] > CH:
                    continue
                if len(t['cells']) < MIN_COVER * len(C):       # too small as it is: lengthen its aisles
                    for axis in (0, 1):
                        room = CW if axis == 0 else CH
                        have = t['w'] if axis == 0 else t['h']
                        t2 = self._stretch(t, axis, room - have)
                        if t2 is not None:
                            t = t2
                    if len(t['cells']) < MIN_COVER * len(C):
                        continue
                    self.stats['templates lengthened'] += 1
                tried += 1
                for do in main[:2]:
                    for dt in t['doors']:
                        if dt[2] != do[2]:
                            continue
                        off = (do[0] - dt[0], do[1] - dt[1])
                        if rect:
                            if off[0] < 0 or off[1] < 0 or off[0] + t['w'] > CW or off[1] + t['h'] > CH:
                                continue
                        elif not all((x + off[0], y + off[1]) in C for x, y in t['cells']):
                            continue
                        td = {(x + off[0], y + off[1], s) for x, y, s, e in t['doors']}
                        score = len(t['cells']) / float(len(C)) + (1.0 if dt[3] == do[3] else 0) + \
                            0.3 * sum(1 for d in D if (d[0], d[1], d[2]) in td)
                        found.append((score, t, off))
                        if best is None or score > best[0]:
                            best = (score, t, off)
                if tried >= 60 and best:
                    break
            if best is None:
                # second try: a template whose door is on the same wall as ours, slid along that wall
                do = main[0]
                for ci in order[:150]:
                    t = cands[ci]
                    if t['w'] > CW or t['h'] > CH:
                        continue
                    for axis in (0, 1):
                        if len(t['cells']) < MIN_COVER * len(C):
                            t2 = self._stretch(t, axis, (CW - t['w']) if axis == 0 else (CH - t['h']))
                            t = t2 if t2 is not None else t
                    if len(t['cells']) < MIN_COVER * len(C):
                        continue
                    for dt in t['doors']:
                        if dt[2] != do[2]:
                            continue
                        if do[2] in 'NS':                               # only positions with its door on our door's wall
                            spots = [(ox_, do[1] - dt[1]) for ox_ in range(0, CW - t['w'] + 1)]
                        else:
                            spots = [(do[0] - dt[0], oy_) for oy_ in range(0, CH - t['h'] + 1)]
                        for ox_, oy_ in spots:
                            if True:
                                line_ok = 0 <= oy_ <= CH - t['h'] and 0 <= ox_ <= CW - t['w']
                                if not line_ok or not (rect or all((x + ox_, y + oy_) in C for x, y in t['cells'])):
                                    continue
                                gap = abs(dt[0] + ox_ - do[0]) + abs(dt[1] + oy_ - do[1])
                                score = len(t['cells']) / float(len(C)) - 0.05 * gap
                                found.append((score, t, (ox_, oy_)))
                                if best is None or score > best[0]:
                                    best = (score, t, (ox_, oy_))
                    if best and best[0] > 0.8:
                        break
                if best is None:
                    self.stats['no template fits'] += 1
                    self._search[key] = []
                    return None
                self.stats['templates slid along the door wall'] += 1
            if len(self._search) < 50000:
                self._search[key] = found
        need = self._essentials(name)
        ranked = sorted(found, key=lambda f: -f[0])[:30]
        # a vanilla room used for a room of this kind just now goes last: neighbours are not copies
        ranked = [f for f in ranked if f[1].get('src') not in self.recent[name]] +                  [f for f in ranked if f[1].get('src') in self.recent[name]]
        seen = set()
        for score, t, off in ranked:
            if (id(t), off) in seen:
                continue
            seen.add((id(t), off))
            place = self._place(t, off, X0, Y0, C, D, clear)
            if len(place) < 2:                         # everything left out: that is no vanilla room
                self.stats['templates passed over: nothing left in them'] += 1
                continue
            names = {self.cat.get(tt, {}).get('CustomName', '') for _, _, tt in place}
            if all(any(e in n for n in names) for e in need):
                self.recent[name].append(t.get('src'))
                self.stats['rooms furnished from a vanilla room'] += 1
                return place, t['floor'], t['wall']
            self.stats['templates passed over: something essential would be missing'] += 1
        return None

    def _essentials(self, name):
        """What (nearly) every vanilla room of the kind has: a toilet in a bathroom, a bed in a bedroom."""
        if not hasattr(self, '_ess'):
            self._ess = {}
        if name not in self._ess:
            import furnish
            rs = furnish.room_stats(name)
            self._ess[name] = [c.split('|')[0] for c, a in (rs or {}).get('cats', {}).items()
                               if a['p'] >= 0.8 and not c.startswith('seat@')]
        return self._ess[name]

    def _place(self, t, off, X0, Y0, C, D, clear):
        keep_free = {(x - X0, y - Y0) for x, y in clear}
        for x, y, s, e in D:                               # the doorway and the square inside it stay clear
            dx, dy = DIRV[s]
            keep_free |= {(x, y), (x - dx, y - dy)}
        place = []
        R = {(x - X0, y - Y0) for x, y in self._walls} if self._walls else C     # where the walls really are
        for meta, tiles in t['pieces']:
            cells = [(x + off[0], y + off[1]) for x, y, _ in tiles]
            props = [self.cat.get(tt, {}) for _, _, tt in tiles]
            if any(c not in C for c in cells):
                continue
            if any('solid' in p or 'solidtrans' in p for p in props) and any(c in keep_free for c in cells):
                self.stats['pieces left out of doorways'] += 1
                continue
            if any('solid' in p and 'solidtrans' not in p and (X0 + cx, Y0 + cy) in self._windows
                   for (cx, cy), p in zip(cells, props)):
                self.stats['tall pieces kept out of windows'] += 1      # vanilla never stands shelves in a window
                continue
            # a piece vanilla never turns to the wall must not end up facing one here (a stretched room repeats
            # its rows, and with them any odd piece at the end of a row)
            own = set(cells)
            E = self._edge
            if any(p.get('CustomName') in face_in_kinds() and p.get('Facing') in DIRV and
                   (cx + DIRV[p['Facing']][0], cy + DIRV[p['Facing']][1]) not in own and
                   ((cx + DIRV[p['Facing']][0], cy + DIRV[p['Facing']][1]) not in R or
                    (E is not None and E(p['Facing'], X0 + cx, Y0 + cy)))
                   for (cx, cy), p in zip(cells, props)):
                self.stats['pieces left out: they would face a wall'] += 1
                continue
            # a piece vanilla stands against a wall (a back bar, a sink, a fridge) needs a wall behind it
            if any(p.get('CustomName') in back_wall_kinds() and p.get('Facing') in DIRV and
                   (cx - DIRV[p['Facing']][0], cy - DIRV[p['Facing']][1]) not in own and
                   ((cx - DIRV[p['Facing']][0], cy - DIRV[p['Facing']][1]) in R or
                    (E is not None and not E(OPPV[p['Facing']], X0 + cx, Y0 + cy)))
                   for (cx, cy), p in zip(cells, props)):
                self.stats['pieces left out: no wall behind them'] += 1
                continue
            hung = True
            for (cx, cy), p in zip(cells, props):
                for a, s in ATTACH.items():
                    if a in p:
                        dx, dy = DIRV[s]
                        if (cx + dx, cy + dy) in R or any((cx, cy, s) == (d[0], d[1], d[2]) for d in D) or \
                                (E is not None and not E(s, X0 + cx, Y0 + cy)):
                            hung = False
            if not hung:
                self.stats['wall pieces with no wall to hang on'] += 1
                continue
            place += [(X0 + x, Y0 + y, tt) for (x, y), (_, _, tt) in zip(cells, tiles)]
        return place


if __name__ == '__main__':
    d = harvest()
    for nm in ('conveniencestore', 'cornerstore', 'grocery', 'liquorstore', 'bookstore', 'restaurantdining', 'bar',
               'kitchen', 'bathroom', 'bedroom', 'office'):
        print(nm, len(d['rooms'].get(nm, [])), d['caps'].get(nm))
