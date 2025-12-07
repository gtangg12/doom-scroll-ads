import base64
import io
import requests
import os
from pathlib import Path
from typing import Optional

from PIL import Image
from xai_sdk import Client
from xai_sdk.chat import system as chat_system


def create_chat(prompt_filename: Path | str, model='grok-4', tools: Optional[list]=None, timeout=3600):
    """ Create Grok chat with system prompt from `prompt_filename`
    """
    with open(prompt_filename, 'r') as f:
        prompt = f.read().strip(' \n\t')
    client = Client(
        api_key=os.getenv("XAI_API_KEY"),
        timeout=timeout,
    )
    chat = client.chat.create(
        model=model,
        tools=tools,
    )
    chat.append(chat_system(prompt))
    return chat


def encode_base64(image: Image.Image, format='PNG'):
    """ Encode PIL image in base64 format
    """
    buffer = io.BytesIO()
    image = image.convert('RGB')
    image.save(buffer, format=format)
    encoded_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{encoded_base64}"


def generate_image(prompt: str, model: str = "grok-imagine-v0p9", image: Optional[Image.Image] = None) -> Image.Image:
    """ Generate an image using Grok Imagine
    """
    if image is None:
        endpoint = 'https://api.x.ai/v1/images/generations'
    else:
        endpoint = 'https://api.x.ai/v1/images/edits'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {os.getenv("XAI_API_KEY")}'
    }
    data = dict(prompt=prompt, model=model)
    if image is not None:
        data['image'] = {'url': encode_base64(image)}
    response = requests.post(endpoint, headers=headers, json=data)
    response.raise_for_status()
    response = response.json()
    image_url = response['data'][0]['url']
    return Image.open(requests.get(image_url, stream=True).raw)