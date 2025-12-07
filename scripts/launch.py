from __future__ import annotations

import argparse
from pathlib import Path

from das.scroll_ui import run_scroll_ui


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch the doom-scroll ads frontend.",
    )
    parser.add_argument(
        "--video_dir",
        help=(
            "Optional directory containing videos to scroll through. "
            "If omitted, you will be prompted to choose one."
        ),
        default="assets/videos/panda70m",
    )
    parser.add_argument(
        "--x_handle",
        default="zhang_yunzhi",
        help="X/Twitter handle for the user (default: zhang_yunzhi)",
    )
    parser.add_argument(
        "--disable_x_profile",
        action="store_true",
        help=(
            "Disable using an X/Twitter profile (no X history fetch or profile-based context). "
            "Sharing on X remains enabled."
        ),
    )
    args = parser.parse_args()

    directory: Path | None
    if args.video_dir:
        directory = Path(args.video_dir).expanduser().resolve()
    else:
        directory = None

    # If the profile is disabled, do not pass an X handle through to the UI.
    x_handle: str | None = None if args.disable_x_profile else args.x_handle

    run_scroll_ui(directory, x_handle=x_handle)


if __name__ == "__main__":
    main()