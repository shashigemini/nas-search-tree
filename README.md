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

The preferred method is a **local DSM login**. It creates the session from the
NAS itself, so it avoids DSM's source-IP binding and never writes your password
to disk:

    ssh nosh@192.168.1.14
    cd ~/.nas-search-tree
    python3 -m nassearch all --account nosh --keyword gandhi --out ~/search-exports/gandhi

The command securely prompts for the DSM password. If DSM two-factor
authentication is enabled, add `--otp-code 123456`. Existing `DSM_SID` / `--sid`
usage remains supported, but a browser SID from another computer cannot be used
by a process running on the NAS because DSM binds it to the request source IP.

For a detached job, obtain a NAS-local session and export it in the same shell:

    eval "$(python3 -m nassearch login --account nosh --shell)"
    nohup python3 -m nassearch all --keyword gandhi --out ~/search-exports/gandhi --quiet >/dev/null 2>&1 &

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
        skipped.txt      files that matched but could not be read
        run.log          timestamped progress, appended across runs
        report.md        totals to compare against the DSM sidebar

Categories are facets, not partitions: `all/` ⊇ each typed directory, and the
same file is linked in both. A folder in `folders/` may contain files also
linked under `documents/` — the tree is a view, not a move.

Link names are the flattened path relative to `/volume1`
(`MEHERBABA__Archive__Talks__Gandhi.doc`). Names over the 255-byte filesystem
limit keep their head and tail and gain a hash of the full path.

## Privileges

No sudo, and no root-only interfaces. The search index (`synoelasticd`,
`fileindexd`) is readable only by root, so the tool goes through the HTTP
WebAPI as a logged-in DSM user instead — you see exactly what that account can
see. Hashing needs ordinary read permission on the matched files; anything it
cannot read is written to `_meta/skipped.txt` and makes `verify` fail, so an
incomplete export is never mistaken for a complete one.

## Tests

    python3 -m venv .venv && .venv/bin/pip install pytest
    .venv/bin/python -m pytest tests/ -q
