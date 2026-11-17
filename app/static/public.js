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
