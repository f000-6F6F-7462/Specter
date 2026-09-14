"""Export JSON Schema for every broker message. Wired into ``make contracts``."""

import json
import sys
from pathlib import Path

from specter.contracts.messages import MESSAGE_MODELS


def write_json_schemas(out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in MESSAGE_MODELS.items():
        path = out / f"{name}.schema.json"
        schema = model.model_json_schema(by_alias=True)
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    out_dir = args[0] if args else "contracts/jsonschema"
    for path in write_json_schemas(out_dir):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
