from collections import deque
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Optional

from PIL import Image
from xai_sdk.chat import user as chat_user, image as chat_image

from das.utils import create_chat, encode_base64


USER_VIDEO_MEMORY_LIMIT = 50
PRODUCT_IMAGE_RESIZE_DIM = (1024, 1024)


@dataclass
class Video:
    path: Path

    @cached_property
    def context(self) -> str:
        path = self.path.parent / (self.path.stem + '_caption.txt')
        assert path.exists(), f"Caption file {path} does not exist"
        with open(path, 'r') as f:
            caption = f.read().strip(' \n\t')
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