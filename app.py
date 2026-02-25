from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request, send_file

try:
    import pdfplumber
except Exception:  # pragma: no cover
    pdfplumber = None

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024


@dataclass
class DocumentData:
    doc_type: str
    filename: str
    extracted_at: str
    raw_text_preview: str
    fields: Dict[str, str]
    line_items: List[Dict[str, str]]


SESSIONS: Dict[str, Dict[str, DocumentData]] = {}

FIELD_PATTERNS = {
    "invoice_number": [r"invoice\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]+)"],
    "packing_list_number": [r"packing\s*list\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]+)"],
    "bl_number": [r"(?:bill\s*of\s*lading|b/?l)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]+)"],
    "po_number": [r"(?:po|purchase\s*order)\s*(?:no|number|#)?\s*[:\-]?\s*([A-Z0-9\-/]+)"],
    "shipper": [r"shipper\s*[:\-]?\s*([^\n]+)"],
    "consignee": [r"consignee\s*[:\-]?\s*([^\n]+)"],
    "origin_country": [r"country\s*of\s*origin\s*[:\-]?\s*([^\n]+)"],
    "destination_country": [r"destination\s*[:\-]?\s*([^\n]+)"],
    "incoterm": [r"incoterm\s*[:\-]?\s*([A-Z]{3})"],
    "currency": [r"currency\s*[:\-]?\s*([A-Z]{3})"],
    "net_weight": [r"net\s*weight\s*[:\-]?\s*([0-9.,]+\s*[A-Z]*)"],
    "gross_weight": [r"gross\s*weight\s*[:\-]?\s*([0-9.,]+\s*[A-Z]*)"],
    "package_count": [r"(?:total\s*)?(?:packages?|cartons?)\s*[:\-]?\s*([0-9.,]+)"],
    "total_value": [r"(?:invoice\s*)?(?:total\s*amount|total\s*value|amount\s*due)\s*[:\-]?\s*([0-9.,]+)"],
    "etd": [r"etd\s*[:\-]?\s*([0-9/\-.]+)"],
    "eta": [r"eta\s*[:\-]?\s*([0-9/\-.]+)"],
}


def extract_text_from_pdf(content: bytes) -> str:
    if not pdfplumber:
        raise RuntimeError("Dependência 'pdfplumber' não disponível para leitura de PDF.")

    text_parts: List[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_fields(raw_text: str) -> Dict[str, str]:
    text = raw_text.lower()
    fields: Dict[str, str] = {}
    for key, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                fields[key] = normalize_spaces(match.group(1))
                break
    return fields


def parse_line_items(raw_text: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for line in raw_text.splitlines():
        clean = normalize_spaces(line)
        if not clean:
            continue
        # Heurística para linhas de item: código + descrição + quantidade e valor ao final
        if re.search(r"\b\d+(?:[.,]\d+)?\b", clean) and len(clean.split()) >= 4:
            qty_match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(pcs|kg|ctn|box|un)?\b", clean, re.IGNORECASE)
            val_match = re.search(r"(\d+[.,]\d{2})\s*$", clean)
            if qty_match or val_match:
                items.append(
                    {
                        "line": clean,
                        "quantity": qty_match.group(1) if qty_match else "",
                        "amount": val_match.group(1) if val_match else "",
                    }
                )
    return items[:30]


def compare_docs(session_docs: Dict[str, DocumentData]) -> Dict[str, object]:
    track_fields = [
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
        "package_count",
        "net_weight",
        "gross_weight",
        "total_value",
        "etd",
        "eta",
    ]

    matrix: List[Dict[str, str]] = []
    divergences: List[str] = []

    for field in track_fields:
        row = {"field": field}
        values = []
        for doc_type in ["invoice", "packing_list", "bl"]:
            val = session_docs.get(doc_type).fields.get(field, "") if session_docs.get(doc_type) else ""
            row[doc_type] = val
            if val:
                values.append(val.lower())
        matrix.append(row)
        if len(set(values)) > 1:
            divergences.append(f"Divergência no campo '{field}': valores diferentes entre documentos.")

    missing_docs = [doc for doc in ["invoice", "packing_list", "bl"] if doc not in session_docs]
    if missing_docs:
        divergences.append(f"Pendência: documentos ausentes para análise cruzada: {', '.join(missing_docs)}")

    status = "Aprovado" if not divergences else "Com divergências"
    return {"status": status, "matrix": matrix, "divergences": divergences}


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/session")
def create_session():
    sid = str(uuid.uuid4())
    SESSIONS[sid] = {}
    return jsonify({"session_id": sid})


@app.post("/api/process/<doc_type>")
def process_doc(doc_type: str):
    if doc_type not in {"invoice", "packing_list", "bl"}:
        return jsonify({"error": "Tipo de documento inválido."}), 400

    sid = request.form.get("session_id")
    if not sid or sid not in SESSIONS:
        return jsonify({"error": "Sessão inválida."}), 400

    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Envie um arquivo PDF válido."}), 400

    content = file.read()
    try:
        text = extract_text_from_pdf(content)
    except Exception as exc:
        return jsonify({"error": f"Falha ao extrair texto do PDF: {exc}"}), 500

    doc = DocumentData(
        doc_type=doc_type,
        filename=file.filename,
        extracted_at=datetime.utcnow().isoformat(),
        raw_text_preview=text[:1500],
        fields=parse_fields(text),
        line_items=parse_line_items(text),
    )
    SESSIONS[sid][doc_type] = doc

    return jsonify({"document": asdict(doc)})


@app.post("/api/analyze")
def analyze():
    payload = request.get_json(silent=True) or {}
    sid = payload.get("session_id")
    if not sid or sid not in SESSIONS:
        return jsonify({"error": "Sessão inválida."}), 400

    result = compare_docs(SESSIONS[sid])
    return jsonify(result)


@app.get("/api/report/<session_id>")
def report(session_id: str):
    if session_id not in SESSIONS:
        return jsonify({"error": "Sessão inválida."}), 400

    result = compare_docs(SESSIONS[session_id])
    report_data = {
        "generated_at": datetime.utcnow().isoformat(),
        "documents": {k: asdict(v) for k, v in SESSIONS[session_id].items()},
        "analysis": result,
    }
    content = json.dumps(report_data, ensure_ascii=False, indent=2)
    mem = io.BytesIO(content.encode("utf-8"))
    mem.seek(0)
    filename = f"relatorio_analise_{session_id[:8]}.json"
    return send_file(mem, as_attachment=True, download_name=filename, mimetype="application/json")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
