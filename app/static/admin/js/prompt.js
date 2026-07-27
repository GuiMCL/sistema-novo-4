/* Card "Instruções do Agente": system prompt via painel.
   Blocos: geral + MCP do dono + MCP do cliente (um textarea cada). */

import { toast } from './toast.js';

const $ = (id) => document.getElementById(id);
let mcpDonoPadrao = '';
let mcpClientePadrao = '';

function msg(texto, ok){
  const el = $('prompt-msg');
  el.textContent = texto;
  el.className = 'ia-msg ' + (ok ? 'ok' : 'err');
}

async function carregar(){
  try {
    const r = await fetch('/admin/agente/prompt');
    const d = await r.json();
    if (!r.ok || d.erro) throw new Error(d.erro || ('HTTP ' + r.status));
    $('prompt-geral').value = d.geral || '';
    $('prompt-mcp-dono').value = d.mcp_dono || '';
    $('prompt-mcp-cliente').value = d.mcp_cliente || '';
    mcpDonoPadrao = d.mcp_dono_padrao || '';
    mcpClientePadrao = d.mcp_cliente_padrao || '';
    const pill = $('prompt-status');
    if (d.fonte === 'painel'){ pill.className = 'pill on'; pill.textContent = 'gerido pelo painel'; }
    else { pill.className = 'pill off'; pill.textContent = 'padrão'; }
  } catch(e){
    toast('erro', 'Falha ao carregar as instruções: ' + e.message);
  }
}

async function salvar(btn){
  const geral = $('prompt-geral').value.trim();
  if (!geral){ toast('erro', 'A instrução geral não pode ficar vazia.'); return; }
  btn.disabled = true; msg('Salvando…', true);
  try {
    const r = await fetch('/admin/agente/prompt', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams({
        geral,
        mcp_dono: $('prompt-mcp-dono').value,
        mcp_cliente: $('prompt-mcp-cliente').value,
      }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.erro) throw new Error(d.erro || d.detail || ('HTTP ' + r.status));
    msg('Instruções publicadas — o agente já responde com o novo prompt.', true);
    const pill = $('prompt-status');
    pill.className = 'pill on'; pill.textContent = 'gerido pelo painel';
  } catch(e){ toast('erro', 'Falha ao salvar: ' + e.message); }
  finally { btn.disabled = false; }
}

// Restaura um bloco MCP para o texto padrão (só preenche o campo — salvar publica).
function restaurar(campoId, padrao, rotulo){
  if (!padrao) return;
  if (!confirm(`Restaurar o bloco MCP (${rotulo}) para o texto padrão? O campo será sobrescrito (salve para publicar).`)) return;
  $(campoId).value = padrao;
  msg(`Bloco MCP (${rotulo}) restaurado ao padrão — clique em Salvar.`, true);
}

if ($('prompt-salvar')) {
  $('prompt-salvar').addEventListener('click', (ev) => salvar(ev.currentTarget));
  $('prompt-restaurar-dono').addEventListener('click', () => restaurar('prompt-mcp-dono', mcpDonoPadrao, 'dono'));
  $('prompt-restaurar-cliente').addEventListener('click', () => restaurar('prompt-mcp-cliente', mcpClientePadrao, 'cliente'));
  carregar();
}
