# Accords français/mooré et lecture facultative — version 0.7

## Parcours

`menu` → `Nouvel accord` → **Français** ou **Mooré**.

- Français : texte ou vocal, transcription française par Gemini, puis extraction du brouillon.
- Mooré : texte ou vocal, transcription mooré par BurkimbIA, traduction française par BurkimbIA, puis extraction du brouillon français par Gemini.
- Le bot affiche la transcription originale et la traduction française avant le résumé. Les corrections en mooré suivent la même chaîne de traduction.
- Le résumé propose **Enregistrer**, **Recommencer** et **Écouter en français/mooré**, selon la langue choisie.
- La lecture énonce client, produit, prix total, avance, reste à payer et date. Elle ne valide et n’enregistre jamais l’accord.

Les vocaux sont téléchargés et traités directement, sans demande Accepter/Refuser. L’aide décrit les services de traitement et la conservation privée. La confirmation du résumé reste nécessaire pour enregistrer un accord. La transcription et la traduction française sont reliées au média d’origine ; la langue est conservée dans l’accord confirmé. Le calcul du solde reste déterministe.

## Modèles utilisés

Le client BurkimbIA utilise le fournisseur choisi dans l’administration : Cloud ou Mac local. Le portail de services autonome a été retiré ; ces modèles restent utilisés dans les accords en mooré. Aucun basculement automatique vers un fournisseur payant. Le Mac doit rester allumé, connecté et relié par son connecteur sortant.

La synthèse mooré utilise BurkimbIA. La voix française utilise le compte Gemini existant, modèle configurable `FRENCH_TTS_MODEL`, par défaut `gemini-3.1-flash-tts-preview`. Elle est appelée uniquement après un clic explicite sur la lecture. Aucun nouvel accès fournisseur n’est demandé.

## Fidélité de la lecture mooré

Un test réel de traduction du résumé complet a supprimé le jour dans une date. La lecture ne traduit donc pas une phrase financière complète : les intitulés génériques et le produit sont traduits séparément, puis le nom, les montants entiers, le solde et la date complète sont réinsérés par le code. Les intitulés seuls sont mis en cache par fournisseur/modèle ; aucune donnée de client n’est mise en cache.

La voix reste un rendu généré à vérifier ; la conservation de ces valeurs dans le texte de synthèse n’est pas une certification linguistique de leur prononciation. Le résumé écrit français et la confirmation explicite restent les références du brouillon.

Les entrées longues sont découpées aux limites de mots pour respecter les 512 caractères des modèles BurkimbIA ; aucune troncature silencieuse. Les parties vocales sont assemblées en MP3. Les textes de plus de 4096 caractères demandent un message plus court. Un échec de traduction ou de synthèse n’enregistre aucun accord.

## Résumés expirés et compatibilité

Chaque choix de langue et chaque bouton de lecture est lié à son brouillon. Corriger le texte, renvoyer un vocal, recommencer ou revenir au menu invalide les anciens résumés. Les brouillons français créés avant 0.7 restent utilisables. Un nouveau brouillon attend explicitement le choix de langue avant tout traitement.

## Vérifications

- 276 tests automatisés réussis, dont choix de langue, routage mooré/français, conservation de l’original, échecs de traduction, absence de validation par lecture, boutons expirés, décodage PCM Gemini et protection des valeurs avant synthèse.
- Appels réels sur une phrase synthétique courte : voix française HTTP 200 ; synthèse mooré MP3 ; transcription mooré ; traduction française. Aucune donnée de commerçant utilisée pour ces appels.
- La chaîne sur un vrai vocal d’accord, la prononciation et la qualité linguistique nécessitent une recette par l’utilisateur. Aucun résultat de compréhension parfaite n’est revendiqué.

Le démarrage local a été exécuté via le skill `burkimbia-mac:start-burkimbia-modeles`. API 8765, laboratoire 8780 et connecteur actifs ; le bot confirme `mac_online=true`. Le lancement n’a pas modifié le choix Cloud/Mac de l’administration.
