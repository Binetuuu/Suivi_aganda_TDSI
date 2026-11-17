// Case « Afficher le mot de passe ».
document.querySelectorAll('[data-afficher]').forEach(function (caseACocher) {
  caseACocher.addEventListener('change', function () {
    var champ = document.getElementById(caseACocher.getAttribute('data-afficher'));
    if (champ) champ.type = caseACocher.checked ? 'text' : 'password';
  });
});

// Profil choisi : adapte le libellé et l'exemple du champ identifiant.
document.querySelectorAll('input[name="profil"]').forEach(function (radio) {
  radio.addEventListener('change', function () {
    var etudiant = radio.value === 'etudiant';
    var libelle = document.querySelector('[data-libelle-identifiant]');
    var champ = document.getElementById('identifiant');
    if (libelle) libelle.textContent = etudiant ? 'Matricule ou e-mail' : 'Adresse e-mail';
    if (champ) champ.placeholder = etudiant ? 'ex. TDSI-L3-0142' : 'prenom.nom@exemple.sn';
  });
});
