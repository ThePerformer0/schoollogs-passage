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

## 25 novembre 2026 : Modèles, authentification, cloisonnement, tests boîte noire et interface

### Outils utilisés
- **Antigravity (Gemini)** : développement backend (modèles, contraintes BD, migrations, repositories cloisonnés, services d'authentification et d'audit, admin restreinte, script de peuplement) et intégration frontend (`app.css`, `login.js`, gabarits Django).
- **Claude (compte gratuit, conversation dédiée n° 1)** : écriture des 62 tests critiques boîte noire (`passage/tests/test_lot1_blackbox.py`) uniquement à partir des exigences et du contrat d'interface, sans jamais voir le code source.
- **Claude (compte gratuit, conversation dédiée n° 2)** : conception d'un guide visuel sobre (`DESIGN.md`, conservé en local hors dépôt) basé sur la charte publique de SchoolLogs (`#7C3AED`, logo « SL »), sans dépendance externe.

### Ce qui a été confié aux IA
- **À Antigravity (Gemini)** : création des 16 modèles dans `passage/models/` et de `0001_initial.py`, couche d'accès cloisonnée (`passage/repositories/`), authentification Argon2id avec blocage par couple `(identifiant, IP)` et sessions serveur, contrôle de rôles serveur (`passage/permissions.py`), administration Django restreinte au responsable (`passage/admin.py`), commande rejouable `seed_demo`, puis intégration de la feuille de style native `app.css` et des gabarits HTML.
- **À Claude (conversation 1)** : production de tests adversariaux vérifiant l'uniformité des messages d'erreur, le blocage temporaire, l'expiration réelle de session, la déconnexion effective, le cloisonnement parent et multi-établissements, le CSRF strict, l'échappement XSS, les contraintes BD et la rejouabilité de `seed_demo`.
- **À Claude (conversation 2)** : extraction des tokens CSS sobres et contrastés alignés sur SchoolLogs pour une lecture confortable sur téléphone en extérieur.

### Ce que j'ai fait moi-même
- Mise en place d'une séparation stricte entre l'IA qui code et l'IA qui écrit les tests, puis exécution de tous les tests sans en modifier une seule assertion (69 tests passés au vert).
- Test manuel complet dans le navigateur (desktop et vue mobile) de tous les rôles (Responsable, Agent, Parent) et revue critique de chaque écran.
- Relecture ligne par ligne de `DECISIONS.md` et pilotage des commits Git.

### Refus et corrections motivés

1. **Interdiction faite à l'assistant de code d'écrire ses propres tests métier**
   - *Situation* : l'assistant proposait de continuer à écrire lui-même les tests des fonctionnalités qu'il venait de coder.
   - *Correction et raison* : **refusé**. Pour éviter tout biais de complaisance (tests écrits pour valider l'implémentation plutôt que le besoin), j'ai confié l'écriture des tests critiques à Claude dans une conversation séparée sans accès au code source.

2. **Recadrage complet de `DECISIONS.md`**
   - *Proposition de l'assistant* : un fichier monté à 16 décisions avec des détails secondaires hors sujet, des formulations trop jargonneuses, et des hypothèses imprécises (supposer que l'agent utilise forcément son téléphone personnel).
   - *Correction et raison* : **corrigé**. Recadré à 10 décisions claires plus les écarts au modèle indicatif, rédigées en français simple compréhensible sans avoir codé le projet, et en parlant d'appareil de scan quel que soit le matériel fourni par l'établissement.

