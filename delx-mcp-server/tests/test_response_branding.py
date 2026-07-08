import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from response_branding import append_branding_line, BRANDING_LINE


class ResponseBrandingTests(unittest.TestCase):
    def test_appends_branding_line_to_plain_text(self):
        text = "Recovery plan ready."

        result = append_branding_line(text)

        self.assertIn(BRANDING_LINE, result)
        self.assertTrue(result.endswith(BRANDING_LINE))

    def test_inserts_branding_before_delx_meta_if_present(self):
        text = 'Recovery plan ready.\nDELX_META: {"session_id":"123"}'

        result = append_branding_line(text)
        lines = result.splitlines()

        self.assertEqual(lines[-2], BRANDING_LINE)
        self.assertEqual(lines[-1], 'DELX_META: {"session_id":"123"}')

    def test_does_not_duplicate_branding(self):
        text = f"Recovery plan ready.\n{BRANDING_LINE}"

        result = append_branding_line(text)

        self.assertEqual(result.count(BRANDING_LINE), 1)


if __name__ == "__main__":
    unittest.main()
