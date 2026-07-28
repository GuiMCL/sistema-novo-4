// Peças — CRUD via API

function abrirModalPeca() {
  document.getElementById('peca-id').value = '0';
  document.getElementById('peca-nome').value = '';
  document.getElementById('peca-codigo-interno').value = '';
  document.getElementById('peca-codigo-fab').value = '';
  document.getElementById('peca-marca').value = '';
  document.getElementById('peca-categoria').value = '';
  document.getElementById('peca-descricao').value = '';
  document.getElementById('peca-observacoes').value = '';
  document.getElementById('peca-ativo').checked = true;
  document.getElementById('modal-peca-titulo').textContent = 'Nova Peça';
  document.getElementById('modal-peca').classList.add('open');
  document.body.classList.add('agm-aberto');
}

function fecharModalPeca() {
  document.getElementById('modal-peca').classList.remove('open');
  document.body.classList.remove('agm-aberto');
}

async function salvarPeca(e) {
  e.preventDefault();
  const id = document.getElementById('peca-id').value;
  const form = document.getElementById('form-peca');
  const data = new FormData(form);

  const url = id !== '0'
    ? `/admin/quote/api/pecas/${id}`
    : '/admin/quote/api/pecas';
  const method = id !== '0' ? 'PUT' : 'POST';

  try {
    const res = await fetch(url, { method, body: data });
    if (!res.ok) throw new Error(await res.text());
    fecharModalPeca();
    location.reload();
  } catch (err) {
    alert('Erro ao salvar: ' + err.message);
  }
  return false;
}

async function editarPeca(id) {
  try {
    const res = await fetch('/admin/quote/api/pecas');
    const pecas = await res.json();
    const p = pecas.find(x => x.id === id);
    if (!p) return;
    document.getElementById('peca-id').value = p.id;
    document.getElementById('peca-nome').value = p.nome;
    document.getElementById('peca-codigo-interno').value = p.codigo_interno || '';
    document.getElementById('peca-codigo-fab').value = p.codigo_fabricante || '';
    document.getElementById('peca-marca').value = p.marca || '';
    document.getElementById('peca-categoria').value = p.categoria_id;
    document.getElementById('peca-descricao').value = p.descricao || '';
    document.getElementById('peca-observacoes').value = p.observacoes || '';
    document.getElementById('peca-ativo').checked = p.ativo;
    document.getElementById('modal-peca-titulo').textContent = 'Editar Peça';
    document.getElementById('modal-peca').classList.add('open');
    document.body.classList.add('agm-aberto');
  } catch (err) {
    alert('Erro ao carregar: ' + err.message);
  }
}

async function excluirPeca(id) {
  if (!confirm('Excluir esta peça?')) return;
  try {
    const res = await fetch(`/admin/quote/api/pecas/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  } catch (err) {
    alert('Erro ao excluir: ' + err.message);
  }
}

function filtrarPecas() {
  const catId = document.getElementById('filtro-categoria').value;
  document.querySelectorAll('#tbl-pecas tbody tr').forEach(tr => {
    if (!catId || tr.dataset.categoria === catId) {
      tr.style.display = '';
    } else {
      tr.style.display = 'none';
    }
  });
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') fecharModalPeca();
});

window.abrirModalPeca = abrirModalPeca;
window.fecharModalPeca = fecharModalPeca;
window.salvarPeca = salvarPeca;
window.editarPeca = editarPeca;
window.excluirPeca = excluirPeca;
window.filtrarPecas = filtrarPecas;
