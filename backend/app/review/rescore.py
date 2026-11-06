"""Explicit re-scoring of a finished job with the active scoring configuration.
Review statuses are kept; manually placed rows keep their manual score."""

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.geocode.base import GeoCandidate
from app.jobs.pipeline import apply_keys_to_rows, raw_of
from app.models import AddressKey, Candidate, Job, Row
from app.normalize.pipeline import NormResult
from app.scoring.signals import score_candidate

CHUNK = 1000


async def rescore_job(db: AsyncSession, job: Job, config: dict) -> int:
    done = 0
    last_id = 0
    while True:
        keys = list((await db.scalars(
            select(AddressKey).where(AddressKey.job_id == job.id, AddressKey.step == "scored", AddressKey.id > last_id)
            .order_by(AddressKey.id).limit(CHUNK))).all())  # fmt: skip
        if not keys:
            break
        last_id = keys[-1].id
        cands: dict[int, list[Candidate]] = {}
        for c in (await db.scalars(select(Candidate).where(Candidate.address_key_id.in_([k.id for k in keys])))).all():
            cands.setdefault(c.address_key_id, []).append(c)
        key_updates, cand_updates = [], []
        for k in keys:
            n = NormResult.from_dict(k.norm_state)
            raw = raw_of(k)
            top = None
            for c in sorted(cands.get(k.id, []), key=lambda c: c.rank):
                gc = GeoCandidate(c.lat, c.lon, c.source, c.precision, c.label, c.components, c.provider_confidence,
                                  list(c.flags))  # fmt: skip
                s = score_candidate(raw, n, gc, k.flags, config)
                cand_updates.append({"id": c.id, "score": s.score, "band": s.band, "reasons": s.reasons})
                if c.id == k.selected_candidate_id:
                    top = s
            if top is None:
                top = score_candidate(raw, n, None, k.flags, config)
            key_updates.append({"id": k.id, "score": top.score, "band": top.band, "reasons": top.reasons})
        if cand_updates:
            await db.execute(update(Candidate), cand_updates)
        await db.execute(update(AddressKey), key_updates)
        await apply_keys_to_rows(db, [k.id for k in keys])
        done += len(keys)
    # Rows that picked an alternative candidate take that candidate's new score.
    await db.execute(
        text("""
        UPDATE rows r SET score = c.score, band = c.band
        FROM candidates c
        WHERE r.job_id = :job AND r.overridden AND r.override->>'type' = 'candidate'
          AND c.id = (r.override->>'candidate_id')::bigint
        """),
        {"job": job.id},
    )
    _ = Row
    return done
