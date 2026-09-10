// サーバ API の薄いラッパ。閲覧層は読み取りしかしないので GET しかない。

async function get(url) {
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (e) {
      /* JSON でないエラー本文は無視する */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

async function send(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch (e) {
      /* JSON でないエラー本文は無視する */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

let rootPromise = null;

export const api = {
  status: () => get('/api/status'),
  // ルートの絶対パスは実行中変わらないので一度だけ取得してキャッシュする。
  root: () => {
    if (!rootPromise) rootPromise = get('/api/status').then((s) => s.root);
    return rootPromise;
  },
  tree: () => get('/api/tree'),
  nodes: (experiment, kind) => {
    const q = new URLSearchParams();
    if (experiment) q.set('experiment', experiment);
    if (kind) q.set('kind', kind);
    const qs = q.toString();
    return get(`/api/nodes${qs ? `?${qs}` : ''}`);
  },
  run: (path) => get(`/api/runs/${encodePath(path)}`),
  metrics: (path, keys, maxPoints = 2000) => {
    const q = new URLSearchParams();
    if (keys && keys.length) q.set('keys', keys.join(','));
    q.set('max_points', String(maxPoints));
    return get(`/api/runs/${encodePath(path)}/metrics?${q}`);
  },
  artifacts: (path) => get(`/api/runs/${encodePath(path)}/artifacts`),
  // ★ blab: component と、run に snapshot されたソース
  components: (tags) => get(`/api/components${tags && tags.length ? `?tags=${encodeURIComponent(tags.join(','))}` : ''}`),
  component: (id) => get(`/api/components/${encodeURIComponent(id)}`),
  componentSource: (id, version) => {
    const q = version ? `?version=${encodeURIComponent(version)}` : '';
    return get(`/api/components/${encodeURIComponent(id)}/source${q}`);
  },
  componentDiff: (id, a, b) =>
    get(`/api/components/${encodeURIComponent(id)}/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  componentRuns: (id) => get(`/api/components/${encodeURIComponent(id)}/runs`),
  code: (path, name) => get(`/api/runs/${encodePath(path)}/code/${encodePath(name)}`),
  aggregate: (path) => get(`/api/groups/${encodePath(path)}/aggregate`),
  renameNode: (path, name) => send('PATCH', `/api/nodes/${encodePath(path)}`, { name }),
  deleteNode: (path) => send('DELETE', `/api/nodes/${encodePath(path)}`),
};

export function encodePath(path) {
  return String(path)
    .split('/')
    .map(encodeURIComponent)
    .join('/');
}

export function fileUrl(runPath, relPath) {
  return `/files/${encodePath(runPath)}/${encodePath(relPath)}`;
}
