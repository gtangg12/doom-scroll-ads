from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Sequence

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QCloseEvent, QKeyEvent, QPainter, QColor
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

    def __init__(self, videos: Sequence[Path]) -> None:
        super().__init__()

        if not videos:
            raise ValueError("At least one video is required")

        self.video_states: List[VideoState] = [VideoState(path=v) for v in videos]
        self.current_index: int = 0
        self._current_started_at: Optional[float] = None

        self.setWindowTitle("Doom Scroll Ads")
        self.resize(900, 700)

        self._init_media()
        self._init_ui()
        self._load_current_video()

    # ---- Media setup -----------------------------------------------------

    def _init_media(self) -> None:
        self.video_widget = QVideoWidget(self)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

    # ---- UI setup --------------------------------------------------------

    def _init_ui(self) -> None:
        root = StarFieldWidget(self)
        root.setObjectName("Root")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(32, 24, 32, 24)
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

        # ---- Main content: video card -------------------------------------
        frame = QWidget(self)
        frame.setObjectName("VideoFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(18, 18, 18, 18)
        frame_layout.setSpacing(12)

        frame.setStyleSheet(
            """
            QWidget#VideoFrame {
                background-color: #000000;
                border-radius: 24px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
            """
        )

        self.video_widget.setStyleSheet(
            """
            QVideoWidget {
                border-radius: 24px;
                background-color: #000000;
            }
            """
        )
        frame_layout.addWidget(self.video_widget, stretch=1)

        # Sticker label (engagement state) – small Apple-style pill
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

        # Info + controls row (like Apple Music bottom bar)
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(16)

        # Meta info on the left
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
        controls_layout.setSpacing(12)

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

        self.share_button = QPushButton("⇪", self)
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
                font-weight: 400;
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

        info_row.addWidget(meta_container, stretch=1)
        info_row.addWidget(controls, stretch=0, alignment=Qt.AlignRight | Qt.AlignVCenter)

        frame_layout.addLayout(info_row)

        # Keyboard hint
        hint = QLabel("↑ / ↓  scroll    ·    1  like    ·    2  share", self)
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

        # Global background / typography (dark Apple Music–like)
        root.setStyleSheet(
            """
            QWidget#Root {
                background-color: #000000;
            }
            QLabel, QPushButton {
                font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
            }
            """
        )

        # Assemble main layout
        root_layout.addWidget(title)
        root_layout.addWidget(frame, stretch=1)
        root_layout.addWidget(hint)

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

    def _on_share_clicked(self, checked: bool) -> None:
        current = self.current_video.engagement
        if checked:
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

        # Sticker label
        sticker_text = self._sticker_text_for_engagement(engagement)
        self.sticker_label.setText(sticker_text)

    @staticmethod
    def _sticker_text_for_engagement(engagement: VideoEngagement) -> str:
        if engagement is VideoEngagement.LIKED_AND_SHARED:
            return "♡ liked   ·   ✶ shared"
        if engagement is VideoEngagement.LIKED:
            return "♡ liked"
        if engagement is VideoEngagement.SHARED:
            return "✶ shared"
        return ""

    # ---- Lifecycle -------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._commit_watch_time()
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

    window = ScrollWindow(videos)
    window.show()
    app.exec()


__all__ = ["run_scroll_ui", "VideoEngagement", "VideoState", "ScrollWindow"]


