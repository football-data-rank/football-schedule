// Football Week：2回目以降に開いた時の「白い時間」をなくすためのサービスワーカー（2026-10-22）
// ・ページ本体(index.html)は、端末に保存した分をすぐ出す。
// ・ページが開いた後に「新しい版はある？」と聞いてくるので、そこで最新版を取りに行って保存分と比べ、
//   違っていれば保存し直して「新しい版あり」と返す（ページ側で、開いた直後なら1回だけ読み込み直す）。
//   ※ページを返した後の裏処理は、ブラウザに途中で止められることがあったため、問い合わせ方式にしている。
// ・Googleの文字(Webフォント)は一度取ったら端末の保存分を使う（毎回取りに行かない）。
// ・試合データ(ESPN)などそれ以外には一切関わらない。
const CACHE = 'fw-v1';

function pageKey(u) {
  const url = new URL(u);
  let path = url.pathname;
  if (path.endsWith('/')) path += 'index.html';
  return url.origin + path;   // ?lang= などのクエリは同じページとして扱う
}

self.addEventListener('install', e => {
  self.skipWaiting();
  // 初回の登録時にページ本体を保存しておく（次に開いた時から即表示）
  e.waitUntil((async () => {
    try {
      const url = new URL('./', self.registration.scope).href;
      const res = await fetch(url, { cache: 'no-cache' });
      if (res.ok) await (await caches.open(CACHE)).put(pageKey(url), res);
    } catch {}
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (req.mode === 'navigate' && url.origin === self.location.origin) {
    e.respondWith(servePage(req));
  } else if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    e.respondWith(cacheFirst(req));
  }
});

async function servePage(req) {
  const cache = await caches.open(CACHE);
  const key = pageKey(req.url);
  const cached = await cache.match(key);
  if (cached) return cached;
  try {
    const res = await fetch(key, { cache: 'no-cache', credentials: 'same-origin' });
    if (res.ok) await cache.put(key, res.clone());
    return res;
  } catch {
    return Response.error();
  }
}

// ページからの問い合わせ：最新版を取って保存分と比べる
self.addEventListener('message', e => {
  if (e.data?.type !== 'check-update' || !e.data.url) return;
  e.waitUntil((async () => {
    try {
      const cache = await caches.open(CACHE);
      const key = pageKey(e.data.url);
      const [res, cached] = await Promise.all([fetch(key, { cache: 'no-cache', credentials: 'same-origin' }), cache.match(key)]);
      if (!res.ok) return;
      const fresh = await res.clone().text();
      const old = cached ? await cached.text() : null;
      if (fresh === old) return;
      await cache.put(key, res);
      if (old !== null) e.source?.postMessage({ type: 'page-updated' });
    } catch {}
  })());
});

async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === 'opaque') cache.put(req, res.clone()).catch(() => {});
  return res;
}
