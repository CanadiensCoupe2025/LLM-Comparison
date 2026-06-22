# Prompt Styles — Canonical Sourced Examples

Companion to `prompt_styles.md`. Same five styles, but here the **task content is drawn from established, license-clear evaluation datasets** instead of hand-written examples.

Why this matters for SCRUM-37: copy-pasting prompts out of blog posts gives you examples of unknown provenance, unknown license, and unknown difficulty calibration. Sourcing tasks from canonical eval datasets gives you the opposite — items that are already validated to differentiate models, are citeable, and come with reference answers and known licenses. The recommended pattern is to **load the exact items from the official source at runtime** (HuggingFace `datasets` or the upstream repo) and apply the five style framings as wrappers. The framing is the only thing that should vary within a benchmark group; the task text comes verbatim from the source.

> **Provenance / license note.** Licenses below were current at the time of writing but can change. Verify the `LICENSE` file in each upstream repo before redistributing any items inside LLMeter, and keep the dataset citation with any stored copy. The style-wrapper templates in this doc are original; the bracketed `{…}` placeholders are where the sourced item is injected at load time.

---

## Source map

| Style | Canonical source | Task category | License | Origin |
|---|---|---|---|---|
| Zero-shot | GSM8K (test split) | Reasoning (math) | MIT | `openai/gsm8k` |
| Few-shot | GSM8K + CoT exemplars | Reasoning (math) | MIT | Wei et al. 2022 / `openai/gsm8k` |
| Instructional | BIG-Bench Hard | Reasoning (multi-step) | Apache-2.0 (BIG-Bench) | `suzgun/BIG-Bench-Hard` |
| Contextual | SQuAD 2.0 | Extraction / reading comp. | CC BY-SA 4.0 | `rajpurkar/squad_v2` |
| Role-based | HumanEval | Code generation | MIT | `openai/human-eval` |

This set already satisfies the story's "≥3 task categories" requirement (reasoning, extraction, code generation). Awesome-ChatGPT-Prompts (CC0-1.0, `f/awesome-chatgpt-prompts`) is listed at the end as an unrestricted source of role/persona framings if you want more "Act as…" material.

---

## 1. Zero-shot — GSM8K (MIT)

**Source.** `openai/gsm8k`, test split (1,319 problems). Grade-school math word problems requiring 2–8 arithmetic steps, each with a reference final answer. This is *the* canonical zero-shot reasoning probe, and the zero-shot chain-of-thought trigger ("Let's think step by step", Kojima et al. 2022) was popularized on exactly this kind of task.

**Load the real items:**

```python
from datasets import load_dataset
gsm8k = load_dataset("openai/gsm8k", "main", split="test")
problem = gsm8k[0]["question"]   # verbatim source text
answer  = gsm8k[0]["answer"]     # reference solution incl. final number after "####"
```

**Style wrapper (original framing, sourced task):**

```text
{question}
```

That is the whole point of zero-shot — the dataset question is presented with no scaffolding. For a zero-shot-CoT variant, append the standard trigger:

```text
{question}

Let's think step by step.
```

*Discriminating because:* GSM8K final-answer accuracy swings widely between plain zero-shot and zero-shot-CoT, and between models — which is precisely the per-model effect SCRUM-37 measures. Score with exact-match on the number after `####`.

---

## 2. Few-shot — GSM8K with chain-of-thought exemplars (MIT)

**Source.** The same `openai/gsm8k` items, but preceded by a small number of worked solutions. The canonical few-shot setup is the 8-shot chain-of-thought prompt from Wei et al. 2022 ("Chain-of-Thought Prompting Elicits Reasoning in Large Language Models"); the exemplars are themselves GSM8K-style problems with step-by-step solutions. Pull a handful of `train`-split items to serve as the shots so your test items stay unseen.

**Load and build shots from the source:**

```python
from datasets import load_dataset
train = load_dataset("openai/gsm8k", "main", split="train")
shots = train.select(range(3))     # 3 verbatim worked examples
test  = load_dataset("openai/gsm8k", "main", split="test")
target = test[0]["question"]
```

**Style wrapper (original framing, sourced tasks):**

```text
Q: {shot_1_question}
A: {shot_1_answer}

Q: {shot_2_question}
A: {shot_2_answer}

Q: {shot_3_question}
A: {shot_3_answer}

Q: {target_question}
A:
```

*Discriminating because:* the worked solutions demonstrate the multi-step reasoning format. Models that genuinely pick up the demonstrated chain-of-thought pattern improve sharply over their own zero-shot baseline; models that don't, barely move. Comparing the few-shot vs. zero-shot delta per model is the headline number for this group.

---

## 3. Instructional — BIG-Bench Hard (Apache-2.0)

**Source.** `suzgun/BIG-Bench-Hard` (Suzgun et al. 2022) — 23 tasks from BIG-Bench that were hard for models, distributed with their official prompts. Tasks like `tracking_shuffled_objects`, `logical_deduction`, and `multistep_arithmetic` come with explicit step-by-step instruction prompts, which is exactly the instructional style. The upstream prompts encode the procedure; you supply the task input.

**Load the real items (repo JSON):**

```python
# Each task ships as data/<task>.json with {"input", "target"} pairs,
# plus a cot-prompts/<task>.txt instructional prompt.
import json, urllib.request
base = "https://raw.githubusercontent.com/suzgun/BIG-Bench-Hard/main"
task = "logical_deduction_three_objects"
items = json.load(urllib.request.urlopen(f"{base}/bbh/{task}.json"))["examples"]
instr = urllib.request.urlopen(f"{base}/cot-prompts/{task}.txt").read().decode()
```

**Style wrapper (original framing, sourced task + sourced instructional prompt):**

```text
{cot_instruction_prompt}

Q: {task_input}
A: Let's work through this step by step.
```

*Discriminating because:* BBH was selected for items where models lagged human raters, and where step-by-step instruction prompting produces large, uneven gains across models. Faithful procedure-following vs. shortcutting is directly observable. Score with exact-match on `target`.

> Verify the `LICENSE` in the mirror you pull from — BIG-Bench is Apache-2.0; confirm before redistributing items inside LLMeter.

---

## 4. Contextual — SQuAD 2.0 (CC BY-SA 4.0)

**Source.** `rajpurkar/squad_v2` — questions answered from a provided Wikipedia passage, including unanswerable questions whose correct response is to abstain. The passage *is* the context wrapper, making this the natural fit for the contextual style: the model must ground its answer in supplied background rather than prior knowledge.

**Load the real items:**

```python
from datasets import load_dataset
squad = load_dataset("rajpurkar/squad_v2", split="validation")
context  = squad[0]["context"]    # verbatim background passage
question = squad[0]["question"]
answers  = squad[0]["answers"]     # empty list == unanswerable
```

**Style wrapper (original framing, sourced context + question):**

```text
Read the passage below and answer the question using only information it
contains. If the passage does not contain the answer, reply exactly:
"Not in the passage."

Passage:
{context}

Question: {question}
Answer:
```

*Discriminating because:* SQuAD 2.0's unanswerable questions are the trap — a context-faithful model abstains, while a model that leans on parametric knowledge hallucinates a plausible answer. That abstention behavior varies a lot between models and is exactly what the contextual style should surface. Score with the official SQuAD F1/exact-match (and abstention correctness on the no-answer subset).

> CC BY-SA 4.0 requires attribution and share-alike. Keep the SQuAD citation with any stored items and note the license in the benchmark metadata.

---

## 5. Role-based — HumanEval under an expert persona (MIT)

**Source.** `openai/human-eval` — 164 Python problems, each a function signature plus docstring with examples; the model must produce a passing implementation, scored by the bundled unit tests via pass@k. HumanEval is normally run with no persona, which makes it a clean control: wrap the *identical* prompt in an expert role and measure whether the persona changes pass rate or code quality.

**Load the real items:**

```python
# From the openai/human-eval repo (read_problems / data/HumanEval.jsonl.gz)
from human_eval.data import read_problems
problems = read_problems()
key = list(problems)[0]
spec = problems[key]["prompt"]    # verbatim signature + docstring
test = problems[key]["test"]      # bundled unit tests
```

**Style wrapper (original framing, sourced spec):**

```text
You are a principal software engineer who writes production-grade Python and
cares about correctness on edge cases above all else. Complete the following
function so that it passes a rigorous test suite. Return only the completed
function.

{spec}
```

*Discriminating because:* the persona is the only thing that changes from the standard HumanEval run, so any pass@k movement is attributable to role framing. Models differ in whether expert framing actually improves edge-case handling or just changes the prose around the code. Score with the standard pass@k harness — no LLM judge needed, which makes this group cheap and unambiguous.

---

## Bonus source — Awesome-ChatGPT-Prompts (CC0-1.0)

`f/awesome-chatgpt-prompts` is a large, community-maintained collection of "I want you to act as …" role prompts released under CC0-1.0 (public-domain dedication, no attribution required). It's a convenient, unrestricted well of role-based framings if you want to vary the persona across many tasks. Pair a persona from here with any task body from the datasets above.

---

## How this feeds the benchmark

- Each style above is a variant in a `prompt_style_benchmark.yaml` group; the group's base task is a single sourced item, and the expected answer is the dataset's reference (number for GSM8K/BBH, F1/EM for SQuAD, pass@k for HumanEval).
- Prefer a small loader script (`scripts/build_prompt_style_benchmark.py`) that pulls items by index from each source and renders the five wrappers, so the YAML stores *references + framings* rather than copied corpora — reproducible and license-safe.
- The runner (SCRUM-19) tags each case with `style`, `task_category`, and `source_dataset`; the Grafana panel plots mean score per style per model.
- Store each source's license and citation in the benchmark metadata. Suggested citations: GSM8K — Cobbe et al. 2021; chain-of-thought — Wei et al. 2022; BBH — Suzgun et al. 2022; SQuAD 2.0 — Rajpurkar et al. 2018; HumanEval — Chen et al. 2021.
- `regression_v2.yaml` stays untouched — separate benchmark, not a non-regression suite.
