# EyesUp

An offline observing planner for deep-sky astrophotography. Point it at **your**
location and **your** local horizon, and it builds a self-contained web app that
opens on today's date and tells you which targets are actually usable tonight —
split into **Narrowband** and **RGB** — plus a whole-year heatmap of when each
target is best.

Because it uses *your measured horizon*, "usable" means usable from your garden
(behind your trees and rooflines), not from an idealised flat horizon.

## The idea: your data stays yours

- **Public (in this repo):** the code and the shared deep-sky catalogue
  (`targets.csv`). Anyone can clone it and build their own calendar.
- **Private (never committed):** your `config.toml` (your location) and
  `horizon.csv` (your measured horizon). Both are git-ignored.
- **Generated (never committed):** the finished app in `dist/`.

So the calculations are shared; your location and results are not.

## Quick start

```bash
pip install -r requirements.txt          # just NumPy (Python 3.11+)

cp config.example.toml config.toml       # then edit: latitude, longitude, timezone
cp horizon.example.csv horizon.csv       # then replace with your own measurements

python build.py                          # writes dist/eyesup.html
```

Open **`dist/eyesup.html`** in any browser (it works fully offline — bookmark it).

## Measuring your horizon

`horizon.csv` is just `azimuth,altitude` pairs in degrees (true azimuth, 0 = N,
90 = E …). You can give as few or as many points as you like — EyesUp
interpolates around the full circle.

A reliable way to measure it: stand where your mount will be, at mount height,
and use a phone **theodolite/clinometer app** (e.g. Theodolite) to read the
azimuth and elevation of the top of each obstruction — every ~10°, plus extra
readings at peaks, corners and gaps. Set the app to **true north**. Elevations
from the phone are accurate; azimuths drift, so it helps to sight the **Sun** at
a known time and correct any compass offset.

```csv
azimuth,altitude
0,56
30,48
90,30
180,27
250,11
330,60
```

## What "usable" means

For every target and every day of the year, EyesUp:
1. finds the hours of real darkness (Sun below `dark_depression`, default −12°);
2. tracks the target across those dark hours;
3. counts the time it is **above your horizon** *and* above `min_altitude`
   (default 30°).

That count — usable dark hours — drives tonight's ranking and the year heatmap.
Targets your latitude or horizon never lift high enough simply never appear.

The Moon is computed live in the browser: narrowband targets ignore it; RGB
(broadband) targets are flagged when the Moon is bright.

## Files

| File | Committed? | What |
|---|---|---|
| `build.py` | ✓ | builds the app from config + horizon + catalogue |
| `template.html` | ✓ | the app (HTML/CSS/JS), data injected at build time |
| `targets.csv` | ✓ | shared catalogue (Messier + popular narrowband/NGC) |
| `fetch_catalog.py` | ✓ | regenerate/extend `targets.csv` from SIMBAD |
| `config.example.toml`, `horizon.example.csv` | ✓ | templates to copy |
| `config.toml`, `horizon.csv` | ✗ (ignored) | **your** private inputs |
| `dist/` | ✗ (ignored) | generated app |

## Extending the catalogue

Edit `targets.csv` directly (`id,name,ra,dec,type,sub`; `type` is `NB` or `RGB`),
or re-run `python fetch_catalog.py` to rebuild it from SIMBAD and add more objects.

## Credits

Object coordinates and types from [SIMBAD](https://simbad.u-strasbg.fr/) (CDS,
Strasbourg). Astronomy computed in NumPy; no online services needed to build or
run the app.
