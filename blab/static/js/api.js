// 閲覧層の HTTP クライアント。
//
// サーバはディレクトリを読むだけで、書き込みは moveGroup（= blab mv）だけ。

async function request(path, { method = 'GET', body = null } = {}) {
  const options = { method, headers: {} };
  if (body !== null) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const doc = await res.json();
      if (doc && doc.detail) detail = doc.detail;
    } catch (e) {
      // JSON でない応答。ステータス行をそのまま使う。
    }
    throw new Error(detail);
  }
  return res.json();
}

export function encodePath(path) {
  return String(path || '')
    .split('/')
    .map(encodeURIComponent)
    .join('/');
}

export function fileUrl(path) {
  return `/files/${encodePath(path)}`;
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

export const api = {
  root: '',

  status: () => request('/api/status'),
  experiments: () => request('/api/experiments'),
  nodes: (params = {}) => request(`/api/nodes${query(params)}`),

  detail: (path) => request(`/api/nodes/${encodePath(path)}/detail`),
  metrics: (path, params = {}) => request(`/api/nodes/${encodePath(path)}/metrics${query(params)}`),
  runSource: (path, file) => request(`/api/nodes/${encodePath(path)}/source${query({ file })}`),
  log: (path, name, tail = 0) => request(`/api/nodes/${encodePath(path)}/log${query({ name, tail })}`),

  components: (params = {}) => request(`/api/components${query(params)}`),
  component: (id) => request(`/api/components/${encodeURIComponent(id)}`),
  componentFiles: (id, hash) =>
    request(`/api/components/${encodeURIComponent(id)}/files${query({ hash })}`),
  componentSource: (id, file, hash) =>
    request(`/api/components/${encodeURIComponent(id)}/source${query({ file, hash })}`),
  componentDiff: (id, a, b) =>
    request(`/api/components/${encodeURIComponent(id)}/diff${query({ a, b })}`),

  // UI が行う唯一の書き込み。blab mv と同じ操作を呼ぶ。
  moveGroup: (path, group) =>
    request(`/api/nodes/${encodePath(path)}/group`, { method: 'POST', body: { group } }),
};
