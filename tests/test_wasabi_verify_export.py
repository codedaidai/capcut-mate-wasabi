import json
from pathlib import Path

import imageio.v3 as iio
import numpy as np

from src.wasabi_jianying.verify_export import BLACK_RE, SILENCE_RE, MediaProbe, verify_export_video


def test_detector_duration_patterns_sum_multiple_events():
    black_output = "black_start:0 black_end:0.6 black_duration:0.6 black_start:1 black_end:1.4 black_duration:0.4"
    silence_output = "silence_start:0 silence_end:1.2 silence_duration:1.2"

    assert sum(float(value) for value in BLACK_RE.findall(black_output)) == 1.0
    assert sum(float(value) for value in SILENCE_RE.findall(silence_output)) == 1.2


def test_verify_export_video_writes_reports_and_flags_black_clip(tmp_path, monkeypatch):
    export = tmp_path / "export.mp4"
    export.write_bytes(b"mp4")
    source_video = tmp_path / "seg_00.mp4"
    source_video.write_bytes(b"video")
    source_audio = tmp_path / "sent_00.wav"
    source_audio.write_bytes(b"audio")
    manifest = tmp_path / "wasabi_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "draft_name": "demo",
                "duration": 4_000_000,
                "width": 1920,
                "height": 1080,
                "clips": [
                    {
                        "index": 0,
                        "text": "first",
                        "start": 0,
                        "duration": 2_000_000,
                        "end": 2_000_000,
                        "video_path": str(source_video),
                        "audio_path": str(source_audio),
                        "checks": {
                            "allow_black": False,
                            "allow_static": False,
                            "allow_silence": False,
                            "check_source_visual": False,
                        },
                    },
                    {
                        "index": 1,
                        "text": "second",
                        "start": 2_000_000,
                        "duration": 2_000_000,
                        "end": 4_000_000,
                        "video_path": str(source_video),
                        "audio_path": str(source_audio),
                        "checks": {
                            "allow_black": False,
                            "allow_static": False,
                            "allow_silence": False,
                            "check_source_visual": False,
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_probe(path: Path, ffprobe: str) -> MediaProbe:
        if path == export:
            return MediaProbe(path=path, duration=4.0, width=1920, height=1080, fps=30.0, has_video=True, has_audio=True)
        return MediaProbe(path=path, duration=2.0, width=1920, height=1080, fps=30.0, has_video=True, has_audio=True)

    def fake_detector(export_path, start, duration, filter_expression, pattern, ffmpeg, *, is_audio):
        if "blackdetect" in filter_expression and start >= 2.0:
            return 2.0
        return 0.0

    def fake_extract(export_path, clip_index, start, duration, frames_dir, ffmpeg):
        frame = frames_dir / f"clip_{clip_index:03d}_mid.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        _write_frame_with_subtitle_activity(frame)
        return [frame]

    def fake_reference(source_path, clip_index, source_start, duration, frames_dir, ffmpeg):
        frame = frames_dir / f"clip_{clip_index:03d}_source_mid.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        iio.imwrite(frame, np.zeros((120, 160, 3), dtype=np.uint8))
        return [frame]

    monkeypatch.setattr("src.wasabi_jianying.verify_export._probe_media", fake_probe)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._sum_detector_durations", fake_detector)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_preview_frames", fake_extract)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_reference_frames", fake_reference)

    result = verify_export_video(export, manifest)

    assert result.clips_count == 2
    assert result.passed_count == 1
    assert result.failed_count == 1
    assert not result.passed
    assert result.report_path.exists()
    assert result.json_path.exists()

    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert payload["clips"][1]["issues"][0]["code"] == "BLACK_SCREEN"
    assert payload["clips"][0]["subtitle"]["checks"]["ocr_required"] is False


def test_verify_export_warns_when_subtitle_contract_requires_ocr(tmp_path, monkeypatch):
    export = tmp_path / "export.mp4"
    export.write_bytes(b"mp4")
    source_video = tmp_path / "seg_00.mp4"
    source_video.write_bytes(b"video")
    source_audio = tmp_path / "sent_00.wav"
    source_audio.write_bytes(b"audio")
    manifest = tmp_path / "wasabi_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "draft_name": "demo",
                "duration": 2_000_000,
                "width": 1920,
                "height": 1080,
                "clips": [
                    {
                        "index": 0,
                        "text": "花字字幕",
                        "start": 0,
                        "duration": 2_000_000,
                        "end": 2_000_000,
                        "video_path": str(source_video),
                        "audio_path": str(source_audio),
                        "checks": {
                            "allow_black": False,
                            "allow_static": False,
                            "allow_silence": False,
                            "check_source_visual": False,
                        },
                        "subtitle": {
                            "enabled": True,
                            "text": "花字字幕",
                            "segment_id": "text-1",
                            "runs": [{"text": "花字字幕", "role": "base", "style": "normal"}],
                            "emphasis_runs": [{"text": "花字", "style": "emphasis_red_pop"}],
                            "decorative_text": [{"text": "字幕", "style": "sticker_pop"}],
                            "checks": {"ocr_required": True},
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_probe(path: Path, ffprobe: str) -> MediaProbe:
        return MediaProbe(path=path, duration=2.0, width=1920, height=1080, fps=30.0, has_video=True, has_audio=True)

    monkeypatch.setattr("src.wasabi_jianying.verify_export._probe_media", fake_probe)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._sum_detector_durations", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_preview_frames", lambda *args, **kwargs: [])
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_reference_frames", lambda *args, **kwargs: [])

    result = verify_export_video(export, manifest)

    assert result.warning_count == 1
    assert result.clip_results[0].issues[0].code == "SUBTITLE_OCR_REQUIRED"


def test_verify_export_flags_visual_source_mismatch(tmp_path, monkeypatch):
    export = tmp_path / "export.mp4"
    export.write_bytes(b"mp4")
    source_video = tmp_path / "seg_00.mp4"
    source_video.write_bytes(b"video")
    source_audio = tmp_path / "sent_00.wav"
    source_audio.write_bytes(b"audio")
    manifest = tmp_path / "wasabi_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "draft_name": "demo",
                "duration": 2_000_000,
                "width": 1920,
                "height": 1080,
                "clips": [
                    {
                        "index": 0,
                        "text": "",
                        "start": 0,
                        "duration": 2_000_000,
                        "end": 2_000_000,
                        "video_path": str(source_video),
                        "audio_path": str(source_audio),
                        "checks": {
                            "allow_black": False,
                            "allow_static": False,
                            "allow_silence": False,
                            "check_source_visual": True,
                            "max_source_frame_difference": 0.18,
                            "max_source_mismatch_ratio": 0.66,
                            "fail_source_frame_difference": 0.35,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_probe(path: Path, ffprobe: str) -> MediaProbe:
        return MediaProbe(path=path, duration=2.0, width=1920, height=1080, fps=30.0, has_video=True, has_audio=True)

    def fake_extract(export_path, clip_index, start, duration, frames_dir, ffmpeg):
        frame = frames_dir / "wrong.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        iio.imwrite(frame, np.full((120, 160, 3), 255, dtype=np.uint8))
        return [frame]

    def fake_reference(source_path, clip_index, source_start, duration, frames_dir, ffmpeg):
        frame = frames_dir / "reference.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        iio.imwrite(frame, np.zeros((120, 160, 3), dtype=np.uint8))
        return [frame]

    monkeypatch.setattr("src.wasabi_jianying.verify_export._probe_media", fake_probe)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._sum_detector_durations", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_preview_frames", fake_extract)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_reference_frames", fake_reference)

    result = verify_export_video(export, manifest)

    assert result.failed_count == 1
    assert result.clip_results[0].issues[0].code == "VISUAL_SOURCE_MISMATCH"
    assert result.clip_results[0].source_visual["matched"] is False


def test_verify_export_warns_when_subtitle_visual_activity_is_low(tmp_path, monkeypatch):
    export = tmp_path / "export.mp4"
    export.write_bytes(b"mp4")
    source_video = tmp_path / "seg_00.mp4"
    source_video.write_bytes(b"video")
    source_audio = tmp_path / "sent_00.wav"
    source_audio.write_bytes(b"audio")
    manifest = tmp_path / "wasabi_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "draft_name": "demo",
                "duration": 2_000_000,
                "width": 1920,
                "height": 1080,
                "clips": [
                    {
                        "index": 0,
                        "text": "字幕",
                        "start": 0,
                        "duration": 2_000_000,
                        "end": 2_000_000,
                        "video_path": str(source_video),
                        "audio_path": str(source_audio),
                        "checks": {"allow_black": False, "allow_static": False, "allow_silence": False},
                        "subtitle": {
                            "enabled": True,
                            "text": "字幕",
                            "segment_id": "text-1",
                            "position": {
                                "safe_zone": {"x_min": 0.08, "x_max": 0.92, "y_min": 0.68, "y_max": 0.93}
                            },
                            "runs": [{"text": "字幕", "role": "base", "style": "normal"}],
                            "checks": {
                                "require_visible": True,
                                "check_safe_zone": True,
                                "ocr_required": False,
                                "min_text_edge_density": 0.004,
                            },
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_probe(path: Path, ffprobe: str) -> MediaProbe:
        return MediaProbe(path=path, duration=2.0, width=1920, height=1080, fps=30.0, has_video=True, has_audio=True)

    def fake_extract(export_path, clip_index, start, duration, frames_dir, ffmpeg):
        frame = frames_dir / "blank.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        iio.imwrite(frame, np.zeros((120, 160, 3), dtype=np.uint8))
        return [frame]

    def fake_reference(source_path, clip_index, source_start, duration, frames_dir, ffmpeg):
        frame = frames_dir / "reference_blank.jpg"
        frame.parent.mkdir(parents=True, exist_ok=True)
        iio.imwrite(frame, np.zeros((120, 160, 3), dtype=np.uint8))
        return [frame]

    monkeypatch.setattr("src.wasabi_jianying.verify_export._probe_media", fake_probe)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._sum_detector_durations", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_preview_frames", fake_extract)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_reference_frames", fake_reference)

    result = verify_export_video(export, manifest)

    assert result.warning_count == 1
    assert result.clip_results[0].issues[0].code == "SUBTITLE_VISIBILITY_LOW"
    assert result.clip_results[0].subtitle_visual["visible"] is False


def _write_frame_with_subtitle_activity(path: Path) -> None:
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    image[88:94, 32:128] = 255
    image[98:104, 48:112] = 255
    iio.imwrite(path, image)
