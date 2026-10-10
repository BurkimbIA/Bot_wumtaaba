# Connecter Meta et tester depuis WhatsApp

## Préparer les identifiants

Dans l’application Meta liée au bot, retrouver le numéro WhatsApp et son identifiant, le token d’accès et le secret de l’application. Les reporter dans `.env`, jamais dans GitHub. Un identifiant déjà présent dans `.env.example` est à vérifier contre l’application actuellement utilisée.

Le token utilisateur Meta de longue durée installé expire le **9 décembre 2026 à 13:10:50 UTC**. Planifier son renouvellement ou la configuration d’un token d’utilisateur système adapté à une exploitation prolongée.

Ajouter son téléphone comme destinataire de test dans Meta, puis le vérifier. Ajouter le même numéro à `ALLOWED_SENDERS` au format international sans espaces et sans `+`. Ces deux autorisations sont distinctes.

Dans [Google AI Studio](https://aistudio.google.com/api-keys), créer une clé API pour le projet Google utilisé, puis la conserver dans `GEMINI_API_KEY`. Saisir les clés localement avec `python scripts/setup_secrets.py`, qui masque la saisie. Les modèles de transcription et d’extraction sont configurables et utilisent `gemini-3.8-flash` par défaut. Le script `check_config.py` ne contrôle pas la facturation, les quotas ou l’accès effectif aux modèles.

## Enregistrer le webhook

1. Démarrer le serveur et vérifier `http://127.0.0.1:8000/healthz`.
2. Rendre le port 8000 accessible sur une URL HTTPS avec un domaine ou un tunnel de développement. Garder le serveur et le tunnel ouverts pendant les tests.
3. Dans la configuration des webhooks WhatsApp de Meta, renseigner `https://VOTRE-DOMAINE/webhook` comme URL de rappel.
4. Copier la valeur locale `WEBHOOK_VERIFY_TOKEN` dans le champ de vérification de Meta, puis vérifier et enregistrer.
5. Abonner le webhook au champ **messages** pour recevoir les messages et statuts WhatsApp.
6. Vérifier que `META_APP_SECRET` est celui de cette même application : le POST signé est contrôlé avant tout traitement.

Le token de vérification est un secret choisi par le développeur. Il est différent du token d’accès WhatsApp et du secret d’application. Un événement synthétique Meta peut utiliser un faux expéditeur : il ne contournera pas la liste des testeurs autorisés.

## Recette réelle depuis WhatsApp sur PC

Ouvrir la conversation avec le numéro de test depuis WhatsApp Desktop ou WhatsApp Web connecté au compte du testeur. Utiliser uniquement des données fictives.

| Étape | Action | Résultat attendu |
| --- | --- | --- |
| Menu | Envoyer `menu` | Boutons Nouvel accord, Mes accords, Aide |
| Texte | Choisir Nouvel accord, envoyer l’exemple du README | Résumé avec total 30 000, avance 10 000, reste 20 000 F CFA |
| Correction | Demander de changer l’avance à 12 000 | Résumé recalculé avec reste 18 000 F CFA avant confirmation |
| Enregistrement | Confirmer le résumé correct | Confirmation puis accord visible dans Mes accords |
| Vocal | Démarrer un nouvel accord et dicter une vente fictive en français | Demande de consentement ; après acceptation, transcription renvoyée dans WhatsApp, puis résumé ou question ciblée |
| Information manquante | Omettre la date | Question sur la date, pas d’accord incomplet enregistré |
| Annulation | Annuler un brouillon | Aucun nouvel accord sauvegardé |
| Isolement | Utiliser deux testeurs autorisés | Chacun consulte uniquement ses propres accords |
| Redémarrage | Relancer le serveur puis consulter Mes accords | Accord conservé dans SQLite |

Dans WhatsApp Web, autoriser le microphone pour le site si nécessaire. Si le vocal a été envoyé mais aucune transcription ne revient, vérifier d’abord les journaux serveur, la réception du webhook, puis l’accès aux API. Ne pas copier de tokens dans un ticket ou une capture.

### Tests réels Gemini réalisés le 10 octobre 2026

Deux appels indépendants au fournisseur ont été validés avec `gemini-3.8-flash` :

- **Transcription** : un WAV français synthétisé localement a été envoyé à Gemini. Le texte complet a été correctement restitué : « Ceci est un test. J’ai vendu deux sacs de riz à Awa pour 30 000 francs CFA. Elle a payé 10 000 francs. Le reste est prévu le 20 octobre 2026. »
- **Extraction** : un autre exemple d’accord a produit les cinq champs attendus. Pour un total de 25 000 F CFA et une avance de 10 000 F CFA, le code a calculé un solde de 15 000 F CFA.

Ces appels valident l’intégration Gemini ; l’audio de transcription ne provenait pas de WhatsApp. Les 66 tests automatisés complètent ces vérifications avec des fournisseurs simulés. Le webhook Meta est désormais vérifié et abonné aux messages ; les requêtes entrantes obtiennent HTTP 200. Le serveur Fly.io répond également HTTP 200 sur `/healthz`. Les résultats de la recette WhatsApp sont détaillés ci-dessous.

### Journal de recette WhatsApp

Vérifications réalisées dans WhatsApp sur PC ; les cas en attente ne sont pas déclarés réussis sur la seule base des tests simulés :

| Cas | Date | Résultat constaté |
| --- | --- | --- |
| Réponse au menu | 2026-10-10 | Validé : menu reçu dans WhatsApp sur PC |
| Accord texte confirmé | 2026-10-10 | Validé : Awa, deux sacs de riz, total 30 000 F CFA, avance 10 000 F CFA, échéance 2026-10-20 ; solde affiché 20 000 F CFA. Après correction et clic sur Enregistrer, référence WT-B189648F créée |
| Vocal français transcrit et affiché | 2026-10-10 | Validé : vocal de deux secondes transmis dans la conversation de test, consentement accepté, transcription française correcte renvoyée sur WhatsApp. Contenu personnel non reproduit |
| Correction avant validation | 2026-10-10 | Validé : avance corrigée à 12 000 F CFA, solde recalculé à 18 000 F CFA |
| Persistance après redémarrage | 2026-10-10 | Validé depuis WhatsApp sur PC après déploiement et redémarrage : Mes accords restitue WT-B189648F, Awa, deux sacs de riz, total 30 000, avance 12 000, reste 18 000 F CFA, échéance 2026-10-20 ; santé serveur HTTP 200 |

## Diagnostic rapide

- **Échec de vérification GET** : comparer le token de vérification et l’URL `/webhook`, tester l’accès HTTPS public.
- **POST refusé** : contrôler le secret d’application et l’en-tête signé envoyé par Meta. Ne pas désactiver la vérification de signature.
- **Aucune réaction** : contrôler l’abonnement `messages`, le numéro expéditeur autorisé, le serveur et le tunnel.
- **Envoi refusé** : contrôler le token, l’identifiant du numéro et le destinataire de test. Reprendre la conversation en envoyant un message depuis le téléphone pour tester une réponse au message entrant.
- **Transcription refusée** : contrôler l’accès Google Gemini au modèle configuré, les quotas, le type et la taille de l’audio (14 000 000 octets maximum). Réessayer avec un vocal court.
- **HTTP sain mais bot silencieux** : `/healthz` ne teste pas les fournisseurs ; examiner les erreurs du worker sans publier les données des utilisateurs.

La V1 répond aux messages entrants. Elle ne fournit pas de campagne sortante ni de relance automatique. Le passage au vrai numéro professionnel, les autorisations Meta, la facturation et un hébergement permanent constituent une étape distincte.

## Recette SQLite V2 — 10 octobre 2026

- Suite automatisée : 134 tests réussis ; Ruff réussi.
- Fly.io : version 0.2.0, contrôle de santé HTTP 200, migration 2 et intégrité SQLite `ok`, sauvegarde préalable présente.
- WhatsApp PC : `calendrier` restitue l’échéance de l’accord conservé après migration.
- Nouveau vocal : consentement affichant la conservation privée, transcription revenue dans WhatsApp, fichier de 7 302 octets présent sur le volume en permissions 0600 et transcription reliée en base.
- Accusés Meta `sent`, `delivered`, `read` reçus et archivés.
- Rappels de l’accord historique en attente de consentement, aucun envoi indu. Les scénarios à deux destinataires sont testés automatiquement, pas encore validés avec deux téléphones réels.
- Modèle `wuumtaaba_rappel_echeance_v1` soumis à Meta en français, statut `PENDING` au dernier contrôle. Son activation hors fenêtre reste à effectuer après approbation.
