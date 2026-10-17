"""Turn raw Overpass JSON into projected, tile-space features."""
import math
from dataclasses import dataclass, field


class Proj:
    """Local equirectangular projection around (lat0, lon0), optionally rotated.

    Tile space: x right, y down, 1 unit = `scale` metres, (width/2, height/2) = the centre.
    `rot` is the compass-style tilt of the map in degrees: the map's x axis points `rot` degrees
    anticlockwise from east. With rot = 0 the top of the map is north. A city whose street grid is
    turned (downtown Austin: about -16) is rotated so its streets run along the tile axes; see
    grid_angle().
    """

    def __init__(self, lat0, lon0, width, height, scale=1.0, rot=0.0):
        self.lat0, self.lon0 = float(lat0), float(lon0)
        self.width, self.height = int(width), int(height)
        self.scale, self.rot = scale, float(rot)
        self.kx = 111320.0 * math.cos(math.radians(self.lat0)) / scale
        self.ky = 110574.0 / scale
        self.c, self.s = math.cos(math.radians(self.rot)), math.sin(math.radians(self.rot))

    def xy(self, lat, lon):
        e = (lon - self.lon0) * self.kx
        n = (lat - self.lat0) * self.ky
        return (e * self.c + n * self.s + self.width / 2.0, e * self.s - n * self.c + self.height / 2.0)

    def latlon(self, x, y):
        x -= self.width / 2.0
        y -= self.height / 2.0
        e = x * self.c + y * self.s
        n = x * self.s - y * self.c
        return (self.lat0 + n / self.ky, self.lon0 + e / self.kx)

    def bbox(self, margin=8.0):
        """(south, west, north, east) covering the whole (rotated) map plus `margin` tiles, rounded
        outwards to 1e-4 degrees so the download cache key is stable."""
        pts = [self.latlon(x, y) for x in (-margin, self.width + margin) for y in (-margin, self.height + margin)]
        lats, lons = [p[0] for p in pts], [p[1] for p in pts]
        return (math.floor(min(lats) * 1e4) / 1e4, math.floor(min(lons) * 1e4) / 1e4,
                math.ceil(max(lats) * 1e4) / 1e4, math.ceil(max(lons) * 1e4) / 1e4)


GRID_ROADS = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'residential', 'unclassified', 'living_street'}


def grid_angle(js, lat0, lon0):
    """Dominant direction of the street grid, in degrees within (-45, 45]: the `rot` that lines the
    streets up with the tile axes. Length-weighted over street segments and building walls, using
    the 4-theta trick so that perpendicular directions reinforce each other.
    Returns (angle, strength) with strength 0..1 (1 = a perfect grid, ~0 = no preferred direction)."""
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110574.0
    sx = sy = tot = 0.0
    for el in js['elements']:
        t = el.get('tags', {}) or {}
        geoms = []
        if el['type'] == 'way' and 'geometry' in el:
            if t.get('highway') in GRID_ROADS:
                geoms.append((el['geometry'], 3.0))
            elif 'building' in t:
                geoms.append((el['geometry'], 1.0))
        for geom, weight in geoms:
            for a, b in zip(geom, geom[1:]):
                e = (b['lon'] - a['lon']) * kx
                n = (b['lat'] - a['lat']) * ky
                ln = math.hypot(e, n)
                if ln < 2.0:
                    continue
                th = math.atan2(n, e)
                sx += weight * ln * math.cos(4 * th)
                sy += weight * ln * math.sin(4 * th)
                tot += weight * ln
    if tot == 0:
        return 0.0, 0.0
    return math.degrees(math.atan2(sy, sx) / 4.0), math.hypot(sx, sy) / tot


@dataclass
class Feature:
    kind: str
    tags: dict
    rings: list = field(default_factory=list)   # [(outer, [holes...])] each ring = list[(x,y)]
    line: list = field(default_factory=list)    # polyline for ways
    pt: tuple = None


def _pts(geom, proj):
    return [proj.xy(g['lat'], g['lon']) for g in geom]


def _stitch(ways):
    """Join open way fragments into closed rings."""
    ways = [list(w) for w in ways if len(w) > 1]
    rings = []
    while ways:
        cur = ways.pop(0)
        grew = True
        while grew and cur[0] != cur[-1]:
            grew = False
            for i, w in enumerate(ways):
                if w[0] == cur[-1]:
                    cur += w[1:]
                elif w[-1] == cur[-1]:
                    cur += w[::-1][1:]
                elif w[-1] == cur[0]:
                    cur = w[:-1] + cur
                elif w[0] == cur[0]:
                    cur = w[::-1][:-1] + cur
                else:
                    continue
                ways.pop(i)
                grew = True
                break
        if len(cur) >= 4 and cur[0] == cur[-1]:
            rings.append(cur)
    return rings


def load(js, proj):
    feats = []
    seen = set()
    for el in js['elements']:
        t = el.get('tags', {}) or {}
        typ = el['type']
        if (typ, el.get('id')) in seen:              # the same element from two queries (a fuel station's shop)
            continue
        seen.add((typ, el.get('id')))
        if typ == 'node':
            if 'lat' in el:
                feats.append(Feature('node', t, pt=proj.xy(el['lat'], el['lon'])))
        elif typ == 'way' and 'geometry' in el:
            pts = _pts(el['geometry'], proj)
            closed = len(pts) > 3 and pts[0] == pts[-1]
            f = Feature('way', t, line=pts)
            if closed:
                f.rings = [(pts, [])]
            feats.append(f)
        elif typ == 'relation' and 'members' in el:
            outer, inner = [], []
            for m in el['members']:
                if m.get('type') == 'way' and 'geometry' in m:
                    (inner if m.get('role') == 'inner' else outer).append(
                        [(g['lat'], g['lon']) for g in m['geometry']])
            o = _stitch(outer)
            i = _stitch(inner)
            rings = []
            for r in o:
                rp = [proj.xy(la, lo) for la, lo in r]
                holes = []
                for h in i:
                    hp = [proj.xy(la, lo) for la, lo in h]
                    if _inside(hp[0], rp):
                        holes.append(hp)
                rings.append((rp, holes))
            if rings:
                feats.append(Feature('rel', t, rings=rings))
    return feats


def _inside(p, ring):
    x, y = p
    c = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def point_in_rings(p, rings):
    for outer, holes in rings:
        if _inside(p, outer) and not any(_inside(p, h) for h in holes):
            return True
    return False
