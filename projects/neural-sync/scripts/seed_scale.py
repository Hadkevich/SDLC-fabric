"""Seed N synthetic developers (+ skill/behavioral embeddings) to prove 10k-scale
behavior (Task04 §8), and tear them down again.

Idempotent: all rows use the id range 50000000-0000-0000-0000-XXXXXXXXXXXX, disjoint from
the demo seed (20000000-…), so this never collides with or clobbers the demo data and can be
removed cleanly with --clear (restoring the DB to the demo's 50 developers).

Usage (inside the backend container):
    docker exec -w /app neural-sync-backend-1 python scripts/seed_scale.py --count 10000
    docker exec -w /app neural-sync-backend-1 python scripts/seed_scale.py --clear
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import random
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from src.db.session import AsyncSessionLocal
from src.engine.embeddings import get_embedding_dim

_PREFIX = "50000000-0000-0000-0000-"
_SKILL_POOL = ["Python", "FastAPI", "PostgreSQL", "React", "TypeScript", "Docker",
               "Kubernetes", "ML", "PyTorch", "Go", "Rust", "Kafka", "Redis", "AWS", "GCP"]
_TZS = ["America/New_York", "America/Los_Angeles", "Europe/London", "Europe/Warsaw",
        "Europe/Berlin", "Asia/Singapore", "Asia/Tokyo", "Asia/Kolkata", "Australia/Sydney"]


def _dev_id(i: int) -> str:
    return f"{_PREFIX}{i:012d}"


def _vec_literal(seed: str, dim: int) -> str:
    rng = random.Random(int(hashlib.md5(seed.encode()).hexdigest()[:8], 16))
    v = [rng.gauss(0, 1) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return "[" + ",".join(f"{x / n:.6f}" for x in v) + "]"


async def clear() -> None:
    async with AsyncSessionLocal() as db:
        # developer_embeddings cascade from developer_profiles, but delete explicitly to be safe
        await db.execute(text(f"DELETE FROM developer_embeddings WHERE developer_id::text LIKE '{_PREFIX}%'"))
        await db.execute(text(f"DELETE FROM allocation_records WHERE developer_id::text LIKE '{_PREFIX}%'"))
        await db.execute(text(f"DELETE FROM developer_profiles WHERE id::text LIKE '{_PREFIX}%'"))
        await db.commit()
    print(f"cleared synthetic developers in range {_PREFIX}…")


async def seed(count: int) -> None:
    dim = get_embedding_dim()
    now = datetime.now(timezone.utc)
    rng = random.Random(42)
    batch = 500
    async with AsyncSessionLocal() as db:
        for start in range(0, count, batch):
            prof_rows, emb_rows, alloc_rows = [], [], []
            for i in range(start, min(start + batch, count)):
                did = _dev_id(i)
                skills = rng.sample(_SKILL_POOL, k=rng.randint(3, 6))
                ws = [round(rng.random(), 3) for _ in range(8)]
                mv = [round(rng.random(), 3) for _ in range(8)]
                prof_rows.append({
                    "id": did, "skills": skills, "exp": rng.randint(1, 15),
                    "stack": skills[:3], "ws": ws, "mv": mv,
                    "tz": rng.choice(_TZS), "avail": rng.randint(20, 45),
                    "goals": ["technical leadership", "ml"], "name": f"Synthetic Dev {i}",
                })
                emb_rows.append({"id": str(uuid.uuid4()), "dev": did, "t": "skill",
                                 "v": _vec_literal(f"{did}-skill", dim)})
                emb_rows.append({"id": str(uuid.uuid4()), "dev": did, "t": "behavioral",
                                 "v": _vec_literal(f"{did}-beh", dim)})
                # one allocation ending soon-ish for a realistic risk spread
                s = date(2026, 1, 1) + timedelta(days=rng.randint(0, 120))
                alloc_rows.append({"id": str(uuid.uuid4()), "dev": did,
                                   "s": s, "e": s + timedelta(weeks=rng.randint(8, 52)),
                                   "wl": round(rng.uniform(0.4, 0.95), 2)})

            await db.execute(text("""
                INSERT INTO developer_profiles
                  (id, skills, experience_years, preferred_stack, work_style_vector,
                   motivation_vector, timezone, availability_hours, career_goals,
                   project_history, is_behavioral_self_reported, embedding_status,
                   display_name, created_at, updated_at)
                VALUES
                  (:id, CAST(:skills AS jsonb), :exp, CAST(:stack AS jsonb),
                   CAST(:ws AS jsonb), CAST(:mv AS jsonb), :tz, :avail,
                   CAST(:goals AS jsonb), '[]'::jsonb, TRUE, 'ready', :name, :now, :now)
                ON CONFLICT (id) DO NOTHING
            """), [{**r, "skills": _j(r["skills"]), "stack": _j(r["stack"]),
                    "ws": _j(r["ws"]), "mv": _j(r["mv"]), "goals": _j(r["goals"]),
                    "now": now} for r in prof_rows])

            await db.execute(text("""
                INSERT INTO developer_embeddings
                  (id, developer_id, embedding_type, vector, model_name, model_version, created_at, updated_at)
                VALUES (:id, :dev, :t, CAST(:v AS vector), 'synthetic', '1', :now, :now)
                ON CONFLICT DO NOTHING
            """), [{**r, "now": now} for r in emb_rows])

            await db.execute(text("""
                INSERT INTO allocation_records
                  (id, developer_id, project_id, start_date, end_date, workload_intensity,
                   is_active, created_at, updated_at)
                VALUES (:id, :dev, NULL, :s, :e, :wl, TRUE, :now, :now)
            """), [{**r, "now": now} for r in alloc_rows])

            await db.commit()
            print(f"  seeded {min(start + batch, count)}/{count}")
    print(f"done — {count} synthetic developers (+{count*2} embeddings) in range {_PREFIX}…")


def _j(obj) -> str:
    import json
    return json.dumps(obj)


def main() -> None:
    p = argparse.ArgumentParser(description="Seed/clear synthetic developers for scale testing.")
    p.add_argument("--count", type=int, default=10000)
    p.add_argument("--clear", action="store_true", help="remove all synthetic developers and exit")
    args = p.parse_args()
    asyncio.run(clear() if args.clear else seed(args.count))


if __name__ == "__main__":
    main()
