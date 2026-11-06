# Déployer sur Fly.io

**État au 10 octobre 2026 (version 0.5, avant Supabase) :** le service `bot-wumtaaba` est déployé sur une machine à Paris (`cdg`), avec 512 Mo de mémoire et un volume de 1 Go. Le contrôle de santé répond HTTP 200. Le webhook Meta est vérifié, abonné au champ `messages`, et les messages entrants sont reçus avec une réponse HTTP 200. La recette WhatsApp sur PC valide le menu, un accord texte, sa correction et son enregistrement. Un vocal de deux secondes envoyé dans la conversation de test a été transcrit en français après consentement, et le résultat est revenu sur WhatsApp. Après déploiement et redémarrage lié au renouvellement du token, le contrôle de santé répond HTTP 200 et Mes accords restitue la référence WT-B189648F avec ses montants corrigés : la persistance a été vérifiée depuis WhatsApp sur PC.

## Configuration déployée

Après la bascule Supabase de la version 0.7, le fichier [fly.toml](../fly.toml) décrit une machine sans volume dans la région `cdg` (Paris), avec un CPU partagé et 512 Mo de mémoire. Les accords sont dans Postgres et les audios dans un bucket privé, chez Supabase ([la base](DATABASE.md)). L’URL du webhook reste `https://bot-wumtaaba.fly.dev/webhook`.

Conserver **une seule machine et un seul worker** : le connecteur Mac garde ses tâches en mémoire. Le contrôle de santé utilise `/healthz` ; il ne vérifie pas l’accès aux fournisseurs.

### Arrêt quand rien ne se passe

`min_machines_running = 0` : la machine ne tourne que lorsqu’il y a du travail, comme bia-backend. La différence tient à `auto_stop_machines = 'off'`. Le proxy de Fly.io arrête une machine quand il ne voit plus de requête ; or le bot répond au webhook Meta tout de suite et prépare la réponse ensuite, parfois pendant plusieurs minutes (BurkimbIA attend jusqu’à 330 s). Le proxy pourrait donc couper une réponse en cours.

C’est le bot qui s’arrête : quand l’inbox et l’outbox sont vides et qu’aucune requête n’est arrivée depuis `IDLE_SHUTDOWN_SECONDS` (300 s sur Fly.io, `/healthz` exclu), il journalise `idle_shutdown` et termine avec le code 0. La politique de redémarrage `on-failure` laisse alors la machine arrêtée ; la requête suivante (webhook Meta, `/admin`, connecteur Mac) la relance en quelques secondes. Tant que le connecteur Mac interroge le bot, la machine reste allumée.

### Réveil quotidien pour les rappels

Les rappels J−1 et J0 sont programmés à 9 h, heure de Ouagadougou (UTC). Une tâche `pg_cron` de Supabase réveille la machine chaque jour à 9 h ; au démarrage, le worker met en file les rappels dus et les envoie. À exécuter une fois dans l’éditeur SQL du projet :

```sql
create extension if not exists pg_cron;
create extension if not exists pg_net;
select cron.schedule('wuumtaaba-reveil', '0 9 * * *',
  $$select net.http_get('https://bot-wumtaaba.fly.dev/healthz')$$);
```

Pas de cron GitHub Actions : le quota de 2 000 minutes par mois est partagé par tous les dépôts.

## Charger les secrets et démarrer

Exécuter les commandes depuis la racine du projet, avec la CLI `fly` connectée au compte approprié. Compléter `.env` localement, puis vérifier les paramètres :

```sh
uv run python scripts/setup_secrets.py
uv run python scripts/check_config.py
```

Transmettre uniquement les variables nécessaires à Fly par l’entrée standard, sans mettre les secrets dans les arguments de commande. Ne pas remplacer ce pipeline par une commande affichant son contenu dans le terminal :

```sh
uv run python - <<'PY' | fly secrets import --app bot-wumtaaba --stage
from dotenv import dotenv_values

values = dotenv_values('.env')
keys = (
    'WHATSAPP_TOKEN', 'WHATSAPP_PHONE_NUMBER_ID', 'META_APP_SECRET',
    'WEBHOOK_VERIFY_TOKEN', 'GEMINI_API_KEY', 'ALLOWED_SENDERS',
    'DATABASE_URL', 'SUPABASE_API_KEY',
)
if any(not values.get(key) for key in keys):
    raise SystemExit('Configuration incomplète : lancer scripts/check_config.py.')
for key in keys:
    print(f'{key}={values[key]}')
PY
fly deploy --app bot-wumtaaba --ha=false
fly status --app bot-wumtaaba
curl --fail https://bot-wumtaaba.fly.dev/healthz
```

`--stage` prépare les secrets pour le prochain déploiement. `--ha=false` évite de créer une seconde machine. Le bot refuse de démarrer sans `DATABASE_URL`, `SUPABASE_URL` et `SUPABASE_API_KEY` : charger ces secrets avant de fusionner sur `main`, puisque la fusion déploie.

Après un contrôle de santé réussi, enregistrer l’URL du webhook et le token de vérification dans Meta, abonner le champ `messages`, puis suivre [la recette réelle WhatsApp](SETUP_META.md). Un service HTTP sain ne suffit pas à déclarer le bot opérationnel.

## Suivi

```sh
fly logs --app bot-wumtaaba
fly checks list --app bot-wumtaaba
```

Un arrêt pour inactivité apparaît dans les journaux sous la forme `idle_shutdown idle_seconds=300`, suivi de l’arrêt d’Uvicorn. Le volume `bot_data` de la version SQLite 0.7 n’est plus monté ; le supprimer seulement après [la copie vers Supabase](DATABASE.md#copier-le-volume-flyio-vers-supabase) et sa vérification.

## Renouvellement du token Meta

Le token utilisateur Meta de longue durée installé a été vérifié et expire le **9 décembre 2026 à 13:10:50 UTC**. Prévoir son renouvellement avant cette date, ou configurer un token d’utilisateur système avec les autorisations et accès requis pour un service de plus longue durée. Après mise à jour du secret Fly, contrôler la santé du serveur et effectuer un nouvel échange WhatsApp. Ne jamais publier la valeur du token.

## Une seule instance du bot

Déployer avec `fly deploy --ha=false --app bot-wumtaaba`, puis vérifier dans `fly machines list` qu'une seule machine exécute le bot. La suppression du volume ne rend pas le bridge Mac en mémoire ni la récupération des files compatibles avec plusieurs instances. La CI utilise aussi `--ha=false`.

La section `[processes]` impose `app = 'python -m bot_wumtaaba.server'`. Un déploiement rétablit ainsi la commande applicative après une pause de maintenance réalisée avec une commande de machine temporaire.
