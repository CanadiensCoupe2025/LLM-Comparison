# `app/judge.py` — LLM-as-Judge Flow (SCRUM-23)

Visual map of how the judge module turns a model's answer into a persisted
score. Diagrams use Mermaid (renders in GitHub + VS Code Markdown preview).

> **Scale contract:** the judge emits a raw float in `[0.0, 1.0]`; it is
> scaled `×5` at the persistence boundary and stored on a `0–5` scale in
> `results.judge_score`. The `3.5/5` regression alert == raw `0.7`.
> (See CLAUDE.md → Database.)

---

## 1. Where the judge sits in the pipeline

```mermaid
flowchart LR
    A[runner.execute_run] -->|model answer| B[judge.judge]
    B -->|call_llm gemini| C[Gemini 2.5 Pro]
    C -->|raw JSON text| B
    B -->|JudgeVerdict raw 0..1| A
    A -->|to_db_scale ×5| D[(results.judge_score<br/>judge_reasoning)]
```

The judge module owns only the middle box. The **runner** decides *when* to
judge and *how to persist*; the judge module is a pure "answer → verdict"
transform reached through the same `call_llm` seam as every provider.

---

## 2. The functions inside `judge.py` (data pipeline)

```mermaid
flowchart TD
    subgraph judge.py
        BP[build_judge_prompt<br/>Q + R + RUBRIQUE → str]
        J[judge<br/>orchestrates one judging]
        PV[parse_verdict<br/>raw text → JudgeVerdict]
        TS[to_db_scale<br/>0..1 float → Decimal 0..5]
        JV[[JudgeVerdict<br/>score: float<br/>reasoning: str]]
    end

    Q[question + answer + rubric] --> BP
    BP -->|prompt str| J
    J -->|call=call_llm| LLM[(call_llm 'gemini')]
    LLM -->|LLMResponse.content| J
    J --> PV
    PV --> JV
    JV -.consumed by runner.-> TS
    TS --> DB[(judge_score)]
```

**Responsibilities, one line each:**

| Function | Input | Output | Job |
|---|---|---|---|
| `build_judge_prompt` | question, answer, rubric | `str` | Fill the rubric's Q / R / RUBRIQUE slots |
| `judge` | question, answer, rubric, `call=` | `JudgeVerdict` | Orchestrate: build → call → parse |
| `parse_verdict` | raw judge text | `JudgeVerdict` | Defensive JSON parse + validate |
| `to_db_scale` | raw score `0..1` | `Decimal` `0..5` | Scale at the persistence boundary |
| `JudgeVerdict` | — | — | Typed, frozen value object |

---

## 3. What happens in one judging call (sequence)

```mermaid
sequenceDiagram
    participant R as runner
    participant J as judge()
    participant P as parse_verdict()
    participant G as Gemini (call_llm)

    R->>J: judge(question, answer, rubric)
    J->>J: build_judge_prompt(...)
    J->>G: call_llm("gemini", "gemini-2.5-pro", prompt)
    G-->>J: LLMResponse(content='{"score":0.6,...}')
    J->>P: parse_verdict(content)
    P->>P: strip ```json fences
    P->>P: json.loads + validate range/types
    P-->>J: JudgeVerdict(score=0.6, reasoning="...")
    J-->>R: JudgeVerdict
    R->>R: to_db_scale(0.6) → Decimal("3.0")
    R->>R: update_judge(result_id, 3.0, reasoning)
```

---

## 4. The data transformation, step by step

```mermaid
flowchart LR
    T1["'```json
    {score: 0.6,
     reasoning: ...}```'<br/><i>str from Gemini</i>"]
    T2["{'score': 0.6,<br/>'reasoning': '...'}<br/><i>dict</i>"]
    T3["JudgeVerdict(<br/>score=0.6,<br/>reasoning='...')<br/><i>validated object</i>"]
    T4["Decimal('3.0')<br/><i>0–5 scale</i>"]

    T1 -->|strip fences + json.loads| T2
    T2 -->|validate + construct| T3
    T3 -->|to_db_scale ×5| T4
```

Each arrow is a function boundary. Untrusted text becomes a trusted object at
the `parse_verdict` step — after that, nothing downstream re-checks the shape.

---

## 5. The testing seam (why `judge` takes `call=`)

```mermaid
flowchart TD
    subgraph Production
        J1[judge] -->|call defaults to call_llm| REAL[(real Gemini API<br/>costs tokens)]
    end
    subgraph Tests
        J2[judge] -->|call=fake_call| FAKE[lambda returning<br/>canned JSON<br/>zero tokens]
    end
```

`judge(question, answer, rubric, *, call=call_llm)` — the same injection
pattern as `runner.execute_run`. Tests pass a fake `call`, so the whole
parse-and-scale path runs offline with no API spend.

---

## Build order (current status)

```mermaid
flowchart LR
    S1[JudgeVerdict ✓] --> S2[to_db_scale ← next]
    S2 --> S3[parse_verdict]
    S3 --> S4[build_judge_prompt]
    S4 --> S5[judge]
    S5 --> S6[wire into runner + persist]
```
