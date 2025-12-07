from collections import deque
from dataclasses import dataclass, field
from functools import cached_property
import logging
from pathlib import Path
from typing import Optional
import json

from PIL import Image
from xai_sdk.chat import user as chat_user, image as chat_image

from das.utils import create_chat, encode_base64


USER_VIDEO_MEMORY_LIMIT = 50
PRODUCT_IMAGE_RESIZE_DIM = (1024, 1024)
DEFAULT_USER_STATS_PATH = Path("assets/logs/user.json")
# Minimum watch time (in seconds) for a video to be considered part of the
# user's context, unless it has explicit engagement (like/share).
MIN_SECONDS_FOR_CONTEXT = 5.0


@dataclass
class Video:
    path: Path

    @cached_property
    def context(self) -> str:
        """Return per-video caption/context.

        Some videos may not have an associated .txt file; instead of raising an
        assertion error and breaking the experience, we treat those as having
        empty context so they can be cleanly skipped by downstream logic.
        """
        path = self.path.parent / (self.path.stem + ".txt")
        if not path.exists():
            # Empty string signals "no context"; callers like User.context can
            # decide to skip such videos if desired.
            return ""
        with open(path, "r") as f:
            caption = f.read().strip(" \n\t")
        return caption


@dataclass
class Product:
    path: Path

    @cached_property
    def context(self) -> str:
        caption_path = self.path.parent / (self.path.stem + '_context.txt')
        if caption_path.exists():
            with open(caption_path, 'r') as f:
                caption = f.read().strip(' \n\t')
            return caption

        image = Image.open(self.path)
        image.thumbnail(PRODUCT_IMAGE_RESIZE_DIM)
        image.show()
        chat = create_chat('assets/prompts/extract_context_product.txt')
        chat.append(chat_user(chat_image(encode_base64(image))))
        response = chat.sample().content

        with open(caption_path, 'w') as f:
            f.write(response)
        return response


@dataclass
class UserReaction:
    heart: bool = False
    share: bool = False
    seconds_watched: Optional[float] = None


@dataclass
class User:
    videos_watched: deque[Video] = field(
        default_factory=lambda: deque(maxlen=USER_VIDEO_MEMORY_LIMIT)
    )
    videos_watched_reaction: deque[UserReaction] = field(
        default_factory=lambda: deque(maxlen=USER_VIDEO_MEMORY_LIMIT)
    )
    cached_context: Optional[str] = field(default=None)

    @property
    def context(self) -> str:
        if self.cached_context is not None:
            return self.cached_context
        contexts = []
        for video, reaction in zip(self.videos_watched, self.videos_watched_reaction):
            video_context = f"Context: {video.context}, Heart: {reaction.heart}, Share: {reaction.share}, Seconds Watched: {reaction.seconds_watched}"
            contexts.append(video_context)
        contexts_combined = "\n".join(contexts)
        # logging.info("User Videos Contexts Combined: %s\n", contexts_combined)

        chat = create_chat('assets/prompts/extract_context_user.txt')
        chat.append(chat_user(contexts_combined))
        response = chat.sample().content
        self.cached_context = response
        return response

    def append_video(self, video: Video, user_reaction: UserReaction):
        self.videos_watched.append(video)
        self.videos_watched_reaction.append(user_reaction)
        self.cached_context = None  # invalidate cached context


def build_user_from_stats(stats_path: Path | str = DEFAULT_USER_STATS_PATH) -> User:
    """Reconstruct a User from the JSON stats file written by the scroll UI.

    A video contributes to the resulting User's context if either:
    - it has at least MIN_SECONDS_FOR_CONTEXT seconds of watch time, or
    - it has explicit engagement (heart or share).
    """
    stats_path = Path(stats_path)
    if not stats_path.exists():
        return User()

    try:
        raw = stats_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return User()

    videos_data = payload.get("videos")
    if not isinstance(videos_data, dict):
        return User()

    user = User()

    for entry in videos_data.values():
        if not isinstance(entry, dict):
            continue

        path_str = entry.get("path")
        if not isinstance(path_str, str):
            continue

        video_path = Path(path_str)
        video = Video(path=video_path)

        heart = bool(entry.get("heart", False))
        share = bool(entry.get("share", False))
        seconds_watched = float(entry.get("seconds_watched", 0.0))
        reaction = UserReaction(heart=heart, share=share, seconds_watched=seconds_watched)

        # Skip videos that don't meet the minimum watch time and had no
        # engagement; they shouldn't influence the user's long-term context.
        if seconds_watched < MIN_SECONDS_FOR_CONTEXT and not (heart or share):
            continue

        reaction = UserReaction(heart=heart, share=share)
        user.append_video(video, reaction)

    return user


if __name__ == '__main__':
    video = Video(path=Path('assets/videos/sample.mp4'))
    print(video.context)

    product = Product(path=Path('assets/products/sample.png'))
    print(product.context)

    user = User()
    user.append_video(video, UserReaction(heart=True, share=False))
    print(user.context())
    print('------- Testing cached user context -------')
    print(user.context())