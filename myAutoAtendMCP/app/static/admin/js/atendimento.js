class Atendimento {
  constructor() {
    this.conversas = [];
    this.ativa = null;
    this.filtro = 'todas';
    this.busca = '';
    this._enviando = false;
    this.init();
  }

  init() {
    this.carregarConversas().then(() => {
      const params = new URLSearchParams(location.search);
      const tel = params.get('conv');
      if (tel) this.abrirConversa(tel);
    });
    this.pollTimer = setInterval(() => this._poll(), 10000);
    window.addEventListener('resize', () => this._ajustarPainelMobile());
  }

  /* ---------- API ---------- */
  async api(path, opts = {}) {
    const resp = await fetch(path, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(opts.headers || {}) },
      ...opts,
    });
    if (resp.redirected) { window.location.href = resp.url; return null; }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  /* ---------- POLL ---------- */
  async _poll() {
    try {
      await this.carregarConversas(true);
      if (this.ativa) await this._atualizarMensagens(true);
    } catch (_) {}
  }

  /* ---------- SIDEBAR / LISTA ---------- */
  async carregarConversas(silent = false) {
    try {
      const params = new URLSearchParams({ filtro: this.filtro });
      if (this.busca) params.set('busca', this.busca);
      const data = await this.api(`/atendimento/api/conversas?${params}`);
      if (!data) return;

      const antigas = this.conversas.map(c => c.telefone).join(',');
      const novas = (data.conversas || []).map(c => c.telefone).join(',');
      this.conversas = data.conversas || [];

      if (antigas !== novas) {
        this.renderizarLista();
      } else if (!silent) {
        this.renderizarLista();
      } else if (this.ativa) {
        const ativa = this.conversas.find(c => c.telefone === this.ativa);
        if (ativa) this._atualizarItemLista(ativa);
      }
    } catch (e) {
      if (!silent) console.error('Erro carregando conversas:', e);
    }
  }

  buscar() {
    this.busca = document.getElementById('conv-busca').value;
    this.carregarConversas();
  }

  filtrar(filtro, btn) {
    this.filtro = filtro;
    document.querySelectorAll('.filtro').forEach(b => b.classList.remove('ativo'));
    if (btn) btn.classList.add('ativo');
    this.carregarConversas();
  }

  renderizarLista() {
    const el = document.getElementById('conv-lista');
    if (!this.conversas.length) {
      el.innerHTML = '<div class="conv-empty">Nenhuma conversa encontrada.</div>';
      return;
    }
    el.innerHTML = this.conversas.map(c => {
      const ini = c.nome ? c.nome.charAt(0).toUpperCase() : '?';
      const ativa = c.telefone === this.ativa ? ' ativa' : '';
      const badge = c.nao_lido > 0 ? `<span class="conv-badge">${c.nao_lido}</span>` : '';
      const pausa = c.pausado ? '<span class="conv-meta" style="font-size:.65rem">⏸</span>' : '';
      return `<div class="conv-item${ativa}" data-tel="${c.telefone}" onclick="atendimento.abrirConversa('${c.telefone}')">
        <div class="conv-avatar">${ini}</div>
        <div class="conv-info">
          <div class="conv-nome">${c.nome || c.telefone}</div>
          <div class="conv-preview">${c.preview || '—'}</div>
          <div class="conv-meta">${c.hora || ''} ${badge} ${pausa}</div>
        </div>
      </div>`;
    }).join('');
  }

  _atualizarItemLista(conv) {
    const item = document.querySelector(`.conv-item[data-tel="${conv.telefone}"]`);
    if (!item) { this.renderizarLista(); return; }
    const preview = item.querySelector('.conv-preview');
    if (preview) preview.textContent = conv.preview || '—';
    const badge = item.querySelector('.conv-badge');
    if (conv.nao_lido > 0) {
      if (!badge) {
        const meta = item.querySelector('.conv-meta');
        if (meta) meta.insertAdjacentHTML('beforeend', `<span class="conv-badge">${conv.nao_lido}</span>`);
      } else {
        badge.textContent = conv.nao_lido;
      }
    } else if (badge) {
      badge.remove();
    }
  }

  /* ---------- CONVERSA ATIVA ---------- */
  async abrirConversa(telefone, { carregarMensagens = true } = {}) {
    this.ativa = telefone;
    this.renderizarLista();
    document.getElementById('chat-placeholder').style.display = 'none';
    document.getElementById('chat-ativo').style.display = 'flex';
    document.getElementById('panel-placeholder').style.display = 'none';
    document.getElementById('panel-conteudo').style.display = 'block';

    if (!carregarMensagens) return;

    try {
      const data = await this.api(`/atendimento/api/conversas/${telefone}`);
      if (!data) return;

      document.getElementById('ch-nome').textContent = data.nome || data.telefone;
      document.getElementById('ch-avatar').textContent = data.nome ? data.nome.charAt(0).toUpperCase() : '?';
      document.getElementById('ch-status').textContent = data.pausado ? 'Pausado' : 'Online';
      document.getElementById('ch-pause-btn').textContent = data.pausado ? '▶' : '⏸';
      document.getElementById('ch-pause-btn').title = data.pausado ? 'Retomar bot' : 'Pausar bot';

      this._renderizarMensagens(data.mensagens || []);
      this._renderizarPainel(data);
      document.getElementById('chat-texto').focus();
    } catch (e) {
      console.error('Erro abrindo conversa:', e);
    }
  }

  async _atualizarMensagens(silent = false) {
    if (!this.ativa) return;
    try {
      const data = await this.api(`/atendimento/api/conversas/${this.ativa}`);
      if (!data) return;
      const msgs = data.mensagens || [];
      const el = document.getElementById('chat-msgs');
      let mudou = false;

      for (const m of msgs) {
        const texto = this.escapeHtml(m.texto);
        const q = m.quem;
        if (el.querySelector(`.msg.${q}:last-child div:first-child`)?.textContent === m.texto) continue;
        const exists = [...el.querySelectorAll(`.msg.${q}`)].some(n => n.querySelector('div')?.textContent === m.texto);
        if (exists) continue;
        el.insertAdjacentHTML('beforeend',
          `<div class="msg ${q}"><div>${texto}</div><div class="msg-hora">${m.hora || ''}</div></div>`
        );
        mudou = true;
      }

      if (mudou) {
        el.scrollTop = el.scrollHeight;
        document.querySelectorAll('.msg.pending').forEach(p => p.remove());
      }
    } catch (e) {
      if (!silent) console.error('Erro atualizando mensagens:', e);
    }
  }

  _renderizarMensagens(mensagens) {
    const el = document.getElementById('chat-msgs');
    if (!mensagens.length) {
      el.innerHTML = '<div class="msg-placeholder">Nenhuma mensagem ainda.</div>';
      return;
    }
    el.innerHTML = mensagens.map(m =>
      `<div class="msg ${m.quem}"><div>${this.escapeHtml(m.texto)}</div><div class="msg-hora">${m.hora || ''}</div></div>`
    ).join('');
    el.scrollTop = el.scrollHeight;
  }

  _renderizarPainel(data) {
    document.getElementById('p-nome').textContent = data.nome || data.telefone;
    document.getElementById('p-telefone').textContent = data.telefone;
    document.getElementById('p-status').textContent = data.pausado ? 'Pausado' : 'Ativo';

    const ags = data.agendamentos || [];
    const agEl = document.getElementById('p-agendamentos');
    if (!ags.length) {
      agEl.innerHTML = 'Nenhum agendamento.';
    } else {
      agEl.innerHTML = ags.map(a => {
        const dh = (a.inicio || '').replace('T', ' ');
        return `<div class="ag-item">
          <div class="ag-servico">${a.servico_nome || '—'}</div>
          <div class="ag-data">${dh} ${a.vaga_nome ? '· ' + a.vaga_nome : ''}</div>
          <div><span class="ag-status ${a.status}">${a.status}</span> ${a.placa ? '· ' + a.placa : ''}</div>
        </div>`;
      }).join('');
    }
  }

  /* ---------- ENVIO OTIMISTA ---------- */
  async enviar() {
    const input = document.getElementById('chat-texto');
    const texto = input.value.trim();
    if (!texto || !this.ativa || this._enviando) return;

    const el = document.getElementById('chat-msgs');
    const placeholder = el.querySelector('.msg-placeholder');
    if (placeholder) placeholder.remove();

    const tempId = `pending_${Date.now()}`;
    el.insertAdjacentHTML('beforeend',
      `<div class="msg bot pending" data-temp="${tempId}"><div>${this.escapeHtml(texto)}</div><div class="msg-hora">enviando<span class="msg-status">...</span></div></div>`
    );
    el.scrollTop = el.scrollHeight;

    input.value = '';
    this._enviando = true;
    document.getElementById('chat-enviar').classList.add('loading');

    try {
      const form = new FormData();
      form.append('texto', texto);
      await this.api(`/atendimento/api/conversas/${this.ativa}/enviar`, {
        method: 'POST',
        body: form,
      });
      const pendente = el.querySelector(`[data-temp="${tempId}"]`);
      if (pendente) pendente.classList.remove('pending');
    } catch (e) {
      console.error('Erro enviando:', e);
      const falha = el.querySelector(`[data-temp="${tempId}"]`);
      if (falha) {
        falha.classList.remove('pending');
        falha.querySelector('.msg-hora').textContent = 'falhou';
      }
    } finally {
      this._enviando = false;
      document.getElementById('chat-enviar').classList.remove('loading');
      input.focus();
    }
  }

  onEnter(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this.enviar();
    }
    const ta = e.target;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 100) + 'px';
  }

  /* ---------- ACOES ---------- */
  async togglePausa() {
    if (!this.ativa) return;
    try {
      const form = new FormData();
      const data = await this.api(`/atendimento/api/conversas/${this.ativa}`);
      if (!data) return;
      form.append('pausar', data.pausado ? 'false' : 'true');
      await this.api(`/atendimento/api/conversas/${this.ativa}/pausa`, { method: 'POST', body: form });
      const pausado = !data.pausado;
      document.getElementById('ch-status').textContent = pausado ? 'Pausado' : 'Online';
      document.getElementById('ch-pause-btn').textContent = pausado ? '▶' : '⏸';
      document.getElementById('ch-pause-btn').title = pausado ? 'Retomar bot' : 'Pausar bot';
    } catch (e) {
      console.error(e);
    }
  }

  recarregarConversa() {
    if (this.ativa) this.abrirConversa(this.ativa);
  }

  async enviarConfirmacao() {
    if (!this.ativa) return;
    const texto = 'Seu horario esta confirmado! Estamos te aguardando.';
    const form = new FormData();
    form.append('texto', texto);
    try {
      await this.api(`/atendimento/api/conversas/${this.ativa}/enviar`, { method: 'POST', body: form });
      this.abrirConversa(this.ativa);
    } catch (e) {
      console.error(e);
    }
  }

  async pausarContato() {
    if (!this.ativa) return;
    if (!confirm('Pausar o bot para este contato?')) return;
    const form = new FormData();
    form.append('pausar', 'true');
    try {
      await this.api(`/atendimento/api/conversas/${this.ativa}/pausa`, { method: 'POST', body: form });
      document.getElementById('ch-status').textContent = 'Pausado';
    } catch (e) {
      console.error(e);
    }
  }

  novoAgendamento() {
    document.getElementById('modal-agenda').style.display = 'flex';
    if (this.ativa) document.getElementById('ag-telefone').value = this.ativa;
  }

  async criarAgendamento(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    if (!formData.get('servico_id')) formData.delete('servico_id');
    try {
      const data = await this.api('/atendimento/api/agendamento', { method: 'POST', body: formData });
      if (data && data.ok) {
        document.getElementById('modal-agenda').style.display = 'none';
        alert('Agendamento criado!');
      }
    } catch (e) {
      console.error(e);
      alert('Erro ao criar agendamento.');
    }
    return false;
  }

  /* ---------- MOBILE ---------- */
  toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
  }

  togglePanel() {
    document.getElementById('panel').classList.toggle('open');
  }

  _ajustarPainelMobile() {
    if (window.innerWidth > 900) {
      document.getElementById('sidebar').classList.remove('open');
      document.getElementById('panel').classList.remove('open');
    }
  }

  /* ---------- UTIL ---------- */
  escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }
}

window.atendimento = new Atendimento();
