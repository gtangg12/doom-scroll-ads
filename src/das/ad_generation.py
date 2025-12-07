import logging
import random
import re
from pathlib import Path

import ffmpeg
from PIL import Image
from xai_sdk.chat import user as chat_user

from das.ad_generation_dataclasses import PRODUCT_IMAGE_RESIZE_DIM, Video, User, Product
from das.ad_performance import AdPerformanceStore
from das.utils import create_chat, encode_base64, generate_image


LOGGING_COLOR_GREEN = '\033[92m'
LOGGING_COLOR_RESET = '\033[0m'

logging.basicConfig(
    format=f'{LOGGING_COLOR_GREEN}%(levelname)s{LOGGING_COLOR_RESET}: %(message)s',
    level=logging.INFO,
)


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
    for prod in _products:
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


_products: list[Product] = _collect_products(Path("assets/products"))
print(f"[ADS] Loaded {len(_products)} products from assets/products")


def generate_ad(
    user: User,
    products: list[Product],
    edit: bool = True,
) -> Video:
    # NOTE queue worker for this function and let user keep scrolling until ad is ready

    # TODO replace with a richer recommendation system dependent on user context, or using simple historical performance metrics.
    prod = random.choice(products)

    user_context = user.context  # key phrase describing the user profile
    prod_context = prod.context
    logging.info("User Context: %s\n", user_context)
    logging.info("Prod Context: %s\n", prod_context)

    chat = create_chat('assets/prompts/image_generation.txt')
    chat.append(chat_user(f"User Context: {user_context}\nProduct Context: {prod_context}"))
    response = chat.sample()
    response = response.content.strip(' \n\t')
    logging.info("Generated Ad Prompt: %s\n", response)

    # Stable, human-readable ID based on product name and user profile key phrase.
    product_name = prod.path.stem
    user_profile_phrase = user_context
    _id = f"{_slugify(product_name[:127])}__{_slugify(user_profile_phrase[:127])}"
    output_dir = Path('assets/videos_generated')
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"{_id}.png"
    video_path = output_dir / f"{_id}.mp4"

    # If we've already generated this exact ad before, reuse the cached video.
    if video_path.exists():
        logging.info("Reusing cached ad video at %s", video_path)
        return Video(path=video_path, product_path=prod.path)

    # If the image exists but the video does not, reuse the image for video creation.
    if image_path.exists():
        logging.info("Reusing cached ad image at %s", image_path)
    else:
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