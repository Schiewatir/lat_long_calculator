from sqlalchemy import select

from app.db import get_sessionmaker
from app.gazetteer.store import DbGazetteer
from app.models import GazetteerMeta, GeonamesPostal
from app.normalize.pipeline import RawAddress, normalize


async def test_import_populates_tables_and_meta(db):
    meta = {m.key: m.value for m in (await db.scalars(select(GazetteerMeta))).all()}
    assert meta["imported_at"] and "DE" in meta["postal_country_list"].split(",")
    row = await db.scalar(select(GeonamesPostal).where(GeonamesPostal.pc_key == "00001"))
    assert row.postcode == "00-001" and row.country == "PL"


async def test_db_gazetteer_matches_memory_behaviour(db):
    gaz = DbGazetteer(get_sessionmaker())
    await gaz.refresh_version()
    r = await normalize(RawAddress(city="Munchen", postcode="80331", country="GE"), gaz)
    assert r.country == "DE" and r.city == "München" and r.resolution == "resolved"
    r = await normalize(RawAddress(city="Frankfurt", country="DE"), gaz)
    assert r.city_match == "ambiguous"
    r = await normalize(RawAddress(city="Paris", postcode="75001"), gaz)
    assert r.country == "FR"
    r = await normalize(RawAddress(city="Tbilisi", postcode="0105", country="GE"), gaz)
    assert r.country == "GE" and r.postcode_found == "unverifiable"


async def test_reimport_is_atomic_swap(db):
    """Running the import twice keeps working tables and indexes."""
    import tempfile
    from pathlib import Path

    from app.db import raw_connection
    from app.gazetteer.importer import import_geonames, write_geonames_files
    from tests.fixtures import PLACES, POSTAL

    with tempfile.TemporaryDirectory() as tmp:
        write_geonames_files(Path(tmp), POSTAL[:3], PLACES[:3])
        async with get_sessionmaker()() as s:
            await import_geonames(await raw_connection(s), Path(tmp))
            await s.commit()
        assert len((await db.scalars(select(GeonamesPostal))).all()) == 3
        await db.rollback()  # release the read lock so the swap can drop the old table
        write_geonames_files(Path(tmp), POSTAL, PLACES)
        async with get_sessionmaker()() as s:
            await import_geonames(await raw_connection(s), Path(tmp))
            await s.commit()
    assert len((await db.scalars(select(GeonamesPostal))).all()) == len(POSTAL)
