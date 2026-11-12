from hashlib import sha256
import json
from pathlib import Path
import unittest

import asciiunits
import unitsml

FIXTURE = Path(__file__).with_name("f40-quantities.json")


def operand(value=None, expression=None, **fields):
    if value is not None:
        fields["value"] = value
    if expression is not None:
        fields["expression"] = expression
    return fields


def run(op, left, right=None, **fields):
    request = {"op": op, "left": left, **fields}
    if right is not None:
        request["right"] = right
    return unitsml.evaluate(request)


def exact_ratio(text):
    return text if "/" in text else text + "/1"


def result_operand(value):
    return operand(value["magnitude"], terms=value["terms"], difference=value["scale"] == "difference")


class InstalledQuantityTests(unittest.TestCase):
    def assert_quantity(self, result, expected, unit=None, scale=None):
        self.assertEqual(exact_ratio(result["magnitude"]), expected["value"]["rational"])
        self.assertEqual(result["dimension_id"], expected["dimension_id"])
        self.assertEqual(result["dimensions"], {
            key: str(power) for key, power in (expected["dimension_vector"] or {}).items()
        })
        self.assertEqual(result["scale"], scale or expected["scale"])
        if unit is None:
            self.assertEqual(result["terms"], [])
        else:
            target = run("resolve", operand("1", unit))
            self.assertEqual(result["terms"], target["terms"])

    def test_f40_source_cases(self):
        data = FIXTURE.read_bytes()
        self.assertEqual(sha256(data).hexdigest(), "2cacb0b5b3c66f7c8a960b73e638b892afb86dd44ee4838371f132c2c5b60218")
        cases = json.loads(data)["cases"]
        self.assertEqual(len(cases), 39)
        for row in cases:
            with self.subTest(case=row["id"]):
                self.replay(row)

    def replay(self, row):
        identity, kind, given = row["id"], row["kind"], row["input"]
        expected = row.get("result")
        if kind == "convert":
            result = run("convert", operand(given["value"], given["unit"]),
                         operand(expression=given["target"]))
            self.assert_quantity(result, expected, unit=given["target"])
        elif kind == "dimension":
            result = run("resolve", operand(given.get("value", "1"), given["unit"]))
            if identity == "dim-1":
                # The historical Ruby call crashed; the structural profile has a dimensionless unit.
                self.assertEqual(row["error_class"], "NoMethodError")
                self.assertEqual(result, {
                    "magnitude": "1", "canonical_magnitude": "1", "dimension_id": None,
                    "dimensions": {}, "scale": "ratio",
                    "terms": [{"unit": "NISTu61", "prefix": None, "power": "1"}],
                })
            else:
                self.assert_quantity(result, expected, unit=given["unit"])
        elif kind == "difference":
            result = run("subtract", operand(*given["high"]), operand(*given["low"]))
            converted = run("convert", result_operand(result), operand(expression=given["target"]))
            # The source records lost scale typing; preserve the exact difference on conversion.
            self.assertEqual(expected["scale"], "ratio")
            self.assert_quantity(converted, expected, unit=given["target"], scale="difference")
        elif kind == "product":
            product = run("multiply", operand(*given["left"]), operand(*given["right"]))
            self.assert_quantity(product, expected["product"], unit=expected["product"]["unit"])
            for field, unit in (("to_W", "W"), ("to_kW", "kW")):
                converted = run("convert", result_operand(product), operand(expression=unit))
                self.assert_quantity(converted, expected[field], unit=unit)
            self.assertFalse(expected["equals_50000_W"])
            self.assertTrue(expected["equals_50_kW"])
            # Physical comparison uses the same exact canonical magnitude in both units.
            self.assertEqual(run("compare", result_operand(product), operand("50000", "W")), 0)
            self.assertEqual(run("compare", result_operand(product), operand("50", "kW")), 0)
        elif kind == "equality":
            self.assertEqual(given, {"left": "10 kV * 5 A", "right": "50 W"})
            self.assertTrue(expected["equal"])
            product = run("multiply", operand("10", "kV"), operand("5", "A"))
            self.assertEqual(run("compare", result_operand(product), operand("50", "W")), 1)
        elif kind == "refusal":
            self.replay_refusal(identity, row["error_class"])
        else:
            self.fail(f"Unsupported F40 case kind {kind}")

    def replay_refusal(self, identity, source_error):
        if identity == "refuse-unknown-unit":
            self.assertEqual(source_error, "Unitsml::Errors::ParseError")
            with self.assertRaises(asciiunits.ParseError):
                run("resolve", operand("1", "notaunit_xyz"))
            return
        choices = {
            "refuse-degc2-to-k2": ("convert", operand("1", "degC^2"), operand(expression="K^2"), {}, "affine_composition", "ConversionError"),
            "refuse-m-to-s": ("convert", operand("1", "m"), operand(expression="s"), {}, "dimension_mismatch", "DimensionMismatchError"),
            "refuse-degc-to-k2": ("convert", operand("1", "degC"), operand(expression="K^2"), {}, "dimension_mismatch", "DimensionMismatchError"),
            "refuse-v-plus-s": ("add", operand("1", "V"), operand("1", "s"), {}, "dimension_mismatch", "DimensionMismatchError"),
            "refuse-degc-times-2": ("scale", operand("10", "degC"), None, {"scalar": "2"}, "scale_operation", "ScaleOperationError"),
            "refuse-degc-times-degc": ("multiply", operand("10", "degC"), operand("2", "degC"), {}, "scale_operation", "ScaleOperationError"),
            "refuse-div-zero-quantity": ("divide", operand("1", "m"), operand("0", "s"), {}, "division_by_zero", "IndeterminateResultError"),
            "refuse-div-zero-scalar": ("divide_by", operand("1", "m"), None, {"scalar": "0"}, "division_by_zero", "IndeterminateResultError"),
            "refuse-both-keywords": ("resolve", operand("1", model={"unitName": "m"}, terms=[]), None, {}, "invalid_request", "ArgumentError"),
        }
        op, left, right, fields, code, source_suffix = choices[identity]
        self.assertEqual(source_error.split("::")[-1], source_suffix)
        error_type = unitsml.IndeterminateError if code == "division_by_zero" else unitsml.EvaluationError
        with self.assertRaises(error_type) as failure:
            run(op, left, right, **fields)
        self.assertEqual(failure.exception.code, code)

    def test_resource_and_syntax_reuse(self):
        with self.assertRaises(unitsml.ResourceLimitError):
            run("resolve", operand("1" * (1024 * 1024)))
        with self.assertRaises(asciiunits.ResourceLimitError):
            run("resolve", operand("1", "m/" * 256 + "m"))
        with self.assertRaises(asciiunits.ParseError) as refusal:
            run("resolve", operand("1", ""))
        self.assertEqual(refusal.exception.byte_offset, 0)
        self.assertEqual(unitsml.evaluate_wire(b"\xff")["error"]["code"], "invalid_request")
        self.assertEqual(run("resolve", operand("1", "m"))["magnitude"], "1")

    def test_known_identity_without_conversion(self):
        left = operand("2", terms=[{"unit": "NISTu230"}])
        self.assertEqual(run("identify", left), {
            "magnitude": "2",
            "terms": [{"unit": "NISTu230", "prefix": None, "power": "1"}],
        })
        with self.assertRaises(unitsml.EvaluationError) as failure:
            run("resolve", left)
        self.assertEqual(failure.exception.code, "missing_conversion")

    def test_structural_operands_and_caller_register(self):
        model = asciiunits.parse("cm^(1/2)")
        by_model = run("resolve", operand("1", model=model))
        by_terms = run("resolve", operand("1", terms=[{"unit": "m", "prefix": "c", "power": "1/2"}]))
        self.assertEqual(by_model, by_terms)
        self.assertEqual(exact_ratio(by_model["canonical_magnitude"]), "1/10")
        for factor, expected in (("50", "100"), ("60", "120")):
            register = [{"unit": "NISTu230", "dimensions": {"Mass": "1"}, "factor": factor}]
            result = run("resolve", operand("2", terms=[{"unit": "NISTu230"}]), register=register)
            self.assertEqual(result["canonical_magnitude"], expected)


if __name__ == "__main__":
    unittest.main()
