"""End-to-end over the fake backend: search -> dedupe -> tree -> verify."""

import os

from fake_api import FakeFinder
from nassearch import crawl, dedupe, link, report, verify


def test_full_pipeline_produces_a_verified_deduplicated_tree(tmp_path):
    vol = tmp_path / "vol"
    (vol / "MEHERBABA" / "archive").mkdir(parents=True)

    payload = b"Gandhi and Meher Baba" * 100
    canonical = vol / "MEHERBABA" / "Gandhi.doc"
    canonical.write_bytes(payload)
    (vol / "MEHERBABA" / "archive" / "Gandhi (copy).doc").write_bytes(payload)
    (vol / "MEHERBABA" / "clip.mp4").write_bytes(b"video bytes")
    (vol / "MEHERBABA" / "box.zip").write_bytes(b"zip bytes")
    (vol / "MEHERBABA" / "Gandhi Talks").mkdir()

    docs = [
        {"path": str(canonical), "size": len(payload), "mtime": 100,
         "is_dir": False, "file_types": ["document"]},
        {"path": str(vol / "MEHERBABA" / "archive" / "Gandhi (copy).doc"),
         "size": len(payload), "mtime": 200, "is_dir": False,
         "file_types": ["document"]},
        {"path": str(vol / "MEHERBABA" / "clip.mp4"), "size": 11, "mtime": 300,
         "is_dir": False, "file_types": ["video"]},
        {"path": str(vol / "MEHERBABA" / "box.zip"), "size": 9, "mtime": 400,
         "is_dir": False, "file_types": []},
        {"path": str(vol / "MEHERBABA" / "Gandhi Talks"), "size": 0, "mtime": 500,
         "is_dir": True, "file_types": []},
    ]
    api = FakeFinder(docs, cap=10000)
    out = str(tmp_path / "export")

    crawl.crawl(api, "gandhi", out, page_size=100, log=lambda *a: None)
    crawl.merge(out, log=lambda *a: None)
    dedupe.dedupe(out, workers=2, log=lambda *a: None)
    counts = link.build(out, root=str(vol), log=lambda *a: None)

    assert verify.verify(out, log=print) == []

    # 4 files -> 3 unique contents, plus 1 folder
    assert counts["all"] == 3
    assert counts["documents"] == 1
    assert counts["videos"] == 1
    assert counts["other"] == 1
    assert counts["folders"] == 1

    tree = os.path.join(out, "tree")
    doc_link = os.path.join(tree, "documents", "MEHERBABA__Gandhi.doc")
    assert os.path.islink(doc_link)
    assert os.readlink(doc_link) == str(canonical)  # shortest path won

    with open(os.path.join(out, "_meta", "duplicates.tsv")) as fh:
        assert "Gandhi (copy).doc" in fh.read()

    body = open(report.write_report(out, counts)).read()
    assert "Dedup" in body and "reduction" in body
