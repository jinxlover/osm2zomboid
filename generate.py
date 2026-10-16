"""osm2zomboid: real-world OpenStreetMap area -> Project Zomboid (Build 42) map mod.

  python generate.py --install                       the Austin preset, south-east of Muldraugh
  python generate.py --name MyTown --title "My Town" --lat 40.7484 --lon -73.9857 --cell0 80,20 --entry-y 0

Pipeline: download (osmfetch) -> project, turned so the street grid follows the tile axes (osmmodel)
-> terrain classes and street objects (ground) -> ground tiles (terrain) -> buildings and furniture
(buildings, furnish) -> road markings and street furniture (terrain) -> lot files (lotwriter).
"""
import argparse, glob, os, random, re, shutil, sys, time
import numpy as np
from scipy import ndimage as ndi

import catalog, chunkdata, worldmapgen, zones, weathering, poster, osmfetch, osmmodel, lotwriter, vanilla_import, terrain, survey
from ground import Ground, T_PAVE, T_PLAZA, T_BRICK
from buildings import BuildingGen

CELL = 256
HERE = os.path.dirname(os.path.abspath(__file__))
ROAD_BLOCKS = {0, 4, 5, 6}          # 16-tile blocks of blends_street_01 that are road surface
DASH_PERIOD = 12                    # vanilla highway lane dashes repeat every 12 squares (5 on, 7 off)


def bbox_for(lat, lon, cw, ch, scale=1.0):
    """North-up box of cw x ch cells around a point: the 'first look' download."""
    kx = 111320.0 * np.cos(np.radians(lat)) / scale
    ky = 110574.0 / scale
    dlon = (cw * CELL) / kx / 2
    dlat = (ch * CELL) / ky / 2
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def pick_rotation(args, js):
    """--rotate auto: turn the map so the street grid runs along the tile axes (straight walls and
    roads instead of staircases). A number forces that angle, 0 keeps north up."""
    if args.rotate != 'auto':
        return float(args.rotate)
    angle, strength = osmmodel.grid_angle(js, args.lat, args.lon)
    if strength < 0.5 or abs(angle) < 1.0:
        print(f'street grid: no clear direction (strength {strength:.2f}); keeping north up')
        return 0.0
    print(f'street grid runs {angle:+.1f} deg off the compass (strength {strength:.2f}); rotating the map to match')
    return angle


def align_entry(args, js, lat, lon, W, H, rot, target_y):
    """Slide the map centre along the map's own y axis until the entry road (--entry-road) meets the
    west edge of the map at local row `target_y`, where the connector from the vanilla road arrives.
    -> (lat, lon) of the new centre."""
    proj = osmmodel.Proj(lat, lon, W, H, args.scale, rot)
    if args.entry_road == 'auto':
        return _auto_entry(js, proj, lat, lon, H, target_y)
    best = None
    ends = []
    for el in js['elements']:
        t = el.get('tags', {}) or {}
        if el['type'] != 'way' or 'highway' not in t or 'geometry' not in el:
            continue
        if not re.search(args.entry_road, t.get('name', ''), re.I):
            continue
        pts = [proj.xy(g['lat'], g['lon']) for g in el['geometry']]
        ends.append((min(pts), t.get('name')))
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            if xa != xb and min(xa, xb) <= 0 <= max(xa, xb):          # this segment crosses the west edge
                y = ya + (yb - ya) * (0 - xa) / (xb - xa)
                if best is None or abs(y - H / 2) < abs(best[0] - H / 2):
                    best = (y, t.get('name'))
    if best is None and ends:
        (x, y), name = min(ends)                                      # stops short of the edge: take its west end
        best = (y, name)
        print(f'entry road {name} ends {x:.0f} squares inside the west edge; aligning on its end')
    if best is None:
        print(f'entry road matching "{args.entry_road}" not found; keeping the map centre')
        return lat, lon
    y, name = best
    dy = y - target_y
    lat2 = lat - dy * proj.c / proj.ky
    lon2 = lon + dy * proj.s / proj.kx
    print(f'entry road {name} meets the west edge at local row {y:.1f}; moving the map {dy:+.1f} squares '
          f'so it arrives at row {target_y:.1f} (centre {lat:.6f},{lon:.6f} -> {lat2:.6f},{lon2:.6f})')
    return lat2, lon2


# roads a connector may join, best first
ENTRY_CLASSES = ['trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential']


def _auto_entry(js, proj, lat, lon, H, target_y, keep=0.3):
    """--entry-road auto: join the most important road that crosses the map's west edge, sliding the map
    no further than keeps the place it was centred on in the middle (1 - 2 * keep) of the map."""
    limit = H * (0.5 - keep)
    best = None
    for el in js['elements']:
        t = el.get('tags', {}) or {}
        if el['type'] != 'way' or t.get('highway') not in ENTRY_CLASSES or 'geometry' not in el:
            continue
        pts = [proj.xy(g['lat'], g['lon']) for g in el['geometry']]
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            if xa != xb and min(xa, xb) <= 0 <= max(xa, xb):
                y = ya + (yb - ya) * (0 - xa) / (xb - xa)
                if abs(y - target_y) <= limit:
                    key = (ENTRY_CLASSES.index(t['highway']), abs(y - target_y))
                    if best is None or key < best[0]:
                        best = (key, y, t.get('name') or t['highway'])
    if best is None:
        print('entry road: no road crosses the west edge near enough; keeping the map centre')
        return lat, lon
    _, y, name = best
    dy = y - target_y
    lat2 = lat - dy * proj.c / proj.ky
    lon2 = lon + dy * proj.s / proj.kx
    print(f'entry road (auto): {name} meets the west edge at local row {y:.1f}; moving the map {dy:+.1f} squares '
          f'so it arrives at row {target_y:.1f}')
    return lat2, lon2


# ------------------------------------------------------------------------------------------ connector
def connector_cells(args):
    """The vanilla cells the connector road crosses, west to east: from the cell that holds the end of
    the vanilla road (--link-cell) to the cell just west of the map (--cell0)."""
    lx, ly = map(int, args.link_cell.split(','))
    cx0, cy0 = map(int, args.cell0.split(','))
    ch = int(args.cells.split('x')[1])
    problems = []
    if not lx < cx0:
        problems.append(f'--link-cell {lx},{ly} must lie west of --cell0 {cx0},{cy0}')
    if not lx * CELL < args.vanilla_end <= (lx + 1) * CELL:
        problems.append(f'--vanilla-end {args.vanilla_end} is not inside link cell {lx} (x {lx * CELL + 1}..{(lx + 1) * CELL})')
    if int(args.entry_y // CELL) != ly:
        problems.append(f'--entry-y {args.entry_y} is in cell row {int(args.entry_y // CELL)}, not in --link-cell row {ly}')
    if not cy0 <= ly < cy0 + ch:
        problems.append(f'--link-cell row {ly} is outside the map rows {cy0}..{cy0 + ch - 1}')
    if problems:
        raise SystemExit('connector: ' + '; '.join(problems))
    return [(cx, ly) for cx in range(lx, cx0)]


def _road_block(tile):
    sheet, _, idx = tile.rpartition('_')
    if sheet == 'blends_street_01' and idx.isdigit() and int(idx) // 16 in ROAD_BLOCKS:
        return int(idx) // 16
    return None


def build_connector(args, rng):
    """Continue a vanilla road east from where it stops (world x = --vanilla-end, centre line at world
    y = --entry-y) to the west edge of the map.

    Every vanilla cell on the way is imported whole, so its forest, zombie map and anything else in it
    survive; only the strip of road is repainted. Unless --entry-width is given, the road is a copy of
    the vanilla road's own last squares (same width, same asphalt, same edge blends and lane lines), so
    the join cannot be seen.  -> (MapData, [(cell x, cell y)...], (top row, width))"""
    cells = connector_cells(args)
    lx, ly = cells[0]
    n = len(cells)
    md = lotwriter.MapData(n * CELL, CELL, lx, ly)
    vdir = vanilla_import.vanilla_dir()
    for i, (cx, cy) in enumerate(cells):
        if not os.path.isfile(os.path.join(vdir, f'{cx}_{cy}.lotheader')):
            raise SystemExit(f'connector: the vanilla map has no cell {cx},{cy}')
        vanilla_import.import_cell(md, cx, cy, ox=i * CELL)
    x0 = args.vanilla_end - lx * CELL               # first column to pave (map-local)
    yc = args.entry_y - ly * CELL                   # centre line (map-local)

    def road(x, y):
        t = md.squares.get((x, y, 0))
        return bool(t) and _road_block(t[0]) is not None

    if args.entry_width > 0:
        width = int(args.entry_width)
        top = int(round(yc - width / 2.0))
        for x in range(x0, n * CELL):
            for k in range(width):
                tiles = ['blends_street_01_%d' % rng.choice((80, 85, 86, 87))]
                if k == 0:
                    tiles.append('blends_natural_01_%d' % rng.choice((24, 28)))        # grass creeping in from the north
                elif k == width - 1:
                    tiles.append('blends_natural_01_%d' % rng.choice((27, 31)))        # ... and from the south
                md.squares[(x, top + k, 0)] = tiles
        return md, cells, (ly * CELL + top, width)

    cols = range(x0 - DASH_PERIOD, x0)
    if x0 < DASH_PERIOD:
        raise SystemExit('connector: --vanilla-end is too close to the west side of the link cell')
    rowset = {y for y in range(CELL) if sum(road(x, y) for x in cols) > DASH_PERIOD // 2}
    yi = min(rowset, key=lambda y: abs(y + 0.5 - yc), default=None)
    if yi is None or abs(yi + 0.5 - yc) > 4:
        raise SystemExit(f'connector: no vanilla road ends at x={args.vanilla_end}, y={args.entry_y}; '
                         f'check --vanilla-end / --entry-y (survey.py draws the vanilla cells)')
    top = bottom = yi
    while top - 1 in rowset:
        top -= 1
    while bottom + 1 in rowset:
        bottom += 1
    for x in range(x0, n * CELL):
        xs = x0 - DASH_PERIOD + (x - x0) % DASH_PERIOD          # repeat the vanilla road's last 12 columns
        for y in range(top, bottom + 1):
            src = md.squares.get((xs, y, 0)) or ['blends_street_01_80']
            block = _road_block(src[0])
            tiles = ['blends_street_01_%d' % (block * 16 + rng.choice((0, 5, 6, 7)))] if block is not None else [src[0]]
            for t in src[1:]:
                sheet, _, idx = t.rpartition('_')
                if sheet == 'street_trafficlines_01':
                    tiles.append(t)
                elif sheet in ('blends_natural_01', 'blends_street_01') and idx.isdigit() and int(idx) % 16 >= 8:
                    i = int(idx)                                 # an edge strip: keep its side, vary its look
                    variants = (0, 4) if sheet == 'blends_natural_01' else (0,)
                    tiles.append('%s_%d' % (sheet, i - i % 16 + 8 + (i % 16 - 8) % 4 + rng.choice(variants)))
            md.squares[(x, y, 0)] = tiles
    return md, cells, (ly * CELL + top, bottom - top + 1)


def check_site(cx0, cy0, cw, ch):
    """Say what the map replaces: every vanilla cell under it, and whether any holds more than forest."""
    vdir = vanilla_import.vanilla_dir()
    busy = []
    present = 0
    for cy in range(cy0, cy0 + ch):
        for cx in range(cx0, cx0 + cw):
            a, rooms = survey.read_cell(vdir, cx, cy)
            if a is None:
                continue
            present += 1
            road = int(np.isin(a, [survey.C_ROAD, survey.C_PAVE, survey.C_GRAVEL, survey.C_RAIL, survey.C_BLD]).sum())
            if rooms or road:
                busy.append(f'{cx},{cy} ({rooms} rooms, {road} paved squares)')
    if busy:
        print('WARNING: the map replaces vanilla cells that are not just forest:', '; '.join(busy))
    elif present:
        print(f'site: replaces {present} vanilla cells, all forest')
    else:
        print('site: outside the vanilla map')


# ------------------------------------------------------------------------------------------ the map
def make(args):
    catalog.load(os.path.join(args.cache, 'catalog.json'))
    cw, ch = map(int, args.cells.split('x'))
    cx0, cy0 = map(int, args.cell0.split(','))
    W, H = cw * CELL, ch * CELL
    rng = random.Random(args.seed)
    check_site(cx0, cy0, cw, ch)

    # ---- first look (north up): find the street grid direction and the entry road
    js0 = osmfetch.fetch(*bbox_for(args.lat, args.lon, cw, ch, args.scale), args.cache)
    rot = pick_rotation(args, js0)
    lat, lon = args.lat, args.lon
    if args.entry_y:
        connector_cells(args)                       # fail early on inconsistent flags
        lat, lon = align_entry(args, js0, lat, lon, W, H, rot, args.entry_y - cy0 * CELL)
    proj = osmmodel.Proj(lat, lon, W, H, args.scale, rot)
    js = osmfetch.fetch(*proj.bbox(), args.cache)
    feats = osmmodel.load(js, proj)
    print(len(feats), 'features')

    # ---- ground
    t0 = time.time()
    g = Ground(feats, proj, seed=args.seed, background=args.background)
    nrow = split_rows(g)
    if nrow:
        print('big buildings among houses split into rows of homes: %d units' % nrow)
    md = lotwriter.MapData(W, H, cx0, cy0)
    terrain.lay_ground(md, g, rng)
    print(f'ground {time.time() - t0:.1f} s: {len(g.roads)} roads, {len(g.crossings)} crossings, '
          f'{int(g.PK.max())} car parks, {len(g.barriers)} fences/walls, {len(g.pois)} street objects')

    # ---- buildings: the shops / amenities mapped inside each outline and the entrances on its walls
    t0 = time.time()
    pois, ents = {}, {}
    for f in feats:
        if f.kind == 'node' and any(k in f.tags for k in ('amenity', 'shop', 'tourism', 'office', 'leisure')):
            x, y = int(f.pt[0]), int(f.pt[1])
            if not (0 <= x < W and 0 <= y < H):
                continue
            b = int(g.B[y, x])
            if b == 0:                                # mapped on the pavement in front: the nearest building's
                near = g.B[max(0, y - 4):y + 5, max(0, x - 4):x + 5]
                if (near > 0).any():
                    ys_, xs_ = np.nonzero(near)
                    k = np.argmin((ys_ - min(y, 4)) ** 2 + (xs_ - min(x, 4)) ** 2)
                    b = int(near[ys_[k], xs_[k]])
            if b and f.tags.get('amenity') not in ('bench', 'waste_basket', 'bicycle_parking', 'parking_entrance',
                                                  'atm', 'vending_machine', 'post_box', 'drinking_water',
                                                  'charging_station', 'recycling', 'fountain', 'toilets'):
                pois.setdefault(b, []).append((f.pt[0], f.pt[1], f.tags))
    for (x, y, t) in g.entrances:
        xi, yi = int(x), int(y)
        near = g.B[max(0, yi - 3):yi + 4, max(0, xi - 3):xi + 4]
        labels = np.unique(near[near > 0])
        if len(labels):
            # the building whose squares are closest to the entrance node
            ys, xs = np.nonzero(near > 0)
            k = np.argmin((xs + max(0, xi - 3) + 0.5 - x) ** 2 + (ys + max(0, yi - 3) + 0.5 - y) ** 2)
            ents.setdefault(int(near[ys[k], xs[k]]), []).append((x, y, t))
    road_dist = ndi.distance_transform_edt(~g.street)
    front_dist = ndi.distance_transform_edt(~g.main_street) if g.main_street.any() else road_dist
    bg = BuildingGen(md, g, road_dist, seed=args.seed, max_levels=args.max_levels, front_dist=front_dist)
    slices = ndi.find_objects(g.B)
    infos = {}
    for bid in range(1, len(slices) + 1):
        sl = slices[bid - 1]
        if sl is None or bid not in g.bfeat:
            continue
        ys, xs = sl
        if args.only:                                    # a quick test build: only the buildings in this box
            bx0, by0, bx1, by1 = (v - o for v, o in zip(args.only, (cx0 * CELL, cy0 * CELL) * 2))
            if xs.stop <= bx0 or xs.start >= bx1 or ys.stop <= by0 or ys.start >= by1:
                continue
        mask = g.B[sl] == bid
        if mask.sum() < 12 or min(mask.shape) < 3:
            continue
        info = bg.build(bid, g.bfeat[bid], pois.get(bid, []), mask, xs.start, ys.start, ents.get(bid, ()))
        if info:
            infos[bid] = info
    bg.roof_rows()                                    # one roof over each row of homes
    bg.emit_walls()
    print(f'buildings {time.time() - t0:.1f} s: {len(infos)} built', dict(bg.stats))
    print('furniture placed:', dict(sorted(bg.furnisher.count.items())))

    # ---- streets: markings, then trees / lamps / signals / benches / fences
    spawns = pick_spawns(g, road_dist)
    terrain.lay_markings(md, g, rng)
    nf = terrain.place_fuel(md, g, feats, rng)
    if nf:
        print('fuel pumps:', nf)
    terrain.decorate(md, g, rng, keep_clear={(sx + dx, sy + dy) for sx, sy in spawns
                                             for dx in (-1, 0, 1) for dy in (-1, 0, 1)})
    weathering.weather(md, g, args.seed)               # grime on walls and floors, as vanilla's
    heat = terrain.zombie_density(md, g, infos, rng, args.zombies)
    print('zombie intensity: mean %.2f, chunks at 0..%d: %s (downtown Louisville: mean 2.18)'
          % (heat.mean(), heat.max(), np.bincount(heat.ravel()).tolist()))

    # ---- write lots
    # The map goes under common/, where the game looks for a mod's maps. (A map under 42/media/maps
    # loads as a mod but never joins the world: no cells, no entry in the spawn menu.)
    outmap = os.path.join(args.out, 'common', 'media', 'maps', args.name)
    for old in (outmap, os.path.join(args.out, '42', 'media')):
        if os.path.isdir(old):
            shutil.rmtree(old)
    done = lotwriter.write_all(md, outmap)
    chunkdata.write_all(md, outmap)                 # collision map of every cell for off-screen zombies
    if args.entry_y:
        conn, cells, (top, width) = build_connector(args, rng)
        done += lotwriter.write_all(conn, outmap)
        vdir = vanilla_import.vanilla_dir()
        for i, (cx, cy) in enumerate(cells):
            # an imported cell keeps its vanilla collision map; only the squares of the new road are redone
            src = os.path.join(vdir, f'chunkdata_{cx}_{cy}.bin')
            a = chunkdata.read(src) if os.path.exists(src) else chunkdata.from_map(conn, i, 0)
            lo = max(0, args.vanilla_end - cx * CELL)
            r0 = top - cy * CELL
            a[r0:r0 + width, lo:] = chunkdata.from_map(conn, i, 0)[r0:r0 + width, lo:]
            chunkdata.write(os.path.join(outmap, f'chunkdata_{cx}_{cy}.bin'), a)
        print(f'connector: {width} squares wide, world rows {top}..{top + width - 1}, from x={args.vanilla_end} '
              f'to x={cx0 * CELL - 1}, through vanilla cells', ' '.join(f'{cx},{cy}' for cx, cy in cells))
    print('cells written', done)
    zones.write_objects(outmap, md, g, infos)        # cars in the car parks, town and zombie-type zones
    # the in-game map: roads, buildings, water, street names, the town label and the painted terrain
    worldmapgen.write_all(outmap, md, g, infos, args.title.split(',')[0].split(' - ')[0].strip(),
                          conn if args.entry_y else None)
    write_modfiles(args, outmap, cx0, cy0, W, H, spawns)
    poster.make(args.out)                             # the picture on the Mods screen: its busiest street
    return md, g, infos


def pick_spawns(g, road_dist):
    """Sidewalk squares a couple of squares off the road: the one nearest the middle of the map first,
    then one towards each quarter, so a new game does not always start on the same corner."""
    H, W = g.G.shape
    ok = (g.B == 0) & np.isin(g.G, [T_PAVE, T_PLAZA, T_BRICK]) & (road_dist >= 2)
    ys, xs = np.nonzero(ok)
    if not len(xs):
        ys, xs = np.nonzero((g.B == 0) & ~g.street)
    out = []
    for fx, fy in ((0.5, 0.5), (0.3, 0.35), (0.7, 0.35), (0.3, 0.65), (0.7, 0.65)):
        k = np.argmin((xs - W * fx) ** 2 + (ys - H * fy) ** 2)
        pt = (int(xs[k]), int(ys[k]))
        if pt not in out:
            out.append(pt)
    return out


# every occupation the vanilla spawn files list
PROFESSIONS = ['chef', 'constructionworker', 'doctor', 'fireofficer', 'nurse', 'parkranger', 'policeofficer', 'repairman', 'securityguard', 'unemployed', 'burglar', 'burgerflipper', 'carpenter', 'electrician', 'engineer', 'farmer', 'fisherman', 'fitnessInstructor', 'lumberjack', 'mechanics', 'metalworker', 'rancher', 'smither', 'tailor', 'veteran']


ROW_TAGS = ('yes', 'residential', 'terrace', 'semidetached_house')


def _biggest_rect(m):
    """Largest all-True rectangle of a mask -> (x, y, w, h) or None."""
    H, W = m.shape
    heights = np.zeros(W, int)
    best = (0, None)
    for j in range(H):
        heights = np.where(m[j], heights + 1, 0)
        stack = []
        for i in range(W + 1):
            hh = int(heights[i]) if i < W else 0
            start = i
            while stack and stack[-1][1] >= hh:
                st, sh = stack.pop()
                if sh * (i - st) > best[0]:
                    best = (sh * (i - st), (st, j - sh + 1, i - st, sh))
                start = st
            stack.append((start, hh))
    return best[1]


def split_rows(g, unit=9.0):
    """A big building among houses is a row of homes, not an apartment block: an untagged (or residential /
    terrace) outline of 320+ squares in a residential area is cut along its length into house-wide units
    (vanilla houses are about 8 x 12), front and back as well when it is deeper than 18. Each unit becomes
    its own house (building=house) sharing blank walls with its neighbours. -> units made"""
    import copy
    made = 0
    nb = int(g.B.max())
    g.row_of = {}
    slices = ndi.find_objects(g.B)
    for bid in range(1, len(slices) + 1):
        sl, f = slices[bid - 1], g.bfeat.get(bid)
        if sl is None or f is None:
            continue
        t = f.tags
        if t.get('building') not in ROW_TAGS or any(k in t for k in ('shop', 'amenity', 'office', 'tourism', 'leisure',
                                                                    'name', 'building:levels')):
            continue
        try:
            tall = float(str(t.get('height', '0')).split()[0])
        except ValueError:
            tall = 0.0
        if tall > 13:                                    # four floors and more: flats after all
            continue
        m = g.B[sl] == bid
        area = int(m.sum())
        if not 320 <= area <= 4000:
            continue
        ys, xs = np.nonzero(m)
        cy, cx = int(ys.mean()) + sl[0].start, int(xs.mean()) + sl[1].start
        lu, cov = int(g.landuse[cy, cx]), float(g.coverage[cy, cx])
        if not (lu == 1 or (lu not in (2, 3) and cov < 0.22)):
            continue
        # a terrace is a plain box (and only a box takes one vanilla roof): fill the outline out to its box
        # where it nearly fills it and nothing else is there, else keep its biggest rectangle (the rest is lawn)
        view0 = g.B[sl]
        if m.mean() >= 0.85 and not ((view0 != 0) & (view0 != bid)).any() and not g.street[sl].any():
            view0[:, :] = bid
            m = view0 == bid
        else:
            r = _biggest_rect(m)
            if r is not None and (r[2] * r[3]) >= 0.7 * area:
                x0_, y0_, rw, rh = r
                keep = np.zeros_like(m)
                keep[y0_:y0_ + rh, x0_:x0_ + rw] = True
                view0[m & ~keep] = 0
                m = keep
        h, w = m.shape
        ys_, xs_ = np.nonzero(m)
        # (the box may sit inside the old outline's bounding box)
        by0, by1, bx0, bx1 = int(ys_.min()), int(ys_.max()) + 1, int(xs_.min()), int(xs_.max()) + 1
        along_x = (bx1 - bx0) >= (by1 - by0)
        L, S = ((bx1 - bx0), (by1 - by0)) if along_x else ((by1 - by0), (bx1 - bx0))
        rows, cols = (2 if S > 18 else 1), max(2, int(round(L / unit)))
        lab = np.zeros(m.shape, np.int32)
        for r in range(rows):
            for c in range(cols):
                a0, a1, b0, b1 = c * L // cols, (c + 1) * L // cols, r * S // rows, (r + 1) * S // rows
                sel = np.zeros_like(m)
                if along_x:
                    sel[by0 + b0:by0 + b1, bx0 + a0:bx0 + a1] = True
                else:
                    sel[by0 + a0:by0 + a1, bx0 + b0:bx0 + b1] = True
                lab[sel & m] = 1 + r * cols + c
        # every piece in one part; a scrap (under 30 squares) joins the unit beside it
        k = int(lab.max())
        for v in range(1, int(lab.max()) + 1):
            comp, n = ndi.label(lab == v)
            for j in range(1, n + 1):
                cm = comp == j
                if n > 1 and j != 1:
                    k += 1
                    lab[cm] = k
        for v in sorted(range(1, k + 1), key=lambda v: (lab == v).sum()):
            cm = lab == v
            if 0 < cm.sum() < 30:
                ring = ndi.binary_dilation(cm) & ~cm & (lab > 0)
                if ring.any():
                    lab[cm] = int(np.bincount(lab[ring]).argmax())
        units = [v for v in range(1, k + 1) if (lab == v).any()]
        if len(units) < 2:
            continue
        view = g.B[sl]
        for i, v in enumerate(units):
            nid = bid if i == 0 else nb + 1
            if i:
                nb += 1
            view[lab == v] = nid
            g.row_of[nid] = bid                          # the homes of one row share a look and a roof
            f2 = copy.copy(f)
            f2.tags = {k: v for k, v in t.items() if k != 'height'}
            f2.tags.update(building='house', **{'building:levels': '2' if tall >= 6 else '1'})
            g.bfeat[nid] = f2
        made += len(units)
    return made


def _map_cells(mod_dir):
    """World cells a map mod occupies (its lot files) -> (mod id, set of (cx, cy)) or None."""
    info = os.path.join(mod_dir, 'mod.info')
    if not os.path.isfile(info):
        return None
    lines = open(info, encoding='utf8', errors='ignore').read().splitlines()
    if 'author=osm2zomboid' not in lines:
        return None
    mid = next((l[3:] for l in lines if l.startswith('id=')), None)
    cells = set()
    for hp in glob.glob(os.path.join(mod_dir, 'common', 'media', 'maps', '*', '*.lotheader')):
        cx, cy = os.path.basename(hp)[:-10].split('_')
        cells.add((int(cx), int(cy)))
    return (mid, cells) if mid else None


def mark_overlaps(mod_dir, name):
    """Maps made by this tool that share world cells with this one cannot run together: mark each pair
    incompatible in both mods' mod.info (in output/ and in the Zomboid mods folder)."""
    mine = _map_cells(mod_dir)
    if not mine:
        return
    homes = [os.path.join(HERE, 'output'), os.path.join(os.path.expanduser('~'), 'Zomboid', 'mods')]
    others = {}
    copies = [mod_dir]                                   # this mod, here and installed
    for home in homes:
        for d in glob.glob(os.path.join(home, '*')):
            if os.path.abspath(d) == os.path.abspath(mod_dir) or d.endswith('_test'):
                continue
            o = _map_cells(d)
            if o and o[0] == name:
                copies.append(d)
            elif o and o[1] & mine[1]:
                others.setdefault(o[0], []).append(d)

    def add(d, ids):
        for sub in (d, os.path.join(d, '42')):
            p = os.path.join(sub, 'mod.info')
            if not os.path.isfile(p):
                continue
            lines = open(p, encoding='utf8').read().splitlines()
            have = set()
            for l in lines:
                if l.startswith('incompatible='):
                    have |= {x.strip().lstrip(chr(92)) for x in l[13:].split(',') if x.strip()}
            new = have | set(ids)
            lines = [l for l in lines if not l.startswith('incompatible=')]
            lines.append('incompatible=' + ','.join(chr(92) + x for x in sorted(new)))
            open(p, 'w', encoding='utf8').write(chr(10).join(lines) + chr(10))
    if others:
        for d in copies:
            add(d, others)
        for oid, dirs in others.items():
            for d in dirs:
                add(d, [name])
        print('overlaps (marked incompatible both ways):', ', '.join(sorted(others)))


def write_modfiles(args, outmap, cx0, cy0, W, H, spawns):
    name = args.name
    ax, ay = cx0 * CELL, cy0 * CELL
    # Build 42 form: absolute world squares. (The older worldX / worldY form is read in 300-square cells.)
    pts = ['    { posX = %d, posY = %d, posZ = 0 },' % (ax + sx, ay + sy) for sx, sy in spawns]
    lines = ['function SpawnPoints()', '  local streets = {'] + pts + ['  }', '  return {']
    lines += ['    %s = streets,' % p for p in PROFESSIONS]
    lines += ['  }', 'end']
    open(os.path.join(outmap, 'spawnpoints.lua'), 'w').write('\n'.join(lines) + '\n')
    sx, sy = spawns[0]
    open(os.path.join(outmap, 'map.info'), 'w').write(
        f'title={args.title}\nlots=Muldraugh, KY\ndescription={args.desc}\nfixed2x=true\n'
        f'zoomX={ax + sx}\nzoomY={ay + sy}\nzoomS=14\n')
    mi = (f'name={args.title}\nid={name}\ndescription={args.desc}\nauthor=osm2zomboid\nmodversion=0.3\n'
          f'versionMin=42.0.0\n')
    os.makedirs(os.path.join(args.out, '42'), exist_ok=True)
    for d in (args.out, os.path.join(args.out, '42')):
        open(os.path.join(d, 'mod.info'), 'w').write(mi)
    mark_overlaps(args.out, name)
    # the OpenStreetMap licence (ODbL) asks for credit wherever the data goes
    open(os.path.join(args.out, 'ATTRIBUTION.txt'), 'w', encoding='utf8').write(chr(10).join([
        'Map data (c) OpenStreetMap contributors, available under the Open Database License (ODbL):',
        'https://www.openstreetmap.org/copyright', '',
        'Generated with osm2zomboid (MIT License): https://github.com/jinxlover/osm2zomboid',
        'Unofficial fan-made map; not affiliated with The Indie Stone. Everything inside the buildings is invented.',
        '']))
    print('spawn points (world squares):', ', '.join('%d,%d' % (ax + x, ay + y) for x, y in spawns))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', default='Austin6thStreet')
    ap.add_argument('--title', default='Austin, TX - 6th Street')
    ap.add_argument('--desc', default='Downtown Austin around 6th Street, generated from OpenStreetMap data.')
    ap.add_argument('--lat', type=float, default=30.2678)
    ap.add_argument('--lon', type=float, default=-97.7420)
    ap.add_argument('--cells', default='4x3', help='map size in 256x256 cells, WIDTHxHEIGHT')
    ap.add_argument('--cell0', default='51,42', help='world cell of the north-west corner of the map')
    ap.add_argument('--rotate', default='auto',
                    help="'auto' turns the map so the street grid follows the tile axes; a number of degrees "
                         "(anticlockwise from east) forces it; 0 keeps north up")
    ap.add_argument('--entry-y', type=float, default=11204.5,
                    help='world y of the centre line of the vanilla road the connector continues (0 = no connector)')
    ap.add_argument('--entry-road', default='6th Street',
                    help="name of the street that meets the connector, or 'auto' for the biggest road crossing the west edge")
    ap.add_argument('--vanilla-end', type=int, default=12900,
                    help='world x of the first square past the end of that vanilla road (where paving starts)')
    ap.add_argument('--link-cell', default='50,43', help='vanilla cell that holds the end of that road')
    ap.add_argument('--entry-width', type=int, default=0,
                    help='connector width in squares (0 = copy the vanilla road: same width, blends and lane lines)')
    ap.add_argument('--background', default='auto', choices=['auto', 'grass', 'concrete'],
                    help='what lies between mapped features (auto: paved lots downtown, lawns in suburbs, grass beyond)')
    ap.add_argument('--zombies', type=float, default=0.5,
                    help='zombie density: 1 = the spread of downtown Louisville (mean 2.1, never above 4), '
                         '0.5 = half of that (default), 0 = none')
    ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--max-levels', type=int, default=28,
                    help="tallest building in floors (vanilla's tallest tower has 28); taller ones keep their plan, squeezed")
    ap.add_argument('--cache', default=os.path.join(HERE, 'cache'), help='folder for downloaded OSM data')
    ap.add_argument('--out', default=None, help='output mod folder (default: ./output/<name>)')
    ap.add_argument('--install', action='store_true', help='also copy the mod into your Zomboid/mods folder')
    ap.add_argument('--only', type=int, nargs=4, metavar=('X0', 'Y0', 'X1', 'Y1'), default=None,
                    help='quick test: build only the buildings touching this box of world squares (streets and '
                         'terrain still cover the whole map); written to output/<name>_test, never installed')
    ap.add_argument('--pz-dir', default=None, help='Project Zomboid install folder (auto-detected normally)')
    a = ap.parse_args()
    if a.pz_dir:
        os.environ['PZ_DIR'] = a.pz_dir
    if a.only:                                           # a test build never replaces or installs the real one
        a.out = os.path.join(HERE, 'output', a.name + '_test')
        a.install = False
    if not a.out:
        a.out = os.path.join(HERE, 'output', a.name)
    make(a)
    if a.install:
        dst = os.path.join(os.path.expanduser('~'), 'Zomboid', 'mods', a.name)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(a.out, dst)
        print('installed to', dst)
    print('done ->', a.out)
