"""Building generator: footprint mask -> walls, doors, windows, rooms, stairs, roof.

Ground floor: the rooms on the street are units (shop, dining room, bar, lobby), each with its own street
door; the rooms behind belong to the unit they back onto (kitchen, storage, office, bathroom). Offices,
flats and hotels are entered through a lobby, which holds the stairs. Upper floors: corridors with guest
rooms, flats or offices off them. Nobody sleeps on the ground floor of a downtown block."""
import random
from collections import Counter, defaultdict, deque
import numpy as np
from scipy import ndimage as ndi

import tilepalette as T
from furnish import Furnisher
from roofs import Roofer
from vanillawalls import Palette, Look
import pitched

# ---------------------------------------------------------------- classification
# What a building is used for comes from its OSM tags and the shops / amenities mapped inside it. Every
# room name below is one the vanilla map uses, so the game's loot tables and furnish.py both know it.
SHOP_ROOM = {
    'clothes': 'clothesstore', 'fashion': 'clothesstore', 'boutique': 'clothesstore', 'shoes': 'shoestore',
    'books': 'bookstore', 'gift': 'giftstore', 'variety_store': 'giftstore', 'antiques': 'giftstore',
    'second_hand': 'giftstore', 'charity': 'giftstore', 'convenience': 'cornerstore',
    'kiosk': 'conveniencestore', 'newsagent': 'conveniencestore', 'supermarket': 'grocery',
    'greengrocer': 'grocery', 'deli': 'grocery', 'health_food': 'grocery', 'pharmacy': 'pharmacy',
    'chemist': 'pharmacy', 'electronics': 'electronicsstore', 'mobile_phone': 'electronicsstore',
    'computer': 'electronicsstore', 'hifi': 'electronicsstore', 'bakery': 'bakery', 'pastry': 'bakery',
    'butcher': 'butcher', 'seafood': 'butcher', 'hardware': 'toolstore', 'doityourself': 'toolstore',
    'trade': 'toolstore', 'alcohol': 'liquorstore', 'wine': 'liquorstore', 'beverages': 'liquorstore',
    'tobacco': 'tobaccostore', 'cannabis': 'tobaccostore', 'e-cigarette': 'tobaccostore',
    'jewelry': 'jewelrystore', 'watches': 'jewelrystore', 'hairdresser': 'aesthetic', 'beauty': 'aesthetic',
    'tattoo': 'aesthetic', 'cosmetics': 'aesthetic', 'massage': 'aesthetic', 'music': 'musicstore',
    'musical_instrument': 'musicstore', 'toys': 'toystore', 'games': 'toystore', 'sports': 'sportstore',
    'bicycle': 'sportstore', 'furniture': 'furniturestore', 'interior_decoration': 'furniturestore',
    'bed': 'furniturestore', 'kitchen': 'furniturestore', 'art': 'artstore', 'frame': 'artstore',
    'craft': 'artstore', 'pet': 'petstore', 'camera': 'camerastore', 'photo': 'camerastore',
    'outdoor': 'camping', 'video': 'movierental', 'video_games': 'movierental', 'florist': 'gardenstore',
    'garden_centre': 'gardenstore', 'bag': 'bagstore', 'tailor': 'tailoringstore', 'fabric': 'tailoringstore',
    'laundry': 'laundry', 'dry_cleaning': 'laundry', 'optician': 'glassesstore',
    'department_store': 'departmentstore', 'mall': 'departmentstore', 'car_parts': 'carsupply',
    'tyres': 'carsupply', 'car_repair': 'mechanic', 'houseware': 'housewarestore',
    'confectionery': 'candystore', 'chocolate': 'candystore', 'comics': 'comicstore', 'weapons': 'gunstore',
    'hunting': 'gunstore', 'pawnbroker': 'pawnshop', 'copyshop': 'office', 'stationery': 'office',
    'general': 'generalstore', 'carpet': 'furniturestore', 'pawnbroker': 'pawnshop', 'sewing': 'sewingstore',
    'gas': 'gasstore', 'fuel': 'gasstore'}
SHOP_BACK = {'cornerstore': 'cornerstorestorage', 'gasstore': 'gasstorage', 'pawnshop': 'pawnshopstorage',
             'electronicsstore': 'electronicsstorage', 'jewelrystore': 'jewelrystorage', 'bookstore': 'bookstorestorage',
             'furniturestore': 'furniturestorage', 'sportstore': 'sportstorage', 'shoestore': 'shoestorage',
             'grocery': 'grocerystorage', 'pharmacy': 'pharmacystorage', 'bakery': 'bakerykitchen',
             'gunstore': 'gunstorestorage', 'toolstore': 'toolstorestorage', 'clothesstore': 'clothingstorage'}
# cuisine=* -> (dining room, kitchen)
CUISINE = {'pizza': ('pizzawhirled', 'pizzakitchen'), 'burger': ('burgerdining', 'burgerkitchen'),
           'chicken': ('jayschicken_dining', 'jayschicken_kitchen'), 'chinese': ('restaurantdining', 'chinesekitchen'),
           'asian': ('restaurantdining', 'chinesekitchen'), 'seafood': ('restaurantdining', 'seafoodkitchen'),
           'fish_and_chips': ('restaurantdining', 'seafoodkitchen'), 'steak_house': ('westerndining', 'restaurantkitchen'),
           'barbecue': ('westerndining', 'restaurantkitchen'), 'texan': ('westerndining', 'restaurantkitchen'),
           'western': ('westerndining', 'restaurantkitchen'), 'donut': ('donut_dining', 'donut_kitchen'),
           'ice_cream': ('icecream', 'icecreamkitchen'), 'sandwich': ('restaurantdining', 'dinerkitchen'),
           'breakfast': ('restaurantdining', 'dinerkitchen'), 'diner': ('restaurantdining', 'dinerkitchen'),
           'mexican': ('restaurantdining', 'mexicankitchen'), 'italian': ('italianrestaurant', 'italiankitchen'),
           'sushi': ('sushidining', 'sushikitchen'), 'japanese': ('sushidining', 'sushikitchen')}
BAR_AMENITY = {'bar', 'pub', 'nightclub', 'biergarten', 'stripclub', 'music_venue'}
SHOPFRONT = ('retail', 'bar', 'restaurant', 'cafe')
TENANT_RANK = {'bar': 0, 'restaurant': 1, 'cafe': 2, 'retail': 3, 'bank': 4}     # who wins a shared room
FOOD_AMENITY = {'restaurant', 'fast_food', 'food_court'}
# house rooms are partitioned at vanilla's sizes (median living room 29, kitchen and dining room 20, bedroom 15,
# kids' bedroom 12): a real house two or three times a Knox County one gets more rooms, not bigger ones
HOUSE_ROOM = 24
HOUSE_BEDROOM = 17
# use -> (main room, back rooms in turn, has a customer bathroom)
PROGRAMME = {
    'bar': ('bar', ['barkitchen', 'barstorage'], True),
    'restaurant': ('restaurantdining', ['restaurantkitchen', 'storage'], True),
    'cafe': ('cafe', ['cafekitchen', 'storage'], True),
    'retail': ('giftstore', ['storage'], False),
    'bank': ('bank', ['office', 'bankstorage'], True),
    'clinic': ('medclinic', ['medical', 'medicaloffice'], True),
    'dentist': ('dentist', ['medicaloffice', 'medicalstorage'], True),
    'library': ('library', ['office', 'storage'], True),
    'theatre': ('theatre', ['theatrestorage', 'office'], True),
    'school': ('classroom', ['classroom', 'classroom', 'office'], True),
    'gym': ('gym', ['changeroom', 'storage'], True),
    'police': ('policeoffice', ['policeoffice', 'security', 'policestorage'], True),
    'post': ('post', ['poststorage', 'office'], False),
    'museum': ('Gallery', ['storage', 'office'], True),
    'studio': ('studio', ['office', 'storage'], True),
    'office': ('office', ['office', 'officestorage'], True),          # a law firm, an agency: offices behind a reception
    'gas': ('gasstore', ['gasstorage'], True),
    'community': ('recreation', ['office', 'storage'], True),
    'market': ('grocery', ['grocerystorage', 'producestorage'], False),
}
# vanilla's 22 schools come in three kinds, each with its own classrooms and corridors (and loot)
SCHOOL_KIND = {'elementary': ('elementaryclassroom', 'elementaryhall'),
               'secondary': ('secondaryclassroom', 'secondaryhall'),
               'university': ('universityclassroom', 'hall')}


def school_kind(tag_list, area):
    """Elementary, secondary or university from the school's name and tags; by size when they say nothing
    (vanilla's elementary schools are the small ones)."""
    text = ' '.join(str(t.get(k, '')) for t in tag_list
                    for k in ('name', 'amenity', 'building', 'school', 'grades', 'isced:level')).lower()
    if any(w in text for w in ('university', 'college', 'institute', 'campus', 'seminary')):
        return 'university'
    if any(w in text for w in ('elementary', 'primary', 'kindergarten', 'preschool', 'pre-k', 'montessori',
                               'early', 'child', 'daycare')):
        return 'elementary'
    if any(w in text for w in ('high', 'middle', 'secondary', 'junior', 'academy', 'prep')):
        return 'secondary'
    return 'elementary' if area < 3000 else 'secondary'


# a mapped place that is the whole of a big building, never one tenant in it
INSTITUTION = {'school', 'church', 'clinic', 'library', 'police', 'theatre', 'museum', 'community', 'civic'}
AMENITY_USE = {'fuel': 'gas', 'community_centre': 'community', 'social_facility': 'community',
               'marketplace': 'market', 'events_venue': 'theatre', 'arts_centre': 'museum', 'courthouse': 'civic',
               'townhall': 'civic', 'fire_station': 'civic', 'bank': 'bank', 'clinic': 'clinic', 'doctors': 'clinic', 'hospital': 'clinic', 'dentist': 'dentist',
               'library': 'library', 'theatre': 'theatre', 'cinema': 'theatre', 'school': 'school',
               'college': 'school', 'university': 'school', 'prep_school': 'school', 'kindergarten': 'school',
               'police': 'police', 'post_office': 'post', 'studio': 'studio', 'pharmacy': 'retail'}
BUILDING_USE = {'church': 'church', 'chapel': 'church', 'cathedral': 'church', 'parking': 'parking',
                'garage': 'garage', 'garages': 'garage', 'carport': 'garage', 'shed': 'shed', 'hut': 'shed', 'cabin': 'shed',
                'apartments': 'apartments',
                'residential': 'apartments', 'dormitory': 'apartments', 'terrace': 'apartments',
                'house': 'house', 'detached': 'house', 'semidetached_house': 'house', 'bungalow': 'house',
                'industrial': 'warehouse', 'warehouse': 'warehouse', 'service': 'warehouse', 'shed': 'warehouse',
                'hangar': 'warehouse', 'storage_tank': 'warehouse', 'school': 'school', 'university': 'school',
                'college': 'school', 'hospital': 'clinic', 'government': 'civic', 'civic': 'civic',
                'public': 'civic', 'fire_station': 'civic', 'train_station': 'civic', 'office': 'office',
                'hotel': 'hotel', 'motel': 'hotel', 'retail': 'retail', 'commercial': 'retail',
                'supermarket': 'retail', 'kiosk': 'retail'}


FRONT_DIST = 10          # an outside square this close to a street's carriageway is street frontage
BAND = 14                # how deep the street units of a big building go; behind them is its service core
CORE_ROOMS = ['storage', 'office', 'storage', 'breakroom', 'storage', 'office', 'storage', 'office', 'officestorage',
              'storage', 'office', 'storage']
# ground-floor room size by use: one street-facing unit (a shop, a dining room, a lobby) is about this big
G_TARGET = {'house': 34, 'shed': 10 ** 6, 'garage': 10 ** 6, 'retail': 170, 'bar': 160, 'restaurant': 150, 'cafe': 120, 'hotel': 200, 'apartments': 180,
            'office': 200, 'civic': 160, 'church': 400, 'parking': 10 ** 6, 'warehouse': 10 ** 6, 'bank': 150,
            'clinic': 90, 'dentist': 70, 'library': 250, 'theatre': 300, 'school': 110, 'gym': 220, 'police': 100,
            'post': 140, 'museum': 240, 'studio': 140}
# buildings entered through a lobby: (lobby, rooms behind it, staff bathroom)
LOBBY = {'office': ('lobby', ['office', 'security', 'janitor'], True),
         'civic': ('lobby', ['office', 'office', 'janitor'], True),
         'apartments': ('lobby', ['storage', 'laundry', 'janitor'], False),
         'hotel': ('lobby', ['office', 'laundry', 'janitor'], True)}
# Share of a room's outside wall that is window, measured on the vanilla map (vstudy: 600,000 wall edges).
# Rooms not listed: 0.18. Shop fronts on the street are glass from end to end instead.
WINDOW_SHARE = {'bathroom': 0.12, 'hall': 0.14, 'lobby': 0.14, 'laundry': 0.10, 'church': 0.16, 'gym': 0.15,
                'security': 0.20, 'medical': 0.14, 'motelroom': 0.20, 'bedroom': 0.17, 'kidsbedroom': 0.21,
                'livingroom': 0.17, 'kitchen': 0.18, 'office': 0.19, 'breakroom': 0.17, 'classroom': 0.20,
                'electronicsstore': 0.03, 'mechanic': 0.04, 'theatre': 0.0, 'elevator': 0.0, 'stairwell': 0.0,
                'janitor': 0.01, 'closet': 0.02, 'warehouse': 0.02, 'garage': 0.03}
# what a back door is worth to a room: the share of such vanilla rooms that have a door to the outside
BACK_DOOR = {'kitchen': 0.46, 'storage': 0.30, 'warehouse': 0.5}
SERVICE_ROOMS = ['bathroom', 'bathroom', 'janitor', 'officestorage']


def window_share(name):
    if not name:
        return 0.0
    if name in WINDOW_SHARE:
        return WINDOW_SHARE[name]
    if 'storage' in name:
        return 0.04
    if name.endswith('kitchen'):
        return 0.06                                    # a restaurant's kitchen, not a home's
    return 0.18
# front rooms that get a plain wall with windows instead of a shop window
NO_GLASS = {'office', 'warehouse', 'garage', 'storage', 'classroom', 'policeoffice', 'church', 'officechurch',
            'livingroom', 'medclinic', 'dentist', 'studio', 'library'}
STREET_SHOPS = ['giftstore', 'clothesstore', 'bookstore', 'electronicsstore', 'conveniencestore', 'shoestore',
                'liquorstore', 'jewelrystore', 'musicstore', 'aesthetic', 'pharmacy', 'bakery', 'toystore',
                'sportstore', 'furniturestore', 'artstore', 'movierental', 'departmentstore']


# Shops of a town, weighted by how many vanilla buildings have one (interiors.py counts them). Gift stores
# keep a fifth of their weight: vanilla towns have few.
SHOP_MIX_KINDS = ['conveniencestore', 'cornerstore', 'clothesstore', 'liquorstore', 'bookstore', 'electronicsstore',
                  'toolstore', 'pharmacy', 'furniturestore', 'movierental', 'jewelrystore', 'shoestore', 'sportstore',
                  'toystore', 'musicstore', 'aesthetic', 'bakery', 'pawnshop', 'generalstore', 'gunstore', 'giftstore',
                  'camerastore', 'candystore', 'comicstore']
_mix = None


def shop_mix():
    global _mix
    if _mix is None:
        import interiors
        have = interiors.load().get('buildings_with', {})
        _mix = [(k, max(1, have.get(k, 1)) * (0.2 if k == 'giftstore' else 1.0)) for k in SHOP_MIX_KINDS]
    return _mix


def pick_shop(rng=None, key=None):
    """A shop kind from vanilla's mix: at random, or (with `key`) the same every time for the same key."""
    import random as _r, zlib
    kinds, weights = zip(*shop_mix())
    r = rng if rng is not None else _r.Random(zlib.crc32(str(key).encode()))
    return r.choices(kinds, weights=weights)[0]


def corridor_mask(mask, width=2, spacing=22):
    """Corridors for an upper floor, along the long axis: one down the side of a narrow building, else one
    for every `spacing` squares of depth, joined by a cross corridor when there are several."""
    h, w = mask.shape
    cor = np.zeros_like(mask)
    along_x = w >= h
    depth, length = (h, w) if along_x else (w, h)
    if depth < 8 or length < 10:
        return cor
    if depth < 12:                                   # too narrow for rooms on both sides
        rows = mask if along_x else mask.T
        p = 0 if rows[:width].sum() >= rows[-width:].sum() else depth - width
        (cor if along_x else cor.T)[p:p + width, :] = True
        return cor & mask
    k = max(1, int(round(depth / float(spacing))))
    for i in range(k):
        p = int((i + 0.5) * depth / k) - width // 2
        (cor if along_x else cor.T)[p:p + width, :] = True
    if k > 1:
        q = length // 2 - width // 2
        (cor if along_x else cor.T)[:, q:q + width] = True
    return cor & mask


def classify(tags, pois):
    """-> (use, programme): what the building is and which vanilla rooms it gets.

    The building's own tags come first, then the shops / amenities mapped inside its outline, most
    specific first: a bar beats a restaurant beats a cafe beats a shop beats an office."""
    cats = list(pois) + [tags]
    am = [c.get('amenity') for c in cats if c.get('amenity')]
    shops = [c.get('shop') for c in cats if c.get('shop') and c.get('shop') not in ('vacant', 'no')]
    tour = [c.get('tourism') for c in cats if c.get('tourism')]
    leisure = [c.get('leisure') for c in cats if c.get('leisure')]
    cuisine = [w.strip() for c in cats if c.get('cuisine') for w in str(c['cuisine']).replace(',', ';').split(';')]
    b = tags.get('building', 'yes')
    main = back = None
    if any(a in BAR_AMENITY for a in am):
        use = 'bar'
    elif any(a in FOOD_AMENITY for a in am):
        use = 'restaurant'
        main, kitchen = next((CUISINE[c] for c in cuisine if c in CUISINE),
                             ('burgerdining', 'burgerkitchen') if 'fast_food' in am and 'restaurant' not in am
                             else ('restaurantdining', 'restaurantkitchen'))
        back = [kitchen, 'storage']
    elif 'fuel' in am:
        use = 'gas'                                   # a fuel station's shop sells what vanilla's gas stations do
    elif 'cafe' in am or 'ice_cream' in am:
        use = 'cafe'
        if 'ice_cream' in am or 'ice_cream' in cuisine:
            main, back = 'icecream', ['icecreamkitchen', 'storage']
    elif shops:
        use = 'retail'
        main = next((SHOP_ROOM[s] for s in shops if s in SHOP_ROOM), None) or \
            pick_shop(key=(shops, tags.get('name'), tags.get('addr:housenumber')))   # a shop vanilla has no twin for
        back = [SHOP_BACK.get(main, 'storage')]
    elif 'hotel' in tour or 'motel' in tour or 'hostel' in tour or b in ('hotel', 'motel'):
        use = 'hotel'
    elif b in ('church', 'chapel', 'cathedral') or 'place_of_worship' in am:
        use = 'church'
    elif any(a in AMENITY_USE for a in am):
        use = next(AMENITY_USE[a] for a in am if a in AMENITY_USE)
        if use == 'retail':
            main, back = 'pharmacy', ['pharmacystorage']
    elif 'museum' in tour or 'gallery' in tour:
        use = 'museum'
    elif 'fitness_centre' in leisure or 'sports_centre' in leisure:
        use = 'gym'
    elif any(c.get('office') for c in cats) or 'coworking_space' in am:
        use = 'office'
    else:
        use = BUILDING_USE.get(b, 'generic')
    prog = None
    if use in PROGRAMME:
        m, bk, bath = PROGRAMME[use]
        if use == 'retail' and not main:                  # a shop of unknown kind: one from vanilla's mix
            main = pick_shop(key=(tags.get('name'), tags.get('addr:street'), tags.get('addr:housenumber'), b))
            back = [SHOP_BACK.get(main, 'storage')]
        prog = (main or m, back or bk, bath)
    return use, prog


# ---------------------------------------------------------------- partitioning
def partition(fp, rng, target_area, min_side):
    h, w = fp.shape
    leaves = []

    def split(x0, y0, x1, y1):
        sub = fp[y0:y1, x0:x1]
        area = int(sub.sum())
        if area == 0:
            return
        rw, rh = x1 - x0, y1 - y0
        if area <= target_area or (rw < 2 * min_side and rh < 2 * min_side):
            leaves.append((x0, y0, x1, y1))
            return
        if rw >= rh and rw >= 2 * min_side:
            lo, hi = x0 + min_side, x1 - min_side
            pos = int(round(x0 + rw * rng.uniform(0.38, 0.62)))
            pos = max(lo, min(hi, pos))
            split(x0, y0, pos, y1)
            split(pos, y0, x1, y1)
        elif rh >= 2 * min_side:
            lo, hi = y0 + min_side, y1 - min_side
            pos = int(round(y0 + rh * rng.uniform(0.38, 0.62)))
            pos = max(lo, min(hi, pos))
            split(x0, y0, x1, pos)
            split(x0, pos, x1, y1)
        else:
            leaves.append((x0, y0, x1, y1))

    split(0, 0, w, h)
    lab = np.zeros((h, w), np.int32)
    n = 0
    for (x0, y0, x1, y1) in leaves:
        m = fp[y0:y1, x0:x1]
        comp, k = ndi.label(m)
        for c in range(1, k + 1):
            n += 1
            sel = comp == c
            lab[y0:y1, x0:x1][sel] = n
    # merge tiny rooms into the neighbour they share most border with
    sizes = {i: int((lab == i).sum()) for i in range(1, n + 1)}
    for i in sorted(sizes, key=lambda k: sizes[k]):
        if sizes[i] == 0 or sizes[i] >= 5:
            continue
        m = lab == i
        ring = ndi.binary_dilation(m, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])) & ~m & (lab > 0)
        vals = lab[ring]
        if vals.size:
            tgt = np.bincount(vals).argmax()
            lab[m] = tgt
            sizes[tgt] += sizes[i]
            sizes[i] = 0
    # compact ids
    ids = [i for i in np.unique(lab) if i > 0]
    remap = {old: new + 1 for new, old in enumerate(ids)}
    out = np.zeros_like(lab)
    for o, nw in remap.items():
        out[lab == o] = nw
    return out, len(ids)


def rects_from_mask(mask):
    """Greedy cover of a boolean mask by rectangles. Returns list of (x,y,w,h) local."""
    h, w = mask.shape
    used = np.zeros_like(mask, bool)
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
                out.append((x, y, x2 - x, y2 - y))
                x = x2
            else:
                x += 1
    return out


# ---------------------------------------------------------------- the generator
class Edge:
    __slots__ = ('kind', 'style', 'fix')

    def __init__(self, kind, style, fix=None):
        self.kind, self.style, self.fix = kind, style, fix      # fix: the door or window that goes in the frame


class BuildingGen:
    """Produces tiles into a MapData given footprints."""

    def __init__(self, md, ground, road_dist, seed=1, max_levels=6, front_dist=None):
        self.md = md
        self.ground = ground
        self.rd = road_dist                                   # squares to the nearest carriageway of any kind
        self.fd = road_dist if front_dist is None else front_dist     # ... to the nearest proper street
        self.rng = random.Random(seed)
        self.max_levels = max_levels
        self.hw = {}     # (x,y,z) -> Edge   wall on the north edge of square
        self.vw = {}     # (x,y,z) -> Edge   wall on the west edge of square
        self.fixtures = defaultdict(list)    # (x,y,z) -> tiles appended after walls
        self.occupied = defaultdict(set)     # bid -> {(x,y,z)} used by furniture/stairs
        self._rows = {}                      # row of homes -> its homes, waiting for their one roof
        self._stamp_recent = {}              # use -> vanilla buildings placed lately (no twins side by side)
        self._lot_owner = None                # which building's lot every square is (nearest original outline)
        self._lot_b0 = None
        self._drive = set()
        self.stamping = False                # True: every building is a copied vanilla one (stamps.py)
        self.stats = defaultdict(int)
        self.furnisher = Furnisher(self, seed)
        self.roofer = Roofer(seed)
        self.palette = Palette()
        self._win_cache = {}

    # ---- per building
    def build(self, bid, feat, pois, mask, ox, oy, entrances=()):
        """mask: bool array h x w (y,x); ox,oy: world offset of mask[0,0]."""
        rng = self.rng
        tags = feat.tags
        h, w = mask.shape
        area = int(mask.sum())
        # What the building is: its own tags first. Businesses mapped inside a small building make it
        # theirs; in a large one they become ground-floor tenants, each in the room where it is mapped,
        # under offices (or hotel rooms).
        poi_tags = [t for (_, _, t) in pois]
        tenants = []
        for (px, py, t) in pois:
            if t.get('_grounds'):
                continue                             # the grounds the building stands in are not a tenant
            u, pr = classify({}, [t])
            if pr:
                tenants.append((TENANT_RANK.get(u, 9), px, py, u, pr))
        use, prog = classify(tags, [])
        if use == 'generic' and any(t.get('tourism') in ('hotel', 'hostel', 'motel') for t in poi_tags):
            use, prog = 'hotel', None                # a mapped hotel makes the whole building one
        if use in ('house', 'apartments') and area <= 700 and not any(k in tags for k in ('amenity', 'shop')):
            # a restaurant, a shop, a clinic mapped in a house-shaped building is that business (in Austin many
            # are old houses)
            u, pr = classify({}, [t for t in poi_tags if not t.get('_grounds')])
            if u in SHOPFRONT or u in INSTITUTION or u in ('gas', 'bank', 'post', 'clinic', 'dentist'):
                use, prog = u, pr
                self.stats['houses that are a mapped business'] += 1
        grounds = [t for t in poi_tags if t.get('_grounds')]
        if use == 'generic' and grounds and area >= 30:
            # standing in mapped school grounds (a church's, a shop's lot): the building is theirs, whatever its size
            u, pr = classify({}, grounds)
            if u != 'generic':
                use, prog = u, pr
                self.stats["buildings given their grounds' use"] += 1
        if use == 'generic' and pois:
            if area <= 700:
                use, prog = classify(tags, poi_tags)
            elif any(t.get('tourism') in ('hotel', 'hostel', 'motel') for t in poi_tags):
                use = 'hotel'
            else:
                # one institution is the whole building (a school, a church, a clinic); shops and restaurants
                # in a big one are a strip of units on the street; anything else is offices with tenants
                one = classify({}, poi_tags)[0]
                if one in INSTITUTION:
                    use, prog = classify({}, poi_tags)
                elif any(tn[3] in SHOPFRONT for tn in tenants):
                    use, prog = classify({'shop': 'yes'}, [])
                    self.stats['strips of shops'] += 1
                else:
                    use = 'office'
        if any(t.get('amenity') == 'fuel' for t in poi_tags) and use in ('generic', 'retail') and area <= 700:
            use, prog = 'gas', PROGRAMME['gas']      # the shop of a fuel station: vanilla's gas station shop
            self.stats['fuel station shops'] += 1
        # what an untagged building is depends on where it stands: houses in a residential street, sheds and
        # garages in the yards, warehouses on industrial land, shops and offices downtown
        cy_, cx_ = [int(v.mean()) for v in np.nonzero(mask)]
        wy, wx = min(max(oy + cy_, 0), self.ground.H - 1), min(max(ox + cx_, 0), self.ground.W - 1)
        lu = int(self.ground.landuse[wy, wx])
        cov = float(self.ground.coverage[wy, wx])
        homely = lu == 1 or (lu not in (2, 3) and cov < 0.22)
        if use == 'apartments' and tags.get('building') in ('residential', 'terrace', 'semidetached_house') \
                and area < 320 and 'building:levels' not in tags:
            use, prog = 'house', None                # building=residential is usually one family's house
        if use == 'generic' and not pois:
            if area < 30:
                use = 'shed'
            elif homely and area < 70 and cov < 0.3:
                use = 'garage' if self.rng.random() < 0.5 else 'shed'
            elif lu == 3:
                use = 'warehouse'
            elif lu == 4:
                use = 'church'
            elif homely:
                # (real houses are bigger than vanilla's 60-100; copied from vanilla, a house is a house up to
                # about 500 square metres of footprint, a block of flats beyond)
                big = 500 * getattr(self, 'scale', 1.0) ** 2 if getattr(self, 'stamping', False) else 320
                use = 'house' if area < big else 'apartments'
            if use != 'generic':
                self.stats['untagged read as ' + use] += 1
        levels = self._levels(tags, use, area)
        # --- layout
        if use == 'generic':
            # untagged downtown building: street frontage decides whether it is a shop/restaurant or offices
            near = min((self.rd[min(max(oy + y, 0), self.ground.H - 1), min(max(ox + x, 0), self.ground.W - 1)]
                        for y in range(0, h, max(1, h // 4)) for x in range(0, w, max(1, w // 4))), default=99)
            r = rng.random()
            if area < 40:
                use = 'warehouse'
            elif near < 12 and r < 0.40:
                use = 'retail'
                shop = pick_shop(rng)
                prog = (shop, [SHOP_BACK.get(shop, 'storage')], False)
            elif near < 12 and r < 0.55:
                use = 'restaurant'
            elif near < 12 and r < 0.68:
                use = 'bar'
            elif near < 12 or r < 0.70:
                use = 'office'                         # shops on the street, offices above
            else:
                use = 'warehouse'
            if prog is None and use in PROGRAMME:
                prog = PROGRAMME[use]
            levels = self._levels(tags, use, area) if 'building:levels' not in tags and 'height' not in tags else levels
        if use == 'school':
            school = school_kind([tags] + poi_tags, area)
            ck = SCHOOL_KIND[school][0]
            prog = (ck, [ck, ck, 'office'], True)
            self.stats['school: ' + school] += 1
        self.stats[use] += 1
        if getattr(self, 'stamping', False):
            return self._stamp(bid, use, mask, ox, oy, levels)
        if use == 'house' and 'building:levels' not in tags:
            levels = self._levels(tags, 'house', area)   # (an untagged building read as a house)
        if use in ('house', 'shed', 'garage'):
            mask = self._square_up(mask, ox, oy, bid)    # vanilla houses are boxes under one roof
            area = int(mask.sum())
            h, w = mask.shape
        # Upper floors: flats over shops (sometimes offices), guest rooms in a hotel, offices elsewhere.
        upper = {'apartments': 'homes', 'house': 'bedrooms', 'hotel': 'rooms'}.get(use)
        if upper is None and use in SHOPFRONT:
            upper = 'homes' if rng.random() < 0.4 else 'offices'
        if upper is None and use in ('office', 'civic', 'bank', 'studio', 'post'):
            upper = 'offices'
        # The ground floor is cut into street units; big buildings get bigger units, not more of them.
        gt = G_TARGET.get(use, 160)
        if use == 'house':
            gt = HOUSE_ROOM                          # rooms of vanilla's size: a big house has more of them
        if use not in ('house', 'shed', 'garage'):
            gt = int(gt * min(3.0, max(1.0, area / 1500.0)))
        P = np.zeros((h + 2, w + 2), bool)
        P[1:-1, 1:-1] = mask
        homely = use in ('house', 'shed', 'garage') or (use == 'apartments' and levels < 3)
        row = getattr(self.ground, 'row_of', {}).get(bid)
        look_rng = random.Random(1000003 * row + 17) if row is not None else rng      # a terrace looks alike
        look = Look(self.palette, look_rng, 'res' if homely else 'com', levels)     # a tall block of flats is a tower
        # --- exterior edges
        ext = []   # (orient, x, y, outside_dist)  orient 'h' => wall on N edge of square (x,y) ; 'v' => W edge
        for y in range(-1, h + 1):
            for x in range(-1, w + 1):
                a = P[y + 1, x + 1]
                # north edge between (x,y-1) and (x,y)
                up = P[y, x + 1] if y - 1 >= -1 else False
                if a != up and y - 1 >= -1:
                    inside_sq = (x, y) if a else (x, y - 1)
                    out_sq = (x, y - 1) if a else (x, y)
                    ext.append(('h', x, y, inside_sq, out_sq))
                lf = P[y + 1, x] if x - 1 >= -1 else False
                if a != lf and x - 1 >= -1:
                    inside_sq = (x, y) if a else (x - 1, y)
                    out_sq = (x - 1, y) if a else (x, y)
                    ext.append(('v', x, y, inside_sq, out_sq))
        # A wall with another building behind it is a party wall: no door, no window, no shop glass.
        B = self.ground.B

        def is_party(e):
            wx, wy = ox + e[4][0], oy + e[4][1]
            return 0 <= wx < self.ground.W and 0 <= wy < self.ground.H and B[wy, wx] > 0 and B[wy, wx] != bid
        party = {(e[0], e[1], e[2]) for e in ext if is_party(e)}
        self._back = []                              # back doors of this building (for the bins)
        self._open0 = set()                          # ground-floor rooms with no wall between them
        self._win_cache = {}                         # windows are worked out once per floor plan of this building
        free = [e for e in ext if (e[0], e[1], e[2]) not in party] or ext
        self.stats['party wall edges'] += len(party)

        def odist(e):
            ox_, oy_ = e[4]
            wx, wy = ox + ox_, oy + oy_
            if 0 <= wx < self.ground.W and 0 <= wy < self.ground.H:
                return self.rd[wy, wx]
            return 99

        def sdist(e):
            ox_, oy_ = e[4]
            wx, wy = ox + ox_, oy + oy_
            if 0 <= wx < self.ground.W and 0 <= wy < self.ground.H:
                return self.fd[wy, wx]
            return 99
        fdist = sdist if min((sdist(e) for e in free), default=99) <= FRONT_DIST else odist   # only an alley: that is the front

        # --- ground floor: street units, each with its own door and its service rooms behind it; a big
        # building keeps its units to the street side and has a service core with a corridor behind them
        ring = None
        if use not in ('house', 'warehouse', 'parking', 'church', 'shed', 'garage') and area >= 900 and min(h, w) >= 28:
            ring = self._ring_plan(mask, free, fdist, gt)
        if ring:
            lab0, n0, core = ring
            self.stats['buildings with a service core'] += 1
        else:
            lab0, n0 = partition(mask, rng, gt, 4)
            core = (set(), set(), set())
        if n0 == 0:
            return
        names0, doors, int0, stair_rooms, glass = self._ground_plan(
            lab0, n0, free, fdist, odist, use, prog, tenants, entrances, ox, oy, levels, area, core)
        # --- upper floors: corridors with rooms off them; the stairs stand in one room on every floor
        stairs = None
        lab1 = lab0
        if levels > 1:
            ut = {'rooms': 50, 'homes': 130, 'bedrooms': HOUSE_BEDROOM, 'offices': int(120 * min(1.5, max(1.0, area / 1500.0)))}.get(
                upper, G_TARGET.get(use, 90))
            lab1, halls, suite, service = self._upper_layout(mask, upper, ut)
            stairs = self._stairs(lab0, lab1, doors, int0, stair_rooms, halls | service)
            if stairs is None:
                levels = 1                           # nowhere for a staircase: one storey after all
                self.stats['buildings left one storey: nowhere for stairs'] += 1
        labs = [lab0] + [lab1] * (levels - 1)
        names = [names0]
        int_doors = [int0]
        opens = [self._open0]
        if levels > 1:
            orient, sx, sy, _ = stairs
            run = {(sx, sy + k) if orient == 'N' else (sx + k, sy) for k in range(3)}
            sr = int(lab1[sy, sx])
            for nr in self._split_landing(lab1, sr, stairs):     # the landing is a strip by the stairs, not a room
                suite[nr] = -100000 - nr
            suite[sr] = 0                            # the landing is nobody's room
            nm1, int1, open1 = self._upper_plan(lab1, halls, suite, service, sr, run, upper, use, prog)
            # Vanilla's towers leave many floors unlet: 40% of the floors above the first are 'empty' office
            # space (no furniture, no loot), which also keeps a tall building light to load.
            for z in range(1, levels):
                if levels >= 4 and z < levels - 1 and upper != 'homes' and rng.random() < 0.4:
                    names.append({r: (v if v in ('hall', 'bathroom', 'janitor') else 'empty') for r, v in nm1.items()})
                    self.stats['empty floors'] += 1
                else:
                    names.append(nm1)
            int_doors += [int1] * (levels - 1)
            opens += [open1] * (levels - 1)
        # --- towers: an elevator bank the way vanilla builds one, and the stairs on up to the roof
        self._ox, self._oy = ox, oy
        self.cur_use = use
        self._must_lift = levels >= 10                   # vanilla: every building of 10 floors has lifts
        tower = levels >= 5 and use not in ('house', 'shed', 'garage', 'church', 'warehouse', 'parking')             and stairs is not None
        elev = self._elevator_bank(lab0, lab1, names, int_doors, halls, sr, stairs) if tower else None
        self._roof_keep = set()
        if tower:
            self._roof_keep = self._stair_house_squares(stairs, mask, ox, oy)
        # --- rooms of vanilla size: a back room many times the size vanilla gives its kind is cut into rooms
        self._right_size(lab0, [names[0]], int_doors[0], {int(stairs[3])} if stairs else set(),
                         {d[3] for d in doors}, opens[0])
        if levels > 1:
            self._right_size(lab1, names[1:], int_doors[1], {sr} | set(halls), set(), opens[1])
        if use == 'school':
            self._school_rooms(labs, names, levels, doors, school, keep_halls=bool(elev))
        # --- walls, roof, floors
        for z in range(levels):
            self._place_walls(z, ox, oy, mask, labs[z], names[z], ext, free, party, doors, int_doors[z], opens[z],
                              use, look, glass if z == 0 else (), fdist)
        if row is not None and use == 'house':        # a row of homes gets one roof, once the whole row stands
            self._rows.setdefault(row, []).append((levels, ox, oy, mask.copy(), ext, look))
        elif not (use in ('house', 'shed', 'garage') and self._roof_any(levels, ox, oy, mask)):
            self._roof(levels, ox, oy, mask, ext, look, equipment=use not in ('house', 'shed', 'garage'))
        self._dress_doors(ox, oy, doors, mask)
        if use == 'house':
            self._drive = self._driveway(ox, oy, mask, doors)
            self._dress_yard(ox, oy, mask, doors, bid)
        floor_for_room = self._floors(labs, names)
        for z in range(levels):
            lab = labs[z]
            for y, x in zip(*np.nonzero(lab)):
                self.md.put(ox + int(x), oy + int(y), z, [floor_for_room[z][int(lab[y, x])]])
        if stairs:
            self._emit_stairs(stairs, ox, oy, None, None, levels + (1 if tower and self._roof_keep else 0))
        if elev:
            self._dress_elevator(elev, ox, oy, levels)
        roof_room = self._stair_house(stairs, ox, oy, levels, look) if tower and self._roof_keep else None
        public = set() if use in ('house', 'apartments') else {0}
        if upper not in ('homes', 'rooms'):
            public |= set(range(1, levels))
        self.furnisher.furnish_building(ox, oy, labs, names, levels, stairs, public)
        # --- rooms to the map
        room_ids = []
        for z in range(levels):
            lab = labs[z]
            for r in range(1, int(lab.max()) + 1):
                m = lab == r
                if not m.any():
                    continue
                rects = [(ox + x, oy + y, ww, hh) for x, y, ww, hh in rects_from_mask(m)]
                room_ids.append(self.md.add_room(names[z].get(r) or 'storage', z, rects))
        if roof_room is not None:
            room_ids.append(roof_room)
        self.md.buildings.append(room_ids)
        self.furnisher.keep_clear = set()
        self.stats['street units'] += len(doors)
        return dict(use=use, levels=levels, tall=self._true_levels(tags) or levels, rooms=int(lab0.max()), lab=lab0,
                    labs=labs, int_doors=int_doors, opens=opens, names=names, doors=doors, stairs=stairs, ox=ox, oy=oy,
                    name=tags.get('name'))

    # which vanilla buildings may stand in for a real one, closest first
    STAMP_GROUP = {'house': ('house', 'apartments'), 'apartments': ('apartments', 'house', 'hotel'),
                   'shed': ('shed', 'garage', 'house'), 'garage': ('garage', 'shed', 'house'),
                   'restaurant': ('restaurant', 'cafe', 'bar'), 'cafe': ('cafe', 'restaurant', 'bar'),
                   'bar': ('bar', 'restaurant', 'cafe'), 'retail': ('retail', 'market', 'restaurant'),
                   'market': ('market', 'retail'), 'gas': ('gas', 'retail'), 'school': ('school', 'library', 'office', 'church'),
                   'church': ('church', 'library', 'office'), 'clinic': ('clinic', 'office'), 'dentist': ('clinic', 'office'),
                   'bank': ('bank', 'office'), 'office': ('office', 'bank', 'clinic', 'retail'),
                   'civic': ('office', 'library', 'police'), 'library': ('library', 'office'),
                   'police': ('police', 'office'), 'fire': ('fire', 'police', 'warehouse'),
                   'hotel': ('hotel', 'apartments'), 'warehouse': ('warehouse', 'garage', 'retail'),
                   'theatre': ('theatre', 'retail'), 'community': ('library', 'church', 'office'),
                   'post': ('office', 'bank', 'retail'), 'museum': ('library', 'retail'), 'gym': ('retail', 'warehouse'),
                   'studio': ('office', 'retail'), 'parking': ('garage', 'warehouse'), 'generic': ('retail', 'office')}
    COMMERCIAL = ('retail', 'restaurant', 'office', 'market', 'cafe', 'bar', 'clinic', 'bank', 'warehouse')
    HOMES = ('house', 'apartments', 'garage', 'shed')

    def _stamp(self, bid, use, mask, ox, oy, levels):
        """Put the vanilla building closest to this real one in its place: the same use (else the nearest),
        its door on the side facing the street, its footprint inside the real lot (its outline, the yard round
        it, never another building, the street or the sidewalk), about the real size and height, and not the
        one just used nearby. Nothing fits: the lot is left empty. -> building info, or None"""
        import stamps
        lib = stamps.load()
        G, rng = self.ground, self.rng
        h, w = mask.shape
        area = int(mask.sum())
        ys, xs = np.nonzero(mask)
        rx0, rx1, ry0, ry1 = ox + int(xs.min()), ox + int(xs.max()), oy + int(ys.min()), oy + int(ys.max())

        def rd(x, y):
            return self.rd[min(max(y, 0), G.H - 1), min(max(x, 0), G.W - 1)]
        side_d = {'N': np.mean([rd(x, ry0 - 2) for x in range(rx0, rx1 + 1)]),
                  'S': np.mean([rd(x, ry1 + 2) for x in range(rx0, rx1 + 1)]),
                  'W': np.mean([rd(rx0 - 2, y) for y in range(ry0, ry1 + 1)]),
                  'E': np.mean([rd(rx1 + 2, y) for y in range(ry0, ry1 + 1)])}
        front = min(side_d, key=side_d.get)
        # where it may stand: a window round the real outline
        E = max(4, int(0.3 * max(rx1 - rx0, ry1 - ry0)))
        X0, Y0 = max(2, rx0 - E), max(2, ry0 - E)
        X1, Y1 = min(G.W - 3, rx1 + E), min(G.H - 3, ry1 + E)
        if X1 <= X0 or Y1 <= Y0:
            G.B[oy:oy + h, ox:ox + w][mask] = 0
            return None
        win = G.B[Y0:Y1 + 1, X0:X1 + 1]
        band = getattr(G, 'sidewalk_band', None)
        ok = ((win == 0) | (win == bid)) & ~G.street[Y0:Y1 + 1, X0:X1 + 1]
        if band is not None:
            ok &= ~band[Y0:Y1 + 1, X0:X1 + 1]
        from scipy import ndimage as _ndi
        clear = _ndi.binary_erosion(ok, iterations=1, border_value=0)      # a square of room round it for its walls
        recent = self._stamp_recent.setdefault(use, deque(maxlen=12))
        cands = []
        group = self.STAMP_GROUP.get(use, ('retail',))
        fallback = group + (self.HOMES if use in self.HOMES else
                            ('office', 'library', 'clinic') if use in ('school', 'church') else self.COMMERCIAL)
        for gi, u in enumerate(dict.fromkeys(fallback)):
            for st in lib['by_use'].get(u, ()):
                if st['front'] != front or st['w'] > X1 - X0 - 1 or st['h'] > Y1 - Y0 - 1:
                    continue
                a = len(st['foot'])
                score = -abs(np.log(a / max(1.0, area))) - 0.6 * gi - 0.25 * abs(st['levels'] - levels) \
                    + 0.35 * rng.random() - (0.8 if id(st) in recent else 0.0)
                cands.append((score, id(st), st))
        cands.sort(key=lambda c: -c[0])
        if self._lot_b0 is None:
            self._lot_b0 = G.B.copy()                     # the real outlines, before copies move them
        if getattr(self, '_occupied', None) is None:
            self._occupied = np.zeros((G.H, G.W), bool)        # every square a placed copy covers, eaves too
        occ = self._occupied

        def fits(st, X, Y):
            """Every floor's rooms (and a square round them for the walls) on free ground of this lot, and
            nothing of it (walls, eaves, roof) on anything placed before."""
            ex0, ey0 = st['eo']
            ea, rad = st['ea'], st['rad']
            x0, y0 = X + ex0, Y + ey0
            eh, ew = ea.shape
            if x0 < 1 or y0 < 1 or x0 + ew > G.W - 1 or y0 + eh > G.H - 1:
                return False
            sl = (slice(y0, y0 + eh), slice(x0, x0 + ew))
            Bs = G.B[sl]
            free = ((Bs == 0) | (Bs == bid)) & ~G.street[sl]
            if band is not None:
                free &= ~band[sl]
            return not (rad & ~free).any() and not (ea & occ[sl]).any()

        for score, _, st in cands[:400]:
            if 'ea' not in st:
                stamps.shape_of(st)
            sw, sh = st['w'], st['h']
            # the front lines up with the real front, centred along it; then slid along it and back from it
            if front in 'NS':
                base = (rx0 + (rx1 - rx0 + 1 - sw) // 2, ry0 if front == 'N' else ry1 - sh + 1)
                moves = [(a_, b_ * (1 if front == 'N' else -1)) for b_ in (0, 1, 2, 3) for a_ in (0, -1, 1, -2, 2, -3, 3)]
            else:
                base = (rx0 if front == 'W' else rx1 - sw + 1, ry0 + (ry1 - ry0 + 1 - sh) // 2)
                moves = [(b_ * (1 if front == 'W' else -1), a_) for b_ in (0, 1, 2, 3) for a_ in (0, -1, 1, -2, 2, -3, 3)]
            for mx, my in moves:
                X, Y = base[0] + mx, base[1] + my
                if fits(st, X, Y):
                    ex0, ey0 = st['eo']
                    eh, ew = st['ea'].shape
                    occ[Y + ey0:Y + ey0 + eh, X + ex0:X + ex0 + ew] |= st['ea']
                    return self._stamp_put(bid, st, X, Y, mask, ox, oy, use, recent)
        self.stats['lots left empty: no vanilla building fits'] += 1
        G.B[oy:oy + h, ox:ox + w][mask] = 0
        return None

    def _stamp_put(self, bid, st, X, Y, mask, ox, oy, use, recent):
        import stamps
        lib = stamps.load()
        G, md = self.ground, self.md
        names = lib['tiles']
        h, w = mask.shape
        G.B[oy:oy + h, ox:ox + w][mask & (G.B[oy:oy + h, ox:ox + w] == bid)] = 0
        for (dx, dy) in st['foot']:
            G.B[Y + dy, X + dx] = bid
        for dx, dy, z, inside, ids in st['data']:
            x, y = X + dx, Y + dy
            if not (0 <= x < G.W and 0 <= y < G.H):
                continue
            tl = [names[i] for i in ids]
            if inside:
                md.put(x, y, z, tl)
            else:
                for t in tl:
                    md.add(x, y, z, t)
        room_ids = [md.add_room(name, lvl, [(X + x, Y + y, rw, rh) for (x, y, rw, rh) in rects],
                                objs=[(t, X + x, Y + y) for (t, x, y) in objs])
                    for name, lvl, rects, objs in st['rooms']]
        md.buildings.append(room_ids)
        recent.append(id(st))
        self.stats['vanilla buildings placed'] += 1
        self.stats['vanilla ' + st['use'] + (' for ' + use if st['use'] != use else '')] += 1
        fm = st['fm']
        lab = fm.astype(np.int32)
        dx, dy = st['door']
        f = st['front']
        edge = {'N': ('h', dx, dy, (dx, dy), (dx, dy - 1)), 'S': ('h', dx, dy + 1, (dx, dy), (dx, dy + 1)),
                'W': ('v', dx, dy, (dx, dy), (dx - 1, dy)), 'E': ('v', dx + 1, dy, (dx, dy), (dx + 1, dy))}[f]
        if st['use'] == 'house':
            self._drive = self._driveway(X, Y, fm, [edge])
            self._stamp_yard(st, X, Y, bid)
            self._front_path(st, X, Y, bid)
            self._dress_yard(X, Y, fm, [edge], bid)
        main = max(((r[0], sum(rw * rh for (_, _, rw, rh) in r[2])) for r in st['rooms'] if r[1] == 0),
                   key=lambda t: t[1], default=('empty', 0))[0]
        return dict(use=st['use'], levels=st['levels'], tall=st['levels'], rooms=len(room_ids), lab=lab,
                    labs=[lab], int_doors=[], opens=[], names=[{1: main}], doors=[edge], stairs=None, ox=X, oy=Y,
                    name=None, vanilla=True)

    PATH = 'floors_exterior_tilesandstone_01_3'      # vanilla's front path: 25,500 of its 48,000 yard paving squares
    SOFT = ('blends_natural', 'blends_grassoverlays', 'vegetation_groundcover')

    def _lot_ok(self, x, y, bid):
        """A square of this house's real lot that is free: no building, street, sidewalk, water or driveway."""
        G = self.ground
        if not (0 <= x < G.W and 0 <= y < G.H) or G.B[y, x] != 0 or G.street[y, x]:
            return False
        band = getattr(G, 'sidewalk_band', None)
        if band is not None and band[y, x]:
            return False
        if self._lot_owner is None:
            from scipy import ndimage as _ndi
            _, (iy, ix) = _ndi.distance_transform_edt(self._lot_b0 == 0, return_indices=True)
            self._lot_owner = self._lot_b0[iy, ix]
        from ground import T_WATER
        return self._lot_owner[y, x] == bid and G.G[y, x] != T_WATER and (x, y) not in (self._drive or ())

    def _stamp_yard(self, st, X, Y, bid):
        """The vanilla house's own yard, where it was round it: its paving (path, patio, deck), its plants and
        trees, garden beds, patio furniture, grill, clothesline, play things, wherever this lot has room."""
        lib = __import__('stamps').load()
        names = lib['tiles']
        md = self.md
        n = 0
        for dx, dy, gid, ob in st.get('yard', ()):
            x, y = X + dx, Y + dy
            if not self._lot_ok(x, y, bid):
                continue
            tl = md.squares.get((x, y, 0), [])
            if gid >= 0:
                tl = [names[gid]] + [t for t in tl[1:] if not t.startswith(self.SOFT)]
            tl = list(tl) + [names[i] for i in ob]
            md.put(x, y, 0, tl)
            n += 1
        if n:
            self.stats['yard squares copied with their house'] += n

    def _front_path(self, st, X, Y, bid):
        """A path from the front door to the sidewalk, as nearly every vanilla house has: straight out from the
        door (past its own porch), on to the sidewalk, the street or the driveway."""
        G, md = self.ground, self.md
        o = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}[st['front']]
        x, y = X + st['door'][0] + o[0], Y + st['door'][1] + o[1]
        band = getattr(G, 'sidewalk_band', None)
        path = []
        for _ in range(45):
            if not (0 <= x < G.W and 0 <= y < G.H):
                return
            if G.street[y, x] or (band is not None and band[y, x]) or (x, y) in (self._drive or ()):
                break
            if G.B[y, x] == bid:                         # its own porch
                x, y = x + o[0], y + o[1]
                continue
            if not self._lot_ok(x, y, bid):
                return
            path.append((x, y))
            x, y = x + o[0], y + o[1]
        else:
            return
        for (x, y) in path:
            tl = md.squares.get((x, y, 0), [])
            if tl and tl[0].startswith('floors_exterior'):
                continue                                 # its copied path already
            md.put(x, y, 0, [self.PATH] + [t for t in tl[1:] if not t.startswith(self.SOFT)
                                           and not t.startswith(('vegetation_', 'f_bushes', 'e_'))])
        self.stats['front paths'] += 1

    def _school_rooms(self, labs, names, levels, doors, kind, keep_halls=False):
        """A school with the rooms of vanilla's 22 schools: classrooms of the school's kind off its corridors; on
        the ground floor a gym (the biggest room) with its storage and locker rooms, a cafeteria (by a street
        door) with its kitchen beside it, a library, a nurse's room, a lab in a secondary school; staff offices
        only as many as vanilla's (4% of a school), a store room or two a floor, and no warehouse of storage
        rooms (vanilla schools have almost none: the rest are classrooms)."""
        ck, hk = SCHOOL_KIND[kind]
        nm0 = names[0]
        lab0 = labs[0]
        counts = np.bincount(lab0.ravel())
        size = {r: int(counts[r]) for r in nm0 if r < len(counts) and counts[r]}
        adj = defaultdict(set)
        for (a_, b_) in self._shared_walls(lab0):
            adj[a_].add(b_)
            adj[b_].add(a_)
        street = {int(lab0[d[3][1], d[3][0]]) for d in doors}      # rooms with a door to the street
        total = sum(size.values())
        free = lambda r: r in size and nm0.get(r) in (ck, 'classroom', 'storage', 'officestorage', 'office')

        def take(r, new):
            nm0[r] = new
            self.stats['school rooms: ' + new] += 1

        if total >= 1500:                              # the gym, and beside it its storage and locker rooms
            big = [r for r in size if free(r) and size[r] >= 150]
            if big:
                gym = max(big, key=lambda r: size[r])
                take(gym, 'gym')
                small = sorted((r for r in adj[gym] if free(r) and size[r] <= 60), key=lambda r: size[r])
                for r, new in zip(small, ('schoolgymstorage', 'lockerroom', 'lockerroom')):
                    take(r, new)
        if total >= 1000:                              # the cafeteria by the way in, its kitchen beside it
            big = [r for r in size if free(r) and size[r] >= 100]
            if big:
                caf = max(big, key=lambda r: (r in street, -abs(size[r] - 314)))
                take(caf, 'cafeteria')
                kit = [r for r in adj[caf] if free(r) and 25 <= size[r] <= 150]
                if kit:
                    take(min(kit, key=lambda r: abs(size[r] - 71)), 'cafeteriakitchen')
        if total >= 1200:
            mid = [r for r in size if free(r) and 80 <= size[r] <= 300]
            if mid:
                take(min(mid, key=lambda r: abs(size[r] - 168)), 'universitylibrary' if kind == 'university' else 'library')
        nurse = [r for r in size if free(r) and 12 <= size[r] <= 45]
        if nurse and total >= 600:
            take(min(nurse, key=lambda r: abs(size[r] - 25)), 'medical')
        if kind != 'elementary':
            labs_ = sorted((r for r in size if free(r) and 60 <= size[r] <= 160), key=lambda r: abs(size[r] - 108))
            for r in labs_[:max(1, total // 3000)]:
                take(r, 'schoollab')
        # every floor: classrooms and corridors of the kind, offices to vanilla's share, a store room or two
        whole = sum(int((l > 0).sum()) for l in labs)
        budget = max(2, int(0.04 * whole / 38))
        offices = 0
        seen = set()
        for z in range(levels):
            nm = names[z]
            if id(nm) in seen:
                continue
            seen.add(id(nm))
            share = sum(1 for zz in range(levels) if names[zz] is nm)      # upper floors share one plan
            cz = np.bincount(labs[z].ravel())
            sz = lambda r: int(cz[r]) if r < len(cz) else 0
            stores = 0
            for r in sorted(nm, key=sz):
                v = nm[r]
                if v == 'classroom':
                    nm[r] = ck
                elif v == 'hall' and hk != 'hall' and not keep_halls:
                    nm[r] = hk
                elif v in ('storage', 'officestorage'):
                    if sz(r) <= 30 and stores < 2:
                        nm[r] = 'schoolstorage'
                        stores += 1
                    elif sz(r) <= 30:
                        nm[r] = 'janitor' if sz(r) <= 16 else 'schoolstorage'
                    elif sz(r) < 50:
                        nm[r] = 'schoolstorage'
                    else:
                        nm[r] = ck
                        self.stats['school store rooms made classrooms'] += 1
                elif v == 'office':
                    offices += share
                    if offices > budget and sz(r) >= 50:
                        nm[r] = ck
                        self.stats['school offices made classrooms'] += 1
                if nm[r] == ck and sz(r) < 50:
                    # too small for desks and benches (a vanilla classroom is 130 squares and more): what a small
                    # room in a vanilla school is, a staff office or a store room
                    nm[r] = ('universityoffice' if kind == 'university' else 'office') if sz(r) >= 25 else 'schoolstorage'
                    self.stats['school rooms too small for a class'] += 1

    # ------------------------------------------------------------------ ground floor
    @staticmethod
    def _shared_walls(lab):
        """(room a, room b) with a < b -> the wall edges between them."""
        h, w = lab.shape
        out = defaultdict(list)
        for y in range(h):
            for x in range(w):
                a = lab[y, x]
                if a == 0:
                    continue
                if x > 0 and lab[y, x - 1] > 0 and lab[y, x - 1] != a:
                    b = lab[y, x - 1]
                    out[(int(min(a, b)), int(max(a, b)))].append(('v', x, y))
                if y > 0 and lab[y - 1, x] > 0 and lab[y - 1, x] != a:
                    b = lab[y - 1, x]
                    out[(int(min(a, b)), int(max(a, b)))].append(('h', x, y))
        return out

    def _street_unit(self):
        """What an unmapped street-level unit downtown is: mostly shops, then food and drink."""
        r = self.rng.random()
        if r < 0.20:
            dining, kitchen = self.rng.choice([('restaurantdining', 'restaurantkitchen')] * 4 + sorted(set(
                v for v in CUISINE.values() if v[0] in ('pizzawhirled', 'burgerdining', 'westerndining',
                                                         'jayschicken_dining'))))
            return (dining, [kitchen, 'storage'], True)
        if r < 0.36:
            return PROGRAMME['bar']
        if r < 0.48:
            return PROGRAMME['cafe']
        if r < 0.52:
            return PROGRAMME['bank']
        shop = pick_shop(self.rng)
        return (shop, [SHOP_BACK.get(shop, 'storage')], False)

    def _door_edge(self, edges, entrances, ox, oy):
        """Where a unit's street door goes: on a mapped entrance if there is one on its front, else in the
        middle of its longest stretch of street wall."""
        rank = {'main': 0, 'yes': 1, 'shop': 1, 'home': 1, 'staircase': 2, 'service': 3}
        for ex, ey, et in sorted(entrances, key=lambda en: rank.get(en[2].get('entrance'), 2)):
            if et.get('entrance') in ('garage', 'emergency', 'exit'):
                continue

            def gap(e):
                mx, my = (ox + e[1] + 0.5, oy + e[2]) if e[0] == 'h' else (ox + e[1], oy + e[2] + 0.5)
                return (mx - ex) ** 2 + (my - ey) ** 2
            e = min(edges, key=gap)
            if gap(e) <= 3.0 ** 2:
                self.stats['doors at mapped entrances'] += 1
                return e
        runs = defaultdict(list)
        for e in edges:
            runs[(e[0], e[2] if e[0] == 'h' else e[1])].append(e)
        best = []
        for es in runs.values():
            es.sort(key=lambda e: e[1] if e[0] == 'h' else e[2])
            run = [es[0]]
            for e in es[1:] + [None]:
                c = None if e is None else (e[1] if e[0] == 'h' else e[2])
                p = run[-1][1] if run[-1][0] == 'h' else run[-1][2]
                if e is not None and c == p + 1:
                    run.append(e)
                else:
                    if len(run) > len(best):
                        best = run
                    run = [e]
        return best[len(best) // 2]

    def _ring_plan(self, mask, free, fdist, gt):
        """Ground floor of a big building: street units in a band along the street walls, and behind them
        a service core laid out like an office floor (a corridor with stores, offices, toilets off it).
        -> (labels, count, (core corridors, all core rooms, core service rooms)) or None"""
        rng = self.rng
        seed = np.zeros_like(mask)
        for e in free:
            if fdist(e) <= FRONT_DIST:
                seed[e[3][1], e[3][0]] = True
        if seed.sum() < 8:
            return None
        d = ndi.distance_transform_cdt(~seed, 'chessboard')
        core = ndi.binary_opening(mask & (d >= BAND), np.ones((5, 5), bool)) & mask
        comp, k = ndi.label(core)
        keep = np.zeros_like(mask)
        parts = []
        for c, sl in enumerate(ndi.find_objects(comp), 1):
            m = comp[sl] == c
            if m.sum() >= 250 and min(m.shape) >= 9:
                keep[sl] |= m
                parts.append((sl, m))
        if not parts:
            return None
        lab, n = partition(mask & ~keep, rng, gt, 4)
        if n == 0:
            return None
        halls, rooms, service = set(), set(), set()
        for sl, m in parts:
            sub, shalls, ssuite, sservice = self._upper_layout(m, 'offices', 110)
            ids = [int(i) for i in np.unique(sub) if i]
            view = lab[sl]
            for i in ids:
                view[sub == i] = n + i
                rooms.add(n + i)
            halls.update(n + i for i in shalls)
            service.update(n + i for i in sservice)
            n += max(ids) if ids else 0
        return lab, n, (halls, rooms, service)

    @staticmethod
    def _attach(shared, fronts, core=(), halls=(), prefer=()):
        """Every room joins a street unit through its longest wall with a room that already belongs to one.
        The street-side rooms sort themselves out first; then each corridor of the service core joins a unit
        (the lobby's if it can), and the core's rooms hang off their corridor.
        -> (room -> its unit's street room, room -> the room it is entered from, room -> rooms from the street)"""
        owner = {r: r for r in fronts}
        parent, depth = {}, {r: 0 for r in fronts}
        while True:
            best = None
            for (a, b), es in shared.items():
                if (a in owner) != (b in owner):
                    r, nb = (b, a) if a in owner else (a, b)
                    if r in halls:
                        key = (1, 0 if owner[nb] in prefer else 1, nb in core, -len(es), depth[nb])
                    elif r in core:
                        key = (2, 0 if nb in halls else 1, False, -len(es), depth[nb])
                    else:
                        key = (0, 0, nb in core, -len(es), depth[nb])
                    if best is None or key < best[0]:
                        best = (key, r, nb)
            if best is None:
                return owner, parent, depth
            _, r, nb = best
            owner[r], parent[r], depth[r] = owner[nb], nb, depth[nb] + 1

    def _split_front(self, lab, u, edges, width=7):
        """Keep a slice `width` squares wide at one end of street room u; the rest becomes a new room.
        Changes lab in place. -> the new room, or None"""
        votes = defaultdict(int)
        for e in edges:
            votes[(e[3][0] - e[4][0], e[3][1] - e[4][1])] += 1
        if not votes:
            return None
        dx, dy = max(votes, key=votes.get)
        m = lab == u
        ys, xs = np.nonzero(m)
        lo, hi = (int(ys.min()), int(ys.max()) + 1) if dx else (int(xs.min()), int(xs.max()) + 1)
        if hi - lo < width + 6:
            return None
        a_, b_ = (lo, lo + width) if self.rng.random() < 0.5 else (hi - width, hi)
        sel = np.zeros_like(m)
        if dx:
            sel[a_:b_, :] = True
        else:
            sel[:, a_:b_] = True
        keep = m & sel
        rest = m & ~sel
        nid = int(lab.max()) + 1
        if rest.sum() < 40 or ndi.label(keep)[1] != 1 or ndi.label(rest)[1] != 1 \
                or sum(1 for e in edges if keep[e[3][1], e[3][0]]) < 3:
            return None
        lab[rest] = nid
        return nid

    def _carve(self, lab, u, edges, count, bath):
        """Cut `count` rooms off the back of street room u (the side away from its street wall), one of
        them a small bathroom if asked: the kitchen behind the dining room, the office behind reception.
        Changes lab in place. -> (new rooms, the bathroom or None)"""
        votes = defaultdict(int)
        for e in edges:
            votes[(e[3][0] - e[4][0], e[3][1] - e[4][1])] += 1
        if not votes:
            return [], None
        dx, dy = max(votes, key=votes.get)               # from the street into the room
        m = lab == u
        ys, xs = np.nonzero(m)
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
        D, l0, l1 = (x1 - x0, y0, y1) if dx else (y1 - y0, x0, x1)
        if D < 9:
            return [], None
        sd = max(3, min(8, int(round(D * 0.32))))
        if dx > 0 or dy > 0:
            d0, d1 = (x1 if dx else y1) - sd, (x1 if dx else y1)
        else:
            d0, d1 = (x0 if dx else y0), (x0 if dx else y0) + sd
        segs, pos = [], l0
        if bath and l1 - l0 >= 3:
            segs.append((pos, pos + 3, True))
            pos += 3
            count -= 1
        k = max(0, min(count, (l1 - pos) // 4))
        if k:                                            # the first (the kitchen, the office) takes the room left
            other = min(6, (l1 - pos) // k)              # over; closets and stores stay closet-sized
            first = (l1 - pos) - other * (k - 1)
            edges_ = [pos, pos + first] + [pos + first + other * (i + 1) for i in range(k - 1)]
            for i in range(k):
                segs.append((edges_[i], edges_[i + 1], False))
        if self.rng.random() < 0.5:
            segs = [(l0 + l1 - b, l0 + l1 - a, t) for a, b, t in segs]
        made, bathroom = [], None
        nid = int(lab.max())
        for a, b, is_bath in segs:
            sel = np.zeros_like(m)
            if dx:
                sel[a:b, d0:d1] = m[a:b, d0:d1]
            else:
                sel[d0:d1, a:b] = m[d0:d1, a:b]
            cc, kc = ndi.label(sel)
            if not kc:
                continue
            big = 1 + int(np.argmax(ndi.sum(sel, cc, range(1, kc + 1))))
            piece = cc == big
            if piece.sum() < (6 if is_bath else 9):
                continue
            nid += 1
            lab[piece] = nid
            made.append(nid)
            if is_bath:
                bathroom = nid
        if not made:
            return [], None
        rem = lab == u
        cross = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
        ok = (ndi.label(rem)[1] == 1 and rem.sum() >= 0.45 * m.sum()
              and sum(1 for e in edges if rem[e[3][1], e[3][0]]) >= min(3, len(edges))
              and all((ndi.binary_dilation(lab == r, cross) & rem).sum() >= 2 for r in made))
        if not ok:
            lab[m] = u
            return [], None
        return made, bathroom

    def _ground_plan(self, lab, n, ext, fdist, odist, use, prog, tenants, entrances, ox, oy, levels, area,
                     core=((), (), ())):
        """Name the ground-floor rooms and place the doors (cuts back rooms into lab where they are missing).

        A room on the street is a unit: a shop, a dining room, a bar, the lobby. Every other room belongs
        to the unit it backs onto and becomes that unit's kitchen, storage, office or bathroom; a unit with
        too few rooms behind it has them cut off its own back. Each unit has its own street door and is
        closed off from its neighbours; the stairs go in the lobby.
        -> (names, street doors, interior doors, rooms the stairs may stand in, rooms with shop windows)"""
        rng = self.rng
        h, w = lab.shape
        core_halls, core_all, core_service = core

        def measure():
            counts = np.bincount(lab.ravel())
            sizes = {r: int(c) for r, c in enumerate(counts) if r and c}
            front, outer = defaultdict(list), defaultdict(list)
            for e in ext:
                r = int(lab[e[3][1], e[3][0]])
                outer[r].append(e)
                if fdist(e) <= FRONT_DIST:
                    front[r].append(e)
            return sizes, front, outer, self._shared_walls(lab)

        sizes, front, outer, shared = measure()
        fronts = [r for r in sizes if len(front[r]) >= 3 and sizes[r] >= 12 and r not in core_all]
        if not fronts:
            e = min(ext, key=odist)
            fronts = [int(lab[e[3][1], e[3][0]])]
        owner, parent, depth = self._attach(shared, fronts, core_all, core_halls)
        while len(owner) < len(sizes):                   # a wing cut off from the rest: its own unit
            fronts.append(max((r for r in sizes if r not in owner), key=lambda r: len(outer[r])))
            owner, parent, depth = self._attach(shared, fronts, core_all, core_halls)

        def street_wall(u):
            """The unit's wall on the street; failing that, its outside wall nearest to a road."""
            if front[u] or not outer[u]:
                return front[u]
            d = min(odist(e) for e in outer[u])
            return [e for e in outer[u] if odist(e) <= d + 2]

        def wall(a, b):
            return shared.get((min(a, b), max(a, b)), [])

        int0 = set()

        def link(a, b):
            es = wall(a, b)
            if es:
                int0.add(es[len(es) // 2])

        def any_door():
            """No door on the main room's street wall (it has none): one on the outside wall nearest a road,
            whichever room it belongs to, so nobody is shut in."""
            rs = [r for r in outer if outer[r]]
            if not rs:
                return []
            r = min(rs, key=lambda r: min(odist(e) for e in outer[r]))
            self.stats['doors put where the room allows'] += 1
            return [self._door_edge(street_wall(r), entrances, ox, oy)]

        names = {}
        by_front = sorted(fronts, key=lambda u: -len(street_wall(u)))
        # ---- buildings that are one thing throughout
        if use in ('house', 'warehouse', 'parking', 'church', 'shed', 'garage'):
            main = by_front[0]
            if use == 'house':
                names, hdoors, self._open0 = self._house_plan(sizes, shared, main, levels)
                doors = [self._door_edge(street_wall(main), entrances, ox, oy)] if street_wall(main) else []
                if not doors:                            # wedged into a row: the door goes where it can
                    doors = any_door()
                kit = next((r for r, v in names.items() if v == 'kitchen'), None)
                es = [e for e in outer.get(kit, []) if fdist(e) > FRONT_DIST] if kit else []
                if len(es) >= 2 and rng.random() < 0.7:           # the kitchen door to the yard
                    doors.append(self._door_edge(es, [], ox, oy))
                stair_first = sorted(sizes, key=lambda r: (names.get(r) != 'livingroom', names.get(r) == 'bathroom'))
                return names, doors, hdoors, stair_first, set()
            for r in sizes:
                names[r] = {'warehouse': 'warehouse', 'parking': 'garage', 'shed': 'shed', 'garage': 'garage'}.get(
                    use, 'church' if sizes[r] > 40 else 'officechurch')
            reached = {main}
            while len(reached) < len(sizes):
                best = None
                for (a, b), es in shared.items():
                    if (a in reached) != (b in reached) and (best is None or len(es) > len(best[1])):
                        best = (b if a in reached else a, es)
                if best is None:
                    break
                reached.add(best[0])
                int0.add(best[1][len(best[1]) // 2])
            doors = [self._door_edge(street_wall(u), entrances, ox, oy)
                     for u in by_front[:1 if use == 'house' else 3] if street_wall(u)] or any_door()
            return names, doors, int0, sorted(sizes, key=lambda r: r != main), set()
        # ---- who is in each unit: mapped businesses first
        unit = {}
        # Each business takes the street unit it is mapped in; mapped deeper inside (OSM often puts the
        # point in the middle of a big building) or sharing a unit with another business, it takes the
        # nearest street unit that is still free. The most specific kind of business chooses first.
        cells = {u: np.argwhere(lab == u) for u in fronts}
        for _, px, py, u_use, pr in sorted(tenants, key=lambda t: t[0]):
            lx, ly = int(px) - ox, int(py) - oy
            r = int(lab[ly, lx]) if 0 <= lx < w and 0 <= ly < h else 0
            u = owner.get(r) if r and r not in core_all else None
            if u is None or u in unit:
                free_units = [f for f in fronts if f not in unit and len(cells[f])]
                if not free_units:
                    continue
                dist = {f: float(np.min((cells[f][:, 0] - ly) ** 2 + (cells[f][:, 1] - lx) ** 2)) for f in free_units}
                u = min(free_units, key=dist.get)
                if dist[u] > 30 ** 2:
                    continue
            unit[u] = pr
            self.stats['businesses in their mapped unit'] += 1
        lobby = None
        tower = levels >= 5 and use in ('office', 'hotel', 'apartments', 'civic')   # a lobby for stairs and lifts
        if use in LOBBY and (levels > 1 or use != 'apartments'):
            pool = [u for u in fronts if u not in unit] or fronts
            mains = [(ex, ey) for ex, ey, et in entrances if et.get('entrance') == 'main']
            if mains:
                def near_main(u):
                    return min(((ox + e[1] - ex) ** 2 + (oy + e[2] - ey) ** 2
                                for e in street_wall(u) for ex, ey in mains), default=1e9)
                lobby = min(pool, key=near_main)
            elif use in ('hotel', 'civic'):
                lobby = max(pool, key=lambda u: len(street_wall(u)))
            else:
                lobby = min([u for u in pool if sizes[u] >= (140 if tower else 40)] or pool, key=lambda u: sizes[u])
            unit[lobby] = LOBBY[use] if not tower else (LOBBY[use][0], LOBBY[use][1][-1:], LOBBY[use][2])
            if sizes[lobby] > (260 if tower else 110):   # vanilla lobbies are ~24 squares (a tower's holds its lifts)
                rest = self._split_front(lab, lobby, street_wall(lobby), width=12 if tower else 7)
                if rest:
                    sizes, front, outer, shared = measure()
                    fronts.append(rest)
                    by_front.append(rest)
                    owner, parent, depth = self._attach(shared, fronts, core_all, core_halls)
                    unit[rest] = PROGRAMME['restaurant'] if use == 'hotel' else self._street_unit()
                    self.stats['lobbies split off a big front room'] += 1
            if use == 'hotel' and len(fronts) == 1:       # a small hotel: reception, with the kitchen behind it
                unit[lobby] = ('lobby', ['restaurantkitchen', 'office', 'laundry', 'storage'], True)
        whole = len(fronts) <= 2 or area <= 700 or use not in SHOPFRONT
        secondary = set()                                 # more street rooms of a business that already has its back rooms
        for i, u in enumerate(u for u in by_front if u not in unit):
            if use == 'hotel':                            # the hotel's restaurant, then its bar
                unit[u] = PROGRAMME['restaurant'] if i == 0 else (PROGRAMME['bar'] if i == 1 else self._street_unit())
            elif use == 'civic':
                unit[u] = ('office', ['office', 'officestorage'], True)
            elif use in ('office', 'apartments'):
                unit[u] = self._street_unit()
            elif prog and i == 0:
                unit[u] = prog
            elif prog and whole:
                unit[u] = (prog[0], [], False)
                secondary.add(u)
            else:
                unit[u] = self._street_unit() if use in SHOPFRONT else (prog or ('office', ['storage'], False))
        # ---- a unit with too few rooms behind it gets them cut off its own back
        bath_of = {}
        for u in fronts:
            main, back, bath = unit[u]
            if u in secondary:
                continue
            have = [r for r in sizes if owner[r] == u and r != u and r not in core_all]
            no_bath = bool(bath) and not any(sizes[r] <= 30 for r in have)
            need = max(len(back) + bool(bath) - len(have), 1 if no_bath else 0)
            if need:
                made, bathroom = self._carve(lab, u, street_wall(u), need, no_bath)
                if bathroom:
                    bath_of[u] = bathroom
                self.stats['back rooms cut'] += len(made)
        sizes, front, outer, shared = measure()
        owner, parent, depth = self._attach(shared, fronts, core_all, core_halls, {lobby})
        # ---- rooms behind each front room
        glass = set()
        for u in fronts:
            main, back, bath = unit[u]
            names[u] = main
            if main not in NO_GLASS:
                glass.add(u)
            behind = sorted((r for r in sizes if owner.get(r) == u and r != u and r not in core_all),
                            key=lambda r: (depth[r], -len(wall(r, parent[r]))))
            store = next((b for b in back + (list(prog[1]) if prog else []) if 'storage' in b), 'storage')
            fill = ['office'] if main == 'lobby' and use in ('office', 'civic') else [store, 'office', store, 'breakroom']
            extra = 0
            if bath:
                small = [r for r in behind if sizes[r] <= 30]
                if u in bath_of:
                    names[bath_of[u]] = 'bathroom'
                elif small:
                    names[min(small, key=lambda r: (depth[r], sizes[r]))] = 'bathroom'
            k = 0
            for r in behind:
                if r in names:
                    pass
                elif k < len(back):
                    names[r] = back[k]
                    k += 1
                elif main != 'lobby' and depth[r] == 1 and len(wall(r, u)) >= 6 and outer[r] \
                        and sizes[r] >= max(40, 0.6 * sizes[u]):
                    names[r] = main                      # more floor for the same business
                else:
                    names[r] = fill[extra % len(fill)]
                    extra += 1
                link(r, parent[r])
        # ---- the service core: its corridor, the rooms off it, and the units' back doors onto it
        nsvc = nfill = 0
        for r in sorted(r for r in core_all if r in sizes):
            if r in core_halls:
                names[r] = 'hall'
            elif r in core_service:
                names[r] = SERVICE_ROOMS[nsvc % len(SERVICE_ROOMS)]
                nsvc += 1
            else:
                names[r] = CORE_ROOMS[nfill % len(CORE_ROOMS)]
                nfill += 1
            if r in parent:
                link(r, parent[r])
        for u in fronts:
            if u == lobby or rng.random() > 0.6:
                continue
            mine = sorted((r for r in sizes if owner.get(r) == u and r not in core_all and names.get(r) != 'bathroom'),
                          key=lambda r: -depth[r])
            done = False
            for r in mine:
                for hl in core_halls:
                    if hl in sizes and len(wall(r, hl)) >= 2 and parent.get(hl) != r:
                        link(r, hl)
                        self.stats['service doors'] += 1
                        done = True
                        break
                if done:
                    break
        for r in sizes:
            names.setdefault(r, 'storage')
        house = [u for u in fronts if u in secondary or (unit[u] is prog and u != lobby)]
        for (a, b) in list(shared):                      # one business across several street rooms: open them up
            if a in house and b in house:
                link(a, b)
        # ---- a street door for every unit
        doors = []
        for u in by_front:
            es = street_wall(u)
            if es:
                doors.append(self._door_edge(es, entrances if u == lobby or len(fronts) == 1 else
                                             [en for en in entrances if en[2].get('entrance') != 'main'], ox, oy))
            else:
                nb = max((r for r in sizes if wall(u, r)), key=lambda r: len(wall(u, r)), default=None)
                if nb:
                    link(u, nb)
        home = lobby if lobby is not None else by_front[0]
        # ---- back doors: kitchens and stock rooms that have a wall on the yard or the alley
        for u in by_front:
            for r in sorted((r for r in sizes if owner.get(r) == u and r != u), key=lambda r: 'kitchen' not in names[r]):
                p = next((v for k, v in BACK_DOOR.items() if k in names[r]), 0)
                es = [e for e in outer[r] if fdist(e) > FRONT_DIST and odist(e) < 60]
                if p and len(es) >= 3 and rng.random() < p:
                    doors.append(self._door_edge(es, [], ox, oy))
                    self._back.append(doors[-1])
                    self.stats['back doors'] += 1
                    break

        def stair_rank(r):                               # the lobby, else a back room; a bathroom last of all
            nm = names[r]
            kind = (0 if nm == 'lobby' else 4 if nm in ('bathroom', 'janitor', 'security') else
                    3 if 'kitchen' in nm or nm == 'hall' else
                    1 if 'storage' in nm or nm in ('office', 'breakroom', 'laundry') else 2)
            return (kind, owner.get(r) != home, depth.get(r, 0), -sizes[r])
        return names, doors, int0, sorted(sizes, key=stair_rank), glass

    # ------------------------------------------------------------------ upper floors
    def _split_landing(self, lab, sr, stairs):
        """Cut the upper-floor stair room down to the stairs and the strip beside them; the rest of the
        room becomes rooms of their own. Changes lab in place. -> new room ids"""
        orient, sx, sy, _ = stairs
        m = lab == sr
        if m.sum() <= 30:
            return []
        h, w = lab.shape
        run = [(sx, sy + k) if orient == 'N' else (sx + k, sy) for k in range(-1, 4)]
        best = None
        for s_ in (1, -1):
            strip = [(x + s_, y) if orient == 'N' else (x, y + s_) for x, y in run]
            if all(0 <= x < w and 0 <= y < h and m[y, x] for x, y in strip):
                best = s_
                break
        if best is None:
            return []
        keep = np.zeros_like(m)
        if orient == 'N':
            c0, c1 = min(sx, sx + best), max(sx, sx + best) + 1
            keep[:, c0:c1] = True
        else:
            r0, r1 = min(sy, sy + best), max(sy, sy + best) + 1
            keep[r0:r1, :] = True
        keep &= m
        rest = m & ~keep
        comp, k = ndi.label(rest)
        out = []
        nid = int(lab.max())
        for c in range(1, k + 1):
            sel = comp == c
            if sel.sum() >= 9:
                nid += 1
                lab[sel] = nid
                out.append(nid)
        return out

    def _ensuite(self, m, cor, mask):
        """A 3 x 3 bathroom in a corner of guest room m, by the corridor if it can be. -> mask or None"""
        ys, xs = np.nonzero(m)
        y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
        h, w = m.shape
        best = None
        for bw, bh in ((3, 3), (2, 4), (4, 2)):                  # room for a tub and a toilet
            if x1 - x0 < bw + 2 and y1 - y0 < bh + 2:
                continue
            for bx in (x0, x1 - bw):
                for by in (y0, y1 - bh):
                    if bx < 0 or by < 0 or not m[by:by + bh, bx:bx + bw].all():
                        continue
                    ring = np.zeros_like(m)
                    ring[max(0, by - 1):by + bh + 1, max(0, bx - 1):bx + bw + 1] = True
                    ring[by:by + bh, bx:bx + bw] = False
                    score = 2 * int((ring & cor).any()) - int((ring & ~mask).any()) + self.rng.random() * 0.5
                    rest = m.copy()
                    rest[by:by + bh, bx:bx + bw] = False
                    if ndi.label(rest)[1] != 1:
                        continue
                    if best is None or score > best[0]:
                        best = (score, bx, by, bw, bh)
        if best is None:
            return None
        _, bx, by, bw, bh = best
        out = np.zeros_like(m)
        out[by:by + bh, bx:bx + bw] = True
        return out

    def _upper_layout(self, mask, upper, target):
        """Corridors, then rooms off them. -> (labels, corridor rooms, room -> suite, service rooms).
        A suite is what one corridor door leads to: an office, a guest room with its bathroom, or a flat of
        several rooms. Between two corridors the rooms stand back to back, so each has a corridor at its
        door. Office floors get a service block on the corridor: toilets, a janitor's closet, a store."""
        rng = self.rng
        h, w = mask.shape
        along_x = w >= h
        spacing = 16 if upper == 'rooms' else 22
        cross = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
        cor = corridor_mask(mask, 2, spacing) if (upper in ('rooms', 'homes', 'offices', 'bedrooms') or target <= 120) \
            else np.zeros_like(mask)
        service_p = []
        if cor.any():
            plab = np.zeros((h, w), np.int32)
            k = 0
            bands, _ = ndi.label(mask & ~cor)
            for b, sl in enumerate(ndi.find_objects(bands), 1):
                m = bands[sl] == b
                view = plab[sl]
                depth, length = m.shape if along_x else m.shape[::-1]
                rows = [(0, depth)] if depth <= 0.72 * spacing else [(0, depth // 2), (depth // 2, depth)]
                for r0, r1 in rows:
                    wd = max(4, int(round(target / float(r1 - r0))))
                    cnt = max(1, int(round(length / float(wd))))
                    for i in range(cnt):
                        a, c = i * length // cnt, (i + 1) * length // cnt
                        sel = np.zeros_like(m)
                        if along_x:
                            sel[r0:r1, a:c] = m[r0:r1, a:c]
                        else:
                            sel[a:c, r0:r1] = m[a:c, r0:r1]
                        cc, kc = ndi.label(sel)
                        for j in range(1, kc + 1):
                            k += 1
                            view[cc == j] = k
            counts = np.bincount(plab.ravel(), minlength=k + 1)
            for i in sorted(range(1, k + 1), key=lambda i: counts[i]):      # slivers join the room beside them
                if not 0 < counts[i] < 9:
                    continue
                m = plab == i
                vals = plab[ndi.binary_dilation(m, cross) & ~m & (plab > 0)]
                if vals.size:
                    tgt = int(np.bincount(vals).argmax())
                    plab[m] = tgt
                    counts[tgt] += counts[i]
                    counts[i] = 0
            for i in range(1, k + 1):                  # a strip two squares wide is no room: it joins the room
                m = plab == i                           # beside it (or the corridor, if nothing else)
                if not m.any() or ndi.distance_transform_cdt(np.pad(m, 1), metric='taxicab').max() > 1:
                    continue
                vals = plab[ndi.binary_dilation(m, cross) & ~m & (plab > 0)]
                if vals.size:                           # (alone, it stays: the stairs' landing fits a strip)
                    tgt = int(np.bincount(vals).argmax())
                    plab[m] = tgt
                    counts[tgt] += counts[i]
                    counts[i] = 0
                    self.stats['two-wide strips joined to a room'] += 1
            if upper not in ('rooms', 'homes', 'bedrooms'):
                near = ndi.binary_dilation(cor, cross)
                cands = [(int(counts[i]), i) for i in range(1, k + 1) if counts[i] >= 30 and (near & (plab == i)).any()]
                if len(cands) >= 4:
                    _, i = min(cands)
                    m = plab == i
                    ys, xs = np.nonzero(m)
                    lo, hi = (int(xs.min()), int(xs.max()) + 1) if along_x else (int(ys.min()), int(ys.max()) + 1)
                    # 3-square strips at one end of the piece: toilets and closets of vanilla size; what
                    # is left of the piece stays an office
                    parts = min(len(SERVICE_ROOMS), (hi - lo) // 3)
                    if parts >= 2:
                        if hi - lo - 3 * parts < 5:
                            parts = max(2, (hi - lo) // 3)
                            parts = min(parts, len(SERVICE_ROOMS))
                        for j in range(parts):
                            a, c = lo + 3 * j, lo + 3 * (j + 1)
                            if j == parts - 1 and hi - c < 5:
                                c = hi                       # too little left over for an office: the last strip takes it
                            sel = np.zeros_like(m)
                            if along_x:
                                sel[:, a:c] = m[:, a:c]
                            else:
                                sel[a:c, :] = m[a:c, :]
                            if not sel.any():
                                continue
                            k += 1
                            plab[sel] = k
                            service_p.append(k)
        else:
            plab, k = partition(mask, rng, target, 4)
        lab = np.zeros((h, w), np.int32)
        n = 0
        halls, suite, service = set(), {}, set()
        comp, kc = ndi.label(cor)
        for c in range(1, kc + 1):
            n += 1
            lab[comp == c] = n
            halls.add(n)
            suite[n] = -n
        for sidx in range(1, k + 1):
            m = plab == sidx
            area = int(m.sum())
            if not area:
                continue
            if upper == 'homes' and area >= 50:              # a flat: living room, kitchen, bath, bedrooms
                ys, xs = np.nonzero(m)
                y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
                sub, k2 = partition(m[y0:y1, x0:x1], rng, 26, 4)        # rooms at least 4 wide: a bed fits
                view = lab[y0:y1, x0:x1]
                for j in range(1, k2 + 1):
                    sel = sub == j
                    if sel.any():
                        n += 1
                        view[sel] = n
                        suite[n] = sidx
                continue
            n += 1
            lab[m] = n
            suite[n] = sidx
            if sidx in service_p:
                service.add(n)
            elif upper == 'rooms' and area >= 36:            # a guest room has its own bathroom
                block = self._ensuite(m, cor, mask)
                if block is not None:
                    n += 1
                    lab[block] = n
                    suite[n] = sidx
        # a sliver at a stair-stepped corner touching nothing but diagonals joins the nearest room
        cross4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
        for r in range(1, n + 1):
            m = lab == r
            if 0 < m.sum() < 6 and r not in halls and not (ndi.binary_dilation(m, cross4) & ~m & (lab > 0)).any():
                other = (lab > 0) & ~m
                if other.any():
                    _, (iy, ix) = ndi.distance_transform_edt(~other, return_indices=True)
                    ys, xs = np.nonzero(m)
                    lab[m] = lab[iy[ys[0], xs[0]], ix[ys[0], xs[0]]]
        return lab, halls, suite, service

    def _upper_doors(self, lab, halls, suite, stair_room, keep_clear):
        """Doors between the suites of an upper floor: from the landing to the corridor, from the corridor
        to every suite beside it, on through a neighbour only where no corridor reaches.
        -> (doors, suite -> the room it is entered by, usable shared walls)"""
        def clear(e):
            o, x, y = e
            return (x, y) not in keep_clear and ((x - 1, y) if o == 'v' else (x, y - 1)) not in keep_clear
        shared = {k: [e for e in es if clear(e)] for k, es in self._shared_walls(lab).items()}
        shared = {k: es for k, es in shared.items() if es}
        sizes = np.bincount(lab.ravel())
        group = {r: r for r in suite}                    # rooms of one suite that can reach each other

        def find(r):
            while group[r] != r:
                group[r] = group[group[r]]
                r = group[r]
            return r
        for (a, b) in shared:
            if suite[a] == suite[b]:
                group[find(a)] = find(b)
        for r in suite:
            suite[r] = (suite[r], find(r))
        between = defaultdict(list)
        for (a, b), es in shared.items():
            if suite[a] != suite[b]:                     # a door wants two squares of wall and the bigger room
                between[(suite[a], suite[b])].append((min(len(es), 2), int(sizes[b]), len(es), a, b, es))
                between[(suite[b], suite[a])].append((min(len(es), 2), int(sizes[a]), len(es), b, a, es))
        doors = set()
        entry = {suite[stair_room]: stair_room}
        done = {suite[stair_room]}
        total = len(set(suite.values()))
        while len(done) < total:
            best = None
            for (sa, sb), lst in between.items():
                if sa in done and sb not in done:
                    _, _, ln, a, b, es = max(lst, key=lambda t: t[:3])
                    key = (0 if (a in halls or b in halls) else 1, -ln)
                    if best is None or key < best[0]:
                        best = (key, sb, b, es)
            if best is None:
                break
            _, sb, b, es = best
            doors.add(es[len(es) // 2])
            done.add(sb)
            entry[sb] = b
        return doors, entry, shared

    def _upper_plan(self, lab, halls, suite, service, stair_room, keep_clear, upper, use, prog):
        """Names, doors and open walls of an upper floor. -> (names, doors, {(a, b)} pairs with no wall between)"""
        rng = self.rng
        doors, entry, shared = self._upper_doors(lab, halls, suite, stair_room, keep_clear)
        counts = np.bincount(lab.ravel())
        rooms = [r for r in range(1, len(counts)) if counts[r]]
        sizes = {r: int(counts[r]) for r in rooms}
        nm = {r: 'hall' for r in rooms if r in halls or r == stair_room}
        opens = set()

        def wall(a, b):
            return shared.get((min(a, b), max(a, b)), [])

        def tree(rs, first, rank):
            """Doors inside a suite, spreading from its first room; rank(q) orders the rooms to hang doors off."""
            reached = [first]
            while len(reached) < len(rs):
                best = None
                for r in rs:
                    if r in reached:
                        continue
                    for q in reached:
                        es = wall(r, q)
                        if es and (best is None or (rank(q), -len(es)) < best[0]):
                            best = ((rank(q), -len(es)), r, q, es)
                if best is None:
                    break
                _, r, q, es = best
                reached.append(r)
                if (min(r, q), max(r, q)) not in opens:
                    doors.add(es[len(es) // 2])

        members = defaultdict(list)
        for r in rooms:
            if r not in nm:
                members[suite[r]].append(r)
        single = []
        for sidx, rs in members.items():
            first = entry.get(sidx) if entry.get(sidx) in rs else max(rs, key=lambda r: sizes[r])
            if upper == 'homes':
                # A vanilla flat: you come in to the living room, the kitchen is open to it, and the
                # bedrooms, the bathroom and the closets all have their doors on the living room.
                nm[first] = 'livingroom'
                rest = [r for r in rs if r != first]
                kit = max(rest, key=lambda r: (len(wall(r, first)), sizes[r]), default=None)
                if kit is not None and len(rest) >= 2 and len(wall(kit, first)) >= 2 and sizes[kit] >= 9:
                    nm[kit] = 'kitchen'
                    opens.add((min(kit, first), max(kit, first)))
                else:
                    kit = None
                others = sorted((r for r in rest if r != kit), key=lambda r: sizes[r])
                if others and (len(others) > 1 or sizes[others[0]] < 16):
                    nm[others[0]] = 'bathroom'
                beds = 0
                for r in reversed(others):
                    if r in nm:
                        continue
                    if sizes[r] < 9:
                        nm[r] = 'closet'
                    else:
                        nm[r] = 'bedroom' if beds != 1 or rng.random() < 0.6 else 'kidsbedroom'
                        beds += 1
                tree(rs, first, lambda q: 0 if q == first else 1 if q == kit else 3 if nm.get(q) == 'bathroom' else 2)
            elif upper == 'rooms':
                main = max(rs, key=lambda r: sizes[r])
                for r in rs:
                    nm[r] = ('motelroom' if sizes[r] >= 12 else 'janitor') if r == main else 'bathroom'
                tree(rs, first, lambda q: 0 if q == main else 1)
            else:
                single += rs
                tree(rs, first, lambda q: 0)
        if upper == 'bedrooms':                          # a house's upstairs: bedrooms and a bathroom off the landing
            order = sorted(single, key=lambda r: sizes[r])
            beds = baths = 0
            for i, r in enumerate(order):
                if i == 0 and len(order) > 1:
                    nm[r] = 'bathroom'
                    baths += 1
                elif sizes[r] < 9:
                    nm[r] = 'closet'
                elif baths < 2 and beds >= 3 and sizes[r] <= 14:
                    nm[r] = 'bathroom'                   # a big house: a second bathroom for its bedrooms
                    baths += 1
                else:
                    nm[r] = 'bedroom' if beds != 1 or rng.random() < 0.6 else 'kidsbedroom'
                    beds += 1
            return nm, doors, opens
        svc = sorted(r for r in single if r in service)
        for i, r in enumerate(svc):
            nm[r] = SERVICE_ROOMS[i % len(SERVICE_ROOMS)]
        rest = sorted((r for r in single if r not in service), key=lambda r: -sizes[r])
        if use in ('warehouse', 'parking', 'church'):
            for r in rest:
                nm[r] = {'warehouse': 'storage', 'parking': 'garage', 'church': 'officechurch'}[use]
        elif prog and use not in SHOPFRONT and use not in LOBBY and upper != 'offices':
            main, back, _ = prog                         # a school, a clinic, a library: the same upstairs
            for i, r in enumerate(rest):
                nm[r] = main if i == 0 else back[i % len(back)]
            if not svc and len(rest) > 2:
                nm[rest[-1]] = 'bathroom'
        else:
            for r in rest:
                nm[r] = 'office'
            if not svc and len(rest) > 2:
                nm[rest[-1]] = 'bathroom'
            if len(rest) > 4:
                nm[rest[-2]] = 'breakroom'
        return nm, doors, opens

    def _stairs(self, lab0, lab1, doors, int0, prefer, halls):       # halls: upper rooms the stairs must keep out of
        """A straight flight (3 squares plus a landing at each end) that lies inside one room on the ground
        floor and one room upstairs, never in a corridor or a doorway, with a clear strip beside it to walk
        from one flight to the next. Best in the lobby, against a wall, under a room on the corridor."""
        h, w = lab0.shape
        busy = {d[3] for d in doors}
        for (o, x, y) in int0:
            busy.add((x, y))
            busy.add((x - 1, y) if o == 'v' else (x, y - 1))
        rank = {r: i for i, r in enumerate(prefer)}
        cross = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
        hall_m = np.isin(lab1, list(halls)) if halls else np.zeros(lab1.shape, bool)
        on_hall = set(np.unique(lab1[ndi.binary_dilation(hall_m, cross) & ~hall_m]).tolist()) if halls else None

        def same(cx, cy, r0, r1):
            return 0 <= cx < w and 0 <= cy < h and lab0[cy, cx] == r0 and lab1[cy, cx] == r1

        best = None
        for y in range(h):
            for x in range(w):
                r0, r1 = lab0[y, x], lab1[y, x]
                if r0 == 0 or r1 == 0 or r1 in halls:
                    continue
                for orient, line, (px, py) in (('N', [(x, y + i) for i in range(-1, 4)], (1, 0)),
                                               ('W', [(x + i, y) for i in range(-1, 4)], (0, 1))):
                    if not all(same(cx, cy, r0, r1) and (cx, cy) not in busy for cx, cy in line):
                        continue
                    sides = [[(cx + px * s_, cy + py * s_) for cx, cy in line] for s_ in (1, -1)]
                    free = [all(same(cx, cy, r0, r1) for cx, cy in sd) for sd in sides]
                    if not any(free):
                        continue
                    walls = [sum(1 for cx, cy in sd if not (0 <= cx < w and 0 <= cy < h and lab0[cy, cx] == r0))
                             for sd in sides]
                    against = max(wl for wl, fr in zip(walls, free[::-1]) if fr)     # wall on the side away from the strip
                    ex, ey = 2 * line[0][0] - line[1][0], 2 * line[0][1] - line[1][1]
                    corner = 0 if same(ex, ey, r0, r1) else 1
                    key = (rank.get(int(r0), len(rank)), 0 if on_hall is None or int(r1) in on_hall else 1,
                           -against, -corner, self.rng.random())
                    if best is None or key < best[0]:
                        best = (key, (orient, x, y, int(r0)))
        return best[1] if best else None

    # ---- helpers
    # ---- helpers
    @staticmethod
    def _true_levels(tags):
        """Floors the real building has (uncapped), from building:levels or height; None if not mapped."""
        for key, per in (('building:levels', 1.0), ('height', 3.6)):
            try:
                return max(1, int(round(float(str(tags[key]).split()[0].split(';')[0]) / per)))
            except (KeyError, ValueError, IndexError):
                pass
        return None

    def _levels(self, tags, use, area):
        rng = self.rng
        try:
            lv = int(float(str(tags.get('building:levels', '')).split(';')[0]))
        except ValueError:
            lv = None
        if lv is None and 'height' in tags:
            try:
                h_ = float(str(tags['height']).split()[0])
                # a house's height includes its pitched roof (about 2.4 m): 5.4 m is one storey, 6.6 m two
                lv = max(1, int(round((h_ - 2.4) / 3.0))) if use == 'house' else max(1, int(round(h_ / 3.6)))
            except ValueError:
                pass
        if lv is None:
            lv = {'house': 1 + (rng.random() < 0.3), 'apartments': rng.choice([3, 4, 5]), 'retail': rng.choice([1, 2, 2, 3]),
                  'bar': rng.choice([1, 2, 2, 3]), 'restaurant': rng.choice([1, 2, 2]), 'cafe': rng.choice([1, 1, 2]),
                  'office': rng.choice([3, 4, 6]), 'hotel': rng.choice([4, 6]), 'church': 1, 'parking': 4,
                  'warehouse': 1, 'shed': 1, 'garage': 1, 'civic': 2, 'generic': rng.choice([1, 2, 2, 3])}.get(use, 2)
        if area < 25:
            lv = 1
        return max(1, min(self.max_levels, int(lv)))

    def _style(self, use):
        r = self.rng.random()
        if use in ('house', 'apartments'):
            return T.BRICK_RED if r < 0.5 else T.BRICK_GREY
        if use == 'warehouse' or use == 'parking':
            return T.BRICK_GREY
        return T.BRICK_RED if r < 0.7 else T.BRICK_GREY

    def _edge_clear(self, e, ext):
        return True

    def _windows(self, lab, names, free, door_keys):
        """Which outside wall edges of a floor get a window: per room, per straight stretch of wall, the
        share of it that vanilla gives a room of that kind, spread evenly; the same on every like floor."""
        key0 = (id(lab), id(names))
        if key0 in self._win_cache:
            return self._win_cache[key0]
        rng = self.rng
        lines = defaultdict(list)
        for e in free:
            r = int(lab[e[3][1], e[3][0]])
            side = (e[3][0] - e[4][0], e[3][1] - e[4][1])
            lines[(r, e[0], e[2] if e[0] == 'h' else e[1], side)].append(e)
        wins = set()
        for (r, o, _, _), es in lines.items():
            share = window_share(names.get(r))
            if share <= 0:
                continue
            es.sort(key=lambda e: e[1] if o == 'h' else e[2])
            runs, run = [], [es[0]]
            for e in es[1:]:
                if (e[1] if o == 'h' else e[2]) == (run[-1][1] if o == 'h' else run[-1][2]) + 1:
                    run.append(e)
                else:
                    runs.append(run)
                    run = [e]
            runs.append(run)
            for run in runs:
                L = len(run)
                n = int(L * share + 0.5)
                if n == 0 and L >= 2 and (share >= 0.12 or rng.random() < L * share):
                    n = 1
                for k in range(n):
                    e = run[int((k + 0.5) * L / n)]
                    if (e[0], e[1], e[2]) not in door_keys:
                        wins.add((e[0], e[1], e[2]))
        self._win_cache[key0] = wins
        return wins

    def _place_walls(self, z, ox, oy, mask, lab, names, ext, free, party, doors, int_doors, opens, use, look,
                     glass, fdist):
        """Walls of one floor. A wall tile stands on the square south or east of its edge and shows that
        side, so a wall whose tile stands on one of our room squares is painted for that room (the north
        and west walls of every room, outside walls included); only tiles standing outside the building
        show its outer material."""
        door_keys = {(d[0], d[1], d[2]) for d in doors} if z == 0 else set()
        wins = self._windows(lab, names, free, door_keys)
        outer = look.ext_g if z == 0 else look.ext_u
        runs = defaultdict(list)
        for e in ext:
            runs[(e[0], e[2] if e[0] == 'h' else e[1])].append(e)
        for key, es in runs.items():
            es.sort(key=lambda e: e[1] if e[0] == 'h' else e[2])
            seg, segs = [], []
            for e in es:
                c = e[1] if e[0] == 'h' else e[2]
                if seg and c != (seg[-1][1] if seg[-1][0] == 'h' else seg[-1][2]) + 1:
                    segs.append(seg)
                    seg = []
                seg.append(e)
            if seg:
                segs.append(seg)
            for seg in segs:
                # Shop fronts in bays, as vanilla's: glass in panels of 1-3 between solid piers of 1-2 (vanilla
                # medians: glass 2, pier 2), each shop's run of front framed by a pier at both ends.
                if not hasattr(look, 'bay'):
                    look.bay = (self.rng.choice((2, 2, 3, 3, 1)), self.rng.choice((1, 2, 2)))
                gl, pr = look.bay
                bays = set()
                run = []
                for i, e in enumerate(seg + [None]):
                    r_ = int(lab[e[3][1], e[3][0]]) if e is not None else None
                    shop_e = e is not None and z == 0 and r_ in glass and (e[0], e[1], e[2]) not in party                         and fdist(e) <= FRONT_DIST
                    if shop_e and (not run or run[-1][1] == r_):
                        run.append((i, r_))
                        continue
                    L = len(run)
                    for j, (ii, _) in enumerate(run):
                        if 0 < j < L - 1 and (j - 1) % (gl + pr) < gl:
                            bays.add(ii)
                    run = [(i, r_)] if shop_e else []
                for i, e in enumerate(seg):
                    orient, x, y = e[0], e[1], e[2]
                    k = (orient, x, y)
                    d = self.hw if orient == 'h' else self.vw
                    at = (ox + x, oy + y, z)
                    side = 1 if orient == 'h' else 0             # index into (west-facing, north-facing) pairs
                    r = int(lab[e[3][1], e[3][0]])
                    nm = names.get(r)
                    mine = e[3] == (x, y)
                    shop = z == 0 and r in glass and k not in party and fdist(e) <= FRONT_DIST
                    kind, st, fix = 'wall', (look.interior(nm) if mine else outer), None
                    if k in party:
                        if not mine and at in d and d[at].kind != 'parapet':
                            continue                         # the neighbour's room has painted this wall
                    elif k in door_keys:
                        kind = 'door'
                        fix = look.street_door(nm)[side]
                        if shop:
                            st = T.STOREFRONT
                    elif shop and i in bays:
                        st = T.STOREFRONT
                    elif use == 'parking' and z > 0:
                        kind = 'window' if i % 2 == 0 else 'wall'     # open decks: frames without glass
                    elif k in wins and not shop:
                        kind = 'window'
                        fix = look.window[side]
                    d[at] = Edge(kind, st, fix)
        # interior partitions (none where two rooms are open to each other)
        sizes = np.bincount(lab.ravel())
        for (orient, x, y) in self._interior_edges(lab):
            a = int(lab[y, x])
            b = int(lab[y, x - 1] if orient == 'v' else lab[y - 1, x])
            if (min(a, b), max(a, b)) in opens:
                continue
            st = look.interior(names.get(a))
            d = self.hw if orient == 'h' else self.vw
            if (orient, x, y) in int_doors:
                small = a if sizes[a] <= sizes[b] else b        # the door is the smaller room's: a toilet door, a store door
                d[(ox + x, oy + y, z)] = Edge('door', st, look.door(names.get(small))[1 if orient == 'h' else 0])
            else:
                d[(ox + x, oy + y, z)] = Edge('wall', st)

    # outside lamp beside a door, by which side of the wall the street is: (tile, the edge it hangs on)
    DOOR_LAMP = {(0, 1): 'lighting_outdoor_01_32', (0, -1): 'lighting_outdoor_01_36',
                 (1, 0): 'lighting_outdoor_01_33', (-1, 0): 'lighting_outdoor_01_37'}
    # dumpster facing away from the wall: (tiles at grid 0,0 and the next square along the wall)
    DUMPSTER = {(0, 1): ('trashcontainers_01_10', 'trashcontainers_01_11'), (0, -1): ('trashcontainers_01_30', 'trashcontainers_01_31'),
                (1, 0): ('trashcontainers_01_9', 'trashcontainers_01_8'), (-1, 0): ('trashcontainers_01_29', 'trashcontainers_01_28')}

    # what vanilla puts round a house (share of 81 vanilla houses with it within 6 squares): a fenced yard
    # (88%), bushes (74%) and flowers (70%) along the walls, an air conditioner on the wall (65%), a barbecue
    # (22%) and bins (19%) out back
    BUSHES = ['vegetation_foliage_01_%d' % i for i in (0, 1, 2, 3, 4, 5, 8, 9)]
    FLOWERS = ['vegetation_groundcover_01_%d' % i for i in (18, 21, 22, 23)]
    WALL_AC = {(0, 1): 'industry_01_4', (1, 0): 'industry_01_5'}          # on south and east walls
    BBQ = ['appliances_cooking_01_36', 'appliances_cooking_01_37', 'appliances_cooking_01_38', 'appliances_cooking_01_39']
    BINS = ['trashcontainers_01_16', 'trashcontainers_01_0', 'trashcontainers_01_1']

    # vanilla driveways: asphalt (blends_street_01 block 48, the most common surface under house car spots)
    DRIVE = ['blends_street_01_%d' % i for i in (48, 53, 54, 55)]

    def _driveway(self, ox, oy, mask, doors):
        """A driveway as vanilla houses have (81% of Muldraugh's): three squares wide, from the street to
        the house beside its front door, with a 3 x 5 car spot (a ParkingStall zone, where the game parks a
        car) at the house end, facing the house. The sidewalk stays sidewalk across it; the curb is cut.
        -> the driveway's squares (map squares), empty when the house gets none"""
        g, rng = self.ground, self.rng
        self._drive_beside = False
        if not doors or rng.random() > 0.82:
            return set()
        from ground import T_PAVE, T_WATER
        (ix, iy), (sx, sy) = doors[0][3], doors[0][4]
        fx, fy = sx - ix, sy - iy                       # towards the street
        ys, xs = np.nonzero(mask)
        x0, x1, y0, y1 = ox + int(xs.min()), ox + int(xs.max()), oy + int(ys.min()), oy + int(ys.max())
        door_x, door_y = ox + sx, oy + sy

        def ok(x, y):
            return 0 <= x < g.W and 0 <= y < g.H and g.B[y, x] == 0 and g.G[y, x] != T_WATER

        best = None
        # the three columns (or rows) just outside one side wall, running from the front line to the street
        for side in (1, -1):
            if fy:
                cols = [x1 + 1 + k for k in range(3)] if side > 0 else [x0 - 1 - k for k in range(3)]
                front = y1 if fy > 0 else y0
                line = lambda a, d: (a, front + fy * d)
            else:
                cols = [y1 + 1 + k for k in range(3)] if side > 0 else [y0 - 1 - k for k in range(3)]
                front = x1 if fx > 0 else x0
                line = lambda a, d: (front + fx * d, a)
            path, reached = [], False
            for d in range(1, 25):
                row = [line(a, d) for a in cols]
                if any(g.street[q[1], q[0]] for q in row if 0 <= q[0] < g.W and 0 <= q[1] < g.H):
                    reached = True
                    break
                if not all(ok(*q) for q in row):
                    break
                path.append(row)
            if not reached:
                continue
            open_rows = [r for r in path if not any(g.G[q[1], q[0]] == T_PAVE for q in r)]
            beside = len(open_rows) < 5                  # a front yard too shallow for the car: beside the house
            if beside:
                extra = []
                for d in range(0, 5):
                    row = [line(a, -d) for a in cols]
                    if not all(ok(*q) for q in row):
                        break
                    extra.append(row)
                if len(extra) < 5:
                    continue
                path = list(reversed(extra)) + path
            near = abs((cols[0] if fy else cols[0]) - (door_x if fy else door_y))
            if best is None or near < best[0]:
                best = (near, path, beside, cols)
        if best is None:
            return set()
        _, path, beside, cols = best
        squares = {q for row in path for q in row}
        for (x, y) in squares:
            if g.G[y, x] == T_PAVE:
                continue                                 # the sidewalk crosses it
            self.md.squares[(x, y, 0)] = [rng.choice(self.DRIVE)]
        # cut the curb where it meets the road
        for row in path[-1:]:
            for (x, y) in row:
                for q in ((x, y), (x + fx, y + fy)):
                    t = self.md.squares.get((q[0], q[1], 0))
                    if t:
                        t[:] = [u for u in t if not u.startswith('street_curbs')]
        # the car spot at the house end: 3 x 5, facing the house
        stall = path[:5] if not beside else path[:5]
        xs_ = [q[0] for row in stall for q in row]
        ys_ = [q[1] for row in stall for q in row]
        facing = {(0, 1): 'N', (0, -1): 'S', (1, 0): 'W', (-1, 0): 'E'}[(fx, fy)]
        if not hasattr(g, 'house_stalls'):
            g.house_stalls = []
        g.house_stalls.append((min(xs_), min(ys_), max(xs_) - min(xs_) + 1, max(ys_) - min(ys_) + 1, facing))
        self._drive_beside = beside
        self.stats['driveways'] += 1
        self.stats['driveways beside the house'] += beside
        return squares

    def _dress_yard(self, ox, oy, mask, doors, bid):
        g, rng = self.ground, self.rng
        if not doors:
            return
        (ix, iy), (sx, sy) = doors[0][3], doors[0][4]
        fx, fy = sx - ix, sy - iy                       # towards the street
        ys, xs = np.nonzero(mask)
        x0, x1, y0, y1 = ox + int(xs.min()), ox + int(xs.max()), oy + int(ys.min()), oy + int(ys.max())

        def free(x, y):
            return 0 <= x < g.W and 0 <= y < g.H and g.B[y, x] == 0 and not g.street[y, x] \
                and len(self.md.squares.get((x, y, 0), ())) <= 2
        door_out = {(ox + d[4][0], oy + d[4][1]) for d in doors}
        # the outside squares along each wall, by the side they face
        sides = {(0, -1): [(x, y0 - 1) for x in range(x0, x1 + 1)], (0, 1): [(x, y1 + 1) for x in range(x0, x1 + 1)],
                 (-1, 0): [(x0 - 1, y) for y in range(y0, y1 + 1)], (1, 0): [(x1 + 1, y) for y in range(y0, y1 + 1)]}
        # bushes and flowers in front, a few along the sides
        for side, sqs in sides.items():
            share = 0.55 if side == (fx, fy) else 0.15 if side != (-fx, -fy) else 0.05
            for (x, y) in sqs:
                near_door = any(abs(x - a) + abs(y - b) <= 1 for a, b in door_out)
                if not near_door and free(x, y) and rng.random() < share:
                    self.md.add(x, y, 0, rng.choice(self.BUSHES if rng.random() < 0.55 else self.FLOWERS))
        # an air conditioner on a south or east wall that is not the front
        for side in rng.sample(list(self.WALL_AC), 2):
            if side == (fx, fy) or rng.random() > 0.65:
                continue
            sqs = [q for q in sides[side] if free(*q) and q not in door_out]
            if sqs:
                q = sqs[len(sqs) // 2]
                self.md.add(q[0], q[1], 0, self.WALL_AC[side])
                break
        # out back: a barbecue and the bins, two squares off the back wall
        back = [(x - fx, y - fy) for (x, y) in sides[(-fx, -fy)]]
        rng.shuffle(back)
        for tiles, p in ((self.BBQ, 0.3), (self.BINS, 0.35)):
            for (x, y) in back:
                if rng.random() < p and free(x, y) and free(x + fx, y + fy):
                    self.md.add(x, y, 0, rng.choice(tiles))
                    back.remove((x, y))
                    break
        # a chain-link fence round the back and side yards, from the front of the house back; it keeps to
        # its own lot: no other building and no street inside it (else it is drawn tighter, or not at all)
        for ms, mb in (((3, 6), (2, 4), (2, 3)) if not getattr(self, '_drive_beside', False) else ()):
            if fx:                                       # street to the east or west: the yard runs the other way
                fx0, fx1 = (x0 - mb, x1) if fx > 0 else (x0, x1 + mb)
                fy0, fy1 = y0 - ms, y1 + ms
                front = x1 + 1 if fx > 0 else x0
            else:
                fy0, fy1 = (y0 - mb, y1) if fy > 0 else (y0, y1 + mb)
                fx0, fx1 = x0 - ms, x1 + ms
                front = y1 + 1 if fy > 0 else y0
            if fx0 < 0 or fy0 < 0 or fx1 >= g.W or fy1 >= g.H:
                continue
            Bw = g.B[fy0:fy1 + 1, fx0:fx1 + 1]
            if ((Bw != 0) & (Bw != bid)).any() or g.street[fy0:fy1 + 1, fx0:fx1 + 1].any():
                continue
            X0, X1, Y0, Y1 = fx0, fx1 + 1, fy0, fy1 + 1        # square corners
            if fx > 0:
                pts = [(front, Y0), (X0, Y0), (X0, Y1), (front, Y1)]
            elif fx < 0:
                pts = [(front, Y0), (X1, Y0), (X1, Y1), (front, Y1)]
            elif fy > 0:
                pts = [(X0, front), (X0, Y0), (X1, Y0), (X1, front)]
            else:
                pts = [(X0, front), (X0, Y1), (X1, Y1), (X1, front)]
            # leave a gate-width gap in one side so the yard can be walked into
            gap = 0 if rng.random() < 0.5 else 2
            lines = [pts[:2], pts[1:3], pts[2:]]
            a, b = lines[gap]
            if gap == 0:
                a = (a[0] + (np.sign(b[0] - a[0]) if a[0] != b[0] else 0), a[1] + (np.sign(b[1] - a[1]) if a[1] != b[1] else 0))
            else:
                b = (b[0] - (np.sign(b[0] - a[0]) if a[0] != b[0] else 0), b[1] - (np.sign(b[1] - a[1]) if a[1] != b[1] else 0))
            lines[gap] = (a, b)
            # a home's yard is fenced in wood: vanilla's wooden mix round houses (low board 21, picket 16, tall 5)
            ftype = rng.choices(['wood', 'picket', 'panel'], weights=[21, 16, 5])[0]
            for a, b in lines:
                g.barriers.append(('fence', [tuple(map(float, a)), tuple(map(float, b))], {'barrier': 'fence', 'fence_type': ftype}))
            self.stats['house yards fenced'] += 1
            break

    def _dress_doors(self, ox, oy, doors, mask):
        """What vanilla hangs around an outside door: a lamp on the wall beside it, and by a back door
        the bins (a dumpster against the wall a couple of squares along)."""
        g = self.ground
        back = {(d[0], d[1], d[2]) for d in self._back}

        drive = getattr(self, '_drive', set()) or set()

        def free(x, y):
            return 0 <= x < g.W and 0 <= y < g.H and g.B[y, x] == 0 and not g.street[y, x]                 and len(self.md.squares.get((x, y, 0), ())) <= 2 and (x, y) not in drive
        for d in doors:
            (ix, iy), (sx, sy) = d[3], d[4]
            out = (sx - ix, sy - iy)                     # from the building out to the street
            along = (1, 0) if d[0] == 'h' else (0, 1)
            wx, wy = ox + sx, oy + sy
            for k in (1, -1):                            # the wall square next to the door
                lx, ly = wx + along[0] * k, wy + along[1] * k
                inside = (ix + along[0] * k, iy + along[1] * k)
                if 0 <= inside[1] < mask.shape[0] and 0 <= inside[0] < mask.shape[1] and mask[inside[1], inside[0]]                         and free(lx, ly):
                    self.md.add(lx, ly, 0, self.DOOR_LAMP[out])
                    self.stats['door lamps'] += 1
                    break
            if (d[0], d[1], d[2]) in back and self.rng.random() < 0.7:
                t0, t1 = self.DUMPSTER[out]
                for k in (2, -3, 3, -4):
                    a = (wx + along[0] * k, wy + along[1] * k)
                    b = (a[0] + along[0], a[1] + along[1])
                    ia = (ix + along[0] * k, iy + along[1] * k)
                    ib = (ia[0] + along[0], ia[1] + along[1])
                    against = all(0 <= q[1] < mask.shape[0] and 0 <= q[0] < mask.shape[1] and mask[q[1], q[0]] for q in (ia, ib))
                    if against and free(*a) and free(*b):
                        self.md.add(a[0], a[1], 0, t0)
                        self.md.add(b[0], b[1], 0, t1)
                        self.stats['dumpsters'] += 1
                        break

    def _odist(self, e, ox, oy):
        wx, wy = ox + e[4][0], oy + e[4][1]
        if 0 <= wx < self.ground.W and 0 <= wy < self.ground.H:
            return self.rd[wy, wx]
        return 99

    def _interior_edges(self, lab):
        h, w = lab.shape
        out = []
        for y in range(h):
            for x in range(w):
                a = lab[y, x]
                if a == 0:
                    continue
                if x > 0 and lab[y, x - 1] > 0 and lab[y, x - 1] != a:
                    out.append(('v', x, y))
                if y > 0 and lab[y - 1, x] > 0 and lab[y - 1, x] != a:
                    out.append(('h', x, y))
        return out

    LAWN = ['blends_natural_01_32', 'blends_natural_01_37', 'blends_natural_01_38', 'blends_natural_01_39']

    def _square_up(self, mask, ox, oy, bid):
        """A house outline from the map, made the box vanilla houses are: a ragged rectangle is filled out
        to its corners where the ground is free; an L keeps its main rectangle and the wing becomes lawn."""
        g = self.ground
        h, w = mask.shape
        area = int(mask.sum())
        win = (slice(oy, oy + h), slice(ox, ox + w))
        if area >= 0.85 * h * w:
            gap = ~mask
            free = (g.B[win] == 0) | (g.B[win] == bid)
            if (free | ~gap).all() and not (g.street[win] & gap).any():
                g.B[win][gap] = bid
                self.stats['house outlines squared'] += 1
                return np.ones_like(mask)
            return mask
        # the largest rectangle inside the outline (row histograms)
        best, hist = (0, None), np.zeros(w, np.int32)
        for y in range(h):
            hist = np.where(mask[y], hist + 1, 0)
            stack = []
            for x in range(w + 1):
                cur = hist[x] if x < w else 0
                start = x
                while stack and stack[-1][1] >= cur:
                    start, hh = stack.pop()
                    if hh * (x - start) > best[0]:
                        best = (hh * (x - start), (y - hh + 1, y + 1, start, x))
                stack.append((start, cur))
        if best[0] < 0.7 * area or best[1] is None:
            return mask
        y0, y1, x0, x1 = best[1]
        keep = np.zeros_like(mask)
        keep[y0:y1, x0:x1] = True
        for y, x in zip(*np.nonzero(mask & ~keep)):         # the trimmed wing: lawn, no longer a building
            g.B[oy + y, ox + x] = 0
            self.md.put(ox + int(x), oy + int(y), 0, [self.rng.choice(self.LAWN)])
        self.stats['house outlines trimmed to a box'] += 1
        return keep

    def _roof_any(self, levels, ox, oy, mask, sheet=None):
        """Pitched roofs over any house outline, the way vanilla roofs bigger houses: one roof if one fits;
        a deep box gets two or three parallel roofs; an L or a T is cut into its biggest rectangles, each
        roofed the same way, and a wing too thin for any vanilla roof gets a low flat roof, like a porch.
        -> False (a flat roof over all of it then) when nothing pitched fits at all."""
        import pitched as _p
        ys, xs = np.nonzero(mask)
        y0, x0 = int(ys.min()), int(xs.min())
        m = mask[y0:int(ys.max()) + 1, x0:int(xs.max()) + 1].copy()
        rects = []
        while m.any() and len(rects) < 8:
            r = self._biggest_rect(m)
            if r is None:
                return False
            rects.append(r)
            m[r[1]:r[1] + r[3], r[0]:r[0] + r[2]] = False
        if m.any():
            return False
        placed, flat = [], []                            # (x, y, roof, w, h) in mask coordinates; flat rectangles
        for (rx, ry, rw, rh) in rects:
            if rw * rh < 6 or min(rw, rh) < 2:            # a notch or a sliver: a flat cap
                flat.append((rx, ry, rw, rh))
                continue
            roof = _p.roof_for(rw, rh, self.rng, paint=True)     # the parts of one house share a colour
            if roof:
                placed.append((rx, ry, roof, rw, rh))
                continue
            done = False
            for k in (2, 3):
                along_x = rw >= rh
                D = rh if along_x else rw
                if D // k < 4:
                    break
                parts = [(rx, ry + i * D // k, rw, (i + 1) * D // k - i * D // k) if along_x else
                         (rx + i * D // k, ry, (i + 1) * D // k - i * D // k, rh) for i in range(k)]
                roofs = [_p.roof_for(pw, ph, self.rng, paint=True) for _, _, pw, ph in parts]
                if all(roofs):
                    placed += [(px, py, rf, pw, ph) for (px, py, pw, ph), rf in zip(parts, roofs)]
                    done = True
                    break
            if not done:
                flat.append((rx, ry, rw, rh))
        if not placed:
            return False
        inside = {(ox + int(x), oy + int(y)) for y, x in zip(ys, xs)}
        several = len(placed) > 1 or flat
        sheet = sheet or (self.rng.choice(self.ROOF_SHEETS) if several else None)
        fixed = [r for _, _, r, _, _ in placed if not _p.paintable(r)]
        if sheet and fixed:                              # a part that keeps its own colour sets the house's
            own = Counter(t[:8] for _, _, _, tl in fixed[0] for t in tl
                          if t.startswith('roofs_0') and t[:8] in self.ROOF_SHEETS)
            sheet = own.most_common(1)[0][0] if own else sheet
        for (rx, ry, roof, rw, rh) in placed:
            self._place_roof(levels, ox + x0 + rx, oy + y0 + ry, roof, sheet, inside, (rw, rh))
        floor = self.roofer.floor()
        for (rx, ry, rw, rh) in flat:                   # the thin wing: a low flat roof
            for a in range(rw):
                for b in range(rh):
                    self.md.put(ox + x0 + rx + a, oy + y0 + ry + b, levels, [floor])
            self.stats['house wings under a flat roof'] += 1
        if several:
            self.stats['houses under several vanilla roofs'] += 1
        return True

    @staticmethod
    def _biggest_rect(m):
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

    def roof_rows(self):
        """One vanilla roof over each row of homes, as a terrace or a duplex has; if none fits the row, each
        home its own."""
        for row, units in self._rows.items():
            X0 = min(ox for _, ox, _, _, _, _ in units)
            Y0 = min(oy for _, _, oy, _, _, _ in units)
            X1 = max(ox + m.shape[1] for _, ox, _, m, _, _ in units)
            Y1 = max(oy + m.shape[0] for _, _, oy, m, _, _ in units)
            whole = np.zeros((Y1 - Y0, X1 - X0), bool)
            for _, ox, oy, m, _, _ in units:
                whole[oy - Y0:oy - Y0 + m.shape[0], ox - X0:ox - X0 + m.shape[1]] |= m
            levels = max(u[0] for u in units)
            sheet = random.Random(row * 7 + 3).choice(self.ROOF_SHEETS)       # one colour per row
            ys_w, xs_w = np.nonzero(whole)
            inside = {(X0 + int(x), Y0 + int(y)) for y, x in zip(ys_w, xs_w)}
            if len(units) > 1:
                ys_, xs_ = np.nonzero(whole)
                box = whole[ys_.min():ys_.max() + 1, xs_.min():xs_.max() + 1]
                self.stats['row roof: %s' % ('not a box %.2f' % box.mean() if not box.all() else
                                            'box %dx%d' % box.shape[::-1])] += 1
            if len(units) > 1 and all(u[0] == levels for u in units) and self._pitched(levels, X0, Y0, whole, sheet):
                self.stats['rows of homes under one roof'] += 1
                continue
            # too deep for any vanilla roof: two or three roofs side by side along the row (an M-shaped roof)
            if len(units) > 1 and all(u[0] == levels for u in units) and whole.all():
                hh, ww = whole.shape
                done = False
                for k in (2, 3):
                    along_x = ww >= hh
                    D = hh if along_x else ww
                    if D // k < 5:
                        break
                    parts = []
                    for i in range(k):
                        a0, a1 = i * D // k, (i + 1) * D // k
                        part = np.zeros_like(whole)
                        if along_x:
                            part[a0:a1, :] = True
                        else:
                            part[:, a0:a1] = True
                        parts.append(part)
                    import pitched as _p
                    sizes = [(int(p.any(0).sum()), int(p.any(1).sum())) for p in parts]
                    if all(_p.roof_for(w_, h_, random.Random(1)) for w_, h_ in sizes):
                        for p in parts:
                            self._pitched(levels, X0, Y0, p, sheet, inside)
                        done = True
                        break
                if done:
                    self.stats['rows of homes under parallel roofs'] += 1
                    continue
            if all(u[0] == levels for u in units) and self._roof_any(levels, X0, Y0, whole, sheet):
                self.stats['rows of homes under several roofs'] += 1
                continue
            for levels_, ox, oy, m, ext, look in units:
                if not self._roof_any(levels_, ox, oy, m, sheet):
                    self._roof(levels_, ox, oy, m, ext, look, equipment=False)
        self._rows = {}

    ROOF_SHEETS = ['roofs_01', 'roofs_02', 'roofs_03', 'roofs_04', 'roofs_05']
    # roof tile numbers that are the same piece in all five roof sheets (checked against the tile definitions)
    ROOF_SAME = set(range(16)) | set(range(20, 32)) | {70, 71, 90, 91, 92, 93}

    def _pitched(self, levels, ox, oy, mask, sheet=None, inside=None):
        """A vanilla roof over a box-shaped house. -> False if there is none for its size.
        sheet: one roof colour for every piece (a row of homes, the parts of an M-shaped roof); inside: the
        building's squares, where no eave piece of the level below may land (the seam between two roofs)."""
        ys, xs = np.nonzero(mask)
        y0, x0 = int(ys.min()), int(xs.min())
        H, W = int(ys.max()) - y0 + 1, int(xs.max()) - x0 + 1
        if len(ys) != W * H:
            return False
        roof = pitched.roof_for(W, H, self.rng, paint=sheet is not None)
        if not roof:
            self.stats['houses without a vanilla roof of their size'] += 1
            return False
        self._place_roof(levels, ox + x0, oy + y0, roof, sheet, inside, (W, H))
        self.stats['pitched roofs'] += 1
        return True

    def _place_roof(self, levels, X0, Y0, roof, sheet=None, inside=None, size=None):
        """size: (w, h) of the roof's own box; with `inside` (the building's squares) the pieces this roof
        hangs over its own box are left off where they would land on another roof of the same building
        (two roof surfaces on one square flicker in game)."""
        top = levels - 1
        if size and inside:
            w_, h_ = size
            roof = [(dx, dy, dz, tl) for dx, dy, dz, tl in roof
                    if (0 <= dx < w_ and 0 <= dy < h_) or (X0 + dx, Y0 + dy) not in inside]
        # the eave row a level below the roof (copied from one-storey vanilla houses) reads as loose triangles
        # on our walls, and vanilla's two-storey houses have none: the roof keeps its surface and gables
        roof = [(dx, dy, dz, [t for t in tl if not (dz <= 0 and t.startswith('roofs_'))]) for dx, dy, dz, tl in roof]
        # one colour for a row: only when every roof piece means the same in every roof sheet (else a shingle
        # can turn into a pane of glass), otherwise the roof keeps its own colour
        if sheet and not all(int(t.rsplit('_', 1)[1]) in self.ROOF_SAME for _, _, _, tl in roof for t in tl
                             if t.startswith('roofs_0') and t[:8] in self.ROOF_SHEETS):
            sheet = None
        for dx, dy, dz, tl in roof:
            X, Y = X0 + dx, Y0 + dy
            for t in tl:
                if sheet and t.startswith('roofs_0') and t[:8] in self.ROOF_SHEETS:
                    t = sheet + t[8:]
                self.md.add(X, Y, top + dz, t)

    # vanilla's elevator shaft: grey concrete walls, a two-square door pair, a call button either side
    GARAGE = None
    ELEV_DOOR = {'S': ('fixtures_escalators_01_50', 'fixtures_escalators_01_51'),
                 'E': ('fixtures_escalators_01_49', 'fixtures_escalators_01_48')}
    ELEV_BUTTON = {'S': 'fixtures_escalators_01_57', 'E': 'fixtures_escalators_01_56'}

    # where a lift may open on the ground floor: the lobby or the corridor first, else the building's own back
    # rooms (a goods lift), never a shop floor, a dining room or a kitchen
    LIFT_GROUND = {'lobby': 0, 'hall': 0, 'office': 1, 'breakroom': 1, 'janitor': 1, 'mailroom': 1, 'security': 1}

    def _elevator_bank(self, lab0, lab1, names, int_doors, halls, stair_room, stairs):
        """A 4 x 3 shaft standing in the same squares on every floor, its doors on a corridor upstairs and on the
        lobby (or a back room) downstairs. It may take a corner off rooms, as long as every room it touches
        stays in one piece and keeps its doors; it touches no doorway, no stair and no outside wall.
        Gun's Elevator recognises exactly vanilla's shaft, door and button sprites. -> (squares, front, side) or None"""
        from scipy import ndimage as _ndi
        h, w = lab0.shape
        orient, sx, sy, _ = stairs
        run = {(sx, sy + k) if orient == 'N' else (sx + k, sy) for k in range(-1, 4)}
        doorsq = set()
        for ds in int_doors:
            for (o, x, y) in ds:
                doorsq |= {(x, y), (x - 1, y) if o == 'v' else (x, y - 1)}

        sizes0 = np.bincount(lab0.ravel())
        lobbies = {r for r, v in names[0].items() if v in ('lobby', 'hall') and r < len(sizes0) and sizes0[r]}
        beside = defaultdict(lambda: defaultdict(int))     # ground room -> lobby/hall -> shared wall length
        for y in range(h):
            for x in range(w):
                a = int(lab0[y, x])
                for b in (int(lab0[y, x + 1]) if x + 1 < w else 0, int(lab0[y + 1, x]) if y + 1 < h else 0):
                    if a and b and a != b:
                        if b in lobbies:
                            beside[a][b] += 1
                        if a in lobbies:
                            beside[b][a] += 1

        def back(r):
            v = names[0].get(r) or ''
            return v in self.LIFT_GROUND or v.endswith('storage') or v.endswith('kitchen')
        # back rooms the lobby reaches without crossing a shop floor (the staff route)
        link = defaultdict(set)
        for (o, x, y) in int_doors[0]:
            a = (x - 1, y) if o == 'v' else (x, y - 1)
            if 0 <= a[0] < w and 0 <= a[1] < h:
                p, q = int(lab0[y, x]), int(lab0[a[1], a[0]])
                link[p].add(q)
                link[q].add(p)
        for p, q in getattr(self, '_open0', ()):
            link[p].add(q)
            link[q].add(p)
        staff = set(lobbies)
        todo = list(lobbies)
        while todo:
            r = todo.pop()
            for q in link[r]:
                if q not in staff and back(q):
                    staff.add(q)
                    todo.append(q)

        def ground_rank(r):
            """0: the lobby or corridor; 1: a back room beside one, which becomes part of it; 2: a back room
            on the staff route from the lobby, which becomes the lift hall; else None."""
            v = names[0].get(r) or ''
            if self.LIFT_GROUND.get(v) == 0:
                return 0
            if back(r) and not v.endswith('kitchen') and beside.get(r) and sizes0[r] <= 160:
                return 1
            if back(r) and not v.endswith('kitchen') and r in staff and sizes0[r] <= 160:
                return 2
            return None

        def stays_whole(lab, rooms, S, least):
            for r in rooms:
                rest = lab == r
                for x, y in S:
                    rest[y, x] = False
                if rest.sum() < least or _ndi.label(rest)[1] != 1:
                    return False
            return True
        # upstairs, a lift opens on the corridor, or on a lift lobby made of a one-room suite beside it
        sizes1 = np.bincount(lab1.ravel())
        hall_door, other_door = set(), set()
        for (o, x, y) in int_doors[1] if len(int_doors) > 1 else ():
            a = (x - 1, y) if o == 'v' else (x, y - 1)
            if not (0 <= a[0] < w and 0 <= a[1] < h):
                continue
            p, q = int(lab1[y, x]), int(lab1[a[1], a[0]])
            for u, v in ((p, q), (q, p)):
                if v in halls:
                    hall_door.add(u)
                elif not any(nm.get(v) == 'bathroom' for nm in names[1:]):
                    other_door.add(u)                   # (its own toilet may stay, off the lobby)
        flats = {r for nm in names[1:] for r, v in nm.items() if v in ('livingroom', 'kitchen', 'bedroom', 'kidsbedroom')}
        lift_lobby = {r for r in hall_door - other_door - set(halls) - flats
                      if r != stair_room and 0 < r < len(sizes1) and 12 <= sizes1[r] <= 260}
        best = None
        stage = 0
        why = None
        spots = []                                       # good upstairs, whatever is below: for a carved lift hall
        for side, (sw, sh) in (('S', (4, 3)), ('E', (3, 4))):
            for y0 in range(1, h - sh):
                for x0 in range(1, w - sw):
                    S = [(x0 + i, y0 + j) for i in range(sw) for j in range(sh)]
                    F = [(x0 + i, y0 + sh) for i in range(sw)] if side == 'S' else [(x0 + sw, y0 + j) for j in range(sh)]
                    if any(c in run or c in doorsq for c in S + F):
                        continue
                    stage = max(stage, 1)
                    rf1 = {int(lab1[y, x]) for x, y in F}
                    if rf1 <= set(halls):
                        up = 0                              # upstairs the doors open on the corridor
                    elif len(rf1) == 1 and rf1 <= lift_lobby:
                        up = 1                              # ... or on a lobby off it
                    else:
                        continue
                    r1s = {int(lab1[y, x]) for x, y in S}
                    if 0 in r1s or r1s & set(halls) or stair_room in r1s or r1s & rf1:
                        continue
                    stage = max(stage, 2)
                    if not all(0 <= x + dx < w and 0 <= y + dy < h and lab0[y + dy, x + dx] and lab1[y + dy, x + dx]
                               for x, y in S for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                        continue                            # an inside core: no shaft against the outside wall
                    stage = max(stage, 3)
                    spots.append((up, abs(x0 - sx) + abs(y0 - sy), S, F, side))
                    r0f = {int(lab0[y, x]) for x, y in F}
                    if len(r0f) != 1:
                        continue
                    r0f = r0f.pop()
                    rank = ground_rank(r0f)
                    if rank is None:
                        why = names[0].get(r0f)
                        continue
                    stage = max(stage, 4)
                    r0s = {int(lab0[y, x]) for x, y in S}
                    if 0 in r0s:
                        continue
                    d = abs(x0 - sx) + abs(y0 - sy)
                    key = (rank + up, len(r0s) + len(r1s), d)
                    if best is not None and key >= best[0]:
                        continue
                    stage = max(stage, 5)
                    if not stays_whole(lab1, r1s, S, 9) or not stays_whole(lab0, r0s, S, 6):
                        continue
                    best = (key, S, F, side, up)
        if best is None and getattr(self, '_must_lift', False):
            # a tall tower has lifts: carve a corridor from the lift doors to the lobby
            for up, _, S, F, side in sorted(spots, key=lambda t: t[:2])[:60]:
                if not stays_whole(lab0, {int(lab0[y, x]) for x, y in S} - {0}, S, 6):
                    continue
                for tg in (lobbies, staff | {int(stairs[3])}):
                    if self._carve_lift_hall(lab0, int_doors[0], S, F, tg, run):
                        best = ((0, 0, 0), S, F, side, up)
                        self.stats['lift halls carved to the lobby'] += 1
                        break
                if best is not None:
                    break
        if best is None:
            self.stats['towers without room for an elevator'] += 1
            self.lift_fail = getattr(self, 'lift_fail', []) + [(self._ox, self._oy, lab0.shape)]
            self.stats['elevator search stopped at stage %d' % stage] += 1
            if why:
                self.stats['elevator ground room was ' + str(why)] += 1
            return None
        _, S, F, side, up = best
        if up:                                           # upstairs: that room is the lift lobby on every floor
            r1 = int(lab1[F[0][1], F[0][0]])
            for nm in names[1:]:
                nm[r1] = 'hall'
            self.stats['lift lobbies made of a room off the corridor'] += 1
        r = int(lab0[F[0][1], F[0][0]])
        if ground_rank(r) == 2:                          # a back room on the staff route: the lift hall
            names[0][r] = 'hall'
            self.stats['lift halls on the staff route'] += 1
        elif ground_rank(r) == 1:                        # the back room the lift opens on joins the lobby
            into = max(beside[r].items(), key=lambda kv: kv[1])[0]
            lab0[lab0 == r] = into
            for e in [e for e in int_doors[0] if self._both_sides(lab0, e)]:
                int_doors[0].remove(e)                   # a door that is now inside one room
            self.stats['lift lobbies widened into a back room'] += 1
        e0, e1 = int(lab0.max()) + 1, int(lab1.max()) + 1
        for x, y in S:
            lab0[y, x] = e0
            lab1[y, x] = e1
        names[0][e0] = 'elevator'
        for nm in names[1:]:
            nm[e1] = 'elevator'
        self.stats['elevator banks'] += 1
        return S, F, side

    # what the rest of an oversized room becomes: more of the same, or the store room it would have
    SPLIT_AS = {'storage': 'storage', 'officestorage': 'storage', 'breakroom': 'storage', 'janitor': 'storage',
                'security': 'office', 'laundry': 'storage', 'office': 'office', 'bathroom': 'storage',
                'restaurantkitchen': 'storage', 'classroom': 'classroom', 'mailroom': 'storage',
                'kitchen': 'storage'}

    KEEP_LONG = {'hall', 'hallway', 'lobby', 'warehouse', 'elevator', 'stairwell', 'empty', 'garage', 'church'}

    def _right_size(self, lab, dicts, int_doors, keep, busy, opens=()):
        """Cut every back room more than twice the size of vanilla's biggest (90th percentile) of its kind
        into rooms of about that size: the piece with the doors keeps its name, the others become what
        SPLIT_AS says and get a door onto a corridor beside them, else into the piece they were cut from.
        dicts: the name dicts of every floor the plan serves (an 'empty' floor keeps its pieces empty)."""
        import vanillawalls
        sizes = vanillawalls.load().get('sizes', {})
        if not sizes:
            return
        for (o, x, y) in int_doors:
            busy = busy | {(x, y), (x - 1, y) if o == 'v' else (x, y - 1)}
        hallish = lambda r: any(d.get(r) in ('hall', 'lobby', 'hallway') for d in dicts)
        open_rooms = {a for pair in opens for a in pair}     # reached through an open wall: left whole
        for r in range(1, int(lab.max()) + 1):
            if r in keep or r in open_rooms:
                continue
            nm = next((d.get(r) for d in dicts if d.get(r) not in (None, 'empty')), None)
            if not nm:
                continue
            m = lab == r
            area = int(m.sum())
            if not area:                                 # merged into another room earlier
                continue
            ys, xs = np.nonzero(m)
            y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
            L, S = max(y1 - y0, x1 - x0), min(y1 - y0, x1 - x0)
            # a long strip (a cafe 12 x 38, a kitchen 6 x 29) is no vanilla room: its far end is the store room
            strip = L >= 3 * S and L > 20 and nm not in self.KEEP_LONG
            if strip:
                filler = self.SPLIT_AS.get(nm) or (nm + 'storage' if nm + 'storage' in sizes else 'storage')
                target = S * max(12, 2 * S)
            else:
                filler = self.SPLIT_AS.get(nm) or ('storage' if nm.endswith('storage') else None)
                q = sizes.get(nm)
                if not filler or not q or area <= max(2 * q[1], 20):
                    continue
                target = max(6, int(0.8 * q[1]))
            sub, k = partition(m[y0:y1, x0:x1], self.rng, target, 3 if (strip and S < 8) or target < 16 else 4)
            if k < 2:
                continue
            parts = {}
            for j in range(1, k + 1):
                pm = np.zeros_like(m)
                pm[y0:y1, x0:x1] = sub == j
                pm &= m
                if pm.any():
                    parts[j] = pm
            # scraps (a square or two at a stepped wall, a strip one square wide) join the piece beside them
            for j in sorted(parts, key=lambda j: int(parts[j].sum())):
                pm = parts[j]
                thin = ndi.distance_transform_cdt(np.pad(pm, 1), metric='taxicab').max() <= 1 and                     min(np.ptp(np.nonzero(pm)[0]), np.ptp(np.nonzero(pm)[1])) == 0
                if len(parts) > 1 and (pm.sum() < 4 or thin):
                    ring = ndi.binary_dilation(pm) & ~pm
                    nbrs = [(int((ring & parts[o]).sum()), o) for o in parts if o != j and (ring & parts[o]).any()]
                    if nbrs:
                        o = max(nbrs)[1]
                        parts[o] = parts[o] | pm
                        del parts[j]
            if len(parts) < 2:
                continue
            main = max(parts, key=lambda j: (sum(1 for (bx, by) in busy if 0 <= by < lab.shape[0] and 0 <= bx < lab.shape[1]
                                                   and parts[j][by, bx]), int(parts[j].sum())))
            new = {}
            for j, pm in parts.items():
                if j != main:
                    nid = int(lab.max()) + 1
                    lab[pm] = nid
                    new[j] = nid
                    for d in dicts:
                        d[nid] = 'empty' if d.get(r) == 'empty' else filler
            # every new room gets a door into the part it was cut from (so a room people walked through still
            # joins what it joined), working outwards from the piece with the doors; one beside a corridor
            # gets a door onto it as well
            reached = {r}
            todo = set(new.values())
            shared = self._shared_walls(lab)

            def walls_of(nid):
                return [(a if b == nid else b, es) for (a, b), es in shared.items() if nid in (a, b)]
            while todo:
                step = [nid for nid in todo if any(o in reached for o, _ in walls_of(nid))]
                if not step:
                    break
                for nid in step:
                    ws = walls_of(nid)
                    es = max((es for o, es in ws if o in reached), key=len)
                    int_doors.add(es[len(es) // 2])
                    halls_ = [es for o, es in ws if hallish(o) and len(es) >= 2]
                    if halls_:
                        es = max(halls_, key=len)
                        int_doors.add(es[len(es) // 2])
                    reached.add(nid)
                    todo.discard(nid)
            for nid in todo:                             # cannot happen for one room cut up; put it back if it does
                lab[lab == nid] = r
            self.stats['oversized rooms cut to vanilla size'] += 1
            self.stats['rooms cut from them'] += len(new)

    def _carve_lift_hall(self, lab0, int0, S, F, targets, run):
        """A corridor two squares wide on the ground floor from the lift doors (F) to the nearest room of
        `targets`, which it becomes part of. Rooms it cuts keep their biggest piece and get a door onto it.
        -> True when carved (lab0 and int0 changed in place)"""
        h, w = lab0.shape
        Sset = set(S)
        targets = {t for t in targets if t}
        if not targets:
            return False
        okm = (lab0 > 0)
        for x, y in Sset | set(run):
            if 0 <= x < w and 0 <= y < h:
                okm[y, x] = False
        blk = okm[:-1, :-1] & okm[1:, :-1] & okm[:-1, 1:] & okm[1:, 1:]      # 2 x 2 blocks of free squares
        tg = np.isin(lab0, list(targets))
        tblk = tg[:-1, :-1] | tg[1:, :-1] | tg[:-1, 1:] | tg[1:, 1:]
        start = {(x + dx, y + dy) for x, y in F for dx in (-1, 0) for dy in (-1, 0)}
        start = [(x, y) for x, y in start if 0 <= x < w - 1 and 0 <= y < h - 1 and blk[y, x]
                 and sum((x + i, y + j) in set(F) for i in (0, 1) for j in (0, 1)) >= 2]
        prev = {b: None for b in start}
        todo = deque(start)
        end = None
        while todo:
            b = todo.popleft()
            if tblk[b[1], b[0]]:
                end = b
                break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (b[0] + dx, b[1] + dy)
                if 0 <= nb[0] < w - 1 and 0 <= nb[1] < h - 1 and blk[nb[1], nb[0]] and nb not in prev:
                    prev[nb] = b
                    todo.append(nb)
        if end is None:
            return False
        into = next(int(lab0[end[1] + j, end[0] + i]) for i in (0, 1) for j in (0, 1)
                    if int(lab0[end[1] + j, end[0] + i]) in targets)
        cut = set(F)
        b = end
        while b is not None:
            cut |= {(b[0] + i, b[1] + j) for i in (0, 1) for j in (0, 1)}
            b = prev[b]
        new = lab0.copy()
        hit = {int(new[y, x]) for x, y in cut} - {into}
        for x, y in cut:
            new[y, x] = into
        for r in hit:                                    # a cut room keeps its biggest piece
            comp, n = ndi.label(new == r)
            if n > 1:
                sizes = np.bincount(comp.ravel())[1:]
                keep = int(np.argmax(sizes)) + 1
                for k in range(1, n + 1):
                    if k != keep:
                        if sizes[k - 1] > 30:
                            return False
                        new[comp == k] = into
        lab0[:] = new
        for e in [e for e in int0 if self._both_sides(lab0, e)]:
            int0.remove(e)
        for r in hit:                                    # ... and a door onto the new corridor
            if not (lab0 == r).any():
                continue
            es = [e for (a, b_), es_ in self._shared_walls(lab0).items() if {a, b_} == {r, into} for e in es_]
            if es and not any(e in int0 for e in es):
                int0.add(es[len(es) // 2])
        return True

    @staticmethod
    def _both_sides(lab, e):
        """Is edge e = (orient, x, y) between two squares of the same room?"""
        o, x, y = e
        a = (x - 1, y) if o == 'v' else (x, y - 1)
        h, w = lab.shape
        if not (0 <= a[0] < w and 0 <= a[1] < h and 0 <= x < w and 0 <= y < h):
            return False
        return lab[y, x] == lab[a[1], a[0]] and lab[y, x] > 0

    def _dress_elevator(self, elev, ox, oy, levels):
        """Concrete shaft walls on every floor, the door pair and call buttons onto the corridor or lobby."""
        from tilepalette import WallStyle
        if BuildingGen.GARAGE is None:
            g = ['walls_garage_01_%d' % i for i in (32, 33, 34, 35)]
            BuildingGen.GARAGE = WallStyle(g[0], g[1], g[2], g[3], g[0], g[1], g[0], g[1])
        S, F, side = elev
        Ss = set(S)
        x0, y0 = min(x for x, _ in S), min(y for _, y in S)
        for z in range(levels):
            for (x, y) in S:                             # walls whose tiles stand in the shaft show concrete
                if (x, y - 1) not in Ss:
                    self.hw[(ox + x, oy + y, z)] = Edge('wall', BuildingGen.GARAGE)
                if (x - 1, y) not in Ss:
                    self.vw[(ox + x, oy + y, z)] = Edge('wall', BuildingGen.GARAGE)
            if side == 'S':
                doors = [(x0 + 1, y0 + 3), (x0 + 2, y0 + 3)]
                buttons = [(x0 + 3, y0 + 3)]
                d = self.hw
            else:
                doors = [(x0 + 3, y0 + 1), (x0 + 3, y0 + 2)]
                buttons = [(x0 + 3, y0 + 3)]
                d = self.vw
            for (x, y), t in zip(doors, self.ELEV_DOOR[side]):
                d.pop((ox + x, oy + y, z), None)          # the elevator door is the wall there
                self.fixtures[(ox + x, oy + y, z)].append(t)
            for (x, y) in buttons:
                self.fixtures[(ox + x, oy + y, z)].append(self.ELEV_BUTTON[side])
        self.furnisher.keep_clear = {(ox + x, oy + y) for x, y in F}

    def _stair_house_squares(self, stairs, mask, ox, oy):
        """Where the stairs come out on the roof: a small room 2 or 3 squares wide over the top flight and its
        landing, with a door out onto the roof at the end or the side. Remembers the door; -> world squares."""
        orient, sx, sy, _ = stairs
        h, w = mask.shape
        at = (lambda a, b: (sx + b, sy + a)) if orient == 'N' else (lambda a, b: (sx + a, sy + b))
        roof = lambda c: 0 <= c[0] < w and 0 <= c[1] < h and mask[c[1], c[0]]
        # the room covers at least the opening (the top two steps) and the landing the stairs arrive on
        for (a0, a1), (b0, b1) in [(aa, bb) for aa in ((-2, 3), (-1, 3), (-2, 1), (-1, 1))
                                   for bb in ((-1, 1), (0, 1), (-1, 0))]:
            sq = [at(a, b) for a in range(a0, a1 + 1) for b in range(b0, b1 + 1)]
            if not all(roof(c) for c in sq):
                continue
            # doors: at the end, beyond the landing; else on a side by the landing
            ends = [(a0, 0, 'a')] + [(a, b, st) for a in range(a0, a1 + 1) for b, st in ((b0, 'b-'), (b1, 'b+'))
                                     if not (b == 0 and a in (0, 1))]       # (not over the stair opening)
            for a, b, step in ends:
                out = at(a - 1, b) if step == 'a' else at(a, b - 1) if step == 'b-' else at(a, b + 1)
                if not roof(out):
                    continue
                x, y = at(a, b)
                if step == 'a':
                    door = ('h', (x, y)) if orient == 'N' else ('v', (x, y))
                elif step == 'b-':
                    door = ('v', (x, y)) if orient == 'N' else ('h', (x, y))
                else:
                    door = ('v', (x + 1, y)) if orient == 'N' else ('h', (x, y + 1))
                self._roof_door = (door[0], (ox + door[1][0], oy + door[1][1]))
                self._roof_out = {(ox + out[0] + i, oy + out[1] + j) for i in (-1, 0, 1) for j in (-1, 0, 1)}
                return {(ox + cx, oy + cy) for cx, cy in sq}
        self.stats['towers with no roof room over the stairs'] += 1
        return set()

    def _stair_house(self, stairs, ox, oy, levels, look):
        """The stairs come up into a small room on the roof with a door out onto it, for a last stand."""
        orient, sx, sy, _ = stairs
        z = levels
        sq = self._roof_keep
        xs = [x for x, _ in sq]
        ys = [y for _, y in sq]
        X0, X1, Y0, Y1 = min(xs), max(xs), min(ys), max(ys)
        holes = {(ox + sx, oy + sy), (ox + sx, oy + sy + 1)} if orient == 'N' else {(ox + sx, oy + sy), (ox + sx + 1, oy + sy)}
        for (x, y) in sq:
            if (x, y) not in holes:
                cur = self.md.squares.get((x, y, z))
                if cur:
                    cur[0] = 'floors_interior_tilesandwood_01_24'
                else:
                    self.md.put(x, y, z, ['floors_interior_tilesandwood_01_24'])
            self.md.put(x, y, z + 1, [self.roofer.floor()])        # its own flat roof
        st = look.ext_u
        for x in range(X0, X1 + 1):
            for yy in (Y0, Y1 + 1):
                self.hw[(x, yy, z)] = Edge('wall', st)
                self.hw[(x, yy, z + 1)] = Edge('parapet', look.rim)
        for y in range(Y0, Y1 + 1):
            for xx in (X0, X1 + 1):
                self.vw[(xx, y, z)] = Edge('wall', st)
                self.vw[(xx, y, z + 1)] = Edge('parapet', look.rim)
        kind, (dx_, dy_) = self._roof_door
        if kind == 'h':
            self.hw[(dx_, dy_, z)] = Edge('door', st, 'fixtures_doors_01_1')
        else:
            self.vw[(dx_, dy_, z)] = Edge('door', st, 'fixtures_doors_01_0')
        self.stats['roof stair houses'] += 1
        return self.md.add_room('stairwell', z, [(X0, Y0, X1 - X0 + 1, Y1 - Y0 + 1)])

    def _roof(self, levels, ox, oy, mask, ext, look, equipment=True):
        """Flat roof the vanilla way: street-asphalt floor, a low parapet, and equipment (roofs.py)."""
        h, w = mask.shape
        par = look.rim
        z = levels
        floor = self.roofer.floor()
        cells = set()
        for y, x in zip(*np.nonzero(mask)):
            self.md.put(ox + int(x), oy + int(y), z, [floor])
            cells.add((ox + int(x), oy + int(y)))
        for e in ext:
            d = self.hw if e[0] == 'h' else self.vw
            k = (ox + e[1], oy + e[2], z)
            if k not in d or d[k].kind == 'parapet':         # a taller neighbour's wall stays a wall
                d[k] = Edge('parapet', par)
        if equipment:                                    # no air handlers on a house
            keep = getattr(self, '_roof_keep', set()) | (getattr(self, '_roof_out', set()) if getattr(self, '_roof_keep', None) else set())
            self.roofer.dress(self.md, {c for c in cells if c not in keep}, z)

    def _floors(self, labs, names):
        """One floor tile per room: the kind the vanilla map lays in that room type."""
        rng = self.rng
        pick = {'storage': T.FLOOR_CONCRETE, 'warehouse': T.FLOOR_CONCRETE, 'garage': T.FLOOR_CONCRETE,
                'hall': T.FLOOR_CARPET, 'lobby': T.FLOOR_WOOD}
        floors = []
        done = {}
        for z, lab in enumerate(labs):
            if id(names[z]) in done:                 # the upper floors share one plan: same floors too
                floors.append(done[id(names[z])])
                continue
            d = {}
            for r in range(1, int(lab.max()) + 1):
                nm = names[z].get(r) or 'storage'
                d[r] = self.furnisher.floor_for(nm, rng) or rng.choice(pick.get(nm, T.FLOOR_WOOD))
            floors.append(d)
            done[id(names[z])] = d
        return floors

    def _house_plan(self, sizes, shared, main, levels):
        """A vanilla house: you come in to the living room; the kitchen is off it, usually open to it; the
        bathroom, bedrooms and closets have their doors on the living room (or the kitchen). With an
        upstairs, the bedrooms are up there and the ground floor has a dining room and a laundry instead.
        -> (names, doors, open pairs)"""
        rng = self.rng

        def wall(a, b):
            return shared.get((min(a, b), max(a, b)), [])
        nm = {main: 'livingroom'}
        rest = [r for r in sizes if r != main]
        opens, doors = set(), set()
        kit = max(rest, key=lambda r: (len(wall(r, main)) >= 2, sizes[r] >= 9, len(wall(r, main))), default=None)
        if kit is not None and wall(kit, main):
            nm[kit] = 'kitchen'
            if rng.random() < 0.85:                           # vanilla: 9 kitchens in 10 are open to the living room
                opens.add((min(kit, main), max(kit, main)))
        others = sorted((r for r in rest if r not in nm), key=lambda r: sizes[r])
        if others:
            nm[others[0]] = 'bathroom'
        beds = 0
        # a guest room downstairs is common in vanilla; a big (real) house has an office and a second bathroom too
        downstairs = ['diningroom', 'laundry', 'bedroom', 'office', 'bathroom', 'bedroom']
        for r in reversed(others[1:]):
            if sizes[r] < 9:
                nm[r] = 'closet'
            elif levels > 1:
                nm[r] = downstairs.pop(0) if downstairs else 'storage'
            else:
                nm[r] = 'bedroom' if beds != 1 or rng.random() < 0.6 else 'kidsbedroom'
                beds += 1

        def rank(q):
            return 0 if q == main else 1 if nm.get(q) == 'kitchen' else 3 if nm.get(q) == 'bathroom' else 2
        reached = [main]
        while len(reached) < len(sizes):
            best = None
            for r in sizes:
                if r in reached:
                    continue
                for q in reached:
                    es = wall(r, q)
                    if es and (best is None or (rank(q), -len(es)) < best[0]):
                        best = ((rank(q), -len(es)), r, q, es)
            if best is None:
                break
            _, r, q, es = best
            reached.append(r)
            if (min(r, q), max(r, q)) not in opens:
                doors.add(es[len(es) // 2])
        return nm, doors, opens

    def _home_names(self, order, sizes, front, n):
        nm = {}
        if n == 1:
            nm[order[0]] = 'livingroom'
            return nm
        nm[front] = 'livingroom'
        rest = sorted((r for r in order if r != front), key=lambda r: -sizes[r])
        smallest = sorted(rest, key=lambda r: sizes[r])
        used = set()
        if rest:                                   # kitchen: the biggest room after the living room
            nm[rest[0]] = 'kitchen'
            used.add(rest[0])
        if len(rest) >= 2:                         # bathroom: the smallest
            bt = smallest[0] if smallest[0] not in used else smallest[-1]
            nm[bt] = 'bathroom'
            used.add(bt)
        beds = 0
        for r in rest:
            if r in used:
                continue
            if sizes[r] < 9:
                nm[r] = 'closet'
            else:
                nm[r] = 'bedroom' if beds != 1 or self.rng.random() < 0.6 else 'kidsbedroom'
                beds += 1
        return nm

    def _emit_stairs(self, st, ox, oy, lab, floors, levels):
        orient, x, y, r = st
        tiles = T.STAIRS_N if orient == 'N' else T.STAIRS_W
        for z in range(levels - 1):
            # squares: top, mid, bottom
            if orient == 'N':
                sq = [(x, y), (x, y + 1), (x, y + 2)]
            else:
                sq = [(x, y), (x + 1, y), (x + 2, y)]
            # tiles tuple is (bottom, middle, top)
            parts = {sq[2]: tiles[0], sq[1]: tiles[1], sq[0]: tiles[2]}
            for (sx, sy), t in parts.items():
                gx, gy = ox + sx, oy + sy
                base = self.md.squares.get((gx, gy, z), [])
                self.md.squares[(gx, gy, z)] = list(base) + [t]
            # open the floor above the middle and top squares
            for (sx, sy) in (sq[0], sq[1]):
                gx, gy = ox + sx, oy + sy
                key = (gx, gy, z + 1)
                if key in self.md.squares:
                    # keep any stair of the next flight, drop the floor
                    keep = [t for t in self.md.squares[key] if t.startswith('fixtures_stairs')]
                    self.md.squares[key] = keep
                    if not keep:
                        del self.md.squares[key]

    # ---- emit wall tiles
    def emit_walls(self):
        md = self.md
        keys = set()
        for d in (self.hw, self.vw):
            keys.update(d.keys())
        for (x, y, z) in sorted(keys, key=lambda k: (k[2], k[1], k[0])):
            n = self.hw.get((x, y, z))
            w = self.vw.get((x, y, z))
            tiles = []
            if n and w and n.kind == 'wall' and w.kind == 'wall' and n.style == w.style:
                tiles.append(n.style.NW)
            else:
                if n:
                    tiles += self._edge_tiles(n, 'N')
                if w:
                    tiles += self._edge_tiles(w, 'W')
            # SE pillar where a south wall (N edge of (x,y+1)) meets an east wall (W edge of (x+1,y))
            s = self.hw.get((x, y + 1, z))
            e = self.vw.get((x + 1, y, z))
            if s and e and s.kind in ('wall', 'window', 'door', 'parapet') and e.kind in ('wall', 'window', 'door', 'parapet') \
                    and (s.kind == 'parapet') == (e.kind == 'parapet'):
                tiles.append(s.style.SE if s.kind != 'parapet' else s.style[3])
            for t in tiles:
                md.add(x, y, z, t)
        # furniture last so it draws over the walls
        for (x, y, z), tl in self.fixtures.items():
            for t in tl:
                md.add(x, y, z, t)
        # pillars alone (no wall on own edges) still need emitting
        pil = set()
        for (x, y, z), s in self.hw.items():
            e = self.vw.get((x + 1, y - 1, z))
            if e and s.kind in ('wall', 'window', 'door', 'parapet') and e.kind in ('wall', 'window', 'door', 'parapet') \
                    and (s.kind == 'parapet') == (e.kind == 'parapet'):
                sq = (x, y - 1, z)
                if sq not in keys:
                    pil.add((sq, s.style.SE if s.kind != 'parapet' else s.style[3]))
        for (sq, t) in pil:
            md.add(sq[0], sq[1], sq[2], t)

    def _edge_tiles(self, e, side):
        st = e.style
        if e.kind == 'parapet':
            p = st  # tuple (W,N,NW,SE) -> indexes: 0=W 1=N
            return [p[1] if side == 'N' else p[0]]
        if e.kind == 'wall':
            return [st.N if side == 'N' else st.W]
        if e.kind == 'window':
            if st is T.STOREFRONT:
                return [st.N if side == 'N' else st.W]
            return [st.winN if side == 'N' else st.winW] + ([e.fix] if e.fix else [])
        if e.kind == 'door':
            return [st.doorN, e.fix or T.DOOR_N] if side == 'N' else [st.doorW, e.fix or T.DOOR_W]
        return []
