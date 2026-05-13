"""Local edit helpers for Wasabi-generated Jianying drafts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import src.pyJianYingDraft as draft
from src.pyJianYingDraft.metadata import VideoCharacterEffectType, VideoSceneEffectType


@dataclass(frozen=True)
class EffectEditResult:
    draft_path: Path
    manifest_path: Path
    track_name: str
    clips_count: int
    segment_ids: list[str]
    effect_ids: list[str]


def apply_effect_to_clips(
    draft_path: str | Path,
    *,
    effect_name: str,
    clip_indexes: Iterable[int],
    manifest_path: str | Path | None = None,
    track_name: str | None = None,
) -> EffectEditResult:
    """Apply a Jianying video effect to Wasabi clip indexes."""
    draft_dir = Path(draft_path).expanduser().resolve()
    if not draft_dir.is_dir():
        raise FileNotFoundError(f"Draft directory does not exist: {draft_dir}")

    resolved_manifest = Path(manifest_path).expanduser().resolve() if manifest_path else draft_dir / "wasabi_manifest.json"
    manifest = _load_manifest(resolved_manifest)
    selected_indexes = sorted(set(int(index) for index in clip_indexes))
    if not selected_indexes:
        raise ValueError("At least one clip index is required")

    clips_by_index = {int(clip["index"]): clip for clip in manifest.get("clips", [])}
    missing_indexes = [index for index in selected_indexes if index not in clips_by_index]
    if missing_indexes:
        raise ValueError(f"Clip indexes are not in manifest: {missing_indexes}")

    effect_type = _find_effect_type_by_name(effect_name)
    if effect_type is None:
        raise ValueError(f"Unknown Jianying effect: {effect_name}")

    script_path = draft_dir / "draft_content.json"
    script = draft.ScriptFile.load_template(str(script_path))

    edit_index = len(manifest.get("edits", [])) + 1
    effect_track = track_name or f"wasabi_effects_{edit_index:02d}"
    script.add_track(draft.TrackType.effect, effect_track)

    segment_ids: list[str] = []
    effect_ids: list[str] = []
    for index in selected_indexes:
        clip = clips_by_index[index]
        timerange = draft.Timerange(int(clip["start"]), int(clip["duration"]))
        effect_segment = draft.EffectSegment(effect_type=effect_type, target_timerange=timerange)
        script.add_segment(effect_segment, effect_track)
        segment_ids.append(effect_segment.segment_id)
        effect_ids.append(effect_segment.effect_inst.global_id)

    script.save()

    edit_record = {
        "type": "effect",
        "effect_name": effect_name,
        "track_name": effect_track,
        "clip_indexes": selected_indexes,
        "segment_ids": segment_ids,
        "effect_ids": effect_ids,
    }
    manifest.setdefault("edits", []).append(edit_record)
    _write_manifest(resolved_manifest, manifest)

    return EffectEditResult(
        draft_path=draft_dir,
        manifest_path=resolved_manifest,
        track_name=effect_track,
        clips_count=len(selected_indexes),
        segment_ids=segment_ids,
        effect_ids=effect_ids,
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing Wasabi manifest: {path}")
    with path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    if not isinstance(manifest, dict):
        raise ValueError(f"Manifest should be a JSON object: {path}")
    return manifest


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _find_effect_type_by_name(effect_name: str):
    for effect_type in VideoSceneEffectType:
        if effect_type.value.name == effect_name:
            return effect_type
    for effect_type in VideoCharacterEffectType:
        if effect_type.value.name == effect_name:
            return effect_type
    return None
