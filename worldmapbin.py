"""worldmap.xml.bin: the binary form of a map folder's worldmap.xml, which the game reads instead of the XML
when it is there (vanilla ships both). Build 42's XML reader stores each shape's point count in shorts
(two per point) and then reads that many points, so every shape loaded from XML fails with an
IndexOutOfBoundsException; the binary reader counts points, so maps load from it.

Format (zombie.worldMap.WorldMapBinary, little-endian):
  'IGMB', int version (2), int cell size (256), int cells wide, int cells high
  string table: int n, then n x (short length, UTF-8 bytes)
  cells wide x high times: int cell x (-1 = no cell), int cell y, int feature count, then per feature:
    short type (string index), byte ring count, per ring: short point count, points as (short x, short y)
    byte property count, per property: short key, short value (string indices)

  python worldmapbin.py <worldmap.xml> [out.bin]      convert;   python worldmapbin.py --read <file.bin>
"""
import struct, sys
import xml.etree.ElementTree as ET


def read(path):
    """-> [(cell x, cell y, [(type, [[(x, y)...] rings], {key: value})])]  (to check files)"""
    b = open(path, 'rb').read()
    p = 0

    def take(fmt):
        nonlocal p
        v = struct.unpack_from('<' + fmt, b, p)
        p += struct.calcsize('<' + fmt)
        return v if len(v) > 1 else v[0]
    assert b[:4] == b'IGMB', 'not a world map binary'
    p = 4
    version, size, w, h = take('iiii')
    strings = []
    for _ in range(take('i')):
        n = take('h')
        strings.append(b[p:p + n].decode('utf8'))
        p += n
    cells = []
    for _ in range(w * h):
        cx = take('i')
        if cx == -1:
            continue
        cy, nf = take('ii')
        feats = []
        for _ in range(nf):
            typ = strings[take('h')]
            rings = []
            for _ in range(take('B')):
                n = take('h')
                rings.append([take('hh') for _ in range(n)])
            props = {}
            for _ in range(take('B')):
                k, v = take('hh')
                props[strings[k]] = strings[v]
            feats.append((typ, rings, props))
        cells.append((cx, cy, feats))
    assert p == len(b), 'trailing bytes'
    return dict(version=version, cell_size=size, w=w, h=h, cells=cells)


def write(cells, path):
    """cells: [(cell x, cell y, [(type, rings, {key: value})])] -> a .bin the game reads."""
    index = {}

    def sid(s):
        if s not in index:
            index[s] = len(index)
        return index[s]
    body = bytearray()
    for cx, cy, feats in cells:
        body += struct.pack('<iii', cx, cy, len(feats))
        for typ, rings, props in feats:
            body += struct.pack('<hB', sid(typ), len(rings))
            for ring in rings:
                body += struct.pack('<h', len(ring))
                for x, y in ring:
                    body += struct.pack('<hh', int(x), int(y))
            body += struct.pack('<B', len(props))
            for k, v in props.items():
                body += struct.pack('<hh', sid(k), sid(v))
    head = bytearray(b'IGMB') + struct.pack('<iiii', 2, 256, len(cells), 1)
    head += struct.pack('<i', len(index))
    for s in sorted(index, key=index.get):
        e = s.encode('utf8')
        head += struct.pack('<h', len(e)) + e
    open(path, 'wb').write(bytes(head + body))


def from_xml(xml_path, out=None):
    """Convert a worldmap.xml (256-square cells) to worldmap.xml.bin beside it."""
    root = ET.parse(xml_path).getroot()
    cells = []
    for c in root.iter('cell'):
        feats = []
        for f in c.iter('feature'):
            g = f.find('geometry')
            rings = [[(int(p.get('x')), int(p.get('y'))) for p in co.iter('point')] for co in g.iter('coordinates')]
            rings = [r for r in rings if r]
            if not rings:
                continue
            props = {p.get('name'): p.get('value') for p in f.iter('property')}
            feats.append((g.get('type'), rings, props))
        cells.append((int(c.get('x')), int(c.get('y')), feats))
    out = out or xml_path + '.bin'
    write(cells, out)
    return out, len(cells), sum(len(f) for _, _, f in cells)


if __name__ == '__main__':
    if sys.argv[1] == '--read':
        d = read(sys.argv[2])
        print('version %d, cell size %d, grid %dx%d, %d cells, %d features' % (
            d['version'], d['cell_size'], d['w'], d['h'], len(d['cells']), sum(len(f) for _, _, f in d['cells'])))
        cx, cy, feats = d['cells'][0]
        print('first cell', cx, cy, feats[:2])
    else:
        print('wrote %s: %d cells, %d features' % from_xml(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
