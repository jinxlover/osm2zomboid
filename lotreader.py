import struct, sys
def rd_header(path):
    b=open(path,'rb').read(); p=0
    def i4():
        nonlocal p; v=struct.unpack_from('<i',b,p)[0]; p+=4; return v
    assert b[:4]==b'LOTH'; p=4
    ver=i4(); nt=i4(); tiles=[]
    for _ in range(nt):
        e=b.index(b'\n',p); tiles.append(b[p:e].decode()); p=e+1
    bw=i4(); bh=i4(); mn=i4(); mx=i4(); nr=i4(); rooms=[]
    for _ in range(nr):
        e=b.index(b'\n',p); name=b[p:e].decode(); p=e+1
        lvl=i4(); n=i4(); rects=[(i4(),i4(),i4(),i4()) for _ in range(n)]
        no=i4(); objs=[(i4(),i4(),i4()) for _ in range(no)]
        rooms.append((name,lvl,rects,objs))
    nb=i4(); blds=[]
    for _ in range(nb):
        n=i4(); blds.append([i4() for _ in range(n)])
    return dict(ver=ver,tiles=tiles,bw=bw,bh=bh,mn=mn,mx=mx,rooms=rooms,blds=blds,rest=len(b)-p,zombie=bytes(b[p:p+1024]))
def rd_pack(path):
    b=open(path,'rb').read()
    assert b[:4]==b'LOTP'
    ver,n=struct.unpack_from('<iI',b,4)
    offs=[struct.unpack_from('<I',b,12+8*i)[0] for i in range(n)]
    return ver,n,offs,b


def iter_squares(h, pack, offs, n):
    """Yield (x, y, level, [tile names]) for every non-empty square of a cell (cell-local x,y).
    `level` is the real level: cells with basements start at h['mn'] = -1 (or lower)."""
    mn = h.get('mn', 0)
    for ci in range(n):
        s = offs[ci]
        e = offs[ci + 1] if ci + 1 < n else len(pack)
        ints = struct.unpack('<%di' % ((e - s) // 4), pack[s:e])
        i = 0
        k = 0
        bx, by = divmod(ci, 32)
        while i < len(ints):
            c = ints[i]
            i += 1
            if c == -1:
                k += ints[i]
                i += 1
            elif c > 1:
                lvl, kk = divmod(k, 64)
                a, b = divmod(kk, 8)
                yield bx * 8 + a, by * 8 + b, lvl + mn, [h['tiles'][t] for t in ints[i + 1:i + c]]
                k += 1
                i += c
            else:
                k += 1


def cell_squares(mapdir, cx, cy):
    h = rd_header(f'{mapdir}/{cx}_{cy}.lotheader')
    ver, n, offs, pack = rd_pack(f'{mapdir}/world_{cx}_{cy}.lotpack')
    return iter_squares(h, pack, offs, n)
