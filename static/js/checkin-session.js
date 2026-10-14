// Leitor de QR da tela de presença da sessão (computador da organização).
(() => {
  const reader = document.getElementById('reader');
  if (!reader || !window.Html5QrcodeScanner) return;
  const result = document.getElementById('scan-result');
  const counter = document.getElementById('present-count');
  let locked = false;

  function csrf() {
    return document.cookie.split('; ').find(x => x.startsWith('csrftoken='))?.split('=')[1] || '';
  }

  function show(ok, text) {
    result.className = 'alert mt-3 ' + (ok ? 'alert-success' : 'alert-danger');
    result.textContent = text;
  }

  // Marca o participante como presente na tabela sem recarregar a página (a câmera continua ativa).
  function markPresent(registrationId) {
    const cell = document.getElementById('status-' + registrationId);
    if (cell) cell.innerHTML = '<span class="pill pill-open"><i class="bi bi-check2" aria-hidden="true"></i>Presente</span>';
  }

  function scanned(text) {
    if (locked) return;
    locked = true;
    fetch(reader.dataset.scanUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
      body: JSON.stringify({token: text}),
    })
      .then(r => r.json())
      .then(d => {
        show(d.ok, (d.name ? d.name + ': ' : '') + d.message);
        if (d.ok) {
          markPresent(d.registration_id);
          counter.textContent = d.present_count;
        }
      })
      .catch(() => show(false, 'Erro ao registrar presença. Verifique a conexão.'))
      .finally(() => setTimeout(() => { locked = false; }, 1500));
  }

  new Html5QrcodeScanner('reader', {fps: 10, qrbox: {width: 240, height: 240}}, false).render(scanned);
})();
