// k6 load test for ANN similar-developer search over the full embedding set (Task04 §8).
//   BASE_URL=http://localhost:8000 TOKEN=$TOKEN k6 run loadtest/k6_similar.js
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
  // synthetic ids from scripts/seed_scale.py occupy 50000000-0000-0000-0000-0000000NNNNN
  const i = Math.floor(Math.random() * 10000);
  const id = `50000000-0000-0000-0000-${String(i).padStart(12, '0')}`;
  const res = http.get(`${BASE}/api/v1/developers/${id}/similar?top_k=10`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  check(res, { 'status 200': (r) => r.status === 200 });
}
