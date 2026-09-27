import argparse
import json
from pathlib import Path

from .providers import generate_profiles


def main() -> None:
    parser = argparse.ArgumentParser(description="Artist Lead Finder development data")
    parser.add_argument("--generate", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.count <= 100000:
        parser.error("count must be between 1 and 100000")
    args.generate.write_text(
        json.dumps(
            [p.model_dump(mode="json") for p in generate_profiles(args.count)], ensure_ascii=False
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
