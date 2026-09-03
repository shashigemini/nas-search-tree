import argparse
import os
import sys

from . import crawl as crawl_mod
from . import dedupe as dedupe_mod
from . import link as link_mod
from . import report as report_mod
from . import verify as verify_mod
from .api import FILE_TYPES, FinderClient, SessionExpired, DEFAULT_BASE_URL


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
    parser.add_argument("stage", choices=["crawl", "merge", "dedupe", "link", "verify", "all"])
    args = parser.parse_args(argv)

    os.makedirs(os.path.join(args.out, "_meta"), exist_ok=True)
    counts = None

    try:
        if args.stage in ("crawl", "all"):
            crawl_mod.crawl(_client(args), args.keyword, args.out, args.cap,
                            args.page_size, args.file_types)
        if args.stage in ("crawl", "merge", "all"):
            crawl_mod.merge(args.out)
        if args.stage in ("dedupe", "all"):
            dedupe_mod.dedupe(args.out, args.workers)
        if args.stage in ("link", "all"):
            counts = link_mod.build(args.out, args.root)
        if args.stage in ("verify", "all"):
            if verify_mod.verify(args.out):
                print("report: %s" % report_mod.write_report(args.out, counts))
                return 1
    except SessionExpired:
        sys.exit("DSM session expired -- grab a fresh _sid and re-run "
                 "(the crawl resumes where it stopped)")

    print("report: %s" % report_mod.write_report(args.out, counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
