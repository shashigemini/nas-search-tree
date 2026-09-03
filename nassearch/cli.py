import argparse
import getpass
import os
import shlex
import sys
import time

from . import crawl as crawl_mod
from . import dedupe as dedupe_mod
from . import link as link_mod
from . import report as report_mod
from . import verify as verify_mod
from .api import (FILE_TYPES, AuthenticationError, DsmError, FinderClient,
                  SessionExpired, SessionSourceMismatch, DEFAULT_BASE_URL,
                  login as dsm_login)
from .runlog import RunLog


def _client(args):
    sid = args.sid or os.environ.get("DSM_SID")
    if sid:
        return FinderClient(sid, base_url=args.base_url,
                            syno_token=args.syno_token or os.environ.get("DSM_SYNOTOKEN"))

    account = args.account or os.environ.get("DSM_ACCOUNT")
    if not account:
        sys.exit("no session: pass --sid/set DSM_SID, or use --account to log in "
                 "from this machine")
    password = os.environ.get("DSM_PASSWORD")
    if password is None:
        password = getpass.getpass("DSM password for %s: " % account)
    otp_code = args.otp_code or os.environ.get("DSM_OTP_CODE")
    return dsm_login(account, password, base_url=args.base_url, otp_code=otp_code)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nassearch")
    parser.add_argument("--out", help="export directory")
    parser.add_argument("--keyword", default="gandhi")
    parser.add_argument("--sid", default=None)
    parser.add_argument("--syno-token", default=None,
                        help="DSM CSRF token returned alongside a SID")
    parser.add_argument("--account", default=None,
                        help="DSM account; password is prompted securely")
    parser.add_argument("--otp-code", default=None,
                        help="DSM two-factor authentication code, if required")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cap", type=int, default=crawl_mod.DEFAULT_CAP)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--root", default=link_mod.VOLUME_ROOT)
    parser.add_argument("--file-type", action="append", dest="file_types",
                        choices=FILE_TYPES, default=None)
    parser.add_argument("--quiet", action="store_true",
                        help="write only to _meta/run.log, not to stdout")
    parser.add_argument("--shell", action="store_true",
                        help="with login, print safe shell exports for the new session")
    parser.add_argument("stage", choices=["login", "crawl", "merge", "dedupe", "link", "verify", "all"])
    args = parser.parse_args(argv)

    if args.stage != "login" and not args.out:
        parser.error("--out is required unless stage is login")
    if args.stage == "login":
        client = _client(args)
        if args.shell:
            print("export DSM_SID=%s" % shlex.quote(client.sid))
            if client.syno_token:
                print("export DSM_SYNOTOKEN=%s" % shlex.quote(client.syno_token))
        else:
            print("DSM login succeeded. Re-run with --shell to export this session.")
        return 0

    os.makedirs(os.path.join(args.out, "_meta"), exist_ok=True)
    log = RunLog(args.out, echo=not args.quiet)
    counts = None

    log("=== stage %s, keyword %r, out %s" % (args.stage, args.keyword, args.out))
    try:
        if args.stage in ("crawl", "all"):
            log("-- crawl")
            crawl_mod.crawl(_client(args), args.keyword, args.out, args.cap,
                            args.page_size, args.file_types, log=log)
        if args.stage in ("crawl", "merge", "all"):
            log("-- merge")
            crawl_mod.merge(args.out, log=log)
        if args.stage in ("dedupe", "all"):
            log("-- dedupe")
            dedupe_mod.dedupe(args.out, args.workers, log=log)
        if args.stage in ("link", "all"):
            log("-- link")
            counts = link_mod.build(args.out, args.root, log=log)
        if args.stage in ("verify", "all"):
            log("-- verify")
            if verify_mod.verify(args.out, log=log):
                log("report: %s" % report_mod.write_report(args.out, counts))
                log("=== FAILED after %s" % _elapsed(log))
                return 1
    except SessionExpired:
        log("DSM session expired -- grab a fresh _sid and re-run "
            "(the crawl resumes where it stopped)")
        return 2
    except SessionSourceMismatch:
        log("DSM session belongs to a different source IP -- use --account to "
            "log in from this machine, then re-run")
        return 2
    except (AuthenticationError, DsmError) as exc:
        log("DSM API failed: %s" % exc)
        return 2

    log("report: %s" % report_mod.write_report(args.out, counts))
    log("=== done in %s" % _elapsed(log))
    return 0


def _elapsed(log):
    seconds = int(time.time() - log.started)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


if __name__ == "__main__":
    sys.exit(main())
