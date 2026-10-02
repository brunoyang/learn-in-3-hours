#!/usr/bin/env python3
"""Objective grading for one run's outputs (works for any HTML course, either skill version).

Usage: python3 grade.py <run_dir>     (run_dir contains outputs/)
Writes <run_dir>/grading.json with expectations [{text, passed, evidence}] + metrics.
"""
import glob
import json
import os
import re
import sys
import tempfile

SKILL_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills", "learn-in-3-hours", "scripts")
sys.path.insert(0, SKILL_SCRIPTS)
import check_course as cc  # noqa: E402
from build_course import char_equiv  # noqa: E402


def find_course(outputs):
    best, n = None, 0
    for d, _, files in os.walk(outputs):
        if "/_work" in d or "/_check" in d:
            continue
        k = len([f for f in files if f.endswith(".html")])
        if k > n:
            best, n = d, k
    return best


def visible_text(doc):
    doc = re.sub(r"<(script|style|svg|header|nav|footer)\b.*?</\1>", " ", doc, flags=re.S | re.I)
    doc = re.sub(r'<section class="card sources">.*?</section>', " ", doc, flags=re.S)
    return re.sub(r"\s+", " ", cc.html.unescape(re.sub(r"<[^>]+>", " ", doc)))


def main(run_dir):
    outputs = os.path.join(run_dir, "outputs")
    course = find_course(outputs)
    exp = []
    metrics = {}
    if not course:
        exp.append({"text": "course produced", "passed": False, "evidence": "no html found"})
        json.dump({"expectations": exp}, open(os.path.join(run_dir, "grading.json"), "w"), ensure_ascii=False, indent=1)
        return
    pages = sorted(glob.glob(os.path.join(course, "*.html")))
    docs = {os.path.basename(p): open(p, encoding="utf-8", errors="ignore").read() for p in pages}
    metrics["pages"] = len(pages)

    # 1 external requests
    ext = {k: cc.external_refs(v) for k, v in docs.items()}
    bad = {k: v for k, v in ext.items() if v}
    exp.append({"text": "No page loads external resources (scripts, styles, fonts, images)",
                "passed": not bad, "evidence": "; ".join("%s: %s" % (k, v[0][:60]) for k, v in bad.items()) or "0 external requests"})

    # render checks at phone width
    rep = cc.Report()
    tmpdir = tempfile.mkdtemp(prefix="grade-")
    res = cc.render_checks(pages, os.path.join(run_dir, "render"), rep)
    over = [n for n, r in res.items() if r["data"].get("sw", 0) > r["data"].get("iw", 1e9) + 1]
    exp.append({"text": "No page scrolls sideways on a 390px phone", "passed": not over,
                "evidence": ("overflow: " + ", ".join(over)) if over else "all %d pages fit" % len(res)})
    svgs = [(n, s) for n, r in res.items() for s in r["data"].get("svgs", [])]
    minpx = [s["minpx"] for _, s in svgs if s["minpx"] is not None]
    metrics["diagrams"] = len(svgs)
    metrics["min_label_px"] = min(minpx) if minpx else None
    metrics["median_diagram_min_label_px"] = sorted(minpx)[len(minpx) // 2] if minpx else None
    small = [(n, s["i"] + 1, s["minpx"]) for n, s in svgs if s["minpx"] is not None and s["minpx"] < 11]
    exp.append({"text": "Every diagram label renders at >= 11px on a 390px phone", "passed": bool(svgs) and not small,
                "evidence": "%d/%d diagrams below 11px; smallest %.1fpx" % (len(small), len(svgs), min(minpx) if minpx else -1)})
    geo = [(n, s["i"] + 1, len(s["overlaps"]) + len(s["spill"]) + len(s["outb"])) for n, s in svgs if s["overlaps"] or s["spill"] or s["outb"]]
    exp.append({"text": "No overlapping, box-overflowing or cut-off diagram labels", "passed": bool(svgs) and not geo,
                "evidence": "%d/%d diagrams with geometry defects: %s" % (len(geo), len(svgs), ", ".join("%s#%d(%d)" % g for g in geo[:8]))})

    # sources per page (exclude map/review-ish pages by requiring content pages = all but first & last)
    content = [k for k in sorted(docs)][1:-1]
    no_src = [k for k in content if not re.search(r'<a\b[^>]*href="https?://', docs[k])]
    exp.append({"text": "Every content page links at least one source URL", "passed": not no_src,
                "evidence": ("missing on: " + ", ".join(no_src)) if no_src else "all %d content pages cite a URL" % len(content)})

    # baseline / as-of on the first (map) page
    first = visible_text(docs[sorted(docs)[0]])
    m = re.search(r"(截至|截止|as of|基线|以[^。]{0,20}为准|资料[^。]{0,6}20\d\d)", first, re.I)
    exp.append({"text": "Map page states a content baseline / as-of date", "passed": bool(m),
                "evidence": first[max(0, m.start() - 30):m.end() + 50] if m else "no baseline/as-of wording found"})

    # self-test with answers on content pages
    no_q = [k for k in content if not re.search(r"<details\b", docs[k])]
    exp.append({"text": "Every content page has a self-check with a hidden answer", "passed": not no_q,
                "evidence": ("missing on: " + ", ".join(no_q)) if no_q else "all content pages"})

    # analogy boundary wording on content pages
    no_b = [k for k in content if not re.search(r"(失效|不成立|边界|不适用|哪里不像)", visible_text(docs[k]))]
    metrics["pages_without_analogy_boundary"] = len(no_b)

    # time honesty
    stated, est = 0, 0.0
    per = []
    for k in sorted(docs):
        d = docs[k]
        head = re.search(r"<header\b.*?</header>", d, re.S)
        hs = cc.html.unescape(re.sub(r"<[^>]+>", " ", head.group(0))) if head else d[:3000]
        sm = re.search(r"(?:本页约|预计|约)\s*(\d+)\s*分钟", hs)
        if not sm:
            sm = re.search(r"data-minutes=\"(\d+)\"", d)
        s = int(sm.group(1)) if sm else 0
        core = re.sub(r"<details\b.*?</details>", " ", d, flags=re.S)
        n_svg = len(re.findall(r"<svg\b", core)) - len(re.findall(r'<svg width="0"', core))
        n_q = len(re.findall(r"<details\b", d))
        e = char_equiv(visible_text(core)) / 250.0 + 2 * max(0, n_svg) + 1.5 * n_q + (3 if re.search(r"复述", visible_text(d)) else 0)
        stated += s
        est += e
        per.append((k, s, round(e, 1)))
    ratio = stated / est if est else 0
    metrics["stated_minutes_total"] = stated
    metrics["content_estimate_minutes_total"] = round(est)
    metrics["stated_over_estimate"] = round(ratio, 2)
    exp.append({"text": "Stated study time is within ±30% of a content-based estimate", "passed": 0.7 <= ratio <= 1.3,
                "evidence": "stated %d min vs content estimate %d min (ratio %.2f)" % (stated, est, ratio)})
    metrics["per_page_minutes"] = per

    passed = sum(1 for x in exp if x["passed"])
    out = {"course_dir": course, "expectations": exp, "metrics": metrics,
           "summary": {"passed": passed, "failed": len(exp) - passed, "total": len(exp), "pass_rate": round(passed / float(len(exp)), 2)}}
    json.dump(out, open(os.path.join(run_dir, "grading.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out["summary"]), json.dumps({k: v for k, v in metrics.items() if k != "per_page_minutes"}, ensure_ascii=False))
    for x in exp:
        print(("PASS " if x["passed"] else "FAIL ") + x["text"] + " — " + x["evidence"][:160])


if __name__ == "__main__":
    main(sys.argv[1])
