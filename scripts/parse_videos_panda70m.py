"""
Download Panda-70M videos and captions from Hugging Face and organize them as:

- assets/videos/panda70m/[video_id]_[clip_idx].mp4   (video files)
- assets/videos/panda70m/[video_id]_[clip_idx].txt   (caption files)

Requires:
    pip install datasets yt-dlp tqdm
"""

import os
import subprocess
import random
import time
from datasets import load_dataset
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import ast

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
DATASET_ID = "multimodalart/panda-70m"
OUTPUT_DIR = "assets/videos/panda70m"
SPLIT = "train_2m"
TARGET_VIDEOS = 1000
NUM_WORKERS = 4  # Number of parallel downloads (keep low to avoid blocking)
MIN_DELAY = 1
MAX_DELAY = 3
# ---------------------------------------------------------------------

# Thread-safe counters
success_count = 0
fail_count = 0
counter_lock = Lock()


def parse_timestamp(ts_str):
    """Convert timestamp string like '0:00:16.300' to seconds."""
    parts = ts_str.split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    else:
        return float(parts[0])


def download_clip(clip):
    """Download a clip from YouTube using yt-dlp with audio."""
    global success_count, fail_count
    
    video_id = clip["video_id"]
    clip_idx = clip["clip_idx"]
    start_time = parse_timestamp(clip["start"])
    end_time = parse_timestamp(clip["end"])
    caption = clip["caption"]
    
    clip_name = f"{video_id}_{clip_idx}"
    video_path = os.path.join(OUTPUT_DIR, f"{clip_name}.mp4")
    caption_path = os.path.join(OUTPUT_DIR, f"{clip_name}.txt")
    
    # Skip if already downloaded
    if os.path.exists(video_path) and os.path.exists(caption_path):
        with counter_lock:
            success_count += 1
        return True
    
    # Random delay
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    cmd = [
        "yt-dlp",
        # Video + audio format
        "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--download-sections", f"*{start_time}-{end_time}",
        "-o", video_path,
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        # Anti-blocking
        "--sleep-requests", "1",
        "--extractor-retries", "3",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        url
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0 and os.path.exists(video_path):
            with open(caption_path, "w", encoding="utf-8") as f:
                f.write(caption)
            with counter_lock:
                success_count += 1
            return True
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        pass
    
    # Clean up partial download
    if os.path.exists(video_path):
        try:
            os.remove(video_path)
        except:
            pass
    
    with counter_lock:
        fail_count += 1
    return False


def main():
    global success_count, fail_count
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print(f"Loading Panda-70M dataset (split={SPLIT})…")
    ds = load_dataset(DATASET_ID, split=SPLIT)
    
    # Flatten all clips into a list
    all_clips = []
    print("Collecting clips…")
    for example in tqdm(ds, desc="Processing metadata"):
        video_id = example["videoID"]
        
        try:
            timestamps = ast.literal_eval(example["timestamp"]) if isinstance(example["timestamp"], str) else example["timestamp"]
            captions = ast.literal_eval(example["caption"]) if isinstance(example["caption"], str) else example["caption"]
        except:
            timestamps = example["timestamp"]
            captions = example["caption"]
        
        if not isinstance(timestamps, list):
            timestamps = [timestamps]
        if not isinstance(captions, list):
            captions = [captions]
        
        for clip_idx, (ts, caption) in enumerate(zip(timestamps, captions)):
            if isinstance(ts, list) and len(ts) == 2:
                all_clips.append({
                    "video_id": video_id,
                    "clip_idx": clip_idx,
                    "start": ts[0],
                    "end": ts[1],
                    "caption": caption
                })
    
    print(f"Found {len(all_clips)} total clips")
    
    # Shuffle for diversity
    random.shuffle(all_clips)
    
    # Take more than needed to account for failures
    clips_to_try = all_clips[:TARGET_VIDEOS * 3]
    
    print(f"Starting download with {NUM_WORKERS} workers…")
    
    pbar = tqdm(total=TARGET_VIDEOS, desc="Downloading clips")
    
    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {}
        clip_iter = iter(clips_to_try)
        
        # Submit initial batch
        for _ in range(min(NUM_WORKERS * 2, len(clips_to_try))):
            try:
                clip = next(clip_iter)
                future = executor.submit(download_clip, clip)
                futures[future] = clip
            except StopIteration:
                break
        
        while futures and success_count < TARGET_VIDEOS:
            # Wait for any future to complete
            done_futures = []
            for future in list(futures.keys()):
                if future.done():
                    done_futures.append(future)
            
            if not done_futures:
                time.sleep(0.1)
                continue
            
            for future in done_futures:
                del futures[future]
                
                # Update progress bar
                pbar.n = success_count
                pbar.set_postfix({"success": success_count, "fail": fail_count})
                pbar.refresh()
                
                # Submit new task if needed
                if success_count < TARGET_VIDEOS:
                    try:
                        clip = next(clip_iter)
                        new_future = executor.submit(download_clip, clip)
                        futures[new_future] = clip
                    except StopIteration:
                        pass
        
        # Cancel remaining futures if we hit target
        for future in futures:
            future.cancel()
    
    pbar.n = success_count
    pbar.refresh()
    pbar.close()
    
    print(f"\nDone!")
    print(f"Successfully downloaded: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Output directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()