from __future__ import annotations

import io
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List

from flask import Flask, jsonify, make_response, render_template, request

from extractors.field_extractor import parse_fields

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover
    pdfium = None

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024


@dataclass
class DocumentData:
    doc_type: str
    filename: str
    extracted_at: str
    raw_text_preview: str
    fields: Dict[str, Dict[str, Any]]
    line_items: List[Dict[str, str]]


SESSIONS: Dict[str, Dict[str, DocumentData]] = {}


COMPARATIVE_FIELDS: List[Dict[str, str]] = [
    {"key": "document_number", "label": "Número do documento"},
    {"key": "issue_or_shipment_date", "label": "Data de Emissão / Embarque"},
    {"key": "consignee", "label": "Importador / Consignee"},
    {"key": "consignee_cnpj", "label": "CNPJ do Importador / Consignee"},
    {"key": "shipper", "label": "Exportador / Shipper"},
    {"key": "total_value", "label": "Valor total das invoices"},
    {"key": "po_number", "label": "Número da Ordem de Compra"},
    {"key": "goods_description", "label": "Descrição da Mercadoria"},
    {"key": "freight_value", "label": "Valor do frete"},
    {"key": "freight_term", "label": "Condição do frete"},
    {"key": "incoterm", "label": "INCOTERM"},
    {"key": "origin_country", "label": "País de Origem"},
    {"key": "provenance_country", "label": "País de Procedência"},
    {"key": "acquisition_country", "label": "País de Aquisição"},
    {"key": "pol", "label": "Porto de carregamento (POL)"},
    {"key": "pod", "label": "Porto de descarga (POD)"},
    {"key": "net_weight", "label": "Peso líquido total"},
    {"key": "gross_weight", "label": "Peso bruto total"},
    {"key": "volume_cbm", "label": "Cubagem"},
    {"key": "package_count", "label": "Quantidade de Volumes"},
    {"key": "ncm", "label": "NCMs"},
]


def _extract_text_pdf_ocr(content: bytes) -> str:
    if not pdfium or not pytesseract:
        return ""

    text_parts: List[str] = []
    pdf = pdfium.PdfDocument(io.BytesIO(content))
    for page_index in range(len(pdf)):
        page = pdf[page_index]
        image = page.render(scale=2.2).to_pil()
        try:
            page_text = pytesseract.image_to_string(image, lang="por+eng")
        except Exception:
            page_text = pytesseract.image_to_string(image, lang="eng")
        text_parts.append(page_text or "")
    return "\n".join(text_parts)


def extract_text_from_pdf(content: bytes) -> str:
    text = _extract_text_pdf_ocr(content)

    if not normalize_spaces(text):
        raise RuntimeError(
            "Não foi possível extrair texto do PDF via OCR. "
            "Valide se OCR está disponível (pytesseract + pypdfium2 + binário tesseract)."
        )

    return text


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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


def _get_value_for_comparative_field(doc: DocumentData, doc_type: str, field_key: str) -> str:
    if field_key == "document_number":
        fallback_order = {
            "invoice": ["invoice_number", "document_number"],
            "packing_list": ["packing_list_number", "document_number"],
            "bl": ["bl_number", "document_number"],
        }
        for key in fallback_order.get(doc_type, ["document_number"]):
            value = doc.fields.get(key, {}).get("value", "")
            if value:
                return value
        return ""

    if field_key == "issue_or_shipment_date":
        for key in ["issue_date", "shipment_date", "issue_or_shipment_date", "etd", "eta"]:
            value = doc.fields.get(key, {}).get("value", "")
            if value:
                return value
        return ""

    return doc.fields.get(field_key, {}).get("value", "")


def compare_docs(session_docs: Dict[str, DocumentData]) -> Dict[str, object]:
    matrix: List[Dict[str, str]] = []
    divergences: List[str] = []

    for field_meta in COMPARATIVE_FIELDS:
        field_key = field_meta["key"]
        row = {"field": field_meta["label"]}
        values = []
        for doc_type in ["invoice", "packing_list", "bl"]:
            doc = session_docs.get(doc_type)
            val = _get_value_for_comparative_field(doc, doc_type, field_key) if doc else ""
            row[doc_type] = val
            if val:
                values.append(val.lower())
        matrix.append(row)
        if len(set(values)) > 1:
            divergences.append(f"Divergência no campo '{field_meta['label']}': valores diferentes entre documentos.")

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
        fields=parse_fields(text, doc_type=doc_type),
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

    html = render_template("report.html", report=report_data)
    response = make_response(html)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=relatorio_analise_{session_id[:8]}.html"
    return response


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
