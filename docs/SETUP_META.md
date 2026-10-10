# Connecter Meta et tester depuis WhatsApp

## Préparer les identifiants

Dans l’application Meta liée au bot, retrouver le numéro WhatsApp et son identifiant, le token d’accès et le secret de l’application. Les reporter dans `.env`, jamais dans GitHub. Un identifiant déjà présent dans `.env.example` est à vérifier contre l’application actuellement utilisée.

Ajouter son téléphone comme destinataire de test dans Meta, puis le vérifier. Ajouter le même numéro à `ALLOWED_SENDERS` au format international sans espaces et sans `+`. Ces deux autorisations sont distinctes.

Créer une clé OpenAI pour le projet utilisé et la conserver dans `OPENAI_API_KEY`. Le script de vérification ne contrôle pas la facturation ou l’accès effectif aux modèles.

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
| Vocal | Démarrer un nouvel accord et dicter une vente fictive en français | Transcription renvoyée dans WhatsApp, puis résumé ou question ciblée |
| Information manquante | Omettre la date | Question sur la date, pas d’accord incomplet enregistré |
| Annulation | Annuler un brouillon | Aucun nouvel accord sauvegardé |
| Isolement | Utiliser deux testeurs autorisés | Chacun consulte uniquement ses propres accords |
| Redémarrage | Relancer le serveur puis consulter Mes accords | Accord conservé dans SQLite |

Dans WhatsApp Web, autoriser le microphone pour le site si nécessaire. Si le vocal a été envoyé mais aucune transcription ne revient, vérifier d’abord les journaux serveur, la réception du webhook, puis l’accès aux API. Ne pas copier de tokens dans un ticket ou une capture.

### Journal de recette

À compléter après exécution réelle, sans déclarer un succès sur la seule base de tests simulés :

| Cas | Date | Résultat constaté |
| --- | --- | --- |
| Réponse au menu | — | Non exécuté |
| Accord texte confirmé | — | Non exécuté |
| Vocal français transcrit et affiché | — | Non exécuté |
| Correction avant validation | — | Non exécuté |
| Persistance après redémarrage | — | Non exécuté |

## Diagnostic rapide

- **Échec de vérification GET** : comparer le token de vérification et l’URL `/webhook`, tester l’accès HTTPS public.
- **POST refusé** : contrôler le secret d’application et l’en-tête signé envoyé par Meta. Ne pas désactiver la vérification de signature.
- **Aucune réaction** : contrôler l’abonnement `messages`, le numéro expéditeur autorisé, le serveur et le tunnel.
- **Envoi refusé** : contrôler le token, l’identifiant du numéro et le destinataire de test. Reprendre la conversation en envoyant un message depuis le téléphone pour tester une réponse au message entrant.
- **Transcription refusée** : contrôler l’accès OpenAI au modèle configuré, le type et la taille de l’audio. Réessayer avec un vocal court.
- **HTTP sain mais bot silencieux** : `/healthz` ne teste pas les fournisseurs ; examiner les erreurs du worker sans publier les données des utilisateurs.

La V1 répond aux messages entrants. Elle ne fournit pas de campagne sortante ni de relance automatique. Le passage au vrai numéro professionnel, les autorisations Meta, la facturation et un hébergement permanent constituent une étape distincte.
