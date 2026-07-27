/* Grade de cards (Gridstack, vendor local): os cards ocupam a janela toda,
   arrastam pelo grip ⠿ e redimensionam pelas bordas/canto. Layout salvo por
   navegador (localStorage). Substitui o antigo drag.js (reordenação vertical).
   A visibilidade continua com o gear.js — aqui só sincronizamos a grade
   quando um card é ocultado/reexibido (evento admin:cards-ocultos). */

const KEY = 'admin-grade-v1';
const CELULA = 30;   // px por linha da grade
const MARGEM = 22;   // vão entre cards (mesmo respiro do painel antigo empilhado)

// Layout padrão de primeira visita (grade de 12 colunas, 2 colunas de cards).
// Card novo que ainda não estiver aqui cai no fallback (linha cheia, auto).
const PADRAO = {
  agendamentos: { x: 0, y: 0,  w: 7, h: 13 },
  wa:           { x: 7, y: 0,  w: 5, h: 14 },
  ia:           { x: 0, y: 13, w: 7, h: 14 },
  horarios:     { x: 7, y: 14, w: 5, h: 17 },
  prompt:       { x: 0, y: 27, w: 7, h: 17 },
  catalogo:     { x: 0, y: 44, w: 7, h: 30 },
  config:       { x: 7, y: 31, w: 5, h: 13 },
  proatividade: { x: 7, y: 44, w: 5, h: 17 },
};

const wrap = document.querySelector('.wrap');
const secs = [...wrap.querySelectorAll(':scope > .drag-sec')];
if (!secs.length || typeof GridStack === 'undefined') {
  console.warn('grade: gridstack indisponível — layout fica no fluxo normal');
} else {
  iniciar();
}

function iniciar() {
  let salvo = {};
  try { salvo = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (_) { /* layout corrompido → padrão */ }

  // posição efetiva de um card: escolha do usuário > layout padrão > nada
  const posDe = (sec) => salvo[sec] || PADRAO[sec];

  // contêiner da grade no lugar dos cards (header/stats ficam fora)
  const grade = document.createElement('div');
  grade.className = 'grid-stack';
  secs[0].before(grade);

  // Pré-ordena pela posição efetiva (y, depois x): o gridstack posiciona na
  // ordem do DOM, e montar "de baixo pra cima" cascateia colisões que
  // embaralham o layout no F5. Sem posição → fim (auto-position).
  const ordenados = [...secs].sort((a, b) => {
    const pa = posDe(a.dataset.sec), pb = posDe(b.dataset.sec);
    if (!pa && !pb) return 0;
    if (!pa) return 1;
    if (!pb) return -1;
    return (pa.y - pb.y) || (pa.x - pb.x);
  });

  // Marca um card como item da grade com a posição efetiva (salva ou padrão).
  // Só entra aqui card VISÍVEL: card oculto não pode participar da montagem —
  // coordenadas velhas/ausentes dele colidem com o layout dos visíveis e
  // empurram card bom pra baixo no F5.
  const prepararItem = (s) => {
    s.classList.add('grid-stack-item');
    const pos = posDe(s.dataset.sec);
    if (pos) {
      s.setAttribute('gs-x', pos.x); s.setAttribute('gs-y', pos.y);
      s.setAttribute('gs-w', pos.w); s.setAttribute('gs-h', pos.h);
    } else {
      // card novo fora do PADRAO: linha cheia com a altura do conteúdo atual
      const px = s.firstElementChild ? s.firstElementChild.offsetHeight : 0;
      const h = px ? Math.max(4, Math.round((px + MARGEM) / (CELULA + MARGEM))) : 10;
      s.setAttribute('gs-w', '12');
      s.setAttribute('gs-h', String(h));
      s.setAttribute('gs-auto-position', 'true');
    }
  };

  ordenados.forEach(s => {
    // gridstack exige item > content; o miolo do card desce pro content
    const conteudo = document.createElement('div');
    conteudo.className = 'grid-stack-item-content';
    while (s.firstChild) conteudo.appendChild(s.firstChild);
    s.appendChild(conteudo);
    s.setAttribute('gs-id', s.dataset.sec);
    grade.appendChild(s);
    if (!s.classList.contains('sec-oculta')) prepararItem(s);
  });

  const grid = GridStack.init({
    cellHeight: CELULA,
    margin: MARGEM,
    column: 12,
    float: true,
    handle: '.drag-grip',
    resizable: { handles: 'e, se, s, sw, w' },
    columnOpts: { breakpoints: [{ w: 900, c: 1 }] },  // 1 coluna no mobile
  }, grade);

  // Restore canônico por id: reafirma as posições salvas depois do init —
  // imune a qualquer colisão/ajuste que o engine tenha feito na montagem.
  // O `false` é essencial: sem ele o load() REMOVE widgets fora da lista.
  const comPos = ordenados.filter(s => s.gridstackNode && posDe(s.dataset.sec));
  if (comPos.length) {
    grid.load(comPos.map(s => ({ id: s.dataset.sec, ...posDe(s.dataset.sec) })), false);
  }

  // só persiste o layout de 12 colunas — o modo 1 coluna (mobile) é derivado
  const salvar = () => {
    if (grid.getColumn() !== 12) return;
    secs.forEach(s => {
      const n = s.gridstackNode;
      if (n) salvo[s.dataset.sec] = { x: n.x, y: n.y, w: n.w, h: n.h };
    });
    localStorage.setItem(KEY, JSON.stringify(salvo));
  };
  // salva SÓ em gesto do usuário (nunca no 'change': o engine dispara change
  // em ajustes da montagem e persistiria um layout transitório errado)
  grid.on('dragstop resizestop', () => salvar());

  // Ponto fixo: persiste UMA vez o layout já resolvido pela montagem. Se o
  // salvo tinha sobreposição (herança de versões antigas), o engine empurra
  // um card — sem gravar o resultado, TODO F5 repetiria o empurrão. Gravando,
  // a tela atual == o que o próximo F5 carrega, e o layout estabiliza.
  salvar();

  // gear.js avisa quando a lista de ocultos muda
  document.addEventListener('admin:cards-ocultos', e => {
    const ocultos = e.detail || [];
    secs.forEach(s => {
      const esconder = ocultos.includes(s.dataset.sec);
      const ativo = !!s.gridstackNode;
      if (esconder && ativo) {
        grid.removeWidget(s, false, false);
      } else if (!esconder && !ativo) {
        // oculto no boot nunca virou item — prepara agora (posição salva ou
        // padrão); quem já foi item mantém os gs-* de quando foi escondido
        if (!s.classList.contains('grid-stack-item')) prepararItem(s);
        grid.makeWidget(s);
      }
    });
    salvar();
  });
}
