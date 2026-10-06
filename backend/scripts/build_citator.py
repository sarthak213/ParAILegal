"""Build the citator from the judgments corpus (app/judgments/citator.py).

    python -m scripts.build_citator <SC>/metadata data/judgments --out data/judgments/citator.json

<SC>/metadata holds the dataset's per-year parquet metadata (all 43,547 judgments, so a citation
resolves even to a judgment whose text is not built yet). Reads every data/judgments/YYYY.jsonl.

Writes:
    edges    [{citing, cited, citation, how, para, treatment, evidence}]   one per citation;
             cited is null when the citation could not be matched to a judgment in the dataset
    status   {cited id: {title, cited_by, followed_by, distinguished_by, doubted_by,
              overruled_noted_in [{id, title, decided, evidence}], per_incuriam_noted_in [...]}}
             "noted in": the citing judgment says the case was overruled (or per incuriam); it may
             have done so itself ("is hereby overruled") or be reporting an earlier ruling ("which has
             been overruled"). The evidence sentence says which.
    stats    how many citations resolved, by kind and by method
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.judgments.citator import Index, _key, learn_parallels, treatments_in

KIND = {"(": "scc", "A": "air", "[": "scr"}


def load_index(meta_dir: Path) -> Index:
    import pyarrow.parquet as pq

    index = Index()
    for f in sorted(meta_dir.glob("*.parquet")):
        for r in pq.read_table(f, columns=["title", "path", "decision_date", "nc_display"]).to_pylist():
            decided = ""
            if r["decision_date"] and len(r["decision_date"]) == 10:
                d, m, y = r["decision_date"].split("-")
                decided = f"{y}-{m}-{d}"
            index.add(r["path"], (r["title"] or "").replace(" versus ", " v. "), decided, r["nc_display"] or "")
    return index


def kind_of(citation: str) -> str:
    return "insc" if "INSC" in citation.upper() else KIND.get(citation[:1], "scr")


def scr_key(raw: str) -> str:
    return " ".join(raw.replace("S.C.R.", "SCR").replace("S. C. R.", "SCR").split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("meta_dir", type=Path)
    ap.add_argument("judgments_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    index = load_index(args.meta_dir)
    records = []
    for f in sorted(args.judgments_dir.glob("[0-9][0-9][0-9][0-9].jsonl")):
        records += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()]
    records = [r for r in records if "error" not in r]
    learned = learn_parallels(index, [p["text"] for r in records for p in r["paragraphs"]])

    names = {}  # the case name printed with each citation, from build_judgments
    for r in records:
        for c in r["cases"]:
            if c["name"]:
                names.setdefault((r["id"], c["citation"]), c["name"])

    edges, stats = [], Counter()
    for r in records:
        own, seen = r["id"], set()
        # the headnote last: older Reports list the cases relied on there ("... F.C.R. 31
        # distinguished."), but a case the judgment itself cites is counted from the judgment
        for p in r["paragraphs"] + [{"n": "headnote", "text": r.get("headnote", ""), "headnote": True}]:
            for raw, _s, _e, treatment, evidence in treatments_in(p["text"]):
                kind = kind_of(raw)
                key = scr_key(raw) if kind == "scr" else _key(raw) if kind in ("scc", "air") else " ".join(raw.split())
                if p.get("headnote") and key in seen:
                    continue
                seen.add(key)
                cited, how = index.resolve(key, kind, names.get((own, key), ""))
                if cited == own:
                    continue  # its own citation, in a running header
                stats[f"{kind} total"] += 1
                stats[f"{kind} resolved"] += bool(cited)
                stats[f"by {how}"] += bool(cited)
                edges.append({"citing": own, "cited": cited, "citation": key, "how": how, "para": p["n"],
                              "treatment": treatment, "evidence": " ".join(evidence.split())[:400] if treatment else ""})

    status: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    decided = {r["id"]: r.get("decided", "") for r in records}
    for e in edges:
        if not e["cited"]:
            continue
        s = status[e["cited"]]
        s["cited_by"].append(e["citing"])
        if e["treatment"] in ("followed", "distinguished", "doubted"):
            s[f"{e['treatment']}_by"].append(e["citing"])
        elif e["treatment"] in ("overruled", "per incuriam"):
            key = "overruled_noted_in" if e["treatment"] == "overruled" else "per_incuriam_noted_in"
            s[key].append({"id": e["citing"], "title": index.by_id[e["citing"]].title if e["citing"] in index.by_id else "",
                           "decided": decided.get(e["citing"], ""), "evidence": e["evidence"]})
    out = {
        "edges": edges,
        "status": {k: {"title": index.by_id[k].title if k in index.by_id else "",
                       **{f: (sorted(set(v)) if f.endswith("_by") and v and isinstance(v[0], str) else v)
                          for f, v in s.items()}} for k, s in status.items()},
        "stats": {**dict(stats), "parallel citations learned": learned, "judgments": len(records),
                  "dataset judgments": len(index.by_id)},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out["stats"], indent=1))


if __name__ == "__main__":
    main()
