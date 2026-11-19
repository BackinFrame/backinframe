import io

import pytest
from helpers import has_ffmpeg, make_image, make_video
from PIL import Image

from backinframe.media import SkipMedia, prepare, prepare_image, prepare_video

MB = 1024 * 1024


def open_jpeg(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG"
    return img


def test_image_is_resized_to_max_edge(tmp_path):
    p = make_image(tmp_path / "big.jpg", size=(4000, 3000))
    out = prepare_image(p, 2048, 15 * MB)
    img = open_jpeg(out.data)
    assert max(img.size) == 2048
    assert out.filename == "big.jpg" and out.content_type == "image/jpeg"


def test_small_image_is_not_upscaled(tmp_path):
    p = make_image(tmp_path / "small.jpg", size=(640, 480))
    assert open_jpeg(prepare_image(p, 2048, 15 * MB).data).size == (640, 480)


def test_exif_orientation_is_applied(tmp_path):
    p = make_image(tmp_path / "rot.jpg", size=(800, 400), orientation=6)  # rotate 90°
    assert open_jpeg(prepare_image(p, 2048, 15 * MB).data).size == (400, 800)


def test_png_with_transparency_becomes_jpeg(tmp_path):
    p = tmp_path / "t.png"
    Image.new("RGBA", (300, 200), (255, 0, 0, 0)).save(p)
    out = prepare_image(p, 2048, MB)
    img = open_jpeg(out.data)
    assert img.mode == "RGB"
    assert out.filename == "t.jpg"


def test_image_shrinks_to_fit_limit(tmp_path):
    p = make_image(tmp_path / "noise.jpg", size=(3000, 3000), noise=True)
    limit = 300 * 1024
    out = prepare_image(p, 2048, limit)
    assert len(out.data) <= limit


def test_image_that_never_fits_is_skipped(tmp_path):
    p = make_image(tmp_path / "noise.jpg", size=(2000, 2000), noise=True)
    with pytest.raises(SkipMedia):
        prepare_image(p, 2048, 2 * 1024)


@pytest.mark.ffmpeg
@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
def test_small_video_passes_through(tmp_path):
    """Not re-encoded: the streams are copied, only the container metadata is rewritten."""
    p = make_video(tmp_path / "clip.mp4", seconds=1)
    out = prepare_video(p, 15 * MB)
    assert out.content_type == "video/mp4" and out.filename == "clip.mp4"
    assert abs(len(out.data) - p.stat().st_size) < 2048


@pytest.mark.ffmpeg
@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
def test_large_video_is_transcoded_under_limit(tmp_path):
    p = make_video(tmp_path / "big.mkv", seconds=4, size="1280x720", noisy=True)
    limit = 400 * 1024
    assert p.stat().st_size > limit
    out = prepare_video(p, limit)
    assert len(out.data) <= limit
    assert out.filename == "big.mp4"


@pytest.mark.ffmpeg
@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
def test_video_too_long_for_limit_is_skipped(tmp_path):
    p = make_video(tmp_path / "long.mp4", seconds=6, size="640x480", noisy=True)
    with pytest.raises(SkipMedia):
        prepare_video(p, 50 * 1024)


def test_prepare_dispatches(tmp_path):
    p = make_image(tmp_path / "a.jpg")
    assert prepare(p, "image", max_edge=1024, image_limit=MB, video_limit=MB).content_type == "image/jpeg"


def test_photo_keeps_only_its_date(tmp_path):
    from datetime import datetime

    p = make_image(tmp_path / "gps.jpg", taken=datetime(2024, 3, 1, 10, 30), gps=(-41.47, -72.94), orientation=6)
    out = prepare_image(p, 2048, 15 * MB, taken_at=datetime(2024, 3, 1, 10, 30))
    exif = Image.open(io.BytesIO(out.data)).getexif()
    assert exif.get_ifd(0x8769).get(0x9003) == "2024:03:01 10:30:00"  # date kept
    assert not exif.get_ifd(0x8825)  # no GPS
    assert 0x0112 not in exif  # no orientation flag: pixels are already upright
    assert set(exif) <= {0x0132, 0x8769}
    # without a date nothing at all is written
    assert not Image.open(io.BytesIO(prepare_image(p, 2048, 15 * MB).data)).getexif()


def _video_tags(data: bytes, tmp_path, suffix=".mp4") -> dict:
    import json
    import subprocess

    f = tmp_path / f"probe{suffix}"
    f.write_bytes(data)
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(f)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)["format"].get("tags", {})


def _with_location(src, dest):
    import subprocess

    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-c", "copy",
                    "-metadata", "location=+52.3676+004.9041/", "-metadata", "title=Thuis", str(dest)], check=True)
    return dest


@pytest.mark.ffmpeg
@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
def test_video_passthrough_drops_location_but_keeps_date(tmp_path):
    from datetime import datetime

    src = _with_location(make_video(tmp_path / "raw.mp4", seconds=1), tmp_path / "clip.mp4")
    assert "location" in _video_tags(src.read_bytes(), tmp_path)
    out = prepare_video(src, 15 * MB, taken_at=datetime(2024, 7, 1, 12, 0))
    tags = _video_tags(out.data, tmp_path)
    assert not any("location" in k for k in tags) and "title" not in tags
    assert tags["creation_time"].startswith("2024-07-01T12:00:00")
    assert out.filename == "clip.mp4"


@pytest.mark.ffmpeg
@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
def test_transcoded_video_drops_location(tmp_path):
    src = _with_location(make_video(tmp_path / "raw.mkv", seconds=3, size="1280x720", noisy=True),
                         tmp_path / "big.mkv")
    out = prepare_video(src, 400 * 1024)
    assert not any("location" in k for k in _video_tags(out.data, tmp_path))
