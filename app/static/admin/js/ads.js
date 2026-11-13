const boxes = [...document.querySelectorAll('.ads-contacts input[type="checkbox"]')];
const count = document.getElementById('ads-contagem');
function updateCount() { if (count) count.textContent = `${boxes.filter(b => b.checked).length} selecionados`; }
boxes.forEach(b => b.addEventListener('change', updateCount));
document.getElementById('ads-todos')?.addEventListener('click', (event) => {
  const all = boxes.length > 0 && boxes.every(b => b.checked);
  boxes.forEach(b => { b.checked = !all; });
  event.currentTarget.textContent = all ? 'Selecionar todos' : 'Limpar seleção';
  updateCount();
});
updateCount();
