"""
app.py  -  Deadstock Inventory Management System  (Flask + MySQL)

FEATURES:
  - Role-based login, forgot password / OTP (email + WhatsApp)
  - SKU tracking, deadstock aging, QR codes per item
  - Bulk send to warehouse / bulk allocate
  - Warehouse capacity bars, sustainability scorecard
  - Live table search + column sort, dark mode toggle
  - PDF branch reports + SendGrid email delivery
  - Custom 404 / 500 error pages
"""

from flask import Flask, render_template, request, redirect, url_for, session, make_response, send_file
import mysql.connector
import re, smtplib, random, secrets, io, json, base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, date
from functools import wraps
from collections import defaultdict

try:
    from twilio.rest import Client as TwilioClient
except ImportError:
    TwilioClient = None

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:
    BackgroundScheduler = None

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
except ImportError:
    SendGridAPIClient = None

# ── ReportLab ──────────────────────────────────────────────────────
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                 Paragraph, Spacer, HRFlowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

app = Flask(__name__)
app.secret_key = 'deadstock_secret_key_2024'

DB_CONFIG = dict(host='127.0.0.1', user='root', password='myfamily21', database='deadstock_db')

# ── Email config ────────────────────────────────────────────────────
MAIL_SENDER   = 'shalakaparhad21@gmail.com'      # ← change to your Gmail
MAIL_PASSWORD = 'uozu otzm cpfm vujk'         # ← change to your App Password

# ── Twilio WhatsApp config ──────────────────────────────────────────
TWILIO_SID   = 'YOUR_ACCOUNT_SID'
TWILIO_TOKEN = 'YOUR_AUTH_TOKEN'
TWILIO_FROM  = 'whatsapp:+14155238886'

# ── SendGrid config ─────────────────────────────────────────────────
SENDGRID_API_KEY = 'YOUR_SENDGRID_API_KEY'
SENDGRID_FROM    = 'noreply@deadstock-ims.com'

# ── In-memory OTP store ─────────────────────────────────────────────
otp_store = {}  # keyed by email

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

ROLE_TABLES = {
    'Admin': ['DEADSTOCK','BRANCH','WAREHOUSE','STOCK_ALLOCATION',
              'HEAD','CONTACTS','MATERIAL','REPORT','RECYCLE_RECORD'], 
    'Branch':           ['DEADSTOCK','MATERIAL'],
    'Warehouse':        ['DEADSTOCK','BRANCH'],
    'Stock_Allocation': ['DEADSTOCK','STOCK_ALLOCATION','REPORT'],
}

ALLOC_COLOR_MAP = {
    'Recycle':  '#2d6a4f',
    'Donate':   '#1565c0',
    'Resell':   '#e65100',
    'Upcycle':  '#6a1e2d',
    'Rebrand':  '#4a148c',
    'Disposal': '#607d8b',
}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'role' not in session:
            return redirect(url_for('landing'))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') not in roles:
                return render_template('denied.html')
            return f(*args, **kwargs)
        return decorated
    return decorator

def valid_email(e):
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', e.strip()))

def valid_phone(p):
    return bool(re.match(r'^\d{10}$', p.strip()))

def mask_email(email):
    """Return a***@domain.com style masked email."""
    parts = email.split('@')
    if len(parts) != 2:
        return email
    local = parts[0]
    return local[0] + '***@' + parts[1]

def send_otp_email(to_email, otp):
    """Send OTP email via Gmail SMTP SSL."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = 'Deadstock Inventory – Your OTP'
    msg['From']    = MAIL_SENDER
    msg['To']      = to_email
    html = f"""
    <html><body style="font-family:'Segoe UI',sans-serif;background:#f4f6f9;padding:32px">
    <div style="max-width:480px;margin:0 auto;background:white;border-radius:14px;
                padding:40px;box-shadow:0 2px 12px rgba(0,0,0,.1)">
      <div style="text-align:center;margin-bottom:28px">
        <div style="font-size:36px">♻</div>
        <h2 style="color:#1a1a2e;margin:8px 0 4px">Deadstock Inventory</h2>
        <p style="color:#888;font-size:14px">Password Reset OTP</p>
      </div>
      <p style="color:#444;font-size:14px;margin-bottom:20px">
        Your one-time password for resetting your account password is:
      </p>
      <div style="text-align:center;margin:24px 0">
        <span style="font-size:42px;font-weight:700;color:#2d6a4f;
                     letter-spacing:10px;border:2px solid #2d6a4f;
                     border-radius:10px;padding:12px 28px">{otp}</span>
      </div>
      <p style="color:#888;font-size:13px;text-align:center;margin-top:20px">
        ⏱ Valid for <strong>10 minutes</strong>.<br>
        🔒 Do not share this OTP with anyone.
      </p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
      <p style="color:#aaa;font-size:11px;text-align:center">
        If you did not request this, please ignore this email.
      </p>
    </div></body></html>"""
    msg.attach(MIMEText(html, 'html'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(MAIL_SENDER, MAIL_PASSWORD)
            s.sendmail(MAIL_SENDER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f'[EMAIL ERROR] {e}')
        return False

def send_whatsapp_alert(to_phone, message):
    """Send a WhatsApp message via Twilio sandbox."""
    if not to_phone:
        return False
    if not TwilioClient:
        print('[WHATSAPP] twilio package not installed')
        return False
    try:
        client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(
            body=message,
            from_=TWILIO_FROM,
            to=f'whatsapp:+91{to_phone}'
        )
        return True
    except Exception as e:
        print(f'[WHATSAPP ERROR] {e}')
        return False

def get_head_phone(head_id):
    """Fetch primary contact number for a head_id from CONTACTS table."""
    try:
        db = get_db(); cur = db.cursor()
        cur.execute("SELECT Contact_no FROM CONTACTS WHERE Head_ID=%s LIMIT 1", (head_id,))
        row = cur.fetchone()
        cur.close(); db.close()
        return str(row[0]) if row else None
    except Exception:
        return None

def alert_warehouse_head_items_sent(branch_id):
    """WhatsApp alert: branch dispatched items to warehouse."""
    try:
        db2 = get_db(); cur2 = db2.cursor()
        cur2.execute("""
            SELECT w.Head_ID, b.City, d.Category, d.Quantity
            FROM BRANCH b
            JOIN WAREHOUSE w ON b.Warehouse_ID = w.Warehouse_ID
            JOIN DEADSTOCK d ON d.Branch_ID = b.Branch_ID
            WHERE b.Branch_ID = %s AND d.Sent_To_Warehouse = 1
            ORDER BY d.Deadstock_ID DESC LIMIT 1
        """, (branch_id,))
        wh_row = cur2.fetchone()
        if wh_row:
            wh_head_id, branch_city, category, qty = wh_row
            phone = get_head_phone(wh_head_id)
            if phone:
                msg = (
                    f"🏭 *Deadstock IMS Alert*\n"
                    f"Items dispatched from Branch ({branch_city}) to your warehouse.\n"
                    f"Category: {category} | Qty: {qty}\n"
                    f"⚡ Action needed — please receive and process."
                )
                send_whatsapp_alert(phone, msg)
        cur2.close(); db2.close()
    except Exception as e:
        print(f'[ALERT ERROR] {e}')

def alert_sa_head_items_sent(warehouse_id):
    """WhatsApp alert: warehouse sent items to Stock Allocation."""
    try:
        db2 = get_db(); cur2 = db2.cursor()
        cur2.execute("""
            SELECT w.City, d.Category, d.Quantity
            FROM WAREHOUSE w
            JOIN BRANCH b ON b.Warehouse_ID = w.Warehouse_ID
            JOIN DEADSTOCK d ON d.Branch_ID = b.Branch_ID
            WHERE w.Warehouse_ID = %s AND d.Sent_To_SA = 1
            ORDER BY d.Deadstock_ID DESC LIMIT 1
        """, (warehouse_id,))
        row = cur2.fetchone()
        cur2.execute("SELECT Head_ID FROM HEAD WHERE Status='Stock_Allocation' LIMIT 1")
        sa_head = cur2.fetchone()
        if row and sa_head:
            wh_city, category, qty = row
            phone = get_head_phone(sa_head[0])
            if phone:
                msg = (
                    f"📦 *Deadstock IMS Alert*\n"
                    f"Items sent from Warehouse ({wh_city}) to Stock Allocation.\n"
                    f"Category: {category} | Qty: {qty}\n"
                    f"⚡ Action needed — please allocate these items."
                )
                send_whatsapp_alert(phone, msg)
        cur2.close(); db2.close()
    except Exception as e:
        print(f'[ALERT ERROR] {e}')

def send_otp_whatsapp(to_phone, otp):
    """Send OTP via WhatsApp using Twilio sandbox."""
    if not TwilioClient:
        print('[WHATSAPP] twilio package not installed')
        return False
    try:
        client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(
            body=f'Your Deadstock IMS OTP is: {otp}. Valid for 10 minutes. Do not share.',
            from_=TWILIO_FROM,
            to=f'whatsapp:+91{to_phone}'
        )
        return True
    except Exception as e:
        print(f'[WHATSAPP ERROR] {e}')
        return False

def send_whatsapp_otp_for_email(email, role, otp):
    """Look up phone in CONTACTS and send WhatsApp OTP if available."""
    if role == 'Admin':
        return
    db = get_db(); cur = db.cursor()
    try:
        cur.execute("""SELECT c.Contact_no FROM HEAD h
                       JOIN CONTACTS c ON h.Head_ID=c.Head_ID
                       WHERE h.Email=%s AND h.Status=%s LIMIT 1""", (email, role))
        row = cur.fetchone()
        if row and row[0]:
            send_otp_whatsapp(row[0], otp)
    finally:
        cur.close(); db.close()

def get_age_class(date_added):
    if not date_added:
        return 'age-unknown'
    days = (date.today() - date_added).days
    if days <= 30:
        return 'age-fresh'
    elif days <= 90:
        return 'age-warning'
    else:
        return 'age-old'

def days_in_system(date_added):
    if not date_added:
        return None
    return (date.today() - date_added).days

def fetch_warehouse_capacity(cur, warehouse_id=None):
    """Return list of (Warehouse_ID, City, Capacity, Current_Stock)."""
    q = """
        SELECT w.Warehouse_ID, w.City, w.Capacity,
               COALESCE(SUM(d.Quantity), 0) AS Current_Stock
        FROM WAREHOUSE w
        LEFT JOIN BRANCH b ON b.Warehouse_ID = w.Warehouse_ID
        LEFT JOIN DEADSTOCK d ON d.Branch_ID = b.Branch_ID
                              AND d.Sent_To_Warehouse = 1
                              AND d.Sent_To_SA = 0
        WHERE 1=1"""
    p = []
    if warehouse_id is not None:
        q += " AND w.Warehouse_ID=%s"
        p.append(warehouse_id)
    q += " GROUP BY w.Warehouse_ID, w.City, w.Capacity ORDER BY w.Warehouse_ID"
    cur.execute(q, p)
    return cur.fetchall()

def compute_deadstock_meta(rows, date_idx):
    """Return age_classes and days_list for deadstock rows."""
    age_classes = []
    days_list = []
    for row in rows:
        d_added = row[date_idx] if len(row) > date_idx else None
        age_classes.append(get_age_class(d_added))
        days_list.append(days_in_system(d_added))
    return age_classes, days_list

@app.template_filter('myenumerate')
def jinja_enumerate(iterable):
    return list(enumerate(iterable))

# ══════════════════════════════════════════════════════════════════
# LANDING
# ══════════════════════════════════════════════════════════════════
@app.route('/')
def landing():
    return render_template('landing.html')

# ══════════════════════════════════════════════════════════════════
# LOGIN  –  blocks deleted-branch/warehouse heads
# ══════════════════════════════════════════════════════════════════
@app.route('/login/<role>', methods=['GET','POST'])
def login(role):
    if role not in ['Admin','Branch','Warehouse','Stock_Allocation']:
        return redirect(url_for('landing'))
    error = None
    if request.method == 'POST':
        db = get_db(); cur = db.cursor()
        try:
            if role == 'Admin':
                cur.execute("SELECT Username FROM ADMIN WHERE Username=%s AND Password=%s",
                            (request.form.get('username',''), request.form.get('password','')))
                row = cur.fetchone()
                if row:
                    session.update({'role':'Admin','name':'Admin',
                                    'head_id':None,'branch_id':None,'warehouse_id':None})
                    return redirect(url_for('dashboard'))
                error = 'Invalid username or password.'
            else:
                cur.execute("SELECT Head_ID,Name FROM HEAD WHERE Email=%s AND Password=%s AND Status=%s",
                            (request.form.get('email',''), request.form.get('password',''), role))
                row = cur.fetchone()
                if row:
                    head_id = row[0]; name = row[1]
                    branch_id = warehouse_id = None
                    if role == 'Branch':
                        cur.execute("SELECT Branch_ID FROM BRANCH WHERE Head_ID=%s LIMIT 1",(head_id,))
                        br = cur.fetchone()
                        if not br:
                            error = 'Access denied. Your branch has been removed. Contact Admin.'
                            return render_template('login.html', role=role, error=error)
                        branch_id = br[0]
                    elif role == 'Warehouse':
                        cur.execute("SELECT Warehouse_ID FROM WAREHOUSE WHERE Head_ID=%s LIMIT 1",(head_id,))
                        wh = cur.fetchone()
                        if not wh:
                            error = 'Access denied. Your warehouse has been removed. Contact Admin.'
                            return render_template('login.html', role=role, error=error)
                        warehouse_id = wh[0]
                    session.update({'role':role,'name':name,'head_id':head_id,
                                    'branch_id':branch_id,'warehouse_id':warehouse_id})
                    return redirect(url_for('dashboard'))
                error = 'Invalid email or password.'
        finally:
            cur.close(); db.close()
    return render_template('login.html', role=role, error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('landing'))

# ══════════════════════════════════════════════════════════════════
# CHANGE SET 1 – FORGOT PASSWORD / OTP RESET
# ══════════════════════════════════════════════════════════════════

@app.route('/forgot_password/<role>', methods=['GET','POST'])
def forgot_password(role):
    if role not in ['Admin','Branch','Warehouse','Stock_Allocation']:
        return redirect(url_for('landing'))
    error = None
    if request.method == 'POST':
        email = request.form.get('email','').strip()
        if not valid_email(email):
            error = 'Please enter a valid email address.'
        else:
            db = get_db(); cur = db.cursor()
            found = False
            try:
                if role == 'Admin':
                    cur.execute("SELECT Email FROM ADMIN WHERE Email=%s", (email,))
                    found = cur.fetchone() is not None
                 
                else:
                    cur.execute("SELECT Head_ID FROM HEAD WHERE Email=%s AND Status=%s",
                                (email, role))
                    row = cur.fetchone()
                    found = row is not None
            finally:
                cur.close(); db.close()

            if not found:
                error = 'No account found with this email for the selected role.'
            else:
                otp   = str(random.randint(100000, 999999))
                token = secrets.token_urlsafe(32)
                otp_store[email] = {
                    'otp':      otp,
                    'expires':  datetime.now() + timedelta(minutes=10),
                    'token':    token,
                    'role':     role,
                    'used':     False,
                    'attempts': 0
                }
                sent = send_otp_email(email, otp)
                if not sent:
                    print(f'[DEV] OTP for {email}: {otp}')
                send_whatsapp_otp_for_email(email, role, otp)
                return redirect(url_for('verify_otp', role=role, email=email))

    return render_template('forgot_password.html', role=role, error=error)


@app.route('/verify_otp/<role>', methods=['GET','POST'])
def verify_otp(role):
    if role not in ['Admin','Branch','Warehouse','Stock_Allocation']:
        return redirect(url_for('landing'))
    email = request.args.get('email','') or request.form.get('email','')
    error = None
    resent = request.args.get('resent','')

    if request.method == 'POST':
        digits = [request.form.get(f'd{i}','') for i in range(1,7)]
        entered_otp = ''.join(digits)
        entry = otp_store.get(email)

        if not entry:
            error = 'OTP expired or not found. Please request a new one.'
        elif datetime.now() > entry['expires']:
            otp_store.pop(email, None)
            error = 'OTP expired. Please request a new one.'
        elif entry['attempts'] >= 3:
            error = 'Too many incorrect attempts. Please request a new OTP.'
        elif entered_otp != entry['otp']:
            entry['attempts'] += 1
            remaining = 3 - entry['attempts']
            if remaining <= 0:
                error = 'Too many incorrect attempts. Please request a new OTP.'
            else:
                error = f'Incorrect OTP. {remaining} attempt(s) remaining.'
        else:
            entry['used'] = True
            return redirect(url_for('reset_password',
                                    token=entry['token'], email=email))

    masked = mask_email(email) if email else ''
    return render_template('verify_otp.html', role=role, email=email,
                           masked_email=masked, error=error, resent=resent)


@app.route('/resend_otp/<role>')
def resend_otp(role):
    email = request.args.get('email','')
    if not email or email not in otp_store:
        return redirect(url_for('forgot_password', role=role))
    otp   = str(random.randint(100000, 999999))
    token = secrets.token_urlsafe(32)
    otp_store[email].update({
        'otp':      otp,
        'token':    token,
        'expires':  datetime.now() + timedelta(minutes=10),
        'attempts': 0,
        'used':     False
    })
    sent = send_otp_email(email, otp)
    if not sent:
        print(f'[DEV] Resend OTP for {email}: {otp}')
    send_whatsapp_otp_for_email(email, role, otp)
    return redirect(url_for('verify_otp', role=role, email=email, resent='1'))


@app.route('/reset_password', methods=['GET','POST'])
def reset_password():
    email = request.args.get('email','') or request.form.get('email','')
    token = request.args.get('token','') or request.form.get('token','')
    entry = otp_store.get(email)

    # Validate token
    valid = (entry is not None and
             entry.get('token') == token and
             entry.get('used') is True)

    if request.method == 'POST':
        if not valid:
            return render_template('reset_password.html',
                                   email=email, token=token,
                                   error='Invalid or expired reset link.',
                                   success=None, role=None)
        pw  = request.form.get('password','')
        cpw = request.form.get('confirm_password','')
        if len(pw) < 4:
            return render_template('reset_password.html',
                                   email=email, token=token,
                                   error='Password must be at least 4 characters.',
                                   success=None, role=entry['role'])
        if pw != cpw:
            return render_template('reset_password.html',
                                   email=email, token=token,
                                   error='Passwords do not match.',
                                   success=None, role=entry['role'])
        db = get_db(); cur = db.cursor()
        try:
            if entry['role'] == 'Admin':
                cur.execute("UPDATE ADMIN SET Password=%s WHERE Email=%s", (pw, email))
            else:
                cur.execute("UPDATE HEAD SET Password=%s WHERE Email=%s AND Status=%s",
                            (pw, email, entry['role']))
            db.commit()
            role = entry['role']
            otp_store.pop(email, None)
            return render_template('reset_password.html',
                                   email=email, token=token,
                                   error=None,
                                   success='Password updated successfully! You can now log in.',
                                   role=role)
        except Exception as e:
            return render_template('reset_password.html',
                                   email=email, token=token,
                                   error=f'Error: {e}', success=None,
                                   role=entry['role'])
        finally:
            cur.close(); db.close()

    if not valid:
        return render_template('reset_password.html',
                               email=email, token=token,
                               error='Invalid or expired reset link.',
                               success=None, role=None)
    return render_template('reset_password.html',
                           email=email, token=token,
                           error=None, success=None, role=entry['role'])

# ══════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════
@app.route('/dashboard')
@login_required
def dashboard():
    role   = session['role']
    tables = ROLE_TABLES.get(role, [])
    db = get_db(); cur = db.cursor()
    stats = {}

    cur.execute("SELECT COUNT(*) FROM DEADSTOCK"); stats['deadstock'] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM BRANCH");    stats['branches']  = cur.fetchone()[0]
    cur.execute("SELECT City,Sustainable_Rating FROM BRANCH ORDER BY Sustainable_Rating DESC LIMIT 1")
    stats['top_branch'] = cur.fetchone()

    if role == 'Branch':
        branch_id = session.get('branch_id')
        stats.update({'my_branch':None,'report':None,
                      'branch_alloc_labels':[],'branch_alloc_values':[],'branch_alloc_colors':[]})
        if branch_id:
            cur.execute("""
                SELECT b.Branch_ID,b.City,b.Sustainable_Rating,b.Last_Audit,
                       b.Warehouse_ID,b.Head_ID,w.City,h2.Name,c.Contact_no
                FROM BRANCH b
                LEFT JOIN WAREHOUSE w ON b.Warehouse_ID=w.Warehouse_ID
                LEFT JOIN HEAD h2     ON w.Head_ID=h2.Head_ID
                LEFT JOIN CONTACTS c  ON h2.Head_ID=c.Head_ID
                WHERE b.Branch_ID=%s LIMIT 1""", (branch_id,))
            stats['my_branch'] = cur.fetchone()
            cur.execute("""SELECT Report_ID,Branch_ID,Items_Resold,Items_Recycled,
                                  Items_Donated,Items_Upcycled,Items_Rebranded,
                                  Items_Disposed,Estimated_Waste_Reduced
                           FROM REPORT WHERE Branch_ID=%s""", (branch_id,))
            stats['report'] = cur.fetchone()
            cur.execute("""SELECT sa.Allocation_Type,SUM(sa.Quantity)
                           FROM STOCK_ALLOCATION sa
                           JOIN DEADSTOCK d ON sa.Deadstock_ID=d.Deadstock_ID
                           WHERE d.Branch_ID=%s GROUP BY sa.Allocation_Type""", (branch_id,))
            rows = cur.fetchall()
            stats['branch_alloc_labels'] = [r[0] for r in rows]
            stats['branch_alloc_values'] = [int(r[1]) for r in rows]
            stats['branch_alloc_colors'] = [ALLOC_COLOR_MAP.get(r[0],'#999') for r in rows]
            cur.execute("""SELECT Rating, Recorded_At FROM RATING_HISTORY
                           WHERE Branch_ID=%s ORDER BY Recorded_At""", (branch_id,))
            history_rows = cur.fetchall()
            history_map = {branch_id: {
                'labels': [str(r[1]) for r in history_rows],
                'data':   [float(r[0]) for r in history_rows],
            }}
            stats['history_json'] = json.dumps(history_map)

    if role == 'Warehouse':
        wh_id = session.get('warehouse_id')
        stats.update({'warehouse_info':None,'sa_contact':None,'warehouse_capacity':[]})
        if wh_id:
            cur.execute("""SELECT w.Warehouse_ID,w.City,w.Last_Audit,w.Capacity,h.Name,c.Contact_no
                           FROM WAREHOUSE w JOIN HEAD h ON w.Head_ID=h.Head_ID
                           LEFT JOIN CONTACTS c ON h.Head_ID=c.Head_ID
                           WHERE w.Warehouse_ID=%s LIMIT 1""", (wh_id,))
            stats['warehouse_info'] = cur.fetchone()
            stats['warehouse_capacity'] = fetch_warehouse_capacity(cur, wh_id)
        cur.execute("""SELECT h.Name,c.Contact_no FROM HEAD h
                       LEFT JOIN CONTACTS c ON h.Head_ID=c.Head_ID
                       WHERE h.Status='Stock_Allocation' LIMIT 1""")
        stats['sa_contact'] = cur.fetchone()

    if role == 'Stock_Allocation':
        cur.execute("""SELECT Allocation_Type,COUNT(*),SUM(Quantity)
                       FROM STOCK_ALLOCATION GROUP BY Allocation_Type ORDER BY Allocation_Type""")
        stats['sustainability'] = cur.fetchall()
        cur.execute("""SELECT Allocation_Type,SUM(Quantity) FROM STOCK_ALLOCATION
                       GROUP BY Allocation_Type ORDER BY Allocation_Type""")
        stats['allocations']  = cur.fetchall()
        stats['alloc_colors'] = ALLOC_COLOR_MAP

    if role == 'Admin':
        cur.execute("""SELECT Allocation_Type,SUM(Quantity) FROM STOCK_ALLOCATION
                       GROUP BY Allocation_Type ORDER BY Allocation_Type""")
        stats['allocations']  = cur.fetchall()
        stats['alloc_colors'] = ALLOC_COLOR_MAP
        stats['warehouse_capacity'] = fetch_warehouse_capacity(cur)

    cur.close(); db.close()
    history_json = stats.get('history_json', '{}')
    return render_template('dashboard.html', tables=tables, stats=stats, history_json=history_json)

# ══════════════════════════════════════════════════════════════════
# SHOW TABLE  –  CHANGE SET 2: LEFT JOINs for Warehouse & Admin
# ══════════════════════════════════════════════════════════════════
@app.route('/show/<table_name>', methods=['GET'])
@login_required
def show_table(table_name):
    role   = session['role']
    tables = ROLE_TABLES.get(role, [])

    if table_name == 'HEAD':
        if role != 'Admin':
            return render_template('denied.html')
        return redirect(url_for('show_heads'))

    if table_name not in tables:
        return render_template('denied.html')

    db = get_db(); cur = db.cursor()
    rows = []; headers = []

    f_branch   = request.args.get('f_branch','')
    f_category = request.args.get('f_category','').strip()
    f_size     = request.args.get('f_size','')
    f_alloc    = request.args.get('f_alloc','')
    f_city     = request.args.get('f_city','').strip()
    f_role     = request.args.get('f_role','')
    f_sq       = request.args.get('f_sq','')
    f_sent_wh  = request.args.get('f_sent_wh','')
    f_sent_sa  = request.args.get('f_sent_sa','')
    f_sku      = request.args.get('f_sku','').strip()

    all_branches  = []
    all_cities_br = []
    all_cities_wh = []
    all_sizes     = ['XS','S','M','L','XL','XXL']
    all_alloc     = ['Recycle','Donate','Resell','Upcycle','Rebrand','Disposal']
    all_sq        = ['High','Medium','Low']
    all_roles     = ['Branch','Warehouse','Stock_Allocation']
    warehouse_capacity = []
    age_classes = []
    days_list = []

    if table_name == 'BRANCH':
        headers = ['Branch ID','Warehouse ID','Head ID','City','Last Audit','Sustainable Rating']
        if role == 'Branch':
            cur.execute("SELECT * FROM BRANCH WHERE Branch_ID=%s",(session.get('branch_id'),))
            rows = cur.fetchall()
        elif role == 'Warehouse':
            wh_id = session.get('warehouse_id')
            cur.execute("SELECT DISTINCT City FROM BRANCH WHERE Warehouse_ID=%s ORDER BY City",(wh_id,))
            all_cities_br = [r[0] for r in cur.fetchall()]
            q = "SELECT * FROM BRANCH WHERE Warehouse_ID=%s"; p = [wh_id]
            if f_city: q += " AND City=%s"; p.append(f_city)
            cur.execute(q + " ORDER BY Branch_ID", p)
            rows = cur.fetchall()
        else:
            # Admin: show active branches + deleted branches (with is_deleted flag)
            cur.execute("SELECT DISTINCT City FROM BRANCH ORDER BY City")
            all_cities_br = [r[0] for r in cur.fetchall()]
            # Each row: (Branch_ID, Warehouse_ID, Head_ID, City, Last_Audit, Sustainable_Rating, is_deleted)
            q = """
                SELECT Branch_ID, Warehouse_ID, Head_ID, City, Last_Audit, Sustainable_Rating, 0 AS is_deleted
                FROM BRANCH
                WHERE 1=1 {city_filter}
                UNION ALL
                SELECT Branch_ID, Warehouse_ID, Head_ID, City, Last_Audit, Sustainable_Rating, 1 AS is_deleted
                FROM DELETED_BRANCH
                WHERE 1=1 {city_filter2}
                ORDER BY is_deleted ASC, Branch_ID ASC
            """
            p = []
            city_clause = ""
            if f_city:
                city_clause = " AND City=%s"
                p.extend([f_city, f_city])
            final_q = q.replace('{city_filter}', city_clause).replace('{city_filter2}', city_clause)
            cur.execute(final_q, p)
            rows = cur.fetchall()

    elif table_name == 'WAREHOUSE':
        headers = ['Warehouse ID','Head ID','City','Last Audit','Capacity','Utilization']
        warehouse_capacity = fetch_warehouse_capacity(cur)
        cap_map = {r[0]: r for r in warehouse_capacity}
        if role == 'Warehouse':
            wh_id = session.get('warehouse_id')
            cur.execute("SELECT * FROM WAREHOUSE WHERE Warehouse_ID=%s",(wh_id,))
            raw_rows = cur.fetchall()
            rows = []
            for r in raw_rows:
                cap = cap_map.get(r[0])
                current = int(cap[3]) if cap else 0
                rows.append(tuple(r) + (current,))
        else:
            cur.execute("SELECT DISTINCT City FROM WAREHOUSE ORDER BY City")
            all_cities_wh = [r[0] for r in cur.fetchall()]
            q = "SELECT * FROM WAREHOUSE WHERE 1=1"; p = []
            if f_city: q += " AND City=%s"; p.append(f_city)
            cur.execute(q + " ORDER BY Warehouse_ID", p)
            raw_rows = cur.fetchall()
            rows = []
            for r in raw_rows:
                cap = cap_map.get(r[0])
                current = int(cap[3]) if cap else 0
                rows.append(tuple(r) + (current,))

    elif table_name == 'DEADSTOCK':
        # Branches for filter dropdown: active + deleted (for Admin history)
        if role == 'Admin':
            cur.execute("""
                SELECT Branch_ID, City FROM BRANCH
                UNION
                SELECT Branch_ID, CONCAT(City,' (Deleted)') FROM DELETED_BRANCH
                ORDER BY Branch_ID
            """)
        else:
            cur.execute("SELECT Branch_ID,City FROM BRANCH ORDER BY Branch_ID")
        all_branches = cur.fetchall()

        if role == 'Branch':
            headers=['ID','SKU','Branch ID','Category','Size','Material ID','Quantity',
                     'Date Added','Days in System','Sent To Warehouse','Sent To SA','Allocated Type']
            q="""SELECT Deadstock_ID, SKU, Branch_ID, Category, Size, Material_ID, Quantity,
                        Date_Added, Sent_To_Warehouse, Sent_To_SA, Allocated_Type
                 FROM DEADSTOCK WHERE Branch_ID=%s AND Sent_To_Warehouse=0"""
            p=[session.get('branch_id')]
            if f_category: q+=" AND Category LIKE %s"; p.append(f'%{f_category}%')
            if f_size:     q+=" AND Size=%s"; p.append(f_size)
            if f_sku:      q+=" AND SKU LIKE %s"; p.append(f'%{f_sku}%')
            cur.execute(q, p)

        elif role == 'Warehouse':
            wh_id = session.get('warehouse_id')
            headers=['ID','SKU','Branch ID','Branch','Category','Size','Material ID','Quantity',
                     'Date Added','Days in System','Sent To Warehouse','Sent To SA']
            q="""SELECT d.Deadstock_ID, d.SKU, d.Branch_ID,
                        COALESCE(b.City, db2.City, 'Deleted Branch') AS BranchCity,
                        d.Category, d.Size, d.Material_ID, d.Quantity, d.Date_Added,
                        d.Sent_To_Warehouse, d.Sent_To_SA
                 FROM DEADSTOCK d
                 LEFT JOIN BRANCH b ON d.Branch_ID = b.Branch_ID
                 LEFT JOIN DELETED_BRANCH db2 ON d.Branch_ID = db2.Branch_ID
                 WHERE d.Sent_To_Warehouse = 1 AND d.Sent_To_SA = 0
                   AND (b.Warehouse_ID = %s OR db2.Warehouse_ID = %s)"""
            p=[wh_id, wh_id]
            if f_category: q+=" AND d.Category LIKE %s"; p.append(f'%{f_category}%')
            if f_size:     q+=" AND d.Size=%s"; p.append(f_size)
            if f_sku:      q+=" AND d.SKU LIKE %s"; p.append(f'%{f_sku}%')
            cur.execute(q+" ORDER BY d.Deadstock_ID", p)

        elif role == 'Stock_Allocation':
            headers=['ID','SKU','Branch ID','Category','Size','Material ID','Quantity',
                     'Date Added','Days in System','Sent To WH','Sent To SA','Allocated Type']
            q="""SELECT Deadstock_ID, SKU, Branch_ID, Category, Size, Material_ID, Quantity,
                        Date_Added, Sent_To_Warehouse, Sent_To_SA, Allocated_Type
                 FROM DEADSTOCK WHERE Sent_To_SA=1"""
            p=[]
            if f_category: q+=" AND Category LIKE %s"; p.append(f'%{f_category}%')
            if f_size:     q+=" AND Size=%s"; p.append(f_size)
            if f_sku:      q+=" AND SKU LIKE %s"; p.append(f'%{f_sku}%')
            if f_alloc=='__none__': q+=" AND Allocated_Type IS NULL"
            elif f_alloc:           q+=" AND Allocated_Type=%s"; p.append(f_alloc)
            cur.execute(q+" ORDER BY Deadstock_ID", p)

        else:
            # Admin: LEFT JOIN with BRANCH so deleted-branch deadstock is always visible.
            # COALESCE shows "Deleted Branch" when branch no longer exists.
            headers=['ID','SKU','Branch ID','Branch','Category','Size','Material ID','Quantity',
                     'Date Added','Days in System','Sent To WH','Sent To SA','Allocated Type']
            q="""SELECT d.Deadstock_ID, d.SKU, d.Branch_ID,
                        COALESCE(b.City, db2.City, 'Deleted Branch') AS BranchCity,
                        d.Category, d.Size, d.Material_ID, d.Quantity, d.Date_Added,
                        d.Sent_To_Warehouse, d.Sent_To_SA, d.Allocated_Type
                 FROM DEADSTOCK d
                 LEFT JOIN BRANCH b ON d.Branch_ID = b.Branch_ID
                 LEFT JOIN DELETED_BRANCH db2 ON d.Branch_ID = db2.Branch_ID
                 WHERE 1=1"""
            p=[]
            if f_branch:   q+=" AND d.Branch_ID=%s"; p.append(f_branch)
            if f_category: q+=" AND d.Category LIKE %s"; p.append(f'%{f_category}%')
            if f_size:     q+=" AND d.Size=%s"; p.append(f_size)
            if f_sku:      q+=" AND d.SKU LIKE %s"; p.append(f'%{f_sku}%')
            if f_alloc=='__none__': q+=" AND d.Allocated_Type IS NULL"
            elif f_alloc:           q+=" AND d.Allocated_Type=%s"; p.append(f_alloc)
            if f_sent_wh:  q+=" AND d.Sent_To_Warehouse=%s"; p.append(f_sent_wh)
            if f_sent_sa:  q+=" AND d.Sent_To_SA=%s"; p.append(f_sent_sa)
            cur.execute(q+" ORDER BY d.Deadstock_ID", p)
        rows = cur.fetchall()
        if rows:
            date_idx = 7 if role in ('Branch', 'Stock_Allocation') else 8
            age_classes, days_list = compute_deadstock_meta(rows, date_idx)

    elif table_name == 'STOCK_ALLOCATION':
        headers=['Allocation ID','Deadstock ID','Head ID','Type','Quantity','Allocated At']
        cur.execute("SELECT Branch_ID,City FROM BRANCH ORDER BY Branch_ID")
        all_branches = cur.fetchall()
        q="""SELECT sa.* FROM STOCK_ALLOCATION sa
             JOIN DEADSTOCK d ON sa.Deadstock_ID=d.Deadstock_ID WHERE 1=1"""
        p=[]
        if f_branch: q+=" AND d.Branch_ID=%s"; p.append(f_branch)
        if f_alloc:  q+=" AND sa.Allocation_Type=%s"; p.append(f_alloc)
        cur.execute(q+" ORDER BY sa.Allocated_At DESC", p)
        rows = cur.fetchall()

    elif table_name == 'REPORT':
        if role == 'Branch':
            headers=['Report ID','Branch ID','Resold','Recycled','Donated','Upcycled','Rebranded','Disposed','Waste Reduced (kg)']
            cur.execute("""SELECT Report_ID,Branch_ID,Items_Resold,Items_Recycled,
                                  Items_Donated,Items_Upcycled,Items_Rebranded,
                                  Items_Disposed,Estimated_Waste_Reduced
                           FROM REPORT WHERE Branch_ID=%s""",(session.get('branch_id'),))
        else:
            headers=['Report ID','Branch City','Resold','Recycled','Donated','Upcycled','Rebranded','Disposed','Waste Reduced (kg)']
            cur.execute("""SELECT r.Report_ID,b.City,r.Items_Resold,r.Items_Recycled,
                                  r.Items_Donated,r.Items_Upcycled,r.Items_Rebranded,
                                  r.Items_Disposed,r.Estimated_Waste_Reduced
                           FROM REPORT r JOIN BRANCH b ON r.Branch_ID=b.Branch_ID
                           ORDER BY b.City""")
        rows = cur.fetchall()

    elif table_name == 'CONTACTS':
        headers=['Contact ID','Head ID','Contact No']
        q="""SELECT c.Contact_ID,c.Head_ID,c.Contact_no
             FROM CONTACTS c JOIN HEAD h ON c.Head_ID=h.Head_ID WHERE 1=1"""
        p=[]
        if f_role: q+=" AND h.Status=%s"; p.append(f_role)
        cur.execute(q+" ORDER BY c.Head_ID", p)
        rows = cur.fetchall()

    elif table_name == 'MATERIAL':
        headers=['Material ID','Material Name','Sustainability Quality']
        q="SELECT * FROM MATERIAL WHERE 1=1"; p=[]
        if f_sq: q+=" AND Sustainability_Quality=%s"; p.append(f_sq)
        cur.execute(q+" ORDER BY Material_ID", p)
        rows = cur.fetchall()

    elif table_name == 'RECYCLE_RECORD':
        headers=['Recycle ID','Deadstock ID','Branch ID','Category',
                 'Size','Material ID','Quantity','Material Name',
                 'Sustainability','Allocated At']
        cur.execute("""
            SELECT Recycle_ID, Deadstock_ID, Branch_ID, Category,
                   Size, Material_ID, Quantity, Material_Name,
                   Sustainability_Quality, Allocated_At
            FROM RECYCLE_RECORD
            ORDER BY Allocated_At DESC
        """)
        rows = cur.fetchall()

    cur.close(); db.close()
    branch_delete_error = session.pop('branch_delete_error', None)
    return render_template('show_tables.html',
                           table_name=table_name, headers=headers, rows=rows,
                           f_branch=f_branch, f_category=f_category, f_size=f_size,
                           f_alloc=f_alloc, f_city=f_city, f_role=f_role,
                           f_sq=f_sq, f_sent_wh=f_sent_wh, f_sent_sa=f_sent_sa,
                           f_sku=f_sku,
                           all_branches=all_branches, all_cities_br=all_cities_br,
                           all_cities_wh=all_cities_wh, all_roles=all_roles,
                           all_sizes=all_sizes, all_alloc=all_alloc, all_sq=all_sq,
                           branch_delete_error=branch_delete_error,
                           warehouse_capacity=warehouse_capacity,
                           age_classes=age_classes, days_list=days_list)

    
# ══════════════════════════════════════════════════════════════════
# SHOW HEADS
# ══════════════════════════════════════════════════════════════════
@app.route('/show/heads')
@login_required
@role_required('Admin')
def show_heads():
    f_role = request.args.get('f_role','')
    f_name = request.args.get('f_name','').strip()
    db = get_db(); cur = db.cursor()
    q="""SELECT h.Head_ID,h.Name,h.Email,h.Status,
                GROUP_CONCAT(c.Contact_no SEPARATOR ', ')
         FROM HEAD h LEFT JOIN CONTACTS c ON h.Head_ID=c.Head_ID WHERE 1=1"""
    p=[]
    if f_role: q+=" AND h.Status=%s"; p.append(f_role)
    if f_name: q+=" AND h.Name LIKE %s"; p.append(f'%{f_name}%')
    q+=" GROUP BY h.Head_ID,h.Name,h.Email,h.Status ORDER BY h.Status,h.Name"
    cur.execute(q, p)
    rows = cur.fetchall()
    cur.close(); db.close()
    return render_template('show_heads.html', rows=rows, f_role=f_role, f_name=f_name,
                           all_roles=['Branch','Warehouse','Stock_Allocation'])

# ══════════════════════════════════════════════════════════════════
# ADMIN BRANCH REPORT
# ══════════════════════════════════════════════════════════════════
@app.route('/admin_branch_report')
@login_required
@role_required('Admin')
def admin_branch_report():
    db = get_db(); cur = db.cursor()
    # LEFT JOIN so reports for deleted branches still appear
    cur.execute("""
        SELECT r.Branch_ID,
               COALESCE(b.City, db2.City, 'Deleted Branch') AS City,
               r.Items_Resold, r.Items_Recycled, r.Items_Donated,
               r.Items_Upcycled, r.Items_Rebranded, r.Items_Disposed,
               r.Estimated_Waste_Reduced
        FROM REPORT r
        LEFT JOIN BRANCH b         ON r.Branch_ID = b.Branch_ID
        LEFT JOIN DELETED_BRANCH db2 ON r.Branch_ID = db2.Branch_ID
        ORDER BY r.Branch_ID
    """)
    rows = cur.fetchall()
    cur.close(); db.close()
    return render_template('admin_branch_report.html', rows=rows)

# ══════════════════════════════════════════════════════════════════
# UPDATE WAREHOUSE
# ══════════════════════════════════════════════════════════════════
@app.route('/update_warehouse/<int:wh_id>', methods=['GET','POST'])
@login_required
@role_required('Admin')
def update_warehouse(wh_id):
    db = get_db(); cur = db.cursor(); msg = None
    if request.method == 'POST':
        f = request.form
        try:
            cur.execute("""UPDATE WAREHOUSE SET Head_ID=%s,City=%s,Last_Audit=%s,Capacity=%s
                           WHERE Warehouse_ID=%s""",
                        (f['head_id'],f['city'],f['last_audit'],f['capacity'],wh_id))
            db.commit(); msg = ('success','Warehouse updated successfully.')
        except Exception as e:
            msg = ('error',f'Error: {e}')
    cur.execute("SELECT * FROM WAREHOUSE WHERE Warehouse_ID=%s",(wh_id,))
    warehouse = cur.fetchone()
    cur.execute("SELECT Head_ID,Name FROM HEAD WHERE Status='Warehouse' ORDER BY Name")
    heads = cur.fetchall()
    cur.close(); db.close()
    return render_template('update_warehouse.html', warehouse=warehouse, heads=heads, msg=msg)

# ══════════════════════════════════════════════════════════════════
# ADD ROUTES
# ══════════════════════════════════════════════════════════════════
@app.route('/add/deadstock', methods=['GET','POST'])
@login_required
@role_required('Branch')
def add_deadstock():
    db = get_db(); cur = db.cursor(); msg = None
    if request.method == 'POST':
        f = request.form
        try:
            year = datetime.now().year
            cat_abbrev = f['category'].upper().replace(' ', '-')[:6]
            branch_id = session['branch_id']
            cur.execute("SELECT COUNT(*) FROM DEADSTOCK WHERE Branch_ID=%s AND SKU LIKE %s",
                        (branch_id, f'%-{year}%'))
            seq = cur.fetchone()[0] + 1
            sku = f"{cat_abbrev}-{branch_id}-{f['size']}-{year}{seq:04d}"
            cur.execute("""INSERT INTO DEADSTOCK
                               (SKU,Branch_ID,Category,Size,Material_ID,Quantity,Date_Added,
                                Sent_To_Warehouse,Sent_To_SA)
                           VALUES (%s,%s,%s,%s,%s,%s,CURDATE(),0,0)""",
                        (sku, branch_id, f['category'], f['size'], f['material_id'], f['quantity']))
            db.commit(); msg = ('success',f'Deadstock item added successfully. SKU: {sku}')
        except Exception as e:
            msg = ('error',f'Error: {e}')
    cur.execute("SELECT Material_ID,Material_Name FROM MATERIAL ORDER BY Material_Name")
    materials = cur.fetchall()
    cur.close(); db.close()
    return render_template('add_deadstock.html', materials=materials, msg=msg)

# CHANGE SET 3: Only unassigned Branch heads in dropdown; enforce one-head-one-branch
@app.route('/add/branch', methods=['GET','POST'])
@login_required
@role_required('Admin')
def add_branch():
    db = get_db(); cur = db.cursor(); msg = None
    if request.method == 'POST':
        f = request.form
        wh_id   = f.get('warehouse_id') or None
        head_id = f.get('head_id') or None
        if not wh_id:
            msg = ('error','Warehouse is required.')
        elif not head_id:
            msg = ('error','Branch Head is required.')
        else:
            # CHANGE SET 3: Check one-head-one-branch
            cur.execute("SELECT Branch_ID FROM BRANCH WHERE Head_ID=%s LIMIT 1", (head_id,))
            if cur.fetchone():
                msg = ('error','This Head is already managing a branch. One head can only manage one branch at a time.')
            else:
                try:
                    cur.execute("""INSERT INTO BRANCH (Warehouse_ID,Head_ID,City,Last_Audit)
                                   VALUES (%s,%s,%s,CURDATE())""",
                                (wh_id, head_id, f['city']))
                    new_branch_id = cur.lastrowid
                    cur.execute("""INSERT INTO REPORT
                                       (Branch_ID,Items_Resold,Items_Recycled,Items_Donated,
                                        Items_Upcycled,Items_Rebranded,Items_Disposed,Estimated_Waste_Reduced)
                                   VALUES (%s,0,0,0,0,0,0,0.00)""", (new_branch_id,))
                    db.commit(); msg = ('success','Branch added successfully.')
                except Exception as e:
                    msg = ('error',f'Error: {e}')
    cur.execute("SELECT Warehouse_ID,City FROM WAREHOUSE ORDER BY City")
    warehouses = cur.fetchall()
    # CHANGE SET 3: Only Branch heads NOT already assigned to any branch
    cur.execute("""SELECT h.Head_ID,h.Name FROM HEAD h
                   WHERE h.Status='Branch'
                   AND h.Head_ID NOT IN (SELECT Head_ID FROM BRANCH)
                   ORDER BY h.Name""")
    heads = cur.fetchall()
    cur.close(); db.close()
    return render_template('add_branch.html', warehouses=warehouses, heads=heads, msg=msg)

# CHANGE SET 3: Only unassigned Warehouse heads in dropdown; enforce one-head-one-warehouse
@app.route('/add/warehouse', methods=['GET','POST'])
@login_required
@role_required('Admin')
def add_warehouse():
    db = get_db(); cur = db.cursor(); msg = None
    if request.method == 'POST':
        f = request.form
        head_id = f.get('head_id') or None
        if not head_id:
            msg = ('error','Warehouse Head is required.')
        else:
            # CHANGE SET 3: Check one-head-one-warehouse
            cur.execute("SELECT Warehouse_ID FROM WAREHOUSE WHERE Head_ID=%s LIMIT 1", (head_id,))
            if cur.fetchone():
                msg = ('error','This Head is already managing a warehouse. One head can only manage one warehouse at a time.')
            else:
                try:
                    cur.execute("""INSERT INTO WAREHOUSE (Head_ID,City,Last_Audit,Capacity)
                                   VALUES (%s,%s,CURDATE(),%s)""",
                                (head_id, f['city'], f['capacity']))
                    db.commit(); msg = ('success','Warehouse added successfully.')
                except Exception as e:
                    msg = ('error',f'Error: {e}')
    # CHANGE SET 3: Only Warehouse heads NOT already assigned to any warehouse
    cur.execute("""SELECT h.Head_ID,h.Name FROM HEAD h
                   WHERE h.Status='Warehouse'
                   AND h.Head_ID NOT IN (SELECT Head_ID FROM WAREHOUSE)
                   ORDER BY h.Name""")
    heads = cur.fetchall()
    cur.close(); db.close()
    return render_template('add_warehouse.html', heads=heads, msg=msg)

@app.route('/add/head', methods=['GET','POST'])
@login_required
@role_required('Admin')
def add_head():
    db = get_db(); cur = db.cursor(); msg = None
    if request.method == 'POST':
        f       = request.form
        email   = f.get('email','').strip()
        contact = f.get('contact_no','').strip()
        if not valid_email(email):
            msg = ('error','Invalid email address format.')
        elif contact and not valid_phone(contact):
            msg = ('error','Contact number must be exactly 10 digits (numbers only).')
        else:
            try:
                cur.execute("INSERT INTO HEAD (Name,Email,Status,Password) VALUES (%s,%s,%s,%s)",
                            (f['name'],email,f['status'],f['password']))
                new_id = cur.lastrowid
                if contact:
                    cur.execute("INSERT INTO CONTACTS (Head_ID,Contact_no) VALUES (%s,%s)",(new_id,contact))
                db.commit(); msg = ('success',f'Head added with ID {new_id}.')
            except Exception as e:
                msg = ('error',f'Error: {e}')
    cur.close(); db.close()
    return render_template('add_head.html', msg=msg)

@app.route('/add/contacts', methods=['GET','POST'])
@login_required
@role_required('Admin')
def add_contacts():
    db = get_db(); cur = db.cursor(); msg = None
    selected_head = request.form.get('head_id','') or request.args.get('head_id','')
    existing_contacts = []
    if request.method == 'POST':
        contact = request.form.get('contact_no','').strip()
        head_id = request.form.get('head_id','')
        if not valid_phone(contact):
            msg = ('error','Contact number must be exactly 10 digits (numbers only).')
        else:
            try:
                cur.execute("INSERT INTO CONTACTS (Head_ID,Contact_no) VALUES (%s,%s)",(head_id,contact))
                db.commit(); msg = ('success','Contact added successfully.')
                selected_head = head_id
            except Exception as e:
                msg = ('error',f'Error: {e}')
    if selected_head:
        cur.execute("""SELECT Contact_ID,Contact_no FROM CONTACTS
                       WHERE Head_ID=%s ORDER BY Contact_ID""", (selected_head,))
        existing_contacts = cur.fetchall()
    cur.execute("SELECT Head_ID,Name,Status FROM HEAD ORDER BY Status,Name")
    heads = cur.fetchall()
    cur.close(); db.close()
    return render_template('add_contacts.html', heads=heads, msg=msg,
                           selected_head=selected_head, existing_contacts=existing_contacts)

@app.route('/add/material', methods=['GET','POST'])
@login_required
@role_required('Admin', 'Warehouse', 'Stock_Allocation')
def add_material():
    db = get_db(); cur = db.cursor(); msg = None
    if request.method == 'POST':
        f = request.form
        try:
            cur.execute("INSERT INTO MATERIAL (Material_Name,Sustainability_Quality) VALUES (%s,%s)",
                        (f['material_name'],f['sustainability_quality']))
            db.commit(); msg = ('success','Material added successfully.')
        except Exception as e:
            msg = ('error',f'Error: {e}')
    cur.close(); db.close()
    return render_template('add_material.html', msg=msg)

# ══════════════════════════════════════════════════════════════════
# REPORT – Branch read-only
# ══════════════════════════════════════════════════════════════════
@app.route('/view_report')
@login_required
@role_required('Branch')
def view_report():
    db = get_db(); cur = db.cursor()
    cur.execute("""SELECT Report_ID,Branch_ID,Items_Resold,Items_Recycled,
                          Items_Donated,Items_Upcycled,Items_Rebranded,
                          Items_Disposed,Estimated_Waste_Reduced
                   FROM REPORT WHERE Branch_ID=%s""", (session.get('branch_id'),))
    report = cur.fetchone()
    cur.close(); db.close()
    return render_template('report.html', report=report, msg=None)

# ══════════════════════════════════════════════════════════════════
# SA BRANCH REPORT
# ══════════════════════════════════════════════════════════════════
@app.route('/sa_branch_report')
@login_required
@role_required('Stock_Allocation')
def sa_branch_report():
    db = get_db(); cur = db.cursor()
    # LEFT JOIN so allocation data for deleted branches still visible.
    # r[9] = Branch_ID used by template for PDF download link.
    cur.execute("""
        SELECT COALESCE(b.City, db2.City, 'Deleted Branch') AS City,
               SUM(CASE WHEN sa.Allocation_Type='Recycle'  THEN sa.Quantity ELSE 0 END),
               SUM(CASE WHEN sa.Allocation_Type='Donate'   THEN sa.Quantity ELSE 0 END),
               SUM(CASE WHEN sa.Allocation_Type='Resell'   THEN sa.Quantity ELSE 0 END),
               SUM(CASE WHEN sa.Allocation_Type='Upcycle'  THEN sa.Quantity ELSE 0 END),
               SUM(CASE WHEN sa.Allocation_Type='Rebrand'  THEN sa.Quantity ELSE 0 END),
               SUM(CASE WHEN sa.Allocation_Type='Disposal' THEN sa.Quantity ELSE 0 END),
               SUM(sa.Quantity),
               r.Estimated_Waste_Reduced,
               d.Branch_ID
        FROM STOCK_ALLOCATION sa
        JOIN  DEADSTOCK d      ON sa.Deadstock_ID = d.Deadstock_ID
        LEFT JOIN BRANCH b     ON d.Branch_ID = b.Branch_ID
        LEFT JOIN DELETED_BRANCH db2 ON d.Branch_ID = db2.Branch_ID
        LEFT JOIN REPORT r     ON d.Branch_ID = r.Branch_ID
        GROUP BY d.Branch_ID, b.City, db2.City, r.Estimated_Waste_Reduced
        ORDER BY City""")
    rows = cur.fetchall()
    cur.execute("SELECT Allocation_Type,SUM(Quantity) FROM STOCK_ALLOCATION GROUP BY Allocation_Type")
    alloc_totals = cur.fetchall()
    cur.close(); db.close()
    return render_template('sa_branch_report.html', rows=rows,
                           alloc_totals=alloc_totals, alloc_colors=ALLOC_COLOR_MAP)

# ══════════════════════════════════════════════════════════════════
# SEND ROUTES
# ══════════════════════════════════════════════════════════════════
@app.route('/send_to_warehouse/<int:deadstock_id>', methods=['POST'])
@login_required
@role_required('Branch')
def send_to_warehouse(deadstock_id):
    branch_id = session.get('branch_id')
    db = get_db(); cur = db.cursor()
    try:
        cur.execute("""SELECT Deadstock_ID FROM DEADSTOCK
                       WHERE Deadstock_ID=%s AND Branch_ID=%s AND Sent_To_Warehouse=0""",
                    (deadstock_id,branch_id))
        if cur.fetchone():
            cur.execute("UPDATE DEADSTOCK SET Sent_To_Warehouse=1 WHERE Deadstock_ID=%s",(deadstock_id,))
            db.commit()
            alert_warehouse_head_items_sent(branch_id)
    except Exception:
        pass
    finally:
        cur.close(); db.close()
    return redirect(url_for('show_table', table_name='DEADSTOCK'))

@app.route('/bulk/send_warehouse', methods=['POST'])
@login_required
@role_required('Branch')
def bulk_send_warehouse():
    ids = request.form.getlist('ids')
    branch_id = session.get('branch_id')
    if ids:
        db = get_db(); cur = db.cursor()
        try:
            fmt = ','.join(['%s'] * len(ids))
            cur.execute(
                f"""UPDATE DEADSTOCK
                    SET Sent_To_Warehouse = 1
                    WHERE Deadstock_ID IN ({fmt})
                      AND Branch_ID = %s
                      AND Sent_To_Warehouse = 0""",
                (*ids, branch_id)
            )
            db.commit()
            alert_warehouse_head_items_sent(branch_id)
        finally:
            cur.close(); db.close()
    return redirect(url_for('show_table', table_name='DEADSTOCK'))

@app.route('/bulk/send_sa', methods=['POST'])
@login_required
@role_required('Warehouse')
def bulk_send_sa():
    ids = request.form.getlist('ids')
    warehouse_id = session.get('warehouse_id')
    if ids and warehouse_id:
        db = get_db(); cur = db.cursor()
        try:
            fmt = ','.join(['%s'] * len(ids))
            cur.execute(
                f"""UPDATE DEADSTOCK d
                    LEFT JOIN BRANCH b ON d.Branch_ID = b.Branch_ID
                    LEFT JOIN DELETED_BRANCH db2 ON d.Branch_ID = db2.Branch_ID
                    SET d.Sent_To_SA = 1
                    WHERE d.Deadstock_ID IN ({fmt})
                      AND (b.Warehouse_ID = %s OR db2.Warehouse_ID = %s)
                      AND d.Sent_To_Warehouse = 1
                      AND d.Sent_To_SA = 0""",
                (*ids, warehouse_id, warehouse_id)
            )
            db.commit()
            alert_sa_head_items_sent(warehouse_id)
        finally:
            cur.close(); db.close()
    return redirect(url_for('show_table', table_name='DEADSTOCK'))

@app.route('/send_to_sa/<int:deadstock_id>', methods=['POST'])
@login_required
@role_required('Warehouse')
def send_to_sa(deadstock_id):
    wh_id = session.get('warehouse_id')
    db = get_db(); cur = db.cursor()
    try:
        # LEFT JOIN handles orphaned deadstock from deleted branches.
        # Also check DELETED_BRANCH so warehouse head can still send it to SA.
        cur.execute("""SELECT d.Deadstock_ID FROM DEADSTOCK d
                       LEFT JOIN BRANCH b ON d.Branch_ID=b.Branch_ID
                       LEFT JOIN DELETED_BRANCH db2 ON d.Branch_ID=db2.Branch_ID
                       WHERE d.Deadstock_ID=%s
                         AND (b.Warehouse_ID=%s OR db2.Warehouse_ID=%s)
                         AND d.Sent_To_Warehouse=1 AND d.Sent_To_SA=0""",
                    (deadstock_id, wh_id, wh_id))
        if cur.fetchone():
            cur.execute("UPDATE DEADSTOCK SET Sent_To_SA=1 WHERE Deadstock_ID=%s",(deadstock_id,))
            db.commit()
            alert_sa_head_items_sent(wh_id)
    except Exception:
        pass
    finally:
        cur.close(); db.close()
    return redirect(url_for('show_table', table_name='DEADSTOCK'))

# ══════════════════════════════════════════════════════════════════
# ALLOCATE
# ══════════════════════════════════════════════════════════════════
@app.route('/allocate/<int:deadstock_id>', methods=['POST'])
@login_required
@role_required('Stock_Allocation')
def allocate(deadstock_id):
    alloc_type  = request.form.get('alloc_type','')
    valid_types = ['Recycle','Donate','Resell','Upcycle','Rebrand','Disposal']
    if alloc_type not in valid_types:
        return redirect(url_for('show_table', table_name='DEADSTOCK'))
    head_id = session.get('head_id')
    db = get_db(); cur = db.cursor()
    try:
        cur.execute("""SELECT Quantity,Branch_ID FROM DEADSTOCK
                       WHERE Deadstock_ID=%s AND Sent_To_SA=1 AND Allocated_Type IS NULL""",
                    (deadstock_id,))
        row = cur.fetchone()
        if row:
            qty = row[0]; branch_id = row[1]
            cur.execute("UPDATE DEADSTOCK SET Allocated_Type=%s WHERE Deadstock_ID=%s",
                        (alloc_type,deadstock_id))
            cur.execute("""INSERT INTO STOCK_ALLOCATION (Deadstock_ID,Head_ID,Allocation_Type,Quantity)
                           VALUES (%s,%s,%s,%s)""",
                        (deadstock_id,head_id,alloc_type,qty))
            col_map = {
                'Resell':   ('Items_Resold',    0.6),
                'Recycle':  ('Items_Recycled',  0.8),
                'Donate':   ('Items_Donated',   0.5),
                'Upcycle':  ('Items_Upcycled',  0.9),
                'Rebrand':  ('Items_Rebranded', 0.7),
                'Disposal': ('Items_Disposed',  0.0),
            }
            col, factor = col_map.get(alloc_type, ('Items_Disposed', 0.0))
            cur.execute("""SELECT Report_ID,Estimated_Waste_Reduced FROM REPORT
                           WHERE Branch_ID=%s""", (branch_id,))
            rep = cur.fetchone()
            if rep:
                new_waste = float(rep[1] or 0) + (qty * factor)
                cur.execute(f"""UPDATE REPORT SET {col}={col}+%s,
                                                   Estimated_Waste_Reduced=%s
                               WHERE Report_ID=%s""",
                            (qty, round(new_waste,2), rep[0]))
            db.commit()
    except Exception:
        pass
    finally:
        cur.close(); db.close()
    return redirect(url_for('show_table', table_name='DEADSTOCK'))

@app.route('/bulk/allocate', methods=['POST'])
@login_required
@role_required('Stock_Allocation')
def bulk_allocate():
    ids = request.form.getlist('ids')
    alloc_type = request.form.get('alloc_type')
    valid_types = ['Recycle','Donate','Resell','Upcycle','Rebrand','Disposal']
    if not ids or alloc_type not in valid_types:
        return redirect(url_for('show_table', table_name='DEADSTOCK'))
    head_id = session.get('head_id')
    db = get_db(); cur = db.cursor()
    try:
        for did in ids:
            cur.execute("""SELECT Quantity, Branch_ID FROM DEADSTOCK
                           WHERE Deadstock_ID=%s AND Sent_To_SA=1 AND Allocated_Type IS NULL""", (did,))
            row = cur.fetchone()
            if row:
                qty, branch_id = row
                cur.execute("UPDATE DEADSTOCK SET Allocated_Type=%s WHERE Deadstock_ID=%s",
                            (alloc_type, did))
                cur.execute("""INSERT INTO STOCK_ALLOCATION (Deadstock_ID,Head_ID,Allocation_Type,Quantity)
                               VALUES (%s,%s,%s,%s)""", (did, head_id, alloc_type, qty))
                col_map = {'Resell':('Items_Resold',0.6),'Recycle':('Items_Recycled',0.8),
                           'Donate':('Items_Donated',0.5),'Upcycle':('Items_Upcycled',0.9),
                           'Rebrand':('Items_Rebranded',0.7),'Disposal':('Items_Disposed',0.0)}
                col, factor = col_map.get(alloc_type, ('Items_Disposed', 0.0))
                cur.execute("SELECT Report_ID, Estimated_Waste_Reduced FROM REPORT WHERE Branch_ID=%s",
                            (branch_id,))
                rep = cur.fetchone()
                if rep:
                    new_waste = float(rep[1] or 0) + (qty * factor)
                    cur.execute(f"UPDATE REPORT SET {col}={col}+%s, Estimated_Waste_Reduced=%s WHERE Report_ID=%s",
                                (qty, round(new_waste, 2), rep[0]))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        cur.close(); db.close()
    return redirect(url_for('show_table', table_name='DEADSTOCK'))

# ══════════════════════════════════════════════════════════════════
# UPDATE HEAD PASSWORD
# ══════════════════════════════════════════════════════════════════
@app.route('/update_head/<int:head_id>', methods=['GET','POST'])
@login_required
@role_required('Admin')
def update_head(head_id):
    db = get_db(); cur = db.cursor(); msg = None
    if request.method == 'POST':
        pw  = request.form.get('password','')
        cpw = request.form.get('confirm_password','')
        if not pw:
            msg = ('error','Password cannot be empty.')
        elif pw != cpw:
            msg = ('error','Passwords do not match.')
        elif len(pw) < 4:
            msg = ('error','Password must be at least 4 characters.')
        else:
            try:
                cur.execute("UPDATE HEAD SET Password=%s WHERE Head_ID=%s",(pw,head_id))
                db.commit(); msg = ('success','Password updated successfully.')
            except Exception as e:
                msg = ('error',f'Error: {e}')
    cur.execute("SELECT Head_ID,Name,Email,Status FROM HEAD WHERE Head_ID=%s",(head_id,))
    head = cur.fetchone()
    cur.close(); db.close()
    return render_template('update_head.html', head=head, msg=msg)

# ══════════════════════════════════════════════════════════════════
# DELETE BRANCH
# ══════════════════════════════════════════════════════════════════
@app.route('/delete/branch/<int:branch_id>', methods=['POST'])
@login_required
@role_required('Admin')
def delete_branch(branch_id):
    """
    Branch deletion logic:
      STEP 1 – Mark all unsent deadstock (Sent_To_Warehouse=0) as sent-to-warehouse
               so the warehouse head can still process it. Nothing is deleted.
      STEP 2 – Deadstock already at warehouse / SA / allocated → untouched.
      STEP 3 – Archive branch to DELETED_BRANCH.
      STEP 4 – Remove the FK reference: set Branch_ID=NULL in DEADSTOCK temporarily
               only to satisfy the DB FK constraint; the DELETED_BRANCH archive
               preserves all branch info and the original Branch_ID is kept in
               DELETED_BRANCH for reporting lookups.
               NOTE: If you removed the DEADSTOCK Branch_ID FK (ON DELETE SET NULL
               or dropped it entirely) in your ALTER script, STEP 4 is not needed.
      STEP 5 – Delete the BRANCH row.

    ⚠  PREREQUISITE: The FK on DEADSTOCK(Branch_ID) must allow branch deletion.
       Run this in MySQL before using this route:
         ALTER TABLE DEADSTOCK DROP FOREIGN KEY <fk_name>;
         ALTER TABLE DEADSTOCK ADD CONSTRAINT fk_deadstock_branch
           FOREIGN KEY (Branch_ID) REFERENCES BRANCH(Branch_ID) ON DELETE SET NULL;
       This lets Branch_ID in DEADSTOCK become NULL when a branch is deleted,
       while DELETED_BRANCH preserves all branch history for reporting.
    """
    db = get_db(); cur = db.cursor()
    try:
        cur.execute("SELECT * FROM BRANCH WHERE Branch_ID=%s", (branch_id,))
        row = cur.fetchone()
        if not row:
            # Already deleted or never existed — just redirect cleanly
            return redirect(url_for('show_table', table_name='BRANCH'))

        # STEP 1: Mark all unsent deadstock as sent-to-warehouse
        cur.execute("""UPDATE DEADSTOCK
                       SET Sent_To_Warehouse = 1
                       WHERE Branch_ID = %s AND Sent_To_Warehouse = 0""",
                    (branch_id,))

        # STEP 3: Archive branch record to DELETED_BRANCH
        # (ignore duplicate-key error in case already archived)
        cur.execute("""INSERT IGNORE INTO DELETED_BRANCH
                           (Branch_ID, Warehouse_ID, Head_ID, City,
                            Last_Audit, Sustainable_Rating)
                       VALUES (%s, %s, %s, %s, %s, %s)""", row)

        # STEP 5: Delete the BRANCH row.
        # If your FK is ON DELETE SET NULL, DEADSTOCK.Branch_ID will become NULL
        # automatically.  The DELETED_BRANCH table preserves history for reports.
        cur.execute("DELETE FROM BRANCH WHERE Branch_ID=%s", (branch_id,))
        db.commit()

    except Exception as e:
        db.rollback()
        print(f'[DELETE BRANCH ERROR] Branch #{branch_id}: {e}')
        # Surface the error briefly in the session so the template can show it
        session['branch_delete_error'] = (
            f'Could not delete Branch #{branch_id}. '
            f'Ensure the DEADSTOCK FK is set to ON DELETE SET NULL '
            f'(see ALTER script). DB error: {e}'
        )
    finally:
        cur.close(); db.close()

    return redirect(url_for('show_table', table_name='BRANCH'))

# ══════════════════════════════════════════════════════════════════
# DELETE WAREHOUSE
# ══════════════════════════════════════════════════════════════════
@app.route('/delete/warehouse/<int:wh_id>', methods=['POST'])
@login_required
@role_required('Admin')
def delete_warehouse(wh_id):
    return redirect(url_for('reassign_warehouse', wh_id=wh_id))

@app.route('/delete/warehouse/reassign/<int:wh_id>', methods=['GET','POST'])
@login_required
@role_required('Admin')
def reassign_warehouse(wh_id):
    db = get_db(); cur = db.cursor(); msg = None
    if request.method == 'POST':
        try:
            cur.execute("SELECT Branch_ID FROM BRANCH WHERE Warehouse_ID=%s",(wh_id,))
            branch_ids = [r[0] for r in cur.fetchall()]
            for bid in branch_ids:
                new_wh = request.form.get(f'new_warehouse_{bid}','')
                if new_wh:
                    cur.execute("UPDATE BRANCH SET Warehouse_ID=%s WHERE Branch_ID=%s",(new_wh,bid))
            if branch_ids:
                fmt = ','.join(['%s'] * len(branch_ids))
                cur.execute(f"""UPDATE DEADSTOCK SET Sent_To_SA=1
                                WHERE Branch_ID IN ({fmt})
                                  AND Sent_To_Warehouse=1
                                  AND Sent_To_SA=0""", branch_ids)
            cur.execute("SELECT * FROM WAREHOUSE WHERE Warehouse_ID=%s",(wh_id,))
            row = cur.fetchone()
            if row:
                cur.execute("""INSERT INTO DELETED_WAREHOUSE
                                   (Warehouse_ID,Head_ID,City,Last_Audit,Capacity)
                               VALUES (%s,%s,%s,%s,%s)""", row)
                cur.execute("DELETE FROM WAREHOUSE WHERE Warehouse_ID=%s",(wh_id,))
            db.commit()
            return redirect(url_for('show_table', table_name='WAREHOUSE'))
        except Exception as e:
            db.rollback()
            msg = ('error', f'Error: {e}')

    cur.execute("SELECT * FROM WAREHOUSE WHERE Warehouse_ID=%s",(wh_id,))
    warehouse = cur.fetchone()
    cur.execute("SELECT Branch_ID,City,Head_ID FROM BRANCH WHERE Warehouse_ID=%s",(wh_id,))
    branches = cur.fetchall()
    cur.execute("SELECT Warehouse_ID,City FROM WAREHOUSE WHERE Warehouse_ID!=%s ORDER BY City",(wh_id,))
    other_warehouses = cur.fetchall()
    cur.close(); db.close()
    return render_template('reassign_warehouse.html',
                           warehouse=warehouse, branches=branches,
                           other_warehouses=other_warehouses, msg=msg)

# ══════════════════════════════════════════════════════════════════
# DELETED RECORDS
# ══════════════════════════════════════════════════════════════════
@app.route('/deleted/<entity>')
@login_required
@role_required('Admin')
def deleted(entity):
    db = get_db(); cur = db.cursor()
    rows = []; headers = []
    if entity == 'branch':
        headers=['Branch ID','Warehouse ID','Head ID','City','Last Audit','Rating','Deleted At']
        cur.execute("SELECT * FROM DELETED_BRANCH ORDER BY Deleted_At DESC")
        rows = cur.fetchall()
    elif entity == 'warehouse':
        headers=['Warehouse ID','Head ID','City','Last Audit','Capacity','Deleted At']
        cur.execute("SELECT * FROM DELETED_WAREHOUSE ORDER BY Deleted_At DESC")
        rows = cur.fetchall()
    cur.close(); db.close()
    return render_template('show_tables.html',
                           table_name=f'DELETED_{entity.upper()}',
                           headers=headers, rows=rows,
                           f_branch='',f_category='',f_size='',f_alloc='',
                           f_city='',f_role='',f_sq='',f_sent_wh='',f_sent_sa='',
                           f_sku='',
                           all_branches=[],all_cities_br=[],all_cities_wh=[],
                           all_roles=[],all_sizes=[],all_alloc=[],all_sq=[],
                           warehouse_capacity=[], age_classes=[], days_list=[])

# ══════════════════════════════════════════════════════════════════
# SUSTAINABILITY SCORECARD
# ══════════════════════════════════════════════════════════════════
@app.route('/sustainability')
@login_required
@role_required('Admin', 'Branch')
def sustainability():
    db = get_db(); cur = db.cursor()
    cur.execute("""
        SELECT b.Branch_ID, b.City, b.Sustainable_Rating,
               r.Items_Recycled, r.Items_Donated, r.Items_Upcycled,
               r.Estimated_Waste_Reduced,
               (r.Items_Recycled + r.Items_Donated + r.Items_Upcycled) AS Green_Total,
               r.Items_Disposed
        FROM BRANCH b
        JOIN REPORT r ON b.Branch_ID = r.Branch_ID
        ORDER BY b.Sustainable_Rating DESC
    """)
    branches = cur.fetchall()

    cur.execute("""
        SELECT Branch_ID, Rating, Recorded_At
        FROM RATING_HISTORY
        ORDER BY Branch_ID, Recorded_At
    """)
    history = cur.fetchall()
    cur.close(); db.close()

    history_map = {}
    for row in history:
        bid = row[0]
        if bid not in history_map:
            history_map[bid] = {'labels': [], 'data': []}
        history_map[bid]['labels'].append(str(row[2]))
        history_map[bid]['data'].append(float(row[1]))

    return render_template('sustainability.html',
                           branches=branches,
                           history_json=json.dumps(history_map))

# ══════════════════════════════════════════════════════════════════
# QR CODE + EMAIL REPORT
# ══════════════════════════════════════════════════════════════════
@app.route('/qr/<int:deadstock_id>')
@login_required
def generate_qr(deadstock_id):
    """Return a QR code PNG encoding the SKU and deadstock details."""
    try:
        import qrcode
    except ImportError:
        return "qrcode package not installed", 500
    cur = get_db().cursor()
    cur.execute("SELECT SKU, Category, Size, Quantity FROM DEADSTOCK WHERE Deadstock_ID=%s",
                (deadstock_id,))
    row = cur.fetchone()
    cur.close()
    if not row:
        return "Not found", 404
    data = f"SKU:{row[0]}|Cat:{row[1]}|Size:{row[2]}|Qty:{row[3]}"
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

def build_branch_report_pdf(branch_id):
    """Generate branch report PDF bytes and filename."""
    db = get_db(); cur = db.cursor()

    cur.execute("""
        SELECT b.Branch_ID,
               COALESCE(b.City, db2.City, 'Deleted Branch') AS City,
               COALESCE(b.Sustainable_Rating, db2.Sustainable_Rating) AS Rating,
               COALESCE(b.Last_Audit, db2.Last_Audit) AS LastAudit,
               w.City AS WhCity,
               h.Name AS HeadName,
               COALESCE(b.Warehouse_ID, db2.Warehouse_ID) AS WarehouseID
        FROM REPORT r
        LEFT JOIN BRANCH b           ON r.Branch_ID = b.Branch_ID
        LEFT JOIN DELETED_BRANCH db2 ON r.Branch_ID = db2.Branch_ID
        LEFT JOIN WAREHOUSE w        ON COALESCE(b.Warehouse_ID, db2.Warehouse_ID) = w.Warehouse_ID
        LEFT JOIN HEAD h             ON COALESCE(b.Head_ID, db2.Head_ID) = h.Head_ID
        WHERE r.Branch_ID=%s LIMIT 1
    """, (branch_id,))
    branch_info = cur.fetchone()
    if not branch_info:
        cur.execute("""
            SELECT db2.Branch_ID, db2.City, db2.Sustainable_Rating, db2.Last_Audit,
                   w.City, h.Name, db2.Warehouse_ID
            FROM DELETED_BRANCH db2
            LEFT JOIN WAREHOUSE w ON db2.Warehouse_ID=w.Warehouse_ID
            LEFT JOIN HEAD h      ON db2.Head_ID=h.Head_ID
            WHERE db2.Branch_ID=%s LIMIT 1
        """, (branch_id,))
        branch_info = cur.fetchone()
    if not branch_info:
        cur.close(); db.close()
        return None, None

    cur.execute("""SELECT Items_Resold,Items_Recycled,Items_Donated,
                          Items_Upcycled,Items_Rebranded,Items_Disposed,
                          Estimated_Waste_Reduced
                   FROM REPORT WHERE Branch_ID=%s""", (branch_id,))
    report = cur.fetchone() or (0,0,0,0,0,0,0.0)

    cur.execute("""SELECT d.SKU, d.Category, d.Size, m.Material_Name, d.Quantity,
                          d.Sent_To_Warehouse, d.Sent_To_SA, d.Allocated_Type, d.Date_Added
                   FROM DEADSTOCK d
                   LEFT JOIN MATERIAL m ON d.Material_ID=m.Material_ID
                   WHERE d.Branch_ID=%s
                   ORDER BY d.Deadstock_ID""", (branch_id,))
    deadstock_rows = cur.fetchall()
    cur.close(); db.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    GREEN  = colors.HexColor('#2d6a4f')
    LGREEN = colors.HexColor('#e8f5e9')
    DGRAY  = colors.HexColor('#333333')
    LGRAY  = colors.HexColor('#f4f6f9')

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('title', fontSize=18, textColor=GREEN,
                                  fontName='Helvetica-Bold', alignment=TA_CENTER,
                                  spaceAfter=4)
    sub_style   = ParagraphStyle('sub',   fontSize=11, textColor=DGRAY,
                                  fontName='Helvetica', alignment=TA_CENTER,
                                  spaceAfter=2)
    sec_style   = ParagraphStyle('sec',   fontSize=12, textColor=GREEN,
                                  fontName='Helvetica-Bold', spaceBefore=14, spaceAfter=6)
    body_style  = ParagraphStyle('body',  fontSize=10, textColor=DGRAY,
                                  fontName='Helvetica', leading=16)
    footer_style= ParagraphStyle('footer',fontSize=9, textColor=colors.grey,
                                  fontName='Helvetica', alignment=TA_CENTER)

    story = []
    story.append(Paragraph('♻ Deadstock Inventory Management System', title_style))
    story.append(Paragraph('Branch Report', sub_style))
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width='100%', thickness=2, color=GREEN))
    story.append(Spacer(1, 4*mm))

    b_city    = branch_info[1]
    b_rating  = branch_info[2] or 'N/A'
    b_audit   = str(branch_info[3]) if branch_info[3] else 'N/A'
    wh_city   = branch_info[4] or 'N/A'
    head_name = branch_info[5] or 'N/A'
    date_gen  = datetime.now().strftime('%d %B %Y, %I:%M %p')

    info_data = [
        ['Branch ID', str(branch_id),       'Branch City',     b_city],
        ['Branch Head', head_name,           'Warehouse City',  wh_city],
        ['Last Audit', b_audit,              'Generated On',    date_gen],
    ]
    info_table = Table(info_data, colWidths=[40*mm, 55*mm, 45*mm, 55*mm])
    info_table.setStyle(TableStyle([
        ('FONTNAME',  (0,0),(-1,-1), 'Helvetica'),
        ('FONTNAME',  (0,0),(0,-1),  'Helvetica-Bold'),
        ('FONTNAME',  (2,0),(2,-1),  'Helvetica-Bold'),
        ('FONTSIZE',  (0,0),(-1,-1), 10),
        ('TEXTCOLOR', (0,0),(0,-1),  GREEN),
        ('TEXTCOLOR', (2,0),(2,-1),  GREEN),
        ('BACKGROUND',(0,0),(-1,-1), LGRAY),
        ('ROWBACKGROUNDS',(0,0),(-1,-1),[LGRAY, colors.white]),
        ('GRID',      (0,0),(-1,-1), 0.5, colors.HexColor('#dddddd')),
        ('PADDING',   (0,0),(-1,-1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 5*mm))

    story.append(Paragraph('Sustainability Rating', sec_style))
    rating_val = float(b_rating) if b_rating != 'N/A' else 0.0
    stars = '★' * int(rating_val) + ('½' if (rating_val % 1 >= 0.5) else '') + '☆' * (5 - int(rating_val) - (1 if rating_val%1>=0.5 else 0))
    rating_data = [['Sustainable Rating', f'{b_rating} / 5.0', stars]]
    rating_table = Table(rating_data, colWidths=[70*mm, 40*mm, 85*mm])
    rating_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,-1), LGREEN),
        ('FONTNAME',   (0,0),(0,0),   'Helvetica-Bold'),
        ('FONTNAME',   (1,0),(1,0),   'Helvetica-Bold'),
        ('FONTSIZE',   (0,0),(-1,-1), 11),
        ('TEXTCOLOR',  (1,0),(1,0),   GREEN),
        ('TEXTCOLOR',  (2,0),(2,0),   colors.HexColor('#e65100')),
        ('GRID',       (0,0),(-1,-1), 0.5, colors.HexColor('#a5d6a7')),
        ('PADDING',    (0,0),(-1,-1), 8),
    ]))
    story.append(rating_table)
    story.append(Spacer(1, 5*mm))

    story.append(Paragraph('Allocation Summary', sec_style))
    alloc_headers = [['Allocation Type', 'Items Count']]
    all_alloc_rows = [
        ['Resold',    int(report[0])],
        ['Recycled',  int(report[1])],
        ['Donated',   int(report[2])],
        ['Upcycled',  int(report[3])],
        ['Rebranded', int(report[4])],
        ['Disposed',  int(report[5])],
    ]
    total_alloc = sum(r[1] for r in all_alloc_rows)
    alloc_rows = [[r[0], str(r[1])] for r in all_alloc_rows if r[1] > 0]
    if not alloc_rows:
        alloc_rows = [['No allocations made yet', '—']]
    alloc_rows.append(['TOTAL', str(total_alloc)])
    alloc_data = alloc_headers + alloc_rows
    alloc_table = Table(alloc_data, colWidths=[100*mm, 95*mm])
    alloc_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0),   GREEN),
        ('TEXTCOLOR',  (0,0),(-1,0),   colors.white),
        ('FONTNAME',   (0,0),(-1,0),   'Helvetica-Bold'),
        ('FONTNAME',   (0,-1),(-1,-1), 'Helvetica-Bold'),
        ('BACKGROUND', (0,-1),(-1,-1), LGREEN),
        ('TEXTCOLOR',  (0,-1),(-1,-1), GREEN),
        ('ROWBACKGROUNDS',(0,1),(-1,-2),[colors.white, LGRAY]),
        ('GRID',       (0,0),(-1,-1),  0.5, colors.HexColor('#cccccc')),
        ('FONTSIZE',   (0,0),(-1,-1),  10),
        ('PADDING',    (0,0),(-1,-1),  7),
        ('ALIGN',      (1,0),(-1,-1),  'CENTER'),
    ]))
    story.append(alloc_table)
    story.append(Spacer(1, 4*mm))

    waste_data = [['Estimated Waste Reduced', f'{float(report[6]):.2f} kg']]
    waste_table = Table(waste_data, colWidths=[100*mm, 95*mm])
    waste_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,-1), colors.HexColor('#fff3e0')),
        ('FONTNAME',   (0,0),(0,0),   'Helvetica-Bold'),
        ('FONTNAME',   (1,0),(1,0),   'Helvetica-Bold'),
        ('TEXTCOLOR',  (1,0),(1,0),   colors.HexColor('#e65100')),
        ('FONTSIZE',   (0,0),(-1,-1), 10),
        ('GRID',       (0,0),(-1,-1), 0.5, colors.HexColor('#ffcc80')),
        ('PADDING',    (0,0),(-1,-1), 7),
        ('ALIGN',      (1,0),(1,0),   'CENTER'),
    ]))
    story.append(waste_table)
    story.append(Spacer(1, 5*mm))

    # ── Status Snapshot (3-cell summary) ──
    story.append(Paragraph('Inventory Status Snapshot', sec_style))

    at_branch  = sum(d[4] for d in deadstock_rows if not d[5])
    at_wh      = sum(d[4] for d in deadstock_rows if d[5] and not d[6])
    at_sa      = sum(d[4] for d in deadstock_rows if d[6] and not d[7])
    allocated  = sum(d[4] for d in deadstock_rows if d[7])

    snap_data = [
        ['At Branch', 'At Warehouse', 'At Stock Allocation', 'Allocated'],
        [str(at_branch), str(at_wh), str(at_sa), str(allocated)],
    ]
    snap_table = Table(snap_data, colWidths=[47*mm, 47*mm, 53*mm, 48*mm])
    snap_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0),(-1,0),   GREEN),
        ('TEXTCOLOR',  (0,0),(-1,0),   colors.white),
        ('FONTNAME',   (0,0),(-1,-1),  'Helvetica-Bold'),
        ('FONTSIZE',   (0,0),(-1,-1),  10),
        ('ALIGN',      (0,0),(-1,-1),  'CENTER'),
        ('GRID',       (0,0),(-1,-1),  0.5, colors.HexColor('#cccccc')),
        ('PADDING',    (0,0),(-1,-1),  8),
        ('ROWBACKGROUNDS', (0,1),(-1,-1), [LGREEN]),
        ('TEXTCOLOR',  (0,1),(-1,1),   GREEN),
    ]))
    story.append(snap_table)
    story.append(Spacer(1, 5*mm))

    # ── Aggregated Deadstock Summary (by Category) ──
    story.append(Paragraph('Deadstock by Category', sec_style))

    agg = defaultdict(lambda: {'sizes': set(), 'qty': 0, 'at_branch': 0, 'at_wh': 0, 'allocated': 0})
    for d in deadstock_rows:
        cat = d[1]
        agg[cat]['sizes'].add(d[2])
        agg[cat]['qty'] += d[4]
        if not d[5]:
            agg[cat]['at_branch'] += d[4]
        elif d[5] and not d[6]:
            agg[cat]['at_wh'] += d[4]
        elif d[7]:
            agg[cat]['allocated'] += d[4]

    agg_headers = [['Category', 'Sizes', 'Total Qty', 'At Branch', 'At WH', 'Allocated']]
    agg_rows = []
    for cat, info in sorted(agg.items()):
        sizes_str = ', '.join(sorted(info['sizes']))
        agg_rows.append([
            cat,
            sizes_str,
            str(info['qty']),
            str(info['at_branch']),
            str(info['at_wh']),
            str(info['allocated']),
        ])
    if not agg_rows:
        agg_rows = [['No deadstock records', '', '', '', '', '']]

    agg_data = agg_headers + agg_rows
    agg_table = Table(agg_data, colWidths=[40*mm, 30*mm, 25*mm, 25*mm, 25*mm, 30*mm])
    agg_table.setStyle(TableStyle([
        ('BACKGROUND',   (0,0),(-1,0),   GREEN),
        ('TEXTCOLOR',    (0,0),(-1,0),   colors.white),
        ('FONTNAME',     (0,0),(-1,0),   'Helvetica-Bold'),
        ('FONTSIZE',     (0,0),(-1,-1),  9),
        ('GRID',         (0,0),(-1,-1),  0.5, colors.HexColor('#cccccc')),
        ('PADDING',      (0,0),(-1,-1),  6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1), [colors.white, LGRAY]),
        ('ALIGN',        (2,0),(-1,-1),  'CENTER'),
    ]))
    story.append(agg_table)
    story.append(Spacer(1, 5*mm))

    # ── Full Item Table — only if ≤ 20 rows ──
    if len(deadstock_rows) <= 20:
        story.append(Paragraph('Detailed Inventory', sec_style))
        ds_headers = [['Category','Size','Material','Qty','Status']]
        ds_rows = []
        for d in deadstock_rows:
            if d[7]:
                status = f'Allocated – {d[7]}'
            elif d[6]:
                status = 'At Stock Allocation'
            elif d[5]:
                status = 'At Warehouse'
            else:
                status = 'At Branch'
            ds_rows.append([d[1], d[2], d[3] or '—', str(d[4]), status])
        ds_data = ds_headers + ds_rows
        ds_table = Table(ds_data, colWidths=[45*mm, 20*mm, 35*mm, 20*mm, 75*mm])
        ds_table.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,0),   GREEN),
            ('TEXTCOLOR',     (0,0),(-1,0),   colors.white),
            ('FONTNAME',      (0,0),(-1,0),   'Helvetica-Bold'),
            ('FONTSIZE',      (0,0),(-1,-1),  9),
            ('GRID',          (0,0),(-1,-1),  0.5, colors.HexColor('#cccccc')),
            ('PADDING',       (0,0),(-1,-1),  6),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),  [colors.white, LGRAY]),
        ]))
        story.append(ds_table)
    else:
        story.append(Paragraph(
            f'Full item-level detail omitted ({len(deadstock_rows)} items). '
            f'See aggregated summary above.',
            body_style
        ))
    story.append(Spacer(1, 8*mm))

    story.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#cccccc')))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(f'Generated on {date_gen} by Deadstock IMS  |  Branch #{branch_id} – {b_city}',
                            footer_style))

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    date_str = datetime.now().strftime('%Y%m%d')
    filename = f'branch_report_{branch_id}_{date_str}.pdf'
    return pdf_bytes, filename

@app.route('/send_report_email/<int:branch_id>', methods=['POST'])
@login_required
@role_required('Admin', 'Stock_Allocation')
def send_report_email(branch_id):
    """Generate PDF and email it to the branch head via SendGrid."""
    if not SendGridAPIClient:
        return redirect(url_for('admin_branch_report') if session.get('role') == 'Admin'
                        else url_for('sa_branch_report'))

    pdf_bytes, filename = build_branch_report_pdf(branch_id)
    if not pdf_bytes:
        return "Branch not found.", 404

    db = get_db(); cur = db.cursor()
    cur.execute("""SELECT h.Email, h.Name FROM BRANCH b
                   JOIN HEAD h ON b.Head_ID=h.Head_ID WHERE b.Branch_ID=%s""", (branch_id,))
    head = cur.fetchone()
    if not head:
        cur.execute("""SELECT h.Email, h.Name FROM DELETED_BRANCH db2
                       JOIN HEAD h ON db2.Head_ID=h.Head_ID WHERE db2.Branch_ID=%s""", (branch_id,))
        head = cur.fetchone()
    cur.close(); db.close()
    if not head:
        return "Branch head email not found.", 404

    to_email, head_name = head
    encoded = base64.b64encode(pdf_bytes).decode()
    message = Mail(
        from_email=SENDGRID_FROM,
        to_emails=to_email,
        subject=f'Deadstock IMS – Branch Report #{branch_id}',
        html_content=f'<p>Hello {head_name},</p><p>Please find your branch report attached.</p>'
    )
    attachment = Attachment(
        FileContent(encoded),
        FileName(filename),
        FileType('application/pdf'),
        Disposition('attachment')
    )
    message.attachment = attachment
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        sg.send(message)
    except Exception as e:
        print(f'[SENDGRID ERROR] {e}')

    if session.get('role') == 'Admin':
        return redirect(url_for('admin_branch_report'))
    return redirect(url_for('sa_branch_report'))

# ══════════════════════════════════════════════════════════════════
# CHANGE SET 4 – DOWNLOAD BRANCH REPORT AS PDF
# ══════════════════════════════════════════════════════════════════
@app.route('/download_report/<int:branch_id>')
@login_required
def download_report(branch_id):
    role = session.get('role')
    if role == 'Branch' and session.get('branch_id') != branch_id:
        return render_template('denied.html')
    if role not in ['Admin','Branch','Stock_Allocation']:
        return render_template('denied.html')

    pdf_bytes, filename = build_branch_report_pdf(branch_id)
    if not pdf_bytes:
        return "Branch not found.", 404

    response = make_response(pdf_bytes)
    response.headers['Content-Type']        = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

def check_aging_deadstock():
    """Daily 9am check: alert branch heads about items ≥30 days unsent."""
    print('[SCHEDULER] Running daily aging check...')
    try:
        db = get_db(); cur = db.cursor()
        cur.execute("""
            SELECT b.Head_ID, b.City, b.Branch_ID,
                   COUNT(d.Deadstock_ID) AS stale_count,
                   SUM(d.Quantity) AS stale_qty
            FROM DEADSTOCK d
            JOIN BRANCH b ON d.Branch_ID = b.Branch_ID
            WHERE d.Sent_To_Warehouse = 0
              AND d.Date_Added IS NOT NULL
              AND DATEDIFF(CURDATE(), d.Date_Added) >= 30
            GROUP BY b.Branch_ID, b.Head_ID, b.City
        """)
        stale_branches = cur.fetchall()
        for row in stale_branches:
            head_id, city, branch_id, count, qty = row
            phone = get_head_phone(head_id)
            if phone:
                msg = (
                    f"⚠️ *Deadstock IMS – Aging Alert*\n"
                    f"Branch {city} (ID: {branch_id}) has {count} item(s) "
                    f"({qty} units) sitting unsent for 30+ days.\n"
                    f"🔴 Critical aging threshold crossed. Please send to warehouse immediately."
                )
                send_whatsapp_alert(phone, msg)
        cur.close(); db.close()
    except Exception as e:
        print(f'[SCHEDULER ERROR] {e}')

@app.route('/admin/trigger_aging_alert')
@login_required
@role_required('Admin')
def trigger_aging_alert():
    """Admin can manually trigger the aging alert — useful for demos."""
    check_aging_deadstock()
    return "Aging alert check triggered. Check server logs.", 200

scheduler = None
if BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_aging_deadstock, 'cron', hour=9, minute=0)
    scheduler.start()
    print('[SCHEDULER] Daily aging check registered (9:00 AM)')

import atexit
if scheduler:
    atexit.register(lambda: scheduler.shutdown(wait=False))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500

if __name__ == '__main__':
    app.run(debug=True)