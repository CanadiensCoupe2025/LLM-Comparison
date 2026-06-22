# Prompt Styles — Definitions (SCRUM-37)

The prompt-style benchmark (`evaluator/datasets/prompt_style_benchmark.yaml`)
compares how each model's answer **quality** changes when the *same* base task
is framed in different prompting styles. Only the framing varies within a
benchmark group, so any score delta is attributable to the style.

This document **defines** the five styles. Its companion,
[`prompt_styles_sourced.md`](prompt_styles_sourced.md), maps each style to a
canonical, license-clear evaluation dataset for sourcing verbatim task items.

> **Scoring.** Each styled response is graded by the LLM judge (SCRUM-23,
> Gemini) on a 0–5 scale and persisted with its `prompt_style` tag, then
> aggregated by `(model, style)` in the `style_metrics` view and the Grafana
> panel.

---

## The five styles

| Style | One-line definition | What it tests |
|---|---|---|
| **zero-shot** | The task alone, with no examples or scaffolding. | The model's baseline ability on the raw task. |
| **few-shot** | The task preceded by a few worked examples (input → solution). | Whether the model picks up the demonstrated pattern / format. |
| **instructional** | The task plus explicit step-by-step instructions on *how* to solve it. | Procedure-following vs shortcutting. |
| **contextual** | The task wrapped in supplied background the model must ground its answer in. | Faithfulness to provided context (and abstention when the answer isn't there). |
| **role-based** | The identical task framed with an expert persona ("You are a …"). | Whether persona framing changes answer quality. |

---

### 1. Zero-shot
The task is presented with **no examples and no extra guidance** — just the
question. This is the control: it measures the model's unaided performance and
serves as the baseline every other style is compared against.

*Example framing:* `{question}`

### 2. Few-shot
The task is preceded by a **small number of worked examples** (typically 2–3)
showing the desired input→output format, often with the reasoning shown
(chain-of-thought). It tests whether a model can imitate a demonstrated method.
The **few-shot vs zero-shot delta per model** is the headline signal of the
benchmark.

*Example framing:*
```
Q: <example 1>
A: <worked solution 1>

Q: <example 2>
A: <worked solution 2>

Q: {question}
A:
```

### 3. Instructional
The task is accompanied by **explicit, step-by-step instructions** describing
the procedure to follow. Unlike few-shot (which *shows* examples), instructional
*tells* the model how to proceed. It probes faithful procedure-following versus
jumping to an answer.

*Example framing:*
```
Solve step by step: first …, then …, finally state the answer on its own line.

{question}
```

### 4. Contextual
The task is **wrapped in supplied background** (e.g. a passage) that the model
must use as the sole basis for its answer. It tests grounding: a context-faithful
model answers only from the provided material and **abstains** when the answer
isn't present, rather than falling back on parametric knowledge.

*Example framing:*
```
Answer using only the passage below. If it doesn't contain the answer,
reply exactly: "Not in the passage."

Passage: {context}
Question: {question}
```

### 5. Role-based
The **identical** task is framed with an **expert persona** ("You are a principal
engineer who …"). Because only the persona changes versus the zero-shot version,
any movement in quality is attributable purely to the role framing.

*Example framing:*
```
You are a {expert role} who cares about {priority}. {task}
```

---

## How styles map to the benchmark
Each style appears as one or more cases inside a `group` in
`prompt_style_benchmark.yaml`. A group shares a base task across ≥2 contrasting
styles (e.g. `math-train` in zero-shot / few-shot / instructional), and the
benchmark as a whole spans ≥3 task categories (reasoning, extraction, code).
See [`prompt_styles_sourced.md`](prompt_styles_sourced.md) for sourcing the
underlying task items from canonical datasets.
