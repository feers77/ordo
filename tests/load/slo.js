// Test de carga contra los SLO de PLAN-MAESTRO §4.3.
//
//   k6 run -e ORDO_URL=http://127.0.0.1:8000 -e ORDO_TENANT=acme tests/load/slo.js
//
// Los umbrales son bloqueantes: si un p95 se degrada, k6 sale con código != 0
// y el pipeline falla. Eso es deliberado — el rendimiento es un contrato, no
// una aspiración.

import http from 'k6/http';
import { check, group } from 'k6';
import { Trend } from 'k6/metrics';
import { uuidv4 } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const BASE = __ENV.ORDO_URL || 'http://127.0.0.1:8000';
const TENANT = __ENV.ORDO_TENANT || 'loadtest';
const MODEL = __ENV.ORDO_MODEL || 'res.partner';

const readById = new Trend('ordo_read_by_id', true);
const searchRead = new Trend('ordo_search_read', true);
const createOne = new Trend('ordo_create', true);

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || 50),
      timeUnit: '1s',
      duration: __ENV.DURATION || '30s',
      preAllocatedVUs: 10,
      maxVUs: 50,
    },
  },
  // SLO de §4.3. Si algo aquí falla, el build falla.
  thresholds: {
    'ordo_read_by_id':  ['p(50)<8',   'p(95)<30'],
    'ordo_search_read': ['p(50)<25',  'p(95)<100'],
    'ordo_create':      ['p(50)<60',  'p(95)<250'],
    'http_req_failed':  ['rate<0.01'],
  },
};

function headers(idempotencyKey) {
  const h = { 'X-Ordo-Tenant': TENANT, 'Content-Type': 'application/json' };
  if (idempotencyKey) h['Idempotency-Key'] = idempotencyKey;
  return h;
}

export function setup() {
  // Un registro conocido para las lecturas: sin él, medir "lectura por id"
  // no significaría nada.
  const res = http.post(
    `${BASE}/api/v1/${MODEL}`,
    JSON.stringify({ values: { name: 'carga-baseline' } }),
    { headers: headers(uuidv4()) },
  );
  if (res.status !== 201) {
    throw new Error(`setup falló: ${res.status} ${res.body}`);
  }
  return { id: res.json('ids.0') };
}

export default function (data) {
  group('lectura por id', () => {
    const res = http.get(`${BASE}/api/v1/${MODEL}/${data.id}?fields=name`, {
      headers: headers(),
    });
    readById.add(res.timings.duration);
    check(res, { 'read 200': (r) => r.status === 200 });
  });

  group('search_read', () => {
    const domain = encodeURIComponent('[["name","like","carga"]]');
    const res = http.get(
      `${BASE}/api/v1/${MODEL}?domain=${domain}&fields=name,state&limit=80`,
      { headers: headers() },
    );
    searchRead.add(res.timings.duration);
    check(res, { 'search 200': (r) => r.status === 200 });
  });

  group('create', () => {
    const res = http.post(
      `${BASE}/api/v1/${MODEL}`,
      JSON.stringify({ values: { name: `carga-${uuidv4()}` } }),
      { headers: headers(uuidv4()) },
    );
    createOne.add(res.timings.duration);
    check(res, { 'create 201': (r) => r.status === 201 });
  });
}
