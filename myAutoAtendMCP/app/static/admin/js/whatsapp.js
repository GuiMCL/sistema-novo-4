/* Card "Conexão WhatsApp": estado da instância, QR Code e desconexão. */

import { toast } from './toast.js';

const elStatus  = document.getElementById('wa-status');
const elHint    = document.getElementById('wa-hint');
const elQr      = document.getElementById('wa-qr');
const elCode    = document.getElementById('wa-code');
const elMsg     = document.getElementById('wa-msg');
const btnCon    = document.getElementById('wa-connect');
const btnOut    = document.getElementById('wa-logout');
const elPerfil     = document.getElementById('wa-perfil');
const elPerfilAva  = document.getElementById('wa-perfil-ava');
const elPerfilNome = document.getElementById('wa-perfil-nome');
const elPerfilNum  = document.getElementById('wa-perfil-num');
let pollTimer   = null;

function pill(cls, txt){ elStatus.className = 'pill ' + cls; elStatus.textContent = txt; }

function showQrBox(on){
  elQr.style.display   = on ? '' : 'none';
  elCode.style.display = (on && elCode.textContent) ? '' : 'none';
  elHint.style.display = on ? 'none' : '';
}

function fotoPerfil(url){
  elPerfilAva.style.backgroundImage = "url('" + url + "')";
  elPerfilAva.classList.add('loaded');
}

/* Identidade do número conectado (prova visual de que o pareamento está ok). */
function mostrarPerfil(p){
  if (!p || !p.numero){ elPerfil.style.display = 'none'; return; }
  elPerfil.style.display = '';
  elPerfilNome.textContent = p.nome || 'WhatsApp conectado';
  elPerfilNum.textContent = p.numero_fmt || p.numero;
  elPerfilAva.textContent = (p.nome || 'W').trim().charAt(0).toUpperCase();
  if (p.foto){ fotoPerfil(p.foto); return; }
  // fetchInstances às vezes vem sem a foto — tenta a rota cacheada por número
  fetch('/admin/whatsapp/foto?numero=' + encodeURIComponent(p.numero))
    .then(r => r.ok ? r.json() : null)
    .then(d => { if (d && d.url) fotoPerfil(d.url); })
    .catch(() => { /* sem foto → fica a inicial */ });
}

function render(state, perfil){
  if (state === 'open'){
    pill('on', 'conectado');
    elHint.textContent = 'WhatsApp conectado.';
    showQrBox(false);
    mostrarPerfil(perfil);
    elMsg.innerHTML = 'Número conectado e atendendo. Para trocar de número, desconecte primeiro.';
    btnCon.style.display = 'none';
    btnOut.style.display = '';
  } else if (state === 'connecting'){
    elPerfil.style.display = 'none';
    pill('connecting', 'aguardando leitura');
    btnCon.style.display = '';
    btnCon.textContent = 'Gerar novo QR';
    btnOut.style.display = '';
  } else {
    elPerfil.style.display = 'none';
    pill('off', 'desconectado');
    showQrBox(false);
    elHint.textContent = 'Sem conexão. Gere o QR Code para parear.';
    btnCon.style.display = '';
    btnCon.textContent = 'Gerar QR Code';
    btnOut.style.display = 'none';
  }
}

async function estado(){
  try {
    const r = await fetch('/admin/whatsapp/estado');
    const d = await r.json();
    if (d.erro){ pill('off', 'erro'); elHint.textContent = 'Evolution API indisponível.'; return null; }
    const st = (d.instance && d.instance.state) || 'close';
    render(st, d.perfil);
    if (st === 'open' && pollTimer){ clearInterval(pollTimer); pollTimer = null; }
    return st;
  } catch(e){ pill('off', 'erro'); return null; }
}

function startPolling(){
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(estado, 4000);
}

async function gerarQr(){
  btnCon.disabled = true; elHint.textContent = 'Gerando QR Code…'; elHint.style.display = '';
  try {
    const r = await fetch('/admin/whatsapp/qr');
    const d = await r.json();
    if (d.erro){ elHint.textContent = 'Falha: ' + d.erro; return; }
    if (d.instance && d.instance.state === 'open'){ estado(); return; }  // já conectado — busca estado com perfil
    const b64 = d.base64 || '';
    if (b64){
      elQr.src = b64.startsWith('data:') ? b64 : ('data:image/png;base64,' + b64);
      elCode.textContent = d.pairingCode || '';
      showQrBox(true);
      render('connecting');
      startPolling();
    } else {
      elHint.textContent = 'Resposta sem QR. Tente novamente.';
    }
  } catch(e){ elHint.textContent = 'Erro ao gerar QR Code.'; }
  finally { btnCon.disabled = false; }
}

async function desconectar(){
  if (!confirm('Desconectar o WhatsApp deste número?')) return;
  btnOut.disabled = true;
  try {
    const r = await fetch('/admin/whatsapp/desconectar', {method:'POST'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
  } catch(e){ toast('erro', 'Não foi possível desconectar agora. Tente de novo em instantes.'); }
  finally { btnOut.disabled = false; estado(); }
}

btnCon.addEventListener('click', gerarQr);
btnOut.addEventListener('click', desconectar);
estado();
