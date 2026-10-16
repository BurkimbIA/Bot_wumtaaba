# Back-office administrateur

Le back-office est disponible à `/admin` sur le même service Fly.io. Il utilise directement la base SQLite du bot, sans copie de données ni dépendance à un autre hébergement.

Quatre vues :

- Vue d’ensemble : contacts, accords en cours, solde à suivre, rappels en attente et activité récente.
- Accords et échéances : groupes par numéro de téléphone du vendeur, avec nombre d’accords, montant total et solde à suivre. Ouvrir un vendeur affiche ses accords paginés. Recherche par vendeur, client ou référence ; les totaux portent alors sur les accords correspondant à la recherche.
- Conversations : recherche par numéro, historique des 200 derniers messages, transcription et lecture des fichiers audio conservés.
- Rappels : destinataires, dates, rôles et filtre par état, notamment consentement requis et blocage de modèle Meta.

Les données métier restent en lecture seule : ni envoi manuel, ni modification d’accord, ni suppression. Depuis la version 0.5, l’onglet **Modèles IA** permet aussi de changer le fournisseur des modèles BurkimbIA entre Mac local et Cloud. Le Mac doit être en ligne pour le sélectionner ; le choix est conservé après redémarrage. Voir [le connecteur et ses limites](BURKIMBIA.md#choisir-les-modèles-mac-ou-cloud). Les listes sont paginées par 25 éléments ; les heures sont affichées au Burkina Faso. Le bouton Actualiser recharge les données.

## Accès privé

`ADMIN_PASSWORD` est un code aléatoire d’au moins 24 caractères, conservé dans les secrets Fly.io et le fichier local `.env` ignoré par Git. Sans cette configuration, les connexions sont désactivées. Le fichier local `.admin-access.txt`, également ignoré, permet au propriétaire de retrouver son accès ; ne jamais le publier.

La connexion produit une session serveur de huit heures avec cookie HttpOnly, Secure et SameSite=Strict. Les sessions disparaissent au redémarrage et à la déconnexion. Les données et audios nécessitent cette session ; les réponses admin utilisent `Cache-Control: no-store`. Dix tentatives de connexion au maximum sont acceptées sur cinq minutes pour cette instance pilote. Les données WhatsApp sont affichées comme texte, jamais interprétées comme HTML.

`ADMIN_COOKIE_SECURE=true` doit rester activé en production HTTPS. Pour une prévisualisation locale sur HTTP uniquement, utiliser `false` avec une configuration de démonstration distincte. Ne pas réutiliser le code de production dans les tests.

Un seul compte administrateur est prévu pour ce pilote. La gestion de plusieurs utilisateurs, leurs rôles et l’authentification à deux facteurs ne sont pas incluses.

## Vérification

Tests automatisés : refus sans session, connexion/déconnexion, limitation des tentatives, origine des requêtes, filtres SQL, pagination, projection des données et lecture partielle des audios protégés. Vérification navigateur sur données de démonstration : connexion, synthèse, recherche d’accord, conversations et rappels.

## Recette du 10 octobre 2026

147 tests réussis et Ruff validé. Version 0.3.0 déployée sur Fly.io : quatre API de consultation HTTP 200 après connexion, refus HTTP 401 sans session et après déconnexion, lecture partielle d’un audio privé HTTP 206 en audio/ogg. Connexion, recherche, conversation, rappels et déconnexion vérifiés dans le navigateur avec des données de démonstration.
