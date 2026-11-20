/**
 * Course Express — estimation des temps de conduite (position → restaurant → client).
 * Ne journalise jamais les adresses client. Ne fabrique jamais de durée : toute absence de donnée = erreur.
 */

export type Position = { latitude: number; longitude: number; accuracyMeters: number; capturedAt: string };
export type Platform = 'uber_eats' | 'deliveroo' | 'unknown';
export type RequestData = {
  requestId?: string;
  restaurantName: string;
  customerAddress: string;
  origin: Position;
  platform?: Platform;
  /** Choix explicite de l'utilisateur après un `needs_confirmation` (« Corriger le restaurant »). */
  restaurantPlaceId?: string;
};

export type Candidate = { placeId: string; name: string; address: string; distanceMeters: number };

export type Estimate = {
  status: 'ok';
  requestId?: string;
  restaurant: { name: string; address: string; placeId: string; confidence: 'high' | 'user_confirmed' };
  customer: { address: string };
  travelMode: 'DRIVE';
  toRestaurantSeconds: number;
  restaurantToCustomerSeconds: number;
  totalSeconds: number;
  /** Minutes affichées, cohérentes : toRestaurant + restaurantToCustomer === total. */
  display: { toRestaurantMinutes: number; restaurantToCustomerMinutes: number; totalMinutes: number };
  trafficUsed: boolean;
  /** Le 2e trajet est calculé avec le trafic « maintenant », pas à l'heure réelle de départ du restaurant. */
  secondLegDepartureApproximated: boolean;
  estimated: true;
  mock: boolean;
};

export type FailureCode =
  | 'invalid_input' | 'location_unavailable' | 'missing_address' | 'needs_confirmation'
  | 'restaurant_not_found' | 'route_unavailable' | 'network_error' | 'rate_limited' | 'maps_not_configured';

export class EstimationError extends Error {
  constructor(public readonly code: FailureCode, message: string, public readonly candidates?: Candidate[]) {
    super(message);
    this.name = 'EstimationError';
  }
}

export type Deps = {
  fetch: typeof fetch;
  now: () => number;
  /** Délai max par appel Google (ms). */
  timeoutMs: number;
};
export const defaultDeps: Deps = { fetch: (...a) => fetch(...a), now: () => Date.now(), timeoutMs: 2500 };

export const MAX_LOCATION_AGE_MS = 120_000;
export const MAX_LOCATION_ACCURACY_M = 300;

type GPlace = {
  id?: string;
  displayName?: { text?: string };
  formattedAddress?: string;
  location?: { latitude?: number; longitude?: number };
  types?: string[];
  businessStatus?: string;
};
type PlacesResponse = { places?: GPlace[] };
type RouteResponse = { routes?: Array<{ duration?: string; distanceMeters?: number }> };

const isRecord = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const validLat = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n >= -90 && n <= 90;
const validLng = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n >= -180 && n <= 180;
export const norm = (str: string) =>
  str.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();

/** Adresse jugée assez fiable pour lancer un calcul d'itinéraire : numéro + voie + code postal. */
export function isReliableCustomerAddress(a: string): boolean {
  return /^\s*\d{1,4}/.test(a) && /\b\d{5}\b/.test(a) && /[A-Za-zÀ-ÿ]{3,}/.test(a);
}

export function validateRequest(input: unknown, now = Date.now()): RequestData {
  if (!isRecord(input)) throw new EstimationError('invalid_input', 'Requête invalide');
  const { restaurantName, customerAddress, requestId, restaurantPlaceId } = input;
  if (typeof restaurantName !== 'string' || restaurantName.trim().length < 2 || restaurantName.length > 180)
    throw new EstimationError('invalid_input', 'Nom de restaurant absent ou invalide');
  if (typeof customerAddress !== 'string' || customerAddress.trim().length < 8 || customerAddress.length > 300)
    throw new EstimationError('missing_address', 'Adresse du client absente ou invalide');
  if (!isReliableCustomerAddress(customerAddress))
    throw new EstimationError('missing_address', 'Adresse du client incomplète (numéro ou code postal manquant)');
  if (requestId !== undefined && (typeof requestId !== 'string' || !/^[A-Za-z0-9-]{8,64}$/.test(requestId)))
    throw new EstimationError('invalid_input', 'Identifiant de requête invalide');
  if (restaurantPlaceId !== undefined && (typeof restaurantPlaceId !== 'string' || !/^[A-Za-z0-9_-]{10,300}$/.test(restaurantPlaceId)))
    throw new EstimationError('invalid_input', 'Identifiant de restaurant invalide');
  if (!isRecord(input.origin)) throw new EstimationError('location_unavailable', 'Position GPS absente');
  const { latitude, longitude, accuracyMeters, capturedAt } = input.origin;
  if (!validLat(latitude) || !validLng(longitude) || (latitude === 0 && longitude === 0))
    throw new EstimationError('location_unavailable', 'Position GPS invalide');
  if (typeof accuracyMeters !== 'number' || !Number.isFinite(accuracyMeters) || accuracyMeters < 0 || accuracyMeters > MAX_LOCATION_ACCURACY_M)
    throw new EstimationError('location_unavailable', 'Position GPS insuffisamment précise');
  if (typeof capturedAt !== 'string' || !Number.isFinite(Date.parse(capturedAt)) || Math.abs(now - Date.parse(capturedAt)) > MAX_LOCATION_AGE_MS)
    throw new EstimationError('location_unavailable', 'Position GPS trop ancienne');
  if (input.platform !== undefined && !['uber_eats', 'deliveroo', 'unknown'].includes(String(input.platform)))
    throw new EstimationError('invalid_input', 'Plateforme inconnue');
  return {
    requestId: requestId as string | undefined,
    restaurantName: restaurantName.trim(),
    customerAddress: customerAddress.trim(),
    origin: { latitude, longitude, accuracyMeters, capturedAt },
    platform: input.platform as Platform | undefined,
    restaurantPlaceId: restaurantPlaceId as string | undefined
  };
}

export function secondsFromDuration(duration: string | undefined): number {
  if (!duration || !/^\d+(?:\.\d+)?s$/.test(duration)) throw new EstimationError('route_unavailable', 'Durée de route manquante');
  const value = Number(duration.slice(0, -1));
  if (!Number.isFinite(value) || value < 0) throw new EstimationError('route_unavailable', 'Durée de route incorrecte');
  return Math.round(value);
}

/**
 * Minutes affichées. Le total est l'arrondi de la SOMME des secondes brutes ; le 2e trajet est
 * déduit (total − 1er) pour que « Restaurant a + Client b = Total c » soit toujours vrai.
 */
export function displayMinutes(leg1Seconds: number, leg2Seconds: number) {
  const totalMinutes = Math.round((leg1Seconds + leg2Seconds) / 60);
  const toRestaurantMinutes = Math.min(Math.round(leg1Seconds / 60), totalMinutes);
  return { toRestaurantMinutes, restaurantToCustomerMinutes: totalMinutes - toRestaurantMinutes, totalMinutes };
}

export function haversineMeters(a: { latitude: number; longitude: number }, b: { latitude: number; longitude: number }): number {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (b.latitude - a.latitude) * rad, dLng = (b.longitude - a.longitude) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.latitude * rad) * Math.cos(b.latitude * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

async function googleCall<T>(deps: Deps, url: string, init: { method: 'GET' | 'POST'; body?: unknown }, fieldMask: string, key: string): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), deps.timeoutMs);
  try {
    const resp = await deps.fetch(url, {
      method: init.method,
      headers: { 'Content-Type': 'application/json', 'X-Goog-Api-Key': key, 'X-Goog-FieldMask': fieldMask },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: ctrl.signal
    });
    if (resp.status === 429) throw new EstimationError('rate_limited', 'Service de cartographie saturé, réessayer dans un instant');
    if (resp.status === 404) throw new EstimationError('restaurant_not_found', 'Restaurant introuvable');
    if (!resp.ok) throw new EstimationError('network_error', `Service de cartographie indisponible (${resp.status})`);
    return (await resp.json()) as T;
  } catch (e) {
    if (e instanceof EstimationError) throw e;
    throw new EstimationError('network_error', 'Temps de réponse dépassé ou connexion indisponible');
  } finally {
    clearTimeout(timer);
  }
}

/** « Pizza Cosy - Aix en Provence » → « pizza cosy » (le suffixe après « - » est souvent la ville/agence). */
export function businessNameOf(text: string): string {
  return norm(text.split(/\s+[-–—|]\s+/)[0] ?? text);
}

/**
 * Choisit l'établissement. Stricte : un seul candidat dont le nom correspond. Plusieurs homonymes →
 * `needs_confirmation` avec la liste triée par distance (choix rapide côté app).
 */
export function selectPlace(places: GPlace[], text: string, origin: Position): GPlace {
  const businessName = businessNameOf(text);
  const fullName = norm(text);
  const usable = places.filter(
    x => x.id && x.displayName?.text && x.formattedAddress && x.location && validLat(x.location.latitude) && validLng(x.location.longitude) &&
      x.businessStatus !== 'CLOSED_PERMANENTLY'
  );
  const matching = usable.filter(x => {
    const c = norm(x.displayName!.text!);
    return c === businessName || c === fullName || c.startsWith(`${businessName} `) || businessName.startsWith(`${c} `);
  });
  if (matching.length === 0) throw new EstimationError('restaurant_not_found', 'Restaurant introuvable — vérifier le nom');
  if (matching.length === 1) return matching[0];
  // Si le suffixe (ex. « Aix en Provence », « Sextius ») ne correspond qu'à un seul candidat, on le retient.
  const suffix = fullName.slice(businessName.length).trim();
  if (suffix) {
    const words = suffix.split(' ').filter(w => w.length > 2);
    const bySuffix = matching.filter(x => {
      const hay = norm(`${x.displayName!.text!} ${x.formattedAddress!}`);
      return words.length > 0 && words.every(w => hay.includes(w));
    });
    if (bySuffix.length === 1) return bySuffix[0];
  }
  const candidates = matching
    .map(x => ({
      placeId: x.id!,
      name: x.displayName!.text!,
      address: x.formattedAddress!,
      distanceMeters: Math.round(haversineMeters(origin, { latitude: x.location!.latitude!, longitude: x.location!.longitude! }))
    }))
    .sort((a, b) => a.distanceMeters - b.distanceMeters)
    .slice(0, 5);
  throw new EstimationError('needs_confirmation', 'Plusieurs établissements portent ce nom', candidates);
}

async function routeSeconds(deps: Deps, origin: unknown, destination: unknown, key: string, departureTime: string): Promise<number> {
  const data = await googleCall<RouteResponse>(
    deps,
    'https://routes.googleapis.com/directions/v2:computeRoutes',
    {
      method: 'POST',
      body: {
        origin, destination,
        travelMode: 'DRIVE',
        routingPreference: 'TRAFFIC_AWARE',
        departureTime,
        languageCode: 'fr', regionCode: 'FR',
        computeAlternativeRoutes: false
      }
    },
    'routes.duration,routes.distanceMeters',
    key
  );
  const route = data.routes?.[0];
  if (!route) throw new EstimationError('route_unavailable', 'Aucun itinéraire en voiture trouvé');
  return secondsFromDuration(route.duration);
}

async function resolveRestaurant(input: RequestData, key: string, deps: Deps): Promise<{ place: GPlace; confirmed: boolean }> {
  if (input.restaurantPlaceId) {
    const place = await googleCall<GPlace>(
      deps,
      `https://places.googleapis.com/v1/places/${encodeURIComponent(input.restaurantPlaceId)}?languageCode=fr&regionCode=FR`,
      { method: 'GET' },
      'id,displayName,formattedAddress,location',
      key
    );
    if (!place.id || !place.formattedAddress || !validLat(place.location?.latitude) || !validLng(place.location?.longitude))
      throw new EstimationError('restaurant_not_found', 'Restaurant choisi introuvable');
    return { place, confirmed: true };
  }
  const res = await googleCall<PlacesResponse>(
    deps,
    'https://places.googleapis.com/v1/places:searchText',
    {
      method: 'POST',
      body: {
        textQuery: input.restaurantName,
        languageCode: 'fr', regionCode: 'FR', pageSize: 8,
        locationBias: { circle: { center: { latitude: input.origin.latitude, longitude: input.origin.longitude }, radius: 15000 } }
      }
    },
    'places.id,places.displayName,places.formattedAddress,places.location,places.types,places.businessStatus',
    key
  );
  return { place: selectPlace(res.places ?? [], input.restaurantName, input.origin), confirmed: false };
}

/** Réponse de démonstration — durées FICTIVES, toujours marquées `mock: true`. */
function mockEstimate(input: RequestData): Estimate {
  const n = norm(input.restaurantName);
  if (n.includes('ambigu'))
    throw new EstimationError('needs_confirmation', 'Plusieurs établissements portent ce nom (démo)', [
      { placeId: 'MOCK_PLACE_A_0000', name: `${input.restaurantName} (démo A)`, address: 'Adresse fictive A', distanceMeters: 800 },
      { placeId: 'MOCK_PLACE_B_0000', name: `${input.restaurantName} (démo B)`, address: 'Adresse fictive B', distanceMeters: 4200 }
    ]);
  if (n.includes('introuvable')) throw new EstimationError('restaurant_not_found', 'Restaurant introuvable — vérifier le nom (démo)');
  const leg1 = 120, leg2 = 420;
  return {
    status: 'ok',
    requestId: input.requestId,
    restaurant: { name: input.restaurantName, address: 'ADRESSE FICTIVE (mode démo)', placeId: 'MOCK_PLACE_ID', confidence: 'high' },
    customer: { address: input.customerAddress },
    travelMode: 'DRIVE',
    toRestaurantSeconds: leg1, restaurantToCustomerSeconds: leg2, totalSeconds: leg1 + leg2,
    display: displayMinutes(leg1, leg2),
    trafficUsed: false, secondLegDepartureApproximated: true, estimated: true, mock: true
  };
}

export async function estimate(input: RequestData, key: string | undefined, mock: boolean, deps: Deps = defaultDeps): Promise<Estimate> {
  if (mock) return mockEstimate(input);
  if (!key) throw new EstimationError('maps_not_configured', 'Clé Google Maps non configurée');
  if (!isReliableCustomerAddress(input.customerAddress))
    throw new EstimationError('missing_address', 'Adresse du client incomplète');

  const { place, confirmed } = await resolveRestaurant(input, key, deps);
  const origin = { location: { latLng: { latitude: input.origin.latitude, longitude: input.origin.longitude } } };
  const restaurant = { location: { latLng: { latitude: place.location!.latitude!, longitude: place.location!.longitude! } } };
  const customer = { address: input.customerAddress };
  // Les deux trajets partent en parallèle pour tenir l'objectif < 3 s ; le second utilise le trafic
  // actuel (approximation signalée par secondLegDepartureApproximated).
  const departureTime = new Date(deps.now() + 5000).toISOString();
  const [leg1, leg2] = await Promise.all([
    routeSeconds(deps, origin, restaurant, key, departureTime),
    routeSeconds(deps, restaurant, customer, key, departureTime)
  ]);
  return {
    status: 'ok',
    requestId: input.requestId,
    restaurant: { name: place.displayName?.text ?? input.restaurantName, address: place.formattedAddress!, placeId: place.id!, confidence: confirmed ? 'user_confirmed' : 'high' },
    customer: { address: input.customerAddress },
    travelMode: 'DRIVE',
    toRestaurantSeconds: leg1, restaurantToCustomerSeconds: leg2, totalSeconds: leg1 + leg2,
    display: displayMinutes(leg1, leg2),
    trafficUsed: true, secondLegDepartureApproximated: true, estimated: true, mock: false
  };
}

export function shortBanner(r: Estimate): string {
  const m = (n: number) => (n < 1 ? '< 1 min' : `${n} min`);
  const d = r.display;
  const text = `🚗 Restaurant ${m(d.toRestaurantMinutes)} · Client ${m(d.restaurantToCustomerMinutes)} · Total ${m(d.totalMinutes)}`;
  return r.mock ? `[DÉMO – durées fictives] ${text}` : text;
}
