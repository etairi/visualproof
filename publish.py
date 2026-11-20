#!/usr/bin/env python3
"""Publish VisualProof boards to the gh-pages branch of this repository.

Runs on the build host, where a VisualProof server follows each project's branch and builds every
new commit in a clone of its own (~/vp/cache/visualproof/deploy/<repo>-<hash>/clone/<repo>). For each
project of projects.json whose clone holds a new commit that is fully built, it exports the board
(`visualproof export`, a self-contained HTML file) and draws two previews of it (`visualproof
artwork`, light and dark), lighting what the project's highlight theorem rests on. It then writes the
landing page and force-pushes the whole site as one commit to gh-pages, so that the repository does
not grow with every board. A commit whose build failed or is still running is not published: the
previous board stays.
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
WEBFONTS = os.path.join(VP, "npm", "node_modules", "visualproof", "dist", "assets", "webfonts")
FONT_FILES = ["jost-latin-wght-normal.woff2", "source-sans-3-latin-wght-normal.woff2",
              "LICENSE-Jost.txt", "LICENSE-SourceSans3.txt"]
SITE_KEY = os.path.join(VP, "keys", "visualproof-site-deploy")
KNOWN_HOSTS = os.path.join(VP, "keys", "known_hosts_github")
LOOKS = {"light": {"style": "board", "theme": "light"}, "dark": {"style": "stars", "theme": "dark"}}

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


def save_meta(slug: str, meta: dict) -> None:
    with open(os.path.join(STATE, slug, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


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
    save_meta(slug, meta)
    log(f"{slug}: exported {sha[:7]} ({meta['size'] / 1e6:.1f} MB)")
    return True


def to_webp(png: str, out: str) -> None:
    from PIL import Image  # on the build host: python3-pil
    with Image.open(png) as im:
        im.convert("RGB").save(out, "WEBP", quality=82, method=6)


def draw_previews(project: dict, clone: str, meta: dict) -> None:
    """Draw the board twice (light, dark) as `visualproof artwork` does, lighting what the first
    highlight theorem that the build has rests on; with none of them, the plain graph."""
    slug = project["slug"]
    candidates = list(project.get("highlight", [])) + [None]
    meta["previews"] = []
    for look, style in LOOKS.items():
        for name in candidates:
            recipe = {"size": {"w": 1600, "h": 1000}, **style, "border": False, "title": "",
                      "subtitle": "", "source": False, "names": {"clusters": 0}, "keys": False,
                      "notes": False, "edges": "all"}
            if name:
                recipe.update(targets=[name], selection={"name": name, "mode": "tcb"})
            else:
                recipe.update(targets="none")
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "recipe.json")
                with open(path, "w") as f:
                    json.dump(recipe, f)
                r = run([VISUALPROOF, "artwork", clone, "--recipe", path, "-o",
                         os.path.join(tmp, "art"), "--png", "--jobs", "4"], check=False)
                png = os.path.join(tmp, "art.png")
                if r.returncode == 0 and os.path.exists(png):
                    to_webp(png, os.path.join(STATE, slug, f"preview-{look}.webp"))
                    meta["previews"].append(look)
                    meta["highlight"] = name
                    break
                log(f"{slug}: no {look} preview with {name}: {(r.stderr or r.stdout).strip()[-200:]}")
    save_meta(slug, meta)
    log(f"{slug}: previews {meta['previews']} (highlight {meta.get('highlight')})")


# ---------------------------------------------------------------------------------------------
# The landing page, in VisualProof's own visual language: Jost capitals for titles (as on its
# posters), Source Sans 3 for text, white panels on a pale canvas, the brand gradient
# #5e5ce6 → #bf5af2 → #ff375f, and in the dark the night sky of its "stars" artwork.

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
           "%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E"
           "%3Cstop offset='0' stop-color='%235e5ce6'/%3E%3Cstop offset='.55' stop-color='%23bf5af2'/%3E"
           "%3Cstop offset='1' stop-color='%23ff375f'/%3E%3C/linearGradient%3E%3C/defs%3E"
           "%3Ccircle cx='32' cy='32' r='25' fill='none' stroke='url(%23g)' stroke-width='6'/%3E"
           "%3Ccircle cx='32' cy='32' r='9' fill='url(%23g)'/%3E%3C/svg%3E")

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<meta name="description" content="Lean 4 formalizations drawn as declaration graphs with VisualProof.">
<link rel="icon" href="{favicon}">
<link rel="preload" href="assets/fonts/jost-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin>
<style>
@font-face {{ font-family: "Jost"; src: url("assets/fonts/jost-latin-wght-normal.woff2") format("woff2");
  font-weight: 100 900; font-display: swap; }}
@font-face {{ font-family: "Source Sans 3"; src: url("assets/fonts/source-sans-3-latin-wght-normal.woff2")
  format("woff2"); font-weight: 200 900; font-display: swap; }}
:root {{
  --canvas: #f4f4f7; --panel: #ffffff; --ink: #1c1c22; --muted: #686876; --line: #e4e4eb;
  --chip: #f3f2f8; --accent: #6b55e0; --ok: #1f9d68;
  --g1: #5e5ce6; --g2: #bf5af2; --g3: #ff375f;
  --shadow: 0 1px 2px rgba(23, 23, 46, .05), 0 14px 34px -10px rgba(23, 23, 46, .14);
  --sky: radial-gradient(1000px 460px at 50% -220px, rgba(191, 90, 242, .14), transparent 70%);
  --stars: none;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --canvas: #090c12; --panel: #121826; --ink: #ecebf6; --muted: #a0a2b6; --line: #242b3c;
    --chip: #19202f; --accent: #a998ff; --ok: #3ccf8e;
    --shadow: 0 1px 2px rgba(0, 0, 0, .5), 0 18px 40px -12px rgba(0, 0, 0, .7);
    --sky: radial-gradient(1100px 520px at 50% -240px, rgba(94, 92, 230, .26), transparent 70%),
      linear-gradient(#121a28, #090c12 70%);
    --stars: radial-gradient(1.3px 1.3px at 23px 41px, rgba(255, 255, 255, .7), transparent 60%),
      radial-gradient(1px 1px at 151px 97px, rgba(255, 255, 255, .5), transparent 60%),
      radial-gradient(1.1px 1.1px at 83px 163px, rgba(200, 210, 255, .55), transparent 60%),
      radial-gradient(.9px .9px at 197px 19px, rgba(255, 255, 255, .45), transparent 60%);
  }}
}}
* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{ margin: 0; min-height: 100vh; color: var(--ink); background: var(--sky), var(--canvas);
  background-repeat: no-repeat;
  font: 17px/1.55 "Source Sans 3", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
body::before {{ content: ""; position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background-image: var(--stars); background-size: 223px 211px; }}
.wrap {{ max-width: 1160px; margin: 0 auto; padding: 18px 16px 0; }}
.bar {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; background: var(--panel);
  border-radius: 14px; box-shadow: var(--shadow); padding: 10px 18px; }}
.wordmark, .wm {{ background: linear-gradient(95deg, var(--g1), var(--g2) 55%, var(--g3));
  -webkit-background-clip: text; background-clip: text; color: transparent; font-weight: 700; }}
.wordmark {{ font-size: 1.3rem; text-decoration: none; letter-spacing: -.01em; }}
.crumb {{ color: var(--muted); }}
.pill {{ display: inline-flex; align-items: center; gap: 7px; font-size: .86rem; color: var(--muted);
  background: var(--chip); border: 1px solid var(--line); border-radius: 999px; padding: 2px 11px; }}
.pill i {{ width: 7px; height: 7px; border-radius: 50%; background: var(--ok); flex: none; }}
.bar .pill {{ margin-left: auto; }}
.hero {{ padding: 64px 4px 40px; }}
h1 {{ font: 400 clamp(1.5rem, 6.2vw, 3.7rem)/1.08 "Jost", sans-serif;
  letter-spacing: clamp(.05em, 1.25vw, .17em); text-transform: uppercase; margin: 0 0 18px;
  overflow-wrap: anywhere; }}
@media (max-width: 600px) {{ .hero {{ padding: 40px 2px 28px; }} .body {{ padding: 18px 18px 20px; }} }}
.sub {{ color: var(--muted); font-size: clamp(1.02rem, 2.2vw, 1.22rem); max-width: 720px; margin: 0; }}
.grid {{ display: grid; gap: 26px; grid-template-columns: repeat(auto-fit, minmax(min(100%, 450px), 1fr)); }}
.card {{ display: flex; flex-direction: column; background: var(--panel); border-radius: 18px;
  box-shadow: var(--shadow); overflow: hidden; transition: transform .35s ease; }}
.card:hover {{ transform: translateY(-3px); }}
.art {{ display: block; aspect-ratio: 16 / 10; overflow: hidden; background: var(--chip); }}
.art img {{ display: block; width: 100%; height: 100%; object-fit: cover; transition: transform .7s ease; }}
.card:hover .art img {{ transform: scale(1.035); }}
.art.pending {{ display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; }}
.art.pending svg {{ width: 30%; animation: turn 24s linear infinite; }}
.art.pending span {{ font: 500 .9rem "Jost", sans-serif; letter-spacing: .18em; text-transform: uppercase;
  color: var(--muted); }}
@keyframes turn {{ to {{ transform: rotate(360deg); }} }}
.body {{ display: flex; flex-direction: column; gap: 10px; flex: 1; padding: 22px 24px 24px; }}
h2 {{ font: 500 1.2rem/1.3 "Jost", sans-serif; letter-spacing: .09em; text-transform: uppercase; margin: 0; }}
.lit {{ margin: 0; font-size: .93rem; color: var(--muted); }}
.lit code {{ color: var(--accent); }}
.desc {{ margin: 0; }}
.pills {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: auto; padding-top: 8px; }}
.btn {{ display: inline-flex; align-items: center; gap: 8px; padding: 9px 17px; border-radius: 11px;
  font-weight: 600; text-decoration: none; color: var(--ink); background: var(--panel);
  border: 1px solid var(--line); transition: filter .2s, transform .2s; }}
.btn:hover {{ filter: brightness(1.06); transform: translateY(-1px); }}
.btn.primary {{ color: #fff; border: 0; background: linear-gradient(100deg, var(--g1), var(--g2) 60%, var(--g3));
  box-shadow: 0 8px 20px -8px rgba(191, 90, 242, .6); }}
code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .86em; }}
footer {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 24px; color: var(--muted);
  font-size: .92rem; padding: 40px 4px 44px; }}
footer a {{ color: inherit; }}
@media (prefers-reduced-motion: reduce) {{ .card, .art img, .btn, .art.pending svg {{ transition: none; animation: none; }} }}
</style>
</head>
<body>
<div class="wrap">
<header class="bar">
  <a class="wordmark" href="https://github.com/LucianoXu/VisualProof">VisualProof</a>
  <span class="crumb">«boards»</span>
  <span class="pill"><i></i>{built}</span>
</header>
<section class="hero">
  <h1>Lean formalizations</h1>
  <p class="sub">Each board is a project's declaration graph. Select a theorem to light up what its
  statement rests on: the definitions to read to believe it, and the axioms it uses.</p>
</section>
<main class="grid">
{cards}
</main>
<footer>
  <span>Generated by <b class="wm">VisualProof</b></span>
  <span>Frozen exports, rebuilt after every new commit · <a href="{home}">{home_name}</a></span>
</footer>
</div>
</body>
</html>
"""

PENDING_ART = """<svg viewBox="0 0 100 100" aria-hidden="true"><defs><linearGradient id="pg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#5e5ce6"/><stop offset=".55" stop-color="#bf5af2"/><stop offset="1" stop-color="#ff375f"/>
</linearGradient></defs><circle cx="50" cy="50" r="46" fill="none" stroke="url(#pg)" stroke-width="1.2" stroke-dasharray="4 5" opacity=".7"/>
<circle cx="38" cy="40" r="17" fill="none" stroke="url(#pg)" stroke-width="1" opacity=".55"/>
<circle cx="64" cy="62" r="13" fill="none" stroke="url(#pg)" stroke-width="1" opacity=".55"/>
<circle cx="40" cy="70" r="7" fill="none" stroke="url(#pg)" stroke-width="1" opacity=".45"/></svg>"""


def nice_date(iso: str) -> str:
    try:
        return dt.datetime.fromisoformat(iso).strftime("%-d %b %Y")
    except ValueError:
        return iso[:10]


def card(project: dict, meta: dict) -> str:
    e = html.escape
    slug, title = project["slug"], project["title"]
    repo_url = f"https://github.com/{project['repo']}"
    parts = ['<article class="card">']
    if meta and meta.get("previews"):
        name = meta.get("highlight")
        alt = f"The declaration graph of {title}" + (f", with what {name} rests on lit" if name else "")
        light = "light" if "light" in meta["previews"] else meta["previews"][0]
        dark = "dark" if "dark" in meta["previews"] else light
        parts.append(f'<a class="art" href="{e(slug)}/"><picture>'
                     f'<source srcset="{e(slug)}/preview-{dark}.webp" media="(prefers-color-scheme: dark)">'
                     f'<img src="{e(slug)}/preview-{light}.webp" alt="{e(alt)}" width="1600" height="1000"'
                     ' loading="lazy" decoding="async"></picture></a>')
    else:
        parts.append(f'<div class="art pending">{PENDING_ART}<span>Being built</span></div>')
    parts.append('<div class="body">')
    parts.append(f"<h2>{e(title)}</h2>")
    if meta and meta.get("highlight"):
        full = meta["highlight"]
        parts.append(f'<p class="lit">Lit: what <code title="{e(full)}">{e(full.split(".")[-1])}</code>'
                     " rests on</p>")
    parts.append(f'<p class="desc">{e(project["description"])}</p>')
    if meta:
        commit = f"<code>{e(meta['sha'][:7])}</code>"
        if project["public"]:
            commit = f'<a href="{repo_url}/commit/{e(meta["sha"])}" style="color:inherit">{commit}</a>'
        parts.append('<div class="pills">'
                     '<span class="pill"><i></i>Built</span>'
                     f"<span class=\"pill\"><code>{e(project['branch'])}</code>&nbsp;@&nbsp;{commit}</span>"
                     f"<span class=\"pill\">Lean {e(meta['lean'])}</span>"
                     f"<span class=\"pill\">{e(nice_date(meta['committed']))}</span></div>")
        actions = [f'<a class="btn primary" href="{e(slug)}/">Open the board</a>']
    else:
        actions = []
    if project["public"]:
        actions.append(f'<a class="btn" href="{repo_url}">Source</a>')
    if actions:
        parts.append(f'<div class="actions">{"".join(actions)}</div>')
    parts.append("</div></article>")
    return "\n".join(parts)


def page(config: dict, metas: dict) -> str:
    site = config["site"]
    built = sum(1 for p in config["projects"] if metas.get(p["slug"]))
    total = len(config["projects"])
    cards = "\n".join(card(p, metas.get(p["slug"], {})) for p in config["projects"])
    home = site["home"]
    return PAGE.format(title=html.escape(site["title"]), favicon=FAVICON, cards=cards,
                       built=f"{built} of {total} boards built" if built < total else f"{total} boards built",
                       home=html.escape(home), home_name=html.escape(home.split("//")[-1]))


def render(config: dict, metas: dict, out: str, state: str = STATE, fonts: str = WEBFONTS) -> None:
    """Write the site (landing page, boards, previews, fonts) into `out`."""
    for p in config["projects"]:
        meta = metas.get(p["slug"])
        src = os.path.join(state, p["slug"])
        if not meta:
            continue
        os.makedirs(os.path.join(out, p["slug"]), exist_ok=True)
        for f in ["index.html"] + [f"preview-{look}.webp" for look in meta.get("previews", [])]:
            if os.path.exists(os.path.join(src, f)):
                shutil.copy2(os.path.join(src, f), os.path.join(out, p["slug"], f))
    os.makedirs(os.path.join(out, "assets", "fonts"), exist_ok=True)
    for f in FONT_FILES:
        if os.path.exists(os.path.join(fonts, f)):
            shutil.copy2(os.path.join(fonts, f), os.path.join(out, "assets", "fonts", f))
    with open(os.path.join(out, "index.html"), "w") as f:
        f.write(page(config, metas))


def publish(config: dict, metas: dict) -> None:
    site = config["site"]
    work = tempfile.mkdtemp(prefix="vp-site-")
    try:
        render(config, metas, work)
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
        meta = load_meta(p["slug"])
        if meta.get("sha") != sha:
            if not fully_built(clone):
                log(f"{p['slug']}: {sha[:7]} is not fully built yet")
                continue
            try:
                if not export(p, clone, sha):
                    continue
            except RuntimeError as err:
                log(f"{p['slug']}: export failed: {err}")
                continue
            changed = True
            meta = load_meta(p["slug"])
        # previews of the published board: after an export, or when the page asks for new ones
        wanted = list(p.get("highlight", []))
        if meta and (not meta.get("previews") or meta.get("highlight_candidates") != wanted):
            meta["highlight_candidates"] = wanted
            draw_previews(p, clone, meta)
            changed = True
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
