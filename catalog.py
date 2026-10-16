"""Parse the game's tile definitions (newtiledefinitions.tiles.txt) into {tile_name: {property: value}}."""
import json, os, re

def _steam_libraries():
    libs = []
    for base in (r"C:/Program Files (x86)/Steam", r"C:/Program Files/Steam"):
        vdf = os.path.join(base, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            libs.append(base)
            for m in re.finditer(r'"path"\s+"([^"]+)"', open(vdf, encoding='utf8', errors='ignore').read()):
                libs.append(m.group(1).replace('\\', '/'))
    return libs


def game_dir():
    cands = [os.environ.get('PZ_DIR', '')]
    cands += [os.path.join(lib, 'steamapps', 'common', 'ProjectZomboid') for lib in _steam_libraries()]
    cands += [r"C:/Program Files (x86)/Steam/steamapps/common/ProjectZomboid", r"D:/SteamLibrary/steamapps/common/ProjectZomboid"]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, 'media', 'newtiledefinitions.tiles.txt')):
            return c
    raise SystemExit('Project Zomboid install not found; set the PZ_DIR environment variable')

_cat = None

def load(cache=None):
    global _cat
    if _cat is not None:
        return _cat
    if cache and os.path.isfile(cache):
        _cat = json.load(open(cache, encoding='utf8'))
        return _cat
    t = open(os.path.join(game_dir(), 'media', 'newtiledefinitions.tiles.txt'), encoding='utf8').read()
    cat = {}
    for m in re.finditer(r'tileset\s*\{\s*file = (\S+)\s*size = (\d+),(\d+)\s*id = (\d+)(.*?)(?=\ntileset\n|\Z)', t, re.S):
        body = m.group(5)
        for tm in re.finditer(r'// (\S+)\s*tile\s*\{(.*?)\n    \}', body, re.S):
            cat[tm.group(1)] = dict(re.findall(r'^\s+(\w+) = ?(.*)$', tm.group(2), re.M))
    _cat = cat
    if cache:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        json.dump(cat, open(cache, 'w'))
    return cat

def variants(sheet, custom, group=None):
    """-> {facing: [variant, ...]}, each variant = [(dx, dy, tile), ...] with its north-west square at (0, 0).

    Several different pieces can share one CustomName / GroupName (three office desks of different
    lengths, two toilets...). Tiles are taken in sheet order; a new piece starts when a grid position
    repeats or when the sheet jumps ahead after a complete rectangle. Variant i of every facing is
    the same piece turned around."""
    cat = load()
    per = {}
    pre = sheet + '_'
    for name, p in cat.items():
        if not name.startswith(pre) or not name[len(pre):].isdigit():
            continue
        if p.get('CustomName') != custom or (p.get('GroupName') or '') != (group or ''):
            continue
        gp = p.get('SpriteGridPos', '')
        gx, gy = (int(v) for v in gp.split(',')[:2]) if gp else (0, 0)
        per.setdefault(p.get('Facing', ''), []).append((int(name[len(pre):]), gx, gy, name))
    out = {}
    for facing, tiles in per.items():
        tiles.sort()
        cur, seen, vs, last = [], set(), [], None
        for idx, gx, gy, name in tiles:
            if cur:
                w = max(x for x, _ in seen) + 1
                h = max(y for _, y in seen) + 1
                whole = len(seen) == w * h          # the piece so far is a full rectangle
                if (gx, gy) in seen or (whole and idx - last > 3):
                    vs.append(cur)
                    cur, seen = [], set()
            cur.append((gx, gy, name))
            seen.add((gx, gy))
            last = idx
        if cur:
            vs.append(cur)
        norm = []
        for v in vs:
            mx, my = min(t[0] for t in v), min(t[1] for t in v)
            norm.append([(gx - mx, gy - my, name) for gx, gy, name in v])
        out[facing] = norm
    return out
