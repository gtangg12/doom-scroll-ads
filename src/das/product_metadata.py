from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class ProductMeta:
    """Lightweight description of a product used for ad creatives and UI."""

    id: str
    name: str
    image_path: Path
    description_path: Optional[Path]
    url: str
    cta_text: str = "View product"


def load_product_metadata(config_path: Path = Path("assets/products.json")) -> Dict[str, ProductMeta]:
    """Load product metadata from JSON and key it by image basename.

    The JSON may either be a bare list of product objects or a dict containing
    a ``"products"`` key with such a list.
    """
    if not config_path.exists():
        print("[ADS] No assets/products.json found; product links disabled.")
        return {}

    try:
        raw = config_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001
        print(f"[ADS] Failed to load products.json: {exc!r}")
        return {}

    if isinstance(data, dict):
        data = data.get("products", [])
    if not isinstance(data, list):
        print("[ADS] products.json has unexpected structure; expected a list.")
        return {}

    by_basename: Dict[str, ProductMeta] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue

        image_path_str = entry.get("image_path")
        url = entry.get("url")
        if not isinstance(image_path_str, str) or not isinstance(url, str):
            continue

        prod_id = str(entry.get("id") or Path(image_path_str).stem)
        name = str(entry.get("name") or prod_id)
        cta_text = str(entry.get("cta_text") or "View product")

        image_path = Path(image_path_str)
        desc_path_raw = entry.get("description_path")
        description_path: Optional[Path]
        if isinstance(desc_path_raw, str):
            description_path = Path(desc_path_raw)
        else:
            description_path = None

        meta = ProductMeta(
            id=prod_id,
            name=name,
            image_path=image_path,
            description_path=description_path,
            url=url,
            cta_text=cta_text,
        )
        by_basename[image_path.name] = meta

    return by_basename


# Global, module-local cache of product metadata keyed by image basename.
_PRODUCT_META_BY_BASENAME: Dict[str, ProductMeta] = load_product_metadata()


def get_product_metadata_for_basename(basename: str) -> Optional[ProductMeta]:
    """Return ProductMeta for a given image basename, if available."""
    return _PRODUCT_META_BY_BASENAME.get(basename)


def load_product_description(meta: ProductMeta) -> str:
    """Return the product's description text, if any."""
    if meta.description_path is None:
        return ""
    path = meta.description_path
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip(" \n\t")
    except OSError:
        return ""
    return text


