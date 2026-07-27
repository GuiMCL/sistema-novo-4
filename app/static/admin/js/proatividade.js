/* Card "Proatividade Pendente": lista ao vivo a fila de ações proativas do bot
   (GET /admin/tarefas/estado) com poll automático. Botão Cancelar por tarefa
   (POST /admin/tarefas/{id}/cancelar) — só em tarefas pendentes. */

import { toast } from './toast.js';

const POLL_MS = 20000;
const lista = document.getElementById('proat-lista');
if (!lista) { /* card nao esta nesta pagina */ } else {
const tagCount = document.getElementById('proat-count');
const live = document.getElementById('proat-live');

const STATUS = {
  pendente:   { rotulo: 'na fila',     cls: 'pendente'   },
  executando: { rotulo: 'enviando',    cls: 'executando' },
  falhou:     { rotulo: 'falhou',      cls: 'falhou'     },
};

const fmtData = iso => {
  if (!iso) return '';
  const [data, hora = ''] = iso.split('T');
  const [a, m, d] = data.split('-');
  return `${d}/${m} ${hora}`.trim();
};

// "pendente" + agendado_para já vencido + fora de 08–20h = preso esperando janela.
function dicaJanela(t) {
  if (t.status !== 'pendente') return '';
  const agora = new Date();
  const venceu = !t.agendado_para || new Date(t.agendado_para.replace(' ', 'T')) <= agora;
  if (!venceu) return '';
  const ini = parseInt(t.janela_inicio, 10);
  const fim = parseInt(t.janela_fim, 10);
  const h = agora.getHours();
  if (h >= ini && h < fim) return '';
  return `aguardando janela ${t.janela_inicio}–${t.janela_fim}`;
}

function linha(t) {
  const st = STATUS[t.status] || { rotulo: t.status, cls: 'pendente' };
  const el = document.createElement('article');
  el.className = `proat-item is-${st.cls}`;

  const alvo = t.nome_cliente || t.alvo || '—';
  const ctx = [t.servico, fmtData(t.quando_agendamento)].filter(Boolean).join(' · ');
  const dica = dicaJanela(t);
  const tentInfo = t.tentativas
    ? `${t.tentativas}/${t.max_tentativas} tentativas`
    : '';

  el.innerHTML = `
    <span class="proat-rail" aria-hidden="true"></span>
    <div class="proat-body">
      <div class="proat-top">
        <span class="proat-badge ${st.cls}">${st.rotulo}</span>
        <span class="proat-titulo">${esc(t.titulo)}</span>
      </div>
      <div class="proat-quem">${esc(alvo)}${ctx ? ` <span class="proat-ctx">— ${esc(ctx)}</span>` : ''}</div>
      <div class="proat-meta">
        <span class="proat-when" title="Disparo programado">⏱ ${esc(fmtData(t.agendado_para)) || '—'}</span>
        ${tentInfo ? `<span>· ${tentInfo}</span>` : ''}
        ${dica ? `<span class="proat-dica">· ${dica}</span>` : ''}
      </div>
      ${t.status === 'falhou' && t.resultado
        ? `<div class="proat-erro" title="Último erro">${esc(t.resultado)}</div>` : ''}
    </div>
    ${t.status === 'pendente'
      ? `<button type="button" class="btn-sm btn-danger proat-cancel" data-id="${t.id}">Cancelar</button>` : ''}
  `;
  return el;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

async function carregar() {
  let dados;
  try {
    const r = await fetch('/admin/tarefas/estado', { headers: { 'Accept': 'application/json' } });
    if (!r.ok) throw new Error(r.status);
    dados = await r.json();
  } catch (_) {
    live?.classList.add('off');
    return;
  }
  live?.classList.remove('off');
  const tarefas = dados.tarefas || [];
  const pendentes = tarefas.filter(t => t.status === 'pendente' || t.status === 'executando').length;
  tagCount.textContent = pendentes ? `${pendentes} na fila` : 'fila vazia';

  lista.innerHTML = '';
  if (!tarefas.length) {
    lista.innerHTML = '<p class="proat-empty">Nada na fila — o bot não tem nenhuma mensagem proativa agendada.</p>';
    return;
  }
  tarefas.forEach(t => lista.appendChild(linha(t)));
}

lista.addEventListener('click', async e => {
  const btn = e.target.closest('.proat-cancel');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const r = await fetch(`/admin/tarefas/${btn.dataset.id}/cancelar`, {
      method: 'POST', headers: { 'Accept': 'application/json' },
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      toast('erro', d.detail || 'Não foi possível cancelar esta tarefa.');
    }
  } catch (_) { toast('erro', 'Falha de conexão ao cancelar a tarefa.'); }
  carregar();  // recarrega de qualquer forma — reflete o estado real
});

carregar();
setInterval(carregar, POLL_MS);
}
