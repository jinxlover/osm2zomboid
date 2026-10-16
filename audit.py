"""Sanity audit of the rooms of a map, generated or vanilla, against what the vanilla map does.

  python audit.py <maps folder> [CX0 CY0 CX1 CY1] [--examples N]

Every check is a rule the vanilla map keeps (run it on a vanilla block to see its own rates):
  dead room          a room no door, opening or staircase leads into
  unreachable        a room you cannot walk to from the street (doors, open walls, stairs)
  door to nowhere    an outside door on an upper floor with no floor behind it
  blocked door       solid furniture on the square in front of a door
  window inside      a window between two rooms (vanilla: only from a hall into an office)
  missing essential  a room without what nearly every vanilla room of its kind has (a toilet in a bathroom,
                     a bed in a bedroom, a stove in a kitchen), from cache/vanilla_rooms.json
  no containers      a room with nothing to loot, for kinds that vanilla nearly always gives one
  oversized          a room more than 8 times the vanilla median for its kind
  sleeping downstairs a bedroom on the ground floor of a building that also has shops or offices
  hole               a square inside a building's outline that is in no room
Room design (how the furniture stands, which vanilla keeps too; compare a vanilla block's rates):
  till backwards     a register whose customer side is farther from the shop's way in than its cashier side
  toilet at door     a toilet looking straight out of the bathroom door
  boxed-in bed       a bed with no free square beside it to get in from
  window blocked     a tall solid piece (shelves, fridge) standing against a window
"""
import collections, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog
from lotreader import rd_header, rd_pack, iter_squares

HERE = os.path.dirname(os.path.abspath(__file__))
CELL = 256
RES = {'bedroom', 'livingroom', 'kitchen', 'bathroom', 'hall', 'closet', 'kidsbedroom', 'diningroom', 'garage',
       'laundry', 'basement', 'shed', 'empty', 'emptyoutside', 'storage', 'attic', 'hallway', 'garagestorage'}
NO_ACCESS_OK = {'elevator', 'empty', 'emptyoutside', 'attic', 'garbage', 'vault'}
WN, WW, DN, DW, GN, GW, SOLID, STAIRN, STAIRW, TALL = 1, 2, 4, 8, 16, 32, 64, 128, 256, 512
STEP = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}


def load(mapdir, box=None):
    cat = catalog.load(os.path.join(HERE, 'cache', 'catalog.json'))
    fl, names = {}, {}

    def flags(t):
        v = fl.get(t)
        if v is None:
            p = cat.get(t, {})
            v = 0
            v |= WN if any(k in p for k in ('WallN', 'WallNW', 'WallNTrans', 'WallNWTrans')) else 0
            v |= WW if any(k in p for k in ('WallW', 'WallNW', 'WallWTrans', 'WallNWTrans')) else 0
            v |= DN if any(k in p for k in ('doorN', 'DoorWallN', 'doorFrN')) else 0
            v |= DW if any(k in p for k in ('doorW', 'DoorWallW', 'doorFrW')) else 0
            v |= GN if any(k in p for k in ('WindowN', 'windowN')) else 0
            v |= GW if any(k in p for k in ('WindowW', 'windowW')) else 0
            v |= SOLID if ('solid' in p or 'solidtrans' in p) and 'Facing' in p else 0
            v |= STAIRN if 'stairsTN' in p else 0
            v |= STAIRW if 'stairsTW' in p else 0
            v |= TALL if 'solid' in p and 'Facing' in p and 'solidtrans' not in p else 0
            fl[t] = v
            names[t] = (p.get('CustomName'), 'container' in p, p.get('Facing'))
        return v

    sq, rooms, blds = {}, [], []
    for hp in sorted(glob.glob(os.path.join(mapdir, '*.lotheader'))):
        cx, cy = map(int, os.path.basename(hp)[:-10].split('_'))
        if box and not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
            continue
        h = rd_header(hp)
        v, n, offs, pack = rd_pack(os.path.join(mapdir, 'world_%d_%d.lotpack' % (cx, cy)))
        for x, y, z, t in iter_squares(h, pack, offs, n):
            k = (cx * CELL + x, cy * CELL + y, z)
            sq[k] = t
            for tt in t:
                flags(tt)
        base = len(rooms)
        for name, lvl, rects, _ in h['rooms']:
            cells = {(cx * CELL + x + i, cy * CELL + y + j, lvl) for x, y, w, hh in rects for i in range(w) for j in range(hh)}
            rooms.append([name, lvl, cells, None])
        for bl in h['blds']:
            bi = len(blds)
            blds.append([base + i for i in bl])
            for i in bl:
                rooms[base + i][3] = bi
    return sq, rooms, blds, fl, names


def essentials():
    v = json.load(open(os.path.join(HERE, 'cache', 'vanilla_rooms.json')))['rooms']
    ess, cont, med = {}, {}, {}
    for nm, r in v.items():
        if r['n'] < 15:
            continue
        ess[nm] = [c.split('|')[0] for c, a in r['cats'].items() if a['p'] >= 0.8 and not c.startswith('seat@')]
        cont[nm] = max((a['p'] for c, a in r['cats'].items() if c.split('|')[1:2] and c.split('|')[1]), default=0)
        med[nm] = r['area']
    return ess, cont, med


def audit(mapdir, box=None, examples=3):
    sq, rooms, blds, fl, names = load(mapdir, box)
    ess, cont, med = essentials()
    room_of = {}
    for i, r in enumerate(rooms):
        for c in r[2]:
            room_of[c] = i
    issues = collections.defaultdict(list)
    whole = set()
    xs = [k[0] for k in sq] or [0]
    ys = [k[1] for k in sq] or [0]
    X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)

    def f(k):
        v = 0
        for t in sq.get(k, ()):
            v |= fl[t]
        return v
    def edge(a, b):
        """Flags of the edge between two squares side by side on one level."""
        (x, y, z), (x2, y2, _) = a, b
        if x2 == x:
            return f((x, max(y, y2), z)) & (WN | DN | GN)
        return f((max(x, x2), y, z)) & (WW | DW | GW)

    def design(name, z, cells, issues):
        tiles = {c: sq.get(c, ()) for c in cells}
        solid = {c for c, tl in tiles.items() if any(fl[t] & SOLID for t in tl)}
        doors_in = []                                    # room squares just inside a door to another room / outside
        for c in cells:
            for dx, dy in STEP.values():
                nb = (c[0] + dx, c[1] + dy, c[2])
                if nb not in cells and edge(c, nb) & (DN | DW):
                    doors_in.append(c)

        def dist_from_doors():
            d = {c: 0 for c in doors_in if c not in solid}
            todo = list(d)
            while todo:
                nxt = []
                for c in todo:
                    for dx, dy in STEP.values():
                        nb = (c[0] + dx, c[1] + dy, c[2])
                        if nb in cells and nb not in solid and nb not in d and not edge(c, nb):
                            d[nb] = d[c] + 1
                            nxt.append(nb)
                todo = nxt
            return d
        dd = None
        for c, tl in tiles.items():
            for t in tl:
                nm, _, face = names[t]
                if not face or face not in STEP:
                    continue
                dx, dy = STEP[face]
                if nm == 'Register' and doors_in:
                    dd = dd or dist_from_doors()
                    fr, bk = (c[0] + dx, c[1] + dy, c[2]), (c[0] - dx, c[1] - dy, c[2])
                    if fr in dd and bk in dd and dd[bk] < dd[fr]:
                        issues['till backwards'].append((name, c))
                    elif fr in dd and bk in dd and dd[fr] < dd[bk]:
                        issues['_till_ok'].append(1)
                    else:
                        issues['_till_' + ('front blocked' if fr not in dd else 'back blocked' if bk not in dd else 'level')].append(1)
                    issues['_tills'].append(1)
                elif nm == 'Toilet':
                    issues['_toilets'].append(1)
                    cur = c
                    for _ in range(6):
                        nb = (cur[0] + dx, cur[1] + dy, cur[2])
                        e = edge(cur, nb)
                        if e & (DN | DW):
                            issues['toilet at door'].append((name, c))
                            break
                        if e or nb not in cells or nb in solid:
                            break
                        cur = nb
        beds = {c for c, tl in tiles.items() if any(names[t][0] == 'Bed' for t in tl)}
        seen = set()
        for b in beds:
            if b in seen:
                continue
            grp, todo = {b}, [b]
            while todo:
                c = todo.pop()
                for dx, dy in STEP.values():
                    nb = (c[0] + dx, c[1] + dy, c[2])
                    if nb in beds and nb not in grp:
                        grp.add(nb)
                        todo.append(nb)
            seen |= grp
            issues['_beds'].append(1)
            if not any((c[0] + dx, c[1] + dy, c[2]) in cells and (c[0] + dx, c[1] + dy, c[2]) not in solid
                       and (c[0] + dx, c[1] + dy, c[2]) not in beds and not edge(c, (c[0] + dx, c[1] + dy, c[2]))
                       for c in grp for dx, dy in STEP.values()):
                issues['boxed-in bed'].append((name, b))
        for c in cells:
            for dx, dy in STEP.values():
                nb = (c[0] + dx, c[1] + dy, c[2])
                if nb not in cells and edge(c, nb) & (GN | GW):
                    issues['_windows'].append(1)
                    if any(fl[t] & TALL for t in tiles[c]):
                        issues['window blocked'].append((name, c))

    for bi, rl in enumerate(blds):
        if any(not (X0 < c[0] < X1 and Y0 < c[1] < Y1) for i in rl for c in rooms[i][2]):
            continue                                   # runs off the audited area
        whole.add(bi)
        adj = collections.defaultdict(set)
        out = set()
        mixed = any(rooms[i][0] not in RES for i in rl)
        for i in rl:
            name, z, cells, _ = rooms[i]
            for (x, y, zz) in cells:
                for nb, kind in (((x, y - 1, z), f((x, y, z)) & (WN | DN | GN)), ((x - 1, y, z), f((x, y, z)) & (WW | DW | GW)),
                                 ((x, y + 1, z), f((x, y + 1, z)) & (WN | DN | GN)), ((x + 1, y, z), f((x + 1, y, z)) & (WW | DW | GW))):
                    j = room_of.get(nb)
                    door = kind & (DN | DW)
                    opening = kind == 0
                    if j == i:
                        continue
                    if j is not None and rooms[j][3] == bi:
                        if door or opening:
                            adj[i].add(j)
                        elif kind & (GN | GW):
                            issues['window inside'].append((name, rooms[j][0], (x, y, z)))
                    elif j is None:
                        if door or (opening and z == 0):
                            if z == 0:
                                out.add(i)
                            elif door and nb not in sq:
                                issues['door to nowhere'].append((name, (x, y, z)))
                            elif door:
                                out.add(i)                # onto a roof or balcony: counts as a way out
                    if door:                              # what stands in front of the door, on both sides
                        for s in ((x, y, z), nb):
                            if f(s) & SOLID:
                                issues['blocked door'].append((name, s))
                # stairs: the top piece leads to the floor above
                v = f((x, y, z))
                if v & STAIRN:
                    j = room_of.get((x, y - 1, z + 1))
                    if j is not None:
                        adj[i].add(j)
                        adj[j].add(i)
                if v & STAIRW:
                    j = room_of.get((x - 1, y, z + 1))
                    if j is not None:
                        adj[i].add(j)
                        adj[j].add(i)
        seen = set(out)
        todo = list(out)
        while todo:
            r = todo.pop()
            for j in adj[r]:
                if j not in seen:
                    seen.add(j)
                    todo.append(j)
        for i in rl:
            name, z, cells, _ = rooms[i]
            if name in NO_ACCESS_OK:
                continue
            if not adj[i] and i not in out:
                issues['dead room'].append((name, z, min(cells)))
            elif i not in seen:
                issues['unreachable'].append((name, z, min(cells)))
            if mixed and z == 0 and name in ('bedroom', 'kidsbedroom'):
                issues['sleeping downstairs'].append((name, min(cells)))
            if name in med and len(cells) > 8 * max(4, med[name]):
                issues['oversized'].append((name, len(cells), med[name]))
            have = {names[t][0] for c in cells for t in sq.get(c, ())}
            for e in ess.get(name, ()):
                if not any(h and e in h for h in have) and len(cells) >= 4:     # a Round Table is a Table
                    issues['missing essential'].append((name, e, len(cells)))
            if cont.get(name, 0) >= 0.8 and not any(names[t][1] for c in cells for t in sq.get(c, ())):
                issues['no containers'].append((name, len(cells)))
            design(name, z, cells, issues)
        # holes: a square with no room whose four neighbours are all rooms of this building, on any floor
        mine = {c for i in rl for c in rooms[i][2]}
        for (x, y, z) in mine:
            for nx, ny in ((x + 1, y), (x, y + 1)):
                if (nx, ny, z) not in mine and all((nx + dx, ny + dy, z) in mine for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    issues['hole'].append((nx, ny, z))
    nrooms = sum(len(blds[b]) for b in whole)
    return nrooms, len(whole), issues


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    ex = 3
    if '--examples' in sys.argv:
        ex = int(sys.argv[sys.argv.index('--examples') + 1])
        args = [a for a in args if a != str(ex)]
    box = list(map(int, args[1:5])) if len(args) >= 5 else None
    n, nb, issues = audit(args[0], box, ex)
    print('%d rooms in %d buildings' % (n, nb))
    for k in ('dead room', 'unreachable', 'door to nowhere', 'blocked door', 'window inside', 'missing essential',
              'no containers', 'oversized', 'sleeping downstairs', 'hole', 'till backwards', 'toilet at door',
              'boxed-in bed', 'window blocked'):
        v = issues.get(k, [])
        per = {'till backwards': '_tills', 'toilet at door': '_toilets', 'boxed-in bed': '_beds',
               'window blocked': '_windows'}.get(k)
        if per:
            print('  %-20s %6d  (%.1f%% of %d)' % (k, len(v), 100.0 * len(v) / max(1, len(issues.get(per, []))),
                                                 len(issues.get(per, []))))
        else:
            print('  %-20s %6d  (%.2f per 100 rooms)' % (k, len(v), 100.0 * len(v) / max(1, n)))
        if v and ex:
            kinds = collections.Counter(str(x[0]) + ('/' + str(x[1]) if k == 'missing essential' else '') for x in v)
            print('      most:', kinds.most_common(8))
            for x in v[:ex]:
                print('      e.g.', x)
