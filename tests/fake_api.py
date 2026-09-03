"""An in-memory stand-in for the search backend.

It enforces the one behaviour that matters for correctness: a hard
deep-pagination ceiling, exactly like synoelasticd.
"""

import json


class PaginationCapExceeded(Exception):
    pass


class FakeFinder:
    """Documents are dicts with path/size/mtime/is_dir/file_types."""

    def __init__(self, documents, cap=10000):
        self.documents = documents
        self.cap = cap
        self.calls = 0

    def _matches(self, doc, file_type, criteria_list):
        if file_type and file_type not in doc["file_types"]:
            return False
        for criterion in criteria_list or []:
            field, value = criterion["field"], criterion["value"]
            if field == "SYNOMDIsDir":
                if (value == "Y") != doc["is_dir"]:
                    return False
            elif field in ("SYNOMDContentModificationDate", "SYNOMDFSSize"):
                key = "mtime" if field.endswith("Date") else "size"
                lo, hi = value.strip("[]").split(" TO ")
                if doc.get(key) is None:
                    return False  # a field the document simply does not have
                if not int(lo) <= doc[key] <= int(hi):
                    return False
        return True

    def _hits(self, file_type, criteria_list):
        return [d for d in self.documents if self._matches(d, file_type, criteria_list)]

    def search(self, keyword, file_type="", frm=0, size=200, criteria_list=None):
        self.calls += 1
        if frm + size > self.cap:
            raise PaginationCapExceeded("from+size=%d exceeds %d" % (frm + size, self.cap))
        hits = self._hits(file_type, criteria_list)
        page = [{
            "SYNOMDPath": d["path"],
            "SYNOMDSharePath": d["path"].replace("/volume1", ""),
            "SYNOMDFSName": d["path"].rsplit("/", 1)[-1],
            "SYNOMDExtension": d["path"].rsplit(".", 1)[-1],
            "SYNOMDFSSize": d["size"],
            "SYNOMDIsDir": "y" if d["is_dir"] else "n",
        } for d in hits[frm:frm + size]]
        return {"total": len(hits), "hits": page}

    def total(self, keyword, file_type="", criteria_list=None):
        self.calls += 1
        return len(self._hits(file_type, criteria_list))
