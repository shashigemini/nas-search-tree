import io
import json

import pytest

from nassearch.api import (DsmError, FinderClient, SessionExpired,
                           construct_keyword, escape_lucene)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _client(payloads, **kwargs):
    sent = []

    def urlopen(url, data=None, timeout=None):
        sent.append((url, data))
        return _Response(json.dumps(payloads[len(sent) - 1]).encode())

    return FinderClient("SID", urlopen=urlopen, sleep=lambda s: None, **kwargs), sent


def _params(body):
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs(body.decode()).items()}


def test_search_body_matches_the_shape_finder_js_sends():
    client, sent = _client([{"success": True, "data": {"total": 3, "hits": []}}])
    client.search("gandhi", file_type="document", frm=40, size=20)

    url, body = sent[0]
    assert url.endswith("/entry.cgi")
    params = _params(body)
    assert params["api"] == "SYNO.Finder.FileIndexing.Search"
    assert params["method"] == "search"
    assert params["version"] == "1"
    assert params["_sid"] == "SID"
    assert params["agent"] == "sus"
    assert params["file_type"] == "document"
    assert params["from"] == "40" and params["size"] == "20"
    # complex values must arrive JSON-encoded, scalars raw
    weights = json.loads(params["search_weight_list"])
    assert {"field": "SYNOMDSearchFileName", "weight": 8.5,
            "trailing_wildcard": True} in weights
    fields = json.loads(params["fields"])
    assert "SYNOMDPath" in fields and "SYNOMDIsDir" in fields
    assert json.loads(params["criteria_list"]) == []
    assert json.loads(params["indice"]) == []


def test_keyword_is_normalised_like_the_ui_but_original_is_preserved():
    client, sent = _client([{"success": True, "data": {}}])
    client.search("**gandhi field:x")
    params = _params(sent[0][1])
    assert params["keyword"] == "gandhi field x"
    assert params["orig_keyword"] == "**gandhi field:x"


def test_expired_session_is_distinguishable_from_other_failures():
    client, _ = _client([{"success": False, "error": {"code": 119}}])
    with pytest.raises(SessionExpired):
        client.search("gandhi")

    client, _ = _client([{"success": False, "error": {"code": 408}}])
    with pytest.raises(DsmError) as caught:
        client.search("gandhi")
    assert not isinstance(caught.value, SessionExpired)


def test_transport_failure_is_retried_then_surfaced():
    attempts = []

    def urlopen(url, data=None, timeout=None):
        attempts.append(1)
        raise OSError("connection reset")

    client = FinderClient("SID", urlopen=urlopen, sleep=lambda s: None, max_retries=3)
    with pytest.raises(DsmError):
        client.search("gandhi")
    assert len(attempts) == 3


def test_total_probes_with_a_single_hit():
    client, sent = _client([{"success": True, "data": {"total": 56340, "hits": []}}])
    assert client.total("gandhi", "document") == 56340
    assert _params(sent[0][1])["size"] == "1"


def test_lucene_escaping_covers_range_and_grouping_syntax():
    assert escape_lucene("a+b") == "a\\+b"
    assert escape_lucene("x[1]") == "x\\[1\\]"
    assert construct_keyword("*gandhi") == "gandhi"
