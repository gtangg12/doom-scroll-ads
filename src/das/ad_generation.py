import logging
import random
import uuid
from pathlib import Path

import ffmpeg
from PIL import Image
from xai_sdk.chat import user as chat_user

from das.ad_generation_dataclasses import PRODUCT_IMAGE_RESIZE_DIM, Video, User, Product
from das.utils import create_chat, encode_base64, generate_image


LOGGING_COLOR_GREEN = '\033[92m'
LOGGING_COLOR_RESET = '\033[0m'

logging.basicConfig(format=f'{LOGGING_COLOR_GREEN}%(levelname)s{LOGGING_COLOR_RESET}: %(message)s', level=logging.INFO)


def generate_ad(user: User, products: list[Product], edit=True) -> Video:
    # NOTE queue worker for this function and let user keep scrolling until ad is ready

    # TODO replace with recommendation system
    prod = random.choice(products)

    user_context = user.context
    prod_context = prod.context
    logging.info("User Context: %s\n", user_context)
    logging.info("Prod Context: %s\n", prod_context)

    chat = create_chat('assets/prompts/image_generation.txt')
    chat.append(chat_user(f"User Context: {user_context}\nProduct Context: {prod_context}"))
    response = chat.sample()
    response = response.content.strip(' \n\t')
    logging.info("Generated Ad Prompt: %s\n", response)

    _id = str(uuid.uuid4())
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
    # Create 5 second video from image
    ffmpeg.input(str(image_path), loop=1, t=5).output(str(video_path), vcodec='libx264', pix_fmt='yuv420p').run()

    return Video(path=video_path)


if __name__ == '__main__':
    from das.ad_generation_dataclasses import Video, Product, User, UserReaction

    video = Video(path=Path('assets/videos/sample.mp4'))
    product = Product(path=Path('assets/products/sample.png'))
    user = User()
    user.append_video(video, UserReaction(heart=True, share=False))

    generate_ad(user, [product])