# UnitsML for Python

`unitsml.evaluate(request)` evaluates exact string magnitudes and unit
operands. The structural request accepts a producer model or terms
containing unit identifiers, optional prefixes and exact powers. For an
`expression` operand, the installed AsciiUnits package supplies that same
structural model. Both packages use their native libraries.

```python
import unitsml

result = unitsml.evaluate({
    "op": "convert",
    "left": {"value": "1", "expression": "km"},
    "right": {"expression": "m"},
})
print(result["magnitude"])
```

The native result preserves exact rational text and quantity metadata.
`evaluate_wire` accepts JSON text or UTF-8 bytes and exposes the full
ABI outcome. `evaluate` raises typed errors carrying the outcome class,
native code and detail. AsciiUnits refusals preserve their byte offsets.

Install the matching AsciiUnits and UnitsML host wheels together. The
UnitsML wheel verifies its native payload digest on load.
