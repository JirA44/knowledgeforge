# Contribuer à KnowledgeForge

Merci de contribuer à KnowledgeForge. Le projet privilégie la traçabilité, les
résultats reproductibles et les qualifications prudentes.

## Prérequis

- Python 3.10 ou supérieur;
- PowerShell 7 recommandé sous Windows;
- Git 2.40 ou supérieur.

## Installation locale sous PowerShell

```powershell
git clone URL_DU_DEPOT knowledgeforge
cd knowledgeforge
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Avant de proposer une modification

```powershell
python -m pytest
python -m compileall -q app tests
```

Vérifiez également que :

- `VERSION`, `pyproject.toml`, `app/__init__.py`, `/health`, `/info` et OpenAPI
  exposent la même version;
- `openapi.yaml` correspond à `app.openapi()`;
- les nouveaux modèles Pydantic utilisent `extra="forbid"`;
- aucun verdict, score final ou qualification calculable n'est accepté depuis
  le client;
- les snapshots restent immuables, idempotents, hashés et audités;
- les tests historiques continuent de réussir.

## Règles de développement

1. Ne réécrivez jamais une version d'affirmation existante.
2. N'inventez ni provenance, ni confiance, ni vérité manquante.
3. Documentez les seuils fixes et leurs limites.
4. Ajoutez des tests pour les cas nominal, insuffisant, intégrité invalide,
   idempotence, immuabilité et payload strict.
5. Ne committez jamais de base SQLite, environnement virtuel, secret ou cache.

## Commits et pull requests

- utilisez un message court et explicite, par exemple
  `feat: ajoute le dossier d'alignement de confiance`;
- décrivez le comportement, les migrations et les tests exécutés;
- liez une issue lorsque cela est pertinent;
- n'incluez pas de changement étranger à la fonctionnalité proposée.

La CI GitHub teste plusieurs versions de Python et vérifie le contrat OpenAPI.
