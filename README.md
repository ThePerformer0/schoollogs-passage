# SchoolLogs — Module « Passage »

Module de suivi des passages d'élèves aux points de contrôle d'un établissement scolaire (portail d'entrée, portail de sortie, autobus), conçu pour fonctionner en **réseau dégradé**, avec **badges numériques rotatifs** résistants à la copie, **détection automatique d'anomalies** et **synthèse quotidienne assistée par IA** avec garde-fous stricts.

---

## 1. Présentation

Dans un établissement scolaire, le portail ne dispose pas toujours d'une connexion fiable, l'horloge d'un téléphone d'agent peut être déréglée et un badge statique photographié pourrait être réutilisé par un tiers. Le module **Passage** répond à ces contraintes pour trois rôles contrôlés côté serveur :

- **Agent de portail** : scanne les badges depuis un écran utilisable hors ligne et synchronise sa file locale au retour du réseau.
- **Responsable** : gère les élèves, parents, badges, points de contrôle et itinéraires ; consulte les passages, anomalies, messages sortants, synthèses quotidiennes et journal d'audit de son établissement.
- **Parent** : consulte exclusivement les passages et anomalies de ses propres enfants.

---

## 2. Technologies et justification

| Composant | Choix retenu | Justification |
|---|---|---|
| **Backend** | Python 3.12 + **Django 5.2 LTS** | Support LTS jusqu'en avril 2028 ; fournit nativement l'ORM paramétré, les migrations, les sessions serveur, l'échappement XSS des gabarits et la protection CSRF. |
| **Base de données** | **SQLite** par défaut (mode **WAL**, `transaction_mode=IMMEDIATE`, `timeout=20 s`) ; PostgreSQL optionnel via `.env` | Zéro service externe à installer pour lancer le projet localement tout en supportant la concurrence entre serveur web et planificateur. |
| **Mots de passe** | **Argon2id** (`argon2-cffi`) | Algorithme moderne résistant aux attaques GPU/ASIC, placé en premier dans `PASSWORD_HASHERS`. |
| **Interface & PWA** | Gabarits Django + JavaScript natif, **PWA unique** (écran de scan + page « Mon badge »), bibliothèques QR copiées dans le dépôt | Aucune dépendance Node ni étape de compilation ; fonctionnement hors ligne sans CDN externe. |
| **Planificateur** | Commande de gestion Django en boucle (sans Celery ni Redis) | Déploiement local minimal et reproductible ; détection d'absence idempotente sans broker externe. |
| **Conteneurisation** | **Pas de Docker** (lancement local documenté ci-dessous) | Docker n'étant pas disponible sur le poste de développement, aucun fichier Docker non testé n'est livré (voir `DECISIONS.md`, D13). |

### Architecture en couches

Le projet suit une séparation explicite entre **interface**, **logique métier** et **accès aux données** (identifiants de code en anglais, interface/documentation/commits en français) :

```text
schoollogs-passage/
├── config/                  # Paramétrage global Django, routage racine, WSGI/ASGI
├── passage/                 # Application métier unique structurée en couches
│   ├── models/              # [À venir — Lot 1] Modèles ORM et contraintes d'intégrité BD
│   ├── repositories/        # [À venir — Lot 1] Requêtes ORM cloisonnées par établissement/parent
│   ├── services/            # [À venir — Lot 1+] Logique métier et passerelles externes (SMS, LLM)
│   ├── views/ & forms/      # [À venir — Lot 1+] Interface HTTP/JSON, contrôle RBAC serveur
│   ├── templates/ & static/ # [À venir — Lot 2+] Gabarits HTML et PWA unique hors ligne
│   ├── management/commands/ # [À venir — Lot 1 & 3] Peuplement rejouable et planificateur
│   └── tests/               # Tests automatisés sur les points critiques
├── .github/workflows/ci.yml # Intégration continue GitHub Actions
├── .env.example             # Gabarit de configuration sans aucun secret réel
├── DECISIONS.md             # Décisions d'architecture et arbitrages
└── JOURNAL-IA.md            # Suivi honnête et daté de l'usage des outils d'IA
```

---

## 3. Installation et lancement

### Prérequis

- Python **3.10 à 3.14** (développé et testé avec Python **3.12**).

### Installation locale (Windows PowerShell)

```powershell
# 1. Créer et activer l'environnement virtuel
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Installer les dépendances figées
pip install -r requirements.txt

# 3. Créer le fichier de configuration local à partir de l'exemple
Copy-Item .env.example .env

# 4. Appliquer les migrations initiales
python manage.py migrate

# 5. Lancer le serveur de développement
python manage.py runserver
```

### Installation locale (Linux / macOS)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

> **Commandes à venir dans les lots suivants :**
> - Script de peuplement rejouable des données de départ.
> - Commande du planificateur en boucle pour la détection automatique des absences `POINT_MANQUANT`.

### Configuration (`.env`)

Tous les seuils, durées et plafonds sont configurables par variables d'environnement (voir [.env.example](.env.example)) :

- `SECRET_KEY` : obligatoire et unique hors `DEBUG=1` (l'application refuse de démarrer si la valeur d'exemple est conservée avec `DEBUG=0`).
- `DEBUG` : `1` en développement local uniquement ; `0` par défaut si omis.
- `CLE_API_IA` : laissée **vide par défaut** → active automatiquement le mode dégradé par gabarit.
- `DEMO_MODE` : **`0` (désactivé) par défaut**. Le banc de démonstration multi-badges n'est jamais actif dans la configuration par défaut.
- `DB_ENGINE` : `sqlite` par défaut (`SQLITE_PATH`, `SQLITE_TIMEOUT_SECONDS=20`). Peut être basculé sur `postgresql` en installant le pilote PostgreSQL (`psycopg`) et en renseignant `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.

### Contexte sécurisé du navigateur (caméra et PWA)

Les API navigateur **caméra** (`getUserMedia`) et **Service Worker** ne fonctionnent qu'en **HTTPS** ou sur **`http://localhost`**. En démonstration locale :

- L'écran de scan de l'agent s'exécute sur `http://localhost:8000` (PC).
- La page « Mon badge » peut être ouverte sur un téléphone via l'adresse IP du réseau local ; l'affichage du code rotatif fonctionne, tandis que son exécution hors ligne sur téléphone nécessite HTTPS (hors périmètre MVP).

---

## 4. Comptes de test

*Section mise à jour dès l'ajout du script de peuplement rejouable.*

Comptes prévus dans le jeu de données initial (mots de passe de démonstration uniquement, jamais réutilisés ailleurs) :

- 1 **Responsable**
- 1 **Agent de portail**
- 2 **Parents** (pour 5 élèves, dont 2 rattachés au même parent)

---

## 5. Commande des tests et intégration continue

```bash
python manage.py check
python manage.py test --verbosity=2
```

La suite de tests est exécutée automatiquement à chaque `push` et `pull_request` par GitHub Actions via [.github/workflows/ci.yml](.github/workflows/ci.yml) (avec `DEBUG=0`, `CLE_API_IA` vide et vérification qu'aucun fichier `.env` n'est versionné).

---

## 6. Sécurité

Le module manipule des données concernant des élèves mineurs. Chaque point de sécurité est conçu côté serveur :

| Axe | Mesure retenue | État actuel |
|---|---|---|
| **Mots de passe** | Hachage **Argon2id** (`Argon2PasswordHasher` en tête de `PASSWORD_HASHERS`), longueur minimale 8 caractères (`AUTH_PASSWORD_VALIDATORS`), aucun mot de passe en clair. | Socle configuré et testé (Tâche 1). |
| **Anti-force brute & énumération** | Limitation par couple identifiant + IP (`MAX_TENTATIVES`, `FENETRE_TENTATIVES`, `DUREE_BLOCAGE`) et message d'erreur strictement identique. | Paramètres chargés ; implémentation au Lot 1 (authentification). |
| **Sessions** | Sessions serveur Django bornées à `DUREE_SESSION` (8 h), cookie `HttpOnly` + `SameSite=Lax`, invalidation effective côté serveur à la déconnexion. | Socle session configuré ; flux connexion/déconnexion au Lot 1. |
| **Contrôle des rôles (RBAC)** | Vérification côté serveur à chaque requête (agent limité au scan, admin Django restreinte au responsable). | Prévu au Lot 1 (authentification et rôles). |
| **Cloisonnement (anti-IDOR)** | Filtrage systématique par parent et par établissement côté serveur/repository sur chaque accès direct par identifiant. | Prévu au Lot 1 (modèles et repositories). |
| **Validation & anti-SQLi** | Validation serveur systématique ; utilisation exclusive de l'ORM Django / requêtes paramétrées. | Socle ORM actif ; formulaires au Lot 1+. |
| **Anti-XSS & en-têtes HTTP** | Échappement automatique des gabarits Django jamais désactivé ; `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`. | Actif et vérifié par test smoke (Tâche 1). |
| **Protection CSRF** | `CsrfViewMiddleware` actif sur toutes les mutations, y compris les requêtes de synchronisation PWA (`X-CSRFToken`). | Actif sur le socle. |
| **Gestion des secrets** | Aucun secret dans le code ni dans l'historique Git ; `.env` ignoré dès le premier commit, `.env.example` sans valeur réelle, garde CI anti-`.env`, refus de démarrage si `SECRET_KEY` est absente ou par défaut hors `DEBUG=1`. | Actif et testé (Tâche 1). |
| **Résistance des badges à la copie** | Code QR rotatif signé toutes les `{PAS_CODE}` s, usage unique par pas de temps, secret jamais transmis à l'agent. *Limite assumée* : une copie présentée dans la même fenêtre avant le vrai scan reste indiscernable ; l'usage unique et la durée courte réduisent la fenêtre d'exposition. | Prévu au Lot 2. |
| **Erreurs sans fuite interne** | `DEBUG=0` par défaut hors développement local ; `/health/` ne divulgue aucun détail interne. | Actif et testé (Tâche 1). |

### Ce qui serait ajouté avec davantage de temps

- Déploiement **HTTPS/TLS** complet (cookies `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, HSTS) permettant le fonctionnement hors ligne du badge sur téléphone.
- **Enregistrement et attestation cryptographique des appareils de scan** des agents.
- **Chiffrement au repos** des secrets de badge avec gestionnaire de clés dédié (KMS) et **rotation** périodique.
- **Journalisation complète des consultations** nominatives (`AUD-02`) et alertes d'intégrité sur le journal d'audit.

---

## 7. Mode dégradé

L'application est conçue pour continuer à rendre service sans tomber dans deux situations dégradées :

1. **Synthèse du jour sans IA disponible** : si `CLE_API_IA` est vide, si le fournisseur de modèle de langage est injoignable, renvoie une erreur, produit une réponse non conforme (après une seule relance) ou si un plafond journalier (`PLAFOND_APPELS_IA_JOUR` / `PLAFOND_COUT_IA_JOUR`) est atteint, la synthèse est produite automatiquement à partir d'un **gabarit déterministe** utilisant les mêmes chiffres exacts calculés par le code. Le chemin effectivement emprunté est enregistré en base.
2. **Scan hors ligne au portail** (Lot 2) : la PWA conserve la liste des points de contrôle et met chaque scan en file persistante dans le navigateur. Tant que le serveur n'a pas répondu, l'interface indique uniquement « scan enregistré » (jamais « badge valide »). Au retour du réseau, la synchronisation idempotente enregistre chaque passage une seule fois.

---

## 8. Ce qui n'est pas terminé

*État au 24 novembre 2026 — fin de la Tâche 1 du Lot 1 (initialisation du socle).*

### Livré à ce stade
- Structure Django en couches (`config/`, `passage/`), configuration `.env.example`, `.gitignore`, `requirements.txt` figé.
- SQLite configuré avec mode **WAL**, `transaction_mode=IMMEDIATE` et délai d'attente sur verrous ; hachage **Argon2id** par défaut ; fuseau `Africa/Douala` (stockage UTC).
- Test smoke vérifiant le socle et workflow GitHub Actions (`.github/workflows/ci.yml`).
- Documentation initiale (`README.md`, `DECISIONS.md`, `JOURNAL-IA.md`).

### Reste à livrer (MVP)
- **Lot 1 (suite)** : modèles métier et contraintes BD, migrations, script de peuplement rejouable, authentification sécurisée, rôles/cloisonnement et journal d'audit minimal.
- **Lot 2** : cycle de vie des badges numériques rotatifs, lien d'activation unique, page « Mon badge », PWA hors ligne de scan, synchronisation idempotente et gestion des écarts d'horloge.
- **Lot 3** : itinéraires, fenêtres, détection des 5 anomalies, planificateur en boucle idempotent et résolution automatique sur synchro tardive.
- **Lot 4** : messages sortants avec interface fournisseur isolée et plafond journalier ; synthèse par gabarit puis couche LLM avec les 4 garde-fous.
- **Lot 5** : banc de démonstration (`DEMO_MODE`), écrans de consultation, recette des 7 scénarios et production des deux vidéos.

### Volontairement hors périmètre MVP
- Envoi réel de SMS (simulé via une table `messages_sortants` derrière une interface dédiée).
- Hébergement de production, nom de domaine et HTTPS.
- Carte NFC physique (simulée par la page « Mon badge » ; même logique cryptographique serveur).
- Bonus éventuels après gel du MVP : exceptions calendaires (jours fériés/absences justifiées), message de rectification automatique, journal détaillé des consultations.
