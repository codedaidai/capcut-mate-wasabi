import json

from src.wasabi_jianying.edit_draft import apply_effect_to_clips


def test_apply_effect_to_clips_updates_manifest(tmp_path, monkeypatch):
    draft_path = tmp_path / "draft"
    draft_path.mkdir()
    (draft_path / "draft_content.json").write_text("{}", encoding="utf-8")
    manifest_path = draft_path / "wasabi_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "clips": [
                    {"index": 0, "start": 0, "duration": 1_000_000},
                    {"index": 2, "start": 3_000_000, "duration": 2_000_000},
                ],
            }
        ),
        encoding="utf-8",
    )

    calls = []

    class FakeEffectInst:
        global_id = "effect-id"

    class FakeEffectSegment:
        def __init__(self, effect_type, target_timerange):
            self.effect_type = effect_type
            self.target_timerange = target_timerange
            self.segment_id = f"segment-{target_timerange.start}"
            self.effect_inst = FakeEffectInst()

    class FakeTimerange:
        def __init__(self, start, duration):
            self.start = start
            self.duration = duration

    class FakeScript:
        def add_track(self, track_type, track_name):
            calls.append(("track", track_name))

        def add_segment(self, segment, track_name):
            calls.append(("segment", track_name, segment.target_timerange.start, segment.target_timerange.duration))

        def save(self):
            calls.append(("save",))

    class FakeScriptFile:
        @staticmethod
        def load_template(path):
            calls.append(("load", path))
            return FakeScript()

    monkeypatch.setattr("src.wasabi_jianying.edit_draft._find_effect_type_by_name", lambda name: "fake-effect")
    monkeypatch.setattr("src.wasabi_jianying.edit_draft.draft.ScriptFile", FakeScriptFile)
    monkeypatch.setattr("src.wasabi_jianying.edit_draft.draft.EffectSegment", FakeEffectSegment)
    monkeypatch.setattr("src.wasabi_jianying.edit_draft.draft.Timerange", FakeTimerange)

    result = apply_effect_to_clips(draft_path, effect_name="DV界面", clip_indexes=[2, 0])

    assert result.track_name == "wasabi_effects_01"
    assert result.segment_ids == ["segment-0", "segment-3000000"]
    assert ("track", "wasabi_effects_01") in calls
    assert ("save",) in calls
    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated_manifest["edits"] == [
        {
            "type": "effect",
            "effect_name": "DV界面",
            "track_name": "wasabi_effects_01",
            "clip_indexes": [0, 2],
            "segment_ids": ["segment-0", "segment-3000000"],
            "effect_ids": ["effect-id", "effect-id"],
        }
    ]
