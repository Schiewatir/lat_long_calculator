"""Calibration report: how close are the coordinates in each confidence band to the truth?

Input: CSV with address columns (street, house_number, city, postcode, country) and the true
coordinates (true_lat, true_lon). Runs the deterministic pipeline (no AI) with the configured
geocoder and reports, per band, the share of rows within 1 / 5 / 25 km."""

import csv
from pathlib import Path

from app.db import get_sessionmaker
from app.gazetteer.base import haversine_km
from app.geocode.router import DbGeocodeCache, geocode_normalized
from app.jobs.context import active_scoring_config, build_context
from app.normalize.pipeline import RawAddress, normalize
from app.scoring.engine import BANDS
from app.scoring.signals import score_candidate

THRESHOLDS_KM = (1, 5, 25)


def _col(row: dict, *names: str) -> str:
    for n in names:
        for k, v in row.items():
            if k and k.strip().lower() == n:
                return v or ""
    return ""


async def calibrate_rows(rows: list[dict], ctx, config: dict) -> dict[str, dict]:
    stats = {b: {"rows": 0, **{f"within_{t}km": 0 for t in THRESHOLDS_KM}} for b in BANDS}
    async with ctx.sessionmaker() as s:
        cache = DbGeocodeCache(s, ctx.settings.geocode_cache_ttl_days)
        for row in rows:
            raw = RawAddress(_col(row, "street"), _col(row, "house_number", "housenumber"), _col(row, "city"),
                             _col(row, "postcode", "zip", "plz"), _col(row, "country"))  # fmt: skip
            try:
                lat, lon = float(_col(row, "true_lat", "lat")), float(_col(row, "true_lon", "lon", "lng"))
            except ValueError:
                continue
            n = await normalize(raw, ctx.gazetteer)
            out = await geocode_normalized(n, ctx.provider, cache, ctx.geo_limiter)
            top = out.candidates[0] if out.candidates else None
            result = score_candidate(raw, n, top, [], config)
            b = stats[result.band]
            b["rows"] += 1
            if top is not None:
                d = haversine_km(top.lat, top.lon, lat, lon)
                for t in THRESHOLDS_KM:
                    b[f"within_{t}km"] += d <= t
        await s.commit()
    return stats


def format_report(stats: dict[str, dict]) -> str:
    lines = [f"{'band':<8}{'rows':>8}" + "".join(f"{f'<= {t} km':>12}" for t in THRESHOLDS_KM)]
    for band, s in stats.items():
        n = s["rows"]
        cells = "".join(f"{(s[f'within_{t}km'] / n * 100 if n else 0):>11.1f}%" for t in THRESHOLDS_KM)
        lines.append(f"{band:<8}{n:>8}{cells}")
    return "\n".join(lines)


async def calibrate_file(path: Path) -> str:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    ctx = build_context()
    await ctx.gazetteer.refresh_version()
    async with get_sessionmaker()() as s:
        _, config = await active_scoring_config(s)
    return format_report(await calibrate_rows(rows, ctx, config))
