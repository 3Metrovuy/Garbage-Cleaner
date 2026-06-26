import logging
import os
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

_log = logging.getLogger(__name__)

# ── HTML template ─────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Garbage Cleaner — {% if preview %}Preview{% else %}Review{% endif %}</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: system-ui, -apple-system, sans-serif;
  background: #0d0d0d; color: #ddd;
  max-width: 1300px; margin: 0 auto; padding: 24px 20px;
}
h1 { font-size: 1.4em; color: #fff; margin-bottom: 16px; }
h2 { font-size: 1.05em; font-weight: 600; padding-bottom: 6px;
     border-bottom: 1px solid #222; margin-bottom: 12px; }
.group-a h2 { color: #ff6b6b; }
.group-b h2 { color: #ffd93d; }
.group-a, .group-b { margin-bottom: 32px; }

.banner {
  border-radius: 6px; padding: 12px 18px; margin-bottom: 20px;
  font-size: 0.95em; font-weight: 600;
}
.banner-preview {
  background: #1a1a00; border: 1px solid #4a4a00; color: #ffd93d;
}
.banner-recycled {
  background: #001a00; border: 1px solid #004a00; color: #6bffb0;
}

.summary {
  display: flex; gap: 24px; flex-wrap: wrap;
  background: #151515; border: 1px solid #2a2a2a;
  border-radius: 6px; padding: 12px 18px; margin-bottom: 24px;
  font-size: 0.95em;
}
.summary .seg-a { color: #ff6b6b; }
.summary .seg-b { color: #ffd93d; }
.summary span { color: #888; margin: 0 6px; }

table { width: 100%; border-collapse: collapse; font-size: 0.92em; }
th {
  text-align: left; padding: 7px 10px;
  background: #181818; border-bottom: 2px solid #2a2a2a;
  color: #aaa; font-weight: 600; white-space: nowrap;
}
td { padding: 6px 10px; border-bottom: 1px solid #1a1a1a; vertical-align: top; }
tr:hover td { background: #161616; }
tr.deleted td { opacity: 0.35; text-decoration: line-through; }

.t-type { color: #666; font-size: 0.82em; white-space: nowrap; }
.t-name { font-weight: 500; color: #eee; white-space: pre; }
.t-name.is-folder { color: #ffd27f; }
.t-name .indent { color: #444; }
.t-check { width: 28px; text-align: center; }
.t-check input { width: 15px; height: 15px; cursor: pointer; }
.t-size { font-family: monospace; color: #aaa; white-space: nowrap; text-align: right; }
.t-reason { color: #777; font-size: 0.85em; max-width: 400px; line-height: 1.4; }
.t-conf  { font-family: monospace; color: #666; text-align: right; white-space: nowrap; }
.t-rec   { white-space: nowrap; font-size: 0.88em; }

.rec-likely_garbage { color: #ff6b6b; }
.rec-likely_keep    { color: #6bffb0; }
.rec-needs_review   { color: #ffd93d; }

.btn {
  border: none; border-radius: 4px; cursor: pointer;
  font-size: 0.88em; padding: 5px 12px;
}
.btn-open {
  background: #1e1e3a; color: #8888ff;
  transition: background 0.15s;
}
.btn-open:hover { background: #2a2a5a; }

.topbar {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 14px;
  background: #0d0d0d; border-bottom: 1px solid #2a2a2a;
  padding: 12px 0; margin-bottom: 20px;
}
.btn-confirm {
  background: #3a0000; color: #ff6b6b; font-size: 0.95em; padding: 9px 20px;
  transition: background 0.15s;
}
.btn-confirm:hover:not(:disabled) { background: #600000; }
.btn-confirm:disabled { background: #1a1a1a; color: #555; cursor: default; }
#sel-count { color: #999; font-size: 0.9em; }

.actions {
  display: flex; align-items: center; gap: 12px;
  padding: 16px 0; margin-top: 8px;
  border-top: 1px solid #1e1e1e;
}
.btn-close {
  background: #222; color: #aaa;
  padding: 9px 24px; font-size: 1em;
  transition: background 0.15s;
}
.btn-close:hover { background: #2e2e2e; }
#status { margin-left: auto; color: #777; font-size: 0.9em; }
</style>
</head>
<body>

<h1>Garbage Cleaner — {% if preview %}Dry Run Preview{% else %}Review{% endif %}</h1>

{% if preview %}
<div class="banner banner-preview">
  DRY RUN — nothing will be deleted. This is a read-only preview of what would happen.
</div>
{% else %}
<div class="banner banner-recycled">
  Nothing is recycled until you click <strong>Delete selected</strong>.
  Group A (rule-confirmed) is pre-checked — uncheck anything you want to keep.
</div>
{% endif %}

<div class="summary">
  <div class="seg-a">
    Group A: <strong>{{ group_a | length }}</strong> item(s)
    — {{ group_a | sum(attribute="size_bytes") | fmt_size }} reclaimable
  </div>
  <span>|</span>
  <div class="seg-b">
    Group B: <strong>{{ group_b | length }}</strong> item(s)
    — {{ group_b | sum(attribute="size_bytes") | fmt_size }} potential
  </div>
</div>

{% if not preview and (group_a or group_b) %}
<div class="topbar">
  <button class="btn btn-confirm" id="delete-selected" onclick="deleteSelected()" disabled>
    Delete selected
  </button>
  <span id="sel-count">0 selected</span>
</div>
{% endif %}

<!-- ── Group A ───────────────────────────────────────────────────── -->
<div class="group-a">
  <h2>Group A — Rule-confirmed Garbage{% if not preview %} (pre-selected — uncheck to keep){% endif %}</h2>
  {% if group_a %}
  <table>
    <thead>
      <tr>
        {% if not preview %}<th class="t-check"><input type="checkbox" class="check-all" checked onclick="toggleAll(this)"></th>{% endif %}
        <th class="t-type">Type</th>
        <th>Name</th>
        <th class="t-size">Size</th>
        <th>Rule</th>
      </tr>
    </thead>
    <tbody>
      {% for r in group_a %}
      <tr>
        {% if not preview %}
        <td class="t-check">
          <input type="checkbox" class="row-check" data-path="{{ r.path }}" checked onchange="updateCount()">
        </td>
        {% endif %}
        <td class="t-type">{{ r.type }}</td>
        <td class="t-name {% if r.type == 'folder' %}is-folder{% endif %}"><span class="indent">{{ "  " * r.indent }}</span>{{ r.name }}{% if r.type == "folder" %}/{% endif %}</td>
        <td class="t-size">{{ r.size_bytes | fmt_size }}</td>
        <td class="t-reason">{{ r.reason }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="color:#444; padding: 8px 0;">None</p>
  {% endif %}
</div>

<!-- ── Group B ───────────────────────────────────────────────────── -->
<div class="group-b">
  <h2>Group B — Needs Human Decision (AI-advised)</h2>
  {% if group_b %}
  <table>
    <thead>
      <tr>
        {% if not preview %}<th class="t-check"><input type="checkbox" class="check-all" onclick="toggleAll(this)"></th>{% endif %}
        <th class="t-type">Type</th>
        <th>Name</th>
        <th class="t-size">Size</th>
        <th>AI Rec</th>
        <th class="t-conf">Conf</th>
        <th>Reason</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for r in group_b %}
      <tr id="row-{{ loop.index }}">
        {% if not preview %}
        <td class="t-check">
          <input type="checkbox" class="row-check" data-path="{{ r.path }}" onchange="updateCount()">
        </td>
        {% endif %}
        <td class="t-type">{{ r.type }}</td>
        <td class="t-name {% if r.type == 'folder' %}is-folder{% endif %}"><span class="indent">{{ "  " * r.indent }}</span>{{ r.name }}{% if r.type == "folder" %}/{% endif %}</td>
        <td class="t-size">{{ r.size_bytes | fmt_size }}</td>
        <td class="t-rec rec-{{ r.recommendation }}">{{ r.recommendation }}</td>
        <td class="t-conf">
          {% if r.confidence is not none %}{{ "%.0f%%" | format(r.confidence * 100) }}
          {% else %}—{% endif %}
        </td>
        <td class="t-reason">{{ r.reason }}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-open" data-path="{{ r.path }}"
            onclick="openPath(this)">Open</button>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p style="color:#444; padding: 8px 0;">None</p>
  {% endif %}
</div>

<div class="actions">
  {% if preview %}
  <button class="btn btn-close" onclick="closeUI()">Close Preview</button>
  {% else %}
  <button class="btn btn-close" onclick="closeUI()">Done — Close</button>
  {% endif %}
  <span id="status"></span>
</div>

<script>
function openPath(btn) {
  const path = btn.dataset.path;
  fetch("/open-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path })
  }).catch(console.error);
}

function checkedBoxes() {
  return Array.from(document.querySelectorAll(".row-check:checked:not(:disabled)"));
}

function updateCount() {
  const n = checkedBoxes().length;
  const label = document.getElementById("sel-count");
  const btn = document.getElementById("delete-selected");
  if (label) label.textContent = n + " selected";
  if (btn) btn.disabled = n === 0;
}

function toggleAll(master) {
  // Toggle only the checkboxes in the same table as this header checkbox.
  master.closest("table").querySelectorAll(".row-check:not(:disabled)")
    .forEach(c => { c.checked = master.checked; });
  updateCount();
}

function deleteSelected() {
  const boxes = checkedBoxes();
  if (boxes.length === 0) return;
  const paths = boxes.map(c => c.dataset.path);
  if (!confirm(
    "Send " + paths.length + " item(s) to the Recycle Bin?\\n\\n" +
    "This is reversible — you can restore from the Recycle Bin."
  )) return;

  const btn = document.getElementById("delete-selected");
  btn.disabled = true;
  btn.textContent = "Deleting…";
  document.getElementById("status").textContent = "";

  fetch("/delete-items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths })
  })
  .then(r => r.json())
  .then(data => {
    btn.textContent = "Delete selected";
    if (data.ok) {
      boxes.forEach(c => {
        c.checked = false;
        c.disabled = true;
        c.closest("tr").classList.add("deleted");
      });
      document.querySelectorAll(".check-all").forEach(m => { m.checked = false; });
      updateCount();
      document.getElementById("status").textContent =
        data.recycled + " item(s) sent to Recycle Bin.";
    } else {
      btn.disabled = false;
      document.getElementById("status").textContent = "Error: " + data.error;
    }
  })
  .catch(err => {
    btn.textContent = "Delete selected";
    btn.disabled = false;
    document.getElementById("status").textContent = "Error: " + err;
  });
}

function closeUI() {
  fetch("/close", { method: "POST" }).catch(console.error);
  document.body.innerHTML =
    '<p style="color:#666; padding:60px; text-align:center; font-size:1.1em">Closed. You can close this tab.</p>';
}

// Reflect any pre-checked rows (Group A) in the button/count on first load.
updateCount();
</script>
</body>
</html>"""


# ── Size formatter (registered as a Jinja2 filter) ───────────────────────────

def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── Public entry point ────────────────────────────────────────────────────────

def launch(group_a: list[dict], group_b: list[dict], preview: bool = False) -> None:
    """
    Start a local Flask server and open the review UI in the default browser.

    preview=True  (--dry-run): read-only view of Group A + B, nothing deleted.
    preview=False (regular):   nothing is recycled up front. Both groups show
                               checkboxes (Group A pre-checked, Group B
                               unchecked); the single "Delete selected" button
                               recycles all checked items. Blocks until the user
                               clicks Done/Close — closing without confirming
                               deletes nothing. Group A is unaffected by close.
    """
    from actions import run_actions

    app = Flask(__name__)
    app.jinja_env.filters["fmt_size"] = _fmt_size

    _done = threading.Event()
    _by_path: dict[str, dict] = {r["path"]: r for r in group_a + group_b}
    _valid_paths: set[str] = set(_by_path)

    @app.route("/")
    def index():
        return render_template_string(_HTML, group_a=group_a, group_b=group_b, preview=preview)

    @app.route("/open-path", methods=["POST"])
    def open_path():
        path = (request.get_json() or {}).get("path", "")
        p = Path(path)
        if path in _valid_paths and p.exists():
            # For a loose file, reveal its containing folder rather than
            # launching the file itself.
            os.startfile(str(p if p.is_dir() else p.parent))
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "path not in review set or not found"}), 400

    @app.route("/delete-items", methods=["POST"])
    def delete_items():
        if preview:
            return jsonify({"ok": False, "error": "dry-run mode — deletion disabled"}), 403
        paths = (request.get_json() or {}).get("paths", [])
        if not isinstance(paths, list) or not paths:
            return jsonify({"ok": False, "error": "no paths supplied"}), 400

        items: list[dict] = []
        for path in paths:
            item = _by_path.get(path)
            if item is None:
                return jsonify({"ok": False, "error": f"path not in review set: {path}"}), 400
            items.append(item)

        try:
            run_actions(items, dry_run=False)
            for it in items:
                _log.info("DELETED VIA UI: %s", it["path"])
            return jsonify({"ok": True, "recycled": len(items)})
        except Exception as exc:
            _log.error("Failed to delete selection: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/close", methods=["POST"])
    def close():
        threading.Thread(target=lambda: (_delay(0.5), _done.set()), daemon=True).start()
        return jsonify({"ok": True})

    import werkzeug.serving
    server = werkzeug.serving.make_server("127.0.0.1", 0, app, threaded=True)
    actual_port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    mode_label = "preview (dry-run)" if preview else "review"
    url = f"http://127.0.0.1:{actual_port}"
    print(f"Web UI [{mode_label}]: {url}")
    webbrowser.open(url)

    try:
        _done.wait()
    except KeyboardInterrupt:
        print("\nInterrupted — closing.")
    finally:
        server.shutdown()


def _delay(seconds: float) -> None:
    time.sleep(seconds)
