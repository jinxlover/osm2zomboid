"""Check the lot reader / writer against the game's own files.

  python roundtrip.py [CX_CY ...]        default: a forest cell, a town cell, a cell with basements, a city cell

Each vanilla cell is imported (vanilla_import), written back out (lotwriter) and read again (lotreader).
The copy must hold the same tiles on every square of every level, the same rooms (name, level,
rectangles, objects), the same buildings and the same zombie map as the original.
"""
import collections, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lotwriter, vanilla_import
from lotreader import rd_header, rd_pack, iter_squares


def canon(h):
    rooms = [(n, lvl, tuple(r), tuple(o)) for n, lvl, r, o in h['rooms']]
    blds = collections.Counter(frozenset(collections.Counter(rooms[i] for i in b).items()) for b in h['blds'])
    return collections.Counter(rooms), blds


def check(cell, out):
    vd = vanilla_import.vanilla_dir()
    cx, cy = map(int, cell.split('_'))
    md = lotwriter.MapData(256, 256, cx, cy)
    vanilla_import.import_cell(md, cx, cy)
    lotwriter.write_all(md, out)
    a = rd_header(f'{vd}/{cell}.lotheader')
    b = rd_header(f'{out}/{cell}.lotheader')
    _, na, oa, pa = rd_pack(f'{vd}/world_{cell}.lotpack')
    _, nb, ob, pb = rd_pack(f'{out}/world_{cell}.lotpack')
    sa = {(x, y, z): t for x, y, z, t in iter_squares(a, pa, oa, na)}
    sb = {(x, y, z): t for x, y, z, t in iter_squares(b, pb, ob, nb)}
    ra, rb = canon(a), canon(b)
    ok = (sa == sb and ra == rb and a['zombie'] == b['zombie'] and (a['mn'], a['mx']) == (b['mn'], b['mx']))
    print(f'{cell}: levels {a["mn"]}..{a["mx"]}, {len(sa)} squares, {len(a["rooms"])} rooms, '
          f'{len(a["blds"])} buildings -> {"identical" if ok else "DIFFERENT"}')
    if not ok:
        print('   squares', sa == sb, 'rooms', ra[0] == rb[0], 'buildings', ra[1] == rb[1],
              'zombies', a['zombie'] == b['zombie'], 'levels', (a['mn'], a['mx']) == (b['mn'], b['mx']))
    return ok


if __name__ == '__main__':
    cells = sys.argv[1:] or ['50_43', '41_38', '41_37', '49_6']
    with tempfile.TemporaryDirectory() as tmp:
        results = [check(c, tmp) for c in cells]
    sys.exit(0 if all(results) else 1)
