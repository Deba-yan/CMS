"""Conference Management System - prototype (Flask + SQLite).
Implements the 11 functional requirements from the SRS (WCA, ESC501)."""
import os, re, sqlite3, uuid
from datetime import datetime
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, send_from_directory, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "cms.db")
UPLOADS = os.path.join(BASE, "uploads")
ALLOWED = {"pdf", "doc", "docx"}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE,
  password TEXT, role TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS conferences(id INTEGER PRIMARY KEY, name TEXT, date TEXT, venue TEXT,
  description TEXT, fee REAL DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS papers(id INTEGER PRIMARY KEY, conference_id INTEGER, author_id INTEGER,
  title TEXT, abstract TEXT, authors TEXT, filename TEXT, status TEXT DEFAULT 'Submitted', created_at TEXT);
CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY, paper_id INTEGER, reviewer_id INTEGER,
  UNIQUE(paper_id, reviewer_id));
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, paper_id INTEGER, reviewer_id INTEGER,
  comments TEXT, score INTEGER, created_at TEXT, UNIQUE(paper_id, reviewer_id));
CREATE TABLE IF NOT EXISTS registrations(id INTEGER PRIMARY KEY, user_id INTEGER, conference_id INTEGER,
  affiliation TEXT, status TEXT DEFAULT 'Pending Payment', created_at TEXT, UNIQUE(user_id, conference_id));
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, registration_id INTEGER, amount REAL,
  status TEXT, card_last4 TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, conference_id INTEGER, name TEXT, speaker TEXT,
  date TEXT, start_time TEXT, end_time TEXT, venue TEXT);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, user_id INTEGER, message TEXT,
  type TEXT, is_read INTEGER DEFAULT 0, created_at TEXT);
"""

def now(): return datetime.now().strftime("%Y-%m-%d %H:%M")

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, args=(), one=False):
    cur = db().execute(sql, args); rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows

def run(sql, args=()):
    cur = db().execute(sql, args); db().commit(); return cur.lastrowid

def notify(user_id, message, typ="Info"):
    run("INSERT INTO notifications(user_id,message,type,created_at) VALUES(?,?,?,?)",
        (user_id, message, typ, now()))

def notify_role(role, message, typ="Info"):
    for u in q("SELECT id FROM users WHERE role=?", (role,)): notify(u["id"], message, typ)

# ---------- auth helpers ----------
@app.before_request
def load_user():
    g.user = q("SELECT * FROM users WHERE id=?", (session["uid"],), one=True) if "uid" in session else None

@app.context_processor
def inject():
    unread = q("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0",
               (g.user["id"],), one=True)["c"] if g.user else 0
    return dict(user=g.user, unread=unread)

def login_required(*roles):
    def deco(f):
        @wraps(f)
        def wrap(*a, **k):
            if not g.user:
                flash("Please log in first.", "error"); return redirect(url_for("login"))
            if roles and g.user["role"] not in roles:
                flash("You don't have access to that page.", "error"); return redirect(url_for("dashboard"))
            return f(*a, **k)
        return wrap
    return deco

# ---------- 1.1 / 1.2 registration & login ----------
@app.route("/")
def index(): return redirect(url_for("dashboard" if g.user else "login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name, email = request.form.get("name", "").strip(), request.form.get("email", "").strip().lower()
        pw, role = request.form.get("password", ""), request.form.get("role", "")
        if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(pw) < 6 \
           or role not in ("author", "reviewer", "participant"):
            flash("Invalid input: check name, email, role and a password of 6+ characters.", "error")
        elif q("SELECT 1 FROM users WHERE email=?", (email,), one=True):
            flash("Email already exists.", "error")
        else:
            run("INSERT INTO users(name,email,password,role,created_at) VALUES(?,?,?,?,?)",
                (name, email, generate_password_hash(pw), role, now()))
            flash("Account created successfully. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q("SELECT * FROM users WHERE email=?", (request.form.get("email", "").strip().lower(),), one=True)
        if u and check_password_hash(u["password"], request.form.get("password", "")):
            session["uid"] = u["id"]; flash(f"Welcome, {u['name']}!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/dashboard")
@login_required()
def dashboard():
    r, uid, s = g.user["role"], g.user["id"], {}
    if r == "admin":
        s = {"Conferences": q("SELECT COUNT(*) c FROM conferences", one=True)["c"],
             "Papers": q("SELECT COUNT(*) c FROM papers", one=True)["c"],
             "Awaiting decision": q("SELECT COUNT(*) c FROM papers WHERE status IN ('Submitted','Under Review')", one=True)["c"],
             "Registrations": q("SELECT COUNT(*) c FROM registrations", one=True)["c"]}
    elif r == "author":
        s = {"My papers": q("SELECT COUNT(*) c FROM papers WHERE author_id=?", (uid,), one=True)["c"],
             "Accepted": q("SELECT COUNT(*) c FROM papers WHERE author_id=? AND status='Accepted'", (uid,), one=True)["c"]}
    elif r == "reviewer":
        s = {"Assigned": q("SELECT COUNT(*) c FROM assignments WHERE reviewer_id=?", (uid,), one=True)["c"],
             "Reviewed": q("SELECT COUNT(*) c FROM reviews WHERE reviewer_id=?", (uid,), one=True)["c"]}
    else:
        s = {"My registrations": q("SELECT COUNT(*) c FROM registrations WHERE user_id=?", (uid,), one=True)["c"]}
    return render_template("dashboard.html", stats=s)

# ---------- 1.3 conferences ----------
@app.route("/conferences", methods=["GET", "POST"])
@login_required()
def conferences():
    if request.method == "POST":
        if g.user["role"] != "admin": abort(403)
        f = {k: request.form.get(k, "").strip() for k in ("name", "date", "venue", "description", "fee")}
        try:
            fee = float(f["fee"] or 0); datetime.strptime(f["date"], "%Y-%m-%d"); assert fee >= 0
        except Exception:
            fee = None
        if not (f["name"] and f["venue"] and f["description"]) or fee is None:
            flash("Missing/invalid conference details.", "error")
        else:
            run("INSERT INTO conferences(name,date,venue,description,fee,created_at) VALUES(?,?,?,?,?,?)",
                (f["name"], f["date"], f["venue"], f["description"], fee, now()))
            notify_role("author", f"New conference open for papers: {f['name']}", "Conference")
            flash("Conference created successfully.", "success")
        return redirect(url_for("conferences"))
    rows = q("""SELECT c.*, (SELECT COUNT(*) FROM papers WHERE conference_id=c.id) papers,
                (SELECT COUNT(*) FROM registrations WHERE conference_id=c.id) regs
                FROM conferences c ORDER BY date""")
    mine = {r["conference_id"] for r in q("SELECT conference_id FROM registrations WHERE user_id=?", (g.user["id"],))}
    return render_template("conferences.html", rows=rows, mine=mine)

# ---------- 1.4 paper submission ----------
@app.route("/submit", methods=["GET", "POST"])
@login_required("author")
def submit():
    confs = q("SELECT * FROM conferences ORDER BY date")
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in ("conference_id", "title", "abstract", "authors")}
        file = request.files.get("file")
        if not all(f.values()) or not file or not file.filename:
            flash("Missing details: all fields and a paper file are required.", "error")
        elif file.filename.rsplit(".", 1)[-1].lower() not in ALLOWED:
            flash("Invalid file format. Upload a PDF, DOC or DOCX file.", "error")
        elif not q("SELECT 1 FROM conferences WHERE id=?", (f["conference_id"],), one=True):
            flash("Please choose a valid conference.", "error")
        else:
            fn = f"{uuid.uuid4().hex[:8]}_{secure_filename(file.filename)}"
            file.save(os.path.join(UPLOADS, fn))
            pid = run("INSERT INTO papers(conference_id,author_id,title,abstract,authors,filename,created_at) VALUES(?,?,?,?,?,?,?)",
                      (f["conference_id"], g.user["id"], f["title"], f["abstract"], f["authors"], fn, now()))
            notify(g.user["id"], f"Paper '{f['title']}' submitted. Submission ID: P{pid:03d}", "Paper")
            notify_role("admin", f"New paper submitted: P{pid:03d} - {f['title']}", "Paper")
            flash(f"Paper submitted with submission ID P{pid:03d}.", "success")
            return redirect(url_for("papers"))
    return render_template("submit.html", confs=confs)

@app.route("/papers")
@login_required("author", "admin")
def papers():
    sql = """SELECT p.*, c.name conf, u.name author_name,
             (SELECT COUNT(*) FROM reviews WHERE paper_id=p.id) nrev,
             (SELECT ROUND(AVG(score),1) FROM reviews WHERE paper_id=p.id) avg
             FROM papers p JOIN conferences c ON c.id=p.conference_id JOIN users u ON u.id=p.author_id"""
    rows = q(sql + (" ORDER BY p.id DESC" if g.user["role"] == "admin"
                    else " WHERE p.author_id=? ORDER BY p.id DESC"),
             () if g.user["role"] == "admin" else (g.user["id"],))
    return render_template("papers.html", rows=rows)

@app.route("/uploads/<fn>")
@login_required()
def download(fn):
    p = q("SELECT * FROM papers WHERE filename=?", (fn,), one=True)
    if not p: abort(404)
    ok = g.user["role"] == "admin" or p["author_id"] == g.user["id"] or \
         q("SELECT 1 FROM assignments WHERE paper_id=? AND reviewer_id=?", (p["id"], g.user["id"]), one=True)
    if not ok: abort(403)
    return send_from_directory(UPLOADS, fn, as_attachment=True)

# ---------- reviewer assignment + 1.6 decision (admin) ----------
@app.route("/paper/<int:pid>", methods=["GET", "POST"])
@login_required("admin")
def paper_admin(pid):
    p = q("""SELECT p.*, c.name conf, u.name author_name FROM papers p
             JOIN conferences c ON c.id=p.conference_id JOIN users u ON u.id=p.author_id WHERE p.id=?""", (pid,), one=True)
    if not p: abort(404)
    if request.method == "POST":
        act = request.form.get("action")
        if act == "assign":
            rid = request.form.get("reviewer_id")
            if not q("SELECT 1 FROM users WHERE id=? AND role='reviewer'", (rid,), one=True):
                flash("Please select a valid reviewer.", "error")
            elif q("SELECT 1 FROM assignments WHERE paper_id=? AND reviewer_id=?", (pid, rid), one=True):
                flash("Reviewer already assigned to this paper.", "error")
            else:
                run("INSERT INTO assignments(paper_id,reviewer_id) VALUES(?,?)", (pid, rid))
                if p["status"] == "Submitted": run("UPDATE papers SET status='Under Review' WHERE id=?", (pid,))
                notify(int(rid), f"You were assigned to review P{pid:03d} - {p['title']}", "Review")
                flash("Reviewer assigned.", "success")
        elif act == "decide":
            d = request.form.get("decision")
            if d not in ("Accepted", "Rejected"):
                flash("Decision not selected.", "error")
            elif not q("SELECT 1 FROM reviews WHERE paper_id=?", (pid,), one=True):
                flash("Cannot decide: no reviews submitted for this paper yet.", "error")
            else:
                run("UPDATE papers SET status=? WHERE id=?", (d, pid))
                notify(p["author_id"], f"Your paper '{p['title']}' (P{pid:03d}) was {d.upper()}.", "Decision")
                flash(f"Paper marked {d}.", "success")
        return redirect(url_for("paper_admin", pid=pid))
    reviewers = q("SELECT id,name FROM users WHERE role='reviewer'")
    assigned = q("""SELECT u.name, a.reviewer_id, r.score, r.comments FROM assignments a
                    JOIN users u ON u.id=a.reviewer_id
                    LEFT JOIN reviews r ON r.paper_id=a.paper_id AND r.reviewer_id=a.reviewer_id
                    WHERE a.paper_id=?""", (pid,))
    return render_template("paper_admin.html", p=p, reviewers=reviewers, assigned=assigned)

# ---------- 1.5 review ----------
@app.route("/reviews")
@login_required("reviewer")
def reviews():
    rows = q("""SELECT p.id, p.title, c.name conf, r.score, r.comments FROM assignments a
                JOIN papers p ON p.id=a.paper_id JOIN conferences c ON c.id=p.conference_id
                LEFT JOIN reviews r ON r.paper_id=p.id AND r.reviewer_id=a.reviewer_id
                WHERE a.reviewer_id=? ORDER BY p.id DESC""", (g.user["id"],))
    return render_template("reviews.html", rows=rows)

@app.route("/review/<int:pid>", methods=["GET", "POST"])
@login_required("reviewer")
def review(pid):
    if not q("SELECT 1 FROM assignments WHERE paper_id=? AND reviewer_id=?", (pid, g.user["id"]), one=True):
        flash("Paper not assigned to you.", "error"); return redirect(url_for("reviews"))
    p = q("SELECT * FROM papers WHERE id=?", (pid,), one=True)
    ex = q("SELECT * FROM reviews WHERE paper_id=? AND reviewer_id=?", (pid, g.user["id"]), one=True)
    if request.method == "POST":
        c, sc = request.form.get("comments", "").strip(), request.form.get("score", "")
        if not c or not sc.isdigit() or not 1 <= int(sc) <= 10:
            flash("Invalid score (1-10) or missing comments.", "error")
        else:
            if ex: run("UPDATE reviews SET comments=?,score=?,created_at=? WHERE id=?", (c, int(sc), now(), ex["id"]))
            else: run("INSERT INTO reviews(paper_id,reviewer_id,comments,score,created_at) VALUES(?,?,?,?,?)",
                      (pid, g.user["id"], c, int(sc), now()))
            notify_role("admin", f"Review received for P{pid:03d} ({sc}/10) from {g.user['name']}", "Review")
            flash("Review submitted successfully.", "success"); return redirect(url_for("reviews"))
    return render_template("review.html", p=p, ex=ex)

# ---------- 1.7 participant registration + 1.8 payment ----------
@app.route("/join/<int:cid>", methods=["POST"])
@login_required("author", "reviewer", "participant")
def join(cid):
    c = q("SELECT * FROM conferences WHERE id=?", (cid,), one=True)
    if not c: flash("Conference unavailable.", "error")
    elif q("SELECT 1 FROM registrations WHERE user_id=? AND conference_id=?", (g.user["id"], cid), one=True):
        flash("Already registered.", "error")
    else:
        status = "Pending Payment" if c["fee"] > 0 else "Confirmed"
        run("INSERT INTO registrations(user_id,conference_id,affiliation,status,created_at) VALUES(?,?,?,?,?)",
            (g.user["id"], cid, request.form.get("affiliation", "").strip(), status, now()))
        notify(g.user["id"], f"Registered for {c['name']}." + (" Please complete payment." if c["fee"] > 0 else ""), "Registration")
        flash("Registration confirmed." + (" Please complete payment." if c["fee"] > 0 else ""), "success")
    return redirect(url_for("my_registrations"))

@app.route("/registrations")
@login_required("author", "reviewer", "participant")
def my_registrations():
    rows = q("""SELECT r.*, c.name conf, c.fee, c.date, (SELECT status FROM payments WHERE registration_id=r.id
                ORDER BY id DESC LIMIT 1) pay_status FROM registrations r JOIN conferences c ON c.id=r.conference_id
                WHERE r.user_id=? ORDER BY r.id DESC""", (g.user["id"],))
    return render_template("registrations.html", rows=rows)

@app.route("/pay/<int:rid>", methods=["GET", "POST"])
@login_required("author", "reviewer", "participant")
def pay(rid):
    r = q("""SELECT r.*, c.name conf, c.fee FROM registrations r JOIN conferences c ON c.id=r.conference_id
             WHERE r.id=? AND r.user_id=?""", (rid, g.user["id"]), one=True)
    if not r: abort(404)
    if r["status"] == "Confirmed":
        flash("This registration is already paid.", "error"); return redirect(url_for("my_registrations"))
    if request.method == "POST":
        card = re.sub(r"\s", "", request.form.get("card", ""))
        exp, cvv = request.form.get("expiry", ""), request.form.get("cvv", "")
        valid = card.isdigit() and len(card) == 16 and re.match(r"^(0[1-9]|1[0-2])/\d{2}$", exp) and re.match(r"^\d{3}$", cvv)
        if not valid:
            flash("Invalid payment details.", "error")
        elif card.endswith("0000"):          # demo: simulate a declined card
            run("INSERT INTO payments(registration_id,amount,status,card_last4,created_at) VALUES(?,?,?,?,?)",
                (rid, r["fee"], "Failed", card[-4:], now()))
            flash("Payment failed: card declined. Try another card.", "error")
        else:
            pid = run("INSERT INTO payments(registration_id,amount,status,card_last4,created_at) VALUES(?,?,?,?,?)",
                      (rid, r["fee"], "Paid", card[-4:], now()))
            run("UPDATE registrations SET status='Confirmed' WHERE id=?", (rid,))
            notify(g.user["id"], f"Payment of ₹{r['fee']:.0f} received for {r['conf']}. Receipt #{pid:04d}.", "Payment")
            return redirect(url_for("receipt", pid=pid))
    return render_template("pay.html", r=r)

@app.route("/receipt/<int:pid>")
@login_required()
def receipt(pid):
    p = q("""SELECT p.*, r.user_id, c.name conf, u.name uname FROM payments p JOIN registrations r ON r.id=p.registration_id
             JOIN conferences c ON c.id=r.conference_id JOIN users u ON u.id=r.user_id WHERE p.id=?""", (pid,), one=True)
    if not p or (p["user_id"] != g.user["id"] and g.user["role"] != "admin"): abort(404)
    return render_template("receipt.html", p=p)

# ---------- 1.9 schedule ----------
def conflict(cid, date, st, en, venue, exclude=0):
    for s in q("SELECT * FROM sessions WHERE conference_id=? AND date=? AND venue=? AND id!=?", (cid, date, venue, exclude)):
        if st < s["end_time"] and en > s["start_time"]: return s
    return None

@app.route("/schedule", methods=["GET", "POST"])
@login_required()
def schedule():
    edit = None
    if request.args.get("edit") and g.user["role"] == "admin":
        edit = q("SELECT * FROM sessions WHERE id=?", (request.args["edit"],), one=True)
    if request.method == "POST":
        if g.user["role"] != "admin": abort(403)
        f = {k: request.form.get(k, "").strip() for k in ("conference_id", "name", "speaker", "date", "start_time", "end_time", "venue")}
        sid = request.form.get("sid")
        if request.form.get("delete") and sid:
            run("DELETE FROM sessions WHERE id=?", (sid,)); flash("Session deleted.", "success")
        elif not all(f.values()) or f["end_time"] <= f["start_time"]:
            flash("Missing details, or end time is not after start time.", "error")
        else:
            c = conflict(f["conference_id"], f["date"], f["start_time"], f["end_time"], f["venue"], int(sid or 0))
            if c: flash(f"Time/venue conflict with '{c['name']}' ({c['start_time']}-{c['end_time']}).", "error")
            else:
                cols = ("conference_id", "name", "speaker", "date", "start_time", "end_time", "venue")
                if sid:
                    run(f"UPDATE sessions SET {','.join(k+'=?' for k in cols)} WHERE id=?", [f[k] for k in cols] + [sid])
                    notify_role("participant", f"Schedule updated: {f['name']} on {f['date']} {f['start_time']}", "Schedule")
                else:
                    run(f"INSERT INTO sessions({','.join(cols)}) VALUES(?,?,?,?,?,?,?)", [f[k] for k in cols])
                flash("Schedule updated.", "success")
        return redirect(url_for("schedule"))
    rows = q("""SELECT s.*, c.name conf FROM sessions s JOIN conferences c ON c.id=s.conference_id
                ORDER BY c.name, s.date, s.start_time""")
    return render_template("schedule.html", rows=rows, confs=q("SELECT id,name FROM conferences"), edit=edit)

# ---------- 1.10 notifications ----------
@app.route("/notifications", methods=["GET", "POST"])
@login_required()
def notifications():
    if request.method == "POST":
        if g.user["role"] != "admin": abort(403)
        uid, msg, typ = request.form.get("user_id", ""), request.form.get("message", "").strip(), request.form.get("type", "Info")
        if not msg: flash("Notification failed: message is empty.", "error")
        elif uid == "all":
            for u in q("SELECT id FROM users WHERE role!='admin'"): notify(u["id"], msg, typ)
            flash("Notification delivered to all users.", "success")
        elif not q("SELECT 1 FROM users WHERE id=?", (uid,), one=True): flash("Invalid user.", "error")
        else: notify(int(uid), msg, typ); flash("Notification delivered.", "success")
        return redirect(url_for("notifications"))
    rows = q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC", (g.user["id"],))
    run("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    users = q("SELECT id,name,role FROM users WHERE role!='admin'") if g.user["role"] == "admin" else []
    return render_template("notifications.html", rows=rows, users=users)

# ---------- 1.11 reports ----------
REPORTS = {
 "papers": ("Papers report", "SELECT p.id, p.title, u.name author, c.name conference, p.status, p.created_at FROM papers p JOIN users u ON u.id=p.author_id JOIN conferences c ON c.id=p.conference_id WHERE date(p.created_at) BETWEEN ? AND ?"),
 "users": ("Users report", "SELECT id, name, email, role, created_at FROM users WHERE date(created_at) BETWEEN ? AND ?"),
 "registrations": ("Registrations report", "SELECT r.id, u.name participant, c.name conference, r.status, r.created_at FROM registrations r JOIN users u ON u.id=r.user_id JOIN conferences c ON c.id=r.conference_id WHERE date(r.created_at) BETWEEN ? AND ?"),
 "payments": ("Payments report", "SELECT p.id, u.name participant, c.name conference, p.amount, p.status, p.created_at FROM payments p JOIN registrations r ON r.id=p.registration_id JOIN users u ON u.id=r.user_id JOIN conferences c ON c.id=r.conference_id WHERE date(p.created_at) BETWEEN ? AND ?"),
}

@app.route("/reports", methods=["GET", "POST"])
@login_required("admin")
def reports():
    data = None
    if request.method == "POST":
        t, a, b = request.form.get("type"), request.form.get("start") or "0000-01-01", request.form.get("end") or "9999-12-31"
        if t not in REPORTS: flash("Invalid report type.", "error")
        else:
            rows = q(REPORTS[t][1], (a, b))
            if not rows: flash("No data available for that report and date range.", "error")
            else: data = dict(title=REPORTS[t][0], cols=rows[0].keys(), rows=rows, a=a, b=b)
    return render_template("reports.html", data=data, types=REPORTS)

# ---------- setup + demo data ----------
def init_db(seed=True):
    os.makedirs(UPLOADS, exist_ok=True)
    c = sqlite3.connect(DB); c.executescript(SCHEMA)
    if seed and not c.execute("SELECT 1 FROM users").fetchone():
        t = now()
        for n, e, r in [("Admin Debayan", "admin@demo.com", "admin"), ("Arjun Author", "author@demo.com", "author"),
                        ("Rajdeep Reviewer", "reviewer@demo.com", "reviewer"), ("Ravi Reviewer", "reviewer2@demo.com", "reviewer"),
                        ("Deba Participant", "participant@demo.com", "participant")]:
            c.execute("INSERT INTO users(name,email,password,role,created_at) VALUES(?,?,?,?,?)",
                      (n, e, generate_password_hash("demo123"), r, t))
        c.execute("INSERT INTO conferences(name,date,venue,description,fee,created_at) VALUES(?,?,?,?,?,?)",
                  ("International Conference on AI & Software Engineering", "2026-12-12", "Techno Main, Salt Lake, Kolkata",
                   "Research papers, workshops and keynotes on AI and modern software engineering.", 1500, t))
        c.execute("INSERT INTO conferences(name,date,venue,description,fee,created_at) VALUES(?,?,?,?,?,?)",
                  ("Student Tech Symposium", "2027-01-20", "Main Auditorium", "A free one-day student symposium.", 0, t))
        c.execute("INSERT INTO sessions(conference_id,name,speaker,date,start_time,end_time,venue) VALUES(1,'Opening Keynote','Dr. S. Banerjee','2026-12-12','09:30','10:30','Hall A')")
        c.execute("INSERT INTO sessions(conference_id,name,speaker,date,start_time,end_time,venue) VALUES(1,'Paper Session 1','Various authors','2026-12-12','11:00','12:30','Hall A')")
        c.commit()
    c.close()

init_db()   # creates tables, demo data and the uploads folder (needed when run by gunicorn on Render)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
