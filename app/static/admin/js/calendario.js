/* Calendário "Agendamentos ativos": clicar num chip (horário do dia) abre o
   modal de detalhe do agendamento. Os dados vêm de window.__CAL__.detalhe
   (Jinja → JSON). Ações dentro do modal:
   - Conversa: navega pro helpdesk /atendimento?conv=<telefone>;
   - Reagendar e Cancelar: posts nos mesmos endpoints do layout antigo. */

const modal = document.getElementById('agd-modal');
const __CAL__ = window.__CAL__ || {};
const detalhes = __CAL__.detalhe || {};

if (modal) {
  // Mesmo padrão do modal "Novo agendamento": realocar pro body para o z-index valer.
  document.body.appendChild(modal);

  const body = document.getElementById('agd-body');

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function montar(d) {
    const nome = esc(d.nome || '?');
    const quando = d.inicio ? esc(String(d.inicio).split('T')[0]) : '—';
    const extra = [];
    if (!__CAL__.apenas_ativos) {
      extra.push(`
        <form class="agd-reagendar" method="post" action="/admin/agendamento/${esc(d.id)}/reagendar">
          <label>Reagendar para outro dia</label>
          <div class="row" style="gap:6px;align-items:center">
            <input type="date" name="nova_data" value="${esc(d.inicio ? String(d.inicio).split('T')[0] : '')}" required>
            <label class="avisar-cli" title="A IA manda mensagem no WhatsApp do cliente avisando a mudança">
              <input type="checkbox" name="avisar_cliente" value="1"> Avisar cliente
            </label>
            <button class="btn-sm btn-primary" style="margin:0">Confirmar</button>
          </div>
        </form>
        <form class="agd-cancelar" method="post" action="/admin/agendamento/${esc(d.id)}/cancelar">
          <input type="hidden" name="avisar_cliente" value="">
          <button class="btn-sm btn-danger">Cancelar agendamento</button>
        </form>`);
    }
    return `
      <dl class="agd-info">
        <div><dt>Cliente</dt><dd>${nome}</dd></div>
        <div><dt>Telefone</dt><dd>${esc(d.telefone)}</dd></div>
        <div><dt>Serviço</dt><dd>${esc(d.servico) || '—'}</dd></div>
        ${d.descricao ? `<div><dt>Descrição</dt><dd>${esc(d.descricao)}</dd></div>` : ''}
        ${d.veiculo ? `<div><dt>Veículo</dt><dd>${esc(d.veiculo)}${d.placa ? ' · <b>' + esc(d.placa) + '</b>' : ''}</dd></div>` : ''}
        <div><dt>Vaga</dt><dd>${esc(d.vaga) || '—'}</dd></div>
        <div><dt>Quando</dt><dd>${quando}</dd></div>
        ${d.obs ? `<div><dt>Observações</dt><dd>${esc(d.obs)}</dd></div>` : ''}
      </dl>
      <div class="agd-acoes">
        <button type="button" class="btn-sm btn-ghost" id="agd-conversa" data-telefone="${esc(d.telefone)}">Conversa</button>
        ${extra.join('')}
      </div>`;
  }

  function abrir(id) {
    const d = detalhes[id] || detalhes[String(id)];
    if (!d) return;
    body.innerHTML = montar(d);
    modal.classList.add('open');
    document.body.classList.add('agd-aberto');
  }

  function fechar() {
    modal.classList.remove('open');
    document.body.classList.remove('agd-aberto');
  }

  // Chips do calendário (delegação: sobrevivem a re-render).
  document.addEventListener('click', e => {
    const chip = e.target.closest('.cal-chip');
    if (chip) abrir(chip.dataset.id);
  });

  document.getElementById('agd-x').addEventListener('click', fechar);
  modal.querySelector('.agd-backdrop').addEventListener('click', fechar);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.classList.contains('open')) fechar();
  });

  body.addEventListener('click', e => {
    const conv = e.target.closest('#agd-conversa');
    if (conv) {
      window.location.href = '/atendimento?conv=' + encodeURIComponent(conv.dataset.telefone);
      return;
    }
    const canc = e.target.closest('.agd-cancelar .btn-danger');
    if (canc) {
      const form = canc.closest('form');
      if (!confirm('Cancelar o agendamento?')) {
        e.preventDefault();
        return;
      }
      form.avisar_cliente.value =
        confirm('Avisar o cliente pelo WhatsApp? A IA manda a mensagem do cancelamento.') ? '1' : '';
    }
  });
}
