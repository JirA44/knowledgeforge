# Changelog de KnowledgeForge

## V1.07 — 1.0.7

- Dossiers de lacunes fondés sur 2 à 100 références strictes de versions/preuves.
- Rechargement et contrôle SHA-256 des claims, sources, preuves et contradictions.
- Couverture soutenue/contredite, sources absentes ou périmées et dépendances manquantes.
- Accessibilité déclarée inconnue lorsqu'aucun état persistant ne permet de la calculer.
- Évolution chronologique, nouvelles lacunes, résolutions, pire transition et témoins.
- Qualifications prudentes `COVERED`, `GAPPED`, `INSUFFICIENT`, `INCOMPATIBLE`.
- API `/v1/evidence-gap-dossiers`, SQLite/PostgreSQL, OpenAPI exact, CI et tests.

## V1.06 — 1.0.6

- Dossiers chronologiques fondés uniquement sur 2 à 50 versions persistées.
- Ordre canonique par numéro de version, date et identifiant.
- Recalcul des hashes, masses probantes, confiance dérivée et alignement.
- Transitions de contradiction, couverture, confiance et direction documentaire.
- Pire transition et seuil matériel fixe de 0,10, avec zone neutre de 0,05.
- Qualifications prudentes `STABLE`, `ESCALATING`, `RESOLVING`, `INSUFFICIENT`
  et `INCOMPATIBLE`, sans verdict factuel.
- Snapshots immuables et idempotents, audit append-only, API POST/GET/liste,
  SQLite, PostgreSQL, OpenAPI et CI mis à jour.

## V1.05 — 1.0.5

- Confiance déclarée optionnelle, persistée dans chaque version immuable.
- Dossiers d'alignement de confiance fondés sur 1 à 20 versions.
- Recalcul des hashes, soutien, contradiction et diversité documentaire.
- Rechargement des rapports de couverture et dossiers de provenance disponibles.
- Écart de confiance et pire claim avec seuil absolu fixe de 0,10.
- Qualifications `ALIGNED`, `OVERCONFIDENT`, `UNDERCONFIDENT`, `INSUFFICIENT`
  et `INCOMPATIBLE`, sans décision de vérité.
- API POST/GET/liste, SQLite, PostgreSQL, OpenAPI et tests étendus.
- Préparation Git/GitHub : ignore, contribution, exemples français et CI.

## V1.04 — 1.0.4

- Dossiers de provenance fondés uniquement sur 1 à 20 versions explicites.
- Regroupement déterministe par éditeur, domaine ou identifiant stable.
- Détection des duplications par empreinte de contenu normalisé.
- Mesure des sources indépendantes, de la concentration et de la couverture.
- Seuils prudents : au moins trois sources et dominante au plus égale à 50 %.
- Avertissement explicite : la diversité documentaire ne prouve pas la vérité.
- API POST/GET/liste, SQLite, PostgreSQL, OpenAPI, documentation et tests étendus.

## V1.03 — 1.0.3

- Dossiers immuables fondés sur 2 à 20 identifiants de versions explicites.
- Vérification des versions courantes ou figées et reconstruction des relations.
- Chemins directionnels bornés et protégés contre les cycles.
- Agrégation des preuves, de leur intégrité, de la couverture et de la fraîcheur.
- Qualifications prudentes avec seuils fixes, sans résolution de vérité.
- Détection d'asymétrie accompagnée d'un avertissement explicite.
- API, SQLite, PostgreSQL, OpenAPI, documentation et tests étendus.

## V1.02 — 1.0.2

- Analyse d'impact déterministe du graphe directionnel de relations.
- Profondeur bornée, protection des cycles, branches et chemins explicites.
- Versions traversées et versions courantes identifiées par empreinte.
- Couverture et fraîcheur jointes lorsqu'un snapshot existe.
- Qualifications structurelles prudentes calculées exclusivement par le serveur.
- Snapshots immuables, idempotents et hashés; audit append-only.
- Schémas SQLite/PostgreSQL, API, OpenAPI, documentation et tests étendus.

## V1.01 — 1.0.1

- Rapports immuables et idempotents de couverture et de fraîcheur.
- Détection des sources manquantes et des empreintes invalides.
- Qualifications prudentes et audit des snapshots.

## V1.00 — 1.0.0

- Registre initial d'affirmations, de versions, de sources et de preuves.
- Évaluation documentaire, contradictions structurées et intégrité SHA-256.
