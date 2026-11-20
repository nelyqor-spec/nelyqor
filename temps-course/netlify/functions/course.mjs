// Reçoit { t: texte lu sur l'écran, lat, lon, wait? } et renvoie le texte de la notification.
// Adresse : https://TON-SITE.netlify.app/api/course

const WAIT_MIN = 5;          // attente moyenne au resto (min)
const CITY_KMH = 30;         // vitesse moyenne si le resto n'est pas trouvé

export default async (req) => {
  let body = {};
  try { body = await req.json(); } catch {}
  const text = String(body.t || '');
  const lat = parseFloat(String(body.lat ?? '').replace(',', '.'));
  const lon = parseFloat(String(body.lon ?? '').replace(',', '.'));
  const me = Number.isFinite(lat) && Number.isFinite(lon) ? { lat, lon } : null;
  const wait = (body.wait !== undefined ? parseFloat(body.wait) : WAIT_MIN) * 60;

  try {
    const offer = parseOffer(text.split(/\r?\n/));
    if (!offer) return reply('❓ Offre non reconnue', 'Le texte de l\'écran ne ressemble pas à une offre.');
    return reply(...await analyze(offer, me, wait));
  } catch (e) {
    return reply('⚠️ Calcul impossible', e.message);
  }
};

export const config = { path: '/api/course' };

const reply = (title, line) =>
  new Response(title + '\n' + line, { headers: { 'content-type': 'text/plain; charset=utf-8' } });

// ---------- Lecture de l'offre Uber Eats ----------
export function parseOffer(raw) {
  const lines = raw
    .map(s => s.trim().replace(/^[oO0°©®□|Dd]\s+(?=\S)/, ''))
    .filter(Boolean);
  const acceptIdx = lines.findLastIndex(l => /accepter/i.test(l));
  if (acceptIdx < 0) return null;
  const priceIdx = lines.slice(0, acceptIdx).findLastIndex(l => /€/.test(l) || /^\d+[,.]\d{2}\s*[eE]?$/.test(l));
  if (priceIdx < 0) return null;

  const num = s => { const m = s.match(/\d+([.,]\d+)?/); return m ? parseFloat(m[0].replace(',', '.')) : null; };
  const price = num(lines[priceIdx]);
  let block = lines.slice(priceIdx + 1, acceptIdx);
  let km = null;
  const kmIdx = block.findIndex(l => /\d\s*km\b/i.test(l));
  if (kmIdx >= 0) { km = num(block[kmIdx]); block = block.slice(kmIdx + 1); }
  block = block.filter(l => l.length > 2 && !/^\d+([.,]\d+)?\s*(km|min|m)\b/i.test(l));
  if (block.length < 2) return null;

  let client = '';
  for (const l of block.slice(1)) client += client.endsWith('-') ? l : (client ? ' ' : '') + l;
  return { price, km, restaurant: block[0], client };
}

// ---------- Adresses et trajets (gratuit, sans clé, sans trafic) ----------
function distM(a, b) {
  const R = 6371000, r = x => x * Math.PI / 180;
  const dLat = r(b.lat - a.lat), dLon = r(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

async function geocode(q, near) {
  const u = new URL('https://photon.komoot.io/api/');
  u.searchParams.set('q', q);
  u.searchParams.set('limit', '1');
  if (near) { u.searchParams.set('lat', near.lat); u.searchParams.set('lon', near.lon); }
  const r = await fetch(u, { headers: { 'User-Agent': 'TempsCourse/1.0' } });
  if (!r.ok) return null;
  const f = (await r.json()).features?.[0];
  if (!f) return null;
  const [lon, lat] = f.geometry.coordinates;
  if (near && distM(near, { lat, lon }) > 30000) return null;
  return { lat, lon };
}

async function route(points) {
  const coords = points.map(p => p.lon.toFixed(6) + ',' + p.lat.toFixed(6)).join(';');
  const r = await fetch('https://router.project-osrm.org/route/v1/driving/' + coords + '?overview=false');
  const j = await r.json();
  if (j.code !== 'Ok') throw new Error('itinéraire impossible');
  return j.routes[0].legs.map(l => l.duration);
}

const fmt = s => Math.max(1, Math.round(s / 60)) + ' min';

export async function analyze(offer, me, wait) {
  const client = await geocode(offer.client, me);
  if (!client) throw new Error('adresse client introuvable : ' + offer.client);

  let resto = await geocode(offer.restaurant, client)
           || await geocode(offer.restaurant.split(' - ')[0], client);
  const approx = !resto;
  if (!resto) resto = client;

  const legs = await route(me ? [me, resto, client] : [resto, client]);
  const toResto = me ? legs[0] : null;
  let restoClient = me ? legs[1] : legs[0];
  if (approx && offer.km) restoClient = offer.km * 1000 / (CITY_KMH * 1000 / 3600);

  const total = (toResto || 0) + wait + restoClient;
  const perHour = offer.price && total ? offer.price / (total / 3600) : null;

  const title = '⏱ ' + fmt(total) +
    (perHour ? ' · ' + offer.price.toFixed(2).replace('.', ',') + ' € → ' + perHour.toFixed(1).replace('.', ',') + ' €/h' : '');
  const line = '🍔 Resto : ' + (toResto !== null ? fmt(toResto) : '?') +
    ' · 🏠 Client : ' + fmt(restoClient) + (approx ? ' (approx.)' : '');
  return [title, line];
}
