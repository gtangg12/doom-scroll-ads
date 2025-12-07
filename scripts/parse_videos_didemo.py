"""
Download DiDeMo videos and captions from Hugging Face and organize them as:

- assets/videos/didemo/[video_id].mp4   (video files)
- assets/videos/didemo/[video_id].txt   (caption files)

Requires:
    pip install huggingface_hub datasets tqdm
"""

import os
import shutil
import tarfile
import glob
from collections import defaultdict

from huggingface_hub import hf_hub_download, list_repo_files
from datasets import load_dataset
from tqdm import tqdm

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
DATASET_ID = "friedrichor/DiDeMo"
OUTPUT_DIR = "assets/videos/didemo"
TMP_EXTRACT_DIR = "_didemo_tmp_extract"
# ---------------------------------------------------------------------


def download_and_extract_videos():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TMP_EXTRACT_DIR, exist_ok=True)

    # Get list of all files in repo
    print("Fetching file list from Hugging Face…")
    all_files = list_repo_files(repo_id=DATASET_ID, repo_type="dataset")
    
    # Find all tar files
    tar_files = [f for f in all_files if f.endswith(".tar") or ".tar.part-" in f]
    print(f"Found tar files: {tar_files}")

    # Download all tar files
    downloaded_parts = []
    for tar_file in tqdm(tar_files, desc="Downloading tar files"):
        path = hf_hub_download(
            repo_id=DATASET_ID,
            filename=tar_file,
            repo_type="dataset",
        )
        downloaded_parts.append((tar_file, path))

    # Handle split tar files (train) and regular tar files (test)
    # Group by base name
    train_parts = sorted([p for name, p in downloaded_parts if "train" in name and ".part-" in name])
    test_tar = [p for name, p in downloaded_parts if "test" in name and ".tar" in name and ".part-" not in name]

    # Extract train (split tar)
    if train_parts:
        print("Combining and extracting train tar parts…")
        combined_tar = os.path.join(TMP_EXTRACT_DIR, "train_combined.tar")
        with open(combined_tar, "wb") as outfile:
            for part in train_parts:
                with open(part, "rb") as infile:
                    shutil.copyfileobj(infile, outfile)
        
        with tarfile.open(combined_tar, "r") as tar:
            tar.extractall(TMP_EXTRACT_DIR)
        os.remove(combined_tar)

    # Extract test tar
    for tar_path in test_tar:
        print(f"Extracting {tar_path}…")
        with tarfile.open(tar_path, "r") as tar:
            tar.extractall(TMP_EXTRACT_DIR)

    # Move all mp4 files to output dir
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
    print("Loading DiDeMo dataset…")
    
    captions_by_video = defaultdict(list)
    
    for split in ["train", "test"]:
        print(f"Processing {split} split…")
        try:
            ds = load_dataset(DATASET_ID, split=split)
            
            for example in tqdm(ds, desc=f"Collecting captions ({split})"):
                # Video field contains the path like "train/xxx.mp4"
                video_path = example["video"]
                video_filename = os.path.basename(video_path)
                video_id = os.path.splitext(video_filename)[0]
                
                caption = example["caption"]
                
                if isinstance(caption, list):
                    captions_by_video[video_id].extend(caption)
                else:
                    captions_by_video[video_id].append(caption)
        except Exception as e:
            print(f"Warning: Could not load {split} split: {e}")

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