import http from 'k6/http';
import { check } from 'k6';

export let options = {
  scenarios: {
    breakpoint: {
      executor: 'ramping-arrival-rate',
      startRate: 50,
      timeUnit: '1s',
      preAllocatedVUs: 200,
      maxVUs: 2000,
      stages: [
        { duration: '30s', target: 200 },
        { duration: '30s', target: 500 },
        { duration: '30s', target: 1000 },
        { duration: '30s', target: 1500 },
        { duration: '30s', target: 2000 },
      ],
      tags: { scenario: 'breakpoint' },
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1500'],
    http_req_failed: ['rate<0.10'],
  },
};

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

export default function () {
  if (Math.random() < 0.5) {
    const res = http.get('http://localhost:8080/api/orders');
    check(res, { 'list ok': (r) => r.status === 200 });
  } else {
    const payload = JSON.stringify({ user_id: randomInt(1, 2), amount: Math.random() * 100, description: 'k6 breakpoint' });
    const res = http.post('http://localhost:8081/api/orders', payload, { headers: { 'Content-Type': 'application/json' } });
    check(res, { 'created': (r) => r.status === 200 || r.status === 201 });
  }
}
