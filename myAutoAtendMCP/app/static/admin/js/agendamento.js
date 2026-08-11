/* Modal "Novo agendamento" do card Agendamentos: cadastro manual pelo dono.
   O atendimento é por DIA INTEIRO — escolhe a data, o backend monta o início/fim
   do dia e auto-atribui a vaga (box). Envio por fetch para não tirar o dono da
   página — sucesso recarrega, erro mostra toast sem fechar o modal.

   O telefone é tratado por telefone.js (máscara + checagem de WhatsApp); aqui
   só cuidamos do modal e do envio. */

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
  const salvarBtn = document.getElementById('agm-salvar');

  // -------------------------------------------------------------------------
  // Abrir / fechar
  // -------------------------------------------------------------------------

  function abrir() {
    form.reset();
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
    if (!dataInput.value) {
      toast('erro', 'Escolha a data para o agendamento.');
      return;
    }
    salvarBtn.disabled = true;
    try {
      const formData = new FormData(form);
      if (!formData.get('servico_id')) formData.delete('servico_id');
      const r = await fetch(form.action, {
        method: 'POST',
        body: formData,
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
