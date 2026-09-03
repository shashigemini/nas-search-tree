"""Stage 3: turn the manifest into a browsable symlink tree.

The tree is a view, never a move: originals are untouched.  Categories are
facets rather than partitions -- `all/` holds every unique file and each typed
directory holds its subset -- because that is exactly what `file_type` means to
the search backend, and it is what the DSM sidebar shows.
"""

import errno
import hashlib
import json
import os
import shutil

VOLUME_ROOT = "/volume1"
NAME_MAX = 255            # Linux single-component limit, in bytes
_HEAD_BYTES, _TAIL_BYTES = 60, 150

CATEGORY_ORDER = ["all", "documents", "photos", "music", "videos", "other", "folders"]


def _truncate_bytes(text, limit, from_end=False):
    """Cut a str to `limit` UTF-8 bytes without splitting a character."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    cut = raw[-limit:] if from_end else raw[:limit]
    return cut.decode("utf-8", "ignore")


def flatten(path, root=VOLUME_ROOT):
    """/volume1/MEHERBABA/Talks/Gandhi.doc -> MEHERBABA__Talks__Gandhi.doc

    Self-describing and, for ordinary paths, collision-free.  Names that would
    exceed the 255-byte filesystem limit keep their head and their tail (so the
    extension survives) and gain a hash of the full path, which keeps them
    unique and stable across runs.
    """
    relative = os.path.relpath(path, root) if path.startswith(root + os.sep) else path.lstrip("/")
    parts = [p.replace("\0", "").replace("\n", " ") for p in relative.split("/") if p]
    name = "__".join(parts)
    if len(name.encode("utf-8")) <= NAME_MAX:
        return name
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
    return "%s~%s~%s" % (_truncate_bytes(name, _HEAD_BYTES),
                         _truncate_bytes(name, _TAIL_BYTES, from_end=True),
                         digest)


def assign_names(entries, root=VOLUME_ROOT):
    """Give every entry a link_name, breaking any residual collision.

    A collision needs two distinct paths whose components differ only by where
    the separators fall -- rare, but not impossible, so it is handled rather
    than assumed away.  Entries arrive sorted by path, so the outcome is stable.
    """
    taken = {}
    for entry in entries:
        name = flatten(entry["canonical_path"], root)
        if name in taken:
            digest = hashlib.sha1(entry["canonical_path"].encode("utf-8")).hexdigest()[:8]
            stem, dot, ext = name.rpartition(".")
            name = ("%s~%s.%s" % (stem, digest, ext)) if dot else ("%s~%s" % (name, digest))
        taken[name] = entry["canonical_path"]
        entry["link_name"] = name
    return entries


def _symlink(target, link_path):
    try:
        os.symlink(target, link_path)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise


def build(out_dir, root=VOLUME_ROOT, log=print):
    """Build into a staging directory, then swap it in atomically.

    An interrupted run therefore never leaves a half-built tree where the old
    complete one used to be.
    """
    meta = os.path.join(out_dir, "_meta")
    manifest_path = os.path.join(meta, "manifest.jsonl")
    with open(manifest_path) as fh:
        entries = [json.loads(line) for line in fh]
    assign_names(entries, root)
    # Persist the names, so `verify` and anyone reading the manifest can map a
    # link in the tree back to the file it stands for.
    with open(manifest_path, "w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")

    staging = os.path.join(out_dir, ".tree.new")
    if os.path.exists(staging):
        shutil.rmtree(staging)
    for category in CATEGORY_ORDER:
        os.makedirs(os.path.join(staging, category))

    counts = {category: 0 for category in CATEGORY_ORDER}
    for entry in entries:
        target = entry["canonical_path"]
        name = entry["link_name"]
        categories = list(entry["categories"])
        if not entry["is_dir"]:
            categories = ["all"] + [c for c in categories if c != "all"]
        for category in categories:
            if category not in counts:
                continue
            _symlink(target, os.path.join(staging, category, name))
            counts[category] += 1

    live = os.path.join(out_dir, "tree")
    previous = os.path.join(out_dir, ".tree.old")
    if os.path.exists(previous):
        shutil.rmtree(previous)
    if os.path.exists(live):
        os.rename(live, previous)
    os.rename(staging, live)
    if os.path.exists(previous):
        shutil.rmtree(previous)

    for category in CATEGORY_ORDER:
        log("  %-10s %d" % (category, counts[category]))
    return counts
