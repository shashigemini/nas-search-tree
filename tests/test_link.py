import json
import os

from nassearch import link


def _manifest(tmp_path, entries):
    meta = tmp_path / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    with open(meta / "manifest.jsonl", "w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _entry(path, categories=("documents",), is_dir=False, content_hash=None):
    return {"content_hash": content_hash or ("h:" + path), "canonical_path": path,
            "share_path": path, "name": os.path.basename(path), "size": 1,
            "mtime": 1, "categories": list(categories), "duplicate_count": 0,
            "is_dir": is_dir}


def test_flatten_makes_a_self_describing_name():
    assert link.flatten("/volume1/MEHERBABA/Archive/Talks/Gandhi.doc") == \
        "MEHERBABA__Archive__Talks__Gandhi.doc"


def test_overlong_names_are_truncated_but_stay_unique_and_stable():
    deep = "/volume1/" + "/".join("component-%02d-%s" % (i, "x" * 30) for i in range(12))
    name = link.flatten(deep)

    assert len(name.encode("utf-8")) <= link.NAME_MAX
    assert link.flatten(deep) == name                      # stable
    assert name != link.flatten(deep + "y")                # still distinguishing
    assert name.endswith(link.flatten(deep)[-8:])


def test_truncation_is_utf8_safe():
    deep = "/volume1/" + "/".join("बापू-%02d" % i for i in range(60))
    name = link.flatten(deep)
    assert len(name.encode("utf-8")) <= link.NAME_MAX
    name.encode("utf-8").decode("utf-8")  # must not have split a character


def test_names_that_would_collide_are_disambiguated():
    entries = [_entry("/volume1/a__b/c.doc"), _entry("/volume1/a/b__c.doc")]
    link.assign_names(entries)
    assert entries[0]["link_name"] != entries[1]["link_name"]
    assert entries[0]["link_name"] == "a__b__c.doc"


def test_a_file_is_linked_into_all_and_into_each_of_its_facets(tmp_path):
    target = tmp_path / "Gandhi.doc"
    target.write_bytes(b"x")
    _manifest(tmp_path, [_entry(str(target), ["documents", "other"])])

    counts = link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: None)

    tree = tmp_path / "tree"
    assert (tree / "all" / "Gandhi.doc").is_symlink()
    assert (tree / "documents" / "Gandhi.doc").is_symlink()
    assert (tree / "other" / "Gandhi.doc").is_symlink()
    assert counts["all"] == 1 and counts["documents"] == 1


def test_folders_land_only_under_folders(tmp_path):
    folder = tmp_path / "Gandhi Talks"
    folder.mkdir()
    _manifest(tmp_path, [_entry(str(folder), ["folders"], is_dir=True)])

    counts = link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: None)
    assert counts["folders"] == 1
    assert counts["all"] == 0
    assert (tmp_path / "tree" / "folders" / "Gandhi Talks").is_symlink()


def test_every_link_resolves_and_no_two_share_a_content_hash(tmp_path):
    entries = []
    for i in range(20):
        target = tmp_path / ("f%02d.doc" % i)
        target.write_bytes(b"x" * i)
        entries.append(_entry(str(target), ["documents"], content_hash="h%d" % i))
    _manifest(tmp_path, entries)

    link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: None)

    all_dir = tmp_path / "tree" / "all"
    links = sorted(all_dir.iterdir())
    assert len(links) == 20
    assert all(os.path.exists(os.readlink(str(p))) for p in links)
    assert len({os.readlink(str(p)) for p in links}) == 20


def test_rebuilding_is_idempotent(tmp_path):
    target = tmp_path / "Gandhi.doc"
    target.write_bytes(b"x")
    _manifest(tmp_path, [_entry(str(target))])

    def snapshot():
        tree = tmp_path / "tree"
        return sorted((str(p.relative_to(tree)), os.readlink(str(p)))
                      for p in tree.rglob("*") if p.is_symlink())

    link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: None)
    first = snapshot()
    link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: None)
    assert snapshot() == first


def test_a_failed_rebuild_leaves_the_previous_tree_intact(tmp_path):
    target = tmp_path / "Gandhi.doc"
    target.write_bytes(b"x")
    _manifest(tmp_path, [_entry(str(target))])
    link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: None)

    # A manifest naming an entry we cannot link (bad category is skipped, but a
    # crash mid-build must not destroy the live tree).
    broken = dict(_entry(str(target)))
    broken["categories"] = ["documents"]
    _manifest(tmp_path, [broken])
    original = os.readlink(str(tmp_path / "tree" / "all" / "Gandhi.doc"))

    try:
        link.build(str(tmp_path), root=str(tmp_path), log=lambda *a: 1 / 0)
    except ZeroDivisionError:
        pass

    assert (tmp_path / "tree" / "all" / "Gandhi.doc").is_symlink()
    assert os.readlink(str(tmp_path / "tree" / "all" / "Gandhi.doc")) == original
