import json
import os

import pytest

from nassearch import cli


def _prepare(tmp_path):
    vol = tmp_path / "vol"
    vol.mkdir()
    payload = b"Gandhi" * 100
    (vol / "Gandhi.doc").write_bytes(payload)
    (vol / "copy.doc").write_bytes(payload)
    meta = tmp_path / "out" / "_meta"
    meta.mkdir(parents=True)
    with open(meta / "hits.jsonl", "w") as fh:
        for name in ("Gandhi.doc", "copy.doc"):
            fh.write(json.dumps({
                "path": str(vol / name), "share_path": "", "name": name,
                "extension": "doc", "size": 0, "is_dir": False,
                "categories": ["documents"]}) + "\n")
    return str(vol), str(tmp_path / "out")


def test_stages_run_from_the_cli_and_write_a_run_log(tmp_path, capsys):
    vol, out = _prepare(tmp_path)
    for stage in ("dedupe", "link", "verify"):
        assert cli.main([stage, "--out", out, "--root", vol]) == 0

    log = open(os.path.join(out, "_meta", "run.log")).read()
    assert "-- dedupe" in log and "-- link" in log and "-- verify" in log
    assert "done in" in log
    assert "1 redundant copies" in log          # the duplicate was caught
    assert capsys.readouterr().out              # and echoed to stdout


def test_quiet_keeps_stdout_clean_but_still_logs(tmp_path, capsys):
    vol, out = _prepare(tmp_path)
    assert cli.main(["dedupe", "--out", out, "--root", vol, "--quiet"]) == 0
    assert capsys.readouterr().out == ""
    assert "-- dedupe" in open(os.path.join(out, "_meta", "run.log")).read()


def test_run_log_appends_across_invocations(tmp_path):
    vol, out = _prepare(tmp_path)
    cli.main(["dedupe", "--out", out, "--root", vol, "--quiet"])
    cli.main(["dedupe", "--out", out, "--root", vol, "--quiet"])
    log = open(os.path.join(out, "_meta", "run.log")).read()
    assert log.count("-- dedupe") == 2


def test_verify_failure_is_a_nonzero_exit(tmp_path):
    vol, out = _prepare(tmp_path)
    cli.main(["dedupe", "--out", out, "--root", vol, "--quiet"])
    cli.main(["link", "--out", out, "--root", vol, "--quiet"])

    with open(os.path.join(out, "_meta", "manifest.jsonl")) as fh:
        canonical = json.loads(fh.readline())["canonical_path"]
    os.remove(canonical)  # break the file the tree actually points at
    assert cli.main(["verify", "--out", out, "--root", vol, "--quiet"]) == 1


def test_a_crawl_without_a_session_stops_before_doing_anything(tmp_path, monkeypatch):
    monkeypatch.delenv("DSM_SID", raising=False)
    with pytest.raises(SystemExit):
        cli.main(["crawl", "--out", str(tmp_path / "out")])


def test_login_uses_account_prompt_and_can_emit_shell_exports(monkeypatch, capsys):
    class Client:
        sid = "LOCAL SID"
        syno_token = "TOKEN"

    captured = {}

    def fake_login(account, password, **kwargs):
        captured.update(account=account, password=password, **kwargs)
        return Client()

    monkeypatch.delenv("DSM_SID", raising=False)
    monkeypatch.delenv("DSM_PASSWORD", raising=False)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "secret")
    monkeypatch.setattr(cli, "dsm_login", fake_login)

    assert cli.main(["login", "--account", "nosh", "--shell"]) == 0
    assert captured == {"account": "nosh", "password": "secret",
                        "base_url": cli.DEFAULT_BASE_URL, "otp_code": None}
    output = capsys.readouterr().out
    assert "export DSM_SID='LOCAL SID'" in output
    assert "export DSM_SYNOTOKEN=TOKEN" in output
