"""Export Wasabi fine-cut output to a local Jianying draft."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import src.pyJianYingDraft as draft

from .review import write_timeline_review


SEC = 1_000_000


@dataclass(frozen=True)
class FineCutClip:
    index: int
    video_path: Path
    audio_path: Path
    text: str


@dataclass(frozen=True)
class ExportResult:
    draft_name: str
    draft_path: Path
    manifest_path: Path
    review_path: Path
    clips_count: int
    duration_us: int

    @property
    def duration_seconds(self) -> float:
        return self.duration_us / SEC


def find_jianying_drafts_folder() -> Path | None:
    """Return the first existing local Jianying draft folder we know about."""
    candidates = [
        "~/Movies/JianyingPro Drafts",
        "~/Movies/JianyingPro/Drafts",
        "~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
        "~/Library/Containers/com.lemon.lvpro/Data/Movies/JianyingPro Drafts",
        "~/Documents/JianyingPro Drafts",
        "~/Videos/JianyingPro Drafts",
    ]
    for candidate in candidates:
        path = Path(os.path.expanduser(candidate))
        if path.is_dir():
            return path
    return None


def export_fine_cut_to_draft(
    fine_cut_dir: str | Path,
    *,
    drafts_folder: str | Path | None = None,
    draft_name: str | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    include_subtitles: bool = True,
    allow_replace: bool = True,
) -> ExportResult:
    """Create a Jianying draft from Wasabi fine-cut assets."""
    fine_cut_path = Path(fine_cut_dir).expanduser().resolve()
    if not fine_cut_path.is_dir():
        raise FileNotFoundError(f"Fine-cut directory does not exist: {fine_cut_path}")

    output_root = _resolve_drafts_folder(fine_cut_path, drafts_folder)
    output_root.mkdir(parents=True, exist_ok=True)

    name = draft_name or f"{fine_cut_path.parent.name}_wasabi"
    clips = load_fine_cut_clips(fine_cut_path)

    folder = draft.DraftFolder(str(output_root))
    script = folder.create_draft(name, width, height, fps, allow_replace=allow_replace)

    video_track = "wasabi_video"
    audio_track = "wasabi_tts"
    subtitle_track = "wasabi_subtitles"
    script.add_track(draft.TrackType.video, video_track)
    script.add_track(draft.TrackType.audio, audio_track)
    if include_subtitles:
        script.add_track(draft.TrackType.text, subtitle_track, relative_index=999)

    cursor_us = 0
    manifest_clips: list[dict[str, Any]] = []
    for clip in clips:
        video = draft.VideoMaterial(str(clip.video_path))
        audio = draft.AudioMaterial(str(clip.audio_path))
        duration_us = audio.duration
        if duration_us <= 0:
            raise ValueError(f"Audio has no duration: {clip.audio_path}")

        source_duration_us = min(video.duration, duration_us)
        if source_duration_us <= 0:
            raise ValueError(f"Video has no duration: {clip.video_path}")

        target_range = draft.Timerange(cursor_us, duration_us)
        source_range = draft.Timerange(0, source_duration_us)
        video_segment = draft.VideoSegment(
            video,
            target_range,
            source_timerange=source_range,
            volume=0.0,
        )
        script.add_segment(video_segment, video_track)

        audio_segment = draft.AudioSegment(audio, target_range, volume=1.0)
        script.add_segment(audio_segment, audio_track)

        text_segment_id = None
        if include_subtitles and clip.text:
            text_segment = draft.TextSegment(
                clip.text,
                target_range,
                style=draft.TextStyle(
                    size=7.0,
                    bold=True,
                    color=(1.0, 1.0, 1.0),
                    align=1,
                    auto_wrapping=True,
                ),
                clip_settings=draft.ClipSettings(transform_y=-0.78),
                border=draft.TextBorder(width=35.0),
                shadow=draft.TextShadow(alpha=0.7, distance=4.0),
            )
            script.add_segment(text_segment, subtitle_track)
            text_segment_id = text_segment.segment_id

        subtitle_contract = _build_subtitle_contract(
            clip.text,
            enabled=include_subtitles and bool(clip.text),
            track_name=subtitle_track if include_subtitles else None,
            segment_id=text_segment_id,
        )
        manifest_clips.append(
            {
                "index": clip.index,
                "text": clip.text,
                "start": cursor_us,
                "duration": duration_us,
                "end": cursor_us + duration_us,
                "video_path": str(clip.video_path),
                "audio_path": str(clip.audio_path),
                "source_video_start": 0,
                "source_video_duration": source_duration_us,
                "source_audio_start": 0,
                "source_audio_duration": duration_us,
                "checks": {
                    "allow_black": False,
                    "allow_static": False,
                    "allow_silence": False,
                    "check_source_visual": True,
                    "max_source_frame_difference": 0.18,
                    "max_source_mismatch_ratio": 0.66,
                    "fail_source_frame_difference": 0.35,
                },
                "subtitle": subtitle_contract,
                "video_segment_id": video_segment.segment_id,
                "audio_segment_id": audio_segment.segment_id,
                "text_segment_id": text_segment_id,
            }
        )

        cursor_us += duration_us

    script.save()
    draft_path = output_root / name
    manifest_path = draft_path / "wasabi_manifest.json"
    _write_manifest(
        manifest_path,
        {
            "version": 1,
            "source": "wasabi_fine_cut",
            "draft_name": name,
            "duration": cursor_us,
            "width": width,
            "height": height,
            "fps": fps,
            "tracks": {
                "video": video_track,
                "audio": audio_track,
                "subtitles": subtitle_track if include_subtitles else None,
            },
            "clips": manifest_clips,
        },
    )
    review_path = write_timeline_review(draft_path, manifest_path=manifest_path)
    return ExportResult(
        draft_name=name,
        draft_path=draft_path,
        manifest_path=manifest_path,
        review_path=review_path,
        clips_count=len(clips),
        duration_us=cursor_us,
    )


def load_fine_cut_clips(fine_cut_dir: Path) -> list[FineCutClip]:
    segments_dir = fine_cut_dir / "segments"
    tts_dir = fine_cut_dir / "tts"
    if not segments_dir.is_dir():
        raise FileNotFoundError(f"Missing segments directory: {segments_dir}")
    if not tts_dir.is_dir():
        raise FileNotFoundError(f"Missing tts directory: {tts_dir}")

    videos = _collect_indexed_files(segments_dir, "seg", [".mp4", ".mov", ".m4v"])
    audios = _collect_indexed_files(tts_dir, "sent", [".wav", ".mp3", ".m4a", ".aac"])
    if not videos:
        raise ValueError(f"No segment videos found in {segments_dir}")
    if not audios:
        raise ValueError(f"No TTS audio found in {tts_dir}")

    sentences = _load_sentences(fine_cut_dir)
    indexes = sorted(set(videos).intersection(audios))
    if not indexes:
        raise ValueError("No matching video/audio indexes found between segments and tts")

    return [
        FineCutClip(
            index=index,
            video_path=videos[index],
            audio_path=audios[index],
            text=sentences.get(index, ""),
        )
        for index in indexes
    ]


def _resolve_drafts_folder(fine_cut_dir: Path, drafts_folder: str | Path | None) -> Path:
    if drafts_folder is not None:
        return Path(drafts_folder).expanduser().resolve()

    detected = find_jianying_drafts_folder()
    if detected is not None:
        return detected

    return fine_cut_dir / "jianying_draft"


def _collect_indexed_files(directory: Path, prefix: str, suffixes: Iterable[str]) -> dict[int, Path]:
    suffix_set = {suffix.lower() for suffix in suffixes}
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
    indexed: dict[int, Path] = {}
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in suffix_set:
            continue
        match = pattern.match(path.stem)
        if not match:
            continue
        indexed[int(match.group(1))] = path.resolve()
    return indexed


def _load_sentences(fine_cut_dir: Path) -> dict[int, str]:
    path = fine_cut_dir / "sentences.json"
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, list):
        raise ValueError(f"sentences.json should contain a list: {path}")

    sentences: dict[int, str] = {}
    for fallback_index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        index = _read_index(item, fallback_index)
        text = _read_text(item)
        if text:
            sentences[index] = text
    return sentences


def _read_index(item: dict[str, Any], fallback_index: int) -> int:
    for key in ("index", "_id", "id"):
        value = item.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return fallback_index


def _read_text(item: dict[str, Any]) -> str:
    for key in ("text", "narration", "sentence"):
        value = item.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _build_subtitle_contract(
    text: str,
    *,
    enabled: bool,
    track_name: str | None,
    segment_id: str | None,
) -> dict[str, Any]:
    runs = [{"text": text, "role": "base", "style": "normal"}] if enabled and text else []
    return {
        "enabled": enabled,
        "text": text if enabled else "",
        "track": track_name,
        "segment_id": segment_id,
        "position": {
            "anchor": "bottom_safe",
            "transform_y": -0.78,
            "safe_zone": {
                "x_min": 0.08,
                "x_max": 0.92,
                "y_min": 0.68,
                "y_max": 0.93,
            },
        },
        "style": {
            "preset": "wasabi_default_subtitle",
            "font_size": 7.0,
            "bold": True,
            "color": "#FFFFFF",
            "border_width": 35.0,
            "shadow_alpha": 0.7,
            "shadow_distance": 4.0,
            "auto_wrapping": True,
        },
        "runs": runs,
        "emphasis_runs": [],
        "decorative_text": [],
        "checks": {
            "require_visible": enabled,
            "check_safe_zone": enabled,
            "check_emphasis_visual": False,
            "ocr_required": False,
            "min_text_edge_density": 0.004,
            "min_safe_difference": 0.012,
            "min_difference_lift": 1.25,
            "max_outside_to_safe_ratio": 0.75,
        },
    }


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
