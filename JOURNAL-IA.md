# Journal d'usage de l'intelligence artificielle

Ce journal recense, jour par jour, les outils d'IA utilisés sur ce projet, ce qui leur a été confié, ce que j'ai réalisé ou arbitré moi-même, ainsi que les propositions que j'ai refusées ou corrigées.

---

## 24 novembre 2026 : Initialisation du socle et de la documentation

### Outils utilisés
- **Antigravity (Gemini)** : assistant de code pour vérifier les bonnes pratiques Django 5.2 LTS / SQLite WAL / Argon2, initialiser le projet, créer la CI GitHub Actions et poser la structure documentaire.

### Ce qui a été confié à l'IA
- Vérification des options natives SQLite de Django 5.2 (`init_command`, `transaction_mode="IMMEDIATE"`, `timeout`), du comportement de `PRAGMA journal_mode=WAL` avec les tests en mémoire, et de `Argon2PasswordHasher`.
- Génération du socle initial : `.gitignore`, `.env.example` sans secret, `requirements.txt` figé, projet Django (`config/`, `passage/`), premiers tests smoke (`passage/tests/test_smoke.py`) et workflow `.github/workflows/ci.yml`.
- Premières versions de `README.md`, `DECISIONS.md` et `JOURNAL-IA.md`.

### Ce que j'ai fait moi-même
- Analyse complète du sujet Afric'Éduc, priorisation des fonctionnalités et définition du plan de travail.
- Arbitrage de tous les choix structurants avant toute génération de fichier (structure en couches, langue du code, absence de Docker non testé, plafond quotidien de coût IA, fichiers exclus du dépôt public).
- Relecture des fichiers générés, vérification locale des tests et contrôle avant commit.

### Refus et corrections motivés

1. **Refus des identifiants de code en français proposés par l'assistant**
   - *Proposition de l'IA* : nommer les modèles et modules Python en français (`Eleve`, `PointControle`, `Anomalie`) pour coller au vocabulaire du sujet.
   - *Correction et raison* : **refusé**. J'ai imposé l'anglais pour tous les identifiants de code (`Student`, `Checkpoint`, `Anomaly`...) afin de respecter les conventions standards Django/Python et garantir la maintenabilité, tout en gardant le français pour l'interface utilisateur, la documentation et les commits.

2. **Abandon de l'ajout de fichiers Docker non exécutés localement**
   - *Piste envisagée* : joindre un `Dockerfile` et un `docker-compose.yml` sans pouvoir les tester localement (Docker absent sur la machine).
   - *Correction et raison* : **écarté**. L'énoncé autorise un lancement local simple par commandes décrites dans le `README.md`. Livrer une configuration Docker jamais exécutée créait un risque élevé d'échec lors de la reprise du projet.

---

## 25 novembre 2026 : Socle applicatif, badges rotatifs, synchronisation hors ligne et PWA

### Outils utilisés
- **Antigravity (Gemini)** : développement backend (modèles, authentification, cloisonnement, badges rotatifs HMAC-SHA256, liens d'activation uniques, horloge, synchronisation idempotente, API) et frontend PWA (`app.css`, `pwa.py`, scripts JS natifs avec bibliothèques QR locales).
- **Claude (compte gratuit, conversation dédiée n° 1)** : écriture des 94 tests critiques boîte noire (`passage/tests/test_lot1_blackbox.py` : 62 tests ; `passage/tests/test_lot2_blackbox.py` : 32 tests) uniquement à partir des comportements attendus et du contrat d'interface, sans jamais voir le code source.
- **Claude (compte gratuit, conversation dédiée n° 2)** : conception d'un guide visuel sobre (`DESIGN.md`, conservé en local hors dépôt) basé sur la charte publique de SchoolLogs (`#7C3AED`, logo « SL »), sans dépendance externe.

### Ce qui a été confié aux IA
- **À Antigravity (Gemini)** :
  - Socle complet : 16 modèles (`passage/models/`) et migration initiale, couche d'accès cloisonnée (`passage/repositories/`), authentification Argon2id avec blocage `(identifiant, IP)` et sessions serveur, contrôle de rôles (`passage/permissions.py`), « Gestion des fiches » restreinte au responsable (`passage/admin.py`), commande `seed_demo`, feuille de style `app.css` et gabarits HTML.
  - Badges et hors ligne : services `badges.py`, `clock.py`, `sync.py`, API JSON (`/api/time/`, `/api/checkpoints/`, `/api/sync/`), PWA unique (`/manifest.webmanifest`, `/sw.js`), scripts `badge_crypto.js` (Web Crypto avec repli SHA-256 pur JS pour HTTP local), `my_badge.js`, `agent_scan.js` (caméra `jsQR`, file `localStorage`, reprise après déconnexion/expiration) et bibliothèques QR locales (`jsQR.js`, `qrcode.js`).
- **À Claude (conversation 1)** : production des tests boîte noire couvrant l'authentification, le cloisonnement, le CSRF, l'anti-XSS, le cycle de vie des badges, les liens d'activation à usage unique, l'anti-rejeu, l'idempotence de synchronisation sur 20 badges, l'interruption réseau et les décalages d'horloge (2 h et horloge aberrante).
- **À Claude (conversation 2)** : extraction des tokens CSS sobres et contrastés alignés sur SchoolLogs pour une lecture confortable sur téléphone en extérieur.

### Ce que j'ai fait moi-même
- Mise en place d'une séparation stricte entre l'IA qui code et l'IA qui écrit les tests, revue critique des brouillons de tests avant exécution, et exécution de toute la suite sans affaiblir aucune assertion (101 tests passés au vert).
- Tests manuels complets sur navigateur (PC et vue mobile) puis tests réels bout-en-bout entre PC (caméra Agent `/scan/`) et téléphone sur le même Wi-Fi : activation du badge, rotation hors ligne, photo expirée, rejeu immédiat, scan hors ligne avec rechargement d'onglet, lien déjà utilisé et badge suspendu (`BADGE_NON_ACTIF`).
- Relecture ligne par ligne de `DECISIONS.md`, revue ergonomique des écrans (notamment le tableau « Badges émis ») et pilotage de tous les commits Git.

### Refus et corrections motivés

1. **Interdiction faite à l'assistant de code d'écrire ses propres tests métier**
   - *Situation* : l'assistant proposait d'écrire lui-même les tests des fonctionnalités qu'il venait de coder.
   - *Correction et raison* : **refusé**. Pour éviter tout biais de complaisance, l'écriture des tests critiques a été confiée à Claude dans une conversation séparée sans accès au code source.

2. **Recadrage complet de `DECISIONS.md`**
   - *Proposition de l'assistant* : un fichier monté à 16 décisions avec des détails secondaires hors sujet, du jargon technique et des hypothèses non demandées (supposer que l'agent utilise son téléphone personnel).
   - *Correction et raison* : **corrigé**. Recadré à 10 décisions claires plus les écarts au modèle indicatif, rédigées en français simple compréhensible sans avoir codé le projet.

3. **Correction du signalement d'horloge aberrante et refonte ergonomique du tableau « Badges émis »**
   - *Situation* : les tests boîte noire sur une horloge aberrante ont révélé qu'un décalage nul pouvait masquer l'indicateur « horloge suspecte » lorsqu'une heure locale aberrante retombait sur l'heure serveur ; par ailleurs, la première interface des actions de badge empilait une liste déroulante pleine largeur et un bouton « Appliquer » peu lisible.
   - *Correction et raison* : **corrigé**. Le service `clock.py` marque désormais aussi les heures non plausibles comme horloge suspecte, et le tableau « Badges émis » a été remplacé par une barre d'actions compacte à icônes SVG inline avec légende. J'ai également refusé l'ajout non demandé d'un bandeau de consigne supplémentaire sur la page d'activation.

