# VisualProof boards

The site behind <https://visualproof.erkantairi.com>: one board per Lean 4 formalization, drawn
with [VisualProof](https://github.com/LucianoXu/VisualProof). A board is the project's
declaration graph; selecting a theorem shows what its statement rests on (its trusted base) and
the axioms it uses.

## How the site updates

A build host runs one VisualProof server per project. Each server follows a branch of the project
and builds every new commit in a clone of its own. Every 10 minutes a systemd timer there runs
`publish.py`, which:

1. exports the board of each project whose clone holds a new, fully built commit
   (`visualproof export`, a self-contained HTML file; a commit whose build failed or is still
   running is skipped, so the previous board stays up);
2. writes the landing page from `projects.json`;
3. force-pushes the whole site as a single commit to `gh-pages`, which GitHub Pages serves.

`gh-pages` therefore always holds one commit, and the repository does not grow with every board.
The boards are frozen exports: the graph, each theorem's trusted base and the sources, without the
live review that a VisualProof server offers.

## Adding a project

1. On the build host, start a VisualProof server that follows the project's branch
   (`visualproof serve <git URL> --branch <branch>`). For a private repository, give it a
   read-only deploy key.
2. Add the project to `projects.json`:
   - `slug`: its path on the site;
   - `title` and `description`: what the landing page shows;
   - `repo`, `public`, `branch`;
   - `deploy`: the repository's name. The server's clone is found under
     `~/vp/cache/visualproof/deploy/<deploy>-*/`.
3. Push to `main`. The next run publishes the project.

## Domain

GitHub Pages serves `gh-pages` under the custom domain in its `CNAME` file. This needs the DNS record
`visualproof CNAME etairi.github.io`.

## Building on GitHub's runners (test)

The build host is temporary, so `.github/workflows/boards-test.yml` tests building and exporting
the boards on GitHub's runners. Start it by hand (Actions → Boards (test) → Run workflow). It
publishes nothing: each board is uploaded as an artifact, with build and export logs that record time
and peak memory.

- Private projects are checked out with a read-only deploy key, stored as the secret
  `DEPLOY_KEY_<SLUG>` (for example `DEPLOY_KEY_SUPERLATTICE_SVP`).
- Each project's `.lake/build` is kept in the Actions cache. Mathlib comes from `lake exe cache get`.
- The vendored `SpherePacking` and `MetricCodes` (about 14 GB of memory to build `MetricCodes`) are
  unpacked from the `vendored-oleans` release.
  - Each asset is `vendored-oleans-<key>.tar.zst`, where
    `key = sha256(lean-toolchain ‖ lake-manifest.json ‖ SpherePacking.lean ‖ MetricCodes.lean)[:16]`.
  - When a key changes, build those modules on a machine with enough memory, pack the files listed
    in the release notes from `.lake/build`, and upload them under the new key.
