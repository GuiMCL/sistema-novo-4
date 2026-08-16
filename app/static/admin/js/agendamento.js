/* Modal "Novo agendamento" do card Agendamentos: cadastro manual pelo dono.
   Escolhe a data → busca os slots (GET /admin/agenda/slots) e mostra um
   seletor de horário em quadradinhos (livre/ocupado). O horário escolhido
   vira o hidden `inicio` (YYYY-MM-DDTHH:MM). O serviço é um campo aberto
   (texto livre) enviado no form. Envio por fetch para não tirar o dono da
   página — sucesso recarrega, erro mostra toast sem fechar o modal.

   O telefone é tratado por telefone.js (máscara + checagem de WhatsApp); aqui
   só cuidamos do modal, dos slots e do envio. */

import { toast } from './toast.js';

const modal = document.getElementById('agm-modal');
if (modal) {
  // O modal nasce dentro de .wrap (stacking em z-index:1) — realocado pro body
  // ele volta ao contexto raiz e o z-index dele passa a valer (padrão do
  // modal de conversas, senão a engrenagem flutuante fica por cima).
  document.body.appendChild(modal);

  const abrirBtn = document.getElementById('ag-novo-abrir');
  const form = document.getElementById('agm-form');
  const dataInput = document.getElementById('agm-data');
  const slotsBox = document.getElementById('agm-slots');
  const legenda = document.getElementById('agm-legenda');
  const inicioHidden = document.getElementById('agm-inicio');
  const salvarBtn = document.getElementById('agm-salvar');

  // -------------------------------------------------------------------------
  // Slots (seletor de horário)
  // -------------------------------------------------------------------------

  function dica(texto) {
    slotsBox.innerHTML = '';
    const p = document.createElement('p');
    p.className = 'agm-slots-dica';
    p.textContent = texto;
    slotsBox.appendChild(p);
    legenda.hidden = true;
  }

  function limparSelecao() {
    inicioHidden.value = '';
    slotsBox.querySelectorAll('.agm-slot.sel').forEach(el => el.classList.remove('sel'));
  }

  function selecionar(btn, hhmm) {
    slotsBox.querySelectorAll('.agm-slot.sel').forEach(el => el.classList.remove('sel'));
    btn.classList.add('sel');
    inicioHidden.value = dataInput.value + 'T' + hhmm;
  }

  function quadrado(s) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'agm-slot ' + s.estado;
    b.textContent = s.inicio;
    if (s.estado === 'ocupado') {
      b.disabled = true;
      b.title = 'Horário ocupado';
    } else {
      b.addEventListener('click', () => selecionar(b, s.inicio));
    }
    return b;
  }

  async function carregarSlots() {
    limparSelecao();
    const data = dataInput.value;
    if (!data) {
      dica('Escolha a data para ver os horários.');
      return;
    }
    dica('Carregando horários…');
    let dados;
    try {
      const r = await fetch(
        '/admin/agenda/slots?data=' + encodeURIComponent(data),
      );
      if (!r.ok) throw new Error(r.status);
      dados = await r.json();
    } catch (_) {
      dica('Não foi possível carregar os horários. Tente de novo.');
      return;
    }
    if (dados.fechado) {
      dica('Dia sem expediente.');
      return;
    }
    if (!dados.slots.length) {
      dica('Nenhum horário nesta data.');
      return;
    }
    slotsBox.innerHTML = '';
    dados.slots.forEach(s => slotsBox.appendChild(quadrado(s)));
    legenda.hidden = false;
  }

  dataInput.addEventListener('change', carregarSlots);

  // -------------------------------------------------------------------------
  // Abrir / fechar
  // -------------------------------------------------------------------------

  function abrir() {
    form.reset();
    limparSelecao();
    dica('Escolha a data para ver os horários.');
    modal.classList.add('open');
    document.body.classList.add('agm-aberto');
    form.querySelector('[name="nome_cliente"]').focus();
  }

  function fechar() {
    modal.classList.remove('open');
    document.body.classList.remove('agm-aberto');
  }

  abrirBtn?.addEventListener('click', abrir);
  document.getElementById('agm-x').addEventListener('click', fechar);
  document.getElementById('agm-cancelar').addEventListener('click', fechar);
  modal.querySelector('.agm-backdrop').addEventListener('click', fechar);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.classList.contains('open')) fechar();
  });

  // -------------------------------------------------------------------------
  // Envio
  // -------------------------------------------------------------------------

  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (!inicioHidden.value) {
      toast('erro', 'Escolha um horário disponível para o agendamento.');
      return;
    }
    salvarBtn.disabled = true;
    try {
      const r = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        redirect: 'manual',                 // 303 vira opaqueredirect (= sucesso)
        headers: { Accept: 'application/json' },
      });
      if (r.type === 'opaqueredirect' || r.ok) {
        toast('ok', 'Agendamento criado.');
        location.reload();
        return;
      }
      let d = null;
      try { d = await r.json(); } catch (_) { /* corpo não-JSON */ }
      toast('erro', (d && typeof d.detail === 'string' && d.detail) ||
        'Não foi possível criar o agendamento.');
    } catch (_) {
      toast('erro', 'Falha de conexão ao criar o agendamento.');
    }
    salvarBtn.disabled = false;
  });
}
