from __future__ import annotations

import argparse
from pathlib import Path

from das.scroll_ui import run_scroll_ui


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch the doom-scroll ads frontend.",
    )
    parser.add_argument(
        "video_dir",
        nargs="?",
        help=(
            "Optional directory containing videos to scroll through. "
            "If omitted, you will be prompted to choose one."
        ),
    )
    args = parser.parse_args()

    directory: Path | None
    if args.video_dir:
        directory = Path(args.video_dir).expanduser().resolve()
    else:
        directory = None

    run_scroll_ui(directory)


if __name__ == "__main__":
    main()
