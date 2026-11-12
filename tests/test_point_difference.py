import json
from pathlib import Path
import unittest

import unitsml

VECTORS = json.loads(Path(__file__).with_name("point-difference-vectors.json").read_bytes())
OPS = {"+": "add", "-": "subtract"}


def operand(side):
    return {"value": side["value"], "expression": side["unit"], "difference": side["difference"]}


def exact_ratio(text):
    return text if "/" in text else text + "/1"


class InstalledPointDifferenceTests(unittest.TestCase):
    """Point and difference sums and differences answer exactly as unitsml-ruby does."""

    def test_all_ruby_cases(self):
        self.assertEqual(VECTORS["profile"], "unitsml-point-difference/v1")
        self.assertEqual(len(VECTORS["cases"]), 24)
        for index, case in enumerate(VECTORS["cases"]):
            with self.subTest(case=index, left=case["left"], op=case["op"], right=case["right"]):
                self.assertEqual(case["outcome"], "ok")
                expected = case["result"]
                result = unitsml.evaluate({"op": OPS[case["op"]], "left": operand(case["left"]),
                                           "right": operand(case["right"])})
                self.assertEqual(exact_ratio(result["magnitude"]), "/".join(expected["value"]))
                self.assertEqual(result["scale"], expected["scale"])
                unit = unitsml.evaluate({"op": "resolve", "left": {"value": "1", "expression": expected["unit"]}})
                self.assertEqual(result["terms"], unit["terms"])


if __name__ == "__main__":
    unittest.main()
