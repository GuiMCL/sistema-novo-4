/* Estado do número aprovado na WhatsApp Cloud API (Meta). */
const status = document.getElementById('wa-status');
const msg = document.getElementById('wa-msg');
const perfil = document.getElementById('wa-perfil');
function pill(cls, text) { status.className = 'pill ' + cls; status.textContent = text; }
fetch('/admin/whatsapp/estado').then(r => r.json()).then(d => {
  if (d.state !== 'connected') { pill('off', d.state === 'not_configured' ? 'não configurado' : 'erro'); if (d.erro) msg.textContent = d.erro; return; }
  pill('on', 'conectado'); const p = d.perfil || {}; perfil.style.display = '';
  document.getElementById('wa-perfil-ava').textContent = (p.nome || 'W').charAt(0).toUpperCase();
  document.getElementById('wa-perfil-nome').textContent = p.nome || 'Número aprovado';
  document.getElementById('wa-perfil-num').textContent = p.numero_fmt || p.numero || '';
  msg.textContent = 'Número aprovado e pronto para receber eventos da Meta.';
}).catch(() => pill('off', 'erro'));
