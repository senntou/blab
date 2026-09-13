// ブラウザ無しで描画モジュールを動かすための最小 DOM。
// config-tree.js / table.js が使う API だけを実装する。

class Node {
  constructor(tag) {
    this.tagName = String(tag || '').toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this._text = '';
    this.className = '';
    this.nodeType = 1;
  }
  append(...kids) {
    for (const k of kids) {
      if (k === null || k === undefined || k === false) continue;
      this.children.push(k);
    }
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeChild(k) { this.children = this.children.filter((c) => c !== k); }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() {
    if (this._text) return this._text;
    return this.children.map((c) => (c.nodeType === 3 ? c.data : c.textContent)).join('');
  }
  set innerHTML(v) { this._html = v; this._text = ''; }
  get innerHTML() { return this._html || ''; }
  get classList() {
    const self = this;
    return {
      toggle(name, on) {
        const set = new Set(String(self.className).split(/\s+/).filter(Boolean));
        if (on) set.add(name); else set.delete(name);
        self.className = [...set].join(' ');
      },
      add(name) { this.toggle(name, true); },
      contains(name) { return String(self.className).split(/\s+/).includes(name); },
    };
  }
  querySelectorAll() { return []; }
}

class TextNode {
  constructor(data) { this.data = String(data); this.nodeType = 3; }
  get textContent() { return this.data; }
}

globalThis.document = {
  createElement: (tag) => new Node(tag),
  createElementNS: (_ns, tag) => new Node(tag),
  createTextNode: (t) => new TextNode(t),
  getElementById: () => new Node('div'),
  querySelectorAll: () => [],
  addEventListener: () => {},
  hidden: false,
};

const store = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, v),
  },
  addEventListener: () => {},
};

/** 描画結果を、検査しやすいプレーンな木にする。 */
export function dump(node) {
  if (!node) return null;
  if (node.nodeType === 3) return node.data;
  return {
    tag: node.tagName.toLowerCase(),
    class: node.className || undefined,
    text: node._text || undefined,
    attrs: Object.keys(node.attributes).length ? node.attributes : undefined,
    children: node.children.map(dump).filter((c) => c !== null),
  };
}

/** 木を平らな文字列にして、含まれるかどうかを見る。 */
export function flatten(node, out = []) {
  if (!node) return out;
  if (node.nodeType === 3) { out.push(node.data); return out; }
  if (node.className) out.push(`.${node.className.split(/\s+/).join('.')}`);
  if (node._text) out.push(node._text);
  for (const child of node.children) flatten(child, out);
  return out;
}
