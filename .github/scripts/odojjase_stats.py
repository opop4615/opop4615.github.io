#!/usr/bin/env python3
"""오도짜세 현황 수집기. GitHub Actions(.github/workflows/odojjase-stats.yml)가 30분마다 돌린다.

  python3 odojjase_stats.py <출력 폴더>

출력 폴더에 latest.json(지금 값 전부)을 쓰고 history.jsonl(숫자만, 한 줄 = 한 번 수집)에 한 줄 덧붙인다.
hall_rows.json은 명예해병 명부 기록을 모아 둔 것 — 명부를 통째로 다시 읽지 않으려고 둔다(읽기 한도, hall() 머리말).
페이지(odojjase-stats/index.html)가 이 셋을 읽는다. 틀은 설화던전 현황(seolhwa_stats.py)과 같다.

읽는 것
  · 명예해병 명부(Firestore, 키 없이 공개 읽기) — 날짜별 사람 수·판 수, 플레이 기록
  · 출시 글의 조회·추천·댓글(POSTS)
  · 다른 곳에서 다룬 것: 구글 뉴스, 네이버 뉴스·블로그·카페, 유튜브·치지직 검색, 커뮤니티 검색(클리앙·디시·에펨코리아)
  · 앱스토어·플레이 페이지와 리뷰(한국), 유튜브 공식 영상 조회수

한 곳이 실패해도 나머지는 계속한다 — 실패는 errors에 적고 그 칸은 지난 값을 둔다(페이지가 "못 읽음"으로 보인다).
막힌 곳(403·429·확인 화면)은 돌아가지 않는다: 다시 두드리지도 머리말을 꾸미지도 않고 "못 읽음"으로 남긴다.
뽐뿌는 늘 막혀 있어 직접 열기 링크만 둔다.
"""
import json, os, re, sys, time, traceback, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

IOS_ID = 6829262345
PLAY_ID = "com.sungkisoft.odojjase"
YT_HANDLE = "sungkisoft"  # 설화던전과 같은 채널 — 제목에 '오도짜세'가 든 영상만 센다
YT_OWN = ["NgkNePCq5wM", "btVs7oHG_oQ"]  # 공식 트레일러(채널 RSS의 최근 15편 밖으로 밀려나도 계속 읽는다)
FS_DOCS = "https://firestore.googleapis.com/v1/projects/odojjase/databases/(default)/documents"  # 키 없이 읽힌다. 막는 일은 firestore.rules가 한다
KST = timezone(timedelta(hours=9))
KEEP = 2 * 24 * 21  # history.jsonl에 남길 줄 수(30분마다 약 3주)
HALL_CACHE = "hall_rows.json"
# 칸이 바뀌면 hall()이 한 번 통째로 다시 읽는다. 페이지의 fsRow()와 맞춘다
HALL_COLS = ["id", "at", "uid", "name", "senior", "rank", "kills", "drowned", "islands", "risk", "score", "win", "ver", "paint", "mods", "mvp", "mvpd", "top"]
HALL_FIELDS = ["at", "uid", "name", "senior", "rank", "kills", "drowned", "islands", "risk", "score", "win", "ver", "paint", "mods", "mvp", "mvpd", "top"]
HALL_OVERLAP = timedelta(minutes=30)  # 규칙(firestore.rules)이 기록의 시각을 서버 시계 ±10분까지 받는다 — 늦은 시계로 올라온 줄을 안 놓치게 겹쳐 읽는다
HALL_EPOCH = "2000-01-01T00:00:00Z"

# 출시 글. 글을 더 올리면 여기에 한 줄 (key는 바꾸지 않는다 — history.jsonl의 열쇠다)
#   kind: clien·fm·cafe = 숫자 + 댓글 내용 / dc = 숫자만(댓글 내용은 따로 불러야 해서 안 읽는다) / manual = 못 읽는 곳, 직접 열기 링크만
#   title(없어도 된다): 한 번도 못 읽은 글에 보일 제목 — 에펨코리아는 깃허브 서버를 막는 때가 많다(HTTP 430)
POSTS = [
    {"key": "clien_iphone", "kind": "clien", "site": "클리앙", "board": "아이포니앙", "url": "https://www.clien.net/service/board/cm_iphonien/19290472"},
    {"key": "clien_andro", "kind": "clien", "site": "클리앙", "board": "안드로메당", "url": "https://www.clien.net/service/board/cm_andro/19290474"},
    {"key": "dc_aidev", "kind": "dc", "site": "디시", "board": "AI 게임 개발 갤", "url": "https://gall.dcinside.com/mgallery/board/view/?id=aidevelop&no=7122"},
    {"key": "fm_mgame", "kind": "fm", "site": "에펨코리아", "board": "모바일게임", "url": "https://www.fmkorea.com/10541329565",
     "title": "오도짜세! 돌격 상륙장갑차 - 진짜 해병이 만든 해병 좀비 로그라이크입니다."},
    {"key": "cafe_hack", "kind": "cafe", "site": "네이버 카페", "board": "신작게임평가단", "url": "https://cafe.naver.com/hackmacke/118429", "cafe": 24576196, "article": 118429},
    {"key": "ppom", "kind": "manual", "site": "뽐뿌", "board": "앱정보 · 내가 만든 앱", "url": "https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomapp&category=2"},
]
CLIEN_ME = "rage4615"  # 클리앙에서 내 댓글을 가려내는 계정 번호
# 커뮤니티 검색: 내 글 말고 누가 따로 쓴 글. 디시는 갤러리 안 검색(제목+내용)
DC_GALLS = [("aidevelop", "AI 게임 개발 갤"), ("indiegame", "인디게임 갤"), ("game_dev", "인디 게임 개발 갤")]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

errors = []


def get(url, headers=None, tries=3, data=None):
    h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    h.update(headers or {})
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 or i == tries - 1:  # 막힘(403·429)·없음(404)은 다시 두드리지 않는다
                raise
        except Exception:
            if i == tries - 1:
                raise
        time.sleep(3 * (i + 1))


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def why(e):
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %d%s" % (e.code, " — 막혀 있다(돌아가지 않는다)" if e.code in (401, 403, 429, 430) else "")
    return ("%s: %s" % (type(e).__name__, e))[:200]


def step(name, fn, *a):
    """한 갈래를 돌리고, 실패하면 errors에 남기고 None"""
    try:
        return fn(*a)
    except Exception as e:
        errors.append({"what": name, "why": why(e)})
        traceback.print_exc()
        return None


def now_iso():
    return datetime.now(KST).isoformat(timespec="seconds")


def kst_iso(s, fmt="%Y-%m-%d %H:%M:%S"):
    """한국 시각 글자("2026-11-10 23:31:16") → ISO. 못 읽으면 빈 글자"""
    try:
        return datetime.strptime((s or "").strip()[:19], fmt).replace(tzinfo=KST).isoformat(timespec="seconds")
    except Exception:
        return ""


def count(s):
    """"조회 1,234" · "1.2 k" · "3.4만" → 수. 숫자가 없으면 None"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM만천]?)", (s or "").replace(",", ""))
    if not m:
        return None
    return int(float(m.group(1)) * {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "만": 1e4, "천": 1e3}.get(m.group(2), 1))


def soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def T(e):
    return re.sub(r"\s+", " ", e.get_text(" ", strip=True)) if e else ""


def unescape(s):
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")


# ---------------------------------------------------------------- 명예해병 명부 (Firestore, 읽기는 공개)

def fs_val(f):
    k, v = next(iter(f.items()))
    if k == "mapValue":
        return {a: fs_val(b) for a, b in v.get("fields", {}).items()}
    if k == "arrayValue":
        return [fs_val(x) for x in v.get("values", [])]
    if k == "integerValue":
        return int(v)
    return v


# 읽기 한도: Firestore 무료 요금제는 하루 읽기 5만 건이고 "돌려준 문서 수"로 센다. 돌 때마다 명부를 통째로 읽으면
# 기록이 수백 줄일 때 한도를 다 쓰고 게임 안 명부까지 429로 멎는다(설화던전에서 2026-10-12에 겪었다).
# 그래서 읽은 줄을 HALL_CACHE에 모아 두고, 마지막으로 본 시각 뒤에 올라온 줄만 읽는다(한 번에 몇 건).
# 줄 수가 서버와 어긋나면(관리자가 지운 기록·놓친 줄) 하루에 한 번까지만 통째로 다시 읽는다.

def fs_post(method, body):
    return get_json("%s:%s" % (FS_DOCS, method), data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


def hall_where(since):
    return {"fieldFilter": {"field": {"fieldPath": "at"}, "op": "GREATER_THAN_OR_EQUAL", "value": {"timestampValue": since}}}


def hall_row(d):
    """문서 → 한 줄. 남이 꾸며 올린 기록이면 형이 엉뚱할 수 있어 칸마다 형을 맞춘다"""
    r = {k: fs_val(v) for k, v in d.get("fields", {}).items()}
    if not isinstance(r.get("at"), str):
        return None
    num = lambda k: int(r[k]) if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool) else 0
    txt = lambda k, n: r[k][:n] if isinstance(r.get(k), str) else ""
    arr = lambda k, n, w: [x[:w] for x in r[k] if isinstance(x, str)][:n] if isinstance(r.get(k), list) else []
    return {"id": d.get("name", "").split("/")[-1], "at": r["at"], "uid": txt("uid", 64), "name": txt("name", 16), "senior": txt("senior", 16),
            "rank": num("rank"), "kills": num("kills"), "drowned": num("drowned"), "islands": num("islands"), "risk": num("risk"), "score": num("score"),
            "win": r.get("win") is True, "ver": txt("ver", 12), "paint": txt("paint", 16), "mods": arr("mods", 80, 24), "mvp": txt("mvp", 16),
            "mvpd": num("mvpd"), "top": arr("top", 3, 40)}


def hall_since(since):
    """시각이 since 이후인 기록을 시각 차례로. 돌려받은 줄 수만큼만 읽기 한도를 쓴다"""
    out, seen = [], set()
    while True:
        q = {"from": [{"collectionId": "hall"}], "where": hall_where(since), "orderBy": [{"field": {"fieldPath": "at"}, "direction": "ASCENDING"}],
             "limit": 300, "select": {"fields": [{"fieldPath": f} for f in HALL_FIELDS]}}
        docs = [e["document"] for e in fs_post("runQuery", {"structuredQuery": q}) if isinstance(e, dict) and "document" in e]
        fresh = 0
        for d in docs:
            row = hall_row(d)
            if row and row["id"] not in seen:
                seen.add(row["id"])
                out.append(row)
                fresh += 1
        if len(docs) < 300 or not fresh:
            return out
        since = out[-1]["at"]  # 다음 쪽: 마지막 줄의 시각부터(같은 초의 줄은 번호로 거른다)


def hall_count():
    """서버의 기록 수. 개수 세기는 색인 1,000줄에 읽기 1건이다"""
    res = fs_post("runAggregationQuery", {"structuredAggregationQuery": {
        "structuredQuery": {"from": [{"collectionId": "hall"}], "where": hall_where(HALL_EPOCH)}, "aggregations": [{"alias": "n", "count": {}}]}})
    return int(res[0]["result"]["aggregateFields"]["n"]["integerValue"])


def hall_time(at):
    return datetime.fromisoformat(re.sub(r"\.\d+", "", at).replace("Z", "+00:00"))


def hall(out):
    path = os.path.join(out, HALL_CACHE)
    rows, full = {}, ""
    try:
        c = json.load(open(path))
        if c.get("cols") == HALL_COLS:  # 칸이 달라졌으면 아래에서 통째로 다시 읽는다
            rows = {r[0]: dict(zip(c["cols"], r)) for r in c["rows"]}
            full = c.get("full", "")
    except Exception:
        pass
    today = datetime.now(KST).date().isoformat()
    try:
        if rows:
            last = max(hall_time(r["at"]) for r in rows.values())
            for r in hall_since((last - HALL_OVERLAP).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")):
                rows[r["id"]] = r
        if not rows or (hall_count() != len(rows) and full != today):
            rows = {r["id"]: r for r in hall_since(HALL_EPOCH)}
            full = today
    except Exception as e:
        if not rows:
            raise
        # 새 줄을 못 읽었으면(한도 초과 등) 모아 둔 줄로 요약한다 — 숫자가 비지 않고 조금 낡을 뿐이다
        errors.append({"what": "명예해병 명부 새 기록", "why": why(e)})
    body = sorted(rows.values(), key=lambda r: (hall_time(r["at"]), r["id"]))
    json.dump({"at": now_iso(), "full": full, "cols": HALL_COLS, "rows": [[r.get(k) for k in HALL_COLS] for r in body]},
              open(path, "w"), ensure_ascii=False, separators=(",", ":"))
    return hall_summary(body)


def hall_summary(cache):
    """숫자 요약(history.jsonl용). 페이지는 hall_rows.json의 줄로 같은 셈을 직접 한다 — 거기서는 '세지 않을 이름'을 뺄 수 있다"""
    rows = sorted((dict(r, t=hall_time(r["at"]).astimezone(KST)) for r in cache), key=lambda r: r["t"])
    today = datetime.now(KST).date()
    first = {}
    for r in rows:
        first.setdefault(r["uid"], r["t"].date())
    days = {}
    for r in rows:
        e = days.setdefault(r["t"].date().isoformat(), {"runs": 0, "people": set(), "new": set(), "wins": 0})
        e["runs"] += 1
        e["people"].add(r["uid"])
        e["wins"] += bool(r["win"])
        if first[r["uid"]] == r["t"].date():
            e["new"].add(r["uid"])
    tr = [r for r in rows if r["t"].date() == today]
    back = sum(1 for u, f in first.items() if f < today and len({r["t"].date() for r in rows if r["uid"] == u}) > 1)
    return {"total_runs": len(rows), "total_people": len(first), "today_runs": len(tr), "today_people": len({r["uid"] for r in tr}),
            "today_new": sum(1 for f in first.values() if f == today), "wins": sum(1 for r in rows if r["win"]),
            "returned": back, "returned_of": sum(1 for f in first.values() if f < today),
            "days": [{"day": k, "runs": v["runs"], "people": len(v["people"]), "new": len(v["new"]), "wins": v["wins"]} for k, v in sorted(days.items())]}


# ---------------------------------------------------------------- 출시 글: 조회·추천·댓글

def post_clien(p):
    s = soup(get(p["url"], tries=2))
    title = s.select_one("h3.post_subject span:not(.post_category)")
    if not title:
        raise RuntimeError("글을 못 찾았다(지워졌거나 화면이 바뀌었다)")
    lst = []
    for c in s.select("div.comment_row[data-comment-sn]"):
        nick = c.select_one(".nickname")
        img = nick.select_one("img[alt]") if nick else None
        lst.append({"id": str(c["data-comment-sn"]), "who": (img["alt"] if img else T(nick))[:24], "text": T(c.select_one(".comment_view"))[:300],
                    "at": kst_iso(T(c.select_one(".comment_time .timestamp"))), "mine": c.get("data-author-id") == CLIEN_ME})
    return {"title": T(title)[:120], "views": count(T(s.select_one(".post_author .view_count strong"))), "likes": count(T(s.select_one("a.symph_count strong"))) or 0,
            "comments": count(T(s.select_one(".comment_head strong"))) or len(lst), "list": lst}


def fm_time(s, now):
    """에펨코리아 댓글 시각("11 시간 전" · "2026.11.10 22:29") → ISO. 어림값이라 한 번 정한 뒤에는 그대로 둔다(posts())"""
    s = (s or "").strip()
    m = re.match(r"(\d+)\s*(초|분|시간|일)\s*전", s)
    if m:
        unit = {"초": "seconds", "분": "minutes", "시간": "hours", "일": "days"}[m.group(2)]
        return (now - timedelta(**{unit: int(m.group(1))})).isoformat(timespec="seconds")
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})(?:\s+(\d{2}):(\d{2}))?", s)
    if m:
        y, mo, d, hh, mm = [int(x) if x else 0 for x in m.groups()]
        return datetime(y, mo, d, hh, mm, tzinfo=KST).isoformat(timespec="seconds")
    return ""


def post_fm(p):
    s = soup(get(p["url"], tries=2))
    title = s.select_one("h1.np_18px span.np_18px_span") or s.select_one("h1.np_18px")
    if not title:
        raise RuntimeError("글을 못 찾았다(지워졌거나 확인 화면이 떴다)")
    side = " ".join(T(x) for x in s.select(".btm_area .side.fr span"))
    g = lambda w: count((re.search(w + r"\s*([\d,.]+\s*[만천]?)", side) or [None, ""])[1])
    srl = lambda a: (re.search(r"member_(\d+)", " ".join(a.get("class", []))) or [None, ""])[1] if a else ""
    writer = srl(s.select_one(".btm_area .side a.member_plate"))
    now, lst = datetime.now(KST), []
    for li in s.select("ul.fdb_lst_ul li.fdb_itm"):
        a = li.select_one(".meta a.member_plate")
        body = li.select_one(".comment-content")
        mine = bool(body and "document_writer" in " ".join(body.get("class", []))) or bool(writer and srl(a) == writer)
        when = T(li.select_one(".meta span.date"))
        lst.append({"id": li.get("id", "").replace("comment_", ""), "who": T(a)[:24], "text": T(li.select_one(".comment-content .xe_content"))[:300],
                    "at": fm_time(when, now), "rough": "전" in when, "mine": mine})
    lst = [c for c in lst if c["id"]]
    n = g("댓글")
    return {"title": T(title)[:120], "views": g("조회 수"), "likes": g("추천 수") or 0, "comments": n if n is not None else len(lst), "list": lst}


def dc_who(w):
    """디시 글쓴이: 별명(아이피 앞자리). 고정닉은 별명만"""
    if w is None:
        return ""
    return (w.get("data-nick") or "") + ("(%s)" % w["data-ip"] if w.get("data-ip") else "")


def post_dc(p):
    s = soup(get(p["url"], tries=2))
    title = s.select_one("span.title_subject")
    if not title:
        raise RuntimeError("글을 못 찾았다(지워졌거나 화면이 바뀌었다)")
    return {"title": T(title)[:120], "views": count(T(s.select_one("span.gall_count"))), "likes": count(T(s.select_one("span.gall_reply_num"))) or 0,
            "comments": count(T(s.select_one("span.gall_comment"))) or 0, "list": None, "who": dc_who(s.select_one(".gall_writer"))}


def post_cafe(p):
    lst, art = [], {}
    for page in range(1, 4):  # 한 쪽에 100개
        d = get_json("https://apis.naver.com/cafe-web/cafe-articleapi/v2/cafes/%d/articles/%d/comments/pages/%d?requestFrom=A&orderBy=asc" % (p["cafe"], p["article"], page), tries=2)
        res = d.get("result") or {}
        art = res.get("article") or art
        items = (res.get("comments") or {}).get("items") or []
        for c in items:
            if c.get("isDeleted"):
                continue
            t = c.get("updateDate")
            lst.append({"id": str(c.get("id")), "who": str((c.get("writer") or {}).get("nick") or "")[:24], "text": re.sub(r"\s+", " ", str(c.get("content") or ""))[:300],
                        "at": datetime.fromtimestamp(t / 1000, KST).isoformat(timespec="seconds") if isinstance(t, (int, float)) else "", "mine": c.get("isArticleWriter") is True})
        if len(items) < 100:
            break
        time.sleep(1)
    if not art:
        raise RuntimeError("글을 못 찾았다(지워졌거나 로그인해야 보인다)")
    n = art.get("commentCount")
    return {"title": str(art.get("subject") or "")[:120], "views": art.get("readCount"), "likes": None, "comments": n if isinstance(n, int) else len(lst), "list": lst}


READERS = {"clien": post_clien, "fm": post_fm, "dc": post_dc, "cafe": post_cafe}


def posts(prev):
    """글마다 숫자와 댓글. 댓글마다 처음 본 때(first)를 지킨다 — 페이지가 그걸로 '새'를 붙인다.
    못 읽은 글은 지난 숫자·댓글을 그대로 두고 err를 단다"""
    old = {p.get("key"): p for p in (prev or []) if isinstance(p, dict)}
    now, out = now_iso(), []
    for p in POSTS:
        o = old.get(p["key"]) or {}
        e = {k: p[k] for k in ("key", "kind", "site", "board", "url")}
        if p["kind"] != "manual":
            try:
                got = READERS[p["kind"]](p)
                if got.get("list") is not None:
                    known = {c.get("id"): c for c in o.get("list") or []}
                    first_read = o.get("list") is None  # 이 글을 처음 읽는다: 이미 달려 있던 댓글은 '새'가 아니다
                    for c in got["list"]:
                        k = known.get(c["id"])
                        if k:
                            c["first"], c["at"] = k.get("first", ""), k.get("at") or c["at"]
                            if "rough" in c:
                                c["rough"] = bool(k.get("rough"))
                        else:
                            c["first"] = c["at"] if first_read else now
                e.update(got)
                e["read_at"] = now
            except Exception as ex:
                for k in ("title", "views", "likes", "comments", "list", "read_at"):
                    if k in o:
                        e[k] = o[k]
                if not e.get("title") and p.get("title"):
                    e["title"] = p["title"]
                e["err"] = why(ex)
                errors.append({"what": "%s %s 글" % (p["site"], p["board"]), "why": why(ex)})
                traceback.print_exc()
            time.sleep(1.5)
        out.append(e)
    return out


# ---------------------------------------------------------------- 앱스토어·플레이 (한국어판만 낸다)

def ios_lookup():
    d = get_json("https://itunes.apple.com/lookup?id=%d&country=kr" % IOS_ID)
    if not d.get("results"):
        return {"listed": False}
    r = d["results"][0]
    return {"listed": True, "name": r.get("trackName"), "version": r.get("version"), "updated": r.get("currentVersionReleaseDate"),
            "rating": r.get("averageUserRating"), "ratings": r.get("userRatingCount", 0)}


def ios_reviews():
    d = get_json("https://itunes.apple.com/kr/rss/customerreviews/page=1/id=%d/sortby=mostrecent/json" % IOS_ID)
    out = []
    for e in d.get("feed", {}).get("entry", []) or []:
        if "im:rating" not in e:
            continue
        out.append({"rating": int(e["im:rating"]["label"]), "title": e["title"]["label"], "text": e["content"]["label"][:400],
                    "ver": e.get("im:version", {}).get("label", ""), "date": e.get("updated", {}).get("label", "")[:10]})
    return out[:10]


def play_app():
    from google_play_scraper import app
    r = app(PLAY_ID, lang="ko", country="kr")
    return {"title": r.get("title"), "installs": r.get("installs"), "real": r.get("realInstalls"), "score": round(r["score"], 2) if r.get("score") else None,
            "ratings": r.get("ratings") or 0, "reviews": r.get("reviews") or 0, "version": r.get("version"), "updated": r.get("updated")}


def play_reviews():
    from google_play_scraper import Sort, reviews
    rows, _ = reviews(PLAY_ID, lang="ko", country="kr", sort=Sort.NEWEST, count=10)
    out = []
    for r in rows:
        at = r.get("at")
        out.append({"rating": int(r.get("score") or 0), "title": "", "text": (r.get("content") or "")[:400], "ver": r.get("reviewCreatedVersion") or r.get("appVersion") or "",
                    "date": at.astimezone(KST).strftime("%Y-%m-%d") if at else "", "replied": bool(r.get("replyContent"))})
    return out


# ---------------------------------------------------------------- 유튜브 공식 영상

YT_COOKIE = {"Cookie": "CONSENT=YES+cb; SOCS=CAI"}
GAME = re.compile(r"오도\s?짜세|odojjase", re.I)


def yt_watch(vid):
    """영상 페이지에서 제목·조회수·올린 때"""
    page = get("https://www.youtube.com/watch?v=%s&hl=en&gl=US" % vid, headers=YT_COOKIE, tries=2)
    t = re.search(r'"videoDetails":\{"videoId":"%s","title":"((?:[^"\\]|\\.)*)"' % re.escape(vid), page)
    v = re.search(r'"videoDetails":\{.*?"viewCount":"(\d+)"', page, re.S)
    d = re.search(r'"publishDate":"([^"]+)"', page) or re.search(r'"uploadDate":"([^"]+)"', page)
    if not v:
        raise RuntimeError("조회수를 못 찾았다")
    return {"id": vid, "title": json.loads('"%s"' % t.group(1)) if t else "", "views": int(v.group(1)), "published": d.group(1) if d else ""}


def youtube(prev):
    """채널 RSS(최근 15편, 조회수가 들어 있다)에서 제목에 '오도짜세'가 든 영상 + 전에 알던 영상·공식 트레일러(영상 페이지에서)"""
    now, vids = now_iso(), {}
    try:
        page = get("https://www.youtube.com/@%s?hl=en&gl=US" % YT_HANDLE, headers=YT_COOKIE, tries=2)
        cid = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page).group(1)
        xml = get("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid, tries=2)
        for ent in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
            title = unescape(re.search(r"<title>(.*?)</title>", ent, re.S).group(1))
            views = re.search(r'<media:statistics views="(\d+)"', ent)
            if GAME.search(title) and views:
                vid = re.search(r"<yt:videoId>(.*?)</yt:videoId>", ent).group(1)
                vids[vid] = {"id": vid, "title": title, "published": re.search(r"<published>(.*?)</published>", ent).group(1), "views": int(views.group(1)), "checked": now}
    except Exception as e:
        errors.append({"what": "유튜브 채널 RSS", "why": "%s — 알던 영상만 영상 페이지에서 읽었다" % why(e)[:120]})
    old = {v["id"]: dict(v) for v in ((prev or {}).get("videos") or []) if v.get("id") and v["id"] not in vids}
    for vid in YT_OWN:
        if vid not in vids and vid not in old:
            old[vid] = {"id": vid, "title": "", "published": "", "views": None, "checked": ""}
    failed = 0
    for v in sorted(old.values(), key=lambda v: (v.get("views") is not None, v.get("checked") or ""))[:12]:
        try:
            w = yt_watch(v["id"])
            v.update({"title": w["title"] or v.get("title", ""), "published": w["published"] or v.get("published", ""), "views": w["views"], "checked": now})
        except Exception:
            failed += 1
        time.sleep(0.7)
    if failed:
        errors.append({"what": "유튜브 영상 조회수", "why": "%d편을 못 읽어 지난 값을 그대로 뒀다" % failed})
    allv = list(vids.values()) + [v for v in old.values() if v.get("views") is not None]
    if not allv:
        raise RuntimeError("영상을 하나도 못 읽었다")
    allv.sort(key=lambda v: v.get("published") or "", reverse=True)
    return {"videos": allv, "total_views": sum(v["views"] or 0 for v in allv)}


# ---------------------------------------------------------------- 다른 곳에서 다룬 것
# 수집할 때마다 읽는다 — 다 키가 없고 가볍다. '오도짜세'도 '오도짜세 상륙장갑차'도 해병문학에서 흔한 말이라
# 게임 이야기인 것만 남긴다: 게임 이름('오도짜세! 돌격…')이 그대로 있거나, '오도짜세'와 함께 게임을 가리키는 말이 있어야 한다.
# 한 번 본 것은 지난 수집본에서 이어 받아 처음 본 때(first)를 지킨다 — 페이지가 '새'를 붙인다.
NAME = re.compile(r"오도\s?짜세\s*[!！]?\s*돌격|odojjase", re.I)
HINT = re.compile(r"로그라이크|성기소프트|sungkisoft|좀비|게임|겜|출시|스토어|구글\s?플레이|해봤|해보[니고는]|후기|리뷰|공략|플레이|모바일|신작|인디|다운|설치|트레일러", re.I)
OWN = re.compile(r"seolhwadungeon\.com|opop4615\.github\.io|play\.google\.com|apps\.apple\.com|youtube\.com/@sungkisoft", re.I)
MENTION_KEEP = 300


def relevant(text):
    return bool(NAME.search(text) or (GAME.search(text) and HINT.search(text)))


def tracked(url):
    """POSTS에 든 내 글인가 — 그 글은 '내 글 반응'에 따로 나온다"""
    for p in POSTS:
        ident = {"clien": lambda: p["url"].split("/service/board/")[-1], "dc": lambda: p["url"].split("?")[-1],
                 "fm": lambda: p["url"].rstrip("/").split("/")[-1], "cafe": lambda: p["url"].split("cafe.naver.com/")[-1]}.get(p["kind"])
        if ident and ident() in url:
            return True
    return False


def gnews(q):
    xml = get("https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}), tries=2)
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        g = lambda tag: (re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), it, re.S) or [None, ""])[1]
        title, desc = unescape(g("title")), unescape(re.sub(r"<[^>]+>", " ", unescape(g("description"))))
        if not relevant(title + " " + desc):
            continue
        try:
            when = datetime.strptime(g("pubDate").strip(), "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            when = ""
        src = unescape(g("source"))
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        out.append({"kind": "news", "via": "구글 뉴스", "title": title, "url": g("link").strip(), "where": src, "date": when})
    return out


def naver(tab):
    """네이버 검색 탭(news·blog·cafe) 최신순. 결과마다 앞뒤 글에 게임 이야기가 있어야 남긴다"""
    page = get("https://search.naver.com/search.naver?" + urllib.parse.urlencode({"ssc": "tab.%s.all" % tab, "query": "오도짜세 돌격 상륙장갑차", "sm": "tab_opt", "nso": "so:dd,p:all"}), tries=2)
    dom = {"news": r"https?://[^\"\s]+", "blog": r"https://(?:m\.)?blog\.naver\.com/[^\"\s]+", "cafe": r"https://(?:m\.)?cafe\.naver\.com/[^\"\s]+"}[tab]
    groups = {}  # 주소 → 그 주소로 가는 고리의 글자들(제목, 요약, 답글 …). 옆 결과의 글이 섞이지 않게 주소로 묶는다
    for m in re.finditer(r'<a[^>]+href="(%s)"[^>]*>(.*?)</a>' % dom, page, re.S):
        url = unescape(m.group(1))
        if tab in ("blog", "cafe"):
            if not re.search(r"\.naver\.com/[\w-]+/\d+", url):  # 글 번호가 없는 주소는 카페·블로그 첫 화면
                continue
            url = url.split("?")[0]  # ?art= 꼬리는 검색할 때마다 바뀐다 — 같은 글이 겹치지 않게
        if "pstatic" in url or "search.naver" in url or "keep.naver" in url or OWN.search(url) or tracked(url):
            continue
        text = unescape(re.sub(r"<[^>]+>", "", m.group(2))).replace("새 창 열림", "").strip()
        if text and "바로가기" not in text:
            groups.setdefault(url, []).append(text)
    out = []
    for url, texts in groups.items():
        title = next((t for t in texts if len(t) >= 6), "")
        if not title or not relevant(" ".join(texts)):
            continue
        where = re.search(r"(?:cafe|blog)\.naver\.com/([\w-]+)/", url)
        host = urllib.parse.urlparse(url).netloc.replace("www.", "")  # 기사는 매체 주소를 출처로
        out.append({"kind": tab, "via": "네이버", "title": title[:120], "url": url, "where": where.group(1) if where else host, "date": ""})
    return out[:15]


def yt_search(q):
    page = get("https://www.youtube.com/results?search_query=%s&sp=CAI%%253D&hl=ko&gl=KR" % urllib.parse.quote(q), headers=YT_COOKIE, tries=2)
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
    if not m:
        raise RuntimeError("유튜브 검색 결과를 못 읽었다")
    runs = lambda o: "".join(r.get("text", "") for r in (o or {}).get("runs", []))
    out, stack = [], [json.loads(m.group(1))]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if isinstance(v, dict) and v.get("videoId"):
                title, ch = runs(v.get("title")), runs(v.get("ownerText"))
                snip = " ".join(runs(x.get("snippetText")) for x in v.get("detailedMetadataSnippets") or [] if isinstance(x, dict))
                mine = ch.lower() == YT_HANDLE or "@sungkisoft" in json.dumps(v.get("ownerText", {})).lower()
                if not mine and relevant(title + " " + snip):
                    out.append({"kind": "youtube", "via": "유튜브", "title": title, "url": "https://youtu.be/" + v["videoId"], "where": ch, "date": "",
                                "when": v.get("publishedTimeText", {}).get("simpleText", ""), "views_text": v.get("viewCountText", {}).get("simpleText", "")})
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return out


def chzzk(kind):
    """치지직 검색(다시보기 videos · 생방송 lives). 메일 보낸 스트리머가 켰는지 본다"""
    d = get_json("https://api.chzzk.naver.com/service/v1/search/%s?" % kind + urllib.parse.urlencode({"keyword": "오도짜세", "size": 20, "offset": 0}), tries=2)
    out = []
    for x in ((d.get("content") or {}).get("data") or []):
        ch = x.get("channel") or {}
        if kind == "videos":
            v = x.get("video") or {}
            title, cat, url = v.get("videoTitle") or "", v.get("videoCategoryValue") or "", "https://chzzk.naver.com/video/%s" % v.get("videoNo")
            t = v.get("publishDateAt")
            when = datetime.fromtimestamp(t / 1000, KST).isoformat(timespec="seconds") if isinstance(t, (int, float)) else ""
            views = "조회 %s" % format(v["readCount"], ",") if isinstance(v.get("readCount"), int) else ""
        else:
            v = x.get("live") or {}
            title, cat, url = v.get("liveTitle") or "", v.get("liveCategoryValue") or "", "https://chzzk.naver.com/live/%s" % ch.get("channelId")
            when = kst_iso(v.get("openDate") or "")
            views = "지금 %s명" % format(v["concurrentUserCount"], ",") if isinstance(v.get("concurrentUserCount"), int) else ""
        if GAME.search(title + " " + cat):
            out.append({"kind": "stream", "via": "치지직 " + ("다시보기" if kind == "videos" else "생방송"), "title": title[:120], "url": url,
                        "where": ch.get("channelName") or "", "date": when, "views_text": views})
    return out


def search_clien():
    """클리앙 통합 검색(최신순). '오도짜세'만으로는 해병 글이 섞여 온다 — 제목이 게임 이야기인 것만"""
    s = soup(get("https://www.clien.net/service/search?" + urllib.parse.urlencode({"q": "오도짜세 장갑차", "sort": "recency"}), tries=2))
    out = []
    for it in s.select("div.list_item[data-board-sn]"):
        a = it.select_one("a.subject_fixed")
        if not a or not a.get("href"):
            continue
        url = "https://www.clien.net" + a["href"].split("?")[0]
        title = a.get("title") or T(a)
        if tracked(url) or not relevant(title):
            continue
        out.append({"kind": "community", "via": "클리앙", "title": title[:120], "url": url, "where": "클리앙 · " + (T(it.select_one(".shortname")) or "게시판"),
                    "date": kst_iso(T(it.select_one(".timestamp"))), "comments": count(it.get("data-comment-count"))})
    return out


def search_dc(gall, name, known, skip, me, budget):
    """디시 갤러리 안 검색(제목+내용에 '오도짜세'). 제목만으로 게임 이야기인지 모르는 글은 한 번 열어 본문을 본다 —
    아닌 글은 skip에 적어 다시 열지 않는다. known = 전에 고른 글, me = 내 글의 글쓴이 표시, budget = 이번에 더 열어 볼 수 있는 글 수([n])"""
    s = soup(get("https://gall.dcinside.com/mgallery/board/lists/?" + urllib.parse.urlencode({"id": gall, "s_type": "search_subject_memo", "s_keyword": "오도짜세"}), tries=2))
    if not s.select_one("table.gall_list"):
        raise RuntimeError("목록을 못 찾았다(화면이 바뀌었거나 확인 화면이 떴다)")
    out = []
    for tr in s.select("tr.ub-content.us-post[data-no]"):
        a = tr.select_one("td.gall_tit a")
        if not a:
            continue
        url = "https://gall.dcinside.com/mgallery/board/view/?id=%s&no=%s" % (gall, tr["data-no"])
        title = T(a)
        if tracked(url) or url in skip:
            continue
        who = dc_who(tr.select_one("td.gall_writer"))
        mine = bool(who) and who in me  # 내 글과 글쓴이 표시가 같다 — 검색에 걸렸으면 본문을 안 열어 봐도 게임 이야기다
        if url not in known and not mine and not relevant(title):
            if budget[0] <= 0:
                continue  # 다음 수집 때 본다
            budget[0] -= 1
            time.sleep(1.5)
            page = soup(get(url, tries=2))
            if not relevant(title + " " + T(page.select_one("div.write_div"))):
                skip.add(url)
                continue
        d = tr.select_one("td.gall_date")
        x = {"kind": "community", "via": "디시", "title": title[:120], "url": url, "where": "디시 · %s%s" % (name, " · " + who if who else ""),
             "date": kst_iso(d.get("title") or "") if d is not None else "", "comments": count(T(tr.select_one("span.reply_num"))) or 0,
             "views_text": "조회 " + T(tr.select_one("td.gall_count")) if tr.select_one("td.gall_count") else ""}
        if mine:
            x["own"] = True  # 페이지가 '내 글'로 보인다
        out.append(x)
    return out


def search_fm():
    """에펨코리아 모바일게임 게시판 검색(제목+내용)"""
    s = soup(get("https://www.fmkorea.com/search.php?" + urllib.parse.urlencode({"mid": "mgame", "search_keyword": "오도짜세", "search_target": "title_content"}), tries=2))
    if not s.select_one(".bd_lst, .fm_best_widget, table"):
        raise RuntimeError("목록을 못 찾았다(확인 화면이 떴다)")
    out = []
    for a in s.select("a.hx"):
        m = re.search(r"document_srl=(\d+)", a.get("href", "")) or re.search(r"/(\d{6,})", a.get("href", ""))
        if not m:
            continue
        url = "https://www.fmkorea.com/" + m.group(1)
        tr = a.find_parent("tr")
        if tracked(url):
            continue
        out.append({"kind": "community", "via": "에펨코리아", "title": T(a)[:120], "url": url,
                    "where": "에펨코리아 · 모바일게임" + (" · " + T(tr.select_one("td.author")) if tr and tr.select_one("td.author") else ""),
                    "date": "", "when": T(tr.select_one("td.time")) if tr else "", "comments": count(T(tr.select_one("a.replyNum"))) or 0 if tr else None})
    return out


def mentions(prev, posts_now):
    now, found = now_iso(), []
    old = {x["url"]: x for x in ((prev or {}).get("items") or [])}
    skip = set((prev or {}).get("skip") or [])
    me = {p["who"] for p in posts_now or [] if p.get("kind") == "dc" and p.get("who")}
    budget = [8]

    def take(name, fn, *a, pause=1.5):
        try:
            found.extend(fn(*a))
        except Exception as e:
            errors.append({"what": name, "why": why(e)})
        time.sleep(pause)

    take("구글 뉴스", gnews, '"오도짜세" 장갑차')
    for tab, nm in (("news", "뉴스"), ("blog", "블로그"), ("cafe", "카페")):
        take("네이버 %s 검색" % nm, naver, tab, pause=2)
    take("유튜브 검색", yt_search, "오도짜세 상륙장갑차", pause=2)
    take("치지직 다시보기 검색", chzzk, "videos")
    take("치지직 생방송 검색", chzzk, "lives")
    take("클리앙 검색", search_clien)
    for gall, name in DC_GALLS:
        take("디시 %s 검색" % name, search_dc, gall, name, old, skip, me, budget)
    take("에펨코리아 검색", search_fm)
    items = dict(old)
    for x in found:
        o = old.get(x["url"]) or {}
        x["first"] = o.get("first", now)
        # 남의 글에 댓글이 늘면 그때를 적는다(bump) — 페이지가 '댓글 +N'을 붙인다
        if isinstance(x.get("comments"), int) and isinstance(o.get("comments"), int) and x["comments"] > o["comments"]:
            x["bump"], x["bump_n"] = now, x["comments"] - o["comments"]
        elif o.get("bump"):
            x["bump"], x["bump_n"] = o["bump"], o.get("bump_n")
        items[x["url"]] = x
    lst = sorted(items.values(), key=lambda x: x.get("date") or x.get("first") or "", reverse=True)[:MENTION_KEEP]
    return {"at": now, "items": lst, "skip": sorted(skip)[-MENTION_KEEP:]}


# ---------------------------------------------------------------- 묶기

def metrics(s):
    """history.jsonl 한 줄에 들어갈 숫자들. 열쇠는 페이지(index.html)의 label()과 맞춘다"""
    m = {}
    h = s.get("hall") or {}
    for k in ["total_runs", "total_people", "today_runs", "today_people", "today_new", "wins"]:
        if k in h:
            m["hall." + k] = h[k]
    for p in s.get("posts") or []:
        for k in ("views", "likes", "comments"):
            if isinstance(p.get(k), int):
                m["post.%s.%s" % (p["key"], k)] = p[k]
    a = s.get("ios") or {}
    if a.get("listed"):
        m["ios.ratings"] = a.get("ratings", 0)
        if a.get("rating") is not None:
            m["ios.rating"] = round(a["rating"], 2)
    p = s.get("play") or {}
    if p.get("real") is not None:
        m["play.real"] = p["real"]
    if p:
        m["play.ratings"] = p.get("ratings") or 0
        if p.get("score") is not None:
            m["play.score"] = p["score"]
    for v in (s.get("youtube") or {}).get("videos") or []:
        if v.get("views") is not None:
            m["yt.v." + v["id"]] = v["views"]
    if s.get("mentions"):
        m["mentions.n"] = len(s["mentions"].get("items") or [])
    return m


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    s = {"at": now_iso()}
    prev = {}
    try:
        prev = json.load(open(os.path.join(out, "latest.json")))
    except Exception:
        pass
    s["hall"] = step("명예해병 명부", hall, out)
    s["posts"] = posts(prev.get("posts"))
    s["ios"] = step("앱스토어 페이지", ios_lookup)
    s["reviews"] = step("앱스토어 리뷰", ios_reviews)
    s["play"] = step("플레이 페이지", play_app)
    time.sleep(2)
    s["play_reviews"] = step("플레이 리뷰", play_reviews)
    s["youtube"] = step("유튜브", youtube, prev.get("youtube"))
    s["mentions"] = step("다른 곳에서 다룬 것", mentions, prev.get("mentions"), s["posts"])
    # 통째로 못 읽은 갈래는 지난 값을 둔다(errors에는 남는다). 리뷰 목록은 가끔 비어 온다 — 그럴 때도 지난 목록을 둔다
    for k in ["hall", "ios", "play", "youtube", "mentions"]:
        if s[k] is None and prev.get(k):
            s[k] = prev[k]
    for k in ["reviews", "play_reviews"]:
        if not s[k] and prev.get(k):
            s[k] = prev[k]
    s["errors"] = errors
    s["m"] = metrics(s)
    json.dump(s, open(os.path.join(out, "latest.json"), "w"), ensure_ascii=False, default=str)
    hp = os.path.join(out, "history.jsonl")
    lines = open(hp).read().splitlines() if os.path.exists(hp) else []
    lines.append(json.dumps({"at": s["at"], "m": s["m"]}, ensure_ascii=False, separators=(",", ":")))
    open(hp, "w").write("\n".join(lines[-KEEP:]) + "\n")
    print("수집 끝: 값 %d개, 못 읽은 것 %d개" % (len(s["m"]), len(errors)))
    for e in errors:
        print("  못 읽음 · %s · %s" % (e["what"], e["why"]))


if __name__ == "__main__":
    main()
