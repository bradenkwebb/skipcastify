"""Ask Overcast to re-crawl a feed we have just published.

Overcast crawls server-side "at varying intervals from a few minutes to 24
hours, depending on their popularity" — and these feeds have exactly one
listener, so they sit at the unpopular end of that range. An episode can be
ad-free and sitting in R2 for hours before it appears on the phone, which
undercuts the point of the whole pipeline.

The ping API (https://overcast.fm/podcasterinfo, "Ping API for immediate feed
crawling") exists for this: a GET to /ping triggers an immediate crawl
"rather than waiting the 5-30 minutes it might typically take". No
authentication, and it is "heavily rate-limited by design", with over-limit
requests "ignored" — so this never retries and a failure is not an error.

**No URL from here may reach a log.** A feed URL embeds PUBLIC_PATH_SECRET,
and the bucket is world-readable, so the URL *is* the audio. requests puts the
full request URL into its exception messages, so the handler below logs the
exception class and status code and never the exception itself.
"""

import logging
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

PING_URL = "https://overcast.fm/ping"
TIMEOUT_S = 10


def ping_url_for(feed_url: str) -> str:
    """The ping request URL for one feed.

    ``urlprefix`` is documented as "Update any known feed URLs that begin with
    this prefix", and must be URL-encoded. A complete feed URL is a valid
    prefix: it matches that feed, and nothing else of ours extends it.
    """
    return f"{PING_URL}?urlprefix={quote(feed_url, safe='')}"


def ping(feed_url: str, timeout: float = TIMEOUT_S, requester=None) -> bool:
    """Request an immediate re-crawl of one feed. True if Overcast accepted it.

    Best effort by design: a rate-limited ping is silently ignored by Overcast,
    so a False here means "no faster crawl this time", not "something is
    broken". The feed still gets picked up on its normal schedule.
    """
    get = requester if requester is not None else requests.get
    try:
        resp = get(ping_url_for(feed_url), timeout=timeout)
    except Exception as e:
        # Deliberately not logging the exception: requests includes the full
        # request URL in its message, and that URL carries the path secret.
        logger.warning("Overcast ping failed (%s)", type(e).__name__)
        return False
    status = getattr(resp, "status_code", 0)
    if 200 <= status < 300:
        logger.debug("Overcast ping accepted (HTTP %s)", status)
        return True
    logger.warning("Overcast ping returned HTTP %s", status)
    return False
