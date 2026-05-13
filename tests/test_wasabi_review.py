import json

from src.wasabi_jianying.review import apply_review_actions, parse_review_actions, write_timeline_review


def test_write_timeline_review_creates_annotatable_markdown(tmp_path):
    draft_path = tmp_path / "draft"
    draft_path.mkdir()
    manifest_path = draft_path / "wasabi_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "draft_name": "demo",
                "duration": 2_000_000,
                "clips": [
                    {
                        "index": 0,
                        "text": "hello",
                        "start": 0,
                        "duration": 2_000_000,
                        "end": 2_000_000,
                        "video_path": "/tmp/seg_00.mp4",
                        "audio_path": "/tmp/sent_00.wav",
                        "video_segment_id": "video-id",
                        "text_segment_id": "text-id",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    review_path = write_timeline_review(draft_path)

    content = review_path.read_text(encoding="utf-8")
    assert "<!-- wasabi-clip:0 -->" in content
    assert "Clip 000" in content
    assert "文案：hello" in content
    assert "- 效果：" in content


def test_parse_review_actions_reads_effect_lines(tmp_path):
    review_path = tmp_path / "timeline_review.md"
    review_path.write_text(
        "\n".join(
            [
                "<!-- wasabi-clip:0 -->",
                "标注：",
                "- 效果：DV界面",
                "<!-- wasabi-clip:2 -->",
                "- effect = 90s画质",
                "<!-- wasabi-clip:3 -->",
                "- 效果：",
            ]
        ),
        encoding="utf-8",
    )

    actions = parse_review_actions(review_path)

    assert [(action.clip_index, action.action, action.value) for action in actions] == [
        (0, "effect", "DV界面"),
        (2, "effect", "90s画质"),
    ]


def test_apply_review_actions_groups_effects(tmp_path, monkeypatch):
    draft_path = tmp_path / "draft"
    draft_path.mkdir()
    manifest_path = draft_path / "wasabi_manifest.json"
    manifest_path.write_text('{"clips": []}', encoding="utf-8")
    review_path = draft_path / "timeline_review.md"
    review_path.write_text(
        "\n".join(
            [
                "<!-- wasabi-clip:0 -->",
                "- 效果：DV界面",
                "<!-- wasabi-clip:1 -->",
                "- 效果：90s画质",
                "<!-- wasabi-clip:2 -->",
                "- 效果：DV界面",
            ]
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_apply_effect_to_clips(draft_dir, *, effect_name, clip_indexes, manifest_path):
        calls.append((str(draft_dir), effect_name, clip_indexes, str(manifest_path)))
        return "result"

    monkeypatch.setattr("src.wasabi_jianying.review.apply_effect_to_clips", fake_apply_effect_to_clips)

    result = apply_review_actions(draft_path)

    assert result.actions_count == 3
    assert result.effect_results == ["result", "result"]
    assert calls == [
        (str(draft_path.resolve()), "DV界面", [0, 2], str(manifest_path.resolve())),
        (str(draft_path.resolve()), "90s画质", [1], str(manifest_path.resolve())),
    ]
