// Categorias — CRUD via API

function abrirModalCategoria() {
  document.getElementById('cat-id').value = '0';
  document.getElementById('cat-nome').value = '';
  document.getElementById('cat-descricao').value = '';
  document.getElementById('cat-ordem').value = '0';
  document.getElementById('cat-ativo').checked = true;
  document.getElementById('modal-categoria-titulo').textContent = 'Nova Categoria';
  document.getElementById('modal-categoria').classList.add('open');
  document.body.classList.add('agm-aberto');
}

function fecharModalCategoria() {
  document.getElementById('modal-categoria').classList.remove('open');
  document.body.classList.remove('agm-aberto');
}

async function salvarCategoria(e) {
  e.preventDefault();
  const id = document.getElementById('cat-id').value;
  const form = document.getElementById('form-categoria');
  const data = new FormData(form);

  const url = id !== '0'
    ? `/admin/quote/api/categorias/${id}`
    : '/admin/quote/api/categorias';
  const method = id !== '0' ? 'PUT' : 'POST';

  try {
    const res = await fetch(url, { method, body: data });
    if (!res.ok) throw new Error(await res.text());
    fecharModalCategoria();
    location.reload();
  } catch (err) {
    alert('Erro ao salvar: ' + err.message);
  }
  return false;
}

async function editarCategoria(id) {
  try {
    const res = await fetch('/admin/quote/api/categorias');
    const cats = await res.json();
    const cat = cats.find(c => c.id === id);
    if (!cat) return;
    document.getElementById('cat-id').value = cat.id;
    document.getElementById('cat-nome').value = cat.nome;
    document.getElementById('cat-descricao').value = cat.descricao || '';
    document.getElementById('cat-ordem').value = cat.ordem || 0;
    document.getElementById('cat-ativo').checked = cat.ativo;
    document.getElementById('modal-categoria-titulo').textContent = 'Editar Categoria';
    document.getElementById('modal-categoria').classList.add('open');
    document.body.classList.add('agm-aberto');
  } catch (err) {
    alert('Erro ao carregar: ' + err.message);
  }
}

async function excluirCategoria(id) {
  if (!confirm('Excluir esta categoria?')) return;
  try {
    const res = await fetch(`/admin/quote/api/categorias/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  } catch (err) {
    alert('Erro ao excluir: ' + err.message);
  }
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') fecharModalCategoria();
});

window.abrirModalCategoria = abrirModalCategoria;
window.fecharModalCategoria = fecharModalCategoria;
window.salvarCategoria = salvarCategoria;
window.editarCategoria = editarCategoria;
window.excluirCategoria = excluirCategoria;
