// Registra o service worker do check-in (funciona sem internet). Endereços vêm dos atributos data-*.
(() => {
  const script = document.currentScript;
  if (!script || !('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register(script.dataset.sw, { scope: script.dataset.scope }).catch(() => {});
})();
