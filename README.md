# KnowledgeForge V1.07

KnowledgeForge est un registre API traçable pour conserver des affirmations versionnées, leurs sources et les extraits qui les soutiennent ou les contredisent. Le système calcule un **état documentaire**, jamais une « vérité » artificielle.

## Nouveauté V1.07 : dossier de lacunes de preuves

`POST /v1/evidence-gap-dossiers` reçoit un `claim_id`, une liste de versions et,
facultativement, une sélection explicite de preuves. L'ensemble contient entre
2 et 100 références persistées et uniques. Aucun résultat, verdict, témoin,
score ou qualification n'est accepté du client.

Le serveur ordonne les versions par numéro, date et identifiant, recharge les
sources, preuves, contradictions et relations de dépendance, puis revérifie les
empreintes SHA-256. Pour chaque version il expose les éléments soutenant ou
contredisant, les sources manquantes ou périmées, les preuves invalides, les
dépendances absentes, la couverture existante et des témoins déterministes.
L'accessibilité réseau n'est jamais inventée : sans état persistant, elle reste
`UNKNOWN_NOT_PERSISTED`.

La péremption utilise un seuil fixe de **365 jours** entre la publication de la
source et la création de la version, ce qui conserve l'idempotence du snapshot.
Les transitions listent les nouvelles lacunes, les lacunes résolues et celles
qui persistent; la pire transition privilégie le plus grand nombre de lacunes,
puis les nouveaux codes.

- `COVERED` : chaque version possède un soutien intègre et sourcé, sans lacune ;
- `GAPPED` : une contradiction, source périmée, dépendance ou autre lacune est présente ;
- `INSUFFICIENT` : une version manque de preuve intègre, sourcée et exploitable ;
- `INCOMPATIBLE` : les références ne correspondent pas toutes au claim et aux versions sélectionnés.

Le dossier est immuable, idempotent, hashé et audité. **`COVERED` ne prouve
jamais la vérité ni la causalité.**

## Nouveauté V1.06 : évolution chronologique d'une affirmation

`POST /claim-evolution-dossiers` accepte uniquement de 2 à 50
`claim_version_ids` persistés et uniques. Le serveur recharge chaque version,
ses sources, preuves, contradictions structurées et sa couverture disponible,
revérifie les empreintes SHA-256 puis ordonne les versions par numéro, date et
identifiant. Des versions de claims différents produisent prudemment
`INCOMPATIBLE`.

Chaque version expose masses de soutien et de contradiction, couverture,
confiance déclarée éventuelle, confiance probante dérivée, écart d'alignement,
direction documentaire et intégrité. Chaque transition expose leurs deltas,
les changements de direction, de couverture et d'alignement, ainsi que les
contradictions structurées ajoutées ou retirées. La pire transition maximise le
delta de risque `delta_contradiction − delta_confiance_probante`.

Règles fixes : un delta absolu matériel vaut `0,10`; la zone neutre de direction
vaut `0,05`. Une hausse de contradiction d'au moins `0,10`, une baisse de
confiance probante d'au moins `0,10`, une nouvelle contradiction explicite ou
un mouvement vers `CONTRADICTING` entraîne `ESCALATING`. Sans aucune escalade,
les mouvements inverses entraînent `RESOLVING`. `STABLE` signifie qu'aucun seuil
n'est franchi; `INSUFFICIENT` protège toute version sans preuve intègre
exploitable. Si des signaux opposés coexistent, `ESCALATING` prévaut par prudence.

Le dossier est canonique, immuable, idempotent, hashé et audité. Il décrit une
évolution documentaire et ne constitue jamais un verdict factuel ou une preuve
de vérité.

## Nouveauté V1.05 : alignement de confiance

Une version d'affirmation peut désormais conserver une
`declared_confidence` facultative entre `0` et `1`. Cette valeur est une
métadonnée déclarée, immuable avec la version; elle n'est ni calculée ni validée
comme vérité par KnowledgeForge.

`POST /trust-alignment-dossiers` reçoit uniquement de 1 à 20
`claim_version_ids` persistés et uniques. Le serveur recharge les affirmations,
preuves, contradictions, rapports de couverture et dossiers de provenance,
puis recalcule l'intégrité et la confiance probante.

Formules et seuils fixes :

- masse de soutien : `1 − produit(1 − poids_source)`;
- facteur de diversité : `min(1, nombre_sources_soutien / 3)`;
- confiance probante :
  `soutien × (1 − contradiction) × facteur_diversité`;
- écart : `confiance_déclarée − confiance_probante`;
- tolérance d'alignement absolue : `0,10`;
- minimum probant : deux sources indépendantes de soutien.

Qualifications serveur :

- `ALIGNED` : toutes les versions sont comparables et le pire écart absolu est
  inférieur ou égal à `0,10`;
- `OVERCONFIDENT` : le pire écart dépasse `0,10` et la confiance déclarée est
  supérieure à la confiance probante;
- `UNDERCONFIDENT` : le pire écart dépasse `0,10` dans l'autre sens;
- `INSUFFICIENT` : confiance déclarée absente, intégrité incomplète ou moins de
  deux sources indépendantes de soutien pour au moins une version;
- `INCOMPATIBLE` : une relation directe `CONTRADICTS` relie des versions
  sélectionnées.

Lorsqu'une version historique ne possède pas de confiance déclarée,
KnowledgeForge calcule uniquement sa confiance probante et retourne
`INSUFFICIENT`; aucune confiance manquante n'est inventée. Le snapshot expose
les mesures de soutien, contradiction, couverture, diversité, provenance,
écart et pire claim. Il est canonique, hashé, immuable, idempotent et audité.

**Avertissement : une confiance élevée ou bien alignée reste une mesure
documentaire et ne prouve jamais la vérité de l'affirmation.**

## Documentation et contribution

- [Exemples d'utilisation en français](docs/USAGE_EXAMPLES.md)
- [Guide de contribution](CONTRIBUTING.md)
- [Contrat OpenAPI 3.1](openapi.yaml)

## Nouveauté V1.04 : indépendance et provenance des sources

`POST /provenance-dossiers` reçoit uniquement une liste de 1 à 20
`claim_version_ids` uniques. Le serveur recharge les versions, les liens de
preuve et les sources enregistrées. Aucun score, verdict, groupe de provenance,
seuil ou qualification ne peut être fourni par le client.

Le regroupement suit exclusivement cet ordre :

1. éditeur normalisé enregistré;
2. domaine extrait de l'URL enregistrée;
3. identifiant stable de source si aucun éditeur ni domaine n'est disponible.

Le dernier cas est explicitement marqué `SOURCE_ID` et entraîne
`UNKNOWN_PROVENANCE` : l'identifiant assure la stabilité interne, mais ne permet
pas d'inventer une provenance externe.

Chaque snapshot expose :

- l'intégrité SHA-256 des preuves et l'existence de leur source;
- les groupes de sources, domaines ou identifiants stables;
- les empreintes de contenu normalisé et leurs duplications entre sources;
- le nombre de sources indépendantes et la part de contenu unique de chacune;
- la source dominante et sa part;
- la couverture par version sélectionnée et le dernier rapport de
  couverture/fraîcheur disponible;
- l'état `CURRENT` ou `FROZEN` de chaque version.

### Qualifications et seuils fixes V1.04

- `DIVERSE` : au moins trois sources indépendantes, provenance externe connue,
  intégrité complète et source dominante inférieure ou égale à `50 %`;
- `CONCENTRATED` : au moins trois sources indépendantes, mais une source détient
  plus de `50 %` du contenu unique;
- `INSUFFICIENT` : moins de trois sources indépendantes, aucune preuve intègre
  exploitable, ou intégrité incomplète;
- `UNKNOWN_PROVENANCE` : au moins un groupe exploitable ne possède ni éditeur ni
  domaine enregistré et repose uniquement sur son identifiant interne.

Les preuves dupliquées sont détectées par une empreinte SHA-256 de leur extrait
normalisé, indépendamment de la source. Elles ne multiplient jamais le nombre de
contenus uniques.

Exemple :

```json
{
  "claim_version_ids": ["UUID_VERSION_A", "UUID_VERSION_B"]
}
```

Le snapshot est canonique, hashé, immuable, idempotent et audité. Les routes
`GET /provenance-dossiers/{id}` et `GET /provenance-dossiers` permettent sa
relecture et la liste paginée par limite.

**Avertissement : la diversité et l'indépendance apparentes des sources ne
prouvent ni la vérité des affirmations ni l'authenticité des sources.**

## Nouveauté V1.03 : dossiers immuables de contradictions

`POST /contradiction-dossiers` reçoit uniquement une liste de 2 à 20
`claim_version_ids` uniques. Il vérifie chacune des versions sélectionnées et
indique si elle est encore `CURRENT` ou désormais `FROZEN`. Aucun verdict, score
final, résolution, preuve, seuil ou qualification ne peut être fourni par le
client.

Le serveur reconstruit les relations directes `CONTRADICTS` entre les versions,
ainsi que leurs chemins directionnels de contexte limités à trois relations. Un
chemin indirect reste uniquement contextuel : il ne crée jamais une
contradiction. Les cycles ne sont pas suivis et vingt chemins au maximum sont
conservés par paire.

Pour chaque côté, le dossier agrège sans réécrire les données :

- les preuves de soutien et de contradiction, leur empreinte et leur intégrité;
- le nombre de sources indépendantes et la masse documentaire correspondante;
- le dernier rapport immuable de couverture et de fraîcheur, s'il existe;
- la version sélectionnée, son empreinte et la version courante.

Exemple minimal :

```json
{
  "claim_version_ids": [
    "UUID_VERSION_A",
    "UUID_VERSION_B"
  ]
}
```

### Qualifications et seuils fixes V1.03

- `NO_CONTRADICTION` : aucune relation directe `CONTRADICTS` ne relie les
  versions sélectionnées;
- `EVIDENCE_ASYMMETRY` : une paire directe présente un côté avec au moins deux
  sources de soutien indépendantes et soit un écart d'au moins deux sources,
  soit un écart de masse documentaire d'au moins `0,25`;
- `UNRESOLVED` : chaque côté de chaque paire directe possède au moins deux
  sources de soutien indépendantes, sans asymétrie dépassant les seuils;
- `INSUFFICIENT` : une contradiction directe existe, mais au moins un côté reste
  sous le seuil de deux sources et aucune asymétrie qualifiable n'est établie.

L'ordre des identifiants n'affecte pas le résultat. Le snapshot JSON canonique
est hashé en SHA-256, immuable, idempotent et audité. Il est relu avec
`GET /contradiction-dossiers/{dossier_id}`.

**Avertissement : une asymétrie de preuves enregistrées ne prouve pas que le
côté le plus documenté est vrai. KnowledgeForge ne résout jamais la vérité de
la contradiction.**

## Nouveauté V1.02 : analyse d'impact du graphe

`POST /claims/{id}/impact-reports` part d'une version d'affirmation et parcourt
uniquement les relations directionnelles sortantes. La profondeur est bornée de
1 à 8, chaque version n'est développée qu'une fois et tout retour vers une
version déjà présente dans le chemin est signalé sans être suivi de nouveau.

Le snapshot expose :

- les affirmations affectées et leur profondeur minimale;
- chaque chemin explicite et les relations `SUPPORTS` / `CONTRADICTS` suivies;
- la version traversée, la version courante et leur empreinte de contenu;
- le dernier rapport de couverture et de fraîcheur de la version, s'il existe;
- les branches, cycles et relations laissées au-delà de la profondeur demandée;
- une qualification serveur `LOCALIZED`, `WIDESPREAD`, `INSUFFICIENT` ou
  `CYCLE_DETECTED`.

`WIDESPREAD` signifie qu'au moins quatre affirmations distinctes ont été
atteintes. `LOCALIZED` en signifie une à trois. Une racine sans relation sortante
est `INSUFFICIENT`. La détection d'un cycle prend priorité sur ces états. Ces
qualifications décrivent exclusivement la structure enregistrée : elles ne
valident ni la vérité des affirmations ni leur sens.

Exemple :

```json
{
  "claim_version_number": 1,
  "max_depth": 3
}
```

Le snapshot canonique est hashé en SHA-256, immuable et idempotent. Sa création
ajoute un événement d'audit protégé contre la modification et la suppression.
Il peut être relu avec `GET /impact-reports/{report_id}`.

## Nouveauté V1.01 : couverture et fraîcheur

`POST /claims/{id}/coverage-reports` produit un snapshot déterministe de la
couverture documentaire d'une version d'affirmation. Le serveur, et jamais le
client, calcule :

- le nombre de sources indépendantes de soutien et de contradiction;
- la couverture structurée `SUPPORTS` / `CONTRADICTS`;
- l'âge des sources à une date de référence et leur état `FRESH`, `STALE`,
  `UNKNOWN` ou `FUTURE_DATED`;
- les éléments dont la source ne peut plus être résolue;
- les éléments dont le SHA-256 ne correspond plus au contenu enregistré;
- la qualification prudente `CURRENT`, `STALE`, `INSUFFICIENT` ou `CONTESTED`.

Le snapshot JSON canonique reçoit son propre SHA-256. Deux demandes portant sur
la même version, la même date de référence et le même état documentaire
renvoient le même identifiant et le même hash. La ligne est protégée contre les
modifications et suppressions par des déclencheurs SQL, et sa création produit
un événement d'audit.

Une source indépendante correspond à un éditeur normalisé distinct. Sans
éditeur, KnowledgeForge utilise le nom d'hôte de l'URL, puis l'identifiant de
source en dernier recours. Le seuil de fraîcheur V1.01 est fixé à 365 jours et
ne peut pas être fourni par le client.

### Règles de qualification V1.01

- `CONTESTED` : au moins une source contradictoire exploitable est présente;
- `INSUFFICIENT` : intégrité incomplète ou moins de deux sources indépendantes
  de soutien exploitables;
- `STALE` : couverture suffisante, mais moins de deux sources indépendantes de
  soutien sont fraîches;
- `CURRENT` : au moins deux sources indépendantes de soutien sont fraîches,
  intègres, et aucune source exploitable ne contredit la version.

Ces états évaluent uniquement la couverture, la fraîcheur et l'intégrité des
documents enregistrés. Ils ne déclarent jamais qu'une affirmation est vraie.

Exemple :

```json
{
  "claim_version_number": 1,
  "as_of": "2026-08-22"
}
```

`as_of` est facultatif; lorsqu'il est absent, le serveur utilise sa date UTC.
La réponse peut être relue avec `GET /coverage-reports/{report_id}`.

## Garanties cumulatives

- une affirmation conserve toutes ses versions; aucune route ne réécrit l'historique;
- chaque extrait est normalisé puis identifié par un SHA-256 reproductible;
- un même extrait peut être dédupliqué et relié à plusieurs versions;
- les relations `SUPPORTS` et `CONTRADICTS` sont explicites et auditables;
- les contradictions sont détectées à partir des liens structurés, sans inventer une analyse sémantique;
- le verdict est limité à `SUPPORTED`, `CONTESTED` ou `INSUFFICIENT`;
- SQLite est prêt immédiatement, et un schéma PostgreSQL de migration est fourni.

## Démarrage rapide sous Windows / PowerShell 7

Prérequis : Python 3.10 ou supérieur.

```powershell
cd knowledgeforge
.\scripts\Start.ps1
```

Le script crée `.venv`, installe les dépendances et démarre l'API sur `http://127.0.0.1:8000`. La documentation interactive est accessible sur `http://127.0.0.1:8000/docs`.

Lancement manuel :

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:KNOWLEDGEFORGE_DB_PATH = "data/knowledgeforge.db"
python -m uvicorn app.main:app --reload
```

Tests :

```powershell
.\scripts\Test.ps1
```

## Parcours API minimal

1. `POST /sources` crée une source avec une fiabilité déclarée entre 0 et 1.
2. `POST /claims` crée une affirmation et sa version 1 immuable.
3. `POST /claims/{id}/versions` ajoute une nouvelle version. `expected_current_version` protège contre les écritures concurrentes.
4. `POST /claims/{id}/evidence` ajoute un extrait et son lien `SUPPORTS` ou `CONTRADICTS`.
5. `GET /evidence/{id}/verify` recalcule et vérifie son empreinte.
6. `GET /claims/{id}/assessment` calcule l'état documentaire de la version courante, ou `?version=1` pour une version historique.
7. `GET /claims/{id}/contradictions` expose les extraits contradictoires et les relations explicites entre affirmations.
8. `POST /claims/{id}/coverage-reports` fige un rapport de couverture et de fraîcheur.
9. `GET /coverage-reports/{id}` restitue exactement ce snapshot immuable.
10. `POST /claims/{id}/impact-reports` fige l'impact directionnel borné d'une version.
11. `GET /impact-reports/{id}` restitue exactement le snapshot d'impact immuable.
12. `POST /contradiction-dossiers` fige l'analyse documentaire de 2 à 20 versions.
13. `GET /contradiction-dossiers/{id}` restitue le dossier immuable sans résolution.
14. `POST /provenance-dossiers` fige l'indépendance documentaire de 1 à 20 versions.
15. `GET /provenance-dossiers/{id}` restitue exactement ce dossier immuable.
16. `GET /provenance-dossiers?limit=50` liste les dossiers récents et leurs métriques.
17. `POST /trust-alignment-dossiers` fige l'alignement de confiance de 1 à 20 versions.
18. `GET /trust-alignment-dossiers/{id}` restitue le dossier immuable.
19. `GET /trust-alignment-dossiers?limit=50` liste les dossiers récents.

`GET /health` et `GET /info` exposent tous deux la version interne `1.0.5`.

Exemple de source :

```json
{
  "url": "https://example.org/etude",
  "title": "Étude reproductible",
  "publisher": "Institut Exemple",
  "published_at": "2026-08-22T08:00:00Z",
  "reliability": 0.9
}
```

Exemple d'élément probant :

```json
{
  "source_id": "UUID_DE_LA_SOURCE",
  "excerpt": "Le résultat a été reproduit sur trois cohortes.",
  "locator": "page 14, tableau 3",
  "strength": 0.85,
  "relation": "SUPPORTS",
  "claim_version_number": 1
}
```

## Calcul documenté

Le poids d'un extrait est :

`poids = fiabilité_source × force_extrait`

Les poids indépendants d'un côté sont agrégés ainsi :

`score = 1 − produit(1 − poids)`

Une version est `SUPPORTED` seulement si :

- le score de soutien atteint `0,55`;
- au moins deux sources distinctes la soutiennent;
- le score contradictoire reste sous `0,35`;
- la marge soutien moins contradiction atteint `0,20`.

Elle est `CONTESTED` si la contradiction atteint `0,35`, ou si les deux côtés disposent d'une masse probante significative. Sinon elle est `INSUFFICIENT`.

Le `confidence_score` mesure le soutien documentaire après pénalité de contradiction et bonus plafonné de diversité des sources. Il ne s'agit ni d'une probabilité de vérité ni d'un remplacement de la revue humaine. Les seuils sont renvoyés dans chaque évaluation pour rendre la décision vérifiable.

Les relations directes entre affirmations signalent un conflit déclaré, mais ne modifient pas le score : seules les sources et les preuves liées le font. Ce choix évite qu'une simple déclaration interne devienne une fausse preuve.

## Intégrité et limites

L'empreinte couvre `source_id`, l'extrait normalisé Unicode NFKC, son localisateur normalisé et sa force déclarée. Une modification directe en base est détectée par la route de vérification. Le hash prouve l'intégrité du contenu enregistré, pas l'authenticité du site source.

La fiabilité et la force sont des métadonnées déclarées. En production, leur saisie doit être soumise à une politique, des rôles et une revue. Cette V1 ne télécharge pas les pages, ne signe pas cryptographiquement les réponses et ne prétend pas déterminer la vérité sémantique.

## Changelog

### V1.05 — 1.0.5

- confiance déclarée facultative et immuable par version d'affirmation;
- dossiers d'alignement fondés uniquement sur 1 à 20 versions persistées;
- confiance probante recalculée avec contrôle des hashes et de l'indépendance;
- couverture, provenance, contradictions, écart et pire claim exposés;
- cinq qualifications prudentes sans assimilation à la vérité;
- API POST/GET/liste, SQLite, PostgreSQL, OpenAPI et tests étendus;
- préparation Git/GitHub avec CI, contribution et exemples français.

### V1.04 — 1.0.4

- dossiers d'indépendance et de provenance fondés sur 1 à 20 versions explicites;
- regroupement strict par éditeur, domaine puis identifiant stable;
- contrôle d'intégrité et détection des contenus dupliqués entre sources;
- calcul de la source dominante, de sa part et de la couverture par version;
- qualifications prudentes avec seuil de trois sources et plafond dominant de 50 %;
- API POST/GET/liste, SQLite, PostgreSQL, OpenAPI et tests étendus.

### V1.03 — 1.0.3

- dossiers de contradictions fondés uniquement sur 2 à 20 versions explicites;
- vérification `CURRENT` / `FROZEN` et chemins directionnels bornés;
- agrégation intègre des preuves, de la couverture et de la fraîcheur disponibles;
- détection prudente des asymétries documentaires selon des seuils fixes;
- snapshots immuables, idempotents, hashés et audités;
- API, SQLite, PostgreSQL, OpenAPI et tests étendus.

### V1.02 — 1.0.2

- analyse d'impact directionnelle avec profondeur bornée et protection des cycles;
- chemins, branches, versions traversées et versions courantes explicités;
- réutilisation prudente des snapshots de couverture/fraîcheur disponibles;
- rapports d'impact immuables, idempotents, hashés et audités;
- journal d'audit rendu append-only;
- contrats SQLite, PostgreSQL et OpenAPI étendus.

### V1.01 — 1.0.1

- rapports immuables et idempotents de couverture/fraîcheur;
- calcul serveur des quatre qualifications prudentes;
- regroupement déterministe des sources indépendantes;
- détection des sources manquantes et empreintes invalides;
- audit et hash canonique de chaque snapshot;
- schémas SQLite, PostgreSQL et OpenAPI étendus.

### V1.00 — 1.0.0

- registre initial d'affirmations, versions, sources et preuves;
- évaluation documentaire et contradictions structurées;
- intégrité SHA-256 des éléments probants.

## Fichiers principaux

- `app/main.py` : routes FastAPI et fabrique d'application;
- `app/service.py` : versionnage, couverture, impact, contradictions, provenance et confiance;
- `app/db.py` : schéma SQLite et transactions;
- `openapi.yaml` : contrat OpenAPI portable;
- `schema/postgresql.sql` : schéma PostgreSQL équivalent;
- `tests/test_api.py` : tests API, intégrité, verdicts et historique.
- `docs/USAGE_EXAMPLES.md` : installation PowerShell et exemples API complets;
- `.github/workflows/tests.yml` : intégration continue GitHub Actions.

Licence : starter interne, à adapter à la politique du projet qui l'intègre.
