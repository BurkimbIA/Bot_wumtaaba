# Schéma local et migrations versionnées

La baseline `migrations/0001_production_baseline.sql` représente le schéma
applicatif `wuumtaaba` de production, sans lignes métier ni propriétaires.
`baseline.json` contient son empreinte de catalogue, pour vérifier l’identité
structurelle avant adoption sur une base déjà créée.

Depuis la racine, avec l’environnement Python du projet :

```sh
python scripts/dev_database.py --env-file .env.dev plan
python scripts/dev_database.py --env-file .env.dev fingerprint
python scripts/dev_database.py --env-file .env.dev apply --adopt-baseline
python scripts/dev_database.py --env-file .env.dev status
```

`plan`, `status` et `fingerprint` ouvrent une connexion PostgreSQL explicitement
en lecture seule. Sans `--production`, seule une URL loopback vers la base
`wumtaaba_dev` est acceptée, sans paramètres URL pouvant rediriger la connexion.

Pour une nouvelle évolution, ajouter le prochain numéro libre (`0003_nom.sql`
après les sessions administrateur), puis tester `apply` en dev.
Les SQL et leur enregistrement sont appliqués dans une transaction unique, avec
verrou consultatif empêchant deux runners simultanés. La table de suivi
`wumtaaba_migrations.schema_versions` est séparée du schéma applicatif et conserve
les SHA-256 ; modifier une migration déjà appliquée bloque toute application.

Sur une base qui contient déjà `wuumtaaba`, la baseline ne se rejoue pas :
`--adopt-baseline` est obligatoire et n’est accepté que si l’empreinte correspond
au manifest. Il n’existe aucune commande reset/drop.

Pour la production, préparer le code revu et une sauvegarde via le processus
opérationnel habituel, puis lire le plan avec une configuration privée :

```sh
python scripts/dev_database.py --env-file .env.prod --production plan
```

L’application exige une commande explicite `apply --production`. Le fichier
`.env.prod` et toutes les URL contenant des identifiants restent hors Git.
Aucun démarrage local n’applique automatiquement de migration distante.

## Publication via la CI/CD

L’image de production contient les scripts et les migrations, sans fichier `.env`.
Le `release_command` de Fly lit directement le secret `DATABASE_URL` de l’application
et `DATABASE_SCHEMA=wuumtaaba` :

```sh
python scripts/dev_database.py --from-environment --production apply --adopt-baseline
```

La première publication adopte la baseline uniquement si son empreinte correspond
exactement à la production, puis applique les migrations suivantes. Une différence
de schéma ou de checksum arrête la publication avant le remplacement du serveur.
Les publications suivantes ne rejouent que les nouvelles migrations. Le contrat
entre les migrations complètes et le schéma du serveur est testé en CI sur une base
jetable : démarrer le Store ne doit pas changer ce schéma.

Les migrations doivent être compatibles avec la version encore en service :
un échec du déploiement après migration ne restaure pas automatiquement la base.
La baseline contient la structure, jamais les données de test ; publier le code
ne transfère ni les audios ni les accords du dev en production.
