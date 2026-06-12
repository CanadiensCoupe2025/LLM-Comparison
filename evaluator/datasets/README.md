# `evaluator/datasets/` — Jeux de prompts d'évaluation

Datasets versionnés consommés par le runner (SCRUM-19) et la pipeline
de régression CI (SCRUM-25). Chaque fichier est une suite de cas
reproductibles : un `prompt`, un `expected` et une `rationale`.

> **Pour le modèle d'exécution complet (flux, scoring, gates),
> voir [`docs/prompt_check_process.md`](../../docs/prompt_check_process.md).**

---

## Versions

| Fichier                  | Version | Cas | Statut | Notes                                                  |
|--------------------------|---------|-----|--------|--------------------------------------------------------|
| `regression_v1.yaml`     | 1       | 19  | Gelé   | Pass/fail uniforme. Conservé pour rejeux historiques. |
| `regression_v2.yaml`     | 2       | 20  | Actif  | Scoring hybride, taxonomie `canary/main/edge`, ajout `code_test`. |

Le v1 reste utilisable (`--dataset evaluator/datasets/regression_v1.yaml`)
mais ne doit plus être modifié. Toute nouvelle évolution se fait sur v2
(ou un futur v3).

---

## Schéma d'un cas (v2)

```yaml
- id:        kebab-case-stable           # unique, jamais réutilisé
  category:  factual | math | reasoning | code | summarization |
             extraction | instruction | translation | classification |
             ambiguity | robustness | safety | hallucination
  kind:      canary | main | edge
  prompt:    "Texte exact envoyé au modèle"
  expected:                              # mono-check OU multi-check (clé `checks`)
    check:       exact | contains | not_contains | regex | json_schema |
                 code_test | refusal | judge
    threshold:   0..1                    # défaut : 1.0 (déterministes), 0.7 (judge)
    weight:      number                  # défaut : 1
    # …champs spécifiques au check (cf. table ci-dessous)
  rationale: "Une ligne — pourquoi ce cas discrimine."
```

Pour les cas multi-check (ex. `code-fix-style`) on remplace `expected.check`
par `expected.checks: [ ... ]` avec un objet par sous-check ; voir
[`prompt_check_process.md` §6](../../docs/prompt_check_process.md#6-cas-multi-check-hybride).

---

## Taxonomie `kind`

| `kind`   | Rôle                                                                 | Effet en CI                                                |
|----------|----------------------------------------------------------------------|------------------------------------------------------------|
| `canary` | Cas trivial déterministe — santé du pipeline.                        | **Bloquant** : un seul canari FAIL fait échouer le run.    |
| `main`   | Cas discriminant représentatif d'un usage réel.                      | Compte dans le gate déterministe et/ou la métrique qualité. |
| `edge`   | Cas limite (prémisse fausse, injection, contradiction, refus, etc.). | Contribue à la métrique de qualité ; rarement bloquant.    |

Cible de répartition en v2 : ~3 canaris, ~9 main, ~8 edge.

---

## Types de check

| `check`        | Champs spécifiques                  | Score                       | `threshold` par défaut | Sert de gate ? |
|----------------|-------------------------------------|-----------------------------|------------------------|-----------------|
| `exact`        | `value: string`                     | 0 ou 1 (binaire)            | 1.0                    | Oui             |
| `contains`     | `value: [string, …]`                | 0 ou 1 — toutes les valeurs (ET) | 1.0               | Oui             |
| `not_contains` | `value: [string, …]`                | 0 ou 1 — aucune des valeurs | 1.0                    | Oui             |
| `regex`        | `pattern: string`                   | 0 ou 1 — IGNORECASE         | 1.0                    | Oui             |
| `json_schema`  | `schema: object`                    | 0 ou 1 — validation stricte | 1.0                    | Oui             |
| `code_test`    | `language`, `function`, `tests: [{args, expected}]` | fraction des asserts OK (0..1) | 1.0           | Oui             |
| `judge`        | `description: string` (rubrique)    | Score continu 0..1 par juge | 0.7                    | Non — métrique qualité |
| `refusal`      | `description: string`               | Score continu 0..1 par juge | 0.7                    | Non — métrique qualité |

Normalisation des checks textuels (`exact`, `contains`, `not_contains`,
`regex`) : trim + insensibles à la casse par défaut, appliqués côté runner.

**Exception `case_sensitive: true`** — un check peut surcharger ce défaut
quand l'intention du test EST de vérifier la casse (sinon la normalisation
annulerait l'évaluation). Voir `edge-unicode-accents` dans v2 :

```yaml
expected:
  check: contains
  value: ["ÉTÉ", "MONTRÉAL"]
  case_sensitive: true   # sans cet override, "été à montréal" passerait
```

---

## Scoring hybride (résumé)

Chaque cas produit deux choses, qu'on stocke et qu'on n'agrège **pas**
de la même façon :

1. **Un score continu ∈ [0,1]** — sert aux moyennes, au dashboard et aux
   alertes de régression. On garde l'information de "presque-passé".
2. **Un verdict ∈ {PASS, FAIL, ERROR}** — sert au gate CI.
   - `PASS` si `score ≥ threshold`.
   - `FAIL` si `score < threshold`.
   - `ERROR` réservé aux échecs **techniques** (timeout, clé manquante,
     réponse inparsable). Une mauvaise réponse n'est pas une `ERROR`.

Cas multi-check : score = moyenne pondérée des sous-scores ; verdict =
PASS uniquement si **tous** les sous-verdicts sont PASS. Un cas peut
donc avoir un score de 0,83 et un verdict FAIL — c'est voulu.

Deux familles de métriques au niveau du dataset :

- **Gate déterministe** (bloquant en CI) : 100 % des canaris en PASS,
  + taux de PASS des checks déterministes ≥ seuil produit.
- **Qualité judge** (suivi continu) : score moyen des checks `judge` et
  `refusal`. Une régression ≥ X points déclenche une alerte Grafana
  (SCRUM-31), pas un échec CI.

---

## Convention pour le juge

- Modèle juge **épinglé** dans la config du runner ; changer de juge
  invalide la comparaison historique (à versionner explicitement).
- Sortie attendue (JSON strict) :
  `{ "score": float ∈ [0,1], "reasoning": string }`.
- **Jamais un modèle ne se juge lui-même** (biais d'auto-préférence).
  Si le modèle évalué est aussi le juge configuré, le runner bascule
  sur un juge secondaire.

---

## Conventions pour ajouter un cas

- `id` stable et unique, en kebab-case ; ne pas réutiliser un `id`
  retiré (briserait l'historique des runs).
- `rationale` en une seule ligne — pourquoi ce cas existe / discrimine.
- Préférer un check déterministe quand c'est possible (moins cher,
  reproductible). Réserver `judge` aux jugements sémantiques.
- Pour `judge`, écrire la rubrique du point de vue de l'évaluateur :
  ce qu'il doit chercher, ce qui doit faire baisser le score, et
  surtout les **réponses incorrectes plausibles** à pénaliser.
- Tester localement avant de committer : `regression_vN.yaml` doit
  parser sans erreur (`python -c "import yaml; yaml.safe_load(open(...))"`).
