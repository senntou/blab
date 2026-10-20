// 閲覧層の HTTP クライアント。
//
// サーバはディレクトリを読むだけで、書き込みは moveGroup（= blab mv）だけ。
//
// どのプロジェクトを読むかはページの URL（`?p=<project_uid>`）で決まり、API はすべて
// `/api/p/<uid>/...` の下にある。タブごとに別のプロジェクトを開ける。

let projectUid = null;
let rootCache = null;

/** 以後の API 呼び出しが読むプロジェクトを決める（起動時に 1 回）。 */
export function setProject(uid) {
  projectUid = uid;
  rootCache = null;
}

export function currentProject() {
  return projectUid;
}

function scoped(path) {
  if (!projectUid) throw new Error('プロジェクトが選ばれていません');
  return `/api/p/${encodeURIComponent(projectUid)}${path}`;
}

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

export function fileUrl(runPath, artifactPath = null) {
  const full = artifactPath ? `${runPath}/${artifactPath}` : runPath;
  return `/files/${encodeURIComponent(projectUid || '')}/${encodePath(full)}`;
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
  projects: () => request('/api/projects'),

  status: () => request(scoped('/status')),
  // ログルートの絶対パス（パスのコピー用）。プロジェクトはページ内で変わらないので 1 回だけ聞く。
  root: async () => {
    if (rootCache === null) rootCache = api.status().then((s) => s.runs_dir);
    return rootCache;
  },
  experiments: () => request(scoped('/experiments')),
  trash: () => request(scoped('/trash')),
  nodes: (params = {}) => request(scoped(`/nodes${query(params)}`)),

  detail: (path) => request(scoped(`/nodes/${encodePath(path)}/detail`)),
  metrics: (path, params = {}) => request(scoped(`/nodes/${encodePath(path)}/metrics${query(params)}`)),
  runSource: (path, file) => request(scoped(`/nodes/${encodePath(path)}/source${query({ file })}`)),
  experimentSource: (path) => request(scoped(`/nodes/${encodePath(path)}/experiment-source`)),
  docs: (path) => request(scoped(`/nodes/${encodePath(path)}/docs`)),
  doc: (path, name) => request(scoped(`/nodes/${encodePath(path)}/doc${query({ name })}`)),
  log: (path, name, tail = 0) => request(scoped(`/nodes/${encodePath(path)}/log${query({ name, tail })}`)),

  components: (params = {}) => request(scoped(`/components${query(params)}`)),
  component: (id) => request(scoped(`/components/${encodeURIComponent(id)}`)),
  // README だけ。component() は全 run の逆引きをするので、run ページからはこちらを使う。
  componentReadme: (id) => request(scoped(`/components/${encodeURIComponent(id)}/readme`)),
  componentFiles: (id, hash) =>
    request(scoped(`/components/${encodeURIComponent(id)}/files${query({ hash })}`)),
  componentSource: (id, file, hash) =>
    request(scoped(`/components/${encodeURIComponent(id)}/source${query({ file, hash })}`)),
  componentDiff: (id, a, b) =>
    request(scoped(`/components/${encodeURIComponent(id)}/diff${query({ a, b })}`)),

  // UI が行う唯一の書き込み。blab mv と同じ操作を呼ぶ。
  moveGroup: (path, group) =>
    request(scoped(`/nodes/${encodePath(path)}/group`), { method: 'POST', body: { group } }),
};
