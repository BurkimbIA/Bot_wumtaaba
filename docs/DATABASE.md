# Base SQLite et calendrier

Une seule machine Fly.io, un seul worker, SQLite en mode WAL et un volume persistant. La migration versionnée s’exécute en transaction et sauvegarde la base avant transformation. Les accords historiques restent lisibles.

| Tables | Données |
| --- | --- |
| contacts, conversations | Numéro, réglage des rappels, dernière réception |
| messages, inbox, outbox | Contenu reçu/envoyé, traitement durable, identifiants Meta |
| media_assets | Média Meta, chemin privé, empreinte, transcription et erreurs |
| delivery_events | Accusés envoyé/livré/lu/échec, dédupliqués |
| agreements | Accord confirmé, propriétaire, client, montants, échéance |
| calendar_events | Échéance à 9 h Africa/Ouagadougou, état de règlement |
| customer_consents | Décision du client, référence, message source et date |
| reminders | Destinataire, rôle, date J−1/J0, état et sortie associée |
| audit_events | Événements de traitement et décisions |

Les messages non autorisés ou hors limites sont archivés comme ignorés. Les contenus des types pris en charge sont enregistrés ; seuls les fichiers audio consentis sont téléchargés. Les URL temporaires Meta ne sont pas archivées. Les fichiers des anciens vocaux traités avant cette version ne sont pas récupérés rétroactivement.

## Envoi des rappels

Toutes les 30 secondes, le worker examine les échéances. Les deux destinataires attendent le consentement du client, donné depuis le numéro exact de l’accord par `ACCEPTER WT-référence`. Un accord sans numéro client ne peut pas déclencher ces rappels. STOP, refus, règlement et messages de refus encore en file sont vérifiés avant émission. Les rappels trop anciens ne sont pas rattrapés en masse. La déduplication repose sur les identifiants persistants et la transaction d’enregistrement ; les erreurs ambiguës restent à examiner manuellement.

La fenêtre de texte libre est limitée volontairement à 23 h depuis le dernier message entrant. Au-delà, renseigner `REMINDER_TEMPLATE_NAME=wuumtaaba_rappel_echeance_v1` et `REMINDER_TEMPLATE_LANGUAGE=fr` seulement après approbation Meta. Le modèle reçoit trois paramètres : référence, solde, date ISO. Sans modèle configuré, état `blocked_template`. `REMINDER_ENABLED=false` empêche aussi l’envoi des rappels déjà en file.

## Conservation et sauvegarde

`RETENTION_DAYS=0` conserve l’historique sans purge automatique. `MEDIA_DIRECTORY=/app/data/media` doit être sur le volume persistant en production. Les audios ne sont exposés par aucune route HTTP. Ne pas mettre le volume, les copies SQLite ou les secrets dans GitHub.

Sauvegarder la base avec l’API `sqlite3.Connection.backup`, et copier les fichiers audio dans une sauvegarde privée. Le fichier de migration `.pre-v2.sqlite3` protège cette migration uniquement. Le volume de 1 Go est limité : contrôler régulièrement son occupation, particulièrement avec les vocaux. Une interface d’administration, une synchronisation de calendrier externe et une sauvegarde distante automatique ne sont pas incluses.
