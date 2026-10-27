# Livraison 0.6 — 27 octobre 2026

Branche locale : `feat/partial-payments-mobile`.

## Livré dans le code

- Paiement partiel avec sélection propriétaire de l’accord, recherche sans accents, montant déterministe, correction et confirmation.
- Journal durable des versements, migration des avances et anciens règlements, synchronisation du solde JSON/colonnes.
- Rejeu idempotent et transaction atomique avec la file WhatsApp ; contrôles des surpaiements et soldes modifiés.
- Consultation paginée du détail et du journal dans WhatsApp ; listes plus courtes.
- Mise à jour du contenu des rappels non envoyés après paiement partiel ; annulation au règlement complet.
- Appairage facultatif de l’application par code WhatsApp à usage unique, jetons révocables et API de lecture propriétaire.
- Recherche, agrégation par client, filtres et pagination. La recherche d’un client conserve les totaux de tous ses accords, y compris si son nom varie.

## Vérifié localement

- **262 tests Python réussis**, dont 20 nouveaux cas de paiement/API mobile.
- `ruff check .` réussi.
- Migration testée sur une base historique et après réouverture, avec sauvegarde et absence de doublons.
- Aucun appel réseau WhatsApp/Gemini ni donnée réelle utilisé par ces tests.

Le code Flutter correspondant est dans `/Users/josias/Documents/Personnal/BurkimbIA/wumtaba/wumtaba_app`, branche `feat/whatsapp-ledger-app`. Son rapport décrit les builds et la vérification visuelle.

## État de mise en service

**Déployé en production le 27 octobre 2026 à la demande de Josias.** Le contrôle HTTPS répond en version 0.6.0. Les trois accords sont conservés ; migrations 2 et 3 présentes ; intégrité OK et aucun écart entre le journal des paiements et les soldes. La même machine et le même volume sont conservés. Voir `DEPLOYMENT_2026-10-27.md`. Aucun versement de test n’a été ajouté aux données réelles.

Recette après déploiement sur un compte de test : créer une vente de 75 000 F avec 25 000 F d’avance, confirmer 20 000 F puis 10 000 F, vérifier le reste de 20 000 F dans WhatsApp et Flutter, solder et vérifier 0 F, redémarrer, vérifier les quatre entrées du journal (avance + trois versements), rejouer un ancien bouton, puis révoquer les accès mobiles.

Le modèle Meta de rappel et son admissibilité ne sont pas modifiés par cette livraison. L’application ne propose aucun enregistrement audio ni modification des données métier.
