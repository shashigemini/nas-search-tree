"""Post-build assertions: does the tree actually say what we claim it says?"""

import json
import os

from .link import CATEGORY_ORDER


def verify(out_dir, log=print):
    meta = os.path.join(out_dir, "_meta")
    tree = os.path.join(out_dir, "tree")
    problems = []

    with open(os.path.join(meta, "manifest.jsonl")) as fh:
        entries = [json.loads(line) for line in fh]
    by_name = {e["link_name"]: e for e in entries if "link_name" in e}

    broken, seen_hash = [], {}
    for category in CATEGORY_ORDER:
        directory = os.path.join(tree, category)
        if not os.path.isdir(directory):
            problems.append("missing category directory: %s" % category)
            continue
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if not os.path.exists(path):
                broken.append(path)
            if category == "all":
                entry = by_name.get(name)
                if entry is None:
                    problems.append("link not in manifest: %s" % path)
                    continue
                if entry["content_hash"] in seen_hash:
                    problems.append("duplicate content in all/: %s and %s"
                                    % (seen_hash[entry["content_hash"]], name))
                seen_hash[entry["content_hash"]] = name

    if broken:
        problems.append("%d broken symlink(s), e.g. %s" % (len(broken), broken[0]))

    # Facets are subsets of all/, never a superset.
    all_names = set(os.listdir(os.path.join(tree, "all"))) if os.path.isdir(
        os.path.join(tree, "all")) else set()
    for category in ("documents", "photos", "music", "videos", "other"):
        directory = os.path.join(tree, category)
        if not os.path.isdir(directory):
            continue
        stray = set(os.listdir(directory)) - all_names
        if stray:
            problems.append("%s has %d entries absent from all/, e.g. %s"
                            % (category, len(stray), sorted(stray)[0]))

    state_path = os.path.join(meta, "slices.json")
    if os.path.exists(state_path):
        with open(state_path) as fh:
            state = json.load(fh)
        for label, lost in (state.get("lost") or {}).items():
            if lost:
                problems.append("%s: %d hits were unreachable under the "
                                "pagination cap" % (label, lost))

    for problem in problems:
        log("FAIL  " + problem)
    if not problems:
        log("OK    %d unique entries, %d links in all/, no broken links"
            % (len(entries), len(all_names)))
    return problems
