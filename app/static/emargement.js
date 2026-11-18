// Émargement : l'enseignant signe du doigt (ou à la souris) dans le cadre.
(function () {
  var zone = document.getElementById('emarg-zone');
  if (!zone) return;
  var ctx = zone.getContext('2d');
  var message = document.getElementById('emarg-message');
  var dessine = false, vide = true;

  function reinitialiser() {
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, zone.width, zone.height);
    ctx.strokeStyle = '#0F1B2D'; ctx.lineWidth = 4; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    vide = true;
  }
  function position(e) {
    var r = zone.getBoundingClientRect();
    return { x: (e.clientX - r.left) * zone.width / r.width, y: (e.clientY - r.top) * zone.height / r.height };
  }
  zone.addEventListener('pointerdown', function (e) {
    dessine = true; zone.setPointerCapture(e.pointerId);
    var p = position(e); ctx.beginPath(); ctx.moveTo(p.x, p.y);
  });
  zone.addEventListener('pointermove', function (e) {
    if (!dessine) return;
    var p = position(e); ctx.lineTo(p.x, p.y); ctx.stroke(); vide = false;
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (t) { zone.addEventListener(t, function () { dessine = false; }); });

  document.getElementById('emarg-effacer').addEventListener('click', function () { reinitialiser(); message.textContent = ''; });
  document.getElementById('emarg-valider').addEventListener('click', function () {
    var nom = document.getElementById('emarg-nom').value.trim();
    if (!nom) { message.textContent = "Indiquez le nom de l'enseignant."; return; }
    if (vide) { message.textContent = "L'enseignant doit signer dans le cadre."; return; }
    message.textContent = 'Enregistrement…';
    fetch('/api/appel/' + window.SEANCE_ID + '/emargement', {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nom: nom, signature: zone.toDataURL('image/png') })
    }).then(function (r) { return r.json(); }).then(function (d) {
      message.textContent = d.message;
      if (d.ok) setTimeout(function () { window.location.hash = 'emargement'; window.location.reload(); }, 700);
    }).catch(function () { message.textContent = 'Réseau indisponible, réessayez.'; });
  });
  reinitialiser();
})();
