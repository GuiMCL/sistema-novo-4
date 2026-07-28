/* Card "Horários de Funcionamento": recolher/expandir + adicionar/remover
   intervalos por dia (form é POST clássico — replace-all no servidor). */

const card = document.getElementById('horarios-card');
if (!card) { /* card nao esta nesta pagina */ } else {

function novaLinha(dia){
  const div = document.createElement('div');
  div.className = 'hf-int';
  div.innerHTML =
    `<input type="hidden" name="dia" value="${dia}">` +
    '<input name="inicio" type="time" required>' +
    '<span class="hf-ate">às</span>' +
    '<input name="fim" type="time" required>' +
    '<button type="button" class="btn-sm btn-danger hf-rm" title="Remover intervalo" aria-label="Remover intervalo">✕</button>';
  return div;
}

function atualizarFechado(secaoDia){
  const vazio = !secaoDia.querySelector('.hf-int');
  secaoDia.querySelector('.hf-fechado').style.display = vazio ? '' : 'none';
}

card.querySelectorAll('.hf-dia').forEach(secaoDia => {
  const dia = secaoDia.dataset.dia;
  const lista = secaoDia.querySelector('.hf-intervalos');
  secaoDia.querySelector('.hf-add').addEventListener('click', () => {
    lista.appendChild(novaLinha(dia));
    atualizarFechado(secaoDia);
  });
  lista.addEventListener('click', e => {
    const btn = e.target.closest('.hf-rm');
    if (!btn) return;
    btn.closest('.hf-int').remove();
    atualizarFechado(secaoDia);
  });
});
}
