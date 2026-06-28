# Load testing (Task04 §8 — scalable to 10k+)

Proves the roster + ANN paths hold the <500ms p95 SLA at 10k developers.

## 1. Seed 10k synthetic developers (idempotent, removable)
```bash
docker exec -w /app neural-sync-backend-1 python scripts/seed_scale.py --count 10000
```

## 2. Mint a manager token
```bash
TOKEN=$(docker exec neural-sync-backend-1 python -c \
  "from src.core.auth import create_access_token; print(create_access_token('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee','manager',None))")
```

## 3. Run k6 (https://k6.io)
```bash
BASE_URL=http://localhost:8000 TOKEN=$TOKEN k6 run loadtest/k6_roster.js
BASE_URL=http://localhost:8000 TOKEN=$TOKEN k6 run loadtest/k6_similar.js
```
Both declare `thresholds: http_req_duration p(95)<500`, so k6 exits non-zero if the SLA is missed.

## 4. Restore the demo DB
```bash
docker exec -w /app neural-sync-backend-1 python scripts/seed_scale.py --clear
```

Latest measured results: [`../artifacts/perf/load_test_report.md`](../artifacts/perf/load_test_report.md)
(p95: roster 39ms, filtered 54ms, ANN /similar 321ms — all under 500ms at 10,050 developers).

> No k6 installed? `scripts/seed_scale.py` + a concurrent `curl`/`httpx` loop reproduces the same
> numbers (that's how the committed report was generated).
