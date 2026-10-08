# 🏠 Bot Telegram de surveillance des logements CROUS (Île-de-France)

Ce projet surveille automatiquement le site officiel
[trouverunlogement.lescrous.fr](https://trouverunlogement.lescrous.fr) et vous
envoie une **alerte Telegram** dès qu'un nouveau logement étudiant apparaît en
**Île-de-France (Paris inclus)**, ou dès qu'un logement est réapprovisionné.

Le bot tourne **entièrement sur GitHub Actions (gratuit)**. Aucun serveur, aucun
VPS, aucune VM, aucun Oracle Cloud, aucun Docker, aucun hébergement payant.

> ⚠️ **Ce bot fait uniquement de la surveillance et de l'alerte.** Il ne
> réserve rien, ne se connecte à aucun compte, ne remplit aucun formulaire. La
> réservation reste 100 % manuelle, de votre côté, sur le site CROUS.

---

## 📋 Sommaire

1. [Fonctionnalités](#-fonctionnalités)
2. [Comment ça marche](#-comment-ça-marche)
3. [Prérequis](#-prérequis)
4. [Étape 1 — Créer le bot Telegram (BotFather)](#-étape-1--créer-le-bot-telegram-botfather)
5. [Étape 2 — S'abonner avec /start](#-étape-2--sabonner-avec-start)
6. [Étape 3 — Créer un compte GitHub](#-étape-3--créer-un-compte-github)
7. [Étape 4 — Créer le dépôt et importer les fichiers](#-étape-4--créer-le-dépôt-et-importer-les-fichiers)
8. [Étape 5 — Configurer les secrets GitHub](#-étape-5--configurer-les-secrets-github)
9. [Étape 6 — Activer GitHub Actions](#-étape-6--activer-github-actions)
10. [Étape 7 — Lancer le bot manuellement](#-étape-7--lancer-le-bot-manuellement)
11. [Fréquence de surveillance (planification cron)](#-fréquence-de-surveillance-planification-cron)
12. [Voir les logs](#-voir-les-logs)
13. [Types de messages Telegram](#-types-de-messages-telegram)
14. [Limites du plan gratuit](#-limites-du-plan-gratuit)
15. [Comment l'état est sauvegardé](#-comment-létat-est-sauvegardé)
16. [Dépannage](#-dépannage)
17. [Conseils pratiques pendant les périodes chargées](#-conseils-pratiques-pendant-les-périodes-chargées)
18. [Rappel légal](#-rappel-légal)

---

## ✨ Fonctionnalités

- Surveillance de **toute l'Île-de-France** via une zone géographique (bounding box).
- **Détection automatique** de l'identifiant de campagne CROUS (« tool id ») en
  lisant la page d'accueil, avec une valeur de secours configurable.
- **Premier démarrage silencieux** : le bot enregistre tous les logements
  existants sans spammer, et envoie un **seul** message de confirmation.
- **Alertes de nouveaux logements**.
- **Alertes de réapprovisionnement** (quand le nombre d'unités disponibles augmente).
- **Abonnement automatique** : toute personne qui envoie `/start` au bot reçoit
  ensuite les alertes ; `/stop` permet de se désabonner.
- Messages Telegram en **français**, avec mise en forme HTML et **lien cliquable**
  vers l'annonce (libellé, résidence, adresse, surface, loyer, lien de réservation).
- **Surveillance des pannes** : après 3 échecs consécutifs, une alerte est
  envoyée (une seule fois), puis un message de **rétablissement** après le retour
  à la normale.
- **Battement de cœur toutes les 8 heures** (heartbeat) indiquant que le bot est
  vivant et le nombre de logements suivis.
- **Fiabilité Telegram** : ré-essais, backoff exponentiel, respect du `retry_after`.
- **État jamais corrompu** : toute exception est capturée et l'état sauvegardé
  reste cohérent.

---

## ⚙️ Comment ça marche

À chaque exécution planifiée, GitHub Actions lance **20 cycles espacés de 15
secondes**. À chaque cycle, le bot :

1. charge l'état précédent (`state.json`) ;
2. lit les nouvelles commandes Telegram (`/start` et `/stop`) ;
3. interroge l'API publique de recherche CROUS sur la zone Île-de-France ;
4. compare avec l'état précédent (nouveaux logements + réapprovisionnements) ;
5. envoie les alertes à tous les abonnés ;
6. sauvegarde le nouvel état localement.

À la fin des 20 cycles, le workflow **committe** une seule fois l'état final
dans le dépôt, puis se termine proprement.

Il n'y a **aucun serveur permanent**. Chaque exécution GitHub Actions surveille
pendant environ 4 minutes 45, puis l'exécution planifiée suivante prend le relais.

---

## 🧩 Prérequis

- Un compte **Telegram** (application mobile ou bureau).
- Un compte **GitHub** (gratuit).
- 15 minutes de mise en place. Aucune compétence en programmation nécessaire.

---

## 🤖 Étape 1 — Créer le bot Telegram (BotFather)

1. Dans Telegram, cherchez **@BotFather** (le compte officiel avec la coche bleue).
2. Ouvrez une conversation et envoyez la commande :
   ```
   /newbot
   ```
3. Choisissez un **nom** (ex. « Alerte CROUS ») puis un **nom d'utilisateur** se
   terminant par `bot` (ex. `mon_alerte_crous_bot`).
4. BotFather vous répond avec un **token** de la forme :
   ```
   123456789:AAH5x...votre_token...
   ```
5. **Copiez ce token** et gardez-le secret. C'est votre `TELEGRAM_BOT_TOKEN`.
6. Après le déploiement (étape 7), chaque personne pourra ouvrir la conversation
   avec le bot et lui envoyer `/start` pour recevoir les alertes.

---

## 🆔 Étape 2 — S'abonner avec /start

Aucun Chat ID n'est à chercher ni à configurer. Une fois le bot déployé :

1. Ouvrez une conversation avec votre bot Telegram.
2. Appuyez sur **Démarrer / Start** ou envoyez `/start`.
3. Le bot confirme l'abonnement lors de la prochaine exécution GitHub Actions
   (généralement dans les 5 minutes avec la planification par défaut,
   hors retard éventuel de GitHub Actions).

Chaque personne qui effectue ces étapes est ajoutée automatiquement. Pour ne
plus recevoir les messages, elle peut envoyer `/stop`.

---

## 🐙 Étape 3 — Créer un compte GitHub

1. Allez sur [github.com](https://github.com) et cliquez sur **Sign up**.
2. Créez un compte gratuit (email + mot de passe + vérification).
3. Connectez-vous.

---

## 📦 Étape 4 — Créer le dépôt et importer les fichiers

1. En haut à droite sur GitHub, cliquez sur **+** puis **New repository**.
2. Donnez un nom, par exemple `bot-crous`.
3. **Choisissez « Public »** (fortement recommandé — voir la section
   [Limites du plan gratuit](#-limites-du-plan-gratuit) : les dépôts publics ont
   des minutes GitHub Actions **gratuites et illimitées**). Le fichier `state.json`
   sera public, mais les identifiants Telegram des abonnés y sont enregistrés
   sous forme **chiffrée** et ne sont jamais écrits en clair.
4. Cliquez sur **Create repository**.
5. Importez les fichiers du projet. Deux méthodes :
   - **Simple (glisser-déposer)** : sur la page du dépôt, cliquez sur
     **Add file → Upload files**, puis déposez tous les fichiers **en conservant
     l'arborescence** (le dossier `.github/workflows/` doit être conservé). Si le
     glisser-déposer ne conserve pas les dossiers, créez d'abord le fichier
     `.github/workflows/crous-monitor.yml` via **Add file → Create new file** en
     tapant ce chemin complet dans le champ du nom, puis collez son contenu.
   - **Avec Git (avancé)** :
     ```bash
     git init
     git add .
     git commit -m "Initial commit"
     git branch -M main
     git remote add origin https://github.com/VOTRE_COMPTE/bot-crous.git
     git push -u origin main
     ```

Arborescence attendue :

```
bot-crous/
├── crous_bot.py
├── requirements.txt
├── .env.example
├── README.md
└── .github/
    └── workflows/
        └── crous-monitor.yml
```

---

## 🔐 Étape 5 — Configurer les secrets GitHub

Les identifiants Telegram ne doivent **jamais** être écrits dans le code. On les
stocke dans les **secrets** GitHub (chiffrés).

1. Sur la page du dépôt, allez dans **Settings**.
2. Menu de gauche : **Secrets and variables → Actions**.
3. Cliquez sur **New repository secret** et créez :
   - Nom : `TELEGRAM_BOT_TOKEN` — Valeur : le token de BotFather.
4. Enregistrez-le avec **Add secret**.

`TELEGRAM_CHAT_ID` n'est plus nécessaire : les destinataires sont gérés par
les commandes `/start` et `/stop`.

Optionnel : vous pouvez aussi créer `SUBSCRIBER_ENCRYPTION_KEY` avec une clé
Fernet afin de pouvoir régénérer le token du bot sans perdre les abonnements.
Sans ce secret, le chiffrement utilise automatiquement une clé dérivée du token.

> Les secrets sont masqués dans les logs et ne sont jamais visibles publiquement.

---

## ▶️ Étape 6 — Activer GitHub Actions

1. Sur la page du dépôt, cliquez sur l'onglet **Actions**.
2. Si GitHub demande une confirmation pour activer les workflows, cliquez sur
   **I understand my workflows, go ahead and enable them**.
3. Le workflow **CROUS Monitor** apparaît dans la liste à gauche.

---

## 🚀 Étape 7 — Lancer le bot manuellement

Avant d'attendre la planification, testez tout de suite :

1. Sur Telegram, ouvrez votre bot et envoyez `/start`.
2. Onglet **Actions** → cliquez sur **CROUS Monitor** (à gauche).
3. Bouton **Run workflow** (à droite) → **Run workflow**.
4. Au bout de quelques secondes, une exécution démarre. Cliquez dessus pour
   suivre les étapes.
5. Vous devriez recevoir **« ✅ Abonnement CROUS activé »**. C'est bon signe :
   votre conversation est maintenant abonnée à tous les messages du bot.

---

## ⏱️ Fréquence de surveillance (planification cron)

Le bot interroge CROUS environ toutes les **15 secondes** pendant une exécution.
La planification définie dans `.github/workflows/crous-monitor.yml` relance cette
série de 20 contrôles toutes les 5 minutes :

```yaml
- cron: "2-59/5 * * * *"   # toutes les 5 minutes (heure UTC)
```

Pour changer la fréquence de lancement des séries, modifiez cette ligne, par
exemple :

| Fréquence            | Valeur cron        |
|----------------------|--------------------|
| Toutes les 5 minutes | `"*/5 * * * *"`    |
| Toutes les 15 minutes| `"*/15 * * * *"`   |
| Toutes les 30 minutes| `"*/30 * * * *"`   |
| Toutes les heures    | `"0 * * * *"`      |

Remarques importantes :

- L'intervalle cron **minimum** autorisé par GitHub est de **5 minutes**. Les
  contrôles à 15 secondes sont réalisés à l'intérieur de chaque exécution.
- Les heures cron sont en **UTC** (l'heure de Paris est UTC+1 en hiver, UTC+2 en été).
- Les exécutions planifiées peuvent parfois être **légèrement retardées** quand
  la plateforme GitHub est très sollicitée. C'est normal et gratuit.

### Le bot tourne-t-il en continu (24h/24) ?

Oui, **de fait**. GitHub relance automatiquement une exécution toutes les 5
minutes (ou selon votre `cron`) et chaque exécution effectue 20 contrôles. Mises
bout à bout, elles assurent une surveillance quasi continue sans serveur à
maintenir.

Deux points à comprendre :

- Le bouton **Run workflow** (étape 7) est un lancement **manuel** et ponctuel.
  Il est **indépendant** de la planification automatique.
- Après le tout premier envoi sur GitHub, la planification peut mettre **quelques
  dizaines de minutes** à démarrer. Patientez, puis vérifiez l'onglet **Actions** :
  les exécutions automatiques y apparaissent avec le déclencheur **« Scheduled »**.

Les contrôles d'une exécution sont espacés d'environ 15 secondes. Un court écart
peut toutefois survenir entre deux exécutions, et GitHub peut retarder le début
d'un workflow planifié lorsque sa plateforme est chargée.

---

## 📜 Voir les logs

1. Onglet **Actions** → cliquez sur une exécution de **CROUS Monitor**.
2. Cliquez sur le job **monitor**, puis dépliez l'étape **Run CROUS monitor**.
3. Vous y voyez tout le déroulé : détection du tool id, nombre de logements
   récupérés, différences détectées, envois Telegram, sauvegarde de l'état.

Ces logs sont votre meilleur outil de diagnostic en cas de problème.

---

## 💬 Types de messages Telegram

Le bot envoie sept types de messages, tous en français :

1. **Abonnement** — confirme `/start` ou `/stop` pour la conversation concernée.
2. **Activation** (au tout premier démarrage) — confirme que la surveillance est
   lancée et indique le nombre de logements déjà présents.
3. **Nouveau logement** 🚨 — libellé, résidence, adresse, surface, loyer et lien
   cliquable de réservation.
4. **Réapprovisionnement** ♻️ — quand un logement déjà connu voit son nombre
   d'unités disponibles augmenter.
5. **Alerte panne** ⚠️ — après 3 échecs consécutifs (envoyée une seule fois pour
   éviter le spam).
6. **Rétablissement** ✅ — une fois que le bot refonctionne après une panne.
7. **Battement de cœur** 💓 — un statut périodique confirmant que le bot est vivant
   et indiquant le nombre de logements surveillés.

Toutes les alertes générales sont diffusées à **chaque conversation abonnée**.

---

## 🆓 Limites du plan gratuit

- **Dépôt public = minutes GitHub Actions gratuites et illimitées.** C'est
  l'option recommandée pour une surveillance fréquente et continue.
- **Dépôt privé = 2000 minutes gratuites par mois.** Chaque exécution dure
  environ 5 minutes facturées. À raison d'une exécution toutes les 5 minutes,
  ce mode épuise très rapidement ce quota.
  Sur un dépôt privé, augmentez donc l'intervalle (par ex. `"*/45 * * * *"` ou
  `"0 * * * *"`) pour rester dans les 2000 minutes, **ou** passez le dépôt en
  public.
- **Intervalle minimum** : 5 minutes.
- **Workflows planifiés inactifs** : GitHub peut désactiver la planification si
  le dépôt n'a **aucune activité pendant 60 jours**. Comme le bot committe l'état
  régulièrement, il reste actif tout seul. Si jamais il est désactivé, un simple
  clic sur **Enable workflow** dans l'onglet Actions le relance.

---

## 💾 Comment l'état est sauvegardé

L'état est stocké dans le fichier **`state.json`**, **committé automatiquement**
dans votre dépôt à la fin de chaque exécution qui modifie quelque chose. C'est le
mécanisme de persistance **natif GitHub** le plus robuste : rien n'est jamais
perdu, même si une exécution échoue.

- L'écriture est **atomique** (fichier temporaire puis renommage) : le fichier
  n'est jamais laissé à moitié écrit.
- La liste des abonnés et la position de lecture Telegram sont persistées dans
  ce fichier. Les Chat IDs sont chiffrés avec une clé dérivée du token du bot :
  même dans un dépôt public, ils ne sont pas lisibles.
- En cas d'échec, **la liste des logements n'est pas modifiée** ; seul le
  compteur d'échecs est mis à jour. L'état ne peut donc pas être corrompu.
- Sur les exécutions « calmes » (aucun changement), rien n'est committé : pas de
  pollution inutile de l'historique.

Vous n'avez rien à faire : `state.json` se crée et se met à jour tout seul.

---

## 🛠️ Dépannage

**Je ne reçois aucun message Telegram.**
- Avez-vous bien envoyé `/start` à votre bot ? Un simple message sans cette
  commande ne déclenche pas l'abonnement.
- `TELEGRAM_BOT_TOKEN` est-il correct dans **Settings → Secrets → Actions** ?
- Avez-vous attendu l'exécution planifiée suivante, ou lancé le workflow à la
  main après `/start` ?
- Regardez les logs (étape *Run CROUS monitor*). Une erreur Telegram y apparaît
  si le token est invalide ou si `getUpdates` est indisponible.

**J'ai changé/régénéré le token du bot.**
- Par défaut, le registre des abonnés est chiffré à partir du token. Après une
  rotation du token, effacez la valeur `subscribers_encrypted` dans `state.json`
  (mettez-la à `null`) puis demandez aux utilisateurs de renvoyer `/start`.

**Quel identifiant de campagne (tool id) le bot utilise-t-il ?**
- Par défaut, le bot est **fixé sur l'identifiant 47** (Île-de-France 2026/2027).
  La détection automatique est **désactivée** car la page d'accueil peut renvoyer
  un mauvais identifiant.
- Pour vérifier l'identifiant réel : ouvrez la page de recherche CROUS et lisez
  le nombre dans l'URL `/tools/<ID>/search`.
- **Changer d'identifiant** (par ex. l'an prochain) : modifiez la constante
  `FALLBACK_TOOL_ID` en haut de `crous_bot.py`, **ou** créez un secret optionnel
  `CROUS_TOOL_ID` avec la bonne valeur. Le secret l'emporte sur tout le reste.
- Pour réactiver la détection automatique : créez le secret
  `CROUS_AUTODETECT_TOOL_ID` avec la valeur `true`.

**Je viens de changer le tool id / la campagne : dois-je faire autre chose ?**
- Oui : **supprimez le fichier `state.json`** du dépôt (bouton corbeille sur
  GitHub, puis *Commit*). L'exécution suivante repart comme un premier démarrage
  (enregistrement silencieux + un seul message de confirmation), ce qui évite de
  recevoir d'un coup une alerte pour chaque logement de la nouvelle campagne.
  Cette suppression efface aussi les abonnements : chaque utilisateur devra
  ensuite renvoyer `/start`.

**Le bot tourne mais ne détecte aucun logement.**
- C'est peut-être simplement qu'il n'y a rien de disponible à cet instant.
- Sinon, l'API CROUS a pu changer le nom de certains champs. Les logs affichent
  au premier passage la ligne `Sample raw item keys: [...]` : comparez ces clés
  avec celles utilisées dans la fonction `parse_listing` de `crous_bot.py` et
  ajustez si besoin. Le code est volontairement tolérant pour éviter les plantages.

**J'ai trop de commits `chore: update CROUS monitoring state`.**
- C'est normal : c'est la sauvegarde de l'état. Ils n'apparaissent que quand
  quelque chose change réellement et n'ont aucun impact.

**L'exécution planifiée ne part pas à l'heure exacte.**
- Les tâches cron GitHub peuvent être légèrement retardées en cas de forte charge.
  C'est une limite connue du service gratuit.

---

## 🎯 Conseils pratiques pendant les périodes chargées

Les logements CROUS d'Île-de-France partent **très vite**, parfois en quelques
minutes, surtout en été (juillet–septembre) pour l'année universitaire à venir.
Pour maximiser vos chances :

- **Restez connecté(e) à votre compte CROUS** dans un onglet de navigateur ouvert
  en permanence pendant les périodes de forte demande. Ainsi, quand une alerte
  arrive, vous êtes déjà authentifié(e) et vous pouvez réserver immédiatement sans
  perdre de temps à vous connecter.
- **Activez les notifications Telegram** (son + bannière) pour ce bot afin de
  réagir en quelques secondes.
- Gardez le **lien de la page de recherche** en favori et vos informations
  (RIB, garant, documents) prêtes.
- Pendant les gros pics, conservez les 20 contrôles espacés de 15 secondes et
  l'intervalle cron par défaut de 5 minutes sur un dépôt public.
- La réservation reste **manuelle et rapide** : l'alerte vous fait juste gagner
  les précieuses minutes d'avance.

---

## ⚖️ Rappel légal

Ce bot se contente de **consulter des informations publiques** et de vous
**notifier**. Il ne réserve pas, ne se connecte pas, ne remplit aucun formulaire
et n'automatise aucune action sur le site CROUS. Utilisez-le de façon raisonnable
(un contrôle toutes les 15 secondes par défaut) et dans le respect des conditions
d'utilisation du site CROUS. Toute réservation se fait manuellement, par vous,
sur le site officiel.
