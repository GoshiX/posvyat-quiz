#!/usr/bin/env python3
"""Render explicitly mapped, ordered video excerpts to looping GIFs and MP4 montages.

Only Python's standard library and the local ffmpeg/ffprobe executables are used.
See docs/group-video-gifs.md for the JSON schema and operational details.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


RENDERER_VERSION = 3
UPLOAD_MAX_BYTES = 64 * 1024 * 1024 - 1
UPLOAD_MAX_FRAMES = 600
UPLOAD_MAX_PIXELS = 80_000_000
UPLOAD_MAX_SIDE = 640
CLIP_FPS = 30


class RenderError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    max_side: int = 640
    fps: float = 30.0
    max_frames: int = 600
    max_pixels: int = 80_000_000
    max_bytes: int = UPLOAD_MAX_BYTES


def run(command: list[str]) -> str:
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode or process.stderr.strip():
        detail = process.stderr.strip()[-2500:] or f"exit code {process.returncode}"
        raise RenderError(f"{Path(command[0]).name}: {detail}")
    return process.stdout


def ffmpeg(*arguments: str) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1",
         *arguments])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe(path: Path, *, count_frames: bool = False) -> dict[str, Any]:
    command = ["ffprobe", "-v", "error"]
    if count_frames:
        command += ["-count_frames"]
    command += ["-show_streams", "-show_format", "-of", "json", str(path)]
    result = json.loads(run(command))
    videos = [stream for stream in result.get("streams", [])
              if stream.get("codec_type") == "video"
              and not stream.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise RenderError(f"No video stream: {path}")
    stream = videos[0]
    duration = stream.get("duration")
    if duration in (None, "N/A") and stream.get("duration_ts") is not None:
        duration = float(stream["duration_ts"]) * float(Fraction(stream["time_base"]))
    if duration in (None, "N/A"):
        duration = result.get("format", {}).get("duration")
    try:
        duration = float(duration)
    except (TypeError, ValueError) as error:
        raise RenderError(f"Cannot determine video duration: {path}") from error
    if not math.isfinite(duration) or duration <= 0:
        raise RenderError(f"Invalid video duration: {path}")
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if width < 1 or height < 1:
        raise RenderError(f"Invalid video dimensions: {path}")
    try:
        sar = Fraction(stream.get("sample_aspect_ratio", "1:1").replace(":", "/"))
        if sar <= 0:
            sar = Fraction(1)
    except (ValueError, ZeroDivisionError):
        sar = Fraction(1)
    rotation = float(stream.get("tags", {}).get("rotate", 0))
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = float(side_data["rotation"])
    display_width, display_height = width * float(sar), float(height)
    if round(rotation) % 180:
        display_width, display_height = display_height, display_width
    frames = stream.get("nb_read_frames", stream.get("nb_frames"))
    return {"duration_seconds": duration, "width": width, "height": height,
            "display_width": display_width, "display_height": display_height,
            "rotation_degrees": rotation, "stream_index": int(stream["index"]),
            "frames": int(frames) if frames not in (None, "N/A") else None,
            "codec": stream.get("codec_name"),
            "audio_streams": sum(s.get("codec_type") == "audio"
                                 for s in result.get("streams", []))}


def scaled_dimensions(width: float, height: float, max_side: int,
                      max_area: float = math.inf) -> tuple[int, int]:
    scale = min(1.0, max_side / max(width, height), math.sqrt(max_area / (width * height)))
    # H.264 yuv420p requires even dimensions. Flooring keeps every limit strict.
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def numeric(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RenderError(f"{description} must be a finite number")
    return float(value)


def ordered_segments(group: dict[str, Any]) -> list[list[float]]:
    raw = group.get("segments")
    if not isinstance(raw, list) or not raw:
        raise RenderError("segments must be a nonempty list of [start, end] pairs")
    result = []
    for index, pair in enumerate(raw):
        if not isinstance(pair, list) or len(pair) != 2:
            raise RenderError(f"segments[{index}] must be [start, end]")
        start = numeric(pair[0], f"segments[{index}].start")
        end = numeric(pair[1], f"segments[{index}].end")
        if start < 0 or end <= start:
            raise RenderError(f"Invalid segment {index}: require 0 <= start < end")
        result.append([start, end])
    return result


def load_groups(manifest: Path, selectors: list[str]) -> list[dict[str, Any]]:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise RenderError("Manifest must be an object with version: 1")
    groups = document.get("groups")
    if not isinstance(groups, list) or not groups:
        raise RenderError("Manifest groups must be a nonempty list")
    ids: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            raise RenderError("Each group must be an object")
        for key in ("id", "number", "input"):
            if not isinstance(group.get(key), str) or not group[key].strip():
                raise RenderError(f"Each group requires a nonempty string {key}")
        if group["id"] in ids:
            raise RenderError(f"Duplicate group id: {group['id']}")
        ids.add(group["id"])
    if not selectors:
        return groups
    selected: set[str] = set()
    for selector in selectors:
        matches = [group for group in groups
                   if selector in (group["id"], group["number"])]
        if not matches:
            raise RenderError(f"Unknown group selector: {selector}")
        if len(matches) > 1:
            raise RenderError(f"Ambiguous group selector {selector!r}; use its unique id")
        selected.add(matches[0]["id"])
    return [group for group in groups if group["id"] in selected]


def output_stem(group: dict[str, Any]) -> str:
    safe_number = re.sub(r"[^\w.-]+", "-", group["number"]).strip(".-")[:70] or "group"
    suffix = hashlib.sha256(group["id"].encode()).hexdigest()[:12]
    return f"{safe_number}--{suffix}"


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def artifact_metadata(path: Path, information: dict[str, Any]) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size,
            **{key: information[key] for key in ("duration_seconds", "width", "height", "frames")},
            "decoded_pixels": information["width"] * information["height"] * information["frames"]}


def validate_artifact(path: Path, kind: str, expected_duration: float, tolerance: float,
                      settings: Settings) -> dict[str, Any]:
    information = probe(path, count_frames=True)
    if information["codec"] != ("gif" if kind == "gif" else "h264"):
        raise RenderError(f"Unexpected {kind} video codec")
    if information["audio_streams"]:
        raise RenderError(f"Unexpected audio in {kind}")
    if not information["frames"] or information["frames"] < 1:
        raise RenderError(f"Empty {kind} animation")
    if abs(information["duration_seconds"] - expected_duration) > tolerance:
        raise RenderError(f"{kind} duration {information['duration_seconds']:.4f}s differs from "
                          f"requested {expected_duration:.4f}s (tolerance {tolerance:.4f}s)")
    if kind == "gif":
        if path.stat().st_size > settings.max_bytes:
            raise RenderError("GIF exceeds the configured export byte budget")
        if max(information["width"], information["height"]) > settings.max_side:
            raise RenderError("GIF exceeds the configured dimension budget")
        if information["frames"] > settings.max_frames:
            raise RenderError("GIF exceeds the configured frame budget")
        if information["width"] * information["height"] * information["frames"] > settings.max_pixels:
            raise RenderError("GIF exceeds the configured decoded pixel budget")
    return information


def render_group(group: dict[str, Any], manifest: Path, output: Path, settings: Settings,
                 tool_version: str) -> dict[str, Any]:
    stem = output_stem(group)
    gif_path, mp4_path, record_path = (output / (stem + ext) for ext in (".gif", ".mp4", ".json"))
    result: dict[str, Any] = {"id": group["id"], "number": group["number"],
                              "segments": group.get("segments"), "status": "failed", "reused": False}
    published = False
    cleanup_allowed = True
    try:
        source = Path(group["input"])
        if not source.is_absolute():
            source = manifest.parent / source
        source = source.resolve(strict=True)
        result["input"] = str(source)
        if source in (gif_path, mp4_path, record_path):
            cleanup_allowed = False
            raise RenderError("Input must not overwrite an output artifact")
        segments = ordered_segments(group)
        result["segments"] = segments
        expected_duration = sum(end - start for start, end in segments)
        result["requested_duration_seconds"] = expected_duration
        result["input_sha256"] = sha256(source)
        source_info = probe(source)
        result["input_duration_seconds"] = source_info["duration_seconds"]
        for index, (start, end) in enumerate(segments):
            if end > source_info["duration_seconds"] + 0.000001:
                raise RenderError(f"Segment {index} ends at {end:g}s beyond video duration "
                                  f"{source_info['duration_seconds']:g}s; refusing silent truncation")
        # GIF timestamps use centiseconds: 30 fps alternates 3/4-centisecond
        # delays. Do not round to one constant delay (that would become 25 fps).
        fps = str(settings.fps)
        if math.ceil(expected_duration * settings.fps) > settings.max_frames:
            raise RenderError("Requested fps and duration exceed frame budget; increase max-frames or explicitly choose a lower fps")
        duration_tolerance = len(segments) / CLIP_FPS + 0.02
        gif_tolerance = duration_tolerance + 1 / settings.fps + 0.02
        signature = {"renderer_version": RENDERER_VERSION, "ffmpeg_version": tool_version,
                     "id": group["id"], "number": group["number"],
                     "input": str(source),
                     "input_sha256": result["input_sha256"], "segments": segments,
                     "settings": asdict(settings), "clip_fps": CLIP_FPS}
        result["fingerprint"] = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        result["renderer"] = signature
        if record_path.exists():
            try:
                previous = json.loads(record_path.read_text(encoding="utf-8"))
                if previous.get("status") == "ok" and previous.get("fingerprint") == result["fingerprint"]:
                    for kind, path, tolerance in (("gif", gif_path, gif_tolerance),
                                                   ("mp4", mp4_path, duration_tolerance)):
                        if sha256(path) != previous[kind]["sha256"]:
                            raise RenderError(f"Stored {kind} hash changed")
                        validate_artifact(path, kind, expected_duration, tolerance, settings)
                    published = True
                    return {**previous, "reused": True}
            except (OSError, ValueError, KeyError, TypeError):
                pass  # A stale, incomplete, or corrupted result must be regenerated.
        for path in (gif_path, mp4_path, record_path):
            path.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{stem}-", dir=output) as temporary:
            working = Path(temporary)
            width, height = scaled_dimensions(source_info["display_width"], source_info["display_height"],
                                               settings.max_side)
            clip_paths = []
            for index, (start, end) in enumerate(segments):
                clip_path = working / f"clip-{index:04d}.mp4"
                ffmpeg("-ss", f"{start:.9f}", "-i", str(source), "-map", f"0:{source_info['stream_index']}",
                       "-t", f"{end - start:.9f}", "-vf",
                       f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos,setsar=1,fps={CLIP_FPS}",
                       "-an", "-sn", "-dn", "-map_metadata", "-1", "-c:v", "libx264", "-threads", "1",
                       "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                       str(clip_path))
                validate_artifact(clip_path, "mp4", end - start, 1 / CLIP_FPS + 0.02, settings)
                clip_paths.append(clip_path)
            concat_list = working / "clips.txt"
            # Only our ASCII-generated relative names enter the concat grammar.
            concat_list.write_text("".join(f"file '{path.name}'\n" for path in clip_paths), encoding="ascii")
            montage = working / "montage.mp4"
            ffmpeg("-f", "concat", "-safe", "1", "-i", str(concat_list), "-map", "0:v:0",
                   "-c:v", "copy", "-an", "-map_metadata", "-1", "-movflags", "+faststart", str(montage))
            montage_info = validate_artifact(montage, "mp4", expected_duration, duration_tolerance, settings)
            estimated_frames = min(settings.max_frames, math.ceil((expected_duration + duration_tolerance) * settings.fps) + 1)
            gif = working / "animation.gif"
            last_error: Exception | None = None
            for fraction, colors in ((1, 256), (.8, 256), (.65, 192), (.5, 128), (.4, 96), (.3, 64), (.2, 32)):
                attempt_side = max(2, int(settings.max_side * fraction))
                gif_width, gif_height = scaled_dimensions(width, height, attempt_side,
                                                          settings.max_pixels / estimated_frames)
                normalizer = f"fps={fps},scale={gif_width}:{gif_height}:flags=lanczos,setsar=1"
                palette = working / "palette.png"
                ffmpeg("-i", str(montage), "-vf", f"{normalizer},palettegen=max_colors={colors}:stats_mode=full",
                       "-frames:v", "1", str(palette))
                ffmpeg("-i", str(montage), "-i", str(palette), "-filter_complex",
                       f"[0:v]{normalizer}[v];[v][1:v]paletteuse=dither=sierra2_4a:diff_mode=rectangle",
                       "-an", "-loop", "0", "-fps_mode", "passthrough", str(gif))
                try:
                    gif_info = validate_artifact(gif, "gif", expected_duration, gif_tolerance, settings)
                    result["render_settings"] = {"fps": settings.fps, "frame_delay_centiseconds": "variable (centisecond timebase)",
                                                  "width": gif_width, "height": gif_height, "palette_colors": colors}
                    break
                except RenderError as error:
                    last_error = error
            else:
                raise RenderError(f"Could not produce a GIF within the export budgets: {last_error}")
            result["gif"] = artifact_metadata(gif, gif_info)
            result["gif"]["path"] = str(gif_path)
            result["gif"]["loop"] = 0
            result["mp4"] = artifact_metadata(montage, montage_info)
            result["mp4"]["path"] = str(mp4_path)
            os.replace(gif, gif_path)
            os.replace(montage, mp4_path)
            result.update(status="ok", error=None)
            write_json(record_path, result)
            published = True
            return result
    except Exception as error:
        result.update(status="failed", error=str(error))
        result.pop("gif", None)
        result.pop("mp4", None)
        return result
    finally:
        if not published and cleanup_allowed:
            # A failed rerun must never expose a stale GIF as its successful output.
            for path in (gif_path, mp4_path, record_path):
                path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group", action="append", default=[], help="Exact group id or unambiguous number; repeatable")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--max-side", type=int, default=640)
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--max-frames", type=int, default=600)
    parser.add_argument("--max-pixels", type=int, default=UPLOAD_MAX_PIXELS)
    parser.add_argument("--max-bytes", type=int, default=UPLOAD_MAX_BYTES)
    args = parser.parse_args(argv)
    try:
        settings = Settings(args.max_side, args.fps, args.max_frames, args.max_pixels, args.max_bytes)
        if not 1 <= args.jobs <= 8:
            raise RenderError("--jobs must be between 1 and 8")
        for name, value, minimum, ceiling in (("max-side", args.max_side, 2, UPLOAD_MAX_SIDE),
                                               ("max-frames", args.max_frames, 2, UPLOAD_MAX_FRAMES),
                                               ("max-pixels", args.max_pixels, 4, UPLOAD_MAX_PIXELS),
                                               ("max-bytes", args.max_bytes, 1, UPLOAD_MAX_BYTES)):
            if not minimum <= value <= ceiling:
                raise RenderError(f"--{name} must be between {minimum} and {ceiling}")
        if not math.isfinite(args.fps) or not 0 < args.fps <= 30:
            raise RenderError("--fps must be greater than 0 and at most 30")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RenderError("Install ffmpeg and ffprobe before rendering")
        manifest = args.manifest.resolve(strict=True)
        groups = load_groups(manifest, args.group)
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        tool_version = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        results: dict[str, dict[str, Any]] = {}
        document: dict[str, Any] = {"version": 1, "manifest": str(manifest), "settings": asdict(settings), "groups": []}
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {executor.submit(render_group, group, manifest, output, settings, tool_version): group
                       for group in groups}
            for future in as_completed(futures):
                result = future.result()
                results[result["id"]] = result
                document["groups"] = [results[group["id"]] for group in groups if group["id"] in results]
                write_json(output / "results.json", document)
                label = "reused" if result["reused"] else result["status"]
                print(f"{result['number']}: {label}" + (f": {result['error']}" if result.get("error") else ""), file=sys.stderr)
        failures = sum(result["status"] != "ok" for result in results.values())
        print(json.dumps({"results": str(output / "results.json"), "groups": len(results), "failed": failures}))
        return 1 if failures else 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
