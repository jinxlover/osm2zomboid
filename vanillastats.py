"""Learn how the vanilla map furnishes rooms, straight from the game's own lot files.

Reads every vanilla cell that has rooms and writes cache/vanilla_rooms.json, which furnish.py uses as its
only source of "what goes in a kitchen". Nothing here is a hand-made list of furniture.

  models[key]  one furniture model, key = "sheet|CustomName|GroupName", with measured habits:
      n        tiles seen                    wall     share placed with a wall at its back
      nearwall share standing next to a wall ontop    share sharing a square with a table / counter
      overlow  share (wall-mounted pieces) hanging above a low piece
      partner  what it usually sits on / hangs above (desk, table, counter, sink ...)
      attable  share (seats) standing beside a table / desk / counter, `host` = which kind
      cont     loot container type           flags    T table, B seat/bed, L low, H high, S solid
      beh      placement behaviour derived from the numbers above (see behaviour()): wall, table, aisle,
               rows, seat, high, ontop, corner, free, switch
      cat      category: models in one category are substitutes (a kitchen has one counter style)
  rooms[name]  one room type:
      n, area (median squares), dens (furniture tiles per square), floors {tile: squares}
      cats[cat] = {p: share of rooms that have it, one: share of those with exactly one,
                   k: pieces per square where present, mean: pieces where present,
                   mix: distinct styles per room, styles {model key: rooms using it}}

Run once:  python vanillastats.py            (about two minutes)
"""
import glob, json, os, sys, time, collections, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog
from lotreader import rd_header, rd_pack, iter_squares

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cache', 'vanilla_rooms.json')
SKIP_SHEETS = ('floors_', 'walls_', 'blends_', 'roofs_', 'fixtures_doors', 'fixtures_windows', 'fixtures_stairs',
               'fixtures_railings', 'overlay_', 'floor_', 'rugs_', 'fixtures_doorframes', 'fixtures_windowframes',
               'walls_detailing', 'fixtures_escalators', 'street_', 'vegetation_trees', 'jumbo_tree', 'signs_',
               'advertising_', 'papernotices', 'trash_', 'brokenglass', 'd_', 'damaged_', 'location_shop_mall_signs',
               'location_shop_accessories_genericsigns', 'constructedobjects_signs', 'crafted_', 'camping_',
               'vegetation_farm', 'vegetation_gard', 'vegetation_dry', 'vegetation_ground', 'vegetation_foliage',
               'vegetation_ornamental', 'fencing_', 'carpentry_02', 'construction_', 'crafting_')
BAD_SUB = ('floor', 'tile', 'sign', 'poster', 'notice', 'certificate', 'window', 'curtain', 'banner', 'neon',
           'decoration', 'mall map', 'customname', 'mannequin', 'graffiti', 'carpet', 'blind', 'light on',
           'cubicle', 'escalator')
BAD_EXACT = {'dish', 'x', 'antenna', 'lights', 'door', 'rug', 'map'}
WALL_N = ('WallN', 'WallNW', 'DoorWallN', 'WindowN', 'WallNTrans')
WALL_W = ('WallW', 'WallNW', 'DoorWallW', 'WindowW', 'WallWTrans')
BACK = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}
STEP = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}


def model_key(name, p):
    return "%s|%s|%s" % (name.rsplit('_', 1)[0], p.get('CustomName', ''), p.get('GroupName', ''))


def is_furniture(name, p):
    if not p or 'CustomName' not in p or name.startswith(SKIP_SHEETS):
        return False
    if 'solidfloor' in p or 'WallN' in p or 'WallW' in p or 'WallNW' in p:
        return False
    cn = p['CustomName'].lower()
    return cn not in BAD_EXACT and not any(b in cn for b in BAD_SUB)


def flags_of(p):
    return ''.join(f for f, k in (('T', 'IsTable'), ('B', 'bed'), ('L', 'IsLow'), ('H', 'IsHigh'), ('S', 'solid'),
                                  ('s', 'solidtrans')) if k in p)


def host_class(custom):
    c = custom.lower()
    for word in ('desk', 'table', 'counter', 'bar', 'piano', 'organ', 'altar'):
        if word in c:
            return 'counter' if word == 'bar' else word
    return c


def category(key, m):
    """Substitutable things share a category: every low kitchen counter, every bed, every hosted chair."""
    sheet, custom, group = key.split('|')
    c = custom
    if m['beh'] == 'seat':
        return 'seat@' + (m.get('host') or 'table')
    if 'T' in m['flags'] and 'table' in custom.lower() and 'low' not in custom.lower():
        c = 'Table'
    return '%s|%s|%s' % (c, m['cont'], m['beh'])


def behaviour(key, m):
    """How a model is placed, decided from how the vanilla map places it."""
    sheet, custom, group = key.split('|')
    cl = custom.lower()
    fl = m['flags']
    if sheet.startswith('lighting_indoor') and cl == 'switch':
        return 'switch'
    if m['ontop'] >= 0.5:
        return 'ontop'                       # sits on a counter / table / desk: sinks, registers, computers, lamps
    if 'B' in fl and not (sheet.startswith('furniture_bedding') or cl == 'bed'):
        if m['attable'] >= 0.4 and m['wall'] < 0.85:
            return 'seat'                    # dining / office chairs, bar stools: placed by their table
        if m['wall'] >= 0.6 or not m['facings']:
            return 'wall'                    # couches, armchairs, toilets
        return 'rows'                        # pews, theatre and waiting-room seats: rows facing one way
    if 'H' in fl:
        return 'high'                        # wall-mounted: cabinets, shelves, boards, hoods
    if m['facings']:
        if m['wall'] >= 0.7:
            return 'wall'
        return 'table' if 'T' in fl else 'aisle'
    if 'T' in fl:
        return 'table'
    return 'corner' if m['nearwall'] >= 0.5 else 'free'


def wall_on(sq_props, x, y, z, side):
    """Is there a wall / door / window on `side` of square (x,y,z)?"""
    if side == 'N':
        return any(k in pr for pr in sq_props.get((x, y, z), ()) for k in WALL_N)
    if side == 'S':
        return any(k in pr for pr in sq_props.get((x, y + 1, z), ()) for k in WALL_N)
    if side == 'W':
        return any(k in pr for pr in sq_props.get((x, y, z), ()) for k in WALL_W)
    return any(k in pr for pr in sq_props.get((x + 1, y, z), ()) for k in WALL_W)


def main(vanilla_dir=None, out=OUT, limit=None):
    cat = catalog.load(os.path.join(HERE, 'cache', 'catalog.json'))
    vanilla_dir = vanilla_dir or os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
    heads = sorted(glob.glob(os.path.join(vanilla_dir, '*.lotheader')))
    models = {}
    inst = []                       # one entry per room instance: (name, area, {key: pieces}, furniture tiles)
    floors = collections.defaultdict(collections.Counter)
    t0 = time.time()
    done = 0
    for hp in heads:
        h = rd_header(hp)
        if not h['rooms']:
            continue
        cell = os.path.basename(hp)[:-10]
        try:
            ver, n, offs, pack = rd_pack(os.path.join(vanilla_dir, 'world_%s.lotpack' % cell))
        except Exception as ex:
            print('skip', cell, ex, file=sys.stderr)
            continue
        sqroom = {}
        base = len(inst)
        for i, (name, lvl, rects, _) in enumerate(h['rooms']):
            area = 0
            whole = True
            for (x, y, w, hh) in rects:
                area += w * hh
                if x < 0 or y < 0 or x + w > 256 or y + hh > 256:
                    whole = False           # runs into the next cell: we only see part of it
                for xx in range(x, x + w):
                    for yy in range(y, y + hh):
                        sqroom[(xx, yy, lvl)] = base + i
            inst.append([name, area if whole else 0, collections.Counter(), 0])
        squares = {(x, y, z): tiles for x, y, z, tiles in iter_squares(h, pack, offs, n)}
        sq_props = {k: [cat.get(t, {}) for t in v] for k, v in squares.items()}
        furn = {}
        for k, tiles in squares.items():
            if k in sqroom:
                fs = [(model_key(t, p), p) for t, p in zip(tiles, sq_props[k]) if is_furniture(t, p)]
                if fs:
                    furn[k] = fs
        for (x, y, z), tiles in squares.items():
            ri = sqroom.get((x, y, z))
            if ri is None:
                continue
            props = sq_props[(x, y, z)]
            if tiles and ('solidfloor' in props[0] or tiles[0].startswith('floors_')):
                floors[inst[ri][0]][tiles[0]] += 1
        for (x, y, z), fs in furn.items():
            room = inst[sqroom[(x, y, z)]]
            anywall = any(wall_on(sq_props, x, y, z, s) for s in 'NSWE')
            for key, p in fs:
                m = models.get(key)
                if m is None:
                    m = models[key] = dict(n=0, wall=0, nearwall=0, ontop=0, overlow=0, attable=0, faced=0,
                                           cont=p.get('container', ''), facings=set(), flags=flags_of(p),
                                           hosts=collections.Counter(), partners=collections.Counter())
                m['n'] += 1
                room[3] += 1
                if 'SpriteGridPos' not in p or p['SpriteGridPos'] == '0,0':
                    room[2][key] += 1
                if anywall:
                    m['nearwall'] += 1
                f = p.get('Facing')
                if f:
                    m['facings'].add(f)
                    m['faced'] += 1
                    back = BACK[f]
                    dx, dy = STEP[back]
                    if wall_on(sq_props, x, y, z, back) or wall_on(sq_props, x + dx, y + dy, z, back):
                        m['wall'] += 1
                others = [(k2, p2) for k2, p2 in fs if k2 != key]
                under = [k2 for k2, p2 in others if 'IsTable' in p2]
                if under:
                    m['ontop'] += 1
                    if 'IsHigh' not in p:
                        m['partners'][host_class(under[0].split('|')[1])] += 1
                low = [k2 for k2, p2 in others if 'IsLow' in p2 or 'IsTable' in p2 or 'IsHigh' not in p2]
                if 'IsHigh' in p and low:
                    m['overlow'] += 1
                    m['partners'][host_class(low[0].split('|')[1])] += 1
                if 'bed' in p:
                    hosts = set()
                    for dx, dy in STEP.values():
                        for k2, p2 in furn.get((x + dx, y + dy, z), ()):
                            if 'IsTable' in p2 and k2 != key:
                                hosts.add(host_class(k2.split('|')[1]))
                    if hosts:
                        m['attable'] += 1
                        for hc in hosts:
                            m['hosts'][hc] += 1
        done += 1
        if limit and done >= limit:
            break
        if done % 100 == 0:
            print(done, 'cells', round(time.time() - t0), 's', flush=True)

    # ---- model habits -> behaviour and category
    out_models = {}
    for k, m in models.items():
        n = m['n']
        o = dict(n=n, wall=round(m['wall'] / m['faced'], 3) if m['faced'] else 0.0,
                 nearwall=round(m['nearwall'] / n, 3), ontop=round(m['ontop'] / n, 3),
                 overlow=round(m['overlow'] / n, 3), attable=round(m['attable'] / n, 3),
                 cont=m['cont'], facings=''.join(sorted(m['facings'])), flags=m['flags'])
        if m['hosts']:
            o['host'] = m['hosts'].most_common(1)[0][0]
        if m['partners']:
            o['partner'] = m['partners'].most_common(1)[0][0]
        o['beh'] = behaviour(k, o)
        o['cat'] = category(k, o)
        out_models[k] = o

    # ---- room types
    by_room = collections.defaultdict(list)
    for name, area, pieces, tiles in inst:
        if area > 0:
            by_room[name].append((area, pieces, tiles))
    out_rooms = {}
    for name, rooms in by_room.items():
        n = len(rooms)
        cats = {}
        for area, pieces, _ in rooms:
            percat = collections.defaultdict(lambda: [0, set()])
            for key, cnt in pieces.items():
                m = out_models[key]
                if m['beh'] == 'switch' or 'corner' in key.split('|')[2].lower():
                    continue
                e = percat[m['cat']]
                e[0] += cnt
                e[1].add(key)
            for c, (cnt, keys) in percat.items():
                a = cats.setdefault(c, dict(rooms=0, one=0, pieces=0, area=0, mix=0, styles=collections.Counter()))
                a['rooms'] += 1
                a['one'] += cnt == 1
                a['pieces'] += cnt
                a['area'] += area
                a['mix'] += len(keys)
                for key in keys:
                    a['styles'][key] += 1
        keep = {}
        for c, a in cats.items():
            if a['rooms'] < max(3, 0.02 * n):
                continue
            keep[c] = dict(p=round(a['rooms'] / n, 3), one=round(a['one'] / a['rooms'], 3),
                           k=round(a['pieces'] / a['area'], 4), mean=round(a['pieces'] / a['rooms'], 2),
                           mix=round(a['mix'] / a['rooms'], 2), styles=dict(a['styles'].most_common(12)))
        tot_area = sum(r[0] for r in rooms)
        out_rooms[name] = dict(n=n, area=statistics.median(r[0] for r in rooms),
                               dens=round(sum(r[2] for r in rooms) / tot_area, 3),
                               floors=dict(floors[name].most_common(8)), cats=keep)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({'rooms': out_rooms, 'models': out_models}, open(out, 'w'), separators=(',', ':'))
    print('wrote', out, len(out_rooms), 'room types', len(out_models), 'models', round(time.time() - t0), 's')


if __name__ == '__main__':
    main(limit=int(sys.argv[1]) if len(sys.argv) > 1 else None)
