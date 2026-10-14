{% load static %}// Service worker do check-in: guarda a tela e os arquivos para abrir mesmo sem internet.
// v2: deixou de guardar a tela de presença da organização (tem e-mails); v3: Bootstrap servido pelo portal
const CACHE = 'arena-checkin-v3';
const ASSETS = [
  '{% url "checkin_home" %}',
  '{% static "css/app.css" %}',
  '{% static "vendor/html5-qrcode/html5-qrcode.min.js" %}',
  '{% static "js/app.js" %}',
  '{% static "js/checkin-app.js" %}',
  '{% static "js/checkin-sw.js" %}',
  '{% static "vendor/public-sans/public-sans.css" %}',
  '{% static "vendor/bootstrap-icons/bootstrap-icons.min.css" %}',
  '{% static "pwa/icon-192.png" %}',
  '{% static "vendor/bootstrap/bootstrap.min.css" %}',
];

const HOME = '{% url "checkin_home" %}';
const OFFLINE_PAGES = new RegExp('^' + HOME.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(session/\\d+/app/)?$');

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => Promise.allSettled(ASSETS.map((url) => cache.add(url)))));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;                 // leituras (POST) são guardadas pela própria página
  const url = new URL(request.url);
  if (url.pathname.endsWith('.json')) return;            // dados sempre da rede; a página tem cópia própria
  const isPage = request.mode === 'navigate';
  if (isPage) {
    // Tela do app: tenta a rede (para pegar a versão nova) e cai no cache sem internet.
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Só as telas do aplicativo (lista de sessões e leitor), que não mostram e-mails.
          if (response.ok && OFFLINE_PAGES.test(url.pathname)) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then((hit) => hit || caches.match('{% url "checkin_home" %}')))
    );
    return;
  }
  // Arquivos estáticos e fontes: cache primeiro.
  event.respondWith(
    caches.match(request).then((hit) => hit || fetch(request).then((response) => {
      if (response.ok || response.type === 'opaque') {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy));
      }
      return response;
    }))
  );
});
