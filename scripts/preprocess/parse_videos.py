"""
Download MSR-VTT videos and captions from Hugging Face and organize them as:

- assets/videos/[video_id].mp4        (all video files)
- assets/msrvtt_train9k_captions.csv  (one row per caption)

Requires:
    pip install huggingface_hub datasets pandas tqdm
"""

import os
import shutil
import zipfile
import csv

from huggingface_hub import hf_hub_download
from datasets import load_dataset
from tqdm import tqdm

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
DATASET_ID = "friedrichor/MSR-VTT"
VIDEO_ZIP_FILENAME = "MSRVTT_Videos.zip"  # from the HF "Files" tab
TARGET_VIDEO_DIR = "assets/videos"
TMP_EXTRACT_DIR = "assets/_msrvtt_tmp_extract"

# Which split to use ("train_9k", "train_7k", or "test_1k" per README)
MSRVTT_CONFIG = "train_9k"
CSV_OUTPUT = "assets/msrvtt_train9k_captions.csv"
# ---------------------------------------------------------------------


def download_and_extract_videos():
    os.makedirs(TARGET_VIDEO_DIR, exist_ok=True)
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
            dest = os.path.join(TARGET_VIDEO_DIR, fname)

            # Avoid overwriting if you rerun
            if not os.path.exists(dest):
                shutil.move(src, dest)
                moved += 1

    print(f"Moved {moved} .mp4 files into {TARGET_VIDEO_DIR!r}")

    # Clean up the temp directory
    shutil.rmtree(TMP_EXTRACT_DIR, ignore_errors=True)


def create_captions_csv():
    """
    Uses the HF dataset to get (video_id, caption) pairs.
    MSR-VTT on HF has columns like: video_id, video, caption, url, …
    
    """
    print(f"Loading MSR-VTT split: config={MSRVTT_CONFIG}, split='train' …")
    ds = load_dataset(DATASET_ID, MSRVTT_CONFIG, split="train")

    os.makedirs(os.path.dirname(CSV_OUTPUT), exist_ok=True)

    with open(CSV_OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # One row per caption
        writer.writerow(["video_id", "filename", "caption"])

        for example in tqdm(ds, desc="Writing captions"):
            video_id = example["video_id"]   # e.g. "video7020"
            caption = example["caption"]     # caption text

            filename = f"{video_id}.mp4"
            video_path = os.path.join(TARGET_VIDEO_DIR, filename)

            # Only keep rows where we actually have the video file
            if os.path.exists(video_path):
                writer.writerow([video_id, filename, caption])

    print(f"Wrote captions to {CSV_OUTPUT!r}")


def main():
    download_and_extract_videos()
    create_captions_csv()
    print("All done.")


if __name__ == "__main__":
    main()
