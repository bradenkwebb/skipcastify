import pytest

from skipcastify.services import cut_policy

SPANS = [{"start_ms": 0, "end_ms": 10, "label": "ADVERTISEMENT"},
         {"start_ms": 20, "end_ms": 30, "label": "SELF_PROMO"},
         {"start_ms": 40, "end_ms": 50, "label": "WHO_KNOWS"}]


def test_missing_file_cuts_ads_and_self_promo(tmp_path):
    p = tmp_path / "policy.yaml"
    assert cut_policy.cut_labels("any-show", p) == {"ad", "self_promo"}
    cut, kept = cut_policy.split(SPANS, "any-show", p)
    assert (len(cut), kept) == (3, [])  # unknown LLM labels count as ads


def test_feed_override_keeps_self_promo_and_reloads_on_change(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("feeds:\n  mormon-stories-podcast:\n    cut: [ad]\n")
    cut, kept = cut_policy.split(SPANS, "mormon-stories-podcast", p)
    assert [s["label"] for s in kept] == ["SELF_PROMO"] and len(cut) == 2
    assert cut_policy.cut_labels("odd-lots", p) == {"ad", "self_promo"}
    p.write_text("default:\n  cut: []\n")
    import os
    os.utime(p, (1, 1))  # a different mtime, as an edit would make
    assert cut_policy.cut_labels("odd-lots", p) == frozenset()


@pytest.mark.parametrize("text", ["default:\n  cut: [ads]\n", "feeds:\n  x:\n    cut: ad\n"])
def test_bad_policy_fails_loudly(tmp_path, text):
    p = tmp_path / "policy.yaml"
    p.write_text(text)
    with pytest.raises(ValueError):
        cut_policy.load(p)


def test_example_file_parses():
    from pathlib import Path
    example = Path(__file__).resolve().parents[1] / "policy.example.yaml"
    assert cut_policy.load(example)["default"] == {"ad", "self_promo"}
