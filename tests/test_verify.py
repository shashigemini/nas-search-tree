import json
import os

from nassearch import link, verify


def test_verify_passes_on_a_freshly_built_tree(tmp_path):
    entries = []
    for i in range(5):
        target = tmp_path / ("f%d.doc" % i)
        target.write_bytes(b"x" * i)
        entries.append({"content_hash": "h%d" % i, "canonical_path": str(target),
                        "share_path": "", "name": target.name, "size": i,
                        "mtime": 1, "categories": ["documents"],
                        "duplicate_count": 0, "is_dir": False})
    meta = tmp_path / "_meta"
    meta.mkdir()
    link.assign_names(entries, root=str(tmp_path))
    with open(meta / "manifest.jsonl", "w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: None)
    assert verify.verify(str(tmp_path), log=lambda *a: None) == []


def test_verify_reports_a_broken_link(tmp_path):
    target = tmp_path / "gone.doc"
    target.write_bytes(b"x")
    entry = {"content_hash": "h", "canonical_path": str(target), "share_path": "",
             "name": "gone.doc", "size": 1, "mtime": 1,
             "categories": ["documents"], "duplicate_count": 0, "is_dir": False}
    meta = tmp_path / "_meta"
    meta.mkdir()
    link.assign_names([entry], root=str(tmp_path))
    with open(meta / "manifest.jsonl", "w") as fh:
        fh.write(json.dumps(entry) + "\n")
    link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: None)

    os.remove(str(target))
    problems = verify.verify(str(tmp_path), log=lambda *a: None)
    assert any("broken symlink" in p for p in problems)


def test_verify_flags_unreachable_hits_from_the_crawl(tmp_path):
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "manifest.jsonl").write_text("")
    (meta / "slices.json").write_text(json.dumps(
        {"keyword": "gandhi", "cap": 10000, "totals": {"document": 15000},
         "lost": {"document": 5000}, "slices": []}))
    (tmp_path / "tree").mkdir()
    for name in link.CATEGORY_ORDER:
        (tmp_path / "tree" / name).mkdir()

    problems = verify.verify(str(tmp_path), log=lambda *a: None)
    assert any("unreachable" in p for p in problems)
