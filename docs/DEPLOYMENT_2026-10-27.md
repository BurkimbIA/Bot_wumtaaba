# Déploiement production — 27 octobre 2026

Déploiement explicitement demandé par Josias dans le chat Wuumtaaba.

- Source applicative : commit `628a7a52d783c0dfada47083812a6b2e5df6d831` de `feat/partial-payments-mobile`.
- Image : `registry.fly.io/bot-wumtaaba:deployment-01M5ZEF3BDV331AEMA80JG0RJM`.
- Machine conservée : `843ed0a4259058`, `cdg`, un CPU partagé, 512 Mo.
- Volume conservé : `vol_rkgoymgk6z3x5z24`, monté sur `/app/data`.
- Image précédente : `registry.fly.io/bot-wumtaaba:deployment-01M538TFQSVKXADVG99AF8CX9J`.

## Sauvegarde préalable

Sauvegarde SQLite cohérente créée via `Connection.backup()` sur le serveur, puis téléchargée avant déploiement.

- Copie serveur : `/app/data/predeploy-0.6-20261027.sqlite3`.
- Copie externe privée : `/Users/josias/.local/share/wuumtaaba/backups/bot-predeploy-0.6-20261027.sqlite3`, permissions 0600.
- Taille : 1 101 824 octets.
- SHA-256 : `c439be7d53daac4a1f05b479372105e6e5728b0a8736530d5ec81723c8cd9c64`.
- Empreintes identiques et intégrité locale vérifiée. La sauvegarde ne figure pas dans Git.

## Vérifications

- 262 tests et Ruff réussis avec les versions exactes de `requirements.lock`.
- Déploiement sur une machine, sans création d’une réplique.
- Contrôles Fly de démarrage, santé et DNS réussis.
- `https://bot-wumtaaba.fly.dev/healthz` : HTTP 200, version 0.6.0.
- Lecture mobile sans session : HTTP 401, aucune donnée exposée.
- Base après migration : intégrité OK, trois accords conservés, migrations 2 et 3. Deux avances historiques migrées. Aucun solde négatif ni différence entre cumul des paiements et colonne `paid`.
- Numéro officiel du bot vérifié auprès de Meta : +226 01 78 00 18 (Wumtaaba). Aucun secret Meta affiché ni modifié.

## Application de test

Application Flutter hors démonstration, API HTTPS de production, numéro public du bot configuré. Installée sur l’iPhone 17 simulé (iOS 27.0) dans Device Hub. Pour Keychain, utiliser une compilation de simulateur signée normalement par Xcode (`flutter build ios --simulator`) ; `--no-codesign` ne permet pas la lecture du stockage sécurisé.

L’appairage suit le parcours normal : commande `application` depuis le WhatsApp du propriétaire, code à usage unique dans l’application. Aucun code ni jeton réel ne doit être archivé dans ce rapport. Aucun versement fictif n’est ajouté à la production pour la recette.
