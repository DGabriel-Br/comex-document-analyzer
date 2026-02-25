from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from urllib import error, request

CONFIDENCE_THRESHOLD = 0.75

CANONICAL_FIELDS = [
    "document_number",
    "invoice_number",
    "packing_list_number",
    "bl_number",
    "issue_date",
    "shipment_date",
    "issue_or_shipment_date",
    "po_number",
    "shipper",
    "consignee",
    "consignee_cnpj",
    "goods_description",
    "freight_value",
    "freight_term",
    "origin_country",
    "provenance_country",
    "acquisition_country",
    "destination_country",
    "pol",
    "pod",
    "incoterm",
    "currency",
    "net_weight",
    "gross_weight",
    "volume_cbm",
    "package_count",
    "ncm",
    "total_value",
    "etd",
    "eta",
]

DOC_TYPE_FIELD_SCOPE: Dict[str, set[str]] = {
    "invoice": {
        "document_number",
        "invoice_number",
        "issue_date",
        "issue_or_shipment_date",
        "po_number",
        "shipper",
        "consignee",
        "consignee_cnpj",
        "goods_description",
        "freight_value",
        "freight_term",
        "origin_country",
        "provenance_country",
        "acquisition_country",
        "incoterm",
        "currency",
        "net_weight",
        "gross_weight",
        "volume_cbm",
        "package_count",
        "ncm",
        "total_value",
        "etd",
        "eta",
    },
    "packing_list": {
        "document_number",
        "packing_list_number",
        "issue_date",
        "issue_or_shipment_date",
        "po_number",
        "shipper",
        "consignee",
        "goods_description",
        "origin_country",
        "provenance_country",
        "acquisition_country",
        "net_weight",
        "gross_weight",
        "volume_cbm",
        "package_count",
        "etd",
        "eta",
    },
    "bl": {
        "document_number",
        "bl_number",
        "shipment_date",
        "issue_or_shipment_date",
        "shipper",
        "consignee",
        "pol",
        "pod",
        "freight_term",
        "freight_value",
        "origin_country",
        "destination_country",
        "net_weight",
        "gross_weight",
        "package_count",
        "etd",
        "eta",
    },
}


@dataclass
class Candidate:
    value: str
    confidence: float


ALIASES: Dict[str, List[str]] = {
    "document_number": ["document number", "número do documento", "doc no"],
    "invoice_number": ["invoice no", "invoice number", "inv#", "commercial invoice number"],
    "packing_list_number": ["packing list no", "packing list number", "p/l number"],
    "bl_number": ["bill of lading no", "bl no", "b/l number"],
    "issue_date": ["issue date", "data de emissão", "invoice date"],
    "shipment_date": ["shipment date", "embarque", "shipping date", "on board date"],
    "issue_or_shipment_date": ["data de emissão / embarque", "issue/shipment date"],
    "po_number": ["po no", "purchase order", "ordem de compra", "order no"],
    "shipper": ["shipper", "exporter", "exportador", "seller"],
    "consignee": ["consignee", "importador", "importer"],
    "consignee_cnpj": ["cnpj do importador", "cnpj consignee", "consignee tax id", "cnpj"],
    "goods_description": ["description of goods", "descrição da mercadoria", "commodity"],
    "freight_value": ["freight value", "valor do frete", "freight amount"],
    "freight_term": ["freight term", "condição do frete", "freight condition", "freight payable"],
    "origin_country": ["country of origin", "país de origem", "made in"],
    "provenance_country": ["país de procedência", "country of provenance"],
    "acquisition_country": ["país de aquisição", "country of acquisition"],
    "destination_country": ["destination", "destination country", "country of destination"],
    "pol": ["port of loading", "pol", "porto de carregamento"],
    "pod": ["port of discharge", "pod", "porto de descarga", "place of delivery"],
    "incoterm": ["incoterm", "terms of delivery", "trade term"],
    "currency": ["currency", "curr", "invoice currency"],
    "net_weight": ["net weight", "peso líquido", "n.w.", "nw"],
    "gross_weight": ["gross weight", "peso bruto", "g.w.", "gw"],
    "volume_cbm": ["cbm", "cubagem", "volume"],
    "package_count": ["total packages", "packages", "quantidade de volumes", "cartons", "no. of packages"],
    "ncm": ["ncm", "ncms", "hs code", "hscode"],
    "total_value": ["total amount", "total value", "amount due", "invoice total"],
    "etd": ["etd", "estimated time of departure", "departure date"],
    "eta": ["eta", "estimated time of arrival", "arrival date"],
}

FIELD_VALUE_PATTERNS: Dict[str, str] = {
    "document_number": r"([A-Z0-9][A-Z0-9\-/]{4,})",
    "invoice_number": r"([A-Z0-9][A-Z0-9\-/]{4,})",
    "packing_list_number": r"([A-Z0-9][A-Z0-9\-/]{4,})",
    "bl_number": r"([A-Z0-9][A-Z0-9\-/]{4,})",
    "issue_date": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
    "shipment_date": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
    "issue_or_shipment_date": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
    "po_number": r"([A-Z0-9][A-Z0-9\-/]{3,})",
    "shipper": r"([A-Za-z0-9&.,()\-\s]{4,80})",
    "consignee": r"([A-Za-z0-9&.,()\-\s]{4,80})",
    "consignee_cnpj": r"([0-9./\-]{14,20})",
    "goods_description": r"([A-Za-z0-9,./()\-\s]{8,120})",
    "freight_value": r"([0-9][0-9.,]{1,})",
    "freight_term": r"([A-Za-z\s]{3,30})",
    "origin_country": r"([A-Za-z\s]{3,30})",
    "provenance_country": r"([A-Za-z\s]{3,30})",
    "acquisition_country": r"([A-Za-z\s]{3,30})",
    "destination_country": r"([A-Za-z\s]{3,30})",
    "pol": r"([A-Za-z\s]{3,30})",
    "pod": r"([A-Za-z\s]{3,30})",
    "incoterm": r"\b([A-Z]{3})\b",
    "currency": r"\b([A-Z]{3})\b",
    "net_weight": r"([0-9][0-9.,]*\s*(?:KG|KGS|LB|LBS)?)",
    "gross_weight": r"([0-9][0-9.,]*\s*(?:KG|KGS|LB|LBS)?)",
    "volume_cbm": r"([0-9][0-9.,]*\s*(?:CBM|M3)?)",
    "package_count": r"([0-9]{1,5})",
    "ncm": r"([0-9]{4,8}(?:\.[0-9]{2})?)",
    "total_value": r"([0-9][0-9.,]{1,})",
    "etd": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
    "eta": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
}

GENERIC_NOISE = {
    "invoice",
    "packing list",
    "bill of lading",
    "document no",
    "document number",
    "buyer",
    "consignee",
    "shipper",
    "exporter",
    "importer",
    "supply",
    "switching",
    "order no & date",
    "order no",
    "date",
    "details",
    "notify party",
}


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" :-\t")


def _contains_digits(value: str) -> bool:
    return any(ch.isdigit() for ch in value)


def _looks_like_noise(value: str) -> bool:
    cleaned = normalize_spaces(value).strip().lower()
    if not cleaned:
        return True
    if cleaned in GENERIC_NOISE:
        return True
    if cleaned.endswith(" no") or cleaned.endswith(" number"):
        return True
    return False


def _is_valid_candidate(field: str, value: str) -> bool:
    value = normalize_spaces(value)
    if not value or _looks_like_noise(value):
        return False

    if field in {"invoice_number", "packing_list_number", "bl_number", "document_number", "po_number"}:
        return _contains_digits(value) and len(value) >= 5

    if field == "consignee_cnpj":
        digits = re.sub(r"\D", "", value)
        return len(digits) == 14

    if field in {"shipper", "consignee"}:
        # evita capturar apenas rótulo ou palavras curtas
        tokens = [t for t in re.split(r"\s+", value) if t]
        if len(tokens) < 2 and not _contains_digits(value):
            return False

    if field in {"freight_value", "total_value", "net_weight", "gross_weight", "volume_cbm", "package_count"}:
        return _contains_digits(value)

    if field in {"pol", "pod", "origin_country", "destination_country", "provenance_country", "acquisition_country"}:
        return not _contains_digits(value)

    return True


def _match_after_alias(line: str, alias: str, field: str) -> Optional[Candidate]:
    pattern = FIELD_VALUE_PATTERNS[field]
    candidate_pattern = re.compile(
        rf"\b{re.escape(alias)}\b\s*(?:no|number|#)?\s*[:\-]?\s*{pattern}",
        flags=re.IGNORECASE,
    )
    match = candidate_pattern.search(line)
    if not match:
        return None
    value = normalize_spaces(match.group(1))
    if not _is_valid_candidate(field, value):
        return None
    return Candidate(value=value, confidence=0.92)


def layer_a_alias_regex(lines: List[str], active_fields: List[str]) -> Dict[str, Candidate]:
    resolved: Dict[str, Candidate] = {}
    for field in active_fields:
        aliases = ALIASES.get(field, [])
        for line in lines:
            for alias in aliases:
                candidate = _match_after_alias(line, alias, field)
                if candidate:
                    resolved[field] = candidate
                    break
            if field in resolved:
                break
    return resolved


def _extract_from_window(window: Iterable[str], field: str) -> Optional[Candidate]:
    pattern = re.compile(FIELD_VALUE_PATTERNS[field], flags=re.IGNORECASE)
    for line in window:
        if ":" not in line and "-" not in line:
            continue
        match = pattern.search(line)
        if match:
            value = normalize_spaces(match.group(1))
            if _is_valid_candidate(field, value):
                return Candidate(value=value, confidence=0.8)
    return None


def layer_b_context(raw_text: str, already_resolved: Dict[str, Candidate], active_fields: List[str]) -> Dict[str, Candidate]:
    lines = [normalize_spaces(line) for line in raw_text.splitlines() if normalize_spaces(line)]
    resolved: Dict[str, Candidate] = dict(already_resolved)

    for field in active_fields:
        if field in resolved:
            continue
        aliases = ALIASES.get(field, [])
        for idx, line in enumerate(lines):
            if any(re.search(rf"\b{re.escape(alias)}\b", line, flags=re.IGNORECASE) for alias in aliases):
                start = max(0, idx - 1)
                end = min(len(lines), idx + 2)
                candidate = _extract_from_window(lines[start:end], field)
                if candidate:
                    resolved[field] = candidate
                    break
    return resolved


def _build_llm_prompt(raw_text: str, active_fields: List[str]) -> str:
    sample = raw_text[:12000]
    schema = {key: "" for key in active_fields}
    return (
        "Extract shipping/commercial document fields and answer ONLY JSON. "
        f"Use this schema keys exactly: {json.dumps(schema, ensure_ascii=False)}. "
        "If value is unknown, keep empty string. Text:\n"
        + sample
    )


def _call_openai_json(prompt: str, active_fields: List[str]) -> Optional[Dict[str, str]]:
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

    content = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not content:
        return None

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None

    return {k: normalize_spaces(str(parsed.get(k, ""))) for k in active_fields}


def _ner_style_fallback(raw_text: str, active_fields: List[str]) -> Dict[str, str]:
    resolved: Dict[str, str] = {k: "" for k in active_fields}

    patterns: List[Tuple[str, str]] = [
        ("invoice_number", r"invoice\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{5,})"),
        ("packing_list_number", r"packing\s*list\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{5,})"),
        ("bl_number", r"(?:bill\s*of\s*lading|b/?l)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{5,})"),
        ("po_number", r"(?:po|purchase\s*order|ordem\s*de\s*compra)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{4,})"),
        ("consignee_cnpj", r"cnpj\s*(?:do\s*importador|consignee)?\s*[:\-]?\s*([0-9./\-]{14,20})"),
        ("incoterm", r"\b(EXW|FCA|FAS|FOB|CFR|CIF|CPT|CIP|DPU|DAP|DDP)\b"),
        ("net_weight", r"(?:net\s*weight|peso\s*líquido)\s*[:\-]?\s*([0-9][0-9.,]*\s*(?:KG|KGS|LB|LBS)?)"),
        ("gross_weight", r"(?:gross\s*weight|peso\s*bruto)\s*[:\-]?\s*([0-9][0-9.,]*\s*(?:KG|KGS|LB|LBS)?)"),
        ("package_count", r"(?:packages?|cartons?|volumes?)\s*[:\-]?\s*([0-9]{1,5})"),
        ("total_value", r"(?:total\s*amount|total\s*value|amount\s*due|invoice\s*total)\s*[:\-]?\s*([0-9][0-9.,]+)"),
        ("pol", r"(?:port\s*of\s*loading|pol)\s*[:\-]?\s*([A-Za-z\s]{3,30})"),
        ("pod", r"(?:port\s*of\s*discharge|pod|place\s*of\s*delivery)\s*[:\-]?\s*([A-Za-z\s]{3,30})"),
    ]

    for field, pattern in patterns:
        if field not in resolved:
            continue
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            value = normalize_spaces(match.group(1))
            if _is_valid_candidate(field, value):
                resolved[field] = value

    if "document_number" in resolved and not resolved["document_number"]:
        for source in ["invoice_number", "packing_list_number", "bl_number"]:
            if source in resolved and resolved[source]:
                resolved["document_number"] = resolved[source]
                break

    if "issue_or_shipment_date" in resolved and not resolved["issue_or_shipment_date"]:
        for source in ["issue_date", "shipment_date", "etd", "eta"]:
            if source in resolved and resolved[source]:
                resolved["issue_or_shipment_date"] = resolved[source]
                break

    return resolved


def layer_c_llm_ner(
    raw_text: str,
    already_resolved: Dict[str, Candidate],
    active_fields: List[str],
) -> Dict[str, Candidate]:
    resolved = dict(already_resolved)
    llm_result = _call_openai_json(_build_llm_prompt(raw_text, active_fields), active_fields)
    if not llm_result:
        llm_result = _ner_style_fallback(raw_text, active_fields)

    for field in active_fields:
        if field in resolved:
            continue
        value = normalize_spaces(str(llm_result.get(field, "")))
        if _is_valid_candidate(field, value):
            resolved[field] = Candidate(value=value, confidence=0.7)

    return resolved


def parse_fields(
    raw_text: str,
    doc_type: Optional[str] = None,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> Dict[str, Dict[str, object]]:
    active_fields = sorted(DOC_TYPE_FIELD_SCOPE.get(doc_type, set(CANONICAL_FIELDS)))
    lines = [normalize_spaces(line) for line in raw_text.splitlines() if normalize_spaces(line)]

    layer_a = layer_a_alias_regex(lines, active_fields)
    layer_b = layer_b_context(raw_text, layer_a, active_fields)
    layer_c = layer_c_llm_ner(raw_text, layer_b, active_fields)

    final: Dict[str, Dict[str, object]] = {}
    for field in CANONICAL_FIELDS:
        if field not in active_fields:
            final[field] = {
                "value": "",
                "source_layer": "ignored",
                "confidence": 0.0,
                "pending_review": False,
            }
            continue

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
