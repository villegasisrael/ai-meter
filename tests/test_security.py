import unittest

from ai_meter.security import redact_sensitive


class SecurityTests(unittest.TestCase):
    def test_redacts_sensitive_keys(self) -> None:
        data = {
            "api_key": "abc",
            "Authorization": "Bearer something",
            "nested": {"token": "123", "ok": "yes"},
        }
        redacted = redact_sensitive(data)
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["Authorization"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["token"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["ok"], "yes")


if __name__ == "__main__":
    unittest.main()
