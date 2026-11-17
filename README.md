# TDSI Campus · application de suivi de cours

Application web pour une classe de TDSI :

- **Inscription par e-mail** : chaque personne reçoit un lien personnel pour activer son compte et choisir son mot de passe. Connexion par matricule, e-mail ou téléphone, puis code de confirmation par e-mail (pas de SMS).
- **Carte QR personnelle et permanente** pour chaque étudiant, avec son nom. Elle est signée : impossible d'en fabriquer une fausse. Une carte perdue est révoquée par l'admin (« Nouveau QR »).
- **Appel** : la responsable (admin) scanne les cartes avec la caméra (menu « Scanner les présences »), ou saisit le matricule. À la clôture, les absents sont enregistrés et prévenus par e-mail.
- **Cahier de texte numérique**, rempli par l'enseignant et validé par le délégué.
- **Emploi du temps**, **supports de cours** (dépôt et téléchargement), **absences et justificatifs**.
- **Suivi individuel** : taux de présence global et par module, alerte sous 85 %.
- **Heures des enseignants** (base du paiement) : une séance est comptée quand l'appel est clôturé ou que l'admin la confirme ; annulation avec motif, correction de la durée réelle, état par enseignant et par UE sur une période, export Excel. Chaque enseignant consulte ses heures (« Mes heures ») sans pouvoir les modifier.
- **Console admin** : chiffres du jour, appels en direct, alertes, justificatifs, import des comptes, planning et journal d'activité.

Technique : Python 3.11+, FastAPI, SQLAlchemy (SQLite par défaut), gabarits Jinja2.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac : source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env          # puis remplir TDSI_SECRET_KEY
```

## Créer le premier compte administrateur

```bash
python -m app.seed --admin-email votre.adresse@exemple.sn
```

La commande affiche un lien (envoyé aussi par e-mail) pour choisir le mot de passe de l'admin.

## Lancer l'application

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Ouvrez ensuite http://localhost:8000.

## Ajouter les étudiants et les enseignants

Dans **Console admin → Utilisateurs**, téléchargez le modèle puis importez votre fichier Excel ou CSV.
Il doit avoir ces colonnes :

| matricule | nom | prenom | role | email | telephone | classe |
|---|---|---|---|---|---|---|
| TDSI-L3-0001 | Diop | Moussa | etudiant | … | +221… | L3 TDSI |
| | Sarr | Ibrahima | enseignant | … | … | |

- Chaque nouveau compte reçoit par e-mail un lien pour activer son compte et choisir son mot de passe (l'e-mail est donc obligatoire). Chaque étudiant a sa carte QR.
- Réimporter le fichier met à jour les comptes existants (même matricule, e-mail ou téléphone).
- Les numéros sont masqués à l'écran.

Gardez la liste réelle hors de git : le `.gitignore` exclut déjà `*.xlsx`, `imports/` et la base de données.
Ensuite, dans **Planning**, créez les modules (avec leur enseignant) et les séances, qui peuvent se répéter chaque semaine.

## Mettre en production

Le guide pas à pas (Render + Neon + Brevo, gratuit et sans carte) est dans [DEPLOIEMENT.md](DEPLOIEMENT.md). Points généraux :

- **HTTPS obligatoire.** Les téléphones n'ouvrent la caméra que sur un site sécurisé. Placez l'application derrière Nginx ou Caddy avec un certificat, et mettez `TDSI_COOKIE_SECURE=1`.
- **E-mails** : voir la section suivante.
- **Sauvegardes** : sauvegardez régulièrement `tdsi.db` et le dossier `uploads/`.

## Envoyer de vrais e-mails gratuitement (Gmail)

Gmail permet d'envoyer environ 500 e-mails par jour sans payer, ce qui suffit pour une classe.

1. Créez (ou utilisez) un compte Gmail dédié, par exemple pour la classe.
2. Activez la **validation en deux étapes** sur ce compte.
3. Ouvrez https://myaccount.google.com/apppasswords et créez un **mot de passe d'application** (16 lettres).
4. Dans votre fichier `.env`, renseignez :

   ```
   TDSI_NOTIF_BACKEND=smtp
   TDSI_SMTP_HOST=smtp.gmail.com
   TDSI_SMTP_PORT=587
   TDSI_SMTP_USER=adresse.de.la.classe@gmail.com
   TDSI_SMTP_PASSWORD=le-mot-de-passe-d-application
   ```

5. Vérifiez l'envoi : `python -m app.seed --test-email votre.adresse@gmail.com`

Ce mot de passe d'application reste dans `.env`, qui n'est jamais enregistré dans git. Ne le partagez avec personne.
Autre option gratuite : Brevo (300 e-mails par jour, serveur `smtp-relay.brevo.com`).

Tant que `TDSI_NOTIF_BACKEND=console`, aucun e-mail ne part : les codes s'affichent dans le terminal du serveur.

## Essayer avec des données fictives

```bash
set TDSI_DATABASE_URL=sqlite:///demo.db
python -m app.seed --demo
uvicorn app.main:app --port 8000
```

Les comptes `admin@demo.tdsi`, `prof.alpha@demo.tdsi` et `DEMO-L3-0001` à `DEMO-L3-0008` ont tous le mot de passe `demo-tdsi-2026`.
Ajoutez `set TDSI_OTP=0` pour vous connecter sans code pendant les essais.

## Tests

```bash
pytest
```
