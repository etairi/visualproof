#!/usr/bin/env python3
"""Publish VisualProof boards to the gh-pages branch of this repository.

Runs on the build host, where a VisualProof server follows each project's branch and builds every
new commit in a clone of its own (~/vp/cache/visualproof/deploy/<repo>-<hash>/clone/<repo>). For each
project of projects.json whose clone holds a new commit that is fully built, it exports the board
(`visualproof export`, a self-contained HTML file). It then writes the landing page and force-pushes
the whole site as one commit to gh-pages, so that the repository does not grow with every board.
A commit whose build failed or is still running is not published: the previous board stays.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile

HOME = os.path.expanduser("~")
VP = os.path.join(HOME, "vp")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(VP, "site-state")
VISUALPROOF = os.path.join(VP, "npm", "node_modules", ".bin", "visualproof")
SITE_KEY = os.path.join(VP, "keys", "visualproof-site-deploy")
KNOWN_HOSTS = os.path.join(VP, "keys", "known_hosts_github")

ENV = dict(os.environ)
ENV.update(
    PATH=f"{HOME}/.elan/bin:{VP}/node/bin:/usr/local/bin:/usr/bin:/bin",
    XDG_CACHE_HOME=f"{VP}/cache",
    XDG_DATA_HOME=f"{VP}/data",
    GIT_SSH_COMMAND=(f"ssh -i {SITE_KEY} -o IdentitiesOnly=yes "
                     f"-o UserKnownHostsFile={KNOWN_HOSTS} -o StrictHostKeyChecking=yes"),
)


def log(msg: str) -> None:
    print(f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S}Z {msg}", flush=True)


def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, env=ENV, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} exited {r.returncode}: {(r.stderr or r.stdout)[-1500:]}")
    return r


def clone_of(project: dict) -> str | None:
    name = project["repo"].split("/")[1]
    hits = glob.glob(f"{VP}/cache/visualproof/deploy/{project['deploy']}-*/clone/{name}")
    return hits[0] if len(hits) == 1 else None


def head(clone: str) -> str:
    return run(["git", "rev-parse", "HEAD"], cwd=clone).stdout.strip()


def fully_built(clone: str) -> bool:
    """Every default target of the checked-out commit is built and up to date."""
    return run(["lake", "build", "--no-build"], cwd=clone, check=False).returncode == 0


def load_meta(slug: str) -> dict:
    try:
        with open(os.path.join(STATE, slug, "meta.json")) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def export(project: dict, clone: str, sha: str) -> bool:
    slug = project["slug"]
    out_dir = os.path.join(STATE, slug)
    site_dir = os.path.join(STATE, "sites", slug)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(site_dir, exist_ok=True)
    tmp = os.path.join(out_dir, "index.html.tmp")
    label = f"{slug} · {project['branch']} @ {sha[:7]}"
    run([VISUALPROOF, "export", clone, "-o", tmp, "--label", label, "--site", site_dir,
         "--jobs", "4"])
    # The server may have moved on to a newer commit meanwhile: publish only a consistent export.
    if head(clone) != sha or not fully_built(clone):
        os.remove(tmp)
        log(f"{slug}: {sha[:7]} changed during the export; trying again next time")
        return False
    board = os.path.join(out_dir, "index.html")
    os.replace(tmp, board)
    with open(os.path.join(clone, "lean-toolchain")) as f:
        toolchain = f.read().strip()
    meta = {
        "sha": sha,
        "committed": run(["git", "show", "-s", "--format=%cI", sha], cwd=clone).stdout.strip(),
        "exported": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "lean": toolchain.split(":")[-1],
        "size": os.path.getsize(board),
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    log(f"{slug}: exported {sha[:7]} ({meta['size'] / 1e6:.1f} MB)")
    return True


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="Lean 4 formalizations drawn as declaration graphs with VisualProof.">
<link rel="icon" href="data:,">
<style>
:root {{ --bg: #f6f6f3; --fg: #1c1c1f; --muted: #66666d; --card: #ffffff; --line: #e2e2dd;
  --accent: #5a3ed0; --on-accent: #ffffff; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #131316; --fg: #ececf1; --muted: #9b9ba4; --card: #1c1c21; --line: #2d2d35;
    --accent: #a995ff; --on-accent: #131316; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }}
main {{ max-width: 860px; margin: 0 auto; padding: 56px 16px 40px; }}
h1 {{ font-size: 2rem; line-height: 1.2; margin: 0 0 10px; letter-spacing: 0.01em; }}
.lede {{ color: var(--muted); margin: 0 0 32px; max-width: 660px; }}
.lede a, footer a {{ color: inherit; }}
.grid {{ display: grid; gap: 16px; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 20px 22px; }}
.card h2 {{ font-size: 1.2rem; line-height: 1.35; margin: 0 0 6px; }}
.card p {{ margin: 0 0 12px; }}
.meta {{ color: var(--muted); font-size: 0.9rem; display: flex; flex-wrap: wrap; gap: 2px 16px;
  margin-bottom: 14px; }}
.meta a {{ color: inherit; }}
code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.85em; }}
.actions {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; }}
a.button {{ display: inline-block; padding: 8px 16px; border-radius: 8px; background: var(--accent);
  color: var(--on-accent); text-decoration: none; font-weight: 600; }}
a.button:hover {{ filter: brightness(1.08); }}
a.link {{ color: var(--accent); text-decoration: none; }}
a.link:hover {{ text-decoration: underline; }}
footer {{ color: var(--muted); font-size: 0.85rem; margin-top: 40px; }}
</style>
</head>
<body>
<main>
<h1>{title}</h1>
<p class="lede">Lean 4 formalizations, drawn as declaration graphs with
<a href="https://github.com/LucianoXu/VisualProof">VisualProof</a>. Select a theorem on a board to see
what its statement rests on: the definitions to read to believe it, and the axioms it uses. Each board
is exported again after every new commit of its branch builds.</p>
<div class="grid">
{cards}
</div>
<footer>The boards are frozen exports: the graph, each theorem's trusted base and the sources, without
the live review. A large board takes a few seconds to open. <a href="{home}">{home_name}</a></footer>
</main>
</body>
</html>
"""


def card(project: dict, meta: dict) -> str:
    e = html.escape
    repo_url = f"https://github.com/{project['repo']}"
    parts = ['<article class="card">', f"<h2>{e(project['title'])}</h2>",
             f"<p>{e(project['description'])}</p>"]
    actions = []
    if meta:
        sha = meta["sha"]
        commit = f"<code>{e(sha[:7])}</code>"
        if project["public"]:
            commit = f'<a href="{repo_url}/commit/{e(sha)}">{commit}</a>'
        parts.append('<div class="meta">'
                     f"<span>Branch <code>{e(project['branch'])}</code> at {commit}</span>"
                     f"<span>Committed {e(meta['committed'][:10])}</span>"
                     f"<span>Lean {e(meta['lean'])}</span>"
                     f"<span>{meta['size'] / 1e6:.0f} MB</span></div>")
        actions.append(f'<a class="button" href="{e(project["slug"])}/">Open the board</a>')
    else:
        parts.append('<div class="meta"><span>The first board is being built.</span></div>')
    if project["public"]:
        actions.append(f'<a class="link" href="{repo_url}">Source on GitHub</a>')
    if actions:
        parts.append(f'<div class="actions">{"".join(actions)}</div>')
    parts.append("</article>")
    return "\n".join(parts)


def page(config: dict, metas: dict) -> str:
    site = config["site"]
    cards = "\n".join(card(p, metas.get(p["slug"], {})) for p in config["projects"])
    home = site["home"]
    return PAGE.format(title=html.escape(site["title"]), cards=cards, home=html.escape(home),
                       home_name=html.escape(home.split("//")[-1]))


def publish(config: dict, metas: dict) -> None:
    site = config["site"]
    work = tempfile.mkdtemp(prefix="vp-site-")
    try:
        for p in config["projects"]:
            board = os.path.join(STATE, p["slug"], "index.html")
            if metas.get(p["slug"]) and os.path.exists(board):
                os.makedirs(os.path.join(work, p["slug"]))
                shutil.copy2(board, os.path.join(work, p["slug"], "index.html"))
        with open(os.path.join(work, "index.html"), "w") as f:
            f.write(page(config, metas))
        with open(os.path.join(work, "CNAME"), "w") as f:
            f.write(site["domain"] + "\n")
        open(os.path.join(work, ".nojekyll"), "w").close()
        run(["git", "init", "-q", "-b", "gh-pages"], cwd=work)
        run(["git", "add", "-A"], cwd=work)
        summary = ", ".join(f"{s}@{m['sha'][:7]}" for s, m in metas.items() if m) or "landing page"
        run(["git", "-c", "user.name=visualproof-publisher",
             "-c", "user.email=visualproof-publisher@users.noreply.github.com",
             "commit", "-q", "-m", f"Publish boards: {summary}"], cwd=work)
        run(["git", "push", "-q", "-f", f"git@github.com:{site['repo']}.git", "gh-pages"], cwd=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    with open(os.path.join(HERE, "projects.json")) as f:
        config = json.load(f)
    os.makedirs(STATE, exist_ok=True)
    changed = False
    for p in config["projects"]:
        clone = clone_of(p)
        if clone is None:
            log(f"{p['slug']}: no VisualProof clone yet")
            continue
        sha = head(clone)
        if load_meta(p["slug"]).get("sha") == sha:
            continue
        if not fully_built(clone):
            log(f"{p['slug']}: {sha[:7]} is not fully built yet")
            continue
        try:
            changed |= export(p, clone, sha)
        except RuntimeError as err:
            log(f"{p['slug']}: export failed: {err}")
    metas = {p["slug"]: load_meta(p["slug"]) for p in config["projects"]}
    # Publish when a board changed, or when the page itself (projects.json, this script) changed.
    with open(__file__, "rb") as f:
        fingerprint = hashlib.sha256(json.dumps([config, metas], sort_keys=True).encode()
                                     + f.read()).hexdigest()
    stamp = os.path.join(STATE, "published.sha256")
    old = open(stamp).read().strip() if os.path.exists(stamp) else ""
    if changed or fingerprint != old:
        publish(config, metas)
        with open(stamp, "w") as f:
            f.write(fingerprint)
        log("published: " + ", ".join(f"{s}@{m['sha'][:7]}" for s, m in metas.items() if m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
