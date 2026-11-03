#!/usr/bin/env python3
"""EyesUp builder — turn your site config + horizon into an offline planner.

Reads:
  config.toml   your location and thresholds   (copy from config.example.toml)
  horizon.csv   your measured horizon (az,alt)  (copy from horizon.example.csv)
  targets.csv   the shared deep-sky catalogue   (ships with the repo)

Writes:
  dist/eyesup.html   the self-contained app (open in any browser)
  dist/data.json     the precomputed dataset it embeds

All astronomy is done here in pure NumPy so the browser only needs a trivial
moon calc. Nothing about your location leaves this machine — dist/ and your
config/horizon are git-ignored by default.

    python build.py
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, os, re, shutil, struct, sys, threading, tomllib
import urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np

YEAR = 2026  # non-leap reference year; results are keyed by day-of-year
DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")  # JavaScript's getDay() order
# Narrowband filters for "worth setting up": (name, wavelength nm, how much a full Moon
# brightens that filter's sky, as a multiple of the dark sky) — SkyGuard's FILTERS.
FILTERS = [["Hα/SII", 656.3, 0.3], ["OIII", 500.7, 2.0]]
# Framing previews, hosted next to the page so they don't depend on a third-party
# service being up: a list thumbnail and the detail-sheet image, in pixels wide.
IMG_WIDTHS = {"t": 128, "f": 360}
IMG_CACHE = "imgcache"
UA = {"User-Agent": "EyesUp/1 (+https://github.com/iamseb/eyesup)"}


def jd_of(dt_utc):
    return dt_utc.timestamp() / 86400.0 + 2440587.5

def gmst_deg(jd):
    d = jd - 2451545.0; T = d / 36525.0
    return (280.46061837 + 360.98564736629 * d + 0.000387933 * T * T - T**3 / 38710000.0) % 360.0

def sun_radec(jd):
    T = (jd - 2451545.0) / 36525.0
    L = math.radians((280.460 + 36000.771 * T) % 360)
    g = math.radians((357.528 + 35999.050 * T) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.013 * T)
    return (math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360,
            math.degrees(math.asin(math.sin(eps) * math.sin(lam))))

def altaz(ra, dec, lst_deg, lat_deg):
    """Vectorised. ra/dec scalar or array; lst array. Returns (alt, az) in degrees."""
    H = np.radians(lst_deg - ra)
    d = np.radians(dec); L = math.radians(lat_deg)
    alt = np.degrees(np.arcsin(np.cos(H)*np.cos(d)*math.cos(L) + np.sin(d)*math.sin(L)))
    e = -np.sin(H)*np.cos(d)
    n = np.sin(d)*math.cos(L) - np.cos(H)*np.cos(d)*math.sin(L)
    return alt, np.degrees(np.arctan2(e, n)) % 360


def load_config():
    try:
        with open("config.toml", "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        sys.exit("config.toml not found — copy config.example.toml to config.toml and edit it.")

def load_horizon():
    """Read az,alt pairs (any azimuths) and interpolate to a 0..359 profile."""
    try:
        rows = list(csv.DictReader(open("horizon.csv")))
    except FileNotFoundError:
        sys.exit("horizon.csv not found — copy horizon.example.csv to horizon.csv (or measure your own).")
    az = np.array([float(r["azimuth"]) for r in rows]) % 360
    al = np.array([float(r["altitude"]) for r in rows])
    order = np.argsort(az); az, al = az[order], al[order]
    azw = np.concatenate([az - 360, az, az + 360]); alw = np.concatenate([al, al, al])
    return np.interp(np.arange(360.0), azw, alw)

def load_icons():
    """Meteocons (icons/, MIT) as data URIs, with each viewBox's size for equal-area drawing."""
    out = {}
    for f in sorted(os.listdir("icons")):
        if f.endswith(".svg"):
            svg = open(os.path.join("icons", f)).read()
            w, h = map(float, re.search(r'viewBox="[^"]*?(\S+) (\S+)"', svg).groups())
            out[f[:-4]] = {"uri": "data:image/svg+xml," + urllib.parse.quote(svg), "w": w, "h": h}
    return out

def jpeg_size(b):
    """(width, height) of a JPEG, or None if it isn't one."""
    if b[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            i += 1; continue
        m = b[i + 1]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        if m == 0xD8 or m == 0x01 or 0xD0 <= m <= 0xD7 or m == 0xFF:
            i += 2 if m != 0xFF else 1; continue
        i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    return None

# Where a preview comes from, best first: CDS's colour DSS2, then NASA SkyView's
# greyscale DSS (slower, and it asks callers to go gently; DSS rather than DSS2 as
# some DSS2 red plates have blank patches). The template falls back through the same
# two live. (name, parallel requests, url)
IMG_SOURCES = [
    ("cds", 4, lambda ra, dec, fw, fh, w, h: "https://alasky.u-strasbg.fr/hips-image-services/hips2fits?"
        f"hips=CDS%2FP%2FDSS2%2Fcolor&ra={ra}&dec={dec}&fov={fw}&width={w}&height={h}&projection=TAN&format=jpg"),
    ("skyview", 2, lambda ra, dec, fw, fh, w, h: "https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl?"
        f"Survey=DSS&Scaling=Sqrt&Position={ra},{dec}&Size={fw},{fh}&Pixels={w},{h}&Return=JPEG"),
]

def fetch_images(targets, fov, hosted, offline=False):
    """Make sure every target has its previews, returning {id: {"t": file, "f": file}}.

    A preview is named by a hash of the request that made it, so it never needs
    fetching twice: files already on the CDN (hosted) or in imgcache/ are reused, and
    only missing ones are downloaded. A SkyView stand-in is swapped for the CDS colour
    image on a later build once CDS answers. Previews no source could supply are left
    out, and the page fetches those live.
    """
    os.makedirs(IMG_CACHE, exist_ok=True)
    have = set(hosted) | set(os.listdir(IMG_CACHE))
    gate = {src: threading.Semaphore(n) for src, n, _ in IMG_SOURCES}
    fails = {src: 0 for src, _, _ in IMG_SOURCES}
    lock = threading.Lock()

    def download(src, url, w, h):
        if fails[src] >= 3:  # the service looks down: stop waiting on it this build
            return None
        with gate[src]:
            for attempt in range(3):
                try:
                    b = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
                except Exception:
                    b = b""
                if jpeg_size(b) == (w, h):
                    with lock:
                        fails[src] = 0
                    return b
                if b"Too Many" not in b:  # SkyView's "busy, try again shortly"
                    break
                threading.Event().wait(5 * (attempt + 1))
        with lock:
            fails[src] += 1
        return None

    def preview(job):
        tid, _, args = job
        for src, _, url in IMG_SOURCES:
            u = url(*args)
            name = re.sub(r"\W", "", tid) + "-" + hashlib.sha1(u.encode()).hexdigest()[:10] + f"-{src}.jpg"
            if name in have:
                return name
            if not offline:
                b = download(src, u, args[4], args[5])
                if b:
                    open(os.path.join(IMG_CACHE, name), "wb").write(b)
                    return name
        return None

    jobs = [(t[0], k, (t[2], t[3], fov["w"], fov["h"], w, round(w * fov["h"] / fov["w"])))
            for t in targets for k, w in IMG_WIDTHS.items()]  # (target id, size, url arguments)
    with ThreadPoolExecutor(4) as ex:
        names = list(ex.map(preview, jobs))
    out = {}
    for j, name in zip(jobs, names):
        if name:
            out.setdefault(j[0], {})[j[1]] = name
    os.makedirs("dist/img", exist_ok=True)
    for name in filter(None, names):
        if name not in hosted:
            shutil.copyfile(os.path.join(IMG_CACHE, name), os.path.join("dist/img", name))
    by_src = {src: sum(1 for n in names if n and n.endswith(f"-{src}.jpg")) for src, _, _ in IMG_SOURCES}
    print("  previews: " + ", ".join(f"{n} {src}" for src, n in by_src.items())
          + f", {names.count(None)} missing (fetched live)")
    return out

def load_targets():
    out = []
    for r in csv.DictReader(open("targets.csv")):
        out.append((r["id"], r["name"], float(r["ra"]), float(r["dec"]), r["type"], r["sub"]))
    return out


def selftest(lat, lon):
    # Sun altitude near local noon at an equinox should be ~ (90 - |lat|).
    tz = ZoneInfo("UTC")
    jd = jd_of(datetime(2026, 3, 20, 12, 0, 0, tzinfo=tz))
    ra, dec = sun_radec(jd)
    alt, _ = altaz(ra, dec, np.array([gmst_deg(jd) + lon]), lat)
    expect = 90 - abs(lat)
    assert abs(float(alt[0]) - expect) < 5, f"self-test failed: sun alt {alt[0]:.1f} vs ~{expect:.1f}"


def main():
    ap = argparse.ArgumentParser(description="Build dist/eyesup.html.")
    ap.add_argument("--hosted", help="file listing preview images already on the CDN, one per line")
    ap.add_argument("--offline", action="store_true", help="don't download previews; use imgcache/ only")
    opts = ap.parse_args()
    cfg = load_config()
    lat = float(cfg["latitude"]); lon = float(cfg["longitude"])
    tz = ZoneInfo(cfg["timezone"]); site = cfg.get("site_name", "My Site")
    dark_dep = float(cfg.get("dark_depression", 18.0))
    min_alt = float(cfg.get("min_altitude", 30.0))
    step = int(cfg.get("step_minutes", 15))
    fov = {"w": float(cfg.get("fov_width_deg", 2.0)), "h": float(cfg.get("fov_height_deg", 1.33))}
    selftest(lat, lon)

    horizon = load_horizon()
    targets = load_targets()
    ras = np.array([t[2] for t in targets]); decs = np.array([t[3] for t in targets])

    hours = np.zeros((len(targets), 365)); darkhours = np.zeros(365)
    n = int(24 * 60 / step)
    for doy in range(365):
        noon = datetime(YEAR, 1, 1, 12, tzinfo=tz) + timedelta(days=doy)
        times = [noon + timedelta(minutes=step * i) for i in range(n)]
        jds = np.array([jd_of(t.astimezone(ZoneInfo("UTC"))) for t in times])
        lst = (np.array([gmst_deg(j) for j in jds]) + lon) % 360
        sun = np.array([sun_radec(j) for j in jds])
        salt, _ = altaz(sun[:, 0], sun[:, 1], lst, lat)
        dark = salt < -dark_dep
        darkhours[doy] = dark.sum() * step / 60.0
        if not dark.any():
            continue
        H = np.radians(lst[dark][None, :] - ras[:, None])
        d = np.radians(decs)[:, None]; L = math.radians(lat)
        alt = np.degrees(np.arcsin(np.cos(H)*np.cos(d)*math.cos(L) + np.sin(d)*math.sin(L)))
        e = -np.sin(H)*np.cos(d); nn = np.sin(d)*math.cos(L) - np.cos(H)*np.cos(d)*math.sin(L)
        az = np.degrees(np.arctan2(e, nn)) % 360
        usable = (alt > horizon[np.round(az).astype(int) % 360]) & (alt > min_alt)
        hours[:, doy] = usable.sum(axis=1) * step / 60.0

    # Reference sun altitudes for the browser to self-check its ported astronomy.
    checks = []
    for cdt in (datetime(YEAR,3,20,12,tzinfo=ZoneInfo("UTC")),
                datetime(YEAR,6,21,22,tzinfo=ZoneInfo("UTC")),
                datetime(YEAR,12,21,12,tzinfo=ZoneInfo("UTC"))):
        jd = jd_of(cdt); ra, dec = sun_radec(jd)
        alt, _ = altaz(ra, dec, np.array([gmst_deg(jd) + lon]), lat)
        checks.append({"ms": int(cdt.timestamp()*1000), "sunAlt": round(float(alt[0]), 3)})

    data = {
        "site": {"name": site, "lat": lat, "lon": lon},
        "minAlt": min_alt,
        "darkDep": dark_dep,
        # Weather verdict thresholds — SkyGuard's method and defaults.
        "wx": {
            "models": [cfg.get("primary_model", "knmi_harmonie_arome_netherlands"),
                       cfg.get("secondary_model", "ecmwf_ifs025")],
            "lowMax": int(cfg.get("low_cloud_max", 20)),
            "midMax": int(cfg.get("mid_cloud_max", 50)),
            "highConfMax": int(cfg.get("high_cloud_confident_max", 60)),
            "highNote": int(cfg.get("high_cloud_note", 40)),
            "expStart": int(cfg.get("exposure_start_hour", 22)),
            # Per evening weekday, the morning hour the gear comes in (later after Fri/Sat).
            "expEndByDay": [int(cfg.get("exposure_end_hour_by_evening", {"fri": 9, "sat": 9})
                                .get(d, cfg.get("exposure_end_hour", 7))) for d in DAYS],
            "precipMm": float(cfg.get("precip_amount_max", 0.0)),
            "precipProb": int(cfg.get("precip_prob_max", 0)),
            "windWarn": int(cfg.get("wind_sustained_warn_kmh", 25)),
            "gustWarn": int(cfg.get("wind_gust_warn_kmh", 40)),
            "gustVeto": int(cfg.get("wind_gust_veto_kmh", 55)),
            "minHours": int(cfg.get("min_usable_hours", 2)),
            "minRun": int(cfg.get("min_continuous_run", 2)),
            "confMinRun": int(cfg.get("confident_min_run", 2)),
            "fogVis": int(cfg.get("fog_visibility_m", 1000)),
            "mistVis": int(cfg.get("mist_visibility_m", 20000)),
            "mistSpread": float(cfg.get("mist_spread_c", 1.5)),
            "aodMax": float(cfg.get("aod_haze_max", 0.3)),
            "dewSpread": float(cfg.get("dew_spread_c", 2.0)),
            "jetSeeing": int(cfg.get("jet_seeing_kmh", 150)),
            # Setup window: an hour clear and dark enough to polar align before bed (bed 24 = 00:00).
            "requireSetup": bool(cfg.get("require_setup_window", True)),
            "setupStart": int(cfg.get("setup_window_start_hour", 21)),
            "setupEndByDay": [int(cfg.get("setup_window_end_hour_by_evening", {"fri": 24, "sat": 24})
                                  .get(d, cfg.get("setup_window_end_hour", 22))) for d in DAYS],
            "setupMinClear": int(cfg.get("setup_min_clear_hours", 1)),
            "alignDep": float(cfg.get("align_depression", 10.0)),
            "worthIt": float(cfg.get("worth_it_hours", 2.0)),
            "mistDepth": float(cfg.get("mist_depth_km", 0.5)),
            "airmass": float(cfg.get("target_airmass", 1.5)),
            "clearVis": int(cfg.get("clear_visibility_m", 50000)),
            "mistStandin": int(cfg.get("mist_standin_vis_m", 10000)),
            "aodClean": float(cfg.get("aod_clean", 0.1)),
            "filters": FILTERS,
        },
        "icons": load_icons(),
        "fov": fov,
        "horizon": [round(float(h), 1) for h in horizon],
        "darkHours": [int(round(h * 10)) for h in darkhours],
        "check": checks,
        "targets": [
            {"id": t[0], "name": t[1], "type": t[4], "sub": t[5],
             "ra": round(t[2], 4), "dec": round(t[3], 4),
             "transit": round(90 - abs(lat - t[3])),
             "hours": [int(round(h * 10)) for h in hours[i]]}
            for i, t in enumerate(targets)
        ],
    }
    data["targets"].sort(key=lambda t: -max(t["hours"]))

    os.makedirs("dist", exist_ok=True)
    shutil.rmtree("dist/img", ignore_errors=True)
    hosted = set(open(opts.hosted).read().split()) if opts.hosted else set()
    data["img"] = fetch_images(targets, fov, hosted, opts.offline)
    json.dump(data, open("dist/data.json", "w"), separators=(",", ":"))
    html = open("template.html").read().replace(
        "__EYESUP_DATA__", json.dumps(data, separators=(",", ":")))
    open("dist/eyesup.html", "w").write(html)
    nb = sum(t["type"] == "NB" for t in data["targets"])
    print(f"Built dist/eyesup.html for {site} ({lat:.2f}, {lon:.2f}) — "
          f"{len(targets)} targets ({nb} NB / {len(targets)-nb} RGB), "
          f"previews for {len(data['img'])} ({len(os.listdir('dist/img'))} files in dist/img).")


if __name__ == "__main__":
    main()
