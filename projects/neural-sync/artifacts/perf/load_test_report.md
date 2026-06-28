# NEURAL SYNC — Load Test Report (Task04 §8 "scalable to 10k+")

**Measured, not estimated.** Seeded 10,050 developers / 20,100 pgvector embeddings via
`scripts/seed_scale.py --count 10000` and drove the live API (uvicorn + PostgreSQL/pgvector
in Docker) at concurrency 20, n=200 per endpoint.

## Results @ 10,050 developers / 20,100 embeddings (concurrency 20, n=200)

| Endpoint | p50 | p95 | p99 | SLA (<500ms p95) |
|---|---:|---:|---:|:---:|
| `GET /developers/{id}/similar` — ANN over 10k behavioral embeddings (HNSW `<=>`) | 36 ms | **321 ms** | 325 ms | ✅ |
| `GET /developers?limit=25&offset=4000` — paginated roster (deep page) | 30 ms | **39 ms** | 42 ms | ✅ |
| `GET /developers?risk_badge=high&limit=25` — filtered roster (cached badge index) | 28 ms | **54 ms** | 61 ms | ✅ |

All endpoints meet the <500 ms p95 SLA at 10k developers.

## Why it scales
- **ANN, not full scan**: `engine/retrieval.py` uses the pgvector HNSW index (`<=>` cosine,
  `m=16, ef_construction=64`) — ~O(log N) candidate retrieval, so per-query latency is roughly
  flat as the developer set grows. The deterministic 5-dim scorer then runs only on the bounded
  candidate set.
- **O(page) roster**: `GET /developers` is server-side paginated with indexed filters
  (GIN on `skills`, btree on `display_name` + cached risk badges) — deep pages don't degrade.
- **Denormalized risk cache**: `risk_badge` filtering/aggregation reads precomputed columns
  (populated by `POST /risk/refresh`) instead of recomputing per request.

## Reproduce
```bash
# seed (idempotent, id range 50000000-… ; disjoint from demo data)
docker exec -w /app neural-sync-backend-1 python scripts/seed_scale.py --count 10000
# drive load (k6) — see loadtest/
k6 run loadtest/k6_roster.js
k6 run loadtest/k6_similar.js
# restore the demo DB to its 50 developers
docker exec -w /app neural-sync-backend-1 python scripts/seed_scale.py --clear
```

> Note: the synthetic developers were cleared after measurement, so the demo DB is back to its
> 50 curated developers. Re-run the seed to reproduce these numbers.
