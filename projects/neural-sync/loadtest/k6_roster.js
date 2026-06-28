// k6 load test for the paginated roster (Task04 §8). Run:
//   TOKEN=$(docker exec neural-sync-backend-1 python -c \
//     "from src.core.auth import create_access_token; print(create_access_token('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee','manager',None))")
//   BASE_URL=http://localhost:8000 TOKEN=$TOKEN k6 run loadtest/k6_roster.js
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE_URL || 'http://localhost:8000';
const TOKEN = __ENV.TOKEN || '';

export const options = {
  vus: 20,
  duration: '30s',
  thresholds: { http_req_duration: ['p(95)<500'] },
};

export default function () {
  const offset = Math.floor(Math.random() * 9000);
  const res = http.get(`${BASE}/api/v1/developers?limit=25&offset=${offset}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  check(res, { 'status 200': (r) => r.status === 200 });
}
