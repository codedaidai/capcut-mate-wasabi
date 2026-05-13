"""CLI for creating or applying Wasabi timeline reviews."""

from __future__ import annotations

import argparse

from .review import apply_review_actions, write_timeline_review


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or apply a Wasabi timeline review")
    parser.add_argument("draft_path", help="Path to a Wasabi-generated Jianying draft folder")
    parser.add_argument("--apply", action="store_true", help="Apply supported review actions to the draft")
    parser.add_argument("--manifest", help="Path to wasabi_manifest.json")
    parser.add_argument("--review", help="Path to timeline_review.md")
    args = parser.parse_args()

    if args.apply:
        result = apply_review_actions(args.draft_path, review_path=args.review, manifest_path=args.manifest)
        print("Timeline review applied")
        print(f"Review: {result.review_path}")
        print(f"Manifest: {result.manifest_path}")
        print(f"Actions: {result.actions_count}")
        print(f"Effect groups: {len(result.effect_results)}")
    else:
        review_path = write_timeline_review(args.draft_path, manifest_path=args.manifest, review_path=args.review)
        print("Timeline review generated")
        print(f"Review: {review_path}")


if __name__ == "__main__":
    main()
