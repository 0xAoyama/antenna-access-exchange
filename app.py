from flask import Flask, request, redirect, url_for, render_template_string, abort, jsonify, Response, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import hashlib
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SQLITE = "sqlite:///" + os.path.join(BASE_DIR, "data", "data.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("AAE_SECRET_KEY", "change-me-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", DEFAULT_SQLITE)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

ADMIN_USER = os.environ.get("AAE_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("AAE_ADMIN_PASSWORD", "admin123")
MAX_REQ_PER_MIN = int(os.environ.get("AAE_MAX_REQ_PER_MIN", "120"))
CLICK_DEDUP_SEC = int(os.environ.get("AAE_CLICK_DEDUP_SEC", "600"))

csrf = "csrf_token"
db = SQLAlchemy(app)


class Application(db.Model):
    __tablename__ = "applications"
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(255), nullable=False)
    site_url = db.Column(db.String(1024), nullable=False)
    contact = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(255), nullable=False)
    note = db.Column(db.Text)
    status = db.Column(db.String(32), nullable=False, default="pending")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False)

    api_token = db.Column(db.String(128), unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    is_banned = db.Column(db.Boolean, nullable=False, default=False)
    ban_reason = db.Column(db.String(255))

    impressions_out = db.Column(db.Integer, nullable=False, default=0)
    impressions_in = db.Column(db.Integer, nullable=False, default=0)
    click_out = db.Column(db.Integer, nullable=False, default=0)
    click_in = db.Column(db.Integer, nullable=False, default=0)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), nullable=False)
    actor = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(64), nullable=False)
    target_id = db.Column(db.Integer)
    ip = db.Column(db.String(128))
    ua = db.Column(db.String(512))
    detail = db.Column(db.Text)


class ClickEvent(db.Model):
    __tablename__ = "click_events"
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), nullable=False)
    from_site_id = db.Column(db.Integer, nullable=False)
    to_site_id = db.Column(db.Integer, nullable=False)
    fingerprint = db.Column(db.String(128), nullable=False, index=True)


class RequestLog(db.Model):
    __tablename__ = "request_logs"
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime(timezone=True), nullable=False)
    ip = db.Column(db.String(128), nullable=False, index=True)
    path = db.Column(db.String(255), nullable=False)


def now_utc():
    return datetime.now(timezone.utc)


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def client_ua():
    return (request.headers.get("User-Agent") or "")[:500]


def audit(action: str, target_id=None, detail=""):
    row = AuditLog(
        at=now_utc(),
        actor="admin" if session.get("is_admin") else "system",
        action=action,
        target_id=target_id,
        ip=client_ip(),
        ua=client_ua(),
        detail=detail[:4000],
    )
    db.session.add(row)
    db.session.commit()


def ensure_csrf():
    if csrf not in session:
        session[csrf] = secrets.token_urlsafe(24)


def verify_csrf_from_form():
    token = request.form.get("csrf_token", "")
    if not token or token != session.get(csrf):
        abort(403, "csrf invalid")


def is_valid_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


def require_admin_login():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login", next=request.path))


def rate_limit_ok() -> bool:
    ip = client_ip()
    now = now_utc()
    minute_ago = now - timedelta(minutes=1)
    db.session.add(RequestLog(at=now, ip=ip, path=request.path[:255]))
    db.session.flush()
    cnt = db.session.query(func.count(RequestLog.id)).filter(RequestLog.ip == ip, RequestLog.at >= minute_ago).scalar() or 0
    if cnt > MAX_REQ_PER_MIN:
        db.session.rollback()
        return False

    cutoff = now - timedelta(days=2)
    db.session.query(RequestLog).filter(RequestLog.at < cutoff).delete()
    db.session.commit()
    return True


def serve_candidate(requester_id: int):
    return (
        db.session.query(Application)
        .filter(
            Application.status == "approved",
            Application.is_active.is_(True),
            Application.is_banned.is_(False),
            Application.id != requester_id,
        )
        .order_by((Application.click_out - Application.click_in).desc(), Application.impressions_in.asc(), Application.id.asc())
        .first()
    )


HOME_TMPL = """
<!doctype html><html lang='ja'><head>
<meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>アクセス交換プラットフォーム</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:1100px;margin:24px auto;padding:0 16px}
.card{border:1px solid #ddd;border-radius:12px;padding:16px;margin-bottom:16px}
input,textarea,select{width:100%;padding:10px;margin-top:4px;margin-bottom:12px}
button{background:#111;color:#fff;border:none;padding:10px 16px;border-radius:8px;cursor:pointer}
table{width:100%;border-collapse:collapse}th,td{border:1px solid #ddd;padding:8px;text-align:left}
.small{color:#555;font-size:12px}code{background:#f6f6f6;padding:2px 6px;border-radius:4px}
</style></head><body>
<h1>アンテナアクセス交換</h1>
<p>承認後に交換ウィジェットを導入し、露出とクリックを相互交換します。</p>
<div class='card'>
<h2>サイト申請</h2>
<form method='post' action='/apply'>
<input type='hidden' name='csrf_token' value='{{ csrf_token }}'>
<label>サイト名<input name='site_name' maxlength='255' required></label>
<label>サイトURL<input name='site_url' type='url' placeholder='https://example.com' required></label>
<label>連絡先（メール or X）<input name='contact' maxlength='255' required></label>
<label>カテゴリ<select name='category' required>
<option value='IT・SaaS・AI'>IT・SaaS・AI</option><option value='ガジェット'>ガジェット</option><option value='その他'>その他</option>
</select></label>
<label>補足メモ<textarea name='note' rows='3' maxlength='2000'></textarea></label>
<label><input type='checkbox' name='agree_terms' value='1' required> 利用規約とプライバシーポリシーに同意する</label>
<button type='submit'>申請する</button>
</form>
<p class='small'><a href='/terms'>利用規約</a> / <a href='/privacy'>プライバシーポリシー</a></p>
</div>
<div class='card'>
<h2>承認サイト一覧</h2>
<table><thead><tr><th>ID</th><th>サイト名</th><th>URL</th><th>カテゴリ</th><th>状態</th></tr></thead><tbody>
{% for r in approved %}<tr><td>{{r.id}}</td><td>{{r.site_name}}</td><td><a href='{{r.site_url}}' target='_blank'>{{r.site_url}}</a></td><td>{{r.category}}</td><td>{% if r.is_active %}交換有効{% else %}承認済み{% endif %}{% if r.is_banned %}/BAN{% endif %}</td></tr>{% else %}<tr><td colspan='5'>まだありません</td></tr>{% endfor %}
</tbody></table>
</div>
<div class='card'>
<h2>交換ウィジェット</h2>
<code>&lt;script src="https://YOUR_HOST/widget.js?token=YOUR_TOKEN"&gt;&lt;/script&gt;</code>
</div>
<p class='small'><a href='/admin'>管理画面</a></p>
</body></html>
"""

ADMIN_LOGIN_TMPL = """
<!doctype html><html lang='ja'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>Admin Login</title></head><body>
<h1>Admin Login</h1>
<form method='post' action='/admin/login'>
<input type='hidden' name='csrf_token' value='{{ csrf_token }}'>
<label>user<input name='user' required></label>
<label>password<input type='password' name='password' required></label>
<button type='submit'>login</button>
</form>
</body></html>
"""

ADMIN_TMPL = """
<!doctype html><html lang='ja'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>管理画面</title>
<style>body{font-family:sans-serif;max-width:1450px;margin:20px auto;padding:0 16px}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ddd;padding:6px;font-size:12px}form{display:inline}button{margin:2px}</style>
</head><body>
<h1>管理画面</h1>
<p><a href='/admin/audit'>監査ログ</a> | <a href='/admin/logout'>logout</a></p>
<table><thead><tr><th>ID</th><th>name</th><th>status</th><th>active</th><th>banned</th><th>token</th><th>imp_out</th><th>imp_in</th><th>click_out</th><th>click_in</th><th>balance</th><th>ops</th></tr></thead><tbody>
{% for r in rows %}<tr>
<td>{{r.id}}</td><td>{{r.site_name}}</td><td>{{r.status}}</td><td>{{1 if r.is_active else 0}}</td><td>{{1 if r.is_banned else 0}}</td>
<td>{{r.api_token or ''}}</td><td>{{r.impressions_out}}</td><td>{{r.impressions_in}}</td><td>{{r.click_out}}</td><td>{{r.click_in}}</td><td>{{r.click_in-r.click_out}}</td>
<td>
<form method='post' action='/admin/update'><input type='hidden' name='csrf_token' value='{{ csrf_token }}'><input type='hidden' name='id' value='{{r.id}}'><input type='hidden' name='status' value='approved'><button>approve</button></form>
<form method='post' action='/admin/update'><input type='hidden' name='csrf_token' value='{{ csrf_token }}'><input type='hidden' name='id' value='{{r.id}}'><input type='hidden' name='status' value='rejected'><button>reject</button></form>
<form method='post' action='/admin/activate'><input type='hidden' name='csrf_token' value='{{ csrf_token }}'><input type='hidden' name='id' value='{{r.id}}'><button>activate/token</button></form>
<form method='post' action='/admin/toggle'><input type='hidden' name='csrf_token' value='{{ csrf_token }}'><input type='hidden' name='id' value='{{r.id}}'><input type='hidden' name='is_active' value='{{0 if r.is_active else 1}}'><button>{% if r.is_active %}off{% else %}on{% endif %}</button></form>
<form method='post' action='/admin/ban'><input type='hidden' name='csrf_token' value='{{ csrf_token }}'><input type='hidden' name='id' value='{{r.id}}'><input type='hidden' name='is_banned' value='{{0 if r.is_banned else 1}}'><input name='reason' placeholder='reason'><button>{% if r.is_banned %}unban{% else %}ban{% endif %}</button></form>
<form method='post' action='/admin/adjust'><input type='hidden' name='csrf_token' value='{{ csrf_token }}'><input type='hidden' name='id' value='{{r.id}}'><input name='field' placeholder='click_in'><input name='delta' placeholder='-1/+1'><button>adjust</button></form>
</td></tr>{% endfor %}
</tbody></table>
</body></html>
"""

AUDIT_TMPL = """
<!doctype html><html><head><meta charset='UTF-8'><title>audit</title>
<style>body{font-family:sans-serif;max-width:1200px;margin:20px auto}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ddd;padding:6px;font-size:12px}</style></head><body>
<h1>監査ログ</h1><p><a href='/admin'>back</a></p>
<table><thead><tr><th>at</th><th>actor</th><th>action</th><th>target</th><th>ip</th><th>detail</th></tr></thead><tbody>
{% for r in rows %}<tr><td>{{r.at}}</td><td>{{r.actor}}</td><td>{{r.action}}</td><td>{{r.target_id}}</td><td>{{r.ip}}</td><td>{{r.detail}}</td></tr>{% else %}<tr><td colspan='6'>none</td></tr>{% endfor %}
</tbody></table></body></html>
"""

TERMS_TMPL = """
<!doctype html><html lang='ja'><head><meta charset='UTF-8'><title>利用規約</title></head><body>
<h1>利用規約</h1>
<p>本サービスはアクセス交換を目的とする。禁止事項: 不正クリック、bot流入、虚偽登録、第三者権利侵害。</p>
<p>運営は不正検知時に掲載停止・BAN・統計補正を行うことがある。</p>
<p><a href='/'>戻る</a></p>
</body></html>
"""

PRIVACY_TMPL = """
<!doctype html><html lang='ja'><head><meta charset='UTF-8'><title>プライバシーポリシー</title></head><body>
<h1>プライバシーポリシー</h1>
<p>取得情報: IPアドレス、User-Agent、アクセスログ、申請情報。目的: 不正対策、運用改善、監査。</p>
<p>法令に基づく場合を除き第三者提供しない。</p>
<p><a href='/'>戻る</a></p>
</body></html>
"""


@app.before_request
def before_all():
    ensure_csrf()
    if request.path in {"/serve"} or request.path.startswith("/c/"):
        if not rate_limit_ok():
            return jsonify({"ok": False, "error": "rate limit"}), 429


@app.get("/")
def home():
    approved = (
        db.session.query(Application)
        .filter(Application.status == "approved")
        .order_by(Application.id.desc())
        .all()
    )
    return render_template_string(HOME_TMPL, approved=approved, csrf_token=session.get(csrf))


@app.post("/apply")
def apply():
    verify_csrf_from_form()
    if request.form.get("agree_terms") != "1":
        abort(400, "規約同意が必要")

    site_name = (request.form.get("site_name") or "").strip()
    site_url = (request.form.get("site_url") or "").strip()
    contact = (request.form.get("contact") or "").strip()
    category = (request.form.get("category") or "").strip()
    note = (request.form.get("note") or "").strip()

    if not all([site_name, site_url, contact, category]):
        abort(400, "必須項目不足")
    if len(site_name) > 255 or len(contact) > 255 or len(category) > 255 or len(note) > 2000:
        abort(400, "文字数超過")
    if not is_valid_url(site_url):
        abort(400, "URL不正")

    now = now_utc()
    row = Application(
        site_name=site_name,
        site_url=site_url,
        contact=contact,
        category=category,
        note=note,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    db.session.commit()
    audit("apply", target_id=row.id, detail=f"site={site_name}")
    return redirect(url_for("home"))


@app.get("/terms")
def terms():
    return render_template_string(TERMS_TMPL)


@app.get("/privacy")
def privacy():
    return render_template_string(PRIVACY_TMPL)


@app.get("/admin/login")
def admin_login():
    return render_template_string(ADMIN_LOGIN_TMPL, csrf_token=session.get(csrf))


@app.post("/admin/login")
def admin_login_post():
    verify_csrf_from_form()
    user = request.form.get("user", "")
    pw = request.form.get("password", "")
    if user == ADMIN_USER and pw == ADMIN_PASSWORD:
        session["is_admin"] = True
        audit("admin_login")
        return redirect(url_for("admin"))
    abort(403, "login failed")


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("home"))


@app.get("/admin")
def admin():
    r = require_admin_login()
    if r:
        return r
    rows = db.session.query(Application).order_by(Application.id.desc()).all()
    return render_template_string(ADMIN_TMPL, rows=rows, csrf_token=session.get(csrf))


@app.get("/admin/audit")
def admin_audit():
    r = require_admin_login()
    if r:
        return r
    rows = db.session.query(AuditLog).order_by(AuditLog.id.desc()).limit(500).all()
    return render_template_string(AUDIT_TMPL, rows=rows)


@app.post("/admin/update")
def admin_update():
    r = require_admin_login()
    if r:
        return r
    verify_csrf_from_form()
    app_id = int(request.form.get("id", "0"))
    status = request.form.get("status", "")
    if status not in {"approved", "rejected", "pending"}:
        abort(400)
    row = db.session.get(Application, app_id)
    if not row:
        abort(404)
    row.status = status
    row.updated_at = now_utc()
    db.session.commit()
    audit("admin_update", target_id=app_id, detail=f"status={status}")
    return redirect(url_for("admin"))


@app.post("/admin/activate")
def admin_activate():
    r = require_admin_login()
    if r:
        return r
    verify_csrf_from_form()
    app_id = int(request.form.get("id", "0"))
    row = db.session.get(Application, app_id)
    if not row:
        abort(404)
    if row.status != "approved":
        abort(400, "承認済みのみ")
    row.api_token = secrets.token_urlsafe(24)
    row.is_active = True
    row.updated_at = now_utc()
    db.session.commit()
    audit("admin_activate", target_id=app_id)
    return redirect(url_for("admin"))


@app.post("/admin/toggle")
def admin_toggle():
    r = require_admin_login()
    if r:
        return r
    verify_csrf_from_form()
    app_id = int(request.form.get("id", "0"))
    is_active = request.form.get("is_active", "0") == "1"
    row = db.session.get(Application, app_id)
    if not row:
        abort(404)
    row.is_active = is_active
    row.updated_at = now_utc()
    db.session.commit()
    audit("admin_toggle", target_id=app_id, detail=f"is_active={is_active}")
    return redirect(url_for("admin"))


@app.post("/admin/ban")
def admin_ban():
    r = require_admin_login()
    if r:
        return r
    verify_csrf_from_form()
    app_id = int(request.form.get("id", "0"))
    is_banned = request.form.get("is_banned", "0") == "1"
    reason = (request.form.get("reason") or "").strip()[:255]
    row = db.session.get(Application, app_id)
    if not row:
        abort(404)
    row.is_banned = is_banned
    row.ban_reason = reason if is_banned else None
    row.updated_at = now_utc()
    db.session.commit()
    audit("admin_ban", target_id=app_id, detail=f"is_banned={is_banned} reason={reason}")
    return redirect(url_for("admin"))


@app.post("/admin/adjust")
def admin_adjust():
    r = require_admin_login()
    if r:
        return r
    verify_csrf_from_form()
    app_id = int(request.form.get("id", "0"))
    field = request.form.get("field", "")
    delta = int(request.form.get("delta", "0"))
    if field not in {"impressions_out", "impressions_in", "click_out", "click_in"}:
        abort(400, "field不正")
    row = db.session.get(Application, app_id)
    if not row:
        abort(404)
    v = getattr(row, field)
    setattr(row, field, max(0, v + delta))
    row.updated_at = now_utc()
    db.session.commit()
    audit("admin_adjust", target_id=app_id, detail=f"{field} {delta:+d}")
    return redirect(url_for("admin"))


@app.get("/serve")
def serve():
    token = request.args.get("token", "")
    requester = (
        db.session.query(Application)
        .filter(
            Application.api_token == token,
            Application.status == "approved",
            Application.is_active.is_(True),
            Application.is_banned.is_(False),
        )
        .first()
    )
    if not requester:
        return jsonify({"ok": False, "error": "invalid token"}), 403

    target = serve_candidate(requester.id)
    if not target:
        return jsonify({"ok": False, "error": "no target"}), 404

    requester.impressions_out += 1
    target.impressions_in += 1
    requester.updated_at = now_utc()
    target.updated_at = now_utc()
    db.session.commit()

    click_url = url_for("click_redirect", target_id=target.id, _external=True) + f"?from={requester.id}"
    return jsonify(
        {
            "ok": True,
            "target": {
                "id": target.id,
                "site_name": target.site_name,
                "site_url": target.site_url,
                "category": target.category,
                "click_url": click_url,
            },
        }
    )


@app.get("/c/<int:target_id>")
def click_redirect(target_id: int):
    from_id = request.args.get("from", "")
    target = db.session.get(Application, target_id)
    if not target or target.status != "approved" or target.is_banned:
        abort(404)

    if from_id.isdigit():
        source = db.session.get(Application, int(from_id))
        if source:
            fp_raw = f"{source.id}:{target.id}:{client_ip()}:{client_ua()}:{int(now_utc().timestamp()) // CLICK_DEDUP_SEC}"
            fp = hashlib.sha256(fp_raw.encode()).hexdigest()
            exists = (
                db.session.query(ClickEvent)
                .filter(ClickEvent.fingerprint == fp)
                .first()
            )
            if not exists:
                db.session.add(
                    ClickEvent(
                        at=now_utc(),
                        from_site_id=source.id,
                        to_site_id=target.id,
                        fingerprint=fp,
                    )
                )
                source.click_out += 1
                target.click_in += 1
                source.updated_at = now_utc()
                target.updated_at = now_utc()
                db.session.commit()
                audit("click_counted", target_id=target.id, detail=f"from={source.id}")
            else:
                audit("click_deduped", target_id=target.id, detail=f"from={source.id}")
    return redirect(target.site_url, code=302)


@app.get("/widget.js")
def widget_js():
    token = request.args.get("token", "")
    if not token:
        return Response("console.error('token required');", mimetype="application/javascript")

    js = f"""
(function() {{
  var token = {token!r};
  fetch('/serve?token=' + encodeURIComponent(token))
    .then(function(r){{ return r.json(); }})
    .then(function(data){{
      if(!data.ok) return;
      var box=document.createElement('div');
      box.style.border='1px solid #ddd';
      box.style.padding='10px';
      box.style.margin='12px 0';
      box.style.borderRadius='8px';
      box.innerHTML='<strong>おすすめサイト</strong><br><a href="'+data.target.click_url+'" target="_blank" rel="noopener">'+data.target.site_name+'</a>';
      document.currentScript.parentNode.insertBefore(box, document.currentScript);
    }})
    .catch(function(e){{ console.error(e); }});
}})();
"""
    return Response(js, mimetype="application/javascript")


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": now_utc().isoformat()}


def init_db():
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    with app.app_context():
        db.create_all()


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=False)
