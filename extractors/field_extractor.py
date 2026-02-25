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
    "shipment_date": ["shipment date", "embarque", "shipping date"],
    "issue_or_shipment_date": ["data de emissão / embarque", "issue/shipment date"],
    "po_number": ["po no", "purchase order", "ordem de compra", "order no"],
    "shipper": ["shipper", "exporter", "exportador", "seller"],
    "consignee": ["consignee", "importador", "buyer", "importer"],
    "consignee_cnpj": ["cnpj do importador", "cnpj consignee", "consignee tax id"],
    "goods_description": ["description of goods", "descrição da mercadoria", "commodity"],
    "freight_value": ["freight value", "valor do frete", "freight amount"],
    "freight_term": ["freight term", "condição do frete", "freight condition"],
    "origin_country": ["country of origin", "país de origem", "made in"],
    "provenance_country": ["país de procedência", "country of provenance"],
    "acquisition_country": ["país de aquisição", "country of acquisition"],
    "destination_country": ["destination", "destination country", "country of destination"],
    "pol": ["port of loading", "pol", "porto de carregamento"],
    "pod": ["port of discharge", "pod", "porto de descarga"],
    "incoterm": ["incoterm", "terms of delivery", "trade term"],
    "currency": ["currency", "curr", "invoice currency"],
    "net_weight": ["net weight", "peso líquido", "n.w.", "nw"],
    "gross_weight": ["gross weight", "peso bruto", "g.w.", "gw"],
    "volume_cbm": ["cbm", "cubagem", "volume"],
    "package_count": ["total packages", "packages", "quantidade de volumes", "cartons"],
    "ncm": ["ncm", "ncms", "hs code", "hscode"],
    "total_value": ["total amount", "total value", "amount due", "invoice total"],
    "etd": ["etd", "estimated time of departure", "departure date"],
    "eta": ["eta", "estimated time of arrival", "arrival date"],
}

DOC_TYPE_PROFILES: Dict[str, Dict[str, object]] = {
    "invoice": {
        "field_priority": [
            "invoice_number",
            "document_number",
            "issue_date",
            "po_number",
            "shipper",
            "consignee",
            "consignee_cnpj",
            "goods_description",
            "total_value",
            "currency",
            "freight_value",
            "freight_term",
            "incoterm",
            "origin_country",
            "provenance_country",
            "acquisition_country",
            "destination_country",
            "net_weight",
            "gross_weight",
            "volume_cbm",
            "package_count",
            "ncm",
        ],
        "aliases": {
            "invoice_number": ["invoice n°", "invoice nr", "invoice num"],
            "issue_date": ["date of issue", "invoice issued on"],
        },
    },
    "packing_list": {
        "field_priority": [
            "packing_list_number",
            "document_number",
            "shipment_date",
            "issue_or_shipment_date",
            "po_number",
            "shipper",
            "consignee",
            "consignee_cnpj",
            "goods_description",
            "origin_country",
            "destination_country",
            "net_weight",
            "gross_weight",
            "volume_cbm",
            "package_count",
            "ncm",
        ],
        "aliases": {
            "packing_list_number": ["p/l no", "packing list n°", "packing no"],
            "package_count": ["qty packages", "number of packages"],
        },
    },
    "bl": {
        "field_priority": [
            "bl_number",
            "document_number",
            "shipment_date",
            "issue_or_shipment_date",
            "shipper",
            "consignee",
            "consignee_cnpj",
            "goods_description",
            "freight_term",
            "origin_country",
            "destination_country",
            "pol",
            "pod",
            "etd",
            "eta",
            "net_weight",
            "gross_weight",
            "volume_cbm",
            "package_count",
        ],
        "aliases": {
            "bl_number": ["b/l no", "bol no", "bill of lading number"],
            "pol": ["load port", "port load"],
            "pod": ["discharge port", "port discharge"],
        },
    },
}

DOC_NUMBER_FALLBACK_BY_TYPE: Dict[str, str] = {
    "invoice": "invoice_number",
    "packing_list": "packing_list_number",
    "bl": "bl_number",
}

FIELD_VALUE_PATTERNS: Dict[str, str] = {
    "document_number": r"([A-Z0-9\-/]{3,})",
    "invoice_number": r"([A-Z0-9\-/]{4,})",
    "packing_list_number": r"([A-Z0-9\-/]{4,})",
    "bl_number": r"([A-Z0-9\-/]{4,})",
    "issue_date": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
    "shipment_date": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
    "issue_or_shipment_date": r"([0-9]{1,4}[./\-][0-9]{1,2}[./\-][0-9]{1,4})",
    "po_number": r"([A-Z0-9\-/]{3,})",
    "shipper": r"([A-Za-z0-9&.,\-\s]{3,})",
    "consignee": r"([A-Za-z0-9&.,\-\s]{3,})",
    "consignee_cnpj": r"([0-9./\-]{14,20})",
    "goods_description": r"([A-Za-z0-9,./\-\s]{5,})",
    "freight_value": r"([0-9][0-9.,]*)",
    "freight_term": r"([A-Za-z\s]{3,})",
    "origin_country": r"([A-Za-z\s]{3,})",
    "provenance_country": r"([A-Za-z\s]{3,})",
    "acquisition_country": r"([A-Za-z\s]{3,})",
    "destination_country": r"([A-Za-z\s]{3,})",
    "pol": r"([A-Za-z\s]{3,})",
    "pod": r"([A-Za-z\s]{3,})",
    "incoterm": r"\b([A-Z]{3})\b",
    "currency": r"\b([A-Z]{3})\b",
    "net_weight": r"([0-9][0-9.,]*\s*[A-Za-z]{0,3})",
    "gross_weight": r"([0-9][0-9.,]*\s*[A-Za-z]{0,3})",
    "volume_cbm": r"([0-9][0-9.,]*\s*(?:CBM|M3)?)",
    "package_count": r"([0-9][0-9.,]*)",
    "ncm": r"([0-9]{4,8}(?:\.[0-9]{2})?)",
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


def _resolve_profile(doc_type: str) -> Tuple[List[str], Dict[str, List[str]]]:
    profile = DOC_TYPE_PROFILES.get(doc_type.lower(), {})
    field_priority = profile.get("field_priority", CANONICAL_FIELDS)
    fields = [field for field in field_priority if field in CANONICAL_FIELDS]
    if "document_number" not in fields:
        fields.insert(1 if len(fields) > 1 else 0, "document_number")

    aliases = {field: list(ALIASES.get(field, [])) for field in CANONICAL_FIELDS}
    for field, extra_aliases in profile.get("aliases", {}).items():
        aliases.setdefault(field, [])
        aliases[field].extend(extra_aliases)
    return fields, aliases


def layer_a_alias_regex(lines: List[str], fields_priority: List[str], aliases_map: Dict[str, List[str]]) -> Dict[str, Candidate]:
    resolved: Dict[str, Candidate] = {}
    for field in fields_priority:
        aliases = aliases_map.get(field, [])
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


def layer_b_context(
    raw_text: str,
    already_resolved: Dict[str, Candidate],
    fields_priority: List[str],
    aliases_map: Dict[str, List[str]],
) -> Dict[str, Candidate]:
    lines = [normalize_spaces(line) for line in raw_text.splitlines() if normalize_spaces(line)]
    resolved: Dict[str, Candidate] = dict(already_resolved)

    for field in fields_priority:
        if field in resolved:
            continue
        aliases = aliases_map.get(field, [])
        for idx, line in enumerate(lines):
            if any(alias.lower() in line.lower() for alias in aliases):
                start = max(0, idx - 2)
                end = min(len(lines), idx + 3)
                candidate = _extract_from_window(lines[start:end], field)
                if candidate:
                    resolved[field] = candidate
                    break
    return resolved


def _build_llm_prompt(raw_text: str, fields_priority: List[str]) -> str:
    sample = raw_text[:12000]
    schema = {key: "" for key in fields_priority}
    return (
        "Extract shipping/commercial document fields and answer ONLY JSON. "
        f"Use this schema keys exactly: {json.dumps(schema, ensure_ascii=False)}. "
        "If value is unknown, keep empty string. Text:\n"
        + sample
    )


def _call_openai_json(prompt: str, fields_priority: List[str]) -> Optional[Dict[str, str]]:
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

    return {k: normalize_spaces(str(parsed.get(k, ""))) for k in fields_priority}


def _ner_style_fallback(raw_text: str, fields_priority: List[str], doc_type: str) -> Dict[str, str]:
    resolved: Dict[str, str] = {k: "" for k in fields_priority}

    patterns: List[Tuple[str, str]] = [
        ("invoice_number", r"invoice\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{4,})"),
        ("packing_list_number", r"packing\s*list\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{4,})"),
        ("bl_number", r"(?:bill\s*of\s*lading|b/?l)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{4,})"),
        ("po_number", r"(?:po|purchase\s*order|ordem\s*de\s*compra)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]{3,})"),
        ("consignee_cnpj", r"cnpj\s*(?:do\s*importador|consignee)?\s*[:\-]?\s*([0-9./\-]{14,20})"),
        ("goods_description", r"(?:description\s*of\s*goods|descrição\s*da\s*mercadoria)\s*[:\-]?\s*([^\n]{5,120})"),
        ("freight_value", r"(?:freight\s*value|valor\s*do\s*frete)\s*[:\-]?\s*([0-9.,]+)"),
        ("freight_term", r"(?:freight\s*term|condição\s*do\s*frete)\s*[:\-]?\s*([^\n]{3,40})"),
        ("pol", r"(?:port\s*of\s*loading|pol|porto\s*de\s*carregamento)\s*[:\-]?\s*([^\n]{3,40})"),
        ("pod", r"(?:port\s*of\s*discharge|pod|porto\s*de\s*descarga)\s*[:\-]?\s*([^\n]{3,40})"),
        ("volume_cbm", r"(?:cbm|cubagem)\s*[:\-]?\s*([0-9.,]+(?:\s*(?:CBM|M3))?)"),
        ("ncm", r"(?:ncm|ncms|hs\s*code)\s*[:\-]?\s*([0-9]{4,8}(?:\.[0-9]{2})?)"),
    ]

    for field, pattern in patterns:
        if field not in resolved:
            continue
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            resolved[field] = normalize_spaces(match.group(1))

    fallback_field = DOC_NUMBER_FALLBACK_BY_TYPE.get(doc_type.lower())
    if "document_number" in resolved and fallback_field in resolved and resolved.get(fallback_field):
        resolved["document_number"] = resolved[fallback_field]

    for mirror_a, mirror_b in [("issue_or_shipment_date", "etd"), ("shipment_date", "etd")]:
        if not resolved.get(mirror_a) and resolved.get(mirror_b):
            resolved[mirror_a] = resolved[mirror_b]

    return resolved


def layer_c_llm_ner(
    raw_text: str,
    already_resolved: Dict[str, Candidate],
    fields_priority: List[str],
    doc_type: str,
) -> Dict[str, Candidate]:
    resolved = dict(already_resolved)
    llm_result = _call_openai_json(_build_llm_prompt(raw_text, fields_priority), fields_priority)
    if not llm_result:
        llm_result = _ner_style_fallback(raw_text, fields_priority, doc_type)

    for field in fields_priority:
        if field in resolved:
            continue
        value = normalize_spaces(str(llm_result.get(field, "")))
        if value:
            resolved[field] = Candidate(value=value, confidence=0.7)

    return resolved


def _apply_document_number_fallback(resolved: Dict[str, Candidate], doc_type: str) -> None:
    fallback_field = DOC_NUMBER_FALLBACK_BY_TYPE.get(doc_type.lower())
    if not fallback_field or "document_number" in resolved:
        return
    candidate = resolved.get(fallback_field)
    if not candidate:
        return
    resolved["document_number"] = Candidate(value=candidate.value, confidence=candidate.confidence)


def parse_fields(
    raw_text: str,
    doc_type: str,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> Dict[str, Dict[str, object]]:
    fields_priority, aliases_map = _resolve_profile(doc_type)
    relevant_fields = set(fields_priority)
    lines = [normalize_spaces(line) for line in raw_text.splitlines() if normalize_spaces(line)]
    layer_a = layer_a_alias_regex(lines, fields_priority, aliases_map)
    layer_b = layer_b_context(raw_text, layer_a, fields_priority, aliases_map)
    layer_c = layer_c_llm_ner(raw_text, layer_b, fields_priority, doc_type)
    _apply_document_number_fallback(layer_c, doc_type)

    final: Dict[str, Dict[str, object]] = {}
    for field in CANONICAL_FIELDS:
        if field not in relevant_fields:
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
