import io
from pathlib import Path

import openpyxl
import pytest

from app.ai.mapping import ai_suggest_mapping
from app.ingest.mapping import mapping_problems, split_full_address, suggest_mapping
from app.ingest.parsing import ParseError, ParseOptions, parse_file
from tests.fakes import FakeAI


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_csv_preview(tmp_path):
    p = write(tmp_path, "a.csv", b"city,postcode,country\nParis,75001,FR\nBerlin,10115,DE\n")
    f = parse_file(p)
    assert f.columns == ["city", "postcode", "country"] and f.rows[0] == ["Paris", "75001", "FR"]
    assert f.has_header and f.delimiter == ","


def test_semicolon_cp1252(tmp_path):
    data = "Ort;PLZ;Land\nMünchen;80331;DE\nKöln;50667;DE\n".encode("cp1252")
    f = parse_file(write(tmp_path, "a.csv", data))
    assert f.delimiter == ";" and f.encoding == "cp1252"
    assert f.rows[0][0] == "München"


def test_utf8_bom_and_utf16(tmp_path):
    f = parse_file(write(tmp_path, "a.csv", "﻿city,country\nMünchen,DE\n".encode()))
    assert f.columns == ["city", "country"] and f.rows[0][0] == "München"
    f = parse_file(write(tmp_path, "b.txt", "city\tcountry\nZürich\tCH\n".encode("utf-16")))
    assert f.delimiter == "\t" and f.rows[0] == ["Zürich", "CH"]


def test_delimiter_override(tmp_path):
    p = write(tmp_path, "a.csv", b"city;postcode\nParis;75001\n")
    f = parse_file(p, ParseOptions(delimiter=","))
    assert f.columns == ["city;postcode"]


def test_headerless_file(tmp_path):
    f = parse_file(write(tmp_path, "a.csv", b"Paris,75001,FR\nBerlin,10115,DE\nNice,06000,FR\n"))
    assert not f.has_header and f.columns == ["column_1", "column_2", "column_3"]
    assert f.rows[0] == ["Paris", "75001", "FR"]


def test_header_toggle(tmp_path):
    p = write(tmp_path, "a.csv", b"Paris,75001,FR\nBerlin,10115,DE\n")
    assert parse_file(p, ParseOptions(has_header=True)).columns == ["Paris", "75001", "FR"]


def test_leading_zero_preserved(tmp_path):
    f = parse_file(write(tmp_path, "a.csv", b"plz,ort\n01067,Dresden\n"))
    assert f.rows[0][0] == "01067"


def test_empty_rows_skipped(tmp_path):
    f = parse_file(write(tmp_path, "a.csv", b"city\nParis\n\n,\n  \nBerlin\n"))
    assert [r[0] for r in f.rows] == ["Paris", "Berlin"] and f.empty_rows_skipped == 3


def test_multi_sheet_xlsx_defaults_to_first_non_empty(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Info"
    ws = wb.create_sheet("Addresses")
    ws.append(["City", "Postcode", "Country"])
    ws.append(["Dresden", "01067", "DE"])
    ws.append(["Dresden", 1067, "DE"])
    buf = io.BytesIO()
    wb.save(buf)
    f = parse_file(write(tmp_path, "a.xlsx", buf.getvalue()))
    assert f.sheets == ["Info", "Addresses"] and f.sheet == "Addresses"
    assert f.rows == [["Dresden", "01067", "DE"], ["Dresden", "1067", "DE"]]
    f2 = parse_file(tmp_path / "a.xlsx", ParseOptions(sheet="Info"))
    assert f2.sheet == "Info" and f2.rows == []


def test_unsupported_and_corrupt(tmp_path):
    with pytest.raises(ParseError):
        parse_file(write(tmp_path, "a.pdf", b"%PDF"))
    with pytest.raises(ParseError, match="Excel"):
        parse_file(write(tmp_path, "a.xlsx", b"not a zip"))


def test_german_headers_mapping():
    m = suggest_mapping(["Straße", "PLZ", "Ort", "Land"], [])
    assert (m["street"], m["postcode"], m["city"], m["country"]) == ("Straße", "PLZ", "Ort", "Land")


def test_content_based_mapping():
    rows = [["Paris", "75001", "FR"], ["Berlin", "10115", "DE"], ["Nice", "06000", "FR"]]
    m = suggest_mapping(["A", "B", "C"], rows)
    assert m["postcode"] == "B" and m["country"] == "C"


async def test_ai_mapping_only_headers_and_five_rows():
    rows = [[f"x{i}", f"y{i}", "Customer Secret"] for i in range(20)]
    ai = FakeAI(lambda req: {"street": None, "house_number": None, "city": "A", "postcode": "B", "country": None,
                             "id": None, "full_address": None})  # fmt: skip
    m = await ai_suggest_mapping(ai, ["A", "B", "C"], rows)
    assert m["city"] == "A" and m["postcode"] == "B"
    import json

    sent = json.loads(ai.requests[0].user)
    assert len(sent["sample_rows"]) == 5


def test_mapping_validation():
    assert mapping_problems({"street": "S"}, ["S"], None)
    assert mapping_problems({"city": "C"}, ["C"], None) == ["map a country column or choose a default country for the job"]
    assert mapping_problems({"city": "C"}, ["C"], "DE") == []


def test_split_full_address():
    assert split_full_address("Marienplatz 1, 80331 München, Germany") == {
        "street": "Marienplatz 1", "house_number": "", "city": "München", "postcode": "80331", "country": "Germany"}
