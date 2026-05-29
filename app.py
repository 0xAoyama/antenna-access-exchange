from flask import Flask, request, redirect, url_for, render_template_string, abort
import sqlite3
from datetime import datetime
import os

app = Flask(__name__)
DB_PATH = os.environ.get("AAE_DB_PATH", "/workspace/antenna-access-exchange/data.db")
ADMIN_TOKEN = os.environ.get("AAE_ADMIN_TOKEN", "admin123")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


HOME_TMPL = """
<!doctype html>
<html lang='ja'>
<head>
  <meta charset='UTF-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>Founding 30 申請</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 920px; margin: 24px auto; padding: 0 16px; }
    .card { border: 1px solid #ddd; border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    input, textarea, select { width: 100%; padding: 10px; margin-top: 4px; margin-bottom: 12px; }
    button { background:#111; color:#fff; border:none; padding:10px 16px; border-radius:8px; cursor:pointer; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    .small { color: #555; font-size: 12px; }
  </style>
</head>
<body>
  <h1>アンテナアクセス交換 Founding 30 申請</h1>
  <p>初期参加サイトを募集しています。申請後24h以内に審査ステータスを更新します。</p>

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
      <thead><tr><th>ID</th><th>サイト名</th><th>URL</th><th>カテゴリ</th><th>登録日</th></tr></thead>
      <tbody>
      {% for r in approved %}
      <tr>
        <td>{{ r['id'] }}</td>
        <td>{{ r['site_name'] }}</td>
        <td><a href='{{ r['site_url'] }}' target='_blank'>{{ r['site_url'] }}</a></td>
        <td>{{ r['category'] }}</td>
        <td>{{ r['created_at'] }}</td>
      </tr>
      {% else %}
      <tr><td colspan='5'>まだ承認済みサイトはありません</td></tr>
      {% endfor %}
      </tbody>
    </table>
    <p class='small'>管理画面: /admin?token=... </p>
  </div>
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
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    form.inline { display:inline; margin-right: 6px; }
    button { border:none; padding:6px 10px; border-radius:6px; cursor:pointer; }
  </style>
</head>
<body>
  <h1>申請管理</h1>
  <table>
    <thead><tr><th>ID</th><th>サイト名</th><th>URL</th><th>連絡先</th><th>カテゴリ</th><th>状態</th><th>操作</th></tr></thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{ r['id'] }}</td>
        <td>{{ r['site_name'] }}</td>
        <td><a href='{{ r['site_url'] }}' target='_blank'>{{ r['site_url'] }}</a></td>
        <td>{{ r['contact'] }}</td>
        <td>{{ r['category'] }}</td>
        <td>{{ r['status'] }}</td>
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
        "SELECT id, site_name, site_url, category, created_at FROM applications WHERE status='approved' ORDER BY id DESC"
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
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    if not all([payload["site_name"], payload["site_url"], payload["contact"], payload["category"]]):
        abort(400, "必須項目が不足しています")

    conn = db()
    conn.execute(
        "INSERT INTO applications(site_name, site_url, contact, category, note, status, created_at) VALUES(?,?,?,?,?,'pending',?)",
        (payload["site_name"], payload["site_url"], payload["contact"], payload["category"], payload["note"], payload["created_at"]),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("home"))


@app.get("/admin")
def admin():
    token = request.args.get("token", "")
    if token != ADMIN_TOKEN:
        abort(403, "token不正")
    conn = db()
    rows = conn.execute("SELECT * FROM applications ORDER BY id DESC").fetchall()
    conn.close()
    return render_template_string(ADMIN_TMPL, rows=rows, token=token)


@app.post("/admin/update")
def admin_update():
    token = request.form.get("token", "")
    if token != ADMIN_TOKEN:
        abort(403, "token不正")
    app_id = request.form.get("id", "")
    status = request.form.get("status", "")
    if status not in {"approved", "rejected", "pending"}:
        abort(400, "status不正")
    conn = db()
    conn.execute("UPDATE applications SET status=? WHERE id=?", (status, app_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin", token=token))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=False)
