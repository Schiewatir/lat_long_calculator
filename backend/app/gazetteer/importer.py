"""Import GeoNames dumps into PostgreSQL with an atomic table swap.

Files (from https://download.geonames.org/export/):
  zip/allCountries.zip        postal codes (GB/NL/CA as outward/4-digit/FSA codes only)
  dump/cities500.zip          places with population >= 500, including all alternate names
  zip/GB_full.csv.zip, NL_full.csv.zip, CA_full.csv.zip   optional full postcodes for those countries

Pass a directory with pre-downloaded files when the server cannot reach geonames.org."""

import csv
import io
import logging
import re
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.gazetteer.base import NameRec, PostalRec, name_records_for_place, postal_name_records
from app.normalize.postcode import compact

log = logging.getLogger(__name__)

BASE_URL = "https://download.geonames.org/export"
FILES = {"allCountries.zip": "zip/allCountries.zip", "cities500.zip": "dump/cities500.zip"}
OPTIONAL_FULL = {"GB": "GB_full.csv.zip", "NL": "NL_full.csv.zip", "CA": "CA_full.csv.zip"}
PLACE_FEATURE_CLASSES = {"P"}
_LATIN = re.compile(r"^[\u0000-ɏḀ-ỿ\s'’.\-()]+$")
_JUNK = re.compile(r"(https?:|\d{3,}|^[A-Z0-9]{2,5}$)")

TABLES = {
    "geonames_postal": ["id", "country", "postcode", "pc_key", "place_name", "admin1", "admin2", "lat", "lon", "accuracy"],
    "geonames_place": ["geonameid", "name", "asciiname", "country", "feature_code", "admin1", "population", "lat", "lon"],
    "gazetteer_names": ["id", "country", "name_folded", "name", "geonameid", "kind", "lat", "lon", "population"],
}
INDEXES = {
    "geonames_postal": [("ix_postal_country_pckey", "(country, pc_key)"), ("ix_postal_pckey", "(pc_key)")],
    "geonames_place": [("ix_geonames_place_country", "(country)")],
    "gazetteer_names": [("ix_gnames_country_folded", "(country, name_folded)"), ("ix_gnames_folded", "(name_folded)")],
}
PRIMARY_KEYS = {"geonames_postal": "id", "geonames_place": "geonameid", "gazetteer_names": "id"}


def download(target_dir: Path, include_full: bool) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    wanted = dict(FILES)
    if include_full:
        wanted |= {name: f"zip/{name}" for name in OPTIONAL_FULL.values()}
    with httpx.Client(timeout=600, follow_redirects=True) as client:
        for name, path in wanted.items():
            dest = target_dir / name
            if dest.exists():
                continue
            log.info("downloading %s", path)
            with client.stream("GET", f"{BASE_URL}/{path}") as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)


def _zip_lines(path: Path, member_suffix: str) -> Iterator[list[str]]:
    with zipfile.ZipFile(path) as z:
        member = next(n for n in z.namelist() if n.endswith(member_suffix))
        with z.open(member) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8"):
                yield line.rstrip("\n").split("\t")


def _float(v: str) -> float | None:
    try:
        return float(v)
    except ValueError:
        return None


def read_postal(source: Path) -> list[PostalRec]:
    recs: list[PostalRec] = []
    full_countries = {c for c, f in OPTIONAL_FULL.items() if (source / f).exists()}
    files = [(source / "allCountries.zip", "allCountries.txt")]
    files += [(source / OPTIONAL_FULL[c], f"{c}_full.txt") for c in sorted(full_countries)]
    for path, member in files:
        if not path.exists():
            continue
        from_full = member != "allCountries.txt"
        for cols in _zip_lines(path, member):
            if len(cols) < 11:
                continue
            country = cols[0]
            if not from_full and country in full_countries:
                continue
            lat, lon = _float(cols[9]), _float(cols[10])
            if lat is None or lon is None or not cols[1]:
                continue
            recs.append(PostalRec(country, cols[1], cols[2], cols[3] or None, lat, lon))
    return recs


def _keep_alt(name: str) -> bool:
    return 2 <= len(name) <= 80 and bool(_LATIN.match(name)) and not _JUNK.search(name)


def read_places(source: Path) -> tuple[list[tuple], list[NameRec]]:
    places, names = [], []
    for cols in _zip_lines(source / "cities500.zip", "cities500.txt"):
        if len(cols) < 15 or cols[6] not in PLACE_FEATURE_CLASSES:
            continue
        gid, name, ascii_name, alts = int(cols[0]), cols[1], cols[2], cols[3]
        lat, lon = float(cols[4]), float(cols[5])
        country, pop = cols[8], int(cols[14] or 0)
        places.append((gid, name, ascii_name, country, cols[7], cols[10], pop, lat, lon))
        alt_list = [a for a in alts.split(",") if _keep_alt(a)] if alts else []
        names += name_records_for_place(country, gid, name, [ascii_name, *alt_list], lat, lon, pop)
    return places, names


async def _copy(conn, table: str, columns: list[str], records: list[tuple]) -> None:
    for i in range(0, len(records), 50_000):
        await conn.copy_records_to_table(table, records=records[i : i + 50_000], columns=columns)


async def import_geonames(conn, source: Path) -> dict:
    """Load files from `source` into staging tables and swap them in atomically.
    `conn` is a raw asyncpg connection."""
    if not (source / "allCountries.zip").exists() or not (source / "cities500.zip").exists():
        raise FileNotFoundError(f"allCountries.zip and cities500.zip are required in {source}")
    log.info("reading postal codes")
    postal = read_postal(source)
    log.info("reading places")
    places, names = read_places(source)
    names += postal_name_records(postal)
    log.info("postal=%d places=%d names=%d", len(postal), len(places), len(names))

    postal_rows = [
        (i + 1, p.country, p.postcode, compact(p.postcode), p.place_name, p.admin1, None, p.lat, p.lon, None)
        for i, p in enumerate(postal)
    ]
    name_rows = [
        (i + 1, n.country, n.folded, n.name, n.geonameid, n.kind, n.lat, n.lon, n.population) for i, n in enumerate(names)
    ]
    data = {"geonames_postal": postal_rows, "geonames_place": places, "gazetteer_names": name_rows}

    for table, cols in TABLES.items():
        await conn.execute(f"DROP TABLE IF EXISTS {table}_new")
        await conn.execute(f"CREATE TABLE {table}_new (LIKE {table} INCLUDING DEFAULTS EXCLUDING IDENTITY)")
        # Defaults referencing the live table's sequence must not survive the swap.
        await conn.execute(f"ALTER TABLE {table}_new ALTER COLUMN {PRIMARY_KEYS[table]} DROP DEFAULT")
        await _copy(conn, f"{table}_new", cols, data[table])
        await conn.execute(f"ALTER TABLE {table}_new ADD CONSTRAINT {table}_new_pkey PRIMARY KEY ({PRIMARY_KEYS[table]})")
        for name, expr in INDEXES[table]:
            await conn.execute(f"CREATE INDEX {name}_new ON {table}_new {expr}")
        await conn.execute(f"ANALYZE {table}_new")

    countries = sorted({p.country for p in postal})
    imported_at = datetime.now(UTC).isoformat(timespec="seconds")
    async with conn.transaction():
        for table in TABLES:
            await conn.execute(f"DROP TABLE {table}")
            await conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            await conn.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {table}_new_pkey TO {table}_pkey")
            for name, _ in INDEXES[table]:
                await conn.execute(f"ALTER INDEX {name}_new RENAME TO {name}")
        meta = {
            "imported_at": imported_at,
            "postal_countries": str(len(countries)),
            "postal_country_list": ",".join(countries),
            "postal_rows": str(len(postal_rows)),
            "places": str(len(places)),
            "names": str(len(name_rows)),
        }
        for k, v in meta.items():
            await conn.execute(
                "INSERT INTO gazetteer_meta(key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                k,
                v,
            )
    return meta


def write_geonames_files(target: Path, postal: list[tuple], places: list[tuple]) -> None:
    """Write GeoNames-format zip files (used by tests and the demo fixture loader)."""
    target.mkdir(parents=True, exist_ok=True)
    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", lineterminator="\n")
    for country, pc, place, admin1, lat, lon in postal:
        w.writerow([country, pc, place, admin1, "", "", "", "", "", lat, lon, "4"])
    with zipfile.ZipFile(target / "allCountries.zip", "w") as z:
        z.writestr("allCountries.txt", out.getvalue())
    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, escapechar="\\")
    for country, gid, name, alts, lat, lon, pop in places:
        w.writerow([gid, name, name, ",".join(alts), lat, lon, "P", "PPLA", country, "", "", "", "", "", pop, "", "",
                    "Europe/Berlin", "2024-01-01"])  # fmt: skip
    with zipfile.ZipFile(target / "cities500.zip", "w") as z:
        z.writestr("cities500.txt", out.getvalue())
