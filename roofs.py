"""Roof equipment the way the vanilla map has it.

  python roofs.py            measure the game's flat commercial roofs -> cache/vanilla_roofs.json

What stands on a vanilla flat roof is not scattered tile by tile: an air conditioner is three sprites on
three squares, a duct run is a line of pieces with the right elbows, vents come in rows. So the measuring
pass cuts every group of touching roof objects out of the vanilla map as one piece ("cluster"), and
Roofer.dress() sets whole clusters down on a generated roof, as many squares' worth as vanilla roofs of
that size carry, each no nearer to the roof's edge than it stood in vanilla.

Measured on 260 flat commercial roofs: 9 in 10 carry equipment, about 9 object squares per 100 squares of
roof; 89% have vents or air conditioners (rooftop_furniture), 82% ducts or pipes (industry_02).
"""
import collections, glob, json, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'cache', 'vanilla_roofs.json')
SHEETS = ('rooftop_furniture_', 'industry_02_')          # what counts as roof equipment
# rooms that make a building residential; a building with any other room is commercial
RES = {'bedroom', 'livingroom', 'kitchen', 'bathroom', 'hall', 'closet', 'kidsbedroom', 'diningroom', 'garage',
       'laundry', 'basement', 'shed', 'empty', 'emptyoutside', 'farmstorage', 'garagestorage', 'storage', 'attic',
       'porch', 'kidsroom', 'office', 'hallway'}
FLAT = 'floors_exterior_street'                           # what vanilla floors a flat roof with
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
N8 = N4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))
MAX_CLUSTER = 48


def harvest(vanilla_dir=None, out=OUT):
    from lotreader import rd_header, rd_pack, iter_squares
    cat = catalog.load(os.path.join(HERE, 'cache', 'catalog.json'))
    vanilla_dir = vanilla_dir or os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
    wallish = {t for t, p in cat.items() if any(k in p for k in ('WallN', 'WallW', 'WallNW', 'WallSE', 'DoorWallN', 'DoorWallW',
                                                                  'WindowN', 'WindowW'))}
    clusters = collections.Counter()
    rim_of = collections.defaultdict(list)
    floors, grime = collections.Counter(), collections.Counter()
    roofs = []                                            # (area, equipment squares)
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
        room_sq = set()
        for name, lvl, rects, _ in rooms:
            for (x, y, w, hh) in rects:
                for xx in range(x, x + w):
                    for yy in range(y, y + hh):
                        room_sq.add((xx, yy, lvl))
        for rl in h['blds']:
            if not rl or all(rooms[ri][0] in RES for ri in rl):
                continue
            if any(x < 0 or y < 0 or x + w > 256 or y + hh > 256 for ri in rl for (x, y, w, hh) in rooms[ri][2]):
                continue
            roof = set()
            for ri in rl:
                lvl = rooms[ri][1]
                for (x, y, w, hh) in rooms[ri][2]:
                    for xx in range(x, x + w):
                        for yy in range(y, y + hh):
                            k = (xx, yy, lvl + 1)
                            if k not in room_sq and k in sq:
                                roof.add(k)
            if len(roof) < 30 or sum(1 for k in roof if sq[k][0].startswith(FLAT)) < 0.8 * len(roof):
                continue
            dist = {k: 0 for k in roof if any((k[0] + dx, k[1] + dy, k[2]) not in roof for dx, dy in N4)}
            todo = list(dist)
            while todo:
                k = todo.pop(0)
                for dx, dy in N4:
                    nb = (k[0] + dx, k[1] + dy, k[2])
                    if nb in roof and nb not in dist:
                        dist[nb] = dist[k] + 1
                        todo.append(nb)
            objs = {}
            for k in roof:
                tl = sq[k]
                floors[tl[0]] += 1
                for t in tl[1:]:
                    if t.startswith('overlay_grime_floor'):
                        grime[t] += 1
                mine = [t for t in tl[1:] if t.startswith(SHEETS)]
                if mine:
                    objs[k] = (mine, any(t in wallish for t in tl))
            seen = set()
            kept = 0
            for k in objs:
                if k in seen:
                    continue
                comp, todo = [], [k]
                seen.add(k)
                while todo:
                    c = todo.pop()
                    comp.append(c)
                    for dx, dy in N8:
                        nb = (c[0] + dx, c[1] + dy, c[2])
                        if nb in objs and nb not in seen:
                            seen.add(nb)
                            todo.append(nb)
                kept += len(comp)
                # a piece hung on a wall (a louvre on a stair head) only makes sense with that wall: leave it out
                near_wall = any(objs[c][1] or any(t in wallish for dx, dy in ((1, 0), (0, 1))
                                                  for t in sq.get((c[0] + dx, c[1] + dy, c[2]), ())) for c in comp)
                if len(comp) > MAX_CLUSTER or near_wall:
                    continue
                x0, y0 = min(c[0] for c in comp), min(c[1] for c in comp)
                key = tuple(sorted((c[0] - x0, c[1] - y0, tuple(objs[c][0])) for c in comp))
                clusters[key] += 1
                rim_of[key].append(min(dist[c] for c in comp))
            roofs.append((len(roof), kept))
    tot_area = sum(a for a, _ in roofs)
    data = dict(
        roofs=len(roofs),
        share_any=round(sum(1 for _, o in roofs if o) / max(1, len(roofs)), 3),
        per100=round(100.0 * sum(o for _, o in roofs) / max(1, tot_area), 2),
        floors=[(t, c) for t, c in floors.most_common(6)],
        grime=[(t, round(c / max(1, tot_area), 4)) for t, c in grime.most_common(12)],
        clusters=[dict(n=c, rim=sorted(rim_of[k])[len(rim_of[k]) // 2], t=[[dx, dy, list(tl)] for dx, dy, tl in k])
                  for k, c in clusters.most_common()],
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(data, open(out, 'w'), separators=(',', ':'))
    print('%d flat commercial roofs, %.0f%% with equipment, %.1f squares of it per 100; %d kinds of cluster kept'
          % (data['roofs'], 100 * data['share_any'], data['per100'], len(data['clusters'])))
    return data


_data = None


def load():
    global _data
    if _data is None:
        if not os.path.isfile(OUT):
            harvest()
        _data = json.load(open(OUT))
    return _data


class Roofer:
    def __init__(self, seed=1):
        self.rng = random.Random(seed * 7919 + 5)
        d = load()
        self.d = d
        self.clusters = d['clusters']
        self.weights = [c['n'] * len(c['t']) ** 0.5 for c in self.clusters]   # big pieces are rarer but cover more
        self.stats = collections.Counter()

    def floor(self):
        tiles = [t for t, _ in self.d['floors'][:2]]
        return self.rng.choice(tiles) if tiles else 'floors_exterior_street_01_16'

    def dress(self, md, squares, z):
        """squares: set of (x, y) of one flat roof at level z, already floored. Adds equipment tiles to md."""
        rng = self.rng
        area = len(squares)
        if area < 30 or not self.clusters or rng.random() > self.d['share_any']:
            return 0
        dist = {k: 0 for k in squares if any((k[0] + dx, k[1] + dy) not in squares for dx, dy in N4)}
        todo = list(dist)
        while todo:
            k = todo.pop(0)
            for dx, dy in N4:
                nb = (k[0] + dx, k[1] + dy)
                if nb in squares and nb not in dist:
                    dist[nb] = dist[k] + 1
                    todo.append(nb)
        want = area * self.d['per100'] / 100.0 * rng.uniform(0.5, 1.4)
        cells = sorted(squares)
        taken = set()
        placed = 0
        for _ in range(60 + area // 4):
            if placed >= want:
                break
            c = rng.choices(self.clusters, weights=self.weights)[0]
            if len(c['t']) > max(3, want - placed + 4):
                continue
            x0, y0 = rng.choice(cells)
            need = max(1, c['rim'])
            ok = True
            for dx, dy, _ in c['t']:
                k = (x0 + dx, y0 + dy)
                if dist.get(k, -1) < need or k in taken:
                    ok = False
                    break
            if not ok:
                continue
            spots = [(x0 + dx, y0 + dy) for dx, dy, _ in c['t']]
            # keep a square of walking room around every piece
            if any((sx + ex, sy + ey) in taken for sx, sy in spots for ex, ey in N8):
                continue
            for (dx, dy, tl) in c['t']:
                for t in tl:
                    md.add(x0 + dx, y0 + dy, z, t)
            taken.update(spots)
            placed += len(spots)
            self.stats['clusters'] += 1
            self.stats['squares'] += len(spots)
        self.stats['roofs dressed'] += 1 if placed else 0
        return placed


if __name__ == '__main__':
    harvest()
