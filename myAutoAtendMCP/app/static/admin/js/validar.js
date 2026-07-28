/* Validação de formato client-side. Campos marcados com data-valida rodam
   antes do submit (ver forms.js): erro → borda vermelha + recado abaixo do
   campo + foco. Vazio passa — o atributo `required` cuida da obrigatoriedade. */

import { toast } from './toast.js';

const VALIDADORES = {
  // Telefone de WhatsApp. O backend normaliza p/ E.164 (aceita nº nacional
  // pela região BR), então só barramos formato claramente inválido.
  telefone(valor) {
    const limpo = valor.trim();
    if (!limpo) return '';
    if (!/^[\d+\s()\-]+$/.test(limpo))
      return 'Telefone com caracteres inválidos. Use só números, espaços e + ( ) -.';
    const digitos = limpo.replace(/\D/g, '');
    if (digitos.length < 10)
      return 'Telefone incompleto. Inclua DDD e número — de preferência com o país 55 (ex.: 55 45 99999-8888).';
    return '';
  },
  // Hora no formato HH:MM (00:00–23:59).
  hora(valor) {
    const v = valor.trim();
    if (!v) return '';
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(v))
      return 'Hora inválida. Use o formato HH:MM (ex.: 08:30).';
    return '';
  },
};

function marcarErro(inp, texto) {
  inp.classList.add('campo-erro');
  inp.setAttribute('aria-invalid', 'true');
  let el = inp._erroEl;
  if (!el) {
    el = document.createElement('p');
    el.className = 'campo-erro-msg';
    inp.insertAdjacentElement('afterend', el);
    inp._erroEl = el;
    inp.addEventListener('input', () => limparErro(inp), { once: true });  // limpa ao corrigir
  }
  el.textContent = texto;
}

function limparErro(inp) {
  inp.classList.remove('campo-erro');
  inp.removeAttribute('aria-invalid');
  if (inp._erroEl) { inp._erroEl.remove(); inp._erroEl = null; }
}

/* Valida todos os campos data-valida do form. Retorna true se pode seguir. */
export function validarForm(form) {
  let primeiro = null;
  form.querySelectorAll('[data-valida]').forEach(inp => {
    limparErro(inp);
    const fn = VALIDADORES[inp.dataset.valida];
    if (!fn) return;
    const erro = fn(inp.value);
    if (erro) {
      marcarErro(inp, erro);
      if (!primeiro) primeiro = inp;
    }
  });
  if (primeiro) {
    primeiro.focus();
    toast('erro', 'Confira os campos destacados antes de continuar.');
    return false;
  }
  return true;
}
