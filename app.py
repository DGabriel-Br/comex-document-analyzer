from __future__ import annotations

import io
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from flask import Flask, jsonify, make_response, render_template, request

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

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

OCR_CONFIDENCE_THRESHOLD = 0.6
OCR_MIN_VALID_WORDS_PER_PAGE = 5
OCR_LANG = (os.getenv("OCR_LANG") or "por+eng").strip() or "por+eng"


@dataclass
class OCRPageMetric:
    page_number: int
    characters: int
    valid_words: int
    estimated_confidence: float
    rotation_applied: float


@dataclass
class DocumentData:
    doc_type: str
    filename: str
    extracted_at: str
    raw_text_preview: str
    fields: Dict[str, Dict[str, Any]]
    line_items: List[Dict[str, str]]
    extraction_method: str
    low_ocr_confidence: bool
    ocr_quality: List[OCRPageMetric]


SESSIONS: Dict[str, Dict[str, DocumentData]] = {}

OCR_LANG = (os.getenv("OCR_LANG") or "por+eng").strip() or "por+eng"


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


def _extract_text_pdfium_native(content: bytes) -> str:
    if not pdfium:
        return ""

    snippets: List[str] = []
    try:
        pdf = pdfium.PdfDocument(io.BytesIO(content))
    except Exception:
        return ""

    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            text_page = None
            try:
                text_page = page.get_textpage()
                text = text_page.get_text_range() or ""
                snippets.append(text)
            except Exception:
                snippets.append("")
            finally:
                try:
                    if text_page is not None:
                        text_page.close()
                except Exception:
                    pass
                try:
                    page.close()
                except Exception:
                    pass
    finally:
        try:
            pdf.close()
        except Exception:
            pass

    return "\n".join(snippets)


def _bitmap_to_pil(bitmap):
    try:
        image = bitmap.to_pil()
        if image is not None:
            return image
    except Exception:
        pass

    if Image is not None:
        try:
            arr = bitmap.to_numpy()
            if arr is not None:
                if getattr(arr, "ndim", 0) > 3:
                    arr = arr[..., :3]
                if getattr(arr, "ndim", 0) == 3 and arr.shape[-1] > 4:
                    arr = arr[..., :3]
                return Image.fromarray(arr)
        except Exception:
            pass

    return None


def _render_page_to_pil(page):
    last_error = None
    render_attempts = [
        {"scale": 2.2},
        {"scale": 2.0, "rotation": 0},
        {"scale": 1.5},
    ]

    for kwargs in render_attempts:
        try:
            bitmap = page.render(**kwargs)
            if bitmap is None:
                continue
            image = _bitmap_to_pil(bitmap)
            if image is not None:
                return image
        except Exception as exc:  # pragma: no cover
            last_error = exc
            continue

    if last_error:
        raise RuntimeError(f"Falha ao renderizar página para OCR: {last_error}")
    raise RuntimeError("Falha ao renderizar página para OCR: resultado vazio.")


def _extract_text_pdf_ocr(content: bytes) -> str:
    if not pdfium or not pytesseract:
        return ""

    text_parts: List[str] = []
    pdf = pdfium.PdfDocument(io.BytesIO(content))
    page_errors: List[str] = []

    try:
        for page_index in range(len(pdf)):
            page = pdf[page_index]
            try:
                image = _render_page_to_pil(page)
                try:
                    page_text = pytesseract.image_to_string(image, lang=OCR_LANG)
                except Exception:
                    page_text = pytesseract.image_to_string(image, lang="eng")
                text_parts.append(page_text or "")
            except Exception as exc:  # pragma: no cover
                page_errors.append(f"página {page_index + 1}: {exc}")
                text_parts.append("")
            finally:
                try:
                    page.close()
                except Exception:
                    pass
    finally:
        try:
            pdf.close()
        except Exception:
            pass

    combined = "\n".join(text_parts)
    if not normalize_spaces(combined) and page_errors:
        raise RuntimeError("; ".join(page_errors))

    return combined


def extract_text_from_pdf(content: bytes) -> str:
    native_text = _extract_text_pdfium_native(content)
    if len(normalize_spaces(native_text)) >= 30:
        return native_text

    text = _extract_text_pdf_ocr(content)

    if not normalize_spaces(text):
        raise RuntimeError(
            "Não foi possível extrair texto do PDF. "
            "Tentamos extração nativa e OCR, mas o arquivo retornou erro de renderização/texto."
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
    doc_completeness: Dict[str, Dict[str, Any]] = {
        doc_type: {
            "total_comparative_fields": len(COMPARATIVE_FIELDS),
            "filled_comparative_fields": 0,
            "comparative_completeness_ratio": 0.0,
            "minimum_comparative_ratio": MIN_COMPARATIVE_COMPLETENESS_RATIO,
            "total_required_fields": len(REQUIRED_FIELDS_BY_DOC.get(doc_type, [])),
            "filled_required_fields": 0,
            "required_completeness_ratio": 0.0,
            "minimum_required_ratio": MIN_REQUIRED_COMPLETENESS_RATIO,
            "below_minimum": False,
            "below_required_minimum": False,
            "required_fields": REQUIRED_FIELDS_BY_DOC.get(doc_type, []),
            "missing_required_fields": [],
            "document_present": doc_type in session_docs,
        }
        for doc_type in DOC_TYPES
    }

    for field_meta in COMPARATIVE_FIELDS:
        field_key = field_meta["key"]
        row = {"field": field_meta["label"]}
        values = []
        for doc_type in ["invoice", "packing_list", "bl"]:
            doc = session_docs.get(doc_type)
            val = _get_value_for_comparative_field(doc, doc_type, field_key) if doc else ""
            row[doc_type] = val
            if val:
                doc_completeness[doc_type]["filled_comparative_fields"] += 1
            if val:
                values.append(val.lower())
        matrix.append(row)
        if len(set(values)) > 1:
            divergences.append(f"Divergência no campo '{field_meta['label']}': valores diferentes entre documentos.")

    missing_docs = [doc for doc in DOC_TYPES if doc not in session_docs]
    if missing_docs:
        divergences.append(f"Pendência: documentos ausentes para análise cruzada: {', '.join(missing_docs)}")

    low_completeness_detected = False
    for doc_type in DOC_TYPES:
        metrics = doc_completeness[doc_type]
        if not metrics["document_present"]:
            continue

        total_fields = metrics["total_comparative_fields"]
        filled_fields = metrics["filled_comparative_fields"]
        metrics["comparative_completeness_ratio"] = (filled_fields / total_fields) if total_fields else 0.0
        metrics["below_minimum"] = metrics["comparative_completeness_ratio"] < MIN_COMPARATIVE_COMPLETENESS_RATIO

        missing_required_fields = []
        for required_field in metrics["required_fields"]:
            doc = session_docs.get(doc_type)
            value = _get_value_for_comparative_field(doc, doc_type, required_field) if doc else ""
            if not value:
                missing_required_fields.append(required_field)
        metrics["missing_required_fields"] = missing_required_fields
        total_required_fields = metrics["total_required_fields"]
        metrics["filled_required_fields"] = total_required_fields - len(missing_required_fields)
        metrics["required_completeness_ratio"] = (
            metrics["filled_required_fields"] / total_required_fields if total_required_fields else 1.0
        )
        metrics["below_required_minimum"] = (
            metrics["required_completeness_ratio"] < MIN_REQUIRED_COMPLETENESS_RATIO
        )

        if metrics["below_minimum"]:
            low_completeness_detected = True
            divergences.append(
                "Pendência de completude: "
                f"{doc_type} com {filled_fields}/{total_fields} campos comparativos preenchidos "
                f"({metrics['comparative_completeness_ratio']:.0%}), "
                f"abaixo do mínimo de {MIN_COMPARATIVE_COMPLETENESS_RATIO:.0%}."
            )

        if metrics["below_required_minimum"]:
            low_completeness_detected = True
            divergences.append(
                "Pendência de completude obrigatória: "
                f"{doc_type} com {metrics['filled_required_fields']}/{total_required_fields} campos obrigatórios preenchidos "
                f"({metrics['required_completeness_ratio']:.0%}), "
                f"abaixo do mínimo de {MIN_REQUIRED_COMPLETENESS_RATIO:.0%}."
            )

        if missing_required_fields:
            divergences.append(
                "Pendência de campos obrigatórios: "
                f"{doc_type} sem preenchimento de {', '.join(missing_required_fields)}."
            )

    low_quality_docs = [doc.doc_type for doc in session_docs.values() if doc.low_ocr_confidence]
    if low_quality_docs:
        divergences.append(
            f"Alerta de OCR: baixa confiabilidade detectada em {', '.join(low_quality_docs)}. Revisão manual recomendada."
        )

    status = "Com divergências" if divergences or low_completeness_detected else "Aprovado"
    return {
        "status": status,
        "matrix": matrix,
        "divergences": divergences,
        "completeness": doc_completeness,
    }


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
        text, ocr_quality, low_ocr_confidence, extraction_method = extract_text_from_pdf(content)
    except Exception as exc:
        return jsonify({"error": f"Falha ao extrair texto do PDF: {exc}"}), 500

    doc = DocumentData(
        doc_type=doc_type,
        filename=file.filename,
        extracted_at=datetime.utcnow().isoformat(),
        raw_text_preview=text[:1500],
        fields=parse_fields(text, doc_type=doc_type),
        line_items=parse_line_items(text),
        extraction_method=extraction_method,
        low_ocr_confidence=low_ocr_confidence,
        ocr_quality=ocr_quality,
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
