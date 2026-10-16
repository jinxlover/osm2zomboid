"""How the vanilla map chooses walls, doors and windows.

  python vanillawalls.py        measure the game's own buildings -> cache/vanilla_walls.json

What it found, and what buildings.py does with it:

  * A wall tile belongs to the square that holds it, and it is drawn facing south or east. So the walls on
    the north and west sides of a room are always painted for that room, even when they are outside walls
    (57,000 of 57,000 checked); the building's outer material only shows on its south and east faces.
  * Wall sheets are laid out in families of eight: W, N, NW corner, SE pillar at base .. base+3, window
    frames at base+8 / +9, door frames at base+10 / +11, with bases at 0 and 4 in every block of 16.
  * Each room type has its own few wall families (bathrooms tiled, offices plain, kitchens their own), each
    building its outer family for the ground floor and one for the floors above, each kind of room its
    kinds of door. All of those are stored here as counts and drawn from by weight.
"""
import collections, glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cache', 'vanilla_walls.json')
RES = {'bedroom', 'livingroom', 'kitchen', 'bathroom', 'hall', 'closet', 'kidsbedroom', 'diningroom', 'garage',
       'laundry', 'basement', 'shed', 'empty', 'emptyoutside', 'farmstorage', 'garagestorage', 'storage', 'attic',
       'porch', 'kidsroom', 'office', 'hallway'}
GLASS = 'walls_commercial_01'           # shop-front glass: not a wall family of its own
_split = re.compile(r'^(.*)_(\d+)$')


def split(t):
    m = _split.match(t)
    return (m.group(1), int(m.group(2))) if m else (t, 0)


def base_of(j):
    m = j % 16
    if m < 8:
        return j - m % 4
    return j - m + (0 if m < 12 else 4)


def family(cat, sheet, base):
    """The eight tiles of a wall family, or None if the sheet does not follow the layout there."""
    need = (('WallW',), ('WallN',), ('WallNW',), ('WallSE',), ('WindowW',), ('WindowN',), ('DoorWallW',), ('DoorWallN',))
    out = []
    for off, keys in zip((0, 1, 2, 3, 8, 9, 10, 11), need):
        t = '%s_%d' % (sheet, base + off)
        p = cat.get(t)
        if p is None or not any(k in p for k in keys):
            return None
        out.append(t)
    return out


def harvest(vanilla_dir=None, out=OUT):
    areas = collections.defaultdict(list)            # room type -> squares of every vanilla room
    from lotreader import rd_header, rd_pack, iter_squares
    cat = catalog.load(os.path.join(HERE, 'cache', 'catalog.json'))
    vanilla_dir = vanilla_dir or os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
    C = collections.Counter
    fam_ok = {}

    def fam_key(t):
        sheet, j = split(t)
        b = base_of(j)
        k = (sheet, b)
        if k not in fam_ok:
            fam_ok[k] = family(cat, sheet, b) is not None
        return k if fam_ok[k] else None

    role = {}

    def roles(t):
        r = role.get(t)
        if r is None:
            p = cat.get(t, {})
            r = (any(k in p for k in ('WallN', 'WallNW', 'WindowN', 'DoorWallN')),
                 any(k in p for k in ('WallW', 'WallNW', 'WindowW', 'DoorWallW')),
                 'doorN' in p and 'DoubleDoor' not in p and 'GarageDoor' not in p,
                 'doorW' in p and 'DoubleDoor' not in p and 'GarageDoor' not in p,
                 'windowN' in p and 'WindowN' not in p, 'windowW' in p and 'WindowW' not in p,
                 'HoppableN' in p or 'HoppableW' in p)
            role[t] = r
        return r

    interior = collections.defaultdict(C)
    door_int = collections.defaultdict(C)
    door_ext = collections.defaultdict(C)
    window = {'com': C(), 'res': C()}
    exterior = {'com': C(), 'res': C(), 'com:3-5': C(), 'com:6+': C()}
    rim = collections.defaultdict(C)
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
        room_of, bld_of, kind_of = {}, {}, {}
        for bi, rl in enumerate(h['blds']):
            kind_of[bi] = 'res' if all(rooms[ri][0] in RES for ri in rl) else 'com'
            for ri in rl:
                bld_of[ri] = bi
        for ri, (name, lvl, rects, _) in enumerate(rooms):
            n_sq = 0
            for (x, y, w, hh) in rects:
                for xx in range(x, x + w):
                    for yy in range(y, y + hh):
                        room_of[(xx, yy, lvl)] = ri
                        n_sq += 1
            if all(0 <= x and x + w <= 256 and 0 <= y and y + hh <= 256 for (x, y, w, hh) in rects):
                areas[name].append(n_sq)
        ext_g = collections.defaultdict(C)
        ext_u = collections.defaultdict(C)
        tops = collections.defaultdict(int)
        for ri, bi in bld_of.items():
            tops[bi] = max(tops[bi], rooms[ri][1])
        rims = collections.defaultdict(C)
        for (x, y, z), tl in sq.items():
            here = room_of.get((x, y, z))
            for t in tl:
                isn, isw, dn, dw, gn, gw, low = roles(t)
                for flag, door, glass, nb in ((isn, dn, gn, (x, y - 1, z)), (isw, dw, gw, (x - 1, y, z))):
                    other = room_of.get(nb)
                    if not (flag or door or glass) or (here is None and other is None):
                        continue
                    if flag and not low:
                        k = fam_key(t)
                        if k and k[0] != GLASS:
                            if here is not None:
                                interior[rooms[here][0]][k] += 1
                            elif other in bld_of:
                                (ext_g if z == 0 else ext_u)[bld_of[other]][k] += 1
                    if door:
                        sheet, j = split(t)
                        tw = t if nb == (x - 1, y, z) else '%s_%d' % (sheet, j - 1)      # store the west-facing twin
                        if here is not None and other is not None:
                            door_int[rooms[here][0]][tw] += 1
                            door_int[rooms[other][0]][tw] += 1
                        else:
                            ri = here if here is not None else other
                            door_ext[rooms[ri][0]][tw] += 1
                    if glass and (here is None) != (other is None):
                        ri = here if here is not None else other
                        sheet, j = split(t)
                        tw = t if nb == (x - 1, y, z) else '%s_%d' % (sheet, j - 1)
                        if ri in bld_of:
                            window[kind_of[bld_of[ri]]][tw] += 1
                if low and here is None:
                    under = room_of.get((x, y, z - 1))
                    if under in bld_of and tops[bld_of[under]] == z - 1:
                        sheet, j = split(t)
                        rims[bld_of[under]][(sheet, j - j % 4)] += 1
        for bi in set(ext_g) | set(ext_u):
            g = ext_g[bi].most_common(1)
            u = ext_u[bi].most_common(1)
            if not g and not u:
                continue
            g = g[0][0] if g else u[0][0]
            u = u[0][0] if u else g
            exterior[kind_of[bi]][(g, u)] += 1
            if kind_of[bi] == 'com' and tops[bi] >= 2:             # (tops: top level, 0-based)
                exterior['com:3-5' if tops[bi] <= 4 else 'com:6+'][(g, u)] += 1
            if rims[bi]:
                rim['%s:%d' % u][rims[bi].most_common(1)[0][0]] += 1

    def valid_door(tw):
        sheet, j = split(tw)
        return 'doorW' in cat.get(tw, {}) and 'doorN' in cat.get('%s_%d' % (sheet, j + 1), {})

    def valid_window(tw):
        sheet, j = split(tw)
        return 'windowW' in cat.get(tw, {}) and 'windowN' in cat.get('%s_%d' % (sheet, j + 1), {})

    def valid_rim(k):
        sheet, b = k
        return all(key in cat.get('%s_%d' % (sheet, b + off), {}) for off, key in ((0, 'WallW'), (1, 'WallN'), (2, 'WallNW'), (3, 'WallSE')))

    every = C()
    for c in interior.values():
        every.update(c)
    data = dict(
        interior={nm: [[k[0], k[1], v] for k, v in c.most_common(6)] for nm, c in interior.items() if sum(c.values()) >= 40},
        interior_any=[[k[0], k[1], v] for k, v in every.most_common(12)],
        exterior={kind: [[g[0], g[1], u[0], u[1], v] for (g, u), v in c.most_common(60)] for kind, c in exterior.items()},
        rim={k: [[r[0], r[1], v] for r, v in c.most_common(3) if valid_rim(r)] for k, c in rim.items()},
        door_int={nm: [[t, v] for t, v in c.most_common(5) if valid_door(t)] for nm, c in door_int.items() if sum(c.values()) >= 20},
        door_ext={nm: [[t, v] for t, v in c.most_common(5) if valid_door(t)] for nm, c in door_ext.items() if sum(c.values()) >= 10},
        window={kind: [[t, v] for t, v in c.most_common(8) if valid_window(t)] for kind, c in window.items()},
        # room sizes: median and 90th percentile squares, per room type
        sizes={nm: [sorted(v)[len(v) // 2], sorted(v)[int(0.9 * (len(v) - 1))]] for nm, v in areas.items() if len(v) >= 15},
    )
    data['rim'] = {k: v for k, v in data['rim'].items() if v}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(data, open(out, 'w'), separators=(',', ':'))
    print('wall families for %d room types, %d outer wall pairs (commercial), doors for %d room types'
          % (len(data['interior']), len(data['exterior']['com']), len(data['door_int'])))
    return data


_data = None


def load():
    global _data
    if _data is None:
        if not os.path.isfile(OUT):
            harvest()
        _data = json.load(open(OUT))
    return _data


if __name__ == '__main__':
    d = harvest()
    for nm in ('office', 'bathroom', 'kitchen', 'bar', 'restaurantdining', 'storage', 'hall', 'lobby', 'bedroom', 'livingroom', 'motelroom'):
        print(' ', nm, d['interior'].get(nm, [])[:4], '| doors', d['door_int'].get(nm, [])[:3], '| street doors', d['door_ext'].get(nm, [])[:3])
    print('  outer walls, commercial:', d['exterior']['com'][:10])
    print('  windows:', d['window'])
    print('  roof rims:', list(d['rim'].items())[:6])


# ---------------------------------------------------------------------------------------------- using it
OUTER_SHEETS = {'com': ('walls_exterior_house_0', 'walls_commercial_0'),
                'com:3-5': ('walls_exterior_house_0', 'walls_commercial_0'),
                'com:6+': ('walls_exterior_house_0', 'walls_commercial_0', 'walls_interior_house_03'),
                'res': ('walls_exterior_house_0', 'walls_exterior_wooden_0')}
DEFAULT_RIM = ('walls_exterior_house_low_01', 0)


class Palette:
    """The measured choices, ready to draw from: wall styles are tilepalette.WallStyle objects."""

    def __init__(self):
        from tilepalette import WallStyle
        d = load()
        cat = catalog.load(os.path.join(HERE, 'cache', 'catalog.json'))
        memo = {}

        def style(sheet, base):
            k = (sheet, base)
            if k not in memo:
                f = family(cat, sheet, base)
                memo[k] = WallStyle(*f) if f else None
            return memo[k]

        def rim(sheet, base):
            return tuple('%s_%d' % (sheet, base + i) for i in range(4))

        def pairs(rows, prefix):
            out = []
            for t, n in rows:
                if t.startswith(prefix):
                    sheet, j = split(t)
                    out.append(((t, '%s_%d' % (sheet, j + 1)), n))
            return out

        self.interior = {}
        for nm, rows in d['interior'].items():
            lst = [(style(s, b), n) for s, b, n in rows if s.startswith('walls_') and style(s, b)]
            if lst:
                self.interior[nm] = lst
        self.interior_any = [(style(s, b), n) for s, b, n in d['interior_any'] if s.startswith('walls_interior') and style(s, b)]
        self.exterior = {}
        for kind, rows in d['exterior'].items():
            lst = []
            for gs, gb, us, ub, n in rows:
                if gs.startswith(OUTER_SHEETS[kind]) and us.startswith(OUTER_SHEETS[kind]) and style(gs, gb) and style(us, ub):
                    r = [x for x in d['rim'].get('%s:%d' % (us, ub), ()) if x[0] == DEFAULT_RIM[0]]
                    lst.append((style(gs, gb), style(us, ub), rim(r[0][0], r[0][1]) if r else rim(*DEFAULT_RIM), n))
            self.exterior[kind] = lst
        self.door_int = {nm: pairs(rows, 'fixtures_doors_0') for nm, rows in d['door_int'].items()}
        self.door_ext = {nm: pairs(rows, 'fixtures_doors_0') for nm, rows in d['door_ext'].items()}
        self.window = {kind: pairs(rows, 'fixtures_windows_0') for kind, rows in d['window'].items()}

    @staticmethod
    def pick(rng, rows, default=None):
        rows = [r for r in rows or () if r]
        if not rows:
            return default
        return rng.choices([r[:-1] if len(r) > 2 else r[0] for r in rows], weights=[r[-1] for r in rows])[0]


def style_key(key, _memo={}):
    """A WallStyle for a family key 'sheet:base' (as interiors.py records a room's paint), or None."""
    if key not in _memo:
        from tilepalette import WallStyle
        _memo[key] = None
        if key and ':' in key:
            sheet, base = key.rsplit(':', 1)
            if sheet.startswith('walls_'):
                f = family(catalog.load(os.path.join(HERE, 'cache', 'catalog.json')), sheet, int(base))
                _memo[key] = WallStyle(*f) if f else None
    return _memo[key]


class Look:
    """What one building looks like: its outer walls, and per kind of room the inner walls and doors."""

    def __init__(self, palette, rng, kind='com', levels=1):
        """kind 'res' or 'com'; a commercial building of 3-5 or 6+ floors takes the outer walls vanilla gives
        buildings of that height (panels and stucco on its towers, not the clapboard of a corner shop)."""
        self.p, self.rng = palette, rng
        band = kind if kind != 'com' or levels < 3 else ('com:3-5' if levels <= 5 else 'com:6+')
        self.ext_g, self.ext_u, self.rim = palette.pick(rng, palette.exterior.get(band) or palette.exterior.get(kind)
                                                        or palette.exterior['com'])
        self.window = palette.pick(rng, palette.window.get(kind) or palette.window['com'],
                                   ('fixtures_windows_01_0', 'fixtures_windows_01_1'))
        self.kind = kind
        self._int, self._door, self._sdoor = {}, {}, {}

    def interior(self, name):
        if name not in self._int:
            self._int[name] = self.p.pick(self.rng, self.p.interior.get(name) or self.p.interior_any)
        return self._int[name]

    def door(self, name):
        if name not in self._door:
            self._door[name] = self.p.pick(self.rng, self.p.door_int.get(name) or self.p.door_int.get('office'),
                                           ('fixtures_doors_01_0', 'fixtures_doors_01_1'))
        return self._door[name]

    def street_door(self, name):
        if name not in self._sdoor:
            self._sdoor[name] = self.p.pick(self.rng, self.p.door_ext.get(name) or self.p.door_ext.get('hall'),
                                            ('fixtures_doors_01_0', 'fixtures_doors_01_1'))
        return self._sdoor[name]
