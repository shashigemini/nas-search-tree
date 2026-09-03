import argparse
import os
import sys
import time

from . import crawl as crawl_mod
from . import dedupe as dedupe_mod
from . import link as link_mod
from . import report as report_mod
from . import verify as verify_mod
from .api import FILE_TYPES, FinderClient, SessionExpired, DEFAULT_BASE_URL
from .runlog import RunLog


def _client(args):
    sid = args.sid or os.environ.get("DSM_SID")
    if not sid:
        sys.exit("no session: pass --sid or set DSM_SID (copy the _sid cookie "
                 "from a logged-in DSM tab)")
    return FinderClient(sid, base_url=args.base_url)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nassearch")
    parser.add_argument("--out", required=True, help="export directory")
    parser.add_argument("--keyword", default="gandhi")
    parser.add_argument("--sid", default=None)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cap", type=int, default=crawl_mod.DEFAULT_CAP)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--root", default=link_mod.VOLUME_ROOT)
    parser.add_argument("--file-type", action="append", dest="file_types",
                        choices=FILE_TYPES, default=None)
    parser.add_argument("--quiet", action="store_true",
                        help="write only to _meta/run.log, not to stdout")
    parser.add_argument("stage", choices=["crawl", "merge", "dedupe", "link", "verify", "all"])
    args = parser.parse_args(argv)

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

    log("report: %s" % report_mod.write_report(args.out, counts))
    log("=== done in %s" % _elapsed(log))
    return 0


def _elapsed(log):
    seconds = int(time.time() - log.started)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


if __name__ == "__main__":
    sys.exit(main())
