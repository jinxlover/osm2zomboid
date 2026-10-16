"""Write Project Zomboid Build 42 map cells (.lotheader / .lotpack).

Format (reverse engineered; verified by round-tripping vanilla cells, see roundtrip.py):
  cell      = 256x256 squares, chunk = 8x8 squares, 32x32 chunks per cell
  lotheader = 'LOTH' i32 version(1) i32 ntiles, tile names ('\n'-terminated),
              i32 8, i32 8, i32 minLevel, i32 maxLevel(inclusive),
              i32 nrooms, rooms{name\n, i32 level, i32 nrects, rects{x,y,w,h}, i32 nobjects, objs{type,x,y}},
              i32 nbuildings, buildings{i32 n, i32 roomIdx...}, 1024 bytes zombie intensity per chunk
  lotpack   = 'LOTP' i32 version(1) u32 1024, 1024 x (u32 offset, u32 0), then per chunk
              for level in min..max: for x in 0..7: for y in 0..7:
                 i32 n; n==-1 -> i32 skip (that many squares are empty, counting this one)
                        n>1  -> i32 roomId(-1 none), (n-1) x i32 tileIndex
  Room rect coordinates are cell-local. The per-square roomId in the lotpack is -1 in vanilla town cells;
  rooms are defined by the header rects. minLevel is negative in cells with basements (level -1 ...).
"""
import os, struct
from collections import defaultdict

CELL = 256
CHUNK = 8
CPC = CELL // CHUNK  # chunks per cell side


class Room:
    __slots__ = ("name", "z", "rects", "id", "objs")

    def __init__(self, name, z, rects, objs=None):
        self.name = name
        self.z = z
        self.rects = rects  # list of (x, y, w, h) in map-local squares
        self.objs = objs or []  # room meta objects (type, x, y) in map-local squares (kept from vanilla cells)


class MapData:
    """Sparse square store in map-local coordinates (0,0 = north-west corner of the map)."""

    def __init__(self, width, height, cell_x0, cell_y0):
        self.width, self.height = width, height
        self.cell_x0, self.cell_y0 = cell_x0, cell_y0
        self.squares = {}        # (x,y,z) -> list[str]
        self.room_of = {}        # (x,y,z) -> room index into self.rooms
        self.rooms = []          # Room
        self.buildings = []      # list[list[room index]]
        self.zombie = {}         # (chunk_x, chunk_y) -> 0..255 (map-local chunk coords)

    def add(self, x, y, z, tile):
        self.squares.setdefault((x, y, z), []).append(tile)

    def put(self, x, y, z, tiles):
        self.squares[(x, y, z)] = list(tiles)

    def add_room(self, name, z, rects, building=None, objs=None):
        r = Room(name, z, rects, objs)
        r.id = len(self.rooms)
        self.rooms.append(r)
        for (x, y, w, h) in rects:
            for xx in range(x, x + w):
                for yy in range(y, y + h):
                    self.room_of[(xx, yy, z)] = r.id
        return r.id

    @property
    def cells_wide(self):
        return (self.width + CELL - 1) // CELL

    @property
    def cells_high(self):
        return (self.height + CELL - 1) // CELL


def _s(name):
    return name.encode('utf8') + b'\n'


def write_cell(md, ci, cj, outdir, default_zombie=0):
    """Write cell (ci, cj) (map-local cell indices). Returns (cx, cy) world cell or None if empty."""
    cx, cy = md.cell_x0 + ci, md.cell_y0 + cj
    x0, y0 = ci * CELL, cj * CELL
    # collect squares in this cell
    cell_sq = {}
    maxz = minz = 0
    for (x, y, z), t in md.squares.items():
        if x0 <= x < x0 + CELL and y0 <= y < y0 + CELL and t:
            cell_sq[(x - x0, y - y0, z)] = t
            if z > maxz:
                maxz = z
            elif z < minz:
                minz = z
    if not cell_sq:
        return None
    # rooms: like vanilla, a building is stored whole in the cell that holds its north-west corner
    # (room rects may then run past 256), never split between two cells
    rooms = []              # (name, z, rects_local, objs_local)
    room_map = {}           # global room id -> local index
    blds = []
    in_building = set()

    def home(room_ids):
        mx = min(x for i in room_ids for (x, y, w, h_) in md.rooms[i].rects)
        my = min(y for i in room_ids for (x, y, w, h_) in md.rooms[i].rects)
        return mx // CELL == ci and my // CELL == cj

    def add(i):
        r = md.rooms[i]
        room_map[i] = len(rooms)
        rooms.append((r.name, r.z, [(x - x0, y - y0, w, h_) for (x, y, w, h_) in r.rects],
                      [(t, x - x0, y - y0) for (t, x, y) in r.objs]))

    for b in md.buildings:
        ids = [i for i in b if md.rooms[i].rects]
        in_building.update(b)
        if ids and home(ids):
            for i in ids:
                add(i)
            blds.append([room_map[i] for i in ids])
    for r in md.rooms:
        if r.id not in in_building and r.rects and home([r.id]):
            add(r.id)
    # tile palette
    palette = {}
    names = []

    def tid(n):
        i = palette.get(n)
        if i is None:
            i = palette[n] = len(names)
            names.append(n)
        return i

    # ---- lotpack ----
    blocks = []
    for bx in range(CPC):
        for by in range(CPC):
            out = bytearray()
            skip = 0
            for z in range(minz, maxz + 1):
                for a in range(CHUNK):
                    for b in range(CHUNK):
                        lx, ly = bx * CHUNK + a, by * CHUNK + b
                        t = cell_sq.get((lx, ly, z))
                        if not t:
                            skip += 1
                            continue
                        if skip:
                            out += struct.pack('<ii', -1, skip)
                            skip = 0
                        rid = -1
                        out += struct.pack('<ii', len(t) + 1, rid)
                        out += struct.pack('<%di' % len(t), *[tid(n) for n in t])
            if skip:
                out += struct.pack('<ii', -1, skip)
            blocks.append(bytes(out))
    head = struct.pack('<4siI', b'LOTP', 1, len(blocks))
    pos = len(head) + 8 * len(blocks)
    table = bytearray()
    for blk in blocks:
        table += struct.pack('<II', pos, 0)
        pos += len(blk)
    pack = head + bytes(table) + b''.join(blocks)

    # ---- lotheader ----
    h = bytearray(b'LOTH')
    h += struct.pack('<ii', 1, len(names))
    for n in names:
        h += _s(n)
    h += struct.pack('<iiii', CHUNK, CHUNK, minz, maxz)
    h += struct.pack('<i', len(rooms))
    offx = offy = 0  # rects are cell-local (verified on vanilla cell 41_36)
    for name, z, rects, objs in rooms:
        h += _s(name)
        h += struct.pack('<ii', z, len(rects))
        for (x, y, w, hh) in rects:
            h += struct.pack('<iiii', x + offx, y + offy, w, hh)
        h += struct.pack('<i', len(objs))
        for (t, x, y) in objs:
            h += struct.pack('<iii', t, x, y)
    h += struct.pack('<i', len(blds))
    for b in blds:
        h += struct.pack('<i', len(b))
        h += struct.pack('<%di' % len(b), *b)
    zb = bytearray(CPC * CPC)
    for bx in range(CPC):
        for by in range(CPC):
            v = md.zombie.get(((x0 // CHUNK) + bx, (y0 // CHUNK) + by), default_zombie)
            zb[bx * CPC + by] = max(0, min(255, int(v)))
    h += zb
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, f'{cx}_{cy}.lotheader'), 'wb') as f:
        f.write(h)
    with open(os.path.join(outdir, f'world_{cx}_{cy}.lotpack'), 'wb') as f:
        f.write(pack)
    return cx, cy


def write_all(md, outdir, **kw):
    done = []
    for cj in range(md.cells_high):
        for ci in range(md.cells_wide):
            r = write_cell(md, ci, cj, outdir, **kw)
            if r:
                done.append(r)
    return done
