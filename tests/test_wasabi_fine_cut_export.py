from pathlib import Path

from src.wasabi_jianying.export_fine_cut import export_fine_cut_to_draft, load_fine_cut_clips


def test_load_fine_cut_clips_pairs_by_index(tmp_path):
    fine_cut = tmp_path / "fine_cut"
    segments = fine_cut / "segments"
    tts = fine_cut / "tts"
    segments.mkdir(parents=True)
    tts.mkdir()
    (segments / "seg_00.mp4").write_bytes(b"video")
    (segments / "seg_02.mp4").write_bytes(b"video")
    (tts / "sent_00.wav").write_bytes(b"audio")
    (tts / "sent_01.wav").write_bytes(b"audio")
    (tts / "sent_02.wav").write_bytes(b"audio")
    (fine_cut / "sentences.json").write_text(
        '[{"text": "first"}, {"text": "second"}, {"text": "third"}]',
        encoding="utf-8",
    )

    clips = load_fine_cut_clips(fine_cut)

    assert [clip.index for clip in clips] == [0, 2]
    assert [clip.text for clip in clips] == ["first", "third"]


def test_export_fine_cut_to_draft_uses_local_tracks(tmp_path, monkeypatch):
    fine_cut = tmp_path / "project" / "fine_cut"
    segments = fine_cut / "segments"
    tts = fine_cut / "tts"
    drafts = tmp_path / "drafts"
    segments.mkdir(parents=True)
    tts.mkdir()
    drafts.mkdir()
    (segments / "seg_00.mp4").write_bytes(b"video")
    (tts / "sent_00.wav").write_bytes(b"audio")
    (fine_cut / "sentences.json").write_text('[{"text": "hello"}]', encoding="utf-8")

    calls = []

    class FakeMaterial:
        def __init__(self, path):
            self.path = str(Path(path))
            self.duration = 2_000_000
            self.width = 1920
            self.height = 1080
            self.material_id = Path(path).stem

    class FakeSegment:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.end = args[1].end

    class FakeTimerange:
        def __init__(self, start, duration):
            self.start = start
            self.duration = duration

        @property
        def end(self):
            return self.start + self.duration

    class FakeScript:
        def add_track(self, *args, **kwargs):
            calls.append(("track", args, kwargs))

        def add_segment(self, segment, track_name=None):
            calls.append(("segment", track_name, segment))

        def save(self):
            calls.append(("save",))

    class FakeDraftFolder:
        def __init__(self, folder):
            self.folder = folder

        def create_draft(self, name, width, height, fps, allow_replace=False):
            calls.append(("create", self.folder, name, width, height, fps, allow_replace))
            return FakeScript()

    monkeypatch.setattr("src.wasabi_jianying.export_fine_cut.draft.DraftFolder", FakeDraftFolder)
    monkeypatch.setattr("src.wasabi_jianying.export_fine_cut.draft.VideoMaterial", FakeMaterial)
    monkeypatch.setattr("src.wasabi_jianying.export_fine_cut.draft.AudioMaterial", FakeMaterial)
    monkeypatch.setattr("src.wasabi_jianying.export_fine_cut.draft.VideoSegment", FakeSegment)
    monkeypatch.setattr("src.wasabi_jianying.export_fine_cut.draft.AudioSegment", FakeSegment)
    monkeypatch.setattr("src.wasabi_jianying.export_fine_cut.draft.TextSegment", FakeSegment)
    monkeypatch.setattr("src.wasabi_jianying.export_fine_cut.draft.Timerange", FakeTimerange)

    result = export_fine_cut_to_draft(fine_cut, drafts_folder=drafts, draft_name="demo")

    assert result.draft_path == drafts / "demo"
    assert result.clips_count == 1
    assert result.duration_us == 2_000_000
    assert ("create", str(drafts.resolve()), "demo", 1920, 1080, 30, True) in calls
    assert ("save",) in calls
    segment_tracks = [call[1] for call in calls if call[0] == "segment"]
    assert segment_tracks == ["wasabi_video", "wasabi_tts", "wasabi_subtitles"]
