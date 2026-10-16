"""objects.lua: the zones the game reads from a map folder, as vanilla's map has them.

  ParkingStall  a 3 x 5 stall where the game parks a car (vanilla: 9,693 of them); name '' = any car
  TownZone      built-up areas (town loot, town foraging, town zombies)
  ZombiesType   what the dead wear and carry around a kind of building: Offices, Bar, Police, CoffeeShop...
  Vegitation    parks and lawns: foraging for herbs and berries
  Forest        woods: forest foraging
  Nav           road surface, where the game's road logic (traffic jams, abandoned cars) looks
Coordinates are world squares.
"""
import os
import numpy as np
from scipy import ndimage as ndi

ZOMBIE_TYPE = {'office': 'Offices', 'civic': 'Offices', 'studio': 'Offices', 'community': 'Offices',
               'restaurant': 'Restaurant', 'bar': 'Bar', 'cafe': 'CoffeeShop', 'police': 'Police',
               'clinic': 'Doctor', 'dentist': 'Doctor', 'bank': 'Bank', 'school': 'School', 'church': 'Church',
               'hotel': 'HotelRich', 'warehouse': 'Factory', 'gym': 'Athletic', 'theatre': 'Theatre',
               'museum': 'StreetRich', 'library': 'Offices', 'post': 'Offices', 'gas': 'Gas2Go',
               'market': 'VariousFoodMarket'}
SHOP_ZOMBIES = {'pharmacy': 'Pharmacist', 'grocery': 'VariousFoodMarket', 'gasstore': 'Gas2Go',
                'butcher': 'Butcher', 'aesthetic': 'Spa', 'pizzawhirled': 'PizzaWhirled', 'jayschicken_dining': 'Jays',
                'spiffo_dining': 'Spiffo', 'gigamart': 'Gigamart', 'nightclub': 'Nightclub', 'bowling': 'Bowling'}


def _rects(mask, minarea=1):
    """Greedy cover of a mask by rectangles -> [(x, y, w, h)]."""
    h, w = mask.shape
    used = np.zeros_like(mask)
    out = []
    for y in range(h):
        x = 0
        while x < w:
            if mask[y, x] and not used[y, x]:
                x2 = x
                while x2 < w and mask[y, x2] and not used[y, x2]:
                    x2 += 1
                y2 = y + 1
                while y2 < h and mask[y2, x:x2].all() and not used[y2, x:x2].any():
                    y2 += 1
                used[y:y2, x:x2] = True
                if (x2 - x) * (y2 - y) >= minarea:
                    out.append((x, y, x2 - x, y2 - y))
                x = x2
            else:
                x += 1
    return out


def _coarse(mask, step):
    """Rectangles of a mask on a coarse grid (step squares), in squares."""
    h, w = mask.shape
    hh, ww = (h + step - 1) // step, (w + step - 1) // step
    pad = np.zeros((hh * step, ww * step), bool)
    pad[:h, :w] = mask
    small = pad.reshape(hh, step, ww, step).mean(axis=(1, 3)) > 0.3
    return [(x * step, y * step, min(w - x * step, ww_ * step), min(h - y * step, hh_ * step))
            for x, y, ww_, hh_ in _rects(small)]


def write_objects(outmap, md, g, infos):
    from ground import T_LAWN, T_GRASS, V_PARK, V_FOREST, V_LAWN
    ox, oy = md.cell_x0 * 256, md.cell_y0 * 256
    rows = []

    def zone(name, typ, x, y, w, h, props=None):
        s = '  { name = "%s", type = "%s", x = %d, y = %d, z = 0, width = %d, height = %d' % (name, typ, ox + x, oy + y, w, h)
        if props:
            s += ', properties = { %s }' % ', '.join('%s = "%s"' % kv for kv in props.items())
        rows.append(s + ' },')
    # cars
    for (x, y, w, h, d) in getattr(g, 'stalls', []) + getattr(g, 'house_stalls', []):    # car parks, driveways
        zone('', 'ParkingStall', x, y, w, h, {'Direction': d})
    # the town
    town = ndi.binary_dilation(g.coverage > 0.04, iterations=8)
    for x, y, w, h in _coarse(town, 16):
        zone('', 'TownZone', x, y, w, h)
    # who the dead were, by building
    for bid, info in infos.items():
        nm = info['names'][0] if info.get('names') else {}
        shop = next((SHOP_ZOMBIES[v] for v in nm.values() if v in SHOP_ZOMBIES), None)
        zt = shop or ZOMBIE_TYPE.get(info['use'])
        if not zt:
            continue
        ys, xs = np.nonzero(info['lab'] > 0)
        x0, y0 = info['ox'] + int(xs.min()) - 2, info['oy'] + int(ys.min()) - 2
        zone(zt, 'ZombiesType', x0, y0, int(xs.max() - xs.min()) + 5, int(ys.max() - ys.min()) + 5)
    # foraging
    open_ground = g.B == 0
    for x, y, w, h in _coarse(open_ground & np.isin(g.V, [V_PARK, V_LAWN]) & np.isin(g.G, [T_LAWN, T_GRASS]), 8):
        zone('', 'Vegitation', x, y, w, h)
    for x, y, w, h in _coarse(open_ground & (g.V == V_FOREST), 8):
        zone('', 'Forest', x, y, w, h)
    # roads
    for x, y, w, h in _rects(g.street, 12):
        zone('', 'Nav', x, y, w, h)
    with open(os.path.join(outmap, 'objects.lua'), 'w', encoding='utf8') as f:
        f.write('objects = {\n' + '\n'.join(rows) + '\n}\n')
    kinds = {}
    for r in rows:
        k = r.split('type = "')[1].split('"')[0]
        kinds[k] = kinds.get(k, 0) + 1
    print('zones:', kinds)
