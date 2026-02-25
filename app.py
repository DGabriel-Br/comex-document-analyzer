from __future__ import annotations

import io
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List

from flask import Flask, jsonify, make_response, render_template, request

from extractors.field_extractor import CANONICAL_FIELDS, parse_fields

try:
    import pdfplumber
except Exception:  # pragma: no cover
    pdfplumber = None

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


def _extract_text_pdfplumber(content: bytes) -> str:
    if not pdfplumber:
        return ""

    text_parts: List[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def _extract_text_ocr(content: bytes) -> str:
    if not pdfium or not pytesseract:
        return ""

    text_parts: List[str] = []
    pdf = pdfium.PdfDocument(io.BytesIO(content))
    for page_index in range(len(pdf)):
        page = pdf[page_index]
        image = page.render(scale=2.2).to_pil()
        page_text = pytesseract.image_to_string(image, lang="por+eng")
        text_parts.append(page_text or "")
    return "\n".join(text_parts)


def extract_text_from_pdf(content: bytes) -> str:
    text = _extract_text_pdfplumber(content)

    # Fallback OCR para PDFs escaneados (texto inexistente/insuficiente)
    if len(normalize_spaces(text)) < 30:
        ocr_text = _extract_text_ocr(content)
        if normalize_spaces(ocr_text):
            return ocr_text

    if not normalize_spaces(text):
        raise RuntimeError(
            "Não foi possível extrair texto do PDF. "
            "Se for documento escaneado, valide se OCR está disponível (pytesseract + pypdfium2 + binário tesseract)."
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


def compare_docs(session_docs: Dict[str, DocumentData]) -> Dict[str, object]:
    matrix: List[Dict[str, str]] = []
    divergences: List[str] = []

    for field in CANONICAL_FIELDS:
        row = {"field": field}
        values = []
        for doc_type in ["invoice", "packing_list", "bl"]:
            field_data = session_docs.get(doc_type).fields.get(field, {}) if session_docs.get(doc_type) else {}
            val = field_data.get("value", "")
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

    html = render_template("report.html", report=report_data)
    response = make_response(html)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=relatorio_analise_{session_id[:8]}.html"
    return response


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
