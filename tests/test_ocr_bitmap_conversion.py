import unittest
from unittest.mock import Mock, patch

import app


class BitmapToPilTests(unittest.TestCase):
    def test_bitmap_to_pil_uses_fromarray_when_to_pil_fails(self):
        bitmap = Mock()
        bitmap.to_pil.side_effect = RuntimeError("to_pil failed")
        bitmap.to_numpy.return_value = object()

        fake_image_module = Mock()
        fake_image = object()
        fake_image_module.fromarray.return_value = fake_image

        with patch.object(app, "Image", fake_image_module):
            result = app._bitmap_to_pil(bitmap)

        self.assertIs(result, fake_image)
        bitmap.to_pil.assert_called_once()
        bitmap.to_numpy.assert_called_once()
        fake_image_module.fromarray.assert_called_once_with(bitmap.to_numpy.return_value)


class RenderPageToPilTests(unittest.TestCase):
    def test_render_page_to_pil_keeps_flow_when_to_pil_fails(self):
        bitmap = Mock()
        bitmap.to_pil.side_effect = RuntimeError("to_pil failed")
        bitmap.to_numpy.return_value = object()

        page = Mock()
        page.render.return_value = bitmap

        fake_image_module = Mock()
        fake_image = object()
        fake_image_module.fromarray.return_value = fake_image

        with patch.object(app, "Image", fake_image_module):
            result = app._render_page_to_pil(page)

        self.assertIs(result, fake_image)
        page.render.assert_called_once_with(scale=2.2)
        bitmap.to_pil.assert_called_once()
        bitmap.to_numpy.assert_called_once()
        fake_image_module.fromarray.assert_called_once_with(bitmap.to_numpy.return_value)


if __name__ == "__main__":
    unittest.main()
