#!/usr/bin/env python3
"""Quality gate for a learn-in-3-hours course.

Usage:  python3 check_course.py <course_dir> [--no-render] [--full-shots]

Static checks (always):
  - every page built, no external resource requests (scripts/styles/images/fonts)
  - required blocks per page type, self-test answers present, act-end recaps
  - sources.json sane; every concept page cites at least one source
  - claims.json: every claim resolved (verified / corrected / softened / removed),
    evidence quoted for verified/corrected ones
  - heuristics: numbers, versions, defaults and absolute statements ("不支持/只能/默认…")
    on a page that no ledger claim covers → warnings to go ledger & verify them
Render checks (if Chrome/Chromium/Edge is found; set CHROME_PATH to override):
  - each page laid out at a 390px phone viewport: horizontal overflow,
    effective on-screen size of every SVG label, overlapping labels,
    labels spilling out of their box or out of the SVG
  - screenshots of every diagram at phone width, light and dark → <course>/_work/check/shots/

Without _work/course.json it runs in "generic" mode on every *.html in the
directory (external requests + render checks only) — handy for auditing any
HTML course, not just ones made by this skill.

Exit code 1 when there are errors. Report: <course>/_work/check/report.md
"""
import concurrent.futures as cf
import glob
import hashlib
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_course import find_blocks, strip_tags, char_equiv  # noqa: E402

MIN_PX_ERROR = 11.0   # below this a label is unreadable on a phone
MIN_PX_WARN = 12.0
PHONE_W = 390

REQUIRED = {
    "concept": ["hook", "anchor", "figure", "points", "analogy", "boundary", "selftest", "retell"],
    "map": ["meta"],
    "capstone": ["hook", "task"],
    "review": ["mindmap", "retell-set", "sticking", "further"],
}
CLAIM_OK = {"verified", "corrected", "softened", "removed"}
ABSOLUTE = re.compile(r"(不支持|不能|无法|从不|从未|永远不|绝不|只能|唯一|不可能|默认|总是|一律|必然|首个|第一个|率先)")
NUMBERISH = re.compile(
    r"(?<![\w.])(\d+\.\d+(?:\.\d+)?|(?:19|20)\d{2}|\d+(?:\.\d+)?\s*(?:%|ms|毫秒|μs|秒|KB|MB|GB|TB|KiB|MiB|GiB|万|亿|倍|bps|Mbps|Gbps|QPS|TPS|IOPS|nm|kW|MW|℃|°C|美元|元|个基点|bp))",
    re.I)


class Report:
    def __init__(self):
        self.errors, self.warns, self.notes = [], [], []
        self.figures = []

    def e(self, where, msg):
        self.errors.append("%s: %s" % (where, msg))

    def w(self, where, msg):
        self.warns.append("%s: %s" % (where, msg))


def attr_val(attrs, key):
    m = re.search(r'\b%s="([^"]*)"' % re.escape(key), attrs)
    return html.unescape(m.group(1)).strip() if m else ""


BLOCK_LABEL = {"hook": "先想一想", "anchor": "从你已经懂的出发", "figure": "图注", "points": "要点", "analogy": "打个比方",
               "boundary": "比方在哪失效", "selftest": "自测", "retell": "复述", "recap": "本幕小结", "meta": "元概念",
               "route": "路线", "task": "动手", "mindmap": "思维导图图注", "retell-set": "复述挑战", "sticking": "易卡壳", "further": "往下挖"}


def export_course_text(course_dir, course):
    """Ordered plain-text version of the course (figures reduced to captions) for the continuity read."""
    work = os.path.join(course_dir, "_work")
    out = ["# %s（文字版，图只保留图注）" % course.get("title", ""), "", "靶心问题：" + course.get("target", "")]
    for p in course["pages"]:
        path = os.path.join(work, "src", p["id"] + ".html")
        if not os.path.exists(path):
            continue
        out.append("\n## %s（第 %d 幕）" % (p["id"] + " " + p["title"], p.get("act", 0)))
        for name, _, _, inner, _, _ in find_blocks(open(path, encoding="utf-8").read()):
            if name == "deep":
                continue
            inner = re.sub(r"<cite\b[^>]*>.*?</cite>", "", inner, flags=re.S)
            if name in ("figure", "mindmap"):
                cap = re.search(r"<figcaption\b[^>]*>(.*?)</figcaption>", inner, re.S)
                t = strip_tags(cap.group(1)) if cap else ""
            else:
                t = strip_tags(re.sub(r"<svg\b.*?</svg>", "", inner, flags=re.S))
            t = " ".join(t.split())
            if t:
                out.append(("【%s】" % BLOCK_LABEL[name] if name in BLOCK_LABEL else "") + t)
    path = os.path.join(work, "check", "course-text.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return path


def continuity_checks(course_dir, course, rep):
    work = os.path.join(course_dir, "_work")
    pages = course["pages"]
    first_concept = next((p["id"] for p in pages if p.get("type", "concept") == "concept"), None)
    for i, p in enumerate(pages):
        if p.get("type", "concept") != "concept" or p["id"] == first_concept:
            continue
        path = os.path.join(work, "src", p["id"] + ".html")
        if not os.path.exists(path):
            continue
        blocks = find_blocks(open(path, encoding="utf-8").read())
        opening = " ".join(strip_tags(b[3]) for b in blocks if b[0] in ("lede", "hook", "anchor", "points"))
        me = int(re.match(r"(\d+)", p["id"]).group(1)) if re.match(r"(\d+)", p["id"]) else i
        refs = [int(x) for x in re.findall(r"第\s*(\d+)\s*页|[Pp]age\s*(\d+)", opening) for x in x if x]
        if not any(r < me for r in refs):
            rep.w(p["id"], "opening (lede/hook/anchor/points) never builds on an earlier page — say which page this continues and use it")
    srcs = glob.glob(os.path.join(work, "src", "*.html"))
    newest = max((os.path.getmtime(x) for x in srcs), default=0)
    cpath = os.path.join(work, "continuity-read.md")
    if not os.path.exists(cpath):
        rep.w("continuity read", "not done yet — give _work/check/course-text.md to a fresh reader (see SKILL.md step 6)")
    elif os.path.getmtime(cpath) + 1 < newest:
        rep.w("continuity read", "pages changed after the last continuity read — re-run it if transitions were touched")


def reader_test_checks(course_dir, rep, shots_dir):
    """Write figures.json for the fresh-reader test and enforce its recorded verdicts."""
    work = os.path.join(course_dir, "_work")
    for f in rep.figures:
        shot = os.path.join(shots_dir, "%s-fig%d-phone-light.png" % (f["page"], f["fig"]))
        f["shot"] = shot if os.path.exists(shot) else None
    with open(os.path.join(work, "check", "figures.json"), "w", encoding="utf-8") as fh:
        json.dump(rep.figures, fh, ensure_ascii=False, indent=1)
    path = os.path.join(work, "reader-test.json")
    if not rep.figures:
        return
    if not os.path.exists(path):
        rep.w("reader test", "not run yet — %d diagrams listed in _work/check/figures.json; see references/diagram-rules.md" % len(rep.figures))
        return
    try:
        verdicts = json.load(open(path, encoding="utf-8"))
    except ValueError as ex:
        rep.e("reader-test.json", "invalid JSON: %s" % ex)
        return
    by_key = {(v.get("page"), v.get("hash")): v for v in verdicts}
    untested = []
    for f in rep.figures:
        v = by_key.get((f["page"], f["hash"]))
        if v is None:
            untested.append("%s fig%d" % (f["page"], f["fig"]))
        elif v.get("verdict") != "pass":
            rep.e("%s fig%d" % (f["page"], f["fig"]), "fresh reader could not recover what it teaches: %s — redraw, then re-test"
                  % (v.get("reader_summary") or v.get("note") or "no summary")[:120])
    if untested:
        rep.w("reader test", "%d diagrams new or changed since the last reader test: %s" % (len(untested), ", ".join(untested[:10])))
    rep.notes.append("reader test: %d/%d diagrams have a current passing verdict" %
                     (sum(1 for f in rep.figures if by_key.get((f["page"], f["hash"]), {}).get("verdict") == "pass"), len(rep.figures)))


def bigrams(s):
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def covered(sentence, claim_texts, thr=0.5):
    """A clause counts as ledgered if it and some claim largely share wording — in either
    direction, so one long composite claim can cover several short clauses — or if every
    number in the clause appears in a single claim."""
    sb = bigrams(sentence)
    nums = set(re.findall(r"\d+(?:\.\d+)?", sentence))
    for c in claim_texts:
        cb = bigrams(c)
        if cb and sb:
            shared = len(cb & sb)
            if shared / float(len(cb)) >= thr or shared / float(len(sb)) >= thr:
                return True
        if nums and nums <= set(re.findall(r"\d+(?:\.\d+)?", c)):
            return True
    return False


def factual_text(src):
    """What the learner reads as statements of fact: drop analogies, slogans, retell prompts
    and self-test questions (their hidden answers stay in), citations and SVG."""
    s = re.sub(r"<cite\b[^>]*>.*?</cite>", "", src, flags=re.S)
    keep = []
    for name, _, _, inner, _, _ in find_blocks(s):
        if name in ("analogy", "retell", "route", "meta"):
            continue
        if name in ("selftest", "retell-set"):
            inner = " ".join(re.findall(r"<details\b.*?</details>", inner, flags=re.S))
        inner = re.sub(r'<p class="motto">.*?</p>', " ", inner, flags=re.S)
        keep.append(inner)
    return strip_tags("\n".join(keep))


def sentences(text):
    parts = re.split(r"(?<=[。！？；;!?])|\n", text)
    return [" ".join(p.split()) for p in parts if len(p.strip()) >= 6]


def clauses(text):
    """Comma-level pieces, so a ledgered fact can't shelter an extra assertion in the same sentence."""
    out = []
    for s in sentences(text):
        out += [c.strip() for c in re.split(r"[，,、：:（）()]|且|但|而", s) if len(c.strip()) >= 4]
    return out


def external_refs(doc):
    bad = []
    for pat in (r"<script\b[^>]*\bsrc\s*=", r"<link\b[^>]*\bhref\s*=\s*[\"']?(?:https?:)?//", r"<img\b[^>]*\bsrc\s*=\s*[\"']?(?:https?:)?//",
                r"url\(\s*[\"']?(?:https?:)?//", r"@import", r"<iframe\b[^>]*\bsrc\s*=\s*[\"']?(?:https?:)?//",
                r"<(?:video|audio|source)\b[^>]*\bsrc\s*=\s*[\"']?(?:https?:)?//"):
        for m in re.finditer(pat, doc, re.I):
            bad.append(doc[m.start():m.start() + 90].replace("\n", " "))
    return bad


# ---------------------------------------------------------------- static ----

def static_checks(course_dir, rep):
    work = os.path.join(course_dir, "_work")
    course = json.load(open(os.path.join(work, "course.json"), encoding="utf-8"))
    pages = course["pages"]
    acts = course.get("acts") or ["立靶", "原理", "应用", "延伸"]
    ids = [p["id"] for p in pages]

    def load(name):
        path = os.path.join(work, name)
        if not os.path.exists(path):
            return None
        try:
            return json.load(open(path, encoding="utf-8"))
        except ValueError as ex:
            rep.e(name, "invalid JSON: %s" % ex)
            return None

    sources = load("sources.json")
    claims = load("claims.json")
    if sources is None:
        rep.e("sources.json", "missing — research sources must be recorded")
        sources = []
    if claims is None:
        rep.e("claims.json", "missing — the fact-check ledger is required")
        claims = []

    # sources
    pubs = set()
    for s in sources:
        url = s.get("url", "")
        # A course may ship its own lab material (e.g. a generated dataset + SQL); such a source
        # may point to a file inside the course folder instead of a web page.
        local_ok = bool(url) and not re.match(r"[a-z]+:", url) and not url.startswith("/") \
            and ".." not in url and os.path.isfile(os.path.join(course_dir, url))
        if not s.get("id") or not url or not (re.match(r"https?://", url) or local_ok):
            rep.e("sources.json", "source needs id + http(s) url (or a path to a file inside the course folder): %s"
                  % json.dumps(s, ensure_ascii=False)[:120])
        if s.get("publisher"):
            pubs.add(s["publisher"].strip().lower())
        if not s.get("date"):
            rep.w("sources.json", "%s has no date/version — readers can't judge freshness" % s.get("id"))
    if len(sources) < 3:
        rep.e("sources.json", "only %d sources; need at least 3" % len(sources))
    if len(pubs) < 2:
        rep.w("sources.json", "all sources from %d publisher(s); cross-check needs independent publishers" % len(pubs))

    # claims
    by_page = {}
    counts = {}
    for c in claims:
        cid = c.get("id", "?")
        st = c.get("status")
        counts[st] = counts.get(st, 0) + 1
        if st not in CLAIM_OK:
            rep.e("claims.json", "%s status %r — every claim must end verified/corrected/softened/removed" % (cid, st))
        if c.get("page") not in ids and c.get("page") != "course":
            rep.e("claims.json", "%s refers to unknown page %r (use a page id, or \"course\" for course-wide facts like the baseline)" % (cid, c.get("page")))
        if st in ("verified", "corrected"):
            if not (c.get("url") or c.get("sources")):
                rep.e("claims.json", "%s is %s but names no source" % (cid, st))
            if not (c.get("evidence") or "").strip():
                rep.e("claims.json", "%s is %s but has no evidence quote" % (cid, st))
        if st != "removed":
            by_page.setdefault(c.get("page"), []).append(c.get("text", ""))
    rep.notes.append("claims: %d total — %s" % (len(claims), ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items(), key=lambda kv: str(kv[0])))))

    # timing
    tpath = os.path.join(work, "timing.json")
    if os.path.exists(tpath):
        t = json.load(open(tpath, encoding="utf-8"))
        target = course.get("target_minutes", 180)
        tot = t.get("total_core_minutes", 0)
        rep.notes.append("estimated core time %d min (target %d), optional deep-dives +%d min" % (tot, target, t.get("total_deep_minutes", 0)))
        if target and abs(tot - target) / float(target) > 0.25:
            rep.w("course", "core time %d min is far from the %d min target — add/trim pages or restate the promise honestly" % (tot, target))

    # pages
    # Acts in the middle of the course close with a recap diagram on their last concept page
    # (single-page acts don't need one; the final act is recapped by the review page).
    concepts_in_act = {}
    for p in pages:
        if p.get("type", "concept") == "concept":
            concepts_in_act.setdefault(p.get("act", 0), []).append(p["id"])
    recap_needed = {concepts_in_act[a][-1] for a in range(1, len(acts) - 1) if len(concepts_in_act.get(a, [])) >= 2}

    for p in pages:
        pid, ptype = p["id"], p.get("type", "concept")
        out = os.path.join(course_dir, pid + ".html")
        src_path = os.path.join(work, "src", pid + ".html")
        if not os.path.exists(src_path):
            rep.e(pid, "fragment missing")
            continue
        if not os.path.exists(out):
            rep.e(pid, "page not built — run build_course.py")
            continue
        src = open(src_path, encoding="utf-8").read()
        doc = open(out, encoding="utf-8").read()
        if os.path.getmtime(src_path) > os.path.getmtime(out) + 1:
            rep.e(pid, "fragment is newer than the built page — rebuild")
        for b in external_refs(doc):
            rep.e(pid, "external resource request: %s" % b)
        blocks = find_blocks(src)
        names = [b[0] for b in blocks]
        need = list(REQUIRED.get(ptype, []))
        if pid in recap_needed:
            need.append("recap")
        for n in need:
            if n not in names:
                rep.e(pid, "missing required block data-block=\"%s\"" % n)
        bd = {}
        for b in blocks:
            bd.setdefault(b[0], []).append(b)
        fig_i = 0
        for name, _, attrs, inner, _, _ in blocks:
            if name != "deep":
                visible = re.sub(r"<details\b.*?</details>", "", inner, flags=re.S)
                for svg_m in re.finditer(r"<svg\b.*?</svg>", visible, re.S):
                    fig_i += 1
                    teaches = attr_val(attrs, "data-teaches")
                    cap = re.search(r"<figcaption\b[^>]*>(.*?)</figcaption>", visible, re.S)
                    caption = strip_tags(re.sub(r"<cite\b[^>]*>.*?</cite>", "", cap.group(1) if cap else re.sub(r"<svg\b.*?</svg>", "", visible, flags=re.S), flags=re.S))
                    rep.figures.append({"page": pid, "fig": fig_i, "block": name, "teaches": teaches,
                                        "caption": " ".join(caption.split()),
                                        "hash": hashlib.sha1(svg_m.group(0).encode("utf-8")).hexdigest()[:12]})
                    if name in ("figure", "mindmap", "recap") and not teaches:
                        rep.w("%s fig%d" % (pid, fig_i), "no data-teaches — say in one sentence what this diagram should teach (used by the reader test)")
            if name in ("selftest", "retell-set"):
                nq = len(re.findall(r'class="q\b', inner))
                nd = len(re.findall(r"<details\b", inner))
                if nq == 0:
                    rep.e(pid, "%s has no <div class=\"q\"> questions" % name)
                elif nd < nq:
                    rep.e(pid, "%s: %d questions but only %d <details> answers/key points" % (name, nq, nd))
                if name == "retell-set" and nq < 3:
                    rep.w(pid, "review page has only %d retell questions (want 3-5)" % nq)
            if name in ("figure", "mindmap", "recap"):
                for svg in re.findall(r"<svg\b[^>]*>", inner):
                    vb = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', svg)
                    if not vb:
                        rep.e(pid, "svg without viewBox (won't scale): %s" % svg[:80])
                    elif float(vb.group(1)) > 480 and 'class="scroll"' not in inner:
                        rep.w(pid, "svg viewBox width %s > 480 — labels shrink ~%.0f%% on phones; redesign portrait or wrap in <div class=\"scroll\">"
                              % (vb.group(1), 100 - 100 * 358 / float(vb.group(1))))
                    if "aria-label" not in svg and "<title" not in inner:
                        rep.w(pid, "svg has no aria-label/<title>")
                if name == "figure" and not re.search(r"<svg\b", inner):
                    rep.e(pid, "figure block has no <svg>")
        core_text = " ".join(strip_tags(b[3]) for b in blocks if b[0] in ("lede", "hook", "anchor", "points", "analogy", "boundary"))
        core_len = char_equiv(core_text)
        if 'data-placeholder="1"' in src or 'data-placeholder="1"' in doc:
            rep.e(pid, "placeholder page — the fragment has not been written")
        if ptype in ("concept", "capstone") and not re.search(r'<cite\s+data-src=', src):
            rep.e(pid, "no <cite data-src> — every concept and capstone page must cite its sources")
        if ptype == "concept":
            if core_len > 450:
                rep.w(pid, "core text ≈%d chars (budget 450) — move detail into the deep block" % core_len)
            n_claims = len(by_page.get(pid, []))
            if n_claims < 3:
                rep.w(pid, "only %d ledger claims for this page — did you extract every checkable statement?" % n_claims)
        # ledger coverage heuristics over everything the learner reads (incl. deep + answers)
        if ptype in ("concept", "capstone", "review"):
            text = factual_text(src)
            ctexts = by_page.get(pid, []) + by_page.get("course", []) + (sum(by_page.values(), []) if ptype in ("review", "capstone") else [])
            nums = []
            for m in NUMBERISH.finditer(text):
                tok = m.group(1).strip()
                if not any(tok.split()[0] in c for c in ctexts):
                    nums.append(tok)
            if nums:
                rep.w(pid, "numbers/versions not in the claims ledger: %s" % ", ".join(sorted(set(nums))[:12]))
            abs_s = [s for s in clauses(text) if ABSOLUTE.search(s) and not covered(s, ctexts)]
            for s in abs_s[:5]:
                rep.w(pid, "possible unledgered claim (absolute/default wording): 「%s」" % s[:70])
    return course


# ---------------------------------------------------------------- render ----

MEASURE_JS = r"""
function measure(f){
  var w=f.contentWindow,d=f.contentDocument,out={iw:w.innerWidth,sw:d.documentElement.scrollWidth,h:d.documentElement.scrollHeight,svgs:[],wide:[]};
  var all=d.body.getElementsByTagName('*');
  for(var i=0;i<all.length&&out.wide.length<4;i++){var el=all[i];if(el.closest&&el.closest('.scroll,.tbl,svg'))continue;
    var r=el.getBoundingClientRect();if(r.right>w.innerWidth+1&&r.width>0)out.wide.push(el.tagName.toLowerCase()+(el.className&&typeof el.className==='string'?'.'+el.className.split(' ')[0]:'')+' → '+Math.round(r.right)+'px');}
  var svgs=[].slice.call(d.querySelectorAll('svg')).filter(function(s){var r=s.getBoundingClientRect();return r.width>1&&r.height>1;});
  svgs.forEach(function(svg,si){
    var sb=svg.getBoundingClientRect();
    var texts=[].slice.call(svg.querySelectorAll('text')).map(function(t){
      var b=t.getBoundingClientRect(),fs=parseFloat(w.getComputedStyle(t).fontSize)||0,m=t.getScreenCTM(),k=m?Math.sqrt(m.a*m.a+m.b*m.b):1;
      return {s:(t.textContent||'').replace(/\s+/g,' ').trim().slice(0,24),l:b.left,t:b.top,r:b.right,b:b.bottom,w:b.width,h:b.height,px:Math.round(fs*k*10)/10};
    }).filter(function(x){return x.s&&x.w>0;});
    var sa=sb.width*sb.height;
    var shapes=[].slice.call(svg.querySelectorAll('rect,circle,ellipse,polygon')).map(function(e){var b=e.getBoundingClientRect();return {l:b.left,t:b.top,r:b.right,b:b.bottom,a:b.width*b.height};}).filter(function(x){return x.a>0&&x.a<0.6*sa;});
    var ov=[],outb=[],spill=[];
    for(var i=0;i<texts.length;i++)for(var j=i+1;j<texts.length;j++){var A=texts[i],B=texts[j];
      var iw=Math.min(A.r,B.r)-Math.max(A.l,B.l),ih=Math.min(A.b,B.b)-Math.max(A.t,B.t);
      if(iw>0&&ih>0&&iw*ih>0.2*Math.min(A.w*A.h,B.w*B.h))ov.push(A.s+' ⟷ '+B.s);}
    texts.forEach(function(x){
      if(x.l<sb.left-1||x.r>sb.right+1||x.t<sb.top-1||x.b>sb.bottom+1)outb.push(x.s);
      var cx=(x.l+x.r)/2,cy=(x.t+x.b)/2,best=null;
      shapes.forEach(function(s){if(cx>=s.l&&cx<=s.r&&cy>=s.t&&cy<=s.b&&(!best||s.a<best.a))best=s;});
      if(best&&(x.l<best.l-2||x.r>best.r+2))spill.push(x.s);});
    var px=texts.map(function(t){return t.px;});
    out.svgs.push({i:si,top:Math.round(sb.top+w.scrollY),w:Math.round(sb.width),h:Math.round(sb.height),n:texts.length,
      minpx:px.length?Math.min.apply(null,px):null,
      small:texts.filter(function(t){return t.px<%(warn)s;}).map(function(t){return t.s+' ('+t.px+'px)';}).slice(0,6),
      overlaps:ov.slice(0,6),outb:outb.slice(0,6),spill:spill.slice(0,6)});
  });
  return out;
}
"""

MEASURE_WRAPPER = """<!DOCTYPE html><html><body style="margin:0">
<iframe id="f" src="%(src)s" style="width:%(w)dpx;height:900px;border:0"></iframe><pre id="out">pending</pre>
<script>%(js)s
document.getElementById('f').onload=function(){var o;try{o=measure(this)}catch(e){o={error:String(e)}}
document.getElementById('out').textContent=JSON.stringify(o);};</script></body></html>"""

SHOT_WRAPPER = """<!DOCTYPE html><html><body style="margin:0;background:#9aa0a6">
<iframe id="f" style="width:%(w)dpx;height:%(h)dpx;border:0;display:block"></iframe>
<script>var f=document.getElementById('f');f.onload=function(){try{var d=f.contentDocument,w=f.contentWindow,a=d.body.getElementsByTagName('*');
for(var i=0;i<a.length;i++){var p=w.getComputedStyle(a[i]).position;if(p==='fixed'||p==='sticky')a[i].style.visibility='hidden';}
w.scrollTo(0,%(y)d);}catch(e){}};f.src="%(src)s";</script></body></html>"""


def find_chrome():
    env = os.environ.get("CHROME_PATH")
    if env and os.path.exists(env):
        return env
    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def chrome(binary, args, profile, done, stdout=None, timeout=60):
    """Run headless Chrome and return once `done()` is true.

    With a throwaway --user-data-dir, Chrome's browser process often keeps running
    after --dump-dom/--screenshot has finished, so waiting for exit (or for EOF on
    a stdout pipe) hangs. Instead poll for the result and then kill the whole group.
    """
    base = [binary, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--allow-file-access-from-files", "--mute-audio", "--use-mock-keychain",
            "--password-store=basic", "--disable-breakpad", "--disable-extensions",
            "--user-data-dir=" + profile, "--force-device-scale-factor=1"]
    kw = {"start_new_session": True} if os.name == "posix" else {}
    proc = subprocess.Popen(base + args, stdout=stdout or subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    t0 = time.time()
    ok = False
    try:
        while time.time() - t0 < timeout:
            if done():
                ok = True
                break
            if proc.poll() is not None:
                time.sleep(0.2)
                ok = done()
                break
            time.sleep(0.25)
    finally:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    return ok


def file_url(path):
    from urllib.request import pathname2url
    return "file://" + pathname2url(os.path.abspath(path))


def png_ready(path):
    state = {"size": -1}

    def done():
        if not os.path.exists(path):
            return False
        sz = os.path.getsize(path)
        ready = sz > 0 and sz == state["size"]
        state["size"] = sz
        return ready
    return done


def render_page(binary, page_path, tmp, shots_dir, full):
    name = os.path.splitext(os.path.basename(page_path))[0]
    profile = tempfile.mkdtemp(prefix="l3h-chrome-")
    try:
        wpath = os.path.join(tmp, name + ".measure.html")
        dump = os.path.join(tmp, name + ".dom.txt")
        with open(wpath, "w", encoding="utf-8") as f:
            f.write(MEASURE_WRAPPER % {"src": file_url(page_path), "w": PHONE_W,
                                       "js": MEASURE_JS.replace("%(warn)s", str(MIN_PX_WARN))})
        # --dump-dom writes to stdout; send it to a file and poll for the closing tag.
        with open(dump, "w") as out_f:
            ok = chrome(binary, ["--window-size=800,1000", "--virtual-time-budget=5000", "--dump-dom", file_url(wpath)],
                        profile, lambda: "</html>" in open(dump, encoding="utf-8", errors="ignore").read(), stdout=out_f)
        dom = open(dump, encoding="utf-8", errors="ignore").read()
        m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
        if not ok or not m or m.group(1).strip() == "pending":
            return name, {"error": "measurement did not run"}, []
        data = json.loads(html.unescape(m.group(1)))
        shots = []
        if shots_dir and "svgs" in data:
            jobs = []
            for s in data["svgs"]:
                h = min(s["h"] + 8, 2400)  # tight crop: just the diagram, not the card label above it
                for scheme in ("light", "dark"):
                    jobs.append(("%s-fig%d-phone-%s.png" % (name, s["i"] + 1, scheme), h, max(0, s["top"] - 4), scheme))
            if full:
                jobs.append(("%s-full-phone.png" % name, min(data.get("h", 3000), 12000), 0, "light"))
            for fname, h, y, scheme in jobs:
                sp = os.path.join(tmp, fname + ".html")
                with open(sp, "w", encoding="utf-8") as f:
                    f.write(SHOT_WRAPPER % {"src": file_url(page_path), "w": PHONE_W, "h": h, "y": y})
                out = os.path.join(shots_dir, fname)
                chrome(binary, ["--window-size=%d,%d" % (PHONE_W + 130, h), "--virtual-time-budget=4000",
                                "--blink-settings=preferredColorScheme=%d" % (1 if scheme == "light" else 0),
                                "--screenshot=" + out, file_url(sp)], profile, png_ready(out))
                if os.path.exists(out):
                    shots.append(out)
        return name, data, shots
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def render_checks(page_paths, check_dir, rep, full=False):
    binary = find_chrome()
    if not binary:
        rep.w("render", "no Chrome/Chromium/Edge found (set CHROME_PATH) — phone-width layout NOT verified; "
                        "keep every diagram viewBox ≤ 400 wide with labels ≥ 13 units as a fallback")
        return {}
    shots_dir = os.path.join(check_dir, "shots")
    if os.path.isdir(shots_dir):
        shutil.rmtree(shots_dir)
    os.makedirs(shots_dir)
    tmp = tempfile.mkdtemp(prefix="l3h-wrap-")
    results = {}
    try:
        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            for name, data, shots in ex.map(lambda p: render_page(binary, p, tmp, shots_dir, full), page_paths):
                results[name] = {"data": data, "shots": shots}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for name in sorted(results):
        d = results[name]["data"]
        if "error" in d:
            rep.w(name, "render check failed: %s" % d["error"])
            continue
        if d["sw"] > d["iw"] + 1:
            rep.e(name, "page scrolls sideways on a %dpx phone (content %dpx wide): %s" % (d["iw"], d["sw"], "; ".join(d["wide"])))
        for s in d["svgs"]:
            where = "%s fig%d" % (name, s["i"] + 1)
            if s["minpx"] is not None and s["minpx"] < MIN_PX_ERROR:
                rep.e(where, "labels render at %.1fpx on a phone (min %.0f): %s" % (s["minpx"], MIN_PX_ERROR, ", ".join(s["small"])))
            elif s["minpx"] is not None and s["minpx"] < MIN_PX_WARN:
                rep.w(where, "some labels only %.1fpx on a phone: %s" % (s["minpx"], ", ".join(s["small"])))
            if s["overlaps"]:
                rep.e(where, "overlapping labels: %s" % "; ".join(s["overlaps"]))
            if s["spill"]:
                rep.e(where, "label wider than its box: %s" % ", ".join(s["spill"]))
            if s["outb"]:
                rep.e(where, "label cut off at the SVG edge: %s" % ", ".join(s["outb"]))
    n_svg = sum(len(r["data"].get("svgs", [])) for r in results.values())
    rep.notes.append("render: %d pages at %dpx, %d diagrams measured; phone screenshots in %s" % (len(results), PHONE_W, n_svg, shots_dir))
    return results


# ------------------------------------------------------------------ main ----

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 1:
        print(__doc__)
        sys.exit(2)
    course_dir = args[0]
    rep = Report()
    course_mode = os.path.exists(os.path.join(course_dir, "_work", "course.json"))
    if course_mode:
        course = static_checks(course_dir, rep)
        page_paths = [os.path.join(course_dir, p["id"] + ".html") for p in course["pages"]
                      if os.path.exists(os.path.join(course_dir, p["id"] + ".html"))]
        check_dir = os.path.join(course_dir, "_work", "check")
    else:
        page_paths = sorted(glob.glob(os.path.join(course_dir, "*.html")))
        for p in page_paths:
            for b in external_refs(open(p, encoding="utf-8", errors="ignore").read()):
                rep.e(os.path.basename(p), "external resource request: %s" % b)
        check_dir = os.path.join(course_dir, "_check")
        rep.notes.append("generic mode (no _work/course.json): %d html files" % len(page_paths))
    os.makedirs(check_dir, exist_ok=True)
    results = {}
    if "--no-render" not in flags:
        results = render_checks(page_paths, check_dir, rep, full="--full-shots" in flags)
    if course_mode:
        reader_test_checks(course_dir, rep, os.path.join(check_dir, "shots"))
        continuity_checks(course_dir, course, rep)
        rep.notes.append("course text for the continuity read: %s" % export_course_text(course_dir, course))

    lines = ["# check report", ""]
    lines += ["- " + n for n in rep.notes]
    lines += ["", "## errors (%d)" % len(rep.errors)] + ["- " + x for x in rep.errors]
    lines += ["", "## warnings (%d)" % len(rep.warns)] + ["- " + x for x in rep.warns]
    with open(os.path.join(check_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(check_dir, "render.json"), "w", encoding="utf-8") as f:
        json.dump({k: v["data"] for k, v in results.items()}, f, ensure_ascii=False, indent=1)
    print("\n".join(lines))
    print("\nreport written to %s" % os.path.join(check_dir, "report.md"))
    sys.exit(1 if rep.errors else 0)


if __name__ == "__main__":
    main()
