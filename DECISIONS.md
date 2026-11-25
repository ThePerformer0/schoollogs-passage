# Décisions d'architecture et de conception

Ce document présente les choix pris sur les questions laissées ouvertes par le sujet du module **Passage** (SchoolLogs), ainsi que les quelques adaptations apportées au modèle de données indicatif. Pour chaque choix : l'option retenue, l'alternative écartée et la raison.

---

## Décisions principales (D1 à D10)

### D1 : Comment empêcher la réutilisation d'un badge photographié ?
- **Option retenue :** un badge numérique sous forme de QR code qui change automatiquement toutes les 30 secondes (durée configurable). Chaque badge possède une clé secrète connue uniquement du serveur et du support de l'élève. Le QR code ne contient ni le nom ni le numéro de l'élève, et chaque code généré ne peut être validé qu'une seule fois. En démonstration, la page « Mon badge » simule ce support sur un écran ; en établissement, ce même principe serveur peut fonctionner avec une carte sans contact (NFC) à code tournant pour les élèves sans téléphone.
- **Option écartée :** un QR code fixe contenant un identifiant aléatoire, ou un code vérifiable directement hors ligne sur le poste de scan.
- **Raison :** un QR code fixe photographié pourrait être réutilisé indéfiniment par un tiers, et une vérification purement hors ligne ne détecte pas qu'un même code a déjà été utilisé ailleurs. *Limite assumée* : si une photo est présentée dans les 30 secondes avant que le vrai badge ne soit scanné, le serveur ne peut pas distinguer les deux ; la durée courte et l'usage unique réduisent fortement ce risque.

### D2 : Quelle contrainte cela impose à l'agent au portail, et quel support utiliser ?
- **Option retenue :** l'appareil de scan de l'agent (qu'il s'agisse d'un téléphone, d'une tablette ou d'un poste dédié fourni par l'établissement) ne reçoit jamais les clés secrètes des badges. L'écran de scan et la page « Mon badge » sont réunis dans une seule application web installable utilisable hors ligne (PWA). Sans connexion, l'agent scanne normalement : l'écran confirme « Scan enregistré » sans jamais affirmer que le badge est valide, et le serveur vérifie les codes dès le retour du réseau.
- **Option écartée :** copier les clés des badges sur l'appareil de scan pour valider hors ligne, ou développer deux applications distinctes (par exemple une application mobile dédiée au scan et un site séparé).
- **Raison :** si un appareil de scan est perdu, volé ou prêté, aucun secret d'élève n'est compromis. De plus, une application web unique utilisable dans un navigateur s'adapte à n'importe quel matériel disponible dans l'établissement sans déploiement complexe.

### D3 : Comment garantir qu'un passage n'est enregistré qu'une seule fois ?
- **Option retenue :** dès qu'un badge est scanné, l'appareil génère un identifiant unique propre à cet événement et l'associe à son identifiant d'appareil. La base de données interdit l'enregistrement en double d'un même couple (appareil, événement). Lors de la synchronisation, le serveur répond individuellement pour chaque scan du lot, et l'appareil ne retire de sa file locale que les scans confirmés par le serveur.
- **Option écartée :** tenter de détecter les doublons côté serveur en comparant simplement l'heure du scan et le badge.
- **Raison :** une comparaison d'heures échoue si l'horloge de l'appareil est déréglée, si la connexion coupe en plein envoi ou si un élève est réellement scanné deux fois de suite. L'identifiant unique couplé à la contrainte en base garantit aucune perte et aucun doublon.

### D4 : Que faire d'un passage dont l'horloge locale est manifestement fausse ?
- **Option retenue :** ne jamais rejeter un passage à cause de son heure. Le serveur conserve toujours l'heure brute envoyée par l'appareil. À chaque synchronisation, il compare l'heure courante de l'appareil avec la sienne pour estimer le décalage et calculer une heure corrigée. Si cette heure corrigée est cohérente (au plus 2 minutes dans le futur et au plus 7 jours dans le passé, seuil configurable), elle est retenue ; sinon, le serveur retient son heure de réception. Au-delà de 5 minutes d'écart (configurable), les passages du lot sont signalés « horloge suspecte » au responsable.
- **Option écartée :** rejeter automatiquement les passages provenant d'un appareil mal réglé.
- **Raison :** une mauvaise heure sur l'appareil au portail ne doit pas faire disparaître la preuve de présence d'un élève. Corriger et signaler préserve la fiabilité du suivi tout en alertant le responsable. *Limite assumée* : le décalage d'horloge est supposé constant pendant une coupure réseau.

### D5 : Comment détecter une absence sans que personne n'ouvre l'application ?
- **Option retenue :** une tâche planifiée indépendante du site web (commande Django lancée en boucle toutes les 60 secondes, fréquence configurable) vérifie les fenêtres clôturées après un délai de grâce de 10 minutes. Une contrainte d'unicité en base empêche de créer deux fois la même anomalie d'absence pour un élève, un jour et un point donnés. Si des scans hors ligne arrivent plus tard et prouvent que l'élève était bien passé dans sa fenêtre, l'anomalie passe automatiquement à l'état « résolue automatiquement » sans être effacée.
- **Option écartée :** installer une pile distribuée (Celery + Redis) ou ne lancer la détection qu'au moment où un responsable ouvre son tableau de bord.
- **Raison :** déclencher la détection à l'ouverture de l'application manquerait des absences si personne ne se connecte ; à l'inverse, Celery et Redis alourdiraient inutilement l'installation locale. Une commande en boucle simple et rejouable sans doublon couvre le besoin sans dépendance externe.

### D6 : Quelle cinquième anomalie ajouter ?
- **Option retenue :** `BADGE_NON_ACTIF`, déclenchée lorsqu'un badge suspendu, perdu, révoqué ou remplacé est présenté avec une signature valide.
- **Option écartée :** « sortie sans entrée enregistrée le même jour ».
- **Raison :** cette anomalie découle directement de la gestion du cycle de vie des badges. Si un tiers tente d'utiliser un badge déclaré perdu ou révoqué, un refus silencieux laisserait le parent et l'établissement dans l'ignorance alors qu'il s'agit d'un incident de sécurité concret.

### D7 : Quelle base de données utiliser par défaut ?
- **Option retenue :** SQLite par défaut, configuré en mode WAL (écritures et lectures simultanées) avec un délai d'attente sur les verrous de 20 secondes, afin de supporter sans blocage le serveur web et le planificateur en parallèle. Le passage à PostgreSQL reste possible par simple variable d'environnement.
- **Option écartée :** rendre PostgreSQL obligatoire dès le démarrage local.
- **Raison :** permet de lancer et tester le projet immédiatement sans installer de serveur de base de données externe, tout en restant compatible avec PostgreSQL.

### D8 : Comment construire l'interface de gestion du responsable ?
- **Option retenue :** s'appuyer sur l'administration intégrée de Django, strictement réservée au rôle Responsable, renommée « Gestion des fiches » et cloisonnée à son seul établissement, complétée par des pages dédiées sobres pour consulter les passages, anomalies, messages sortants, synthèses et journal d'audit.
- **Option écartée :** développer intégralement tous les écrans de gestion (ajout, modification, rattachement) sur mesure.
- **Raison :** évite de disperser le temps limité du projet sur des formulaires classiques afin de concentrer l'effort sur les points critiques (sécurité, fonctionnement hors ligne, détection d'anomalies et garde-fous IA).

### D9 : Comment gérer l'authentification et les refus d'accès ?
- **Option retenue :** sessions stockées côté serveur (durée maximale de 8 heures, cookie protégé contre la lecture par script) avec déconnexion par requête `POST` qui détruit immédiatement la session en base, et protection CSRF sur toutes les actions d'écriture. Toute tentative d'accès à un élève ou à un établissement hors périmètre renvoie un refus uniforme (code HTTP `403`, sans révéler si l'identifiant existe) et laisse une trace dans le journal d'audit.
- **Option écartée :** jetons JWT conservés dans le navigateur, et distinction entre code `404` et `403` selon que l'élève existe ou non.
- **Raison :** les sessions serveur garantissent qu'un ancien cookie ne fonctionne plus après déconnexion. Le refus uniforme en `403` empêche de deviner quels identifiants d'élèves existent dans la base.

### D10 : Quelles données envoyer au modèle d'IA pour la synthèse du jour ?
- **Option retenue :** le programme calcule tous les chiffres depuis la base de données et n'envoie au modèle d'IA que ces nombres agrégés, sans aucun nom d'élève. Définitions retenues : *présent* (au moins un passage valide au portail d'entrée ce jour-là), *retard au portail* (passage au portail d'entrée après la fin de sa fenêtre), *sortie hors plage* (passage au portail de sortie hors fenêtre, avec son heure), *absent* (attendu mais sans passage valide après clôture). Le texte renvoyé par le modèle est contrôlé : chaque nombre présent dans la phrase doit correspondre aux chiffres transmis, avec une seule relance autorisée avant bascule automatique sur un texte gabarit exact.
- **Option écartée :** transmettre au modèle la liste des élèves ou des passages bruts pour qu'il fasse lui-même le décompte.
- **Raison :** protège les données personnelles des élèves mineurs et rend impossible toute erreur de calcul ou invention de chiffre par le modèle d'IA.

---

## Adaptations du modèle de données indicatif

Quatre ajustements ciblés ont été apportés au schéma proposé dans l'énoncé :

- **Une table dédiée pour les établissements (`School`)** au lieu d'un simple champ texte, afin de garantir le cloisonnement strict entre établissements sans risque d'erreur de saisie.
- **Trois types de points de contrôle** (`entry_gate` pour portail d'entrée, `exit_gate` pour portail de sortie, `bus` pour autobus) au lieu de deux (`portail`, `autobus`), pour distinguer sans ambiguïté les entrées et les sorties lors du calcul de la synthèse du jour.
- **Des tables de sécurité et de suivi dédiées** : historique des changements d'état d'un badge (`BadgeStatusHistory`), liens d'activation stockés uniquement sous forme d'empreinte hachée (`BadgeActivationLink`), historique des décalages d'horloge (`ClockOffset`), suivi des appels et coûts IA (`ModelCallLog`) et suivi persistant des tentatives de connexion (`LoginAttempt`).
- **Un lien badge optionnel sur les passages (`Passage.badge` nullable)** afin de pouvoir enregistrer côté serveur les scans rejetés pour badge inconnu ou code illisible avec leur motif, tout en imposant l'unicité d'usage sur les passages valides.
