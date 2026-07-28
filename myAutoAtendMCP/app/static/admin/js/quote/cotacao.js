// Nova Cotação — fluxo principal

let pecasSelecionadas = [];
let fornecedoresSelecionados = [];

async function carregarPecas() {
  const catId = document.getElementById('cot-categoria').value;
  const container = document.getElementById('cot-pecas-container');
  const fornContainer = document.getElementById('cot-fornecedores-container');

  if (!catId) {
    container.innerHTML = '<p class="empty">Selecione uma categoria primeiro</p>';
    fornContainer.innerHTML = '<p class="empty">Selecione uma categoria para ver os fornecedores</p>';
    return;
  }

  try {
    const [pecasRes, fornecedoresRes] = await Promise.all([
      fetch(`/admin/quote/api/pecas-por-categoria/${catId}`),
      fetch(`/admin/quote/api/fornecedores/por-categoria/${catId}`),
    ]);
    const pecas = await pecasRes.json();
    const fornecedores = await fornecedoresRes.json();

    if (pecas.length === 0) {
      container.innerHTML = '<p class="empty">Nenhuma peça nesta categoria</p>';
    } else {
      container.innerHTML = '';
      pecas.forEach(p => {
        const label = document.createElement('label');
        label.className = 'avisar-cli';
        label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = p.id;
        cb.dataset.nome = p.nome;
        cb.addEventListener('change', atualizarPecasSelecionadas);
        label.appendChild(cb);
        const span = document.createElement('span');
        span.innerHTML = `<strong>${p.nome}</strong> ${p.codigo_interno ? '<span class="meta">' + p.codigo_interno + '</span>' : ''} ${p.marca ? '<span class="meta">' + p.marca + '</span>' : ''}`;
        label.appendChild(span);
        const qtd = document.createElement('input');
        qtd.type = 'number';
        qtd.min = 1;
        qtd.value = 1;
        qtd.style.cssText = 'width:60px;margin-left:auto;';
        qtd.dataset.partId = p.id;
        qtd.addEventListener('change', atualizarPecasSelecionadas);
        label.appendChild(qtd);
        container.appendChild(label);
      });
    }

    if (fornecedores.length === 0) {
      fornContainer.innerHTML = '<p class="empty">Nenhum fornecedor atende esta categoria</p>';
    } else {
      fornContainer.innerHTML = '';
      fornecedores.forEach(f => {
        const label = document.createElement('label');
        label.className = 'avisar-cli';
        label.style.cssText = 'display:flex;align-items:center;gap:8px;padding:6px 0;';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = f.id;
        cb.dataset.nome = f.nome;
        cb.addEventListener('change', atualizarFornecedoresSelecionados);
        label.appendChild(cb);
        const span = document.createElement('span');
        span.innerHTML = `<strong>${f.nome}</strong> ${f.empresa ? '<span class="meta">' + f.empresa + '</span>' : ''}`;
        label.appendChild(span);
        fornContainer.appendChild(label);
      });
    }
  } catch (err) {
    container.innerHTML = '<p class="empty">Erro ao carregar dados</p>';
  }
}

function atualizarPecasSelecionadas() {
  const container = document.getElementById('cot-lista-pecas');
  const section = document.getElementById('cot-pecas-selecionadas');
  pecasSelecionadas = [];

  document.querySelectorAll('#cot-pecas-container input[type="checkbox"]:checked').forEach(cb => {
    const qtdInput = cb.parentElement.querySelector('input[type="number"]');
    pecasSelecionadas.push({
      part_id: parseInt(cb.value),
      nome: cb.dataset.nome,
      quantidade: qtdInput ? parseInt(qtdInput.value) || 1 : 1,
    });
  });

  if (pecasSelecionadas.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  container.innerHTML = '';
  pecasSelecionadas.forEach(p => {
    const li = document.createElement('li');
    li.style.cssText = 'padding:4px 0;font-size:14px;';
    li.textContent = `${p.nome} (${p.quantidade}x)`;
    container.appendChild(li);
  });
}

function atualizarFornecedoresSelecionados() {
  fornecedoresSelecionados = [];
  document.querySelectorAll('#cot-fornecedores-container input[type="checkbox"]:checked').forEach(cb => {
    fornecedoresSelecionados.push(parseInt(cb.value));
  });
}

async function enviarCotacao(e) {
  e.preventDefault();

  const categoriaId = document.getElementById('cot-categoria').value;
  if (!categoriaId) { alert('Selecione uma categoria'); return false; }
  if (pecasSelecionadas.length === 0) { alert('Selecione pelo menos uma peça'); return false; }
  if (fornecedoresSelecionados.length === 0) { alert('Selecione pelo menos um fornecedor'); return false; }

  const sessaoId = document.getElementById('cot-sessao').value;
  if (!sessaoId) { alert('Selecione uma sessão WhatsApp para enviar'); return false; }
  const observacoes = document.getElementById('cot-observacoes').value;
  const template = document.getElementById('cot-template').value;

  const data = new FormData();
  data.set('categoria_id', categoriaId);
  data.set('pecas', JSON.stringify(pecasSelecionadas.map(p => ({ part_id: p.part_id, quantidade: p.quantidade }))));
  data.set('fornecedores', JSON.stringify(fornecedoresSelecionados));
  data.set('sessao_id', sessaoId || '0');
  data.set('observacoes', observacoes);

  // Save template first if changed
  try {
    const res = await fetch('/admin/quote/api/solicitacoes', { method: 'POST', body: data });
    if (!res.ok) throw new Error(await res.text());
    const req = await res.json();

    // Envia a cotação
    const sendRes = await fetch(`/admin/quote/api/solicitacoes/${req.id}/enviar`, { method: 'POST' });
    if (!sendRes.ok) throw new Error(await sendRes.text());
    const result = await sendRes.json();

    alert(`Cotação ${req.numero} enviada para ${result.enviados} fornecedor(es).`);
    window.location.href = `/admin/quote/${req.id}`;
  } catch (err) {
    alert('Erro ao enviar cotação: ' + err.message);
  }
  return false;
}

function formatarPlaca(input) {
  let v = input.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (v.length > 3 && v.length <= 7) {
    v = v.slice(0, 3) + '-' + v.slice(3);
  }
  input.value = v;
}

async function consultarPlaca() {
  const input = document.getElementById('cot-placa');
  const placaRaw = input.value.replace(/[^A-Z0-9]/g, '');
  if (placaRaw.length !== 7) { alert('Digite uma placa válida (7 caracteres)'); return; }

  const btn = document.getElementById('btn-consultar-placa');
  const loading = document.getElementById('cot-placa-loading');
  const erro = document.getElementById('cot-placa-erro');
  const info = document.getElementById('cot-veiculo-info');

  erro.style.display = 'none';
  info.style.display = 'none';
  loading.style.display = 'block';
  btn.disabled = true;

  try {
    const fd = new FormData();
    fd.set('placa', placaRaw);
    const res = await fetch('/admin/quote/api/consultar-placa', { method: 'POST', body: fd });
    if (!res.ok) {
      const errText = await res.text();
      let msg = 'Erro ao consultar placa';
      try { const j = JSON.parse(errText); msg = j.detail || msg; } catch {}
      throw new Error(msg);
    }
    const data = await res.json();
    document.getElementById('cot-veiculo-marca').value = data.marca || '';
    document.getElementById('cot-veiculo-modelo').value = data.modelo || '';
    document.getElementById('cot-veiculo-ano').value = data.ano || '';
    info.style.display = 'block';
    input.value = data.placa;
    if (data.placa.length > 3) {
      input.value = data.placa.slice(0, 3) + '-' + data.placa.slice(3);
    }
  } catch (err) {
    erro.textContent = err.message;
    erro.style.display = 'block';
  } finally {
    loading.style.display = 'none';
    btn.disabled = false;
  }
}

async function enviarCotacao(e) {
  e.preventDefault();

  const categoriaId = document.getElementById('cot-categoria').value;
  if (!categoriaId) { alert('Selecione uma categoria'); return false; }
  if (pecasSelecionadas.length === 0) { alert('Selecione pelo menos uma peça'); return false; }
  if (fornecedoresSelecionados.length === 0) { alert('Selecione pelo menos um fornecedor'); return false; }

  const sessaoId = document.getElementById('cot-sessao').value;
  if (!sessaoId) { alert('Selecione uma sessão WhatsApp para enviar'); return false; }
  const observacoes = document.getElementById('cot-observacoes').value;
  const template = document.getElementById('cot-template').value;

  const data = new FormData();
  data.set('categoria_id', categoriaId);
  data.set('pecas', JSON.stringify(pecasSelecionadas.map(p => ({ part_id: p.part_id, quantidade: p.quantidade }))));
  data.set('fornecedores', JSON.stringify(fornecedoresSelecionados));
  data.set('sessao_id', sessaoId || '0');
  data.set('observacoes', observacoes);

  const placaRaw = document.getElementById('cot-placa').value.replace(/[^A-Z0-9]/g, '');
  data.set('placa', placaRaw);
  data.set('veiculo_marca', document.getElementById('cot-veiculo-marca').value);
  data.set('veiculo_modelo', document.getElementById('cot-veiculo-modelo').value);
  data.set('veiculo_ano', document.getElementById('cot-veiculo-ano').value);

  try {
    const res = await fetch('/admin/quote/api/solicitacoes', { method: 'POST', body: data });
    if (!res.ok) throw new Error(await res.text());
    const req = await res.json();

    const sendRes = await fetch(`/admin/quote/api/solicitacoes/${req.id}/enviar`, { method: 'POST' });
    if (!sendRes.ok) throw new Error(await sendRes.text());
    const result = await sendRes.json();

    alert(`Cotação ${req.numero} enviada para ${result.enviados} fornecedor(es).`);
    window.location.href = `/admin/quote/${req.id}`;
  } catch (err) {
    alert('Erro ao enviar cotação: ' + err.message);
  }
  return false;
}

window.carregarPecas = carregarPecas;
window.formatarPlaca = formatarPlaca;
window.consultarPlaca = consultarPlaca;
window.enviarCotacao = enviarCotacao;
