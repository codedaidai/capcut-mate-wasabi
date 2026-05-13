import json
from pathlib import Path

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
                        "checks": {"allow_black": False, "allow_static": False, "allow_silence": False},
                    },
                    {
                        "index": 1,
                        "text": "second",
                        "start": 2_000_000,
                        "duration": 2_000_000,
                        "end": 4_000_000,
                        "video_path": str(source_video),
                        "audio_path": str(source_audio),
                        "checks": {"allow_black": False, "allow_static": False, "allow_silence": False},
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
        frame.write_bytes(b"jpg")
        return [frame]

    monkeypatch.setattr("src.wasabi_jianying.verify_export._probe_media", fake_probe)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._sum_detector_durations", fake_detector)
    monkeypatch.setattr("src.wasabi_jianying.verify_export._extract_preview_frames", fake_extract)

    result = verify_export_video(export, manifest)

    assert result.clips_count == 2
    assert result.passed_count == 1
    assert result.failed_count == 1
    assert not result.passed
    assert result.report_path.exists()
    assert result.json_path.exists()

    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert payload["clips"][1]["issues"][0]["code"] == "BLACK_SCREEN"
