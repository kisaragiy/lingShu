// 灵枢调研助手 — Service Worker
// 职责边界：只做「应用壳」离线兜底与静态资源加速，绝不缓存 API。
// 历史坑：上一版预缓存的是客服 demo 页面（旧客服路径），且对所有请求缓存优先，
// 主应用从未注册它 —— 等于装了没用，还可能把 /v1/ 响应读脏。

const CACHE_NAME = 'lingShu-research-v2';

// 只放本应用真实存在的资源：install 时逐个 add，单个 404 不会让整个安装失败。
const PRECACHE_URLS = [
  '/',
  '/static/css/lingShu.css',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/js/api.js',
  '/static/js/ui.js',
  '/static/js/chat.js',
  '/static/js/sidebar.js',
  '/static/js/tabs.js',
  '/static/js/safety-ui.js',
  '/static/js/orchestrator.js',
];

// 这些路径永不进缓存：接口数据必须实时（脏读比离线不可用更糟）。
const NEVER_CACHE_PREFIXES = ['/v1/', '/ws/', '/docs', '/openapi.json'];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await Promise.allSettled(
      PRECACHE_URLS.map((url) => cache.add(new Request(url, { cache: 'reload' })))
    );
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    // 清掉旧版本（含客服时代的 lingShu-cs-v1）
    await Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

function _isCacheable(resp) {
  return !!resp && resp.status === 200 && resp.type === 'basic';
}

async function _put(request, resp) {
  if (!_isCacheable(resp)) return;
  const cache = await caches.open(CACHE_NAME);
  await cache.put(request, resp);
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;          // 第三方（GitHub 版本检查等）直连
  if (NEVER_CACHE_PREFIXES.some((p) => url.pathname.startsWith(p))) return;

  // ① 页面导航：网络优先。避免用户拿到旧版页面（HTML 里有每请求注入的
  //    __NEEDS_ADMIN__ / __VERSION__，缓存优先会把它冻住）；离线才回退缓存壳。
  if (req.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const resp = await fetch(req);
        if (_isCacheable(resp)) {
          const copy = resp.clone();
          // 只留一份壳，路径归一化到 '/'，防止 URL 变体把缓存撑爆。
          // 这里 await 而不是 waitUntil：put 挂在 respondWith 的 promise 上才不会被回收。
          await _put('/', copy);
        }
        return resp;
      } catch (err) {
        const cache = await caches.open(CACHE_NAME);
        const cached = (await cache.match(req)) || (await cache.match('/'));
        return cached || Response.error();
      }
    })());
    return;
  }

  // ② 静态资源：缓存优先 + 后台更新（stale-while-revalidate）
  // waitUntil 必须在 dispatch 同步阶段调用（异步回调里再调会抛 InvalidStateError），
  // 所以这里先把 network/respondWith 两个 promise 建好，再同步登记。
  const cachedPromise = caches.open(CACHE_NAME).then((c) => c.match(req));
  const networkPromise = fetch(req).then(async (resp) => {
    if (_isCacheable(resp)) await _put(req, resp.clone());
    return resp;
  }).catch(() => null);

  event.respondWith(
    cachedPromise.then((cached) => cached || networkPromise).then((r) => r || Response.error())
  );
  event.waitUntil(networkPromise);
});
