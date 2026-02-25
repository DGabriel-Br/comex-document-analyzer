import unittest

from extractors.field_extractor import _extract_from_window, layer_b_context


class FieldExtractorContextTests(unittest.TestCase):
    def test_extract_from_window_without_delimiter_uses_strong_tokens(self):
        candidate = _extract_from_window(
            ["INVOICE NO INV-2024-0001"],
            "invoice_number",
            aliases=["invoice no", "invoice number"],
            anchor_index=0,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.value, "INV-2024-0001")

    def test_layer_b_context_supports_vertical_key_value_without_separator(self):
        raw_text = "\n".join(
            [
                "INVOICE NUMBER",
                "INV-2024-0001",
                "CNPJ DO IMPORTADOR",
                "12.345.678/0001-95",
            ]
        )

        resolved = layer_b_context(raw_text, {}, ["invoice_number", "consignee_cnpj"])

        self.assertEqual(resolved["invoice_number"].value, "INV-2024-0001")
        self.assertEqual(resolved["consignee_cnpj"].value, "12.345.678/0001-95")

    def test_layer_b_context_fallback_when_alias_is_previous_line(self):
        raw_text = "\n".join(
            [
                "B/L NUMBER",
                "ABCD12345",
                "NET WEIGHT",
                "1250 KG",
            ]
        )

        resolved = layer_b_context(raw_text, {}, ["bl_number", "net_weight"])

        self.assertEqual(resolved["bl_number"].value, "ABCD12345")
        self.assertEqual(resolved["net_weight"].value, "1250 KG")


if __name__ == "__main__":
    unittest.main()
