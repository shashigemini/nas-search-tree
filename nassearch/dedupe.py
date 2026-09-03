"""Stage 2: collapse the hit set to one entry per unique file content.

Runs on the NAS -- 22 TB must never cross the LAN.  Three tiers, each one
shrinking the work for the next:

  1. size          two files of different length cannot be duplicates, so a
                   file with a unique size is canonical having read zero bytes
  2. quick key     first + last 1 MiB, which separates same-size media files
                   without reading the middle
  3. full hash     only for files that survived both, i.e. genuine suspects
"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

from .runlog import Progress

CHUNK = 1 << 20  # 1 MiB
READ_BLOCK = 1 << 20


def _digest(path, spans):
    """Hash `spans` of a file. A span of None means 'to the end'."""
    hasher = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as fh:
        for offset, length in spans:
            fh.seek(offset)
            remaining = length
            while remaining is None or remaining > 0:
                want = READ_BLOCK if remaining is None else min(READ_BLOCK, remaining)
                block = fh.read(want)
                if not block:
                    break
                hasher.update(block)
                if remaining is not None:
                    remaining -= len(block)
    return hasher.hexdigest()


def quick_key(path, size):
    if size <= 2 * CHUNK:
        return _digest(path, [(0, None)])
    return _digest(path, [(0, CHUNK), (size - CHUNK, CHUNK)])


def full_hash(path, size=None):
    return _digest(path, [(0, None)])


def _group_by(records, keyfn, workers, compute=None, failures=None):
    """Bucket records by a key, computing it in parallel when it costs I/O.

    A record whose key could not be computed -- an unreadable file, typically --
    is collected in `failures` rather than dropped. Losing a file silently would
    leave an export that looks healthy while being incomplete.
    """
    groups = {}
    if compute is None:
        for record in records:
            groups.setdefault(keyfn(record), []).append(record)
        return groups
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for record, key in zip(records, pool.map(compute, records)):
            if key is None:
                if failures is not None:
                    failures.append(record["path"])
                continue
            groups.setdefault(key, []).append(record)
    return groups


def _canonical(records):
    """Deterministic pick, so reruns produce an identical tree."""
    return min(records, key=lambda r: (len(r["path"]), r["mtime"], r["path"]))


def dedupe(out_dir, workers=4, log=print):
    meta = os.path.join(out_dir, "_meta")
    hits_path = os.path.join(meta, "hits.jsonl")
    manifest_path = os.path.join(meta, "manifest.jsonl")
    duplicates_path = os.path.join(meta, "duplicates.tsv")

    files, dirs, missing = [], [], []
    with open(hits_path) as fh:
        for line in fh:
            record = json.loads(line)
            if record["is_dir"]:
                dirs.append(record)
                continue
            try:
                stat = os.lstat(record["path"])
            except OSError:
                missing.append(record["path"])
                continue
            if not os.path.isfile(record["path"]):
                missing.append(record["path"])
                continue
            # Trust the filesystem, not the index -- the index can be stale.
            record["size"] = stat.st_size
            record["mtime"] = int(stat.st_mtime)
            files.append(record)

    log("%d files, %d folders, %d missing/unreadable" % (len(files), len(dirs), len(missing)))

    by_size = _group_by(files, lambda r: r["size"], workers)
    unique_size = [g[0] for g in by_size.values() if len(g) == 1]
    contested = [r for g in by_size.values() if len(g) > 1 for r in g]
    log("tier 1 (size): %d unique with zero bytes read, %d to inspect"
        % (len(unique_size), len(contested)))

    quick_progress = Progress(log, "quick key", len(contested))

    def _quick(record):
        try:
            return "quick:%d:%s" % (record["size"],
                                    quick_key(record["path"], record["size"]))
        except OSError:
            return None
        finally:
            quick_progress.advance(min(record["size"], 2 * CHUNK))

    unreadable = []
    by_quick = _group_by(contested, None, workers, compute=_quick,
                         failures=unreadable)
    quick_unique = [g[0] for g in by_quick.values() if len(g) == 1]
    suspects = [r for g in by_quick.values() if len(g) > 1 for r in g]
    log("tier 2 (quick key): %d resolved, %d need a full hash"
        % (len(quick_unique), len(suspects)))

    full_progress = Progress(log, "full hash", len(suspects))

    def _full(record):
        try:
            return full_hash(record["path"])
        except OSError:
            return None
        finally:
            full_progress.advance(record["size"])

    by_hash = _group_by(suspects, None, workers, compute=_full,
                        failures=unreadable)
    log("tier 3 (full hash): %d distinct contents among %d suspects"
        % (len(by_hash), len(suspects)))

    entries, duplicates = [], []

    def _emit(records, content_hash):
        winner = _canonical(records)
        categories = sorted({c for r in records for c in r["categories"]})
        entries.append({
            "content_hash": content_hash,
            "canonical_path": winner["path"],
            "share_path": winner["share_path"],
            "name": winner["name"],
            "size": winner["size"],
            "mtime": winner["mtime"],
            "categories": categories,
            "duplicate_count": len(records) - 1,
            "is_dir": False,
        })
        for record in records:
            if record is not winner:
                duplicates.append((content_hash, record["path"]))

    # Tiers 1 and 2 proved uniqueness without a full hash; label them by how
    # they were resolved so the manifest stays honest about what was read.
    for record in unique_size:
        _emit([record], "size:%d" % record["size"])
    for key, group in by_quick.items():
        if len(group) == 1:
            _emit(group, key)  # already proven unique; never re-read the file
    for content_hash, group in by_hash.items():
        _emit(group, "blake2b:" + content_hash)

    for record in dirs:
        entries.append({
            "content_hash": "dir:" + record["path"],
            "canonical_path": record["path"],
            "share_path": record["share_path"],
            "name": record["name"],
            "size": 0,
            "mtime": 0,
            "categories": ["folders"],
            "duplicate_count": 0,
            "is_dir": True,
        })

    entries.sort(key=lambda e: e["canonical_path"])
    with open(manifest_path, "w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    with open(duplicates_path, "w") as fh:
        for content_hash, path in sorted(duplicates):
            fh.write("%s\t%s\n" % (content_hash, path))

    skipped = missing + unreadable
    with open(os.path.join(meta, "skipped.txt"), "w") as fh:
        for path in sorted(missing):
            fh.write("missing\t%s\n" % path)
        for path in sorted(unreadable):
            fh.write("unreadable\t%s\n" % path)

    log("manifest: %d unique entries, %d redundant copies"
        % (len(entries), len(duplicates)))
    if skipped:
        log("!! %d file(s) could not be read and are ABSENT from the tree "
            "(%d missing, %d unreadable) -- see _meta/skipped.txt"
            % (len(skipped), len(missing), len(unreadable)))
    return {"entries": len(entries), "duplicates": len(duplicates),
            "missing": missing, "unreadable": unreadable,
            "files": len(files), "dirs": len(dirs)}
