#!/usr/bin/env python3
"""Assemble a learn-in-3-hours course from page fragments.

Usage:  python3 build_course.py <course_dir>

Reads   <course_dir>/_work/course.json
        <course_dir>/_work/src/<page-id>.html   (fragments: data-block sections only)
        <course_dir>/_work/sources.json
        <course_dir>/_work/claims.json          (optional; used for the review page summary)
Writes  <course_dir>/<page-id>.html             (self-contained pages)
        <course_dir>/_work/timing.json          (computed minutes per page)

Why a builder: the style, header, navigation, citation numbering and — most
importantly — the time estimates are produced by code, so they stay consistent
and honest no matter how many pages the model writes.
"""
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(HERE, "..", "assets", "course.css")

# Study-speed constants (Chinese technical text, learning mode — slower than casual reading).
CPM = 250          # char-equivalents per minute
FIG_MIN = 2.0      # minutes to study one diagram
Q_MIN = 1.5        # minutes per self-test question
RETELL_MIN = 3.0   # minutes for a spoken retell
REVIEW_Q_MIN = 3.0 # minutes per retell question on the review page
TASK_MAX = 20      # cap for a declared hands-on task

LABELS = {
    "hook": "先想一想",
    "anchor": "从你已经懂的出发",
    "points": "要点",
    "analogy": "打个比方",
    "boundary": "这个比方在哪失效",
    "selftest": "自测（先答再看）",
    "retell": "复述挑战",
    "recap": "本幕小结",
    "meta": "先立骨架：全课反复用到的几样东西",
    "route": "学习路线",
    "task": "动手",
    "sticking": "最容易卡壳的地方",
    "retell-set": "复述挑战（先口答，再展开看要点）",
    "further": "往下挖",
    "mindmap": "全程思维导图",
    "note": "",
}
NO_CARD = {"lede"}
CORE_BLOCKS = {"lede", "hook", "anchor", "points", "analogy", "boundary", "recap", "meta", "route", "sticking", "note"}

THEMES = {
    "ocean": None,  # default palette in course.css
    "forest": (("#15803d", "#e8f6ec", "#7c3aed", "#f3edff"), ("#4ade80", "#13261a", "#b196ff", "#221a38")),
    "plum": (("#7c3aed", "#f1ebff", "#0e7490", "#e4f4f7"), ("#b196ff", "#221a38", "#38bdf8", "#10242e")),
    "ember": (("#c2410c", "#fdf0e8", "#1d4ed8", "#eaf0ff"), ("#fb923c", "#2d1e12", "#7aa7ff", "#1a2440")),
    "teal": (("#0f766e", "#e3f4f2", "#b45309", "#fdf3e2"), ("#2dd4bf", "#10292a", "#f5b041", "#2e2515")),
}

ARROW_DEFS = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>'
    + "".join(
        '<marker id="{0}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse"><path class="{0}" d="M0,0 L10,5 L0,10 z"/></marker>'.format(m)
        for m in ("ah", "ah-a", "ah-b", "ah-w")
    )
    + "</defs></svg>"
)


class BuildError(Exception):
    pass


PLACEHOLDER = '<section data-block="note" data-placeholder="1"><p>（本页尚未写完）</p></section>'


def esc(s):
    return html.escape(str(s), quote=True)


def strip_tags(s):
    s = re.sub(r"<svg\b.*?</svg>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return html.unescape(s)


def tidy(text):
    """Collapse whitespace left by stripped tags and drop spaces before CJK punctuation."""
    t = " ".join(text.split())
    return re.sub(r"\s+([，。；：！？、）】」』])", r"\1", t)


def char_equiv(text):
    """CJK chars count 1; each Latin word ~1.2; digit groups 1."""
    cjk = len(re.findall(r"[㐀-鿿豈-﫿]", text))
    words = len(re.findall(r"[A-Za-z][A-Za-z'\-\.]*", text))
    nums = len(re.findall(r"\d+(?:\.\d+)?", text))
    return cjk + 1.2 * words + nums


def find_blocks(src):
    """Return list of (name, tag, attrs_str, inner_html, start, end) for top-level data-block elements."""
    blocks = []
    pos = 0
    open_re = re.compile(r'<(section|figure|details|div|p)\b([^>]*\bdata-block="([a-z\-]+)"[^>]*)>', re.I)
    while True:
        m = open_re.search(src, pos)
        if not m:
            break
        tag, attrs, name = m.group(1).lower(), m.group(2), m.group(3)
        depth, i = 1, m.end()
        tok = re.compile(r"<(/?)%s\b[^>]*>" % tag, re.I)
        while depth:
            t = tok.search(src, i)
            if not t:
                raise BuildError("unclosed <%s data-block=\"%s\">" % (tag, name))
            depth += -1 if t.group(1) else 1
            i = t.end()
            if depth == 0:
                inner = src[m.end():t.start()]
                blocks.append((name, tag, attrs, inner, m.start(), t.end()))
        pos = i
    return blocks


def attr(attrs, key):
    m = re.search(r'\b%s="([^"]*)"' % re.escape(key), attrs)
    return m.group(1) if m else None


def page_minutes(ptype, blocks):
    core = deep = 0.0
    figs = qs = 0
    task_min = 0.0
    retell = False
    review_qs = 0
    for name, tag, attrs, inner, _, _ in blocks:
        text = strip_tags(inner)
        n_svg = len(re.findall(r"<svg\b", inner, re.I))
        if name == "deep":
            deep += char_equiv(text) / CPM + n_svg * FIG_MIN
            continue
        if name in ("figure", "mindmap"):
            figs += max(1, n_svg)
            core += char_equiv(text) / CPM  # caption
            continue
        if name == "selftest":
            n = len(re.findall(r'class="q\b', inner))
            qs += n  # reading the question is included in Q_MIN
            continue
        if name == "retell":
            retell = True
            continue
        if name == "retell-set":
            review_qs += len(re.findall(r'class="q\b', inner))
            continue
        if name == "task":
            try:
                task_min += min(TASK_MAX, float(attr(attrs, "data-minutes") or 5))
            except ValueError:
                task_min += 5
            core += char_equiv(text) / CPM
            continue
        figs += n_svg
        core += char_equiv(text) / CPM
    core += figs * FIG_MIN + qs * Q_MIN + (RETELL_MIN if retell else 0) + task_min + review_qs * REVIEW_Q_MIN
    return max(2, int(round(core))), (max(1, int(round(deep))) if deep else 0)


def load_json(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        raise BuildError("missing %s" % path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def theme_css(name):
    t = THEMES.get(name or "ocean")
    if name and name not in THEMES:
        print("warning: unknown theme %r, using ocean" % name, file=sys.stderr)
    if not t:
        return ""
    (a, asoft, b, bsoft), (da, dasoft, db, dbsoft) = t
    return (":root{--accent:%s;--accent-soft:%s;--accent2:%s;--accent2-soft:%s}"
            "@media (prefers-color-scheme:dark){:root{--accent:%s;--accent-soft:%s;--accent2:%s;--accent2-soft:%s}}"
            % (a, asoft, b, bsoft, da, dasoft, db, dbsoft))


def render_block(name, tag, attrs, inner, minutes_deep):
    if name in NO_CARD:
        return '<p class="lede">%s</p>' % inner.strip() if tag == "p" else '<div class="lede">%s</div>' % inner
    if name == "figure" or name == "mindmap":
        cls = "card card-figure"
        label = '<h2 class="lbl">%s</h2>' % LABELS["mindmap"] if name == "mindmap" else ""
        return '<figure class="%s">%s%s</figure>' % (cls, label, inner)
    if name == "deep":
        extra = " · 约 %d 分钟" % minutes_deep if minutes_deep else ""
        summary = re.search(r"<summary\b[^>]*>(.*?)</summary>", inner, re.S)
        body = re.sub(r"<summary\b[^>]*>.*?</summary>", "", inner, count=1, flags=re.S)
        title = summary.group(1).strip() if summary else "深入一层"
        return '<details class="card card-deep"><summary>%s（选读%s）</summary>%s</details>' % (title, extra, body)
    label = LABELS.get(name, name)
    if name == "task":
        m = attr(attrs, "data-minutes")
        if m:
            label += " · 约 %s 分钟" % m
    h = '<h2 class="lbl">%s</h2>' % label if label else ""
    return '<section class="card card-%s">%s%s</section>' % (name, h, inner)


def main(course_dir):
    work = os.path.join(course_dir, "_work")
    course = load_json(os.path.join(work, "course.json"))
    sources = load_json(os.path.join(work, "sources.json"), default=[])
    claims = load_json(os.path.join(work, "claims.json"), default=[])
    src_by_id = {s["id"]: s for s in sources}
    pages = course["pages"]
    acts = course.get("acts") or ["立靶", "原理", "应用", "延伸"]
    slug = course.get("slug") or os.path.basename(os.path.abspath(course_dir))
    css = open(CSS_PATH, encoding="utf-8").read() + theme_css(course.get("theme"))
    errors = []

    # Pass 1: parse fragments and compute minutes.
    parsed = []
    for p in pages:
        path = os.path.join(work, "src", p["id"] + ".html")
        if not os.path.exists(path):
            # Build what exists so early pages can be previewed; check_course.py fails on placeholders.
            print("warning: %s not written yet — building a placeholder page" % p["id"], file=sys.stderr)
            src = PLACEHOLDER
        else:
            src = open(path, encoding="utf-8").read()
        try:
            blocks = find_blocks(src)
        except BuildError as e:
            errors.append("%s: %s" % (p["id"], e))
            parsed.append(None)
            continue
        if not blocks:
            errors.append("%s: no data-block elements found" % p["id"])
        core_min, deep_min = page_minutes(p.get("type", "concept"), blocks)
        parsed.append({"src": src, "blocks": blocks, "core": core_min, "deep": deep_min})
    if errors:
        raise BuildError("\n".join(errors))

    total = sum(x["core"] for x in parsed)
    total_deep = sum(x["deep"] for x in parsed)
    starts, acc = [], 0
    for x in parsed:
        starts.append(acc)
        acc += x["core"]

    # Analogy library for the review page, harvested from concept pages.
    analogies = []
    for p, x in zip(pages, parsed):
        d = {b[0]: b[3] for b in x["blocks"]}
        if "analogy" in d:
            a = tidy(strip_tags(re.sub(r"<cite\b[^>]*>.*?</cite>", "", d["analogy"], flags=re.S)))
            bnd = re.sub(r"<cite\b[^>]*>.*?</cite>", "", d.get("boundary", ""), flags=re.S)
            items = re.findall(r"<li\b[^>]*>(.*?)</li>", bnd, re.S) or [bnd]
            bt = [tidy(strip_tags(i)) for i in items if strip_tags(i).strip()]
            analogies.append((p, a, bt))

    def cite_repl_factory(order):
        def repl(m):
            ids = [i.strip() for i in m.group(1).split(",") if i.strip()]
            out = []
            for sid in ids:
                if sid not in src_by_id:
                    errors.append("unknown source id %r" % sid)
                    continue
                if sid not in order:
                    order.append(sid)
                n = order.index(sid) + 1
                out.append('<a href="#src-%s" title="%s">[%d]</a>' % (esc(sid), esc(src_by_id[sid].get("title", "")), n))
            return '<sup class="cite">%s</sup>' % "".join(out) if out else ""
        return repl

    def source_li(s, anchor=True):
        bits = ['<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(s["url"]), esc(s.get("title", s["url"])))]
        meta = " · ".join(esc(v) for v in (s.get("publisher"), s.get("date")) if v)
        if meta:
            bits.append('<span class="meta-note"> — %s</span>' % meta)
        return '<li%s>%s</li>' % (' id="src-%s"' % esc(s["id"]) if anchor else "", "".join(bits))

    def page_href(i):
        return pages[i]["id"] + ".html"

    for i, (p, x) in enumerate(zip(pages, parsed)):
        ptype = p.get("type", "concept")
        order = []
        body_parts = []
        for name, tag, attrs, inner, _, _ in x["blocks"]:
            inner2 = re.sub(r'<cite\s+data-src="([^"]+)"\s*>\s*</cite>', cite_repl_factory(order), inner)
            body_parts.append(render_block(name, tag, attrs, inner2, x["deep"] if name == "deep" else 0))
        body = "\n".join(body_parts)

        if ptype == "map":
            head = '<section class="card card-target"><h2 class="lbl">靶心问题：学完你能做成的事</h2><p>%s</p></section>' % esc(course.get("target", ""))
            info = []
            if course.get("learner"):
                info.append("<p><b>适合：</b>%s</p>" % esc(course["learner"]))
            if course.get("baseline"):
                info.append("<p><b>内容基线：</b>%s</p>" % esc(course["baseline"]))
            if course.get("disclaimer"):
                info.append('<p class="meta-note">%s</p>' % esc(course["disclaimer"]))
            if info:
                head += '<section class="card card-note">%s</section>' % "".join(info)
            # Insert target right after the lede if there is one.
            if body.startswith('<p class="lede">') or body.startswith('<div class="lede">'):
                cut = body.find("\n") if "\n" in body else len(body)
                body = body[:cut] + "\n" + head + body[cut:]
            else:
                body = head + body
            rows = []
            for ai, aname in enumerate(acts):
                idx = [j for j, q in enumerate(pages) if q.get("act", 0) == ai]
                if not idx:
                    continue
                mins = sum(parsed[j]["core"] for j in idx)
                rows.append('<tr><th colspan="3">第 %d 幕 · %s（约 %d 分钟）</th></tr>' % (ai, esc(aname), mins))
                for j in idx:
                    rows.append('<tr><td><a href="%s">%s</a></td><td>%d 分钟%s</td><td class="tick" data-page="%s"></td></tr>'
                                % (page_href(j), esc(pages[j]["title"]), parsed[j]["core"],
                                   (" <span class=\"meta-note\">+%d 选读</span>" % parsed[j]["deep"]) if parsed[j]["deep"] else "",
                                   esc(pages[j]["id"])))
            body += ('<section class="card card-timetable"><h2 class="lbl">时间表（按内容量估算：核心约 %d 分钟，选读另加约 %d 分钟）</h2>'
                     '<div class="tbl"><table>%s</table></div></section>' % (total, total_deep, "".join(rows)))
            if sources:
                body += '<section class="card sources"><h2 class="lbl">信息源</h2><ol>%s</ol></section>' % "".join(source_li(s, anchor=False) for s in sources)

        if ptype == "review":
            if analogies:
                items = "".join('<div class="ana"><a class="ana-p" href="%s">%s</a><p>%s</p><ul class="ana-b">%s</ul></div>'
                                % (esc(p2["id"]) + ".html", esc(p2["title"]), esc(a), "".join("<li>%s</li>" % esc(x) for x in b))
                                for p2, a, b in analogies)
                items += '<div class="ana ana-blank"><span class="ana-p">你的比方</span><p>　</p><ul class="ana-b"><li>　</li></ul></div>' * 2
                body += ('<section class="card card-analogies"><h2 class="lbl">比方库：本课所有类比，以及它们在哪失效（最后两格留给你自己的比方）</h2>%s</section>' % items)
            if claims:
                st = {}
                for c in claims:
                    st[c.get("status", "?")] = st.get(c.get("status", "?"), 0) + 1
                fixed = [c for c in claims if c.get("status") == "corrected"]
                soft = [c for c in claims if c.get("status") == "softened"]
                gone = [c for c in claims if c.get("status") == "removed"]
                lis = "".join("<li>%s</li>" % esc(c.get("text", "")) for c in fixed[:12])
                body += ('<section class="card card-factcheck"><h2 class="lbl">事实核查说明</h2>'
                         '<p>本课共登记 %d 条可核查的事实，逐条对照信息源核对：%d 条核实无误，%d 条按信息源修正，%d 条按依据收紧了措辞，%d 条因无法核实而删除。</p>%s</section>'
                         % (len(claims), st.get("verified", 0), st.get("corrected", 0), len(soft), len(gone),
                            ("<p>以下说法按信息源修正过（页面上已是修正后的版本）：</p><ul>%s</ul>" % lis) if lis else ""))

        if order:
            body += '<section class="card sources"><h2 class="lbl">本页来源</h2><ol>%s</ol></section>' % "".join(source_li(src_by_id[s]) for s in order)

        act = p.get("act", 0)
        act_name = acts[act] if act < len(acts) else ""
        pos = "约 %d–%d / %d 分钟" % (starts[i], starts[i] + x["core"], total)
        dur = "本页约 %d 分钟" % x["core"] + (" · 选读 +%d" % x["deep"] if x["deep"] else "")
        prev_a = '<a href="%s">← %s</a>' % (page_href(i - 1), esc(pages[i - 1]["title"])) if i > 0 else "<span></span>"
        next_a = '<a class="nx" href="%s">%s →</a>' % (page_href(i + 1), esc(pages[i + 1]["title"])) if i + 1 < len(pages) else '<span class="nx"></span>'
        map_i = next((j for j, q in enumerate(pages) if q.get("type") == "map"), 0)
        mid = '<a class="mid" href="%s">学习地图</a>' % page_href(map_i) if map_i not in (i, i - 1) else '<span class="mid"></span>'
        num = re.match(r"(\d+)", p["id"])
        kicker = "%s · 第 %d 幕 · %s" % (num.group(1) if num else "", act, act_name)

        doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {course}</title>
<style>{css}</style></head>
<body data-page="{pid}" data-type="{ptype}" data-minutes="{core}" data-minutes-deep="{deep}">
{defs}
<header class="top"><div class="top-in"><span class="act">第 {act} 幕 · {act_name}</span><span>{pos}</span><span>{dur}</span></div></header>
<main class="wrap">
<p class="kicker">{kicker}</p>
<h1>{title}</h1>
{body}
</main>
<div class="done-row"><button class="done-btn" id="done" type="button">标记为已学完</button></div>
<div class="bar"><i id="bar"></i></div>
<nav class="pager">{prev}{mid}{next}</nav>
<script>
(function(){{var K="l3h:{slug}",ids={ids},me="{pid}";
function g(){{try{{return JSON.parse(localStorage.getItem(K)||"{{}}")||{{}}}}catch(e){{return {{}}}}}}
function s(v){{try{{localStorage.setItem(K,JSON.stringify(v))}}catch(e){{}}}}
function paint(){{var d=g(),n=0;ids.forEach(function(i){{if(d[i])n++}});
var b=document.getElementById("done");if(b){{b.className="done-btn"+(d[me]?" on":"");b.textContent=d[me]?"✓ 已学完（再点取消）":"标记为已学完";}}
var bar=document.getElementById("bar");if(bar)bar.style.width=(100*n/ids.length)+"%";
[].forEach.call(document.querySelectorAll("[data-page].tick"),function(td){{td.textContent=d[td.getAttribute("data-page")]?"✓":""}});}}
var b=document.getElementById("done");if(b)b.onclick=function(){{var d=g();d[me]=!d[me];s(d);paint();}};paint();}})();
</script>
</body></html>
""".format(title=esc(p["title"]), course=esc(course.get("title", "")), css=css, pid=esc(p["id"]), ptype=esc(ptype),
           core=x["core"], deep=x["deep"], defs=ARROW_DEFS, act=act, act_name=esc(act_name), pos=pos, dur=dur,
           kicker=esc(kicker), body=body, prev=prev_a, mid=mid, next=next_a, slug=esc(slug),
           ids=json.dumps([q["id"] for q in pages]))
        with open(os.path.join(course_dir, p["id"] + ".html"), "w", encoding="utf-8") as f:
            f.write(doc)

    if errors:
        raise BuildError("\n".join(sorted(set(errors))))
    timing = {"total_core_minutes": total, "total_deep_minutes": total_deep,
              "pages": [{"id": p["id"], "core": x["core"], "deep": x["deep"]} for p, x in zip(pages, parsed)]}
    with open(os.path.join(work, "timing.json"), "w", encoding="utf-8") as f:
        json.dump(timing, f, ensure_ascii=False, indent=1)
    print("built %d pages → %s" % (len(pages), os.path.abspath(course_dir)))
    print("estimated study time: core %d min (+%d min optional deep-dives)" % (total, total_deep))
    for p, x in zip(pages, parsed):
        print("  %-28s %3d min%s" % (p["id"], x["core"], (" (+%d)" % x["deep"]) if x["deep"] else ""))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    try:
        main(sys.argv[1])
    except BuildError as e:
        print("BUILD FAILED:\n" + str(e), file=sys.stderr)
        sys.exit(1)
