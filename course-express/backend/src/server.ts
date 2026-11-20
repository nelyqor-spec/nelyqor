import { createServer, type IncomingMessage, type Server } from 'node:http';
import { createHash, randomUUID, timingSafeEqual } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { defaultDeps, estimate, EstimationError, shortBanner, validateRequest, type Deps } from './estimate.js';

export type Config = {
  token: string;
  key?: string;
  mock: boolean;
  /** Requêtes autorisées par minute et par jeton (garde-fou coût Google). */
  ratePerMinute: number;
  /** Plafond journalier global d'estimations réelles (garde-fou facturation). */
  dailyCap: number;
  deps?: Deps;
  log?: (line: string) => void;
};

const digest = (s: string) => createHash('sha256').update(s).digest();

/** Comparaison en temps constant du jeton Bearer. */
export function authorized(header: string | undefined, token: string): boolean {
  if (!header?.startsWith('Bearer ')) return false;
  return timingSafeEqual(digest(header.slice(7)), digest(token));
}

async function readBody(req: IncomingMessage, limit: number): Promise<string | null> {
  let text = '';
  for await (const chunk of req) {
    text += chunk.toString();
    if (text.length > limit) return null;
  }
  return text;
}

const HTTP_STATUS: Record<string, number> = {
  invalid_input: 400, missing_address: 422, location_unavailable: 422, needs_confirmation: 409,
  restaurant_not_found: 404, route_unavailable: 422, network_error: 503, rate_limited: 503, maps_not_configured: 500
};

export function createApp(cfg: Config): Server {
  const deps = cfg.deps ?? defaultDeps;
  const log = cfg.log ?? ((l: string) => process.stdout.write(`${l}\n`));
  const hits: number[] = [];
  let day = new Date().toISOString().slice(0, 10);
  let dayCount = 0;

  return createServer(async (req, res) => {
    const started = performance.now();
    const json = (status: number, value: unknown) => {
      res.writeHead(status, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
        'Strict-Transport-Security': 'max-age=31536000'
      });
      res.end(JSON.stringify(value));
    };
    if (req.method === 'GET' && req.url === '/health') return json(200, { status: 'healthy', mock: cfg.mock });
    if (req.method !== 'POST' || req.url !== '/v1/estimate') return json(404, { error: 'not_found' });
    if (!authorized(req.headers.authorization, cfg.token)) return json(401, { error: 'unauthorized' });

    const now = deps.now();
    while (hits.length && now - hits[0] > 60_000) hits.shift();
    if (hits.length >= cfg.ratePerMinute) return json(429, { status: 'error', code: 'rate_limited', message: 'Trop de demandes, patienter une minute' });
    hits.push(now);
    const today = new Date(now).toISOString().slice(0, 10);
    if (today !== day) { day = today; dayCount = 0; }
    if (!cfg.mock && dayCount >= cfg.dailyCap)
      return json(429, { status: 'error', code: 'rate_limited', message: 'Quota journalier atteint' });

    let requestId: string = randomUUID();
    let code = 'ok';
    try {
      const text = await readBody(req, 8000);
      if (text === null) { code = 'payload_too_large'; return json(413, { status: 'error', code }); }
      let raw: unknown;
      try { raw = JSON.parse(text); } catch { code = 'invalid_json'; return json(400, { status: 'error', code }); }
      const data = validateRequest(raw, now);
      requestId = data.requestId ?? requestId;
      if (!cfg.mock) dayCount++;
      const result = await estimate({ ...data, requestId }, cfg.key, cfg.mock, deps);
      return json(200, { ...result, banner: shortBanner(result), serverMs: Math.round(performance.now() - started) });
    } catch (e) {
      if (e instanceof EstimationError) {
        code = e.code;
        return json(HTTP_STATUS[e.code] ?? 422, {
          status: e.code, requestId, message: e.message, ...(e.candidates ? { candidates: e.candidates } : {})
        });
      }
      code = 'internal_error';
      return json(500, { status: 'error', code, requestId });
    } finally {
      // Journal technique uniquement : jamais d'adresse, de nom de restaurant ni de coordonnées.
      log(JSON.stringify({ t: new Date(now).toISOString(), requestId, code, ms: Math.round(performance.now() - started) }));
    }
  });
}

function main() {
  const token = process.env.API_TOKEN;
  const key = process.env.GOOGLE_MAPS_API_KEY;
  const mock = process.env.MOCK_MAPS === '1';
  if (!token || token.length < 24) {
    process.stderr.write('ERROR: API_TOKEN doit contenir au moins 24 caractères aléatoires.\n');
    process.exit(1);
  }
  if (!mock && !key) {
    process.stderr.write('ERROR: GOOGLE_MAPS_API_KEY requis (ou MOCK_MAPS=1 pour une démonstration).\n');
    process.exit(1);
  }
  const port = Number(process.env.PORT ?? '8080');
  createApp({
    token, key, mock,
    ratePerMinute: Number(process.env.RATE_PER_MINUTE ?? '20'),
    dailyCap: Number(process.env.DAILY_CAP ?? '300')
  }).listen(port, '0.0.0.0', () => process.stdout.write(`Course Express API sur le port ${port} ; MOCK_MAPS=${mock}\n`));
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) main();
