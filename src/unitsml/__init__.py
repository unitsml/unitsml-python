"""Exact UnitsML quantities backed by the native C ABI."""

from collections.abc import Mapping
from ctypes import CDLL, c_char_p, c_size_t, c_uint32, c_void_p, string_at
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path

__all__ = [
    "UnitsMLError", "EvaluationError", "IndeterminateError", "ResourceLimitError",
    "NativeRuntimeError", "evaluate", "evaluate_wire",
]


class UnitsMLError(Exception):
    """Base exception carrying an ABI outcome class and the native error."""

    def __init__(self, outcome_class, error):
        self.outcome_class = outcome_class
        self.error = error
        self.code = error["code"]
        self.detail = error.get("detail")
        super().__init__(self.detail or self.code)


class EvaluationError(UnitsMLError):
    """Typed refusal of the requested operation."""


class IndeterminateError(UnitsMLError):
    """The requested result is indeterminate, for example division by zero."""


class ResourceLimitError(EvaluationError):
    """The native arithmetic budget was reached."""


class NativeRuntimeError(UnitsMLError):
    """The native library or its reply could not be used."""


def _runtime_error(message):
    return NativeRuntimeError("error", {"code": "native_runtime", "detail": message})


@lru_cache(maxsize=1)
def _library():
    override = os.environ.get("UNITSML_CDYLIB")
    try:
        if override:
            path = Path(override).resolve(strict=True)
        else:
            native = Path(__file__).parent / "_native"
            info = json.loads((native / "build-info.json").read_bytes())
            path = native / info["library"]
            if sha256(path.read_bytes()).hexdigest() != info["sha256"]:
                raise _runtime_error("Packaged UnitsML library failed checksum verification")
        library = CDLL(str(path))
        library.unitsml_abi_version.argtypes = []
        library.unitsml_abi_version.restype = c_uint32
        if library.unitsml_abi_version() != 1:
            raise _runtime_error("Unsupported UnitsML C ABI version")
        library.unitsml_evaluate_json_v1.argtypes = [c_char_p, c_size_t]
        library.unitsml_evaluate_json_v1.restype = c_void_p
        library.unitsml_free_json_v1.argtypes = [c_void_p]
        library.unitsml_free_json_v1.restype = None
        return library
    except (OSError, AttributeError, KeyError, ValueError) as error:
        raise _runtime_error(f"Could not load UnitsML runtime: {error}") from error


def evaluate_wire(request):
    """Send UTF-8 JSON to the ABI and return its complete outcome envelope."""
    if isinstance(request, str):
        request = request.encode("utf-8")
    if not isinstance(request, bytes):
        raise TypeError("UnitsML wire request must be str or bytes")
    library = _library()
    pointer = library.unitsml_evaluate_json_v1(request, len(request))
    if not pointer:
        raise _runtime_error("UnitsML returned a null response")
    try:
        reply = json.loads(string_at(pointer))
    except (UnicodeError, ValueError) as error:
        raise _runtime_error("UnitsML returned invalid JSON") from error
    finally:
        library.unitsml_free_json_v1(pointer)
    if not isinstance(reply, dict) or reply.get("abi_version") != 1:
        raise _runtime_error("UnitsML returned an invalid ABI envelope")
    outcome = reply.get("class")
    if outcome == "value" and "value" in reply:
        return reply
    error = reply.get("error")
    if outcome in ("error", "indeterminate") and isinstance(error, dict) and isinstance(error.get("code"), str):
        return reply
    raise _runtime_error("UnitsML returned an invalid outcome envelope")


def _structural_request(request):
    if not isinstance(request, Mapping):
        raise TypeError("UnitsML request must be a mapping")
    result = dict(request)
    for side in ("left", "right"):
        operand = result.get(side)
        if not isinstance(operand, Mapping) or "expression" not in operand:
            continue
        operand = dict(operand)
        expression = operand.pop("expression")
        if "model" in operand or "terms" in operand:
            raise EvaluationError("error", {
                "code": "unsupported_operand",
                "detail": "expression, model and terms are mutually exclusive",
            })
        from asciiunits import parse
        operand["model"] = parse(expression)
        result[side] = operand
    return result


def evaluate(request):
    """Evaluate exact string operands and raise typed native refusals.

    Textual operands use the installed AsciiUnits parser. Structural
    operands may supply a model or resolved terms directly.
    """
    source = json.dumps(_structural_request(request), allow_nan=False, ensure_ascii=False)
    reply = evaluate_wire(source)
    outcome = reply["class"]
    if outcome == "value":
        return reply["value"]
    error = reply["error"]
    if outcome == "indeterminate":
        raise IndeterminateError(outcome, error)
    if error["code"] == "resource_limit":
        raise ResourceLimitError(outcome, error)
    raise EvaluationError(outcome, error)
