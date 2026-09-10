"""One fixed native Performance unit; never a full acceptance report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from uquant.validation.promotion import diagnose_promotion_unit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("h1_sentinel", "h1_2024", "bull"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose_promotion_unit(case=args.case, output=args.output)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
