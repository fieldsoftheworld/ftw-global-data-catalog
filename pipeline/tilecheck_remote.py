import math, gzip, sys
import urllib.request
from pmtiles.reader import Reader
import mapbox_vector_tile as mvt, duckdb
year=sys.argv[1]
def tile(lon,lat,z):
    n=2**z; return int((lon+180)/360*n), int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
def bounds(x,y,z):
    n=2**z; f=lambda yy: math.degrees(math.atan(math.sinh(math.pi*(1-2*yy/n))))
    return x/n*360-180, f(y+1), (x+1)/n*360-180, f(y)
c=duckdb.connect(); c.execute("install spatial; load spatial")
URL=f"https://data.source.coop/ftw/global-data-beta/vector/{year}/fields-{year}.pmtiles"
def get_bytes(off,ln):
    rq=urllib.request.Request(URL,headers={"Range":f"bytes={off}-{off+ln-1}","User-Agent":"Mozilla/5.0"})
    return urllib.request.urlopen(rq,timeout=60).read()
if True:
    r=Reader(get_bytes)
    h=r.header(); print(year,"zoom",h["min_zoom"],h["max_zoom"],"tiles",h["tile_entries_count"],"addressed",h["addressed_tiles_count"])
    c0=r.get(0,0,0); print("  z0 tile",None if c0 is None else len(gzip.decompress(c0)),"bytes; layers",list(mvt.decode(gzip.decompress(c0))) if c0 else None)
    for name,lon,lat in [("iowa",-93.6,42.0),("kagera",31.2,-1.5),("pampas",-62.0,-34.0),("sask",-106.0,52.0),("punjab",75.0,31.0)]:
        z=13; x,y=tile(lon,lat,z); d=r.get(z,x,y)
        if d is None: print(name,"no tile"); continue
        t=mvt.decode(gzip.decompress(d)); fs=t.get("fields",{}).get("features",[])
        w,s,e,n=bounds(x,y,z)
        zn=int((lon+180)//6)+1
        zs=[zn-1,zn,zn+1]
        k=0
        for zz in zs:
            if 1<=zz<=60:
                fp=f"/projects/bgtj/isaaccorley/ftw-fiboa-v2/{year}/zone={zz:02d}/utm{zz:02d}.parquet"
                k+=c.execute(f"select count(*) from '{fp}' where bbox.xmin<={e} and bbox.xmax>={w} and bbox.ymin<={n} and bbox.ymax>={s}").fetchone()[0]
        print(name,(z,x,y),"tile features",len(fs),"| parquet features in tile bbox",k,"| props",list(fs[0]["properties"]) if fs else None)
