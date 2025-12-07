from collections import deque
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Optional
import json

from PIL import Image
from xai_sdk.chat import user as chat_user, image as chat_image

from das.utils import create_chat, encode_base64


USER_VIDEO_MEMORY_LIMIT = 50
PRODUCT_IMAGE_RESIZE_DIM = (1024, 1024)
DEFAULT_USER_STATS_PATH = Path("assets/logs/user.json")


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
            video_context = f"Context: {video.context}, Heart: {reaction.heart}, Share: {reaction.share}"
            contexts.append(video_context)
        contexts_combined = "\n".join(contexts)

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

    This is intended for the "many ads" case: you call this once after a scroll
    session, then reuse the resulting User for multiple ad generations.
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

        # Preferred: explicit heart/share flags.
        has_heart = "heart" in entry
        has_share = "share" in entry

        heart = bool(entry.get("heart", False))
        share = bool(entry.get("share", False))

        # Backwards-compat: if the JSON only has "engagement", map it into flags.
        if not (has_heart or has_share):
            engagement = entry.get("engagement")
            if isinstance(engagement, str):
                engagement = engagement.upper()
                if engagement == "LIKED_AND_SHARED":
                    heart, share = True, True
                elif engagement == "LIKED":
                    heart, share = True, False
                elif engagement == "SHARED":
                    heart, share = False, True
                else:
                    heart, share = False, False

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