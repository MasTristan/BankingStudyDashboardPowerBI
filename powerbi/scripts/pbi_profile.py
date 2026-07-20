#!/usr/bin/env python3
"""pbi_profile.py - Data profiling against Power BI Desktop's local model.

Power BI Desktop hosts the semantic model in a local Analysis Services
instance (msmdsrv.exe) listening on a random port. This script finds that
port, connects with ADOMD.NET (the client DLL that ships inside Desktop
itself, no license or extra install), and runs DAX. It gives the agent the
PROFILE step of the pipeline: cardinalities, ranges, distributions, measure
totals, without any Power BI Service or MCP dependency.

Requires Desktop to be RUNNING with the .pbip open. If it is not, pass
--launch <file.pbip> and the script starts Desktop and waits for the model
itself (reusing pbi_render's launch machinery, trust dialog included).

Install once:  pip install pythonnet

Usage:
    python scripts/pbi_profile.py ports
    python scripts/pbi_profile.py tables --launch Dashboard.pbip
    python scripts/pbi_profile.py tables
    python scripts/pbi_profile.py columns FCT_SALES
    python scripts/pbi_profile.py cardinality DIM_CUSTOMER REGION_NAME
    python scripts/pbi_profile.py stats FCT_SALES gross_margin
    python scripts/pbi_profile.py measure "Gross Margin %"
    python scripts/pbi_profile.py topn DIM_PRODUCTS BRAND --by "Total Gross Margin" -n 10
    python scripts/pbi_profile.py dax "EVALUATE TOPN(5, FCT_SALES)"

All commands accept --port to target a specific instance when several
Desktop windows are open (default: the most recently started one).
"""

import argparse
import sys
from pathlib import Path

ADOMD_CANDIDATES = [
    # Desktop ships the client under the PowerBI-branded assembly name.
    Path(r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.PowerBI.AdomdClient.dll"),
    Path(r"C:\Program Files\Microsoft Power BI Desktop\bin\Microsoft.AnalysisServices.AdomdClient.dll"),
]


def find_ports():
    """Every live Desktop workspace writes Data/msmdsrv.port.txt (UTF-16)."""
    base = Path.home() / "AppData" / "Local" / "Microsoft" / "Power BI Desktop" / "AnalysisServicesWorkspaces"
    ports = []
    if not base.exists():
        return ports
    for f in base.glob("*/Data/msmdsrv.port.txt"):
        try:
            raw = f.read_bytes()
            # Desktop writes this file in UTF-16-LE, usually WITHOUT a BOM.
            enc = "utf-16-le" if (raw[:2] == b"\xff\xfe" or b"\x00" in raw) else "utf-8"
            port = int(raw.decode(enc).replace("﻿", "").strip())
            ports.append((f.stat().st_mtime, port))
        except (ValueError, OSError):
            continue
    return [p for _, p in sorted(ports, reverse=True)]


def load_adomd():
    try:
        import clr  # pythonnet
    except ImportError:
        sys.exit("PROFILE FAIL: pythonnet not installed. Run: pip install pythonnet")
    dll = next((c for c in ADOMD_CANDIDATES if c.exists()), None)
    if dll is None:
        sys.exit("PROFILE FAIL: AdomdClient dll not found. Check the Power BI Desktop install path "
                 "in pbi_profile.py (ADOMD_CANDIDATES).")
    clr.AddReference(str(dll))
    # Namespace differs between the branded and the classic assembly.
    try:
        from Microsoft.PowerBI.AdomdClient import AdomdConnection
    except ImportError:
        from Microsoft.AnalysisServices.AdomdClient import AdomdConnection
    return AdomdConnection


def connect(port):
    AdomdConnection = load_adomd()
    conn = AdomdConnection(f"Data Source=localhost:{port}")
    conn.Open()
    return conn


def run_query(conn, query):
    """Execute DAX or DMV text, return (columns, rows) of stringified cells."""
    cmd = conn.CreateCommand()
    cmd.CommandText = query
    reader = cmd.ExecuteReader()
    cols = [reader.GetName(i) for i in range(reader.FieldCount)]
    rows = []
    while reader.Read():
        rows.append([_cell(reader, i) for i in range(reader.FieldCount)])
    reader.Close()
    return cols, rows


def _cell(reader, i):
    v = reader.GetValue(i)
    return "" if v is None else str(v)


def print_table(cols, rows, limit=200):
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print(" | ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("-+-".join("-" * w for w in widths))
    for r in rows[:limit]:
        print(" | ".join(v.ljust(w) for v, w in zip(r, widths)))
    if len(rows) > limit:
        print(f"... ({len(rows) - limit} more rows)")


def q(name):
    """Quote a table name for DAX."""
    return f"'{name}'"


def launch_desktop(pbip: Path):
    """Start Desktop on the .pbip and block until the model is queryable.
    Reuses pbi_render's launch machinery (window discovery, trust dialog,
    load detection) so the two scripts cannot drift apart."""
    import subprocess

    import pbi_render as r

    subprocess.Popen([r.PBI_EXE, str(pbip.resolve())])
    print("Launching Desktop, waiting for the report to open...")
    win = r.find_desktop_window(timeout=90)
    if win is None:
        sys.exit("PROFILE FAIL: could not find PBIDesktop window via UIA")
    if r.wait_for_report_open(pbip.resolve()) is None:
        sys.exit("PROFILE FAIL: report never opened")
    if not r.wait_for_model_loaded():
        sys.exit("PROFILE FAIL: model did not load within timeout")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["ports", "tables", "columns", "cardinality",
                                        "stats", "measure", "topn", "dax"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--by", default=None, help="measure name for topn ordering")
    ap.add_argument("-n", type=int, default=10, help="row count for topn")
    ap.add_argument("--launch", type=Path, default=None, metavar="PBIP",
                    help="if no Desktop instance is running, launch this .pbip and wait")
    a = ap.parse_args()

    ports = find_ports()
    if not ports and a.launch is not None and a.command != "ports":
        launch_desktop(a.launch)
        ports = find_ports()
    if a.command == "ports":
        if not ports:
            print("No running Desktop instance found (no msmdsrv.port.txt). Open the .pbip first.")
            return
        for p in ports:
            print(p)
        return

    port = a.port or (ports[0] if ports else None)
    if port is None:
        sys.exit("PROFILE FAIL: no running Power BI Desktop instance found. Open the .pbip first "
                 "(or pass --port).")
    conn = connect(port)

    if a.command == "tables":
        cols, rows = run_query(conn, "SELECT [Name], [IsHidden] FROM $SYSTEM.TMSCHEMA_TABLES")
    elif a.command == "columns":
        table = a.args[0]
        cols, rows = run_query(
            conn,
            "SELECT [TableID], [ExplicitName], [ExplicitDataType] FROM $SYSTEM.TMSCHEMA_COLUMNS",
        )
        tcols, trows = run_query(conn, "SELECT [ID], [Name] FROM $SYSTEM.TMSCHEMA_TABLES")
        tmap = {r[0]: r[1] for r in trows}
        rows = [[tmap.get(r[0], r[0]), r[1], r[2]] for r in rows if tmap.get(r[0]) == table]
        cols = ["Table", "Column", "DataType"]
    elif a.command == "cardinality":
        table, col = a.args[0], a.args[1]
        cols, rows = run_query(
            conn, f"EVALUATE ROW(\"cardinality\", COUNTROWS(VALUES({q(table)}[{col}])))")
    elif a.command == "stats":
        # One query per statistic: on a DirectQuery table a combined ROW() can
        # blow the external rowset limit; this way a failing stat degrades to
        # 'ERR' instead of killing the whole profile.
        table, col = a.args[0], a.args[1]
        parts = {
            "min": f"MIN({q(table)}[{col}])",
            "max": f"MAX({q(table)}[{col}])",
            "blanks": f"COUNTBLANK({q(table)}[{col}])",
            "rows": f"COUNTROWS({q(table)})",
        }
        cols, vals = [], []
        for name, expr in parts.items():
            cols.append(name)
            try:
                _, r = run_query(conn, f"EVALUATE ROW(\"v\", {expr})")
                vals.append(r[0][0] if r else "")
            except Exception as e:
                vals.append(f"ERR ({type(e).__name__})")
        rows = [vals]
    elif a.command == "measure":
        name = a.args[0]
        cols, rows = run_query(conn, f"EVALUATE ROW(\"value\", [{name}])")
    elif a.command == "topn":
        table, col = a.args[0], a.args[1]
        order = f"[{a.by}]" if a.by else f"COUNTROWS({q(table)})"
        cols, rows = run_query(
            conn,
            f"EVALUATE TOPN({a.n}, "
            f"SUMMARIZECOLUMNS({q(table)}[{col}], \"__v\", {order}), [__v], DESC)",
        )
    elif a.command == "dax":
        cols, rows = run_query(conn, a.args[0])
    else:
        sys.exit(f"unknown command {a.command}")

    print_table(cols, rows)
    conn.Close()


if __name__ == "__main__":
    main()
