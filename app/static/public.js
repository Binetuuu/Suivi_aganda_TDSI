// Case « Afficher le mot de passe ».
document.querySelectorAll('[data-afficher]').forEach(function (caseACocher) {
  caseACocher.addEventListener('change', function () {
    var champ = document.getElementById(caseACocher.getAttribute('data-afficher'));
    if (champ) champ.type = caseACocher.checked ? 'text' : 'password';
  });
});
