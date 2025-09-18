from flask import request
from sqlalchemy import or_

def get_page_limit(default_limit=20, max_limit=100):
    try:
        page  = max(int(request.args.get("page", 1)), 1)
        limit = min(max(int(request.args.get("limit", default_limit)), 1), max_limit)
    except ValueError:
        page, limit = 1, default_limit
    return page, limit

def apply_q_search(query, *cols):
    q = (request.args.get("q") or "").strip().lower()
    if not q: return query
    like = f"%{q}%"
    return query.filter(or_(*[c.ilike(like) for c in cols]))
