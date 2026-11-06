"""End-to-end: upload -> map -> process (fake AI, mocked OpenCage) -> review -> bulk -> export."""

import asyncio
import csv
import io
import json

import httpx
import pytest
import respx
from sqlalchemy import select

from app.config import get_settings
from app.geocode.providers import OPENCAGE_URL, OpenCageProvider
from app.jobs.context import build_context
from app.jobs.pipeline import run_job
from app.jobs.ratelimit import NoopLimiter
from app.models import Job
from tests.conftest import login, make_user
from tests.fakes import FakeAI, echo_repair

CSV = (
    "Customer,Street,PLZ,Ort,Land\n"
    "C1,,80331,Munchen,GE\n"
    "C2,,75001,Paris,FR\n"
    "C3,,1067,Dresden,DEU\n"
    "C4,,,Frankfurt,DE\n"
    "C5,,,???,\n"
    "C6,,75001,Paris,FR\n"
    "C7,Marienplatz 1,80331,München,DE\n"
    'C8,"=HYPERLINK(""x"")",10115,Berlin,DE\n'
    "C9,,6031,Frnakfurt a.M.,DEU.\n"
)


def opencage_responder(request: httpx.Request) -> httpx.Response:
    q = request.url.params["q"]
    if "Marienplatz" in q:
        return httpx.Response(200, json={"results": [{"geometry": {"lat": 48.13743, "lng": 11.57549}, "confidence": 10,
                                                      "formatted": "Marienplatz 1, 80331 München, Germany",
                                                      "components": {"_type": "building", "house_number": "1",
                                                                     "road": "Marienplatz", "postcode": "80331",
                                                                     "city": "München", "country_code": "de"}}]})  # fmt: skip
    return httpx.Response(200, json={"results": []})


@pytest.fixture
def ai_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "openai_api_key", "test-key")
    yield


def make_ctx(ai=None, provider=None):
    return build_context(ai=ai, provider=provider or OpenCageProvider("k"), geo_limiter=NoopLimiter(),
                         ai_limiter=NoopLimiter(), retry_base_delay=0)  # fmt: skip


def fake_ai():
    return FakeAI(echo_repair({"Frnakfurt a.M.": {"city": "Frankfurt am Main", "postcode": "60311", "country": "DE",
                                                   "reason": "typo; postcode digit missing"}}))  # fmt: skip


async def upload(client, content: str = CSV, name: str = "customers.csv"):
    r = await client.post("/api/jobs/upload", files={"file": (name, content.encode(), "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()


async def start(client, job_id, mapping, **kw):
    r = await client.post(f"/api/jobs/{job_id}/start", json={"name": "Customers", "mapping": mapping, **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def process(job_id, ctx):
    with respx.mock(assert_all_called=False) as mock:
        mock.get(OPENCAGE_URL).mock(side_effect=opencage_responder)
        return await run_job(job_id, ctx)


async def all_rows(client, job_id, **filt):
    r = await client.post(f"/api/jobs/{job_id}/rows/query", json={"filter": filt, "limit": 1000})
    assert r.status_code == 200, r.text
    return {row["in_city"] + "|" + row["in_postcode"]: row for row in r.json()["rows"]}, r.json()


@pytest.fixture
async def processed(client, db, ai_enabled):
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client)
    job_id = up["job"]["id"]
    m = up["preview"]["mapping"]
    assert (m["street"], m["postcode"], m["city"], m["country"]) == ("Street", "PLZ", "Ort", "Land")
    started = await start(client, job_id, m, ai_repair=True)
    assert started["status"] == "queued" and client.queued == [job_id]
    ai = fake_ai()
    status = await process(job_id, make_ctx(ai=ai))
    assert status == "ready"
    return job_id, ai


async def test_pipeline_results(client, processed):
    job_id, ai = processed
    job = (await client.get(f"/api/jobs/{job_id}")).json()
    assert job["status"] == "ready"
    assert job["counts"]["total_rows"] == 9 and job["counts"]["unique_keys"] == 8
    assert job["counts"]["provider_calls"] == 2  # only the two street rows went to the provider
    rows, _ = await all_rows(client, job_id)

    munich = rows["Munchen|80331"]
    assert (munich["m_city"], munich["m_country"], munich["score"], munich["band"]) == ("München", "DE", 85, "Medium")
    assert "country_changed" in munich["flags"]

    paris = rows["Paris|75001"]
    assert paris["score"] == 100 and paris["band"] == "High" and paris["precision"] == "postcode"

    dresden = rows["Dresden|1067"]
    assert dresden["m_postcode"] == "01067" and dresden["score"] == 92 and "postcode_repaired" in dresden["flags"]

    frankfurt = rows["Frankfurt|"]
    assert frankfurt["precision"] == "city" and frankfurt["score"] == 55 and frankfurt["band"] == "Low"

    failed = rows["???|"]
    assert failed["band"] == "Failed" and failed["lat"] is None

    street = rows["München|80331"]
    assert street["precision"] == "rooftop" and street["score"] == 100 and street["source"] == "provider:opencage"

    berlin = rows["Berlin|10115"]
    assert berlin["precision"] == "postcode" and berlin["score"] == 85  # street given, provider found nothing

    repaired = rows["Frnakfurt a.M.|6031"]
    assert "ai_repaired" in repaired["flags"] and repaired["m_city"] == "Frankfurt am Main"
    assert repaired["m_postcode"] == "60311" and repaired["score"] < 100

    # Only unresolved rows went to the AI, and only address fields
    sent = ai.all_payload_text()
    assert "Frnakfurt" in sent and "Paris" not in sent and "C9" not in sent and "Customer" not in sent


async def test_detail_reasons_and_candidates(client, processed):
    job_id, _ = processed
    rows, _ = await all_rows(client, job_id)
    d = (await client.get(f"/api/jobs/{job_id}/rows/{rows['Munchen|80331']['id']}")).json()
    assert d["normalized"]["country"] == "DE" and d["normalized"]["country_method"] == "inferred"
    assert any(c["field"] == "country" and c["from"] == "GE" for c in d["corrections"])
    assert d["reasons"][0]["text"] == "country GE→DE inferred from postcode and city"
    assert d["raw"] == ["C1", "", "80331", "Munchen", "GE"]
    fr = (await client.get(f"/api/jobs/{job_id}/rows/{rows['Frankfurt|']['id']}")).json()
    assert {c["label"] for c in fr["candidates"]} == {"Frankfurt am Main", "Frankfurt (Oder)"}


async def test_filters_sorting_and_paging(client, processed):
    job_id, _ = processed
    _, res = await all_rows(client, job_id, bands=["Low", "Failed"])
    assert {r["band"] for r in res["rows"]} <= {"Low", "Failed"} and res["total"] == len(res["rows"])
    _, res = await all_rows(client, job_id, corrections=["ai_repaired"])
    assert res["total"] == 1
    _, res = await all_rows(client, job_id, q="dresd")
    assert res["total"] == 1
    r = await client.post(f"/api/jobs/{job_id}/rows/query", json={"sort": "score", "limit": 3})
    page1 = r.json()
    assert [x["score"] for x in page1["rows"]] == sorted(x["score"] for x in page1["rows"])
    assert page1["rows"][0]["band"] == "Failed"
    page2 = (await client.post(f"/api/jobs/{job_id}/rows/query",
                               json={"sort": "score", "limit": 3, "cursor": page1["next_cursor"]})).json()  # fmt: skip
    assert not {x["id"] for x in page1["rows"]} & {x["id"] for x in page2["rows"]}
    assert page2["rows"][0]["score"] >= page1["rows"][-1]["score"]


async def test_single_row_actions_and_history(client, processed, db):
    job_id, _ = processed
    rows, _ = await all_rows(client, job_id)
    paris = rows["Paris|75001"]
    r = await client.post(f"/api/jobs/{job_id}/rows/{paris['id']}/action", json={"action": "confirm", "version": 1})
    assert r.json()["status"] == "confirmed" and r.json()["reviewed_by"] == "alice"
    stale = await client.post(f"/api/jobs/{job_id}/rows/{paris['id']}/action", json={"action": "reject", "version": 1})
    assert stale.status_code == 409

    failed = rows["???|"]
    pin = await client.post(f"/api/jobs/{job_id}/rows/{failed['id']}/action",
                            json={"action": "manual_pin", "version": 1, "lat": 52.52, "lon": 13.405})  # fmt: skip
    body = pin.json()
    assert (body["status"], body["score"], body["band"], body["precision"], body["source"]) == \
        ("edited", 100, "High", "manual", "manual")  # fmt: skip
    detail = (await client.get(f"/api/jobs/{job_id}/rows/{failed['id']}")).json()
    assert detail["reasons"][0]["text"] == "manually placed by alice"
    assert detail["override"]["previous"]["score"] == 0
    assert [h["action"] for h in detail["history"]] == ["row_manual_pin"]

    fr = rows["Frankfurt|"]
    fd = (await client.get(f"/api/jobs/{job_id}/rows/{fr['id']}")).json()
    alt = next(c for c in fd["candidates"] if not c["selected"])
    picked = await client.post(f"/api/jobs/{job_id}/rows/{fr['id']}/action",
                               json={"action": "pick_candidate", "version": 1, "candidate_id": alt["id"]})  # fmt: skip
    assert picked.json()["status"] == "edited" and picked.json()["lat"] == alt["lat"]


async def test_edit_and_rerun(client, processed):
    job_id, _ = processed
    rows, _ = await all_rows(client, job_id)
    failed = rows["???|"]
    r = await client.post(f"/api/jobs/{job_id}/rows/{failed['id']}/action", json={
        "action": "rerun", "version": 1, "input": {"city": "Berlin", "postcode": "10115", "country": "DE"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "edited" and body["score"] == 100 and body["m_city"] == "Berlin"
    assert body["in_city"] == "Berlin"


async def test_bulk_confirm_high(client, processed):
    job_id, _ = processed
    filt = {"score_min": 90, "statuses": ["pending"]}
    count = (await client.post(f"/api/jobs/{job_id}/bulk/preview", json={"action": "confirm", "filter": filt})).json()
    assert count["count"] == 5  # Paris x2, Dresden, Marienplatz, ...
    wrong = await client.post(f"/api/jobs/{job_id}/bulk", json={"action": "confirm", "filter": filt, "expected_count": 1})
    assert wrong.status_code == 409 and wrong.json()["detail"]["count"] == 5
    ok = await client.post(f"/api/jobs/{job_id}/bulk", json={"action": "confirm", "filter": filt,
                                                             "expected_count": count["count"]})  # fmt: skip
    assert ok.json()["updated"] == 5
    _, res = await all_rows(client, job_id, statuses=["confirmed"])
    assert res["total"] == 5 and all(r["score"] >= 90 for r in res["rows"])


async def test_confirm_entire_job_skips_rejected(client, processed):
    job_id, _ = processed
    rows, _ = await all_rows(client, job_id)
    await client.post(f"/api/jobs/{job_id}/rows/{rows['???|']['id']}/action", json={"action": "reject", "version": 1})
    n = (await client.post(f"/api/jobs/{job_id}/bulk", json={"action": "confirm", "filter": {}})).json()["updated"]
    assert n == 8
    _, res = await all_rows(client, job_id, statuses=["rejected"])
    assert res["total"] == 1


async def test_export_confirmed_and_all(client, processed):
    job_id, _ = processed
    rows, _ = await all_rows(client, job_id)
    await client.post(f"/api/jobs/{job_id}/bulk", json={"action": "confirm", "filter": {"score_min": 90}})
    await client.post(f"/api/jobs/{job_id}/rows/{rows['???|']['id']}/action",
                      json={"action": "manual_pin", "version": 1, "lat": 1.5, "lon": 2.25})  # fmt: skip
    r = await client.get(f"/api/jobs/{job_id}/export", params={"scope": "confirmed"})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith('attachment; filename="Customers_confirmed_')
    raw = r.content
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    data = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    hdr = data[0]
    assert hdr[:5] == ["Customer", "Street", "PLZ", "Ort", "Land"]
    assert hdr[5:9] == ["lat", "lon", "confidence", "confidence_band"]
    body = {line[0]: dict(zip(hdr, line, strict=True)) for line in data[1:]}
    assert len(body) == 6  # 5 confirmed + 1 edited
    assert body["C5"]["lat"] == "1.500000" and body["C5"]["status"] == "edited"
    assert body["C3"]["PLZ"] == "1067" and body["C3"]["matched_postcode"] == "01067"
    assert "zero-padded" in body["C3"]["corrections"]
    all_rows_csv = (await client.get(f"/api/jobs/{job_id}/export", params={"scope": "all"})).content
    all_lines = list(csv.reader(io.StringIO(all_rows_csv.decode("utf-8-sig"))))
    assert len(all_lines) == 10
    c8 = dict(zip(hdr, next(line for line in all_lines if line[0] == "C8"), strict=True))
    assert c8["Street"] == "'=HYPERLINK(\"x\")" and c8["status"] == "pending"
    flt = json.dumps({"bands": ["Medium"]})
    cur = (await client.get(f"/api/jobs/{job_id}/export", params={"scope": "current_filter", "filter": flt})).content
    lines = list(csv.reader(io.StringIO(cur.decode("utf-8-sig"))))[1:]
    assert lines and all(line[8] == "Medium" for line in lines)


async def test_export_header_collision():
    from app.export.csv_export import header

    assert header(["lat", "city"])[2] == "geo_lat"


async def test_access_control(client, processed, db):
    job_id, _ = processed
    await make_user(db, "mallory")
    await make_user(db, "root", role="admin")
    client.cookies.clear()
    await login(client, "mallory")
    assert (await client.get(f"/api/jobs/{job_id}")).status_code == 404
    assert (await client.get(f"/api/jobs/{job_id}/export")).status_code == 404
    assert (await client.post(f"/api/jobs/{job_id}/rows/query", json={})).status_code == 404
    assert (await client.get("/api/jobs")).json() == []
    client.cookies.clear()
    await login(client, "root")
    jobs = (await client.get("/api/jobs")).json()
    assert len(jobs) == 1 and jobs[0]["owner"] == "alice"


async def test_map_points(client, processed):
    job_id, _ = processed
    r = (await client.post(f"/api/jobs/{job_id}/map", json={})).json()
    assert len(r["points"]) == 8 and not r["truncated"]  # all but the failed row


async def test_delete_job(client, processed, db):
    job_id, _ = processed
    assert (await client.delete(f"/api/jobs/{job_id}")).status_code == 204
    assert (await client.get(f"/api/jobs/{job_id}")).status_code == 404


async def test_ai_disabled_sends_nothing(client, db, ai_enabled):
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client)
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"], ai_repair=False)
    ai = fake_ai()
    assert await process(job_id, make_ctx(ai=ai)) == "ready"
    assert ai.requests == []
    rows, _ = await all_rows(client, job_id)
    assert rows["Frnakfurt a.M.|6031"]["band"] in ("Low", "Failed")


async def test_upload_validation(client, db, monkeypatch):
    await make_user(db, "alice")
    await login(client, "alice")
    r = await client.post("/api/jobs/upload", files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 415
    monkeypatch.setattr(get_settings(), "max_rows", 3)
    r = await client.post("/api/jobs/upload", files={"file": ("a.csv", CSV.encode(), "text/csv")})
    assert r.status_code == 422 and "9 data rows" in r.json()["detail"]


async def test_start_validation(client, db):
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client)
    job_id = up["job"]["id"]
    r = await client.post(f"/api/jobs/{job_id}/start", json={"name": "x", "mapping": {"street": "Street"}})
    assert r.status_code == 422 and r.json()["detail"]["problems"]
    r = await client.post(f"/api/jobs/{job_id}/preview", json={"delimiter": ";"})
    assert r.status_code == 200 and len(r.json()["preview"]["columns"]) == 1


async def test_default_country_and_preview_override(client, db):
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client, "city;plz\nDresden;1067\nMünchen;80331\n")
    job_id = up["job"]["id"]
    assert up["preview"]["file"]["delimiter"] == ";"
    await start(client, job_id, up["preview"]["mapping"], default_country="DE")
    assert await process(job_id, make_ctx()) == "ready"
    rows, _ = await all_rows(client, job_id)
    assert rows["Dresden|1067"]["m_country"] == "DE"
    assert rows["Dresden|1067"]["score"] == 93  # job default -2, postcode repaired -5


# ----------------------------------------------------------------------------- scale, dedup, resume


def big_csv(n_rows: int, n_unique: int) -> str:
    lines = ["street,plz,city,country"]
    for i in range(n_rows):
        k = i % n_unique
        lines.append(f"Teststraße {k},80331,München,DE")
    return "\n".join(lines) + "\n"


async def test_dedup_processes_each_unique_address_once(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "chunk_size", 25)
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client, big_csv(2000, 160))
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"])
    calls = 0

    def responder(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"results": [{"geometry": {"lat": 48.1372, "lng": 11.5755}, "confidence": 9,
                                                      "formatted": "x", "components": {"_type": "road"}}]})  # fmt: skip

    with respx.mock as mock:
        mock.get(OPENCAGE_URL).mock(side_effect=responder)
        assert await run_job(job_id, make_ctx()) == "ready"
    assert calls == 160
    job = (await client.get(f"/api/jobs/{job_id}")).json()
    assert job["counts"]["unique_keys"] == 160 and job["summary"]["bands"] == {"High": 2000}


async def test_worker_crash_resumes_from_last_chunk(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "chunk_size", 20)
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client, big_csv(200, 100))
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"])
    calls = 0

    def crashing(request):
        nonlocal calls
        calls += 1
        if calls == 45:
            raise asyncio.CancelledError()  # the worker process dies mid-chunk
        return httpx.Response(200, json={"results": [{"geometry": {"lat": 48.1, "lng": 11.5}, "confidence": 9,
                                                      "formatted": "x", "components": {"_type": "road"}}]})  # fmt: skip

    with respx.mock as mock:
        mock.get(OPENCAGE_URL).mock(side_effect=crashing)
        with pytest.raises(asyncio.CancelledError):
            await run_job(job_id, make_ctx())
    job = await db.scalar(select(Job).where(Job.id == job_id))
    await db.refresh(job)
    assert job.status == "geocoding" and 40 <= job.counts["geocoded"] < 100

    with respx.mock as mock:
        mock.get(OPENCAGE_URL).mock(side_effect=crashing)
        assert await run_job(job_id, make_ctx()) == "ready"
    # Completed chunks were not geocoded again; only the interrupted chunk is redone (served from cache).
    assert calls <= 100 + 1
    final = (await client.get(f"/api/jobs/{job_id}")).json()
    assert final["counts"]["geocoded"] == 100


async def test_quota_pauses_then_resume(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "chunk_size", 10)
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client, big_csv(30, 30))
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"])
    with respx.mock as mock:
        mock.get(OPENCAGE_URL).mock(return_value=httpx.Response(402))
        assert await run_job(job_id, make_ctx()) == "paused"
    job = (await client.get(f"/api/jobs/{job_id}")).json()
    assert job["message"] == "geocoder quota exhausted"
    r = await client.post(f"/api/jobs/{job_id}/resume")
    assert r.status_code == 200 and client.queued[-1] == job_id
    with respx.mock as mock:
        mock.get(OPENCAGE_URL).mock(return_value=httpx.Response(200, json={"results": []}))
        assert await run_job(job_id, make_ctx()) == "ready"


async def test_cancel_and_retry_errors(client, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "chunk_size", 10)
    await make_user(db, "alice")
    await login(client, "alice")
    up = await upload(client, big_csv(12, 12))
    job_id = up["job"]["id"]
    await start(client, job_id, up["preview"]["mapping"])
    with respx.mock as mock:
        mock.get(OPENCAGE_URL).mock(return_value=httpx.Response(500))
        assert await run_job(job_id, make_ctx()) == "ready"
    _, res = await all_rows(client, job_id, errors_only=True)
    assert res["total"] == 12
    r = await client.post(f"/api/jobs/{job_id}/retry-errors")
    assert r.status_code == 200 and r.json()["rows"] == 12
    with respx.mock as mock:
        mock.get(OPENCAGE_URL).mock(return_value=httpx.Response(200, json={"results": []}))
        assert await run_job(job_id, make_ctx()) == "ready"
    _, res = await all_rows(client, job_id, errors_only=True)
    assert res["total"] == 0

    up = await upload(client, big_csv(5, 5))
    job2 = up["job"]["id"]
    await start(client, job2, up["preview"]["mapping"])
    r = await client.post(f"/api/jobs/{job2}/cancel")
    assert r.json()["status"] == "cancelled"


async def test_rescore_keeps_review_status(client, db, processed):
    job_id, _ = processed
    await make_user(db, "root", role="admin")
    rows, _ = await all_rows(client, job_id)
    munich = rows["Munchen|80331"]
    await client.post(f"/api/jobs/{job_id}/rows/{munich['id']}/action", json={"action": "confirm", "version": 1})
    client.cookies.clear()
    await login(client, "root")
    r = await client.post("/api/admin/scoring-config", json={"config": {"penalties": {"country_inferred": 30}}})
    assert r.status_code == 201 and r.json()["active_version"] == 2
    assert (await client.post(f"/api/jobs/{job_id}/rescore")).status_code == 200
    rows, _ = await all_rows(client, job_id)
    assert rows["Munchen|80331"]["score"] == 70 and rows["Munchen|80331"]["status"] == "confirmed"
    assert (await client.get(f"/api/jobs/{job_id}")).json()["scoring_config_version"] == 2
