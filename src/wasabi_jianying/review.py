"""Human-readable review layer for Wasabi-generated Jianying drafts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .edit_draft import EffectEditResult, apply_effect_to_clips


ACTION_PATTERN = re.compile(r"^\s*(?:[-*]\s*)?(?:效果|effect)\s*[:：=]\s*(.+?)\s*$", re.IGNORECASE)
CLIP_MARKER_PATTERN = re.compile(r"<!--\s*wasabi-clip\s*:\s*(\d+)\s*-->")
SEC = 1_000_000


@dataclass(frozen=True)
class ReviewAction:
    clip_index: int
    action: str
    value: str


@dataclass(frozen=True)
class ReviewApplyResult:
    review_path: Path
    manifest_path: Path
    actions_count: int
    effect_results: list[EffectEditResult]


def write_timeline_review(
    draft_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    review_path: str | Path | None = None,
) -> Path:
    """Write a Markdown timeline that can be reviewed or annotated by humans."""
    draft_dir = Path(draft_path).expanduser().resolve()
    resolved_manifest = Path(manifest_path).expanduser().resolve() if manifest_path else draft_dir / "wasabi_manifest.json"
    manifest = _load_manifest(resolved_manifest)
    resolved_review = Path(review_path).expanduser().resolve() if review_path else draft_dir / "timeline_review.md"
    resolved_review.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Wasabi Timeline Review",
        "",
        f"Draft: {manifest.get('draft_name', draft_dir.name)}",
        f"Duration: {_format_time(int(manifest.get('duration', 0)))}",
        "",
        "在每个片段的标注区写 `效果：DV界面`，再运行 review apply，就会回写到剪映草稿。",
        "",
    ]

    edits_by_clip = _collect_edits_by_clip(manifest)
    for clip in manifest.get("clips", []):
        index = int(clip["index"])
        start = int(clip["start"])
        duration = int(clip["duration"])
        end = int(clip.get("end", start + duration))
        text = str(clip.get("text", "")).strip()
        lines.extend(
            [
                f"<!-- wasabi-clip:{index} -->",
                f"## Clip {index:03d} | {_format_time(start)} - {_format_time(end)} | {duration / SEC:.2f}s",
                "",
                f"文案：{text}",
                "",
                f"视频：`{clip.get('video_path', '')}`",
                f"音频：`{clip.get('audio_path', '')}`",
                f"视频片段 ID：`{clip.get('video_segment_id', '')}`",
                f"字幕片段 ID：`{clip.get('text_segment_id', '')}`",
                "",
                "已有修改：",
            ]
        )
        edits = edits_by_clip.get(index, [])
        if edits:
            lines.extend(f"- {edit}" for edit in edits)
        else:
            lines.append("- 无")
        lines.extend(
            [
                "",
                "标注：",
                "- 效果：",
                "- 备注：",
                "",
            ]
        )

    resolved_review.write_text("\n".join(lines), encoding="utf-8")
    return resolved_review


def parse_review_actions(review_path: str | Path) -> list[ReviewAction]:
    """Parse supported action lines from a timeline review Markdown file."""
    path = Path(review_path).expanduser().resolve()
    current_clip: int | None = None
    actions: list[ReviewAction] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        marker_match = CLIP_MARKER_PATTERN.search(line)
        if marker_match:
            current_clip = int(marker_match.group(1))
            continue
        if current_clip is None:
            continue

        effect_match = ACTION_PATTERN.match(line)
        if not effect_match:
            continue
        value = effect_match.group(1).strip()
        if not value:
            continue
        actions.append(ReviewAction(clip_index=current_clip, action="effect", value=value))

    return actions


def apply_review_actions(
    draft_path: str | Path,
    *,
    review_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> ReviewApplyResult:
    """Apply supported actions from a timeline review file to the draft."""
    draft_dir = Path(draft_path).expanduser().resolve()
    resolved_review = Path(review_path).expanduser().resolve() if review_path else draft_dir / "timeline_review.md"
    resolved_manifest = Path(manifest_path).expanduser().resolve() if manifest_path else draft_dir / "wasabi_manifest.json"
    actions = parse_review_actions(resolved_review)

    effect_groups: dict[str, list[int]] = {}
    for action in actions:
        if action.action == "effect":
            effect_groups.setdefault(action.value, []).append(action.clip_index)

    effect_results = [
        apply_effect_to_clips(
            draft_dir,
            effect_name=effect_name,
            clip_indexes=clip_indexes,
            manifest_path=resolved_manifest,
        )
        for effect_name, clip_indexes in effect_groups.items()
    ]

    return ReviewApplyResult(
        review_path=resolved_review,
        manifest_path=resolved_manifest,
        actions_count=len(actions),
        effect_results=effect_results,
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing Wasabi manifest: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Manifest should be a JSON object: {path}")
    return data


def _format_time(microseconds: int) -> str:
    milliseconds = microseconds // 1000
    seconds = milliseconds // 1000
    ms = milliseconds % 1000
    minutes = seconds // 60
    sec = seconds % 60
    hours = minutes // 60
    minute = minutes % 60
    if hours:
        return f"{hours:02d}:{minute:02d}:{sec:02d}.{ms:03d}"
    return f"{minute:02d}:{sec:02d}.{ms:03d}"


def _collect_edits_by_clip(manifest: dict[str, Any]) -> dict[int, list[str]]:
    edits_by_clip: dict[int, list[str]] = {}
    for edit in manifest.get("edits", []):
        if not isinstance(edit, dict):
            continue
        if edit.get("type") != "effect":
            continue
        effect_name = edit.get("effect_name", "")
        track_name = edit.get("track_name", "")
        for clip_index in edit.get("clip_indexes", []):
            edits_by_clip.setdefault(int(clip_index), []).append(f"效果：{effect_name}（{track_name}）")
    return edits_by_clip
