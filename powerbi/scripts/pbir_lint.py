#!/usr/bin/env python3
"""pbir_lint.py - Fast validation of a PBIP report layer before opening Desktop.

Checks, in order of severity:
  1. Every .json under <X>.Report/definition parses.
  2. pages.json lists exactly the page folders present (order + membership),
     with no duplicates; Tooltip pages must be HiddenInViewMode.
  3. Every visual.json 'name' matches its folder name; ids unique across report.
  4. Every field reference (Entity/Property pairs) in every visual resolves to a
     table + column/measure that actually exists in the semantic model (TMDL),
     including filterConfig references that go through From-clause aliases.
     A ref declared Measure that is actually a column (or vice versa) is a
     finding too.
  5. No leftover 'PLACEHOLDER' from a template that was not fully bound.
  6. Layout sanity per page: visuals inside the canvas, no overlapping pair.
  7. Custom tooltip bindings point at an existing page of type Tooltip.

Exit code 0 = clean, 1 = findings. Output is designed to be read by Claude Code
(one finding per line, path-prefixed) so the agent can self-correct.

Usage: python scripts/pbir_lint.py [repo_root]
"""

import json
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^[0-9a-fA-F]{20}$|^[0-9a-zA-Z]{20}$")
OVERLAP_TOLERANCE = 1.0  # px


def find_dirs(root: Path):
    report = next(root.glob("*.Report"), None)
    model = next(root.glob("*.SemanticModel"), None)
    return report, model


def parse_tmdl_objects(model_dir: Path):
    """Extract table -> {columns, measures} from TMDL files. Regex-based, tolerant."""
    objects = {}
    tables_dir = model_dir / "definition" / "tables"
    if not tables_dir.exists():
        return objects
    tbl_re = re.compile(r"^table\s+(?:'([^']+)'|(\S+))", re.M)
    col_re = re.compile(r"^\s+column\s+(?:'([^']+)'|([^\s=]+))", re.M)
    mea_re = re.compile(r"^\s+measure\s+(?:'([^']+)'|([^\s=]+))\s*=", re.M)
    for f in tables_dir.glob("*.tmdl"):
        text = f.read_text(encoding="utf-8")
        m = tbl_re.search(text)
        if not m:
            continue
        tname = m.group(1) or m.group(2)
        cols = {a or b for a, b in col_re.findall(text)}
        meas = {a or b for a, b in mea_re.findall(text)}
        objects[tname] = {"columns": cols, "measures": meas}
    return objects


def walk_refs(node, refs, path="", aliases=None):
    """Collect (entity, property, kind, jsonpath) from PBIR query structures.

    PBIR encodes bindings as {"Column": {"Expression": {"SourceRef": {"Entity": T}},
    "Property": C}} and the same shape under "Measure". Inside filterConfig the
    SourceRef may carry {"Source": alias} instead, with the alias declared in a
    sibling "From" clause: [{"Name": alias, "Entity": T}]. We thread the alias
    environment down the walk so those resolve too.
    """
    aliases = aliases or {}
    if isinstance(node, dict):
        frm = node.get("From")
        if isinstance(frm, list):
            local = dict(aliases)
            for entry in frm:
                if isinstance(entry, dict) and "Name" in entry and "Entity" in entry:
                    local[entry["Name"]] = entry["Entity"]
            aliases = local
        for kind in ("Column", "Measure", "HierarchyLevel"):
            sub = node.get(kind)
            if isinstance(sub, dict) and "Property" in sub:
                entity = _find_entity(sub, aliases)
                refs.append((entity, sub["Property"], kind, path))
        for k, v in node.items():
            walk_refs(v, refs, f"{path}.{k}", aliases)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_refs(v, refs, f"{path}[{i}]", aliases)


def _find_entity(node, aliases):
    if isinstance(node, dict):
        if isinstance(node.get("Entity"), str):
            return node["Entity"]
        if isinstance(node.get("Source"), str):
            return aliases.get(node["Source"])
        for v in node.values():
            e = _find_entity(v, aliases)
            if e:
                return e
    elif isinstance(node, list):
        for v in node:
            e = _find_entity(v, aliases)
            if e:
                return e
    return None


def check_layout(pages_dir, parsed, root, findings):
    for page_dir in sorted(p for p in pages_dir.iterdir() if p.is_dir()):
        page_json = page_dir / "page.json"
        if page_json not in parsed:
            findings.append(f"[PAGES] {page_dir.name}: missing page.json")
            continue
        page = parsed[page_json]
        pw, ph = page.get("width", 1280), page.get("height", 720)
        rects = []
        for vjson in sorted(page_dir.glob("visuals/*/visual.json")):
            pos = parsed[vjson].get("position", {})
            x, y = pos.get("x", 0), pos.get("y", 0)
            w, h = pos.get("width", 0), pos.get("height", 0)
            rel = vjson.relative_to(root)
            if x < -OVERLAP_TOLERANCE or y < -OVERLAP_TOLERANCE \
                    or x + w > pw + OVERLAP_TOLERANCE or y + h > ph + OVERLAP_TOLERANCE:
                findings.append(f"[LAYOUT] {rel}: visual ({x},{y},{w}x{h}) "
                                f"outside canvas {pw}x{ph}")
            rects.append((rel, x, y, w, h))
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                r1, x1, y1, w1, h1 = rects[i]
                r2, x2, y2, w2, h2 = rects[j]
                ox = min(x1 + w1, x2 + w2) - max(x1, x2)
                oy = min(y1 + h1, y2 + h2) - max(y1, y2)
                if ox > OVERLAP_TOLERANCE and oy > OVERLAP_TOLERANCE:
                    findings.append(f"[LAYOUT] {r1.parent.name} overlaps {r2.parent.name} "
                                    f"on page {page.get('displayName', page_dir.name)} "
                                    f"({ox:.0f}x{oy:.0f} px)")


def check_tooltips(pages_dir, parsed, root, findings):
    page_types = {}
    for page_dir in (p for p in pages_dir.iterdir() if p.is_dir()):
        pj = parsed.get(page_dir / "page.json")
        if pj:
            page_types[page_dir.name] = pj.get("type")
    for vjson in pages_dir.glob("*/visuals/*/visual.json"):
        vt = parsed[vjson].get("visual", {}).get("visualContainerObjects", {}).get("visualTooltip")
        if not vt:
            continue
        try:
            section = vt[0]["properties"]["section"]["expr"]["Literal"]["Value"].strip("'")
        except (KeyError, IndexError, AttributeError):
            continue
        rel = vjson.relative_to(root)
        if section not in page_types:
            findings.append(f"[TOOLTIP] {rel}: tooltip page '{section}' does not exist")
        elif page_types[section] != "Tooltip":
            findings.append(f"[TOOLTIP] {rel}: page '{section}' is not of type Tooltip")


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    report_dir, model_dir = find_dirs(root)
    findings = []

    if not report_dir:
        print("LINT SKIP: no *.Report folder found under", root)
        return 0

    definition = report_dir / "definition"

    # 0. encoding: Desktop refuses any project text file carrying a UTF-8 BOM
    #    ("Only text with UTF8 encoding without BOM is supported"), and the
    #    failure mode is a blocking dialog at open. Catch it here instead.
    for base in (report_dir, model_dir):
        if base is None:
            continue
        for f in base.rglob("*"):
            if f.is_file() and (f.suffix in (".json", ".tmdl", ".pbir", ".pbism")
                                or f.name == ".platform"):
                try:
                    if f.read_bytes()[:3] == b"\xef\xbb\xbf":
                        findings.append(f"[ENCODING] {f.relative_to(root)}: UTF-8 BOM present; "
                                        f"Desktop refuses BOM'd files, rewrite as plain UTF-8")
                except OSError:
                    pass

    # 1. JSON parse
    parsed = {}
    for f in definition.rglob("*.json"):
        try:
            parsed[f] = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            findings.append(f"[SYNTAX] {f.relative_to(root)}: {e}")
    if findings:
        _report(findings)
        return 1

    # 2. pages.json coherence
    pages_dir = definition / "pages"
    pages_json = pages_dir / "pages.json"
    if pages_json.exists():
        order = parsed[pages_json].get("pageOrder", [])
        listed = set(order)
        if len(order) != len(listed):
            dupes = sorted({p for p in order if order.count(p) > 1})
            findings.append(f"[PAGES] pageOrder has duplicate entries: {', '.join(dupes)}")
        actual = {p.name for p in pages_dir.iterdir() if p.is_dir()}
        for missing in actual - listed:
            findings.append(f"[PAGES] folder '{missing}' exists but is not in pages.json pageOrder")
        for ghost in listed - actual:
            findings.append(f"[PAGES] pages.json lists '{ghost}' but the folder does not exist")
        active = parsed[pages_json].get("activePageName")
        if active and active not in actual:
            findings.append(f"[PAGES] activePageName '{active}' does not exist")
    for page_dir in (p for p in pages_dir.iterdir() if p.is_dir()):
        pj = parsed.get(page_dir / "page.json")
        if pj and pj.get("type") == "Tooltip" and pj.get("visibility") != "HiddenInViewMode":
            findings.append(f"[PAGES] page '{pj.get('displayName', page_dir.name)}' has type Tooltip "
                            f"but visibility '{pj.get('visibility')}' (expected HiddenInViewMode)")

    # 3. visual name/folder coherence + id uniqueness
    seen_ids = {}
    for vjson in pages_dir.glob("*/visuals/*/visual.json"):
        folder = vjson.parent.name
        name = parsed[vjson].get("name")
        if name != folder:
            findings.append(f"[ID] {vjson.relative_to(root)}: name '{name}' != folder '{folder}'")
        if name in seen_ids:
            findings.append(f"[ID] duplicate visual id '{name}' ({vjson.relative_to(root)} and {seen_ids[name]})")
        seen_ids[name] = vjson.relative_to(root)
        if name and not ID_RE.match(name):
            findings.append(f"[ID] {vjson.relative_to(root)}: id '{name}' not a 20-char alphanumeric id")

    # 4. field reference resolution + kind coherence
    model = parse_tmdl_objects(model_dir) if model_dir else {}
    if model:
        for vjson in pages_dir.glob("*/visuals/*/visual.json"):
            refs = []
            walk_refs(parsed[vjson], refs)
            rel = vjson.relative_to(root)
            for entity, prop, kind, path in refs:
                if entity is None:
                    findings.append(f"[BIND] {rel}: '{prop}' has no resolvable Entity or alias ({path})")
                    continue
                if entity not in model:
                    findings.append(f"[BIND] {rel}: table '{entity}' not in model")
                    continue
                cols, meas = model[entity]["columns"], model[entity]["measures"]
                if prop not in cols | meas:
                    findings.append(f"[BIND] {rel}: '{entity}'[{prop}] ({kind}) not found in model")
                elif kind == "Measure" and prop not in meas:
                    findings.append(f"[KIND] {rel}: '{entity}'[{prop}] bound as Measure but is a column")
                elif kind in ("Column", "HierarchyLevel") and prop not in cols:
                    findings.append(f"[KIND] {rel}: '{entity}'[{prop}] bound as {kind} but is a measure")
        for pjs in pages_dir.glob("*/page.json"):
            refs = []
            walk_refs(parsed[pjs].get("filterConfig", {}), refs)
            rel = pjs.relative_to(root)
            for entity, prop, kind, path in refs:
                if entity is None:
                    findings.append(f"[BIND] {rel}: '{prop}' has no resolvable Entity or alias ({path})")
                elif entity not in model:
                    findings.append(f"[BIND] {rel}: table '{entity}' not in model")
                elif prop not in model[entity]["columns"] | model[entity]["measures"]:
                    findings.append(f"[BIND] {rel}: '{entity}'[{prop}] ({kind}) not found in model")

    # 5. leftover template placeholders
    for f, data in parsed.items():
        if "PLACEHOLDER" in json.dumps(data):
            findings.append(f"[TEMPLATE] {f.relative_to(root)}: leftover 'PLACEHOLDER' from an unbound template slot")

    # 6. layout sanity
    check_layout(pages_dir, parsed, root, findings)

    # 7. tooltip bindings
    check_tooltips(pages_dir, parsed, root, findings)

    # 7b. model hygiene: Desktop's auto date-time scaffolding
    #     (LocalDateTable_*/DateTableTemplate_* + a hidden variation on the
    #     source date column). Verified failure mode (2026-07, Desktop 2.153):
    #     Actualiser throws a FALSE "cyclic reference" on these tables; the
    #     engine refreshes fine via XMLA, only Desktop's UI scheduler trips.
    #     This pipeline's convention is an explicit hand-built dim_date, which
    #     makes the auto tables pure redundant risk. Flag them so Claude
    #     disables auto date-time instead of debugging a phantom cycle later.
    if model_dir:
        auto_date_tables = sorted(t for t in model if t.startswith(("LocalDateTable_", "DateTableTemplate_")))
        if auto_date_tables:
            findings.append(
                f"[MODEL] auto date-time tables present ({', '.join(auto_date_tables)}); "
                f"known to cause a false 'cyclic reference' error on Actualiser in Desktop 2.153. "
                f"Disable auto date-time (model.tmdl annotation __PBI_TimeIntelligenceEnabled = 0, "
                f"remove these table refs from model.tmdl, delete their .tmdl files, and remove the "
                f"variation + relationship on the source date column) in favour of the explicit "
                f"date dimension."
            )

    # 8. design hints (advisory only, never fail the lint): mechanical checks
    #    of the skill's design rules so the agent does not have to re-read them.
    hints = design_hints(pages_dir, parsed)

    if findings:
        _report(findings)
        for h in hints:
            print("  " + h)
        return 1
    print(f"LINT OK: {len(seen_ids)} visuals, {len(model)} model tables checked.")
    for h in hints:
        print("  " + h)
    return 0


NON_DATA_TYPES = {"textbox", "slicer", "advancedSlicerVisual", "actionButton"}


def design_hints(pages_dir, parsed):
    hints = []
    for page_dir in sorted(p for p in pages_dir.iterdir() if p.is_dir()):
        page = parsed.get(page_dir / "page.json")
        if not page or page.get("type") == "Tooltip":
            continue
        data_visuals = 0
        untitled_charts = 0
        for vjson in page_dir.glob("visuals/*/visual.json"):
            vis = parsed[vjson].get("visual", {})
            vtype = vis.get("visualType", "")
            if vtype in NON_DATA_TYPES:
                continue
            data_visuals += 1
            if vtype.endswith("Chart") or vtype in ("pivotTable", "tableEx"):
                title = vis.get("visualContainerObjects", {}).get("title", [{}])[0]
                show = title.get("properties", {}).get("show", {}).get("expr", {}) \
                    .get("Literal", {}).get("Value")
                if show == "false":
                    untitled_charts += 1
        name = page.get("displayName", page_dir.name)
        if data_visuals > 7:
            hints.append(f"[HINT] page '{name}': {data_visuals} data visuals (> 7); "
                         f"the skill says one message per page - consider splitting")
        if untitled_charts:
            hints.append(f"[HINT] page '{name}': {untitled_charts} chart(s) without a message "
                         f"title; charts should state their takeaway")
    return hints


def _report(findings):
    print(f"LINT FAILED: {len(findings)} finding(s)")
    for f in findings:
        print("  " + f)


if __name__ == "__main__":
    sys.exit(main())
