"""web_src/app.html -> docs/index.html (loads docs/data/feed.json at runtime).
Also writes docs/fragment.html with the feed inlined, for single-file previews."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
app = (ROOT / "web_src" / "app.html").read_text()
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "index.html").write_text(
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n' + app + "\n</html>\n")
(ROOT / "docs" / ".nojekyll").write_text("")
feed = ROOT / "docs" / "data" / "feed.json"
if feed.exists():
    (ROOT / "docs" / "fragment.html").write_text(app.replace("/*DATA*/", feed.read_text().replace("</", "<\\/")))
print("docs/index.html written")
