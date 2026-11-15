// Nelyqor — phrases quotidiennes (stockées localement, sans IA).
// Deux banques : activité (≥ 250) et repos (≥ 115).
// La phrase est stable toute la journée et change le lendemain ; la rotation parcourt
// toute la banque avant de répéter une phrase (pas de répétitions rapprochées).

import { dayNumber } from '../core/dates.js';

// ----------------------------------------------------------------------------
// BANQUE ACTIVITÉ
// ----------------------------------------------------------------------------
const ACTIVITY_STANDALONE = [
  'Chaque petit progrès compte.',
  'Aujourd’hui aussi, tu avances.',
  'Encore un jour plus près du but.',
  'Chaque effort rapproche ton objectif.',
  'Ton objectif se construit un jour après l’autre.',
  'La régularité fait la différence.',
  'Un pas de plus vers ce que tu as décidé.',
  'Ce que tu fais aujourd’hui compte pour demain.',
  'Avancer un peu, c’est déjà avancer.',
  'Ton objectif est plus proche qu’hier.',
  'Les grands résultats naissent de petites journées.',
  'Concentre-toi sur la journée d’aujourd’hui.',
  'Une journée bien menée, c’est une victoire.',
  'Garde le cap, une journée à la fois.',
  'Tu sais où tu vas.',
  'Chaque euro gagné rapproche la ligne d’arrivée.',
  'Fais simple, fais bien, continue.',
  'La constance bat l’intensité.',
  'Ton rythme te mène loin.',
  'Aujourd’hui, tu poses une brique de plus.',
  'Tu construis quelque chose de solide.',
  'La discipline d’aujourd’hui, la liberté de demain.',
  'Ton futur toi te remerciera.',
  'Rien ne remplace une journée de travail sérieuse.',
  'Ce n’est pas la vitesse qui compte, c’est la direction.',
  'Une journée de plus au service de ton projet.',
  'Ton objectif mérite ton attention aujourd’hui.',
  'Progresser, c’est répéter les bons gestes.',
  'La journée commence, ton objectif aussi.',
  'Tu es exactement là où tu dois être : en mouvement.',
  'Reste concentré sur ce qui dépend de toi.',
  'Ta progression se lit dans la durée.',
  'Un objectif clair, une journée utile.',
  'Le chemin se fait en marchant.',
  'Chaque journée d’activité est un investissement.',
  'Tu transformes tes efforts en résultats.',
  'Ton engagement fait avancer les chiffres.',
  'Ce que tu construis prend forme.',
  'Commence, puis continue.',
  'Une bonne journée se prépare dès le premier pas.',
  'Ton objectif avance à ton rythme.',
  'Tu as déjà parcouru du chemin.',
  'Le progrès aime la régularité.',
  'Aujourd’hui est une occasion d’avancer.',
  'Un jour après l’autre, la ligne se rapproche.',
  'Les efforts discrets font les grandes réussites.',
  'Ta persévérance est ton meilleur atout.',
  'Fais de ta journée une étape utile.',
  'Ton projet avance parce que tu avances.',
  'La motivation va et vient, l’habitude reste.',
];

const ACTIVITY_OPENINGS = [
  'Aujourd’hui,',
  'Pas à pas,',
  'Jour après jour,',
  'Avec régularité,',
  'Sans te presser,',
  'À ton rythme,',
  'Petit à petit,',
  'Un effort après l’autre,',
  'Avec constance,',
  'Tranquillement,',
  'Une journée de plus,',
  'Avec sérieux,',
];

const ACTIVITY_ENDINGS = [
  'tu te rapproches de ton objectif.',
  'ton objectif prend forme.',
  'chaque euro compte.',
  'tu avances dans la bonne direction.',
  'tu fais grandir ton épargne.',
  'tu restes sur ta trajectoire.',
  'ton projet avance.',
  'tu poses une pierre de plus.',
  'tu gagnes du terrain.',
  'ta progression continue.',
  'le cap se rapproche.',
  'tu tiens ton engagement.',
  'tu fais ta part du chemin.',
  'les résultats se construisent.',
  'ta régularité paie.',
  'tu bâtis ce que tu as décidé.',
  'la ligne d’arrivée se rapproche.',
  'ton effort devient progrès.',
];

// ----------------------------------------------------------------------------
// BANQUE REPOS
// ----------------------------------------------------------------------------
const REST_STANDALONE = [
  'Le repos fait aussi partie du chemin.',
  'Aujourd’hui, tu récupères pour mieux avancer demain.',
  'Faire une pause, c’est aussi progresser.',
  'Ton rythme compte autant que ton objectif.',
  'Aujourd’hui, on souffle. Demain, on reprend.',
  'Une pause bien prise vaut une journée bien faite.',
  'Récupérer, c’est préparer la suite.',
  'Ton objectif ne bouge pas, toi tu te reposes.',
  'Le calme d’aujourd’hui nourrit l’énergie de demain.',
  'Prendre soin de toi fait partie du plan.',
  'Un jour de repos, c’est un investissement.',
  'Ralentir permet de durer.',
  'Aujourd’hui, l’important c’est de recharger.',
  'Se reposer, c’est rester dans la course.',
  'La régularité passe aussi par le repos.',
  'Respire, ton objectif t’attend.',
  'Une journée calme pour repartir plus fort.',
  'Le repos est un allié, pas un retard.',
  'Accorde-toi cette pause, tu l’as méritée.',
  'Ton énergie est ta première ressource.',
  'Même au repos, ton objectif reste en vue.',
  'Aujourd’hui, tu laisses le temps faire son travail.',
  'Un esprit reposé prend de meilleures décisions.',
  'Profite de ta journée, demain on avance.',
  'Le chemin est long, le repos le rend possible.',
  'Pause méritée, reprise assurée.',
  'Ton plan prévoit aussi des jours comme celui-ci.',
  'Aujourd’hui, rien à prouver : juste à récupérer.',
  'Recharger les batteries fait partie du travail.',
  'Une pause aujourd’hui, de l’élan demain.',
  'Ton corps et ton esprit te remercient.',
  'Le repos protège ta constance.',
  'Lève le pied, garde le cap.',
  'Demain sera plus facile grâce à aujourd’hui.',
  'Savoir s’arrêter, c’est savoir durer.',
];

const REST_OPENINGS = [
  'Aujourd’hui,',
  'Pendant cette pause,',
  'Sans culpabiliser,',
  'En douceur,',
  'Pour mieux repartir,',
  'Le temps d’une journée,',
  'Tranquillement,',
  'À ton rythme,',
  'En prenant soin de toi,',
  'Loin de l’effort,',
];

const REST_ENDINGS = [
  'tu recharges ton énergie.',
  'tu prépares la suite.',
  'ton objectif t’attend patiemment.',
  'tu prends le recul nécessaire.',
  'tu récupères pour mieux avancer.',
  'tu restes sur ta trajectoire.',
  'tu respires avant de reprendre.',
  'tu gardes le cap autrement.',
  'ton rythme est respecté.',
  'tu construis aussi ta régularité.',
  'tu laisses la fatigue derrière toi.',
  'tu prépares une belle reprise.',
];

const combine = (openings, endings) => {
  const out = [];
  endings.forEach((e) => openings.forEach((o) => out.push(`${o} ${e}`)));
  return out;
};

export const ACTIVITY_QUOTES = [...ACTIVITY_STANDALONE, ...combine(ACTIVITY_OPENINGS, ACTIVITY_ENDINGS)];
export const REST_QUOTES = [...REST_STANDALONE, ...combine(REST_OPENINGS, REST_ENDINGS)];

// Pas de rotation premier avec la taille des banques : parcourt toute la banque avant
// de revenir sur une phrase, en sautant loin à chaque jour (évite les phrases voisines).
const STEP = 97;

function pick(list, dateKey) {
  const n = list.length;
  let step = STEP % n;
  const gcd = (a, b) => (b ? gcd(b, a % b) : a);
  while (gcd(step, n) !== 1) step++;
  const idx = ((dayNumber(dateKey) * step) % n + n) % n;
  return list[idx];
}

/** Phrase du jour, stable pour la date donnée. */
export function getDailyQuote(dateKey, isActivityDay) {
  return pick(isActivityDay ? ACTIVITY_QUOTES : REST_QUOTES, dateKey);
}
