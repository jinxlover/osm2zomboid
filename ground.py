"""Rasterise OpenStreetMap features into terrain classes, building footprints and object lists.

This is the one place where OSM tags are read for the outdoors. Everything is in tile space (1 unit = one
square, x east, y south); terrain.py turns the result into game tiles.

  G        terrain class per square (T_* below)
  B        building label per square (0 = none); bfeat[label] = the OSM feature
  V        vegetation zone per square (V_*): where trees / bushes grow and how densely
  PK       label of the surface car park a square belongs to (0 = none)
  street   True on vehicle carriageways (not car parks); main_street: the same without alleys and driveways
  roads    [{line, w, hw, cls, lanes, oneway, parking, name}] vehicle roads, for lane markings
  crossings[(x, y)] pedestrian crossings (on the carriageway)
  pois     [(kind, x, y, tags)] street objects: tree lamp signal stop bench bus_stop bin postbox vending
  barriers [(kind, line, tags)] fences, walls, hedges
  entrances[(x, y, tags)] mapped building entrances
"""
import random
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

(T_PAVE, T_GRASS, T_ASPHALT, T_PLAZA, T_DIRT, T_LAWN, T_GRAVEL, T_SAND, T_WATER, T_CONCRETE, T_DIRTROAD,
 T_BRICK, T_FLOWERBED) = range(13)
V_NONE, V_PARK, V_FOREST, V_SCRUB, V_LAWN = range(5)

HW_WIDTH = {'motorway': 14, 'trunk': 12, 'primary': 12, 'secondary': 10, 'tertiary': 9, 'residential': 8,
            'unclassified': 8, 'living_street': 6, 'service': 5, 'motorway_link': 7, 'trunk_link': 7,
            'primary_link': 7, 'secondary_link': 7, 'tertiary_link': 6, 'road': 7, 'busway': 6, 'track': 3}
FOOT_WIDTH = {'footway': 2, 'path': 2, 'cycleway': 2, 'steps': 2, 'pedestrian': 5, 'bridleway': 2}
SIDEWALK_ROADS = {'primary', 'secondary', 'tertiary', 'residential', 'unclassified', 'living_street', 'trunk',
                  'primary_link', 'secondary_link', 'tertiary_link', 'road'}
SKIP_HW = {'construction', 'proposed', 'elevator', 'corridor', 'platform', 'bus_stop', 'street_lamp',
           'traffic_signals', 'stop', 'crossing', 'give_way', 'turning_circle', 'mini_roundabout',
           'motorway_junction', 'turning_loop', 'speed_camera', 'milestone', 'rest_area', 'services', 'raceway',
           'abandoned', 'razed', 'planned', 'via_ferrata'}
# OSM surface=* -> terrain class
SURFACE = {'asphalt': T_ASPHALT, 'chipseal': T_ASPHALT, 'concrete': T_CONCRETE, 'concrete:plates': T_CONCRETE,
           'concrete:lanes': T_CONCRETE, 'paving_stones': T_PLAZA, 'sett': T_BRICK, 'cobblestone': T_BRICK,
           'unhewn_cobblestone': T_BRICK, 'bricks': T_BRICK, 'brick': T_BRICK, 'gravel': T_GRAVEL,
           'fine_gravel': T_GRAVEL, 'compacted': T_GRAVEL, 'pebblestone': T_GRAVEL, 'rock': T_GRAVEL,
           'unpaved': T_DIRTROAD, 'dirt': T_DIRTROAD, 'ground': T_DIRTROAD, 'earth': T_DIRTROAD,
           'mud': T_DIRTROAD, 'grass': T_GRASS, 'sand': T_SAND, 'woodchips': T_DIRT, 'wood': T_PLAZA}
# area tags -> (terrain, vegetation zone)
LANDUSE = {'residential': (T_LAWN, V_LAWN), 'commercial': (T_CONCRETE, V_NONE), 'retail': (T_CONCRETE, V_NONE),
           'industrial': (T_CONCRETE, V_NONE), 'garages': (T_CONCRETE, V_NONE), 'construction': (T_GRAVEL, V_NONE),
           'brownfield': (T_GRAVEL, V_NONE), 'railway': (T_GRAVEL, V_NONE), 'quarry': (T_GRAVEL, V_NONE),
           'grass': (T_LAWN, V_LAWN), 'village_green': (T_LAWN, V_PARK), 'cemetery': (T_LAWN, V_PARK),
           'recreation_ground': (T_LAWN, V_LAWN), 'religious': (T_LAWN, V_LAWN), 'meadow': (T_GRASS, V_NONE),
           'greenfield': (T_GRASS, V_NONE), 'farmland': (T_GRASS, V_NONE), 'farmyard': (T_DIRT, V_NONE),
           'orchard': (T_GRASS, V_PARK), 'vineyard': (T_GRASS, V_NONE), 'allotments': (T_GRASS, V_SCRUB),
           'forest': (T_GRASS, V_FOREST), 'flowerbed': (T_FLOWERBED, V_NONE), 'plant_nursery': (T_GRASS, V_SCRUB)}
LEISURE = {'park': (T_LAWN, V_PARK), 'garden': (T_LAWN, V_PARK), 'playground': (T_LAWN, V_LAWN),
           'dog_park': (T_LAWN, V_LAWN), 'common': (T_LAWN, V_PARK), 'pitch': (T_LAWN, V_NONE),
           'golf_course': (T_LAWN, V_PARK), 'recreation_ground': (T_LAWN, V_LAWN),
           'nature_reserve': (T_GRASS, V_SCRUB), 'outdoor_seating': (T_PLAZA, V_NONE)}
NATURAL = {'wood': (T_GRASS, V_FOREST), 'scrub': (T_GRASS, V_SCRUB), 'shrubbery': (T_LAWN, V_SCRUB),
           'heath': (T_GRASS, V_SCRUB), 'grassland': (T_GRASS, V_NONE), 'wetland': (T_GRASS, V_SCRUB),
           'tree_row': (T_GRASS, V_FOREST), 'water': (T_WATER, V_NONE), 'sand': (T_SAND, V_NONE),
           'beach': (T_SAND, V_NONE), 'bare_rock': (T_GRAVEL, V_NONE), 'scree': (T_GRAVEL, V_NONE),
           'shingle': (T_GRAVEL, V_NONE), 'mud': (T_DIRT, V_NONE)}
WATERWAY_WIDTH = {'river': 14, 'canal': 8, 'stream': 2, 'ditch': 1, 'drain': 1}
POI_KIND = {('highway', 'street_lamp'): 'lamp', ('highway', 'traffic_signals'): 'signal', ('highway', 'stop'): 'stop',
            ('highway', 'bus_stop'): 'bus_stop', ('amenity', 'bench'): 'bench', ('amenity', 'waste_basket'): 'bin',
            ('amenity', 'post_box'): 'postbox', ('amenity', 'vending_machine'): 'vending',
            ('natural', 'tree'): 'tree'}
NO = ('no', 'none', 'separate', 'no_parking', 'no_stopping', 'no_standing')


def _num(v, default=None):
    try:
        return float(str(v).replace(',', '.').split()[0].split(';')[0])
    except (TypeError, ValueError, IndexError):
        return default


def parking_sides(tags):
    """Number of sides (0-2) with on-street parking, from either OSM parking tagging scheme."""
    def yes(v):
        return v is not None and v not in NO
    if yes(tags.get('parking:both', tags.get('parking:lane:both'))):
        return 2
    return sum(yes(tags.get('parking:' + s, tags.get('parking:lane:' + s))) for s in ('left', 'right'))


def road_width(tags):
    w = HW_WIDTH.get(tags.get('highway'), 5)
    tw = _num(tags.get('width'))
    if tw:
        return max(2.0, min(40.0, tw))
    lanes = _num(tags.get('lanes'))
    if lanes:
        w = max(w, lanes * 3.3)
    return w + 2.4 * parking_sides(tags)


def _area(ring):
    return abs(sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1))) / 2


class Ground:
    def __init__(self, feats, proj, seed=1, background='auto'):
        self.proj = proj
        self.W, self.H = proj.width, proj.height
        self.rng = random.Random(seed)
        self.feats = feats
        self.bfeat = {}
        self.density = None
        self.roads, self.crossings, self.pois, self.barriers, self.entrances = [], [], [], [], []
        size = (self.W, self.H)
        self._g = Image.new('L', size, T_GRASS)
        self._v = Image.new('L', size, V_NONE)
        self._pk = Image.new('L', size, 0)
        self._st = Image.new('L', size, 0)
        self._ms = Image.new('L', size, 0)
        self._bld = Image.new('I', size, 0)
        self._buildings()
        self.B = np.array(self._bld, dtype=np.int32)
        # an outline drawn at an angle can leave single squares inside it unfilled (a hole in the floor plan):
        # a gap with the same building on both sides is that building's
        B = self.B
        for _ in range(2):
            gap = B[:, 1:-1] == 0
            same = (B[:, :-2] == B[:, 2:]) & (B[:, :-2] > 0) & gap
            B[:, 1:-1][same] = B[:, :-2][same]
            gap = B[1:-1, :] == 0
            same = (B[:-2, :] == B[2:, :]) & (B[:-2, :] > 0) & gap
            B[1:-1, :][same] = B[:-2, :][same]
        # how built up the neighbourhood is (share of a 60 m square covered by buildings: a downtown block
        # is 0.4 and more, a street of houses 0.1 to 0.2) and the mapped land use, for untagged buildings
        self.coverage = ndi.uniform_filter((self.B > 0).astype(np.float32), size=61, mode='nearest')
        self.landuse = self._landuse()
        self._collect_roads()
        self._background(background)
        self._areas()
        self._draw_roads()
        self.G = np.array(self._g, dtype=np.uint8)
        self.V = np.array(self._v, dtype=np.uint8)
        self.street = np.array(self._st, dtype=np.uint8) > 0
        self.main_street = np.array(self._ms, dtype=np.uint8) > 0      # streets proper: no alleys, driveways, tracks
        self._trim_buildings()
        pk = ((np.array(self._pk, dtype=np.uint8) > 0) & (self.B == 0) & ~self.street
              & np.isin(self.G, [T_ASPHALT, T_CONCRETE]))
        self.PK = ndi.label(pk)[0]
        self.G[self.PK > 0] = T_ASPHALT                      # one surface per lot, not a patchwork of polygons
        self.V[(self.B > 0) | self.street] = V_NONE
        self.forecourts = self._forecourts()
        self.asph = self.street           # old name, kept for callers that measure distance to the road
        self._points()

    def _forecourts(self):
        """A fuel station's forecourt is paved, mapped as such or not (left to the background it would be lawn
        and get no pumps): the station's own area (a canopy is one) or, for a point or a station mapped as its
        shop building, the ground round it. -> [(feature, (ys, xs) of the forecourt)]"""
        out = []
        H, W = self.G.shape
        soft = np.isin(self.G, [T_GRASS, T_LAWN, T_DIRT, T_SAND, T_GRAVEL])
        for f in self.feats:
            if f.tags.get('amenity') != 'fuel':
                continue
            if f.rings and f.tags.get('building') in (None, 'roof', 'canopy', 'carport'):
                xs = [p[0] for outer, _ in f.rings for p in outer]
                ys = [p[1] for outer, _ in f.rings for p in outer]
                cx, cy, r = sum(xs) / len(xs), sum(ys) / len(ys), 0
                im = Image.new('L', (W, H), 0)
                for outer, _ in f.rings:
                    ImageDraw.Draw(im).polygon(outer, fill=1)
                m = ndi.binary_dilation(np.array(im, bool), iterations=2)
            else:
                if f.rings:
                    xs = [p[0] for outer, _ in f.rings for p in outer]
                    ys = [p[1] for outer, _ in f.rings for p in outer]
                    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
                elif f.pt:
                    cx, cy = f.pt
                else:
                    continue
                yy, xx = np.ogrid[:H, :W]
                m = (xx - cx) ** 2 + (yy - cy) ** 2 <= 14 ** 2
            m &= (self.B == 0) & ~self.street
            pave = m & soft
            self.G[pave] = T_CONCRETE
            self.V[pave] = V_NONE
            out.append((f, np.nonzero(m)))
        return out

    def _trim_buildings(self):
        """Roads and sidewalks win where a mapped outline reaches over them: vanilla never builds on a
        sidewalk. The squares go back to the street; a building cut in two keeps its biggest piece."""
        over = (self.B > 0) & (self.street | getattr(self, 'sidewalk_band', np.zeros_like(self.street)))
        # nor on the map's last row or column: a square's south and east walls stand on the next square, which
        # is outside the map there (the building would be open to the vanilla cells beside it)
        edge = np.zeros_like(over)
        edge[-1, :] = edge[:, -1] = True
        self.edge_trimmed = int(((self.B > 0) & edge).sum())
        over |= (self.B > 0) & edge
        self.trimmed = int(over.sum())
        if not self.trimmed:
            return
        hit = np.unique(self.B[over])
        self.B[over] = 0
        for bid in hit.tolist():
            m = self.B == bid
            comp, n = ndi.label(m)
            if n > 1:
                sizes = np.bincount(comp.ravel())[1:]
                self.B[m & (comp != int(np.argmax(sizes)) + 1)] = 0

    # ------------------------------------------------------------------ buildings
    def _buildings(self):
        d = ImageDraw.Draw(self._bld)
        feats = [f for f in self.feats if 'building' in f.tags and f.rings
                 and f.tags.get('building') not in ('roof', 'no', 'carport', 'construction', 'ruins', 'parking')
                 and not (f.tags.get('amenity') == 'parking' and f.tags.get('parking') == 'multi-storey')
                 and f.tags.get('layer') not in ('-1', '-2') and f.tags.get('location') != 'underground']
        bid = 0
        for f in sorted(feats, key=lambda f: -_area(f.rings[0][0])):     # larger first, small ones on top
            bid += 1
            self.bfeat[bid] = f
            for outer, holes in f.rings:
                d.polygon(outer, fill=bid)
                for h in holes:
                    d.polygon(h, fill=0)

    # ------------------------------------------------------------------ roads
    def _collect_roads(self):
        self._foot = []
        for f in self.feats:
            t = f.tags
            hw = t.get('highway')
            if f.kind != 'way' or not hw or hw in SKIP_HW or len(f.line) < 2:
                continue
            if t.get('tunnel') in ('yes', 'building_passage', 'culvert') or t.get('indoor') == 'yes':
                continue
            if _num(t.get('layer'), 0) < 0 or t.get('location') == 'underground':
                continue
            is_area = t.get('area') == 'yes' and bool(f.rings)
            if hw in FOOT_WIDTH:
                if hw in ('footway', 'path') and t.get('footway') == 'crossing':
                    mid = f.line[len(f.line) // 2]
                    if len(f.line) == 2:
                        mid = ((f.line[0][0] + f.line[1][0]) / 2, (f.line[0][1] + f.line[1][1]) / 2)
                    self.crossings.append(mid)
                    continue
                if t.get('footway') == 'traffic_island' or (t.get('covered') == 'yes' and hw == 'footway'):
                    continue
                default = T_PLAZA if hw == 'pedestrian' else (T_DIRTROAD if hw in ('path', 'bridleway') else T_PAVE)
                cls = SURFACE.get(t.get('surface'), default)
                if cls in (T_ASPHALT, T_CONCRETE):          # a paved footpath is still a footpath
                    cls = T_PAVE
                w = FOOT_WIDTH[hw] + (1 if t.get('footway') == 'sidewalk' else 0)
                tw = _num(t.get('width'))
                if tw:
                    w = max(1.0, min(12.0, tw))
                self._foot.append(dict(line=f.line, w=w, cls=cls, hw=hw, area=is_area, rings=f.rings))
                continue
            cls = SURFACE.get(t.get('surface'), T_DIRTROAD if hw == 'track' else T_ASPHALT)
            if cls in (T_PLAZA, T_BRICK):
                cls = T_ASPHALT
            elif cls in (T_GRASS, T_SAND, T_DIRT):
                cls = T_DIRTROAD
            lanes = _num(t.get('lanes'))
            oneway = (t.get('oneway') in ('yes', '1', '-1', 'true') or t.get('junction') == 'roundabout'
                      or hw == 'motorway')
            self.roads.append(dict(line=f.line, w=road_width(t), hw=hw, cls=cls, lanes=int(lanes) if lanes else None,
                                   oneway=oneway, parking=parking_sides(t) > 0, name=t.get('name'),
                                   sidewalk=t.get('sidewalk', t.get('sidewalk:both')), area=is_area, rings=f.rings))

    @staticmethod
    def _stroke(d, line, w, fill):
        w = max(1, int(round(w)))
        d.line(line, fill=fill, width=w, joint='curve')
        if w >= 3:                                   # round the ends so ways meeting at an angle leave no notch
            r = w / 2.0 - 0.5
            for (x, y) in (line[0], line[-1]):
                d.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    def _background(self, mode):
        """What lies between the mapped features: paved lots downtown, lawns in suburbs, grass elsewhere."""
        if mode == 'grass':
            return
        if mode == 'concrete':
            self._g.paste(T_CONCRETE, (0, 0, self.W, self.H))
            return
        tmp = Image.new('L', (self.W, self.H), 0)
        d = ImageDraw.Draw(tmp)
        for r in self.roads:
            if not r['area']:
                self._stroke(d, r['line'], r['w'], 1)
        built = (np.array(tmp, dtype=np.uint8) > 0) | (self.B > 0)
        dens = ndi.uniform_filter(built.astype(np.float32), size=97, mode='nearest')
        self.density = dens
        bg = np.full((self.H, self.W), T_GRASS, np.uint8)
        bg[dens > 0.10] = T_LAWN
        bg[dens > 0.30] = T_CONCRETE
        self._g = Image.fromarray(bg)

    LU_CODE = {'residential': 1, 'commercial': 2, 'retail': 2, 'industrial': 3, 'garages': 3, 'railway': 3,
               'religious': 4}

    def _landuse(self):
        """0 unknown, 1 residential, 2 commercial / retail, 3 industrial, 4 religious; big areas first."""
        im = Image.new('L', (self.W, self.H), 0)
        d = ImageDraw.Draw(im)
        polys = [(f, self.LU_CODE[f.tags['landuse']]) for f in self.feats
                 if f.tags.get('landuse') in self.LU_CODE and f.rings]
        for f, c in sorted(polys, key=lambda fc: -sum(_area(o) for o, _ in fc[0].rings)):
            for outer, _ in f.rings:
                d.polygon(outer, fill=c)
        return np.array(im, dtype=np.uint8)

    def _areas(self):
        g, v, pk = ImageDraw.Draw(self._g), ImageDraw.Draw(self._v), ImageDraw.Draw(self._pk)
        items = []
        for f in self.feats:
            t = f.tags
            garage = t.get('building') == 'parking' or (t.get('amenity') == 'parking' and t.get('parking') == 'multi-storey')
            if 'building' in t and not garage:
                continue
            if (f.kind == 'way' and t.get('waterway') in WATERWAY_WIDTH and len(f.line) >= 2 and not f.rings
                    and t.get('tunnel') not in ('yes', 'culvert')):
                items.append((0, 'line', f, (T_WATER, V_NONE), _num(t.get('width'), WATERWAY_WIDTH[t['waterway']])))
                continue
            if not f.rings:
                continue
            cls = None
            park = False
            if t.get('leisure') == 'swimming_pool':
                indoor = t.get('location') in ('roof', 'indoor') or t.get('indoor') == 'yes'
                cls = None if indoor else (T_WATER, V_NONE)
            elif t.get('natural') in NATURAL:
                cls = NATURAL[t['natural']]
            elif t.get('leisure') in LEISURE:
                cls = LEISURE[t['leisure']]
            elif t.get('amenity') == 'parking' or garage:
                if t.get('parking') not in ('underground', 'rooftop', 'sheds', 'carports',
                                            'garage_boxes'):
                    c = SURFACE.get(t.get('surface'), T_ASPHALT)
                    cls = (c if c in (T_ASPHALT, T_CONCRETE, T_GRAVEL, T_DIRTROAD) else T_ASPHALT, V_NONE)
                    park = t.get('parking') != 'street_side'
            elif t.get('amenity') == 'fuel':                  # a forecourt: asphalt for the pumps (terrain.place_fuel)
                cls = (SURFACE.get(t.get('surface'), T_ASPHALT), V_NONE)
            elif t.get('place') == 'square' or (t.get('highway') == 'pedestrian' and t.get('area') == 'yes'):
                cls = (SURFACE.get(t.get('surface'), T_PLAZA), V_NONE)
            elif t.get('landuse') in LANDUSE:
                cls = LANDUSE[t['landuse']]
            elif t.get('amenity') in ('school', 'university', 'college', 'kindergarten', 'hospital'):
                cls = (T_LAWN, V_LAWN)
            if cls is None:
                continue
            # big background areas first, small detailed ones (flowerbeds, pools, car parks) on top
            items.append((-sum(_area(o) for o, _ in f.rings), 'poly', f, cls, park))
        for _, kind, f, (c, veg), extra in sorted(items, key=lambda it: it[0]):
            if kind == 'line':
                self._stroke(g, f.line, extra, c)
                continue
            for outer, holes in f.rings:
                if not holes:
                    g.polygon(outer, fill=c)
                    v.polygon(outer, fill=veg)
                    pk.polygon(outer, fill=1 if extra else 0)
                    continue
                # polygon with holes (an island in a lake, a courtyard): paint through a mask
                m = Image.new('L', (self.W, self.H), 0)
                md = ImageDraw.Draw(m)
                md.polygon(outer, fill=255)
                for h in holes:
                    md.polygon(h, fill=0)
                self._g.paste(c, (0, 0), m)
                self._v.paste(veg, (0, 0), m)
                self._pk.paste(1 if extra else 0, (0, 0), m)

    def _draw_roads(self):
        # sidewalk bands along streets first; the carriageway is drawn on top of them
        band = Image.new('L', (self.W, self.H), 0)
        untagged = Image.new('L', (self.W, self.H), 0)
        bd, bu = ImageDraw.Draw(band), ImageDraw.Draw(untagged)
        for r in self.roads:
            if r['hw'] not in SIDEWALK_ROADS or r['area'] or r['cls'] not in (T_ASPHALT, T_CONCRETE):
                continue
            sw = r['sidewalk']
            if sw in ('no', 'none'):
                continue
            self._stroke(bd if sw else bu, r['line'], r['w'] + (4 if sw == 'separate' else 6), 1)
        mask = np.array(band, dtype=np.uint8) > 0
        umask = np.array(untagged, dtype=np.uint8) > 0
        if self.density is not None:
            umask &= self.density > 0.10              # untagged country roads get no sidewalk
        mask |= umask
        self.sidewalk_band = mask
        gm = np.array(self._g, dtype=np.uint8)
        gm[mask] = T_PAVE
        self._g = Image.fromarray(gm)
        vm = np.array(self._v, dtype=np.uint8)
        vm[mask] = V_NONE
        self._v = Image.fromarray(vm)
        g, v, st = ImageDraw.Draw(self._g), ImageDraw.Draw(self._v), ImageDraw.Draw(self._st)
        ms = ImageDraw.Draw(self._ms)
        # footpaths, then vehicle roads from narrow to wide so the main road wins at junctions
        for f in self._foot:
            if f['area']:
                for outer, _ in f['rings']:
                    g.polygon(outer, fill=f['cls'])
                    v.polygon(outer, fill=V_NONE)
            else:
                w = max(1, int(round(f['w'])))
                g.line(f['line'], fill=f['cls'], width=w, joint='curve')
                v.line(f['line'], fill=V_NONE, width=w, joint='curve')
        for r in sorted(self.roads, key=lambda r: r['w']):
            if r['area']:
                for outer, _ in r['rings']:
                    g.polygon(outer, fill=r['cls'])
                    st.polygon(outer, fill=1)
            else:
                self._stroke(g, r['line'], r['w'], r['cls'])
                self._stroke(st, r['line'], r['w'], 1)
                if r['hw'] not in ('service', 'track', 'busway'):
                    self._stroke(ms, r['line'], r['w'], 1)
        self.roads = [r for r in self.roads if not r['area']]
        # a wide line drawn at an angle comes out with pinholes: single squares of grass inside a sidewalk.
        # Vanilla paving has none, so a soft square with paving on three or four sides is paved too
        gm = np.array(self._g, dtype=np.uint8)
        pave = gm == T_PAVE
        soft = np.isin(gm, [T_GRASS, T_LAWN, T_DIRT, T_SAND])
        n = np.zeros(gm.shape, np.int8)
        n[1:, :] += pave[:-1, :]
        n[:-1, :] += pave[1:, :]
        n[:, 1:] += pave[:, :-1]
        n[:, :-1] += pave[:, 1:]
        holes = soft & (n >= 3)
        self.pinholes = int(holes.sum())
        gm[holes] = T_PAVE
        self._g = Image.fromarray(gm)
        vm = np.array(self._v, dtype=np.uint8)
        vm[holes] = V_NONE
        self._v = Image.fromarray(vm)

    # ------------------------------------------------------------------ point and line objects
    def _points(self):
        W, H = self.W, self.H
        for f in self.feats:
            t = f.tags
            if f.kind == 'node':
                x, y = f.pt
                if not (0 <= x < W and 0 <= y < H):
                    continue
                if t.get('highway') == 'crossing':
                    self.crossings.append((x, y))
                if 'entrance' in t and t['entrance'] != 'no':
                    self.entrances.append((x, y, t))
                for (k, val), kind in POI_KIND.items():
                    if t.get(k) == val:
                        self.pois.append((kind, x, y, t))
                        break
            elif f.kind == 'way' and 'barrier' in t and len(f.line) >= 2:
                self.barriers.append((t['barrier'], f.line, t))
            elif f.kind == 'way' and t.get('natural') == 'tree_row' and len(f.line) >= 2:
                for (xa, ya), (xb, yb) in zip(f.line, f.line[1:]):
                    n = max(1, int(((xb - xa) ** 2 + (yb - ya) ** 2) ** 0.5 // 6))
                    for i in range(n + 1):
                        self.pois.append(('tree', xa + (xb - xa) * i / n, ya + (yb - ya) * i / n, t))
        self.crossings = [(x, y) for x, y in self.crossings if 0 <= x < W and 0 <= y < H]

    def nearest_road_dist(self):
        return ndi.distance_transform_edt(~self.street)
