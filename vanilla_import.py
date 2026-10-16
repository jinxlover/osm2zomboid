"""Copy a vanilla cell into a MapData so a mod can extend it without destroying it."""
import os, sys, struct
sys.path.insert(0, os.path.dirname(__file__))
from lotreader import rd_header, rd_pack, iter_squares
import catalog


def vanilla_dir():
    return os.path.join(catalog.game_dir(), 'media', 'maps', 'Muldraugh, KY')


def import_cell(md, cx, cy, ox=0, oy=0, vanilla=None):
    """Add vanilla cell (cx,cy) into md at map-local offset (ox,oy) (multiple of 256)."""
    vanilla = vanilla or vanilla_dir()
    h = rd_header(f'{vanilla}/{cx}_{cy}.lotheader')
    ver, n, offs, pack = rd_pack(f'{vanilla}/world_{cx}_{cy}.lotpack')
    for x, y, z, t in iter_squares(h, pack, offs, n):
        md.squares[(ox + x, oy + y, z)] = t
    ids = []
    for name, lvl, rects, objs in h['rooms']:
        ids.append(md.add_room(name, lvl, [(ox + x, oy + y, w, hh) for x, y, w, hh in rects],
                               objs=[(t, ox + x, oy + y) for t, x, y in objs]))
    for b in h['blds']:
        md.buildings.append([ids[i] for i in b if i < len(ids)])
    z = h['zombie']
    for bx in range(32):
        for by in range(32):
            md.zombie[((ox // 8) + bx, (oy // 8) + by)] = z[bx * 32 + by]
    return h
