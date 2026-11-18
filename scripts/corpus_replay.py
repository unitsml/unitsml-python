"""Replay the pinned unitsml-testsuite corpora on the installed unitsml wheel.

ci/testsuite-pin.yml pins the same testsuite commit as unitsml-ruby
(spec/fixtures/testsuite-pin.yml). ci/testsuite-replay.json is this
package's own record of what it executes. Before any row runs, the script
checks that the checkout is at the pinned commit and that each corpus file
matches the recorded SHA-256, git blob id and row count.

corpora/conversions: every ``convert`` and ``difference`` row that needs no
caller-supplied conversion definition runs through ``unitsml.evaluate``
(``convert`` and ``subtract`` operations). The result is compared as an exact
rational: the reduced ``numerator/denominator`` text of the native magnitude
must equal that of the Ruby expected decimal. Rows that need caller-supplied
conversion definitions, definition execution, initialUnit resolution or
typed-refusal checks of conversion XML have no counterpart in this package's
API; they are counted per family, not skipped silently, and the counts must
equal the record.

corpora/renders: every row needs unitsml-ruby's norm_text, AsciiMath and
UnitsML XML renderers. This package has none, so all rows are counted as
unsupported.

The executed id set must equal the recorded one. A per-row report is written
to .ci-evidence/corpus-replay-report.json.

Usage: python scripts/corpus_replay.py [--checkout DIR] [--report FILE]
"""
from __future__ import annotations
import argparse
from collections import Counter
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "ci/testsuite-pin.yml"
EXPECTATION = ROOT / "ci/testsuite-replay.json"
REPOSITORY = "https://github.com/unitsml/unitsml-testsuite"
RENDERS = "corpora/renders/expected-renders.json"
CONVERSIONS = "corpora/conversions/expected-conversions.json"


class ReplayError(RuntimeError):
    """The pinned testsuite failed verification or replay."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pin(path=PIN) -> dict:
    pin = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or line.strip() in ("", "---"):
            continue
        match = re.fullmatch(r"(repository|commit): (\S+)", line)
        if not match or match.group(1) in pin:
            raise ReplayError(f"unexpected pin line: {line!r}")
        pin[match.group(1)] = match.group(2)
    if set(pin) != {"repository", "commit"}:
        raise ReplayError("pin must carry exactly repository and commit")
    if pin["repository"] != REPOSITORY:
        raise ReplayError("pin repository is not unitsml/unitsml-testsuite")
    if not re.fullmatch(r"[0-9a-f]{40}", pin["commit"]):
        raise ReplayError("pin commit must be a full 40-hex SHA")
    return pin


def checkout(pin: dict, destination: Path) -> Path:
    """Fetch exactly the pinned commit into a fresh directory."""
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ReplayError(f"{destination} is not empty; replay needs a fresh checkout")
    destination.mkdir(parents=True, exist_ok=True)

    def git(*args):
        return subprocess.run(["git", *args], cwd=destination, check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip()

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("remote", "add", "origin", pin["repository"] + ".git")
    git("fetch", "-q", "--depth", "1", "origin", pin["commit"])
    git("checkout", "-q", "--detach", "FETCH_HEAD")
    return destination


def verify(root: Path, pin: dict, record: dict) -> dict:
    """Check commit, file bytes and row counts; return the parsed corpora."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True,
                          stdout=subprocess.PIPE).stdout.strip()
    if head != pin["commit"] or record["commit"] != pin["commit"]:
        raise ReplayError(f"checkout {head}, pin {pin['commit']}, record {record['commit']} must agree")
    corpora = {}
    for name, expected in record["files"].items():
        data = (root / name).read_bytes()
        if sha256(data) != expected["sha256"]:
            raise ReplayError(f"{name}: sha256 differs from the record")
        blob = subprocess.run(["git", "rev-parse", f"HEAD:{name}"], cwd=root, check=True, text=True,
                              stdout=subprocess.PIPE).stdout.strip()
        if blob != expected["git_blob"]:
            raise ReplayError(f"{name}: git blob differs from the record")
        corpus = json.loads(data)
        ids = [row.get("id", row.get("input")) for row in corpus["cases"]]
        if len(ids) != expected["rows"] or len(set(ids)) != len(ids):
            raise ReplayError(f"{name}: {len(ids)} rows ({len(set(ids))} unique), record says {expected['rows']}")
        corpora[name] = corpus
    return corpora


def classify_conversion(row: dict) -> str | None:
    """Return None when this package can execute the row, else its unsupported family."""
    kind = row["kind"]
    if kind in ("convert", "difference"):
        if row.get("definitions") or row.get("document_units"):
            return f"{kind}-with-caller-definition"
        return None
    if kind in ("definition", "resolve", "error"):
        return kind
    raise ReplayError(f"{row['id']}: unknown conversion kind {kind!r}")


def ratio_text(value) -> str:
    fraction = Fraction(Decimal(value)) if not isinstance(value, Fraction) else value
    return f"{fraction.numerator}/{fraction.denominator}"


def exact_ratio(text: str) -> str:
    return text if "/" in text else text + "/1"


def execute(unitsml, row: dict) -> str:
    """Run one supported row and return the native exact ratio text."""
    def operand(value, expression):
        return {"value": value, "expression": expression}

    if row["kind"] == "convert":
        result = unitsml.evaluate({"op": "convert", "left": operand(row["value"], row["unit_expression"]),
                                   "right": {"expression": row["target"]}})
    else:
        difference = unitsml.evaluate({"op": "subtract", "left": operand(row["value"], row["unit_expression"]),
                                       "right": operand(row["other_value"], row["other_unit_expression"])})
        result = unitsml.evaluate({
            "op": "convert",
            "left": {"value": difference["magnitude"], "terms": difference["terms"],
                     "difference": difference["scale"] == "difference"},
            "right": {"expression": row["target"]}})
    return exact_ratio(result["magnitude"])


def replay(root: Path, record: dict, corpora: dict, unitsml) -> dict:
    report = {"commit": record["commit"], "boundaries": {}, "rows": []}
    renders = corpora[RENDERS]["cases"]
    unsupported = Counter(row["kind"] for row in renders)
    expected = record["files"][RENDERS]
    if dict(unsupported) != expected["unsupported"] or expected["executed"] != 0:
        raise ReplayError(f"{RENDERS}: unsupported counts {dict(unsupported)} differ from the record")
    report["rows"] += [{"file": RENDERS, "id": row["input"], "status": "unsupported", "family": row["kind"]}
                       for row in renders]
    report["boundaries"][RENDERS] = {"rows": len(renders), "executed": 0, "unsupported": dict(unsupported)}

    rows = corpora[CONVERSIONS]["cases"]
    expected = record["files"][CONVERSIONS]
    executed, unsupported, failures = [], Counter(), []
    for row in rows:
        family = classify_conversion(row)
        if family:
            unsupported[family] += 1
            report["rows"].append({"file": CONVERSIONS, "id": row["id"], "status": "unsupported", "family": family})
            continue
        want = ratio_text(row["expected"])
        try:
            got = execute(unitsml, row)
        except Exception as error:  # a refusal here is a failed row, never a skip
            got = f"{type(error).__name__}: {error}"
        executed.append(row["id"])
        status = "pass" if got == want else "fail"
        if status == "fail":
            failures.append(f"{row['id']}: expected {want}, got {got}")
        report["rows"].append({"file": CONVERSIONS, "id": row["id"], "status": status,
                               "expected": want, "actual": got})
    digest = sha256("\n".join(sorted(executed)).encode("utf-8"))
    if (len(executed), digest) != (expected["executed"], expected["executed_ids_sha256"]):
        raise ReplayError(f"{CONVERSIONS}: executed {len(executed)} rows, digest {digest}; "
                          f"record says {expected['executed']}, {expected['executed_ids_sha256']}")
    if dict(unsupported) != expected["unsupported"]:
        raise ReplayError(f"{CONVERSIONS}: unsupported counts {dict(unsupported)} differ from the record")
    report["boundaries"][CONVERSIONS] = {"rows": len(rows), "executed": len(executed),
                                         "failed": len(failures), "unsupported": dict(unsupported)}
    report["failures"] = failures
    return report


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    cli.add_argument("--checkout", help="empty directory for the pinned testsuite (default: a temporary one)")
    cli.add_argument("--report", default=str(ROOT / ".ci-evidence/corpus-replay-report.json"))
    args = cli.parse_args(argv)
    pin = load_pin()
    record = json.loads(EXPECTATION.read_bytes())
    with tempfile.TemporaryDirectory(prefix="unitsml-testsuite-") as scratch:
        root = checkout(pin, Path(args.checkout) if args.checkout else Path(scratch) / "testsuite")
        corpora = verify(root, pin, record)
        import unitsml
        report = replay(root, record, corpora, unitsml)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, summary in report["boundaries"].items():
        print(name, json.dumps(summary, sort_keys=True))
    for failure in report["failures"]:
        print("FAIL", failure)
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
