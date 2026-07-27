/* Gerenciamento de instancias WhatsApp - QR Code inline, estado, perfil */

import { toast } from './toast.js';

const POLL_MS = 4000;
const polls = {};

function el(id) { return document.getElementById(id); }

function statusPill(instId, state, perfil) {
  const pill = el('inst-status-' + instId);
  if (!pill) return;
  if (state === 'open') {
    pill.textContent = 'Conectado';
    pill.className = 'pill on';
  } else if (state === 'connecting') {
    pill.textContent = 'Conectando';
    pill.className = 'pill connecting';
  } else {
    pill.textContent = 'Desconectado';
    pill.className = 'pill off';
  }
}

function showProfile(instId, perfil) {
  const ava = el('inst-avatar-' + instId);
  const nome = el('inst-pnome-' + instId);
  const num = el('inst-pnum-' + instId);
  const box = el('inst-perfil-' + instId);
  if (!box) return;
  if (!perfil || !perfil.numero) {
    box.style.display = 'none';
    return;
  }
  box.style.display = 'flex';
  if (ava) ava.textContent = (perfil.nome || 'W').trim().charAt(0).toUpperCase();
  if (nome) nome.textContent = perfil.nome || 'WhatsApp conectado';
  if (num) num.textContent = perfil.numero_fmt || perfil.numero;
  if (perfil.foto && ava) {
    ava.style.backgroundImage = "url('" + perfil.foto + "')";
    ava.classList.add('loaded');
  }
}

function showQr(instId, b64, pairingCode) {
  const img = el('inst-qr-img-' + instId);
  const code = el('inst-qr-code-' + instId);
  const hint = el('inst-qr-hint-' + instId);
  if (!img) return;
  if (b64) {
    img.src = b64.startsWith('data:') ? b64 : ('data:image/png;base64,' + b64);
    img.style.display = '';
    if (hint) hint.style.display = 'none';
  }
  if (code && pairingCode) {
    code.textContent = pairingCode;
    code.style.display = '';
  }
}

function hideQr(instId) {
  const img = el('inst-qr-img-' + instId);
  const code = el('inst-qr-code-' + instId);
  if (img) { img.style.display = 'none'; img.src = ''; }
  if (code) { code.style.display = 'none'; code.textContent = ''; }
}

function showQrArea(instId, show) {
  const area = el('inst-qr-area-' + instId);
  if (area) area.style.display = show ? '' : 'none';
}

async function fetchEstado(instId) {
  try {
    const r = await fetch('/admin/instancia/' + instId + '/estado');
    const d = await r.json();
    if (d.erro) {
      statusPill(instId, 'close', null);
      return null;
    }
    const st = (d.instance && d.instance.state) || 'close';
    statusPill(instId, st, d.perfil);
    if (st === 'open') {
      showProfile(instId, d.perfil);
      showQrArea(instId, false);
      if (polls[instId]) {
        clearInterval(polls[instId]);
        delete polls[instId];
      }
    }
    return st;
  } catch(e) {
    statusPill(instId, 'close', null);
    return null;
  }
}

function startPoll(instId) {
  if (polls[instId]) clearInterval(polls[instId]);
  polls[instId] = setInterval(() => fetchEstado(instId), POLL_MS);
}

async function instanciaQR(instId) {
  const btn = event.target;
  btn.disabled = true;
  const hint = el('inst-qr-hint-' + instId);
  if (hint) { hint.textContent = 'Gerando QR Code...'; hint.style.display = ''; }
  showQrArea(instId, true);
  try {
    const r = await fetch('/admin/instancia/' + instId + '/qr');
    const d = await r.json();
    if (d.erro) {
      if (hint) hint.textContent = 'Falha: ' + d.erro;
      return;
    }
    if (d.instance && d.instance.state === 'open') {
      fetchEstado(instId);
      return;
    }
    const b64 = d.base64 || '';
    if (b64) {
      showQr(instId, b64, d.pairingCode);
      statusPill(instId, 'connecting', null);
      startPoll(instId);
    } else {
      if (hint) hint.textContent = 'Resposta sem QR. Tente novamente.';
    }
  } catch(e) {
    if (hint) hint.textContent = 'Erro ao gerar QR Code.';
  } finally {
    btn.disabled = false;
  }
}

async function instanciaDesconectar(instId) {
  if (!confirm('Desconectar o WhatsApp desta instancia?')) return;
  try {
    const r = await fetch('/admin/instancia/' + instId + '/desconectar', { method: 'POST' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
  } catch(e) {
    toast('erro', 'Nao foi possivel desconectar agora.');
  }
  hideQr(instId);
  showQrArea(instId, false);
  fetchEstado(instId);
}

async function instanciaEstado(instId) {
  await fetchEstado(instId);
}

window.instanciaQR = instanciaQR;
window.instanciaDesconectar = instanciaDesconectar;
window.instanciaEstado = instanciaEstado;

// Inicializa estado de todas as instancias ao carregar
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-inst-id]').forEach(el => {
    const id = parseInt(el.dataset.instId);
    if (!isNaN(id)) fetchEstado(id);
  });
});