#!/usr/bin/env python3
"""Regenerate targets.csv — the shared deep-sky catalogue EyesUp plans against.

Pulls coordinates and object types from SIMBAD for the Messier catalogue plus a
set of popular non-Messier / narrowband targets, classifies each as Narrowband
(emission: HII / SNR / planetary) or RGB (galaxies, clusters, reflection), and
writes targets.csv. You only need to run this to extend or refresh the list;
the committed targets.csv is enough to build the app offline.

    python fetch_catalog.py
"""
from __future__ import annotations
import csv, io, urllib.parse, urllib.request

SIMBAD = "https://simbad.u-strasbg.fr/simbad/sim-tap/sync"
EXTRAS = ["NGC 7000","IC 5070","NGC 6888","IC 1805","IC 1848","IC 1396","NGC 7380","NGC 6960",
"NGC 6992","NGC 7635","NGC 281","NGC 2237","NGC 2264","NGC 1499","NGC 7822","IC 434","NGC 2359",
"NGC 3372","NGC 253","NGC 7293","NGC 869","NGC 884","NGC 6543","NGC 891","NGC 4565","NGC 7331"]

NAMES={"M1":"Crab","M8":"Lagoon","M13":"Hercules Cluster","M16":"Eagle","M17":"Omega","M20":"Trifid",
"M27":"Dumbbell","M31":"Andromeda","M33":"Triangulum","M42":"Orion Nebula","M43":"de Mairan's",
"M44":"Beehive","M45":"Pleiades","M51":"Whirlpool","M57":"Ring","M63":"Sunflower","M64":"Black Eye",
"M76":"Little Dumbbell","M81":"Bode's","M82":"Cigar","M97":"Owl","M101":"Pinwheel","M104":"Sombrero",
"M11":"Wild Duck","M6":"Butterfly","M7":"Ptolemy","M22":"Sgr Cluster",
"NGC7000":"North America","IC5070":"Pelican","NGC6888":"Crescent","IC1805":"Heart","IC1848":"Soul",
"IC1396":"Elephant Trunk","NGC6960":"Veil (West)","NGC6992":"Veil (East)","NGC7635":"Bubble",
"NGC281":"Pacman","NGC2237":"Rosette","NGC2264":"Cone","NGC1499":"California","IC434":"Horsehead",
"NGC2359":"Thor's Helmet","NGC3372":"Carina","NGC253":"Sculptor","NGC7293":"Helix","NGC869":"Double Cluster",
"NGC884":"Double Cluster","NGC6543":"Cat's Eye","NGC4565":"Needle","NGC7380":"Wizard"}
# Emission nebulae SIMBAD tags as clusters/other -> force Narrowband:
NB_FORCE={"M8","M16","M17","M20","M42","M43","IC1805","IC1848","IC1396","IC5070","IC434","NGC7000",
"NGC2237","NGC2264","NGC7380","NGC281","NGC1499","NGC7635","NGC7822","NGC6888","NGC6960","NGC6992","NGC2359"}
NB_OTYPES={"SNR","HII","PN","EmN","ISM","GNe","Neb","Cld","MoC","DNe"}
SUB={"GlC":"globular cluster","OpC":"open cluster","SNR":"supernova remnant","HII":"emission nebula",
"PN":"planetary nebula","RNe":"reflection nebula","Cl*":"star cluster","As*":"association"}

def query(adql):
    data=urllib.parse.urlencode({"request":"doQuery","lang":"adql","format":"csv",
        "maxrec":"2000","query":adql}).encode()
    with urllib.request.urlopen(SIMBAD, data=data, timeout=60) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode())))

GALAXY_OTYPES={"AGN","Sy1","Sy2","SyG","LIN","SBG","EmG","H2G","rG","GiG","GiC","GiP","IG","BiC"}
def sub(o):
    o=o.strip()
    if o=="GlC": return "globular cluster"
    if o.startswith("G") or o in GALAXY_OTYPES: return "galaxy"
    return SUB.get(o, "emission nebula" if o in NB_OTYPES else o)

def classify(tid,o):
    return "NB" if (tid in NB_FORCE or o.strip() in NB_OTYPES) else "RGB"

def main():
    rows = query("SELECT id, ra, dec, otype FROM ident JOIN basic ON oidref=oid WHERE id LIKE 'M %'")
    inlist = ",".join("'%s'" % e for e in EXTRAS)
    rows += query(f"SELECT id, ra, dec, otype FROM ident JOIN basic ON oidref=oid WHERE id IN ({inlist})")
    out=[]
    for r in rows:
        tid=r["id"].replace(" ","")
        try: ra=float(r["ra"]); dec=float(r["dec"])
        except (TypeError,ValueError): continue
        out.append({"id":tid,"name":NAMES.get(tid,tid),"ra":round(ra,4),"dec":round(dec,4),
                    "type":classify(tid,r["otype"]),"sub":sub(r["otype"])})
    out.sort(key=lambda x:(0 if x["id"].startswith("M") else 1, x["id"]))
    with open("targets.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["id","name","ra","dec","type","sub"]); w.writeheader()
        w.writerows(out)
    print(f"Wrote targets.csv: {len(out)} targets "
          f"({sum(t['type']=='NB' for t in out)} NB, {sum(t['type']=='RGB' for t in out)} RGB)")

if __name__ == "__main__":
    main()
