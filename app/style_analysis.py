"""Style-adjusted scoring (arena-hard style control, adapted to absolute judging).

Arena-hard removes response-style bias by adding style features as covariates in
a *pairwise* Bradley-Terry model. LLMeter judges *absolutely* (a 0–5 score per
answer), so we adapt: fit one pooled OLS of judge_score on the style covariates
plus a per-model fixed effect, then report each model's score evaluated at the
GLOBAL-MEAN style profile — "what would this model score if its answers had
average length and formatting?".

Two outputs:
  1. per-feature slopes — is the JUDGE itself rewarding length/formatting?
  2. per-model raw mean vs style-adjusted score — does the ranking survive once
     length/markdown are held constant?

LIMITS (see also db/012_style_confound_view.sql): this is associational, not a
causal de-bias. Length is confounded with difficulty (hard prompts legitimately
run longer), and absolute judging can't difference difficulty out the way a
pairwise design does. Treat the adjusted score as a diagnostic.

Replicates: rows are aggregated to per-(model, case) MEANS first, so repeated
samples (`sample_idx`) don't masquerade as independent observations.

Run:  python -m app.style_analysis [--run-id N] [--exclude-code]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np
import psycopg

from app.logging_setup import configure_logging

# Style covariates held at their global mean to compute the adjusted score.
# Length comes first as log1p(output_tokens); the rest are raw markdown counts.
FEATURE_NAMES = [
    "log1p_tokens",
    "headers",
    "bold",
    "ordered",
    "unordered",
    "code_blocks",
]


@dataclass(frozen=True)
class Observation:
    """One per-(model, case) mean across its samples."""

    model: str
    case_id: str
    judge_score: float       # 0–5
    output_tokens: float
    headers: float
    bold: float
    ordered: float
    unordered: float
    code_blocks: float

    def feature_vector(self) -> list[float]:
        return [
            math.log1p(self.output_tokens),
            self.headers,
            self.bold,
            self.ordered,
            self.unordered,
            self.code_blocks,
        ]


@dataclass
class StyleModel:
    models: list[str]                       # in fixed-effect order (models[0] = reference)
    feature_slopes: dict[str, float]        # judge's sensitivity to each style feature
    raw_mean: dict[str, float]              # mean judge_score per model
    adjusted: dict[str, float]              # score at global-mean style profile
    n_per_model: dict[str, int]
    rank_deficient: bool


def fit_style_model(observations: list[Observation]) -> StyleModel:
    """Pooled OLS: judge_score ~ 1 + style features + per-model fixed effects.

    The first model encountered is the reference (its fixed effect folds into the
    intercept). The adjusted score holds every style covariate at the global mean,
    so models differ only by their fixed effect — the style-controlled ranking.
    """
    if not observations:
        raise ValueError("no observations to fit")

    models = sorted({o.model for o in observations})
    model_index = {m: i for i, m in enumerate(models)}
    n_features = len(FEATURE_NAMES)
    # Columns: [intercept, *features, *model_dummies(for models[1:])].
    n_dummies = max(len(models) - 1, 0)
    n_cols = 1 + n_features + n_dummies

    X = np.zeros((len(observations), n_cols), dtype=float)
    y = np.zeros(len(observations), dtype=float)
    for r, obs in enumerate(observations):
        X[r, 0] = 1.0
        X[r, 1 : 1 + n_features] = obs.feature_vector()
        idx = model_index[obs.model]
        if idx > 0:  # reference model (idx 0) carried by the intercept
            X[r, 1 + n_features + (idx - 1)] = 1.0
        y[r] = obs.judge_score

    beta, _residuals, rank, _sv = np.linalg.lstsq(X, y, rcond=None)
    rank_deficient = rank < n_cols

    feature_slopes = {
        name: float(beta[1 + j]) for j, name in enumerate(FEATURE_NAMES)
    }

    # Global-mean style profile (the covariate values every model is evaluated at).
    feat_matrix = np.array([o.feature_vector() for o in observations], dtype=float)
    grand_mean = feat_matrix.mean(axis=0)
    base = float(beta[0]) + float(np.dot(beta[1 : 1 + n_features], grand_mean))

    adjusted: dict[str, float] = {}
    for m in models:
        idx = model_index[m]
        fixed_effect = 0.0 if idx == 0 else float(beta[1 + n_features + (idx - 1)])
        adjusted[m] = base + fixed_effect

    raw_mean: dict[str, float] = {}
    n_per_model: dict[str, int] = {}
    for m in models:
        scores = [o.judge_score for o in observations if o.model == m]
        raw_mean[m] = sum(scores) / len(scores)
        n_per_model[m] = len(scores)

    return StyleModel(
        models=models,
        feature_slopes=feature_slopes,
        raw_mean=raw_mean,
        adjusted=adjusted,
        n_per_model=n_per_model,
        rank_deficient=rank_deficient,
    )


def load_observations(
    conn, *, run_id: Optional[int] = None, exclude_code: bool = False
) -> list[Observation]:
    """Per-(model, case) means of judged rows that have style features.

    Aggregating in SQL collapses `sample_idx` replicates into one observation per
    pair — the de-pseudo-replication step the regression needs.
    """
    where = ["res.judge_score IS NOT NULL", "res.resp_style_headers IS NOT NULL"]
    params: list = []
    if run_id is not None:
        where.append("res.run_id = %s")
        params.append(run_id)
    if exclude_code:
        # Code-dominated answers: markdown penalties are meaningless for them.
        where.append("res.resp_style_code_blocks = 0")

    sql = f"""
        SELECT m.name, res.case_id,
               AVG(res.judge_score)::float8,
               AVG(res.output_tokens)::float8,
               AVG(res.resp_style_headers)::float8,
               AVG(res.resp_style_bold)::float8,
               AVG(res.resp_style_ordered)::float8,
               AVG(res.resp_style_unordered)::float8,
               AVG(res.resp_style_code_blocks)::float8
        FROM results res
        JOIN models m ON m.id = res.model_id
        WHERE {' AND '.join(where)}
        GROUP BY m.name, res.case_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [Observation(*row) for row in rows]


def format_report(model: StyleModel) -> str:
    lines: list[str] = []
    lines.append("Judge sensitivity to response style (OLS slopes):")
    lines.append("  (positive ⇒ the judge rewards more of this — a style bias)")
    for name in FEATURE_NAMES:
        lines.append(f"    {name:<14} {model.feature_slopes[name]:+.4f}")
    lines.append("")
    lines.append("Per-model raw vs style-adjusted score (0–5):")
    lines.append(f"  {'model':<22} {'raw':>6} {'adjusted':>9} {'Δ':>7}  n")
    for m in sorted(model.models, key=lambda k: model.adjusted[k], reverse=True):
        raw, adj, n = model.raw_mean[m], model.adjusted[m], model.n_per_model[m]
        lines.append(f"  {m:<22} {raw:6.2f} {adj:9.2f} {adj - raw:+7.2f}  {n}")
    if model.rank_deficient:
        lines.append("")
        lines.append("⚠ design matrix is rank-deficient (too few/!varied cases) — "
                     "adjusted scores are unreliable. Collect more judged data.")
    return "\n".join(lines)


def _connect_db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set — check your .env or compose env_file.")
    return psycopg.connect(url)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="style_analysis",
        description="Style-adjusted scoring: control judge scores for length/markdown.",
    )
    parser.add_argument("--run-id", type=int, default=None,
                        help="Restrict to one run (default: all judged rows).")
    parser.add_argument("--exclude-code", action="store_true",
                        help="Drop code-dominated answers (resp_style_code_blocks > 0).")
    parser.add_argument("--min-obs", type=int, default=20,
                        help="Refuse to fit below this many observations (default: 20).")
    args = parser.parse_args(argv)
    configure_logging()

    conn = _connect_db()
    try:
        obs = load_observations(conn, run_id=args.run_id, exclude_code=args.exclude_code)
    finally:
        conn.close()

    if len(obs) < args.min_obs:
        print(
            f"Only {len(obs)} judged observation(s) with style features "
            f"(need ≥ {args.min_obs}). Run more judged evals first.",
            file=sys.stderr,
        )
        return 1

    model = fit_style_model(obs)
    print(f"Fitted on {len(obs)} per-(model,case) observations across "
          f"{len(model.models)} models.\n")
    print(format_report(model))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
