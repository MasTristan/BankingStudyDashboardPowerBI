#!/usr/bin/env python3
"""pbi_init.py - Bootstrap the autonomous visual pipeline onto a virgin PBIP project.

Cold-start scenario: a .pbip exists with its semantic model connected (tables
loading, measures in TMDL), but the report layer is empty and the project has
none of the pipeline machinery. This script installs everything the agent
needs to work autonomously:

  1. scripts/            compile_page, pbir_lint, pbi_render, pbi_profile, pbi_init
  2. .claude/skills/pbi-report/   SKILL.md + templates + references
  3. .claude/settings.json        PostToolUse lint hook (created or merged)
  4. Theme                        report.theme.json copied into the target
                                  Report's RegisteredResources and wired into
                                  report.json (customTheme + resourcePackages)
  5. specs/              empty, ready for page specs
  6. pages.json          created from existing page folders if missing
  7. .gitignore          render/cache entries appended when the target is a repo

Idempotent: re-running refreshes scripts/skill/theme without touching specs
or compiled pages. The kit source is the repo this script lives in, so an
initialized project can itself initialize the next one.

Usage: python scripts/pbi_init.py <target-project-dir>
"""

import json
import shutil
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
THEME_FILE = "report.theme.json"
GITIGNORE_BLOCK = """
# --- pbi visual pipeline ---
.render/
__pycache__/
*.pyc
*.Report/.pbi/localSettings.json
*.SemanticModel/.pbi/localSettings.json
*.SemanticModel/.pbi/cache.abf
*.SemanticModel/.pbi/editorSettings.json
"""

LINT_HOOK = {
    "matcher": "Write|Edit|MultiEdit",
    "hooks": [{"type": "command", "command": "python scripts/pbir_lint.py"}],
}


def fail(msg):
    sys.exit(f"INIT FAIL: {msg}")


def find_project(target: Path):
    pbip = next(target.glob("*.pbip"), None)
    report = next(target.glob("*.Report"), None)
    model = next(target.glob("*.SemanticModel"), None)
    if not pbip or not report or not model:
        fail(f"{target} must contain a .pbip, a *.Report and a *.SemanticModel "
             f"(found: pbip={pbip}, report={report}, model={model})")
    if not (report / "definition" / "report.json").exists():
        fail(f"{report} has no definition/report.json (open and save the project "
             f"in Desktop once, PBIR format enabled)")
    return pbip, report, model


def copy_tree(src: Path, dst: Path):
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if "__pycache__" in item.parts:
            continue
        rel = item.relative_to(src)
        if item.is_dir():
            (dst / rel).mkdir(exist_ok=True)
        else:
            shutil.copy2(item, dst / rel)


def harden_pbip(pbip: Path):
    """Pipeline projects must not auto-recover: the render script force-kills
    Desktop, and with auto recovery enabled Desktop rewrites stale PBIR files
    from its recovery state on the next launch, silently undoing on-disk
    changes (observed: deleted pages coming back)."""
    data = json.loads(pbip.read_text(encoding="utf-8"))
    data.setdefault("settings", {})["enableAutoRecovery"] = False
    pbip.write_text(json.dumps(data, indent=2), encoding="utf-8")


def harden_report_settings(report_dir: Path):
    """Keep the filter pane collapsed in edit mode so renders are dominated by
    the canvas."""
    rj_path = report_dir / "definition" / "report.json"
    rj = json.loads(rj_path.read_text(encoding="utf-8"))
    rj.setdefault("settings", {})["filterPaneHiddenInEditMode"] = True
    rj_path.write_text(json.dumps(rj, indent=2), encoding="utf-8")


def install_theme(report_dir: Path):
    theme_src = KIT_ROOT / ".claude" / "skills" / "pbi-report" / "templates" / THEME_FILE
    if not theme_src.exists():
        fail(f"kit theme not found at {theme_src}")
    res_dir = report_dir / "StaticResources" / "RegisteredResources"
    res_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(theme_src, res_dir / THEME_FILE)

    rj_path = report_dir / "definition" / "report.json"
    rj = json.loads(rj_path.read_text(encoding="utf-8"))

    versions = (rj.get("themeCollection", {}).get("baseTheme", {})
                .get("reportVersionAtImport",
                     {"visual": "2.8.0", "report": "3.2.0", "page": "2.3.1"}))
    rj.setdefault("themeCollection", {})["customTheme"] = {
        "name": THEME_FILE,
        "reportVersionAtImport": versions,
        "type": "RegisteredResources",
    }
    packages = rj.setdefault("resourcePackages", [])
    reg = next((p for p in packages if p.get("type") == "RegisteredResources"), None)
    if reg is None:
        reg = {"name": "RegisteredResources", "type": "RegisteredResources", "items": []}
        packages.append(reg)
    if not any(i.get("name") == THEME_FILE for i in reg["items"]):
        reg["items"].append({"name": THEME_FILE, "path": THEME_FILE, "type": "CustomTheme"})
    rj_path.write_text(json.dumps(rj, indent=2), encoding="utf-8")


def ensure_pages_json(report_dir: Path):
    pages_dir = report_dir / "definition" / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    pages_json = pages_dir / "pages.json"
    if pages_json.exists():
        return
    order = sorted(p.name for p in pages_dir.iterdir() if p.is_dir())
    payload = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": order,
    }
    if order:
        payload["activePageName"] = order[0]
    pages_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def install_settings(target: Path):
    settings_path = target / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        settings = {}
    post = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    if not any("pbir_lint" in json.dumps(h) for h in post):
        post.append(LINT_HOOK)
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def append_gitignore(target: Path):
    gi = target / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if ".render/" in existing:
        return False
    gi.write_text(existing + GITIGNORE_BLOCK, encoding="utf-8")
    return True


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1]).resolve()
    if target == KIT_ROOT:
        fail("target is the kit repo itself")
    pbip, report_dir, model = find_project(target)

    copy_tree(KIT_ROOT / "scripts", target / "scripts")
    print(f"  scripts/           -> {target / 'scripts'}")
    copy_tree(KIT_ROOT / ".claude" / "skills" / "pbi-report",
              target / ".claude" / "skills" / "pbi-report")
    print(f"  skill pbi-report   -> {target / '.claude' / 'skills' / 'pbi-report'}")
    install_settings(target)
    print("  lint hook          -> .claude/settings.json")
    install_theme(report_dir)
    print(f"  theme              -> {report_dir.name}/StaticResources + report.json")
    harden_pbip(pbip)
    harden_report_settings(report_dir)
    print("  hardening          -> auto recovery off, filter pane collapsed")
    ensure_pages_json(report_dir)
    (target / "specs").mkdir(exist_ok=True)
    if append_gitignore(target):
        print("  .gitignore         -> render/cache entries appended")

    print(f"INIT OK: {pbip.name} is ready for the pipeline.")
    print("Next: profile the model (scripts/pbi_profile.py tables --launch "
          f"{pbip.name}), write specs/<page>.yaml, compile, lint, render.")


if __name__ == "__main__":
    main()
