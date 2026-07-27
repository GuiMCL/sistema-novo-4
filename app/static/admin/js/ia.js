/* Card "Provedores de IA": abas por uso (texto/áudio/imagem), chaves one-way
   (gravadas localmente, nunca exibidas de volta). O form da chave fica oculto
   atrás de "Atualizar chave"; o resumo mostra status + provedor atual. */

const PROVEDORES = window.__ADMIN__.provedores;
const ALVOS = ['texto', 'audio', 'imagem'];
const provedorSalvo = { texto: null, audio: null, imagem: null };  // o que está salvo hoje
const modelosCache = { texto: [], imagem: [] };                    // lista completa p/ busca
const temModelo = (alvo) => !!$('ia-modelo-sel-' + alvo);  // áudio: whisper-1 fixo

const $ = (id) => document.getElementById(id);

function msg(alvo, texto, ok){
  const el = $('ia-msg-' + alvo);
  el.textContent = texto;
  el.className = 'ia-msg ' + (ok ? 'ok' : 'err');
}

/* ---------- abas ---------- */

document.querySelectorAll('[data-ia-tab]').forEach(btn =>
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-ia-tab]').forEach(b => b.classList.toggle('active', b === btn));
    for (const alvo of ALVOS)
      $('ia-pane-' + alvo).classList.toggle('active', alvo === btn.dataset.iaTab);
  }));

/* ---------- form da chave (oculto por padrão) ---------- */

function abrirChave(alvo){
  $('ia-chave-' + alvo).hidden = false;
  $('ia-key-' + alvo).focus();
}

function fecharChave(alvo){
  $('ia-chave-' + alvo).hidden = true;
  $('ia-key-' + alvo).value = '';
  // Volta o select ao provedor salvo (descarta troca não confirmada).
  const sel = $('ia-prov-' + alvo);
  if (provedorSalvo[alvo] && sel.querySelector('option[value="' + provedorSalvo[alvo] + '"]'))
    sel.value = provedorSalvo[alvo];
  $('ia-url-wrap-' + alvo).style.display = sel.value === 'custom' ? '' : 'none';
}

document.querySelectorAll('[data-ia-editar]').forEach(b =>
  b.addEventListener('click', () => {
    const alvo = b.dataset.iaEditar;
    $('ia-chave-' + alvo).hidden ? abrirChave(alvo) : fecharChave(alvo);
  }));
document.querySelectorAll('[data-ia-cancelar]').forEach(b =>
  b.addEventListener('click', () => fecharChave(b.dataset.iaCancelar)));

/* ---------- provedores ---------- */

function montarSelect(alvo){
  const sel = $('ia-prov-' + alvo);
  if (!sel) return;
  for (const [chave, p] of Object.entries(PROVEDORES)){
    if (!p[alvo]) continue;
    const opt = document.createElement('option');
    opt.value = chave;
    opt.textContent = p.nome;
    sel.appendChild(opt);
  }
  sel.addEventListener('change', () => {
    $('ia-url-wrap-' + alvo).style.display = sel.value === 'custom' ? '' : 'none';
    aoTrocarProvedor(alvo);
  });
  // Chave colada/alterada → tenta listar modelos do provedor escolhido.
  $('ia-key-' + alvo).addEventListener('change', () => previewModelos(alvo));
}

function atualizarResumo(alvo, info){
  const pill = $('ia-status-' + alvo);
  const atual = $('ia-atual-' + alvo);
  const dot = $('ia-dot-' + alvo);
  if (info && info.provedor){
    pill.className = 'pill on';
    pill.textContent = info.atualizado_em
      ? 'chave de ' + new Date(info.atualizado_em).toLocaleDateString('pt-BR')
      : 'configurado';
    const nome = (PROVEDORES[info.provedor] || {}).nome || info.provedor;
    atual.textContent = nome + (info.modelo ? ' · ' + info.modelo : (alvo === 'audio' ? ' · whisper-1' : ''));
    dot.className = 'dot on';
  } else {
    pill.className = 'pill off';
    pill.textContent = 'sem chave';
    atual.textContent = 'nenhum provedor configurado';
    dot.className = 'dot';
    abrirChave(alvo);  // sem chave não há o que resumir — já abre o form
  }
}

async function carregarEstado(){
  try {
    const r = await fetch('/admin/ia/estado');
    const d = await r.json();
    if (d.erro) throw new Error(d.erro);
    for (const alvo of ALVOS){
      const info = d[alvo] || {};
      provedorSalvo[alvo] = info.provedor || null;
      const sel = $('ia-prov-' + alvo);
      if (!sel) continue;
      if (info.provedor && sel.querySelector('option[value="' + info.provedor + '"]')){
        sel.value = info.provedor;
        const wrap = $('ia-url-wrap-' + alvo);
        if (wrap) wrap.style.display = info.provedor === 'custom' ? '' : 'none';
      }
      if (info.modelo && temModelo(alvo)) {
        const mod = $('ia-modelo-' + alvo);
        if (mod) mod.value = info.modelo;
      }
      atualizarResumo(alvo, info);
    }
    for (const alvo of ALVOS) if (temModelo(alvo)) carregarModelos(alvo, (d[alvo] || {}).modelo);
  } catch(e){
    for (const alvo of ALVOS){
      const pill = $('ia-status-' + alvo);
      if (!pill) continue;
      pill.className = 'pill off';
      pill.textContent = 'indisponível';
      const at = $('ia-atual-' + alvo);
      if (at) at.textContent = 'estado indisponível';
    }
  }
}

/* ---------- modelos (lista + busca) ---------- */

function renderOpcoes(alvo, filtro){
  const sel = $('ia-modelo-sel-' + alvo);
  const f = (filtro || '').trim().toLowerCase();
  const anterior = sel.value;
  sel.innerHTML = '';
  for (const m of modelosCache[alvo]){
    if (f && !m.toLowerCase().includes(f)) continue;
    const o = document.createElement('option');
    o.value = m; o.textContent = m;
    sel.appendChild(o);
  }
  const outro = document.createElement('option');
  outro.value = '__outro'; outro.textContent = 'Outro (digitar)…';
  sel.appendChild(outro);
  if (anterior && sel.querySelector('option[value="' + CSS.escape(anterior) + '"]'))
    sel.value = anterior;
}

function popularModelos(alvo, modelos, atual){
  const sel = $('ia-modelo-sel-' + alvo);
  const inp = $('ia-modelo-' + alvo);
  if (!sel || !modelos.length) return;
  const atualVal = atual || '';
  if (atualVal && !modelos.includes(atualVal)) modelos.unshift(atualVal);
  modelosCache[alvo] = modelos;
  $('ia-busca-' + alvo).value = '';
  renderOpcoes(alvo, '');
  if (atualVal) sel.value = atualVal;
  sel.style.display = '';
  $('ia-busca-' + alvo).style.display = '';
  inp.style.display = 'none';
  sel.onchange = () => {
    inp.style.display = sel.value === '__outro' ? '' : 'none';
    if (sel.value === '__outro') inp.focus();
  };
}

function resetarModelos(alvo){
  const sel = $('ia-modelo-sel-' + alvo);
  if (!sel) return;
  modelosCache[alvo] = [];
  sel.style.display = 'none'; sel.innerHTML = '';
  $('ia-busca-' + alvo).style.display = 'none';
  $('ia-busca-' + alvo).value = '';
  $('ia-modelo-' + alvo).style.display = '';
}

// Lista com a chave JÁ SALVA (provedor atual).
async function carregarModelos(alvo, atual){
  if (!temModelo(alvo)) return;
  try {
    const r = await fetch('/admin/ia/modelos?alvo=' + alvo);
    const d = await r.json();
    if (!r.ok || d.erro) throw new Error(d.erro || ('HTTP ' + r.status));
    popularModelos(alvo, (d.modelos || []).map(m => m.valor),
                   atual || $('ia-modelo-' + alvo).value.trim());
  } catch(e){ /* sem lista — segue com campo de texto livre */ }
}

// Lista direto no provedor com a chave recém-digitada (antes de salvar).
async function previewModelos(alvo){
  if (!temModelo(alvo)) return;   // áudio: nada a listar (whisper-1 fixo)
  const chave = $('ia-key-' + alvo).value.trim();
  if (!chave) return;
  msg(alvo, 'Listando modelos do provedor…', true);
  try {
    const d = await postar('/admin/ia/modelos-preview', {
      provedor: $('ia-prov-' + alvo).value,
      api_key: chave,
      base_url: $('ia-url-' + alvo).value.trim(),
    });
    popularModelos(alvo, (d.modelos || []).map(m => m.valor), null);
    msg(alvo, 'Modelos carregados — escolha um e salve a chave.', true);
  } catch(e){ msg(alvo, 'Falha ao listar modelos: ' + e.message, false); }
}

function aoTrocarProvedor(alvo){
  if ($('ia-prov-' + alvo).value === provedorSalvo[alvo]){
    // Voltou ao provedor já salvo — lista com a chave armazenada.
    resetarModelos(alvo);
    carregarModelos(alvo, null);
    return;
  }
  resetarModelos(alvo);
  if ($('ia-key-' + alvo).value.trim()) previewModelos(alvo);
  else if (temModelo(alvo)) msg(alvo, 'Cole a chave do provedor para listar os modelos dele.', true);
  else msg(alvo, 'Cole a chave do provedor e salve.', true);
}

function modeloEscolhido(alvo){
  const sel = $('ia-modelo-sel-' + alvo);
  if (sel.style.display !== 'none' && sel.value && sel.value !== '__outro') return sel.value;
  return $('ia-modelo-' + alvo).value.trim();
}

/* ---------- ações ---------- */

async function postar(url, dados){
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams(dados),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.erro || d.detail || ('HTTP ' + r.status));
  return d;
}

async function salvarChave(alvo, btn){
  const chave = $('ia-key-' + alvo).value.trim();
  if (!chave){ msg(alvo, 'Cole a chave antes de salvar.', false); return; }
  btn.disabled = true; msg(alvo, 'Gravando chave…', true);
  try {
    await postar('/admin/ia/credencial', {
      alvo,
      provedor: $('ia-prov-' + alvo).value,
      api_key: chave,
      base_url: $('ia-url-' + alvo).value.trim(),
    });
    $('ia-key-' + alvo).value = '';   // chave some da tela na hora
    $('ia-chave-' + alvo).hidden = true;
    msg(alvo, 'Chave gravada.', true);
    carregarEstado();                  // re-lista modelos do provedor novo
  } catch(e){ msg(alvo, 'Falha: ' + e.message, false); }
  finally { btn.disabled = false; }
}

async function salvarModelo(alvo, btn){
  const modelo = modeloEscolhido(alvo);
  if (!modelo){ msg(alvo, 'Informe o nome do modelo.', false); return; }
  btn.disabled = true; msg(alvo, 'Atualizando modelo…', true);
  try {
    await postar('/admin/ia/modelo', { alvo, modelo });
    msg(alvo, 'Modelo atualizado.', true);
    if (provedorSalvo[alvo]){
      const nome = (PROVEDORES[provedorSalvo[alvo]] || {}).nome || provedorSalvo[alvo];
      $('ia-atual-' + alvo).textContent = nome + ' · ' + modelo;
    }
  } catch(e){ msg(alvo, 'Falha: ' + e.message, false); }
  finally { btn.disabled = false; }
}

for (const alvo of ALVOS) montarSelect(alvo);
document.querySelectorAll('[data-ia-key]').forEach(b =>
  b.addEventListener('click', () => salvarChave(b.dataset.iaKey, b)));
document.querySelectorAll('[data-ia-modelo]').forEach(b =>
  b.addEventListener('click', () => salvarModelo(b.dataset.iaModelo, b)));
document.querySelectorAll('.ia-busca').forEach(inp =>
  inp.addEventListener('input', () => renderOpcoes(inp.id.replace('ia-busca-', ''), inp.value)));

if ($('ia-prov-texto')) carregarEstado();  // so carrega se o card IA estiver na pagina
