"""Score predicted cuts against reviewed /label ground truth, in milliseconds of
transcribed audio. Same rules as the /label page: only spoken time counts, and
the pipeline cuts every whole transcript line a span touches."""

from skipcastify.services.ground_truth import CUT_LABELS

Interval = tuple[int, int]


def merge(intervals) -> list[Interval]:
    out: list[list[int]] = []
    for s, e in sorted((int(s), int(e)) for s, e in intervals if e > s):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Both inputs merged and sorted."""
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if e > s:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def total(intervals: list[Interval]) -> int:
    return sum(e - s for s, e in intervals)


def _bounds(x):
    return (x["start"], x["end"]) if isinstance(x, dict) else (x.start, x.end)


def cut_lines(segments, spans: list[dict]) -> list[Interval]:
    """What the pipeline removes: each line overlapping any span, whole."""
    lines = [_bounds(seg) for seg in segments]
    return merge((s, e) for s, e in lines
                 if any(s < sp["end_ms"] and e > sp["start_ms"] for sp in spans))


def score(segments, labels: list[dict], spans: list[dict]) -> dict:
    """labels: /label v2 spans; spans: predicted LLM spans. All values in ms."""
    spoken = merge(_bounds(seg) for seg in segments)
    truth = intersect(merge((l["start_ms"], l["end_ms"]) for l in labels if l["label"] in CUT_LABELS), spoken)
    cut = intersect(cut_lines(segments, spans), spoken)
    hit = total(intersect(truth, cut))
    return {"spoken_ms": total(spoken), "truth_ms": total(truth), "cut_ms": total(cut),
            "missed_ms": total(truth) - hit, "wrong_ms": total(cut) - hit}
