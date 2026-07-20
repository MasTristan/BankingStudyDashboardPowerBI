#!/usr/bin/env python3
"""compile_page.py - Deterministic compiler: page spec (YAML) -> PBIR files.

Design principle: the agent never writes PBIR JSON by hand. It writes a small
spec; this script fills validated templates. Templates are raw visual.json
files extracted from a working report, each paired with a <type>.map.yaml
sidecar that declares WHERE in the JSON each slot lives (dotted paths).

Spec schema (specs/<page>.yaml):
---
page: Credit Risk Overview          # displayName
page_id: creditrisk0overview        # optional fixed id (else derived from name)
page_type: Tooltip                  # optional (tooltip pages)
visibility: HiddenInViewMode        # optional (tooltip pages)
canvas: {width: 1280, height: 720}
grid: {cols: 12, row_px: 60, margin: 16, gutter: 8}
page_filters:                       # optional page-level filters (same schema
  - field: {table: DIM_DATE, name: YEAR}     # as visual `filters`, below)
    type: In
    values: [2024, 2025]
visuals:
  - type: barChart
    slots:
      axis: {table: Exposure, name: Segment}
      measure:                      # a slot accepts one binding or a list
        - {table: _Measures, name: Total EAD}
        - {table: _Measures, name: EAD Limit}
    pos: [0, 2, 6, 6]               # col, row, colspan, rowspan
    title: "EAD by segment"
    sort: {slot: measure, direction: desc}   # or field: {table,name,kind}
    filters:
      - field: {table: Exposure, name: Segment}
        type: TopN                  # TopN | In | NotIn | NotBlank
        count: 10
        order_by: {table: _Measures, name: Total EAD}
    format:                         # keys must exist in <type>.map.yaml `format:`
      dataLabels: true
      legendPosition: Top
    tooltip_page: ttdetail000000001 # PBIR page `name` of a Tooltip page

Map sidecar (templates/<type>.map.yaml):
---
slots:
  measure:
    - path: visual.query.queryState.Y.projections[0].field
      kind: Measure                 # Measure | Column
title:
  path: visual.visualContainerObjects.title[0].properties.text.expr.Literal.Value
format:
  dataLabels: {path: visual.objects.labels[0].properties.show, kind: bool}
  legendPosition: {path: visual.objects.legend[0].properties.position, kind: string}

Format kinds: bool -> Literal true/false, string -> Literal 'x',
double -> Literal xD, long -> Literal xL, color -> solid color structure.

"title" targets a DAX Literal.Value expression and gets quote-wrapped. Some
visuals (textbox, caption) instead hold a plain JSON string, declared as "text".

Recompilation is idempotent: an existing page folder with the same page_id is
wiped and rebuilt, and pages.json keeps its position in pageOrder. Visual ids
are deterministic (hash of page_id + index + type) so recompiles diff cleanly.

Usage:
    python scripts/compile_page.py specs/<page>.yaml [repo_root]
    python scripts/compile_page.py --all [repo_root]        # every specs/*.yaml
    python scripts/compile_page.py --remove <page_id> [repo_root]
"""

import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

import yaml  # pip install pyyaml

TEMPLATES = Path(__file__).parent.parent / ".claude" / "skills" / "pbi-report" / "templates"

DIRECTIONS = {"asc": "Ascending", "desc": "Descending"}


def det_id(*parts):
    """Deterministic 20-char id from arbitrary string parts."""
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:20]


def re_tokens(dotted):
    out = []
    for part in dotted.split("."):
        while "[" in part:
            head, rest = part.split("[", 1)
            idx, part = rest.split("]", 1)
            if head:
                out.append(head)
            out.append(int(idx))
            part = part.lstrip(".")
            if not part:
                break
        if part:
            out.append(part)
    return out


def set_path(obj, dotted, value):
    """Set a value at a dotted path with [i] list indices, e.g. a.b[0].c"""
    tokens = re_tokens(dotted)
    cur = obj
    for tok in tokens[:-1]:
        cur = cur[tok]
    cur[tokens[-1]] = value


def ensure_path(obj, tokens):
    """Walk tokens, creating missing dicts / extending lists, return the parent
    container of the final token."""
    cur = obj
    for i, tok in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        if isinstance(tok, int):
            while len(cur) <= tok:
                cur.append([] if isinstance(nxt, int) else {})
            if not cur[tok]:
                cur[tok] = [] if isinstance(nxt, int) else cur[tok] or {}
            cur = cur[tok]
        else:
            if tok not in cur or cur[tok] is None:
                cur[tok] = [] if isinstance(nxt, int) else {}
            cur = cur[tok]
    return cur


def set_path_create(obj, dotted, value):
    tokens = re_tokens(dotted)
    parent = ensure_path(obj, tokens)
    last = tokens[-1]
    if isinstance(last, int):
        while len(parent) <= last:
            parent.append({})
        parent[last] = value
    else:
        parent[last] = value


def field_expr(kind, table, name, source_alias=None):
    """Canonical PBIR expression for a Column or Measure. With source_alias the
    SourceRef points at a query alias (filter From entries) instead of Entity."""
    src = {"Source": source_alias} if source_alias else {"Entity": table}
    return {kind: {"Expression": {"SourceRef": src}, "Property": name}}


def literal(value, kind=None):
    """PBIR Literal encoding. Infers kind from the python type when omitted."""
    if kind == "bool" or isinstance(value, bool):
        return "true" if value else "false"
    if kind == "long" or (kind is None and isinstance(value, int)):
        return f"{value}L"
    if kind == "double" or (kind is None and isinstance(value, float)):
        return f"{value}D"
    return "'" + str(value).replace("'", "''") + "'"


def expr_literal(value, kind=None):
    return {"expr": {"Literal": {"Value": literal(value, kind)}}}


def grid_to_px(pos, grid, canvas):
    col, row, cspan, rspan = pos
    margin, gutter, cols, row_px = grid["margin"], grid["gutter"], grid["cols"], grid["row_px"]
    usable = canvas["width"] - 2 * margin - (cols - 1) * gutter
    colw = usable / cols
    x = margin + col * (colw + gutter)
    y = margin + row * (row_px + gutter)
    w = cspan * colw + (cspan - 1) * gutter
    h = rspan * row_px + (rspan - 1) * gutter
    return round(x, 2), round(y, 2), round(w, 2), round(h, 2)


def as_binding_list(binding):
    """A slot value is one binding dict or a list of them."""
    return binding if isinstance(binding, list) else [binding]


def bind_projection(visual, slot_path, kind, binding, proj_index):
    """Bind projection number proj_index of the projections list addressed by
    slot_path (which addresses [0]). Clones projection 0 for higher indices.
    Missing containers (e.g. an optional Series bucket absent from the
    template) are created on the way down."""
    kind = binding.get("kind", kind)  # per-binding override (e.g. tableEx mixing columns and measures)
    tokens = re_tokens(slot_path)
    # locate the projections list: tokens = [..., 'projections', 0, 'field']
    try:
        zero_at = len(tokens) - 1 - tokens[::-1].index(0)
    except ValueError:
        # no list index in the path: only single binding supported
        if proj_index > 0:
            sys.exit(f"COMPILE FAIL: slot path '{slot_path}' does not support multiple bindings")
        set_path_create(visual, slot_path, field_expr(kind, binding["table"], binding["name"]))
        return
    projections = ensure_path(visual, tokens[:zero_at] + [0])
    while len(projections) <= proj_index:
        projections.append(copy.deepcopy(projections[0]) if projections else {"field": None})
    proj = projections[proj_index]
    tail = tokens[zero_at + 1:]  # e.g. ['field']
    cur = proj
    for tok in tail[:-1]:
        cur = cur.setdefault(tok, {})
    cur[tail[-1]] = field_expr(kind, binding["table"], binding["name"])
    if tail and tail[-1] == "field":
        proj["queryRef"] = f"{binding['table']}.{binding['name']}"
        proj["nativeQueryRef"] = binding["name"]


def prune_placeholder_projections(visual):
    """Drop queryState roles the spec left unbound: templates ship a
    PLACEHOLDER projection per optional role (e.g. matrix Columns) and an
    unbound one must not survive into the compiled visual."""
    query_state = visual.get("visual", {}).get("query", {}).get("queryState")
    if not isinstance(query_state, dict):
        return
    for role in list(query_state):
        projections = query_state[role].get("projections")
        if not isinstance(projections, list):
            continue
        projections[:] = [p for p in projections if "PLACEHOLDER" not in json.dumps(p)]
        if not projections:
            del query_state[role]


def build_filter(fspec, name_seed):
    """Build one PBIR filterConfig entry from a spec filter."""
    table, col = fspec["field"]["table"], fspec["field"]["name"]
    fkind = fspec["field"].get("kind", "Column")
    ftype = fspec["type"]
    alias = "t0"
    from_clause = [{"Name": alias, "Entity": table, "Type": 0}]
    col_expr_aliased = field_expr(fkind, table, col, source_alias=alias)

    if ftype in ("In", "NotIn"):
        values = fspec.get("values")
        if not values:
            sys.exit(f"COMPILE FAIL: filter type {ftype} on {table}.{col} needs `values`")
        cond = {
            "In": {
                "Expressions": [col_expr_aliased],
                "Values": [[{"Literal": {"Value": literal(v)}}] for v in values],
            }
        }
        if ftype == "NotIn":
            cond = {"Not": {"Expression": cond}}
        pbi_type = "Categorical"
    elif ftype == "TopN":
        # semanticQuery 1.4 shape: an In condition whose Table is a Subquery
        # with OrderBy (the ranking measure) and Top (the count). This is how
        # Desktop serializes a filter-card Top N; a bare "TopN" condition is
        # rejected by the PBIR schema and VisualTopN carries no ordering.
        count = fspec.get("count", 10)
        ob = fspec.get("order_by")
        if not ob:
            sys.exit(f"COMPILE FAIL: TopN filter on {table}.{col} needs `order_by` (a measure)")
        sub_from = [{"Name": "s0", "Entity": table, "Type": 0}]
        ob_alias = "s0"
        if ob["table"] != table:
            ob_alias = "s1"
            sub_from.append({"Name": ob_alias, "Entity": ob["table"], "Type": 0})
        direction = 2 if fspec.get("direction", "desc") == "desc" else 1
        sub_query = {
            "Version": 2,
            "From": sub_from,
            "Select": [
                dict(field_expr(fkind, table, col, source_alias="s0"),
                     Name=f"{table}.{col}")
            ],
            "OrderBy": [
                {
                    "Direction": direction,
                    "Expression": field_expr(
                        ob.get("kind", "Measure"), ob["table"], ob["name"],
                        source_alias=ob_alias),
                }
            ],
            "Top": count,
        }
        # In.Table must be a SourceRef, so the subquery goes into the From
        # clause as an expression-produced table (Type 2).
        from_clause.append({"Name": "q0", "Type": 2,
                            "Expression": {"Subquery": {"Query": sub_query}}})
        cond = {
            "In": {
                "Expressions": [col_expr_aliased],
                "Table": {"SourceRef": {"Source": "q0"}},
            }
        }
        pbi_type = "TopN"
    elif ftype == "NotBlank":
        cond = {
            "Not": {
                "Expression": {
                    "Comparison": {
                        "ComparisonKind": 0,
                        "Left": col_expr_aliased,
                        "Right": {"Literal": {"Value": "null"}},
                    }
                }
            }
        }
        pbi_type = "Advanced"
    else:
        sys.exit(f"COMPILE FAIL: unknown filter type '{ftype}' (use In, NotIn, TopN, NotBlank)")

    return {
        "name": det_id("filter", name_seed, table, col, ftype),
        "field": field_expr(fkind, table, col),
        "type": pbi_type,
        "filter": {"Version": 2, "From": from_clause, "Where": [{"Condition": cond}]},
    }


COMPARISON_KINDS = {"=": 0, ">": 1, ">=": 2, "<": 3, "<=": 4}
CONDITION_RE = None  # compiled lazily below


def parse_rule_condition(text):
    """Parse 'op value' (e.g. '< 0', '>= 0.6') into (ComparisonKind, literal)."""
    global CONDITION_RE
    if CONDITION_RE is None:
        import re
        CONDITION_RE = re.compile(r"^(<=|>=|<|>|=)\s*(-?\d+(?:\.\d+)?)$")
    m = CONDITION_RE.match(str(text).strip())
    if not m:
        sys.exit(f"COMPILE FAIL: conditional_color rule '{text}' must be '<op> <number>' "
                 f"with op in {sorted(COMPARISON_KINDS)}")
    op, num = m.groups()
    # double literal, formatted the way Desktop writes it: '0D', '0.8D'
    # (never '0.0D')
    val = float(num)
    body = str(int(val)) if val == int(val) else str(val)
    return COMPARISON_KINDS[op], f"{body}D"


def apply_conditional_color(visual, vmap, spec, vtype):
    """Rules-based color (Desktop's 'fx' conditional formatting): a Conditional
    expression over a measure, written at the template's conditionalColor path."""
    cc_def = vmap.get("conditionalColor")
    if not cc_def:
        sys.exit(f"COMPILE FAIL: template '{vtype}' has no conditionalColor path in its map.yaml")
    by = spec.get("by")
    if not by:
        sys.exit("COMPILE FAIL: conditional_color needs `by: {table, name}` (the driving measure)")
    by_expr = field_expr(by.get("kind", "Measure"), by["table"], by["name"])
    cases = []
    for rule in spec.get("rules", []):
        kind, right = parse_rule_condition(rule["if"])
        cases.append({
            "Condition": {"Comparison": {"ComparisonKind": kind, "Left": by_expr,
                                         "Right": {"Literal": {"Value": right}}}},
            "Value": {"Literal": {"Value": literal(str(rule["color"]))}},
        })
    if not cases:
        sys.exit("COMPILE FAIL: conditional_color needs at least one rule")
    conditional = {"Cases": cases}
    if "default" in spec:
        conditional["DefaultValue"] = {"Literal": {"Value": literal(str(spec["default"]))}}
    node = {"solid": {"color": {"expr": {"Conditional": conditional}}}}
    set_path_create(visual, cc_def["path"], node)
    # Some objects require a selector on the entry holding the expression
    # (tables/matrices need dataViewWildcard, card value objects need the
    # 'default' id); the template map declares which.
    sel = cc_def.get("selector")
    if sel:
        tokens = re_tokens(cc_def["path"])
        props_at = len(tokens) - 1 - tokens[::-1].index("properties")
        entry = visual
        for tok in tokens[:props_at]:
            entry = entry[tok]
        if sel == "wildcard":
            entry["selector"] = {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}
        elif sel == "default":
            entry["selector"] = {"id": "default"}


def apply_format(visual, vmap, fmt, vtype):
    declared = vmap.get("format", {})
    for key, value in fmt.items():
        fdef = declared.get(key)
        if not fdef:
            known = ", ".join(sorted(declared)) or "(none)"
            sys.exit(f"COMPILE FAIL: template '{vtype}' has no format key '{key}' (known: {known})")
        kind = fdef.get("kind", "string")
        if kind == "color":
            node = {"solid": {"color": {"expr": {"Literal": {"Value": literal(str(value))}}}}}
        else:
            node = expr_literal(value, kind)
        set_path_create(visual, fdef["path"], node)


def resolve_sort_field(sort_spec, v, vmap, vtype):
    if "field" in sort_spec:
        f = sort_spec["field"]
        return field_expr(f.get("kind", "Column"), f["table"], f["name"])
    slot_name = sort_spec.get("slot")
    if not slot_name:
        sys.exit("COMPILE FAIL: sort needs `slot: <slot name>` or `field: {table,name,kind}`")
    slot_defs = vmap["slots"].get(slot_name)
    bindings = v.get("slots", {}).get(slot_name)
    if not slot_defs or not bindings:
        sys.exit(f"COMPILE FAIL: sort slot '{slot_name}' is not bound on this '{vtype}' visual")
    b = as_binding_list(bindings)[0]
    return field_expr(slot_defs[0]["kind"], b["table"], b["name"])


def compile_visual(v, i, page_id, grid, canvas):
    vtype = v["type"]
    tpl_file = TEMPLATES / f"{vtype}.visual.json"
    map_file = TEMPLATES / f"{vtype}.map.yaml"
    if not tpl_file.exists() or not map_file.exists():
        sys.exit(f"COMPILE FAIL: missing template or map for visual type '{vtype}'. "
                 f"Extract one from a working report into {TEMPLATES}/")
    visual = json.loads(tpl_file.read_text(encoding="utf-8"))
    vmap = yaml.safe_load(map_file.read_text(encoding="utf-8"))

    vid = det_id(page_id, i, vtype)
    visual["name"] = vid
    x, y, w, h = grid_to_px(v["pos"], grid, canvas)
    visual["position"] = {"x": x, "y": y, "z": i, "width": w, "height": h}

    for slot_name, binding in v.get("slots", {}).items():
        slot_defs = vmap["slots"].get(slot_name) if vmap.get("slots") else None
        if not slot_defs:
            sys.exit(f"COMPILE FAIL: template '{vtype}' has no slot '{slot_name}'")
        bindings = as_binding_list(binding)
        for slot in slot_defs:
            for k, b in enumerate(bindings):
                bind_projection(visual, slot["path"], slot["kind"], b, k)

    prune_placeholder_projections(visual)

    if "title" in vmap:
        if "title" in v:
            set_path(visual, vmap["title"]["path"], f"'{v['title']}'")
        else:
            # No title in the spec: hide the container title instead of leaving
            # the template placeholder (or Desktop's auto 'Sum of X' text).
            tokens = re_tokens(vmap["title"]["path"])
            props_at = len(tokens) - 1 - tokens[::-1].index("properties")
            parent = visual
            for tok in tokens[:props_at]:
                parent = parent[tok]
            parent["properties"] = {"show": {"expr": {"Literal": {"Value": "false"}}}}

    if "text" in v and "text" in vmap:
        # Plain JSON string property (e.g. textbox textRuns[].value), unlike
        # "title" which targets a DAX Literal.Value expression and needs quoting.
        set_path(visual, vmap["text"]["path"], v["text"])

    sort_spec = v.get("sort")
    if sort_spec is None:
        # A TopN filter's ordering lives in the visual sort (VisualTopN carries
        # only ItemCount), so synthesize one from the filter's order_by.
        for f in v.get("filters", []):
            if f.get("type") == "TopN":
                ob = f["order_by"]
                sort_spec = {"field": {"kind": ob.get("kind", "Measure"), **ob},
                             "direction": f.get("direction", "desc")}
                break
    if sort_spec is not None:
        direction = DIRECTIONS.get(sort_spec.get("direction", "desc"))
        if direction is None:
            sys.exit("COMPILE FAIL: sort direction must be 'asc' or 'desc'")
        visual["visual"]["query"]["sortDefinition"] = {
            "sort": [{"field": resolve_sort_field(sort_spec, v, vmap, vtype), "direction": direction}],
            "isDefaultSort": True,
        }

    if "filters" in v:
        visual["filterConfig"] = {
            "filters": [build_filter(f, f"{vid}{j}") for j, f in enumerate(v["filters"])]
        }

    if "format" in v:
        apply_format(visual, vmap, v["format"], vtype)

    if "conditional_color" in v:
        apply_conditional_color(visual, vmap, v["conditional_color"], vtype)

    if "tooltip_page" in v:
        # Cross-cutting: any visual type can show a custom report-page tooltip.
        # "tooltip_page" is the target tooltip page's internal `name` (not its
        # displayName), matching PBIR's visualTooltip.section reference.
        visual["visual"].setdefault("visualContainerObjects", {})["visualTooltip"] = [
            {
                "properties": {
                    "show": {"expr": {"Literal": {"Value": "true"}}},
                    "type": {"expr": {"Literal": {"Value": "'ReportPage'"}}},
                    "section": {"expr": {"Literal": {"Value": f"'{v['tooltip_page']}'"}}},
                }
            }
        ]
    return vid, visual


def compile_page(spec_path: Path, root: Path):
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    report_dir = next(root.glob("*.Report"))
    pages_dir = report_dir / "definition" / "pages"

    canvas = spec.get("canvas", {"width": 1280, "height": 720})
    grid = spec.get("grid", {"cols": 12, "row_px": 60, "margin": 16, "gutter": 8})

    # A fixed page_id lets other specs cross-reference this page (e.g. a
    # tooltip_page binding) before it exists; without one the id is derived
    # from the display name, so recompiling the same spec hits the same folder.
    page_id = spec.get("page_id") or det_id("page", spec["page"])
    page_dir = pages_dir / page_id
    if page_dir.exists():
        shutil.rmtree(page_dir)  # idempotent recompile: wipe and rebuild
    (page_dir / "visuals").mkdir(parents=True)

    # page.json from template
    page_tpl = json.loads((TEMPLATES / "page.template.json").read_text(encoding="utf-8"))
    page_tpl["name"] = page_id
    page_tpl["displayName"] = spec["page"]
    page_tpl["width"] = canvas["width"]
    page_tpl["height"] = canvas["height"]
    if "page_type" in spec:
        # e.g. "Tooltip" for a custom report-page tooltip; paired with visibility below.
        page_tpl["type"] = spec["page_type"]
    if "visibility" in spec:
        # e.g. "HiddenInViewMode" to keep a tooltip page out of the page-tab strip.
        page_tpl["visibility"] = spec["visibility"]
    if "page_filters" in spec:
        page_tpl["filterConfig"] = {
            "filters": [build_filter(f, f"{page_id}{j}") for j, f in enumerate(spec["page_filters"])]
        }
    (page_dir / "page.json").write_text(json.dumps(page_tpl, indent=2), encoding="utf-8")

    for i, v in enumerate(spec["visuals"]):
        vid, visual = compile_visual(v, i, page_id, grid, canvas)
        vdir = page_dir / "visuals" / vid
        vdir.mkdir()
        (vdir / "visual.json").write_text(json.dumps(visual, indent=2), encoding="utf-8")
        print(f"  visual {v['type']} -> {vid} at {v['pos']}")

    # register in pages.json (keep position on recompile)
    pages_json = pages_dir / "pages.json"
    pj = json.loads(pages_json.read_text(encoding="utf-8"))
    order = pj.setdefault("pageOrder", [])
    if page_id not in order:
        order.append(page_id)
    pj["activePageName"] = page_id
    pages_json.write_text(json.dumps(pj, indent=2), encoding="utf-8")

    print(f"COMPILED page '{spec['page']}' -> {page_id} ({len(spec['visuals'])} visuals)")


def remove_page(page_id: str, root: Path):
    """Delete a compiled page and deregister it from pages.json."""
    report_dir = next(root.glob("*.Report"))
    pages_dir = report_dir / "definition" / "pages"
    page_dir = pages_dir / page_id
    if page_dir.exists():
        shutil.rmtree(page_dir)
    pages_json = pages_dir / "pages.json"
    pj = json.loads(pages_json.read_text(encoding="utf-8"))
    pj["pageOrder"] = [p for p in pj.get("pageOrder", []) if p != page_id]
    if pj.get("activePageName") == page_id:
        if pj["pageOrder"]:
            pj["activePageName"] = pj["pageOrder"][0]
        else:
            pj.pop("activePageName", None)
    pages_json.write_text(json.dumps(pj, indent=2), encoding="utf-8")
    print(f"REMOVED page {page_id}")


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        sys.exit(__doc__)
    if args[0] == "--remove":
        root = Path(args[2]) if len(args) > 2 else Path.cwd()
        remove_page(args[1], root)
        return
    if args[0] == "--all":
        root = Path(args[1]) if len(args) > 1 else Path.cwd()
        for spec in sorted((root / "specs").glob("*.yaml")):
            print(f"== {spec.name}")
            compile_page(spec, root)
    else:
        spec = Path(args[0])
        root = Path(args[1]) if len(args) > 1 else Path.cwd()
        compile_page(spec, root)


if __name__ == "__main__":
    main()
