// Contatos — CRUD via API

function abrirModalContato() {
  document.getElementById('cont-id').value = '0';
  document.getElementById('cont-fornecedor').value = '';
  document.getElementById('cont-nome').value = '';
  document.getElementById('cont-whatsapp').value = '';
  document.getElementById('cont-email').value = '';
  document.getElementById('cont-observacoes').value = '';
  document.getElementById('cont-ativo').checked = true;
  document.getElementById('modal-contato-titulo').textContent = 'Novo Contato';
  document.getElementById('modal-contato').classList.add('open');
  document.body.classList.add('agm-aberto');
}

function fecharModalContato() {
  document.getElementById('modal-contato').classList.remove('open');
  document.body.classList.remove('agm-aberto');
}

async function salvarContato(e) {
  e.preventDefault();
  const id = document.getElementById('cont-id').value;
  const form = document.getElementById('form-contato');
  const data = new FormData(form);

  const url = id !== '0'
    ? `/admin/quote/api/contatos/${id}`
    : '/admin/quote/api/contatos';
  const method = id !== '0' ? 'PUT' : 'POST';

  try {
    const res = await fetch(url, { method, body: data });
    if (!res.ok) throw new Error(await res.text());
    fecharModalContato();
    location.reload();
  } catch (err) {
    alert('Erro ao salvar: ' + err.message);
  }
  return false;
}

async function editarContato(id) {
  try {
    const res = await fetch('/admin/quote/api/contatos');
    const contatos = await res.json();
    const c = contatos.find(x => x.id === id);
    if (!c) return;
    document.getElementById('cont-id').value = c.id;
    document.getElementById('cont-fornecedor').value = c.supplier_id;
    document.getElementById('cont-nome').value = c.nome;
    document.getElementById('cont-whatsapp').value = c.whatsapp || '';
    document.getElementById('cont-email').value = c.email || '';
    document.getElementById('cont-observacoes').value = c.observacoes || '';
    document.getElementById('cont-ativo').checked = c.ativo !== false;
    document.getElementById('modal-contato-titulo').textContent = 'Editar Contato';
    document.getElementById('modal-contato').classList.add('open');
    document.body.classList.add('agm-aberto');
  } catch (err) {
    alert('Erro ao carregar: ' + err.message);
  }
}

async function excluirContato(id) {
  if (!confirm('Excluir este contato?')) return;
  try {
    const res = await fetch(`/admin/quote/api/contatos/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await res.text());
    location.reload();
  } catch (err) {
    alert('Erro ao excluir: ' + err.message);
  }
}

function filtrarContatos() {
  const fId = document.getElementById('filtro-fornecedor').value;
  document.querySelectorAll('#tbl-contatos tbody tr').forEach(tr => {
    if (!fId || tr.dataset.fornecedor === fId) {
      tr.style.display = '';
    } else {
      tr.style.display = 'none';
    }
  });
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') fecharModalContato();
});

window.abrirModalContato = abrirModalContato;
window.fecharModalContato = fecharModalContato;
window.salvarContato = salvarContato;
window.editarContato = editarContato;
window.excluirContato = excluirContato;
window.filtrarContatos = filtrarContatos;
