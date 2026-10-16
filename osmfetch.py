"""Fetch OpenStreetMap features for a bbox from Overpass (cached)."""
import json, os, sys, time, urllib.request, urllib.parse, hashlib
ENDPOINTS=["https://overpass.openstreetmap.fr/api/interpreter","https://overpass.private.coffee/api/interpreter","https://overpass-api.de/api/interpreter"]
def queries(s,w,n,e):
    b=f"({s},{w},{n},{e})"
    hdr="[out:json][timeout:90];(";ftr=");out geom;"
    return {
     'buildings': hdr+f' way["building"]{b}; relation["building"]{b};'+ftr,
     'highways': hdr+f' way["highway"]{b};'+ftr,
     'areas': hdr+f' way["landuse"]{b}; way["natural"]{b}; way["leisure"]{b}; way["amenity"="parking"]{b}; way["railway"]{b}; way["waterway"]{b}; relation["natural"="water"]{b};'+ftr,
     'barriers': hdr+f' way["barrier"]{b};'+ftr,
     'services': hdr+f' way["amenity"="fuel"]{b}; node["amenity"="fuel"]{b};'+ftr,
     'pois': hdr+f' node["natural"="tree"]{b}; node["highway"]{b}; node["amenity"]{b}; node["shop"]{b}; node["tourism"]{b}; node["office"]{b}; node["entrance"]{b};'+ftr,
    }
OPTIONAL={'services'}       # added later: an old cache without it still works offline
def fetch(s,w,n,e,cache_dir):
    os.makedirs(cache_dir,exist_ok=True)
    key=hashlib.md5(f"{s},{w},{n},{e}".encode()).hexdigest()[:10]
    out={'elements':[]}
    for name,q in queries(s,w,n,e).items():
        path=os.path.join(cache_dir,f"osm_{key}_{name}.json")
        if os.path.isfile(path): js=json.load(open(path,encoding='utf8'))
        else:
            js=None; last=None
            for attempt in range(3):
                for ep in ENDPOINTS:
                    try:
                        req=urllib.request.Request(ep,data=urllib.parse.urlencode({'data':q}).encode(),headers={'User-Agent':'osm2zomboid/0.1 (personal modding tool)'})
                        with urllib.request.urlopen(req,timeout=100) as r: js=json.loads(r.read().decode('utf8'))
                        break
                    except Exception as ex: last=ex; print('overpass fail',name,ep,ex,file=sys.stderr,flush=True); time.sleep(2)
                if js: break
                time.sleep(8)
            if js is None and name in OPTIONAL:
                print('  (could not fetch %s; going on without it)' % name, file=sys.stderr); continue
            if js is None: raise RuntimeError(last)
            json.dump(js,open(path,'w',encoding='utf8')); print('fetched',name,len(js['elements']),flush=True)
        out['elements']+=js['elements']
    return out
if __name__=='__main__':
    s,w,n,e=map(float,sys.argv[1:5]); js=fetch(s,w,n,e,sys.argv[5])
    import collections
    c=collections.Counter(); 
    for el in js['elements']:
        t=el.get('tags',{}); c[(el['type'],'building' if 'building' in t else 'highway' if 'highway' in t else 'other')]+=1
    print(len(js['elements']),c)
