from collections import deque
from dataclasses import dataclass, field
from functools import cached_property
import logging
from pathlib import Path
from typing import Optional, List
import json
import os

from PIL import Image
from xai_sdk import Client
from xai_sdk.chat import user as chat_user, image as chat_image
from xai_sdk.tools import web_search as chat_web_search, x_search as chat_x_search
from xai_sdk.search import SearchParameters, x_source

from das.utils import create_chat, encode_base64


USER_VIDEO_MEMORY_LIMIT = 50
USER_X_HISTORY_LIMIT = 20  # Max posts/likes/reposts to fetch from X
PRODUCT_IMAGE_RESIZE_DIM = (1024, 1024)
DEFAULT_USER_STATS_PATH = Path("assets/logs/user.json")
DEFAULT_X_HANDLE_PATH = Path("assets/logs/x_handle.txt")
# Minimum watch time (in seconds) for a video to be considered part of the
# user's context, unless it has explicit engagement (like/share).
MIN_SECONDS_FOR_CONTEXT = 5.0

# Fast model for quick lookups (no reasoning overhead)
FAST_MODEL = "grok-3-fast"
NORMAL_MODEL = "grok-4"


@dataclass
class XPost:
    """Represents a post from the user's X history (liked, reposted, or authored)."""
    text: str
    interaction_type: str  # 'liked', 'reposted', 'authored'
    author_handle: Optional[str] = None
    url: Optional[str] = None


@dataclass 
class XHistory:
    """Holds the user's X activity for context."""
    posts: List[XPost] = field(default_factory=list)
    x_handle: Optional[str] = None
    cached_context: Optional[str] = None
    
    @property
    def context(self) -> str:
        """Generate a summarized context from X history."""
        if self.cached_context is not None:
            return self.cached_context
            
        if not self.posts:
            return ""
        
        # Build raw context from posts
        post_contexts = []
        for post in self.posts[:USER_X_HISTORY_LIMIT]:
            ctx = f"[{post.interaction_type.upper()}] {post.text}"
            if post.author_handle:
                ctx = f"[{post.interaction_type.upper()} from @{post.author_handle}] {post.text}"
            post_contexts.append(ctx)
        
        raw_context = "\n".join(post_contexts)
        
        # Use fast Grok model to summarize the X activity into user interests
        try:
            chat = create_chat('assets/prompts/extract_context_x_history.txt', model=FAST_MODEL)
            chat.append(chat_user(raw_context))
            response = chat.sample()
            self.cached_context = response.content
        except Exception as e:
            logging.warning(f"Failed to summarize X history: {e}")
            # Fallback: return truncated raw context
            self.cached_context = raw_context[:2000]
        
        return self.cached_context


def fetch_x_history(x_handle: str) -> XHistory:
    """
    Fetch a user's recent X activity using xAI's Live Search.
    
    This uses xAI's x_source to search for posts from/about the user.
    Note: This can only see PUBLIC activity. Private likes are not accessible
    without X API v2 OAuth authentication.
    
    Args:
        x_handle: The user's X handle (without @)
        
    Returns:
        XHistory object with the user's activity
    """
    x_handle = x_handle.lstrip('@')
    history = XHistory(x_handle=x_handle)
    limit = USER_X_HISTORY_LIMIT  # Use the constant, not a parameter
    
    try:
        client = Client(api_key=os.getenv("XAI_API_KEY"))
        
        # Use fast model with simple Live Search (not agentic tools)
        chat = client.chat.create(
            model=FAST_MODEL,
            search_parameters=SearchParameters(
                mode="on",
                max_search_results=limit,
                sources=[x_source(included_x_handles=[x_handle])],
                return_citations=True,
            ),
        )
        
        prompt = f"""Get the {limit} most recent posts from @{x_handle}.

Return ONLY a JSON array, no other text:
[{{"text": "post content", "type": "authored", "author": null}}, ...]

type = "authored" for their posts, "reposted" for retweets, "liked" for likes.
author = original author handle (without @) if reposted/liked, null if authored."""
        
        chat.append(chat_user(prompt))
        response = chat.sample()
        
        # Parse the response
        try:
            content = response.content.strip()
            # Handle markdown code blocks
            if "```" in content:
                # Extract content between code blocks
                parts = content.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("["):
                        content = part
                        break
            
            # Find the JSON array in the response
            start = content.find("[")
            end = content.rfind("]") + 1
            if start != -1 and end > start:
                content = content[start:end]
            
            posts_data = json.loads(content)
            
            for post_data in posts_data[:limit]:
                post = XPost(
                    text=post_data.get("text", "")[:500],  # Truncate long posts
                    interaction_type=post_data.get("type", "authored"),
                    author_handle=post_data.get("author"),
                )
                if post.text:
                    history.posts.append(post)
                    
        except json.JSONDecodeError as e:
            logging.warning(f"Failed to parse X history JSON: {e}")
            # Store the raw response as a single "context" post
            history.posts.append(XPost(
                text=response.content[:1500],
                interaction_type="summary",
            ))
            
        # Also store citations as additional context
        if hasattr(response, 'citations') and response.citations:
            for url in response.citations[:5]:  # Limit citations too
                history.posts.append(XPost(
                    text=f"Engaged with: {url}",
                    interaction_type="citation",
                    url=url,
                ))
                
    except Exception as e:
        logging.error(f"Failed to fetch X history for @{x_handle}: {e}")
    
    print(f"[X] Fetched {len(history.posts)} posts for @{x_handle}")
    return history


@dataclass
class Video:
    path: Path
    # Optional pointer back to the underlying product for generated ads.
    # For organic (non-ad) videos this will be None.
    product_path: Optional[Path] = None

    @cached_property
    def context(self) -> str:
        """Return per-video caption/context.

        Some videos may not have an associated .txt file; instead of raising an
        assertion error and breaking the experience, we treat those as having
        empty context so they can be cleanly skipped by downstream logic.
        """
        path = self.path.parent / (self.path.stem + ".txt")
        if not path.exists():
            return ""
        with open(path, "r") as f:
            caption = f.read().strip(" \n\t")
        return caption


@dataclass
class Product:
    path: Path

    @cached_property
    def context(self) -> str:
        caption_path = self.path.parent / (self.path.stem + '.txt')
        if caption_path.exists():
            with open(caption_path, 'r') as f:
                caption = f.read().strip(' \n\t')
            return caption

        image = Image.open(self.path)
        image.thumbnail(PRODUCT_IMAGE_RESIZE_DIM)
        chat = create_chat(
            'assets/prompts/extract_context_product.txt',
            model=NORMAL_MODEL,
        )
        chat.append(chat_user(chat_image(encode_base64(image))))
        response = chat.sample()

        with open(caption_path, 'w') as f:
            f.write(response.content)
        return response.content


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
    x_history: Optional[XHistory] = None
    x_handle: Optional[str] = None
    cached_context: Optional[str] = field(default=None)

    @property
    def context(self) -> str:
        if self.cached_context is not None:
            return self.cached_context
            
        context_parts = []
        
        # Part 1: Video watching history
        if self.videos_watched:
            video_contexts = []
            for video, reaction in zip(self.videos_watched, self.videos_watched_reaction):
                video_context = f"Context: {video.context}, Heart: {reaction.heart}, Share: {reaction.share}, Seconds Watched: {reaction.seconds_watched}"
                video_contexts.append(video_context)
            
            if video_contexts:
                contexts_combined = "\n".join(video_contexts)
                chat = create_chat('assets/prompts/extract_context_user.txt', model=FAST_MODEL)
                chat.append(chat_user(contexts_combined))
                response = chat.sample()
                context_parts.append(f"VIDEO PREFERENCES:\n{response.content}")
        
        # Part 2: X history (if available)
        if self.x_history and self.x_history.posts:
            x_context = self.x_history.context
            if x_context:
                context_parts.append(f"X ACTIVITY:\n{x_context}")
        
        # Combine all context parts
        if context_parts:
            self.cached_context = "\n\n".join(context_parts)
        else:
            self.cached_context = ""
            
        return self.cached_context

    def append_video(self, video: Video, user_reaction: UserReaction):
        self.videos_watched.append(video)
        self.videos_watched_reaction.append(user_reaction)
        self.cached_context = None  # invalidate cached context
    
    def set_x_handle(self, handle: str, fetch_history: bool = True):
        """Set the user's X handle and optionally fetch their history."""
        self.x_handle = handle.lstrip('@')
        if fetch_history:
            self.x_history = fetch_x_history(self.x_handle)
        self.cached_context = None  # invalidate cached context
    
    def refresh_x_history(self):
        """Refresh the X history from the API."""
        if self.x_handle:
            self.x_history = fetch_x_history(self.x_handle)
            self.cached_context = None


def build_user_from_stats(
    stats_path: Path | str = DEFAULT_USER_STATS_PATH,
    x_handle_path: Path | str = DEFAULT_X_HANDLE_PATH,
) -> User:
    """Reconstruct a User from the JSON stats file written by the scroll UI.

    A video contributes to the resulting User's context if either:
    - it has at least MIN_SECONDS_FOR_CONTEXT seconds of watch time, or
    - it has explicit engagement (heart or share).
    
    If an X handle file exists, also fetch the user's X history.
    """
    stats_path = Path(stats_path)
    x_handle_path = Path(x_handle_path)
    
    user = User()
    
    # Load X handle if available
    if x_handle_path.exists():
        try:
            x_handle = x_handle_path.read_text(encoding="utf-8").strip()
            if x_handle:
                print(f"[USER] Loading X history for @{x_handle}...")
                user.set_x_handle(x_handle, fetch_history=True)
                print(f"[USER] Loaded {len(user.x_history.posts) if user.x_history else 0} X posts")
        except Exception as e:
            logging.warning(f"Failed to load X handle: {e}")
    
    # Load video stats
    if not stats_path.exists():
        return user

    try:
        raw = stats_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return user

    videos_data = payload.get("videos")
    if not isinstance(videos_data, dict):
        return user

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

        # Skip videos that don't meet the minimum watch time and had no
        # engagement; they shouldn't influence the user's long-term context.
        if seconds_watched < MIN_SECONDS_FOR_CONTEXT and not (heart or share):
            continue

        reaction = UserReaction(heart=heart, share=share, seconds_watched=seconds_watched)
        user.append_video(video, reaction)

    return user


if __name__ == '__main__':
    # Test X history (requires XAI_API_KEY and a valid handle)
    test_handle = os.getenv("TEST_X_HANDLE", "zhang_yunzhi")
    print(f"--- Testing X history for @{test_handle} ---")
    
    user = User()
    user.set_x_handle(test_handle)

    print(f"\nX posts fetched: {len(user.x_history.posts) if user.x_history else 0}")
    if user.x_history:
        for i, post in enumerate(user.x_history.posts[:5]):
            print(f"  {i+1}. [{post.interaction_type}] {post.text[:80]}...")
    
    print("\n--- Full User Context ---")
    user.append_video(
        Video(path=Path("assets/videos/sample.mp4")),
        UserReaction(heart=True, share=False, seconds_watched=10.0),
    )
    print(user.context)