"""Grime, as the vanilla map has it: a wall or floor overlay on most walls and many floors, so nothing looks
freshly built. Rates measured on vanilla (wall tiles carrying an overlay_grime_wall, floor squares carrying an
overlay_grime_floor):

                         downtown Louisville      small towns (Muldraugh, West Point)
  inside walls, ground          0.92               0.38 shops   0.19 houses
  inside walls, upstairs        0.75               0.27
  outside walls, ground         0.86               0.92 shops   0.97 houses
  outside walls, upstairs       0.22               0.08
  room floors                   0.24               0.15 shops   0.09 houses
  sidewalks                     0.20               -
  roads                         0.09               0.00

Between town and downtown the rate follows how built up the neighbourhood is (ground.coverage). Wall grime
comes in sets like walls do (west, north, corner): the heavy set at 16-18 on most walls, the light set at
0-2, and a streaked one at 26/27; floor grime is mostly 28/29, some 0/1 and 30.
"""
import random
import numpy as np

import catalog

WALL_SETS = [((16, 17, 18), 0.72), ((0, 1, 2), 0.2), ((26, 27, None), 0.08)]
FLOOR_TILES = [(28, 0.38), (29, 0.38), (0, 0.1), (1, 0.1), (30, 0.04)]
RES = {'bedroom', 'livingroom', 'kitchen', 'bathroom', 'closet', 'kidsbedroom', 'diningroom', 'garage', 'laundry',
       'attic', 'garagestorage'}
#              downtown, town shop, town house
RATES = {'in0': (0.92, 0.38, 0.19), 'in1': (0.75, 0.27, 0.19), 'out0': (0.86, 0.92, 0.97), 'out1': (0.22, 0.08, 0.0),
         'floor': (0.24, 0.15, 0.09), 'sidewalk': (0.20, 0.0, 0.0), 'road': (0.09, 0.0, 0.0)}


def _rate(key, town, house):
    d, s, h = RATES[key]
    base = h if house else s
    return base + (d - base) * town


def weather(md, g, seed=1):
    """Add grime overlays to the generated part of the map (vanilla squares of the connector keep theirs)."""
    from ground import T_PAVE, T_PLAZA, T_BRICK
    rng = random.Random(seed * 101 + 5)
    cat = catalog.load()
    H, W = g.G.shape
    # how downtown a spot is: 0 in a small town, 1 in a downtown as dense as Louisville's
    town = np.clip((g.coverage - 0.15) / 0.35, 0.0, 1.0)
    rooms = md.rooms
    kind = {}

    def wall_roles(t):
        k = kind.get(t)
        if k is None:
            p = cat.get(t, {})
            k = ''
            if t.startswith('walls_') and not t.startswith(('walls_commercial', 'walls_exterior_house_low')) and \
                    not any(x in p for x in ('DoorWallN', 'DoorWallW', 'WindowN', 'WindowW', 'doorFrN', 'doorFrW')):
                if 'WallNW' in p or 'CornerNorthWall' in p:
                    k = 'NW'
                elif 'WallN' in p:
                    k = 'N'
                elif 'WallW' in p:
                    k = 'W'
            kind[t] = k
        return k

    def pick_wall(side):
        r = rng.random()
        for tiles, w in WALL_SETS:
            if r < w:
                break
            r -= w
        if side == 'NW':
            if tiles[2] is None:
                tiles = WALL_SETS[0][0]
            return ['overlay_grime_wall_01_%d' % tiles[2]]
        return ['overlay_grime_wall_01_%d' % tiles[0 if side == 'W' else 1]]

    def pick_floor():
        r = rng.random()
        for i, w in FLOOR_TILES:
            if r < w:
                return 'overlay_grime_floor_01_%d' % i
            r -= w
        return 'overlay_grime_floor_01_28'

    walls = floors = 0
    for (x, y, z), tl in md.squares.items():
        if not tl or not (0 <= x < W and 0 <= y < H) or any(t.startswith('overlay_grime') for t in tl):
            continue
        t_ = float(town[y, x])
        ri = md.room_of.get((x, y, z))
        nb = ri if ri is not None else md.room_of.get((x, y - 1, z), md.room_of.get((x - 1, y, z)))
        house = nb is not None and rooms[nb].name in RES
        # walls: one overlay for the wall tile this square holds
        side, at = '', -1
        for i, t in enumerate(tl):
            r_ = wall_roles(t)
            if r_:
                side, at = r_, i
        if side:
            key = ('in' if ri is not None else 'out') + ('0' if z == 0 else '1')
            if (ri is not None or nb is not None) and rng.random() < _rate(key, t_, house):
                tl[at + 1:at + 1] = pick_wall(side)            # right on the wall, under what stands there
                walls += 1
        # floors
        f = tl[0]
        if not f.startswith(('floors_', 'blends_street', 'street_')):
            continue
        if ri is not None:
            p = _rate('floor', t_, house)
        elif z == 0 and g.street[y, x]:
            p = _rate('road', t_, False)
        elif z == 0 and g.G[y, x] in (T_PAVE, T_PLAZA, T_BRICK) and g.B[y, x] == 0:
            p = _rate('sidewalk', t_, False)
        else:
            continue
        if p and rng.random() < p:
            tl.insert(1, pick_floor())
            floors += 1
    print('grime: %d walls, %d floors' % (walls, floors))
