// Nelyqor — mini-outil de rendu DOM + icônes (aucune dépendance).

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      // Pas d'option « html » : tout contenu passe par des nœuds texte (aucune injection HTML possible).
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'value') el.value = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const P = {
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/>',
  revenue: '<path d="M4 17l5-5 4 4 7-8"/><path d="M15 8h5v5"/>',
  transfer: '<path d="M4 8h13l-3-3"/><path d="M20 16H7l3 3"/>',
  withdrawal: '<path d="M12 4v13"/><path d="M6 11l6 6 6-6"/><path d="M5 20h14"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15" rx="3"/><path d="M8 3v4M16 3v4M3.5 10h17"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  percent: '<path d="M6 18L18 6"/><circle cx="7.5" cy="7.5" r="2.5"/><circle cx="16.5" cy="16.5" r="2.5"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  about: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 11v5M12 8v.5"/>',
  warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 019.5 4a8 8 0 1010.5 10.5z"/>',
  download: '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  folder: '<path d="M3.5 7.5a2 2 0 012-2h4l2 2.5h7a2 2 0 012 2v7.5a2 2 0 01-2 2h-13a2 2 0 01-2-2z"/>',
  camera: '<path d="M4 8.5a2 2 0 012-2h2l1.5-2h5L16 6.5h2a2 2 0 012 2V18a2 2 0 01-2 2H6a2 2 0 01-2-2z"/><circle cx="12" cy="13" r="3.5"/>',
  palette: '<path d="M12 3a9 9 0 100 18c1.2 0 1.8-.8 1.8-1.7 0-1.3-1.1-1.6-1.1-2.8 0-1 .8-1.7 1.8-1.7H17a4 4 0 004-4C21 6.6 17 3 12 3z"/><circle cx="7.5" cy="11" r="1.2"/><circle cx="10" cy="7" r="1.2"/><circle cx="15" cy="7.5" r="1.2"/>',
  cloud: '<path d="M7 18a4.5 4.5 0 01-.6-8.96A6 6 0 0118 9.5a4.25 4.25 0 01-1 8.5z"/>',
  logout: '<path d="M15 4h3a2 2 0 012 2v12a2 2 0 01-2 2h-3"/><path d="M10 16l-4-4 4-4M6 12h10"/>',
  mail: '<rect x="3.5" y="5.5" width="17" height="13" rx="2.5"/><path d="M4 7l8 6 8-6"/>',
  shield: '<path d="M12 3l7 3v5.5c0 4.3-3 7.8-7 9.5-4-1.7-7-5.2-7-9.5V6z"/>',
  refresh: '<path d="M20 11a8 8 0 00-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0014.3 4.9L20 16"/><path d="M20 20v-4h-4"/>',
};

export function icon(name, size = 22, stroke = 1.9) {
  const span = document.createElement('span');
  span.style.display = 'inline-grid';
  span.setAttribute('aria-hidden', 'true');
  span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">${P[name] || ''}</svg>`;
  return span;
}
