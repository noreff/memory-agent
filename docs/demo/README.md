# Demo assets (README GIFs)

GIFs are generated from `.tape` scripts with [VHS](https://github.com/charmbracelet/vhs) — recorded
as code, so they regenerate deterministically. The `kb/` notes here are a small **sanitized** demo
knowledge base (no personal data) used for anything that shows note contents on screen.

```bash
brew install vhs ttyd                 # ffmpeg already required; bat for pretty cat
# VHS needs a headless browser; if none is bundled, point it at an installed one:
ROD_BROWSER_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  vhs docs/demo/hero.tape             # → docs/img/hero.gif
```

Tapes: `hero.tape` (memory = files with receipts). Output lands in `docs/img/`.
