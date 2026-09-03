"""Stage 1: drive the search API until every matching hit has been retrieved.

The hard part is not pagination, it is the backend's deep-pagination ceiling.
synoelasticd is Elasticsearch-shaped and refuses `from + size` beyond ~10k, but
a single category here reports 56,340 hits.  A naive paging loop would not crash
so much as quietly stop, leaving a tree that silently omits most of the corpus.

So we recursively cut the query into slices until every leaf is under the
ceiling, and we verify each cut by checking that the children's totals still add
up to the parent's.  A cut that loses hits is rejected rather than trusted.
"""

import json
import os

from .api import CATEGORY_DIRS, FILE_TYPES

DEFAULT_CAP = 10000

# The UI's own open-ended upper bound (Finder.js TwinDateChooser).
DATE_MIN, DATE_MAX = 0, 2147483647
SIZE_MIN, SIZE_MAX = 0, 1 << 48

# Bisection dimensions, tried in this order.  Each is fully general: none needs
# a vocabulary of possible values, so none can leave an untestable remainder.
DIM_ISDIR = "isdir"
DIM_MTIME = "mtime"
DIM_SIZE = "size"
DIM_ORDER = [DIM_ISDIR, DIM_MTIME, DIM_SIZE]


def make_slice(file_type, criteria=None, ranges=None, used=None, skip=None):
    return {
        "file_type": file_type,
        "criteria": list(criteria or []),
        "ranges": dict(ranges or {}),
        "used": list(used or []),   # dimensions already cut on
        "skip": list(skip or []),   # dimensions proven to lose hits here
    }


def slice_key(sl):
    return json.dumps([sl["file_type"], sl["criteria"]], sort_keys=True)


def _range_criteria(field, lo, hi):
    return {"field": field, "value": "[%d TO %d]" % (lo, hi)}


def _with(sl, dim, criterion):
    return make_slice(
        sl["file_type"],
        sl["criteria"] + [criterion],
        sl["ranges"],
        sl["used"] + ([dim] if dim not in sl["used"] else []),
        sl["skip"],
    )


def _replace_range(sl, dim, field, lo, hi):
    """Narrow an existing range criterion rather than stacking a second one."""
    criteria = [c for c in sl["criteria"] if c["field"] != field]
    criteria.append(_range_criteria(field, lo, hi))
    ranges = dict(sl["ranges"])
    ranges[dim] = [lo, hi]
    used = list(sl["used"])
    if dim not in used:
        used.append(dim)
    return make_slice(sl["file_type"], criteria, ranges, used, sl["skip"])


def candidate_children(sl):
    """The next cut to try, as a list of child slices (empty if none is left)."""
    for dim in DIM_ORDER:
        if dim in sl["skip"]:
            continue
        if dim == DIM_ISDIR:
            if dim in sl["used"]:
                continue
            return [
                _with(sl, dim, {"field": "SYNOMDIsDir", "value": "Y"}),
                _with(sl, dim, {"field": "SYNOMDIsDir", "value": "N"}),
            ]

        field, bounds = {
            DIM_MTIME: ("SYNOMDContentModificationDate", (DATE_MIN, DATE_MAX)),
            DIM_SIZE: ("SYNOMDFSSize", (SIZE_MIN, SIZE_MAX)),
        }[dim]
        lo, hi = sl["ranges"].get(dim, bounds)
        if lo >= hi:
            continue  # exhausted: this dimension can no longer be halved
        mid = (lo + hi) // 2
        return [
            _replace_range(sl, dim, field, lo, mid),
            _replace_range(sl, dim, field, mid + 1, hi),
        ]
    return []


def plan_slices(client, keyword, file_type, cap=DEFAULT_CAP, log=None):
    """Cut `file_type` into leaf slices that each fit under `cap`.

    Returns (leaves, root_total, lost) where `lost` is the number of hits the
    bisection could not account for -- non-zero means the result is incomplete
    and the caller must say so loudly.
    """
    log = log or (lambda msg: None)
    root = make_slice(file_type)
    root_total = client.total(keyword, file_type, root["criteria"])
    leaves, lost = [], 0
    stack = [(root, root_total)]

    while stack:
        sl, total = stack.pop()
        if total <= cap:
            if total:
                sl["total"] = total
                leaves.append(sl)
            continue

        children = candidate_children(sl)
        if not children:
            log("  ! slice cannot be split further; %d of %d hits unreachable"
                % (total - cap, total))
            sl["total"] = cap
            sl["truncated"] = True
            leaves.append(sl)
            lost += total - cap
            continue

        child_totals = [client.total(keyword, file_type, c["criteria"])
                        for c in children]
        if sum(child_totals) < total:
            # The cut dropped hits (a field some documents simply do not have).
            # Don't trust it -- mark the dimension spent and try the next one.
            dim = children[0]["used"][-1]
            log("  ~ cut on %s lost %d hits; trying the next dimension"
                % (dim, total - sum(child_totals)))
            stack.append((make_slice(sl["file_type"], sl["criteria"], sl["ranges"],
                                     sl["used"], sl["skip"] + [dim]), total))
            continue

        for child, child_total in zip(children, child_totals):
            stack.append((child, child_total))

    return leaves, root_total, lost


def _paginate(client, keyword, sl, page_size, cap, start, on_hit, log):
    """Walk one leaf slice, yielding raw hits. Returns hits consumed."""
    frm = start
    seen = 0
    while frm < min(sl["total"], cap):
        size = min(page_size, cap - frm)
        data = client.search(keyword, sl["file_type"], frm, size, sl["criteria"])
        hits = data.get("hits") or []
        if not hits:
            break
        for hit in hits:
            on_hit(hit)
        seen += len(hits)
        frm += len(hits)
    return frm


def crawl(client, keyword, out_dir, cap=DEFAULT_CAP, page_size=200,
          file_types=None, log=print):
    """Run every category pass and append raw hits to _meta/raw.jsonl.

    Resumable: slices.json records each leaf's cursor, so an interrupted run
    picks up on the page it stopped at.
    """
    meta = os.path.join(out_dir, "_meta")
    os.makedirs(meta, exist_ok=True)
    state_path = os.path.join(meta, "slices.json")
    raw_path = os.path.join(meta, "raw.jsonl")

    state = {"keyword": keyword, "cap": cap, "totals": {}, "lost": {}, "slices": []}
    if os.path.exists(state_path):
        with open(state_path) as fh:
            prev = json.load(fh)
        if prev.get("keyword") == keyword and prev.get("cap") == cap:
            state = prev
            log("resuming from %s" % state_path)
        else:
            os.path.exists(raw_path) and os.remove(raw_path)

    done = {s["key"]: s for s in state["slices"]}

    for file_type in (file_types if file_types is not None else FILE_TYPES):
        label = file_type or "all"
        if label in state["totals"] and all(
                s.get("complete") for s in state["slices"]
                if s["file_type"] == file_type):
            log("%s: already complete (%d hits)" % (label, state["totals"][label]))
            continue

        log("planning %s ..." % label)
        leaves, total, lost = plan_slices(client, keyword, file_type, cap, log)
        state["totals"][label] = total
        state["lost"][label] = lost
        log("%s: total=%d, %d slice(s)%s"
            % (label, total, len(leaves), ", LOST %d" % lost if lost else ""))

        with open(raw_path, "a") as raw:
            for index, leaf in enumerate(leaves, 1):
                key = slice_key(leaf)
                record = done.get(key)
                if record and record.get("complete"):
                    continue
                start = record["cursor"] if record else 0

                def on_hit(hit, _ft=file_type):
                    hit["_file_type"] = _ft
                    raw.write(json.dumps(hit, separators=(",", ":")) + "\n")

                cursor = _paginate(client, keyword, leaf, page_size, cap,
                                   start, on_hit, log)
                raw.flush()
                record = {"key": key, "file_type": file_type,
                          "criteria": leaf["criteria"], "total": leaf["total"],
                          "cursor": cursor, "complete": True,
                          "truncated": leaf.get("truncated", False)}
                done[key] = record
                state["slices"] = list(done.values())
                with open(state_path, "w") as fh:
                    json.dump(state, fh, indent=2)
                log("  [%d/%d] %s: %d hits" % (index, len(leaves), label, cursor))

    return state


def merge(out_dir, log=print):
    """Fold raw.jsonl (one line per API hit, per category pass) into hits.jsonl
    (one line per unique path, carrying the set of categories it appeared in)."""
    meta = os.path.join(out_dir, "_meta")
    raw_path = os.path.join(meta, "raw.jsonl")
    hits_path = os.path.join(meta, "hits.jsonl")

    merged = {}
    with open(raw_path) as fh:
        for line in fh:
            hit = json.loads(line)
            path = hit.get("SYNOMDPath")
            if not path:
                continue
            record = merged.get(path)
            if record is None:
                record = merged[path] = {
                    "path": path,
                    "share_path": hit.get("SYNOMDSharePath"),
                    "name": hit.get("SYNOMDFSName"),
                    "extension": hit.get("SYNOMDExtension"),
                    "size": int(hit.get("SYNOMDFSSize") or 0),
                    "is_dir": hit.get("SYNOMDIsDir") == "y",
                    "categories": [],
                }
            file_type = hit.get("_file_type")
            category = CATEGORY_DIRS.get(file_type)
            if category and category not in record["categories"]:
                record["categories"].append(category)

    for record in merged.values():
        # A file the typed passes never claimed still belongs somewhere.
        if not record["is_dir"] and not record["categories"]:
            record["categories"] = ["other"]

    with open(hits_path, "w") as fh:
        for record in sorted(merged.values(), key=lambda r: r["path"]):
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")

    log("merged %d unique paths -> %s" % (len(merged), hits_path))
    return len(merged)
