# doom-scroll-ads

## Installation
```
pip install -e .
```

## Download content videos
Choose one of the following options:
```bash
# Execute in project root because the script uses relative path
python scripts/preprocess/parse_videos_msrvtt.py
python scripts/preprocess/parse_videos_didemo.py
python scripts/preprocess/parse_videos_panda70m.py
```

## 
```bash
python scripts/launch.py --video_dir assets/videos/msrvtt
```