# BurkimbIA dans le bot WhatsApp

Le portail conserve deux parcours séparés. `menu`, `bonjour` ou `salut` affiche les boutons **BurkimbIA** et **Wuumtaaba**. Choisir Wuumtaaba ouvre le carnet d’accords existant. Choisir BurkimbIA ouvre une liste de quatre services.

| Service | Entrée | Sortie |
| --- | --- | --- |
| Traduction | Texte français ou mooré ; choisir le sens | Traduction texte français ↔ mooré |
| Transcription | Vocal en mooré, après consentement pour ce fichier | Transcription dans WhatsApp |
| Synthèse vocale | Texte en mooré | Fichier audio dans WhatsApp |
| Yaaba | Entrée de menu réservée à l’assistant | Message d’indisponibilité tant que son API de conversation n’est pas connectée et vérifiée |

Pour utiliser les services sans ouvrir les boutons interactifs, envoyer `/traduction`, `/transcription`, `/voix` ou `/yaaba`. La traduction propose aussi `/fr-mo` et `/mo-fr` pour choisir le sens. Ces commandes explicites permettent de changer de service pendant une saisie. Les mots simples `traduction`, `transcription`, `synthèse vocale` et `yaaba` sont également reconnus quand aucun service n’est actif ; dans un service actif, ils restent du contenu à traiter.

Dans BurkimbIA, `services` revient à la liste des services ; `menu` revient au choix des deux espaces. Ces commandes abandonnent la saisie et toute demande de consentement audio en attente. Changer d’espace abandonne aussi un brouillon Wuumtaaba non enregistré : un ancien bouton de confirmation ne peut pas le sauvegarder ensuite. Les accords déjà enregistrés restent conservés.

Les commandes `STOP`, `rappels oui`, `ACCEPTER WT-référence` et `REFUSER WT-référence` gardent leur rôle pour les rappels, même depuis BurkimbIA. Le client d’un accord ne reçoit pas les droits du commerçant en ouvrant Wuumtaaba.

## Fournisseurs et confidentialité

Le parcours **Wuumtaaba** conserve Google Gemini pour l’extraction des accords et la transcription française. Le parcours **BurkimbIA** utilise ses propres API. Une indisponibilité BurkimbIA produit un message d’erreur ; le bot ne bascule pas silencieusement vers Gemini.

Chaque vocal destiné à la transcription BurkimbIA demande un accord explicite avant téléchargement depuis Meta, transmission à BurkimbIA et conservation privée. Les commandes `/accepter-vocal` et `/refuser-vocal` répondent à la demande concernant le dernier vocal reçu, uniquement dans le service Transcription avec une demande en attente. Sans demande valide, elles ne déclenchent aucun traitement. Refuser ne déclenche aucun téléchargement ni appel de transcription. L’audio et la transcription sont associés au message reçu dans SQLite et dans le répertoire privé des médias.

Les fichiers vocaux générés sont également archivés dans le répertoire privé, avec le texte source dans les métadonnées. Le bot téléverse l’audio auprès de Meta et envoie son identifiant de média ; il n’expose pas de lien public vers l’archive locale. Les métadonnées internes d’archivage sont retirées avant l’envoi à Meta. L’accès aux médias du back-office reste soumis à l’authentification administrateur.

Les règles de conservation de l’application restent applicables. Avec `RETENTION_DAYS=0`, aucune purge automatique n’est effectuée. Sauvegarder le répertoire des médias avec SQLite et surveiller l’espace disponible.

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

Pour la recette réelle, vérifier successivement le choix des espaces, une traduction dans chaque sens, un vocal mooré consenti et transcrit, puis une synthèse vocale reçue et lisible. Vérifier aussi `services`, `menu`, un refus audio et un accord Wuumtaaba. Consigner séparément les résultats des appels API et ceux observés dans WhatsApp ; ne déclarer Yaaba opérationnel qu’après test de son API propre.


## Résultats des appels API réels — 16 octobre 2026

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

Dans `/admin`, ouvrir **Modèles IA**, puis sélectionner **Mac local** ou **Cloud BurkimbIA (RunPod)**. Le choix est enregistré en SQLite et s’applique aux prochaines demandes BurkimbIA. Une demande déjà commencée termine sur son fournisseur initial. Les accords Wuumtaaba gardent Gemini.

Le mode Cloud conserve les endpoints de la plateforme BurkimbIA ; selon le modèle, le calcul utilise Fly/CPU ou RunPod. Yaaba reste indisponible dans ce mode tant que son endpoint cloud n’est pas réparé. Le mode Mac fournit traduction, transcription, synthèse et Yaaba/Tengsoaba.

Le connecteur du Mac interroge le bot Fly par HTTPS, authentifié avec `MAC_BRIDGE_API_KEY` (au moins 32 caractères aléatoires). Il récupère une demande, appelle l’API native sur `127.0.0.1:8765`, puis renvoie le résultat. Aucun port entrant public n’est nécessaire. Les tâches et audios en transit restent en mémoire dans la passerelle ; l’historique normal du bot garde sa politique de conservation existante. Le choix de fournisseur et ses changements sont journalisés.

Sur le Mac, lancer `mac/start.sh`, puis `mac/start-bridge.sh` dans le dépôt `inference_endpoints`. Le second lit `mac/.bridge-config.json` privé (permissions 600, ignoré par Git), contenant `MAC_BRIDGE_API_KEY`. Les deux processus doivent rester actifs, avec le Mac éveillé et connecté. Après 20 secondes sans contact, le back-office indique le Mac hors ligne. Il n’existe aucun basculement automatique vers le cloud payant.

Le conteneur Docker `mac-gateway-1` est seulement un proxy local du port 8766 vers l’API native 8765. Les modèles tournent dans Python sur macOS pour utiliser le GPU Apple Metal ; ils ne tournent pas dans ce conteneur. Le connecteur Fly appelle directement 8765 : Docker n’est pas nécessaire pour cette liaison.

### Recette du sélecteur — 16 octobre 2026

240 tests du bot réussis (dont routage des quatre services, authentification, absence de repli cloud, changement pendant une requête). API administrateur en production : connexion, état Mac en ligne, passage Mac puis Cloud, HTTP 200. Interface vérifiée visuellement sur une instance locale avec données de démonstration.

Échange WhatsApp réel en mode Mac : `/yaaba`, puis « Présente-toi en une phrase. » ; résultat produit par l’API locale et reçu/lu dans WhatsApp, environ 129 secondes de bout en bout. Le texte était répétitif et incohérent : transport validé, qualité du modèle non validée. La limite de sortie du bot est de 256 tokens, contre 64 pour le microbenchmark local antérieur ; ne pas présenter le temps du microbenchmark comme une garantie en conversation.
