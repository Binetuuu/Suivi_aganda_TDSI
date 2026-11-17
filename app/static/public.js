// Afficher / masquer le mot de passe.
document.querySelectorAll('[data-oeil]').forEach(function (bouton) {
  bouton.addEventListener('click', function () {
    var champ = document.getElementById(bouton.getAttribute('data-oeil'));
    var visible = champ.type === 'text';
    champ.type = visible ? 'password' : 'text';
    bouton.setAttribute('aria-label', visible ? 'Afficher le mot de passe' : 'Masquer le mot de passe');
    bouton.classList.toggle('actif', !visible);
  });
});

// Fond : réseau de points reliés qui dérivent lentement, avec des « paquets » qui
// circulent sur les liaisons. Image fixe si l'appareil demande moins d'animations.
(function () {
  var canvas = document.getElementById('reseau');
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext('2d');
  var calme = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var points = [], paquets = [], largeur = 0, hauteur = 0, ratio = 1, DISTANCE = 140;

  function initialiser() {
    ratio = Math.min(window.devicePixelRatio || 1, 2);
    largeur = canvas.clientWidth; hauteur = canvas.clientHeight;
    canvas.width = largeur * ratio; canvas.height = hauteur * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    var nombre = Math.max(28, Math.min(90, Math.round(largeur * hauteur / 16000)));
    points = [];
    for (var i = 0; i < nombre; i++) {
      points.push({ x: Math.random() * largeur, y: Math.random() * hauteur,
        vx: (Math.random() - .5) * .25, vy: (Math.random() - .5) * .25, r: 1 + Math.random() * 1.6 });
    }
    paquets = [];
  }

  function dessiner() {
    ctx.clearRect(0, 0, largeur, hauteur);
    var liaisons = [];
    for (var i = 0; i < points.length; i++) {
      for (var j = i + 1; j < points.length; j++) {
        var dx = points[i].x - points[j].x, dy = points[i].y - points[j].y;
        var d = Math.sqrt(dx * dx + dy * dy);
        if (d < DISTANCE) {
          liaisons.push([i, j]);
          ctx.strokeStyle = 'rgba(94,163,255,' + (0.28 * (1 - d / DISTANCE)).toFixed(3) + ')';
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(points[i].x, points[i].y); ctx.lineTo(points[j].x, points[j].y); ctx.stroke();
        }
      }
    }
    points.forEach(function (p) {
      ctx.fillStyle = 'rgba(127,184,255,.85)';
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
    });
    return liaisons;
  }

  function animer() {
    if (document.hidden) { requestAnimationFrame(animer); return; }
    points.forEach(function (p) {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > largeur) p.vx *= -1;
      if (p.y < 0 || p.y > hauteur) p.vy *= -1;
    });
    var liaisons = dessiner();
    if (liaisons.length && paquets.length < 6 && Math.random() < 0.04) {
      var l = liaisons[Math.floor(Math.random() * liaisons.length)];
      paquets.push({ a: l[0], b: l[1], t: 0 });
    }
    paquets = paquets.filter(function (q) {
      q.t += 0.012;
      var a = points[q.a], b = points[q.b];
      var x = a.x + (b.x - a.x) * q.t, y = a.y + (b.y - a.y) * q.t;
      ctx.fillStyle = 'rgba(245,163,92,.95)';
      ctx.shadowColor = '#F5A35C'; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
      return q.t < 1;
    });
    requestAnimationFrame(animer);
  }

  initialiser();
  window.addEventListener('resize', function () { initialiser(); if (calme) dessiner(); });
  if (calme) dessiner(); else requestAnimationFrame(animer);
})();
