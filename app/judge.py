"""
Sends a (question, candidate answer, rubric) triple to a judge model (Gemini),
parses the strict-JSON verdict it returns, and scales the raw 0..1 score onto
the 0..5 scale persisted in `results.judge_score` (see CLAUDE.md → Database).

The judge model is reached through the same `call_llm` entry point as every
other provider; `judge()` takes it as an injectable parameter so tests can
swap in a fake and spend zero tokens.
"""
from __future__ import annotations


import json
import re
from dataclasses import dataclass,field
from decimal import Decimal
from typing import Callable, Any
from app.llm_client import LLMResponse, call_llm
from pathlib import Path
from app.prompts.loader import load_prompt

class JudgeParseError(ValueError):
    """Raised when the judge score is not valid, in-range verdict JSON"""

@dataclass(frozen=True)
class JudgeVerdict:
    score:float
    reasoning:str
    response: Any = field(repr=False)

def to_db_scale(raw_score: float)->Decimal:
    score = Decimal(str(raw_score))
    return (score*5).quantize(Decimal("0.1"))


def parse_verdict(text:str) -> JudgeVerdict:
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    cleaned = match.group(1) if match else text
    try:
        doc = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise JudgeParseError("Json was not properly cleaned") from e

    if not isinstance(doc, dict):
        raise JudgeParseError(f"expected a JSON object, got {type(doc).__name__}")

    score = doc.get("score")
    if not isinstance(score,(float,int)) or isinstance(score,bool):
        raise JudgeParseError("The score was either not there or not valid")
    if score>1 or score<0:
        raise JudgeParseError("The score must be in the range of [0,1]")
    
    reasoning = doc.get("reasoning")
    if not isinstance(reasoning,str) or not reasoning.strip():
        raise JudgeParseError("The reasoning must not be an empty string")
    
    return JudgeVerdict(
        score = score,
        reasoning=reasoning,
        response = text,
    )
RUBRIC_PATH = Path(__file__).parent / "prompts" / "templates" / "judge_rubric.yaml"
def load_rubric()->str:
    return load_prompt(RUBRIC_PATH).content

def build_judge_prompt(
    rubric: str,
    question: str,
    answer: str,
) -> str:
    return f"{rubric.strip()}\n\nQ: {question}\n\nR: {answer}"

def judge(
        question: str,
        answer: str,
        *,
        rubric:str | None = None,
        model:str = "gemini-2.5-flash",
        max_tokens:int = 4096,
        call: Callable[..., LLMResponse] = call_llm
)->JudgeVerdict:
    rubric = rubric or load_rubric()
    prompt = build_judge_prompt(rubric, question, answer)
    response = call("gemini",model,prompt,max_tokens=max_tokens)
    return parse_verdict(response.content)





