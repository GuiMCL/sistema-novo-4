let precosAtuais = [];

function filtrarStatus() {
  const status = document.getElementById('filtro-status').value;
  const url = status ? `/admin/quote/historico?status_filtro=${status}` : '/admin/quote/historico';
  window.location.href = url;
}

async function cancelarCotacao(id) {
  if (!confirm('Cancelar esta cotação?')) return;
  try {
    const data = new FormData();
    data.set('status', 'cancelada');
    const res = await fetch(`/admin/quote/api/solicitacoes/${id}/status`, { method: 'POST', body: data });
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  } catch (err) {
    alert('Erro: ' + err.message);
  }
}

async function alterarStatus(id, status) {
  if (!confirm(`Alterar status para "${status}"?`)) return;
  try {
    const data = new FormData();
    data.set('status', status);
    const res = await fetch(`/admin/quote/api/solicitacoes/${id}/status`, { method: 'POST', body: data });
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  } catch (err) {
    alert('Erro: ' + err.message);
  }
}

async function salvarPrecos(requestId) {
  const inputs = document.querySelectorAll('.cmp-preco-input');
  const precos = [];
  inputs.forEach(inp => {
    const partId = parseInt(inp.dataset.partId);
    const supplierId = parseInt(inp.dataset.supplierId);
    const valor = parseFloat(inp.value.replace(',', '.')) || 0;
    precos.push({ part_id: partId, supplier_id: supplierId, valor });
  });
  try {
    const data = new FormData();
    data.set('data', JSON.stringify(precos));
    const res = await fetch(`/admin/quote/api/solicitacoes/${requestId}/precos`, { method: 'POST', body: data });
    if (!res.ok) throw new Error(await res.text());
    alert('Preços salvos!');
  } catch (err) {
    alert('Erro ao salvar: ' + err.message);
  }
}

function montarComparativo(data) {
  const container = document.getElementById('comparativo-container');
  const btnSalvar = document.getElementById('btn-salvar-precos');
  if (!container) return;
  const itens = data.itens || [];
  const fornecedores = data.fornecedores || [];
  precosAtuais = data.precos || [];
  if (itens.length === 0 || fornecedores.length === 0) {
    container.innerHTML = '<p class="empty">Adicione peças e fornecedores para ver o comparativo.</p>';
    return;
  }
  btnSalvar.style.display = 'block';
  let html = '<table class="tbl" style="min-width:700px;"><thead><tr><th style="min-width:140px;">Peça</th>';
  fornecedores.forEach(f => {
    html += `<th style="text-align:center;min-width:110px;">${f.nome}</th>`;
  });
  html += '<th style="text-align:center;min-width:130px;color:var(--green);">Melhor Preço</th>';
  html += '</tr></thead><tbody>';
  itens.forEach(item => {
    let melhorValor = Infinity;
    let melhorFornId = null;
    fornecedores.forEach(f => {
      const precosItem = precosAtuais.filter(p => p.part_id === item.part_id && p.supplier_id === f.id);
      const preco = precosItem.length > 0 ? precosItem[0].valor : 0;
      if (preco > 0 && preco < melhorValor) {
        melhorValor = preco;
        melhorFornId = f.id;
      }
    });
    html += `<tr><td class="who" style="font-weight:500;">${item.part_nome} <span class="meta">(${item.quantidade}x)</span></td>`;
    fornecedores.forEach(f => {
      const precosItem = precosAtuais.filter(p => p.part_id === item.part_id && p.supplier_id === f.id);
      const preco = precosItem.length > 0 ? precosItem[0].valor : 0;
      const formatted = preco ? preco.toFixed(2).replace('.', ',') : '';
      const isBest = preco > 0 && f.id === melhorFornId;
      const bg = isBest ? 'background:var(--green);color:#fff;font-weight:600;' : '';
      html += `<td style="text-align:center;padding:4px 6px;">
        <input type="text" class="cmp-preco-input" data-part-id="${item.part_id}" data-supplier-id="${f.id}"
          value="${formatted}" style="width:85px;text-align:center;padding:4px 6px;font-size:13px;${bg}"
          placeholder="0,00">
      </td>`;
    });
    if (melhorValor < Infinity) {
      const fornNome = fornecedores.find(f => f.id === melhorFornId);
      const lbl = fornNome ? `${fornNome.nome}` : '';
      html += `<td style="text-align:center;font-size:13px;font-weight:600;color:var(--green);">R$ ${melhorValor.toFixed(2).replace('.', ',')}<br><span style="font-size:11px;font-weight:400;">${lbl}</span></td>`;
    } else {
      html += `<td style="text-align:center;font-size:13px;color:var(--ink-soft);">—</td>`;
    }
    html += '</tr>';
  });
  html += '</tbody></table>';
  container.innerHTML = html;
}

document.addEventListener('DOMContentLoaded', function () {
  const detalhe = document.getElementById('cotacao-detalhe');
  if (!detalhe) return;
  const reqId = detalhe.dataset.id;
  carregarDetalhe(reqId);
  setInterval(() => carregarMensagens(reqId), 10000);
});

async function carregarDetalhe(id) {
  try {
    const res = await fetch(`/admin/quote/api/solicitacoes/${id}`);
    const data = await res.json();
    montarComparativo(data);
    atualizarInterface(data);
  } catch (err) {
    console.error('Erro ao carregar detalhe:', err);
  }
}

async function carregarMensagens(id) {
  try {
    const res = await fetch(`/admin/quote/api/solicitacoes/${id}`);
    if (!res.ok) return;
    const data = await res.json();
    const badge = document.getElementById('cot-status-badge');
    if (badge) badge.textContent = data.status;
    const msgsDiv = document.getElementById('cot-mensagens');
    if (!msgsDiv) return;
    const oldCount = msgsDiv.children.length;
    if (data.mensagens.length === oldCount) return;
    atualizarInterface(data);
  } catch (err) {
  }
}

function atualizarInterface(data) {
  const badge = document.getElementById('cot-status-badge');
  if (badge) badge.textContent = data.status;
  const tbody = document.getElementById('cot-itens-body');
  if (tbody) {
    tbody.innerHTML = '';
    (data.itens || []).forEach(item => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td class="who">${item.part_nome || ''}</td><td class="meta">${item.codigo_interno || ''}</td><td>${item.quantidade}x</td>`;
      tbody.appendChild(tr);
    });
  }
  const fornList = document.getElementById('cot-fornecedores-list');
  if (fornList) {
    fornList.innerHTML = '';
    (data.fornecedores || []).forEach(f => {
      const div = document.createElement('div');
      div.style.cssText = 'padding:8px 0;border-bottom:1px solid var(--line);font-size:14px;';
      div.innerHTML = `<strong>${f.nome}</strong><br><span class="meta">${f.whatsapp || ''}</span>`;
      fornList.appendChild(div);
    });
  }
  const msgsDiv = document.getElementById('cot-mensagens');
  if (msgsDiv) {
    msgsDiv.innerHTML = '';
    (data.mensagens || []).forEach(m => {
      const div = document.createElement('div');
      const isRecebida = m.tipo === 'recebida';
      div.style.cssText = `padding:10px 14px;border-radius:10px;font-size:14px;${
        isRecebida
          ? 'background:rgba(63,107,76,.10);border:1px solid rgba(63,107,76,.20);align-self:flex-start;'
          : 'background:rgba(201,79,10,.08);border:1px solid rgba(201,79,10,.18);align-self:flex-end;'
      }`;
      div.innerHTML = `<div style="font-weight:600;font-size:12px;margin-bottom:4px;">${m.fornecedor_nome || ''} — ${m.tipo === 'recebida' ? 'Recebida' : 'Enviada'}</div><div>${m.mensagem}</div>`;
      msgsDiv.appendChild(div);
    });
  }
  const histDiv = document.getElementById('cot-historico');
  if (histDiv) {
    histDiv.innerHTML = '';
    (data.historico || []).forEach(h => {
      const div = document.createElement('div');
      div.style.cssText = 'padding:6px 0;border-bottom:1px solid var(--paper-2);';
      div.innerHTML = `<span class="meta">${(h.data_hora || '').slice(0, 16)}</span> — ${h.descricao || h.acao}`;
      histDiv.appendChild(div);
    });
  }
}

window.filtrarStatus = filtrarStatus;
window.cancelarCotacao = cancelarCotacao;
window.alterarStatus = alterarStatus;
window.salvarPrecos = salvarPrecos;