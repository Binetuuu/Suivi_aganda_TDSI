// Appel : lecture des QR personnels à la caméra, saisie manuelle du matricule et mise à jour en direct.
(function () {
  var id = window.SEANCE_ID;
  var resultat = document.getElementById('resultat');
  var titre = document.getElementById('res-titre');
  var dernierToken = '', dernierInstant = 0, lecteur = null;

  function afficher(r) {
    resultat.className = 'resultat ' + (r.ok ? 'ok' : 'erreur');
    titre.textContent = r.message;
    if (r.ok && navigator.vibrate) navigator.vibrate(80);
    rafraichir();
  }

  function envoyer(url, corps) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corps)
    }).then(function (r) { return r.json(); })
      .then(afficher)
      .catch(function () { afficher({ ok: false, message: 'Réseau indisponible, réessayez.' }); });
  }

  function rafraichir() {
    fetch('/api/appel/' + id + '/etat', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (etat) {
        ['presents', 'absents', 'total'].forEach(function (k) {
          document.querySelectorAll('[data-compte="' + k + '"]').forEach(function (el) { el.textContent = etat[k]; });
        });
        etat.etudiants.forEach(function (e) {
          var bouton = document.querySelector('[data-eleve="' + e.id + '"]');
          if (!bouton) return;
          bouton.className = 'eleve ' + e.statut;
          var avatar = bouton.querySelector('.avatar');
          avatar.className = 'avatar ' + (e.statut === 'present' ? 'vert' : 'gris');
          bouton.querySelector('[data-statut]').textContent = e.statut === 'present'
            ? 'Présent · ' + e.heure + ' · ' + (e.via === 'qr' ? 'QR' : 'manuel')
            : (e.statut === 'absent' ? 'Absent' : 'Pas encore scanné');
        });
      }).catch(function () {});
  }

  function surLecture(texte) {
    var maintenant = Date.now();
    if (texte === dernierToken && maintenant - dernierInstant < 4000) return; // même carte encore devant la caméra
    dernierToken = texte; dernierInstant = maintenant;
    envoyer('/api/appel/' + id + '/scan', { token: texte });
  }

  document.getElementById('demarrer').addEventListener('click', function () {
    var bouton = this;
    if (lecteur) {
      lecteur.stop().then(function () { lecteur = null; bouton.textContent = 'Démarrer la caméra'; });
      return;
    }
    if (typeof Html5Qrcode === 'undefined') {
      afficher({ ok: false, message: 'Lecteur QR indisponible (pas d’accès internet ?). Utilisez la saisie du matricule.' });
      return;
    }
    document.getElementById('lecteur').textContent = '';
    lecteur = new Html5Qrcode('lecteur');
    lecteur.start({ facingMode: 'environment' }, { fps: 10, qrbox: { width: 220, height: 220 } }, surLecture, function () {})
      .then(function () { bouton.textContent = 'Arrêter la caméra'; })
      .catch(function () {
        lecteur = null;
        afficher({ ok: false, message: 'Caméra inaccessible : autorisez-la, et utilisez HTTPS en dehors de localhost.' });
      });
  });

  document.getElementById('saisie').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var champ = document.getElementById('matricule');
    envoyer('/api/appel/' + id + '/manuel', { matricule: champ.value }).then(function () { champ.value = ''; champ.focus(); });
  });

  setInterval(rafraichir, 5000);
})();
