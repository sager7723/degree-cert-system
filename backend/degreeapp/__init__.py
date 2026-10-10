"""Flask application for the real ChainMaker degree-certificate registry."""
from contextlib import closing
from datetime import timedelta
from functools import wraps
import hmac
import json
from pathlib import Path
import secrets
import threading
import time
import unicodedata
import uuid

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from . import db
from .chain import ChainClient, ChainError
from .domain import FIELDS, ID, ValidationError, canonical_document, canonical_json, content_hash
from .service import ApplicationError, Registry

ROLE_LABELS = {"uni": "高校签发机构", "edu": "教育主管部门", "query": "查询机构"}
STATUS_LABELS = {"pending": "待链上确认", "valid": "有效", "revoked": "已撤销", "failed": "未签发 / 操作失败",
                 "confirmed": "已确认"}


def create_app(project=None, *, testing=False, chain=None):
    project = Path(project or Path(__file__).resolve().parents[2]).resolve()
    instance = project / "instance"
    database = instance / "degree-cert.sqlite3"
    if not database.is_file():
        raise RuntimeError("网页数据库尚未初始化，请先执行第六步安装脚本。")
    secret = (instance / "web-secret.key").read_text(encoding="ascii").strip()
    if len(secret) < 64:
        raise RuntimeError("会话密钥无效，请保留文件并反馈。")
    app = Flask(__name__, instance_path=str(instance))
    app.config.update(SECRET_KEY=secret, TESTING=testing, MAX_CONTENT_LENGTH=1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=2), TRUSTED_HOSTS=["localhost", "127.0.0.1"],
                      PROJECT_DIR=project, DATABASE=database)
    registry = Registry(database, chain if chain is not None else ChainClient(project))
    app.extensions["registry"] = registry
    login_attempts, login_lock = {}, threading.Lock()
    dummy_hash = generate_password_hash(secrets.token_hex(16), method="pbkdf2:sha256:600000")

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(32)
        return session["csrf"]

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        if session.get("user_id"):
            with closing(db.connect(database)) as connection:
                row = connection.execute("SELECT id,username,display_name,role FROM users WHERE id=?", (session["user_id"],)).fetchone()
                g.user = dict(row) if row else None
        if request.method == "POST":
            supplied, expected = request.form.get("csrf_token", ""), session.get("csrf", "")
            if not expected or not supplied.isascii() or not hmac.compare_digest(supplied, expected):
                abort(400, description="页面已过期或表单校验失败，请重新打开页面后提交。")

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.context_processor
    def context():
        return {"csrf_token": csrf_token, "role_labels": ROLE_LABELS, "status_labels": STATUS_LABELS}

    def require_login(roles=None):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if g.user is None:
                    return redirect(url_for("login"))
                if roles and g.user["role"] not in roles:
                    abort(403)
                return fn(*args, **kwargs)
            return wrapped
        return decorate

    def chain_status():
        try:
            registry.chain.health()
            return {"available": True, "detail": "已实时连接 chain1，合约初始化与机构配置正常。"}
        except ChainError as exc:
            return {"available": False, "detail": str(exc)}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username, password = request.form.get("username", "").strip(), request.form.get("password", "")
            key, moment = (request.remote_addr, username[:64]), time.monotonic()
            with login_lock:
                for candidate in list(login_attempts):
                    if moment - login_attempts[candidate][1] > 300:
                        del login_attempts[candidate]
                tries = login_attempts.get(key, (0, moment))[0]
                if tries >= 5 or len(login_attempts) > 1000:
                    flash("尝试次数较多，请 5 分钟后重试。", "error")
                    return render_template("login.html"), 429
            with closing(db.connect(database)) as connection:
                row = connection.execute("SELECT * FROM users WHERE username=?", (username[:64],)).fetchone()
            valid = len(username) <= 64 and len(password) <= 256 and check_password_hash(row["password_hash"] if row else dummy_hash, password)
            if not row or not valid:
                with login_lock:
                    login_attempts[key] = (tries + 1, moment)
                flash("账号或密码不正确。", "error")
                return render_template("login.html"), 401
            with login_lock:
                login_attempts.pop(key, None)
            session.clear()
            session["user_id"], session.permanent = row["id"], True
            with db.transaction(database) as connection:
                db.audit(connection, row["id"], "登录", detail="账号登录成功。")
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.post("/logout")
    @require_login()
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @require_login()
    def dashboard():
        can_browse = g.user["role"] in ("uni", "edu")
        return render_template("dashboard.html", stats=registry.stats() if can_browse else None,
                               recent=registry.search()[:5] if can_browse else [], chain_status=chain_status())

    @app.get("/certificates")
    @require_login()
    def certificates():
        # q is the current form field; accept search for old bookmarked URLs.
        search = unicodedata.normalize("NFC", request.args.get("q", request.args.get("search", ""))).strip()
        can_browse = g.user["role"] in ("uni", "edu")
        field = request.args.get("field", "all" if can_browse else "cert_id")
        order = request.args.get("order", "asc")
        try:
            page = int(request.args.get("page", "1"))
            result = registry.search_page(search, can_browse, field=field, order=order, page=page)
        except (ValueError, ApplicationError) as exc:
            abort(400, description=str(exc) if isinstance(exc, ApplicationError) else "页码须为正整数。")
        return render_template("certificates.html", certificates=result["items"], search_result=result,
                               search=search, can_browse=can_browse, search_field=field, sort_order=order)

    @app.get("/certificates/<cert_id>")
    @require_login()
    def certificate_detail(cert_id):
        certificate = registry.get(cert_id) if ID.fullmatch(cert_id) else None
        if certificate is None:
            abort(404)
        ledger = None
        integrity = {"available": False, "matches": False, "detail": "暂无法完成链上核验。"}
        try:
            ledger = registry.chain.get_certificate(cert_id)
            try:
                document = canonical_document(certificate["document"])
                integrity = {"available": True, "matches": content_hash(document) == ledger["content_hash"],
                             "detail": "本次已实时读取链上存证，并核对本地证书全文。"}
            except ValidationError:
                integrity = {"available": True, "matches": False, "detail": "本地证书全文格式异常，与链上存证无法匹配。"}
        except ChainError as exc:
            integrity["detail"] = "暂无法完成链上核验：" + str(exc)
        return render_template("certificate_detail.html", certificate=certificate, chain_record=ledger, integrity=integrity,
                               pending_operation=registry.pending(cert_id), request_id=uuid.uuid4().hex)

    @app.route("/certificates/new", methods=["GET", "POST"])
    @require_login(("uni",))
    def issue():
        values = {field: request.form.get(field, "") for field in FIELDS} if request.method == "POST" else {
            "cert_id": "DEG_" + time.strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:10], "school_name": "演示大学", "degree": "学士"}
        errors = {}
        request_id = request.form.get("request_id", "") if request.method == "POST" else uuid.uuid4().hex
        if request.method == "POST":
            try:
                operation = registry.issue(g.user, values, request_id)
                operation_flash(operation)
                return redirect(url_for("certificate_detail", cert_id=operation["cert_id"]))
            except ValidationError as exc:
                errors = exc.errors
            except ApplicationError as exc:
                errors = {"_form": str(exc)}
        return render_template("issue.html", values=values, errors=errors, request_id=request_id), 400 if errors else 200

    @app.post("/certificates/<cert_id>/revoke")
    @require_login(("edu",))
    def revoke(cert_id):
        if not ID.fullmatch(cert_id):
            abort(404)
        try:
            operation = registry.revoke(g.user, cert_id, request.form.get("reason", ""), request.form.get("request_id", ""))
            operation_flash(operation)
        except (ApplicationError, ValidationError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("certificate_detail", cert_id=cert_id))

    def operation_flash(operation):
        if operation["status"] == "confirmed":
            flash("操作已由链上交易确认，交易 ID：" + operation["tx_id"], "success")
        elif operation["status"] == "failed":
            flash("合约明确拒绝了操作：" + operation["error"], "error")
        else:
            flash("交易结果尚待确认。请在操作记录中查证原交易，不要重复提交。", "info")

    @app.route("/verify", methods=["GET", "POST"])
    @require_login()
    def verify():
        values, errors, result, error = {}, {}, None, None
        if request.method == "GET":
            cert_id = request.args.get("cert_id", "")
            record = registry.get(cert_id) if ID.fullmatch(cert_id or "") else None
            if record:
                values = record["document"]
        else:
            values = {field: request.form.get(field, "") for field in FIELDS}
            upload = request.files.get("document_file")
            try:
                if upload and upload.filename:
                    raw = upload.read(32769)
                    if len(raw) > 32768:
                        raise ValueError("证书文件超过 32 KB。")
                    def unique(pairs):
                        value = {}
                        for key, item in pairs:
                            if key in value:
                                raise ValueError("证书文件含重复字段。")
                            value[key] = item
                        return value
                    values = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
                    if not isinstance(values, dict):
                        raise ValueError("证书文件须为 JSON 对象。")
                    if set(values) != set(FIELDS) | {"schema_version"}:
                        raise ValueError("证书文件字段不完整或含额外字段，请使用本系统导出的格式。")
                document = canonical_document(values)
                hashed = content_hash(document)
                result = registry.chain.verify_certificate(document["cert_id"], hashed)
                result = dict(result, supplied_hash=hashed, local_available=registry.get(document["cert_id"]) is not None,
                              source_message="已按提交的完整证书内容计算 SHA-256，并实时查询链上结果。")
                with db.transaction(database) as connection:
                    db.audit(connection, g.user["id"], "内容核验", document["cert_id"],
                             "内容一致" if result["content_match"] else "内容不一致")
                values = document
            except ValidationError as exc:
                errors = exc.errors
                if "schema_version" in errors:
                    errors["_form"] = errors["schema_version"]
            except (ValueError, UnicodeError) as exc:
                error = "证书文件格式不正确：" + str(exc)
                if not isinstance(values, dict):
                    values = {}
            except ChainError as exc:
                error = "暂无法完成链上核验：" + str(exc)
        return render_template("verify.html", values=values, errors=errors, result=result, error=error)

    @app.get("/certificates/<cert_id>/export")
    @require_login()
    def export_certificate(cert_id):
        record = registry.get(cert_id) if ID.fullmatch(cert_id) else None
        if record is None:
            abort(404)
        response = app.response_class(canonical_json(record["document"]) + "\n", mimetype="application/json")
        response.headers["Content-Disposition"] = 'attachment; filename="' + cert_id + '.json"'
        return response

    @app.get("/operations")
    @require_login(("uni", "edu"))
    def operations():
        return render_template("operations.html", operations=registry.operations(g.user["id"]))

    @app.post("/operations/<int:op_id>/reconcile")
    @require_login(("uni", "edu"))
    def reconcile(op_id):
        try:
            operation_flash(registry.reconcile(g.user, op_id))
        except ApplicationError as exc:
            flash(str(exc), "error")
        return redirect(url_for("operations"))

    @app.get("/audit")
    @require_login(("uni", "edu"))
    def audit():
        return render_template("audit.html", entries=registry.audit_entries())

    @app.get("/healthz")
    def healthz():
        # Web process health only; this endpoint intentionally makes no chain claim.
        return jsonify(service="degree-cert-web", status="running")

    def handle_error(exc):
        code = getattr(exc, "code", 500)
        titles = {400: "请求未完成", 403: "当前账号无此操作权限", 404: "未找到这条记录", 413: "上传文件过大", 500: "操作暂未完成"}
        message = str(getattr(exc, "description", "")) if code == 400 else "请返回上一页或查看操作记录。"
        if code == 500:
            message = "发生意外错误。若刚刚提交了交易，请先在操作记录中查证，不要重复发送。"
        return render_template("error.html", code=code, title=titles.get(code, "请求未完成"), message=message), code
    for code in (400, 403, 404, 413, 500):
        app.register_error_handler(code, handle_error)
    return app
