"""chunkdata_X_Y.bin: the collision map of a cell for the game's off-screen zombie simulation.

The game gives this file to its native population / path library at start-up, for the parts of the map
that are not loaded. (When a chunk does load, the game rebuilds the same bits from the live squares.)

Format, read from the game's own map converter (zombie.pot.POTChunkData) and proved by re-writing
vanilla files byte for byte:
  int16 big-endian   version = 1
  32 x 32 chunks, row by row (chunk y outer, chunk x inner), each
      byte type      0 empty | 1 all solid | 3 all water | 4 all room | 2 regular
                     5 nothing there: chunks past the edge of the world (217 vanilla border files)
      type 2 only:   64 bytes, one per square, index = x + y * 8
  square bits        1 solid, 2 wall on the north edge, 4 wall on the west edge, 8 water, 16 inside a room

What sets each bit follows zombie.MapCollisionData (isSolid / isBlockedN / isBlockedW / isWater /
isRoom): solid furniture, but not trees or water; walls, door frames, windows and tall fences, but not
fences that can be hopped; inside a room; only ground level counts. Scored against vanilla's own
files these rules agree on 99.8% of squares or better for every bit (rooms and water exactly).
"""
import os
import numpy as np

import catalog

CELL, CHUNK, CPC = 256, 8, 32
SOLID, WALLN, WALLW, WATER, ROOM = 1, 2, 4, 8, 16
VOID = 255                                           # stands for chunk type 5 in the arrays used here
_UNIFORM = {0: 0, SOLID: 1, WATER: 3, ROOM: 4, VOID: 5}   # a chunk whose 64 squares all hold this value -> type
_VALUE = {t: v for v, t in _UNIFORM.items()}


def read(path):
    """-> uint8 array [y, x] (256 x 256) of square bits."""
    b = open(path, 'rb').read()
    if int.from_bytes(b[:2], 'big') != 1:
        raise ValueError('%s: unknown chunkdata version' % path)
    a = np.zeros((CELL, CELL), np.uint8)
    p = 2
    for cy in range(CPC):
        for cx in range(CPC):
            t = b[p]
            p += 1
            blk = a[cy * CHUNK:(cy + 1) * CHUNK, cx * CHUNK:(cx + 1) * CHUNK]
            if t == 2:
                blk[:] = np.frombuffer(b, np.uint8, 64, p).reshape(CHUNK, CHUNK)
                p += 64
            else:
                blk[:] = _VALUE[t]
    if p != len(b):
        raise ValueError('%s: %d bytes left over' % (path, len(b) - p))
    return a


def write(path, a):
    out = bytearray((1).to_bytes(2, 'big'))
    for cy in range(CPC):
        for cx in range(CPC):
            blk = a[cy * CHUNK:(cy + 1) * CHUNK, cx * CHUNK:(cx + 1) * CHUNK]
            v = int(blk[0, 0])
            if v in _UNIFORM and (blk == v).all():
                out.append(_UNIFORM[v])
            else:
                out.append(2)
                out += blk.astype(np.uint8).tobytes()
    with open(path, 'wb') as f:
        f.write(out)


_bits = {}
HOP_N, HOP_W = 32, 64          # working flags: a hoppable fence on that edge cancels the wall bit


def tile_bits(name):
    """Bits one tile contributes to its square (plus the two working hop flags, and 128 = clears solid)."""
    v = _bits.get(name)
    if v is None:
        p = catalog.load().get(name) or {}
        v = 0
        tree = 'tree' in p or name.startswith(('vegetation_trees', 'jumbo_tree'))
        if 'water' in p:
            v |= WATER | 128
        elif tree:
            v |= 128
        elif 'solid' in p or 'solidtrans' in p:       # (the live game also counts stairs; vanilla's files do not)
            v |= SOLID
        # walls, door frames and windows all stop the simulated zombie; only a hoppable fence does not
        if any(k in p for k in ('WallN', 'WallNW', 'WallNTrans', 'collideN', 'doorFrN', 'DoorWallN', 'WindowN', 'windowN')):
            v |= WALLN
        if any(k in p for k in ('WallW', 'WallNW', 'WallWTrans', 'collideW', 'doorFrW', 'DoorWallW', 'WindowW', 'windowW')):
            v |= WALLW
        if 'HoppableN' in p:
            v |= HOP_N
        if 'HoppableW' in p:
            v |= HOP_W
        _bits[name] = v
    return v


def square_bits(tiles, in_room=False):
    v = 0
    for t in tiles:
        v |= tile_bits(t)
    if v & 128:
        v &= ~SOLID
    if v & HOP_N:
        v &= ~WALLN
    if v & HOP_W:
        v &= ~WALLW
    return (v & 31) | (ROOM if in_room else 0)


def from_squares(squares, rooms):
    """squares: {(x, y): [tile names]} of ground level, cell-local; rooms: set of (x, y) inside a room."""
    a = np.zeros((CELL, CELL), np.uint8)
    for (x, y), tiles in squares.items():
        if 0 <= x < CELL and 0 <= y < CELL:
            a[y, x] = square_bits(tiles, (x, y) in rooms)
    return a


def from_map(md, ci, cj):
    """Bits of cell (ci, cj) of a lotwriter.MapData (map-local cell indices)."""
    x0, y0 = ci * CELL, cj * CELL
    a = np.zeros((CELL, CELL), np.uint8)
    room_of = md.room_of
    for (x, y, z), tiles in md.squares.items():
        if z == 0 and x0 <= x < x0 + CELL and y0 <= y < y0 + CELL:
            a[y - y0, x - x0] = square_bits(tiles, (x, y, 0) in room_of)
    return a


def write_all(md, outdir):
    """One chunkdata file per cell of the map that has squares."""
    os.makedirs(outdir, exist_ok=True)
    done = []
    for cj in range(md.cells_high):
        for ci in range(md.cells_wide):
            a = from_map(md, ci, cj)
            write(os.path.join(outdir, 'chunkdata_%d_%d.bin' % (md.cell_x0 + ci, md.cell_y0 + cj)), a)
            done.append((md.cell_x0 + ci, md.cell_y0 + cj))
    return done
