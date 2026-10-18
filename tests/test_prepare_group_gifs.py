"""Exercise the real FFmpeg renderer with generated, recognisable video frames."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "prepare_group_gifs.py"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="ffmpeg and ffprobe are required")


def command(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(arguments, capture_output=True, check=True)


@pytest.fixture
def colour_video(tmp_path):
    source = tmp_path / "source with spaces.mp4"
    command("ffmpeg", "-v", "error", "-nostdin", "-y", "-filter_complex_threads", "1",
            "-f", "lavfi", "-i", "color=c=red:s=96x64:r=30:d=1",
            "-f", "lavfi", "-i", "color=c=lime:s=96x64:r=30:d=1",
            "-f", "lavfi", "-i", "color=c=blue:s=96x64:r=30:d=1",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]",
            "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(source))
    return source


def manifest_for(tmp_path, source, segments, *, number="Б05-605"):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"version": 1, "groups": [
        {"id": "stable-id", "number": number, "input": source.name, "segments": segments}
    ]}, ensure_ascii=False), encoding="utf-8")
    return path


def render(manifest, output, *options, expected=0):
    process = subprocess.run([sys.executable, str(SCRIPT), str(manifest), "--output", str(output), *options],
                             capture_output=True, text=True)
    assert process.returncode == expected, (process.stdout, process.stderr)
    return json.loads((output / "results.json").read_text(encoding="utf-8"))["groups"][0]


def frame_colour(path, timestamp):
    raw = command("ffmpeg", "-v", "error", "-threads", "1", "-filter_threads", "1", "-ss", str(timestamp),
                  "-i", str(path), "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
                  "-pix_fmt", "rgb24", "pipe:1").stdout
    assert len(raw) == 3
    return tuple(raw)


def test_list_order_exact_intervals_and_safe_looping_gif(tmp_path, colour_video):
    # A chronological sort would start red. The explicit list must start blue.
    manifest = manifest_for(tmp_path, colour_video, [[2, 3], [0, 1]])
    record = render(manifest, tmp_path / "out")
    assert record["status"] == "ok"
    assert record["segments"] == [[2, 3], [0, 1]]
    assert record["requested_duration_seconds"] == 2
    for kind in ("gif", "mp4"):
        info = record[kind]
        path = Path(info["path"])
        assert path.exists()
        assert info["duration_seconds"] == pytest.approx(2, abs=.12)
        blue = frame_colour(path, .3)
        red = frame_colour(path, 1.3)
        assert blue[2] > 200 and blue[0] < 40, blue
        assert red[0] > 200 and red[2] < 40, red
    gif = record["gif"]
    assert gif["width"] / gif["height"] == pytest.approx(1.5, abs=.02)
    assert gif["frames"] == 60
    assert record["render_settings"]["fps"] == 30
    assert gif["decoded_pixels"] <= 80_000_000
    assert gif["bytes"] < 10 * 1024 * 1024
    assert b"NETSCAPE2.0\x03\x01\x00\x00" in Path(gif["path"]).read_bytes()


def test_out_of_bounds_rerun_removes_stale_outputs(tmp_path, colour_video):
    manifest = manifest_for(tmp_path, colour_video, [[0, 1]])
    output = tmp_path / "out"
    previous = render(manifest, output)
    manifest_for(tmp_path, colour_video, [[2, 4]])
    failed = render(manifest, output, expected=1)
    assert failed["status"] == "failed"
    assert "beyond video duration" in failed["error"]
    assert not Path(previous["gif"]["path"]).exists()
    assert not Path(previous["mp4"]["path"]).exists()
    assert "gif" not in failed


def test_resume_checks_segment_order_and_output_hash(tmp_path, colour_video):
    manifest = manifest_for(tmp_path, colour_video, [[2, 3], [0, 1]])
    output = tmp_path / "out"
    first = render(manifest, output)
    gif_path = Path(first["gif"]["path"])
    timestamp = gif_path.stat().st_mtime_ns
    reused = render(manifest, output)
    assert reused["reused"] is True
    assert gif_path.exists() and gif_path.stat().st_mtime_ns == timestamp
    gif_path.write_bytes(b"corrupted GIF")
    repaired = render(manifest, output)
    assert repaired["reused"] is False
    assert repaired["gif"]["sha256"] == first["gif"]["sha256"]
    manifest_for(tmp_path, colour_video, [[0, 1], [2, 3]])
    reordered = render(manifest, output)
    assert reordered["reused"] is False
    assert reordered["fingerprint"] != first["fingerprint"]
    assert frame_colour(gif_path, .3)[0] > 200


def test_portrait_rotation_and_reduced_frame_pixel_budgets(tmp_path):
    landscape = tmp_path / "landscape.mp4"
    portrait = tmp_path / "portrait.mp4"
    command("ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "testsrc2=s=160x96:r=30:d=3", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
            str(landscape))
    command("ffmpeg", "-v", "error", "-nostdin", "-y", "-display_rotation", "90", "-i", str(landscape), "-c", "copy", str(portrait))
    manifest = manifest_for(tmp_path, portrait, [[0, 3]])
    record = render(manifest, tmp_path / "out", "--fps", "3", "--max-frames", "12", "--max-pixels", "50000")
    gif = record["gif"]
    assert gif["width"] < gif["height"]
    assert gif["width"] / gif["height"] == pytest.approx(96 / 160, abs=.03)
    assert gif["frames"] <= 12
    assert gif["decoded_pixels"] <= 50000
    assert gif["duration_seconds"] == pytest.approx(3, abs=.35)


def test_ambiguous_group_number_requires_explicit_id(tmp_path, colour_video):
    manifest = manifest_for(tmp_path, colour_video, [[0, 1]])
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["groups"].append({**data["groups"][0], "id": "other-id"})
    manifest.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "out"
    process = subprocess.run([sys.executable, str(SCRIPT), str(manifest), "--output", str(output),
                              "--group", "Б05-605"], capture_output=True, text=True)
    assert process.returncode == 2
    assert "Ambiguous group selector" in process.stderr
    assert not output.exists()
    selected = render(manifest, output, "--group", "other-id")
    assert selected["id"] == "other-id"


def test_long_30fps_clip_preserves_cadence_and_scales_to_pixel_budget(tmp_path):
    source = tmp_path / "long.mp4"
    command("ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "testsrc2=s=640x360:r=30:d=17", "-c:v", "libx264", "-threads", "1",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source))
    record = render(manifest_for(tmp_path, source, [[0, 17]]), tmp_path / "out")
    assert record["gif"]["frames"] == 510
    assert record["gif"]["duration_seconds"] == pytest.approx(17, abs=.05)
    assert record["gif"]["decoded_pixels"] <= 80_000_000
    assert record["render_settings"]["fps"] == 30
    packets = json.loads(command("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets", "-of", "json", record["gif"]["path"]).stdout)["packets"]
    assert {round(float(packet["duration_time"]), 2) for packet in packets} == {.03, .04}
