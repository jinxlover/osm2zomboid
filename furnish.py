"""Furnish rooms the way the vanilla map does.

There are no hand-written furniture lists here. vanillastats.py measures, on the game's own map, which
furniture each room type contains (how often, how many per square, which styles) and how every model is
placed (against a wall, on top of a counter, beside a table ...). This module turns those measurements
into furniture for a new room:

  1. recipe      for each category the room type uses: roll for presence (likelier in rooms bigger
                 than usual), scale the count with the room's area, pick a style (one style per
                 category per room, as in vanilla)
  2. placement   by the model's measured behaviour:
       wall      back against a wall; scored so pieces gather in corners and line up in runs
                 (a kitchen line of fridge + counters + oven comes out of the scoring, not a rule)
       table     free-standing in the open floor with a walking ring; a long row of single counters
                 becomes an island one square off a wall (a bar with its service aisle)
       aisle     free-standing shelves / racks in parallel rows; rows: seats all facing one way
       seat      never placed alone: chairs go around tables, one in front of a desk, stools along
                 the front of a bar
       high      wall-mounted, above the low piece it usually hangs over (medicine cabinet over the
                 sink, hood over the oven) or on a free stretch of wall
       ontop     on a table / counter / desk of the kind it usually sits on (sink, register, computer)
       corner    small things that stand next to walls (plants, bins); free: anywhere in the open
  3. rules that always hold: door fronts and stairs stay clear, the square in front of every wall
     piece stays walkable, nothing may cut the walkable floor in two, at most ~45% of a room is
     blocked, nothing is backed against shop-window glass, and every room gets a light switch.
"""
import math
import json
import os
import random

import catalog
import interiors
from vanillawalls import style_key
import tilepalette as T

HERE = os.path.dirname(os.path.abspath(__file__))
OPP = {'S': 'N', 'N': 'S', 'E': 'W', 'W': 'E'}
DIRS = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
SWITCH = {'N': 'lighting_indoor_01_0', 'W': 'lighting_indoor_01_1', 'E': 'lighting_indoor_01_2',
          'S': 'lighting_indoor_01_3'}
ORDER = {'wall': 0, 'table': 1, 'aisle': 2, 'rows': 2, 'corner': 3, 'free': 3, 'ontop': 4, 'high': 5}
# room names the generator uses that the vanilla map spells differently
# how vanilla furnishes the big version of a room type (stock rooms of a store, not a closet with a table)
BIG_ROOM = {'storage': 'miscstorage', 'storageunit': 'miscstorage', 'barstorage': 'grocerystorage',
            'officestorage': 'miscstorage', 'bookstorestorage': 'miscstorage', 'sportstorage': 'miscstorage',
            'jewelrystorage': 'miscstorage', 'electronicsstorage': 'miscstorage', 'furniturestorage': 'miscstorage',
            'shoestorage': 'miscstorage', 'theatrestorage': 'miscstorage', 'cornerstorestorage': 'grocerystorage'}
ALIAS = {'restaurant': 'restaurantdining', 'clothingstore': 'clothesstore', 'electronicstore': 'electronicsstore',
         'dining': 'diningroom'}
MIN_ROOMS = 6            # room types rarer than this on the vanilla map are not learned from
MIN_SHARE = 0.08         # what fewer than 1 in 12 vanilla rooms of a type hold is an oddity (a bed in an office)

_stats = None
_pieces = {}


def stats():
    """The vanilla measurements (built on first use; needs the game installed)."""
    global _stats
    if _stats is None:
        path = os.path.join(HERE, 'cache', 'vanilla_rooms.json')
        if not os.path.isfile(path):
            import vanillastats
            print('measuring how the vanilla map furnishes rooms (once, about two minutes) ...', flush=True)
            vanillastats.main(out=path)
        _stats = json.load(open(path, encoding='utf8'))
    return _stats


class Piece:
    """One physical piece of furniture: a model variant with its tiles for each facing."""
    __slots__ = ('key', 'm', 'shapes', 'custom', 'beh', 'cat', 'low', 'table', 'size')

    def __init__(self, key, m, shapes):
        self.key, self.m, self.shapes = key, m, shapes
        self.custom = key.split('|')[1]
        self.beh, self.cat = m['beh'], m['cat']
        self.low = 'L' in m['flags']
        self.table = 'T' in m['flags']
        self.size = max(len(s) for s in shapes.values())

    def dims(self, facing):
        s = self.shapes[facing]
        return max(t[0] for t in s) + 1, max(t[1] for t in s) + 1


def pieces(key):
    """Usable variants of a model ([] when its tiles do not form clean rectangles in every facing)."""
    if key not in _pieces:
        m = stats()['models'].get(key)
        out = []
        if m:
            sheet, custom, group = key.split('|')
            v = catalog.variants(sheet, custom, group or None)
            for i in range(min(len(vs) for vs in v.values()) if v else 0):
                shapes = {f: vs[i] for f, vs in v.items()}
                if all(len(s) == (max(t[0] for t in s) + 1) * (max(t[1] for t in s) + 1) for s in shapes.values()):
                    out.append(Piece(key, m, shapes))
        _pieces[key] = out
    return _pieces[key]


def room_stats(name):
    rooms = stats()['rooms']
    name = ALIAS.get(name, name)
    r = rooms.get(name)
    if r and r['n'] >= MIN_ROOMS:
        return r
    for suffix, fallback in (('kitchen', 'restaurantkitchen'), ('storage', 'storage'), ('dining', 'restaurantdining'),
                             ('store', 'giftstore'), ('office', 'office'), ('classroom', 'classroom'),
                             ('restaurant', 'restaurantdining'), ('shop', 'giftstore'),
                             ('bedroom', 'bedroom')):
        if name.endswith(suffix) and name != fallback and rooms.get(fallback):
            return rooms[fallback]
    return r


class Item:
    __slots__ = ('piece', 'facing', 'cells', 'beh', 'top', 'seated')

    def __init__(self, piece, facing, cells, beh):
        self.piece, self.facing, self.cells, self.beh = piece, facing, cells, beh
        self.top = None          # what stands on it (names), once something does
        self.seated = False


class Room:
    """Working state while one room is furnished."""

    def __init__(self, f, name, squares, z, clear, doors):
        self.f, self.name, self.z = f, name, z
        self.squares = squares
        self.clear = clear & squares      # no furniture at all: door fronts, stairs
        self.doors = doors & squares
        self.block = set()                # squares a walker cannot enter
        self.taken = set()                # squares holding any floor-standing piece (chairs included)
        self.front = set()                # must stay unblocked: access to wall pieces, service aisles
        self.at = {}                      # square -> Item
        self.items = []
        self.high = set()                 # (x, y, side) wall spots already carrying something
        self.perim = [(x, y, s) for (x, y) in sorted(squares) for s in 'NSWE' if f.backable(s, x, y, z)]
        xs = [x for x, _ in squares]
        ys = [y for _, y in squares]
        self.x0, self.x1, self.y0, self.y1 = min(xs), max(xs), min(ys), max(ys)
        self.lost = len(squares) - self._reach(set())

    def _reach(self, extra):
        walk = self.squares - self.block - extra
        if not walk:
            return 0
        start = next(iter(self.doors & walk), None) or next(iter(walk))
        seen = {start}
        stack = [start]
        while stack:
            x, y = stack.pop()
            for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nb in walk and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return len(seen)

    def keeps_floor_whole(self, cells):
        """Would blocking `cells` leave every walkable square reachable?"""
        cells = set(cells)
        walk = len(self.squares - self.block - cells)
        return walk > 0 and walk - self._reach(cells) <= self.lost

    def can_block(self, sq):
        return sq in self.squares and sq not in self.taken and sq not in self.clear and sq not in self.front

    def can_seat(self, sq):
        return sq in self.squares and sq not in self.taken and sq not in self.clear

    def open_floor(self, sq):
        return sq in self.squares and sq not in self.block


class Furnisher:
    def __init__(self, bg, seed=1):
        self.fitter = interiors.Fitter(seed)
        self.bg = bg
        self.rng = random.Random(seed * 7919 + 13)
        self.count = {}
        stats()

    # ------------------------------------------------------------------ walls of the building
    def _edge(self, side, x, y, z):
        bg = self.bg
        if side == 'N':
            return bg.hw.get((x, y, z))
        if side == 'S':
            return bg.hw.get((x, y + 1, z))
        if side == 'W':
            return bg.vw.get((x, y, z))
        return bg.vw.get((x + 1, y, z))

    def backable(self, side, x, y, z):
        """A wall or window (not a door, not shop-window glass) that furniture can stand against."""
        e = self._edge(side, x, y, z)
        return e is not None and e.kind in ('wall', 'window') and e.style is not T.STOREFRONT

    def window(self, side, x, y, z):
        """A window, or shop-front glass (the game counts that glass as windows too)."""
        e = self._edge(side, x, y, z)
        return e is not None and (e.kind == 'window' or (e.kind == 'wall' and e.style is T.STOREFRONT))

    def door(self, side, x, y, z):
        e = self._edge(side, x, y, z)
        return e is not None and e.kind == 'door'

    def edge(self, side, x, y, z):
        return self._edge(side, x, y, z) is not None

    def floor_for(self, name, rng=None):
        """A floor tile the vanilla map uses for this room type (None when it is not known)."""
        r = room_stats(name)
        if not r or not r['floors']:
            return None
        tiles = [t for t in r['floors'] if t.startswith('floors_interior')] or list(r['floors'])
        w = [r['floors'][t] for t in tiles]
        return (rng or self.rng).choices(tiles, weights=w)[0]

    # ------------------------------------------------------------------ entry points
    def furnish_building(self, ox, oy, labs, names, levels, stairs, public=()):
        """public: levels whose bathrooms are customer / staff toilets (no bath tub, no shower)."""
        for z in range(levels):
            self.public = z in public
            lab = labs[z]
            ys_, xs_ = (lab > 0).nonzero()
            self.inside = {(ox + int(x), oy + int(y)) for y, x in zip(ys_, xs_)}
            n = int(lab.max())
            blocked = set(getattr(self, 'keep_clear', ()))     # e.g. in front of the elevator doors
            if stairs:
                orient, sx, sy, _ = stairs
                for k in range(-1, 4):
                    blocked.add((ox + sx, oy + sy + k) if orient == 'N' else (ox + sx + k, oy + sy))
                for (bx, by) in list(blocked):            # keep the squares beside the stairs open too
                    blocked.update(((bx + 1, by), (bx - 1, by), (bx, by + 1), (bx, by - 1)))
            for r in range(1, n + 1):
                nm = names[z].get(r)
                if not nm:
                    continue
                ys, xs = (lab == r).nonzero()
                squares = {(ox + int(x), oy + int(y)) for y, x in zip(ys, xs)}
                if len(squares) >= 2:
                    self.furnish_room(nm, squares, z, blocked)

    NO_TEMPLATE = {'empty', 'elevator', 'stairwell'}
    # a tower's lobby is a front desk and seats, not an apartment block's hall of drawers and wardrobes
    FRONT_DESK_USES = {'office', 'hotel', 'civic', 'bank', 'post', 'studio', 'library'}
    LOBBY_FRONT = {'Desk', 'Counter', 'Couch', 'Seat', 'Seating', 'Register', 'Reception'}
    # The share of a shop's wall squares (against a solid wall, not beside a door) that vanilla lines with
    # containers: the median over the shops of Louisville, Muldraugh, West Point, Riverside and Rosewood.
    WALL_STOCK = {'liquorstore': 0.48, 'gasstore': 0.36, 'toolstore': 0.41, 'clothesstore': 0.2, 'grocery': 0.38,
                  'cafe': 0.13, 'conveniencestore': 0.36, 'movierental': 0.41, 'bakery': 0.2, 'giftstore': 0.24,
                  'bookstore': 0.47, 'pawnshop': 0.32, 'jewelrystore': 0.03, 'shoestore': 0.23, 'furniturestore': 0.12,
                  'musicstore': 0.25, 'aesthetic': 0.26, 'gunstore': 0.35, 'generalstore': 0.52, 'cornerstore': 0.55,
                  'electronicsstore': 0.25, 'artstore': 0.4, 'toystore': 0.17, 'pharmacy': 0.4, 'comicstore': 0.62,
                  'candystore': 0.45, 'sportstore': 0.3, 'camerastore': 0.3}

    def _open_beyond(self, squares, z):
        """Squares just outside the room with no wall between (a kitchen open to the living room): for the
        fitter these are floor, not wall, so nothing stands with its back to the open side."""
        out = set()
        for (x, y) in squares:
            for side, (dx, dy) in DIRS.items():
                nb = (x + dx, y + dy)
                if nb not in squares and not self.edge(side, x, y, z):
                    out.add(nb)
        return out

    def _pieces_in(self, squares, z):
        cat = self.fitter.cat
        return sum(1 for (x, y) in squares for t in self.bg.fixtures.get((x, y, z), ())
                   if interiors._is_object(t, cat.get(t, {})) and not t.startswith('lighting_indoor'))

    @staticmethod
    def _largest_rect(free):
        """Largest axis-aligned rectangle of squares in `free` -> set of squares."""
        if not free:
            return set()
        xs = [x for x, _ in free]
        ys = [y for _, y in free]
        X0, Y0, W, H = min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        heights = [0] * W
        best = (0, None)
        for j in range(H):
            for i in range(W):
                heights[i] = heights[i] + 1 if (X0 + i, Y0 + j) in free else 0
            stack = []
            for i in range(W + 1):
                h = heights[i] if i < W else 0
                start = i
                while stack and stack[-1][1] >= h:
                    st, sh = stack.pop()
                    if sh * (i - st) > best[0]:
                        best = (sh * (i - st), (st, j - sh + 1, i - st, sh))
                    start = st
                stack.append((start, h))
        if not best[1]:
            return set()
        i0, j0, w, h = best[1]
        return {(X0 + i0 + a, Y0 + j0 + b) for a in range(w) for b in range(h)}

    def _top_up(self, room, name, squares, z):
        """A room much bigger than the vanilla room it was furnished from is left half bare; vanilla fills a
        big room with more of the same. While it holds clearly less than vanilla's rooms of its kind per square,
        fit another vanilla room of the kind into the biggest free rectangle, entered from the floor beside it,
        keeping every square of the room reachable."""
        target = interiors.density().get(name)
        if not target or len(squares) < 30 or name.endswith('storage'):
            return                                       # store rooms come out as full as vanilla's already
        cat = self.fitter.cat
        for _ in range(4):
            if self._pieces_in(squares, z) >= 0.85 * target * len(squares):
                return
            free = {s_ for s_ in squares if s_ not in room.block and s_ not in room.clear and s_ not in room.front}
            region = self._largest_rect(free)
            if len(region) < 12:
                return
            doors = []
            for (x, y) in region:
                for side, (dx, dy) in DIRS.items():
                    nb = (x + dx, y + dy)
                    if nb in squares and nb not in region and room.open_floor(nb) and not self.edge(side, x, y, z):
                        doors.append((x, y, side, 0))
            if not doors:
                return
            self.rng.shuffle(doors)
            r = self.fitter.fit(name, region, doors[:4], clear=set(), walls=squares | self._open_beyond(squares, z))
            if r is None:
                return
            place = [(x, y, t) for x, y, t in r[0] if (x, y) not in room.block]
            solid = {(x, y) for x, y, t in place if 'solid' in cat.get(t, {}) or 'solidtrans' in cat.get(t, {})}
            if not place or not room.keeps_floor_whole(solid):
                return
            for x, y, t in place:
                self.bg.fixtures[(x, y, z)].append(t)
                room.block.add((x, y))
                room.taken.add((x, y))
            self.fitter.stats['rooms topped up with another vanilla room'] += 1

    def _line_walls(self, room, name):
        """A shop bigger than the vanilla room it was furnished from keeps bare walls; stock them as vanilla
        does that kind of shop: its wall containers (shelves, racks, freezers), until as much of the wall is
        lined as in vanilla's shops of the kind, and no more containers than vanilla's densest rooms of it."""
        target = self.WALL_STOCK.get(name)
        rs = room_stats(name)
        if not target or not rs:
            return
        z, cat = room.z, self.fitter.cat
        has_box = lambda sq: any('container' in cat.get(t, {}) for t in self.bg.fixtures.get((sq[0], sq[1], z), ()))
        wall_sq = set()
        for (x, y) in room.squares:
            if any(self.door(s, x, y, z) for s in 'NSWE'):
                continue
            if any(self.backable(s, x, y, z) and not self.window(s, x, y, z) for s in 'NSWE'):
                wall_sq.add((x, y))
        if len(wall_sq) < 6:
            return
        caps = interiors.load()['caps'].get(ALIAS.get(name, name)) or {}
        boxes = sum(1 for c in room.squares if has_box(c))
        cap = max(boxes, int(round(caps.get('cpa', 0.3) * len(room.squares))))
        lined = sum(1 for c in wall_sq if has_box(c))
        if lined >= target * len(wall_sq) or boxes >= cap:
            return
        pool = []
        for key_cat, a in rs['cats'].items():
            parts = key_cat.split('|')
            if len(parts) < 2 or not parts[1] or any(w in parts[0] for w in ('Register', 'Counter')):
                continue
            for k, c in a['styles'].items():
                ps = pieces(k)
                if ps and ps[0].beh == 'wall' and not self._endcap(k, a['styles']):
                    pool.append((ps, a['p'] * c))
        if not pool:
            return
        rng = self.rng
        kinds = []
        for _ in range(min(2, len(pool))):                # one or two kinds of shelving, as a shop has
            ps = rng.choices([p for p, _ in pool], weights=[w for _, w in pool])[0]
            if ps not in kinds:
                kinds.append(ps)
        styles = [rng.choice(ps) for ps in kinds]
        room.taken |= room.block                          # what the vanilla room put down stays
        keep = room.perim
        room.perim = [p for p in keep if (p[0], p[1]) in wall_sq and not self.window(p[2], p[0], p[1], z)]
        i = 0
        fails = 0
        while lined < target * len(wall_sq) and boxes < cap and fails < len(styles):
            piece = styles[i % len(styles)]
            i += 1
            n0 = len(room.items)
            if not self._wall(room, [piece], 1):
                fails += 1
                continue
            fails = 0
            for c in room.items[n0].cells:
                if has_box(c):
                    boxes += 1
                    lined += c in wall_sq
            self.fitter.stats['shop wall pieces added'] += 1
        room.perim = keep


    def _within_vanilla(self, name, area, plan):
        """Hold a rolled room to what vanilla puts in a room of its kind: no more containers or lamps than the
        90th percentile of vanilla rooms of that kind (a big room a little more), which is also what keeps
        the loot the game rolls as a cell loads within vanilla's."""
        caps = interiors.load()['caps'].get(ALIAS.get(name, name))
        if not caps:
            return plan
        rs = room_stats(name)
        grow = min(2.0, max(1.0, area / float(max(1.0, rs['area'] if rs else area)) ** 0.5))
        box = max(1, int(round(caps['c90'] * grow)))
        lit = max(0, int(round(caps['l90'] * grow)))
        out = []
        for cat, n, styles, a in plan:
            parts = cat.split('|')
            if len(parts) > 1 and parts[1]:              # a container
                n = min(n, box)
                box -= n
            if any(w in parts[0] for w in ('Lamp', 'Light', 'Chandelier')):
                n = min(n, lit)
                lit -= n
            if n > 0:
                out.append((cat, n, styles, a))
        return out

    def _from_template(self, room, name, squares, z, blocked):
        """Furnish the room as a whole vanilla room of its kind (interiors.py): its pieces where they stood,
        turned to our door, its floor and the paint of its walls. -> False when no vanilla room fits."""
        if name in self.NO_TEMPLATE or len(squares) < 2:
            return False
        doors = []
        for (x, y) in squares:
            for side in 'NSWE':
                if self.door(side, x, y, z):
                    dx, dy = DIRS[side]
                    doors.append((x, y, side, 0 if (x + dx, y + dy) in getattr(self, 'inside', ()) else 1))
        wins = {(x, y) for (x, y) in squares if any(self.window(s, x, y, z) for s in 'NSWE')}
        prefer = self.LOBBY_FRONT if name == 'lobby' and getattr(self.bg, 'cur_use', None) in self.FRONT_DESK_USES else ()
        r = self.fitter.fit(name, squares, doors, clear=set(blocked) & squares, windows=wins, prefer=prefer,
                            walls=squares | self._open_beyond(squares, z))
        if r is None:
            return False
        place, floor, wall = r
        for x, y, t in place:
            self.bg.fixtures[(x, y, z)].append(t)
            room.block.add((x, y))
        if floor:
            for (x, y) in squares:
                cur = self.bg.md.squares.get((x, y, z))
                if cur and not any(c.startswith('fixtures_stairs') for c in cur):
                    cur[0] = floor
        st = style_key(wall)
        if st is not None:
            import tilepalette as T_
            for (x, y) in squares:                      # the room's own walls: those on its squares' N and W edges
                for d in (self.bg.hw, self.bg.vw):
                    e = d.get((x, y, z))
                    if e is not None and e.kind != 'parapet' and e.style is not T_.STOREFRONT:
                        e.style = st
        return True

    def furnish_room(self, name, squares, z, blocked=()):
        rng = self.rng
        area = len(squares)
        doors = set()
        clear = set(blocked)
        reach = 0 if area < 12 else (1 if area < 30 else 2)
        for (x, y) in squares:
            for side in 'NSWE':
                if self.door(side, x, y, z):
                    doors.add((x, y))
                    clear.add((x, y))
                    dx, dy = DIRS[OPP[side]]                  # into the room
                    for k in range(1, reach + 1):
                        clear.add((x + dx * k, y + dy * k))
                    if reach == 2:
                        px, py = (1, 0) if dx == 0 else (0, 1)
                        clear.update(((x + px, y + py), (x - px, y - py)))
        room = Room(self, name, squares, z, clear, doors)
        if self._from_template(room, name, squares, z, blocked):
            self._top_up(room, name, squares, z)
            self._line_walls(room, name)
            self._switch(room)
            return
        rs = room_stats(name)
        if rs and area > 3 * rs['area'] and name in BIG_ROOM:
            # a stock room ten times the vanilla size is a stock room, not ten little ones with a table each
            rs = room_stats(BIG_ROOM[name]) or rs
        if rs and area >= 4:
            plan, seats = self._recipe(rs, area)
            plan = self._within_vanilla(name, area, plan)
            limit = 0.45 * area
            for cat, n, styles, a in plan:
                beh = styles[0].beh
                if len(room.block) >= limit and ORDER[beh] <= 3:
                    continue
                if beh == 'wall':
                    self._wall(room, styles, n)
                    # desks standing against a wall get their chair
                    for it in room.items:
                        if it.beh == 'wall' and it.piece.table and 'desk' in it.piece.custom.lower() and not it.seated:
                            self._seat_front(room, it, seats)
                            it.seated = True
                elif beh == 'table':
                    self._tables(room, styles, n, seats, a)
                elif beh in ('aisle', 'rows'):
                    self._rows(room, styles, n, 2 if beh == 'aisle' else 1)
                elif beh in ('corner', 'free'):
                    self._loose(room, styles, n, beh == 'corner')
                elif beh == 'ontop':
                    self._ontop(room, styles, n)
                elif beh == 'high':
                    self._high(room, styles, n)
        self._line_walls(room, name)
        self._switch(room)

    # ------------------------------------------------------------------ recipe
    def _recipe(self, rs, area):
        """-> ([(category, count, [Piece...], category stats)] in placement order, seat styles by host)."""
        rng = self.rng
        plan = []
        seats = {}
        for cat, a in rs['cats'].items():
            usable = [(k, c) for k, c in a['styles'].items() if pieces(k) and not self._endcap(k, a['styles'])]
            light = any(w in cat.split('|')[0] for w in ('Lamp', 'Light', 'Chandelier'))
            if not usable or a['p'] < (0.03 if light else MIN_SHARE):      # a hall's wall lamps are no oddity
                continue
            if getattr(self, 'public', False) and cat.split('|')[0] in ('Bath', 'Shower', 'Bathtub'):
                continue                                   # a shop's or an office's toilet has no tub
            if cat.startswith('seat@'):
                key = rng.choices([k for k, _ in usable], weights=[c for _, c in usable])[0]
                seats[cat[5:]] = (rng.choice(pieces(key)), a)
                continue
            # a room several times the usual size is like several usual rooms: more chances to have it
            p = 1.0 - (1.0 - a['p']) ** min(6.0, max(1.0, area / max(1.0, rs['area'])))
            if not (a['p'] >= 0.5 or rng.random() < p):      # what most rooms of the type have, every room gets
                continue
            if a['one'] >= 0.7:
                n = 1 if area <= 2 * rs['area'] else max(1, int(round(a['k'] * area * 0.5)))
            else:
                n = max(1, int(round(a['k'] * area * rng.uniform(0.8, 1.2))))
            if a['one'] >= 0.5:                            # once-per-room fixtures: a big room is a few rooms, not k x area
                n = min(n, max(1, int(math.ceil(a.get('mean', 1.0) * min(3.0, max(1.0, area / max(1.0, rs['area'])))))))
            kinds = max(1, min(len(usable), int(round(a['mix'])))) if a['mix'] >= 1.6 else 1
            keys = []
            pool = list(usable)
            for _ in range(kinds):
                k = rng.choices([k for k, _ in pool], weights=[c for _, c in pool])[0]
                keys.append(k)
                pool = [(kk, c) for kk, c in pool if kk != k]
            plan.append((cat, n, [rng.choice(pieces(k)) for k in keys], a))
        # big fixed pieces first, then the rest in behaviour order
        # what (nearly) every vanilla room of the kind has goes in first: the toilet, the bed, the stove
        # (within a stage, single fixtures before runs: the stove gets its wall before the counters; what
        # stands on a counter still waits for the counters)
        plan.sort(key=lambda it: (it[3]['p'] < 0.75, ORDER.get(it[2][0].beh, 9), it[3]['one'] < 0.7, -it[2][0].size, -it[3]['p']))
        return plan, seats

    @staticmethod
    def _endcap(key, styles):
        """Left / right end pieces of a composite (back-bar shelving) when the plain middle piece exists."""
        group = key.split('|')[2]
        words = group.split()
        if 'Left' in words or 'Right' in words:
            base = ' '.join(w for w in words if w not in ('Left', 'Right'))
            return any(k.split('|')[2] == base for k in styles)
        return False

    # ------------------------------------------------------------------ primitives
    def _put(self, room, piece, facing, x, y, beh, block=True):
        cells = []
        for dx, dy, tile in piece.shapes[facing]:
            self.bg.fixtures[(x + dx, y + dy, room.z)].append(tile)
            cells.append((x + dx, y + dy))
        it = Item(piece, facing, cells, beh)
        room.items.append(it)
        for c in cells:
            room.taken.add(c)
            room.at[c] = it
            if block:
                room.block.add(c)
        self.count[beh] = self.count.get(beh, 0) + 1
        return it

    @staticmethod
    def _cells(piece, facing, x, y):
        return [(x + dx, y + dy) for dx, dy, _ in piece.shapes[facing]]

    @staticmethod
    def _front(cells, facing):
        dx, dy = DIRS[facing]
        cs = set(cells)
        return [(x + dx, y + dy) for (x, y) in cells if (x + dx, y + dy) not in cs]

    @staticmethod
    def _back_row(cells, facing):
        dx, dy = DIRS[OPP[facing]]
        cs = set(cells)
        return [(x, y) for (x, y) in cells if (x + dx, y + dy) not in cs]

    # ------------------------------------------------------------------ wall pieces
    def _tall(self, piece, _memo={}):
        """Solid to the eye (the game's 'solid', not 'solidtrans'): shelves, wardrobes, a bar. Never in a window."""
        if id(piece) not in _memo:
            cat = self.fitter.cat
            _memo[id(piece)] = any('solid' in cat.get(t, {}) and 'solidtrans' not in cat.get(t, {})
                                   for sh in piece.shapes.values() for _, _, t in sh)
        return _memo[id(piece)]

    def _wall_fits(self, room, piece, facing, x, y):
        cells = self._cells(piece, facing, x, y)
        if not all(room.can_block(c) for c in cells):
            return None
        side = OPP[facing]
        if not all(self.backable(side, cx, cy, room.z) for cx, cy in self._back_row(cells, facing)):
            return None
        if self._tall(piece) and any(self.window(s, cx, cy, room.z) for cx, cy in cells for s in 'NSWE'):
            return None                                   # nothing tall in front of a window
        front = self._front(cells, facing)
        if not any(room.open_floor(c) for c in front):
            return None
        return cells

    def _wall_score(self, room, piece, facing, cells):
        s = self.rng.random() * 0.6
        cs = set(cells)
        side = OPP[facing]
        for (x, y) in cells:
            for d, (dx, dy) in DIRS.items():
                nb = (x + dx, y + dy)
                if nb in cs or d == facing:
                    continue
                other = room.at.get(nb)
                if other is not None and other.beh == 'wall':
                    s += 1.0 + (1.0 if other.piece.cat == piece.cat else 0.0) + (0.5 if other.facing == facing else 0.0)
                elif d != side and self.edge(d, x, y, room.z):
                    s += 0.8                                   # tucked into a corner
        if any(self.window(side, x, y, room.z) for x, y in self._back_row(cells, facing)):
            s -= 1.5 if piece.low else 5.0
        return s

    def _wall(self, room, styles, n):
        placed = 0
        for i in range(n):
            piece = styles[i % len(styles)]
            cands = {}
            for (x, y, side) in room.perim:
                facing = OPP[side]
                shape = piece.shapes.get(facing)
                if shape is None:
                    continue
                for dx, dy, _ in shape:                        # any tile of the piece may sit on this wall square
                    key = (facing, x - dx, y - dy)
                    if key in cands:
                        continue
                    cells = self._wall_fits(room, piece, facing, x - dx, y - dy)
                    cands[key] = (self._wall_score(room, piece, facing, cells), cells) if cells else None
            ranked = sorted(((v[0], k, v[1]) for k, v in cands.items() if v), key=lambda t: -t[0])
            for score, (facing, x, y), cells in ranked[:8]:
                if room.keeps_floor_whole(cells):
                    self._put(room, piece, facing, x, y, 'wall')
                    room.front.update(c for c in self._front(cells, facing) if c in room.squares)
                    placed += 1
                    break
            else:
                break
        return placed

    # ------------------------------------------------------------------ free-standing tables, desks, islands
    def _tables(self, room, styles, n, seats, a):
        piece = styles[0]
        single = piece.size == 1 and len(piece.shapes) >= 2 and '' not in piece.shapes
        if single and n >= 3 and self._island(room, piece, n, seats):
            return
        host = self._host_class(piece)

        def spot(facing, x, y):
            """Footprint free, and a ring of open floor around it for chairs and walking."""
            w, h = piece.dims(facing)
            cells = [(x + dx, y + dy) for dx in range(w) for dy in range(h)]
            if not all(room.can_block(c) for c in cells):
                return None
            for dx in range(-1, w + 1):
                for dy in range(-1, h + 1):
                    c = (x + dx, y + dy)
                    if not (0 <= dx < w and 0 <= dy < h) and (c not in room.squares or c in room.taken
                                                               or c in room.clear):
                        return None
            return cells

        # every possible spot once, best first: spots on a regular grid win, so tables line up
        cands = []
        for facing in piece.shapes:
            w, h = piece.dims(facing)
            for y in range(room.y0 + 1, room.y1):
                for x in range(room.x0 + 1, room.x1):
                    if spot(facing, x, y):
                        s = self.rng.random() * 0.5
                        s += 1.0 * ((x - room.x0 - 2) % (w + 3) == 0) + 1.0 * ((y - room.y0 - 2) % (h + 3) == 0)
                        cands.append((s, facing, x, y))
        cands.sort(reverse=True)
        placed = 0
        for _, facing, x, y in cands:
            if placed >= n or len(room.block) >= 0.45 * len(room.squares):
                break
            cells = spot(facing, x, y)
            if not cells or not room.keeps_floor_whole(cells):
                continue
            it = self._put(room, piece, facing, x, y, 'table')
            placed += 1
            if host == 'desk':
                self._seat_front(room, it, seats)
            else:
                self._seat_around(room, it, seats, host, a)

    def _island(self, room, piece, n, seats):
        """A straight row of counters one square off a wall: service aisle behind, customers in front."""
        best = None
        z = room.z
        for side in 'NSWE':
            facing = OPP[side]
            if facing not in piece.shapes:
                continue
            ax, ay = DIRS[facing]                              # away from the wall
            lx, ly = (1, 0) if side in 'NS' else (0, 1)        # along the wall
            starts = [(x, y) for (x, y, s) in room.perim if s == side]
            done = set()
            for (x, y) in starts:
                if (x, y) in done:
                    continue
                run = []
                cx, cy = x, y
                while ((cx, cy) in room.squares and self.backable(side, cx, cy, z) and (cx, cy) not in room.taken
                       and room.can_block((cx + ax, cy + ay)) and room.open_floor((cx + 2 * ax, cy + 2 * ay))):
                    run.append((cx, cy))
                    done.add((cx, cy))
                    cx, cy = cx + lx, cy + ly
                if len(run) >= 4 and (best is None or len(run) > len(best[1])):
                    best = (side, run)
        if best is None:
            return False
        side, run = best
        facing = OPP[side]
        ax, ay = DIRS[facing]
        length = min(n, len(run) - 1)
        while length >= 3:
            part = run[:length] if self.rng.random() < 0.5 else run[-length:]
            cells = [(x + ax, y + ay) for (x, y) in part]
            if room.keeps_floor_whole(cells):
                break
            length -= 1
        else:
            return False
        stool = seats.get('counter')
        ratio = min(1.0, stool[1]['mean'] / max(1.0, length)) if stool else 0
        for (x, y) in part:
            self._put(room, piece, facing, x + ax, y + ay, 'table')
            room.front.add((x, y))                             # the service aisle stays open
            sq = (x + 2 * ax, y + 2 * ay)
            if stool and self.rng.random() < max(0.5, ratio) and room.can_seat(sq):
                self._seat(room, stool[0], sq, OPP[facing])
        return True

    @staticmethod
    def _host_class(piece):
        c = piece.custom.lower()
        for word in ('desk', 'table', 'counter', 'bar'):
            if word in c:
                return 'counter' if word == 'bar' else word
        return 'table'

    def _seat(self, room, piece, sq, facing):
        f = facing if facing in piece.shapes else next(iter(piece.shapes))
        if piece.size != 1:
            return
        self._put(room, piece, f, sq[0], sq[1], 'seat', block=False)

    def _seat_around(self, room, it, seats, host, a_host):
        seat = seats.get(host) or seats.get('table')
        if not seat:
            return
        piece, a = seat
        cs = set(it.cells)
        spots = [((x + dx, y + dy), OPP[d]) for (x, y) in it.cells for d, (dx, dy) in DIRS.items()
                 if (x + dx, y + dy) not in cs]
        # vanilla seats per table for this room type, spread over the sides of this table
        per_table = a['mean'] / max(1.0, a_host['mean'])
        p = min(1.0, max(0.3, per_table / max(1, len(spots))))
        for sq, facing in spots:
            if room.can_seat(sq) and self.rng.random() < p:
                self._seat(room, piece, sq, facing)

    def _seat_front(self, room, it, seats):
        seat = seats.get('desk') or seats.get('table')
        if not seat or it.facing not in DIRS:
            return
        front = [c for c in self._front(it.cells, it.facing) if room.can_seat(c)]
        if front:
            self._seat(room, seat[0], front[len(front) // 2], OPP[it.facing])

    # ------------------------------------------------------------------ rows of shelves / seats
    def _rows(self, room, styles, n, gap):
        best = []
        for facing in styles[0].shapes:
            if facing == '' or not all(facing in p.shapes for p in styles):
                continue
            w, h = styles[0].dims(facing)
            along_x = facing in 'NS'                            # pieces stand side by side along x
            spots = []
            step = (h if along_x else w) + gap
            first = (room.y0 if along_x else room.x0) + 1
            last = (room.y1 if along_x else room.x1)
            for r, line in enumerate(range(first, last, step)):
                piece = styles[r % len(styles)]
                pw, ph = piece.dims(facing)
                pos = (room.x0 if along_x else room.y0) + 1
                end = (room.x1 if along_x else room.y1)
                while pos + (pw if along_x else ph) - 1 < end:
                    x, y = (pos, line) if along_x else (line, pos)
                    cells = [(x + dx, y + dy) for dx in range(pw) for dy in range(ph)]
                    if along_x:                                 # the walking strips on both sides of the row
                        before = [(x + dx, y - 1) for dx in range(pw)]
                        after = [(x + dx, y + ph) for dx in range(pw)]
                    else:
                        before = [(x - 1, y + dy) for dy in range(ph)]
                        after = [(x + pw, y + dy) for dy in range(ph)]
                    if (all(room.can_block(c) for c in cells) and all(room.open_floor(c) for c in before)
                            and all(room.open_floor(c) for c in after)
                            and (not self._tall(piece) or not any(self.window(s_, cx, cy, room.z) for cx, cy in cells for s_ in 'NSWE'))):
                        spots.append((piece, facing, x, y, cells))
                        pos += pw if along_x else ph
                    else:
                        pos += 1
            if len(spots) > len(best):
                best = spots
        placed = 0
        for piece, facing, x, y, cells in best:
            if placed >= n:
                break
            if all(room.can_block(c) for c in cells) and room.keeps_floor_whole(cells):
                self._put(room, piece, facing, x, y, piece.beh)
                placed += 1
        return placed

    # ------------------------------------------------------------------ small loose things
    def _loose(self, room, styles, n, at_wall):
        z = room.z
        for i in range(n):
            piece = styles[i % len(styles)]
            facing = next(iter(piece.shapes))
            if piece.size != 1:
                continue
            cands = []
            for sq in room.squares:
                if not room.can_block(sq):
                    continue
                walls = sum(self.edge(s, sq[0], sq[1], z) for s in 'NSWE')
                if at_wall and walls == 0 or (not at_wall and walls):
                    continue
                if self._tall(piece) and any(self.window(s, sq[0], sq[1], z) for s in 'NSWE'):
                    continue
                cands.append((walls + self.rng.random(), sq))
            cands.sort(reverse=True)
            for _, sq in cands[:6]:
                if room.keeps_floor_whole([sq]):
                    # back to a wall, front to the room (vanilla never turns a chair or a cabinet to the wall)
                    turn = [OPP[s_] for s_ in 'NSWE' if self.edge(s_, sq[0], sq[1], z) and OPP[s_] in piece.shapes
                            and room.open_floor((sq[0] + DIRS[OPP[s_]][0], sq[1] + DIRS[OPP[s_]][1]))]
                    f_ = turn[0] if turn else facing
                    if not turn and any(self.edge(s_, sq[0], sq[1], z) for s_ in (f_,)):
                        continue                         # it could only face the wall: try another spot
                    self._put(room, piece, f_, sq[0], sq[1], piece.beh)
                    break

    # ------------------------------------------------------------------ wall-mounted and on-top pieces
    def _high(self, room, styles, n):
        z = room.z
        for i in range(n):
            piece = styles[i % len(styles)]
            partner = piece.m.get('partner')
            over = piece.m['overlow'] >= 0.4
            best = None
            for (x, y, side) in room.perim:
                facing = OPP[side]
                if facing not in piece.shapes:
                    continue
                cells = self._cells(piece, facing, x, y)
                if not all(c in room.squares and (c[0], c[1], side) not in room.high
                           and self.backable(side, c[0], c[1], z) for c in cells):
                    continue
                s = self.rng.random()
                for c in cells:
                    under = room.at.get(c)
                    if under is None:
                        s += 0.0 if over else 1.0
                    elif under.facing == facing and (under.piece.low or under.piece.table):
                        s += 2.0 if over else 0.5
                        if partner and partner in under.piece.custom.lower():
                            s += 3.0
                        if under.top and partner and any(partner in t.lower() for t in under.top):
                            s += 3.0                            # e.g. the counter that carries the sink
                    else:
                        s -= 6.0                                # behind a wardrobe or a fridge
                if best is None or s > best[0]:
                    best = (s, facing, x, y, side, cells)
            if best is None or best[0] < -1:
                break
            _, facing, x, y, side, cells = best
            for dx, dy, tile in piece.shapes[facing]:
                self.bg.fixtures[(x + dx, y + dy, z)].append(tile)
            room.high.update((c[0], c[1], side) for c in cells)
            self.count['high'] = self.count.get('high', 0) + 1

    def _ontop(self, room, styles, n):
        for i in range(n):
            piece = styles[i % len(styles)]
            if piece.size != 1:
                continue
            partner = piece.m.get('partner')
            hosts = [it for it in room.items if it.piece.table and not it.top and it.beh in ('wall', 'table')]
            if partner:
                liked = [it for it in hosts if partner in it.piece.custom.lower()]
                hosts = liked or [it for it in hosts if it.beh == 'wall']
            if not hosts:
                break
            host = self.rng.choice(hosts)
            facing = host.facing if host.facing in piece.shapes else next(iter(piece.shapes))
            x, y = self.rng.choice(host.cells)
            self.bg.fixtures[(x, y, room.z)].append(piece.shapes[facing][0][2])
            host.top = [piece.custom]
            self.count['ontop'] = self.count.get('ontop', 0) + 1

    def _switch(self, room):
        """One light switch per room, on the wall beside a door."""
        z = room.z
        best = None
        for (x, y, side) in room.perim:
            d = min((abs(x - dx) + abs(y - dy) for dx, dy in room.doors), default=3)
            if d == 0 and self.door(side, x, y, z):
                continue
            s = -d + (0.5 if side in 'NW' else 0.0) - (2.0 if self.window(side, x, y, z) else 0.0) + self.rng.random() * 0.3
            if best is None or s > best[0]:
                best = (s, x, y, side)
        if best:
            _, x, y, side = best
            self.bg.fixtures[(x, y, z)].append(SWITCH[side])
            room.high.add((x, y, side))
            self.count['switch'] = self.count.get('switch', 0) + 1
