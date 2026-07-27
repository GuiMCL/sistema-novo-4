/* Campos de telefone (input[data-telefone]): máscara BR progressiva enquanto
   digita + checagem de cortesia "esse número tem WhatsApp?" no blur/pausa.
   Reutilizável — hoje no modal de agendamento e no telefone do dono (config).

   O que trafega no submit é sempre dígitos E.164: quando a Evolution confirma
   o número, guardamos o jid canônico em dataset.canon (resolve o nono dígito);
   caso contrário, mandamos os próprios dígitos digitados. A normalização é
   feita num listener de submit em CAPTURE, então roda antes do forms.js e do
   módulo do modal — os dois leem o FormData já com os dígitos limpos. */

const DEBOUNCE_MS = 600;

function soDigitos(v) {
  return (v || '').replace(/\D/g, '');
}

// Formata os dígitos de um número nacional BR: "+55 (45) 99999-0000".
function mascararBR(national) {
  const d = national.slice(0, 11);
  const ddd = d.slice(0, 2);
  const resto = d.slice(2);
  let out = '+55';
  if (ddd) out += ' (' + ddd + (ddd.length === 2 ? ')' : '');
  if (resto) {
    // Móvel (9 díg.) parte 5+4; fixo (8 díg.) parte 4+4 — corte fixo em 4 no fim.
    const meio = resto.length > 4 ? resto.slice(0, resto.length - 4) : resto;
    const fim = resto.length > 4 ? resto.slice(resto.length - 4) : '';
    out += ' ' + meio + (fim ? '-' + fim : '');
  }
  return out;
}

// Texto exibido no campo a partir do que o usuário digitou. Sem DDI assume 55;
// com "+" e DDI diferente de 55, não força BR — só agrupa os dígitos.
function formatar(valor) {
  const teveMais = (valor || '').trim().startsWith('+');
  const d = soDigitos(valor);
  if (!d) return teveMais ? '+' : '';
  if (teveMais && !d.startsWith('55')) return '+' + d;
  let national = d;
  if (d.startsWith('55') && (d.length >= 12 || teveMais)) national = d.slice(2);
  return mascararBR(national);
}

// ---------------------------------------------------------------------------
// Badge de resultado (pill + avatar) ao lado do campo
// ---------------------------------------------------------------------------

function limparBadge(inp) {
  if (inp._telBadge) {
    inp._telBadge.remove();
    inp._telBadge = null;
  }
}

function inicialDe(inp) {
  // Fallback do avatar: inicial do nome do cliente, se houver campo no form.
  const nome = inp.form?.querySelector('[name="nome_cliente"]')?.value?.trim();
  return (nome || '?').charAt(0).toUpperCase();
}

function mostrarBadge(inp, existe, foto) {
  limparBadge(inp);
  const el = document.createElement('div');
  el.className = 'tel-badge ' + (existe ? 'ok' : 'no');
  if (existe) {
    const ava = document.createElement('span');
    ava.className = 'ava tel-badge-ava';
    ava.textContent = inicialDe(inp);
    if (foto) {
      ava.style.backgroundImage = "url('" + foto + "')";
      ava.classList.add('loaded');
    }
    el.appendChild(ava);
    const txt = document.createElement('span');
    txt.textContent = 'WhatsApp ✓';
    el.appendChild(txt);
  } else {
    el.textContent = 'sem WhatsApp';
  }
  inp.insertAdjacentElement('afterend', el);
  inp._telBadge = el;
}

async function checar(inp) {
  const d = soDigitos(inp.value);
  if (d.length < 10) {
    limparBadge(inp);
    return;
  }
  if (inp._telChecado === d) return;  // já consultado este número
  inp._telChecado = d;
  let info;
  try {
    const r = await fetch('/admin/whatsapp/checar?numero=' + encodeURIComponent(d));
    if (!r.ok) {           // 502 = checagem indisponível → some sem alarde
      limparBadge(inp);
      inp._telChecado = null;
      return;
    }
    info = await r.json();
  } catch (_) {
    limparBadge(inp);
    inp._telChecado = null;
    return;
  }
  if (info.existe) {
    inp.dataset.canon = soDigitos(info.numero) || d;
    if (info.numero_fmt) inp.value = info.numero_fmt;  // assume o canônico
    mostrarBadge(inp, true, info.foto);
  } else {
    delete inp.dataset.canon;
    mostrarBadge(inp, false);
  }
}

// ---------------------------------------------------------------------------
// Ligação nos campos
// ---------------------------------------------------------------------------

function ligar(inp) {
  if (inp._telLigado) return;
  inp._telLigado = true;

  // Máscara + valor inicial (ex.: telefone do dono já preenchido pelo Jinja).
  if (inp.value.trim()) inp.value = formatar(inp.value);

  let timer = null;
  inp.addEventListener('input', () => {
    inp.value = formatar(inp.value);
    delete inp.dataset.canon;   // editou → cai a confirmação anterior
    inp._telChecado = null;
    limparBadge(inp);
    clearTimeout(timer);
    timer = setTimeout(() => checar(inp), DEBOUNCE_MS);
  });
  inp.addEventListener('blur', () => {
    clearTimeout(timer);
    checar(inp);
  });
}

document.querySelectorAll('input[data-telefone]').forEach(ligar);

// Normaliza para dígitos E.164 ANTES do envio (capture → roda antes dos
// handlers de submit dos outros módulos). Devolve a máscara logo depois, para
// o campo não ficar com dígitos crus caso o envio não recarregue a página.
document.addEventListener('submit', (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement)) return;
  form.querySelectorAll('input[data-telefone]').forEach(inp => {
    const canon = inp.dataset.canon || soDigitos(inp.value);
    inp.value = canon;
    setTimeout(() => { inp.value = formatar(canon); }, 0);
  });
}, true);

// Reset do form (ex.: reabrir o modal) → limpa máscara/badge/confirmação.
document.addEventListener('reset', (e) => {
  if (!(e.target instanceof HTMLFormElement)) return;
  e.target.querySelectorAll('input[data-telefone]').forEach(inp => {
    delete inp.dataset.canon;
    inp._telChecado = null;
    setTimeout(() => limparBadge(inp), 0);
  });
}, true);

export { formatar as formatarTelefone, soDigitos };
