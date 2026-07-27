/* Intercepta todos os <form method="post"> do painel e os envia por fetch,
   sem tirar o usuário da página. Sucesso (redirect 303 ou 2xx) → recarrega;
   erro → toast com o detail do servidor, preservando os valores digitados.
   Um form pode optar por submissão nativa com o atributo data-nativo. */

import { toast } from './toast.js';
import { validarForm } from './validar.js';

function detalheErro(d, status) {
  if (d && typeof d.detail === 'string') return d.detail;
  if (d && d.detail && typeof d.detail.msg === 'string') return d.detail.msg;
  if (d && typeof d.erro === 'string') return d.erro;
  return 'Não foi possível concluir a ação (HTTP ' + status + ').';
}

async function enviar(form, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      redirect: 'manual',                 // 303 vira opaqueredirect (= sucesso)
      headers: { 'Accept': 'application/json' },
    });
    if (r.type === 'opaqueredirect' || r.ok) {
      location.reload();
      return;
    }
    let d = null;
    try { d = await r.json(); } catch (_) { /* corpo não-JSON */ }
    toast('erro', detalheErro(d, r.status));
  } catch (_) {
    toast('erro', 'Falha de conexão com o servidor. Confira a rede e tente de novo.');
  }
  if (btn) btn.disabled = false;
}

document.addEventListener('submit', (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement)) return;
  if ((form.method || '').toLowerCase() !== 'post') return;
  if (form.hasAttribute('data-nativo')) return;   // opt-out explícito
  if (e.defaultPrevented) return;                  // confirm() nativo abortou o envio
  e.preventDefault();
  if (!validarForm(form)) return;
  const btn = e.submitter || form.querySelector('[type="submit"], button:not([type="button"])');
  enviar(form, btn);
});
