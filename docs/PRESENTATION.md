# KnowledgeForge — Présentation complète

## Présentation
knowledgeforge est un registre immuable, hashé (SHA-256), auditable et rejouable.

## À quoi ça sert ? (problèmes réglés)
- **Documentation trouée sans le savoir** → résolu par un dossier déterministe, ordre-indépendant
- **FAQ qui ne couvre pas les questions réelles** → résolu par un dossier déterministe, ordre-indépendant
- **Onboarding qui oublie 30% des procédures** → résolu par un dossier déterministe, ordre-indépendant

## Cas d'utilisation concrets
- Wiki entreprise: lister les lacunes documentaires par dossier
- Support client: couvrir 100 questions par 40 articles
- Produit: vérifier que chaque feature a sa doc

## Exemples d'utilisation (API)
```bash
curl -X POST http://localhost:8000/v1/gap-dossiers -d '{"knowledge_ids": [...] }'
# → { "qualification": "COMPLETE|GAPPED|INSUFFICIENT|INCOMPATIBLE", "coverage_ratio": 0.94, ... }
```

## À quoi ça pourrait servir (futur / possibilités)
- Knowledge gap analytics
- Assistant qui propose quoi documenter en priorité
- Certification documentation

## Pour qui ?
Devs, auditeurs, ops, chercheurs — qui ont besoin d'une preuve opposable, pas d'un verdict déclaratif.

## Problèmes réglés (détaillés)
- **Knowledgeforge** → - Preuve / dossier / trace non opposable → résolu par dossier immuable et hash SHA-256
- **Knowledgeforge** → - Verdict déclaratif sans justification → le dossier expose obligations, fournisseurs et ratios
- **Knowledgeforge** → - Chaînage caché ou lacune invisible → serveur recharge et recalcule indépendamment du client
- **Knowledgeforge** → - Tiers qui ne peut pas relancer → le dossier est public et rejouable sans clé client

## Exemples d'utilisation (scénarios réels)
- **Wiki : lacunes par dossier détectées** → le dossier sert de preuve technique (pas d'autorité déclarative)
- **Support : 100 questions / 40 articles** → le dossier sert de preuve technique (pas d'autorité déclarative)
- **Produit : chaque feature documentée** → le dossier sert de preuve technique (pas d'autorité déclarative)


