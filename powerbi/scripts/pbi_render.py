#!/usr/bin/env python3
"""pbi_render.py - Give Claude eyes on Power BI Desktop, without a Service license.

Closes Desktop, reopens the .pbip, waits for the model to load, screenshots
every report page into .render/<pageDisplayName>.png. Claude Code then reads
the PNGs (multimodal) and critiques the layout.

Requirements (install once):
    pip install pywinauto mss psutil pillow

Usage:
    python scripts/pbi_render.py MyProject.pbip [--pages "Name1,Name2"] [--settle N]

    --pages   capture only these page displayNames (comma-separated); default all.
              Cuts the critique loop from minutes to ~1 min when iterating on
              a single page.
    --settle  seconds to wait after switching to a page before capturing
              (default 40; 15 is plenty for a small import-mode model).

Hidden pages (visibility HiddenInViewMode, i.e. tooltip pages) have no tab in
the page navigator and are skipped; to inspect one, temporarily compile it
without `visibility` in its spec.

Notes for on-machine tuning (first run WILL need adjustment, do it with Claude
Code interactively on the Windows box):
  - PBI_EXE path below may differ (Store install vs standalone installer).
  - Page tab discovery uses UIA; if descendants() finds nothing, dump the
    control tree with print_control_identifiers() and adjust the selector.
  - Set Windows display scaling to 100% for pixel-stable captures, or leave
    it and accept scaled captures (fine for critique purposes).
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil
from pywinauto import Application

PBI_EXE = r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe"
LOAD_TIMEOUT = 180  # seconds
DEFAULT_SETTLE = 40  # seconds to let DirectQuery visuals render after page switch


def harden_pbip(pbip: Path):
    """Desktop strips enableAutoRecovery=false from the .pbip every time it
    opens the project, and auto recovery restores stale PBIR files after this
    script's force-kill. Re-apply the setting before every launch."""
    data = json.loads(pbip.read_text(encoding="utf-8"))
    if data.setdefault("settings", {}).get("enableAutoRecovery") is not False:
        data["settings"]["enableAutoRecovery"] = False
        pbip.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("  hardened .pbip: enableAutoRecovery = false")


def kill_desktop():
    for p in psutil.process_iter(["name"]):
        if p.info["name"] and p.info["name"].lower().startswith("pbidesktop"):
            p.kill()
    time.sleep(3)


def find_desktop_window(timeout=60):
    """PBI Desktop's window title is the file name (e.g. 'Dashboard'), not a
    fixed 'Power BI Desktop' suffix, so match by process instead of title."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid = next(
            (p.info["pid"] for p in psutil.process_iter(["pid", "name"])
             if p.info["name"] and p.info["name"].lower().startswith("pbidesktop")),
            None,
        )
        if pid is not None:
            try:
                return Application(backend="uia").connect(process=pid).top_window()
            except Exception:
                pass
        time.sleep(2)
    return None


def dismiss_security_dialog(win, timeout=20):
    """First load of a .pbip with multiple data sources shows a 'Potential
    security risk' trust dialog that blocks the report from rendering. It may
    not reappear once the file is trusted, so this is a short best-effort poll.

    Use the UIA Invoke pattern, NOT click_input: a physical click lands at
    screen coordinates, so if Desktop is behind another window it hits that
    window instead (observed: the OK landed in the foreground app and the
    dialog stayed open forever)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ok_btns = win.descendants(title="OK", control_type="Button")
        except Exception:
            ok_btns = []
        if ok_btns:
            try:
                ok_btns[0].invoke()
            except Exception:
                try:
                    win.set_focus()
                    time.sleep(0.5)
                    ok_btns[0].click_input()
                except Exception:
                    time.sleep(1)
                    continue
            time.sleep(2)
            return True
        time.sleep(1)
    return False


def wait_for_report_open(pbip, timeout=LOAD_TIMEOUT):
    """Desktop keeps the title 'Sans titre'/'Untitled' until the report is
    actually open; the reliable 'file is open' signal is the file stem showing
    up in the window title. The trust dialog can pop at any point during the
    load (its timing varies with data source count), so each poll iteration
    also tries to dismiss it."""
    stem = pbip.stem.lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        win = find_desktop_window(timeout=10)
        if win is not None:
            try:
                if stem in win.window_text().lower():
                    return win
            except Exception:
                pass
            if dismiss_security_dialog(win, timeout=1):
                print("  dismissed 'potential security risk' trust dialog")
        time.sleep(2)
    return None


def wait_for_model_loaded():
    """Model is loaded once the local Analysis Services child (msmdsrv) is up
    and CPU settles. Coarse but reliable enough for a render loop."""
    deadline = time.time() + LOAD_TIMEOUT
    while time.time() < deadline:
        as_up = any(
            p.info["name"] and p.info["name"].lower().startswith("msmdsrv")
            for p in psutil.process_iter(["name"])
        )
        if as_up:
            time.sleep(15)  # visuals still rendering after AS is up
            return True
        time.sleep(2)
    return False


def report_pages(pbip: Path):
    """Ordered list of (page_id, displayName) read from the PBIR definition,
    used to pick the real page-navigator tabs out of the UIA tree (which also
    exposes ribbon tabs, panel tabs, etc. as TabItem controls). Hidden pages
    (tooltip pages) have no navigator tab and are skipped."""
    report_dir = next(pbip.parent.glob("*.Report"))
    pages_dir = report_dir / "definition" / "pages"
    page_order = json.loads((pages_dir / "pages.json").read_text(encoding="utf-8"))["pageOrder"]
    pages = []
    for page_id in page_order:
        page_json = json.loads((pages_dir / page_id / "page.json").read_text(encoding="utf-8"))
        if page_json.get("visibility") == "HiddenInViewMode":
            print(f"  skip hidden page: {page_json['displayName']} ({page_id})")
            continue
        pages.append((page_id, page_json["displayName"]))
    return pages


def declutter_ui(win):
    """Fresh Desktop opens in editing view with the Filters/Visualisations/Data
    panes expanded, eating most of the canvas width, plus dismissible tip and
    autosave-recovery banners (recovery banner shows up because kill_desktop()
    force-kills rather than closing gracefully). Collapse the panes and close
    what banners we can find so the capture is dominated by the report canvas.
    Best-effort: every step is optional, a missing control just gets skipped.
    Labels cover the French and English UI so a language switch degrades to
    'panes stay open' at worst, not a crash."""
    for tab_label in ("Afficher", "View"):
        try:
            win.child_window(title=tab_label, control_type="TabItem").click_input()
            time.sleep(1)
            break
        except Exception:
            continue
    for label in ("Visualisations", "Données", "Visualizations", "Data"):
        try:
            matches = [b for b in win.descendants(control_type="Button") if b.window_text() == label]
            if matches:
                matches[0].click_input()
                time.sleep(0.5)
        except Exception:
            pass
    try:
        matches = [b for b in win.descendants(control_type="Button")
                   if b.window_text() and ("veloppez le volet de filtre" in b.window_text()
                                           or "the filters pane" in b.window_text().lower())]
        if matches:
            matches[0].click_input()
            time.sleep(0.5)
    except Exception:
        pass
    for label in ("Ne plus afficher", "Don't show again"):
        try:
            matches = [b for b in win.descendants(control_type="Button") if b.window_text() == label]
            if matches:
                matches[0].click_input()
                time.sleep(0.5)
        except Exception:
            pass
    for b in win.descendants(control_type="Button"):
        try:
            if b.window_text() in ("Fermer", "Close") and b.rectangle().width() > 0:
                b.click_input()
                time.sleep(0.3)
        except Exception:
            pass


def fail_with_diagnostics(msg, out_dir: Path):
    """On failure, leave the agent something to look at: a screenshot of the
    Desktop window in .render/_error.png plus any modal dialog text on stdout
    (a TMDL error, a schema validation dialog, a credentials prompt...)."""
    win = find_desktop_window(timeout=5)
    if win is not None:
        try:
            win.set_focus()
            time.sleep(0.5)
        except Exception:
            pass
        try:
            for c in win.descendants(control_type="Window"):
                texts = [t.window_text() for t in c.descendants(control_type="Text")
                         if t.window_text().strip()]
                if texts:
                    print(f"  dialog '{c.window_text()}':")
                    for t in texts[:8]:
                        print(f"    {t}")
        except Exception:
            pass
        try:
            out_dir.mkdir(exist_ok=True)
            win.capture_as_image().save(str(out_dir / "_error.png"))
            print(f"  screenshot saved to {out_dir / '_error.png'}")
        except Exception:
            pass
    sys.exit(f"RENDER FAIL: {msg}")


def main():
    ap = argparse.ArgumentParser(description="Screenshot every report page of a .pbip")
    ap.add_argument("pbip", type=Path)
    ap.add_argument("--pages", default=None,
                    help="comma-separated page displayNames to capture (default: all)")
    ap.add_argument("--settle", type=int, default=DEFAULT_SETTLE,
                    help="seconds to wait after a page switch before capture")
    args = ap.parse_args()

    pbip = args.pbip.resolve()
    wanted = {p.strip() for p in args.pages.split(",")} if args.pages else None
    out = pbip.parent / ".render"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()

    kill_desktop()
    harden_pbip(pbip)
    subprocess.Popen([PBI_EXE, str(pbip)])
    print("Launching Desktop, waiting for the window...")
    win = find_desktop_window(timeout=90)
    if win is None:
        sys.exit("RENDER FAIL: could not find PBIDesktop window via UIA")
    print("Waiting for the report to open (trust dialog auto-dismissed if shown)...")
    win = wait_for_report_open(pbip)
    if win is None:
        fail_with_diagnostics("report never opened (file name absent from window title); "
                              "likely a PBIR/TMDL validation error, see dialog text above", out)
    print("Waiting for model load...")
    if not wait_for_model_loaded():
        fail_with_diagnostics("model did not load within timeout", out)
    try:
        win.wait("ready", timeout=120)
    except Exception:
        fail_with_diagnostics("window never became ready (a modal dialog is likely blocking)", out)
    win.set_focus()
    win.maximize()
    time.sleep(2)
    declutter_ui(win)

    # win.descendants(control_type="TabItem") returns every tab in the whole
    # window (ribbon tabs, view tabs, panel tabs, AND the page-navigator
    # tabs), all flattened with no reliable structural marker between them.
    # Filter by the actual page display names read from the PBIR definition
    # instead of guessing which pane the navigator lives in.
    pages = report_pages(pbip)
    if wanted is not None:
        unknown = wanted - {d for _, d in pages}
        if unknown:
            print(f"  WARN: --pages names not in report: {', '.join(sorted(unknown))}")
        pages = [(i, d) for i, d in pages if d in wanted]
    all_tabs = win.descendants(control_type="TabItem")
    by_name = {}
    for t in all_tabs:
        by_name.setdefault(t.window_text(), t)

    for page_id, display_name in pages:
        tab = by_name.get(display_name)
        if tab is None:
            print(f"  WARN: no tab found for page '{display_name}' ({page_id}), skipping")
            continue
        try:
            tab.click_input()
        except Exception:
            tab.select()
        time.sleep(args.settle)
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in display_name).strip()
        _capture(win, out / f"{safe}.png")
        print(f"  captured page: {display_name}")

    print(f"RENDER OK: {len(list(out.glob('*.png')))} page(s) in {out}")


def _capture(win, path: Path):
    img = win.capture_as_image()
    img.save(str(path))


if __name__ == "__main__":
    main()
