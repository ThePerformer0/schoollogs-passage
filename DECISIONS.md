# Décisions d'architecture et de conception (`DECISIONS.md`)

Ce document recense les décisions laissées ouvertes par l'énoncé et les choix structurants du module **Passage** (SchoolLogs). Pour chaque décision : option retenue, option(s) écartée(s) et justification.

---

## Décisions métier et produit (D1 à D10)

### D1 — Support du badge et résistance à la copie
- **Option retenue :** Badge numérique rotatif affiché sous forme de QR code renouvelé toutes les `{PAS_CODE}` secondes (30 s par défaut), signé avec un secret propre à chaque badge (inconnu du scanneur) et à usage unique par pas de temps. Aucune donnée nominative ni identifiant d'élève lisible ne figure dans le code. En démonstration, la page « Mon badge » de la PWA joue le rôle du support ; en déploiement réel (où tous les élèves n'ont pas de téléphone), le support serait une carte NFC à compteur signé sans changer la logique serveur.
- **Options écartées :**
  - *Code statique opaque* : un tiers qui photographie le badge peut le rejouer indéfiniment (note de sécurité à zéro).
  - *Vérification hors ligne par clé publique sur le scanneur* : ne permet pas de détecter un rejeu du même code sur deux appareils ou à deux instants sans état serveur.
- **Raison :** Empêche la réutilisation d'un badge photographié (scénario S3) tout en restant testable localement sans matériel NFC. *Limite assumée et documentée* : une copie présentée dans la même fenêtre avant que le vrai code ne soit scanné reste indiscernable ; la courte durée de vie et l'usage unique limitent fortement cette fenêtre.

### D2 — Contrainte pour l'agent de portail hors ligne et PWA unique
- **Option retenue :** Une **PWA unique** couvre à la fois l'écran de scan de l'agent et la page « Mon badge » (service worker + stockage persistant du navigateur). En mode hors ligne, aucun secret de badge n'est jamais envoyé ni stocké sur l'appareil de l'agent ; l'écran affiche uniquement « scan enregistré » (jamais « badge valide ») et la validation cryptographique intervient à la synchronisation serveur.
- **Option écartée :** Embarquer les secrets des badges sur le téléphone de l'agent pour valider hors ligne, ou développer deux applications front-end séparées.
- **Raison :** Un appareil d'agent volé ou compromis ne doit pas compromettre les secrets de tous les élèves. L'approche PWA unique simplifie le cache hors ligne, le déploiement local sans outil de build et la maintenance.

### D3 — Garantie d'enregistrement unique d'un passage (idempotence)
- **Option retenue :** Chaque scan génère sur l'appareil un identifiant d'événement unique (UUID) associé à l'identifiant d'appareil. Une contrainte d'unicité en base sur le couple `(appareil, identifiant_evenement)` garantit l'absence de doublon, et le serveur renvoie un accusé individuel par élément du lot ; le client ne purge de sa file locale que les éléments accusés.
- **Option écartée :** Dédoublonnage approximatif par comparaison des horodatages et du badge côté serveur.
- **Raison :** La comparaison d'heures échoue en cas d'horloge fausse, de synchronisation interrompue ou de double scan réel. L'identifiant d'événement client + contrainte BD + accusé individuel garantit mathématiquement « aucune perte, aucun doublon » (scénarios S1 et S2).

### D4 — Traitement d'une horloge locale manifestement fausse
- **Option retenue :** Conserver toujours l'heure locale brute (jamais modifiée), calculer à la synchronisation le décalage `heure_serveur - heure_appareil` pour déduire une heure corrigée, retenir cette heure corrigée si elle est plausible (au plus 2 min dans le futur et au plus `{AGE_MAX_PASSAGE}` dans le passé) sinon l'heure serveur, marquer le lot « horloge suspecte » au-delà de `{SEUIL_HORLOGE}`, et **ne jamais rejeter** un passage à cause de son heure.
- **Option écartée :** Rejeter les passages dont l'horloge locale s'écarte trop de l'heure serveur.
- **Raison :** Rejeter un scan ferait perdre la trace de présence d'un élève pour un simple téléphone mal réglé (scénario S5). La correction + signalement préserve à la fois l'intégrité des présences et la visibilité pour le responsable. *Limite assumée* : le décalage d'horloge est supposé constant pendant une session hors ligne.

### D5 — Détection d'une absence de passage sans action utilisateur
- **Option retenue :** Traitement planifié indépendant des requêtes web sous forme d'une commande de gestion Django exécutée en boucle toutes les `{PERIODE_PLANIFICATEUR}` secondes, après clôture de la fenêtre + `{DELAI_GRACE}`. La détection est idempotente (contrainte d'unicité en base sur `(élève, date, point, type)`), également rejouée lors des synchronisations, et toute synchronisation tardive valide résout automatiquement l'anomalie (`résolue automatiquement`) sans la supprimer.
- **Options écartées :** Celery + Redis (complexité d'infrastructure inutile en local) ; détection déclenchée uniquement à l'ouverture de l'application (ne détecte rien si personne ne se connecte).
- **Raison :** Répond directement au scénario S4 sans imposer de service broker tiers au correcteur tout en restant fiable en cas de reconnexion tardive.

### D6 — Choix de la cinquième anomalie
- **Option retenue :** `BADGE_NON_ACTIF` — présentation d'un badge `suspendu`, `perdu`, `révoqué` ou remplacé avec un code correctement signé.
- **Option écartée :** « Sortie sans entrée le même jour ».
- **Raison :** Découle directement du cycle de vie des badges : sans cette anomalie, la tentative d'utilisation d'un badge déclaré perdu ou révoqué serait silencieusement ignorée sans alerter le parent ni le responsable. « Sortie sans entrée » dépend davantage de cas particuliers d'itinéraire et porte un risque sécurité moindre.

### D7 — Base de données par défaut
- **Option retenue :** **SQLite** par défaut avec mode **WAL** (`PRAGMA journal_mode=WAL`), `PRAGMA synchronous=NORMAL`, `transaction_mode=IMMEDIATE` et délai d'attente sur verrous (`timeout=20 s`). Basculement vers **PostgreSQL** prévu par variables d'environnement (`DB_ENGINE=postgresql`).
- **Option écartée :** PostgreSQL obligatoire dès le démarrage local.
- **Raison :** Permet au correcteur de lancer immédiatement l'application sans installer ni configurer de serveur de base de données, tout en sécurisant les accès concurrents entre serveur web et planificateur.

### D8 — Interface du responsable
- **Option retenue :** Administration Django restreinte exclusivement au rôle **Responsable** (et cloisonnée à son établissement) pour la gestion CRUD de base, complétée par des pages dédiées sobres pour la consultation des passages, anomalies, messages sortants, synthèses et journal d'audit.
- **Option écartée :** Développement complet d'un back-office sur mesure ou d'une SPA JavaScript dédiée.
- **Raison :** Concentre l'effort de développement de 5 jours sur les points critiques à forte valeur (sécurité, hors ligne, détection d'anomalies, garde-fous IA) sans sacrifier la lisibilité.

### D9 — Mécanisme d'authentification
- **Option retenue :** **Sessions serveur Django** (durée maximale `{DUREE_SESSION}` = 8 h, cookie `HttpOnly` + `SameSite=Lax`, déconnexion invalidant immédiatement la session en base) combinées à la protection CSRF native.
- **Option écartée :** Jetons JWT stockés dans `localStorage` ou cookies sans révocation serveur.
- **Raison :** Garantit une déconnexion réellement effective côté serveur (exigence `AUT-05` / `SEC-03`), évite l'exposition des jetons en cas de XSS et s'intègre nativement avec les vues Django et la PWA sur la même origine.

### D10 — Données transmises au modèle de langage pour la synthèse
- **Option retenue :** Envoi exclusif d'**agrégats numériques déjà calculés par le code** (présents, retards au portail, sorties hors plage avec heures, absents), **sans aucun nom d'élève** ni donnée personnelle. Vérification stricte de la sortie (schéma + contrôle que chaque nombre du texte figure dans les agrégats transmis), 1 seule relance autorisée, puis bascule automatique sur un gabarit déterministe.
- **Option écartée :** Transmission de listes nominatives ou de passages bruts au modèle de langage en lui demandant de compter.
- **Raison :** Protège la vie privée des élèves mineurs et élimine les hallucinations numériques du modèle (prouvé par test automatisé, exigence `SYN-11`).

---

## Décisions techniques de socle (Lot 1 — Tâche 1)

### D11 — Front-end sans étape de compilation et bibliothèques QR locales
- **Option retenue :** Gabarits Django + JavaScript natif (ES6+) dans la PWA unique ; bibliothèques open source de lecture et génération de QR code copiées localement dans `passage/static/` (aucun CDN).
- **Option écartée :** Stack Node.js / Vite / React ou chargement des scripts QR depuis un CDN externe.
- **Raison :** Zéro dépendance Node pour le correcteur et garantie de fonctionnement 100 % hors ligne au portail.

### D12 — Architecture en couches dans une application Django unique (`passage/`) et identifiants en anglais
- **Option retenue :** Projet `config/` + une application métier unique `passage/` découpée en couches explicites (`models/`, `repositories/`, `services/`, `services/gateways/`, `views/`, `forms/`), avec identifiants de code en **anglais** (`Student`, `Checkpoint`, `Anomaly`...) et interface, documentation et commits en **français**.
- **Options écartées :** Découpage en 6–7 micro-applications Django (risque d'imports circulaires et de migrations croisées sur un domaine fortement couplé) ; identifiants de code en français.
- **Raison :** Séparation nette Interface / Logique métier / Accès aux données immédiatement lisible et maintenable, prête pour une extraction ultérieure si nécessaire, tout en respectant les conventions standards Python/Django.

### D13 — Pas de fichiers Docker non testés dans le dépôt
- **Option retenue :** Lancement local en quelques commandes Python/Django documentées dans le `README.md` (avec `Django 5.2 LTS`), sans inclure de `Dockerfile` ni de `docker-compose.yml`.
- **Option écartée :** Ajouter un `Dockerfile` et un `docker-compose.yml` écrits à l'aveugle sans pouvoir les exécuter sur la machine de développement (Docker non installé).
- **Raison :** L'énoncé autorise explicitement *« quelques commandes décrites dans le README, ou un docker compose up »*. Livrer une configuration Docker non testée risquerait de casser l'installation chez le correcteur.

### D14 — Plafond journalier par défaut du coût IA (`PLAFOND_COUT_IA_JOUR`)
- **Option retenue :** Plafond estimé par défaut fixé à **`0.50 USD / jour`** (en complément de `PLAFOND_APPELS_IA_JOUR = 20`), modifiable par variable d'environnement.
- **Option écartée :** Absence de plafond financier ou plafond arbitrairement élevé (ex. 10 USD/jour).
- **Raison :** Cohérent avec un usage sur un modèle léger à palier gratuit (20 synthèses quotidiennes représentent une fraction de centime en grille tarifaire standard) tout en démontrant un garde-fou budgétaire réaliste.
