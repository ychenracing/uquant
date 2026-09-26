"""Generated rule inventory: every governed setting with its production read sites.

A setting is a market rule, safety bound, economic rule, derived value or
compatibility field according to the reviewed governance; this tool adds only
where production code reads it. Settings without any production read are
listed for review, not deleted automatically: serialization, identity and
historical readers may still depend on them.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from uquant.config_governance import load_config_governance

ROOT = Path(__file__).resolve().parents[1]


def read_sites(package: Path, names: set[str]) -> dict[str, list[str]]:
    """Map each setting name to ``module:line`` attribute reads under ``package``."""
    sites: dict[str, list[str]] = defaultdict(list)
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(package.parent).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in names and isinstance(node.ctx, ast.Load):
                sites[node.attr].append(f"{relative}:{node.lineno}")
    return sites


def build_inventory(root: Path = ROOT) -> dict[str, Any]:
    governance = load_config_governance()
    names = {entry.field for entry in governance.entries}
    sites = read_sites(root / "uquant", names)
    rows = [
        {
            "field": entry.field,
            "category": entry.category.value,
            "owner": entry.owner.value,
            "production_reads": len(sites.get(entry.field, [])),
            "modules": sorted({site.rsplit(":", 1)[0] for site in sites.get(entry.field, [])}),
        }
        for entry in governance.entries
    ]
    by_category = Counter(entry.category.value for entry in governance.entries)
    return {
        "total_settings": len(rows),
        "by_category": dict(sorted(by_category.items())),
        "unread_in_production": sorted(row["field"] for row in rows if not row["production_reads"]),
        "settings": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    text = json.dumps(build_inventory(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
