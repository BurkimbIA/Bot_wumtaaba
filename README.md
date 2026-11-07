# Wuumtaaba · le carnet d’accords sur WhatsApp

Une V1 simple : écrire ou dicter un accord en français, vérifier son résumé, puis l’enregistrer. Tout se passe dans une conversation WhatsApp.

**État initial au 10 octobre 2026 : V1 validée sur WhatsApp.** Des appels réels à `gemini-3.8-flash` ont également validé une transcription française sur un WAV synthétisé localement et l’extraction des cinq champs d’un accord. Le service est déployé sur Fly.io et son contrôle de santé répond HTTP 200. Le webhook Meta est vérifié et abonné aux messages. Depuis WhatsApp sur PC, le menu, la saisie d’un accord, la correction du montant et l’enregistrement ont été validés. Un vocal WhatsApp a également été transcrit en français après consentement, puis la transcription a été renvoyée dans la conversation. Après redémarrage, le contrôle de santé répond HTTP 200 et Mes accords restitue le même accord avec ses montants corrigés.

## Accords en français ou mooré — version 0.7

Après **Nouvel accord**, choisir **Français** ou **Mooré**. Le vocal mooré est transcrit et traduit en français par les modèles BurkimbIA, puis Gemini prépare le brouillon. Le résumé offre une lecture vocale facultative dans la langue choisie. Il reste nécessaire de vérifier et confirmer avant d’enregistrer. Le fournisseur BurkimbIA est celui sélectionné dans le back-office, y compris le Mac local. Voir [le parcours, les modèles et les vérifications](docs/AGREEMENT_LANGUAGES.md).

## Paiements partiels et application mobile — version 0.6

Le bot permet désormais de choisir un accord, saisir un versement, vérifier son nouveau solde puis confirmer. Chaque versement est daté dans un journal ; `historique WT-référence` donne les détails. Les accords peuvent être consultés dans l’application Flutter facultative, reliée par un code obtenu avec `application` dans WhatsApp. L’application est en lecture seule : les actions et vocaux restent dans WhatsApp. Voir [le fonctionnement, la migration et l’API mobile](docs/PAYMENTS_MOBILE.md).

Cette section décrit le nouveau code. Les validations de production datées ci-dessous concernent les versions précédentes ; consulter le rapport de livraison pour l’état du déploiement 0.6.

## Le parcours

1. Envoyer `menu`, puis choisir **Nouvel accord** et la langue **Mooré** ou **Français**.
2. Écrire ou dicter : « J’ai vendu deux sacs de riz à Awa pour 30 000 francs CFA. Elle a payé 10 000 francs. Le reste est prévu le 20 octobre 2026. »
3. Le bot restitue la transcription du vocal et demande les informations manquantes.
4. Vérifier le résumé : client, produit, total, avance, reste et date. Corriger si besoin, puis confirmer.
5. Retrouver l’enregistrement dans **Mes accords**.

Le solde est calculé par le code. Un accord enregistré est une **note personnelle du commerçant**, sans confirmation du client. Le consentement aux rappels est distinct de la validation de la vente. Les paiements mobiles ne sont pas intégrés.

## Historique, calendrier et rappels — version 0.2

**Déployée et vérifiée le 10 octobre 2026 : 134 tests passent, Ruff valide le code.** La migration du volume Fly.io conserve l’accord initial, avec sauvegarde préalable et intégrité SQLite vérifiées. La commande `calendrier` a restitué son échéance depuis WhatsApp sur PC. Un nouveau vocal a été consenti, transcrit, renvoyé dans WhatsApp et archivé sur le volume privé avec sa transcription. Les callbacks envoyé/livré/lu sont enregistrés. Les rappels et leurs garde-fous sont validés par tests automatisés ; aucun rappel réel à deux destinataires n’a encore été envoyé.

La base conserve les contacts, conversations, messages entrants/sortants, statuts de livraison, accords, consentements et événements de rappel. Les vocaux reçus sont traités directement ; le fichier sonore privé et sa transcription sont reliés au message d’origine. Les clients connus peuvent aussi demander une transcription sans accéder aux accords du commerçant.

- `calendrier` : échéances du commerçant, à 9 h au Burkina Faso.
- Fournir le numéro international du client dans le brouillon pour préparer ses rappels.
- Le client envoie lui-même `ACCEPTER WT-référence` au bot : les deux destinataires peuvent alors recevoir les rappels J−1/J0. Le commerçant transmet la référence au client.
- `REFUSER WT-référence` retire ce consentement ; `STOP` suspend les rappels du numéro. Après STOP, envoyer `rappels oui`, puis renouveler les consentements client nécessaires.
- `regle WT-référence` : le commerçant clôture son accord et annule ses rappels à venir.

Un contrôle avant chaque envoi vérifie consentement, STOP, état de l’accord et fenêtre de conversation. En dehors de la fenêtre de texte libre retenue de 23 heures, un modèle approuvé par Meta est requis. Le modèle `wuumtaaba_rappel_echeance_v1` est soumis, **en attente d’approbation** ; les rappels concernés restent bloqués tant qu’il n’est pas configuré après approbation. Un envoi ambigu n’est pas retenté automatiquement.

Le calendrier est intégré à la base et consultable dans WhatsApp, sans synchronisation Google Calendar. Voir [le schéma et l’exploitation](docs/DATABASE.md).

## Back-office administrateur — version 0.5

Accès à **[/admin](https://bot-wumtaaba.fly.dev/admin)** : tableau de bord, recherche d’accords, conversations avec audios/transcriptions et suivi des rappels. Les données clients sont consultables sous session administrateur. L’onglet **Modèles IA** permet de choisir **Mac local** ou **Cloud BurkimbIA (RunPod)** ; voir [le fonctionnement du connecteur](docs/BURKIMBIA.md#choisir-les-modèles-mac-ou-cloud). Le code d’accès est dans le fichier local privé `.admin-access.txt` ; il ne figure pas dans GitHub. Voir [la configuration et les limites](docs/ADMIN.md).

### Wumtaaba Check

Depuis le backoffice, **Wumtaaba Check** ouvre [/admin/check](https://bot-wumtaaba.fly.dev/admin/check)
avec la même session administrateur : historique audio en lignes, écoute, choix
Mac/cloud et trois zones de résultats remplacées à chaque analyse.
Les tests enregistrés et leurs derniers résultats sont conservés dans la table
Supabase `wuumtaaba.experiments` ; les fichiers sont dans le bucket privé existant
sous `experiments/`, accessibles uniquement par les routes admin authentifiées.
Ils sont retrouvables depuis les autres navigateurs après connexion. Un audio
ajouté reste temporaire jusqu’au clic **Enregistrer** ; les résultats d’un audio
enregistré sont ensuite actualisés après chaque analyse. Supprimer une ligne
supprime sa fiche et son fichier privé. Limites : 100 tests, 7 Mo par audio.
Les essais ne créent pas d’accord réel et ne modifient pas le fournisseur du bot.
Le mode cloud de Check appelle directement RunPod avec `RUNPOD_API_KEY` et
`RUNPOD_TRANSCRIPTION_ENDPOINT_ID`, sans passer par le token de l’API BurkimbIA.
Il propose Whisper pour la transcription seule. NLLB n’a plus d’endpoint RunPod ;
Mistral et Tengsoaba sont exclus des essais cloud. Les zones traduction et détails
restent vides en mode cloud. Utiliser le Mac pour une analyse complète.
Les modèles Mac nécessitent le connecteur actif et les poids déjà installés ;
l’extraction du texte français utilise Gemini.

## Entrée directe dans Wuumtaaba

`menu`, `bonjour`, `salut` et `accueil` ouvrent directement le carnet : **Nouvel accord**, **Mes accords**, **Ajouter paiement**. `annuler` abandonne le brouillon et revient au carnet. Le choix entre BurkimbIA et Wuumtaaba et les services autonomes de traduction, transcription, synthèse et Yaaba ont été retirés.

Les modèles BurkimbIA restent utilisés pour les accords en mooré : transcription, traduction et lecture du résumé. Le fournisseur Mac/Cloud reste sélectionnable dans le back-office. Les brouillons Wuumtaaba existants sont conservés ; les anciennes sessions de services et leurs boutons reviennent au carnet sans traiter leurs vocaux en attente. Voir [les accords en mooré](docs/AGREEMENT_LANGUAGES.md).

## Supabase et arrêt à vide — migration de la version 0.7

Les données passent du SQLite du volume Fly.io à Postgres chez Supabase (projet de bia-backend, schéma `wuumtaaba`), et les audios à un bucket privé du même projet. Sans volume, la machine s’arrête d’elle-même quand sa file est vide et repart à la requête suivante ; Supabase la réveille chaque jour à 9 h pour les rappels. Voir [la base](docs/DATABASE.md), dont la copie du volume, et [le déploiement](docs/DEPLOY_FLY.md).

## Démarrer en local

Il faut [uv](https://docs.astral.sh/uv/) et [just](https://just.systems) ; uv installe Python 3.12.

```sh
just install
cp .env.example .env
```

Remplir `.env` localement :

| Variable | Valeur attendue |
| --- | --- |
| `WHATSAPP_TOKEN` | Token Meta autorisé à envoyer des messages pour le numéro configuré |
| `WHATSAPP_PHONE_NUMBER_ID` | Identifiant du numéro dans WhatsApp > configuration API, pas le numéro de téléphone |
| `META_APP_SECRET` | Secret de l’application Meta, utilisé pour vérifier la signature des webhooks |
| `WEBHOOK_VERIFY_TOKEN` | Secret aléatoire choisi pour la vérification du webhook, au moins 24 caractères |
| `GEMINI_API_KEY` | Clé Gemini créée dans Google AI Studio pour la transcription et l’extraction des champs |
| `PUBLIC_ACCESS` | `true` pour ouvrir le bot à tous ; `false` pour le pilote fermé (défaut local) |
| `ALLOWED_SENDERS` | Numéros des testeurs au format international sans `+`, séparés par des virgules |
| `DATABASE_URL` | Chaîne de connexion Postgres de Supabase, pooler de session (voir [la base](docs/DATABASE.md)) |
| `SUPABASE_URL` | `https://iagyxtstnbgstdyppgnp.supabase.co` |
| `SUPABASE_API_KEY` | Clé secrète du projet Supabase, pour le bucket privé des audios |

Créer ou sélectionner une clé dans [Google AI Studio](https://aistudio.google.com/api-keys). Pour saisir les trois secrets sans les afficher, lancer `uv run python scripts/setup_secrets.py`. Le bot utilise `gemini-3.8-flash` pour la transcription et l’extraction des champs ; aucun compte OpenAI n’est nécessaire.

Les autres réglages et modèles sont dans `.env.example`. Générer le secret du webhook avec `python -c "import secrets; print(secrets.token_urlsafe(32))"`, puis le conserver uniquement dans `.env` et la configuration Meta. Ne jamais publier un token, une capture le contenant ou le fichier `.env`.

```sh
uv run python scripts/check_config.py
just run
```

`GET /healthz` vérifie que le service HTTP répond ; il ne garantit pas la validité des tokens ni la disponibilité des fournisseurs. Le serveur doit être lancé avec **un seul processus** : le worker est intégré au serveur.

Voir [la configuration Meta et le protocole de test réel](docs/SETUP_META.md).

## Exécuter dans Docker

```sh
docker compose up --build -d
docker compose logs -f bot
```

Compose expose le service sur `127.0.0.1:8000` ; les données sont dans la base indiquée par `DATABASE_URL`. Placer un accès HTTPS devant ce port pour recevoir les webhooks. Aucun hébergement payant n’est provisionné par ces commandes.

Pour l’héberger, suivre [le déploiement Fly.io](docs/DEPLOY_FLY.md) : une machine à Paris, sans volume, qui s’arrête quand il n’y a rien à faire.

## Vérifier le code

```sh
just lint
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres just test
```

Les tests ont besoin d’un Postgres local : chaque test crée son propre schéma et le supprime ensuite. GitHub Actions exécute ces contrôles sur Python 3.12, avec un Postgres de service, sans identifiants de production et sans envoyer de messages WhatsApp.

## Organisation

```text
src/bot_wumtaaba/   API, intégrations et logique des accords
scripts/           Vérification de configuration, copie du volume Fly.io vers Supabase
tests/             Tests automatisés isolés des fournisseurs
docs/              Installation et recette WhatsApp
```

FastAPI reçoit le webhook ; le worker traite les événements ; HTTPX communique avec Meta, Google Gemini et Supabase Storage ; Postgres (Supabase) conserve les états et accords. Le flux et les précautions d’exploitation sont décrits dans [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Limites de la V1

- Production : `PUBLIC_ACCESS=true` dans Fly.io. Chaque utilisateur conserve son propre carnet, les consentements et la limite de messages restent appliqués.
- Pilote fermé (`PUBLIC_ACCESS=false`) : seuls les commerçants de `ALLOWED_SENDERS` et les clients dont le numéro figure dans un accord sont traités. Avec le numéro de test Meta, le testeur doit aussi être vérifié dans l’interface Meta.
- Vocaux limités à 14 000 000 octets pour leur transmission intégrée à Gemini. Le bot télécharge et transcrit directement les vocaux reçus ; la transcription et le résumé sont affichés avant confirmation de l’accord.
- Français et montants entiers en F CFA. La transcription peut se tromper : lire le résumé avant de confirmer.
- Consultation des données et sélection Mac/Cloud dans le back-office ; pas d’encaissement ni de signature électronique.
- Les appels Meta/Google Gemini peuvent avoir un coût. Le token utilisateur Meta installé expire le **9 décembre 2026 à 13:10:50 UTC** : prévoir son renouvellement ou une configuration adaptée avec un token d’utilisateur système pour prolonger le service.
- **Conservation sans purge automatique par défaut** (`RETENTION_DAYS=0`) pour l’historique et les fichiers audio. Surveiller la base (500 Mo partagés avec bia-backend) et le bucket des audios (1 Go) sur l’offre gratuite de Supabase.
- Les textes et vocaux sont transmis aux fournisseurs nécessaires au traitement. Ne pas utiliser le pilote avec des données sensibles ; définir accès, durée de conservation et sauvegardes avant une ouverture plus large.

## CI et déploiement automatique

Le dépôt est hébergé sur [BurkimbIA/Bot_wumtaaba](https://github.com/BurkimbIA/Bot_wumtaaba). Chaque pull request vérifie Ruff, les tests et la construction Docker. Un push sur `main` lance les mêmes contrôles puis déploie `bot-wumtaaba` sur Fly.io et vérifie `/healthz`. Le workflow peut également être lancé manuellement sur `main`. Les déploiements sont sérialisés.

Le secret GitHub `FLY_API_TOKEN` est un jeton de déploiement limité à cette application (validité initiale : un an). Les secrets WhatsApp, Gemini et BurkimbIA restent dans les secrets Fly ; ils ne sont pas injectés dans le workflow. En local, `.env` est exclu de Git et du contexte Docker. Renouveler le jeton de déploiement avant son expiration.
