from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from urllib import error, request

CONFIDENCE_THRESHOLD = 0.75

CANONICAL_FIELDS = [
    "invoice_number",
    "packing_list_number",
    "bl_number",
    "po_number",
    "shipper",
    "consignee",
    "origin_country",
    "destination_country",
    "incoterm",
    "currency",
    "net_weight",
    "gross_weight",
    "package_count",
    "total_value",
    "etd",
    "eta",
]


@dataclass
class Candidate:
    value: str
    confidence: float


ALIASES: Dict[str, List[str]] = {
    "invoice_number": ["invoice no", "invoice number", "inv#", "commercial invoice number"],
    "packing_list_number": ["packing list no", "packing list number", "p/l number"],
    "bl_number": ["bill of lading no", "bl no", "b/l number"],
    "po_number": ["po no", "purchase order", "order no"],
    "shipper": ["shipper", "exporter", "seller"],
    "consignee": ["consignee", "buyer", "importer"],
    "origin_country": ["country of origin", "origin country", "made in"],
    "destination_country": ["destination", "destination country", "country of destination"],
    "incoterm": ["incoterm", "terms of delivery", "trade term"],
    "currency": ["currency", "curr", "invoice currency"],
    "net_weight": ["net weight", "n.w.", "nw"],
    "gross_weight": ["gross weight", "g.w.", "gw"],
    "package_count": ["total packages", "packages", "cartons"],
    "total_value": ["total amount", "total value", "amount due", "invoice total"],
    "etd": ["etd", "estimated time of departure", "departure date"],
    "eta": ["eta", "estimated time of arrival", "arrival date"],
}

FIELD_VALUE_PATTERNS: Dict[str, str] = {
    "invoice_number": r"([A-Z0-9\-/]{4,})",
    "packing_list_number": r"([A-Z0-9\-/]{4,})",
    "bl_number": r"([A-Z0-9\-/]{4,})",
    "po_number": r"([A-Z0-9\-/]{3,})",
    "shipper": r"([A-Za-z0-9&.,\-\s]{3,})",
    "consignee": r"([A-Za-z0-9&.,\-\s]{3,})",
    "origin_country": r"([A-Za-z\s]{3,})",
    "destination_country": r"([A-Za-z\s]{3,})",
    "incoterm": r"\b([A-Z]{3})\b",
    "currency": r"\b([A-Z]{3})\b",
    "net_weight": r"([0-9][0-9.,]*\s*[A-Za-z]{0,3})",
    "gross_weight": r"([0-9][0-9.,]*\s*[A-Za-z]{0,3})",
    "package_count": r"([0-9][0-9.,]*)",
    "total_value": r"([0-9][0-9.,]*)",
    "etd": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
    "eta": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
}


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" :-\t")


def _match_after_alias(line: str, alias: str, field: str) -> Optional[Candidate]:
    pattern = FIELD_VALUE_PATTERNS[field]
    candidate_pattern = re.compile(
        rf"{re.escape(alias)}\s*(?:no|number|#)?\s*[:\-]?\s*{pattern}",
        flags=re.IGNORECASE,
    )
    match = candidate_pattern.search(line)
    if not match:
        return None
    return Candidate(value=normalize_spaces(match.group(1)), confidence=0.92)


def layer_a_alias_regex(lines: List[str]) -> Dict[str, Candidate]:
    resolved: Dict[str, Candidate] = {}
    for field in CANONICAL_FIELDS:
        aliases = ALIASES.get(field, [])
        for line in lines:
            for alias in aliases:
                candidate = _match_after_alias(line, alias, field)
                if candidate and candidate.value:
                    resolved[field] = candidate
                    break
            if field in resolved:
                break
    return resolved


def _extract_from_window(window: Iterable[str], field: str) -> Optional[Candidate]:
    pattern = re.compile(FIELD_VALUE_PATTERNS[field], flags=re.IGNORECASE)
    for line in window:
        match = pattern.search(line)
        if match:
            value = normalize_spaces(match.group(1))
            if value:
                return Candidate(value=value, confidence=0.8)
    return None


def layer_b_context(raw_text: str, already_resolved: Dict[str, Candidate]) -> Dict[str, Candidate]:
    lines = [normalize_spaces(line) for line in raw_text.splitlines() if normalize_spaces(line)]
    resolved: Dict[str, Candidate] = dict(already_resolved)

    for field in CANONICAL_FIELDS:
        if field in resolved:
            continue
        aliases = ALIASES.get(field, [])
        for idx, line in enumerate(lines):
            if any(alias.lower() in line.lower() for alias in aliases):
                start = max(0, idx - 2)
                end = min(len(lines), idx + 3)
                candidate = _extract_from_window(lines[start:end], field)
                if candidate:
                    resolved[field] = candidate
                    break
    return resolved


def _build_llm_prompt(raw_text: str) -> str:
    sample = raw_text[:12000]
    schema = {key: "" for key in CANONICAL_FIELDS}
    return (
        "Extract shipping/commercial document fields and answer ONLY JSON. "
        f"Use this schema keys exactly: {json.dumps(schema, ensure_ascii=False)}. "
        "If value is unknown, keep empty string. Text:\n"
        + sample
    )


def _call_openai_json(prompt: str) -> Optional[Dict[str, str]]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": "You output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    req = request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    content = (
        body.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not content:
        return None

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None

    return {k: normalize_spaces(str(parsed.get(k, ""))) for k in CANONICAL_FIELDS}


def _ner_style_fallback(raw_text: str) -> Dict[str, str]:
    resolved: Dict[str, str] = {k: "" for k in CANONICAL_FIELDS}

    document_patterns: List[Tuple[str, str]] = [
        ("invoice_number", r"\binv(?:oice)?\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{4,})"),
        ("packing_list_number", r"packing\s*list\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{4,})"),
        ("bl_number", r"(?:bill\s*of\s*lading|b/?l)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{4,})"),
        ("po_number", r"(?:po|purchase\s*order)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{3,})"),
    ]

    for field, pattern in document_patterns:
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            resolved[field] = normalize_spaces(match.group(1))

    entity_like = {
        "shipper": r"(?:shipper|exporter)\s*[:\-]?\s*([^\n]{3,80})",
        "consignee": r"consignee\s*[:\-]?\s*([^\n]{3,80})",
        "origin_country": r"country\s*of\s*origin\s*[:\-]?\s*([^\n]{3,40})",
        "destination_country": r"destination\s*[:\-]?\s*([^\n]{3,40})",
        "incoterm": r"incoterm\s*[:\-]?\s*([A-Z]{3})",
        "currency": r"currency\s*[:\-]?\s*([A-Z]{3})",
        "net_weight": r"net\s*weight\s*[:\-]?\s*([0-9.,]+\s*[A-Za-z]{0,3})",
        "gross_weight": r"gross\s*weight\s*[:\-]?\s*([0-9.,]+\s*[A-Za-z]{0,3})",
        "package_count": r"(?:packages?|cartons?)\s*[:\-]?\s*([0-9.,]+)",
        "total_value": r"(?:total\s*amount|total\s*value|amount\s*due)\s*[:\-]?\s*([0-9.,]+)",
        "etd": r"etd\s*[:\-]?\s*([0-9/\-.]+)",
        "eta": r"eta\s*[:\-]?\s*([0-9/\-.]+)",
    }
    for field, pattern in entity_like.items():
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            resolved[field] = normalize_spaces(match.group(1))

    return resolved


def layer_c_llm_ner(raw_text: str, already_resolved: Dict[str, Candidate]) -> Dict[str, Candidate]:
    resolved = dict(already_resolved)
    llm_result = _call_openai_json(_build_llm_prompt(raw_text))
    if not llm_result:
        llm_result = _ner_style_fallback(raw_text)

    for field in CANONICAL_FIELDS:
        if field in resolved:
            continue
        value = normalize_spaces(str(llm_result.get(field, "")))
        if value:
            resolved[field] = Candidate(value=value, confidence=0.7)

    return resolved


def parse_fields(raw_text: str, confidence_threshold: float = CONFIDENCE_THRESHOLD) -> Dict[str, Dict[str, object]]:
    lines = [normalize_spaces(line) for line in raw_text.splitlines() if normalize_spaces(line)]
    layer_a = layer_a_alias_regex(lines)
    layer_b = layer_b_context(raw_text, layer_a)
    layer_c = layer_c_llm_ner(raw_text, layer_b)

    final: Dict[str, Dict[str, object]] = {}
    for field in CANONICAL_FIELDS:
        candidate = layer_c.get(field)
        if not candidate:
            final[field] = {
                "value": "",
                "source_layer": "unresolved",
                "confidence": 0.0,
                "pending_review": True,
            }
            continue

        if field in layer_a:
            source_layer = "A"
        elif field in layer_b:
            source_layer = "B"
        else:
            source_layer = "C"

        final[field] = {
            "value": candidate.value,
            "source_layer": source_layer,
            "confidence": round(max(0.0, min(candidate.confidence, 1.0)), 2),
            "pending_review": candidate.confidence < confidence_threshold,
        }

    return final
