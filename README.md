# comex-document-analyzer

Sistema web para análise documental de importação (Invoice, Packing List e Bill of Lading) com extração de dados em JSON e conferência cruzada.

## Funcionalidades
- Upload individual para Invoice, Packing List e B/L em PDF.
- Processamento por documento com extração de texto e mapeamento para JSON.
- Quadro comparativo de campos críticos entre os 3 documentos.
- Lista de divergências e pendências.
- Download de relatório consolidado em JSON.

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

## Campos comparados
`invoice_number`, `packing_list_number`, `bl_number`, `po_number`, `shipper`, `consignee`, `origin_country`, `destination_country`, `incoterm`, `currency`, `package_count`, `net_weight`, `gross_weight`, `total_value`, `etd`, `eta`.

## Observações
- A extração de texto é feita via OCR (pypdfium2 + pytesseract) e o parser em camadas está em `extractors/field_extractor.py`.
- É necessário ter o binário do Tesseract instalado no sistema para OCR funcionar.
