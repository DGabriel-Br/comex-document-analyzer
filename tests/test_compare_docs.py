import unittest
from datetime import datetime

from app import DocumentData, compare_docs


def make_doc(doc_type: str, fields: dict[str, str]) -> DocumentData:
    return DocumentData(
        doc_type=doc_type,
        filename=f"{doc_type}.pdf",
        extracted_at=datetime.utcnow().isoformat(),
        raw_text_preview="",
        fields={
            key: {
                "value": value,
                "source_layer": "A",
                "confidence": 1.0,
                "pending_review": False,
            }
            for key, value in fields.items()
        },
        line_items=[],
        extraction_method="ocr",
        low_ocr_confidence=False,
        ocr_quality=[],
    )


class CompareDocsCompletenessTests(unittest.TestCase):
    def test_low_required_completeness_generates_pendency_and_status(self):
        session_docs = {
            "invoice": make_doc(
                "invoice",
                {
                    "invoice_number": "INV-001",
                    "issue_date": "2024-01-01",
                },
            )
        }

        result = compare_docs(session_docs)

        self.assertEqual(result["status"], "Com divergências")
        self.assertTrue(
            any("Pendência de completude obrigatória" in item for item in result["divergences"])
        )
        self.assertTrue(result["completeness"]["invoice"]["below_required_minimum"])

    def test_high_completeness_without_mismatch_is_approved(self):
        common = {
            "issue_date": "2024-01-01",
            "consignee": "ACME",
            "consignee_cnpj": "12.345.678/0001-90",
            "shipper": "EXPORT LTDA",
            "total_value": "1000",
            "po_number": "PO-1",
            "goods_description": "PARTS",
            "freight_value": "100",
            "freight_term": "PREPAID",
            "incoterm": "FOB",
            "origin_country": "BR",
            "provenance_country": "BR",
            "acquisition_country": "BR",
            "pol": "SANTOS",
            "pod": "HAMBURG",
            "net_weight": "90",
            "gross_weight": "100",
            "volume_cbm": "1.2",
            "package_count": "10",
            "ncm": "01010101",
        }
        invoice = make_doc("invoice", {"invoice_number": "INV-001", **common})
        packing = make_doc("packing_list", {"packing_list_number": "INV-001", **common})
        bl = make_doc("bl", {"bl_number": "INV-001", **common})

        result = compare_docs({"invoice": invoice, "packing_list": packing, "bl": bl})

        self.assertEqual(result["status"], "Aprovado")
        self.assertFalse(result["divergences"])


if __name__ == "__main__":
    unittest.main()
