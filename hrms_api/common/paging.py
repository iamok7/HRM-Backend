# hrms_api/common/paging.py
from flask import request

DEFAULT_PAGE = 1
DEFAULT_SIZE = 20
MAX_SIZE = 100

def page_limit():
    try:
        page = max(int(request.args.get("page", DEFAULT_PAGE)), 1)
    except Exception:
        page = DEFAULT_PAGE
    try:
        size = int(request.args.get("size", DEFAULT_SIZE))
        size = max(1, min(size, MAX_SIZE))
    except Exception:
        size = DEFAULT_SIZE
    return page, size

def sort_params(allowed: dict[str, object]):
    """
    allowed: {"name": Model.name, "created_at": Model.created_at, ...}
    ?sort=name,-created_at  => returns list of (column, asc:bool)
    Unknown keys ignored.
    """
    raw = request.args.get("sort", "")
    items = []
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        asc = True
        key = part
        if part.startswith("-"):
            asc = False
            key = part[1:]
        col = allowed.get(key)
        if col is not None:
            items.append((col, asc))
    return items

def text_q():
    q = request.args.get("q", "")
    return q.strip() or None
