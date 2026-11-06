"""Read CSV / TXT / TSV / XLSX / XLS files as text, detecting encoding, delimiter and header."""

import codecs
import csv
import datetime as dt
import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from charset_normalizer import from_bytes

TEXT_EXTS = {".csv", ".txt", ".tsv"}
EXCEL_EXTS = {".xlsx", ".xls"}
SUPPORTED_EXTS = TEXT_EXTS | EXCEL_EXTS
DELIMITERS = [",", ";", "\t", "|"]
SAMPLE_BYTES = 1 << 20
PREVIEW_ROWS = 20
_NUMERIC = re.compile(r"^[\s\d.,\-+/]+$")


class ParseError(Exception):
    pass


@dataclass
class ParseOptions:
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    has_header: bool | None = None


@dataclass
class ParsedFile:
    format: str  # csv | excel
    columns: list[str]
    rows: list[list[str]]  # data rows only, every value a string, padded to len(columns)
    encoding: str | None = None
    delimiter: str | None = None
    sheets: list[str] = field(default_factory=list)
    sheet: str | None = None
    has_header: bool = True
    empty_rows_skipped: int = 0

    def meta(self) -> dict:
        d = asdict(self)
        d.pop("rows")
        d["row_count"] = len(self.rows)
        return d


def detect_encoding(data: bytes) -> str:
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    try:
        data[:SAMPLE_BYTES].decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        # A multi-byte character may be cut off at the sample boundary.
        if e.start >= min(len(data), SAMPLE_BYTES) - 4:
            return "utf-8"
    sample = data[:SAMPLE_BYTES]
    best = from_bytes(sample).best()
    enc = (best.encoding if best else "cp1252").lower()
    # Short samples are often ambiguous between single-byte code pages; prefer Windows-1252 (the
    # usual Western Excel export) whenever it decodes the sample to the same text.
    try:
        if enc == "cp1252" or sample.decode("cp1252") == sample.decode(enc):
            return "cp1252"
    except (UnicodeDecodeError, LookupError):
        pass
    return enc


def detect_delimiter(sample: str) -> str:
    lines = [ln for ln in sample.splitlines()[:50] if ln.strip()]
    if not lines:
        return ","
    try:
        return csv.Sniffer().sniff("\n".join(lines), delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        pass
    best, best_score = ",", -1.0
    for d in DELIMITERS:
        counts = [ln.count(d) for ln in lines]
        if not counts[0]:
            continue
        consistent = sum(1 for c in counts if c == counts[0]) / len(counts)
        score = consistent * counts[0]
        if score > best_score:
            best, best_score = d, score
    return best


def detect_header(rows: list[list[str]]) -> bool:
    if not rows:
        return False
    from app.ingest.mapping import header_field

    first = rows[0]
    if sum(1 for v in first if header_field(v)) >= 1:
        return True
    if any(_NUMERIC.match(v) for v in first if v.strip()):
        return False
    body = rows[1:21]
    if not body:
        return True
    # A column that is numeric in the body but not in the first row indicates a header.
    for i, v in enumerate(first):
        col = [r[i] for r in body if i < len(r) and r[i].strip()]
        if col and v.strip() and sum(1 for x in col if _NUMERIC.match(x)) / len(col) > 0.7:
            return True
    return len(set(first)) == len(first) and all(v.strip() for v in first)


def _cell_text(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


def _finish(raw_rows: list[list[str]], has_header: bool | None, **meta) -> ParsedFile:
    skipped = 0
    rows = []
    for r in raw_rows:
        if any(v.strip() for v in r):
            rows.append(r)
        else:
            skipped += 1
    header = detect_header(rows) if has_header is None else has_header
    width = max((len(r) for r in rows), default=0)
    if header and rows:
        columns = [c.strip() or f"column_{i + 1}" for i, c in enumerate(rows[0])]
        rows = rows[1:]
    else:
        columns = []
    columns += [f"column_{i + 1}" for i in range(len(columns), width)]
    # de-duplicate header names
    seen: dict[str, int] = {}
    for i, c in enumerate(columns):
        if c in seen:
            seen[c] += 1
            columns[i] = f"{c}_{seen[c]}"
        else:
            seen[c] = 1
    rows = [r + [""] * (len(columns) - len(r)) for r in rows]
    return ParsedFile(columns=columns, rows=rows, has_header=header, empty_rows_skipped=skipped, **meta)


def parse_text(data: bytes, opts: ParseOptions) -> ParsedFile:
    encoding = opts.encoding or detect_encoding(data)
    try:
        text = data.decode(encoding)
    except (UnicodeDecodeError, LookupError) as e:
        raise ParseError(f"the file could not be decoded as {encoding}") from e
    delimiter = opts.delimiter or detect_delimiter(text[:SAMPLE_BYTES])
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        raw = [[v for v in row] for row in reader]
    except csv.Error as e:
        raise ParseError(f"the file could not be read as delimited text: {e}") from e
    return _finish(raw, opts.has_header, format="csv", encoding=encoding, delimiter=delimiter)


def _excel_rows_xlsx(data: bytes, sheet: str | None) -> tuple[list[str], str, list[list[str]]]:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - openpyxl raises many types for corrupt files
        raise ParseError("the file could not be read as Excel") from e
    names = wb.sheetnames
    chosen = sheet if sheet in names else None
    sheets_rows: dict[str, list[list[str]]] = {}
    for name in [chosen] if chosen else names:
        ws = wb[name]
        rows = [[_cell_text(v) for v in r] for r in ws.iter_rows(values_only=True)]
        sheets_rows[name] = rows
        if chosen or any(any(v.strip() for v in r) for r in rows):
            chosen = name
            break
    wb.close()
    chosen = chosen or names[0]
    return names, chosen, sheets_rows.get(chosen, [])


def _excel_rows_xls(data: bytes, sheet: str | None) -> tuple[list[str], str, list[list[str]]]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as e:  # noqa: BLE001
        raise ParseError("the file could not be read as Excel") from e
    names = book.sheet_names()
    order = [sheet] if sheet in names else names
    for name in order:
        sh = book.sheet_by_name(name)
        rows = []
        for i in range(sh.nrows):
            row = []
            for c in sh.row(i):
                if c.ctype == xlrd.XL_CELL_DATE:
                    row.append(_cell_text(xlrd.xldate.xldate_as_datetime(c.value, book.datemode)))
                else:
                    row.append(_cell_text(c.value))
            rows.append(row)
        if sheet in names or any(any(v.strip() for v in r) for r in rows):
            return names, name, rows
    return names, names[0], []


def parse_excel(data: bytes, ext: str, opts: ParseOptions) -> ParsedFile:
    names, chosen, rows = (_excel_rows_xlsx if ext == ".xlsx" else _excel_rows_xls)(data, opts.sheet)
    return _finish(rows, opts.has_header, format="excel", sheets=names, sheet=chosen)


def parse_file(path: Path, opts: ParseOptions | None = None) -> ParsedFile:
    opts = opts or ParseOptions()
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise ParseError(f"unsupported file type {ext}")
    data = path.read_bytes()
    if ext in EXCEL_EXTS:
        return parse_excel(data, ext, opts)
    return parse_text(data, opts)
