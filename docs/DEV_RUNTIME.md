# Backoffice local isolé

Le serveur de développement sert exactement les assets et les routes admin du
paquet du bot. Il ne démarre ni worker WhatsApp, ni webhook, ni récupération ou
purge des files de production.

La base doit être sur `127.0.0.1`, `localhost` ou `::1`, se nommer
`wumtaaba_dev`, et ne contenir aucune option de connexion dans l’URL. Une autre
cible est refusée avant la construction du Store. Préparer d’abord la base et son
schéma avec les commandes de développement du dépôt.

`just dev` prépare la base Docker persistante, crée un `.env.dev` privé (ignoré
par Git) s’il manque, applique les migrations locales et démarre le serveur avec
rechargement automatique. `just dev-db` prépare uniquement la base.

La configuration générée contient notamment :

```dotenv
DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:55432/wumtaaba_dev
DATABASE_SCHEMA=wuumtaaba
ADMIN_PASSWORD=UN_CODE_LOCAL_ALEATOIRE_DE_24_CARACTERES_MINIMUM
ADMIN_COOKIE_SECURE=false
DEV_MAC_API_KEY=
DEV_DATABASE_PASSWORD=LE_MEME_MOT_DE_PASSE_QUE_DATABASE_URL
```

Puis lancer depuis la racine :

```sh
uv run python -m bot_wumtaaba.dev --host 127.0.0.1 --port 8000 --reload
```

Ouvrir `http://127.0.0.1:8000/admin`. Le code de connexion est celui de
`.env.dev`, indépendant de la production. Les audios ajoutés en dev sont stockés
uniquement dans `data/dev/media`, jamais dans Tigris. Les tâches d’annotation
peuvent utiliser le même stockage local avec leur chemin physique distinct.

Le menu propose aussi `/admin/simulator`, disponible en dev et en production sous
session administrateur. Ce simulateur
réutilise le moteur de conversation actuel avec une base en mémoire et des médias
temporaires distincts du backoffice. Il permet le texte, les vocaux et la sélection
des modèles Mac. Les conversations simulées ne deviennent pas des conversations
WhatsApp réelles ; réinitialiser la simulation efface sa session de test.
Les essais ne sont pas persistants : ils disparaissent au redémarrage du serveur,
y compris lorsque Fly.io s’arrête pour inactivité. Le simulateur ne maintient pas
le serveur éveillé. En production, les appels Mac utilisent le connecteur existant.

Le routage Mac des essais appelle l’API locale sur `127.0.0.1:8765`, sans toucher
au connecteur ou à la présence Supabase de production. `DEV_MAC_API_KEY` est la
clé de cette API si elle est protégée. Les paramètres Gemini/RunPod facultatifs
peuvent être configurés dans `.env.dev` pour lancer explicitement des inférences
distantes ; sans ces clés, les fonctions concernées restent indisponibles.

Ne pas mettre une URL de base de production dans `.env.dev`. Le processus dev
n’applique aucune migration distante et ne publie aucune page en production.
