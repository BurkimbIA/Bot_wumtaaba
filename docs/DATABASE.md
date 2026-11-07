# Base Postgres et calendrier

Après la bascule Supabase de la version 0.7, les données vivent dans Postgres chez Supabase, projet `iagyxtstnbgstdyppgnp` (celui de bia-backend), dans le schéma `wuumtaaba`. Un schéma à part sépare ces tables de celles de bia-backend et les tient hors de l’API REST publique de Supabase, qui n’expose que `public`. Les audios vont dans le bucket privé `wuumtaaba-media` du même projet. La machine Fly.io n’a plus de volume : elle peut s’arrêter sans rien perdre.

Le bot crée le schéma et ses tables au démarrage (`src/bot_wumtaaba/schema.sql`, instructions `IF NOT EXISTS`). Les tables et colonnes sont celles de la base SQLite V2 ; les dates restent en secondes depuis 1970 et le JSON en texte.

Toutes les tables du schéma ont la sécurité par ligne (RLS), activée par le bot au démarrage. Le bot en est propriétaire et n’est pas concerné ; tout autre rôle ne voit aucune ligne sans règle, même si le schéma était un jour ouvert à l’API REST. La seule règle, `mac_connector`, laisse le rôle du connecteur Mac lire la ligne de `mac_link` et y écrire `last_seen` (voir `BURKIMBIA.md`).

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
| payments | Versements et historique, rattachés à leur accord |
| mobile_codes, mobile_tokens | Codes temporaires et sessions mobiles liés au propriétaire |
| reminders | Destinataire, rôle, date J−1/J0, état et sortie associée |
| audit_events | Événements de traitement et décisions |
| mac_link | Une ligne : dernier signe de vie du connecteur Mac et heure jusqu’à laquelle le bot l’attend |

Les messages non autorisés ou hors limites sont archivés comme ignorés. Les contenus des types pris en charge sont enregistrés ; les vocaux reçus et autorisés sont téléchargés directement, sans demande de confirmation audio. Les URL temporaires Meta ne sont pas archivées. Les fichiers des anciens vocaux traités avant cette version ne sont pas récupérés rétroactivement.

## Envoi des rappels

Toutes les 30 secondes tant que la machine tourne, le worker examine les échéances. Les rappels tombent à 9 h, heure de Ouagadougou (UTC) : une tâche `pg_cron` de Supabase appelle `/healthz` chaque jour à 9 h pour réveiller la machine (voir [le déploiement](DEPLOY_FLY.md#réveil-quotidien-pour-les-rappels)). Les deux destinataires attendent le consentement du client, donné depuis le numéro exact de l’accord par `ACCEPTER WT-référence`. Un accord sans numéro client ne peut pas déclencher ces rappels. STOP, refus, règlement et messages de refus encore en file sont vérifiés avant émission. Les rappels trop anciens ne sont pas rattrapés en masse. La déduplication repose sur les identifiants persistants et la transaction d’enregistrement ; les erreurs ambiguës restent à examiner manuellement.

La fenêtre de texte libre est limitée volontairement à 23 h depuis le dernier message entrant. Au-delà, renseigner `REMINDER_TEMPLATE_NAME=wuumtaaba_rappel_echeance_v1` et `REMINDER_TEMPLATE_LANGUAGE=fr` seulement après approbation Meta. Le modèle reçoit trois paramètres : référence, solde, date ISO. Sans modèle configuré, état `blocked_template`. `REMINDER_ENABLED=false` empêche aussi l’envoi des rappels déjà en file.

## Conservation et sauvegarde

`RETENTION_DAYS=0` conserve l’historique sans purge automatique. Les audios sont lisibles seulement depuis `/admin`, après authentification ; le bucket n’a pas d’URL publique et la clé Supabase reste côté serveur. Ne mettre ni copie de la base, ni audio, ni secret dans GitHub.

L’offre gratuite de Supabase donne 500 Mo de base, partagés avec bia-backend, et 1 Go de stockage de fichiers. Pour une copie de la base : `pg_dump --schema=wuumtaaba`. Supabase met en pause un projet gratuit resté une semaine sans activité ; le réveil quotidien du bot et bia-backend interrogent la base, sans qu’on ait encore vérifié que cela suffit.

## Copier le volume Fly.io vers Supabase

La source doit déjà être en **0.7** : le schéma inclut `payments`, `mobile_tokens` et `mobile_codes`. Le script refuse toute table ou colonne inconnue au lieu de perdre des données silencieusement. Les anciennes bases 0.5 doivent être migrées par la version SQLite 0.7 avant export.

Faire une répétition dans un schéma Postgres dédié, sans démarrer de worker dessus. La copie initiale exige une cible vide (hors réglage modèle initial). `--replace-snapshot` remplace explicitement les tables connues par le snapshot complet dans une transaction : modifications de soldes, états des files et suppressions de codes/tokens sont repris. Chaque valeur est relue et comparée avant commit ; une erreur conserve l'ancien snapshot. Ne jamais lancer ce remplacement sur un bot cible en service.

Pour la bascule finale :

1. Sauvegarder la base SQLite et les audios, et conserver l'image/volume Fly pour retour arrière.
2. Suspendre les écritures : arrêter le bot SQLite et le connecteur Mac, attendre la fin des traitements en cours, puis créer un export cohérent. Ne pas exporter avant cette suspension ; les changements ultérieurs ne sont pas capturés. Si l'export se fait via SSH sur la machine, mettre le bot en maintenance sans arrêter l'accès SSH. Les webhooks ne doivent pas recevoir de succès s'ils n'ont pas été enregistrés ; Meta doit pouvoir les retenter.
3. Garder le bot Postgres arrêté. Exporter le snapshot final et les médias, puis copier :

```sh
fly ssh console --app bot-wumtaaba -C "python -c \"import sqlite3; sqlite3.connect('/app/data/bot.sqlite3').backup(sqlite3.connect('/app/data/export.sqlite3'))\""
fly ssh console --app bot-wumtaaba -C "tar czf /app/data/media.tgz -C /app/data media"
fly ssh sftp get /app/data/export.sqlite3 backup/bot.sqlite3 --app bot-wumtaaba
fly ssh sftp get /app/data/media.tgz backup/media.tgz --app bot-wumtaaba
tar xzf backup/media.tgz -C backup
uv run python scripts/copy_volume_to_supabase.py backup/bot.sqlite3 backup/media --replace-snapshot
```

4. Vérifier les comptes exacts, les soldes et l'historique des versements, les états inbox/outbox/rappels, les sessions et les audios. Le script contrôle les données de chaque table, pas seulement les comptes. Une erreur audio interdit la mise en service ; corriger puis recommencer tant que la source et la cible restent suspendues.
5. Configurer les secrets et le réveil quotidien, puis déployer sur **une seule machine** (`fly deploy --ha=false`). Vérifier `fly machines list` : une seule machine doit exécuter le bot. Le bridge Mac en mémoire et la reprise globale des files ne prennent pas en charge plusieurs instances.
6. Contrôler `/healthz`, menu WhatsApp, paiement et lecture mobile, audio privé, arrêt à vide et réveil par webhook/rappel. Réactiver le connecteur Mac si souhaité ; son polling empêche l'arrêt à vide.
7. Conserver le volume et les sauvegardes jusqu'à validation de la bascule. Un retour SQLite après des écritures Postgres exige une réconciliation des nouvelles données ; redémarrer l'ancien volume seul perdrait ces écritures.

### Tests audio privés

`experiments` conserve les tests du backoffice : UUID, nom, chemin Storage privé,
MIME, taille, dates et derniers résultats JSON (trois étapes maximum). La table
appartient au schéma du bot, avec RLS activé sans politique publique. Les fichiers
sont sous `experiments/` dans le bucket privé existant ; aucun lien public ni clé
Supabase n’est envoyé au navigateur. La suppression admin cible la fiche et son
objet, sans toucher aux audios des conversations WhatsApp.
