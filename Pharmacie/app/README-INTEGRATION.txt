ORDONNANCE MOBILE — MODULE FEUILLES D’ASSURANCE

CONTENU
- feuilles-assurance.html : interface intégrée, accessible depuis chaque application.
- feuilles-assurance.js : logique du module et utilisation de la session Supabase.
- migration_feuilles_assurance.sql : tables dédiées, RLS et bucket privé.

IMPORTANT
1. Le module est ajouté aux ZIP des quatre applications, mais il n’est pas encore déployé sur Netlify.
2. Pour activer le module, appliquer une seule fois migration_feuilles_assurance.sql dans Supabase > SQL Editor, après vérification. Le SQL ajoute des tables dédiées et un bucket privé ; il ne supprime pas les tables existantes.
3. La reconnaissance automatique/OCR de la photo n’est pas incluse : la photo est conservée comme référence, le personnel doit vérifier et décrire les champs.
4. Les modèles AMU, INAM et autres ne sont pas déclarés officiels sans validation documentaire par l’assureur.
5. Avant production, tester les politiques RLS avec des comptes patient, médecin, pharmacien et administrateur. Une vérification finale de sécurité est nécessaire, notamment les droits de validation et les corrections post-validation.
6. Le PDF généré par le bouton « Consulter / imprimer » est un rendu imprimable côté navigateur, pas une signature électronique officielle.
7. Le module réutilise le projet Supabase déjà configuré dans les applications.
