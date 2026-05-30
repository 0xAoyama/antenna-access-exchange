from flask import Flask, request, redirect, url_for, render_template_string, abort, jsonify, Response
import sqlite3
from datetime import datetime
import os
import secrets

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("AAE_DB_PATH", os.path.join(BASE_DIR, "data", "data.db"))
ADMIN_TOKEN = os.environ.get("AAE_ADMIN_TOKEN", "admin123")


def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def init_db():
    conn = db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            site_url TEXT NOT NULL,
            contact TEXT NOT NULL,
            category TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            api_token TEXT,
            is_active INTEGER NOT NULL DEFAULT 0,
            impressions_out INTEGER NOT NULL DEFAULT 0,
            impressions_in INTEGER NOT NULL DEFAULT 0,
            click_out INTEGER NOT NULL DEFAULT 0,
            click_in INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        )
        """
    )

    columns = {r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()}
    migrations = [
        ("api_token", "ALTER TABLE applications ADD COLUMN api_token TEXT"),
        ("is_active", "ALTER TABLE applications ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0"),
        ("impressions_out", "ALTER TABLE applications ADD COLUMN impressions_out INTEGER NOT NULL DEFAULT 0"),
        ("impressions_in", "ALTER TABLE applications ADD COLUMN impressions_in INTEGER NOT NULL DEFAULT 0"),
        ("click_out", "ALTER TABLE applications ADD COLUMN click_out INTEGER NOT NULL DEFAULT 0"),
        ("click_in", "ALTER TABLE applications ADD COLUMN click_in INTEGER NOT NULL DEFAULT 0"),
        ("updated_at", "ALTER TABLE applications ADD COLUMN updated_at TEXT"),
    ]
    for col, ddl in migrations:
        if col not in columns:
            conn.execute(ddl)

    conn.commit()
    conn.close()


def require_admin(token):
    if token != ADMIN_TOKEN:
        abort(403, "token不正")


def get_site_by_token(conn, token):
    if not token:
        return None
    return conn.execute(
        "SELECT * FROM applications WHERE api_token=? AND status='approved' AND is_active=1",
        (token,),
    ).fetchone()


def choose_target(conn, requester_id):
    candidates = conn.execute(
        """
        SELECT *
        FROM applications
        WHERE status='approved' AND is_active=1 AND id != ?
        ORDER BY (click_out - click_in) DESC, impressions_in ASC, id ASC
        """,
        (requester_id,),
    ).fetchall()
    if not candidates:
        return None
    return candidates[0]


HOME_TMPL = """
<!doctype html>
<html lang='ja'>
<head>
  <meta charset='UTF-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Founding 30 申請</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1020px; margin: 24px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    input, textarea, select { width: 100%; padding: 10px; margin-top: 4px; margin-bottom: 12px; }
    button { background:#111; color:#fff; border:none; padding:10px 16px; border-radius:8px; cursor:pointer; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    .small { color: #555; font-size: 12px; }
    code { background:#f6f6f6; padding:2px 6px; border-radius:4px; }
  </style>
</head>
<body>
  <h1>アンテナアクセス交換 Founding 30 申請</h1>
  <p>申請→承認→有効化で、アクセス交換ウィジェットが使えます。</p>

  <div class='card'>
    <h2>サイト申請フォーム</h2>
    <form method='post' action='/apply'>
      <label>サイト名<input name='site_name' required></label>
      <label>サイトURL<input name='site_url' type='url' placeholder='https://example.com' required></label>
      <label>連絡先（メール or X）<input name='contact' required></label>
      <label>カテゴリ
        <select name='category' required>
          <option value='IT・SaaS・AI'>IT・SaaS・AI</option>
          <option value='ガジェット'>ガジェット</option>
          <option value='その他'>その他</option>
        </select>
      </label>
      <label>補足メモ<textarea name='note' rows='4'></textarea></label>
      <button type='submit'>申請する</button>
    </form>
  </div>

  <div class='card'>
    <h2>公開掲載一覧（承認済み）</h2>
    <table>
      <thead><tr><th>ID</th><th>サイト名</th><th>URL</th><th>カテゴリ</th><th>状態</th><th>登録日</th></tr></thead>
      <tbody>
      {% for r in approved %}
      <tr>
        <td>{{ r['id'] }}</td>
        <td>{{ r['site_name'] }}</td>
        <td><a href='{{ r['site_url'] }}' target='_blank'>{{ r['site_url'] }}</a></td>
        <td>{{ r['category'] }}</td>
        <td>{% if r['is_active'] == 1 %}交換有効{% else %}承認済み{% endif %}</td>
        <td>{{ r['created_at'] }}</td>
      </tr>
      {% else %}
      <tr><td colspan='6'>まだ承認済みサイトはありません</td></tr>
      {% endfor %}
      </tbody>
    </table>
  </div>

  <div class='card'>
    <h2>交換ウィジェット導入（承認 + 有効化後）</h2>
    <p class='small'>管理画面で発行された token を使って、以下をサイトに埋め込みます。</p>
    <code>&lt;script src="/widget.js?token=YOUR_TOKEN"&gt;&lt;/script&gt;</code>
  </div>

  <p class='small'>管理画面: /admin?token=... </p>
</body>
</html>
"""

ADMIN_TMPL = """
<!doctype html>
<html lang='ja'>
<head>
  <meta charset='UTF-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>管理画面</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1400px; margin: 24px auto; padding: 0 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 13px; }
    form.inline { display:inline; margin-right: 6px; }
    button { border:none; padding:6px 10px; border-radius:6px; cursor:pointer; }
    code { background:#f6f6f6; padding:2px 4px; border-radius:4px; }
  </style>
</head>
<body>
  <h1>申請管理 + 交換ダッシュボード</h1>
  <table>
    <thead>
      <tr>
        <th>ID</th><th>サイト名</th><th>URL</th><th>連絡先</th><th>カテゴリ</th>
        <th>状態</th><th>有効</th><th>token</th>
        <th>imp_out</th><th>imp_in</th><th>click_out</th><th>click_in</th><th>収支(click_in-click_out)</th><th>操作</th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{ r['id'] }}</td>
        <td>{{ r['site_name'] }}</td>
        <td><a href='{{ r['site_url'] }}' target='_blank'>{{ r['site_url'] }}</a></td>
        <td>{{ r['contact'] }}</td>
        <td>{{ r['category'] }}</td>
        <td>{{ r['status'] }}</td>
        <td>{{ r['is_active'] }}</td>
        <td>{% if r['api_token'] %}<code>{{ r['api_token'] }}</code>{% endif %}</td>
        <td>{{ r['impressions_out'] }}</td>
        <td>{{ r['impressions_in'] }}</td>
        <td>{{ r['click_out'] }}</td>
        <td>{{ r['click_in'] }}</td>
        <td>{{ r['click_in'] - r['click_out'] }}</td>
        <td>
          <form class='inline' method='post' action='/admin/update'>
            <input type='hidden' name='token' value='{{ token }}'>
            <input type='hidden' name='id' value='{{ r['id'] }}'>
            <input type='hidden' name='status' value='approved'>
            <button style='background:#0a7;color:#fff;'>承認</button>
          </form>
          <form class='inline' method='post' action='/admin/update'>
            <input type='hidden' name='token' value='{{ token }}'>
            <input type='hidden' name='id' value='{{ r['id'] }}'>
            <input type='hidden' name='status' value='rejected'>
            <button style='background:#c33;color:#fff;'>却下</button>
          </form>
          <form class='inline' method='post' action='/admin/activate'>
            <input type='hidden' name='token' value='{{ token }}'>
            <input type='hidden' name='id' value='{{ r['id'] }}'>
            <button style='background:#1463ff;color:#fff;'>有効化/再発行</button>
          </form>
          <form class='inline' method='post' action='/admin/toggle'>
            <input type='hidden' name='token' value='{{ token }}'>
            <input type='hidden' name='id' value='{{ r['id'] }}'>
            <input type='hidden' name='is_active' value='{% if r['is_active']==1 %}0{% else %}1{% endif %}'>
            <button style='background:#444;color:#fff;'>{% if r['is_active']==1 %}無効化{% else %}有効化ON{% endif %}</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</body>
</html>
"""


@app.get("/")
def home():
    conn = db()
    approved = conn.execute(
        "SELECT id, site_name, site_url, category, status, is_active, created_at FROM applications WHERE status='approved' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template_string(HOME_TMPL, approved=approved)


@app.post("/apply")
def apply():
    payload = {
        "site_name": request.form.get("site_name", "").strip(),
        "site_url": request.form.get("site_url", "").strip(),
        "contact": request.form.get("contact", "").strip(),
        "category": request.form.get("category", "").strip(),
        "note": request.form.get("note", "").strip(),
        "created_at": now_iso(),
    }
    if not all([payload["site_name"], payload["site_url"], payload["contact"], payload["category"]]):
        abort(400, "必須項目が不足しています")

    conn = db()
    conn.execute(
        "INSERT INTO applications(site_name, site_url, contact, category, note, status, created_at, updated_at) VALUES(?,?,?,?,?,'pending',?,?)",
        (payload["site_name"], payload["site_url"], payload["contact"], payload["category"], payload["note"], payload["created_at"], payload["created_at"]),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("home"))


@app.get("/admin")
def admin():
    token = request.args.get("token", "")
    require_admin(token)
    conn = db()
    rows = conn.execute("SELECT * FROM applications ORDER BY id DESC").fetchall()
    conn.close()
    return render_template_string(ADMIN_TMPL, rows=rows, token=token)


@app.post("/admin/update")
def admin_update():
    token = request.form.get("token", "")
    require_admin(token)
    app_id = request.form.get("id", "")
    status = request.form.get("status", "")
    if status not in {"approved", "rejected", "pending"}:
        abort(400, "status不正")
    conn = db()
    conn.execute("UPDATE applications SET status=?, updated_at=? WHERE id=?", (status, now_iso(), app_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin", token=token))


@app.post("/admin/activate")
def admin_activate():
    token = request.form.get("token", "")
    require_admin(token)
    app_id = request.form.get("id", "")
    conn = db()
    row = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    if not row:
        conn.close()
        abort(404, "site not found")
    if row["status"] != "approved":
        conn.close()
        abort(400, "承認済みのみ有効化可能")

    api_token = secrets.token_urlsafe(16)
    conn.execute(
        "UPDATE applications SET api_token=?, is_active=1, updated_at=? WHERE id=?",
        (api_token, now_iso(), app_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin", token=token))


@app.post("/admin/toggle")
def admin_toggle():
    token = request.form.get("token", "")
    require_admin(token)
    app_id = request.form.get("id", "")
    is_active = request.form.get("is_active", "0")
    if is_active not in {"0", "1"}:
        abort(400, "is_active不正")
    conn = db()
    conn.execute("UPDATE applications SET is_active=?, updated_at=? WHERE id=?", (int(is_active), now_iso(), app_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin", token=token))


@app.get("/serve")
def serve():
    token = request.args.get("token", "")
    conn = db()
    requester = get_site_by_token(conn, token)
    if not requester:
        conn.close()
        return jsonify({"ok": False, "error": "invalid token or inactive site"}), 403

    target = choose_target(conn, requester["id"])
    if not target:
        conn.close()
        return jsonify({"ok": False, "error": "no available target"}), 404

    conn.execute(
        "UPDATE applications SET impressions_out=impressions_out+1, updated_at=? WHERE id=?",
        (now_iso(), requester["id"]),
    )
    conn.execute(
        "UPDATE applications SET impressions_in=impressions_in+1, updated_at=? WHERE id=?",
        (now_iso(), target["id"]),
    )
    conn.commit()

    click_url = url_for("click_redirect", target_id=target["id"], _external=True) + f"?from={requester['id']}"
    payload = {
        "ok": True,
        "target": {
            "id": target["id"],
            "site_name": target["site_name"],
            "site_url": target["site_url"],
            "category": target["category"],
            "click_url": click_url,
        },
    }
    conn.close()
    return jsonify(payload)


@app.get("/c/<int:target_id>")
def click_redirect(target_id):
    from_id = request.args.get("from", "")
    conn = db()
    target = conn.execute("SELECT * FROM applications WHERE id=? AND status='approved'", (target_id,)).fetchone()
    if not target:
        conn.close()
        abort(404, "target not found")

    if from_id.isdigit():
        requester = conn.execute("SELECT * FROM applications WHERE id=?", (int(from_id),)).fetchone()
        if requester:
            conn.execute("UPDATE applications SET click_out=click_out+1, updated_at=? WHERE id=?", (now_iso(), requester["id"]))
    conn.execute("UPDATE applications SET click_in=click_in+1, updated_at=? WHERE id=?", (now_iso(), target["id"]))
    conn.commit()
    conn.close()
    return redirect(target["site_url"], code=302)


@app.get("/widget.js")
def widget_js():
    token = request.args.get("token", "")
    if not token:
        return Response("console.error('token required');", mimetype="application/javascript")

    js = f"""
(function() {{
  var base = window.location.origin;
  var token = {token!r};
  fetch(base + '/serve?token=' + encodeURIComponent(token))
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      if (!data.ok) return;
      var box = document.createElement('div');
      box.style.border = '1px solid #ddd';
      box.style.padding = '10px';
      box.style.margin = '12px 0';
      box.style.borderRadius = '8px';
      box.style.fontFamily = 'sans-serif';
      box.innerHTML = '<strong>おすすめサイト</strong><br>' +
                      '<a href="' + data.target.click_url + '" target="_blank" rel="noopener">' +
                      data.target.site_name + ' (' + data.target.category + ')' +
                      '</a>';
      document.currentScript.parentNode.insertBefore(box, document.currentScript);
    }})
    .catch(function(err) {{ console.error('widget error', err); }});
}})();
"""
    return Response(js, mimetype="application/javascript")


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": now_iso()}


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=False)
