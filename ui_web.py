import os
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

# ── HTML template ─────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Garbage Cleaner — Review</title>
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

.t-type { color: #666; font-size: 0.82em; white-space: nowrap; }
.t-name { font-weight: 500; color: #eee; }
.t-size { font-family: monospace; color: #aaa; white-space: nowrap; text-align: right; }
.t-reason { color: #777; font-size: 0.85em; max-width: 400px; line-height: 1.4; }
.t-conf  { font-family: monospace; color: #666; text-align: right; white-space: nowrap; }
.t-rec   { white-space: nowrap; font-size: 0.88em; }

.rec-likely_garbage { color: #ff6b6b; }
.rec-likely_keep    { color: #6bffb0; }
.rec-needs_review   { color: #ffd93d; }

input[type=checkbox] { width: 15px; height: 15px; cursor: pointer; accent-color: #ff6b6b; }

.btn {
  border: none; border-radius: 4px; cursor: pointer;
  font-size: 0.88em; padding: 5px 12px;
}
.btn-open {
  background: #1e1e3a; color: #8888ff;
  transition: background 0.15s;
}
.btn-open:hover { background: #2a2a5a; }

.toolbar {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 10px; font-size: 0.88em; color: #777;
}
.toolbar label { cursor: pointer; display: flex; align-items: center; gap: 6px; }

.actions {
  display: flex; align-items: center; gap: 12px;
  padding: 16px 0; margin-top: 8px;
  border-top: 1px solid #1e1e1e;
}
.btn-confirm {
  background: #8b0000; color: #fff;
  padding: 9px 28px; font-size: 1em; font-weight: 600;
  transition: background 0.15s;
}
.btn-confirm:hover { background: #b00000; }
.btn-cancel {
  background: #222; color: #888;
  padding: 9px 20px; font-size: 1em;
  transition: background 0.15s;
}
.btn-cancel:hover { background: #2e2e2e; }
#status { margin-left: auto; color: #777; font-size: 0.9em; }

#done-msg {
  display: none; padding: 28px; border-radius: 8px;
  text-align: center; font-size: 1.05em; line-height: 1.6;
}
.done-confirmed { background: #0a1f0a; border: 1px solid #1a4a1a; color: #6bffb0; }
.done-cancelled { background: #1a1a1a; border: 1px solid #2a2a2a; color: #666; }
</style>
</head>
<body>

<h1>Garbage Cleaner — Review</h1>

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

<div id="main-ui">

  <!-- ── Group A ─────────────────────────────────────────────────── -->
  <div class="group-a">
    <h2>Group A — Confirmed Garbage (rule-based, always deleted on confirm)</h2>
    {% if group_a %}
    <table>
      <thead>
        <tr>
          <th class="t-type">Type</th>
          <th>Name</th>
          <th class="t-size">Size</th>
          <th>Rule</th>
        </tr>
      </thead>
      <tbody>
        {% for r in group_a %}
        <tr>
          <td class="t-type">{{ r.type }}</td>
          <td class="t-name">{{ r.name }}</td>
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

  <!-- ── Group B ─────────────────────────────────────────────────── -->
  <div class="group-b">
    <h2>Group B — Needs Human Decision (AI-advised)</h2>
    {% if group_b %}
    <div class="toolbar">
      <label>
        <input type="checkbox" id="select-all"> Select all
      </label>
      <span style="color:#3a3a3a">|</span>
      <label>
        <input type="checkbox" id="select-garbage"> Auto-select likely_garbage
      </label>
    </div>
    <table>
      <thead>
        <tr>
          <th></th>
          <th class="t-type">Type</th>
          <th>Name</th>
          <th class="t-size">Size</th>
          <th>AI Recommendation</th>
          <th class="t-conf">Conf</th>
          <th>Reason</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {% for r in group_b %}
        <tr>
          <td><input type="checkbox" class="row-check" data-path="{{ r.path }}"
            data-rec="{{ r.recommendation }}"></td>
          <td class="t-type">{{ r.type }}</td>
          <td class="t-name">{{ r.name }}</td>
          <td class="t-size">{{ r.size_bytes | fmt_size }}</td>
          <td class="t-rec rec-{{ r.recommendation }}">{{ r.recommendation }}</td>
          <td class="t-conf">
            {% if r.confidence is not none %}{{ "%.0f%%" | format(r.confidence * 100) }}
            {% else %}—{% endif %}
          </td>
          <td class="t-reason">{{ r.reason }}</td>
          <td>
            {% if r.type == "folder" %}
            <button class="btn btn-open" data-path="{{ r.path }}"
              onclick="openPath(this)">Open folder</button>
            {% endif %}
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
    <button class="btn btn-confirm" onclick="confirmDeletion()">Confirm Deletion</button>
    <button class="btn btn-cancel" onclick="cancelDeletion()">Cancel</button>
    <span id="status"></span>
  </div>

</div>

<div id="done-msg"></div>

<script>
const selectAll = document.getElementById("select-all");
const selectGarbage = document.getElementById("select-garbage");

selectAll?.addEventListener("change", () => {
  document.querySelectorAll(".row-check").forEach(cb => cb.checked = selectAll.checked);
  if (selectGarbage) selectGarbage.checked = false;
});

selectGarbage?.addEventListener("change", () => {
  document.querySelectorAll(".row-check").forEach(cb => {
    cb.checked = selectGarbage.checked && cb.dataset.rec === "likely_garbage";
  });
  if (selectAll) selectAll.checked = false;
});

function openPath(btn) {
  const path = btn.dataset.path;
  fetch("/open-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path })
  }).catch(console.error);
}

function confirmDeletion() {
  const selected = Array.from(document.querySelectorAll(".row-check:checked"))
    .map(cb => cb.dataset.path);
  const groupACount = {{ group_a | length }};
  const total = groupACount + selected.length;

  if (!confirm(
    `Send ${total} item(s) to the Recycle Bin?\\n\\n` +
    `  Group A (rule-confirmed): ${groupACount}\\n` +
    `  Group B (your selection): ${selected.length}\\n\\n` +
    `This is reversible — items go to the Recycle Bin, not permanent delete.`
  )) return;

  document.getElementById("status").textContent = "Sending to Recycle Bin…";
  fetch("/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected })
  })
  .then(r => r.json())
  .then(() => {
    document.getElementById("main-ui").style.display = "none";
    const msg = document.getElementById("done-msg");
    msg.className = "done-confirmed";
    msg.innerHTML = `
      <strong>Done!</strong> ${total} item(s) sent to the Recycle Bin.<br>
      <span style="color:#4a9a6a; font-size:0.9em">
        Undo: open the Recycle Bin and restore items.
      </span><br><br>
      <span style="color:#3a6a3a; font-size:0.85em">You can close this tab.</span>`;
    msg.style.display = "block";
  })
  .catch(err => {
    document.getElementById("status").textContent = "Error: " + err;
  });
}

function cancelDeletion() {
  fetch("/cancel", { method: "POST" }).catch(console.error);
  document.getElementById("main-ui").style.display = "none";
  const msg = document.getElementById("done-msg");
  msg.className = "done-cancelled";
  msg.innerHTML = `Cancelled — nothing was deleted.<br>
    <span style="font-size:0.85em">You can close this tab.</span>`;
  msg.style.display = "block";
}
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

def launch(group_a: list[dict], group_b: list[dict]) -> list[dict]:
    """
    Start a local Flask server, open the review UI in the default browser,
    and block until the user confirms or cancels.  Returns the list of
    confirmed items (Group A + selected Group B), or [] on cancel.
    """
    app = Flask(__name__)
    app.jinja_env.filters["fmt_size"] = _fmt_size

    _result: dict = {"confirmed": None}
    _done = threading.Event()

    @app.route("/")
    def index():
        return render_template_string(_HTML, group_a=group_a, group_b=group_b)

    @app.route("/open-path", methods=["POST"])
    def open_path():
        path = (request.get_json() or {}).get("path", "")
        if path and Path(path).exists():
            os.startfile(path)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "path not found"}), 400

    @app.route("/confirm", methods=["POST"])
    def confirm():
        data = request.get_json() or {}
        selected = set(data.get("selected", []))
        confirmed_b = [r for r in group_b if r["path"] in selected]
        _result["confirmed"] = group_a + confirmed_b
        # Delay shutdown so the HTTP response reaches the browser first.
        threading.Thread(
            target=lambda: (_delay(0.5), _done.set()), daemon=True
        ).start()
        return jsonify({"ok": True})

    @app.route("/cancel", methods=["POST"])
    def cancel():
        _result["confirmed"] = []
        threading.Thread(
            target=lambda: (_delay(0.5), _done.set()), daemon=True
        ).start()
        return jsonify({"ok": True})

    import werkzeug.serving
    server = werkzeug.serving.make_server("127.0.0.1", 5000, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = "http://127.0.0.1:5000"
    print(f"Web UI: {url}  (waiting for your decision in the browser…)")
    webbrowser.open(url)

    _done.wait()
    server.shutdown()

    return _result["confirmed"] or []


def _delay(seconds: float) -> None:
    time.sleep(seconds)
