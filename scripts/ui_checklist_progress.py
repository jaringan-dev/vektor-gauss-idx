#!/usr/bin/env python3
"""Progress monitor for CHECKLIST-UI-UX.md (stdlib only).

    python scripts/ui_checklist_progress.py            # summary per group + overall
    python scripts/ui_checklist_progress.py --open     # list items not done yet
    python scripts/ui_checklist_progress.py --p P0     # only one priority (P0..P3, G)
    python scripts/ui_checklist_progress.py --file X   # another checklist file

Item format:  - [ ] **A1** (P0·M) Title — selesai bila: ...
Groups are the "### A. Name" headings; items without a (priority·effort) tag still count.
Exit code is always 0 — this is a report, not a gate.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "CHECKLIST-UI-UX.md"
GROUP_RE = re.compile(r"^###\s+([A-Z])\.\s+(.*\S)\s*$")
ITEM_RE = re.compile(r"^- \[([ xX])\]\s+\*\*([A-Z]\d+)\*\*\s*(?:\((P\d|G)\s*[·.\-]\s*([SML])\)\s*)?(.*)$")
EFFORT_DAYS = {"S": 0.5, "M": 1.5, "L": 3.0}  # rough midpoints of the S/M/L bands in the doc


def parse(path: Path) -> tuple[dict[str, str], list[dict]]:
    groups: dict[str, str] = {}
    items: list[dict] = []
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        g = GROUP_RE.match(line)
        if g:
            current = g.group(1)
            groups[current] = g.group(2)
            continue
        m = ITEM_RE.match(line)
        if m:
            done, ident, prio, effort, title = m.groups()
            items.append({
                "group": ident[0] if ident[0] in groups or not current else current,
                "id": ident,
                "done": done.lower() == "x",
                "prio": prio or "-",
                "effort": effort or "-",
                "title": title.strip(),
            })
    return groups, items


def bar(done: int, total: int, width: int = 20) -> str:
    filled = round(width * done / total) if total else 0
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def pct(done: int, total: int) -> str:
    return f"{(100 * done // total) if total else 0:3d}%"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=DEFAULT)
    ap.add_argument("--open", action="store_true", help="list items that are not done")
    ap.add_argument("--p", metavar="PRIO", help="filter by priority: P0 P1 P2 P3 G")
    args = ap.parse_args(argv)

    if not args.file.exists():
        print(f"file not found: {args.file}")
        return 0
    groups, items = parse(args.file)
    if args.p:
        items = [i for i in items if i["prio"] == args.p.upper()]
    if not items:
        print("no checklist items matched")
        return 0

    if args.open:
        todo = [i for i in items if not i["done"]]
        print(f"Butir belum selesai: {len(todo)} dari {len(items)}\n")
        for i in sorted(todo, key=lambda x: (x["prio"], x["id"])):
            print(f"  {i['id']:<4} {i['prio']:<2}·{i['effort']}  {i['title'][:96]}")
        return 0

    by_group: dict[str, list[dict]] = defaultdict(list)
    for i in items:
        by_group[i["group"]].append(i)
    print(f"Checklist: {args.file.name}" + (f"  (prioritas {args.p.upper()})" if args.p else ""))
    print()
    for gk in sorted(by_group):
        gi = by_group[gk]
        d = sum(1 for i in gi if i["done"])
        print(f"  {gk}  {groups.get(gk, '?')[:34]:<34} {bar(d, len(gi))} {d:2d}/{len(gi):<2d} {pct(d, len(gi))}")

    total, done = len(items), sum(1 for i in items if i["done"])
    # "Actionable now" excludes gated (G) and open decisions (J): the work that can start today.
    act = [i for i in items if i["prio"] != "G" and i["group"] != "J"]
    adone = sum(1 for i in act if i["done"])
    print()
    print(f"  SEMUA                                   {bar(done, total)} {done:2d}/{total:<2d} {pct(done, total)}")
    print(f"  DAPAT DIKERJAKAN SEKARANG (tanpa G, J)  {bar(adone, len(act))} {adone:2d}/{len(act):<2d} {pct(adone, len(act))}")

    print("\n  Per prioritas (sisa / total):")
    for p in ("P0", "P1", "P2", "P3", "G", "-"):
        pi = [i for i in items if i["prio"] == p]
        if pi:
            left = [i for i in pi if not i["done"]]
            days = sum(EFFORT_DAYS.get(i["effort"], 0) for i in left)
            extra = f"  ~{days:g} hari kerja tersisa (perkiraan kasar)" if days else ""
            print(f"    {p:<2} {len(left):2d} / {len(pi):<2d}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
