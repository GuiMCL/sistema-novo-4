/* Notificações flutuantes (toasts) — erros, avisos e sucessos do painel.
   Uso: import { toast } from './toast.js'; toast('erro', 'mensagem', {titulo}).
   Tipos: 'erro' | 'aviso' | 'ok'. Empilha no canto inferior direito,
   some sozinho (erro dura mais) e nunca tira o usuário da página. */

const TIPOS = {
  erro:  { rotulo: 'Erro',  vida: 10000, role: 'alert',  live: 'assertive' },
  aviso: { rotulo: 'Aviso', vida: 6000,  role: 'status', live: 'polite'    },
  ok:    { rotulo: 'Feito', vida: 6000,  role: 'status', live: 'polite'    },
};

let container = null;

function pegarContainer() {
  if (container) return container;
  container = document.createElement('div');
  container.className = 'toast-wrap';
  document.body.appendChild(container);
  return container;
}

export function toast(tipo, mensagem, opts = {}) {
  const cfg = TIPOS[tipo] || TIPOS.aviso;
  const wrap = pegarContainer();

  const el = document.createElement('div');
  el.className = 'toast toast-' + (TIPOS[tipo] ? tipo : 'aviso');
  el.setAttribute('role', cfg.role);
  el.setAttribute('aria-live', cfg.live);

  const eyebrow = document.createElement('p');
  eyebrow.className = 'toast-eyebrow';
  eyebrow.textContent = cfg.rotulo;
  el.appendChild(eyebrow);

  if (opts.titulo) {
    const titulo = document.createElement('p');
    titulo.className = 'toast-titulo';
    titulo.textContent = opts.titulo;
    el.appendChild(titulo);
  }

  const corpo = document.createElement('p');
  corpo.className = 'toast-corpo';
  corpo.textContent = mensagem;
  el.appendChild(corpo);

  const fechar = document.createElement('button');
  fechar.type = 'button';
  fechar.className = 'toast-fechar';
  fechar.setAttribute('aria-label', 'Fechar aviso');
  fechar.textContent = '✕';
  el.appendChild(fechar);

  wrap.appendChild(el);
  requestAnimationFrame(() => el.classList.add('shown'));  // dispara a animação de entrada

  let timer = null;
  const remover = () => {
    if (el.dataset.saindo) return;
    el.dataset.saindo = '1';
    clearTimeout(timer);
    el.classList.remove('shown');
    el.classList.add('saindo');
    el.addEventListener('transitionend', () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 450);  // fallback se não houver transição (reduced motion)
  };
  const agendar = () => { timer = setTimeout(remover, cfg.vida); };

  fechar.addEventListener('click', remover);
  el.addEventListener('mouseenter', () => clearTimeout(timer));  // pausa a contagem no hover
  el.addEventListener('mouseleave', agendar);
  agendar();

  return remover;
}
