# Annotations WhatsApp — proposition pour revue

Cette branche corrige les boutons A/B envoyés pendant le pilote et prépare leur
stockage central. Elle ne déploie rien, n’envoie aucun nouveau message et ne crée
pas de campagne quotidienne. Le simulateur du laboratoire Mac reste un prototype
distinct : ses données navigateur ne deviennent pas des annotations de production.

## Parcours proposé dans cette PR

1. Recevoir un court extrait mooré et deux transcriptions anonymisées A/B.
2. Choisir A, B ou Égalité avec les boutons WhatsApp déjà envoyés.
3. Noter A, puis B, de 1 à 5 avec une liste WhatsApp ou un chiffre.
4. Proposer une transcription mooré corrigée, ou écrire PASSER.
5. Recevoir une confirmation après la transaction en base.

Échelle proposée : 1 inutilisable, 2 nombreuses erreurs, 3 compréhensible à
corriger, 4 presque exacte, 5 fidèle à l’audio. Les notes et la correction sont
facultatives ; une étape passée reste vide, sans valeur inventée. MENU ou STOP
gardent le comportement habituel du bot. Aucun accord commercial n’est créé.

Le correctif reconnaît `annpreview:002:*` et `annpreview:030:*`. Un identifiant de
bouton ne donne pas accès à une tâche : il faut une attribution en base et la
preuve d’une carte réellement envoyée au même numéro, contenant exactement les
propositions A/B du snapshot. Attribution désactivée, STOP ou expiration de sept
jours bloquent l’annotation. Un résultat terminé n’est pas rouvert par un ancien
bouton. Cette limite de sept jours est un choix du pilote, à discuter.

## Où sont les données en production ?

Les quatre tables sont créées dans le schéma configuré par `DATABASE_SCHEMA`
(`wuumtaaba` sur Supabase pour le bot Fly), via `annotation_schema.sql` :

| Table | Données conservées |
| --- | --- |
| `annotation_tasks` | Type de tâche, référence du dataset, révision, extrait, hash audio, chemin du fichier privé, propositions et identifiants des modèles |
| `annotation_assignments` | Destinataire, tâche, carte WhatsApp effectivement envoyée, date, état |
| `annotation_responses` | Préférence, notes A/B, correction éventuelle, état, snapshot de la source et dernier message traité |
| `annotation_events` | Historique des réponses appliquées, lié à l’identifiant unique du message entrant |

Les octets audio vont dans le **bucket privé Supabase Storage existant**, sous
`annotations/<sha256>.audio`. La table garde le chemin et l’empreinte ; aucun
fichier de données n’est conservé sur le disque du conteneur Fly. Le code de ce
parcours n’utilise ni SQLite ni localStorage. L’audio est relu depuis Storage et
son empreinte vérifiée avant que la tâche soit enregistrée. Les tâches sont
immuables : un même identifiant ne peut pas recevoir un autre snapshot.

Les réponses, l’état conversationnel et l’accusé sortant sont enregistrés dans
la même transaction que la fin du message entrant. Si la sauvegarde échoue,
l’accusé de réussite n’est pas ajouté à l’outbox. Un message déjà appliqué ne
crée pas une seconde annotation. RLS est activée sur les quatre tables ; aucun
accès anonyme ou depuis le navigateur n’est ajouté.

## Import des exemples déjà envoyés

Le pilote a envoyé des cartes avant de disposer de ce traitement. Le script
ci-dessous récupère leurs métadonnées depuis l’audit du bot, télécharge seulement
les deux extraits publics concernés et vérifie révision, identifiant et SHA-256.
Il contrôle que le bucket est privé, archive les audios, puis attribue les tâches
aux destinataires dont les cartes A/B correspondantes ont réellement été envoyées.
Il n’envoie aucun message et ne réinitialise pas les attributions existantes.

```sh
# Plan en lecture seule ; utilise les secrets de l’environnement serveur.
python scripts/import_annotation_previews.py
# Après revue et déploiement du code, import explicite dans Supabase :
python scripts/import_annotation_previews.py --apply
```

L’import est volontairement borné aux lignes 2 et 30 de
`burkimbia/asr-benchmark-public`. Les sorties des deux modèles sont celles déjà
calculées et conservées dans l’audit ; aucune nouvelle inférence payante n’est
lancée. Les octets transitent en mémoire, jamais dans un fichier local.

Les anciens clics reçus avant le correctif restent dans `inbox`/`messages` ; le
script ne les transforme pas silencieusement en annotations structurées. Il faut
recliquer après import, ou effectuer un backfill revu à partir des identifiants
de messages réellement reçus. Il n’existe pas encore d’interface d’administration
ou d’export central des annotations dans cette PR.

## Conditions avant un passage en production

- Réconcilier la branche principale avec les modifications actuellement déployées
  hors de cette branche : le portail de production diffère de `main`. Cette PR
  n’embarque pas ces autres modifications. La fusion sur `main` déclenche le
  déploiement automatique existant ; ne pas fusionner sans vérifier cette base.
- Déployer le code approuvé, puis exécuter l’import explicite côté serveur.
- Vérifier les quatre tables dans Supabase, le caractère privé du bucket et la
  présence des fichiers référencés. Refaire une annotation, puis la relire avec
  une nouvelle connexion après redémarrage du bot.
- Vérifier qu’une confirmation WhatsApp correspond bien à une ligne persistée,
  que les doublons sont ignorés et que STOP reste effectif.

Les tests locaux utilisent un Postgres isolé et un stockage média de test.
Ils prouvent le comportement transactionnel et la vérification des octets ; ils
ne sont pas présentés comme une recette déjà effectuée dans Supabase production.

## Tâches et décisions à discuter dans la PR

| Tâche | Dans cette PR | Décision attendue |
| --- | --- | --- |
| Préférence ASR A/B | Oui, avec Égalité | Ajouter « aucune des deux » ? Comparaison aveugle et ordre A/B par annotateur ? |
| Note individuelle 1–5 | Oui | Une note globale ou plusieurs critères : mots, nombres, omissions ? |
| Transcription corrigée | Oui, facultative | Orthographe attendue, ponctuation, passages inaudibles, validation indépendante |
| Traduction mooré/français | Non | Noter fidélité et omissions, comparer deux traductions, fournir une correction ? |
| Qualité de l’audio | Non | Bruit, musique, intelligibilité, durée, conditions de rejet |
| Extraction métier | Non | Nombres, montants, noms, produits et dates à annoter séparément |

Autres points : inscription des contributeurs, nombre d’extraits par lot,
expiration, double annotation et arbitrage, export, durée de conservation,
anonymisation des contributeurs et limites de coût Meta. Une relance proactive
hors fenêtre de 24 h nécessite un template approuvé ; aucun envoi récurrent ni
basculement vers une catégorie payante n’est activé ici.

Les extraits du benchmark ne doivent pas rejoindre l’entraînement sans retrait
du jeu d’évaluation. Les préférences humaines de ce pilote ne constituent pas à
elles seules une mesure globale de performance des modèles.
