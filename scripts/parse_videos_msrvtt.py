"""
Download MSR-VTT videos and captions from Hugging Face and organize them as:

- assets/videos/msrvtt/[video_id].mp4   (video files)
- assets/videos/msrvtt/[video_id].txt   (caption files)

Requires:
    pip install huggingface_hub datasets tqdm
"""

import os
import shutil
import zipfile
from collections import defaultdict

from huggingface_hub import hf_hub_download
from datasets import load_dataset
from tqdm import tqdm

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
DATASET_ID = "friedrichor/MSR-VTT"
VIDEO_ZIP_FILENAME = "MSRVTT_Videos.zip"
OUTPUT_DIR = "assets/videos/msrvtt"
TMP_EXTRACT_DIR = "_msrvtt_tmp_extract"

MSRVTT_CONFIG = "train_9k"
# ---------------------------------------------------------------------


def download_and_extract_videos():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TMP_EXTRACT_DIR, exist_ok=True)

    print("Downloading video zip from Hugging Face…")
    zip_path = hf_hub_download(
        repo_id=DATASET_ID,
        filename=VIDEO_ZIP_FILENAME,
        repo_type="dataset",
    )

    print(f"Downloaded to: {zip_path}")
    print("Extracting videos (this might take a bit)…")

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(TMP_EXTRACT_DIR)

    moved = 0
    for root, _, files in os.walk(TMP_EXTRACT_DIR):
        for fname in files:
            if not fname.lower().endswith(".mp4"):
                continue
            src = os.path.join(root, fname)
            dest = os.path.join(OUTPUT_DIR, fname)

            if not os.path.exists(dest):
                shutil.move(src, dest)
                moved += 1

    print(f"Moved {moved} .mp4 files into {OUTPUT_DIR!r}")
    shutil.rmtree(TMP_EXTRACT_DIR, ignore_errors=True)


def create_caption_files():
    print(f"Loading MSR-VTT split: config={MSRVTT_CONFIG}, split='train' …")
    ds = load_dataset(DATASET_ID, MSRVTT_CONFIG, split="train")

    captions_by_video = defaultdict(list)
    for example in tqdm(ds, desc="Collecting captions"):
        video_id = example["video_id"]
        caption = example["caption"]
        
        if isinstance(caption, list):
            captions_by_video[video_id].extend(caption)
        else:
            captions_by_video[video_id].append(caption)

    written = 0
    for video_id, captions in tqdm(captions_by_video.items(), desc="Writing caption files"):
        video_path = os.path.join(OUTPUT_DIR, f"{video_id}.mp4")
        
        if os.path.exists(video_path):
            caption_path = os.path.join(OUTPUT_DIR, f"{video_id}.txt")
            with open(caption_path, "w", encoding="utf-8") as f:
                f.write("\n".join(captions))
            written += 1

    print(f"Wrote {written} caption files to {OUTPUT_DIR!r}")


def main():
    download_and_extract_videos()
    create_caption_files()
    print("All done.")


if __name__ == "__main__":
    main()