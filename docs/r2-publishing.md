# Publishing ad-free feeds to Cloudflare R2

## Why

Overcast does not fetch feeds from your phone. Its crawlers (`crawl1-6.overcast.fm`)
fetch them server-side, and those servers cannot reach a tailnet. That was proven
empirically: three credential mechanisms all failed in Overcast and all three
worked in Chrome on the same phone, against the same URL.

The obvious fix — Tailscale Funnel — means accepting inbound traffic from the
public internet onto this box. We don't want that. So the pipeline **pushes**
static objects out to a Cloudflare R2 bucket instead, and podcast apps read from
there. `bw-server` keeps taking no inbound connections at all.

## Layout

```
audio/<SECRET>/<feed-slug>/<file>.mp3
feeds/<SECRET>/<feed-slug>.xml
art/<SECRET>/<file>
```

`<SECRET>` is `PUBLIC_PATH_SECRET`, a fresh `secrets.token_urlsafe(32)`. It is
**obscurity, not access control**: the bucket is world-readable, so anyone
holding a URL can fetch the object, and Overcast's crawlers will hold those URLs
indefinitely. Treat a feed URL like a password. Because keys embed the secret,
nothing here logs a key without passing it through `redact_key()` first.

The `audio/` prefix must stay as-is — the bucket's lifecycle rule (delete after
30 days) matches on it as a backstop beneath our own 14-day retention.

## What ends up in a feed

| episode state | enclosure |
|---|---|
| processed audio uploaded | R2 URL |
| downloaded, unprocessed, < `R2_FALLBACK_HOURS` | withheld |
| downloaded, unprocessed, past that window | original URL, alerted once |
| published once, then evicted from R2 | original URL, same guid |
| never downloaded (pre-go-live backlog) | original URL |

So a published feed is a rolling 14-day window of ad-free episodes, with
everything outside that window — the pre-go-live backlog and episodes that have
aged out of it alike — still listed against its original audio.

Two of those rows are deliberate choices worth stating:

- **An unprocessed episode is withheld rather than published with its ads.**
  The point of the feed is that it's ad-free; a silent fallback would defeat it.
  After `R2_FALLBACK_HOURS` the episode appears with the original audio anyway,
  because a permanently missing episode is worse than an honest one with ads —
  and an alert fires so the failure isn't silent.
- **Once an episode is evicted from R2 it reverts to its original audio**
  rather than disappearing. Withdrawing the item would be the tidier story, but
  episodes are often still in progress well past the retention window, and an
  item vanishing can drop one out of a listening queue. Because the guid never
  changes, the app keeps the episode — and whatever it has already downloaded —
  and only a fresh download would pick up the version with ads.

## Free-tier guardrails

R2's free tier is 10 GB-month of storage, 1M Class A operations and 10M Class B.
Egress is free, which is the whole reason R2 and not S3.

| guardrail | setting | why |
|---|---|---|
| retention | `R2_RETENTION_DAYS=14` | bounds storage regardless of feed volume |
| hard cap | `R2_CAP_GB=7` | headroom under 10 GB; evicts oldest first |
| LIST only to reconcile | `R2_RECONCILE_HOURS=24` | `ListObjectsV2` is Class A, so a local manifest is the source of truth |
| one PUT per object | — | boto3 would otherwise bill a 90 MB episode as ~14 multipart ops |
| changed-only feeds | — | a feed is uploaded only when its content hash changes |
| cache headers | — | feeds `max-age=300`, audio and art `immutable`; CDN hits don't reach R2 |
| alerting | 80% | ntfy when storage or monthly Class A crosses 80% |

The content hash **normalises `<lastBuildDate>` out** before comparing. feedgen
stamps that element with the generation time, so hashing raw bytes would make
every feed look changed and re-upload three identical feeds every 15 minutes —
roughly 8,600 wasted Class A operations a month, defeating the guardrail it
exists to serve.

### The manifest

`data/publish_manifest.json` records every uploaded key with its size, upload
time and (for feeds) a content hash. It is written atomically, and a corrupt
manifest is moved aside loudly rather than silently stranding objects.

Deletes are recorded as **tombstones** rather than removing the entry. The
processed `.mp3` stays on local disk after retention evicts it from R2, so
forgetting the key would make the next run see "file on disk, nothing uploaded"
and upload it again — re-publishing every episode retention just expired, every
15 minutes, forever. Tombstones are pruned after 60 days.

### Daily reconciliation

The manifest is authoritative so that we never have to LIST — but it can fall
out of step with the bucket:

- a crash between a successful PUT and `record_put` leaves an **orphan** that
  the cap cannot see and retention will never evict (this happened: a kernel
  crash on 2026-10-30);
- a partially failed delete, or a manual delete in the Cloudflare dashboard,
  leaves a **ghost** the published feed still points at;
- `feeds/` and `art/` sit outside the bucket lifecycle rule, so nothing else
  would ever clean them up.

So `R2Publisher.reconcile()` runs one `ListObjectsV2` at most every
`R2_RECONCILE_HOURS` (default 24) at the top of a publish run — about 30 Class
A operations a month, against an allowance of a million. **This is the only
place LIST is allowed.**

| drift | what happens |
|---|---|
| in the bucket, not in the manifest, matches our layout, over 1h old | deleted |
| same but under 1h old | left alone — it may be a PUT from a run still in flight |
| same but under a *previous* `PUBLIC_PATH_SECRET` | deleted; the secret segment is matched loosely so a rotation leaves nothing behind |
| in the bucket, not matching our layout at all | **never deleted**, alerted once; we are not necessarily the only writer |
| in the manifest, not in the bucket | tombstoned, alerted once; the feed entry reverts to its original enclosure on the next build |
| size disagrees | corrected from the listing; alerted once past `max(5%, 100 MB)` |

It runs **before** retention and the cap, so both work from reconciled sizes.
A failed listing does not record a reconcile — one network blip should not mean
24 hours of unrepaired drift — and does not block the publish run that follows.
A tripped breaker skips it entirely, LIST included. Its summary log line
carries counts only, never keys.

Reconciliation is a repair pass, not the safety net. The bucket lifecycle rule
is still the backstop: anything we lose track of under `audio/` is deleted
after 30 days regardless.

For go-live day, or any time you want it now rather than in 24 hours:

```
uv run python -m skipcastify.tools.r2ctl reconcile
```

## Alerting

`data/alert_state.json` + `skipcastify/utils/alerts.py`. Alerts go to
`NTFY_TOPIC`; with it unset every alert below is a silent no-op, which is what
a fresh checkout gets.

**Every alert is throttled by a fingerprint**, at most one push per 6h. The
timer runs every 15 minutes, so an unguarded alert on a recurring failure is
~96 identical pushes a day for one broken thing — which just teaches you to
ignore the channel.

**Alert text is scrubbed on the send path**, not at each call site: env secret
values, URL credentials, `?token=`-style query parameters and
`/private/<token>` paths are replaced before anything leaves the machine. An
exception message almost always contains the thing that failed, and here that
is often the private Substack feed URL or an object key embedding
`PUBLIC_PATH_SECRET`. An ntfy topic is a public channel over the open
internet.

| alert | fires when |
|---|---|
| `Pipeline failed: <stage>` | an unhandled exception in `run()` or `main()`, high priority; the stage is `download`/`process`/`cleanup`/`feeds`/`publish`/`startup` |
| `Pipeline recovered` | the first clean run after any failure |
| `Episode published with ads` | the `R2_FALLBACK_HOURS` fallback, once per episode |
| `R2 storage cap reached` | an upload skipped because nothing older could be evicted |
| `R2 storage at 80%` / `R2 Class A ops at 80%` | budget warnings |
| `R2 credentials rejected` | `AccessDenied`, `InvalidAccessKeyId`, `SignatureDoesNotMatch` or `ExpiredToken`, high priority |
| `R2 bucket unreachable` | the `head_bucket` check at the top of a publish run failed |
| `Repeated R2 failures` | `R2_MAX_FAILURES_PER_RUN` (default 3) uploads or deletes failed in one run |

A failing run is recorded separately from the throttle window, because a
pipeline broken for two days and then fixed still needs to announce it — and
recovery clears the window so a later recurrence of the same failure is
reported rather than silenced by a leftover window.

Per-feed failures are deliberately **not** pipeline failures: one unreachable
feed already degrades to a warning and the run continues, and that stays true.

### Why an R2 credential error stops the run

A revoked or rotated API token is the one failure mode that looks exactly like
nothing happening: every upload fails, the feeds quietly stop updating, and
without an alert the first sign is noticing an episode never arrived. So R2
answers in the table above **halt publishing for the rest of the run** instead
of retrying per object — otherwise one dead token means one identical failure
per episode per feed.

Halting stops *publishing only*. Downloading and processing carry on, so when
the token is replaced the already-processed episodes publish on the next run.

One `head_bucket` (Class B, free) runs before anything else in `publish_all`
so an unreachable endpoint is reported once, up front, rather than discovered
one object at a time.

A single failed upload is **not** treated this way: one flaky PUT retries next
run and the feed still goes out. Only the credential codes halt immediately;
otherwise it takes `R2_MAX_FAILURES_PER_RUN` failures to raise a warning.

A delete R2 *refuses* leaves the key live in the manifest rather than
tombstoned. It is still costing storage, so the next run should try again — and
the reconciliation pass below is what catches one that never succeeds.

## The Class A circuit breaker

`data/r2_breaker.json` + `skipcastify/services/r2_breaker.py`.

The alerting above is aimed at errors, but the likelier route to an unexpected
Cloudflare bill is runaway **success**: a loop that re-uploads the same three
feeds, a fingerprint that stops matching, a retry that never backs off. Every
log line in those cases says the upload worked.

So the operation counts are themselves a tripwire:

| limit | default | env |
|---|---|---|
| Class A per run | 200 | `R2_MAX_CLASS_A_PER_RUN` |
| Class A per UTC day | 2,000 | `R2_MAX_CLASS_A_PER_DAY` |

A normal pilot run spends single digits — three feed PUTs at most, plus an
episode or two — so 200 is roughly 20x headroom and still two orders of
magnitude below anything that costs money. Reconciliation's `LIST` pages count
toward the same totals.

On a trip: a marker is written, a high-priority alert goes out, and publishing
stops immediately for the rest of the run.

**Clearing it is manual**, by design:

```
uv run python -m skipcastify.tools.r2ctl clear-breaker
```

An automatic reset after an hour, or at midnight, would let a genuine runaway
spend its budget in daily instalments. A marker that exists but cannot be
parsed also counts as tripped — failing closed is the right way round here,
since a wrong "tripped" costs stale feeds and a wrong "fine" costs the bill
this exists to prevent.

While it is tripped, a publish run spends **nothing at all** — not even the
Class B `head_bucket` — and sends a default-priority reminder (throttled to
6h) rather than going quiet, because a disabled publisher that says so once is
exactly the silent failure the rest of this file is about.

Downloading and processing are untouched, so episodes accumulate locally and
publish on the first run after the breaker is cleared.

## Going live

Publishing is inert until `PUBLISH_TO_R2=true`, so none of this runs by
accident.

1. Generate the path secret and add it to `.env` — do not reuse `EPISODE_TOKEN`:
   ```
   python -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```
2. Set `PUBLISH_TO_R2=true` and point `SUBSCRIPTIONS` at the pilot OPML.
3. Install the units:
   ```
   sudo cp systemd/skipcastify-*.service systemd/skipcastify-*.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now skipcastify-pipeline.timer skipcastify-server
   ```
4. Confirm an episode is reachable and seekable — podcast apps need range
   requests, so a `206` here matters more than the `200`:
   ```
   curl -r 0-99 -o /dev/null -w '%{http_code}\n' "$PUBLIC_BASE_URL/audio/.../ep.mp3"
   ```
5. Remove `~/.skipcastify-maintenance` once the timer is running, so
   `monitor.sh` resumes its feed and pipeline-freshness alerts.

Feed URLs for subscribing are written to
`~/skipcastify-handoff/PILOT_FEED_URLS.txt` (mode 600). They contain the secret,
so they don't belong in the handoff log, a commit, or a chat window.
