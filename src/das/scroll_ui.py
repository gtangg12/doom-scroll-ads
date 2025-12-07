from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Sequence
from urllib.parse import quote

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeyEvent, QPainter, QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QSizePolicy,
    QStackedLayout,
)


class VideoEngagement(Enum):
    """Per-video user state."""

    NEUTRAL = auto()
    LIKED = auto()
    SHARED = auto()
    LIKED_AND_SHARED = auto()


@dataclass
class VideoState:
    """Holds state and metrics for a single video."""

    path: Path
    seconds_watched: float = 0.0
    engagement: VideoEngagement = VideoEngagement.NEUTRAL


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

        self.video_states: List[VideoState] = [VideoState(path=v) for v in videos]
        self.current_index: int = 0
        self._current_started_at: Optional[float] = None
        # Optional path where per-video user stats are persisted across sessions.
        self._stats_path: Optional[Path] = stats_path

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

    # ---- Media setup -----------------------------------------------------

    def _init_media(self) -> None:
        self.video_widget = QVideoWidget(self)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
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
            self.like_button.toggle()
            event.accept()
            return
        if event.key() == Qt.Key_2:
            self.share_button.toggle()
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
            key = state.path.name
            entry = videos_data.get(key)
            if not isinstance(entry, dict):
                continue

            seconds = entry.get("seconds_watched")
            engagement_name = entry.get("engagement")

            if isinstance(seconds, (int, float)):
                state.seconds_watched = float(seconds)

            if isinstance(engagement_name, str) and engagement_name in VideoEngagement.__members__:
                state.engagement = VideoEngagement[engagement_name]

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
            videos_out[state.path.name] = {
                "path": str(state.path),
                "seconds_watched": state.seconds_watched,
                "engagement": state.engagement.name,
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
        self._update_ui_from_state()

    def _go_next(self) -> None:
        self._commit_watch_time()
        self.current_index = (self.current_index + 1) % len(self.video_states)
        self._load_current_video()

    def _go_prev(self) -> None:
        self._commit_watch_time()
        self.current_index = (self.current_index - 1) % len(self.video_states)
        self._load_current_video()

    # ---- Engagement handling ---------------------------------------------

    def _on_like_clicked(self, checked: bool) -> None:
        current = self.current_video.engagement
        if checked:
            if current is VideoEngagement.SHARED:
                self.current_video.engagement = VideoEngagement.LIKED_AND_SHARED
            elif current is VideoEngagement.LIKED_AND_SHARED:
                # stay
                pass
            else:
                self.current_video.engagement = VideoEngagement.LIKED
        else:
            if current is VideoEngagement.LIKED_AND_SHARED:
                self.current_video.engagement = VideoEngagement.SHARED
            elif current is VideoEngagement.LIKED:
                self.current_video.engagement = VideoEngagement.NEUTRAL
        self._update_ui_from_state()
        self._persist_state()

    def _on_share_clicked(self, checked: bool) -> None:
        current = self.current_video.engagement
        if checked:
            # Open X share intent
            self._share_on_x()
            if current is VideoEngagement.LIKED:
                self.current_video.engagement = VideoEngagement.LIKED_AND_SHARED
            elif current is VideoEngagement.LIKED_AND_SHARED:
                # stay
                pass
            else:
                self.current_video.engagement = VideoEngagement.SHARED
        else:
            if current is VideoEngagement.LIKED_AND_SHARED:
                self.current_video.engagement = VideoEngagement.LIKED
            elif current is VideoEngagement.SHARED:
                self.current_video.engagement = VideoEngagement.NEUTRAL
        self._update_ui_from_state()
        self._persist_state()

    def _share_on_x(self) -> None:
        """Open X (Twitter) share intent in the default browser."""
        video_name = self.current_video.path.stem
        text = f'Check out "{video_name}" on Doom Scroll Ads 🎬'
        encoded_text = quote(text)
        url = f"https://twitter.com/intent/tweet?text={encoded_text}"
        QDesktopServices.openUrl(QUrl(url))

    # ---- Video loading & state sync --------------------------------------

    def _load_current_video(self) -> None:
        path = self.current_video.path
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self._current_started_at = time.monotonic()
        self._update_ui_from_state()

    def _commit_watch_time(self) -> None:
        if self._current_started_at is None:
            return
        elapsed = time.monotonic() - self._current_started_at
        if elapsed > 0:
            self.current_video.seconds_watched += elapsed
        self._current_started_at = None
        self._persist_state()

    def _update_ui_from_state(self) -> None:
        # Meta
        idx = self.current_index + 1
        total = len(self.video_states)
        watched = self.current_video.seconds_watched
        self.title_label.setText(self.current_video.path.stem)
        self.meta_label.setText(f"{idx:02d}/{total:02d}  ·  {watched:4.1f}s watched")

        # Buttons
        engagement = self.current_video.engagement
        like_on = engagement in (VideoEngagement.LIKED, VideoEngagement.LIKED_AND_SHARED)
        share_on = engagement in (VideoEngagement.SHARED, VideoEngagement.LIKED_AND_SHARED)

        try:
            self.like_button.blockSignals(True)
            self.share_button.blockSignals(True)
            self.like_button.setChecked(like_on)
            self.share_button.setChecked(share_on)
        finally:
            self.like_button.blockSignals(False)
            self.share_button.blockSignals(False)

        # Sticker label is no longer shown; we keep engagement tracking only.

    @staticmethod
    def _sticker_text_for_engagement(engagement: VideoEngagement) -> str:
        """Return the small pill text under the video.

        We only surface "liked" now; the explicit "shared" tag is no longer
        shown in the UI, while the share button state and analytics still
        track sharing in the background.
        """
        if engagement in (VideoEngagement.LIKED, VideoEngagement.LIKED_AND_SHARED):
            return "♡ liked"
        # For NEUTRAL or SHARED-only, don't show any sticker.
        return ""

    # ---- Lifecycle -------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._commit_watch_time()
        # Stop periodic snapshots and make one last best-effort persist so that
        # stats are flushed even if we didn't hit the timer again.
        if hasattr(self, "_snapshot_timer"):
            self._snapshot_timer.stop()
        self._persist_state()
        self._print_summary()
        super().closeEvent(event)

    def _print_summary(self) -> None:
        print("\n=== Doom Scroll Session Summary ===")
        for state in self.video_states:
            Eng = state.engagement.name
            print(
                f"- {state.path.name:30s}  |  watched {state.seconds_watched:5.1f}s  |  {Eng}"
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
    paths = [p for p in directory.iterdir() if p.suffix.lower() in exts and p.is_file()]
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


__all__ = ["run_scroll_ui", "VideoEngagement", "VideoState", "ScrollWindow"]