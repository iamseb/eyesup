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
import csv, json, math, sys, tomllib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np

YEAR = 2026  # non-leap reference year; results are keyed by day-of-year


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
    cfg = load_config()
    lat = float(cfg["latitude"]); lon = float(cfg["longitude"])
    tz = ZoneInfo(cfg["timezone"]); site = cfg.get("site_name", "My Site")
    dark_dep = float(cfg.get("dark_depression", 12.0))
    min_alt = float(cfg.get("min_altitude", 30.0))
    step = int(cfg.get("step_minutes", 15))
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
        "fov": {"w": float(cfg.get("fov_width_deg", 2.0)),
                "h": float(cfg.get("fov_height_deg", 1.33))},
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

    json.dump(data, open("dist/data.json", "w"), separators=(",", ":"))
    html = open("template.html").read().replace(
        "__EYESUP_DATA__", json.dumps(data, separators=(",", ":")))
    open("dist/eyesup.html", "w").write(html)
    nb = sum(t["type"] == "NB" for t in data["targets"])
    print(f"Built dist/eyesup.html for {site} ({lat:.2f}, {lon:.2f}) — "
          f"{len(targets)} targets ({nb} NB / {len(targets)-nb} RGB).")


if __name__ == "__main__":
    main()
