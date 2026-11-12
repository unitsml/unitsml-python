import json
from pathlib import Path
import unittest

import unitsml

VECTORS = json.loads(Path(__file__).with_name("register-scope-vectors.json").read_bytes())


def convert(case):
    return unitsml.evaluate({
        "op": "convert",
        "register": case["register"],
        "left": {"value": case["value"], "terms": [{"unit": case["unit"]}]},
        "right": {"expression": case["target"]},
    })


class InstalledRegisterScopeTests(unittest.TestCase):
    """Caller registrations reach only the unit the request names, as in unitsml-ruby."""

    def test_vectors_follow_the_ruby_reference(self):
        self.assertEqual(VECTORS["profile"], "unitsml-register-scope/v1")
        self.assertEqual([case["id"] for case in VECTORS["cases"]],
                         ["register-direct-lb", "register-chained-cwt-us-refused"])
        for case in VECTORS["cases"]:
            with self.subTest(case=case["id"]):
                expected = case["ruby"]
                if expected["outcome"] == "ok":
                    result = convert(case)
                    self.assertEqual(result["magnitude"], expected["converted_value"])
                    target = unitsml.evaluate({"op": "resolve", "left": {"value": "1", "expression": case["target"]}})
                    self.assertEqual(result["terms"], target["terms"])
                else:
                    self.assertEqual(expected["error_class"], "Unitsml::Errors::ConversionError")
                    with self.assertRaises(unitsml.EvaluationError) as refusal:
                        convert(case)
                    self.assertEqual(refusal.exception.code, case["native_code"])


if __name__ == "__main__":
    unittest.main()
