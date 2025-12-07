from __future__ import annotations

import copy
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, Future

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeyEvent, QPainter, QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from das.ad_generation import collect_cached_ads
from das.ad_generation_dataclasses import (
    Video,
    UserReaction,
    Product,
    User,
    build_user_from_stats,
    MIN_SECONDS_FOR_CONTEXT,
)
from das.ad_performance import AdPerformanceStore


@dataclass
class VideoState:
    """Holds state and metrics for a single video, wired to ad-generation dataclasses."""

    video: Video
    seconds_watched: float = 0.0
    reaction: UserReaction = field(default_factory=UserReaction)
    is_ad: bool = False  # True for generated ad videos, False for organic videos
    # Whether this video has already been appended to the in-memory User
    # context for this session.
    context_appended: bool = False


@dataclass
class Star:
    x: float
    y: float
    speed: float
    radius: float
    alpha: int


class StarFieldWidget(QWidget):
    """Pure-black animated star field background."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stars: list[Star] = []
        self._initialized = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(40)  # ~25fps

    def _init_stars(self) -> None:
        self.stars.clear()
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        for _ in range(80):
            self.stars.append(
                Star(
                    x=random.uniform(0, w),
                    y=random.uniform(0, h),
                    speed=random.uniform(0.3, 1.0),
                    radius=random.uniform(0.4, 1.4),
                    alpha=random.randint(40, 160),
                )
            )

    def resizeEvent(self, event) -> None:  # noqa: N802
        if not self._initialized and self.width() > 0 and self.height() > 0:
            self._initialized = True
            self._init_stars()
        super().resizeEvent(event)

    def _advance(self) -> None:
        if not self.stars:
            return
        h = self.height()
        w = self.width()
        for star in self.stars:
            star.y += star.speed
            if star.y > h:
                star.y = 0
                star.x = random.uniform(0, w)
            # gentle twinkle
            delta = random.randint(-10, 10)
            star.alpha = max(30, min(200, star.alpha + delta))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if not self.stars:
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        for star in self.stars:
            color = QColor(255, 255, 255, star.alpha)
            painter.setPen(color)
            painter.setBrush(color)
            painter.drawEllipse(star.x, star.y, star.radius, star.radius)


class ScrollWindow(QMainWindow):
    """Simple Instagram-style vertical video scroller."""

    def __init__(
        self,
        videos: Sequence[Path],
        stats_path: Optional[Path] = None,
    ) -> None:
        super().__init__()

        if not videos:
            raise ValueError("At least one video is required")

        # Wrap raw paths in the shared `Video` dataclass so ad-generation can
        # consume the same objects the UI is using.
        self.video_states: List[VideoState] = [
            VideoState(video=Video(path=v)) for v in videos
        ]
        self.current_index: int = 0
        self._current_started_at: Optional[float] = None
        # Optional path where per-video user stats are persisted across sessions.
        self._stats_path: Optional[Path] = stats_path

        # In-memory User object that mirrors the stats JSON. We build it once
        # from the existing stats file (if any), then keep it incrementally
        # updated as the user watches / likes / shares videos.
        if self._stats_path is not None:
            self._user: User = build_user_from_stats(self._stats_path)
        else:
            self._user = User()

        # Preload available products once; ad generation will happen on a
        # background worker using this pool and product list.
        # Cache of ready-to-insert ads.
        self._ad_cache: list[Video] = []
        # Count of organic (non-ad) videos the user has scrolled through since
        # the last inserted ad. Used to place an ad after every N organic views.
        self._organic_views_since_last_ad: int = 0

        # Aggregate performance metrics for generated ads; used both for
        # tracking outcomes (watch time / engagement) and for biasing future
        # product selection in ad generation.
        self._ad_performance_store: AdPerformanceStore = AdPerformanceStore.load()

        # Pre-populate the ad cache with any already-generated ads on disk so
        # the UI starts with a rich set of creatives even before the first
        # background generation job completes.
        cached_ads_dir = Path("assets/videos_generated")
        cached_ads = collect_cached_ads(cached_ads_dir)
        if cached_ads:
            self._ad_cache.extend(cached_ads)
            print(
                f"[ADS] Preloaded {len(cached_ads)} cached ad videos from {cached_ads_dir}"
            )

        # Background ad-generation executor + polling timer so we never block
        # the UI thread while calling the LLM or ffmpeg.
        self._ad_executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)
        self._ad_future: Optional[Future[Video]] = None
        self._ad_poll_timer = QTimer(self)
        self._ad_poll_timer.setInterval(500)  # ms
        self._ad_poll_timer.timeout.connect(self._check_ad_future)

        self.setWindowTitle("Doom Scroll Ads")
        self.resize(900, 700)

        # Load any previously persisted engagement + watch-time before the UI
        # is initialized so labels reflect existing state.
        self._load_persisted_state()

        self._init_media()
        self._init_ui()
        self._load_current_video()

        # Periodically snapshot watch time + engagement so that, even if the
        # app crashes or is closed abruptly, stats are mostly up to date.
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.setInterval(5000)  # ms
        self._snapshot_timer.timeout.connect(self._snapshot_tick)
        self._snapshot_timer.start()

        # Real-time UI update timer for seconds watched display
        self._ui_update_timer = QTimer(self)
        self._ui_update_timer.setInterval(100)  # 100ms for smooth updates
        self._ui_update_timer.timeout.connect(self._update_watch_time_display)
        self._ui_update_timer.start()

        # Start polling for completion of any background ad-generation tasks.
        self._ad_poll_timer.start()

        # Kick off the first ad-generation request in the background so we
        # already have an ad ready by the time the user scrolls far enough.
        self._ensure_ad_queued()

    # ---- Media setup -----------------------------------------------------

    def _init_media(self) -> None:
        self.video_widget = QVideoWidget(self)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        # Keep the outer "phone" frame a consistent size by ignoring per-video
        # size hints from the underlying media. This prevents the card from
        # subtly resizing when videos have different resolutions/aspect ratios.
        self.video_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    # ---- UI setup --------------------------------------------------------

    def _init_ui(self) -> None:
        root = StarFieldWidget(self)
        root.setObjectName("Root")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(32, 24, 32, 32)
        root_layout.setSpacing(18)

        # ---- Header -------------------------------------------------------
        title = QLabel("doom scroll ads", self)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            """
            QLabel {
                font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", system-ui, sans-serif;
                font-size: 18px;
                font-weight: 600;
                letter-spacing: 0.20em;
                text-transform: uppercase;
                color: #ffffff;
            }
            """
        )

        # ---- Main content: centered "phone" card --------------------------
        # Store as an attribute so we can keep its size iPhone-like and
        # responsive in the window's resize handler.
        frame = QWidget(self)
        frame.setObjectName("VideoFrame")
        self.phone_frame = frame  # type: ignore[attr-defined]
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(18, 18, 16, 14)
        frame_layout.setSpacing(10)

        frame.setStyleSheet(
            """
            QWidget#VideoFrame {
                /* Slightly translucent "glass" phone body so background stars peek through */
                background-color: rgba(0, 0, 0, 0.65);
                border-radius: 24px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
            """
        )

        # Inner star field + video stacked so stars are visible inside the phone
        # behind letterboxing areas, without affecting video playback.
        inner_container = QWidget(self)
        inner_stack = QStackedLayout(inner_container)
        inner_stack.setContentsMargins(0, 0, 0, 0)
        inner_stack.setStackingMode(QStackedLayout.StackAll)

        inner_starfield = StarFieldWidget(inner_container)
        inner_starfield.setObjectName("InnerStarField")

        self.video_widget.setStyleSheet(
            """
            QVideoWidget {
                border-radius: 24px;
                background-color: transparent;
            }
            """
        )

        inner_stack.addWidget(inner_starfield)  # background
        inner_stack.addWidget(self.video_widget)  # foreground

        frame_layout.addWidget(inner_container, stretch=1)

        # Sticker label (engagement state) – previously a small Apple-style pill.
        # We keep the widget around but hide it so the box no longer appears.
        self.sticker_label = QLabel("", self)
        self.sticker_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.sticker_label.setStyleSheet(
            """
            QLabel {
                padding: 4px 12px;
                border-radius: 999px;
                background-color: transparent;
                border: 1px solid rgba(255, 255, 255, 0.18);
                color: #e5e5e5;
                font-size: 11px;
                font-weight: 400;
                letter-spacing: 0.10em;
                text-transform: uppercase;
            }
            """
        )
        frame_layout.addWidget(self.sticker_label, alignment=Qt.AlignLeft)
        self.sticker_label.hide()

        # Footer strip with top/bottom separators
        footer = QWidget(self)
        footer.setObjectName("Footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 8, 10, 8)
        footer_layout.setSpacing(12)

        # Left thumbnail placeholder
        thumb = QWidget(self)
        thumb.setObjectName("ThumbPlaceholder")
        thumb.setFixedSize(36, 36)

        # Meta info (title + small line) in the middle
        meta_container = QWidget(self)
        meta_layout = QVBoxLayout(meta_container)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(2)

        self.title_label = QLabel("", self)
        self.title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title_label.setStyleSheet(
            """
            QLabel {
                color: #ffffff;
                font-size: 14px;
                font-weight: 600;
            }
            """
        )

        self.meta_label = QLabel("", self)
        self.meta_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.meta_label.setStyleSheet(
            """
            QLabel {
                color: #b0b0b0;
                font-size: 11px;
            }
            """
        )

        meta_layout.addWidget(self.title_label)
        meta_layout.addWidget(self.meta_label)

        # Controls on the right
        controls = QWidget(self)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        self.like_button = QPushButton("♥", self)
        self.like_button.setCheckable(True)
        self.like_button.clicked.connect(self._on_like_clicked)
        self.like_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.6);
                min-width: 32px;
                min-height: 32px;
                max-width: 32px;
                max-height: 32px;
                border-radius: 16px;
                font-size: 16px;
                font-weight: 500;
            }
            QPushButton:checked {
                background-color: #ffffff;
                color: #000000;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
            }
            """
        )

        self.share_button = QPushButton("𝕏", self)
        self.share_button.setCheckable(True)
        self.share_button.clicked.connect(self._on_share_clicked)
        self.share_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                color: #ffffff;
                border: none;
                min-width: 32px;
                min-height: 32px;
                max-width: 32px;
                max-height: 32px;
                border-radius: 16px;
                border: 1px solid rgba(255, 255, 255, 0.4);
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:checked {
                background-color: #ffffff;
                color: #000000;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
            }
            """
        )

        controls_layout.addWidget(self.like_button)
        controls_layout.addWidget(self.share_button)

        footer_layout.addWidget(thumb)
        footer_layout.addWidget(meta_container, stretch=1)
        footer_layout.addWidget(controls, stretch=0, alignment=Qt.AlignRight | Qt.AlignVCenter)

        frame_layout.addWidget(footer)

        # Keyboard hint
        hint = QLabel("↑ / ↓  scroll    ·    1  like    ·    2  share on X", self)
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(
            """
            QLabel {
                color: #a0a0a0;
                font-size: 11px;
                letter-spacing: 0.14em;
                text-transform: uppercase;
            }
            """
        )

        # Global background / typography
        root.setStyleSheet(
            """
            QWidget#Root {
                background-color: #000000;
            }
            QWidget#VideoFrame {
                /* Video card already styled above */
            }
            QWidget#Footer {
                border-top: 1px solid rgba(255, 255, 255, 0.16);
                border-bottom: 1px solid rgba(255, 255, 255, 0.12);
                background-color: #000000;
            }
            QWidget#ThumbPlaceholder {
                border-radius: 2px;
                border: 1px solid rgba(255, 255, 255, 0.24);
                background-color: transparent;
            }
            QLabel, QPushButton {
                font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
            }
            """
        )

        # Assemble main layout
        frame_row = QHBoxLayout()
        frame_row.addStretch(1)
        frame_row.addWidget(frame)
        frame_row.addStretch(1)

        root_layout.addWidget(title)
        root_layout.addLayout(frame_row, stretch=1)
        root_layout.addWidget(hint)

        # Initialize the phone frame geometry to an iPhone‑like aspect.
        self._update_phone_frame_geometry()

        self._update_ui_from_state()

    # ---- Convenience properties ------------------------------------------

    @property
    def current_video(self) -> VideoState:
        return self.video_states[self.current_index]

    # ---- Interaction & navigation ----------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Down, Qt.Key_J):
            self._go_next()
            event.accept()
            return
        if event.key() in (Qt.Key_Up, Qt.Key_K):
            self._go_prev()
            event.accept()
            return
        if event.key() == Qt.Key_1:
            # Use .click() so the same slot + side‑effects run as when the
            # user presses the on‑screen heart button.
            self.like_button.click()
            event.accept()
            return
        if event.key() == Qt.Key_2:
            # Use .click() so sharing via keyboard behaves exactly like
            # clicking the on‑screen share button (including opening X).
            self.share_button.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Keep the central phone card iPhone-like and responsive."""
        super().resizeEvent(event)
        self._update_phone_frame_geometry()

    def _update_phone_frame_geometry(self) -> None:
        """Resize the central phone frame with an iPhone-style aspect ratio.

        We keep a tall, slim card (~19.5:9 aspect, like modern iPhones)
        that scales with the window but never touches the edges, and is
        independent from the underlying video resolution.
        """
        frame = getattr(self, "phone_frame", None)
        root = self.centralWidget()
        if frame is None or root is None:
            return

        # Target iPhone-like portrait aspect
        aspect = 19.5 / 9.0  # height / width

        root_width = max(root.width(), 1)
        root_height = max(root.height(), 1)

        # Leave generous margins around the phone
        max_phone_width = max(root_width - 220, 320)
        max_phone_height = max(root_height - 200, 480)

        # Start from height, then clamp to available width if needed
        target_height = max_phone_height
        target_width = int(target_height / aspect)

        if target_width > max_phone_width:
            target_width = max_phone_width
            target_height = int(target_width * aspect)

        # Apply a reasonable minimum so it doesn't get too tiny
        min_width, min_height = 320, int(320 * aspect)
        target_width = max(target_width, min_width)
        target_height = max(target_height, min_height)

        frame.setFixedSize(target_width, target_height)

    # ---- Persistence helpers -----------------------------------------------

    def _load_persisted_state(self) -> None:
        """Populate in-memory video state from an on-disk stats file, if any."""
        if self._stats_path is None or not self._stats_path.exists():
            return

        try:
            raw = self._stats_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            # If the file can't be read or parsed, just ignore it; the UI should
            # still be fully functional for this session.
            return

        videos_data = payload.get("videos")
        if not isinstance(videos_data, dict):
            return

        for state in self.video_states:
            key = state.video.path.name
            entry = videos_data.get(key)
            if not isinstance(entry, dict):
                continue

            seconds = entry.get("seconds_watched")
            heart = entry.get("heart")
            share = entry.get("share")

            if isinstance(seconds, (int, float)):
                state.seconds_watched = float(seconds)

            if isinstance(heart, bool):
                state.reaction.heart = heart
            if isinstance(share, bool):
                state.reaction.share = share

    def _persist_state(self) -> None:
        """Write the current in-memory video state to disk."""
        if self._stats_path is None:
            return

        data: dict[str, object] = {
            "version": 1,
            "videos": {},
        }

        videos_out: dict[str, dict[str, object]] = {}
        for state in self.video_states:
            videos_out[state.video.path.name] = {
                "path": str(state.video.path),
                "seconds_watched": state.seconds_watched,
                "heart": state.reaction.heart,
                "share": state.reaction.share,
            }
        data["videos"] = videos_out

        try:
            self._stats_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            # Persistence failures should never break the interactive experience.
            pass

    def _snapshot_tick(self) -> None:
        """Periodically commit watch time for the current video and persist."""
        if self._current_started_at is None:
            return

        now = time.monotonic()
        elapsed = now - self._current_started_at
        if elapsed <= 0:
            return

        self.current_video.seconds_watched += elapsed
        self._current_started_at = now
        self._persist_state()

    def _update_watch_time_display(self) -> None:
        """Update the UI to show real-time seconds watched."""
        if self._current_started_at is None:
            return
        # Calculate current watch time without committing it
        elapsed = time.monotonic() - self._current_started_at
        total_watched = self.current_video.seconds_watched + elapsed
        
        # Update just the meta label for efficiency
        idx = self.current_index + 1
        total = len(self.video_states)
        self.meta_label.setText(f"{idx:02d}/{total:02d}  ·  {total_watched:4.1f}s watched")
        
        # Check if video now qualifies for user context (but don't call full UI update)
        self._maybe_append_current_video_to_user()

    # ---- Background ad generation -----------------------------------------

    def _ensure_ad_queued(self) -> None:
        """Ensure there is at least one ad-generation task in flight.

        This runs entirely off the UI thread via a ThreadPoolExecutor so that
        users can keep scrolling with zero lag while the ad is being prepared.
        """
        # Keep the ad cache near a target size; only generate more when the
        # number of ready-to-insert ads falls below this threshold.
        TARGET_CACHE_SIZE = 5
        current_cache_size = len(self._ad_cache)
        if current_cache_size >= TARGET_CACHE_SIZE:
            print(
                "[ADS] Ad cache at or above target size "
                f"({current_cache_size}/{TARGET_CACHE_SIZE}); "
                "not queuing additional ad generation."
            )
            return
        if self._ad_future is not None and not self._ad_future.done():
            print("[ADS] Ad generation already in progress; not queuing another.")
            return

        # Submit a background task that rebuilds the User from stats and calls
        # generate_ad(user, products).
        print("[ADS] Queuing background ad generation task...")
        self._ad_future = self._ad_executor.submit(self._generate_ad_for_current_user)

    def _generate_ad_for_current_user(self) -> Video:
        """Worker function executed in a background thread."""
        from das.ad_generation import generate_ad

        # Take a snapshot of the current in-memory User so ad generation sees a
        # stable view of behaviour up to this point, without re-reading JSON.
        print("[ADS][worker] Starting ad generation for current user...")
        user_snapshot = copy.deepcopy(self._user)
        ad_video = generate_ad(user_snapshot)
        print(f"[ADS][worker] Ad generation completed: {ad_video.path}")
        return ad_video

    def _check_ad_future(self) -> None:
        """Poll for completion of the background ad task and insert when ready."""
        future = self._ad_future
        if future is None or not future.done():
            return

        # Clear the reference so _ensure_ad_queued can schedule the next one.
        self._ad_future = None

        try:
            ad_video: Video = future.result()
        except Exception as exc:  # noqa: BLE001
            # If ad-generation failed, don't surface it to the user; simply try
            # again later, but do print a debug line so we can see failures.
            print(f"[ADS] Ad generation failed: {exc!r}")
            return

        # Cache the ready ad; it will be inserted after the user has viewed a
        # fixed number of organic videos. This keeps insertion logic tied to
        # actual scrolling behaviour, while generation stays fully async.
        self._ad_cache.append(ad_video)
        print(f"[ADS] Cached new ad video ({len(self._ad_cache)} in cache).")

        # Immediately queue up the next ad so there's always one coming down
        # the pipe while the user keeps scrolling.
        self._ensure_ad_queued()

    def _maybe_insert_ad_after_current(self) -> None:
        """Insert an ad after every N organic videos, if one is cached.

        We only insert when:
        - The user has just landed on an organic (non-ad) video
        - We've counted at least 5 such organic views since the last ad
        - At least one ad video is already cached and ready to play
        """
        N = 5
        if self._organic_views_since_last_ad < N:
            return
        if not self._ad_cache:
            print("[ADS] Threshold reached but no cached ads ready yet.")
            return

        ad_video = self._ad_cache.pop(0)
        insert_at = min(self.current_index + 1, len(self.video_states))
        self.video_states.insert(insert_at, VideoState(video=ad_video, is_ad=True))
        self._organic_views_since_last_ad = 0
        print(f"[ADS] Inserted ad after index {self.current_index}, total videos now {len(self.video_states)}.")

        # After consuming an ad, we may have dropped the cache below the target
        # size; attempt to queue a new generation task if appropriate.
        self._ensure_ad_queued()

    def _go_next(self) -> None:
        self._commit_watch_time()
        self.current_index = (self.current_index + 1) % len(self.video_states)
        self._load_current_video()
        # Count only organic videos towards the "every N videos show an ad"
        # threshold; ad views themselves do not move the goalpost.
        if not self.current_video.is_ad:
            self._organic_views_since_last_ad += 1
            print(
                f"[ADS] Moved to video index {self.current_index} "
                f"(organic views since last ad: {self._organic_views_since_last_ad}, "
                f"ad cache size: {len(self._ad_cache)})"
            )
            self._maybe_insert_ad_after_current()
        else:
            print(f"[ADS] Moved to ad video at index {self.current_index}")

    def _go_prev(self) -> None:
        self._commit_watch_time()
        self.current_index = (self.current_index - 1) % len(self.video_states)
        self._load_current_video()

    # ---- Engagement handling ---------------------------------------------

    def _on_like_clicked(self, checked: bool) -> None:
        # Directly toggle the UserReaction "heart" flag used by ad-generation.
        self.current_video.reaction.heart = checked
        self._update_ui_from_state()
        self._persist_state()
        self._maybe_append_current_video_to_user()

    def _on_share_clicked(self, checked: bool) -> None:
        # When turning "share" on, open X, then flip the underlying reaction
        # flag so ad-generation can see that the user shared this video.
        if checked:
            self._share_on_x()
        self.current_video.reaction.share = checked
        self._update_ui_from_state()
        self._persist_state()
        self._maybe_append_current_video_to_user()

    def _share_on_x(self) -> None:
        """Open X (Twitter) share intent in the default browser."""
        video_name = self.current_video.video.path.stem
        text = f'Check out "{video_name}" on Doom Scroll Ads 🎬'
        encoded_text = quote(text)
        url = f"https://twitter.com/intent/tweet?text={encoded_text}"
        QDesktopServices.openUrl(QUrl(url))

    # ---- Video loading & state sync --------------------------------------

    def _load_current_video(self) -> None:
        path = self.current_video.video.path
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self._current_started_at = time.monotonic()
        self._update_ui_from_state()

    def _commit_watch_time(self) -> None:
        if self._current_started_at is None:
            return
        elapsed = time.monotonic() - self._current_started_at
        if elapsed > 0:
            state = self.current_video
            state.seconds_watched += elapsed

            # If this is an ad, treat this as one "impression" and record basic
            # performance metrics about how it did before the user scrolled
            # away (watch time + engagement flags).
            if state.is_ad and state.video.product_path is not None:
                try:
                    self._ad_performance_store.record_impression(
                        str(state.video.product_path),
                        seconds_watched=elapsed,
                        liked=state.reaction.heart,
                        shared=state.reaction.share,
                        autosave=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Never let metrics tracking interfere with the scroll UX.
                    print(f"[ADS][metrics] Failed to record ad performance: {exc!r}")

        self._current_started_at = None
        self._persist_state()
        self._maybe_append_current_video_to_user()

    def _update_ui_from_state(self) -> None:
        # Meta
        idx = self.current_index + 1
        total = len(self.video_states)
        watched = self.current_video.seconds_watched
        self.title_label.setText(self.current_video.video.path.stem)
        self.meta_label.setText(f"{idx:02d}/{total:02d}  ·  {watched:4.1f}s watched")

        # Buttons
        like_on = self.current_video.reaction.heart
        share_on = self.current_video.reaction.share

        try:
            self.like_button.blockSignals(True)
            self.share_button.blockSignals(True)
            self.like_button.setChecked(like_on)
            self.share_button.setChecked(share_on)
        finally:
            self.like_button.blockSignals(False)
            self.share_button.blockSignals(False)

        # Sticker label is no longer shown; we keep engagement tracking only.

    def _maybe_append_current_video_to_user(self) -> None:
        """Append the current video to the in-memory User when it "matters".

        A video is considered meaningful for long-term user context if:
        - it has been watched for at least MIN_SECONDS_FOR_CONTEXT seconds, or
        - it has explicit engagement (like or share).
        """
        state = self.current_video

        # Never let ads influence the user preference model.
        if state.is_ad:
            return

        # Only append once per video per session.
        if state.context_appended:
            return

        if (
            state.seconds_watched >= MIN_SECONDS_FOR_CONTEXT
            or state.reaction.heart
            or state.reaction.share
        ):
            # Append the shared Video / UserReaction objects so future UI
            # toggles (like/share) keep the User's history in sync.
            self._user.append_video(state.video, state.reaction)
            state.context_appended = True

    # ---- Lifecycle -------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._commit_watch_time()
        # Stop periodic snapshots and make one last best-effort persist so that
        # stats are flushed even if we didn't hit the timer again.
        if hasattr(self, "_snapshot_timer"):
            self._snapshot_timer.stop()
        if hasattr(self, "_ui_update_timer"):
            self._ui_update_timer.stop()
        self._persist_state()
        self._print_summary()
        super().closeEvent(event)

    def _print_summary(self) -> None:
        print("\n=== Doom Scroll Session Summary ===")
        for state in self.video_states:
            heart = "♥" if state.reaction.heart else " "
            share = "↗" if state.reaction.share else " "
            kind = "AD " if state.is_ad else "VID"
            print(
                f"- {state.video.path.name:30s}  |  {kind}  |  watched {state.seconds_watched:5.1f}s  |  heart={heart} share={share}"
            )

        # Aggregate ad performance by underlying product, restricted to ads
        # that were actually seen this session so the summary stays focused.
        seen_product_paths: set[str] = set()
        for state in self.video_states:
            if state.is_ad and state.video.product_path is not None:
                seen_product_paths.add(str(state.video.product_path))

        if not seen_product_paths:
            print("\n=== Ad Performance Summary ===")
            print("No ad impressions with linked products were recorded this session.")
            return

        all_metrics = self._ad_performance_store.metrics_for_debug()
        print("\n=== Ad Performance Summary (by product) ===")
        for product_path_str in sorted(seen_product_paths):
            metric = all_metrics.get(product_path_str)
            if metric is None:
                continue

            engagement_score = self._ad_performance_store.score(
                product_path_str, objective="engagement"
            )
            # Show only the basename for readability.
            product_name = Path(product_path_str).name
            print(
                f"- {product_name:30s}  |  impressions={metric.impressions:3d}  "
                f"|  avg_watch={metric.avg_watch_seconds:4.1f}s  "
                f"|  like_rate={metric.like_rate:.2%}  "
                f"|  share_rate={metric.share_rate:.2%}  "
                f"|  engagement_score={engagement_score:.4f}"
            )


def _select_directory(parent: QWidget | None = None) -> Optional[Path]:
    directory = QFileDialog.getExistingDirectory(
        parent, "Choose a folder of videos", str(Path.cwd())
    )
    if not directory:
        return None
    return Path(directory)


def _collect_videos(directory: Path) -> List[Path]:
    exts = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
    paths: list[Path] = []
    for p in directory.iterdir():
        if p.suffix.lower() not in exts or not p.is_file():
            continue
        # Require a matching .txt caption file; if it's missing, skip this
        # video so we don't later crash or generate ads with no context.
        caption_path = p.parent / (p.stem + ".txt")
        if not caption_path.exists():
            print(f"[VIDEOS] Skipping {p.name} (missing {caption_path.name})")
            continue
        paths.append(p)
    random.shuffle(paths)
    return paths




def run_scroll_ui(video_dir: Optional[Path] = None) -> None:
    """Launch the scroll UI.

    Parameters
    ----------
    video_dir:
        Directory containing video files. If omitted or empty, the user
        is prompted to choose one.
    """
    app = QApplication.instance() or QApplication(sys.argv)

    directory = video_dir
    if directory is None:
        directory = _select_directory()
        if directory is None:
            return

    videos = _collect_videos(directory)
    if not videos:
        QMessageBox.warning(
            None,
            "No videos found",
            "This folder does not contain any supported video files.\n\n"
            "Supported extensions: .mp4, .mov, .m4v, .avi, .mkv",
        )
        return

    # Persist user stats in a JSON file alongside the selected video directory
    # so that watch time / likes / shares survive across sessions.
    stats_path = Path("assets/logs/user.json")  # hardcoded path for now

    window = ScrollWindow(videos, stats_path=stats_path)
    window.show()
    app.exec()


__all__ = ["run_scroll_ui", "VideoState", "ScrollWindow"]