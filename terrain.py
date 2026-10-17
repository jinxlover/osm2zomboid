"""Lay Project Zomboid ground tiles and street furniture from the terrain classes in ground.Ground.

Everything here copies how the vanilla map does it; the conventions were measured on the game's own
"Muldraugh, KY" cells (neighbour statistics of ~160 sampled cells), not guessed:

  * surfaces live in 16-tile blocks of the blends sheets:  +0,+5,+6,+7 full tiles, +1 NW half,
    +2 SE half, +3 SW half, +4 NE half, +8/+9/+10/+11 thin strips with the *higher* terrain to the
    N/W/E/S (+12..+15 second strip variants on the natural sheets). A higher-priority surface bleeds
    onto a lower neighbour by putting the higher family's piece on the LOWER square.
  * curbs sit on the square whose S or E edge is the road boundary: street_curbs_01_8 / _9 on the
    sidewalk square (road to its S / E) and _10 / _11 on the road square (sidewalk to its S / E).
  * ~70% of grass squares carry a tuft overlay (blends_grassoverlays_01, 3 sets per grass shade).
  * lane lines (street_trafficlines_01): tiles 0 / 2 / 4 / 6 are white lines on the W / N / E / S edge
    of a square, 16 / 18 / 20 / 22 the same in yellow. Yellow 22 + 18 on two neighbouring rows make the
    double centre line of an east-west two-way road, 20 + 16 that of a north-south one. Lane dividers
    are white, one square on, two off. A crosswalk over an east-west road is two columns of squares
    carrying the wide band 34 plus the outer rail (34 + 0, then 34 + 4); over a north-south road two
    rows carrying 32 + 2, then 32 + 6.
  * zombie intensity: one byte per chunk, 0-10 on the vanilla map. Downtown Louisville averages 2.2
    (13% of chunks at 0, 13% at 1, 43% at 2, 12% at 3, 17% at 4, 3% at 5-7); zombie_density() deals
    out the same proportions, capped at 4.
"""
import math
import numpy as np
from scipy import ndimage as ndi

from ground import (T_PAVE, T_GRASS, T_ASPHALT, T_PLAZA, T_DIRT, T_LAWN, T_GRAVEL, T_SAND, T_WATER,
                    T_CONCRETE, T_DIRTROAD, T_BRICK, T_FLOWERBED, V_PARK, V_FOREST, V_SCRUB, V_LAWN)

DIRS = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
HALF = {'NW': 1, 'SE': 2, 'SW': 3, 'NE': 4}
STRIP = {'N': 8, 'W': 9, 'E': 10, 'S': 11}


def _tufts(starts):
    out = []
    for start, weight in starts:
        out += [f'blends_grassoverlays_01_{start + i}' for i in range(6)] * weight
    return out


TUFTS_DARK = _tufts([(0, 4), (8, 2), (16, 2)])
TUFTS_MEDIUM = _tufts([(24, 4), (32, 2), (40, 2)])
# a mown lawn, as vanilla's yards: the short overlays only (vanilla yards: sets 0-24, none of the long 32 / 40)
TUFTS_LAWN = _tufts([(0, 1), (8, 1), (16, 1), (24, 1)])
TREES = [f'vegetation_trees_01_{i}' for i in (8, 9, 10, 11)]
JUMBO = 'jumbo_tree_01_0'
BUSHES = [f'vegetation_foliage_01_{i}' for i in range(8, 15)]
PLANTS = [f'vegetation_groundcover_01_{i}' for i in range(18, 24)]
FLOWERS = [f'vegetation_ornamental_01_{i}' for i in (16, 17, 18, 19, 20, 21, 22, 23)]
LAMP = 'lighting_outdoor_01_3'
POLE = 'lighting_outdoor_01_16'                # vanilla's residential street light: a wooden pole...
POLE_HEAD = {'N': 'lighting_outdoor_01_8', 'E': 'lighting_outdoor_01_9', 'S': 'lighting_outdoor_01_10',
             'W': 'lighting_outdoor_01_11'}     # ...its lamp a floor up, over the street on that side of it
# traffic signals as vanilla builds them: a plain pole on the sidewalk square, the signal head on the same
# square one level up, picked by the side the road is on (two head styles per side)
SIGNAL_POLE = 'lighting_outdoor_01_16'
SIGNAL_HEAD = {'W': ('lighting_outdoor_01_12', 'lighting_outdoor_01_20'), 'S': ('lighting_outdoor_01_13', 'lighting_outdoor_01_21'),
               'E': ('lighting_outdoor_01_14', 'lighting_outdoor_01_22'), 'N': ('lighting_outdoor_01_15', 'lighting_outdoor_01_23')}
STOP_SIGN = {'S': 'street_decoration_01_0', 'E': 'street_decoration_01_1', 'N': 'street_decoration_01_2', 'W': 'street_decoration_01_3'}
METER = {'S': 'street_decoration_01_4', 'E': 'street_decoration_01_5', 'N': 'street_decoration_01_6', 'W': 'street_decoration_01_7'}
POSTBOX = {'S': 'street_decoration_01_8', 'E': 'street_decoration_01_9', 'N': 'street_decoration_01_10', 'W': 'street_decoration_01_11'}
HYDRANT = 'street_decoration_01_12'
DRAINS = ['street_decoration_01_13', 'street_decoration_01_14']
MANHOLE = 'street_decoration_01_15'
STREET_SIGN = 'street_decoration_01_22'
BIN = 'trashcontainers_01_21'
GARBAGE_BIN = 'trashcontainers_01_17'          # vanilla's green sidewalk bin
LITTER = ['trash_01_%d' % i for i in (0, 1, 4, 5, 9, 11, 12, 18)]
VENDING = ['location_shop_accessories_01_8', 'location_shop_accessories_01_12']
# park bench (2 squares); facing -> [(dx, dy, tile)]
BENCH = {'S': [(0, 0, 'furniture_seating_outdoor_01_0'), (1, 0, 'furniture_seating_outdoor_01_1')],
         'N': [(0, 0, 'furniture_seating_outdoor_01_4'), (1, 0, 'furniture_seating_outdoor_01_5')],
         'E': [(0, 0, 'furniture_seating_outdoor_01_3'), (0, 1, 'furniture_seating_outdoor_01_2')],
         'W': [(0, 0, 'furniture_seating_outdoor_01_7'), (0, 1, 'furniture_seating_outdoor_01_6')]}
HEDGE_EW, HEDGE_NS = 'vegetation_ornamental_01_0', 'vegetation_ornamental_01_1'
HEDGE_LOW_EW, HEDGE_LOW_NS = 'vegetation_ornamental_01_4', 'vegetation_ornamental_01_5'
# fence styles: (N pieces, W pieces, NW corner, SE post)   (fencing_01 unless the name says otherwise)
FENCES = {
    'chain': (['fencing_01_56', 'fencing_01_57'], ['fencing_01_58', 'fencing_01_59'], 'fencing_01_60', 'fencing_01_61'),
    'chain_low': (['fencing_01_24', 'fencing_01_25'], ['fencing_01_26', 'fencing_01_27'], 'fencing_01_28', 'fencing_01_29'),
    'iron': (['fencing_01_64', 'fencing_01_65'], ['fencing_01_66', 'fencing_01_67'], 'fencing_01_68', 'fencing_01_69'),
    'railing': (['fencing_01_1'], ['fencing_01_2'], 'fencing_01_3', 'fencing_01_0'),
    'wood': (['fencing_01_32', 'fencing_01_33'], ['fencing_01_34', 'fencing_01_35'], 'fencing_01_36', 'fencing_01_37'),
    'wood_tall': (['fencing_01_8', 'fencing_01_9'], ['fencing_01_10', 'fencing_01_11'], 'fencing_01_12', 'fencing_01_13'),
    'picket': (['fencing_01_5'], ['fencing_01_4'], 'fencing_01_6', 'fencing_01_7'),
    'concrete': (['fencing_01_41'], ['fencing_01_40'], 'fencing_01_42', 'fencing_01_43'),
    'brick': (['walls_exterior_house_low_01_1'], ['walls_exterior_house_low_01_0'], 'walls_exterior_house_low_01_2', 'walls_exterior_house_low_01_3'),
    'brick_red': (['walls_exterior_house_low_01_5'], ['walls_exterior_house_low_01_4'], 'walls_exterior_house_low_01_6', 'walls_exterior_house_low_01_7'),
}
MARKED_ROADS = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'residential', 'unclassified',
                'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link'}
EDGE_LINE_ROADS = {'motorway', 'trunk', 'primary', 'secondary'}


class Fam:
    """One vanilla surface family."""

    def __init__(self, name, full=None, block=None, prio=0, road=False, tufts=None, strips2=True):
        self.name = name
        self.sheet, self.base = block if block else (None, None)
        self.full = full or [f'{self.sheet}_{self.base + i}' for i in (0, 5, 6, 7)]
        self.prio = prio
        self.road = road
        self.tufts = tufts
        self.variants = 2 if strips2 else 1

    def half(self, corner):
        return f'{self.sheet}_{self.base + HALF[corner]}'

    def strip(self, side, rng):
        i = STRIP[side] + (4 if self.variants == 2 and rng.random() < 0.5 else 0)
        return f'{self.sheet}_{self.base + i}'


FAMS = {
    T_PAVE: Fam('pave', full=['floors_exterior_tilesandstone_01_3']),
    T_PLAZA: Fam('plaza', full=['floors_exterior_tilesandstone_01_5']),
    T_BRICK: Fam('brick', full=['floors_exterior_tilesandstone_01_1', 'floors_exterior_tilesandstone_01_2']),
    T_ASPHALT: Fam('asphalt', block=('blends_street_01', 80), prio=1, road=True, strips2=False),
    T_CONCRETE: Fam('concrete', block=('blends_street_01', 96), prio=1, road=True, strips2=False),
    T_DIRTROAD: Fam('dirtroad', full=['blends_street_01_16', 'blends_street_01_21'], block=('blends_street_01', 16), prio=2, strips2=False),
    T_GRAVEL: Fam('gravel', block=('blends_street_01', 32), prio=3, strips2=False),
    T_SAND: Fam('sand', block=('blends_natural_01', 0), prio=4),
    T_DIRT: Fam('dirt', block=('blends_natural_01', 64), prio=5),
    T_LAWN: Fam('lawn', block=('blends_natural_01', 32), prio=7, tufts=TUFTS_LAWN),
    T_FLOWERBED: Fam('lawn', block=('blends_natural_01', 32), prio=7, tufts=TUFTS_MEDIUM),
    T_GRASS: Fam('grass', block=('blends_natural_01', 16), prio=8, tufts=TUFTS_DARK),
    T_WATER: Fam('water', block=('blends_natural_02', 0), prio=9),
}
NCLS = max(FAMS) + 1


def _shift(a, dx, dy, fill):
    """out[y, x] = a[y + dy, x + dx] (the neighbour's value), `fill` outside the map."""
    out = np.full_like(a, fill)
    H, W = a.shape
    ys = slice(max(0, dy), min(H, H + dy))
    xs = slice(max(0, dx), min(W, W + dx))
    yd = slice(max(0, -dy), min(H, H - dy))
    xd = slice(max(0, -dx), min(W, W - dx))
    out[yd, xd] = a[ys, xs]
    return out


# ------------------------------------------------------------------------------------ ground tiles
def lay_ground(md, g, rng):
    """Base tile for every square, then curbs, edge blends and grass tufts."""
    G, B = g.G, g.B
    H, W = G.shape
    nrng = np.random.RandomState(rng.randrange(1 << 30))
    ground = B == 0
    # a lawn is two shades of grass in patches, about half and half, as vanilla's yards are; laid as two
    # classes so the edge blends soften where they meet (as vanilla's do), not hard squares
    patch = (ndi.uniform_filter(nrng.random_sample(G.shape), 7) > 0.5) & (G == T_LAWN)
    G = G.copy()
    G[patch] = T_GRASS
    prio = np.array([FAMS[c].prio for c in range(NCLS)], np.int8)
    road = np.array([FAMS[c].road for c in range(NCLS)], bool)
    bleed = np.array([FAMS[c].prio >= 2 for c in range(NCLS)], bool)
    P = prio[G]
    R = road[G] & ground
    BL = bleed[G] & ground
    paved = np.isin(G, [T_PAVE, T_PLAZA, T_BRICK]) & ground
    soft = np.isin(G, [T_GRASS, T_LAWN, T_FLOWERBED, T_DIRT, T_SAND]) & ground
    dens = getattr(g, 'density', None)
    urban = (dens > 0.10) if dens is not None else np.ones(G.shape, bool)
    curbok = paved | (soft & urban)          # country roads fade into the grass instead (edge blends)
    sq = md.squares

    # 1. base tiles
    var = nrng.randint(0, 4, size=G.shape)
    for cls, fam in FAMS.items():
        full = fam.full
        n = len(full)
        for y, x in np.argwhere(G == cls):
            sq[(int(x), int(y), 0)] = [full[var[y, x] % n]]

    # 2. curbs (road <-> sidewalk/grass boundaries), remembering the sides they cover
    curbed = {s: np.zeros(G.shape, bool) for s in 'NSWE'}
    both = ground[:-1, :] & ground[1:, :]
    a, b = R[:-1, :], R[1:, :]
    for mask, tile in (((both & ~a & b & curbok[:-1, :]), 'street_curbs_01_8'),
                       ((both & a & ~b & curbok[1:, :]), 'street_curbs_01_10')):
        for y, x in np.argwhere(mask):
            sq[(int(x), int(y), 0)].append(tile)
            curbed['S'][y, x] = True
            curbed['N'][y + 1, x] = True
    both = ground[:, :-1] & ground[:, 1:]
    a, b = R[:, :-1], R[:, 1:]
    for mask, tile in (((both & ~a & b & curbok[:, :-1]), 'street_curbs_01_9'),
                       ((both & a & ~b & curbok[:, 1:]), 'street_curbs_01_11')):
        for y, x in np.argwhere(mask):
            sq[(int(x), int(y), 0)].append(tile)
            curbed['E'][y, x] = True
            curbed['W'][y, x + 1] = True

    # 3. edge blends: a higher family bleeds onto this (lower) square
    hi = {}
    cand = np.zeros(G.shape, bool)
    for s, (dx, dy) in DIRS.items():
        m = ground & _shift(BL, dx, dy, False) & (_shift(P, dx, dy, -1) > P) & ~curbed[s]
        hi[s] = m
        cand |= m
    for y, x in np.argwhere(cand):
        y, x = int(y), int(x)
        by_prio = {}
        for s, (dx, dy) in DIRS.items():
            if hi[s][y, x]:
                fam = FAMS[G[y + dy, x + dx]]
                by_prio.setdefault(fam.prio, (fam, set()))[1].add(s)
        fam, sides = by_prio[max(by_prio)]
        tiles = []
        for corner, pair in (('NW', {'N', 'W'}), ('SE', {'S', 'E'}), ('SW', {'S', 'W'}), ('NE', {'N', 'E'})):
            if pair <= sides:
                tiles.append(fam.half(corner))
                sides -= pair
                break
        for s in sorted(sides):
            tiles.append(fam.strip(s, rng))
        sq[(x, y, 0)].extend(tiles)

    # 4. grass tufts
    for cls in (T_GRASS, T_LAWN, T_FLOWERBED):
        fam = FAMS[cls]
        lawnish = (G == cls) & ground & ((g.G == T_LAWN) if cls != T_LAWN else True)
        rate = np.where(lawnish & (g.G == T_LAWN), 0.2, 0.35)              # vanilla: yards ~20%, fields 31-40%
        m = (G == cls) & ground & (nrng.random_sample(G.shape) < rate)
        tufts = FAMS[T_LAWN].tufts
        idx = nrng.randint(0, max(len(fam.tufts), len(tufts)), size=G.shape)
        for y, x in np.argwhere(m):
            own = tufts if g.G[y, x] == T_LAWN else fam.tufts      # a lawn's dark patches keep the short tufts
            sq[(int(x), int(y), 0)].append(own[idx[y, x] % len(own)])


# ---------------------------------------------------------------------------------- road markings
def _run_lengths(mask, axis):
    """Length of the run of True values containing each square, along `axis` (0 = columns, 1 = rows)."""
    m = mask if axis == 1 else mask.T
    out = np.zeros(m.shape, np.int32)
    for i in range(m.shape[0]):
        row = m[i]
        j = 0
        n = len(row)
        while j < n:
            if row[j]:
                k = j
                while k < n and row[k]:
                    k += 1
                out[i, j:k] = k - j
                j = k
            else:
                j += 1
    return out if axis == 1 else out.T


def _segment_orient(ux, uy):
    if abs(ux) >= 0.906:
        return 'EW'
    if abs(uy) >= 0.906:
        return 'NS'
    return None


def _road_orient_at(g, px, py, maxd=4.0):
    best = None
    for rd in g.roads:
        line = rd['line']
        for (xa, ya), (xb, yb) in zip(line, line[1:]):
            dx, dy = xb - xa, yb - ya
            L2 = dx * dx + dy * dy
            if L2 < 0.25:
                continue
            t = max(0.0, min(1.0, ((px - xa) * dx + (py - ya) * dy) / L2))
            d = math.hypot(xa + t * dx - px, ya + t * dy - py)
            if d <= maxd and (best is None or d < best[0]):
                L = math.sqrt(L2)
                best = (d, _segment_orient(dx / L, dy / L))
    return best[1] if best else None


def lay_markings(md, g, rng):
    """Crosswalks, parking stalls, centre / lane / edge lines on asphalt streets."""
    G, B = g.G, g.B
    H, W = G.shape
    A = (G == T_ASPHALT) & (B == 0)
    hrun = _run_lengths(A, 1)
    vrun = _run_lengths(A, 0)
    used = set()

    def put(x, y, tile):
        if 0 <= x < W and 0 <= y < H and A[y, x] and (x, y) not in used:
            t = md.squares[(x, y, 0)]
            if tile not in t:
                t.append(tile)

    def span(x, y, dx, dy, limit=24):
        """Asphalt squares reachable from (x, y) going both ways along (dx, dy)."""
        a = b = 0
        while a < limit and 0 <= x - dx * (a + 1) < W and 0 <= y - dy * (a + 1) < H and A[y - dy * (a + 1), x - dx * (a + 1)]:
            a += 1
        while b < limit and 0 <= x + dx * (b + 1) < W and 0 <= y + dy * (b + 1) < H and A[y + dy * (b + 1), x + dx * (b + 1)]:
            b += 1
        return a, b

    # crosswalks
    done = []
    for (cx, cy) in g.crossings:
        if any((cx - ox) ** 2 + (cy - oy) ** 2 < 36 for ox, oy in done):
            continue
        orient = _road_orient_at(g, cx, cy)
        x, y = int(cx), int(cy)
        if orient is None or not (0 <= x < W and 0 <= y < H):
            continue
        if not A[y, x]:
            near = [(xx, yy) for yy in range(y - 3, y + 4) for xx in range(x - 3, x + 4)
                    if 0 <= xx < W and 0 <= yy < H and A[yy, xx]]
            if not near:
                continue
            x, y = min(near, key=lambda q: (q[0] - cx) ** 2 + (q[1] - cy) ** 2)
        squares = []
        if orient == 'EW':                       # walkers cross north-south: two columns over all road rows
            for c, tiles in ((x, ('street_trafficlines_01_34', 'street_trafficlines_01_0')),
                             (x + 1, ('street_trafficlines_01_34', 'street_trafficlines_01_4'))):
                if not (0 <= c < W and A[y, c]):
                    continue
                a, b = span(c, y, 0, 1)
                if a + b + 1 <= 22:
                    squares += [(c, r, tiles) for r in range(y - a, y + b + 1)]
        else:                                    # road runs north-south: two rows over all road columns
            for r, tiles in ((y, ('street_trafficlines_01_32', 'street_trafficlines_01_2')),
                             (y + 1, ('street_trafficlines_01_32', 'street_trafficlines_01_6'))):
                if not (0 <= r < H and A[r, x]):
                    continue
                a, b = span(x, r, 1, 0)
                if a + b + 1 <= 22:
                    squares += [(c, r, tiles) for c in range(x - a, x + b + 1)]
        if len(squares) < 6:
            continue
        for (c, r, tiles) in squares:
            if (c, r) not in used:
                md.squares[(c, r, 0)].extend(t for t in tiles if t not in md.squares[(c, r, 0)])
        used.update((c, r) for c, r, _ in squares)
        done.append((cx, cy))

    _parking_stalls(md, g, A, used)

    # lane lines, one road at a time, only where the road runs along a tile axis and outside junctions
    for rd in g.roads:
        if rd['cls'] != T_ASPHALT or rd['hw'] not in MARKED_ROADS:
            continue
        w = rd['w']
        lanes = rd['lanes'] or (2 if w >= 5.5 else 1)
        two_way = not rd['oneway']
        edge_lines = w >= 9 and (rd['hw'] in EDGE_LINE_ROADS or lanes >= 3)
        line = rd['line']
        for (xa, ya), (xb, yb) in zip(line, line[1:]):
            dx, dy = xb - xa, yb - ya
            L = math.hypot(dx, dy)
            if L < 1:
                continue
            orient = _segment_orient(dx / L, dy / L)
            if orient is None:
                continue
            ew = orient == 'EW'
            lo, hi = (min(xa, xb), max(xa, xb)) if ew else (min(ya, yb), max(ya, yb))
            for u in range(int(lo), int(hi) + 1):
                t = (u + 0.5 - (xa if ew else ya)) / (dx if ew else dy)
                if not 0 <= t <= 1:
                    continue
                c = (ya + t * dy) if ew else (xa + t * dx)          # centre of the road across the travel axis
                ci = int(c)
                x, y = (u, ci) if ew else (ci, u)
                if not (0 <= x < W and 0 <= y < H) or not A[y, x]:
                    continue
                if (vrun[y, x] if ew else hrun[y, x]) > 1.5 * w + 2:  # inside a junction or a car park
                    continue

                def mark(v, tile):
                    put(u, v, tile) if ew else put(v, u, tile)

                if two_way and lanes >= 2 and w >= 5.5:
                    v = int(round(c)) - 1
                    mark(v, 'street_trafficlines_01_22' if ew else 'street_trafficlines_01_20')
                    mark(v + 1, 'street_trafficlines_01_18' if ew else 'street_trafficlines_01_16')
                if lanes >= 2 and u % 3 == 0:
                    lw = w / lanes
                    for k in range(1, lanes):
                        vb = c - w / 2 + k * lw
                        if two_way and abs(vb - c) < 0.75:
                            continue
                        mark(int(round(vb)) - 1, 'street_trafficlines_01_6' if ew else 'street_trafficlines_01_4')
                if edge_lines:
                    a, b = span(x, y, 0, 1, int(w)) if ew else span(x, y, 1, 0, int(w))
                    if a + b + 1 <= w + 2 and a + b + 1 >= 7:
                        mark(ci - a + 1, 'street_trafficlines_01_2' if ew else 'street_trafficlines_01_0')
                        mark(ci + b - 1, 'street_trafficlines_01_6' if ew else 'street_trafficlines_01_4')


def _parking_stalls(md, g, A, used):
    """Vanilla parking: 3-wide, 5-deep stalls in rows along the lot's long side, back to back in pairs,
    each row facing a 6-square drive aisle; a white line between stalls and across each stall's back.
    Every stall is also recorded (g.stalls) for a ParkingStall zone, where the game parks a car."""
    g.stalls = []
    LINE = {'W': 'street_trafficlines_01_0', 'N': 'street_trafficlines_01_2', 'E': 'street_trafficlines_01_4',
            'S': 'street_trafficlines_01_6'}
    PK = g.PK
    for lab, sl in enumerate(ndi.find_objects(PK), start=1):
        if sl is None:
            continue
        ys, xs = sl
        ok = (PK[sl] == lab) & (g.B[sl] == 0) & ~g.street[sl]
        inner = ndi.binary_erosion(ok, iterations=1)
        h, w = inner.shape
        along_x = w >= h                                  # rows run along the long side
        depth = h if along_x else w
        length = w if along_x else h
        # rows across the depth: [row facing +][aisle][row facing -][row facing +][aisle]...
        rows, d = [], 0
        while d + 5 <= depth:
            if d + 11 <= depth:
                rows.append((d, +1))                      # backs to the lot edge / the previous row
                d += 11                                   # its 5 squares and the aisle in front
                if d + 5 <= depth:
                    rows.append((d, -1))                  # facing back onto that aisle
                    d += 5
            else:
                rows.append((d, -1 if rows and rows[-1][1] > 0 else +1))
                d += 5
        for d0, face in rows:
            run = []
            for a in range(0, length - 2, 3):
                cells = [(a + i, d0 + j) for i in range(3) for j in range(5)]
                if all((inner[c[1], c[0]] if along_x else inner[c[0], c[1]]) for c in cells):
                    run.append(a)
            for k, a in enumerate(run):
                if along_x:
                    x0, y0 = xs.start + a, ys.start + d0
                    for j in range(5):                    # the line on the stall's west side
                        md.squares[(x0, y0 + j, 0)].append(LINE['W'])
                    if k == len(run) - 1 or run[k + 1] != a + 3:
                        for j in range(5):
                            md.squares[(x0 + 3, y0 + j, 0)].append(LINE['W'])
                    back_y = y0 if face > 0 else y0 + 4
                    for i in range(3):
                        md.squares[(x0 + i, back_y, 0)].append(LINE['N'] if face > 0 else LINE['S'])
                    used.update((x0 + i, y0 + j) for i in range(3) for j in range(5))
                    g.stalls.append((x0, y0, 3, 5, 'S' if face > 0 else 'N'))
                else:
                    x0, y0 = xs.start + d0, ys.start + a
                    for i in range(5):
                        md.squares[(x0 + i, y0, 0)].append(LINE['N'])
                    if k == len(run) - 1 or run[k + 1] != a + 3:
                        for i in range(5):
                            md.squares[(x0 + i, y0 + 3, 0)].append(LINE['N'])
                    back_x = x0 if face > 0 else x0 + 4
                    for j in range(3):
                        md.squares[(back_x, y0 + j, 0)].append(LINE['W'] if face > 0 else LINE['E'])
                    used.update((x0 + i, y0 + j) for i in range(5) for j in range(3))
                    g.stalls.append((x0, y0, 5, 3, 'E' if face > 0 else 'W'))


# ---------------------------------------------------------------------------------- decorations
def _edges_from_line(line):
    """Snap a polyline to grid nodes and walk it as unit edges: ('N', x, y) = north edge of square
    (x, y), ('W', x, y) = west edge of square (x, y)."""
    nodes = [(int(round(x)), int(round(y))) for x, y in line]
    edges = []
    for (x0, y0), (x1, y1) in zip(nodes, nodes[1:]):
        x, y = x0, y0
        dx, dy = x1 - x0, y1 - y0
        sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        ax, ay = abs(dx), abs(dy)
        err = ax - ay
        guard = 0
        while (x, y) != (x1, y1) and guard < 4096:
            guard += 1
            e2 = 2 * err
            if x != x1 and (y == y1 or e2 > -ay):
                nx = x + sx
                edges.append(('N', min(x, nx), y))
                x = nx
                err -= ay
            else:
                ny = y + sy
                edges.append(('W', x, min(y, ny)))
                y = ny
                err += ax
    return edges


def _fence_style(kind, tags):
    mat = (tags.get('material') or '').lower()
    ft = (tags.get('fence_type') or '').lower()
    wall = (tags.get('wall') or '').lower()
    if kind == 'fence':
        if 'chain' in ft or 'chain' in mat:
            return 'chain'
        if ft in ('railing', 'bars', 'metal_bars') or mat in ('iron', 'steel', 'metal') or ft == 'metal':
            return 'iron' if 'iron' in (ft + mat) or ft == 'metal' else 'railing'
        if 'wood' in ft or 'wood' in mat or ft in ('split_rail', 'panel'):
            return 'wood_tall' if ft == 'panel' else 'wood'
        if 'picket' in ft or mat == 'white':
            return 'picket'
        return 'chain'
    if kind == 'guard_rail' or kind == 'handrail':
        return 'railing'
    if kind in ('wall', 'retaining_wall', 'city_wall'):
        if 'concrete' in (mat + wall) or 'block' in wall:
            return 'concrete'
        if 'brick' in (mat + wall) or 'red' in (tags.get('colour') or ''):
            return 'brick_red'
        return 'brick'
    return 'chain'


SKIP_BARRIERS = {'bollard', 'gate', 'entrance', 'kerb', 'cycle_barrier', 'block', 'lift_gate', 'swing_gate',
                 'chain', 'jersey_barrier', 'cattle_grid', 'toll_booth', 'stile', 'turnstile', 'height_restrictor'}


def fence_fields(g, min_area=2500, gate_every=40, gate=3):
    """Chain-link round every big open field (wild grass, dirt or sand of 2500+ squares: not a lawn, a park,
    a wood, a car park or a road), one square in from its edge so both sides of the fence are open ground,
    with a gate-width gap every 40 squares along each side. Added to g.barriers. -> fields fenced"""
    open_ = ((g.B == 0) & ~g.street & np.isin(g.G, [T_GRASS, T_DIRT, T_SAND])
             & np.isin(g.V, [0, V_SCRUB]) & (getattr(g, 'PK', np.zeros_like(g.B)) == 0))
    lab, n = ndi.label(open_)
    if not n:
        return 0
    sizes = np.bincount(lab.ravel())
    done = 0
    tags = {'barrier': 'fence', 'fence_type': 'chain_link'}
    for f in range(1, n + 1):
        if sizes[f] < min_area:
            continue
        inner = ndi.binary_erosion(lab == f)
        if inner.sum() < min_area // 2:
            continue
        H, W = inner.shape
        up = np.zeros_like(inner); up[1:, :] = inner[:-1, :]
        lf = np.zeros_like(inner); lf[:, 1:] = inner[:, :-1]
        dn = np.zeros_like(inner); dn[:-1, :] = inner[1:, :]
        rt = np.zeros_like(inner); rt[:, :-1] = inner[:, 1:]
        gap = lambda v: (v % gate_every) < gate
        for y, x in np.argwhere(inner & ~up):            # north edge of the field's inner squares
            if not gap(int(x)):
                g.barriers.append(('fence', [(float(x), float(y)), (float(x + 1), float(y))], tags))
        for y, x in np.argwhere(inner & ~dn):            # south edge
            if not gap(int(x)):
                g.barriers.append(('fence', [(float(x), float(y + 1)), (float(x + 1), float(y + 1))], tags))
        for y, x in np.argwhere(inner & ~lf):            # west edge
            if not gap(int(y)):
                g.barriers.append(('fence', [(float(x), float(y)), (float(x), float(y + 1))], tags))
        for y, x in np.argwhere(inner & ~rt):            # east edge
            if not gap(int(y)):
                g.barriers.append(('fence', [(float(x + 1), float(y)), (float(x + 1), float(y + 1))], tags))
        done += 1
    return done


def _place_fences(md, g, blocked, rng):
    """Fences and walls stand on square edges (like building walls); hedges fill squares."""
    G, B = g.G, g.B
    H, W = G.shape
    street = g.street
    hw, vw = {}, {}

    def ok(x, y):
        return 0 <= x < W and 0 <= y < H and B[y, x] == 0 and not street[y, x] and G[y, x] != T_WATER

    for kind, line, tags in g.barriers:
        if kind in SKIP_BARRIERS:
            continue
        if kind == 'hedge':
            low = _num(tags.get('height'), 2.0) < 1.0
            for (o, x, y) in _edges_from_line(line):
                if ok(x, y) and (x, y) not in blocked:
                    ew = o == 'N'
                    md.add(x, y, 0, (HEDGE_LOW_EW if ew else HEDGE_LOW_NS) if low else (HEDGE_EW if ew else HEDGE_NS))
                    blocked.add((x, y))
            continue
        style = FENCES[_fence_style(kind, tags)]
        for (o, x, y) in _edges_from_line(line):
            if o == 'N':
                if ok(x, y) and ok(x, y - 1):
                    hw[(x, y)] = style
            elif ok(x, y) and ok(x - 1, y):
                vw[(x, y)] = style
    for (x, y) in set(hw) | set(vw):
        n, w = hw.get((x, y)), vw.get((x, y))
        if n and w and n is w:
            md.add(x, y, 0, n[2])
        else:
            if n:
                md.add(x, y, 0, rng.choice(n[0]))
            if w:
                md.add(x, y, 0, rng.choice(w[1]))
    # corner posts where a fence on a square's south edge meets one on its east edge
    for (x, y) in {(x, y - 1) for (x, y) in hw} | {(x - 1, y) for (x, y) in vw}:
        s_, e_ = hw.get((x, y + 1)), vw.get((x + 1, y))
        if s_ and e_ and s_ is e_ and ok(x, y):
            md.add(x, y, 0, s_[3])


def _num(v, default):
    try:
        return float(str(v).split()[0])
    except (TypeError, ValueError):
        return default


def decorate(md, g, rng, keep_clear=()):
    """Trees, bushes, flowerbeds, fences/hedges and street furniture (tagged objects first, then
    plausible fill: lamps, meters on parking streets, hydrants and drains)."""
    G, B = g.G, g.B
    H, W = G.shape
    ground = B == 0
    street = g.street
    rd = ndi.distance_transform_edt(~street)
    bd = ndi.distance_transform_edt(ground)
    blocked = set(keep_clear)
    occupied = set()

    def free(x, y, classes=None):
        return (0 <= x < W and 0 <= y < H and ground[y, x] and not street[y, x] and G[y, x] != T_WATER
                and (x, y) not in blocked and (x, y) not in occupied and (classes is None or G[y, x] in classes))

    def put(x, y, tile):
        md.add(x, y, 0, tile)
        occupied.add((x, y))

    def nearest_free(px, py, r=3, classes=None):
        best = None
        for yy in range(int(py) - r, int(py) + r + 1):
            for xx in range(int(px) - r, int(px) + r + 1):
                if free(xx, yy, classes):
                    d = (xx + 0.5 - px) ** 2 + (yy + 0.5 - py) ** 2
                    if best is None or d < best[0]:
                        best = (d, xx, yy)
        return (best[1], best[2]) if best else None

    def toward_street(x, y, r=5):
        best = None
        for s, (dx, dy) in DIRS.items():
            for k in range(1, r + 1):
                nx, ny = x + dx * k, y + dy * k
                if 0 <= nx < W and 0 <= ny < H and street[ny, nx]:
                    if best is None or k < best[0]:
                        best = (k, s)
                    break
        return best[1] if best else 'S'

    def corner_dirs(x, y):
        """Sidewalk corner square: which two perpendicular sides touch the street?"""
        s = {d for d, (dx, dy) in DIRS.items() if 0 <= x + dx < W and 0 <= y + dy < H and street[y + dy, x + dx]}
        for a, b in (('N', 'W'), ('N', 'E'), ('S', 'W'), ('S', 'E')):
            if a in s and b in s:
                return a + b
        return None

    # ---- tagged objects
    tree_pts = []
    for kind, px, py, tags in g.pois:
        if kind == 'tree':
            tree_pts.append((px, py))
            continue
        if kind == 'crossing':
            continue
        if kind == 'signal':
            # the node sits in the middle of the junction: poles go on up to two sidewalk corners around it
            cx, cy = int(px), int(py)
            cands = [(xx, yy) for yy in range(cy - 12, cy + 13) for xx in range(cx - 12, cx + 13)
                     if free(xx, yy, (T_PAVE, T_PLAZA, T_BRICK)) and corner_dirs(xx, yy)]
            cands.sort(key=lambda q: (q[0] - px) ** 2 + (q[1] - py) ** 2)
            placed = []
            for (xx, yy) in cands:
                if len(placed) >= 2:
                    break
                if all(abs(xx - a) + abs(yy - b) >= 6 for a, b in placed):
                    side = corner_dirs(xx, yy)[len(placed) % 2]         # the two poles face the two streets
                    put(xx, yy, SIGNAL_POLE)
                    md.add(xx, yy, 1, rng.choice(SIGNAL_HEAD[side]))
                    placed.append((xx, yy))
            continue
        paved = (T_PAVE, T_PLAZA, T_BRICK, T_LAWN, T_GRASS)
        sq = nearest_free(px, py, 6 if kind == 'stop' else 3, classes=None if kind in ('bench', 'bin') else paved)
        if not sq:
            continue
        x, y = sq
        face = toward_street(x, y)
        if kind == 'lamp':
            put(x, y, LAMP)
        elif kind == 'stop':
            put(x, y, STOP_SIGN[face])
        elif kind == 'bench' or kind == 'bus_stop':
            placed = False
            for (bx, by) in ((x, y), (x - 1, y), (x, y - 1)):
                tiles = BENCH[face]
                if all(free(bx + dx, by + dy) for dx, dy, _ in tiles):
                    for dx, dy, t in tiles:
                        put(bx + dx, by + dy, t)
                    placed = True
                    break
            if kind == 'bus_stop' and placed:
                b2 = nearest_free(x + 2, y, 2, (T_PAVE, T_PLAZA, T_BRICK))
                if b2:
                    put(b2[0], b2[1], BIN)
        elif kind == 'bin':
            put(x, y, BIN)
        elif kind == 'postbox':
            put(x, y, POSTBOX[face])
        elif kind == 'vending':
            put(x, y, rng.choice(VENDING))

    # ---- trees: tagged, then by land cover, then street trees
    for px, py in tree_pts:
        sq = nearest_free(px, py, 1)
        if sq and bd[sq[1], sq[0]] > 1:
            put(sq[0], sq[1], rng.choice(TREES))
    V = g.V
    nrng = np.random.RandomState(rng.randrange(1 << 30))
    dens = {V_FOREST: 0.11, V_SCRUB: 0.04, V_PARK: 0.025, V_LAWN: 0.006}
    rnd = nrng.random_sample(G.shape)
    for v, p in dens.items():
        m = (V == v) & ground & np.isin(G, [T_GRASS, T_LAWN, T_DIRT]) & (rnd < p) & (bd > 2)
        for y, x in np.argwhere(m):
            x, y = int(x), int(y)
            if free(x, y):
                big = v in (V_PARK, V_FOREST) and rng.random() < 0.15
                put(x, y, JUMBO if big else rng.choice(TREES))
    # bushes / plants in parks, scrub and forest
    m = np.isin(V, [V_PARK, V_SCRUB, V_FOREST]) & ground & np.isin(G, [T_GRASS, T_LAWN]) & (nrng.random_sample(G.shape) < 0.035)
    for y, x in np.argwhere(m):
        x, y = int(x), int(y)
        if free(x, y):
            put(x, y, rng.choice(BUSHES if rng.random() < 0.6 else PLANTS))
    # flowerbeds
    for y, x in np.argwhere((G == T_FLOWERBED) & ground):
        x, y = int(x), int(y)
        if free(x, y) and rng.random() < 0.85:
            put(x, y, rng.choice(FLOWERS))
    # street trees on wide sidewalks beside larger roads
    cand = [tuple(int(v) for v in c[::-1]) for c in np.argwhere((rd >= 2) & (rd < 3.3) & ground & (G == T_PAVE) & (bd > 2.5))]
    rng.shuffle(cand)
    grid = {}
    spacing = 13
    for x, y in cand:
        gx, gy = x // spacing, y // spacing
        if any((gx + dx, gy + dy) in grid and (grid[(gx + dx, gy + dy)][0] - x) ** 2 + (grid[(gx + dx, gy + dy)][1] - y) ** 2 < spacing ** 2
               for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
            continue
        if free(x, y) and rng.random() < 0.5:
            grid[(gx, gy)] = (x, y)
            put(x, y, rng.choice(TREES))

    # ---- litter and garbage bins on downtown sidewalks, at vanilla Louisville's rates (per 100 sidewalk
    # squares: 0.25 piles of trash, 0.11 green garbage bins), thinning out with the town
    town = np.clip((g.coverage - 0.15) / 0.35, 0.0, 1.0)
    side = np.argwhere(ground & ~street & np.isin(G, [T_PAVE, T_PLAZA, T_BRICK]))
    rr = nrng.random_sample(len(side))
    for (y, x), r_ in zip(side, rr):
        x, y = int(x), int(y)
        p = float(town[y, x])
        if r_ < 0.0025 * p and free(x, y):
            put(x, y, rng.choice(LITTER))
        elif r_ < 0.0036 * p and free(x, y) and bd[y, x] <= 1.5:      # bins stand against a wall
            put(x, y, GARBAGE_BIN)

    # ---- fences, walls, hedges
    _place_fences(md, g, blocked | occupied, rng)

    # ---- street lamps where OSM did not give any: downtown a lamp post every ~18 squares; in the suburbs what
    # vanilla's residential streets have, a wooden pole at the curb with its lamp head over the street, one
    # every ~60 squares (583 along 34,850 squares of Riverside, Muldraugh and West Point streets)
    lamps = [(x, y) for (x, y) in occupied if md.squares.get((x, y, 0), [None])[-1] in (LAMP, POLE)]
    suburb = (g.coverage < 0.25) | ((g.landuse == 1) & (g.coverage < 0.4))   # residential land, not downtown
    cand = [tuple(int(v) for v in c[::-1]) for c in np.argwhere((rd >= 1) & (rd < 2.3) & ground & np.isin(G, [T_PAVE, T_PLAZA, T_BRICK, T_LAWN, T_GRASS]) & (bd > 1.5))]
    rng.shuffle(cand)
    for x, y in cand:
        sub = bool(suburb[y, x])
        gap = 60 if sub else 18
        if sub and int(rd[y, x]) != 1:
            continue                                     # the pole stands at the curb
        if not sub and G[y, x] == T_GRASS:
            continue
        if free(x, y) and all((x - lx) ** 2 + (y - ly) ** 2 > gap ** 2 for lx, ly in lamps):
            lamps.append((x, y))
            if sub:
                put(x, y, POLE)
                md.add(x, y, 1, POLE_HEAD[toward_street(x, y)])
            else:
                put(x, y, LAMP)

    # ---- parking meters along streets with on-street parking
    for rdd in g.roads:
        if not rdd.get('parking'):
            continue
        line = rdd['line']
        step = 0
        for (xa, ya), (xb, yb) in zip(line, line[1:]):
            L = math.hypot(xb - xa, yb - ya)
            if L < 1:
                continue
            ux, uy = (xb - xa) / L, (yb - ya) / L
            nx, ny = -uy, ux
            k = 0.0
            while k < L:
                px, py = xa + ux * k, ya + uy * k
                off = rdd['w'] / 2 + 1.5
                side = 1 if (step // 1) % 2 == 0 else -1
                mx, my = int(px + nx * off * side), int(py + ny * off * side)
                if free(mx, my, (T_PAVE, T_PLAZA, T_BRICK)) and rd[my, mx] >= 1 and rd[my, mx] < 2.5:
                    put(mx, my, METER[toward_street(mx, my)])
                k += 7
                step += 1

    # ---- hydrants and street-name signs at some junction corners, drains along curbs
    corners = [tuple(int(v) for v in c[::-1]) for c in np.argwhere((rd >= 1) & (rd < 1.5) & ground & (G == T_PAVE))]
    rng.shuffle(corners)
    hyd = []
    for x, y in corners:
        if not free(x, y):
            continue
        cd = corner_dirs(x, y)
        if cd and all((x - a) ** 2 + (y - b) ** 2 > 14 ** 2 for a, b in hyd):
            r = rng.random()
            if r < 0.35:
                put(x, y, HYDRANT)
                hyd.append((x, y))
            elif r < 0.6:
                put(x, y, STREET_SIGN)
                hyd.append((x, y))
    m = (rd >= 1) & (rd < 1.5) & ground & (G == T_PAVE) & (nrng.random_sample(G.shape) < 0.012)
    for y, x in np.argwhere(m):
        x, y = int(x), int(y)
        if free(x, y):
            put(x, y, rng.choice(DRAINS))
    m = street & (rd == 0) & (nrng.random_sample(G.shape) < 0.0025) & ground
    for y, x in np.argwhere(m):
        x, y = int(x), int(y)
        if (x, y) not in occupied and not any(t.startswith('street_trafficlines') for t in md.squares.get((x, y, 0), [])):
            put(x, y, MANHOLE)


# ---------------------------------------------------------------------------------- zombie density
# Share of chunks at each intensity in vanilla's downtown: the 12 densest Louisville cells (12,288
# chunks, mean 2.18). The 2.7% of vanilla chunks at 5-7 are folded into 4, so nothing here goes above
# what an ordinary downtown Louisville block has; the mean of this profile is 2.12.
DOWNTOWN_PROFILE = ((0, 0.128), (1, 0.132), (2, 0.427), (3, 0.121), (4, 0.192))
# How far above / below an average downtown building vanilla puts each kind of room (mean intensity of
# the chunks holding that room type, minus 2.4), for the kinds a generated town can contain.
ROOM_HEAT = {'policeoffice': 3.2, 'storageunit': 2.0, 'officestorage': 1.9, 'medclinic': 1.6, 'library': 1.5,
             'medical': 1.4, 'breakroom': 1.4, 'pharmacystorage': 1.3, 'conveniencestore': 0.9, 'Gallery': 0.9,
             'pharmacy': 0.9, 'classroom': 0.6, 'bakery': 0.6, 'giftstore': 0.55, 'hall': 0.4, 'garage': 0.45,
             'office': 0.2, 'cafe': 0.2, 'bar': 0.2, 'motelroom': 0.2, 'restaurantdining': -0.3, 'lobby': 0.0,
             'church': -0.1, 'clothesstore': -0.2, 'bank': -0.2, 'bathroom': -0.3, 'bedroom': -0.3,
             'kitchen': -0.5, 'livingroom': -0.5, 'theatre': -0.9, 'bookstore': -0.9, 'shoestore': -1.2,
             'gym': -1.3, 'toolstore': -1.5}
BAR_BONUS = 0.8          # ours, not vanilla's: the bar strip is where this map's crowd is
DENSEST_VANILLA_CELL = 2.69    # mean intensity of cell 50,5 (downtown Louisville), the highest on the map


def zombie_density(md, g, infos=None, rng=None, level=1.0):
    """Per-chunk zombie intensity with the spread of a vanilla downtown.

    Every chunk gets a heat score from what stands on it, the way vanilla's own map behaves: height
    matters most (vanilla: 2.4 for 1-2 floors, 3.0 from 3, 3.5 from 6, 3.9 for towers), then the kind
    of rooms, with parks, water and building sites at the bottom and the map's edge fading towards the
    forest around it. Scores are smoothed into neighbourhoods. Then chunks are ranked and handed
    intensities in exactly the proportions of DOWNTOWN_PROFILE, so the totals match downtown
    Louisville whatever the town looks like, and any cell that would still average more than
    vanilla's densest cell is cooled down to it. `level` scales the result (0.5 = half as many)."""
    G, B = g.G, g.B
    H, W = G.shape
    ny, nx = H // 8, W // 8

    def per_chunk(a):
        return a[:ny * 8, :nx * 8].reshape(ny, 8, nx, 8).mean(axis=(1, 3))

    built = per_chunk((B > 0).astype(np.float32))
    water = per_chunk((G == T_WATER) & (B == 0))
    green = per_chunk(np.isin(G, [T_GRASS, T_LAWN, T_FLOWERBED]) & (B == 0))
    gravel = per_chunk(np.isin(G, [T_GRAVEL, T_DIRT, T_SAND]) & (B == 0))
    tall = np.zeros((ny, nx), np.float32)
    use = np.full((ny, nx), np.nan, np.float32)
    for info in (infos or {}).values():
        lab = info['lab']
        ys, xs = np.nonzero(lab)
        cy, cx = (ys + info['oy']) // 8, (xs + info['ox']) // 8
        ok = (cy < ny) & (cx < nx)
        np.maximum.at(tall, (cy[ok], cx[ok]), info.get('tall', info['levels']))
        names = info['names'][0]
        heat = np.array([0.0] + [ROOM_HEAT.get(names.get(r), 0.0) + (BAR_BONUS if names.get(r) == 'bar' else 0.0)
                                 for r in range(1, int(lab.max()) + 1)], np.float32)
        np.fmax.at(use, (cy[ok], cx[ok]), heat[lab[ys, xs]][ok])      # the hottest room in a chunk decides
    use = np.nan_to_num(use)
    height = np.select([tall >= 10, tall >= 6, tall >= 4, tall >= 3, tall >= 1], [2.4, 2.0, 1.5, 1.0, 0.3], 0.0)
    score = (height + use) * np.minimum(1.0, built / 0.4)
    open_ = built < 0.02
    score[open_ & (gravel > 0.5)] = -1.5
    score[open_ & (green > 0.5)] = -2.0
    score[water > 0.5] = -4.0
    # the town stops at the edge of the map: let it thin out over the last few chunks
    yy, xx = np.mgrid[0:ny, 0:nx]
    edge = np.minimum.reduce([yy, xx, ny - 1 - yy, nx - 1 - xx])
    score -= np.select([edge == 0, edge == 1, edge == 2], [1.5, 1.0, 0.5], 0.0)
    # vanilla paints by neighbourhood, not by building: its street chunks are as busy as the blocks beside
    # them. So most of a chunk's score is that of its surroundings (~3 chunks around), the rest its own.
    score = 0.65 * ndi.gaussian_filter(score, 2.0, mode='nearest') + 0.35 * ndi.uniform_filter(score, 3, mode='nearest')
    # vanilla's map is painted in broad strokes that no building explains: quieter and busier quarters
    nrng = np.random.RandomState((rng.randrange(1 << 30) if rng else 12345))
    noise = ndi.gaussian_filter(nrng.standard_normal((ny, nx)), 3.0, mode='nearest')
    score += 0.7 * noise / (noise.std() or 1.0)
    # rank the chunks and deal out the vanilla proportions, coldest first
    order = np.argsort(score, axis=None, kind='stable')
    n = order.size
    want = np.zeros(n)
    start = 0.0
    for value, share in DOWNTOWN_PROFILE:
        want[int(round(start * n)):int(round((start + share) * n))] = value
        start += share
    want[int(round(start * n)):] = DOWNTOWN_PROFILE[-1][0]
    target = np.cumsum(want * level)                       # running total the ranked chunks should reach
    out = np.diff(np.concatenate([[0.0], np.round(target)]))
    out = np.clip(out, 0, 10).astype(np.uint8)
    vals = np.zeros(n, np.uint8)
    vals[order] = out
    vals = vals.reshape(ny, nx)
    # no single cell may average more than vanilla's densest one: cool its mildest hot chunks until it fits
    cap = DENSEST_VANILLA_CELL * level
    for j in range(0, ny, 32):
        for i in range(0, nx, 32):
            cell, sc = vals[j:j + 32, i:i + 32], score[j:j + 32, i:i + 32]
            while cell.mean() > cap and cell.max() > 0:
                top = np.argwhere(cell == cell.max())
                y, x = min(top, key=lambda q: sc[q[0], q[1]])
                cell[y, x] -= 1
    for cy in range(ny):
        for cx in range(nx):
            md.zombie[(cx, cy)] = int(vals[cy, cx])
    return vals


# ---------------------------------------------------------------------------------- fuel stations
# Vanilla pump islands (measured on 43 vanilla stations): a two-square pump, facing south (the pair runs
# east-west) or east (the pair runs north-south), standing on a curb.
PUMPS = {'gas2go': {'S': ('location_shop_gas2go_01_15', 'location_shop_gas2go_01_14'),
                    'E': ('location_shop_gas2go_01_13', 'location_shop_gas2go_01_12')},
         'fossoil': {'S': ('location_shop_fossoil_01_15', 'location_shop_fossoil_01_14'),
                     'E': ('location_shop_fossoil_01_13', 'location_shop_fossoil_01_12')}}


def place_fuel(md, g, feats, rng):
    """Pump islands on the forecourt of every mapped fuel station (amenity=fuel, area or point)."""
    from PIL import Image, ImageDraw
    H, W = g.G.shape
    done = 0
    every = []                                       # islands of all stations (a station mapped twice, as area and point)
    for f in feats:
        if f.tags.get('amenity') != 'fuel':
            continue
        area = np.zeros((H, W), bool)
        if f.rings:
            im = Image.new('L', (W, H), 0)
            for outer, _ in f.rings:
                ImageDraw.Draw(im).polygon(outer, fill=1)
            area = np.array(im, bool)
            cx, cy = np.mean([p[0] for p in f.rings[0][0]]), np.mean([p[1] for p in f.rings[0][0]])
        elif f.pt:
            cx, cy = f.pt
            yy, xx = np.ogrid[:H, :W]
            area = (xx - cx) ** 2 + (yy - cy) ** 2 <= 18 ** 2
        else:
            continue
        fc = next((sq for ff, sq in getattr(g, 'forecourts', ()) if ff is f), None)
        if fc is not None and len(fc[0]):                # the paved forecourt Ground laid for it
            area = np.zeros((H, W), bool)
            area[fc] = True
        ok = area & (g.B == 0) & ~g.street & np.isin(g.G, [T_PAVE, T_ASPHALT, T_CONCRETE, T_PLAZA])
        brand = rng.choice(sorted(PUMPS))
        facing = 'S' if rng.random() < 0.5 else 'E'
        cand = sorted(zip(*np.nonzero(ok)), key=lambda p: (p[1] - cx) ** 2 + (p[0] - cy) ** 2)
        placed = []
        for y, x in cand:
            if len(placed) >= 4:
                break
            sq = [(x, y), (x + 1, y)] if facing == 'S' else [(x, y), (x, y + 1)]
            box = [(x + dx, y + dy) for dx in range(-2, 4) for dy in range(-2, 4)]
            if not all(0 <= bx < W and 0 <= by < H and ok[by, bx] for bx, by in box):
                continue
            if any(abs(x - px) < 6 and abs(y - py) < 6 for px, py in every):
                continue
            for (sx, sy), t in zip(sq, PUMPS[brand][facing]):
                md.add(int(sx), int(sy), 0, 'street_curbs_01_8')
                md.add(int(sx), int(sy), 0, t)
            placed.append((x, y))
            every.append((x, y))
        done += len(placed)
    return done
