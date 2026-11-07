# Trois exemples d’accords Wuumtaaba

Ces exemples sont fictifs. Ils servent à tester la transcription, la traduction et l’extraction des informations d’un accord.

## 1. Achat de ciment avec une avance

« Bonjour, je m’appelle Moussa Ouédraogo et je tiens une boutique de matériaux de construction à Koudougou. Aujourd’hui, le 7 novembre 2026, ma cliente Awa Sawadogo est venue acheter dix sacs de ciment pour les travaux de sa maison.

Nous nous sommes mis d’accord sur un prix de six mille cinq cents francs CFA par sac. Le montant total de son achat est donc de soixante-cinq mille francs CFA. Le transport n’est pas compris dans ce prix : Awa viendra récupérer les sacs avec son propre véhicule, demain dans la matinée.

Elle m’a déjà versé vingt-cinq mille francs CFA en espèces aujourd’hui. Nous avons convenu qu’elle paiera les quarante mille francs CFA restants au plus tard le 20 novembre 2026. Elle pourra passer à la boutique pour effectuer ce paiement.

Je souhaite enregistrer cet accord pour garder une trace du produit acheté, du prix total, de l’avance reçue et de la date prévue pour régler le solde. »

### Résultat attendu

| Champ | Valeur |
| --- | --- |
| Client | Awa Sawadogo |
| Produit | 10 sacs de ciment |
| Prix total | 65 000 F CFA |
| Avance reçue | 25 000 F CFA |
| Solde calculé | 40 000 F CFA |
| Échéance | 2026-11-20 |

Le vendeur Moussa ne doit pas être confondu avec la cliente. La date de retrait des sacs n’est pas l’échéance du paiement. Le transport ne doit pas être ajouté au prix.

## 2. Commande de couture avec une avance

« Je suis Mariam, couturière à Ouagadougou. Le 7 novembre 2026, j’ai reçu une commande de mon client Issa Kaboré. Il souhaite faire confectionner trois tenues pour une cérémonie familiale. Il m’a apporté lui-même les tissus ; je ne lui vends donc pas de pagne dans cette commande.

Nous avons fixé le prix de la couture à douze mille francs CFA par tenue. Pour les trois tenues, le prix total de la prestation est de trente-six mille francs CFA. Ce montant comprend la coupe, la couture et les petites retouches nécessaires après l’essayage.

Issa m’a remis quinze mille francs CFA comme avance. Il doit revenir pour un essayage le 12 novembre 2026. Cette visite servira seulement à vérifier les mesures et à faire les ajustements. Nous avons convenu que les vingt et un mille francs CFA restants seront payés au plus tard le 18 novembre 2026, lorsqu’il viendra récupérer ses trois tenues.

Je veux conserver cet accord pour retrouver facilement sa commande, le montant déjà reçu et la somme qu’il lui restera à payer. »

### Résultat attendu

| Champ | Valeur |
| --- | --- |
| Client | Issa Kaboré |
| Produit ou prestation | Confection de 3 tenues, tissus fournis par le client |
| Prix total | 36 000 F CFA |
| Avance reçue | 15 000 F CFA |
| Solde calculé | 21 000 F CFA |
| Échéance | 2026-11-18 |

La date d’essayage ne doit pas remplacer l’échéance de paiement. Aucun prix de tissu ne doit être inventé.

## 3. Vente de riz à crédit, sans avance

« Bonjour, je suis Adama et je vends des céréales au marché de Bobo-Dioulasso. Aujourd’hui, le 7 novembre 2026, mon client Paul Zongo est venu chercher quatre sacs de riz de vingt-cinq kilogrammes chacun pour approvisionner son petit restaurant.

Le prix convenu est de dix-huit mille francs CFA par sac. La vente représente donc un total de soixante-douze mille francs CFA. Paul a vérifié les quatre sacs et les a emportés aujourd’hui. Il n’y a pas de frais de livraison dans cet accord.

Paul ne m’a rien payé pour le moment. Je précise qu’il n’a versé aucune avance : le montant déjà reçu est de zéro franc CFA. Je lui accorde un délai et nous avons convenu qu’il réglera la totalité des soixante-douze mille francs CFA au plus tard le 30 novembre 2026.

Il m’a dit qu’il essaierait de passer me saluer la semaine prochaine, mais nous n’avons prévu aucun paiement obligatoire à cette occasion. La date limite de règlement reste bien le 30 novembre 2026. Je souhaite enregistrer cette vente à crédit afin de suivre ce qui m’est dû et de retrouver notre accord sans confusion. »

### Résultat attendu

| Champ | Valeur |
| --- | --- |
| Client | Paul Zongo |
| Produit | 4 sacs de riz de 25 kg |
| Prix total | 72 000 F CFA |
| Avance reçue | 0 F CFA, explicitement indiqué |
| Solde calculé | 72 000 F CFA |
| Échéance | 2026-11-30 |

L’avance est explicitement nulle : elle ne doit pas être considérée comme une information manquante. La visite évoquée pour la semaine suivante ne crée pas une autre échéance.

## Utilisation dans le laboratoire

Pour chaque essai, utiliser uniquement le texte entre guillemets. Garder les tableaux à part pour comparer le résultat obtenu au résultat attendu.

Ces textes sont volontairement longs : pour tester le parcours vocal, respecter la durée maximale affichée dans le laboratoire et découper l’enregistrement si nécessaire.









| Brique existante | Ce qu’elle pourrait apporter à agent-yaaba | Limite |
|---|---|---|
| Correction orthographique | Corriger les graphies connues avec une table de 1 018 correspondances, plutôt que laisser Yaaba inventer une correction. | Ne traite que les mots répertoriés ; aucune substitution globale des accents. |
| Salutations et formules courantes | Retrouver des réponses validées pour bonjour, bonsoir, merci et les nouvelles. | Ressource ciblée, pas un traducteur général. |
| Dictionnaire avec exemples | Fournir au modèle une traduction attestée et une phrase d’usage avant sa réponse. | L’extraction existe ; la recherche dans ce dictionnaire reste à brancher à l’agent. |
| Glossaire du numérique | Retrouver les termes techniques mooré et leurs définitions. | Certaines définitions françaises et mooré décrivent le même concept sans être des traductions exactes. |
| Analyse des mots composés et classes nominales | Expliquer certaines compositions et proposer des correspondances singulier/pluriel. | Règles partielles ; certaines classes restent insuffisamment validées. |
| Contrôle de la langue produite | Signaler une réponse en français, des caractères suspects ou une faible couverture lexicale. | Heuristiques : cela ne prouve ni la grammaticalité ni le bon sens de la réponse. |



Pour Wuumtaaba, ma première priorité serait donc les audios annotés et l’interpréteur numérique contextuel. Ces données permettront ensuite de décider où un entraînement ciblé apporte réellement un gain : transcription, repérage des nombres ou traduction.
