# nassearch — Synology Universal Search → deduplicated symlink tree

Turns a Universal Search keyword into a browsable tree of symlinks on the NAS,
with **one link per unique file content** and the same category facets the DSM
sidebar shows. Nothing is moved or copied; originals are never touched.

## Why it works the way it does

* It calls the exact API the DSM web client calls —
  `SYNO.Finder.FileIndexing.Search` v1 with the same `search_weight_list` and
  `file_type` values read off `/var/packages/SynoFinder/target/ui/Finder.js` —
  so the result set matches the sidebar counts you see in the browser.
* The search backend (`synoelasticd`) refuses `from + size` past ~10,000, but a
  single category here returns 56,340 hits. `crawl` recursively cuts the query
  (by `SYNOMDIsDir`, then `SYNOMDContentModificationDate`, then `SYNOMDFSSize`)
  until every leaf fits, and **checks that each cut's children still add up to
  the parent's total** so nothing is lost silently.
* Dedup runs on the NAS in three tiers — unique size (zero bytes read) → first
  and last 1 MiB → full hash — so only genuine suspects are read end to end.

## Run it

Everything runs on the NAS. Deploy with:

    rsync -a --delete --exclude .venv --exclude __pycache__ \
      ~/nas-search-tree/ nosh@192.168.1.14:~/.nas-search-tree/

Get a session: log into DSM in a browser, copy the `_sid` cookie, then

    ssh nosh@192.168.1.14
    cd ~/.nas-search-tree
    export DSM_SID='<paste>'
    python3 -m nassearch all --keyword gandhi --out ~/search-exports/gandhi

`all` runs every stage in order: **crawl → merge → dedupe → link → verify**.
Stages also run individually if you want to redo just one of them.

`crawl` is resumable — if the session expires, paste a fresh `_sid` and re-run;
it picks up at the page it stopped on.

## Running it as a job

Every stage writes to `<out>/_meta/run.log` (timestamped, line-buffered,
appended across runs) as well as to stdout, so a detached run stays visible:

    nohup python3 -m nassearch all --keyword gandhi \
      --out ~/search-exports/gandhi --quiet >/dev/null 2>&1 &

    tail -f ~/search-exports/gandhi/_meta/run.log

`--quiet` suppresses the stdout copy and logs only to the file. The two passes
that read real bytes (quick key, full hash) emit a heartbeat with a percentage,
GiB read, rate and ETA, so a long-running job never looks like a hang.

Exit codes: `0` success, `1` verification found a problem, `2` the DSM session
expired (re-export `DSM_SID` and re-run — the crawl resumes).

## What you get

    ~/search-exports/gandhi/
      tree/
        all/          every unique matched file
        documents/  photos/  music/  videos/     the sidebar's facets
        other/        matched files no facet claims (.zip, .eml, source, …)
        folders/      matched directories
      _meta/
        slices.json      crawl partition state + cursors (resume point)
        hits.jsonl       one line per unique matched path
        manifest.jsonl   one line per unique content hash (the canonical set)
        duplicates.tsv   content hash -> every redundant copy
        report.md        totals to compare against the DSM sidebar

Categories are facets, not partitions: `all/` ⊇ each typed directory, and the
same file is linked in both. A folder in `folders/` may contain files also
linked under `documents/` — the tree is a view, not a move.

Link names are the flattened path relative to `/volume1`
(`MEHERBABA__Archive__Talks__Gandhi.doc`). Names over the 255-byte filesystem
limit keep their head and tail and gain a hash of the full path.

## Tests

    python3 -m venv .venv && .venv/bin/pip install pytest
    .venv/bin/python -m pytest tests/ -q
