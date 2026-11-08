# Paiements partiels et carnet mobile — version 0.6

## Répartition du produit

WhatsApp conserve la création des accords, les vocaux, les confirmations et l’ajout des paiements. L’application Flutter iOS/Android est facultative et permet uniquement le suivi : synthèse, recherche de clients, accords, filtres et historique des versements. Elle utilise cette API et la même base, sans copie métier indépendante.

## Parcours WhatsApp

1. Choisir **Ajouter paiement**, ou envoyer `paiement` dans l’espace Wuumtaaba.
2. Donner la référence `WT-…` ou chercher le nom du client. Une recherche propose les accords ; elle ne choisit pas automatiquement entre homonymes.
3. Saisir un montant entier en F CFA. Un vocal est transcrit directement dans WhatsApp ; le montant doit être numérique et sans ambiguïté.
4. Vérifier le nouveau versement et le nouveau solde. Corriger par message ou confirmer **Enregistrer**. `annuler` ou **Annuler** abandonne la saisie.
5. `historique WT-référence` donne le détail et les versements, par pages de cinq. Les listes d’accords proposent aussi l’ouverture de la fiche.

Les montants sont des entiers positifs, limités au solde. Les calculs sont déterministes. Un changement du solde après préparation invalide le résumé. L’identifiant de confirmation est unique ; un ancien bouton et un double appui ne créent pas deux versements. Le commit SQLite regroupe paiement, solde, état de conversation, message entrant et réponse sortante. Le paiement final clôture l’échéance et annule les rappels. Un rappel partiel déjà en file et non envoyé est actualisé avec le nouveau solde.

La commande historique `réglé WT-référence` reste disponible et ajoute désormais un versement du solde. Les opérations sont des déclarations du commerçant ; elles ne vérifient aucun encaissement bancaire.

## Migration et sauvegarde

La migration **3** s’exécute une seule fois, en transaction, après la migration 2. Elle crée `payments`, `mobile_codes` et `mobile_tokens`. Une sauvegarde `*.pre-v3.sqlite3` est créée avant transformation d’une base sur disque.

L’avance des accords existants devient une entrée `initial`. Un ancien accord marqué `settled` reçoit une entrée `legacy_settlement` pour la différence entre le total et l’avance. Cette entrée est explicitement présentée comme un règlement historique déclaré/reconstitué, avec la date de l’événement d’audit si disponible ; à défaut, la date de migration. Aucun reçu bancaire ni date réelle de versement ne sont inventés. Le JSON et la colonne `paid` sont synchronisés.

Les avances d’origine restent dans le journal. Le total payé est mis à jour pour que WhatsApp, rappels, administration et application consultent le même solde. Les tables métier ne sont pas supprimées par la purge des messages.

Avant déploiement : conserver une sauvegarde externe du volume SQLite. Pour annuler une migration, arrêter les écritures, exporter les nouveaux versements et restaurer une sauvegarde dans une instance de contrôle. Revenir simplement à une ancienne image ne supprime pas les nouvelles données.

## Connexion de l’application

Le commerçant envoie `application` au bot depuis son numéro WhatsApp. Une réponse lui fournit un code de 12 caractères, valable cinq minutes et utilisable une seule fois. Un nouveau code invalide le précédent. Il n’y a aucun mot de passe d’administration dans l’application.

`POST /mobile/v1/session` échange ce code contre un jeton aléatoire valable 30 jours. Seules les empreintes des codes et jetons sont dans les tables d’authentification ; le code reçu reste naturellement dans l’historique privé des messages WhatsApp. Cinq appareils maximum par commerçant. L’application conserve le jeton dans Keychain/Keystore, jamais dans un fichier `.env`. La déconnexion révoque le jeton ; `deconnecter application` dans WhatsApp révoque tous les appareils et codes.

L’échange est limité globalement à 30 tentatives par cinq minutes sur l’instance. Les réponses mobiles ont `Cache-Control: no-store`. L’API native ne nécessite pas de CORS public. Chaque lecture est filtrée sur le propriétaire obtenu par le jeton, jamais sur un numéro fourni par le téléphone. Le client qui consent à un rappel n’obtient aucun accès au carnet du commerçant.

## API en lecture

| Route | Usage |
|---|---|
| `POST /mobile/v1/session` | Corps JSON `{"code":"…"}` ; connexion |
| `DELETE /mobile/v1/session` | Déconnexion de cet appareil |
| `GET /mobile/v1/summary` | Totaux, clients, accords, échéances passées |
| `GET /mobile/v1/clients?q=&page=1` | Recherche par nom/téléphone ; 25 résultats/page |
| `GET /mobile/v1/agreements?q=&status=all&client=&page=1` | Recherche client/produit/référence ; statuts `all`, `open`, `settled`, `overdue` |
| `GET /mobile/v1/agreements/{id}?page=1` | Fiche et 25 versements/page |

Toutes les routes sauf l’échange nécessitent `Authorization: Bearer …`. Les listes renvoient `items`, `has_more`, `page`. Les dates financières utilisent UTC/Burkina Faso. Une échéance passée n’est pas un jugement sur le client. Les clients sont regroupés par téléphone ; sans téléphone, les accords restent séparés pour éviter une fusion erronée d’homonymes.

Le suivi nécessite Internet. L’application garde uniquement la session sur disque et recharge les données au retour au premier plan, à la demande ou en tirant la liste vers le bas. Elle ne prétend pas assurer une synchronisation hors ligne.

## Validation et limites de recette

Les tests couvrent le journal, les limites de montant, le rollback, les rejouements, la persistance, la migration historique, l’isolation des propriétaires, les codes consommés/expirés, la révocation, la pagination, les recherches et la mise à jour des rappels déjà en file. Ils utilisent des données fictives et aucun fournisseur WhatsApp/Gemini réel.

Une recette de production devra encore utiliser un compte de test réel pour créer un accord, ajouter deux paiements, vérifier le solde dans le carnet Flutter, redémarrer et révoquer l’appareil. Ne pas confondre compilation, tests isolés et échange réel WhatsApp.
