"""CLI for applying an effect to Wasabi clip indexes."""

from __future__ import annotations

import argparse

from .edit_draft import apply_effect_to_clips


def _parse_clip_indexes(raw: str) -> list[int]:
    indexes = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        indexes.append(int(part))
    return indexes


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply a Jianying effect to Wasabi draft clips")
    parser.add_argument("draft_path", help="Path to a Wasabi-generated Jianying draft folder")
    parser.add_argument("--effect", required=True, help="Jianying effect display name, for example: DV界面")
    parser.add_argument("--clips", required=True, help="Comma-separated Wasabi clip indexes, for example: 0,3,7")
    parser.add_argument("--manifest", help="Path to wasabi_manifest.json")
    args = parser.parse_args()

    result = apply_effect_to_clips(
        args.draft_path,
        effect_name=args.effect,
        clip_indexes=_parse_clip_indexes(args.clips),
        manifest_path=args.manifest,
    )

    print("Effect applied")
    print(f"Draft: {result.draft_path}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Track: {result.track_name}")
    print(f"Clips: {result.clips_count}")


if __name__ == "__main__":
    main()
