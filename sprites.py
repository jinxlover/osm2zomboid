"""Read PZ texture packs (PZPK) so we can look at tiles and render previews."""
import io, os, struct, pickle
from PIL import Image
import catalog

def _u32(d,p): return struct.unpack_from('<I',d,p)[0], p+4
def _i32(d,p): return struct.unpack_from('<i',d,p)[0], p+4
def _blob(d,p):
    n,p=_u32(d,p); return d[p:p+n], p+n

def load_pack(path):
    d=open(path,'rb').read(); p=0
    ver=0
    if d[:4]==b'PZPK': ver,p=_u32(d,4)
    npg,p=_u32(d,p); out={}
    for _ in range(npg):
        name,p=_blob(d,p); cnt,p=_u32(d,p); _,p=_u32(d,p); texs=[]
        for _ in range(cnt):
            nm,p=_blob(d,p); v=[]
            for _ in range(8): x,p=_i32(d,p); v.append(x)
            texs.append((nm.decode(),*v))
        if ver==0:
            e=d.index(b'\xef\xbe\xad\xde',p)+4; png=d[p:e-4]; p=e
        else: png,p=_blob(d,p)
        img=Image.open(io.BytesIO(png)).convert('RGBA')
        for nm,x,y,w,h,ox,oy,ow,oh in texs:
            out[nm]=(img.crop((x,y,x+w,y+h)),ox,oy,ow,oh)
    return out

_lib=None
def library(packs=('Tiles2x.pack','Tiles2x.floor.pack','JumboTrees2x.pack','Overlays2x.pack','Overlays2x.floor.pack')):
    global _lib
    if _lib is None:
        _lib={}
        PZ=os.path.join(catalog.game_dir(),'media','texturepacks')
        for pk in packs:
            f=os.path.join(PZ,pk)
            if os.path.isfile(f): _lib.update(load_pack(f))
    return _lib

