# Exemples d'utilisation de KnowledgeForge V1.07

## 1. Installation sous Windows et PowerShell 7

```powershell
cd knowledgeforge
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
$env:KNOWLEDGEFORGE_DB_PATH = "data/knowledgeforge.db"
python -m uvicorn app.main:app --reload
```

L'API répond sur `http://127.0.0.1:8000`. Swagger UI est disponible sur
`http://127.0.0.1:8000/docs` et le schéma runtime sur `/openapi.json`.

Le démarrage automatisé reste possible avec :

```powershell
.\scripts\Start.ps1
```

## 2. Créer une affirmation avec confiance déclarée

```powershell
$claim = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/claims" `
  -ContentType "application/json" `
  -Body (@{
    statement = "Le protocole est reproductible dans ce contexte."
    context = "Protocole A, population définie"
    declared_confidence = 0.72
  } | ConvertTo-Json)

$claimVersionId = $claim.current_version.id
```

`declared_confidence` est facultative. Si elle est absente, un dossier
d'alignement calcule la confiance probante disponible mais reste
`INSUFFICIENT`.

## 3. Ajouter deux sources indépendantes

```powershell
$sourceA = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sources" `
  -ContentType "application/json" `
  -Body (@{
    url = "https://institut-a.example/etude"
    title = "Étude A"
    publisher = "Institut A"
    published_at = "2026-08-01T00:00:00Z"
    reliability = 0.90
  } | ConvertTo-Json)

$sourceB = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/sources" `
  -ContentType "application/json" `
  -Body (@{
    url = "https://institut-b.example/replication"
    title = "Réplication B"
    publisher = "Institut B"
    published_at = "2026-08-02T00:00:00Z"
    reliability = 0.90
  } | ConvertTo-Json)

foreach ($source in @($sourceA, $sourceB)) {
  Invoke-RestMethod -Method Post `
    -Uri "http://127.0.0.1:8000/claims/$($claim.id)/evidence" `
    -ContentType "application/json" `
    -Body (@{
      source_id = $source.id
      excerpt = "Résultat reproduit selon le protocole enregistré."
      locator = "section résultats"
      strength = 0.90
      relation = "SUPPORTS"
      claim_version_number = 1
    } | ConvertTo-Json)
}
```

## 4. Créer un rapport de couverture

```powershell
$coverage = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/claims/$($claim.id)/coverage-reports" `
  -ContentType "application/json" `
  -Body (@{
    claim_version_number = 1
    as_of = "2026-08-24"
  } | ConvertTo-Json)
```

## 5. Créer un dossier de provenance

```powershell
$provenance = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/provenance-dossiers" `
  -ContentType "application/json" `
  -Body (@{ claim_version_ids = @($claimVersionId) } | ConvertTo-Json)
```

## 6. Créer un dossier d'alignement de confiance

```powershell
$alignment = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/trust-alignment-dossiers" `
  -ContentType "application/json" `
  -Body (@{ claim_version_ids = @($claimVersionId) } | ConvertTo-Json)

$alignment.qualification
$alignment.worst_claim
$alignment.claim_measures
```

Le corps n'accepte que `claim_version_ids`. Les résultats, seuils, scores,
écarts et qualifications sont calculés par le serveur.

## 7. Relire et lister les dossiers

```powershell
$id = $alignment.id
Invoke-RestMethod "http://127.0.0.1:8000/trust-alignment-dossiers/$id"
Invoke-RestMethod "http://127.0.0.1:8000/trust-alignment-dossiers?limit=20"
```

## 8. Interprétation prudente

- `ALIGNED` signifie seulement que l'écart documentaire est dans la tolérance;
- `OVERCONFIDENT` et `UNDERCONFIDENT` comparent deux mesures, pas la vérité;
- `INSUFFICIENT` évite d'inventer une confiance manquante;
- `INCOMPATIBLE` signale une contradiction structurée directe;
- un snapshot ancien reste inchangé même si de nouvelles preuves arrivent.

## 9. Créer un dossier d'évolution chronologique

Après avoir créé au moins deux versions et lié leurs preuves respectives :

```powershell
$evolution = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/claim-evolution-dossiers" `
  -ContentType "application/json" `
  -Body (@{
    claim_version_ids = @($versionV1.id, $versionV2.id, $versionV3.id)
  } | ConvertTo-Json)

$evolution.qualification
$evolution.transitions
$evolution.worst_transition

Invoke-RestMethod `
  "http://127.0.0.1:8000/claim-evolution-dossiers/$($evolution.id)"
Invoke-RestMethod `
  "http://127.0.0.1:8000/claim-evolution-dossiers?limit=20"
```

Le client ne fournit aucun score, delta, verdict ou qualification. `STABLE`,
`ESCALATING` et `RESOLVING` décrivent uniquement les artefacts documentaires
persistés; ils ne prouvent pas la vérité de l'affirmation.

## 10. Créer un dossier de lacunes de preuves

```powershell
$lacunes = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/v1/evidence-gap-dossiers" `
  -ContentType "application/json" `
  -Body (@{
    claim_id = $claim.id
    claim_version_ids = @($versionV1.id, $versionV2.id)
    evidence_ids = @($preuveA.id, $preuveB.id)
  } | ConvertTo-Json)

$lacunes.qualification
$lacunes.versions
$lacunes.transitions
$lacunes.worst_transition

Invoke-RestMethod `
  "http://127.0.0.1:8000/v1/evidence-gap-dossiers/$($lacunes.id)"
Invoke-RestMethod `
  "http://127.0.0.1:8000/v1/evidence-gap-dossiers?limit=20"
```

Omettre `evidence_ids` recharge toutes les preuves liées aux versions
sélectionnées. `COVERED` signifie seulement qu'aucune lacune documentaire n'a
été détectée selon les règles fixes; ce n'est pas une preuve de vérité.
