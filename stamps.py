"""Whole vanilla buildings, copied in place of real ones.

  python stamps.py        cut every building out of your copy of the vanilla map -> cache/vanilla_buildings.pkl.gz

A real building is not generated: it is replaced by the vanilla building closest to it (same use, a footprint that
fits its lot, its door facing the same street), so every wall, room, piece of furniture, curtain and roof is one
The Indie Stone placed by hand. Each copy keeps:
  * every tile of every square of its rooms on every floor (floors, walls, doors, windows, furniture, decor),
  * the walls, windows, doors, steps and roofs on the squares just outside them (a south or east wall stands on the
    next square; eaves hang over), and everything outside them on the upper floors (balconies, outside stairs),
  * its rooms (names, so vanilla's loot) and their meta objects.
Nothing of the ground round it (grass, paths, fences, cars, trees) comes along: that is the real place's.

Which way it faces is read from the vanilla map: the side with an outside door nearest a road. Buildings that cross
a cell border, or stand too close to another building to cut out cleanly, are left out.
"""
import collections, glob, gzip, os, pickle, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cache', 'vanilla_buildings.pkl.gz')
M = 2                       # squares round the footprint searched for its walls and eaves
OUTSIDE_KEEP = ('walls_', 'roofs_', 'fixtures_windows', 'fixtures_doors', 'lighting_outdoor', 'fixtures_overhead',
                'fixtures_stairs', 'fixtures_railings')      # on the ground; above it, everything (balconies, stairs)
# a house's yard comes with it: the hard ground of its lot (front path, patio, deck, gravel) and what stands in
# it (plants, trees, garden beds, patio furniture, the grill, the clothesline, play things, bins, the mailbox).
# Not its fences (vanilla's lot lines are not the real ones) and nothing of any other building.
YARD_GROUND = ('floors_exterior_tilesandstone_01', 'floors_exterior_natural_01', 'carpentry_02', 'recreational_sports_01')
YARD_KEEP = ('vegetation_', 'f_bushes', 'f_flowerbed', 'd_plants', 'e_', 'furniture_seating_outdoor',
             'furniture_tables_high', 'appliances_cooking', 'appliances_laundry', 'camping_', 'trash_',
             'trashcontainers_', 'location_community_park', 'recreational_', 'boulders', 'street_decoration',
             'fixtures_railings', 'carpentry_03', 'furniture_outdoor', 'location_garden')
YARD_R = 14                 # how far from the house its lot reaches (nearer it than to any other building)
RES = {'bedroom', 'livingroom', 'kitchen', 'bathroom', 'hall', 'closet', 'kidsbedroom', 'diningroom', 'laundry',
       'empty', 'storage', 'attic', 'garage', 'hallway', 'shed', 'emptyoutside', 'basement', 'garagestorage',
       'office', 'utility', 'pantry', 'storageunit'}
SERVICE = {'storage', 'bathroom', 'hall', 'hallway', 'office', 'kitchen', 'closet', 'janitor', 'empty',
           'emptyoutside', 'breakroom', 'stairwell', 'elevator', 'security', 'lobby', 'restroom'}


def use_of(rooms):
    """What a vanilla building is, from its rooms (name, squares): the vocabulary of buildings.py's uses."""
    names = {n for n, _ in rooms}
    area = collections.Counter()
    for n, a in rooms:
        area[n] += a
    if names <= RES:
        kitchens = sum(1 for n, _ in rooms if n == 'kitchen')
        return 'apartments' if kitchens >= 3 else ('garage' if names <= {'garage', 'garagestorage', 'storage', 'empty'}
                                                   else 'shed' if names <= {'shed', 'storage', 'empty'} else 'house')
    has = lambda *ws: any(any(w in n for w in ws) for n in names)
    if has('classroom', 'school'):
        return 'school'
    if has('gasstore'):
        return 'gas'
    if has('church'):
        return 'church'
    if has('police'):
        return 'police'
    if has('fire'):
        return 'fire'
    if has('medclinic', 'clinic', 'medical', 'dentist', 'hospital'):
        return 'clinic'
    if has('bank'):
        return 'bank'
    if has('library'):
        return 'library'
    if has('theatre', 'cinema'):
        return 'theatre'
    if has('motel', 'hotel'):
        return 'hotel'
    main = max((n for n in area if n not in SERVICE), key=lambda n: area[n], default=None)
    if main is None:
        return 'office' if 'office' in names else 'warehouse'
    if any(w in main for w in ('bar',)) and 'barbershop' not in main:
        return 'bar'
    if any(w in main for w in ('dining', 'restaurant', 'burger', 'pizza', 'spiffo', 'diner', 'chinese', 'jays',
                               'fastfood', 'seafood', 'italian', 'mexican', 'kitchen')):
        return 'restaurant'
    if any(w in main for w in ('cafe', 'icecream', 'donut', 'bakery', 'coffee')):
        return 'cafe'
    if any(w in main for w in ('warehouse', 'factory', 'storageunit', 'garagestorage', 'mechanic', 'garage')):
        return 'warehouse'
    if any(w in main for w in ('office',)):
        return 'office'
    if any(w in main for w in ('grocery', 'gigamart', 'market', 'produce', 'butcher')):
        return 'market'
    return 'retail'


def harvest(vanilla_dir=None, out=OUT):
    from lotreader import rd_header, rd_pack, iter_squares
    import survey
    import numpy as np
    from scipy import ndimage as ndi
    vanilla_dir = vanilla_dir or os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
    cat = catalog.load(os.path.join(HERE, 'cache', 'catalog.json'))
    dn = {t for t, p in cat.items() if any(k in p for k in ('doorN', 'DoorWallN', 'doorFrN'))}
    dw = {t for t, p in cat.items() if any(k in p for k in ('doorW', 'DoorWallW', 'doorFrW'))}
    tid, names = {}, []

    def ids(tl):
        out_ = []
        for t in tl:
            if t not in tid:
                tid[t] = len(names)
                names.append(t)
            out_.append(tid[t])
        return tuple(out_)

    found = {}                                   # key -> stamp (identical buildings counted once)
    cells = sorted(glob.glob(os.path.join(vanilla_dir, '*.lotheader')))
    for ci, hp in enumerate(cells):
        h = rd_header(hp)
        if not h['rooms']:
            continue
        cell = os.path.basename(hp)[:-10]
        try:
            ver, n, offs, pack = rd_pack(os.path.join(vanilla_dir, 'world_%s.lotpack' % cell))
        except Exception:
            continue
        sq = {}
        cls = np.zeros((256, 256), np.uint8)
        for x, y, z, t in iter_squares(h, pack, offs, n):
            sq[(x, y, z)] = t
            if z == 0 and t:
                cls[y, x] = survey.classify(t)
        rooms = h['rooms']
        lab = np.zeros((256, 256), np.int32)            # which building's lot every square is in
        for bi, bl in enumerate(h['blds']):
            for name, lvl, rects, _ in (rooms[i] for i in bl):
                if lvl == 0:
                    for (x, y, w, hh) in rects:
                        lab[y:y + hh, x:x + w] = bi + 1
        ldist, (liy, lix) = ndi.distance_transform_edt(lab == 0, return_indices=True)
        lot_of = lab[liy, lix]
        lot_of[(ldist > YARD_R) | (lab > 0) | (cls == survey.C_ROAD)] = 0
        blds = []
        for bi, bl in enumerate(h['blds']):
            rs = [rooms[i] for i in bl]
            if not rs or min(r[1] for r in rs) < 0:
                continue
            squares = collections.defaultdict(set)          # level -> squares of its rooms
            for name, lvl, rects, _ in rs:
                for (x, y, w, hh) in rects:
                    squares[lvl].update((x + i, y + j) for i in range(w) for j in range(hh))
            foot = squares.get(0)
            if not foot:
                continue
            allsq = set().union(*squares.values())
            xs = [p[0] for p in allsq]
            ys = [p[1] for p in allsq]
            blds.append((min(xs), max(xs), min(ys), max(ys), rs, squares, foot, bi + 1))
        for i, (x0, x1, y0, y1, rs, squares, foot, lot_id) in enumerate(blds):
            if x0 - M < 0 or y0 - M < 0 or x1 + M > 255 or y1 + M > 255:
                continue                                    # crosses into the next cell
            if any(not (ox1 < x0 - M - 1 or ox0 > x1 + M + 1 or oy1 < y0 - M - 1 or oy0 > y1 + M + 1)
                   for j, (ox0, ox1, oy0, oy1, *_r) in enumerate(blds) if j != i):
                continue                                    # too close to another building to cut out cleanly
            if len(foot) < 12:
                continue
            use = use_of([(r[0], sum(w * hh for (_, _, w, hh) in r[2])) for r in rs])
            # outside doors by side, and the road beyond each side: the front is the door side nearest a road
            doors = collections.defaultdict(list)
            for (x, y) in foot:
                for t in sq.get((x, y, 0), ()):
                    if t in dn and (x, y - 1) not in foot:
                        doors['N'].append((x, y))
                    if t in dw and (x - 1, y) not in foot:
                        doors['W'].append((x, y))
                for t in sq.get((x, y + 1, 0), ()):
                    if t in dn and (x, y + 1) not in foot:
                        doors['S'].append((x, y))
                for t in sq.get((x + 1, y, 0), ()):
                    if t in dw and (x + 1, y) not in foot:
                        doors['E'].append((x, y))
            if not doors:
                continue
            fx0, fx1 = min(p[0] for p in foot), max(p[0] for p in foot)
            fy0, fy1 = min(p[1] for p in foot), max(p[1] for p in foot)
            band = {'N': cls[max(0, fy0 - 25):max(0, fy0 - 1), fx0:fx1 + 1],
                    'S': cls[fy1 + 2:fy1 + 26, fx0:fx1 + 1],
                    'W': cls[fy0:fy1 + 1, max(0, fx0 - 25):max(0, fx0 - 1)],
                    'E': cls[fy0:fy1 + 1, fx1 + 2:fx1 + 26]}
            road = {s: int(np.isin(b, [survey.C_ROAD]).sum()) for s, b in band.items()}
            front = max(doors, key=lambda s: (road.get(s, 0), len(doors[s])))
            if road.get(front, 0) == 0:
                continue                                    # no road beyond any door: cannot tell its front
            # the copy: everything in its rooms, walls / windows / doors / roofs just outside, the roof above
            top = max(squares)
            data = []
            for z in range(0, top + 7):
                inside = squares.get(z, set())
                for x in range(x0 - M, x1 + M + 1):
                    for y in range(y0 - M, y1 + M + 1):
                        tl = sq.get((x, y, z))
                        if not tl:
                            continue
                        if (x, y) in inside:
                            data.append((x - fx0, y - fy0, z, 1, ids(tl)))
                        else:
                            keep = list(tl) if z >= 1 else [t for t in tl if t.startswith(OUTSIDE_KEEP)]
                            if keep:
                                data.append((x - fx0, y - fy0, z, 0, ids(keep)))
            rooms_out = [(name, lvl, [(x - fx0, y - fy0, w, hh) for (x, y, w, hh) in rects],
                          [(t, x - fx0, y - fy0) for (t, x, y) in objs]) for name, lvl, rects, objs in rs]
            yard = []
            if use == 'house':
                ys_, xs_ = np.nonzero(lot_of == lot_id)
                for y, x in zip(ys_.tolist(), xs_.tolist()):
                    tl = sq.get((x, y, 0), ())
                    if not tl:
                        continue
                    g_ = tl[0] if tl[0].startswith(YARD_GROUND) else None
                    ob = [t for t in tl[1:] if t.startswith(YARD_KEEP)]
                    if g_ or ob:
                        yard.append((x - fx0, y - fy0, ids([g_])[0] if g_ else -1, ids(ob)))
            fd = doors[front][len(doors[front]) // 2]
            key = (tuple(sorted(data)), front)
            if key in found:
                found[key]['n'] += 1
                continue
            found[key] = dict(use=use, front=front, w=fx1 - fx0 + 1, h=fy1 - fy0 + 1, levels=top + 1,
                              foot=sorted((x - fx0, y - fy0) for x, y in foot), data=data, rooms=rooms_out,
                              door=(fd[0] - fx0, fd[1] - fy0), n=1, cell=cell, yard=yard)
        if ci % 100 == 0:
            print('  %d / %d cells, %d buildings' % (ci, len(cells), len(found)), flush=True)
    lib = dict(tiles=names, stamps=list(found.values()))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with gzip.open(out, 'wb') as fh:
        pickle.dump(lib, fh, protocol=4)
    c = collections.Counter(s['use'] for s in lib['stamps'])
    print('%d vanilla buildings (%d copies): %s' % (len(lib['stamps']), sum(s['n'] for s in lib['stamps']),
                                                    dict(c.most_common())))
    return lib


def shape_of(st):
    """Masks of a copy in one frame (offset 'eo' from its footprint corner): 'ea' everything it covers (walls,
    eaves, roof, balconies), 'rad' every floor's rooms grown by a square (its walls need that room), 'fm' the
    ground floor's rooms in the footprint's own frame."""
    import numpy as np
    from scipy import ndimage as ndi
    ext = {(d[0], d[1]) for d in st['data']}
    rooms = {(x + i, y + j) for _, _, rects, _ in st['rooms'] for (x, y, w, h) in rects for i in range(w) for j in range(h)}
    pts = ext | {(x + a, y + b) for x, y in rooms for a in (-1, 0, 1) for b in (-1, 0, 1)}
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    ea = np.zeros((y1 - y0 + 1, x1 - x0 + 1), bool)
    ra = np.zeros_like(ea)
    for x, y in ext:
        ea[y - y0, x - x0] = True
    for x, y in rooms:
        ra[y - y0, x - x0] = True
    st['ea'], st['rad'], st['eo'] = ea, ndi.binary_dilation(ra, structure=np.ones((3, 3), bool)), (x0, y0)
    fm = np.zeros((st['h'], st['w']), bool)
    for (dx, dy) in st['foot']:
        fm[dy, dx] = True
    st['fm'] = fm


_lib = None


def load():
    global _lib
    if _lib is None:
        if not os.path.isfile(OUT):
            harvest()
        with gzip.open(OUT, 'rb') as fh:
            _lib = pickle.load(fh)
        _lib['by_use'] = collections.defaultdict(list)
        for s in _lib['stamps']:
            s['footset'] = set(map(tuple, s['foot']))
            _lib['by_use'][s['use']].append(s)
    return _lib


if __name__ == '__main__':
    harvest()
