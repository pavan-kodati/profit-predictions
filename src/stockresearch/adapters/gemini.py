"""Gemini adapter. The model only reads documents we give it (no web search) and must quote what it relies on.

The response is validated against a strict schema here. Quotes and numbers are checked against the stored documents
by the research service before anything is used (plan section 2.3).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from stockresearch.adapters.http import SourceUnavailable
from stockresearch.ports import ResearchDocument

log = logging.getLogger(__name__)

MAX_DOC_CHARS = 3500
MAX_TOTAL_CHARS = 60000
REQUEST_TIMEOUT_MS = 120_000  # without a timeout a busy model can hang a job indefinitely
# Rate limits, overload and timeouts are temporary: retry with backoff. Anything else (bad key, unknown model) is not.
TRANSIENT = ("429", "RESOURCE_EXHAUSTED", "500", "503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED")

PROMPT = """You are a careful equity research assistant for an Indian retail investor who buys shares (no
futures or options) and holds them from a few days to three weeks.

Company: {name} (NSE: {symbol})

You are given (1) structured FACTS and (2) numbered DOCUMENTS. The documents are the ONLY source you may use.
Do not use outside knowledge about this company. Do not guess.

Rules:
- Every event and every evidence item must include an exact quote copied word for word from ONE document,
  and that document's id. Quotes must be at least 4 words. Never paraphrase inside "quote".
- Any number in a "claim" must appear in its quote.
- Separate delivered facts (orders received, results reported, capacity commissioned, stake bought) from stories
  about the future (plans, ambitions, "could", "may", themes). Set future_story=true if the main reason the stock
  is moving is a story about the future.
- Documents from tier 3 are opinions (broker views). They may only be used for "sentiment".
- Include evidence AGAINST the stock as well (weak results, promoter selling, debt, delays), not only evidence for.
- If you cannot find something (e.g. an order book), leave it null. Writing "not found" is better than guessing.
- Write in plain English for a non-expert.

FACTS
{facts}

DOCUMENTS
{documents}

Reply with ONLY this JSON object:
{{
  "thesis": "2-3 sentences: why this stock could move up in the next 1-3 weeks and what must go right",
  "events": [{{"type": "order_win|results|expansion|approval|acquisition|stake_buy|fundraise|negative|other",
              "polarity": 1 or -1 or 0, "title": "short factual title", "date": "YYYY-MM-DD or null",
              "value_cr": number in crore rupees or null, "doc_ids": [ids of ALL documents reporting this same event],
              "quote_doc_id": id, "quote": "exact words"}}],
  "evidence": [{{"area": "quality|price|trigger|order_book|ownership|sentiment", "stance": "for|against",
                "claim": "plain-English claim", "doc_id": id, "quote": "exact words"}}],
  "order_book": {{"value_cr": number or null, "doc_id": id or null, "quote": "exact words or null"}},
  "article_stances": [{{"doc_id": id, "stance": "positive|neutral|negative"}}],
  "future_story": true or false,
  "pick_type": "hype|mixed|fundamentals",
  "track_record": "1-2 sentences on the business and share price record, from the FACTS",
  "risks": ["3 to 5 short, specific risks"],
  "confidence": "low|medium|high"
}}"""


class AiEvent(BaseModel):
    type: str
    polarity: int = Field(ge=-1, le=1)
    title: str
    date: str | None = None
    value_cr: float | None = None
    doc_ids: list[int] = Field(default_factory=list)
    quote_doc_id: int
    quote: str


class AiEvidence(BaseModel):
    area: Literal["quality", "price", "trigger", "order_book", "ownership", "sentiment"]
    stance: Literal["for", "against"]
    claim: str
    doc_id: int
    quote: str


class AiOrderBook(BaseModel):
    value_cr: float | None = None
    doc_id: int | None = None
    quote: str | None = None


class AiStance(BaseModel):
    doc_id: int
    stance: Literal["positive", "neutral", "negative"]


class AiFindings(BaseModel):
    thesis: str = ""
    events: list[AiEvent] = Field(default_factory=list)
    evidence: list[AiEvidence] = Field(default_factory=list)
    order_book: AiOrderBook | None = None
    article_stances: list[AiStance] = Field(default_factory=list)
    future_story: bool = False
    pick_type: Literal["hype", "mixed", "fundamentals"] | None = None
    track_record: str = ""
    risks: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"

    @field_validator("risks")
    @classmethod
    def _cap_risks(cls, v: list[str]) -> list[str]:
        return [r for r in v if r.strip()][:6]


def render_documents(documents: list[ResearchDocument]) -> str:
    parts, total = [], 0
    for d in documents:
        body = d.text[:MAX_DOC_CHARS]
        block = f"[doc {d.doc_id}] tier {d.tier} | {d.publisher} | {d.published}\nTITLE: {d.title}\nTEXT: {body}\n"
        if total + len(block) > MAX_TOTAL_CHARS:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts) or "(no documents)"


def parse_findings(text: str) -> AiFindings:
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        raise ValueError("no JSON object in the AI reply")
    return AiFindings.model_validate(json.loads(match.group(0)))


class GeminiResearcher:
    def __init__(self, api_key: str, model: str) -> None:
        from google import genai
        from google.genai import types

        self.model_name = model
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))

    def analyse(self, symbol: str, name: str, facts: dict[str, Any], documents: list[ResearchDocument]) -> dict[str, Any]:
        from google.genai import types

        prompt = PROMPT.format(name=name, symbol=symbol, facts=json.dumps(facts, indent=1, default=str),
                               documents=render_documents(documents))
        config = types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json")
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._client.models.generate_content(model=self.model_name, contents=prompt, config=config)
                return parse_findings(resp.text or "").model_dump()
            except (ValueError, ValidationError) as e:
                last = e
                log.warning("AI reply for %s failed validation (attempt %d): %s", symbol, attempt + 1, str(e)[:200])
            except Exception as e:
                last = e
                msg = str(e)
                if any(code in msg for code in TRANSIENT):
                    wait = 30 * (attempt + 1)
                    log.warning("Gemini busy or rate-limited for %s (%s); retrying in %ds", symbol, msg[:60], wait)
                    time.sleep(wait)
                else:
                    raise SourceUnavailable(f"Gemini call failed: {msg[:300]}") from e
        raise SourceUnavailable(f"Gemini gave no usable reply for {symbol}: {last}")
