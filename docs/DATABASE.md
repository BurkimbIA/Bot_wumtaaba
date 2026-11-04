# Base Postgres et calendrier

Depuis la version 0.6, les données vivent dans Postgres chez Supabase, projet `iagyxtstnbgstdyppgnp` (celui de bia-backend), dans le schéma `wuumtaaba`. Un schéma à part sépare ces tables de celles de bia-backend et les tient hors de l’API REST publique de Supabase, qui n’expose que `public`. Les audios vont dans le bucket privé `wuumtaaba-media` du même projet. La machine Fly.io n’a plus de volume : elle peut s’arrêter sans rien perdre.

Le bot crée le schéma et ses tables au démarrage (`src/bot_wumtaaba/schema.sql`, instructions `IF NOT EXISTS`). Les tables et colonnes sont celles de la base SQLite V2 ; les dates restent en secondes depuis 1970 et le JSON en texte.

`DATABASE_URL` pointe vers le pooler de session Supabase (`aws-1-eu-west-3.pooler.supabase.com`, port 5432), joignable en IPv4. L’hôte direct `db.iagyxtstnbgstdyppgnp.supabase.co` n’a qu’une adresse IPv6 : il marche depuis Fly.io, pas depuis un poste sans IPv6. Ne pas utiliser le pooler de transaction (port 6543) : le bot fixe le `search_path` une fois par connexion.

| Tables | Données |
| --- | --- |
| contacts, conversations | Numéro, réglage des rappels, dernière réception |
| messages, inbox, outbox | Contenu reçu/envoyé, traitement durable, identifiants Meta |
| media_assets | Média Meta, nom de l’objet dans le bucket, empreinte, transcription et erreurs |
| delivery_events | Accusés envoyé/livré/lu/échec, dédupliqués |
| agreements | Accord confirmé, propriétaire, client, montants, échéance |
| calendar_events | Échéance à 9 h Africa/Ouagadougou, état de règlement |
| customer_consents | Décision du client, référence, message source et date |
| reminders | Destinataire, rôle, date J−1/J0, état et sortie associée |
| audit_events | Événements de traitement et décisions |

Les messages non autorisés ou hors limites sont archivés comme ignorés. Les contenus des types pris en charge sont enregistrés ; seuls les fichiers audio consentis sont téléchargés. Les URL temporaires Meta ne sont pas archivées. Les fichiers des anciens vocaux traités avant cette version ne sont pas récupérés rétroactivement.

## Envoi des rappels

Toutes les 30 secondes tant que la machine tourne, le worker examine les échéances. Les rappels tombent à 9 h, heure de Ouagadougou (UTC) : une tâche `pg_cron` de Supabase appelle `/healthz` chaque jour à 9 h pour réveiller la machine (voir [le déploiement](DEPLOY_FLY.md#réveil-quotidien-pour-les-rappels)). Les deux destinataires attendent le consentement du client, donné depuis le numéro exact de l’accord par `ACCEPTER WT-référence`. Un accord sans numéro client ne peut pas déclencher ces rappels. STOP, refus, règlement et messages de refus encore en file sont vérifiés avant émission. Les rappels trop anciens ne sont pas rattrapés en masse. La déduplication repose sur les identifiants persistants et la transaction d’enregistrement ; les erreurs ambiguës restent à examiner manuellement.

La fenêtre de texte libre est limitée volontairement à 23 h depuis le dernier message entrant. Au-delà, renseigner `REMINDER_TEMPLATE_NAME=wuumtaaba_rappel_echeance_v1` et `REMINDER_TEMPLATE_LANGUAGE=fr` seulement après approbation Meta. Le modèle reçoit trois paramètres : référence, solde, date ISO. Sans modèle configuré, état `blocked_template`. `REMINDER_ENABLED=false` empêche aussi l’envoi des rappels déjà en file.

## Conservation et sauvegarde

`RETENTION_DAYS=0` conserve l’historique sans purge automatique. Les audios sont lisibles seulement depuis `/admin`, après authentification ; le bucket n’a pas d’URL publique et la clé Supabase reste côté serveur. Ne mettre ni copie de la base, ni audio, ni secret dans GitHub.

L’offre gratuite de Supabase donne 500 Mo de base, partagés avec bia-backend, et 1 Go de stockage de fichiers. Pour une copie de la base : `pg_dump --schema=wuumtaaba`. Supabase met en pause un projet gratuit resté une semaine sans activité ; le réveil quotidien du bot et bia-backend interrogent la base, sans qu’on ait encore vérifié que cela suffit.

## Copier le volume Fly.io vers Supabase

À faire une fois, avant le premier déploiement de la version 0.6. Le script ne supprime rien et peut être relancé : une ligne déjà copiée est ignorée.

```sh
fly ssh console --app bot-wumtaaba -C "python -c \"import sqlite3; sqlite3.connect('/app/data/bot.sqlite3').backup(sqlite3.connect('/app/data/export.sqlite3'))\""
fly ssh console --app bot-wumtaaba -C "tar czf /app/data/media.tgz -C /app/data media"
fly ssh sftp get /app/data/export.sqlite3 backup/bot.sqlite3 --app bot-wumtaaba
fly ssh sftp get /app/data/media.tgz backup/media.tgz --app bot-wumtaaba
tar xzf backup/media.tgz -C backup
uv run python scripts/copy_volume_to_supabase.py backup/bot.sqlite3 backup/media
```

L’API `backup` de SQLite donne une copie cohérente même pendant une écriture, journal WAL compris ; copier le seul fichier `bot.sqlite3` pourrait perdre les dernières transactions. Le script affiche, table par table, le nombre de lignes dans SQLite et dans Postgres, puis le nombre d’audios envoyés dans le bucket ; il s’arrête en erreur si Postgres en a moins. Garder le volume jusqu’à ce que les comptes soient vérifiés et la version 0.6 en service.
