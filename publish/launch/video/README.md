# Launch video

`make_video.py` makes the 24-second launch video (1080×1920, for Reels, Shorts, TikTok and
Stories) from a folder of photos. Album names and capture dates from the photos end up in the
notifications, as in the real app.

```bash
pip install playwright pillow && playwright install chrome   # and ffmpeg
python3 make_video.py --photos ~/Pictures/launch --out backinframe-launch.mp4
```

- One folder per album (`Chile 2025/IMG_1234.jpg`). 12 or more photos look best.
- Mix years and occasions: the video is about all the photos on a phone, not one trip.
- Without `--photos` the illustrations in `demo-photos/` are used.
- Only use photos you may publish, and of people who agreed.
