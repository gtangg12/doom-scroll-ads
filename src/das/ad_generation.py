from PIL import Image

from das.ad_generation_dataclasses import Video, User, Product


def generate_ad(user: User, prod: Product) -> Video:
    # queue worker for this function and let user keep scrolling until ad is ready
    user_context = user.context()
    prod_context = prod.context()

    # create image first from user and product context
    image = None

    # create video from user, product, and image
    video = None

    return video