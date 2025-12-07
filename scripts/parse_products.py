from pathlib import Path
from das.ad_generation_dataclasses import Product


if __name__ == '__main__':
    products = [
        'camera.png',
        'catfood.png',
        'energy_drink.png',
        'gpu.png',
        'shoes.png',
        'tesla.png',
    ]
    products = [Product(path=Path('assets/products') / p) for p in products]
    for prod in products:
        print(f"--- Product: {prod.path.name} ---")
        print(prod.context)
        print()