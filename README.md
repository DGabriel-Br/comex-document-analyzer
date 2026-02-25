# comex-document-analyzer

Sistema web para análise documental de importação (Invoice, Packing List e Bill of Lading) com extração de dados em JSON e conferência cruzada.

## Funcionalidades
- Upload individual para Invoice, Packing List e B/L em PDF.
- Pipeline de extração em camadas:
  - **Camada A**: alias + regex (regras determinísticas);
  - **Camada B**: regex contextual no texto completo;
  - **Camada C**: fallback com LLM/NER para campos ainda não resolvidos.
- Fallback de OCR para PDFs escaneados quando o texto extraído via `pdfplumber` é insuficiente.
- Quadro comparativo de campos críticos entre os 3 documentos.
- Lista de divergências e pendências (incluindo alerta de baixa confiança de OCR).
- Download de relatório consolidado em **HTML**.

## Stack
- Backend: Flask
- Parsing de PDF: OCR com pypdfium2 + pytesseract
- Frontend: HTML/CSS/JavaScript

## Como executar
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Acesse: `http://localhost:5000`

## Dependências de OCR
Para habilitar OCR de PDFs digitalizados (imagem), é necessário:

1. **Pacotes Python** (já em `requirements.txt`):
   - `pytesseract`
   - `pypdfium2`
2. **Binário de sistema**:
   - `tesseract` instalado e disponível no `PATH`.

Exemplo (Ubuntu/Debian):
```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-por tesseract-ocr-eng
```

## Campos comparados (`COMPARATIVE_FIELDS`)
`document_number`, `issue_or_shipment_date`, `consignee`, `consignee_cnpj`, `shipper`, `total_value`, `po_number`, `goods_description`, `freight_value`, `freight_term`, `incoterm`, `origin_country`, `provenance_country`, `acquisition_country`, `pol`, `pod`, `net_weight`, `gross_weight`, `volume_cbm`, `package_count`, `ncm`.

## Exemplo de payload de campos extraídos
Estrutura retornada por `parse_fields` (com metadados de camada e confiança):

```json
{
  "invoice_number": {
    "value": "INV-2025-000123",
    "source_layer": "A",
    "confidence": 0.92,
    "pending_review": false
  },
  "shipper": {
    "value": "ACME EXPORT LTD",
    "source_layer": "B",
    "confidence": 0.8,
    "pending_review": false
  },
  "freight_term": {
    "value": "PREPAID",
    "source_layer": "C",
    "confidence": 0.7,
    "pending_review": true
  },
  "destination_country": {
    "value": "",
    "source_layer": "unresolved",
    "confidence": 0.0,
    "pending_review": true
  },
  "packing_list_number": {
    "value": "",
    "source_layer": "ignored",
    "confidence": 0.0,
    "pending_review": false
  }
}
```

## Observações
- A extração de texto é feita via OCR (pypdfium2 + pytesseract) e o parser em camadas está em `extractors/field_extractor.py`.
- É necessário ter o binário do Tesseract instalado no sistema para OCR funcionar.
