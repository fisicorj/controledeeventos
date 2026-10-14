// Check-in no celular (PWA): lê o QR, guarda as leituras sem internet e envia quando a conexão volta.
(() => {
  const PREFIX = 'ARENA-EVENTS:';
  const config = document.getElementById('checkin-config').dataset;
  const URLS = { roster: config.rosterUrl, status: config.statusUrl, scan: config.scanUrl };
  const KEY = `checkin:${config.session}:`;
  const $ = (id) => document.getElementById(id);
  const store = {
    get(name, fallback) { try { return JSON.parse(localStorage.getItem(KEY + name)) ?? fallback; } catch (e) { return fallback; } },
    set(name, value) { try { localStorage.setItem(KEY + name, JSON.stringify(value)); } catch (e) { /* sem espaço */ } },
  };

  // Dados de outras sessões guardados há mais de 2 dias, sem leituras pendentes, saem do aparelho.
  try {
    const prefixes = new Set(Object.keys(localStorage).filter((k) => /^checkin:\d+:/.test(k))
      .map((k) => k.split(':').slice(0, 2).join(':') + ':'));
    prefixes.forEach((p) => {
      if (p === KEY) return;
      const queued = JSON.parse(localStorage.getItem(p + 'queue') || '[]');
      const saved = Number(localStorage.getItem(p + 'savedAt') || 0);
      if (!queued.length && Date.now() - saved > 2 * 864e5) {
        Object.keys(localStorage).filter((k) => k.startsWith(p)).forEach((k) => localStorage.removeItem(k));
      }
    });
  } catch (e) { /* armazenamento indisponível */ }

  let roster = store.get('roster', { people: [] });   // inscritos (guardados para funcionar offline)
  let queue = store.get('queue', []);                  // leituras ainda não enviadas
  let problems = store.get('problems', []);
  let serverPresent = new Set(store.get('present', []));
  let busy = false, lastText = '', lastAt = 0;

  const byKey = () => Object.fromEntries(roster.people.map((p) => [p.k, p]));
  const keyOf = (token) => token.replace(PREFIX, '').trim().replace(/-/g, '').slice(0, 12).toLowerCase();
  const queuedIds = () => new Set(queue.map((q) => q.id));

  function render() {
    const ids = new Set([...serverPresent, ...queuedIds()]);
    $('present').textContent = ids.size;
    $('total').textContent = roster.people.length;
    const pending = $('pending');
    pending.hidden = false;
    pending.querySelector('span').textContent = queue.length
      ? `${queue.length} leitura${queue.length > 1 ? 's' : ''} aguardando envio`
      : 'Tudo sincronizado';
    pending.classList.toggle('is-waiting', queue.length > 0);
    const box = $('problems');
    box.hidden = !problems.length;
    box.querySelector('summary span').textContent = problems.length;
    // Monta a lista como texto puro: nomes vêm do cadastro dos participantes e nunca viram HTML.
    const list = box.querySelector('ul');
    list.replaceChildren(...problems.map((p) => { const li = document.createElement('li'); li.textContent = p; return li; }));
  }

  function show(kind, text) {
    const el = $('result');
    el.className = 'checkin-msg is-' + kind;
    el.textContent = text;
    if (navigator.vibrate) navigator.vibrate(kind === 'ok' ? 60 : [80, 60, 80]);
  }

  function setOnline(on) {
    const net = $('net');
    net.className = 'net ' + (on ? 'net-on' : 'net-off');
    net.innerHTML = on ? '<i class="bi bi-wifi"></i><span>Online</span>' : '<i class="bi bi-wifi-off"></i><span>Offline</span>';
  }

  function csrf() {
    return document.cookie.split('; ').find((x) => x.startsWith('csrftoken='))?.split('=')[1] || '';
  }

  async function loadRoster() {
    try {
      const r = await fetch(URLS.roster, { credentials: 'same-origin', cache: 'no-store' });
      if (r.status === 403 || r.redirected) { show('error', 'Sua sessão expirou. Entre de novo no sistema.'); return; }
      const data = await r.json();
      roster = { people: data.people };
      serverPresent = new Set(data.people.filter((p) => p.present).map((p) => p.id));
      store.set('roster', roster);
      store.set('savedAt', Date.now());
      store.set('present', [...serverPresent]);
      setOnline(true);
    } catch (e) { setOnline(false); }
    render();
  }

  async function sync() {
    if (busy || !queue.length) return;
    busy = true;
    try {
      while (queue.length) {
        const item = queue[0];
        let r, data;
        try {
          r = await fetch(URLS.scan, {
            method: 'POST', credentials: 'same-origin', redirect: 'manual',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
            body: JSON.stringify({ token: item.token, scanned_at: item.at, offline: true }),
          });
        } catch (e) { setOnline(false); break; }       // sem internet: tenta de novo depois
        setOnline(true);
        // Sessão de login expirada (redireciona para o login) ou sem permissão: a leitura fica guardada.
        if (r.type === 'opaqueredirect' || r.redirected || r.status === 401 || r.status === 403) {
          show('error', 'Sua sessão expirou. Entre de novo no sistema: as leituras continuam guardadas neste aparelho.');
          break;
        }
        try { data = await r.json(); } catch (e) { data = null; }
        // Servidor instável (502, 504, página de erro): mantém na fila e tenta de novo depois.
        if (!data || r.status >= 500) { setOnline(false); break; }
        if (r.ok && data.ok) serverPresent.add(data.registration_id);
        else problems.push(`${item.name || 'QR'}: ${data.message || 'recusado'}`);  // recusa definitiva (400/404)
        queue.shift();
        store.set('queue', queue); store.set('problems', problems); store.set('present', [...serverPresent]);
        render();
      }
    } finally { busy = false; }
  }

  async function refreshStatus() {
    if (!navigator.onLine) { setOnline(false); return; }
    try {
      const r = await fetch(URLS.status, { credentials: 'same-origin', cache: 'no-store' });
      const data = await r.json();
      serverPresent = new Set(data.present);         // inclui as leituras de outros celulares
      store.set('present', [...serverPresent]);
      setOnline(true);
      render();
    } catch (e) { setOnline(false); }
  }

  function scanned(text) {
    const now = Date.now();
    if (text === lastText && now - lastAt < 3000) return;  // a câmera lê o mesmo QR várias vezes seguidas
    lastText = text; lastAt = now;
    if (!text.startsWith(PREFIX)) { show('error', 'QR Code de outra plataforma.'); return; }
    const person = byKey()[keyOf(text)];
    if (!person && roster.people.length) { show('error', 'Não é inscrito confirmado desta atividade.'); return; }
    const name = person ? person.name : 'Participante';
    if (person && (serverPresent.has(person.id) || queuedIds().has(person.id))) {
      show('warn', `${name}: presença já registrada.`); return;
    }
    queue.push({ token: text, id: person ? person.id : null, name, at: new Date().toISOString() });
    store.set('queue', queue);
    show('ok', `${name}: presença registrada${navigator.onLine ? '' : ' (será enviada quando voltar a internet)'}.`);
    render();
    sync();
  }

  window.addEventListener('online', () => { setOnline(true); sync(); refreshStatus(); });
  window.addEventListener('offline', () => setOnline(false));
  setInterval(() => { sync(); refreshStatus(); }, 8000);

  render();
  loadRoster().then(sync);
  // Câmera traseira direto, com botão em português (o leitor padrão da biblioteca só tem textos em inglês).
  const reader = new Html5Qrcode('reader');
  const button = $('camera');
  async function startCamera() {
    button.disabled = true;
    try {
      await reader.start({ facingMode: 'environment' }, { fps: 10, qrbox: { width: 240, height: 240 } }, scanned, () => {});
      button.hidden = true;
      try { localStorage.setItem('checkin:camera', '1'); } catch (e) { /* ok */ }
    } catch (e) {
      button.disabled = false;
      show('error', 'Não foi possível abrir a câmera. Permita o acesso à câmera nas configurações do navegador (o site precisa estar em HTTPS).');
    }
  }
  button.addEventListener('click', startCamera);
  try { if (localStorage.getItem('checkin:camera') === '1') startCamera(); } catch (e) { /* ok */ }
})();
