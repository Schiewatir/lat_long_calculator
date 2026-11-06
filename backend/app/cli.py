"""Operator commands:  python -m app.cli <command> --help"""

import argparse
import asyncio
import getpass
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_sessionmaker, raw_connection


async def create_admin(username: str, password: str | None) -> int:
    from app.auth.security import hash_password, password_policy_error
    from app.models import User

    password = password or getpass.getpass("Password for the new admin: ")
    if error := password_policy_error(password):
        print(error, file=sys.stderr)
        return 2
    async with get_sessionmaker()() as s:
        if await s.scalar(select(func.count()).where(User.role == "admin")):
            print("An admin already exists; use the web UI to manage users.", file=sys.stderr)
            return 1
        if await s.scalar(select(User.id).where(func.lower(User.username) == username.lower())):
            print(f"Username {username!r} already exists.", file=sys.stderr)
            return 1
        s.add(User(username=username, password_hash=hash_password(password), role="admin"))
        await s.commit()
    print(f"Admin {username!r} created.")
    return 0


async def import_geonames_cmd(source_dir: Path | None, download: bool, full: bool) -> int:
    from app.gazetteer.importer import download as do_download
    from app.gazetteer.importer import import_geonames

    source = source_dir or get_settings().geonames_dir
    if download:
        do_download(source, full)
    async with get_sessionmaker()() as s:
        conn = await raw_connection(s)
        meta = await import_geonames(conn, source)
        await s.commit()
    print("Gazetteer imported:", ", ".join(f"{k}={v}" for k, v in meta.items() if k != "postal_country_list"))
    return 0


async def load_demo_gazetteer() -> int:
    """Import the small built-in demo gazetteer (for development without GeoNames access)."""
    import tempfile

    from app.gazetteer.demo import DEMO_PLACES, DEMO_POSTAL
    from app.gazetteer.importer import write_geonames_files

    with tempfile.TemporaryDirectory() as tmp:
        write_geonames_files(Path(tmp), DEMO_POSTAL, DEMO_PLACES)
        return await import_geonames_cmd(Path(tmp), False, False)


async def calibrate_cmd(path: Path) -> int:
    from app.scoring.calibrate import calibrate_file

    print(await calibrate_file(path))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-admin", help="create the first admin account")
    p.add_argument("username")
    p.add_argument("--password", help="omit to be prompted")
    p = sub.add_parser("import-geonames", help="import GeoNames postal codes and places")
    p.add_argument("--source-dir", type=Path, help="directory with allCountries.zip and cities500.zip")
    p.add_argument("--download", action="store_true", help="download the files from geonames.org first")
    p.add_argument("--full", action="store_true", help="also download full GB/NL/CA postcodes")
    sub.add_parser("load-demo-gazetteer", help="import a tiny demo gazetteer (development only)")
    p = sub.add_parser("calibrate", help="per-band accuracy report for a hand-labeled CSV")
    p.add_argument("csv", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "create-admin":
        return asyncio.run(create_admin(args.username, args.password))
    if args.cmd == "import-geonames":
        return asyncio.run(import_geonames_cmd(args.source_dir, args.download, args.full))
    if args.cmd == "load-demo-gazetteer":
        return asyncio.run(load_demo_gazetteer())
    if args.cmd == "calibrate":
        return asyncio.run(calibrate_cmd(args.csv))
    return 1


if __name__ == "__main__":
    sys.exit(main())
