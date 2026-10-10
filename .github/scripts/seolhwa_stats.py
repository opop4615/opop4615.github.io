#!/usr/bin/env python3
"""설화던전 현황 수집기. GitHub Actions(.github/workflows/seolhwa-stats.yml)가 한 시간마다 돌린다.

  python3 seolhwa_stats.py <출력 폴더>

출력 폴더에 latest.json(지금 값 전부)을 쓰고 history.jsonl(숫자만, 한 줄 = 한 번 수집)에 한 줄 덧붙인다.
페이지(seolhwa-stats/index.html)는 이 둘을 읽어 "달라진 것"을 만든다.
브라우저가 직접 못 읽는 곳(플레이·앱스토어 차트·리뷰·유튜브)은 여기서만 읽는다.

한 곳이 실패해도 나머지는 계속한다 — 실패는 errors에 적고, 그 값은 비운다(페이지가 "못 읽음"으로 보인다).
YT_API_KEY 환경변수가 있으면 유튜브 Data API로, 없으면 채널 RSS(최근 15편) + 채널 탭·영상 페이지(그 전 영상) + 검색 페이지(다른 채널)로 읽는다.
"""
import json, os, re, sys, time, traceback, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

IOS_ID = 6814757012
PLAY_ID = "com.sungkisoft.seolhwadungeon"
YT_HANDLE = "sungkisoft"
FS_DOCS = "https://firestore.googleapis.com/v1/projects/seolhwa-dungeon/databases/(default)/documents"
FS_KEY = "AIzaSyCHvKcIPGZKwk5lwPmvg7AuU1iAhuMheRw"  # 게임에 박혀 나가는 공개 웹 키(hall.gd와 같다). 막는 일은 firestore.rules가 한다
KST = timezone(timedelta(hours=9))
LAUNCH = datetime(2026, 10, 2, tzinfo=KST)  # 전당 "출시 뒤"를 세는 날 (marketing/calendar.md 점검과 같은 기준)
KEEP = 24 * 21  # history.jsonl에 남길 줄 수(약 3주)

# 검색 순위를 볼 낱말. 페이지(index.html)의 같은 표와 맞춘다
IOS_TERMS = {"kr": ["설화던전", "정통 로그라이크", "던전크롤", "던전", "턴제 로그라이크", "로그라이크", "한국 설화", "설화", "도깨비"],
             "us": ["Seolhwa Dungeon", "korean folklore", "traditional roguelike", "roguelike", "dungeon crawl", "roguelike rpg", "dokkaebi"]}
PLAY_TERMS = {"kr": ["설화던전", "한국 설화 게임", "정통 로그라이크", "던전크롤", "로그라이크", "설화"],
              "us": ["Seolhwa Dungeon", "korean folklore roguelike", "traditional roguelike", "roguelike"]}
# 애플 차트 (무료, 기기·장르 번호). 웹 차트 페이지에 200위까지 실려 온다(2026-10-10) — 못 읽으면 공개 RSS(100위까지)로
CHARTS = [("kr", "iphone", 7014, "롤플레잉"), ("kr", "iphone", 7001, "액션"), ("kr", "iphone", 6014, "게임"), ("kr", "ipad", 7014, "롤플레잉"),
          ("us", "iphone", 7014, "롤플레잉"), ("us", "iphone", 6014, "게임")]
# 플레이 설명문에 장르 낱말이 들어갔는지 (2026-10-09: 옛 글엔 셋 다 0번이었다)
GENRE_WORDS = {"kr": ["로그라이크", "턴제", "RPG", "던전크롤", "도트"], "us": ["roguelike", "turn-based", "RPG", "dungeon crawl", "pixel"]}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

errors = []


def get(url, headers=None, tries=3):
    h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
    h.update(headers or {})
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def step(name, fn, *a):
    """한 갈래를 돌리고, 실패하면 errors에 남기고 None"""
    try:
        return fn(*a)
    except Exception as e:
        errors.append({"what": name, "why": "%s: %s" % (type(e).__name__, str(e)[:200])})
        traceback.print_exc()
        return None


def hangul(s):
    return bool(re.search("[가-힣]", s or ""))


# ---------------------------------------------------------------- 앱스토어

def ios_lookup(cc):
    d = get_json("https://itunes.apple.com/lookup?id=%d&country=%s" % (IOS_ID, cc))
    if not d.get("results"):
        return {"listed": False}
    r = d["results"][0]
    return {"listed": True, "name": r.get("trackName"), "version": r.get("version"),
            "updated": r.get("currentVersionReleaseDate"), "rating": r.get("averageUserRating"),
            "ratings": r.get("userRatingCount", 0), "english": not hangul(r.get("description", "")[:300]),
            "desc_head": (r.get("description") or "")[:140], "shots": len(r.get("screenshotUrls") or [])}


def ios_rank(cc, term):
    q = urllib.parse.urlencode({"term": term, "country": cc, "entity": "software", "limit": 200})
    rows = get_json("https://itunes.apple.com/search?" + q).get("results", [])
    for i, r in enumerate(rows, 1):
        if r.get("trackId") == IOS_ID:
            return i
    return None


def chart_key(cc, device, genre):
    """아이폰은 옛 열쇠 그대로(kr.7014) — history.jsonl과 이어지게. 아이패드만 뒤에 붙인다"""
    return "%s.%d" % (cc, genre) + ("" if device == "iphone" else "." + device)


def ios_chart_web(cc, device, genre):
    """앱스토어 웹 차트. 화면에는 25개만 그려지지만 페이지에 실린 JSON에 나머지 175개의 번호가 차례대로 들어 있다
    (segments[].shelves = 그려진 25개, segments[].nextPage.remainingContent = 그 뒤). 2026-10-09에 '50개만 준다'고 본 것은 그려진 것만 센 탓"""
    page = get("https://apps.apple.com/%s/%s/charts/%d" % (cc, device, genre), headers={"Accept-Language": "en-US,en;q=0.9"})
    for raw in re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', page, re.S):
        try:
            segs = json.loads(raw)["data"][0]["data"]["segments"]
        except Exception:
            continue
        for seg in segs:
            if seg.get("chart") != "top-free":
                continue
            ids = []

            def walk(o):
                if isinstance(o, dict):
                    a = o.get("adamId")
                    if isinstance(a, str) and a not in ids:
                        ids.append(a)
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(seg.get("shelves"))
            for x in (seg.get("nextPage") or {}).get("remainingContent", []):
                if x.get("id") and x["id"] not in ids:
                    ids.append(x["id"])
            if len(ids) >= 50:
                return (ids.index(str(IOS_ID)) + 1 if str(IOS_ID) in ids else None), len(ids)
    raise RuntimeError("웹 차트에서 목록을 못 찾았다")


def ios_chart_rss(cc, device, genre):
    kind = "topfreeapplications" if device == "iphone" else "topfreeipadapplications"
    feed = get_json("https://itunes.apple.com/%s/rss/%s/limit=200/genre=%d/json" % (cc, kind, genre))["feed"]
    rows = feed.get("entry", [])
    if not rows:
        raise RuntimeError("빈 목록")
    for i, e in enumerate(rows, 1):
        if e["id"]["attributes"].get("im:id") == str(IOS_ID):
            return i, len(rows)
    return None, len(rows)


def ios_chart(cc, device, genre):
    """(순위 또는 None, 목록 길이)"""
    try:
        return ios_chart_web(cc, device, genre)
    except Exception as e:
        r = ios_chart_rss(cc, device, genre)
        errors.append({"what": "앱스토어 차트 %s %s %d (200위까지)" % (cc, device, genre), "why": "%s — 공개 목록(100위까지)으로 읽었다" % str(e)[:120]})
        return r


def ios_reviews(cc):
    d = get_json("https://itunes.apple.com/%s/rss/customerreviews/page=1/id=%d/sortby=mostrecent/json" % (cc, IOS_ID))
    out = []
    for e in d.get("feed", {}).get("entry", []) or []:
        if "im:rating" not in e:
            continue
        out.append({"rating": int(e["im:rating"]["label"]), "title": e["title"]["label"],
                    "text": e["content"]["label"][:400], "ver": e.get("im:version", {}).get("label", ""),
                    "date": e.get("updated", {}).get("label", "")[:10]})
    return out[:10]


# ---------------------------------------------------------------- 구글 플레이 (google-play-scraper)

def play_app(cc, lang):
    from google_play_scraper import app
    r = app(PLAY_ID, lang=lang, country=cc)
    desc = r.get("description") or ""
    words = GENRE_WORDS["kr" if lang == "ko" else "us"]
    return {"title": r.get("title"), "installs": r.get("installs"), "real": r.get("realInstalls"),
            "score": round(r["score"], 2) if r.get("score") else None, "ratings": r.get("ratings") or 0,
            "reviews": r.get("reviews") or 0, "version": r.get("version"), "updated": r.get("updated"),
            "english": not hangul(desc[:300]), "desc_head": desc[:140],
            # 한국어는 띄어쓰기를 지우고 센다 — '던전 크롤'과 '던전크롤'을 같은 낱말로
            "genre_words": {w: (desc.replace(" ", "") if lang == "ko" else desc).lower().count(w.lower()) for w in words}}


def play_rank(cc, lang, term):
    """(순위 또는 None, 목록 길이). 검색 페이지에 실린 앱 링크의 차례 — google-play-scraper의 search는 맨 위 큰 카드의
    앱 번호를 비워 줄 때가 있다(2026-10-10 '던전크롤'). 비어 오면(구글이 잠깐 막을 때) 몇 초 쉬고 다시, 끝내 비면 scraper로"""
    url = "https://play.google.com/store/search?" + urllib.parse.urlencode({"q": term, "c": "apps", "hl": lang, "gl": cc.upper()})
    ids = []
    for i in range(3):
        try:
            page = get(url, headers={"Accept-Language": "ko-KR,ko;q=0.9" if lang == "ko" else "en-US,en;q=0.9"}, tries=1)
            for m in re.finditer(r"/store/apps/details\?id=([\w.]+)", page):
                if m.group(1) not in ids:
                    ids.append(m.group(1))
        except Exception:
            pass
        if ids:
            break
        time.sleep(5 * (i + 1))
    if not ids:
        from google_play_scraper import search
        ids = [r.get("appId") for r in search(term, lang=lang, country=cc, n_hits=30)]
    if not ids:
        raise RuntimeError("빈 결과")
    return (ids.index(PLAY_ID) + 1 if PLAY_ID in ids else None), len(ids)


def play_reviews(cc, lang):
    from google_play_scraper import Sort, reviews
    rows, _ = reviews(PLAY_ID, lang=lang, country=cc, sort=Sort.NEWEST, count=10)
    out = []
    for r in rows:
        at = r.get("at")
        out.append({"rating": int(r.get("score") or 0), "title": "", "text": (r.get("content") or "")[:400],
                    "ver": r.get("reviewCreatedVersion") or r.get("appVersion") or "",
                    "date": at.astimezone(KST).strftime("%Y-%m-%d") if at else "", "replied": bool(r.get("replyContent"))})
    return out


# ---------------------------------------------------------------- 유튜브

def yt_api(path, **q):
    q["key"] = os.environ["YT_API_KEY"]
    return get_json("https://www.googleapis.com/youtube/v3/%s?%s" % (path, urllib.parse.urlencode(q)))


SEARCH_EVERY = 6  # 다른 채널 검색(search.list)은 한 번에 100단위라 여섯 시간에 한 번만. 나머지 호출은 한 번에 3단위 남짓


def youtube(prev):
    """prev = 지난 수집의 youtube. 키가 있어도 API가 실패하면(하루 한도 10,000단위를 넘기는 등) 키 없는 길로 내려간다"""
    if os.environ.get("YT_API_KEY"):
        try:
            return youtube_api(prev or {})
        except Exception as e:
            errors.append({"what": "유튜브 API", "why": "%s — 키 없이 읽었다" % str(e)[:160]})
    return youtube_rss(prev or {})


def youtube_api(prev):
    ch = yt_api("channels", part="snippet,statistics,contentDetails", forHandle=YT_HANDLE)["items"][0]
    up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, tok = [], ""
    for _ in range(4):
        q = {"part": "contentDetails", "playlistId": up, "maxResults": 50}
        if tok:
            q["pageToken"] = tok
        d = yt_api("playlistItems", **q)
        ids += [x["contentDetails"]["videoId"] for x in d.get("items", [])]
        tok = d.get("nextPageToken", "")
        if not tok:
            break
    vids = []
    for i in range(0, len(ids), 50):
        for v in yt_api("videos", part="snippet,statistics", id=",".join(ids[i:i + 50])).get("items", []):
            s = v.get("statistics", {})
            vids.append({"id": v["id"], "title": v["snippet"]["title"], "published": v["snippet"]["publishedAt"],
                         "views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                         "comments": int(s.get("commentCount", 0))})
    others, others_at = prev.get("others"), prev.get("others_at")
    due = others is None or not others_at or datetime.now(KST) - datetime.fromisoformat(others_at) >= timedelta(hours=SEARCH_EVERY) - timedelta(minutes=10)
    if due:
        try:
            d = yt_api("search", part="snippet", q='설화던전|"Seolhwa Dungeon"', type="video", maxResults=25, order="date")
            others = [{"id": x["id"]["videoId"], "title": x["snippet"]["title"], "channel": x["snippet"]["channelTitle"],
                       "published": x["snippet"]["publishedAt"]} for x in d.get("items", [])
                      if x["snippet"]["channelId"] != ch["id"]]
            others_at = datetime.now(KST).isoformat(timespec="seconds")
        except Exception as e:
            errors.append({"what": "유튜브 다른 채널 검색", "why": str(e)[:200]})
    st = ch["statistics"]
    return {"via": "api", "channel_id": ch["id"], "subs": int(st.get("subscriberCount", 0)),
            "total_views": int(st.get("viewCount", 0)), "videos": vids, "others": others, "others_at": others_at}


YT_COOKIE = {"Cookie": "CONSENT=YES+cb; SOCS=CAI"}
OLD_REFRESH = 25  # RSS(최근 15편) 밖의 옛 영상은 영상 페이지를 하나씩 열어야 조회수가 나온다 — 한 번에 이만큼만, 오래 안 본 것부터


def yt_tab_ids(tab):
    page = get("https://www.youtube.com/@%s/%s?hl=en&gl=US" % (YT_HANDLE, tab), headers=YT_COOKIE)
    ids = []
    for m in re.finditer(r'"videoId":"([\w-]{11})"', page):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def yt_watch(vid):
    """영상 페이지에서 제목·조회수·올린 때·채널 이름"""
    page = get("https://www.youtube.com/watch?v=%s&hl=en&gl=US" % vid, headers=YT_COOKIE, tries=2)
    t = re.search(r'"videoDetails":\{"videoId":"%s","title":"((?:[^"\\]|\\.)*)"' % re.escape(vid), page)
    v = re.search(r'"videoDetails":\{.*?"viewCount":"(\d+)"', page, re.S)
    d = re.search(r'"publishDate":"([^"]+)"', page) or re.search(r'"uploadDate":"([^"]+)"', page)
    o = re.search(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"', page)
    if not v:
        raise RuntimeError("조회수를 못 찾았다")
    return {"id": vid, "title": json.loads('"%s"' % t.group(1)) if t else "", "views": int(v.group(1)),
            "published": d.group(1) if d else "", "owner": json.loads('"%s"' % o.group(1)) if o else ""}


def yt_others(prev):
    """다른 채널이 올린 영상: 유튜브 검색(최근순) 결과에서 제목에 게임 이름이 든 남의 영상. 여섯 시간에 한 번"""
    others, others_at = prev.get("others"), prev.get("others_at")
    due = others is None or not others_at or datetime.now(KST) - datetime.fromisoformat(others_at) >= timedelta(hours=SEARCH_EVERY) - timedelta(minutes=10)
    if not due:
        return others, others_at
    try:
        found = {}
        for q in ["설화던전", "Seolhwa Dungeon"]:
            page = get("https://www.youtube.com/results?search_query=%s&sp=CAI%%253D&hl=ko&gl=KR" % urllib.parse.quote(q), headers=YT_COOKIE)
            m = re.search(r"var ytInitialData = (\{.*?\});</script>", page, re.S)
            if not m:
                raise RuntimeError("검색 결과를 못 읽었다 ('%s')" % q)
            stack = [json.loads(m.group(1))]
            while stack:
                o = stack.pop()
                if isinstance(o, dict):
                    v = o.get("videoRenderer")
                    if isinstance(v, dict) and v.get("videoId"):
                        title = "".join(r.get("text", "") for r in v.get("title", {}).get("runs", []))
                        ch = "".join(r.get("text", "") for r in v.get("ownerText", {}).get("runs", []))
                        if ch.lower() != YT_HANDLE and re.search(r"설화\s?던전|seolhwa", title, re.I):
                            found[v["videoId"]] = {"id": v["videoId"], "title": title, "channel": ch, "published": "",
                                                   "when": v.get("publishedTimeText", {}).get("simpleText", ""),
                                                   "views_text": v.get("viewCountText", {}).get("simpleText", "")}
                    stack.extend(o.values())
                elif isinstance(o, list):
                    stack.extend(o)
            time.sleep(2)
        return list(found.values()), datetime.now(KST).isoformat(timespec="seconds")
    except Exception as e:
        errors.append({"what": "유튜브 다른 채널 검색(키 없이)", "why": str(e)[:200]})
        return others, others_at


def youtube_rss(prev):
    """키 없이: 채널 페이지에서 채널 번호·구독자 → RSS(최근 15편, 조회수는 들어 있다)
    → 채널의 쇼츠·동영상 탭에서 나머지 영상 번호 → 영상 페이지에서 조회수(한 번에 OLD_REFRESH편씩, 오래 안 본 것부터)"""
    page = get("https://www.youtube.com/@%s?hl=en&gl=US" % YT_HANDLE, headers=YT_COOKIE)
    m = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', page)
    if not m:
        raise RuntimeError("채널 번호를 못 찾았다")
    cid = m.group(1)
    subs = None
    s = re.search(r'([\d][\d.,]*\s?[KM]?)\s+subscribers?\b', page)
    k = re.search(r"구독자\s*([\d.,]+)\s*(천|만)?\s*명", page)
    if s:
        n = s.group(1).replace(",", "").replace(" ", "")
        subs = int(float(n[:-1]) * (1000 if n[-1] == "K" else 1000000)) if n[-1] in "KM" else int(float(n))
    elif k:
        subs = int(float(k.group(1).replace(",", "")) * {"천": 1000, "만": 10000}.get(k.group(2) or "", 1))
    else:
        i = max(page.find("subscriber"), page.find("구독자"))
        errors.append({"what": "유튜브 구독자 수(키 없이)", "why": "채널 페이지에서 못 찾음 — 길이 %d, 주변: %s" % (len(page), page[max(0, i - 80):i + 40].replace("\n", " ") if i >= 0 else "없음")})
    now = datetime.now(KST).isoformat(timespec="seconds")
    xml = get("https://www.youtube.com/feeds/videos.xml?channel_id=" + cid)
    vids = {}
    for ent in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        vid = re.search(r"<yt:videoId>(.*?)</yt:videoId>", ent).group(1)
        title = re.search(r"<title>(.*?)</title>", ent, re.S).group(1)
        pub = re.search(r"<published>(.*?)</published>", ent).group(1)
        views = re.search(r'<media:statistics views="(\d+)"', ent)
        vids[vid] = {"id": vid, "title": unescape(title), "published": pub, "views": int(views.group(1)) if views else None, "checked": now}
    # 옛 영상: 지난 수집에서 알던 것 + 채널 탭에 보이는 것
    old = {v["id"]: dict(v) for v in (prev.get("videos") or []) if v.get("id") and v["id"] not in vids}
    try:
        for tab in ["shorts", "videos"]:
            for vid in yt_tab_ids(tab):
                if vid not in vids and vid not in old:
                    old[vid] = {"id": vid, "title": "", "published": "", "views": None, "checked": ""}
            time.sleep(1)
    except Exception as e:
        errors.append({"what": "유튜브 채널의 옛 영상 목록(키 없이)", "why": str(e)[:160]})
    failed = 0
    for v in sorted(old.values(), key=lambda v: (v.get("views") is not None, v.get("checked") or ""))[:OLD_REFRESH]:
        try:
            w = yt_watch(v["id"])
            if w["owner"] and w["owner"].lower() != YT_HANDLE:  # 채널 탭에 섞여 온 남의 영상
                old.pop(v["id"], None)
                continue
            v.update({"title": w["title"] or v.get("title", ""), "published": w["published"] or v.get("published", ""), "views": w["views"], "checked": now})
        except Exception:
            failed += 1
        time.sleep(0.7)
    if failed:
        errors.append({"what": "유튜브 옛 영상 조회수(키 없이)", "why": "%d편을 못 읽어 지난 값을 그대로 뒀다" % failed})
    allv = list(vids.values()) + [v for v in old.values() if v.get("views") is not None]
    allv.sort(key=lambda v: v.get("published") or "", reverse=True)
    others, others_at = yt_others(prev)
    return {"via": "rss", "channel_id": cid, "subs": subs, "total_views": sum(v["views"] or 0 for v in allv), "videos": allv,
            "others": others, "others_at": others_at}


def unescape(s):
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")


# ---------------------------------------------------------------- 명예의 전당 (Firestore, 읽기는 공개)

def fs_val(f):
    k, v = next(iter(f.items()))
    if k == "mapValue":
        return {a: fs_val(b) for a, b in v.get("fields", {}).items()}
    if k == "arrayValue":
        return [fs_val(x) for x in v.get("values", [])]
    if k == "integerValue":
        return int(v)
    return v


def hall():
    fields = ["uid", "name", "at", "depth", "dungeon", "by", "ver", "species", "cls", "win", "score", "turn", "detail.story"]
    docs, tok = [], ""
    while True:
        q = [("key", FS_KEY), ("pageSize", "300")] + [("mask.fieldPaths", f) for f in fields]
        if tok:
            q.append(("pageToken", tok))
        d = get_json(FS_DOCS + "/hall?" + urllib.parse.urlencode(q))
        docs += d.get("documents", [])
        tok = d.get("nextPageToken", "")
        if not tok:
            break
    rows = []
    for d in docs:
        r = {k: fs_val(v) for k, v in d.get("fields", {}).items()}
        if "at" not in r:
            continue
        t = datetime.fromisoformat(r["at"].replace("Z", "+00:00")).astimezone(KST)
        if t < LAUNCH:
            continue
        story = " ".join((r.get("detail") or {}).get("story", []))
        rows.append({"t": t, "uid": r.get("uid", ""), "name": r.get("name", ""), "depth": r.get("depth", 0),
                     "dungeon": r.get("dungeon", ""), "by": r.get("by", ""), "ver": r.get("ver", ""),
                     "species": r.get("species", ""), "cls": r.get("cls", ""), "win": r.get("win", False),
                     "en": bool(story) and not hangul(story)})
    rows.sort(key=lambda r: r["t"])
    today = datetime.now(KST).date()
    first = {}
    for r in rows:
        first.setdefault(r["uid"], r["t"].date())
    days = {}
    for r in rows:
        k = r["t"].date().isoformat()
        e = days.setdefault(k, {"runs": 0, "people": set(), "new": set()})
        e["runs"] += 1
        e["people"].add(r["uid"])
        if first[r["uid"]] == r["t"].date():
            e["new"].add(r["uid"])
    tr = [r for r in rows if r["t"].date() == today]
    back = sum(1 for u, f in first.items() if f < today and len({r["t"].date() for r in rows if r["uid"] == u}) > 1)
    who = {}
    for r in tr:
        e = who.setdefault(r["uid"], {"name": r["name"], "runs": 0, "new": first[r["uid"]] == today})
        e["runs"] += 1
        e["name"] = r["name"]
    lost = [r for r in rows if not r["win"]]
    spots, causes = {}, {}
    for r in lost:
        k = "%s %s층" % (r["dungeon"], r["depth"])
        spots[k] = spots.get(k, 0) + 1
        causes[r["by"] or "?"] = causes.get(r["by"] or "?", 0) + 1
    top = lambda d: [{"name": k, "n": n} for k, n in sorted(d.items(), key=lambda kv: -kv[1])[:6]]
    return {"total_runs": len(rows), "total_people": len(first), "today_runs": len(tr),
            "today_people": len({r["uid"] for r in tr}), "today_new": sum(1 for f in first.values() if f == today),
            "en_runs": sum(r["en"] for r in rows), "wins": sum(1 for r in rows if r["win"]),
            "returned": back, "returned_of": sum(1 for f in first.values() if f < today),
            "today": sorted(who.values(), key=lambda e: -e["runs"]), "lost": len(lost), "spots": top(spots), "causes": top(causes),
            "days": [{"day": k, "runs": v["runs"], "people": len(v["people"]), "new": len(v["new"])} for k, v in sorted(days.items())],
            "recent": [{"at": r["t"].isoformat(), "name": r["name"], "who": "%s %s" % (r["species"], r["cls"]),
                        "dungeon": r["dungeon"], "depth": r["depth"], "by": r["by"], "ver": r["ver"], "win": r["win"]}
                       for r in rows[-15:][::-1]]}


# ---------------------------------------------------------------- 묶기

def metrics(s):
    """history.jsonl 한 줄에 들어갈 숫자들. 열쇠는 페이지(index.html)의 LABEL과 맞춘다"""
    m = {}
    h = s.get("hall") or {}
    for k in ["total_runs", "total_people", "today_runs", "today_people", "today_new", "en_runs"]:
        if k in h:
            m["hall." + k] = h[k]
    for cc in ["kr", "us"]:
        a = (s.get("ios") or {}).get(cc) or {}
        if a.get("listed"):
            m["ios.%s.ratings" % cc] = a.get("ratings", 0)
            if a.get("rating") is not None:
                m["ios.%s.rating" % cc] = round(a["rating"], 2)
        p = (s.get("play") or {}).get(cc) or {}
        if p.get("real") is not None:
            m["play.%s.real" % cc] = p["real"]
        if p.get("ratings") is not None and p:
            m["play.%s.ratings" % cc] = p["ratings"]
    for k, v in (s.get("ios_rank") or {}).items():
        m["ios.rank." + k] = v
    for k, v in (s.get("play_rank") or {}).items():
        m["play.rank." + k] = v
    for k, v in (s.get("charts") or {}).items():
        m["chart." + k] = v
    y = s.get("youtube") or {}
    if y.get("subs") is not None:
        m["yt.subs"] = y["subs"]
    for v in y.get("videos") or []:
        if v.get("views") is not None:
            m["yt.v." + v["id"]] = v["views"]
    return m


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    s = {"at": datetime.now(KST).isoformat(timespec="seconds")}
    prev = {}
    try:
        prev = json.load(open(os.path.join(out, "latest.json")))
    except Exception:
        pass
    s["hall"] = step("명예의 전당", hall)
    s["ios"] = {cc: step("앱스토어 %s 페이지" % cc, ios_lookup, cc) for cc in ["kr", "us"]}
    s["ios_rank"] = {}
    for cc, terms in IOS_TERMS.items():
        for t in terms:
            try:
                s["ios_rank"]["%s.%s" % (cc, t)] = ios_rank(cc, t)
            except Exception as e:
                errors.append({"what": "앱스토어 검색 %s '%s'" % (cc, t), "why": str(e)[:200]})
            time.sleep(1)
    s["charts"], s["charts_n"] = {}, {}
    for cc, device, g, name in CHARTS:
        k = chart_key(cc, device, g)
        try:
            s["charts"][k], s["charts_n"][k] = ios_chart(cc, device, g)
        except Exception as e:
            errors.append({"what": "앱스토어 차트 %s %s %s" % (cc, device, name), "why": str(e)[:200]})
        time.sleep(1)
    s["reviews"] = {cc: step("앱스토어 %s 리뷰" % cc, ios_reviews, cc) for cc in ["kr", "us"]}
    for cc in ["kr", "us"]:  # 애플 리뷰 목록은 가끔 비어 온다 — 그럴 땐 지난 목록을 그대로 둔다
        old = (prev.get("reviews") or {}).get(cc)
        if not s["reviews"][cc] and old:
            s["reviews"][cc] = old
    s["play"] = {"kr": step("플레이 한국 페이지", play_app, "kr", "ko")}
    time.sleep(2)
    s["play"]["us"] = step("플레이 미국 페이지", play_app, "us", "en")
    s["play_reviews"] = {"kr": step("플레이 한국 리뷰", play_reviews, "kr", "ko")}
    time.sleep(2)
    s["play_reviews"]["us"] = step("플레이 미국 리뷰", play_reviews, "us", "en")
    for cc in ["kr", "us"]:
        old = (prev.get("play_reviews") or {}).get(cc)
        if not s["play_reviews"][cc] and old:
            s["play_reviews"][cc] = old
    s["play_rank"], s["play_rank_n"] = {}, {}
    for cc, terms in PLAY_TERMS.items():
        for t in terms:
            time.sleep(3)  # 몰아서 읽으면 구글이 503으로 막는다 (2026-10-09)
            try:
                k = "%s.%s" % (cc, t)
                s["play_rank"][k], s["play_rank_n"][k] = play_rank(cc, "ko" if cc == "kr" else "en", t)
            except Exception as e:
                errors.append({"what": "플레이 검색 %s '%s'" % (cc, t), "why": str(e)[:200]})
    s["youtube"] = step("유튜브", youtube, prev.get("youtube"))
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
