"""Write every contract's JSON Schema and the example search to shared/contracts/.

Run from backend/:  python -m scripts.export_contracts

The frontend turns the schemas into TypeScript types (see frontend/README.md) and uses the
examples as mock data, so both sides always agree on the same shapes. CI re-runs this script
and fails if the committed files are out of date.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

import app.contracts as C
from app.contracts import examples

OUT = Path(__file__).resolve().parents[2] / "shared" / "contracts"


def _dump(obj: object) -> object:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_dump(o) for o in obj]
    raise TypeError(f"cannot export {type(obj)!r}")


def _write(path: Path, text: str) -> None:
    # Always UTF-8 with LF, whatever the OS default: the examples contain the rupee sign, and the
    # committed files must come out byte-identical on Windows and Linux.
    data = text.encode("utf-8")
    # A file that is already right is left alone, so its timestamp stays older than the frontend types made from
    # it (scripts/doctor.py and the frontend's contract-types script compare the two).
    if path.is_file() and path.read_bytes() == data:
        return
    path.write_bytes(data)


def main(out: Path = OUT) -> int:
    (out / "schema").mkdir(parents=True, exist_ok=True)
    (out / "examples").mkdir(parents=True, exist_ok=True)

    models = sorted(
        (v for v in vars(C).values() if isinstance(v, type) and issubclass(v, C.Contract) and v is not C.Contract),
        key=lambda m: m.__name__,
    )
    for model in models:
        schema = model.model_json_schema(mode="serialization")
        _write(out / "schema" / f"{model.__name__}.json", json.dumps(schema, indent=2, ensure_ascii=False) + "\n")

    for name, build in examples.ALL_EXAMPLES.items():
        data = _dump(build())
        _write(out / "examples" / f"{name}.json", json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    _write(out / "VERSION", C.SCHEMA_VERSION + "\n")
    print(f"wrote {len(models)} schemas and {len(examples.ALL_EXAMPLES)} examples to {out}")
    return 0


if __name__ == "__main__":
    # No options: this only makes --help answer (tests/unit/test_imports.py) before anything is written.
    argparse.ArgumentParser(
        prog="python -m scripts.export_contracts",
        description="Write the contracts' JSON schemas and examples to shared/contracts.",
    ).parse_args()
    sys.exit(main())
