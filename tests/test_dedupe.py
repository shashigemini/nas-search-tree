import json
import os

from nassearch import dedupe

MIB = 1 << 20


def _write(root, name, data):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _hits(tmp_path, records):
    meta = tmp_path / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    with open(meta / "hits.jsonl", "w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _record(path, categories=("documents",), is_dir=False):
    return {"path": path, "share_path": path.replace("/volume1", ""),
            "name": os.path.basename(path), "extension": path.rsplit(".", 1)[-1],
            "size": 0, "is_dir": is_dir, "categories": list(categories)}


def _manifest(tmp_path):
    with open(tmp_path / "_meta" / "manifest.jsonl") as fh:
        return [json.loads(line) for line in fh]


def test_unique_sizes_are_canonical_without_reading_any_bytes(tmp_path):
    root = str(tmp_path / "vol")
    paths = [_write(root, "f%d.doc" % i, b"x" * (100 + i)) for i in range(5)]
    _hits(tmp_path, [_record(p) for p in paths])

    stats = dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)

    assert stats["duplicates"] == 0
    assert len(_manifest(tmp_path)) == 5
    assert all(e["content_hash"].startswith("size:") for e in _manifest(tmp_path))


def test_same_size_different_content_is_separated_by_the_quick_key(tmp_path):
    root = str(tmp_path / "vol")
    a = _write(root, "a.doc", b"A" * 4096)
    b = _write(root, "b.doc", b"B" * 4096)
    _hits(tmp_path, [_record(a), _record(b)])

    dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    entries = _manifest(tmp_path)

    assert len(entries) == 2
    assert all(e["duplicate_count"] == 0 for e in entries)
    assert all(e["content_hash"].startswith("quick:") for e in entries)


def test_files_differing_only_in_the_middle_need_the_full_hash(tmp_path):
    """Identical first and last MiB -- only tier 3 can tell these apart."""
    root = str(tmp_path / "vol")
    head, tail = b"H" * MIB, b"T" * MIB
    a = _write(root, "a.bin", head + b"\x01" * MIB + tail)
    b = _write(root, "b.bin", head + b"\x02" * MIB + tail)
    _hits(tmp_path, [_record(a), _record(b)])

    dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    entries = _manifest(tmp_path)

    assert len(entries) == 2
    assert all(e["content_hash"].startswith("blake2b:") for e in entries)
    assert all(e["duplicate_count"] == 0 for e in entries)


def test_true_duplicates_collapse_to_one_canonical_entry(tmp_path):
    root = str(tmp_path / "vol")
    payload = b"Gandhi" * 1000
    short = _write(root, "Gandhi.doc", payload)
    deep = _write(root, "archive/copies/Gandhi (copy).doc", payload)
    deeper = _write(root, "archive/copies/more/Gandhi (copy 2).doc", payload)
    _hits(tmp_path, [_record(deep), _record(deeper), _record(short)])

    dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    entries = _manifest(tmp_path)

    assert len(entries) == 1
    entry = entries[0]
    assert entry["canonical_path"] == short  # shortest path wins
    assert entry["duplicate_count"] == 2

    with open(tmp_path / "_meta" / "duplicates.tsv") as fh:
        rows = [line.split("\t")[1].strip() for line in fh]
    assert sorted(rows) == sorted([deep, deeper])


def test_categories_of_every_copy_are_merged_onto_the_survivor(tmp_path):
    root = str(tmp_path / "vol")
    payload = b"same" * 500
    a = _write(root, "a.doc", payload)
    b = _write(root, "bb/b.doc", payload)
    _hits(tmp_path, [_record(a, ["documents"]), _record(b, ["documents", "other"])])

    dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    entry = _manifest(tmp_path)[0]
    assert entry["categories"] == ["documents", "other"]


def test_canonical_choice_is_stable_across_reruns(tmp_path):
    root = str(tmp_path / "vol")
    payload = b"z" * 2048
    paths = [_write(root, "d%d/same.doc" % i, payload) for i in range(4)]
    _hits(tmp_path, [_record(p) for p in paths])

    dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    first = _manifest(tmp_path)
    dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    assert _manifest(tmp_path) == first


def test_folders_are_carried_through_without_being_hashed(tmp_path):
    root = str(tmp_path / "vol")
    os.makedirs(os.path.join(root, "Gandhi Talks"))
    _hits(tmp_path, [_record(os.path.join(root, "Gandhi Talks"), [], is_dir=True)])

    dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    entry = _manifest(tmp_path)[0]
    assert entry["is_dir"] is True
    assert entry["categories"] == ["folders"]
    assert entry["content_hash"].startswith("dir:")


def test_paths_that_vanished_since_the_crawl_are_reported_not_fatal(tmp_path):
    root = str(tmp_path / "vol")
    alive = _write(root, "here.doc", b"data")
    _hits(tmp_path, [_record(alive), _record(os.path.join(root, "gone.doc"))])

    stats = dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    assert len(stats["missing"]) == 1
    assert len(_manifest(tmp_path)) == 1


def test_stale_index_sizes_are_re_stated_from_the_filesystem(tmp_path):
    root = str(tmp_path / "vol")
    path = _write(root, "grown.doc", b"x" * 999)
    record = _record(path)
    record["size"] = 12  # what a stale index claimed
    _hits(tmp_path, [record])

    dedupe.dedupe(str(tmp_path), workers=2, log=lambda *a: None)
    assert _manifest(tmp_path)[0]["size"] == 999
