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


### Suite du 25 novembre 2026 : Détection des anomalies et planificateur autonome
- **Antigravity (Gemini)** : service `anomalies.py` (5 types d'anomalies, résolution tardive automatique, traitement responsable), commande `run_scheduler`, branchement dans `sync.py`, bouton « Traiter » et affichage de l'itinéraire sur la fiche élève.
- **Claude (conversation dédiée n° 1)** : écriture des 29 tests boîte noire (`passage/tests/test_lot3_blackbox.py`), passés du premier coup (130 tests au total).


---

## 26 et 27 novembre 2026 : Notifications parents, synthèse IA, ergonomie, banc démo et PWA installable

### Outils utilisés
- **Antigravity (Gemini)** : service de notifications sortantes derrière une interface fournisseur isolée (`notification_gateway.py`, `notifications.py`), service de synthèse assistée par IA (`llm_gateway.py`, `summaries.py`), vue `/responsable/synthese/`, améliorations d'ergonomie (navigation fixe, menu mobile, filtres instantanés et pagination des tableaux, disposition mobile des boutons d'action, sélecteur d'heure `type="time"`), banc de démonstration (`DEMO_MODE=0` par défaut) et finalisation de la PWA installable (icônes PNG 180/192/512, balises iOS et bouton d'installation).
- **Claude (conversation dédiée n° 1)** : écriture des 100 tests boîte noire couvrant les notifications, le plafond journalier, les 4 garde-fous IA et la fidélité numérique (`passage/tests/test_lot4_blackbox.py`), portant la suite à **230 tests au vert**.
- **API Gemini (`gemini-2.5-flash`)** : modèle appelé en cours d'exécution par le produit pour rédiger les synthèses quotidiennes uniquement à partir des chiffres agrégés calculés côté serveur, sans aucun nom d'élève ni donnée sensible.

### Ce que j'ai fait moi-même
- Vérification de la confidentialité côté IA (aucun numéro de parent ni nom d'élève transmis au modèle) et essais réels avec une clé Gemini active puis en mode dégradé sans clé.
- Revue ergonomique complète sur PC et téléphone (navigation fixe, pagination, filtres, boutons mobiles, formulaire d'itinéraire, signalement immédiat d'un rejet serveur en ligne côté Agent).
- Enrichissement du jeu de démonstration (20 élèves sur 3 classes, 6 parents, 5 jours ouvrés) et exécution de la suite complète en conditions CI (`DEBUG=0`, `DEMO_MODE=0`, sans clé API : **230/230 OK**).

### Refus et corrections motivés

1. **Correction de deux anomalies révélées par les tests boîte noire et l'essai réel sur Gemini 2.5**
   - *Situation* : les tests boîte noire ont montré que Python 3.12 acceptait une date compacte sans tirets (`20261123`) ; par ailleurs, l'appel réel à `gemini-2.5-flash` a d'abord renvoyé un JSON tronqué car les jetons de réflexion interne du modèle consommaient le budget de sortie.
   - *Correction et raison* : **corrigé**. Validation stricte `AAAA-MM-JJ` ajoutée dans `views/summaries.py`, et ajustement de `GeminiLLMGateway` (`thinkingBudget: 0`, `responseSchema` strict et filtrage des blocs de réflexion).

---

## 28 novembre 2026 : Production des deux vidéos (capsule de communication et démonstration technique)

### Outils utilisés
- **ElevenLabs (modèle Eleven v3)** : génération de la voix off française expressive de la capsule de communication (1 min 14 s).
- **Antigravity (Gemini)** : création des visuels 16:9 aux couleurs SchoolLogs (écran de titre, image de fin, mise en page des captures réelles de l'application), analyse acoustique des pauses de la voix off pour générer les sous-titres synchronisés (`sous_titres.srt`), et raccourcissement local via `FFmpeg` de la démonstration technique (accélération des scans répétitifs, compression des silences et normalisation audio).
- **Claude (conversation dédiée)** : assemblage de la capsule de communication en MP4 1080p via Python et `FFmpeg` (transitions fondues, léger mouvement, incrustation des sous-titres français, durée finale 1 min 18 s).
- **OBS Studio** : capture d'écran commentée en direct des 7 scénarios de la démonstration technique entre PC et téléphone.

### Ce que j'ai fait moi-même
- Captures d'écran de l'application réelle, pilotage de la voix off sur ElevenLabs, vérification visuelle et auditive de la capsule montée, puis enregistrement en direct des 7 scénarios techniques sur OBS.
