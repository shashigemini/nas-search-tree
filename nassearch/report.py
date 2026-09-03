"""The parity check: does what we built actually match what the UI shows?"""

import json
import os


def write_report(out_dir, counts=None):
    meta = os.path.join(out_dir, "_meta")
    lines = ["# Universal Search export", ""]

    state_path = os.path.join(meta, "slices.json")
    if os.path.exists(state_path):
        with open(state_path) as fh:
            state = json.load(fh)
        lines += ["## Crawl", "",
                  "Keyword: `%s`  |  pagination cap: %d" % (state["keyword"], state["cap"]),
                  "", "| category (`file_type`) | API total | unreachable |",
                  "|---|---|---|"]
        for label, total in state["totals"].items():
            lost = state.get("lost", {}).get(label, 0)
            lines.append("| %s | %d | %s |" % (label, total, lost or "-"))
        truncated = [s for s in state["slices"] if s.get("truncated")]
        lines += ["", "Slices: %d, of which truncated: %d"
                  % (len(state["slices"]), len(truncated)), ""]
        lines.append("Compare the totals above against the counts in the DSM "
                     "sidebar. They must match exactly; any `unreachable` value "
                     "means the bisection could not get under the pagination "
                     "cap and the export is incomplete.")
        lines.append("")

    manifest_path = os.path.join(meta, "manifest.jsonl")
    if os.path.exists(manifest_path):
        unique = dupes = dirs = 0
        with open(manifest_path) as fh:
            for line in fh:
                entry = json.loads(line)
                unique += 1
                dupes += entry["duplicate_count"]
                dirs += 1 if entry["is_dir"] else 0
        total = unique + dupes
        lines += ["## Dedup", "",
                  "- unique entries: **%d** (%d folders)" % (unique, dirs),
                  "- redundant copies suppressed: **%d**" % dupes,
                  "- reduction: %.1f%%" % (100.0 * dupes / total if total else 0.0),
                  ""]

    if counts:
        lines += ["## Tree", "", "| directory | links |", "|---|---|"]
        lines += ["| %s | %d |" % (name, n) for name, n in counts.items()]
        lines.append("")

    path = os.path.join(meta, "report.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
    return path
