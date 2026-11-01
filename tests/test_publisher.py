"""Tests for publishing ad-free feeds to R2.

No network and no boto3 client: R2Publisher takes its client as a
collaborator, so a recording fake stands in for it and every assertion is
about which operations we would have performed.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from skipcastify.services.publish_manifest import PublishManifest
from skipcastify.services.publisher import (CACHE_FEED, CACHE_IMMUTABLE, GB,
                                            R2Publisher, feed_fingerprint)
from skipcastify.services.r2_client import redact_key

SECRET = "test-secret-segment"
SLUG = "test-podcast"
EP1 = "test-podcast-ep_1.mp3"
EP2 = "test-podcast-ep_2.mp3"


class FakeClientError(Exception):
    """Shaped like a botocore ClientError, without importing botocore."""

    def __init__(self, code):
        super().__init__(f"An error occurred ({code}) when calling the operation")
        self.response = {"Error": {"Code": code}}


class FakeR2:
    """Records operations instead of performing them."""

    def __init__(self, on_class_a=None, fail_keys=(), fail_all=False,
                 fail_code=None, reachable=True, refuse_deletes=False):
        self.puts = []          # (key, cache_control, size) in upload order
        self.attempts = []      # every key we were asked to write, failures too
        self.deleted = []
        self.bucket = "test-bucket"
        self.fail_keys = set(fail_keys)
        self.fail_all = fail_all
        self.fail_code = fail_code
        self.reachable = reachable
        self.refuse_deletes = refuse_deletes
        self.head_calls = 0
        self._on_class_a = on_class_a or (lambda n: None)

    def _maybe_fail(self, key):
        self.attempts.append(key)
        if self.fail_all or key in self.fail_keys:
            if self.fail_code:
                raise FakeClientError(self.fail_code)
            raise RuntimeError("simulated upload failure")

    def put_file(self, key, path, cache_control):
        self._maybe_fail(key)
        size = os.path.getsize(path)
        self.puts.append((key, cache_control, size))
        self._on_class_a(1)
        return size

    def put_bytes(self, key, body, content_type, cache_control):
        self._maybe_fail(key)
        self.puts.append((key, cache_control, len(body)))
        self._on_class_a(1)
        return len(body)

    def delete_keys(self, keys):
        keys = list(keys)
        self._on_class_a(1)
        if self.refuse_deletes:
            return []
        self.deleted.extend(keys)
        return keys

    def head_bucket(self):
        self.head_calls += 1
        return self.reachable

    @property
    def keys(self):
        return [k for k, _, _ in self.puts]


# --- fixtures and helpers -------------------------------------------------

@pytest.fixture
def alerts():
    sent = []

    def notifier(title, message, priority="default"):
        sent.append({"title": title, "message": message, "priority": priority})
        return True

    notifier.sent = sent
    return notifier


def make_publisher(tmp_path, alerts, client=None, **kwargs):
    manifest = PublishManifest(str(tmp_path))
    client = client or FakeR2(on_class_a=manifest.bump_class_a)
    pub = R2Publisher(
        client=client, manifest=manifest, secret=SECRET,
        public_base_url="https://pods.example.com",
        data_dir=str(tmp_path), notifier=alerts, **kwargs,
    )
    return pub


@pytest.fixture
def pub(tmp_path, alerts):
    return make_publisher(tmp_path, alerts)


def write_audio(tmp_path, kind, filename, size=1024, age_s=0, slug=SLUG):
    d = Path(tmp_path) / "podcasts" / kind / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / filename
    f.write_bytes(b"x" * size)
    if age_s:
        t = time.time() - age_s
        os.utime(f, (t, t))
    return f


def _entry(title="Ep 1", href="https://cdn.example.com/ep1.mp3"):
    e = MagicMock()
    e.title = title
    e.published_parsed = (2026, 1, 1, 12, 0, 0, 0, 0, 0)
    e.link = "https://example.com/ep"
    e.get.side_effect = lambda k, default=None: {"summary": "desc"}.get(k, default)
    e.enclosures = [{"length": "999", "href": href}]
    return e


def _feed(entries):
    f = MagicMock()
    f.feed.title = "Test Podcast"
    f.feed.get.side_effect = lambda k, default=None: {
        "description": "A podcast", "image": {}
    }.get(k, default)
    f.entries = entries
    return f


def publish(pub, entries):
    with patch("feedparser.parse", return_value=_feed(entries)), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        return pub.publish_feed("https://example.com/feed.xml")


def publish_all(pub, entries, urls=("https://example.com/feed.xml",)):
    with patch("feedparser.parse", return_value=_feed(entries)), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        return pub.publish_all(list(urls))


def feed_xml(pub):
    return (Path(pub.data_dir) / "feeds_r2" / f"{SLUG}.xml").read_text()


# --- inclusion rules ------------------------------------------------------

def test_processed_episode_published_with_r2_url(pub, tmp_path):
    write_audio(tmp_path, "raw", EP1)
    write_audio(tmp_path, "processed", EP1, size=2048)

    publish(pub, [_entry()])

    # Check the enclosure, not the whole document: the original CDN URL
    # legitimately remains as the entry's guid so the episode keeps its
    # identity when its audio moves to R2.
    _title, _guid, url = _items(pub)[0]
    assert url == f"https://pods.example.com/audio/{SECRET}/{SLUG}/{EP1}"
    assert "cdn.example.com" not in url
    assert pub.audio_key(SLUG, EP1) in pub.client.keys


def test_unprocessed_episode_is_withheld(pub, tmp_path):
    """A freshly downloaded episode must not reach the feed with its ads."""
    write_audio(tmp_path, "raw", EP1, age_s=600)

    publish(pub, [_entry()])

    xml = feed_xml(pub)
    assert "Ep 1" not in xml
    assert "cdn.example.com" not in xml
    assert pub.client.keys == [pub.feed_key(SLUG)]      # feed only, no audio


def test_fallback_to_original_after_window(pub, tmp_path):
    """Stuck past the fallback window, publish the original and say so."""
    write_audio(tmp_path, "raw", EP1, age_s=7 * 3600)

    publish(pub, [_entry()])

    xml = feed_xml(pub)
    assert "https://cdn.example.com/ep1.mp3" in xml
    assert "Ep 1" in xml
    assert len(pub._notify.sent) == 1
    assert "ads" in pub._notify.sent[0]["title"].lower()


def test_no_fallback_before_window(pub, tmp_path):
    write_audio(tmp_path, "raw", EP1, age_s=2 * 3600)

    publish(pub, [_entry()])

    assert pub._notify.sent == []
    assert "cdn.example.com" not in feed_xml(pub)


def test_fallback_alerts_only_once(pub, tmp_path):
    """The timer runs every 15 minutes; the alert must not run with it."""
    write_audio(tmp_path, "raw", EP1, age_s=7 * 3600)

    publish(pub, [_entry()])
    publish(pub, [_entry()])
    publish(pub, [_entry()])

    assert len(pub._notify.sent) == 1


def test_backlog_episode_keeps_original_url(pub, tmp_path):
    """Never downloaded, so it predates go-live: list the original audio."""
    publish(pub, [_entry()])

    xml = feed_xml(pub)
    assert "https://cdn.example.com/ep1.mp3" in xml
    assert pub._notify.sent == []          # not a failure, so no alert


def test_evicted_episode_reverts_to_its_original_audio(pub, tmp_path):
    """After eviction the item stays, pointing back at the original audio.

    Withdrawing it would be tidier, but episodes are often still in progress
    past the retention window and a vanishing item can drop out of a
    listening queue. The tombstone still does its main job: stopping the
    processed .mp3 that is still on local disk from being uploaded again.
    """
    write_audio(tmp_path, "processed", EP1, size=2048)
    key = pub.audio_key(SLUG, EP1)
    pub.manifest.record_put(key, 2048, "audio", now=time.time() - 20 * 86400)
    pub.enforce_retention()
    assert key in pub.client.deleted

    pub.client.puts.clear()
    publish(pub, [_entry()])

    title, _guid, url = _items(pub)[0]
    assert title == "Ep 1"                              # still listed
    assert url == "https://cdn.example.com/ep1.mp3"     # original audio
    assert key not in pub.client.keys                   # but not re-uploaded


# --- retention and the storage cap ---------------------------------------

def test_retention_evicts_only_expired_audio(pub):
    now = time.time()
    old = pub.audio_key(SLUG, EP1)
    fresh = pub.audio_key(SLUG, EP2)
    pub.manifest.record_put(old, 1000, "audio", now=now - 20 * 86400)
    pub.manifest.record_put(fresh, 1000, "audio", now=now - 1 * 86400)

    evicted = pub.enforce_retention(now=now)

    assert evicted == [old]
    assert pub.client.deleted == [old]
    assert pub.manifest.has(fresh)
    assert not pub.manifest.has(old)
    assert pub.manifest.is_tombstoned(old)


def test_cap_evicts_oldest_first(tmp_path, alerts):
    pub = make_publisher(tmp_path, alerts, cap_bytes=3000)
    now = time.time()
    oldest = pub.audio_key(SLUG, "oldest.mp3")
    newer = pub.audio_key(SLUG, "newer.mp3")
    pub.manifest.record_put(oldest, 1000, "audio", now=now - 300)
    pub.manifest.record_put(newer, 1000, "audio", now=now - 100)

    path = write_audio(tmp_path, "processed", EP1, size=2000)
    url = pub.ensure_audio(pub.audio_key(SLUG, EP1), str(path), SLUG, EP1)

    assert url is not None
    assert pub.client.deleted == [oldest]            # oldest went first
    assert pub.manifest.has(newer)
    assert pub.manifest.total_bytes("audio") <= 3000


def test_cap_skips_and_alerts_when_no_room_can_be_made(tmp_path, alerts):
    pub = make_publisher(tmp_path, alerts, cap_bytes=500)
    path = write_audio(tmp_path, "processed", EP1, size=4096)

    url = pub.ensure_audio(pub.audio_key(SLUG, EP1), str(path), SLUG, EP1)

    assert url is None
    assert pub.client.keys == []
    assert len(alerts.sent) == 1
    assert "cap" in alerts.sent[0]["title"].lower()


def test_upload_refusal_withholds_episode_rather_than_serving_ads(tmp_path, alerts):
    pub = make_publisher(tmp_path, alerts, cap_bytes=500)
    write_audio(tmp_path, "raw", EP1)
    write_audio(tmp_path, "processed", EP1, size=4096)

    publish(pub, [_entry()])

    xml = feed_xml(pub)
    assert "cdn.example.com" not in xml
    assert "Ep 1" not in xml


# --- Class A thrift -------------------------------------------------------

def test_feed_not_reuploaded_when_unchanged(pub, tmp_path):
    write_audio(tmp_path, "processed", EP1, size=2048)

    publish(pub, [_entry()])
    publish(pub, [_entry()])
    publish(pub, [_entry()])

    assert pub.client.keys.count(pub.feed_key(SLUG)) == 1


def test_feed_reuploaded_when_an_episode_appears(pub, tmp_path):
    write_audio(tmp_path, "processed", EP1, size=2048)
    publish(pub, [_entry()])

    write_audio(tmp_path, "processed", EP2, size=2048)
    publish(pub, [_entry(), _entry(title="Ep 2", href="https://cdn.example.com/ep2.mp3")])

    assert pub.client.keys.count(pub.feed_key(SLUG)) == 2


def test_fingerprint_ignores_lastbuilddate():
    """feedgen stamps the generation time into every feed it writes.

    Without normalising it the changed-only rule would never fire and we
    would re-upload three identical feeds every 15 minutes.
    """
    a = b"<rss><lastBuildDate>Sat, 01 Nov 2026 01:00:00 +0000</lastBuildDate><x/></rss>"
    b = b"<rss><lastBuildDate>Sat, 01 Nov 2026 02:00:00 +0000</lastBuildDate><x/></rss>"
    c = b"<rss><lastBuildDate>Sat, 01 Nov 2026 02:00:00 +0000</lastBuildDate><y/></rss>"

    assert feed_fingerprint(a) == feed_fingerprint(b)
    assert feed_fingerprint(a) != feed_fingerprint(c)


def test_audio_is_uploaded_before_the_feed(pub, tmp_path):
    """A feed must never reference an object that isn't in the bucket yet."""
    write_audio(tmp_path, "processed", EP1, size=2048)

    publish(pub, [_entry()])

    keys = pub.client.keys
    assert keys.index(pub.audio_key(SLUG, EP1)) < keys.index(pub.feed_key(SLUG))


def test_class_a_operations_are_counted(pub, tmp_path):
    write_audio(tmp_path, "processed", EP1, size=2048)
    publish(pub, [_entry()])

    # one audio PUT + one feed PUT
    assert pub.manifest.class_a_this_month() == 2


def test_cache_headers_match_the_object_kind(pub, tmp_path):
    write_audio(tmp_path, "processed", EP1, size=2048)
    publish(pub, [_entry()])

    headers = {k: cc for k, cc, _ in pub.client.puts}
    assert headers[pub.audio_key(SLUG, EP1)] == CACHE_IMMUTABLE
    assert headers[pub.feed_key(SLUG)] == CACHE_FEED


def test_budget_alert_fires_at_eighty_percent(tmp_path, alerts):
    pub = make_publisher(tmp_path, alerts, cap_bytes=1000)
    pub.manifest.record_put(pub.audio_key(SLUG, EP1), 850, "audio")

    pub.check_budgets()

    assert any("80%" in a["title"] for a in alerts.sent)


# --- secret handling ------------------------------------------------------

def test_keys_embed_the_secret_and_urls_are_public(pub):
    assert pub.audio_key(SLUG, EP1) == f"audio/{SECRET}/{SLUG}/{EP1}"
    assert pub.feed_key(SLUG) == f"feeds/{SECRET}/{SLUG}.xml"
    assert pub.art_key("a.jpg") == f"art/{SECRET}/a.jpg"
    assert pub.public_url("feeds/x") == "https://pods.example.com/feeds/x"


def test_redact_key_hides_the_secret():
    key = f"audio/{SECRET}/{SLUG}/{EP1}"
    assert SECRET not in redact_key(key, SECRET)
    assert redact_key(key, SECRET) == f"audio/<SECRET>/{SLUG}/{EP1}"


def test_publisher_refuses_an_empty_secret(tmp_path, alerts):
    with pytest.raises(ValueError):
        R2Publisher(client=FakeR2(), manifest=PublishManifest(str(tmp_path)),
                    secret="", public_base_url="https://x", data_dir=str(tmp_path))


def test_from_env_requires_a_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLIC_PATH_SECRET", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://pods.example.com")
    with pytest.raises(ValueError, match="PUBLIC_PATH_SECRET"):
        R2Publisher.from_env(str(tmp_path))


# --- manifest durability --------------------------------------------------

def test_manifest_round_trips(tmp_path):
    m = PublishManifest(str(tmp_path))
    m.record_put("audio/k.mp3", 123, "audio", sha256="abc")
    m.save()

    again = PublishManifest(str(tmp_path))
    assert again.has("audio/k.mp3")
    assert again.total_bytes("audio") == 123
    assert again.sha256("audio/k.mp3") == "abc"


def test_corrupt_manifest_is_moved_aside_not_silently_dropped(tmp_path):
    path = Path(tmp_path) / "publish_manifest.json"
    path.write_text("{ this is not json")

    m = PublishManifest(str(tmp_path))

    assert m.total_bytes() == 0
    assert (Path(tmp_path) / "publish_manifest.corrupt").exists()


def test_tombstones_are_pruned_after_their_ttl(tmp_path):
    m = PublishManifest(str(tmp_path))
    now = time.time()
    m.record_put("audio/old.mp3", 10, "audio", now=now - 100 * 86400)
    m.record_delete(["audio/old.mp3"], now=now - 90 * 86400)

    assert m.is_tombstoned("audio/old.mp3")
    assert m.prune_tombstones(60 * 86400, now=now) == 1
    assert not m.is_tombstoned("audio/old.mp3")


def test_class_a_counter_resets_each_month(tmp_path):
    m = PublishManifest(str(tmp_path))
    october = time.mktime(time.strptime("2026-10-15", "%Y-%m-%d"))
    november = time.mktime(time.strptime("2026-11-15", "%Y-%m-%d"))

    m.bump_class_a(5, now=october)
    assert m.class_a_this_month(october) == 5
    assert m.class_a_this_month(november) == 0

    m.bump_class_a(2, now=november)
    assert m.class_a_this_month(november) == 2


# --- pre-go-live backlog --------------------------------------------------

def _items(pub):
    """(title, guid, enclosure url) for each item in the published feed."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(feed_xml(pub))
    out = []
    for item in root.iter("item"):
        enc = item.find("enclosure")
        out.append((item.findtext("title"), item.findtext("guid"),
                    enc.get("url") if enc is not None else None))
    return out


def test_old_processed_audio_is_treated_as_backlog(pub, tmp_path):
    """July's processed episodes must not be uploaded on the first run.

    This box holds ~90 days of them. Uploading would contradict "start
    fresh", and they would all vanish again 14 days later.
    """
    write_audio(tmp_path, "processed", EP1, size=2048, age_s=92 * 86400)

    publish(pub, [_entry()])

    title, _guid, url = _items(pub)[0]
    assert url == "https://cdn.example.com/ep1.mp3"
    assert pub.audio_key(SLUG, EP1) not in pub.client.keys
    assert pub.manifest.total_bytes("audio") == 0


def test_old_raw_audio_is_treated_as_backlog(pub, tmp_path):
    """Downloaded long before go-live: still listed, never withheld."""
    write_audio(tmp_path, "raw", EP1, age_s=92 * 86400)

    publish(pub, [_entry()])

    title, _guid, url = _items(pub)[0]
    assert title == "Ep 1"
    assert url == "https://cdn.example.com/ep1.mp3"
    assert pub._notify.sent == []


def test_recent_processed_audio_is_still_uploaded(pub, tmp_path):
    """Guard against the backlog rule swallowing genuinely new episodes."""
    write_audio(tmp_path, "processed", EP1, size=2048, age_s=2 * 86400)

    publish(pub, [_entry()])

    assert pub.audio_key(SLUG, EP1) in pub.client.keys


# --- episode identity across a URL change --------------------------------

def test_guid_is_stable_across_the_fallback_to_r2_transition(pub, tmp_path):
    """The bug the guid exists to prevent.

    An episode first published with its original audio (the 6h fallback) and
    later republished from R2 changes enclosure URL. Without a guid a podcast
    app keys on that URL and sees a brand-new episode: a duplicate in the
    queue and a second download.
    """
    write_audio(tmp_path, "raw", EP1, age_s=7 * 3600)
    publish(pub, [_entry()])
    before = _items(pub)[0]
    assert before[2] == "https://cdn.example.com/ep1.mp3"

    write_audio(tmp_path, "processed", EP1, size=2048)
    publish(pub, [_entry()])
    after = _items(pub)[0]

    assert after[2].startswith("https://pods.example.com/audio/")   # URL moved
    assert after[1] == before[1]                                    # identity did not
    assert after[1] is not None


def test_guid_is_unchanged_when_an_episode_is_evicted(pub, tmp_path):
    """The R2 -> original switch must not look like a new episode.

    This is what makes reverting safe rather than disruptive: the app keeps
    the episode, and anything already downloaded, because identity held.
    """
    write_audio(tmp_path, "processed", EP1, size=2048)
    publish(pub, [_entry()])
    on_r2 = _items(pub)[0]
    assert on_r2[2].startswith("https://pods.example.com/audio/")

    pub.manifest.record_delete([pub.audio_key(SLUG, EP1)])
    publish(pub, [_entry()])
    evicted = _items(pub)[0]

    assert evicted[2] == "https://cdn.example.com/ep1.mp3"   # audio switched back
    assert evicted[1] == on_r2[1]                            # identity did not
    assert evicted[1] is not None


def test_cap_will_not_evict_an_episode_already_in_this_feed(tmp_path, alerts):
    """A feed must never be left pointing at an object we just deleted.

    Within one pass an earlier episode has already been written into the
    feed, so the cap has to refuse to evict it and skip the new upload
    instead.
    """
    pub = make_publisher(tmp_path, alerts, cap_bytes=3000)
    write_audio(tmp_path, "processed", EP1, size=2000)
    write_audio(tmp_path, "processed", EP2, size=2000)

    publish(pub, [_entry(),
                  _entry(title="Ep 2", href="https://cdn.example.com/ep2.mp3")])

    items = {t: u for t, _g, u in _items(pub)}
    assert items["Ep 1"].startswith("https://pods.example.com/audio/")
    assert pub.manifest.has(pub.audio_key(SLUG, EP1))     # kept, not evicted
    assert pub.audio_key(SLUG, EP1) not in pub.client.deleted
    assert "Ep 2" not in items                            # withheld instead
    assert any("cap" in a["title"].lower() for a in alerts.sent)


# --- R2 failures must be audible ------------------------------------------

def test_a_rejected_credential_alerts_and_halts_publishing(tmp_path, alerts):
    """A revoked API token otherwise looks exactly like nothing happening."""
    manifest = PublishManifest(str(tmp_path))
    client = FakeR2(on_class_a=manifest.bump_class_a, fail_all=True,
                    fail_code="InvalidAccessKeyId")
    pub = make_publisher(tmp_path, alerts, client=client)
    write_audio(tmp_path, "processed", EP1, size=2048)

    published = publish_all(pub, [_entry()])

    assert published == {}
    assert pub.halted is not None
    assert len(alerts.sent) == 1
    assert alerts.sent[0]["priority"] == "high"
    assert "InvalidAccessKeyId" in alerts.sent[0]["message"]


@pytest.mark.parametrize("code", ["AccessDenied", "InvalidAccessKeyId",
                                  "SignatureDoesNotMatch"])
def test_every_credential_code_is_treated_as_fatal(tmp_path, alerts, code):
    manifest = PublishManifest(str(tmp_path))
    client = FakeR2(on_class_a=manifest.bump_class_a, fail_all=True, fail_code=code)
    pub = make_publisher(tmp_path, alerts, client=client)
    write_audio(tmp_path, "processed", EP1, size=2048)

    publish_all(pub, [_entry()])

    assert pub.halted is not None
    assert any(code in a["message"] for a in alerts.sent)


def test_a_credential_failure_stops_the_remaining_feeds(tmp_path, alerts):
    """Pushing on means one identical failure per episode per feed."""
    manifest = PublishManifest(str(tmp_path))
    client = FakeR2(on_class_a=manifest.bump_class_a, fail_all=True,
                    fail_code="AccessDenied")
    pub = make_publisher(tmp_path, alerts, client=client)
    write_audio(tmp_path, "processed", EP1, size=2048)

    publish_all(pub, [_entry()], urls=["https://a.example/f.xml",
                                       "https://b.example/f.xml",
                                       "https://c.example/f.xml"])

    assert len(client.attempts) == 1          # gave up after the first
    assert len(alerts.sent) == 1


def test_an_unreachable_bucket_alerts_and_publishes_nothing(tmp_path, alerts):
    manifest = PublishManifest(str(tmp_path))
    client = FakeR2(on_class_a=manifest.bump_class_a, reachable=False)
    pub = make_publisher(tmp_path, alerts, client=client)
    write_audio(tmp_path, "processed", EP1, size=2048)

    assert publish_all(pub, [_entry()]) == {}
    assert client.attempts == []
    assert len(alerts.sent) == 1
    assert "unreachable" in alerts.sent[0]["title"].lower()


def test_an_unreachable_bucket_alerts_once_not_every_15_minutes(tmp_path, alerts):
    manifest = PublishManifest(str(tmp_path))
    client = FakeR2(on_class_a=manifest.bump_class_a, reachable=False)
    pub = make_publisher(tmp_path, alerts, client=client)

    for _ in range(6):
        publish_all(pub, [_entry()])

    assert len(alerts.sent) == 1


def test_one_transient_upload_failure_does_not_halt_publishing(tmp_path, alerts):
    """A single flaky PUT is not a reason to stop: the feed still updates."""
    manifest = PublishManifest(str(tmp_path))
    pub = make_publisher(tmp_path, alerts)
    pub.client.fail_keys = {pub.audio_key(SLUG, EP1)}
    write_audio(tmp_path, "processed", EP1, size=2048)

    published = publish_all(pub, [_entry()])

    assert pub.halted is None
    assert published != {}                   # the feed itself went out
    assert alerts.sent == []                 # below the repeated-failure bar


def test_repeated_failures_alert_once(tmp_path, alerts):
    """Three failures in a run is systemic for a pilot of single-digit ops."""
    manifest = PublishManifest(str(tmp_path))
    client = FakeR2(on_class_a=manifest.bump_class_a, fail_all=True)
    pub = make_publisher(tmp_path, alerts, client=client)
    for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
        write_audio(tmp_path, "processed", name, size=1024)
        pub.ensure_audio(pub.audio_key(SLUG, name), str(
            Path(tmp_path) / "podcasts" / "processed" / SLUG / name), SLUG, name)

    assert pub._failures == 4
    assert len(alerts.sent) == 1
    assert "Repeated R2 failures" == alerts.sent[0]["title"]


def test_a_refused_delete_is_not_tombstoned(tmp_path, alerts):
    """It is still costing storage, so the next run must try again."""
    manifest = PublishManifest(str(tmp_path))
    client = FakeR2(on_class_a=manifest.bump_class_a, refuse_deletes=True)
    pub = make_publisher(tmp_path, alerts, client=client)
    now = time.time()
    key = pub.audio_key(SLUG, EP1)
    pub.manifest.record_put(key, 1000, "audio", now=now - 20 * 86400)

    pub.enforce_retention(now=now)

    assert pub.manifest.has(key)                   # still live, will retry
    assert not pub.manifest.is_tombstoned(key)


def test_a_raising_delete_does_not_crash_the_run(tmp_path, alerts):
    pub = make_publisher(tmp_path, alerts)

    def boom(keys):
        raise FakeClientError("InternalError")

    pub.client.delete_keys = boom
    now = time.time()
    pub.manifest.record_put(pub.audio_key(SLUG, EP1), 1000, "audio",
                            now=now - 20 * 86400)

    pub.enforce_retention(now=now)                 # no raise

    assert pub.manifest.has(pub.audio_key(SLUG, EP1))


def test_an_r2_alert_never_contains_the_path_secret(tmp_path, alerts, monkeypatch):
    """Keys embed PUBLIC_PATH_SECRET and ntfy is a public channel."""
    monkeypatch.setenv("PUBLIC_PATH_SECRET", SECRET)
    manifest = PublishManifest(str(tmp_path))
    client = FakeR2(on_class_a=manifest.bump_class_a, fail_all=True,
                    fail_code="AccessDenied")
    pub = make_publisher(tmp_path, alerts, client=client)
    write_audio(tmp_path, "processed", EP1, size=2048)

    publish_all(pub, [_entry()])

    for alert in alerts.sent:
        assert SECRET not in alert["title"]
        assert SECRET not in alert["message"]
