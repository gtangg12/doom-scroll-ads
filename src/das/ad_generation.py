import logging
import random
import re
from pathlib import Path

import ffmpeg
from PIL import Image
from pydantic import BaseModel
from xai_sdk.chat import user as chat_user

from das.ad_generation_dataclasses import PRODUCT_IMAGE_RESIZE_DIM, FAST_MODEL, Video, User, Product
from das.utils import create_chat, generate_image


class ProductSelection(BaseModel):
    selected_index: int
    reasoning: str


def _slugify(text: str) -> str:
    """Turn arbitrary text into a filesystem-friendly slug."""
    text = text.lower().strip()
    # Replace any run of non-alphanumeric characters with a single dash.
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text or "unknown"


def collect_cached_ads(directory: Path) -> list[Video]:
    """Collect already-generated ad videos from disk to seed the ad cache.

    We also attempt to infer the originating Product from the filename so that
    performance metrics can be correctly attributed when these cached ads are
    shown in the UI.
    """
    if not directory.exists():
        return []

    # Build a lookup table from slugified product name → product.path
    product_by_slug: dict[str, Path] = {}
    for prod in _PRODUCTS:
        slug = _slugify(prod.path.stem)
        product_by_slug[slug] = prod.path

    exts = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
    ads: list[Video] = []
    for p in directory.iterdir():
        if p.suffix.lower() not in exts or not p.is_file():
            continue

        # Generated ads follow the pattern "<product-slug>__<user-profile-slug>.ext".
        # We recover the product slug and map it back to a Product if possible.
        stem = p.stem
        product_slug = stem.split("__", 1)[0]
        product_path = product_by_slug.get(product_slug)

        if product_path is not None:
            ads.append(Video(path=p, product_path=product_path))
        else:
            # Fallback: keep the ad but without a product pointer; it will not
            # contribute to performance metrics but is still playable.
            ads.append(Video(path=p))

    random.shuffle(ads)
    return ads


def _collect_products(directory: Path) -> list[Product]:
    """Collect product images for ad generation."""
    if not directory.exists():
        return []
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    products: list[Product] = []
    for p in directory.iterdir():
        if p.suffix.lower() in exts and p.is_file():
            products.append(Product(path=p))
    return products


_PRODUCTS: list[Product] = _collect_products(Path("assets/products"))
print(f"[ADS] Loaded {len(_PRODUCTS)} products from assets/products")


def generate_ad(
    user: User,
    edit: bool = True,
) -> Video:
    user_context = user.context  # key phrase describing the user profile
    print("User Context: %s\n", user_context)

    product_list_paragraph = "\n".join(f"{i}. {p.path.stem}" for i, p in enumerate(_PRODUCTS))
    chat = create_chat('assets/prompts/product_selection.txt', model=FAST_MODEL)
    chat.append(chat_user(f"User context: {user_context}\n\nProducts:\n{product_list_paragraph}"))
    response, selection = chat.parse(ProductSelection)
    prod_index = min(max(selection.selected_index, 0), len(_PRODUCTS) - 1)
    prod = _PRODUCTS[prod_index]
    print(f"Selected Product Index: {prod_index}")  # should log catfood for test case below
    print(f"Reasoning: {selection.reasoning}")
    #prod = random.choice(_PRODUCTS)

    prod_context = prod.context
    print("Prod Context: %s\n", prod_context)

    chat = create_chat('assets/prompts/image_generation.txt', model=FAST_MODEL)
    chat.append(chat_user(f"User Context: {user_context}\nProduct Context: {prod_context}"))
    response = chat.sample()
    response = response.content.strip(' \n\t')
    print("Generated Ad Prompt: %s\n", response)

    # Stable, human-readable ID based on product name and user profile key phrase.
    product_name = prod.path.stem
    user_profile_phrase = user_context
    _id = f"{_slugify(product_name[:127])}__{_slugify(user_profile_phrase[:127])}"
    output_dir = Path('assets/videos_generated')
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"{_id}.png"
    video_path = output_dir / f"{_id}.mp4"

    if edit:
        image = Image.open(prod.path)
        image.thumbnail(PRODUCT_IMAGE_RESIZE_DIM)
        image = generate_image(response, image=image)
    else:
        image = generate_image(response)
    image.save(image_path)

    # Create 5 second video from image, suppressing noisy ffmpeg output.
    (
        ffmpeg
        .input(str(image_path), loop=1, t=5)
        .output(str(video_path), vcodec='libx264', pix_fmt='yuv420p', loglevel='error')
        .overwrite_output()
        .run(quiet=True)
    )

    return Video(path=video_path, product_path=prod.path)


if __name__ == '__main__':
    from das.ad_generation_dataclasses import Video, Product, User, UserReaction

    video = Video(path=Path('assets/videos/sample.mp4'))
    product = Product(path=Path('assets/products/sample.png'))
    user = User()
    user.append_video(video, UserReaction(heart=True, share=False))

    generate_ad(user, [product])