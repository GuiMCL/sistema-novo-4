/* Card "Lembretes": gera com IA o texto do lembrete a partir do modelo que o
   dono escreveu, mantendo as variaveis {nome}, {servico}, {data}, {hora}
   para serem preenchidas automaticamente no envio. */

const $ = (id) => document.getElementById(id);

async function postar(url, dados){
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams(dados),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.erro || d.detail || ('HTTP ' + r.status));
  return d;
}

function status(estagio, texto, ok){
  const el = $('lembrete-msg-status-' + estagio);
  if (!el) return;
  el.textContent = texto;
  el.className = 'ia-msg ' + (ok ? 'ok' : 'err');
  el.style.display = '';
}

async function gerar(estagio, btn){
  const area = $('lembrete-msg-' + estagio);
  if (!area) return;
  btn.disabled = true;
  status(estagio, 'Gerando mensagem com IA…', true);
  try {
    const d = await postar('/admin/lembrete/gerar', {
      mensagem: area.value,
      estagio,
    });
    area.value = d.mensagem || '';
    status(estagio, 'Pronto! Ajuste se quiser e salve a configuracao de lembretes.', true);
  } catch(e){
    status(estagio, 'Falha: ' + e.message, false);
  } finally {
    btn.disabled = false;
  }
}

document.querySelectorAll('[data-lembrete-gerar]').forEach(btn =>
  btn.addEventListener('click', () => gerar(parseInt(btn.dataset.lembreteGerar, 10), btn)));
