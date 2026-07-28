// Fornecedores — CRUD via API

function abrirModalFornecedor() {
  document.getElementById('forn-id').value = '0';
  document.getElementById('forn-nome').value = '';
  document.getElementById('forn-empresa').value = '';
  document.getElementById('forn-whatsapp').value = '';
  document.getElementById('forn-telefone').value = '';
  document.getElementById('forn-email').value = '';
  document.getElementById('forn-cidade').value = '';
  document.getElementById('forn-estado').value = '';
  document.getElementById('forn-observacoes').value = '';
  document.getElementById('forn-ativo').checked = true;
  document.querySelectorAll('.forn-cat-check').forEach(cb => cb.checked = false);
  document.getElementById('modal-fornecedor-titulo').textContent = 'Novo Fornecedor';
  document.getElementById('modal-fornecedor').classList.add('open');
  document.body.classList.add('agm-aberto');
}

function fecharModalFornecedor() {
  document.getElementById('modal-fornecedor').classList.remove('open');
  document.body.classList.remove('agm-aberto');
}

async function salvarFornecedor(e) {
  e.preventDefault();
  const id = document.getElementById('forn-id').value;
  const form = document.getElementById('form-fornecedor');
  const data = new FormData(form);

  const catIds = [];
  document.querySelectorAll('.forn-cat-check:checked').forEach(cb => catIds.push(parseInt(cb.value)));
  data.set('categorias', JSON.stringify(catIds));

  const url = id !== '0'
    ? `/admin/quote/api/fornecedores/${id}`
    : '/admin/quote/api/fornecedores';
  const method = id !== '0' ? 'PUT' : 'POST';

  try {
    const res = await fetch(url, { method, body: data });
    if (!res.ok) throw new Error(await res.text());
    fecharModalFornecedor();
    location.reload();
  } catch (err) {
    alert('Erro ao salvar: ' + err.message);
  }
  return false;
}

async function editarFornecedor(id) {
  try {
    const res = await fetch(`/admin/quote/api/fornecedores/${id}`);
    const f = await res.json();
    document.getElementById('forn-id').value = f.id;
    document.getElementById('forn-nome').value = f.nome;
    document.getElementById('forn-empresa').value = f.empresa || '';
    document.getElementById('forn-whatsapp').value = f.whatsapp || '';
    document.getElementById('forn-telefone').value = f.telefone || '';
    document.getElementById('forn-email').value = f.email || '';
    document.getElementById('forn-cidade').value = f.cidade || '';
    document.getElementById('forn-estado').value = f.estado || '';
    document.getElementById('forn-observacoes').value = f.observacoes || '';
    document.getElementById('forn-ativo').checked = f.ativo !== false;
    document.querySelectorAll('.forn-cat-check').forEach(cb => {
      cb.checked = (f.categorias || []).includes(parseInt(cb.value));
    });
    document.getElementById('modal-fornecedor-titulo').textContent = 'Editar Fornecedor';
    document.getElementById('modal-fornecedor').classList.add('open');
    document.body.classList.add('agm-aberto');
  } catch (err) {
    alert('Erro ao carregar: ' + err.message);
  }
}

async function excluirFornecedor(id) {
  if (!confirm('Excluir este fornecedor?')) return;
  try {
    const res = await fetch(`/admin/quote/api/fornecedores/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  } catch (err) {
    alert('Erro ao excluir: ' + err.message);
  }
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') fecharModalFornecedor();
});

window.abrirModalFornecedor = abrirModalFornecedor;
window.fecharModalFornecedor = fecharModalFornecedor;
window.salvarFornecedor = salvarFornecedor;
window.editarFornecedor = editarFornecedor;
window.excluirFornecedor = excluirFornecedor;
