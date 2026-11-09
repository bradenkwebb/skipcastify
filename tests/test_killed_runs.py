"""A run killed without unwinding (OOM) is reported by the next run."""

import json

import pipeline as pl
from pipeline import report_killed_run
from skipcastify.services.run_log import (
    RunRecord,
    recent_runs,
    take_inflight,
    write_inflight,
)
from skipcastify.utils.alerts import Alerter
from tests.test_pipeline_alerts import sent  # noqa: F401


def test_no_marker_means_nothing_to_report(tmp_path, sent):
    report_killed_run(str(tmp_path), Alerter(str(tmp_path), notifier=sent))
    assert sent.sent == [] and recent_runs(str(tmp_path)) == []


def test_a_leftover_marker_alerts_and_records_a_killed_run(tmp_path, sent):
    write_inflight(str(tmp_path), RunRecord(), "process", "show/long.mp3")
    report_killed_run(str(tmp_path), Alerter(str(tmp_path), notifier=sent))

    assert len(sent.sent) == 1 and sent.sent[0]["priority"] == "high"
    assert "show/long.mp3" in sent.sent[0]["message"]
    (run,) = recent_runs(str(tmp_path))
    assert run["outcome"] == "killed" and run["stage"] == "process"
    assert take_inflight(str(tmp_path)) is None  # reported once


def test_main_clears_the_marker_after_a_normal_run(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SUBSCRIPTIONS", "unused")
    monkeypatch.setattr(pl, "load_dotenv", lambda: None)

    class Fake:
        stage, alerted = "startup", False

        def __init__(self, _cfg, data_dir, **_kw):
            write_inflight(data_dir, RunRecord(), "process")

        def run(self):
            pass
    monkeypatch.setattr(pl, "Pipeline", Fake)

    assert pl.main() == 0
    assert not (tmp_path / "metrics" / "run_inflight.json").exists()
    assert json.loads((tmp_path / "metrics" / "runs.jsonl").read_text())["outcome"] == "ok"
