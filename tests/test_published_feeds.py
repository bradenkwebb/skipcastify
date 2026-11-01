"""Tests for working out which feeds are actually published to R2.

No Flask here: the service answers "which feed URLs would work right now",
which is a question about the publish manifest, not about HTTP.
"""

from skipcastify.services.publish_manifest import PublishManifest
from skipcastify.services.published_feeds import (overcast_add_url,
                                                  published_feeds)

SECRET = "path-secret-value-abcdef123456"


def feed_key(slug, secret=SECRET):
    return f"feeds/{secret}/{slug}.xml"


def audio_key(slug, filename, secret=SECRET):
    return f"audio/{secret}/{slug}/{filename}"


def write_local_feed(tmp_path, slug, title):
    d = tmp_path / "feeds_r2"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.xml").write_text(
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f'<title>{title}</title><link>https://x.test/</link>'
        '<item><title>An episode</title></item>'
        '</channel></rss>')


def publish(tmp_path, slug, title="A Show", episodes=0, secret=SECRET):
    """Put a feed in the manifest, as a successful upload would."""
    manifest = PublishManifest(str(tmp_path))
    manifest.record_put(feed_key(slug, secret), 2048, "feed")
    for i in range(episodes):
        manifest.record_put(audio_key(slug, f"ep{i}.mp3", secret),
                            30 * 1024 ** 2, "audio")
    manifest.save()
    write_local_feed(tmp_path, slug, title)
    return manifest


# --- which feeds, and what URL ---------------------------------------------


def test_the_url_comes_from_the_key_not_the_current_secret(tmp_path):
    """After a secret rotation the old key is still the URL that works."""
    publish(tmp_path, "a-show", secret="an-older-secret")

    rows = published_feeds(str(tmp_path), "https://example.com")

    assert rows[0].url == "https://example.com/feeds/an-older-secret/a-show.xml"



def test_a_missing_local_xml_falls_back_to_the_slug(tmp_path):
    """A blank row would be worse than an ugly but accurate one."""
    manifest = PublishManifest(str(tmp_path))
    manifest.record_put(feed_key("the-ezra-klein-show"), 2048, "feed")
    manifest.save()

    rows = published_feeds(str(tmp_path), "https://example.com")

    assert rows[0].title == "the-ezra-klein-show"



def test_feeds_are_sorted_by_title(tmp_path):
    publish(tmp_path, "b-show", title="Zebra")
    publish(tmp_path, "a-show", title="apple")

    rows = published_feeds(str(tmp_path), "https://example.com")

    assert [r.title for r in rows] == ["apple", "Zebra"]



def test_episodes_are_counted_per_feed(tmp_path):
    publish(tmp_path, "one", episodes=2)
    publish(tmp_path, "two", episodes=5)

    rows = {r.slug: r.episodes for r in published_feeds(str(tmp_path), "https://example.com")}

    assert rows == {"one": 2, "two": 5}


def test_a_feed_only_generated_locally_is_not_listed(tmp_path):
    """The XML exists the moment a feed is generated, which is before it is
    uploaded and stays true if the upload failed."""
    publish(tmp_path, "uploaded", title="Uploaded Show")
    write_local_feed(tmp_path, "never-uploaded", "Never Uploaded")

    rows = published_feeds(str(tmp_path), "https://example.com")

    assert [r.slug for r in rows] == ["uploaded"]


def test_a_tombstoned_feed_is_not_listed(tmp_path):
    manifest = publish(tmp_path, "gone")
    manifest.record_delete([feed_key("gone")])
    manifest.save()

    assert published_feeds(str(tmp_path), "https://example.com") == []


def test_a_trailing_slash_on_the_base_url_does_not_double_up(tmp_path):
    publish(tmp_path, "a-show")

    rows = published_feeds(str(tmp_path), "https://example.com/")

    assert "//feeds" not in rows[0].url


# --- the Overcast shortcut -------------------------------------------------


def test_the_overcast_url_uses_the_documented_scheme():
    """overcast.fm/podcasterinfo: overcast://x-callback-url/add?url=<encoded>."""
    url = overcast_add_url("https://example.com/feeds/abc/a-show.xml")

    assert url == ("overcast://x-callback-url/add?url="
                   "https%3A%2F%2Fexample.com%2Ffeeds%2Fabc%2Fa-show.xml")



def test_the_overcast_url_fully_encodes_a_query_string():
    """An unencoded feed URL carrying its own query string gets truncated."""
    url = overcast_add_url("https://example.com/f.xml?token=a&b=1")

    assert "?token=a&b=1" not in url
    assert "%3Ftoken%3Da%26b%3D1" in url
