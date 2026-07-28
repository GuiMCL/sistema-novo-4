let qrPollTimer = null;
let qrSessaoId = null;

async function verificarEstado(id) {
  const tr = document.querySelector(`#tbl-sessoes tbody tr[data-id="${id}"]`);
  if (!tr) return;
  const pill = tr.querySelector('.status-pill');
  const btnConectar = tr.querySelector('[data-action="conectar"]');
  const btnDesconectar = tr.querySelector('[data-action="desconectar"]');
  try {
    const res = await fetch(`/admin/quote/api/sessoes/${id}/estado`);
    const est = await res.json();
    const state = (est.instance || {}).state || (est.erro ? 'disconnected' : 'unknown');
    if (state === 'open') {
      pill.className = 'pill on status-pill';
      pill.textContent = 'Conectado';
      btnConectar.style.display = 'none';
      btnDesconectar.style.display = '';
    } else {
      pill.className = 'pill off status-pill';
      pill.textContent = 'Desconectado';
      btnConectar.style.display = '';
      btnDesconectar.style.display = 'none';
    }
  } catch (err) {
    pill.className = 'pill off status-pill';
    pill.textContent = 'Erro';
    btnConectar.style.display = 'none';
    btnDesconectar.style.display = 'none';
  }
}

function abrirModalSessao() {
  document.getElementById('sessao-nome').value = '';
  document.getElementById('sessao-numero').value = '';
  document.getElementById('modal-sessao').classList.add('open');
  document.body.classList.add('agm-aberto');
}

function fecharModalSessao() {
  document.getElementById('modal-sessao').classList.remove('open');
  document.body.classList.remove('agm-aberto');
}

async function salvarSessao(e) {
  e.preventDefault();
  const form = document.getElementById('form-sessao');
  const data = new FormData(form);
  try {
    const res = await fetch('/admin/quote/api/sessoes', { method: 'POST', body: data });
    if (!res.ok) throw new Error(await res.text());
    fecharModalSessao();
    location.reload();
  } catch (err) {
    alert('Erro ao criar: ' + err.message);
  }
  return false;
}

function fecharModalQR() {
  document.getElementById('modal-qr').classList.remove('open');
  document.body.classList.remove('agm-aberto');
  if (qrPollTimer) { clearInterval(qrPollTimer); qrPollTimer = null; }
  qrSessaoId = null;
}

async function conectarSessao(id) {
  qrSessaoId = id;
  document.getElementById('modal-qr').classList.add('open');
  document.body.classList.add('agm-aberto');
  document.getElementById('qr-code-container').innerHTML = '<p class="empty" id="qr-status">Gerando QR Code...</p>';
  await buscarQR(id);
  if (qrPollTimer) { clearInterval(qrPollTimer); }
  qrPollTimer = setInterval(async () => {
    if (qrSessaoId) await buscarQR(qrSessaoId);
  }, 3000);
}

async function buscarQR(id) {
  const container = document.getElementById('qr-code-container');
  try {
    const res = await fetch(`/admin/quote/api/sessoes/${id}/qr`);
    const data = await res.json();
    if (data.erro) {
      container.innerHTML = `<p class="empty">Erro: ${data.erro}</p>`;
      return;
    }
    const state = (data.instance || {}).state;
    if (state === 'open') {
      container.innerHTML = '<p style="color:var(--success);font-weight:600;">✓ Conectado!</p>';
      if (qrPollTimer) { clearInterval(qrPollTimer); qrPollTimer = null; }
      verificarEstado(id);
      setTimeout(() => fecharModalQR(), 1500);
      return;
    }
    const b64 = data.base64 || '';
    if (b64) {
      const src = b64.startsWith('data:') ? b64 : 'data:image/png;base64,' + b64;
      const pairing = data.pairingCode || data.code || '';
      container.innerHTML = `<img src="${src}" alt="QR Code" style="width:260px;height:260px;image-rendering:pixelated;">` +
        (pairing ? `<p style="font-size:12px;color:var(--ink-soft);margin-top:8px;">Código: ${pairing}</p>` : '');
    } else {
      container.innerHTML = '<p class="empty">QR Code expirado. Clique em Conectar novamente.</p>';
    }
  } catch (err) {
    container.innerHTML = `<p class="empty">Erro: ${err.message}</p>`;
  }
}

async function desconectarSessao(id) {
  if (!confirm('Desconectar esta sessão do WhatsApp?')) return;
  try {
    const res = await fetch(`/admin/quote/api/sessoes/${id}/desconectar`, { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    verificarEstado(id);
  } catch (err) {
    alert('Erro ao desconectar: ' + err.message);
  }
}

async function excluirSessao(id) {
  if (!confirm('Excluir esta sessão? O pareamento WhatsApp será perdido.')) return;
  try {
    const res = await fetch(`/admin/quote/api/sessoes/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  } catch (err) {
    alert('Erro ao excluir: ' + err.message);
  }
}

document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('#tbl-sessoes tbody tr[data-id]').forEach(tr => {
    const id = tr.dataset.id;
    verificarEstado(id);
  });
});

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') { fecharModalSessao(); fecharModalQR(); }
});

window.abrirModalSessao = abrirModalSessao;
window.fecharModalSessao = fecharModalSessao;
window.salvarSessao = salvarSessao;
window.conectarSessao = conectarSessao;
window.desconectarSessao = desconectarSessao;
window.excluirSessao = excluirSessao;
window.fecharModalQR = fecharModalQR;