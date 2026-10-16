"""The in-game map of a generated map, in the files and formats the game reads for vanilla and for map mods.

For every map folder (mods first) the game loads (ISUI/Maps/ISMapDefinitions.lua):
  worldmap.xml              vector features per 256-square cell: roads, buildings, water; drawn on the paper
                            map, the minimap and the spawn-select map. The first folder that has features for
                            a cell owns that cell, so a vanilla cell we touch gets vanilla's own features copied.
  streets.xml               street centre lines with names, in world squares
  worldmap-annotations.lua  labels (the town name)
  pyramid.zip               the painted terrain image, 1 pixel per square, 256-pixel tiles at zoom levels 0-4,
                            level 0 in vanilla's 14-colour palette; pyramid.txt gives the world bounds
  forest.pyramid.zip        the same for the forest mask (1 = forest)

The painted colours are not guessed: learn_colors() reads vanilla's painted map and the vanilla squares under
it, and records which colour each ground tile (and each building floor) is painted in.
"""
import collections, math, glob, io, json, os, re, sys, zipfile
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
CELL = 256
COLORS = os.path.join(HERE, 'cache', 'vanilla_mapcolors.json')
PALETTE = [(48, 73, 32), (217, 207, 183), (218, 165, 32), (160, 160, 164), (128, 128, 128), (127, 120, 45),
           (142, 91, 86), (97, 103, 36), (75, 88, 27), (112, 61, 56), (91, 63, 21), (73, 58, 43), (93, 44, 39),
           (81, 53, 11)]
# what a building is on vanilla's map (property building=...)
USE_CAT = {'house': 'Residential', 'apartments': 'Residential', 'shed': 'yes', 'garage': 'yes',
           'hotel': 'Hospitality', 'clinic': 'Medical', 'dentist': 'Medical',
           'bar': 'RestaurantsAndEntertainment', 'restaurant': 'RestaurantsAndEntertainment',
           'cafe': 'RestaurantsAndEntertainment', 'theatre': 'RestaurantsAndEntertainment',
           'retail': 'RetailAndCommercial', 'office': 'RetailAndCommercial', 'bank': 'RetailAndCommercial',
           'studio': 'RetailAndCommercial', 'gas': 'RetailAndCommercial', 'market': 'RetailAndCommercial',
           'warehouse': 'Industrial', 'parking': 'Industrial',
           'civic': 'CommunityServices', 'school': 'CommunityServices', 'church': 'CommunityServices',
           'library': 'CommunityServices', 'police': 'CommunityServices', 'post': 'CommunityServices',
           'museum': 'CommunityServices', 'community': 'CommunityServices', 'gym': 'CommunityServices'}
# OpenStreetMap highway -> vanilla's road classes
HW_CLASS = {'motorway': 'primary', 'trunk': 'primary', 'primary': 'primary', 'motorway_link': 'primary',
            'trunk_link': 'primary', 'primary_link': 'primary', 'secondary': 'secondary', 'secondary_link': 'secondary',
            'tertiary': 'tertiary', 'tertiary_link': 'tertiary', 'residential': 'tertiary', 'unclassified': 'tertiary',
            'living_street': 'tertiary', 'service': 'tertiary', 'road': 'tertiary', 'busway': 'tertiary',
            'track': 'trail', 'footway': 'trail', 'path': 'trail', 'pedestrian': 'trail', 'cycleway': 'trail',
            'steps': 'trail', 'bridleway': 'trail'}


def _sheet(t):
    return re.sub(r'_\d+$', '', t)


# ------------------------------------------------------------------------------------------------- colours
def learn_colors(vanilla_dir=None, cells=None, out=COLORS):
    """Which palette colour vanilla paints each ground tile / building floor in. -> {'tile':{}, 'sheet':{}, 'room':i}"""
    import catalog
    from lotreader import rd_header, rd_pack, iter_squares
    vanilla_dir = vanilla_dir or os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')
    z = zipfile.ZipFile(os.path.join(vanilla_dir, 'pyramid.zip'))
    tiles, sheets, rooms = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter), collections.Counter()
    cells = cells or [(cx, cy) for cx in range(38, 46) for cy in range(34, 44)] + [(cx, cy) for cx in range(45, 51) for cy in range(3, 9)]
    for cx, cy in cells:
        hp = os.path.join(vanilla_dir, '%d_%d.lotheader' % (cx, cy))
        if not os.path.isfile(hp):
            continue
        h = rd_header(hp)
        v, n, offs, pack = rd_pack(os.path.join(vanilla_dir, 'world_%d_%d.lotpack' % (cx, cy)))
        inroom = set()
        for name, lvl, rects, _ in h['rooms']:
            if lvl == 0:
                for (x, y, w, hh) in rects:
                    inroom.update((x + i, y + j) for i in range(w) for j in range(hh))
        try:
            img = Image.open(io.BytesIO(z.read('0/tile%dx%d.png' % (cx, cy))))
        except KeyError:
            continue
        px = np.array(img.convert('RGB'))                # every tile has its own palette: compare colours
        for x, y, zz, t in iter_squares(h, pack, offs, n):
            if zz != 0 or not t:
                continue
            c = '%d,%d,%d' % tuple(px[y, x])
            if (x, y) in inroom:
                rooms[c] += 1
            else:
                tiles[t[0]][c] += 1
                sheets[_sheet(t[0])][c] += 1
    data = dict(tile={k: c.most_common(1)[0][0] for k, c in tiles.items() if sum(c.values()) >= 20},
                sheet={k: c.most_common(1)[0][0] for k, c in sheets.items() if sum(c.values()) >= 20},
                room=rooms.most_common(1)[0][0] if rooms else '142,91,86',
                rooms=dict(rooms.most_common(4)))
    json.dump(data, open(out, 'w'))
    print('map colours learnt for %d ground tiles, %d sheets; building floors -> %s' % (len(data['tile']), len(data['sheet']), data['room']))
    return data


def colors():
    if not os.path.isfile(COLORS):
        learn_colors()
    return json.load(open(COLORS))


def paint(squares, room_of, W, H):
    """Level-0 image (RGB) of a map: each square in vanilla's colour for its ground tile, building floors in
    vanilla's building colour. squares/room_of keyed (x, y, z) map-local."""
    col = colors()
    rgb = lambda c: tuple(int(v) for v in c.split(','))
    tile = {k: rgb(v) for k, v in col['tile'].items()}
    sheet = {k: rgb(v) for k, v in col['sheet'].items()}
    room = rgb(col['room'])
    grass = sheet.get('blends_natural_01', PALETTE[0])
    sheet['floors_exterior_tilesandstone_01'] = (160, 160, 164)      # pavements: vanilla paints them light grey
    tile = {k: v for k, v in tile.items() if not k.startswith('floors_exterior_tilesandstone_01')}
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = grass
    for (x, y, z), t in squares.items():
        if z or not t or not (0 <= x < W and 0 <= y < H):
            continue
        if (x, y, 0) in room_of:                          # buildings: red, edged darker, as vanilla draws them
            edge = any((x + dx, y + dy, 0) not in room_of for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
            img[y, x] = (93, 44, 39) if edge else (112, 61, 56)
        else:
            img[y, x] = tile.get(t[0]) or sheet.get(_sheet(t[0]), grass)
    return img


def forest_mask(squares, W, H):
    m = np.zeros((H, W), bool)
    for (x, y, z), t in squares.items():
        if z == 0 and 0 <= x < W and 0 <= y < H and any(tt.startswith(('vegetation_trees', 'jumbo_tree', 'e_')) for tt in t):
            m[y, x] = True
    from scipy import ndimage as ndi
    return ndi.binary_closing(ndi.binary_dilation(m, iterations=1), iterations=2)


def write_pyramid(path, img, bounds, mask=False):
    """img: H x W x 3 colours (or a 0/1 mask) covering bounds (x0, y0, x1, y1), one pixel per square."""
    x0, y0, x1, y1 = bounds
    H, W = img.shape[:2]
    if mask:
        base = Image.fromarray((img > 0).astype(np.uint8), 'P')
        base.putpalette([0, 0, 0, 255, 255, 255] + [0] * 762)
    else:
        base = Image.fromarray(img, 'RGB')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        level = base
        for lv in range(5):
            zf.writestr('%d/' % lv, b'')
            w, h = level.size
            for ty in range((h + 255) // 256):
                for tx in range((w + 255) // 256):
                    tile = level.crop((tx * 256, ty * 256, tx * 256 + 256, ty * 256 + 256))
                    buf = io.BytesIO()
                    if mask and lv == 0:
                        tile.save(buf, 'PNG', transparency=0)    # 0 = no forest, as vanilla's
                    else:
                        tile.save(buf, 'PNG')
                    zf.writestr('%d/tile%dx%d.png' % (lv, tx, ty), buf.getvalue())
            level = level.convert('RGB').resize((max(1, (w + 1) // 2), max(1, (h + 1) // 2)), Image.BOX)
        zf.writestr('pyramid.txt', 'VERSION=1\nbounds=%d %d %d %d\nimageSize=%d %d' % (x0, y0, x1, y1, W, H))


# ------------------------------------------------------------------------------------------------- vectors
RIGHT = {(1, 0): (0, 1), (0, 1): (-1, 0), (-1, 0): (0, -1), (0, -1): (1, 0)}
LEFT = {v: k for k, v in RIGHT.items()}


def _simplify(ring, tol):
    """Drop points on straight runs; with tol > 0 also smooth staircases (Douglas-Peucker, closed ring)."""
    pts = [p for i, p in enumerate(ring)
           if (ring[i - 1][0] - p[0]) * (ring[(i + 1) % len(ring)][1] - p[1]) !=
           (ring[i - 1][1] - p[1]) * (ring[(i + 1) % len(ring)][0] - p[0])]
    if tol <= 0 or len(pts) < 5:
        return pts

    def dp(seq):
        if len(seq) < 3:
            return seq
        (x0, y0), (x1, y1) = seq[0], seq[-1]
        L = math.hypot(x1 - x0, y1 - y0) or 1e-9
        far, k = 0.0, 0
        for i in range(1, len(seq) - 1):
            d = abs((x1 - x0) * (y0 - seq[i][1]) - (x0 - seq[i][0]) * (y1 - y0)) / L
            if d > far:
                far, k = d, i
        if far <= tol:
            return [seq[0], seq[-1]]
        return dp(seq[:k + 1])[:-1] + dp(seq[k:])
    i = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    out = dp(pts[:i + 1])[:-1] + dp(pts[i:] + [pts[0]])[:-1]
    return out if len(out) >= 3 else pts


def _area(r):
    return sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r))) / 2.0


def _inside(pt, ring):
    x, y = pt
    c = False
    for i in range(len(ring)):
        (x0, y0), (x1, y1) = ring[i - 1], ring[i]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / float(y1 - y0):
            c = not c
    return c


def _outlines(mask, tol=0.0):
    """Polygons of a boolean mask (cell-local squares): [[outer ring, hole rings...], ...]. Rings follow the
    squares' edges (diagonal touches kept apart), then lose their straight-run points and, with tol > 0,
    their staircases: one shape per building or road network instead of a pile of rectangles."""
    m = np.pad(np.asarray(mask, bool), 1)
    out_edges = collections.defaultdict(list)          # start corner -> [direction]
    ys, xs = np.nonzero(m[1:-1, 1:-1])
    for y, x in zip(ys.tolist(), xs.tolist()):
        if not m[y, x + 1]:                              # nothing above: edge east along the top
            out_edges[(x, y)].append((1, 0))
        if not m[y + 1, x + 2]:                          # nothing right: edge south
            out_edges[(x + 1, y)].append((0, 1))
        if not m[y + 2, x + 1]:                          # nothing below: edge west
            out_edges[(x + 1, y + 1)].append((-1, 0))
        if not m[y + 1, x]:                              # nothing left: edge north
            out_edges[(x, y + 1)].append((0, -1))
    rings = []
    while out_edges:
        start = next(iter(out_edges))
        d = out_edges[start].pop()
        if not out_edges[start]:
            del out_edges[start]
        ring, v = [start], (start[0] + d[0], start[1] + d[1])
        while v != start or len(ring) < 2:
            opts = out_edges.get(v)
            if not opts:
                break
            for nd in (RIGHT[d], d, LEFT[d]):                  # turn right first: touching corners stay apart
                if nd in opts:
                    break
            else:
                nd = opts[0]
            opts.remove(nd)
            if not opts:
                del out_edges[v]
            ring.append(v)
            d = nd
            v = (v[0] + d[0], v[1] + d[1])
        if len(ring) >= 4:
            rings.append(ring)
    outers, holes = [], []
    for r in rings:
        (outers if _area(r) > 0 else holes).append(r)
    polys = [[_simplify(o, tol)] for o in outers]
    for h in holes:
        probe = ((h[0][0] + h[1][0]) / 2.0 + 0.01, (h[0][1] + h[1][1]) / 2.0 + 0.01)
        for p, o in zip(polys, outers):
            if _inside(probe, o):
                hs = _simplify(h, tol)
                if len(hs) >= 3 and abs(_area(hs)) >= 1:
                    p.append(hs)
                break
    return [p for p in polys if len(p[0]) >= 3 and abs(_area(p[0])) >= 1]


def _rects(mask):
    """Greedy cover of a mask by rectangles -> [(x, y, w, h)]."""
    h, w = mask.shape
    used = np.zeros_like(mask)
    out = []
    for y in range(h):
        x = 0
        row = mask[y] & ~used[y]
        while x < w:
            if row[x]:
                x2 = x
                while x2 < w and row[x2]:
                    x2 += 1
                y2 = y + 1
                while y2 < h and mask[y2, x:x2].all() and not used[y2, x:x2].any():
                    y2 += 1
                used[y:y2, x:x2] = True
                out.append((x, y, x2 - x, y2 - y))
                x = x2
            else:
                x += 1
    return out


def _poly_xml(rings, props):
    s = ['  <feature>', '   <geometry type="Polygon">']
    for ring in rings:
        s.append('    <coordinates>')
        s += ['     <point x="%d" y="%d"/>' % (int(round(px)), int(round(py))) for px, py in ring]
        s.append('    </coordinates>')
    s.append('   </geometry>')
    s.append('   <properties>')
    s += ['    <property name="%s" value="%s"/>' % kv for kv in props]
    s.append('   </properties>')
    s.append('  </feature>')
    return '\n'.join(s)


def _clip(ring, x0, y0, x1, y1):
    """Sutherland-Hodgman clip of a closed ring to a rectangle."""
    def edge(pts, inside, cross):
        out = []
        for i in range(len(pts)):
            a, b = pts[i - 1], pts[i]
            ia, ib = inside(a), inside(b)
            if ib:
                if not ia:
                    out.append(cross(a, b))
                out.append(b)
            elif ia:
                out.append(cross(a, b))
        return out

    def cx(a, b, X):
        t = (X - a[0]) / (b[0] - a[0])
        return (X, a[1] + t * (b[1] - a[1]))

    def cy(a, b, Y):
        t = (Y - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), Y)
    pts = list(ring)
    for inside, cross in ((lambda p: p[0] >= x0, lambda a, b: cx(a, b, x0)), (lambda p: p[0] <= x1, lambda a, b: cx(a, b, x1)),
                          (lambda p: p[1] >= y0, lambda a, b: cy(a, b, y0)), (lambda p: p[1] <= y1, lambda a, b: cy(a, b, y1))):
        if not pts:
            break
        pts = edge(pts, inside, cross)
    return pts


def vanilla_features(vanilla_dir, wx0, wy0):
    """Vanilla's own map features for the 256-cell whose north-west square is (wx0, wy0), clipped to it, in
    that cell's coordinates. (vanilla's worldmap.xml is in 300-square cells.)"""
    import xml.etree.ElementTree as ET
    path = os.path.join(vanilla_dir, 'worldmap.xml')
    out = []
    for ev, el in ET.iterparse(path, events=('end',)):
        if el.tag != 'cell':
            continue
        cx, cy = int(el.get('x')), int(el.get('y'))
        ox, oy = cx * 300, cy * 300
        if ox > wx0 + CELL or oy > wy0 + CELL or ox + 300 < wx0 or oy + 300 < wy0:
            el.clear()
            continue
        for f in el.findall('feature'):
            g = f.find('geometry')
            if g is None or g.get('type') != 'Polygon':
                continue
            rings = []
            for co in g.findall('coordinates'):
                ring = [(ox + float(p.get('x')) - wx0, oy + float(p.get('y')) - wy0) for p in co.findall('point')]
                ring = _clip(ring, 0, 0, CELL, CELL)
                if len(ring) >= 3:
                    rings.append(ring)
            if rings:
                props = [(p.get('name'), p.get('value')) for p in f.find('properties').findall('property')] if f.find('properties') is not None else []
                out.append((rings, props))
        el.clear()
    return out


def write_all(outmap, md, g, infos, label, conn=None, vanilla_dir=None):
    """All the in-game map files for map `md` (and the vanilla cells of the connector, `conn`)."""
    W, H = md.width, md.height
    ox, oy = md.cell_x0 * CELL, md.cell_y0 * CELL
    # ---- rasters of what to draw
    road = {k: Image.new('L', (W, H), 0) for k in ('primary', 'secondary', 'tertiary', 'trail')}
    draws = {k: ImageDraw.Draw(v) for k, v in road.items()}
    for r in g.roads:
        k = HW_CLASS.get(r.get('hw'), 'tertiary')
        if len(r['line']) >= 2:
            draws[k].line(r['line'], fill=1, width=max(1, int(round(r['w']))), joint='curve')
    for f in getattr(g, '_foot', []):
        if not f.get('area') and len(f.get('line', [])) >= 2:
            draws['trail'].line(f['line'], fill=1, width=max(1, int(round(f['w']))))
    masks = {k: np.array(v, bool) & (g.B == 0) for k, v in road.items()}
    # a class keeps only what no bigger road already covers
    taken = np.zeros((H, W), bool)
    for k in ('primary', 'secondary', 'tertiary', 'trail'):
        masks[k] &= ~taken
        taken |= masks[k]
    from ground import T_WATER
    water = (g.G == T_WATER) & (g.B == 0)
    cat = np.zeros((H, W), np.uint8)
    cats = sorted(set(USE_CAT.values()))
    for bid, info in infos.items():
        cat[g.B == bid] = 1 + cats.index(USE_CAT.get(info['use'], 'yes'))
    # ---- worldmap.xml, cell by cell
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<world version="1.0">']
    nfeat = 0
    for cj in range((H + CELL - 1) // CELL):
        for ci in range((W + CELL - 1) // CELL):
            sl = (slice(cj * CELL, (cj + 1) * CELL), slice(ci * CELL, (ci + 1) * CELL))
            feats = []
            for k in ('primary', 'secondary', 'tertiary', 'trail'):
                feats += [(p, [('highway', k)]) for p in _outlines(masks[k][sl], 1.0)]
            feats += [(p, [('water', 'river')]) for p in _outlines(water[sl], 1.0)]
            c = cat[sl]
            for v in range(1, len(cats) + 1):
                feats += [(p, [('building', cats[v - 1])]) for p in _outlines(c == v, 1.0)]   # stepped walls: straight
            if not feats:
                continue
            out.append(' <cell x="%d" y="%d">' % (md.cell_x0 + ci, md.cell_y0 + cj))
            out += [_poly_xml(r, p) for r, p in feats]
            out.append(' </cell>')
            nfeat += len(feats)
    # the vanilla cells of the connector: vanilla's features, plus the new stretch of road
    if conn is not None:
        import vanilla_import
        vanilla_dir = vanilla_dir or vanilla_import.vanilla_dir()
        for ci in range(conn.width // CELL):
            cx, cy = conn.cell_x0 + ci, conn.cell_y0
            feats = vanilla_features(vanilla_dir, cx * CELL, cy * CELL)
            asphalt = np.zeros((CELL, CELL), bool)
            for (x, y, z), t in conn.squares.items():
                if z == 0 and ci * CELL <= x < (ci + 1) * CELL and t and t[0].startswith('blends_street'):
                    asphalt[y, x - ci * CELL] = True
            feats += [(p, [('highway', 'secondary')]) for p in _outlines(asphalt, 1.0)]
            out.append(' <cell x="%d" y="%d">' % (cx, cy))
            out += [_poly_xml(r, p) for r, p in feats]
            out.append(' </cell>')
            nfeat += len(feats)
    out.append('</world>')
    open(os.path.join(outmap, 'worldmap.xml'), 'w', encoding='utf8').write('\n'.join(out) + '\n')
    # the game reads the binary form when it is there; Build 42's XML reader drops every shape (worldmapbin.py)
    import worldmapbin
    worldmapbin.from_xml(os.path.join(outmap, 'worldmap.xml'))
    # ---- streets.xml
    named = collections.defaultdict(list)
    for r in g.roads:
        if r.get('name') and len(r['line']) >= 2:
            named[r['name']].append(r)
    s = ['<streets version="1">']
    for name, rs in sorted(named.items()):
        for r in rs:
            s.append('    <street name="%s" width="%d">' % (name.replace('&', '&amp;').replace('"', '&quot;'), max(2, int(round(r['w'])))))
            s.append('        <points>')
            s += ['            <point x="%.1f" y="%.1f"/>' % (ox + px, oy + py) for px, py in r['line']]
            s.append('        </points>')
            s.append('    </street>')
    s.append('</streets>')
    open(os.path.join(outmap, 'streets.xml'), 'w', encoding='utf8').write('\n'.join(s) + '\n')
    # ---- the town label
    lua = ['return function(mapUI)', '\tlocal mapAPI = mapUI.javaObject:getAPIv3()', '\tlocal symbolsAPI = mapAPI:getSymbolsAPIv2()',
           '\tlocal symbol = symbolsAPI:addUntranslatedText("%s", "text-town", %d, %d)' % (label.replace('"', ''), ox + W // 2, oy + H // 2),
           '\tsymbol:setRGBA(0.000, 0.000, 0.000, 0.000)', '\tsymbol:setScale(4.500)', '\tsymbol:setAnchor(0.50, 0.50)',
           '\tsymbol:setRotation(0.0)', '\tsymbol:setMatchPerspective(true)', '\tsymbol:setApplyZoom(true)',
           '\tsymbol:setMinZoom(0.00)', '\tsymbol:setMaxZoom(13.00)', '\tsymbol:setUserDefined(false)', 'end', '']
    open(os.path.join(outmap, 'worldmap-annotations.lua'), 'w', encoding='utf8').write('\n'.join(lua))
    # ---- painted terrain and forest
    img = paint(md.squares, md.room_of, W, H)
    write_pyramid(os.path.join(outmap, 'pyramid.zip'), img, (ox, oy, ox + W, oy + H))
    fm = forest_mask(md.squares, W, H).astype(np.uint8)
    write_pyramid(os.path.join(outmap, 'forest.pyramid.zip'), fm, (ox, oy, ox + W, oy + H), mask=True)
    print('world map: %d features, %d named streets, painted %dx%d' % (nfeat, len(named), W, H))


if __name__ == '__main__':
    learn_colors()
