"""Floor plan of part of a map (generated or vanilla): rooms by name, walls, doors, windows, furniture.

  python plan.py <maps folder> X0 Y0 X1 Y1 out.png [level] [pixels per square]

X/Y are absolute world squares. Black lines are walls, blue are windows, gaps with an orange tick are doors.
Grey dots are furniture, yellow dots lights, green squares stairs. Use it next to preview.py to compare a
generated building with one of the game's own.
"""
import os, sys, colorsys, zlib
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog
from lotreader import rd_header, rd_pack, iter_squares

CELL = 256
WALL_N = ('WallN', 'WallNW', 'WallNTrans', 'WallNWTrans')
WALL_W = ('WallW', 'WallNW', 'WallWTrans', 'WallNWTrans')
DOOR_N = ('doorN', 'DoorWallN', 'doorFrN', 'DoorWallNTrans')
DOOR_W = ('doorW', 'DoorWallW', 'doorFrW', 'DoorWallWTrans')
WIN_N = ('WindowN', 'windowN')
WIN_W = ('WindowW', 'windowW')


def load(mapdir, x0, y0, x1, y1):
    """-> ({(x, y, z): tiles}, {(x, y, z): room name}) for the cells the box touches, in world squares."""
    sq, rooms = {}, {}
    for cx in range(x0 // CELL - 1, (x1 - 1) // CELL + 1):       # a building is stored whole in the cell of
        for cy in range(y0 // CELL - 1, (y1 - 1) // CELL + 1):   # its north-west corner, so look one cell back too
            hp = os.path.join(mapdir, '%d_%d.lotheader' % (cx, cy))
            if not os.path.isfile(hp):
                continue
            h = rd_header(hp)
            ver, n, offs, pack = rd_pack(os.path.join(mapdir, 'world_%d_%d.lotpack' % (cx, cy)))
            for x, y, z, t in iter_squares(h, pack, offs, n):
                sq[(cx * CELL + x, cy * CELL + y, z)] = t
            for name, lvl, rects, _ in h['rooms']:
                for (x, y, w, hh) in rects:
                    for xx in range(x, x + w):
                        for yy in range(y, y + hh):
                            rooms[(cx * CELL + xx, cy * CELL + yy, lvl)] = name
    return sq, rooms


def colour(name):
    hue = (zlib.crc32(name.encode()) % 360) / 360.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.82, 0.55)
    return int(r * 255), int(g * 255), int(b * 255)


def render(mapdir, x0, y0, x1, y1, out, level=0, px=12):
    cat = catalog.load()
    sq, rooms = load(mapdir, x0 - 1, y0 - 1, x1 + 1, y1 + 1)
    W, H = (x1 - x0) * px, (y1 - y0) * px
    img = Image.new('RGB', (W + 1, H + 1), (245, 245, 240))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('arial.ttf', max(9, px - 2))
    except OSError:
        font = ImageFont.load_default()
    cent = {}
    for y in range(y0, y1):
        for x in range(x0, x1):
            nm = rooms.get((x, y, level))
            tiles = sq.get((x, y, level))
            X, Y = (x - x0) * px, (y - y0) * px
            if nm:
                d.rectangle([X, Y, X + px, Y + px], fill=colour(nm))
            elif tiles:
                d.rectangle([X, Y, X + px, Y + px], fill=(222, 222, 216))
            if not tiles:
                continue
            props = [cat.get(t, {}) for t in tiles]
            if any(k.startswith('stairs') for p in props for k in p):
                d.rectangle([X + 2, Y + 2, X + px - 2, Y + px - 2], fill=(60, 170, 80))
            elif any('LightRadius' in p for p in props):
                d.ellipse([X + 3, Y + 3, X + px - 3, Y + px - 3], fill=(240, 200, 0), outline=(120, 100, 0))
            elif any(('IsMoveAble' in p or 'container' in p or 'solid' in p or 'solidtrans' in p)
                     and not any(k in p for k in WIN_N + WIN_W + DOOR_N + DOOR_W + WALL_N + WALL_W) for p in props[1:]):
                d.ellipse([X + 4, Y + 4, X + px - 4, Y + px - 4], fill=(110, 110, 110))
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            props = [cat.get(t, {}) for t in sq.get((x, y, level), ())]
            X, Y = (x - x0) * px, (y - y0) * px
            for keys_d, keys_g, keys_w, line in ((DOOR_N, WIN_N, WALL_N, [X, Y, X + px, Y]),
                                                 (DOOR_W, WIN_W, WALL_W, [X, Y, X, Y + px])):
                if any(k in p for p in props for k in keys_d):
                    mx, my = (line[0] + line[2]) // 2, (line[1] + line[3]) // 2
                    d.line([mx - 2, my - 2, mx + 2, my + 2], fill=(230, 120, 0), width=3)
                elif any(k in p for p in props for k in keys_g):
                    d.line(line, fill=(40, 120, 255), width=3)
                elif any(k in p for p in props for k in keys_w):
                    d.line(line, fill=(0, 0, 0), width=2)
    # one label per room: flood fill by name
    seen = set()
    for y in range(y0, y1):
        for x in range(x0, x1):
            nm = rooms.get((x, y, level))
            if not nm or (x, y) in seen:
                continue
            todo, cells = [(x, y)], []
            seen.add((x, y))
            while todo:
                cx, cy = todo.pop()
                cells.append((cx, cy))
                for nx, ny, edge_sq, keys in ((cx, cy - 1, (cx, cy), WALL_N + DOOR_N + WIN_N), (cx, cy + 1, (cx, cy + 1), WALL_N + DOOR_N + WIN_N),
                                              (cx - 1, cy, (cx, cy), WALL_W + DOOR_W + WIN_W), (cx + 1, cy, (cx + 1, cy), WALL_W + DOOR_W + WIN_W)):
                    if (nx, ny) in seen or not (x0 <= nx < x1 and y0 <= ny < y1) or rooms.get((nx, ny, level)) != nm:
                        continue
                    if any(k in cat.get(t, {}) for t in sq.get(edge_sq + (level,), ()) for k in keys):
                        continue
                    seen.add((nx, ny))
                    todo.append((nx, ny))
            mx = sum(c[0] for c in cells) / len(cells) - x0 + 0.5
            my = sum(c[1] for c in cells) / len(cells) - y0 + 0.5
            label = nm if len(cells) >= 12 else nm[:4]
            tw = d.textlength(label, font=font)
            d.text((mx * px - tw / 2, my * px - px / 2), label, fill=(20, 20, 60), font=font)
    img.save(out)


if __name__ == '__main__':
    if len(sys.argv) < 7:
        sys.exit(__doc__)
    a = sys.argv
    render(a[1], int(a[2]), int(a[3]), int(a[4]), int(a[5]), a[6], int(a[7]) if len(a) > 7 else 0,
           int(a[8]) if len(a) > 8 else 12)
