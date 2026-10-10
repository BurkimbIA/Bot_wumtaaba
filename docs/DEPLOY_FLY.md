# Déployer sur Fly.io

**État au 10 octobre 2026 :** le service `bot-wumtaaba` est déployé sur une machine à Paris (`cdg`), avec 512 Mo de mémoire et un volume de 1 Go. Le contrôle de santé répond HTTP 200. Le webhook Meta est vérifié, abonné au champ `messages`, et les messages entrants sont reçus avec une réponse HTTP 200. La recette WhatsApp sur PC valide le menu, un accord texte, sa correction et son enregistrement. Un vocal de deux secondes envoyé dans la conversation de test a été transcrit en français après consentement, et le résultat est revenu sur WhatsApp. Après déploiement et redémarrage lié au renouvellement du token, le contrôle de santé répond HTTP 200 et Mes accords restitue la référence WT-B189648F avec ses montants corrigés : la persistance a été vérifiée depuis WhatsApp sur PC.

## Configuration déployée

Le fichier [fly.toml](../fly.toml) définit une machine dans la région `cdg` (Paris), avec un CPU partagé et 512 Mo de mémoire. Un volume `bot_data` de 1 Go est monté dans `/app/data` pour conserver SQLite. L’URL du webhook est `https://bot-wumtaaba.fly.dev/webhook`.

Conserver **une seule machine et un seul worker**. L’arrêt automatique est désactivé : le worker intégré doit pouvoir traiter la file durable même en l’absence de nouvelles requêtes HTTP. Le contrôle de santé utilise `/healthz` ; il ne vérifie pas l’accès aux fournisseurs.

L’entrée du conteneur prépare les permissions du point de montage, puis abandonne les droits root. Uvicorn et le worker s’exécutent avec l’utilisateur `bot`, UID 10001. Les journaux d’accès Uvicorn sont désactivés pour ne pas enregistrer le token présent dans la requête GET de vérification du webhook.

## Charger les secrets et démarrer

Exécuter les commandes depuis la racine du projet, avec l’environnement Python activé et la CLI `fly` connectée au compte approprié. Compléter `.env` localement, puis vérifier les paramètres :

```sh
python scripts/setup_secrets.py
python scripts/check_config.py
```

Transmettre uniquement les variables nécessaires à Fly par l’entrée standard, sans mettre les secrets dans les arguments de commande. Ne pas remplacer ce pipeline par une commande affichant son contenu dans le terminal :

```sh
python - <<'PY' | fly secrets import --app bot-wumtaaba --stage
from dotenv import dotenv_values

values = dotenv_values('.env')
keys = (
    'WHATSAPP_TOKEN', 'WHATSAPP_PHONE_NUMBER_ID', 'META_APP_SECRET',
    'WEBHOOK_VERIFY_TOKEN', 'GEMINI_API_KEY', 'ALLOWED_SENDERS',
)
if any(not values.get(key) for key in keys):
    raise SystemExit('Configuration incomplète : lancer scripts/check_config.py.')
for key in keys:
    print(f'{key}={values[key]}')
PY
fly deploy --app bot-wumtaaba --ha=false
fly status --app bot-wumtaaba
fly volumes list --app bot-wumtaaba
curl --fail https://bot-wumtaaba.fly.dev/healthz
```

`--stage` prépare les secrets pour le prochain déploiement. `--ha=false` évite de créer une seconde machine pour cette V1 à stockage local. Le montage déclaré dans `fly.toml` prévoit la création initiale du volume ; vérifier sa présence après déploiement. Les machines et volumes sont facturés selon le compte Fly.io.

Après un contrôle de santé réussi, enregistrer l’URL du webhook et le token de vérification dans Meta, abonner le champ `messages`, puis suivre [la recette réelle WhatsApp](SETUP_META.md). Un service HTTP sain ne suffit pas à déclarer le bot opérationnel.

## Suivi et persistance

```sh
fly logs --app bot-wumtaaba
fly checks list --app bot-wumtaaba
```

Les accords vivent sur le volume, pas dans l’image. Ne pas supprimer le volume lors d’une mise à jour et ne pas augmenter le nombre de machines sans revoir le stockage et le worker. Prévoir une sauvegarde SQLite cohérente avant toute utilisation durable ; `RETENTION_DAYS=0` désactive la purge automatique. Sauvegarder également `/app/data/media`. La migration V2 produit une copie SQLite préalable `.pre-v2.sqlite3` ; elle ne remplace pas une sauvegarde régulière externe.

## Renouvellement du token Meta

Le token utilisateur Meta de longue durée installé a été vérifié et expire le **9 décembre 2026 à 13:10:50 UTC**. Prévoir son renouvellement avant cette date, ou configurer un token d’utilisateur système avec les autorisations et accès requis pour un service de plus longue durée. Après mise à jour du secret Fly, contrôler la santé du serveur et effectuer un nouvel échange WhatsApp. Ne jamais publier la valeur du token.
