# SchoolLogs : Module « Passage »

Module de suivi des passages d'élèves aux points de contrôle d'un établissement scolaire (portail d'entrée, portail de sortie, autobus), conçu pour fonctionner en **réseau dégradé**, avec **badges numériques rotatifs** résistants à la copie, **détection automatique d'anomalies** et **synthèse quotidienne assistée par IA** encadrée par des garde-fous stricts.

---

## 1. Présentation

Dans un établissement scolaire, le portail ne dispose pas toujours d'une connexion fiable, l'horloge d'un appareil de scan peut être déréglée et un badge statique photographié pourrait être réutilisé par un tiers. Le module **Passage** organise le suivi autour de trois rôles contrôlés côté serveur :

- **Agent de portail** : scanne les badges depuis un écran unique utilisable hors ligne et synchronise sa file locale au retour du réseau.
- **Responsable** : gère les élèves, parents, badges, points de contrôle et itinéraires de son établissement ; consulte les passages, anomalies, messages sortants, synthèses du jour et journal d'audit.
- **Parent** : consulte exclusivement les passages et anomalies de ses propres enfants.

---

## 2. Technologies et justification

| Composant | Choix retenu | Justification |
|---|---|---|
| **Backend** | Python 3.12 + **Django 5.2 LTS** | Support long terme jusqu'en avril 2028 ; fournit nativement l'ORM paramétré, les migrations, les sessions serveur, l'échappement XSS et la protection CSRF. |
| **Base de données** | **SQLite** par défaut (mode **WAL**, `transaction_mode=IMMEDIATE`, `timeout=20 s`) ; PostgreSQL optionnel via `.env` | Zéro service externe à installer pour lancer le projet localement, tout en gérant la concurrence entre serveur web et planificateur. |
| **Mots de passe** | **Argon2id** (`argon2-cffi`) | Hachage moderne résistant aux attaques par force brute, configuré en premier dans `PASSWORD_HASHERS`. |
| **Interface et PWA** | Gabarits Django + CSS/JS natifs, **PWA unique** (écran de scan et page « Mon badge »), bibliothèques QR locales | Zéro dépendance Node ni étape de build ; fonctionne hors ligne sans CDN ni police distante. |
| **Planificateur** | Commande Django exécutée en boucle (sans Celery ni Redis) | Détection d'absence autonome et idempotente sans infrastructure tierce. |
| **Lancement** | Commandes locales directes (sans Docker) | Docker n'étant pas disponible sur le poste de développement, aucun fichier Docker non testé n'est inclus. |

### Organisation du code

Séparation en trois couches dans `passage/` (identifiants de code en anglais, interface, documentation et commits en français) :

```text
schoollogs-passage/
├── config/                  # Paramétrage Django, routage racine, WSGI/ASGI
├── passage/
│   ├── models/              # Modèles ORM (7 modules) et contraintes d'intégrité BD
│   ├── migrations/          # Migrations SQL reproductibles
│   ├── repositories/        # Requêtes ORM cloisonnées (parent et établissement)
│   ├── services/            # Logique métier (authentification, audit ; à venir : badges, synchro, anomalies, IA)
│   ├── forms/ & views/      # Validation serveur, contrôle des rôles, vues HTML/JSON
│   ├── admin.py             # Interface « Gestion des fiches » réservée au Responsable et cloisonnée
│   ├── templates/ & static/ # Interface sobre alignée SchoolLogs (CSS/JS natifs)
│   ├── management/commands/ # Script de peuplement rejouable (et planificateur à venir)
│   └── tests/               # Tests smoke + tests boîte noire sur les points critiques
├── .github/workflows/ci.yml # Intégration continue GitHub Actions
├── .env.example             # Gabarit de configuration sans secret réel
├── DECISIONS.md             # Choix d'architecture et adaptations du modèle
└── JOURNAL-IA.md            # Suivi quotidien de l'usage des outils d'IA
```

---

## 3. Installation et lancement

### Prérequis
- Python **3.10 à 3.14** (développé et testé avec Python **3.12**).

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Ouvrir ensuite **http://localhost:8000**. La commande `python manage.py seed_demo` est rejouable : l'exécuter plusieurs fois ne crée aucun doublon.

### Configuration (`.env`)

Les seuils, durées et plafonds sont configurables via [.env.example](.env.example) :
- `SECRET_KEY` : obligatoire et unique hors `DEBUG=1` (l'application refuse de démarrer si la valeur d'exemple est conservée avec `DEBUG=0`).
- `DEBUG` : `1` en développement local uniquement ; `0` par défaut si omis.
- `CLE_API_IA` : vide par défaut (active automatiquement le mode dégradé par gabarit).
- `DEMO_MODE` : `0` (désactivé) par défaut.
- `DB_ENGINE` : `sqlite` par défaut ; bascule possible vers `postgresql` (requiert `psycopg` et les variables `DB_*`).

### Contexte sécurisé du navigateur (caméra et PWA)

Les API navigateur **caméra** et **Service Worker** nécessitent HTTPS ou `http://localhost`. En démonstration locale, l'écran de scan tourne sur `http://localhost:8000` (PC) ; l'affichage d'un badge sur téléphone via le réseau local fonctionne, mais son exécution hors ligne sur téléphone exige HTTPS (hors périmètre local).

---

## 4. Comptes de test

Créés de façon reproductible par `python manage.py seed_demo` (mots de passe de démonstration uniquement, jamais réutilisés ailleurs) :

| Rôle | Identifiant | Mot de passe | Accès |
|---|---|---|---|
| **Responsable** | `responsable` | `DemoResp!2026` | `/responsable/`, `/responsable/audit/`, `/admin/` |
| **Agent de portail** | `agent` | `DemoAgent!2026` | `/scan/` uniquement |
| **Parent 1** (2 enfants : Amina, Samuel) | `parent1` | `DemoParent1!2026` | `/parent/` |
| **Parent 2** (3 enfants : Clarisse, Junior, Diane) | `parent2` | `DemoParent2!2026` | `/parent/` |

---

## 5. Tests et intégration continue

```bash
python manage.py check
python manage.py test --verbosity=2
```

La suite comprend actuellement **69 tests** (7 tests de socle + 62 tests boîte noire critiques sur l'authentification, le blocage temporaire, l'expiration de session, le cloisonnement parent et multi-établissements, le CSRF, l'anti-XSS, les contraintes BD et le peuplement). Elle est exécutée automatiquement à chaque envoi via [.github/workflows/ci.yml](.github/workflows/ci.yml) avec `DEBUG=0` et contrôle d'absence de fichier `.env` versionné.

---

## 6. Sécurité

| Axe | Mesure en place / prévue | État |
|---|---|---|
| **Mots de passe** | Hachage Argon2id, longueur minimale 8 caractères, jamais stockés en clair ni dans l'audit. | En place et testé |
| **Anti-force brute et énumération** | Blocage temporaire par couple `(identifiant, IP)` (5 échecs / 15 min), message d'erreur strictement identique et égalisation du temps de réponse. | En place et testé |
| **Sessions** | Sessions serveur bornées à 8 h, cookie `HttpOnly` + `SameSite=Lax`, déconnexion en `POST` détruisant la session en base. | En place et testé |
| **Contrôle des rôles** | Vérification serveur sur chaque vue ; Agent cantonné à `/scan/`, « Gestion des fiches » (`/admin/`) réservée au Responsable. | En place et testé |
| **Cloisonnement (anti-IDOR)** | Accès parent filtré à ses propres enfants et accès responsable filtré à son seul établissement ; refus uniforme `403` + trace dans `AuditLog`. | En place et testé |
| **Validation, CSRF et anti-XSS** | Validation serveur systématique, ORM exclusif, `CsrfViewMiddleware` actif sur toutes les écritures, échappement automatique des gabarits, en-têtes `nosniff` et `X-Frame-Options: DENY`. | En place et testé |
| **Gestion des secrets** | Aucun secret dans le dépôt ; `.env` ignoré dès le premier commit, garde CI anti-`.env`, `Badge.secret` masqué partout. | En place et testé |
| **Badges résistants à la copie** | Code QR rotatif signé (30 s), usage unique par pas de temps, secret jamais transmis à l'appareil de scan. | À implémenter (prochaine étape) |

### Ce qui serait ajouté avec davantage de temps
- Déploiement **HTTPS/TLS** complet (cookies `Secure`, HSTS) permettant aussi le hors ligne sur téléphone.
- Enregistrement et attestation des appareils de scan.
- Chiffrement au repos des secrets de badge avec rotation périodique.
- Journalisation détaillée des consultations nominatives.

---

## 7. Mode dégradé

1. **Synthèse du jour sans IA disponible** *(à venir)* : si `CLE_API_IA` est vide, si le service répond en erreur ou hors délai, si la sortie est non conforme après une relance ou si un plafond journalier est atteint, la synthèse est produite automatiquement à partir d'un gabarit déterministe utilisant exactement les chiffres calculés par le code.
2. **Scan hors ligne au portail** *(à venir)* : la PWA stocke localement les points de contrôle et la file des scans dans le navigateur, affiche uniquement « Scan enregistré » sans affirmer la validité hors ligne, puis synchronise sans perte ni doublon au retour du réseau.

---

## 8. État d'avancement et ce qui n'est pas terminé

*Point au 25 novembre 2026.*

### Fait et fonctionnel
- Socle Django 5.2 LTS, SQLite WAL + `IMMEDIATE` + timeout de verrou, Argon2id, fuseau `Africa/Douala` (stockage UTC), CI GitHub Actions.
- Modèle de données complet (16 modèles, migration initiale reproductible et contraintes d'intégrité imposées en base).
- Script de peuplement rejouable (`seed_demo`) créant 1 établissement, 4 comptes, 5 élèves, points de contrôle, itinéraires, badges actifs et 3 anomalies de types distincts.
- Authentification complète (Argon2id, blocage temporaire, message uniforme, expiration de session, déconnexion serveur effective) et journal d'audit minimal.
- Contrôle strict des rôles côté serveur, cloisonnement parent et multi-établissements, « Gestion des fiches » (`/admin/`) réservée au Responsable, formulaires validés côté serveur.
- Interface sobre mobile-first alignée sur la charte SchoolLogs (CSS/JS natifs sans CDN).
- **69 tests automatisés au vert** (socle + boîte noire).

### Reste à réaliser (périmètre cible)
- **Badges numériques et scan hors ligne** : cycle de vie complet (réémission, lien d'activation unique), code QR rotatif signé, page « Mon badge », PWA hors ligne (Service Worker + file persistante + caméra QR), synchronisation idempotente et correction/signalement d'horloge.
- **Détection des anomalies et planificateur** : détection automatique des 5 types (`POINT_MANQUANT`, `HORS_FENETRE`, `POINT_INCONNU`, `DOUBLE_SCAN`, `BADGE_NON_ACTIF`), commande planifiée en boucle idempotente et résolution automatique sur synchronisation tardive.
- **Notifications et synthèse IA** : table de messages sortants derrière une interface fournisseur isolée avec plafond journalier ; synthèse du jour par gabarit puis modèle IA avec les 4 garde-fous et test de fidélité numérique.
- **Finalisation** : banc de démonstration (`DEMO_MODE`), recette complète des scénarios et production des deux vidéos (démonstration technique et capsule de communication).

### Volontairement hors périmètre
- Envoi réel de SMS (isolé derrière une interface et simulé en base).
- Hébergement de production, nom de domaine et HTTPS.
- Carte NFC physique réelle (simulée par la page « Mon badge »).
- Entraînement d'un modèle d'IA propre.
- Bonus conditionnels si le temps le permet après le cœur fonctionnel : exceptions calendaires (jours fériés, absences justifiées), messages de rectification automatique et journal des consultations.
