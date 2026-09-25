import json
import shutil
from pathlib import Path

import pytest

from hunch import Project, resolve
from hunch import providers as prov
from hunch.ai import check_draft, enrich_domain
from hunch.extras import load_questions, run_eval
from hunch.providers import Provider, extract_json

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "home-notes"


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "notes"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(".hunch"))
    p = Project(root)
    p.build()
    return p


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, reply):
        super().__init__("fake-1")
        self.reply = reply
        self.calls = []

    def complete(self, system, messages, max_tokens=2048, temperature=None):
        self.calls.append((system, messages))
        return self.reply


def test_folders_become_areas(project):
    ids = {d.id for d in project.domains}
    assert {"house", "car", "insurance", "pets"} <= ids
    assert any(d.source == "cluster" for d in project.domains)


def test_signal_brings_in_unmentioned_area(project):
    pack = resolve(project, "we're getting a cold snap this weekend, what should I do?")
    labels = {a.domain.id for a in pack.active}
    assert "house" in labels
    assert any(p.doc == "house/plumbing.md" and p.via == "hunch" for p in pack.passages)


def test_training_mode_excludes_documents(project):
    project.set_authority("pets", "training")
    pack = resolve(project, "can I give the dog a chicken jerky treat?")
    assert not any(p.doc.startswith("pets/") for p in pack.passages)
    assert any(a.domain.id == "pets" for a in pack.parked)
    assert "general knowledge" in pack.render()


def test_ask_mode_is_flagged(project):
    project.set_authority("insurance", "ask")
    pack = resolve(project, "a hail storm just rolled through")
    assert [d.id for d in pack.ask_domains] == ["insurance"]
    assert "HIGH-STAKES" in pack.render()


def test_user_signal_persists(project):
    project.add_signal("car", "weird noise when braking")
    pack = resolve(Project(project.root), "there's a weird noise when braking")
    assert any(a.domain.id == "car" for a in pack.active)
    cfg = json.loads((project.root / "hunch.json").read_text())
    assert "weird noise when braking" in cfg["domains"]["car"]["signals"]


def test_rebuild_detects_changes_and_keeps_settings(project):
    (project.root / "house" / "garage.md").write_text("# Garage\nThe garage door opener remote battery is CR2032.")
    assert project.is_stale()
    project.ensure_fresh(min_interval=0)
    pack = resolve(project, "garage door opener battery")
    assert pack.passages[0].doc == "house/garage.md"
    assert project.domain("house").label == "House"


def test_cluster_ids_survive_rebuild(project):
    before = {d.id for d in project.domains if d.source == "cluster"}
    project.build()
    after = {d.id for d in project.domains if d.source == "cluster"}
    assert before == after


def test_enrich_stores_signals(project):
    fake = FakeProvider('```json\n{"label": "Vehicle", "description": "Car upkeep.", '
                        '"signals": ["long commute", "grinding sound"]}\n```')
    enrich_domain(project, fake, "car")
    d = project.domain("car")
    assert "grinding sound" in d.signals
    assert d.description == "Car upkeep."
    assert any(a.domain.id == "car" for a in resolve(project, "I hear a grinding sound").active)


def test_check_draft_parses_conflicts(project):
    pack = resolve(project, "what oil does the outback take?")
    fake = FakeProvider('{"conflicts": [{"draft_says": "5W-30", "documents_say": "0W-20", "passage": 1}]}')
    conflicts = check_draft(fake, "Use 5W-30.", pack)
    assert conflicts and conflicts[0].documents_say == "0W-20"
    assert conflicts[0].doc == pack.passages[0].doc


def test_eval_runs(project):
    qs = load_questions(EXAMPLE.parent / "home-notes-questions.jsonl")
    report = run_eval(project, qs, k=6)
    assert report["questions"] == len(qs)
    assert report["hunch_recall"] >= report["baseline_recall"]


def test_extract_json_variants():
    assert extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}
    assert extract_json('```json\n["x", "y"]\n```') == ["x", "y"]


def test_provider_selection(monkeypatch):
    for k in ("HUNCH_PROVIDER", "HUNCH_MODEL", "ANTHROPIC_API_KEY", "XAI_API_KEY", "HUNCH_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(prov.ProviderError):
        prov.get_provider()
    monkeypatch.setenv("XAI_API_KEY", "x")
    p = prov.get_provider()
    assert p.name == "xai" and p.model == "grok-4.7"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert prov.get_provider().name == "anthropic"
    assert prov.get_provider("grok").name == "xai"


def test_request_shapes(monkeypatch):
    sent = {}

    def fake_post(url, headers, body, timeout=120):
        sent.update(url=url, headers=headers, body=body)
        if "anthropic" in url:
            return {"content": [{"type": "text", "text": "hi"}]}
        return {"choices": [{"message": {"content": "hey"}}]}

    monkeypatch.setattr(prov, "_post", fake_post)
    a = prov.Anthropic("claude-sonnet-5", "k")
    assert a.complete("sys", [{"role": "user", "content": "q"}], temperature=0) == "hi"
    assert sent["body"]["system"] == "sys" and sent["headers"]["x-api-key"] == "k"
    assert "temperature" not in sent["body"]
    x = prov.OpenAICompatible("grok-4.7", "https://api.x.ai/v1", "k", name="xai")
    assert x.complete("sys", [{"role": "user", "content": "q"}]) == "hey"
    assert sent["url"] == "https://api.x.ai/v1/chat/completions"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_read_document_blocks_escape(project):
    from hunch import HunchError
    with pytest.raises(HunchError):
        project.read_document("../../etc/passwd")
