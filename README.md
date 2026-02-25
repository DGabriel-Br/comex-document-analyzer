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
- Parsing de PDF: pdfplumber
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
- A extração usa heurísticas de regex sobre o texto do PDF; dependendo do layout dos documentos reais, você pode precisar ajustar os padrões em `FIELD_PATTERNS` no arquivo `app.py`.
- Para OCR de PDFs digitalizados (imagem), recomenda-se integrar `pytesseract` + `pdf2image` em etapa complementar.

## Configuração de OCR em produção
- Defina a variável de ambiente `OCR_LANG` para controlar o idioma principal do OCR.
- Exemplo: `OCR_LANG=por+eng` (padrão) ou `OCR_LANG=eng`.
- Quando configurado, o sistema tenta primeiro `OCR_LANG` e, em caso de falha do Tesseract, aplica fallback automático para `eng`.
