/**
 * Course Express — extraction du nom du restaurant et de l'adresse client à partir du texte OCR
 * d'une capture de proposition de course (Uber Eats, Deliveroo).
 *
 * Implémentation de référence, testée en Node. Le port Swift (ios/CourseExpress/Shared/OfferParser.swift)
 * suit exactement les mêmes règles et les mêmes fixtures.
 *
 * Règles importantes :
 *  - l'adresse du restaurant n'est JAMAIS déduite de la capture (elle n'y figure pas) ;
 *  - les distances affichées (« 0.9 km ») et les durées ne sont jamais réutilisées comme temps de trajet ;
 *  - en cas de doute, on renvoie une erreur explicite plutôt qu'une valeur supposée.
 */

export type Platform = 'uber_eats' | 'deliveroo' | 'unknown';

export type ParsedOffer = {
  platform: Platform;
  restaurantName: string;
  /** Adresse telle que lue (lignes recollées), sans normalisation. */
  customerAddressRaw: string;
  /** Adresse normalisée, prête à être envoyée au backend. */
  customerAddress: string;
};

export type ParseFailure = 'no_text' | 'missing_address' | 'missing_restaurant';

export class OfferParseError extends Error {
  constructor(public readonly code: ParseFailure, message: string) {
    super(message);
    this.name = 'OfferParseError';
  }
}

const fold = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();

/** Types de voie reconnus en début d'adresse (formes complètes et abrégées). */
const STREET_TYPES =
  'av|ave|avenue|rue|r|bd|boulevard|blvd|bld|ch|chem|chemin|rte|route|all|allee|allées|allees|pl|place|imp|impasse|' +
  'cours|crs|quai|qu|sq|square|traverse|trav|tse|montee|mtee|lotissement|lot|residence|res|résidence|' +
  'domaine|dom|hameau|ham|cite|cité|parc|esplanade|esp|passage|pass|voie|sentier|faubourg|fg|promenade|prom|' +
  'rond-point|rd-pt|carrefour|zac|za|zi|mail|villa|clos|vc|lieu-dit|ld';

const STREET_START = new RegExp(`^\\d{1,4}\\s*(?:bis|ter|b|t)?\\s*,?\\s+(?:${STREET_TYPES})\\b\\.?`, 'i');
const POSTCODE = /\b\d{5}\b/;

/** Lignes d'interface à ignorer (badges, boutons, montants, distances, heures…). */
function isNoise(line: string): boolean {
  const f = fold(line);
  if (f.length < 2) return true;
  if (/^(livraison|exclusivite|accepter|accept|refuser|decline|retrait|collecte|depot|nouvelle commande|new order|commande|course|ignorer|x|×)$/.test(f)) return true;
  if (/€|eur\b/.test(f)) return true; // montant
  if (/^\d+([.,]\d+)?\s*(km|m|mi)\b/.test(f)) return true; // distance affichée
  if (/^\d+\s*(min|mn|h)\b/.test(f)) return true; // durée affichée
  if (/^\d{1,2}[:h]\d{2}$/.test(f)) return true; // heure
  if (/^(4g|5g|lte|wifi)\b/.test(f)) return true;
  return false;
}

export function detectPlatform(lines: string[]): Platform {
  const all = fold(lines.join('\n'));
  if (/deliveroo|retrait chez|collecte chez/.test(all)) return 'deliveroo';
  if (/uber|exclusivite|\baccepter\b/.test(all)) return 'uber_eats';
  return 'unknown';
}

/** Recolle une adresse coupée sur plusieurs lignes : « Aix-en-\nProvence » → « Aix-en-Provence ». */
export function joinAddressLines(parts: string[]): string {
  let out = '';
  for (const raw of parts) {
    const p = raw.trim();
    if (!p) continue;
    if (!out) out = p;
    else if (out.endsWith('-')) out += p;
    else out += (out.endsWith(',') ? ' ' : ', ') + p;
  }
  return out.replace(/\s+/g, ' ').trim();
}

/** Abréviations de type de voie → forme complète (clé sans accent ni point, en minuscules). */
export const STREET_ABBREVIATIONS: Record<string, string> = {
  av: 'avenue', ave: 'avenue', bd: 'boulevard', blvd: 'boulevard', bld: 'boulevard',
  ch: 'chemin', chem: 'chemin', rte: 'route', all: 'allée', pl: 'place', imp: 'impasse',
  crs: 'cours', sq: 'square', trav: 'traverse', tse: 'traverse', res: 'résidence',
  fg: 'faubourg', prom: 'promenade', esp: 'esplanade', mtee: 'montée', ham: 'hameau', dom: 'domaine'
};

/**
 * Normalise une adresse française : abréviations de voie, virgule parasite après le code postal,
 * espaces, pays. Les accents sont conservés (Google les gère).
 */
export function normalizeAddress(address: string): string {
  let a = address.replace(/\s+/g, ' ').trim();
  // Seul le type de voie (juste après le numéro) est développé, pour ne pas toucher aux noms propres.
  a = a.replace(/^(\d{1,4}(?:\s*(?:bis|ter))?)\s*,?\s+([A-Za-zÀ-ÿ]+)\.?(?=\s)/i, (m, num: string, word: string) => {
    const full = STREET_ABBREVIATIONS[fold(word)];
    return full ? `${num} ${full}` : m;
  });
  // Abréviations Saint/Sainte n'importe où (St-Mitre → Saint-Mitre).
  a = a.replace(/\bSt(e)?\.?(?=[ -][A-ZÀ-Ý])/g, (_m, e: string | undefined) => (e ? 'Sainte' : 'Saint'));
  // « 13090, Aix-en-Provence » → « 13090 Aix-en-Provence »
  a = a.replace(/\b(\d{5}),\s*/g, '$1 ');
  // « Aix en Provence » → « Aix-en-Provence » (forme officielle), idem quelques communes composées courantes.
  a = a.replace(/\bAix[ -]en[ -]Provence\b/gi, 'Aix-en-Provence');
  a = a.replace(/\s*,\s*/g, ', ').replace(/,\s*$/, '');
  if (!/\bfrance\b/i.test(a)) a += ', France';
  return a;
}

function looksLikeAddressStart(line: string): boolean {
  return STREET_START.test(line.trim());
}

/** Lit l'adresse à partir de la ligne `start` jusqu'au code postal + commune ou à une ligne d'interface. */
function collectAddress(lines: string[], start: number): { text: string; end: number } {
  const parts: string[] = [];
  let i = start;
  for (; i < lines.length && parts.length < 4; i++) {
    const line = lines[i].trim();
    if (!line) break;
    if (parts.length > 0 && (isNoise(line) || looksLikeAddressStart(line))) break;
    parts.push(line);
    const joined = joinAddressLines(parts);
    // Arrêt quand on a code postal + commune complète (pas de trait d'union final).
    if (POSTCODE.test(joined) && /\d{5}[ ,]+[A-Za-zÀ-ÿ]/.test(joined) && !joined.endsWith('-')) {
      i++;
      break;
    }
  }
  return { text: joinAddressLines(parts), end: i };
}

function parseUberEats(lines: string[]): { restaurant?: string; address?: string } {
  // La carte de proposition commence après le montant ; on ignore les étiquettes de carte au-dessus.
  let from = lines.findIndex(l => /\d+[.,]\d{2}\s*€/.test(l));
  if (from < 0) from = 0;
  const to = (() => {
    const idx = lines.findIndex((l, i) => i > from && /^(accepter|accept)$/i.test(fold(l)));
    return idx < 0 ? lines.length : idx;
  })();
  for (let i = from; i < to; i++) {
    if (!looksLikeAddressStart(lines[i])) continue;
    const { text } = collectAddress(lines.slice(0, to), i);
    // Le restaurant est la ligne utile qui précède immédiatement l'adresse.
    let restaurant: string | undefined;
    for (let j = i - 1; j >= from; j--) {
      if (!isNoise(lines[j])) { restaurant = lines[j].trim(); break; }
    }
    return { restaurant, address: text };
  }
  return {};
}

/**
 * Deliveroo — format NON validé sur une vraie capture. Extracteur à étiquettes :
 * « Retrait / Collecte (chez) » → nom du restaurant ; « Livraison / Dépôt / Client » → adresse.
 */
function parseDeliveroo(lines: string[]): { restaurant?: string; address?: string } {
  let restaurant: string | undefined;
  let address: string | undefined;
  for (let i = 0; i < lines.length; i++) {
    const f = fold(lines[i]);
    const pickup = f.match(/^(retrait|collecte|pick ?up)(\s+chez)?\s*:?\s*(.*)$/);
    if (pickup && !restaurant) {
      const inline = lines[i].trim().slice(lines[i].trim().length - pickup[3].length).trim();
      if (inline && !isNoise(inline)) restaurant = inline;
      else {
        const next = lines.slice(i + 1).find(l => !isNoise(l));
        if (next) restaurant = next.trim();
      }
      continue;
    }
    const drop = f.match(/^(livraison|depot|client|drop ?off)(\s+a)?\s*:?\s*(.*)$/);
    if (drop && !address) {
      const inline = lines[i].trim().slice(lines[i].trim().length - drop[3].length).trim();
      if (inline && looksLikeAddressStart(inline)) {
        address = collectAddress([inline, ...lines.slice(i + 1)], 0).text;
      } else {
        const k = lines.findIndex((l, idx) => idx > i && looksLikeAddressStart(l));
        if (k >= 0) address = collectAddress(lines, k).text;
      }
    }
  }
  return { restaurant, address };
}

/** Point d'entrée : texte OCR (lignes dans l'ordre de lecture, haut → bas). */
export function parseOffer(ocrText: string): ParsedOffer {
  const lines = ocrText.split(/\r?\n/).map(l => l.replace(/\s+/g, ' ').trim()).filter(Boolean);
  if (lines.length === 0) throw new OfferParseError('no_text', 'Aucun texte lisible sur la capture');
  const platform = detectPlatform(lines);
  let found = platform === 'deliveroo' ? parseDeliveroo(lines) : parseUberEats(lines);
  if (!found.address && platform !== 'deliveroo') found = { ...parseDeliveroo(lines), ...stripUndefined(found) };
  if (!found.address || !POSTCODE.test(found.address))
    throw new OfferParseError('missing_address', 'Adresse du client introuvable sur la capture');
  if (!found.restaurant)
    throw new OfferParseError('missing_restaurant', 'Nom du restaurant introuvable sur la capture');
  if (fold(found.restaurant) === fold(found.address))
    throw new OfferParseError('missing_restaurant', 'Restaurant et adresse client confondus');
  return {
    platform,
    restaurantName: found.restaurant,
    customerAddressRaw: found.address,
    customerAddress: normalizeAddress(found.address)
  };
}

function stripUndefined<T extends object>(o: T): Partial<T> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as Partial<T>;
}
