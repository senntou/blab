// インライン SVG アイコン。外部依存もフォントも持ち込まない。
// すべて 24x24 のストローク線画で、色は currentColor に従う。

const NS = 'http://www.w3.org/2000/svg';

const PATHS = {
  flask: 'M9 3h6M10 3v6.5L4.6 18A2 2 0 0 0 6.3 21h11.4a2 2 0 0 0 1.7-3L14 9.5V3M7.5 15h9',
  layers: 'M12 3 3 8l9 5 9-5-9-5ZM3 13l9 5 9-5M3 17.5l9 5 9-5',
  compare: 'M9 4v16M15 4v16M3 8l3-3 3 3M21 16l-3 3-3-3',
  table: 'M3 5h18v14H3zM3 10h18M9 10v9M15 10v9',
  chart: 'M4 4v16h16M7 15l3.5-4.5 3 2.5L20 7',
  image: 'M3 5h18v14H3zM3 16l5-5 4 4 3-3 6 6M15.5 8.5h.01',
  file: 'M6 3h7l5 5v13H6zM13 3v5h5',
  folder: 'M3 6h6l2 2h10v11H3z',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  check: 'M4 12.5 9 18 20 6',
  x: 'M6 6l12 12M18 6 6 18',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14ZM16 16l4.5 4.5',
  filter: 'M3 5h18l-7 8v6l-4 2v-8L3 5Z',
  columns: 'M4 5h16v14H4zM10 5v14M16 5v14',
  sliders: 'M4 8h10M18 8h2M4 16h4M12 16h8M14 5v6M8 13v6',
  chevronDown: 'M6 9l6 6 6-6',
  chevronRight: 'M9 6l6 6-6 6',
  arrowUp: 'M12 20V4M6 10l6-6 6 6',
  arrowDown: 'M12 4v16M6 14l6 6 6-6',
  sort: 'M8 4v16M4 8l4-4 4 4M16 20V4M12 16l4 4 4-4',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 7v5l3.5 2',
  play: 'M7 4.5v15l13-7.5z',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  refresh: 'M20 11a8 8 0 1 0-2 6M20 5v6h-6',
  hash: 'M5 9h14M5 15h14M10 4 8 20M17 4l-2 16',
  tag: 'M3 3h8l10 10-8 8L3 11zM7.5 7.5h.01',
  terminal: 'M5 4h14v16H5zM8.5 9.5l2.5 2.5-2.5 2.5M13 15h3',
  git: 'M6 3v12M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM18 9v2a4 4 0 0 1-4 4H9',
  cpu: 'M7 7h10v10H7zM4 10h3M4 14h3M17 10h3M17 14h3M10 4v3M14 4v3M10 17v3M14 17v3',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 11v6M12 7.5h.01',
  alert: 'M12 4 2.5 20h19zM12 10v4M12 17.5h.01',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  eye: 'M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12Zm10 2.6a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2Z',
  eyeOff: 'M4 4l16 16M9.9 5.7A9.9 9.9 0 0 1 12 5.5c6.5 0 10 6.5 10 6.5a17 17 0 0 1-3.3 4M6.4 8.1A16.6 16.6 0 0 0 2 12s3.5 6.5 10 6.5c1 0 2-.2 2.8-.4M9.9 9.9a2.6 2.6 0 0 0 3.6 3.6',
  zoom: 'M4 9V4h5M20 15v5h-5M15 4h5v5M9 20H4v-5',
  download: 'M12 3v12M7.5 10.5 12 15l4.5-4.5M4 20h16',
  sigma: 'M18 5H6l6 7-6 7h12',
  star: 'm12 3.5 2.6 5.5 6 .9-4.3 4.2 1 6-5.3-2.9-5.3 2.9 1-6L3.4 9.9l6-.9z',
  home: 'M4 11 12 4l8 7v9h-6v-6h-4v6H4z',
  edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z',
  trash: 'M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6',
  dot: 'M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z',
  // v2 で足したもの。component（箱）とコード、ラベル参照、移動。
  box: 'M12 2.8 21 7v10l-9 4.2L3 17V7l9-4.2ZM3 7l9 4.2L21 7M12 11.2V21',
  code: 'M8.5 8 4 12l4.5 4M15.5 8 20 12l-4.5 4M13.5 5l-3 14',
  link: 'M10 13a4 4 0 0 0 5.7.3l3-3A4 4 0 0 0 13 4.7l-1.7 1.7M14 11a4 4 0 0 0-5.7-.3l-3 3A4 4 0 0 0 11 19.3l1.7-1.7',
  move: 'M5 9V6a1 1 0 0 1 1-1h3M19 15v3a1 1 0 0 1-1 1h-3M9 19H6a1 1 0 0 1-1-1v-3M15 5h3a1 1 0 0 1 1 1v3M9 12h6M12 9l3 3-3 3',
  'chevron-left': 'M15 5l-7 7 7 7',
};

/**
 * @param {string} name PATHS のキー
 * @param {object} opts size / class
 */
export function icon(name, { size = 16, class: cls = '' } = {}) {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', `icon${cls ? ` ${cls}` : ''}`);
  const d = PATHS[name];
  if (d) {
    const p = document.createElementNS(NS, 'path');
    p.setAttribute('d', d);
    svg.append(p);
  }
  return svg;
}

/** 状態を表す小さな丸（badge の中に置く）。 */
export function statusDot() {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 8 8');
  svg.setAttribute('width', '8');
  svg.setAttribute('height', '8');
  svg.setAttribute('class', 'status-dot');
  const c = document.createElementNS(NS, 'circle');
  c.setAttribute('cx', '4');
  c.setAttribute('cy', '4');
  c.setAttribute('r', '4');
  c.setAttribute('fill', 'currentColor');
  svg.append(c);
  return svg;
}
