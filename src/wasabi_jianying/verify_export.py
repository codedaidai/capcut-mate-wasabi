"""Verify an exported Wasabi video against its Jianying manifest."""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SEC = 1_000_000
DEFAULT_DURATION_TOLERANCE = 1.0
BLACK_FAIL_RATIO = 0.80
BLACK_WARN_RATIO = 0.25
FREEZE_FAIL_RATIO = 0.80
FREEZE_WARN_RATIO = 0.50
SILENCE_FAIL_RATIO = 0.80
SILENCE_WARN_RATIO = 0.25

BLACK_RE = re.compile(r"black_duration:([0-9.]+)")
FREEZE_RE = re.compile(r"freeze_duration:([0-9.]+)")
SILENCE_RE = re.compile(r"silence_duration:([0-9.]+)")


@dataclass(frozen=True)
class MediaProbe:
    path: Path
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    has_video: bool
    has_audio: bool


@dataclass(frozen=True)
class QCIssue:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class ClipQCResult:
    index: int
    start: float
    duration: float
    end: float
    text: str
    status: str
    issues: list[QCIssue]
    frames: list[Path]
    video_path: str
    audio_path: str


@dataclass(frozen=True)
class ExportVerificationResult:
    export_path: Path
    manifest_path: Path
    report_path: Path
    json_path: Path
    clips_count: int
    passed_count: int
    warning_count: int
    failed_count: int
    project_issues: list[QCIssue]
    clip_results: list[ClipQCResult]

    @property
    def passed(self) -> bool:
        return self.failed_count == 0 and not any(issue.severity == "fail" for issue in self.project_issues)


def verify_export_video(
    export_path: str | Path,
    manifest_path: str | Path,
    *,
    report_path: str | Path | None = None,
    json_path: str | Path | None = None,
    frames_dir: str | Path | None = None,
    duration_tolerance: float = DEFAULT_DURATION_TOLERANCE,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> ExportVerificationResult:
    """Run deterministic QC checks on an exported MP4."""
    resolved_export = Path(export_path).expanduser().resolve()
    resolved_manifest = Path(manifest_path).expanduser().resolve()
    if not resolved_export.exists():
        raise FileNotFoundError(f"Missing exported video: {resolved_export}")
    if not resolved_manifest.exists():
        raise FileNotFoundError(f"Missing Wasabi manifest: {resolved_manifest}")

    manifest = _load_manifest(resolved_manifest)
    resolved_report = Path(report_path).expanduser().resolve() if report_path else resolved_manifest.parent / "qc_report.html"
    resolved_json = Path(json_path).expanduser().resolve() if json_path else resolved_manifest.parent / "qc_report.json"
    resolved_frames = Path(frames_dir).expanduser().resolve() if frames_dir else resolved_manifest.parent / "qc_frames"
    resolved_frames.mkdir(parents=True, exist_ok=True)

    export_probe = _probe_media(resolved_export, ffprobe)
    project_issues = _check_project(manifest, export_probe, duration_tolerance)

    clip_results = [
        _verify_clip(
            clip,
            export_path=resolved_export,
            export_probe=export_probe,
            frames_dir=resolved_frames,
            duration_tolerance=duration_tolerance,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
        for clip in manifest.get("clips", [])
    ]

    passed_count = sum(1 for clip in clip_results if clip.status == "pass")
    warning_count = sum(1 for clip in clip_results if clip.status == "warn")
    failed_count = sum(1 for clip in clip_results if clip.status == "fail")
    result = ExportVerificationResult(
        export_path=resolved_export,
        manifest_path=resolved_manifest,
        report_path=resolved_report,
        json_path=resolved_json,
        clips_count=len(clip_results),
        passed_count=passed_count,
        warning_count=warning_count,
        failed_count=failed_count,
        project_issues=project_issues,
        clip_results=clip_results,
    )

    _write_json_report(resolved_json, result, export_probe, manifest)
    _write_html_report(resolved_report, result, export_probe, manifest)
    return result


def _verify_clip(
    clip: dict[str, Any],
    *,
    export_path: Path,
    export_probe: MediaProbe,
    frames_dir: Path,
    duration_tolerance: float,
    ffmpeg: str,
    ffprobe: str,
) -> ClipQCResult:
    index = int(clip["index"])
    start = _us_to_seconds(int(clip.get("start", 0)))
    duration = _us_to_seconds(int(clip.get("duration", 0)))
    end = _us_to_seconds(int(clip.get("end", clip.get("start", 0) + clip.get("duration", 0))))
    text = str(clip.get("text", "")).strip()
    video_path = str(clip.get("video_path", ""))
    audio_path = str(clip.get("audio_path", ""))
    checks = clip.get("checks") if isinstance(clip.get("checks"), dict) else {}

    issues: list[QCIssue] = []
    if duration <= 0:
        issues.append(QCIssue("fail", "CLIP_DURATION_EMPTY", "片段时长为 0。"))
    if end > export_probe.duration + duration_tolerance:
        issues.append(QCIssue("fail", "CLIP_OUTSIDE_EXPORT", "片段结束时间超过导出视频总时长。"))

    _check_source_media(issues, video_path, audio_path, duration, ffprobe)

    frames = _extract_preview_frames(export_path, index, start, duration, frames_dir, ffmpeg) if export_probe.has_video else []
    if export_probe.has_video and duration > 0:
        black_duration = _sum_detector_durations(
            export_path,
            start,
            duration,
            "blackdetect=d=0.10:pix_th=0.10:pic_th=0.98",
            BLACK_RE,
            ffmpeg,
            is_audio=False,
        )
        black_ratio = _ratio(black_duration, duration)
        if black_ratio >= BLACK_FAIL_RATIO and not checks.get("allow_black", False):
            issues.append(QCIssue("fail", "BLACK_SCREEN", f"片段黑屏占比 {black_ratio:.0%}。"))
        elif black_ratio >= BLACK_WARN_RATIO and not checks.get("allow_black", False):
            issues.append(QCIssue("warn", "BLACK_SCREEN_PARTIAL", f"片段存在较多黑屏，约 {black_ratio:.0%}。"))

        if duration >= 1.2:
            freeze_duration = _sum_detector_durations(
                export_path,
                start,
                duration,
                "freezedetect=n=-60dB:d=0.8",
                FREEZE_RE,
                ffmpeg,
                is_audio=False,
            )
            freeze_ratio = _ratio(freeze_duration, duration)
            if freeze_ratio >= FREEZE_FAIL_RATIO and not checks.get("allow_static", False):
                issues.append(QCIssue("fail", "STATIC_FRAME", f"片段疑似静帧卡住，静帧占比 {freeze_ratio:.0%}。"))
            elif freeze_ratio >= FREEZE_WARN_RATIO and not checks.get("allow_static", False):
                issues.append(QCIssue("warn", "STATIC_FRAME_PARTIAL", f"片段静帧偏多，约 {freeze_ratio:.0%}。"))

    if export_probe.has_audio and duration > 0:
        silence_duration = _sum_detector_durations(
            export_path,
            start,
            duration,
            "silencedetect=n=-45dB:d=0.4",
            SILENCE_RE,
            ffmpeg,
            is_audio=True,
        )
        silence_ratio = _ratio(silence_duration, duration)
        if silence_ratio >= SILENCE_FAIL_RATIO and not checks.get("allow_silence", False):
            issues.append(QCIssue("fail", "AUDIO_SILENCE", f"片段音频大面积静音，静音占比 {silence_ratio:.0%}。"))
        elif silence_ratio >= SILENCE_WARN_RATIO and not checks.get("allow_silence", False):
            issues.append(QCIssue("warn", "AUDIO_SILENCE_PARTIAL", f"片段存在较多静音，约 {silence_ratio:.0%}。"))

    status = _status_from_issues(issues)
    return ClipQCResult(
        index=index,
        start=start,
        duration=duration,
        end=end,
        text=text,
        status=status,
        issues=issues,
        frames=frames,
        video_path=video_path,
        audio_path=audio_path,
    )


def _check_project(manifest: dict[str, Any], export_probe: MediaProbe, tolerance: float) -> list[QCIssue]:
    issues: list[QCIssue] = []
    expected_duration = _us_to_seconds(int(manifest.get("duration", 0)))
    if expected_duration > 0 and abs(export_probe.duration - expected_duration) > tolerance:
        issues.append(
            QCIssue(
                "warn",
                "EXPORT_DURATION_MISMATCH",
                f"导出时长 {export_probe.duration:.2f}s，manifest 预期 {expected_duration:.2f}s。",
            )
        )
    expected_width = manifest.get("width")
    expected_height = manifest.get("height")
    if expected_width and expected_height and export_probe.width and export_probe.height:
        if int(expected_width) != export_probe.width or int(expected_height) != export_probe.height:
            issues.append(
                QCIssue(
                    "warn",
                    "EXPORT_RESOLUTION_MISMATCH",
                    f"导出分辨率 {export_probe.width}x{export_probe.height}，manifest 预期 {expected_width}x{expected_height}。",
                )
            )
    if not export_probe.has_video:
        issues.append(QCIssue("fail", "EXPORT_VIDEO_MISSING", "导出文件没有视频轨。"))
    if not export_probe.has_audio:
        issues.append(QCIssue("fail", "EXPORT_AUDIO_MISSING", "导出文件没有音频轨。"))
    return issues


def _check_source_media(
    issues: list[QCIssue],
    video_path: str,
    audio_path: str,
    expected_duration: float,
    ffprobe: str,
) -> None:
    if not video_path or not Path(video_path).exists():
        issues.append(QCIssue("fail", "SOURCE_VIDEO_MISSING", f"源视频不存在：{video_path}"))
    else:
        source_video = _probe_media(Path(video_path), ffprobe)
        if source_video.duration <= 0:
            issues.append(QCIssue("warn", "SOURCE_VIDEO_DURATION_EMPTY", "源视频时长无法识别。"))
        elif expected_duration > 0 and source_video.duration + DEFAULT_DURATION_TOLERANCE < expected_duration:
            issues.append(QCIssue("warn", "SOURCE_VIDEO_TOO_SHORT", "源视频时长异常偏短。"))

    if not audio_path or not Path(audio_path).exists():
        issues.append(QCIssue("fail", "SOURCE_AUDIO_MISSING", f"源音频不存在：{audio_path}"))
    else:
        source_audio = _probe_media(Path(audio_path), ffprobe)
        if expected_duration > 0 and abs(source_audio.duration - expected_duration) > DEFAULT_DURATION_TOLERANCE:
            issues.append(
                QCIssue(
                    "warn",
                    "SOURCE_AUDIO_DURATION_MISMATCH",
                    f"源音频 {source_audio.duration:.2f}s，片段预期 {expected_duration:.2f}s。",
                )
            )


def _extract_preview_frames(
    export_path: Path,
    clip_index: int,
    start: float,
    duration: float,
    frames_dir: Path,
    ffmpeg: str,
) -> list[Path]:
    if duration <= 0:
        return []
    offsets = [
        min(0.2, duration * 0.25),
        duration * 0.5,
        max(duration - min(0.2, duration * 0.25), 0.0),
    ]
    frames: list[Path] = []
    seen: set[int] = set()
    for label, offset in zip(("start", "mid", "end"), offsets):
        timestamp = max(start + offset, 0.0)
        marker = int(round(timestamp * 1000))
        if marker in seen:
            continue
        seen.add(marker)
        output = frames_dir / f"clip_{clip_index:03d}_{label}.jpg"
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(export_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=480:-1",
            "-y",
            str(output),
        ]
        _run_command(command)
        if output.exists():
            frames.append(output)
    return frames


def _sum_detector_durations(
    export_path: Path,
    start: float,
    duration: float,
    filter_expression: str,
    pattern: re.Pattern[str],
    ffmpeg: str,
    *,
    is_audio: bool,
) -> float:
    filter_flag = "-af" if is_audio else "-vf"
    disabled_stream = "-vn" if is_audio else "-an"
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(export_path),
        filter_flag,
        filter_expression,
        disabled_stream,
        "-f",
        "null",
        "-",
    ]
    output = _run_command(command)
    return sum(float(value) for value in pattern.findall(output))


def _probe_media(path: Path, ffprobe: str) -> MediaProbe:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    raw = _run_command(command)
    data = json.loads(raw)
    streams = data.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if duration <= 0:
        duration = _stream_duration(video_stream) or _stream_duration(audio_stream) or 0.0
    return MediaProbe(
        path=path,
        duration=duration,
        width=int(video_stream["width"]) if video_stream and video_stream.get("width") else None,
        height=int(video_stream["height"]) if video_stream and video_stream.get("height") else None,
        fps=_read_fps(video_stream),
        has_video=video_stream is not None,
        has_audio=audio_stream is not None,
    )


def _run_command(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing required media tool: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        raise RuntimeError(f"Media command failed: {' '.join(command)}\n{output}") from exc
    return (completed.stdout or "") + (completed.stderr or "")


def _load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest should be a JSON object: {path}")
    clips = data.get("clips")
    if not isinstance(clips, list):
        raise ValueError(f"Manifest should contain a clips list: {path}")
    return data


def _write_json_report(path: Path, result: ExportVerificationResult, export_probe: MediaProbe, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "passed": result.passed,
        "export_path": str(result.export_path),
        "manifest_path": str(result.manifest_path),
        "expected_duration": _us_to_seconds(int(manifest.get("duration", 0))),
        "export_duration": export_probe.duration,
        "resolution": {"width": export_probe.width, "height": export_probe.height},
        "summary": {
            "clips": result.clips_count,
            "passed": result.passed_count,
            "warnings": result.warning_count,
            "failed": result.failed_count,
        },
        "project_issues": [_issue_to_dict(issue) for issue in result.project_issues],
        "clips": [_clip_to_dict(clip, result.report_path.parent) for clip in result.clip_results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_html_report(path: Path, result: ExportVerificationResult, export_probe: MediaProbe, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(_render_clip_row(clip, path.parent) for clip in result.clip_results)
    project_issues = "".join(f"<li>{_render_issue(issue)}</li>" for issue in result.project_issues) or "<li>无</li>"
    expected_duration = _us_to_seconds(int(manifest.get("duration", 0)))
    status_class = "pass" if result.passed else "fail"
    status_text = "通过" if result.passed else "需要处理"
    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Wasabi QC Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; color: #1f2328; }}
    h1 {{ margin-bottom: 6px; }}
    .summary {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 18px 0; }}
    .metric {{ border: 1px solid #d0d7de; border-radius: 6px; padding: 10px 12px; min-width: 120px; }}
    .label {{ color: #57606a; font-size: 12px; }}
    .value {{ font-size: 20px; font-weight: 700; }}
    .pass {{ color: #1a7f37; }}
    .warn {{ color: #9a6700; }}
    .fail {{ color: #cf222e; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 18px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px; vertical-align: top; }}
    th {{ background: #f6f8fa; text-align: left; }}
    img {{ width: 150px; margin-right: 6px; border: 1px solid #d0d7de; }}
    code {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    .text {{ max-width: 360px; }}
  </style>
</head>
<body>
  <h1>Wasabi QC Report</h1>
  <p class="{status_class}">状态：{status_text}</p>
  <div class="summary">
    <div class="metric"><div class="label">片段</div><div class="value">{result.clips_count}</div></div>
    <div class="metric"><div class="label">通过</div><div class="value pass">{result.passed_count}</div></div>
    <div class="metric"><div class="label">警告</div><div class="value warn">{result.warning_count}</div></div>
    <div class="metric"><div class="label">失败</div><div class="value fail">{result.failed_count}</div></div>
    <div class="metric"><div class="label">导出时长</div><div class="value">{export_probe.duration:.2f}s</div></div>
    <div class="metric"><div class="label">预期时长</div><div class="value">{expected_duration:.2f}s</div></div>
  </div>
  <p>导出文件：<code>{html.escape(str(result.export_path))}</code></p>
  <p>Manifest：<code>{html.escape(str(result.manifest_path))}</code></p>
  <h2>项目问题</h2>
  <ul>{project_issues}</ul>
  <h2>逐句检查</h2>
  <table>
    <thead>
      <tr>
        <th>Clip</th>
        <th>时间</th>
        <th>状态</th>
        <th>文案</th>
        <th>截图</th>
        <th>问题</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""
    path.write_text(content, encoding="utf-8")


def _render_clip_row(clip: ClipQCResult, base_dir: Path) -> str:
    issues = "".join(f"<li>{_render_issue(issue)}</li>" for issue in clip.issues) or "<li>无</li>"
    frames = " ".join(
        f'<img src="{html.escape(_relative_path(frame, base_dir))}" alt="clip {clip.index}">'
        for frame in clip.frames
    )
    return f"""<tr>
  <td>{clip.index:03d}</td>
  <td>{_format_seconds(clip.start)} - {_format_seconds(clip.end)}<br>{clip.duration:.2f}s</td>
  <td class="{clip.status}">{_status_label(clip.status)}</td>
  <td class="text">{html.escape(clip.text)}</td>
  <td>{frames}</td>
  <td><ul>{issues}</ul></td>
</tr>"""


def _render_issue(issue: QCIssue) -> str:
    return f'<span class="{issue.severity}">[{html.escape(issue.code)}]</span> {html.escape(issue.message)}'


def _clip_to_dict(clip: ClipQCResult, base_dir: Path) -> dict[str, Any]:
    return {
        "index": clip.index,
        "start": clip.start,
        "duration": clip.duration,
        "end": clip.end,
        "text": clip.text,
        "status": clip.status,
        "video_path": clip.video_path,
        "audio_path": clip.audio_path,
        "frames": [_relative_path(frame, base_dir) for frame in clip.frames],
        "issues": [_issue_to_dict(issue) for issue in clip.issues],
    }


def _issue_to_dict(issue: QCIssue) -> dict[str, str]:
    return {"severity": issue.severity, "code": issue.code, "message": issue.message}


def _status_from_issues(issues: list[QCIssue]) -> str:
    if any(issue.severity == "fail" for issue in issues):
        return "fail"
    if any(issue.severity == "warn" for issue in issues):
        return "warn"
    return "pass"


def _status_label(status: str) -> str:
    return {"pass": "通过", "warn": "警告", "fail": "失败"}.get(status, status)


def _ratio(value: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return min(value / total, 1.0)


def _read_fps(stream: dict[str, Any] | None) -> float | None:
    if not stream:
        return None
    raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    if not raw or raw == "0/0":
        return None
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            return None
        return float(numerator) / denominator_value
    return float(raw)


def _stream_duration(stream: dict[str, Any] | None) -> float | None:
    if not stream or not stream.get("duration"):
        return None
    return float(stream["duration"])


def _us_to_seconds(value: int) -> float:
    return value / SEC


def _format_seconds(value: float) -> str:
    minutes = int(value // 60)
    seconds = value - minutes * 60
    return f"{minutes:02d}:{seconds:06.3f}"


def _relative_path(path: Path, base_dir: Path) -> str:
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify an exported Wasabi video against wasabi_manifest.json")
    parser.add_argument("export_path", help="Path to exported MP4")
    parser.add_argument("--manifest", required=True, help="Path to wasabi_manifest.json")
    parser.add_argument("--report", help="Path to qc_report.html")
    parser.add_argument("--json", help="Path to qc_report.json")
    parser.add_argument("--frames-dir", help="Directory for extracted QC frames")
    parser.add_argument("--duration-tolerance", type=float, default=DEFAULT_DURATION_TOLERANCE)
    args = parser.parse_args()

    result = verify_export_video(
        args.export_path,
        args.manifest,
        report_path=args.report,
        json_path=args.json,
        frames_dir=args.frames_dir,
        duration_tolerance=args.duration_tolerance,
    )

    print("Export QC finished")
    print(f"Status: {'passed' if result.passed else 'needs attention'}")
    print(f"Report: {result.report_path}")
    print(f"JSON: {result.json_path}")
    print(f"Clips: {result.clips_count}")
    print(f"Passed: {result.passed_count}")
    print(f"Warnings: {result.warning_count}")
    print(f"Failed: {result.failed_count}")


if __name__ == "__main__":
    main()
