# Modèles BurkimbIA dans Wuumtaaba

Le bot ouvre directement le carnet Wuumtaaba. Le choix des espaces et les services autonomes ont été retirés. Les modèles BurkimbIA servent à transcrire les vocaux mooré, traduire les descriptions et lire les résumés des accords. Voir [le parcours français/mooré](AGREEMENT_LANGUAGES.md).

## Fournisseurs et confidentialité

Le parcours **Wuumtaaba** conserve Google Gemini pour l’extraction des accords et la transcription française. Les étapes mooré utilisent les API BurkimbIA. Une indisponibilité BurkimbIA produit un message d’erreur ; le bot ne bascule pas silencieusement vers Gemini.

Chaque vocal destiné à la transcription BurkimbIA demande un accord explicite avant téléchargement depuis Meta, transmission à BurkimbIA et conservation privée. Les boutons Accepter et Refuser répondent à la demande liée au vocal courant dans le carnet. Un bouton expiré ne déclenche aucun traitement. Refuser ne déclenche aucun téléchargement ni appel de transcription. L’audio et la transcription sont associés au message reçu, dans la base et dans le bucket privé des médias.

Les fichiers vocaux générés sont également archivés dans le bucket privé, avec le texte source dans les métadonnées. Le bot téléverse l’audio auprès de Meta et envoie son identifiant de média ; il n’expose pas de lien public vers l’archive. Les métadonnées internes d’archivage sont retirées avant l’envoi à Meta. L’accès aux médias du back-office reste soumis à l’authentification administrateur.

Les règles de conservation de l’application restent applicables. Avec `RETENTION_DAYS=0`, aucune purge automatique n’est effectuée. Surveiller l’espace du bucket : 1 Go sur l’offre gratuite de Supabase.

## Configuration serveur

| Variable | Usage |
| --- | --- |
| `BURKIMBIA_API_URL` | Base HTTPS de l’API, par défaut `https://bia-backend.fly.dev/api/v1` |
| `BURKIMBIA_API_KEY` | Clé privée, envoyée dans l’en-tête `X-API-Key` |
| `BURKIMBIA_TRANSLATION_MODEL` | Modèle de traduction, par défaut `bia-translation-v1-fast` (CPU) |
| `BURKIMBIA_TIMEOUT_SECONDS` | Délai maximal d’un appel, par défaut 330 secondes |
| `BURKIMBIA_YAABA_ENABLED` | Faux par défaut ; ne pas activer avant raccordement et vérification d’une vraie API Yaaba |

Conserver les clés uniquement dans `.env` ignoré par Git et dans les secrets d’hébergement. Ne pas les mettre dans le frontend, les journaux ou les exemples de commande publiés.

Les routes utilisées par le client, relatives à `BURKIMBIA_API_URL`, sont :

- `POST /translate` : `text`, `src_lang`, `tgt_lang`, `model` ; résultat texte dans `output`.
- `POST /transcribe` : `audio_base64`, `language=mos`, `model=bia-transcription-v1`, `return_timestamps=false` ; résultat dans `text`.
- `POST /tts` : `text`, `model=bia-tts-v1`, `voice_cloning=false` ; WAV encodé en base64 dans `wav`, converti pour WhatsApp.

Le client limite traduction et synthèse à 512 caractères. Pour la transcription, utiliser des vocaux courts, de 60 secondes maximum et de moins de 7 Mio. Le WAV de synthèse est converti en audio compatible WhatsApp ; `ffmpeg` doit être installé dans l’environnement d’exécution.

La route de compatibilité OpenAI de traduction ne prouve pas la présence de Yaaba : ne pas la présenter comme un modèle de conversation généraliste.

## Accès et vérification

Cette intégration ne modifie pas le filtre d’accès : les commerçants de `ALLOWED_SENDERS` et les clients connus sont traités. Elle ne rend pas le bot accessible à tous les numéros.

Les tests isolés vérifient navigation, directions de traduction, consentement avant téléchargement, archivage, envoi de média, erreurs fournisseur, expiration des boutons et maintien des autorisations Wuumtaaba. Ils n’établissent pas à eux seuls qu’un endpoint de production ou un échange WhatsApp réel fonctionne.

Pour la recette réelle, vérifier l’entrée directe dans le carnet, un accord mooré avec vocal consenti, transcription, traduction, résumé et lecture audio, puis une correction et une confirmation. Vérifier aussi le refus audio et l’annulation. Consigner séparément les appels API et les échanges WhatsApp réellement observés.


## Historique : résultats des appels API réels — 16 octobre 2026

Les vérifications suivantes ont été exécutées depuis l’environnement local contre les API réelles. Une clé dédiée au bot est utilisée ; aucune valeur secrète n’est conservée dans cette documentation.

| Appel | Résultat observé | Portée de la vérification |
| --- | --- | --- |
| Traduction `bia-translation-v1-fast` | HTTP 200, environ 1,8 s ; « Bonjour comment vas-tu » → « Yibeoogo, laafi laafi??. » | Transport et réponse du modèle vérifiés ; qualité linguistique non validée |
| Traduction V2 | HTTP 502 ; service amont en pause | Non opérationnelle lors de ce test ; le modèle CPU V1-fast est retenu par défaut |
| Synthèse « Laafi bala » | HTTP 200, 26,49 s ; WAV à 16 kHz, durée 1,68 s | Génération audio vérifiée par API |
| Transcription du WAV | HTTP 200, 29,55 s ; « laafɩ bala. » | Chaîne synthèse puis transcription vérifiée par API, sans équivaloir à un test de vocal spontané |
| Yaaba / Tengsoaba | HTTP 409 initialement ; essai borné après activation expiré à 120 s | Aucun résultat ; endpoint remis en pause et workersMax=0 revérifié |

Dans WhatsApp sur PC, le menu d’entrée BurkimbIA / Wuumtaaba et la réponse de traduction française vers mooré ont été observés sur le numéro de production. Le traitement d’un vocal consenti et la livraison de l’audio généré restent à vérifier.

Le diagnostic RunPod de Tengsoaba a trouvé un template référencé renvoyant HTTP 404, absent de la liste accessible. Cela suggère un template supprimé ou inaccessible, sans preuve suffisante pour affirmer la cause exacte du délai. Aucun worker actif après remise en pause. Une requête reste en file ; aucune purge effectuée. Réparer et examiner la configuration avant une nouvelle activation. Le test borné à un worker et environ 125 secondes était largement sous le budget autorisé de 2 €, sans facture définitive vérifiée.

## Choisir les modèles Mac ou Cloud

Dans `/admin`, ouvrir **Modèles IA**, puis sélectionner **Mac local** ou **Cloud BurkimbIA (RunPod)**. Le choix est enregistré dans la base et s’applique aux prochaines demandes BurkimbIA. Une demande déjà commencée termine sur son fournisseur initial. Les accords Wuumtaaba gardent Gemini.

Le mode Cloud conserve les endpoints de la plateforme BurkimbIA ; selon le modèle, le calcul utilise Fly/CPU ou RunPod. Yaaba reste indisponible dans ce mode tant que son endpoint cloud n’est pas réparé. Le mode Mac fournit traduction, transcription, synthèse et Yaaba/Tengsoaba.

Le connecteur du Mac interroge le bot Fly par HTTPS, authentifié avec `MAC_BRIDGE_API_KEY` (au moins 32 caractères aléatoires). Il récupère une demande, appelle l’API native sur `127.0.0.1:8765`, puis renvoie le résultat. Aucun port entrant public n’est nécessaire. Les tâches et audios en transit restent en mémoire dans la passerelle ; l’historique normal du bot garde sa politique de conservation existante. Le choix de fournisseur et ses changements sont journalisés.

Sur le Mac, lancer `mac/start.sh`, puis `mac/start-bridge.sh` dans le dépôt `inference_endpoints`. Le second lit `mac/.bridge-config.json` privé (permissions 600, ignoré par Git), contenant `MAC_BRIDGE_API_KEY`. Les deux processus doivent rester actifs, avec le Mac éveillé et connecté. Après 20 secondes sans contact, le back-office indique le Mac hors ligne. Il n’existe aucun basculement automatique vers le cloud payant.

Un connecteur qui interroge Fly toutes les 2 s, avec un signe de vie toutes les 5 s, empêche la machine Fly de s’arrêter : chaque appel remet son compteur d’inactivité à zéro, et la réveille si elle dort. Le connecteur peut à la place attendre dans la table `mac_link` de Supabase, toujours disponible :

- il écrit `last_seen` toutes les 5 s, ce qui suffit au bot pour le voir en ligne ;
- il lit `wanted_until` toutes les 2 s et n’interroge Fly que si cette heure est dans le futur, c’est-à-dire quand une tâche l’attend.

Le bot écrit `wanted_until` au dépôt d’une tâche (maintenant plus le délai, 330 s) et le remet à maintenant quand plus aucune tâche n’attend. Les deux côtés prennent l’heure de Postgres, pas celle du Mac. Un connecteur qui interroge encore Fly reste accepté et garde la machine allumée.

Le connecteur se connecte avec un rôle Postgres à lui, qui ne lit que `mac_link` et n’écrit que `last_seen`, avec au plus trois connexions et cinq secondes par requête. Il existe en production depuis le 6 novembre 2026 ; pour le recréer, dans l’éditeur SQL du projet, avec un mot de passe aléatoire gardé hors de Git :

```sql
CREATE ROLE wuumtaaba_mac LOGIN CONNECTION LIMIT 3 PASSWORD '…';
ALTER ROLE wuumtaaba_mac SET statement_timeout = '5s';
GRANT USAGE ON SCHEMA wuumtaaba TO wuumtaaba_mac;
GRANT SELECT, UPDATE (last_seen) ON wuumtaaba.mac_link TO wuumtaaba_mac;
```

La sécurité par ligne (RLS) est active sur toutes les tables du schéma : la règle `mac_connector`, créée par le bot au démarrage, laisse ce rôle accéder à la ligne de `mac_link`. Sans elle, ses requêtes ne verraient aucune ligne et le bot le croirait hors ligne.

Il passe par le pooler en mode session, `postgresql://wuumtaaba_mac.iagyxtstnbgstdyppgnp:…@aws-1-eu-west-3.pooler.supabase.com:5432/postgres?sslmode=require`, avec deux requêtes :

```sql
UPDATE wuumtaaba.mac_link SET last_seen = extract(epoch FROM now()) WHERE id = 1;
SELECT wanted_until > extract(epoch FROM now()) FROM wuumtaaba.mac_link WHERE id = 1;
```

Côté Mac, c’est le mode attente du connecteur (BurkimbIA/inference_endpoints#56), activé par `MAC_DATABASE_URL` dans son fichier de configuration privé. Déployer ce bot d’abord : un connecteur en mode attente paraît hors ligne pour un bot qui ne lit pas `mac_link`.

Le conteneur Docker `mac-gateway-1` est seulement un proxy local du port 8766 vers l’API native 8765. Les modèles tournent dans Python sur macOS pour utiliser le GPU Apple Metal ; ils ne tournent pas dans ce conteneur. Le connecteur Fly appelle directement 8765 : Docker n’est pas nécessaire pour cette liaison.

### Recette du sélecteur — 16 octobre 2026

240 tests du bot réussis (dont routage des quatre services, authentification, absence de repli cloud, changement pendant une requête). API administrateur en production : connexion, état Mac en ligne, passage Mac puis Cloud, HTTP 200. Interface vérifiée visuellement sur une instance locale avec données de démonstration.

Échange WhatsApp réel en mode Mac : `/yaaba`, puis « Présente-toi en une phrase. » ; résultat produit par l’API locale et reçu/lu dans WhatsApp, environ 129 secondes de bout en bout. Le texte était répétitif et incohérent : transport validé, qualité du modèle non validée. La limite de sortie du bot est de 256 tokens, contre 64 pour le microbenchmark local antérieur ; ne pas présenter le temps du microbenchmark comme une garantie en conversation.
