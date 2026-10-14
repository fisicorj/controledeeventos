// Comportamentos das páginas, ligados por atributos data-* (sem <script> nem onclick no HTML,
// o que permite a Content-Security-Policy bloquear qualquer script inline).
(() => {
  'use strict';

  // Botão "Copiar link" (página da atividade): no celular abre o compartilhamento do sistema.
  document.querySelectorAll('[data-copy]').forEach((button) => button.addEventListener('click', async () => {
    const url = button.dataset.copy;
    const label = button.querySelector('span') || button;
    if (navigator.share && matchMedia('(pointer: coarse)').matches) {
      try { await navigator.share({ title: document.title, url }); return; } catch (e) { /* cancelado */ }
    }
    try {
      await navigator.clipboard.writeText(url);
      const original = label.textContent;
      label.textContent = 'Link copiado!';
      setTimeout(() => { label.textContent = original; }, 2000);
    } catch (e) { window.prompt('Copie o link:', url); }
  }));

  // Copiar uma lista (códigos de recuperação).
  document.querySelectorAll('[data-copy-list]').forEach((button) => button.addEventListener('click', async () => {
    const items = document.querySelectorAll(button.dataset.copyList + ' li');
    try {
      await navigator.clipboard.writeText([...items].map((li) => li.textContent.trim()).join('\n'));
      button.textContent = 'Copiado';
    } catch (e) { /* o usuário copia à mão */ }
  }));

  // Atalhos de data ("hoje", "igual ao início") nos formulários de certificado.
  document.querySelectorAll('[data-to]').forEach((link) => link.addEventListener('click', (event) => {
    event.preventDefault();
    const target = document.querySelector(link.dataset.to);
    if (!target) return;
    if (link.dataset.copyDate) {
      const source = document.querySelector(link.dataset.copyDate);
      if (source && source.value) target.value = source.value;
    } else {
      const d = new Date();
      target.value = [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
    }
    target.dispatchEvent(new Event('change'));
  }));

  // Formulário que pede confirmação antes de enviar.
  document.querySelectorAll('form[data-confirm]').forEach((form) => form.addEventListener('submit', (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  }));

  // Filtro de linhas de uma tabela pelo texto digitado.
  document.querySelectorAll('[data-filter-rows]').forEach((input) => input.addEventListener('input', () => {
    const q = input.value.toLowerCase();
    document.querySelectorAll(input.dataset.filterRows).forEach((row) => {
      row.style.display = row.innerText.toLowerCase().includes(q) ? '' : 'none';
    });
  }));

  // Configurações > E-mail: ao trocar o tipo de segurança, sugere a porta padrão
  // (só se a porta atual for a padrão de outro tipo).
  const portDefaults = document.querySelector('[data-port-defaults]');
  if (portDefaults) {
    const ports = JSON.parse(portDefaults.dataset.portDefaults);
    const security = document.getElementById(portDefaults.dataset.security);
    const port = document.getElementById(portDefaults.dataset.port);
    if (security && port) security.addEventListener('change', () => {
      if (Object.values(ports).map(String).includes(port.value)) port.value = ports[security.value];
    });
  }

  // Configurações: prévia da cor e do nome do portal.
  const brand = document.querySelector('[data-brand-preview]');
  if (brand) {
    const color = document.getElementById(brand.dataset.color);
    const name = document.getElementById(brand.dataset.name);
    const preview = document.querySelector(brand.dataset.brandPreview);
    const contrast = (hex) => {
      const c = [1, 3, 5].map((i) => parseInt(hex.substr(i, 2), 16) / 255)
        .map((v) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)));
      return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] < 0.4 ? '#ffffff' : '#1c2533';
    };
    if (color && preview) color.addEventListener('input', () => {
      preview.style.setProperty('--brand', color.value);
      preview.style.setProperty('--brand-contrast', contrast(color.value));
    });
    const target = document.getElementById('pv-name');
    if (name && target) name.addEventListener('input', () => { target.textContent = name.value; });
  }

  // Ao sair: apaga deste aparelho os dados do check-in (lista de inscritos e telas guardadas offline).
  // Se ainda houver leituras sem enviar, pergunta antes, porque elas se perderiam.
  const checkinKeys = () => {
    try { return Object.keys(localStorage).filter((k) => k.startsWith('checkin:')); } catch (e) { return []; }
  };
  const unsent = () => checkinKeys().filter((k) => k.endsWith(':queue')).reduce((total, k) => {
    try { return total + (JSON.parse(localStorage.getItem(k)) || []).length; } catch (e) { return total; }
  }, 0);
  document.querySelectorAll('form[data-logout]').forEach((form) => form.addEventListener('submit', (event) => {
    const pending = unsent();
    if (pending && !window.confirm(`Há ${pending} leitura(s) de presença ainda não enviada(s) neste aparelho. `
      + 'Se sair agora, elas serão perdidas. Sair mesmo assim?')) {
      event.preventDefault();
      return;
    }
    checkinKeys().forEach((k) => { try { localStorage.removeItem(k); } catch (e) { /* ok */ } });
    if (window.caches) caches.keys().then((keys) => keys.filter((k) => k.startsWith('arena-checkin'))
      .forEach((k) => caches.delete(k))).catch(() => {});
  }));
})();
