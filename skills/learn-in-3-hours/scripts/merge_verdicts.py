#!/usr/bin/env python3
"""Merge fact-check verdicts from one or more verifiers into claims.json and the page fragments.

Usage:  python3 merge_verdicts.py <course_dir> [--dry-run]

Each verifier writes its own file <course_dir>/_work/verify/<name>.json (so parallel
verifiers never overwrite each other) containing a list of verdicts:

  {"id": "C12", "status": "corrected",                 # verified | corrected | softened | removed
   "url": "https://…", "evidence": "verbatim quote or computation",
   "text": "claim text as it now reads on the page",   # required for corrected/softened
   "note": "原稿写 X，按 S3 改为 Y",
   "edits": [{"page": "05-producer", "old": "exact old string", "new": "replacement"}]}

  New statements the verifier found unledgered: use "id": "new" plus "page", "kind",
  "text", "sources" and a final status — they are appended with fresh ids.

Edits are exact-string replacements in _work/src/<page>.html (every occurrence). An edit
whose "old" text is not found is reported and makes the script exit 1 — fix it by hand.
"""
import glob
import json
import os
import sys

FINAL = {"verified", "corrected", "softened", "removed"}


def main(course_dir, dry):
    work = os.path.join(course_dir, "_work")
    cpath = os.path.join(work, "claims.json")
    claims = json.load(open(cpath, encoding="utf-8"))
    by_id = {c["id"]: c for c in claims}
    nums = [int(c["id"][1:]) for c in claims if c.get("id", "").startswith("C") and c["id"][1:].isdigit()]
    next_id = max(nums or [0]) + 1
    files = sorted(glob.glob(os.path.join(work, "verify", "*.json")))
    if not files:
        print("no verdict files in %s" % os.path.join(work, "verify"))
        return 1
    problems, applied, added, edits_ok = [], 0, 0, 0
    src_cache = {}

    def src(page):
        if page not in src_cache:
            p = os.path.join(work, "src", page + ".html")
            src_cache[page] = open(p, encoding="utf-8").read() if os.path.exists(p) else None
        return src_cache[page]

    for f in files:
        try:
            verdicts = json.load(open(f, encoding="utf-8"))
        except ValueError as e:
            problems.append("%s: invalid JSON (%s)" % (os.path.basename(f), e))
            continue
        for v in verdicts:
            st = v.get("status")
            if st not in FINAL:
                problems.append("%s: %s has non-final status %r" % (os.path.basename(f), v.get("id"), st))
                continue
            if v.get("id") == "new":
                c = {k: v[k] for k in ("page", "kind", "text", "sources", "url", "evidence", "status", "note", "version_sensitive") if k in v}
                c["id"] = "C%d" % next_id
                next_id += 1
                claims.append(c)
                by_id[c["id"]] = c
                added += 1
            else:
                c = by_id.get(v.get("id"))
                if c is None:
                    problems.append("%s: unknown claim id %r" % (os.path.basename(f), v.get("id")))
                    continue
                for k in ("status", "url", "evidence", "text", "note", "sources"):
                    if v.get(k):
                        c[k] = v[k]
                applied += 1
            if st in ("verified", "corrected") and not (c.get("evidence") or "").strip():
                problems.append("%s: %s is %s without evidence" % (os.path.basename(f), c["id"], st))
            for e in v.get("edits", []):
                page, old, new = e.get("page"), e.get("old", ""), e.get("new", "")
                s = src(page)
                if s is None:
                    problems.append("%s: edit for unknown page %r" % (c["id"], page))
                elif not old or old not in s:
                    problems.append("%s: edit text not found in %s: %r" % (c["id"], page, old[:60]))
                else:
                    src_cache[page] = s.replace(old, new)
                    edits_ok += 1

    if not dry:
        for page, s in src_cache.items():
            if s is not None:
                with open(os.path.join(work, "src", page + ".html"), "w", encoding="utf-8") as fh:
                    fh.write(s)
        with open(cpath, "w", encoding="utf-8") as fh:
            json.dump(claims, fh, ensure_ascii=False, indent=1)
    left = [c["id"] for c in claims if c.get("status") not in FINAL]
    print("%s%d verdicts applied, %d new claims added, %d page edits applied" % ("[dry run] " if dry else "", applied, added, edits_ok))
    print("claims still unresolved: %d%s" % (len(left), (" (" + ", ".join(left[:15]) + ")") if left else ""))
    for p in problems:
        print("PROBLEM: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], "--dry-run" in sys.argv))
