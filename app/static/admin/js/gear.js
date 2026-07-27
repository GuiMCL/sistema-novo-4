/* Ocultar/exibir cards: botão ✕ em cada card-head esconde a seção; a
   engrenagem flutuante (canto da página) reexibe. Estado no localStorage. */

const KEY = 'admin-cards-ocultos';
const NOMES = {
  wa: 'Conexão WhatsApp', instancias: 'Instâncias WhatsApp',
  ia: 'Provedores de IA', prompt: 'Instruções do Agente',
  agendamentos: 'Agendamentos ativos', vagas: 'Vagas / Boxes',
  proatividade: 'Proatividade Pendente',
  lembretes: 'Lembretes automáticos',
  catalogo: 'Serviços & Bloqueios',
  horarios: 'Horários de Funcionamento', usuarios: 'Usuários',
  config: 'Configuração geral',
};
const wrap  = document.querySelector('.wrap');
const fab   = document.getElementById('gear-fab');
const badge = document.getElementById('gear-badge');
const panel = document.getElementById('gear-panel');
const lista = document.getElementById('gear-lista');
const secs  = [...wrap.querySelectorAll(':scope > .drag-sec')];

let ocultos;
try { ocultos = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch(_){ ocultos = []; }
if (!Array.isArray(ocultos)) ocultos = [];

function render(){
  secs.forEach(s => s.classList.toggle('sec-oculta', ocultos.includes(s.dataset.sec)));
  badge.textContent = ocultos.length;
  badge.style.display = ocultos.length ? '' : 'none';
  lista.querySelectorAll('.gear-row').forEach(r => r.classList.toggle('on', !ocultos.includes(r.dataset.sec)));
  // grade.js sincroniza os itens do gridstack com a lista de ocultos
  document.dispatchEvent(new CustomEvent('admin:cards-ocultos', { detail: [...ocultos] }));
}

function alternar(chave){
  ocultos = ocultos.includes(chave) ? ocultos.filter(c => c !== chave) : ocultos.concat(chave);
  localStorage.setItem(KEY, JSON.stringify(ocultos));
  render();
}

// Uma linha por seção no painel da engrenagem.
secs.forEach(s => {
  const chave = s.dataset.sec;
  const row = document.createElement('div');
  row.className = 'gear-row';
  row.dataset.sec = chave;
  row.innerHTML = '<span class="gear-nome"></span><span class="sw"></span>';
  row.querySelector('.gear-nome').textContent = NOMES[chave] || chave;
  row.addEventListener('click', () => alternar(chave));
  lista.appendChild(row);
});

// Botão "ocultar" em cada card-head (a seção inteira some).
wrap.querySelectorAll('.drag-sec .card-head').forEach(head => {
  const sec = head.closest('.drag-sec');
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'card-hide';
  b.textContent = '✕';
  b.title = 'Ocultar este card (reative na engrenagem ⚙)';
  b.setAttribute('aria-label', 'Ocultar card');
  b.addEventListener('click', e => { e.stopPropagation(); alternar(sec.dataset.sec); });
  head.appendChild(b);
});

fab.addEventListener('click', e => { e.stopPropagation(); panel.classList.toggle('open'); });
panel.addEventListener('click', e => e.stopPropagation());
document.addEventListener('click', () => panel.classList.remove('open'));

render();
