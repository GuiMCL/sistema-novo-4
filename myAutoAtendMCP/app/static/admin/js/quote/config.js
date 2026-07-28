// Configurações de Cotação — Template management

async function salvarTemplate(e) {
  e.preventDefault();
  const form = document.getElementById('form-template');
  const data = new FormData(form);
  const id = document.getElementById('tmpl-id').value;

  try {
    const res = await fetch(`/admin/quote/api/templates/${id}`, { method: 'PUT', body: data });
    if (!res.ok) throw new Error(await res.text());
    alert('Template salvo com sucesso!');
    location.reload();
  } catch (err) {
    alert('Erro ao salvar: ' + err.message);
  }
  return false;
}

async function carregarTemplate(id) {
  try {
    const res = await fetch('/admin/quote/api/templates');
    const templates = await res.json();
    const t = templates.find(x => x.id === id);
    if (!t) return;
    document.getElementById('tmpl-id').value = t.id;
    document.getElementById('tmpl-nome').value = t.nome;
    document.getElementById('tmpl-template').value = t.template;
    document.getElementById('tmpl-ativo').checked = t.ativo !== false;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (err) {
    alert('Erro ao carregar: ' + err.message);
  }
}

async function salvarPlacaConfig(e) {
  e.preventDefault();
  const fd = new FormData();
  fd.set('token', document.getElementById('placa-token').value);
  fd.set('device_token', document.getElementById('placa-device-token').value);

  try {
    const res = await fetch('/admin/quote/api/config-placa', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    alert('Credenciais da API Placa salvas com sucesso!');
  } catch (err) {
    alert('Erro ao salvar: ' + err.message);
  }
  return false;
}

window.salvarTemplate = salvarTemplate;
window.carregarTemplate = carregarTemplate;
window.salvarPlacaConfig = salvarPlacaConfig;
