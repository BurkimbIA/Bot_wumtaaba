# Wuumtaaba · le carnet d’accords sur WhatsApp

Une V1 simple : écrire ou dicter un accord en français, vérifier son résumé, puis l’enregistrer. Tout se passe dans une conversation WhatsApp.

**Statut au 10 octobre 2026 : V1 vérifiée par 66 tests automatisés.** Des appels réels à `gemini-3.8-flash` ont également validé une transcription française sur un WAV synthétisé localement et l’extraction des cinq champs d’un accord. Le service est déployé sur Fly.io et son contrôle de santé répond HTTP 200. Le webhook Meta est vérifié et abonné aux messages. Depuis WhatsApp sur PC, le menu, la saisie d’un accord, la correction du montant et l’enregistrement ont été validés. Un vocal WhatsApp a également été transcrit en français après consentement, puis la transcription a été renvoyée dans la conversation. Après redémarrage, le contrôle de santé répond HTTP 200 et Mes accords restitue le même accord avec ses montants corrigés.

## Le parcours

1. Envoyer `menu`, puis choisir **Nouvel accord**.
2. Écrire ou dicter : « J’ai vendu deux sacs de riz à Awa pour 30 000 francs CFA. Elle a payé 10 000 francs. Le reste est prévu le 20 octobre 2026. »
3. Le bot restitue la transcription du vocal et demande les informations manquantes.
4. Vérifier le résumé : client, produit, total, avance, reste et date. Corriger si besoin, puis confirmer.
5. Retrouver l’enregistrement dans **Mes accords**.

Le solde est calculé par le code. Un accord enregistré est une **note personnelle du commerçant**, sans confirmation du client. La V1 n’envoie pas de message au client et ne gère pas les paiements mobiles.

## Démarrer en local

Python 3.12 ou plus récent est nécessaire.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
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
| `ALLOWED_SENDERS` | Numéros des testeurs au format international sans `+`, séparés par des virgules |

Créer ou sélectionner une clé dans [Google AI Studio](https://aistudio.google.com/api-keys). Pour saisir les trois secrets sans les afficher, lancer `python scripts/setup_secrets.py`. Le bot utilise `gemini-3.8-flash` pour la transcription et l’extraction des champs ; aucun compte OpenAI n’est nécessaire.

Les autres réglages et modèles sont dans `.env.example`. Générer le secret du webhook avec `python -c "import secrets; print(secrets.token_urlsafe(32))"`, puis le conserver uniquement dans `.env` et la configuration Meta. Ne jamais publier un token, une capture le contenant ou le fichier `.env`.

```sh
python scripts/check_config.py
uvicorn bot_wumtaaba.main:app --host 127.0.0.1 --port 8000
```

`GET /healthz` vérifie que le service HTTP répond ; il ne garantit pas la validité des tokens ni la disponibilité des fournisseurs. Le serveur doit être lancé avec **un seul processus** : le worker et SQLite sont intégrés à cette V1.

Voir [la configuration Meta et le protocole de test réel](docs/SETUP_META.md).

## Exécuter dans Docker

```sh
docker compose up --build -d
docker compose logs -f bot
```

Compose expose le service sur `127.0.0.1:8000` et conserve SQLite dans le volume `bot_data`, monté dans `/app/data`. Placer un accès HTTPS devant ce port pour recevoir les webhooks. Aucun hébergement payant n’est provisionné par ces commandes. Ne pas utiliser `docker compose down -v` si les accords doivent être conservés.

Pour héberger la V1, suivre [le déploiement Fly.io](docs/DEPLOY_FLY.md) : une machine à Paris avec volume SQLite persistant.

## Vérifier le code

```sh
ruff check .
pytest -q
```

GitHub Actions exécute ces contrôles sur Python 3.12 sans identifiants de production et sans envoyer de messages WhatsApp.

## Organisation

```text
src/bot_wumtaaba/   API, intégrations et logique des accords
scripts/           Vérification locale de configuration sans exposer les clés
tests/             Tests automatisés isolés des fournisseurs
docs/              Installation et recette WhatsApp
```

FastAPI reçoit le webhook ; le worker traite les événements ; HTTPX communique avec Meta et Google Gemini ; SQLite conserve les états et accords. Le flux et les précautions d’exploitation sont décrits dans [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Limites de la V1

- Pilote fermé : seuls les numéros de `ALLOWED_SENDERS` sont traités. Avec le numéro de test Meta, le testeur doit aussi être vérifié dans l’interface Meta.
- Vocaux limités à 14 000 000 octets pour leur transmission intégrée à Gemini. Le bot demande un consentement avant de télécharger et transmettre chaque vocal.
- Français et montants entiers en F CFA. La transcription peut se tromper : lire le résumé avant de confirmer.
- Pas d’interface d’administration, d’encaissement, de signature électronique ou de relance planifiée.
- Les appels Meta/Google Gemini peuvent avoir un coût. Le token utilisateur Meta installé expire le **9 décembre 2026 à 13:10:50 UTC** : prévoir son renouvellement ou une configuration adaptée avec un token d’utilisateur système pour prolonger le service.
- **Conservation pilote : 30 jours par défaut**, puis suppression automatique des accords, états et messages sur ce serveur (`RETENTION_DAYS`). Prévoir une sauvegarde pour toute utilisation durable.
- Les textes et vocaux sont transmis aux fournisseurs nécessaires au traitement. Ne pas utiliser le pilote avec des données sensibles ; définir accès, durée de conservation et sauvegardes avant une ouverture plus large.
