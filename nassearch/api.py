"""Client for the Synology Universal Search (SynoFinder) WebAPI.

The request shape here is not invented: it was read off the shipping DSM 7.4.1
client at /var/packages/SynoFinder/target/ui/Finder.js.  Keeping it identical to
what the web UI sends is what guarantees our result set matches the sidebar
counts the user sees.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://192.168.1.14:5000/webapi"

SEARCH_API = "SYNO.Finder.FileIndexing.Search"
FOLDER_API = "SYNO.Finder.FileIndexing.Folder"

# Finder.js: SYNO.Finder.Searcher.File#getSearchWeights
SEARCH_WEIGHT_LIST = [
    {"field": "SYNOMDWildcard", "weight": 1},
    {"field": "SYNOMDTextContent", "weight": 1},
    {"field": "SYNOMDSearchFileName", "weight": 8.5, "trailing_wildcard": True},
]

# Finder.js: getSearchFields(), "all" branch, plus the path fields the UI reads
# out of extraData.  SYNOMDPath is the absolute path we symlink to.
SEARCH_FIELDS = [
    "SYNOMDAcquisitionMake", "SYNOMDAcquisitionModel", "SYNOMDAlbum",
    "SYNOMDAperture", "SYNOMDAudioBitRate", "SYNOMDAudioTrackNumber",
    "SYNOMDAuthors", "SYNOMDCodecs", "SYNOMDContentCreationDate",
    "SYNOMDContentModificationDate", "SYNOMDCreator", "SYNOMDDurationSecond",
    "SYNOMDExposureTimeString", "SYNOMDExtension", "SYNOMDFSCreationDate",
    "SYNOMDFSName", "SYNOMDFSSize", "SYNOMDISOSpeed", "SYNOMDLastUsedDate",
    "SYNOMDMediaTypes", "SYNOMDMusicalGenre", "SYNOMDOwnerUserID",
    "SYNOMDOwnerUserName", "SYNOMDRecordingYear", "SYNOMDResolutionHeightDPI",
    "SYNOMDResolutionWidthDPI", "SYNOMDTitle", "SYNOMDIsEncrypted",
    "SYNOMDIsDir", "SYNOMDPath", "SYNOMDSharePath",
]

# Finder.js: getSearchFileType() -- the sidebar categories, verbatim.
FILE_TYPES = ["", "document", "image", "audio", "video"]

# Sidebar label -> output directory name.
CATEGORY_DIRS = {
    "document": "documents",
    "image": "photos",
    "audio": "music",
    "video": "videos",
}

# DSM auth error codes: invalid/expired session.
_SESSION_ERRORS = {105, 106, 107, 119}

_LUCENE_SPECIAL = r'+-&|!(){}[]^"~*?:\/'


def escape_lucene(text):
    """Mirror of SYNO.Finder.Utils.escapeLucene for criteria values."""
    return "".join("\\" + c if c in _LUCENE_SPECIAL else c for c in text)


def construct_keyword(text):
    """Mirror of SYNO.Finder.Utils.ConstructKeyword.

    Strips leading wildcards from each token (the backend rejects them) and
    removes colons so a keyword cannot be read as a field selector.
    """
    tokens = [t.lstrip("*") for t in text.replace(":", " ").split(" ")]
    return " ".join(t for t in tokens if t)


class DsmError(Exception):
    def __init__(self, code, api, method):
        super().__init__("DSM %s.%s failed with error code %s" % (api, method, code))
        self.code = code


class SessionExpired(DsmError):
    pass


class FinderClient:
    def __init__(self, sid, base_url=DEFAULT_BASE_URL, urlopen=None,
                 sleep=time.sleep, max_retries=5, timeout=300):
        if not sid:
            raise ValueError("a DSM _sid is required")
        self.sid = sid
        self.base_url = base_url.rstrip("/")
        self._urlopen = urlopen or urllib.request.urlopen
        self._sleep = sleep
        self.max_retries = max_retries
        self.timeout = timeout

    @staticmethod
    def encode_params(params):
        """Scalars go over the wire raw; everything else as JSON.

        This is what SYNO.SDS' sendWebAPI does, and the backend's JSON
        requestFormat parser depends on it.
        """
        out = {}
        for key, value in params.items():
            if isinstance(value, str):
                out[key] = value
            elif isinstance(value, bool):
                out[key] = "true" if value else "false"
            elif isinstance(value, (int, float)):
                out[key] = str(value)
            else:
                out[key] = json.dumps(value, separators=(",", ":"))
        return out

    def call(self, api, method, version, params=None):
        body = {"api": api, "method": method, "version": version, "_sid": self.sid}
        body.update(self.encode_params(params or {}))
        data = urllib.parse.urlencode(body).encode("utf-8")
        url = self.base_url + "/entry.cgi"

        last_error = None
        for attempt in range(self.max_retries):
            try:
                with self._urlopen(url, data=data, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_error = exc
                self._sleep(2 ** attempt)
                continue

            if payload.get("success"):
                return payload.get("data", {})

            code = (payload.get("error") or {}).get("code")
            if code in _SESSION_ERRORS:
                raise SessionExpired(code, api, method)
            # 400-series Finder codes are deterministic; retrying is pointless.
            raise DsmError(code, api, method)

        raise DsmError("transport: %s" % last_error, api, method)

    def search(self, keyword, file_type="", frm=0, size=200, criteria_list=None):
        params = {
            "agent": "sus",
            "indice": [],
            "keyword": construct_keyword(keyword),
            "orig_keyword": keyword,
            "criteria_list": criteria_list or [],
            "from": frm,
            "size": size,
            "fields": SEARCH_FIELDS,
            "file_type": file_type,
            "search_weight_list": SEARCH_WEIGHT_LIST,
        }
        return self.call(SEARCH_API, "search", 1, params)

    def total(self, keyword, file_type="", criteria_list=None):
        """Cheapest possible probe: ask for one hit, read the total."""
        data = self.search(keyword, file_type=file_type, frm=0, size=1,
                           criteria_list=criteria_list)
        return int(data.get("total", 0))
