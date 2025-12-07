from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Dict


AD_METRICS_PATH = Path("assets/logs/ad_metrics.json")


@dataclass
class AdPerformance:
    """Aggregated performance metrics for ads tied to a given product.

    We attribute performance at the product level (product image file path),
    regardless of which specific user profile the ad was generated for.
    """

    product_path: str
    impressions: int = 0
    total_watch_seconds: float = 0.0
    likes: int = 0
    shares: int = 0

    @property
    def avg_watch_seconds(self) -> float:
        return self.total_watch_seconds / self.impressions if self.impressions else 0.0

    @property
    def like_rate(self) -> float:
        return self.likes / self.impressions if self.impressions else 0.0

    @property
    def share_rate(self) -> float:
        return self.shares / self.impressions if self.impressions else 0.0


class AdPerformanceStore:
    """Small helper around a JSON file of ad performance metrics.

    The file is a dict keyed by product_path (string) with simple numeric stats.
    """

    def __init__(self, metrics: Dict[str, AdPerformance] | None = None) -> None:
        self._metrics: Dict[str, AdPerformance] = metrics or {}

    # ---- Persistence helpers ---------------------------------------------

    @classmethod
    def load(cls, path: Path = AD_METRICS_PATH) -> "AdPerformanceStore":
        if not path.exists():
            return cls()
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return cls()

        metrics: Dict[str, AdPerformance] = {}
        if isinstance(payload, dict):
            for key, entry in payload.items():
                if not isinstance(entry, dict):
                    continue
                product_path = entry.get("product_path") or key
                impressions = int(entry.get("impressions", 0))
                total_watch_seconds = float(entry.get("total_watch_seconds", 0.0))
                likes = int(entry.get("likes", 0))
                shares = int(entry.get("shares", 0))
                metrics[product_path] = AdPerformance(
                    product_path=product_path,
                    impressions=impressions,
                    total_watch_seconds=total_watch_seconds,
                    likes=likes,
                    shares=shares,
                )

        store = cls(metrics)
        return store

    def save(self, path: Path = AD_METRICS_PATH) -> None:
        data = {
            product_path: asdict(metric)
            for product_path, metric in self._metrics.items()
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            # Metrics are helpful but never critical to the interactive experience.
            pass

    # ---- Recording + scoring ---------------------------------------------

    def _get_or_create(self, product_path: str) -> AdPerformance:
        metric = self._metrics.get(product_path)
        if metric is None:
            metric = AdPerformance(product_path=product_path)
            self._metrics[product_path] = metric
        return metric

    def record_impression(
        self,
        product_path: str,
        *,
        seconds_watched: float,
        liked: bool,
        shared: bool,
        autosave: bool = True,
    ) -> None:
        """Record a single ad impression.

        We treat each time a user scrolls away from an ad as one impression and
        accumulate watch time + engagement at the product level.
        """
        if seconds_watched <= 0:
            return

        metric = self._get_or_create(product_path)
        metric.impressions += 1
        metric.total_watch_seconds += max(0.0, seconds_watched)
        if liked:
            metric.likes += 1
        if shared:
            metric.shares += 1

        if autosave:
            self.save()

    def score(self, product_path: str, objective: str = "engagement") -> float:
        """Return a scalar score for a product under the given objective.

        Objectives:
        - "engagement": blend of like/share rate and normalized watch time
        - "watch_time": average seconds watched
        - "shares": share rate only
        """
        metric = self._metrics.get(product_path)
        if metric is None or metric.impressions == 0:
            return 0.0

        if objective == "watch_time":
            return metric.avg_watch_seconds
        if objective == "shares":
            return metric.share_rate

        # Default: engagement = 0.4 * like_rate + 0.4 * share_rate + 0.2 * normalized_watch_time
        like = metric.like_rate
        share = metric.share_rate
        # Normalize watch time very roughly by capping at 10 seconds.
        normalized_watch = min(metric.avg_watch_seconds, 10.0) / 10.0
        return 0.4 * like + 0.4 * share + 0.2 * normalized_watch

    def metrics_for_debug(self) -> Dict[str, AdPerformance]:
        """Expose raw metrics for printing / debugging."""
        return dict(self._metrics)


__all__ = ["AdPerformance", "AdPerformanceStore", "AD_METRICS_PATH"]


