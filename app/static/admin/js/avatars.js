/* Avatares do WhatsApp na tabela de agendamentos (foto de perfil por número). */

// Agrupa por número p/ buscar a foto uma vez só por cliente.
const porNumero = {};
document.querySelectorAll('.ava[data-num]').forEach(el => {
  (porNumero[el.dataset.num] = porNumero[el.dataset.num] || []).push(el);
});
Object.entries(porNumero).forEach(async ([num, els]) => {
  try {
    const r = await fetch('/admin/whatsapp/foto?numero=' + encodeURIComponent(num));
    if (!r.ok) return;
    const d = await r.json();
    if (!d.url) return; // sem foto / privada → fica a inicial
    els.forEach(el => {
      el.style.backgroundImage = "url('" + d.url + "')";
      el.classList.add('loaded');
    });
  } catch (_) { /* falha de rede → mantém fallback de inicial */ }
});
