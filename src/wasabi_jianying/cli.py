"""Command line entrypoint for Wasabi local Jianying draft export."""

from __future__ import annotations

import argparse

from .export_fine_cut import export_fine_cut_to_draft


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Wasabi fine-cut assets to a Jianying draft")
    parser.add_argument("fine_cut_dir", help="Wasabi fine_cut output directory")
    parser.add_argument("--draft-name", help="Jianying draft name")
    parser.add_argument("--drafts-folder", help="Jianying drafts root folder")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--no-subtitle", action="store_true", help="Skip subtitle track")
    parser.add_argument("--no-replace", action="store_true", help="Do not overwrite an existing draft")
    args = parser.parse_args()

    result = export_fine_cut_to_draft(
        args.fine_cut_dir,
        drafts_folder=args.drafts_folder,
        draft_name=args.draft_name,
        width=args.width,
        height=args.height,
        fps=args.fps,
        include_subtitles=not args.no_subtitle,
        allow_replace=not args.no_replace,
    )

    print("Jianying draft generated")
    print(f"Name: {result.draft_name}")
    print(f"Path: {result.draft_path}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Clips: {result.clips_count}")
    print(f"Duration: {result.duration_seconds:.1f}s")


if __name__ == "__main__":
    main()
