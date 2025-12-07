import deque
from typing import Optional
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path


USER_VIDEO_MEMORY_LIMIT = 50


@dataclass
class Video:
    path: Path | str
    # contains frames/metadata

    def load(self):
        # populates frames/metadata
        pass

    def unload(self):
        # clears frames/metadata to save memory
        pass

    @cached_property
    def context(self) -> str:
        # function to convert video frames/metadata into natural language description
        pass


@dataclass
class User:
    # contains user preferences
    # contains list of videos watched (memory limit of 50 videos)
    videos_watched: deque[Video] = field(default_factory=lambda: deque(maxlen=USER_VIDEO_MEMORY_LIMIT))
    cached_context: Optional[str] = field(default=None)

    # function to iterate through watched videos and extra info into natural language description
    def context(self) -> str:
        if self.cached_context is not None:
            return self.cached_context
        # if user hasn't consumed more videos, return cached context
        pass

    def append_video(self, video: Video):
        self.videos_watched.append(video)
        self.cached_context = None  # invalidate cached context


@dataclass
class Product:
    # contains product information

    # function to convert product information into natural language description
    @cached_property
    def context(self) -> str:
        pass