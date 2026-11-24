# Journal d'usage de l'intelligence artificielle (`JOURNAL-IA.md`)

Ce journal trace de façon factuelle et quotidienne les outils d'IA utilisés, leur contribution, ce que j'ai réalisé ou arbitré moi-même, ainsi que les propositions de l'IA refusées ou corrigées.

---

## 24 novembre 2026 — Lot 1, Tâche 1 : Initialisation du socle et de la documentation

### Outils utilisés
- **Assistant de code IA** : Antigravity (modèle Gemini) pour la vérification des bonnes pratiques Django 5.2 LTS / SQLite WAL / Argon2, la structuration du socle initial, le workflow CI et la mise en forme de la documentation de base.
- **Outils vidéo / voix** : aucun utilisé ce jour (production de la démonstration technique et de la capsule de communication prévue au Lot 5 ; les outils retenus seront nommés dès leur utilisation).

### Ce que l'IA a produit
- Vérification documentaire des options SQLite natives de Django 5.2 LTS (`init_command`, `transaction_mode="IMMEDIATE"`, `timeout`), du fonctionnement de `PRAGMA journal_mode=WAL` face à la base de test `:memory:`, et de `Argon2PasswordHasher`.
- Proposition d'arborescence en couches (interface / logique métier / accès aux données).
- Génération des fichiers initiaux : `.gitignore`, `.env.example` (ensemble des paramètres configurables sans aucun secret), `requirements.txt` (liste courte et figée), `manage.py`, `config/`, `passage/`, test smoke `passage/tests/test_smoke.py`, workflow `.github/workflows/ci.yml`.
- Rédaction initiale de `README.md`, `DECISIONS.md` (D1 à D14) et ouverture de `JOURNAL-IA.md`.

### Ce que j'ai fait moi-même
- Analyse de l'énoncé Afric'Éduc et définition du cahier des charges complet du projet (périmètre MVP, priorités par lots, règles de sécurité et plan de tests).
- Arbitrage explicite de tous les choix structurants avant toute génération de fichier (langue des identifiants de code, maintenabilité de l'architecture, exclusion de Docker non testé, valeur du plafond de coût IA, exclusion des documents internes du dépôt public).
- Relecture des fichiers générés, contrôle du passage au vert des vérifications Django et des tests smoke, et validation finale avant commit.

### Refus et corrections motivés

1. **Refus des identifiants de code en français proposés par l'assistant**
   - *Proposition de l'IA* : utiliser des identifiants Python en français (`Eleve`, `PointControle`, `Anomalie`, `services/anomalies.py`) pour coller au vocabulaire métier du cahier des charges.
   - *Décision et raison* : **refusé**. J'ai imposé l'anglais pour tous les identifiants de code (`Student`, `Checkpoint`, `Anomaly`...) afin de respecter les conventions standards de l'écosystème Django/Python et assurer la maintenabilité du projet, tout en gardant le français pour l'interface utilisateur, la documentation et l'historique Git.

2. **Abandon de la piste « fichiers Docker non exécutés localement »**
   - *Piste initialement envisagée* : inclure un `Dockerfile` et un `docker-compose.yml` dans le dépôt malgré l'absence de Docker sur le poste de développement.
   - *Correction et raison* : **écarté** après relecture critique de l'énoncé (§2.1, qui demande quelques commandes dans le README *ou* `docker compose up`). Livrer une configuration Docker jamais exécutée faisait courir un risque élevé d'échec chez le correcteur ; le choix d'un lancement local direct sans Docker a été acté et consigné dans `DECISIONS.md` (D13).

### Outils d'IA pour la capsule vidéo
- Non démarré au 24 novembre 2026 (planifié pour le Lot 5, les 27 et 28 novembre 2026).
