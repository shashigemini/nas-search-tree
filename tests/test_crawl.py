import json
import random

import pytest

from fake_api import FakeFinder, PaginationCapExceeded
from nassearch import crawl


def _corpus(n, file_types=("document",), seed=1, mtime=None, size=None):
    rng = random.Random(seed)
    return [{
        "path": "/volume1/MEHERBABA/f%05d.doc" % i,
        "size": size if size is not None else rng.randrange(1, 10 ** 7),
        "mtime": mtime if mtime is not None else rng.randrange(1, 2 ** 31 - 1),
        "is_dir": False,
        "file_types": list(file_types),
    } for i in range(n)]


def test_small_result_set_is_a_single_slice():
    api = FakeFinder(_corpus(50), cap=10000)
    leaves, total, lost = crawl.plan_slices(api, "gandhi", "document", cap=10000)
    assert total == 50 and lost == 0
    assert len(leaves) == 1 and leaves[0]["criteria"] == []


def test_oversized_result_set_is_bisected_until_every_leaf_fits():
    """The guard against silent truncation: 56,340 hits, a 10k ceiling."""
    api = FakeFinder(_corpus(56340), cap=10000)
    leaves, total, lost = crawl.plan_slices(api, "gandhi", "document", cap=10000)

    assert total == 56340
    assert lost == 0
    assert len(leaves) > 1
    assert all(leaf["total"] <= 10000 for leaf in leaves)
    assert sum(leaf["total"] for leaf in leaves) == total


def test_bisected_crawl_retrieves_every_document_exactly_once(tmp_path):
    api = FakeFinder(_corpus(25000), cap=10000)
    crawl.crawl(api, "gandhi", str(tmp_path), cap=10000, page_size=500,
                file_types=["document"], log=lambda *a: None)
    crawl.merge(str(tmp_path), log=lambda *a: None)

    with open(tmp_path / "_meta" / "hits.jsonl") as fh:
        paths = [json.loads(line)["path"] for line in fh]
    assert len(paths) == 25000
    assert len(set(paths)) == 25000
    assert set(paths) == {d["path"] for d in api.documents}


def test_pagination_never_asks_the_backend_past_its_ceiling(tmp_path):
    """The fake raises if from+size exceeds the cap, so this would blow up."""
    api = FakeFinder(_corpus(25000), cap=10000)
    crawl.crawl(api, "gandhi", str(tmp_path), cap=10000, page_size=1000,
                file_types=["document"], log=lambda *a: None)  # must not raise


def test_identical_mtimes_fall_through_to_the_size_dimension():
    """A bulk copy gives thousands of files one timestamp; date cuts can't help."""
    api = FakeFinder(_corpus(30000, mtime=1600000000), cap=10000)
    leaves, total, lost = crawl.plan_slices(api, "gandhi", "document", cap=10000)
    assert lost == 0
    assert all(leaf["total"] <= 10000 for leaf in leaves)
    assert any(c["field"] == "SYNOMDFSSize"
               for leaf in leaves for c in leaf["criteria"])


def test_a_cut_that_loses_hits_is_rejected_not_trusted():
    """Documents missing the cut field would vanish; the planner must notice."""
    docs = _corpus(12000)
    for doc in docs[:5000]:
        doc["mtime"] = None  # field absent from the index for these
    api = FakeFinder(docs, cap=10000)
    leaves, total, lost = crawl.plan_slices(api, "gandhi", "document", cap=10000,
                                            log=lambda *a: None)

    assert total == 12000
    assert lost == 0
    assert sum(leaf["total"] for leaf in leaves) == 12000


def test_unsplittable_overflow_is_reported_rather_than_hidden():
    """Every document identical in every dimension: nothing can cut it."""
    docs = _corpus(15000, mtime=1600000000, size=4096)
    api = FakeFinder(docs, cap=10000)
    leaves, total, lost = crawl.plan_slices(api, "gandhi", "document", cap=10000,
                                            log=lambda *a: None)
    assert total == 15000
    assert lost == 5000
    assert any(leaf.get("truncated") for leaf in leaves)


def test_crawl_resumes_from_its_recorded_cursor(tmp_path):
    docs = _corpus(3000)
    api = FakeFinder(docs, cap=10000)
    crawl.crawl(api, "gandhi", str(tmp_path), cap=10000, page_size=500,
                file_types=["document"], log=lambda *a: None)

    crawl.crawl(api, "gandhi", str(tmp_path), cap=10000, page_size=500,
                file_types=["document"], log=lambda *a: None)
    # The second run must not re-fetch a single page.
    with open(tmp_path / "_meta" / "raw.jsonl") as fh:
        assert sum(1 for _ in fh) == 3000


def test_merge_unions_category_passes_onto_one_record_per_path(tmp_path):
    docs = _corpus(10, file_types=["document"])
    docs += [{"path": "/volume1/MEHERBABA/clip.mp4", "size": 5, "mtime": 5,
              "is_dir": False, "file_types": ["video"]}]
    docs += [{"path": "/volume1/MEHERBABA/box.zip", "size": 5, "mtime": 5,
              "is_dir": False, "file_types": []}]
    docs += [{"path": "/volume1/MEHERBABA/Gandhi", "size": 0, "mtime": 5,
              "is_dir": True, "file_types": []}]
    api = FakeFinder(docs, cap=10000)

    crawl.crawl(api, "gandhi", str(tmp_path), cap=10000, page_size=100,
                log=lambda *a: None)
    crawl.merge(str(tmp_path), log=lambda *a: None)

    records = {}
    with open(tmp_path / "_meta" / "hits.jsonl") as fh:
        for line in fh:
            record = json.loads(line)
            records[record["path"]] = record

    assert len(records) == 13
    assert records["/volume1/MEHERBABA/f00000.doc"]["categories"] == ["documents"]
    assert records["/volume1/MEHERBABA/clip.mp4"]["categories"] == ["videos"]
    # present in the "all" pass but claimed by no typed pass
    assert records["/volume1/MEHERBABA/box.zip"]["categories"] == ["other"]
    assert records["/volume1/MEHERBABA/Gandhi"]["is_dir"] is True
    assert records["/volume1/MEHERBABA/Gandhi"]["categories"] == []
