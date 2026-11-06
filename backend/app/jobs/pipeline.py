"""Job pipeline: ingesting -> normalizing -> ai_repair -> geocoding (+scoring per chunk) -> ready.

Work happens on unique address keys in chunks; every chunk commits its results and the key's
`step`, so a restarted worker continues where the previous one stopped."""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import AIError
from app.ai.repair import (
    disambiguate,
    parse_repair_response,
    repair_addresses,
    repair_request,
    validate_repair,
)
from app.db import raw_connection
from app.geocode.base import GeoCandidate, ProviderError
from app.geocode.router import DbGeocodeCache, geocode_normalized
from app.ingest.mapping import split_full_address
from app.ingest.parsing import ParseError, ParseOptions, parse_file
from app.jobs.context import PipelineContext, active_scoring_config, scoring_config_version
from app.jobs.progress import StageTimer, band_counts, publish
from app.models import AddressKey, Candidate, Job
from app.normalize.pipeline import NormResult, RawAddress, city_query_variants, normalize
from app.normalize.postcode import compact
from app.normalize.text import clean
from app.scoring.signals import score_candidate

log = logging.getLogger(__name__)

FIELDS = ("street", "house_number", "city", "postcode", "country")
GEOCODE_CONCURRENCY = 8


class JobCancelled(Exception):
    pass


class JobPaused(Exception):
    pass


def address_key(raw: RawAddress, default_country: str | None) -> str:
    parts = [clean(getattr(raw, f)).lower() for f in FIELDS] + [default_country or ""]
    return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()


def raw_of(k: AddressKey) -> RawAddress:
    return RawAddress(k.in_street, k.in_house_number, k.in_city, k.in_postcode, k.in_country)


# ----------------------------------------------------------------------------- job state helpers


async def _set_status(session: AsyncSession, job: Job, status: str, message: str | None = None, **extra) -> None:
    job.status = status
    job.message = message
    await session.commit()
    await publish(job, extra)


async def _check_cancel(session: AsyncSession, job: Job) -> None:
    await session.refresh(job, ["cancel_requested"])
    if job.cancel_requested:
        raise JobCancelled()


async def _bump_counts(session: AsyncSession, job: Job, **values) -> None:
    job.counts = {**(job.counts or {}), **values}
    await session.commit()


# ----------------------------------------------------------------------------- stage 1: ingest


async def stage_ingest(session: AsyncSession, job: Job, ctx: PipelineContext) -> None:
    await session.execute(text("DELETE FROM rows WHERE job_id = :j"), {"j": job.id})
    await session.execute(text("DELETE FROM address_keys WHERE job_id = :j"), {"j": job.id})
    await session.commit()
    meta = job.file_meta
    opts = ParseOptions(meta.get("encoding"), meta.get("delimiter"), meta.get("sheet"), meta.get("has_header"))
    if not job.file_path or not Path(job.file_path).exists():
        raise ParseError("the uploaded file is no longer available")
    parsed = await asyncio.to_thread(parse_file, Path(job.file_path), opts)
    if len(parsed.rows) > ctx.settings.max_rows:
        raise ParseError(f"the file has {len(parsed.rows):,} rows; the limit is {ctx.settings.max_rows:,}")
    idx = {c: i for i, c in enumerate(parsed.columns)}
    mapping = job.mapping
    default_country = job.options.get("default_country")

    def mapped(row: list[str]) -> RawAddress:
        vals = {f: (row[idx[mapping[f]]] if mapping.get(f) in idx else "") for f in FIELDS}
        if mapping.get("full_address") in idx:
            split = split_full_address(row[idx[mapping["full_address"]]])
            vals = {f: vals[f] or split[f] for f in FIELDS}
        return RawAddress(**{f: clean(v) for f, v in vals.items()})

    keys: dict[str, tuple[int, RawAddress, int]] = {}  # hash -> (seq, raw, count)
    row_keys: list[tuple[RawAddress, str]] = []
    for row in parsed.rows:
        raw = mapped(row)
        h = address_key(raw, default_country)
        if h in keys:
            seq, r, n = keys[h]
            keys[h] = (seq, r, n + 1)
        else:
            keys[h] = (len(keys), raw, 1)
        row_keys.append((raw, h))

    conn = await raw_connection(session)
    ids = [r["id"] for r in await conn.fetch("SELECT nextval('address_keys_id_seq') AS id FROM generate_series(1, $1)", len(keys))]
    key_ids = {}
    key_records = []
    for (h, (seq, raw, n)), kid in zip(keys.items(), ids, strict=True):
        key_ids[h] = kid
        key_records.append((kid, job.id, h, seq, n, raw.street, raw.house_number, raw.city, raw.postcode, raw.country,
                            "{}", "[]", [], "[]", "new"))  # fmt: skip
    await conn.copy_records_to_table(
        "address_keys",
        records=key_records,
        columns=["id", "job_id", "key_hash", "seq", "row_count", "in_street", "in_house_number", "in_city",
                 "in_postcode", "in_country", "norm_state", "corrections", "flags", "reasons", "step"],
    )  # fmt: skip
    row_records = [
        (job.id, i + 1, json.dumps(parsed.rows[i], ensure_ascii=False), raw.street, raw.house_number, raw.city,
         raw.postcode, raw.country, key_ids[h], False, [], "pending", 1)
        for i, (raw, h) in enumerate(row_keys)
    ]  # fmt: skip
    for i in range(0, len(row_records), 20_000):
        await conn.copy_records_to_table(
            "rows",
            records=row_records[i : i + 20_000],
            columns=["job_id", "row_index", "raw", "in_street", "in_house_number", "in_city", "in_postcode",
                     "in_country", "address_key_id", "overridden", "flags", "status", "version"],
        )  # fmt: skip
    job.file_meta = {**meta, "columns": parsed.columns}
    await _bump_counts(session, job, total_rows=len(parsed.rows), unique_keys=len(keys),
                       empty_rows_skipped=parsed.empty_rows_skipped, normalized=0, ai_sent=0, ai_repaired=0,
                       ai_rejected=0, geocoded=0, provider_calls=0, cache_hits=0, errors=0)  # fmt: skip


# ----------------------------------------------------------------------------- stage 2: normalize


def derived_flags(n: NormResult) -> list[str]:
    """Correction-type flags used by grid filters."""
    out = []
    if n.country_method in ("alias", "fuzzy", "inferred", "job_default"):
        out.append("country_changed")
    if n.postcode_status == "repaired":
        out.append("postcode_repaired")
    if n.city_match == "fuzzy":
        out.append("city_fuzzy")
    if n.fields_fixed:
        out.append("fields_fixed")
    return out


def _store_norm(k: AddressKey, n: NormResult) -> dict:
    return {
        "id": k.id,
        "n_street": n.street,
        "n_house_number": n.house_number,
        "n_city": n.city,
        "n_postcode": n.postcode,
        "n_country": n.country,
        "resolution": n.resolution,
        "norm_state": n.to_dict(),
        "corrections": [c.to_dict() for c in n.corrections],
        "flags": list(dict.fromkeys(n.flags + derived_flags(n) + [f for f in k.flags if f.startswith("ai_")])),
    }


async def _prefetch(ctx: PipelineContext, raws: list[RawAddress]) -> None:
    pc_keys, names = set(), set()
    for r in raws:
        if r.postcode:
            c = compact(r.postcode.removesuffix(".0"))
            pc_keys |= {c, "0" + c, "00" + c}
        for v in (r.city, r.postcode):
            if v:
                names |= set(city_query_variants(clean(v), None))
    await ctx.gazetteer.prefetch(pc_keys, names)


async def _chunks(session: AsyncSession, job: Job, steps: tuple[str, ...], size: int, extra=None):
    """Yield chunks of keys in `steps`, re-querying after each chunk (results move keys forward)."""
    while True:
        await _check_cancel(session, job)
        q = select(AddressKey).where(AddressKey.job_id == job.id, AddressKey.step.in_(steps))
        if extra is not None:
            q = q.where(extra)
        keys = list((await session.scalars(q.order_by(AddressKey.seq).limit(size))).all())
        if not keys:
            return
        yield keys


async def stage_normalize(session: AsyncSession, job: Job, ctx: PipelineContext) -> None:
    default_country = job.options.get("default_country")
    total = job.counts.get("unique_keys", 0)
    done = await session.scalar(select(func.count()).where(AddressKey.job_id == job.id, AddressKey.step != "new")) or 0
    timer = StageTimer(total, done)
    async for keys in _chunks(session, job, ("new",), ctx.settings.chunk_size):
        raws = [raw_of(k) for k in keys]
        await _prefetch(ctx, raws)
        updates = []
        for k, raw in zip(keys, raws, strict=True):
            n = await normalize(raw, ctx.gazetteer, default_country)
            updates.append({**_store_norm(k, n), "step": "normalized"})
        await session.execute(update(AddressKey), updates)
        done += len(keys)
        await _bump_counts(session, job, normalized=done)
        await publish(job, {"stage_done": done, "stage_total": total, "eta_seconds": timer.eta(done)})


# ----------------------------------------------------------------------------- stage 3: AI repair


async def _apply_ai(session: AsyncSession, job: Job, ctx: PipelineContext, keys: list[AddressKey], reps: dict) -> tuple[int, int]:
    default_country = job.options.get("default_country")
    ok = rejected = 0
    updates = []
    for k in keys:
        rep = reps.get(str(k.id))
        if rep is None:
            updates.append({"id": k.id, "step": "ai_done", "error": k.error or "ai_error",
                            "flags": list(dict.fromkeys([*k.flags, "ai_error"]))})  # fmt: skip
            continue
        n, reason = await validate_repair(raw_of(k), rep, ctx.gazetteer, default_country)
        if n is None:
            rejected += 1
            updates.append({"id": k.id, "step": "ai_done", "ai_reason": reason,
                            "flags": list(dict.fromkeys([*k.flags, "ai_rejected"]))})  # fmt: skip
        else:
            ok += 1
            updates.append({**_store_norm(k, n), "step": "ai_done", "ai_reason": reason,
                            "flags": list(dict.fromkeys(n.flags + derived_flags(n) + ["ai_repaired"]))})  # fmt: skip
    if updates:
        await session.execute(update(AddressKey), updates)
    return ok, rejected


def _needs_ai():
    return AddressKey.resolution == "unresolved"


async def stage_ai_repair(session: AsyncSession, job: Job, ctx: PipelineContext) -> bool:
    """Returns False when the job must wait for an OpenAI batch (resumed by the batch poller)."""
    use_ai = job.options.get("ai_repair") and ctx.ai is not None
    pending = await session.scalar(
        select(func.count()).where(AddressKey.job_id == job.id, AddressKey.step == "normalized", _needs_ai())
    ) or 0
    if use_ai and pending:
        assert ctx.ai is not None
        per_req = ctx.settings.ai_addresses_per_request
        use_batch = ctx.settings.ai_batch_supported and pending > ctx.settings.ai_batch_threshold
        if job.ai_batch_id is not None or use_batch:
            await session.execute(
                update(AddressKey).where(AddressKey.job_id == job.id, AddressKey.step == "normalized",
                                         AddressKey.resolution != "unresolved").values(step="ai_done")
            )  # fmt: skip
            await session.commit()
        if job.ai_batch_id is None and use_batch:
            keys = list((await session.scalars(select(AddressKey).where(
                AddressKey.job_id == job.id, AddressKey.step == "normalized", _needs_ai()).order_by(AddressKey.seq))).all())
            reqs = [repair_request(f"g{i}", [(str(k.id), raw_of(k)) for k in keys[i : i + per_req]])
                    for i in range(0, len(keys), per_req)]  # fmt: skip
            job.ai_batch_id = await ctx.ai.submit_batch(reqs)
            await _bump_counts(session, job, ai_sent=len(keys))
            await _set_status(session, job, "ai_repair", f"waiting for AI batch ({len(keys):,} addresses)")
            return False
        if job.ai_batch_id is not None:
            status, results = await ctx.ai.batch_results(job.ai_batch_id)
            if results is None:
                return False
            reps: dict = {}
            for data in results.values():
                ids = {str(it.get("id")) for it in data.get("items", [])}
                reps |= parse_repair_response(data, ids)
            ok, rej = 0, 0
            async for keys in _chunks(session, job, ("normalized",), ctx.settings.chunk_size, _needs_ai()):
                a, b = await _apply_ai(session, job, ctx, keys, reps)
                ok, rej = ok + a, rej + b
                await session.commit()
            job.ai_batch_id = None
            await _bump_counts(session, job, ai_repaired=job.counts.get("ai_repaired", 0) + ok,
                               ai_rejected=job.counts.get("ai_rejected", 0) + rej)  # fmt: skip
            if status != "completed":
                job.message = f"AI batch ended with status {status}; affected rows were not repaired"
        else:
            concurrency = max(1, ctx.settings.openai_concurrency)
            sem = asyncio.Semaphore(concurrency)
            sent = 0
            async for keys in _chunks(session, job, ("normalized",), per_req * concurrency, _needs_ai()):
                groups = [keys[i : i + per_req] for i in range(0, len(keys), per_req)]

                async def run(group: list[AddressKey]) -> dict:
                    async with sem:
                        await ctx.ai_limiter.acquire()
                        try:
                            return await repair_addresses(ctx.ai, [(str(k.id), raw_of(k)) for k in group])  # type: ignore[arg-type]
                        except AIError as e:
                            log.warning("AI repair failed for job %s: %s", job.id, e)
                            return {}

                results = await asyncio.gather(*(run(g) for g in groups))
                reps = {k: v for r in results for k, v in r.items()}
                ok, rej = await _apply_ai(session, job, ctx, keys, reps)
                sent += len(keys)
                await _bump_counts(session, job, ai_sent=job.counts.get("ai_sent", 0) + len(keys),
                                   ai_repaired=job.counts.get("ai_repaired", 0) + ok,
                                   ai_rejected=job.counts.get("ai_rejected", 0) + rej)  # fmt: skip
                await publish(job, {"stage_done": sent, "stage_total": pending})
    await session.execute(
        update(AddressKey).where(AddressKey.job_id == job.id, AddressKey.step == "normalized").values(step="ai_done")
    )
    await session.commit()
    return True


# ----------------------------------------------------------------------------- stage 4: geocode + score


async def resolve_key(
    session: AsyncSession, job: Job, ctx: PipelineContext, k: AddressKey, config: dict
) -> tuple[dict, list[dict], int, int]:
    """Geocode, (optionally) AI-disambiguate and score one key.
    Returns (key update, candidate rows, provider calls, cache hits)."""
    n = NormResult.from_dict(k.norm_state) if k.norm_state else await normalize(raw_of(k), ctx.gazetteer)
    outcome = await geocode_normalized(
        n, ctx.provider, DbGeocodeCache(session, ctx.settings.geocode_cache_ttl_days), ctx.geo_limiter,
        mismatch_km=ctx.settings.postcode_mismatch_km, retry_base_delay=ctx.retry_base_delay,
    )  # fmt: skip
    cands = outcome.candidates
    flags = [f for f in k.flags if f != "ai_disambiguated"]
    ai_reason = k.ai_reason
    ambiguous = [c for c in cands if "ambiguous" in c.flags]
    if len(ambiguous) > 1 and job.options.get("ai_repair") and ctx.ai is not None:
        await ctx.ai_limiter.acquire()
        idx, reason = await disambiguate(ctx.ai, raw_of(k), ambiguous)
        if idx is not None:
            chosen = ambiguous[idx]
            cands = [chosen] + [c for c in cands if c is not chosen]
            flags.append("ai_disambiguated")
            ai_reason = reason
    raw = raw_of(k)
    scored = [(c, score_candidate(raw, n, c, flags, config)) for c in cands]
    top = scored[0] if scored else None
    top_score = top[1] if top else score_candidate(raw, n, None, flags, config)
    cand_rows = [
        {"address_key_id": k.id, "rank": i, "lat": c.lat, "lon": c.lon, "source": c.source, "precision": c.precision,
         "label": c.label, "components": c.components, "provider_confidence": c.provider_confidence,
         "flags": c.flags, "score": s.score, "band": s.band, "reasons": s.reasons}
        for i, (c, s) in enumerate(scored)
    ]  # fmt: skip
    best: GeoCandidate | None = top[0] if top else None
    comps = best.components if best else {}
    upd = {
        "id": k.id,
        "lat": best.lat if best else None,
        "lon": best.lon if best else None,
        "source": best.source if best else None,
        "precision": best.precision if best else "none",
        "m_street": _street(comps) or (n.street or None),
        "m_city": comps.get("city") or comps.get("town") or comps.get("village") or n.city,
        "m_postcode": comps.get("postcode") or n.postcode,
        "m_country": (str(comps.get("country_code") or "").upper() or n.country) if best else n.country,
        "score": top_score.score,
        "band": top_score.band,
        "reasons": top_score.reasons,
        "error": outcome.error or (k.error if k.error == "ai_error" else None),
        "flags": list(dict.fromkeys(flags + ([outcome.error] if outcome.error else []))),
        "ai_reason": ai_reason,
        "step": "scored",
    }
    return upd, cand_rows, outcome.provider_calls, outcome.cache_hits


def _street(comps: dict) -> str | None:
    road = comps.get("road") or comps.get("street")
    if not road:
        return None
    return f"{road} {comps['house_number']}" if comps.get("house_number") else str(road)


async def _write_results(session: AsyncSession, updates: list[dict], cand_rows: list[dict]) -> None:
    key_ids = [u["id"] for u in updates]
    await session.execute(text("DELETE FROM candidates WHERE address_key_id = ANY(:ids)"), {"ids": key_ids})
    selected: dict[int, int] = {}
    if cand_rows:
        from sqlalchemy import insert

        res = await session.execute(
            insert(Candidate).returning(Candidate.id, Candidate.address_key_id, Candidate.rank, sort_by_parameter_order=True),
            cand_rows,
        )
        for cid, kid, rank in res:
            if rank == 0:
                selected[kid] = cid
    for u in updates:
        u["selected_candidate_id"] = selected.get(u["id"])
    await session.execute(update(AddressKey), updates)
    await apply_keys_to_rows(session, key_ids)


async def apply_keys_to_rows(session: AsyncSession, key_ids: list[int]) -> None:
    """Copy key results onto rows that the user has not overridden."""
    await session.execute(
        text("""
        UPDATE rows r SET lat = k.lat, lon = k.lon, source = k.source, precision = k.precision,
               m_street = k.m_street, m_city = k.m_city, m_postcode = k.m_postcode, m_country = k.m_country,
               score = k.score, band = k.band, flags = k.flags, error = k.error
        FROM address_keys k
        WHERE r.address_key_id = k.id AND k.id = ANY(:ids) AND r.overridden = false
        """),
        {"ids": key_ids},
    )


def _error_result(k: AddressKey) -> tuple[dict, list[dict], int, int]:
    upd = {"id": k.id, "lat": None, "lon": None, "source": None, "precision": "none", "m_street": None,
           "m_city": k.n_city, "m_postcode": k.n_postcode, "m_country": k.n_country, "score": 0, "band": "Failed",
           "reasons": [{"code": "error", "penalty": 100, "text": "processing error; use 'retry errors'"}],
           "error": "geocode_error", "flags": list(dict.fromkeys([*k.flags, "geocode_error"])),
           "ai_reason": k.ai_reason, "step": "scored"}  # fmt: skip
    return upd, [], 0, 0


async def stage_geocode(session: AsyncSession, job: Job, ctx: PipelineContext) -> None:
    if job.scoring_config_version is None:
        job.scoring_config_version, config = await active_scoring_config(session)
        await session.commit()
    else:
        config = await scoring_config_version(session, job.scoring_config_version)
    total = job.counts.get("unique_keys", 0)
    done = await session.scalar(select(func.count()).where(AddressKey.job_id == job.id, AddressKey.step == "scored")) or 0
    timer = StageTimer(total, done)
    sem = asyncio.Semaphore(GEOCODE_CONCURRENCY)
    async for keys in _chunks(session, job, ("ai_done",), ctx.settings.chunk_size):
        quota_error: ProviderError | None = None

        async def run(k: AddressKey):
            nonlocal quota_error
            async with sem:
                if quota_error:
                    return None
                # Each concurrent lookup gets its own session for cache reads/writes.
                async with ctx.sessionmaker() as s2:
                    try:
                        result = await resolve_key(s2, job, ctx, k, config)
                        await s2.commit()
                        return result
                    except ProviderError as e:
                        if e.quota:
                            quota_error = e
                            return None
                        log.warning("geocoding failed for key %s: %s", k.id, e)
                        return _error_result(k)
                    except Exception:
                        # One bad address must not fail a 200k-row job; it can be retried later.
                        log.exception("unexpected error for key %s in job %s", k.id, job.id)
                        return _error_result(k)

        results = await asyncio.gather(*(run(k) for k in keys))
        finished = [r for r in results if r is not None]
        if finished:
            await _write_results(session, [r[0] for r in finished], [c for r in finished for c in r[1]])
            done += len(finished)
            calls = sum(r[2] for r in finished)
            hits = sum(r[3] for r in finished)
            errors = sum(1 for r in finished if r[0]["error"])
            await _bump_counts(session, job, geocoded=done,
                               provider_calls=job.counts.get("provider_calls", 0) + calls,
                               cache_hits=job.counts.get("cache_hits", 0) + hits,
                               errors=job.counts.get("errors", 0) + errors)  # fmt: skip
            await publish(job, {"stage_done": done, "stage_total": total, "eta_seconds": timer.eta(done),
                                **(await band_counts(session, job.id))})  # fmt: skip
        if quota_error is not None:
            raise JobPaused(str(quota_error))


# ----------------------------------------------------------------------------- orchestration


async def run_job(job_id: uuid.UUID, ctx: PipelineContext) -> str:
    """Run (or resume) a job until it is ready, paused, waiting for an AI batch, failed or cancelled."""
    await ctx.gazetteer.refresh_version()
    async with ctx.sessionmaker() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status in ("ready", "failed", "cancelled", "draft"):
            return job.status if job else "missing"
        try:
            # Every stage only touches keys at its own step, so re-running a stage is a no-op.
            if job.status in ("queued", "ingesting"):
                await _set_status(session, job, "ingesting")
                await stage_ingest(session, job, ctx)
            await _set_status(session, job, "normalizing")
            await stage_normalize(session, job, ctx)
            await _set_status(session, job, "ai_repair", job.message if job.ai_batch_id else None)
            if not await stage_ai_repair(session, job, ctx):
                # Waiting for an OpenAI batch (up to 24h): geocode and score everything that does
                # not need AI now, so those rows can already be reviewed.
                await stage_geocode(session, job, ctx)
                await publish(job, await band_counts(session, job.id))
                return job.status
            await _set_status(session, job, "geocoding")
            await stage_geocode(session, job, ctx)
            await _set_status(session, job, "scoring")
            job.counts = {**job.counts, **(await band_counts(session, job.id))}
            job.finished_at = datetime.now(UTC)
            await _set_status(session, job, "ready", job.message)
        except JobCancelled:
            job.finished_at = datetime.now(UTC)
            await _set_status(session, job, "cancelled", "cancelled by user")
        except JobPaused as e:
            await _set_status(session, job, "paused", str(e))
        except ParseError as e:
            await session.rollback()
            await _set_status(session, job, "failed", str(e))
        except Exception:
            log.exception("job %s failed", job_id)
            await session.rollback()
            job = await session.get(Job, job_id)
            assert job is not None
            await _set_status(session, job, "failed", "an internal error occurred while processing the job")
        return job.status


async def rerun_row(session: AsyncSession, job: Job, new_input: RawAddress, ctx: PipelineContext) -> AddressKey:
    """Edit-and-rerun for a single row: normalize, AI-repair (if enabled), geocode and score now.
    Keys are shared by identical inputs, so an already-scored key is reused as is."""
    default_country = job.options.get("default_country")
    raw = RawAddress(**{f: clean(getattr(new_input, f)) for f in FIELDS})
    h = address_key(raw, default_country)
    k = await session.scalar(select(AddressKey).where(AddressKey.job_id == job.id, AddressKey.key_hash == h))
    if k is not None and k.step == "scored":
        k.row_count += 1
        return k
    if k is None:
        seq = (await session.scalar(select(func.max(AddressKey.seq)).where(AddressKey.job_id == job.id)) or 0) + 1
        k = AddressKey(job_id=job.id, key_hash=h, seq=seq, row_count=1, in_street=raw.street,
                       in_house_number=raw.house_number, in_city=raw.city, in_postcode=raw.postcode,
                       in_country=raw.country, norm_state={}, corrections=[], flags=[], reasons=[], step="new")  # fmt: skip
        session.add(k)
        await session.flush()
    await _prefetch(ctx, [raw])
    n = await normalize(raw, ctx.gazetteer, default_country)
    ai_flag: list[str] = []
    if n.resolution == "unresolved" and job.options.get("ai_repair") and ctx.ai is not None:
        try:
            reps = await repair_addresses(ctx.ai, [(str(k.id), raw)])
            if str(k.id) in reps:
                n2, reason = await validate_repair(raw, reps[str(k.id)], ctx.gazetteer, default_country)
                k.ai_reason = reason
                if n2 is not None:
                    n, ai_flag = n2, ["ai_repaired"]
                else:
                    ai_flag = ["ai_rejected"]
        except AIError:
            ai_flag = ["ai_error"]
    stored = _store_norm(k, n)
    stored["flags"] = list(dict.fromkeys(n.flags + derived_flags(n) + ai_flag))
    for key, value in stored.items():
        if key != "id":
            setattr(k, key, value)
    k.step = "ai_done"
    await session.flush()
    config = await scoring_config_version(session, job.scoring_config_version)
    upd, cand_rows, _, _ = await resolve_key(session, job, ctx, k, config)
    await _write_results(session, [upd], cand_rows)
    await session.refresh(k)
    return k
