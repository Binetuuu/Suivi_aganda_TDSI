# Mettre TDSI Campus en ligne (gratuit, sans carte bancaire)

## Comment c'est organisé

```
Navigateur ──HTTPS──▶ Render (https://tdsi-campus.onrender.com) : l'application
                          │
                          ├──▶ Neon : base de données Postgres gratuite (comptes, présences, cours…)
                          └──▶ Brevo : envoi gratuit des e-mails (invitations, codes, absences)
```

Les trois services sont gratuits et ne demandent pas de carte bancaire.
- **Render** fait tourner l'application. Sur l'offre gratuite, le site s'endort après 15 minutes sans visite : la première ouverture prend alors environ 1 minute, ensuite il est rapide.
- Le disque de Render s'efface à chaque redémarrage. Tout est donc rangé dans la base **Neon**, y compris les fichiers de cours (10 Mo maximum par fichier).
- **Brevo** envoie les e-mails par internet (300 par jour gratuits), car Render bloque l'envoi « classique » (SMTP) sur l'offre gratuite.

---

## Vos tâches (environ 20 minutes)

Je ne peux pas créer ces comptes à votre place : ils sont à votre nom et protégés par vos mots de passe.

### 1. Mettre le code sur GitHub
- Créez un compte sur https://github.com si vous n'en avez pas.
- Créez un dépôt **privé** vide (bouton **New repository**), nommé par exemple `tdsi-campus`, sans README.
- Donnez-moi son adresse (`https://github.com/<vous>/tdsi-campus.git`) : j'y envoie le code. Aucun secret ni aucune donnée personnelle ne part : `.env`, les bases et les listes Excel sont exclus.

### 2. Créer la base de données (Neon)
- Créez un compte sur https://neon.tech (connexion possible avec GitHub ou Google), puis un projet nommé `tdsi`, région **Europe (Frankfurt)**.
- Dans **Connection details**, copiez la chaîne qui commence par `postgresql://` et se termine par `?sslmode=require`.
- Gardez-la pour l'étape 4 et ne l'envoyez à personne.

### 3. Préparer l'envoi des e-mails (Brevo)
- Créez un compte sur https://www.brevo.com (offre gratuite).
- Dans **Expéditeurs, domaines et IP dédiées → Expéditeurs**, ajoutez l'adresse qui enverra les e-mails (par exemple le Gmail de la classe) et validez-la avec le lien reçu.
- Dans **SMTP & API → Clés API**, créez une clé et copiez-la (elle commence par `xkeysib-`).

### 4. Créer le site sur Render
1. Créez un compte sur https://render.com avec **Sign in with GitHub**, et autorisez l'accès au dépôt `tdsi-campus`.
2. Cliquez sur **New → Blueprint** et choisissez le dépôt : Render lit le fichier `render.yaml` fourni.
3. Render vous demande quatre valeurs :

| Variable | À coller |
|---|---|
| `TDSI_DATABASE_URL` | la chaîne Neon de l'étape 2 |
| `TDSI_ADMIN_EMAIL` | l'e-mail de la responsable (son compte admin est créé automatiquement) |
| `TDSI_BREVO_API_KEY` | la clé Brevo de l'étape 3 |
| `TDSI_EMAIL_EXPEDITEUR` | l'adresse validée dans Brevo |

4. Cliquez sur **Apply**. La construction dure environ 5 minutes. Le site est ensuite en ligne à l'adresse affichée (ex. `https://tdsi-campus.onrender.com`).

La clé secrète est générée par Render (`generateValue`) : vous n'avez rien à faire pour elle.

### 5. Activer le compte de la responsable
- Au premier démarrage, la responsable reçoit l'e-mail « Activez votre compte TDSI Campus ». Elle ouvre le lien et choisit son mot de passe.
- Si l'e-mail n'arrive pas, le lien est aussi écrit dans **Render → Logs** (ligne `[TDSI] Lien d'activation…`).

### 6. Inviter les étudiants et les enseignants
- Connectez-vous en admin et ouvrez **Utilisateurs → Télécharger le modèle**. Remplissez-le, puis cliquez sur **Importer**.
- Chacun reçoit un e-mail avec un lien personnel pour activer son compte et choisir son mot de passe.
- Ceux qui ont raté l'e-mail utilisent le bouton **Activer mon compte** de la page de connexion.
- Dans **Planning**, créez les modules et les séances.

### 7. Le jour du cours
- La responsable ouvre le site sur son téléphone, puis **Scanner les présences → Scanner → Ouvrir l'appel → Démarrer la caméra**.
- Le site est en HTTPS, donc la caméra du téléphone fonctionne.
- Chaque étudiant présente sa carte (menu **Ma carte QR**).

---

## Mettre à jour le site
Chaque fois que le code est envoyé sur GitHub (branche `main`), Render reconstruit et republie le site tout seul.

## Limites de l'offre gratuite
- Site endormi après 15 minutes sans visite : ouvrez-le quelques minutes avant l'appel.
- Base Neon : 0,5 Go. Cela suffit largement pour les comptes et les présences ; pour les cours, préférez des PDF légers.
- Brevo : 300 e-mails par jour. Pour inviter plus de 300 personnes, importez la liste en deux jours.

## Plus tard : Firebase
Les fichiers `firebase.json`, `.firebaserc` et `env.prod.example.yaml` restent prêts. Une fois le forfait Blaze activé (avec une carte), on pourra passer sur Firebase Hosting et Cloud Run.
