from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response, send_from_directory
from sqlalchemy.orm import joinedload
from sqlalchemy import func, text
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, timedelta, timezone
from collections import Counter
import csv
import io
import random
import sqlalchemy.exc
import base64
import traceback

# ================== استدعاء مكتبات البصمة (WebAuthn) ==================
from webauthn import (
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria, UserVerificationRequirement, AuthenticatorAttachment, PublicKeyCredentialDescriptor
)
from webauthn.helpers.options_to_json import options_to_json
from webauthn.helpers.parse_registration_credential_json import parse_registration_credential_json
from webauthn.helpers.parse_authentication_credential_json import parse_authentication_credential_json

app = Flask(__name__)

# ================== إعدادات التطبيق والبروكسي ==================
app.config['SECRET_KEY'] = 'abdullah-voucher-system-2026-final'
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:bajaberasobbaj72@db.hlepldoaxayyazklvthv.supabase.co:5432/postgres'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# إخبار Flask بالوثوق بـ Cloudflare والأنفاق (لحل مشكلة HTTP/HTTPS)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

db = SQLAlchemy(app)

# ================== إعدادات نظام البصمة (RP) الجذرية ==================
RP_NAME = "Andal Star VIP"

def get_rp_id():
    forwarded = request.headers.get('X-Forwarded-Host')
    if forwarded:
        return forwarded.split(':')[0]
    return request.host.split(':')[0]

def get_origin():
    proto = request.headers.get('X-Forwarded-Proto', 'http')
    host = request.headers.get('X-Forwarded-Host', request.host)
    if "trycloudflare.com" in host or "serveo.net" in host or "ngrok" in host:
        proto = "https"
    return f"{proto}://{host}"

# ================== الجداول ==================
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(15), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(100))
    password_hash = db.Column(db.String(256))
    balance = db.Column(db.Float, default=0.0)
    debt = db.Column(db.Float, default=0.0)
    points = db.Column(db.Integer, default=0)
    role = db.Column(db.String(10), default='customer')
    created_at = db.Column(db.String(20), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    last_spin_date = db.Column(db.String(20), nullable=True)
    bonus_points = db.Column(db.Integer, default=0)
    active_discount = db.Column(db.Integer, default=0)
    webauthn_cred_id = db.Column(db.String(250), unique=True, nullable=True)
    webauthn_public_key = db.Column(db.String(500), nullable=True)
    webauthn_sign_count = db.Column(db.Integer, default=0)

class Package(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    price = db.Column(db.Float, nullable=False)
    validity_days = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(200))

class Voucher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mikrotik_username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    mikrotik_password = db.Column(db.String(50), default='')
    package_id = db.Column(db.Integer, db.ForeignKey('package.id'), index=True)
    status = db.Column(db.String(10), default='available', index=True)
    sold_to = db.Column(db.Integer, nullable=True)
    sold_at = db.Column(db.String(20), nullable=True)
    package = db.relationship('Package', backref='vouchers')

class PointsTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    title = db.Column(db.String(100), nullable=False)
    message = db.Column(db.String(300), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    voucher_id = db.Column(db.Integer, db.ForeignKey('voucher.id'))
    package_name = db.Column(db.String(50))
    amount_paid = db.Column(db.Float)
    original_price = db.Column(db.Float)
    discount_percent = db.Column(db.Integer, default=0)
    purchased_at = db.Column(db.String(20), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))

class Offer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    slogan = db.Column(db.String(200))
    package_id = db.Column(db.Integer, db.ForeignKey('package.id'))
    discount = db.Column(db.Integer)
    package = db.relationship('Package', backref='offers')

class FinancialTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    transaction_type = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.String(20), default=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    user = db.relationship('User', backref='financial_transactions')

# ====================== نظام المكافآت والاقتراحات ======================
def add_points(user, amount_paid):
    points_earned = int(amount_paid // 100 * 10)
    user.points += points_earned
    return points_earned

def get_smart_recommendation(user_id):
    result = db.session.query(
        Purchase.package_name, func.count(Purchase.id).label('total')
    ).filter_by(user_id=user_id).group_by(Purchase.package_name).order_by(func.count(Purchase.id).desc()).first()

    if result:
        return Package.query.filter_by(name=result.package_name).first()
    return Package.query.first()

def get_user_level(points):
    if points >= 3000: return "💎 Diamond"
    elif points >= 1500: return "🥇 Gold"
    elif points >= 500: return "🥈 Silver"
    else: return "🥉 Bronze"

def get_level_discount_and_bonus(level):
    if level == "💎 Diamond": return 0.15, 50
    elif level == "🥇 Gold": return 0.10, 30
    elif level == "🥈 Silver": return 0.05, 15
    else: return 0.0, 0

def add_points_advanced(user, amount_paid, reason="شراء باقة"):
    base_points = int(amount_paid // 10)
    level = get_user_level(user.points)
    level_discount, extra_bonus = get_level_discount_and_bonus(level)
    total_points = base_points + extra_bonus

    user.points += total_points

    trans = PointsTransaction(
        user_id=user.id,
        amount=total_points,
        reason=reason + f" (أساسي: {base_points} + مكافأة {level}: {extra_bonus})",
        expires_at=datetime.now(timezone.utc) + timedelta(days=180)
    )
    db.session.add(trans)

    notif = Notification(
        user_id=user.id,
        title="🎉 حصلت على نقاط جديدة!",
        message=f"تم إضافة {total_points} نقطة من {reason}\n(أساسي: {base_points} + مكافأة المستوى: {extra_bonus})"
    )
    db.session.add(notif)
    return total_points

# ================== إنشاء القاعدة + التحديث التلقائي للبصمة ==================
with app.app_context():
    db.create_all()

    try:
        with db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE user ADD COLUMN webauthn_cred_id VARCHAR(250)'))
            conn.execute(text('ALTER TABLE user ADD COLUMN webauthn_public_key VARCHAR(500)'))
            conn.execute(text('ALTER TABLE user ADD COLUMN webauthn_sign_count INTEGER DEFAULT 0'))
    except Exception:
        pass # الحقول موجودة مسبقاً

    if not User.query.filter_by(phone='admin').first():
        admin = User(
            phone='admin',
            full_name='عبدالله الأدمن',
            password_hash=generate_password_hash('123456'),
            role='admin',
            balance=10000,
            points=500
        )
        db.session.add(admin)

    if not Package.query.first():
        packs = [
            Package(name='باقة يومية', price=10, validity_days=1, description='24 ساعة'),
            Package(name='باقة أسبوعية', price=35, validity_days=7, description='أسبوع'),
            Package(name='باقة شهرية', price=90, validity_days=30, description='شهر')
        ]
        db.session.add_all(packs)
    db.session.commit()
    print("✅ تم تجهيز قاعدة البيانات وتحديثها لدعم البصمة!")

# ================== مسارات تطبيق الهاتف (PWA Bridge) ==================
@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

# ================== الروتات الأساسية ==================
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect('/admin/dashboard' if session.get('role') == 'admin' else '/dashboard')
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(phone=request.form['phone']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            session['user_id'] = user.id
            session['role'] = user.role
            session['full_name'] = user.full_name
            flash(f'مرحباً {user.full_name} 👋', 'success')
            return redirect(url_for('home'))
        flash('بيانات خاطئة', 'error')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        if User.query.filter_by(phone=request.form['phone']).first():
            flash('الرقم موجود مسبقاً', 'error')
        else:
            new_user = User(
                phone=request.form['phone'],
                full_name=request.form['name'],
                password_hash=generate_password_hash(request.form['password'])
            )
            db.session.add(new_user)
            db.session.commit()
            flash('تم إنشاء الحساب!', 'success')
            return redirect('/login')
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ================== روتات البصمة (WebAuthn API) الجذرية ==================
@app.route('/webauthn/register/begin', methods=['POST'])
def webauthn_register_begin():
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'غير مسجل دخول'}), 401
        
        user = db.session.get(User, session['user_id'])
        rp_id = get_rp_id()
        print(f"[*] Starting WebAuthn Registration for RP_ID: {rp_id}")

        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=RP_NAME,
            user_id=str(user.id).encode('utf-8'),
            user_name=user.phone,
            user_display_name=user.full_name,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                user_verification=UserVerificationRequirement.REQUIRED
            )
        )
        session['challenge'] = base64.urlsafe_b64encode(options.challenge).decode('utf-8')
        return Response(options_to_json(options), mimetype='application/json')
    except Exception as e:
        print("=== CRASH IN REGISTER BEGIN ===")
        traceback.print_exc()
        return jsonify({'error': f'Server Error: {str(e)}'}), 500

@app.route('/webauthn/register/complete', methods=['POST'])
def webauthn_register_complete():
    try:
        user = db.session.get(User, session['user_id'])
        data = request.json
        credential = parse_registration_credential_json(data)
        challenge = base64.urlsafe_b64decode(session.get('challenge', ''))

        verification = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_origin=get_origin(),
            expected_rp_id=get_rp_id()
        )
        user.webauthn_cred_id = base64.urlsafe_b64encode(verification.credential_id).decode('utf-8')
        user.webauthn_public_key = base64.urlsafe_b64encode(verification.credential_public_key).decode('utf-8')
        user.webauthn_sign_count = verification.sign_count
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        print("=== CRASH IN REGISTER COMPLETE ===")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/webauthn/login/begin', methods=['POST'])
def webauthn_login_begin():
    try:
        phone = request.json.get('phone')
        user = User.query.filter_by(phone=phone).first()
        
        if not user or not user.webauthn_cred_id:
            return jsonify({'error': 'لم يتم تفعيل البصمة لهذا الحساب.'}), 404
            
        rp_id = get_rp_id()
        print(f"[*] Starting WebAuthn Login for RP_ID: {rp_id}")

        options = generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=[PublicKeyCredentialDescriptor(
                id=base64.urlsafe_b64decode(user.webauthn_cred_id)
            )],
            user_verification=UserVerificationRequirement.PREFERRED
        )
        session['challenge'] = base64.urlsafe_b64encode(options.challenge).decode('utf-8')
        session['login_user_id'] = user.id
        return Response(options_to_json(options), mimetype='application/json')
    except Exception as e:
        print("=== CRASH IN LOGIN BEGIN ===")
        traceback.print_exc()
        return jsonify({'error': f'Server Error: {str(e)}'}), 500

@app.route('/webauthn/login/complete', methods=['POST'])
def webauthn_login_complete():
    try:
        data = request.json
        user_id = session.get('login_user_id')
        user = db.session.get(User, user_id)

        if not user:
            return jsonify({'error': 'انتهت صلاحية الجلسة'}), 401
            
        credential = parse_authentication_credential_json(data)
        challenge = base64.urlsafe_b64decode(session.get('challenge', ''))

        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_origin=get_origin(),
            expected_rp_id=get_rp_id(),
            credential_public_key=base64.urlsafe_b64decode(user.webauthn_public_key),
            credential_current_sign_count=user.webauthn_sign_count
        )
        user.webauthn_sign_count = verification.new_sign_count
        db.session.commit()

        session.clear()
        session['user_id'] = user.id
        session['role'] = user.role
        session['full_name'] = user.full_name

        redirect_url = '/admin/dashboard' if user.role == 'admin' else '/dashboard'
        return jsonify({'success': True, 'redirect': redirect_url})
    except Exception as e:
        print("=== CRASH IN LOGIN COMPLETE ===")
        traceback.print_exc()
        return jsonify({'error': f'فشلت المطابقة: {str(e)}'}), 500

# ================== بقية الروتات ==================
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session or session.get('role') == 'admin':
        return redirect('/login')

    user = db.session.get(User, session['user_id'])
    if not user:
        session.clear()
        return redirect('/login')

    purchases = Purchase.query.filter_by(user_id=user.id).order_by(Purchase.id.desc()).limit(5).all()
    unread_count = Notification.query.filter(
        (Notification.user_id == user.id) | (Notification.user_id == None)
    ).filter(Notification.is_read == False).count()
    
    return render_template('dashboard.html', user=user, purchases=purchases, unread_count=unread_count)

@app.route('/transfer_balance', methods=['POST'])
def transfer_balance():
    if 'user_id' not in session or session.get('role') == 'admin':
        return redirect('/login')

    sender = db.session.get(User, session['user_id'])
    recipient_phone = request.form.get('recipient_phone')
    
    try:
        amount = float(request.form.get('amount', 0))
    except ValueError:
        flash('❌ المبلغ غير صحيح', 'error')
        return redirect('/dashboard')
        
    if amount < 500:
        flash('❌ عذراً، الحد الأدنى للتحويل هو 500 ريال', 'error')
        return redirect('/dashboard')
        
    if sender.balance < amount:
        flash('❌ رصيدك الحالي لا يكفي لإتمام التحويل', 'error')
        return redirect('/dashboard')

    recipient = User.query.filter_by(phone=recipient_phone).first()
    
    if not recipient:
        flash('❌ لم يتم العثور على زبون بهذا الرقم', 'error')
        return redirect('/dashboard')

    if sender.id == recipient.id:
        flash('❌ لا يمكنك تحويل رصيد لنفسك!', 'error')
        return redirect('/dashboard')

    try:
        sender.balance -= amount
        recipient.balance += amount

        trans_out = FinancialTransaction(user_id=sender.id, amount=-amount, transaction_type=f'تحويل صادر إلى ({recipient.full_name})')
        trans_in = FinancialTransaction(user_id=recipient.id, amount=amount, transaction_type=f'تحويل وارد من ({sender.full_name})')

        db.session.add(trans_out)
        db.session.add(trans_in)
        db.session.commit()

        flash(f'✅ تم تحويل {amount} ريال بنجاح إلى {recipient.full_name}', 'success')
    except Exception as e:
        db.session.rollback()
        flash('❌ حدث خطأ غير متوقع أثناء التحويل. تمت إعادة الأموال.', 'error')

    return redirect('/dashboard')

@app.route('/spin_wheel', methods=['POST'])
def spin_wheel():
    if 'user_id' not in session:
        return redirect('/login')

    user = db.session.get(User, session['user_id'])
    today = datetime.now().strftime("%Y-%m-%d")

    if user.last_spin_date == today:
        flash('⏳ لقد استخدمت محاولتك اليوم! عد غداً.', 'error')
        return redirect('/dashboard')

    prizes = ["10_points", "20_points", "50_points", "3_discount", "5_discount"]
    won_prize = random.choice(prizes)

    if won_prize.endswith("_points"):
        points = int(won_prize.split("_")[0])
        user.bonus_points += points
        flash(f'🎉 مبروك! كسبت {points} نقطة إضافية.', 'success')
    elif won_prize.endswith("_discount"):
        discount = int(won_prize.split("_")[0])
        user.active_discount = discount
        flash(f'🎉 مبروك! كسبت خصم {discount}% لعملية شرائك القادمة.', 'success')

    user.last_spin_date = today
    db.session.commit()
    return redirect('/dashboard')

@app.route('/take_salfah', methods=['POST'])
def take_salfah():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'})
    user = db.session.get(User, session['user_id'])
    if user.debt > 0:
        return jsonify({'success': False, 'message': '❌ لديك دين سابق'})

    try:
        user.balance += 100.0
        user.debt = 100.0
        db.session.commit()
        return jsonify({'success': True})
    except:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'حدث خطأ في النظام'})

@app.route('/buy')
def buy():
    if 'user_id' not in session or session.get('role') == 'admin':
        return redirect('/login')

    user = db.session.get(User, session['user_id'])
    packages = Package.query.all()

    for p in packages:
        offer = Offer.query.filter_by(package_id=p.id).first()
        offer_discount = offer.discount / 100 if offer else 0.0
        current_level = get_user_level(user.points)
        level_discount, _ = get_level_discount_and_bonus(current_level)

        p.final_price = round(p.price * (1 - offer_discount) * (1 - level_discount), 1)
        p.discount_info = f"خصم العرض: {offer_discount*100:.0f}% | خصم المستوى ({current_level}): {level_discount*100:.0f}%"

        if offer:
            p.has_offer = True
            p.discount_percent = offer.discount
        else:
            p.has_offer = False
            p.discount_percent = 0
            
        p.available_count = Voucher.query.filter_by(package_id=p.id, status='available').count()

    recommendation = get_smart_recommendation(session['user_id'])
    return render_template('buy.html', packages=packages, user=user, recommendation=recommendation)

@app.route('/buy/<int:package_id>', methods=['GET'])
def purchase(package_id):
    if 'user_id' not in session or session.get('role') == 'admin':
        return redirect('/login')

    user = db.session.get(User, session['user_id'])
    package = db.session.get(Package, package_id)

    if not package:
        flash('الباقة غير موجودة!', 'error')
        return redirect('/buy')

    offer = Offer.query.filter_by(package_id=package.id).first()
    offer_discount = offer.discount / 100 if offer else 0.0
    current_level = get_user_level(user.points)
    level_discount, _ = get_level_discount_and_bonus(current_level)
    final_price = round(package.price * (1 - offer_discount) * (1 - level_discount), 1)

    if user.balance < final_price:
        flash('❌ رصيدك غير كافي!', 'error')
        return redirect('/buy')

    try:
        voucher = db.session.query(Voucher).filter_by(package_id=package_id, status='available').with_for_update().first()

        if not voucher:
            db.session.rollback()
            flash('❌ لا يوجد كروت متاحة لهذه الباقة', 'error')
            return redirect('/buy')

        user.balance -= final_price
        voucher.status = 'sold'
        voucher.sold_to = user.id
        voucher.sold_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        purchase_record = Purchase(
            user_id=user.id,
            voucher_id=voucher.id,
            package_name=package.name,
            amount_paid=final_price,
            original_price=package.price,
            discount_percent=offer.discount if offer else 0
        )
        db.session.add(purchase_record)
        add_points_advanced(user, package.price, f"شراء باقة {package.name}")
        db.session.commit()

        flash(f'✅ تم شراء {package.name} بنجاح!', 'success')
        return render_template('voucher_success.html', voucher=voucher, package=package, final_price=final_price)

    except sqlalchemy.exc.OperationalError:
        db.session.rollback()
        flash('❌ النظام مشغول حالياً، يرجى المحاولة بعد قليل.', 'error')
        return redirect('/buy')
    except Exception as e:
        db.session.rollback()
        flash('❌ حدث خطأ غير متوقع أثناء المعالجة، لم يتم خصم الرصيد.', 'error')
        return redirect('/buy')

@app.route('/myvouchers')
def myvouchers():
    if 'user_id' not in session: return redirect('/login')
    vouchers = Voucher.query.filter_by(sold_to=session['user_id']).order_by(Voucher.id.desc()).all()
    return render_template('myvouchers.html', vouchers=vouchers)

@app.route('/history')
def history():
    if 'user_id' not in session: return redirect('/login')
    purchases = Purchase.query.filter_by(user_id=session['user_id']).order_by(Purchase.id.desc()).all()
    return render_template('history.html', purchases=purchases)

@app.route('/offers')
def offers():
    if 'user_id' not in session: return redirect('/login')
    offers = Offer.query.all()
    return render_template('offers.html', offers=offers)

@app.route('/admin/delete_offer/<int:id>', methods=['GET', 'POST'])
def delete_offer(id):
    if session.get('role') != 'admin':
        return redirect('/login')

    try:
        offer = Offer.query.get(id)
        if offer:
            db.session.delete(offer)
            db.session.commit()
            flash('✅ تم حذف العرض بنجاح!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('❌ حدث خطأ أثناء الحذف.', 'error')

    return redirect('/admin/offers')

@app.route('/profile')
def profile():
    if 'user_id' not in session or session.get('role') == 'admin':
        return redirect('/login')

    user = db.session.get(User, session['user_id'])
    purchases = Purchase.query.filter_by(user_id=user.id).order_by(Purchase.id.desc()).all()

    total_spent = sum(p.amount_paid for p in purchases) if purchases else 0
    package_counts = Counter(p.package_name for p in purchases)
    most_purchased = package_counts.most_common(1)[0][0] if package_counts else "لا توجد مشتريات بعد"

    level = get_user_level(user.points)
    has_fingerprint = True if user.webauthn_cred_id else False

    return render_template('profile.html', user=user, purchases=purchases[:6], total_spent=total_spent, most_purchased=most_purchased, level=level, has_fingerprint=has_fingerprint)

@app.route('/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session: return redirect('/login')
    user = db.session.get(User, session['user_id'])
    if check_password_hash(user.password_hash, request.form['current_password']):
        if request.form['new_password'] == request.form['confirm_password']:
            user.password_hash = generate_password_hash(request.form['new_password'])
            db.session.commit()
            flash('✅ تم تغيير كلمة المرور بنجاح!', 'success')
        else:
            flash(' ❌ كلمة المرور الجديدة غير متطابقة', 'error')
    else:
        flash('❌ كلمة المرور الحالية غير صحيحة', 'error')
    return redirect('/profile')

# ================== روتات الإدارة ==================
@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'admin': return redirect('/login')
    
    # جلب الإحصائيات السريعة لرادار النظام
    total_users = User.query.filter_by(role='customer').count()
    total_sold = Voucher.query.filter_by(status='sold').count()
    total_available = Voucher.query.filter_by(status='available').count()
    
    return render_template('admin_dashboard.html', 
                           total_users=total_users, 
                           total_sold=total_sold, 
                           total_available=total_available)

@app.route('/admin/customers')
def customers_list():
    if session.get('role') != 'admin': return redirect('/login')
    customers = User.query.filter_by(role='customer').all()
    total_debt = sum(c.debt for c in customers) if customers else 0
    total_balance = sum(c.balance for c in customers) if customers else 0
    return render_template('customers.html', customers=customers, total_debt=total_debt, total_balance=total_balance)

@app.route('/admin/topup_customer/<int:uid>', methods=['POST'])
def topup_customer(uid):
    if session.get('role') != 'admin': return redirect('/login')

    user = db.session.get(User, uid)
    if not user:
        flash('❌ الزبون غير موجود', 'error')
        return redirect('/admin/customers')

    try:
        amount = float(request.form['amount'])
    except ValueError:
        flash('❌ المبلغ غير صحيح', 'error')
        return redirect('/admin/customers')

    try:
        debt_paid = 0
        if user.debt > 0 and amount > 0:
            debt_paid = min(amount, user.debt)
            user.debt -= debt_paid
            amount -= debt_paid
            trans1 = FinancialTransaction(user_id=user.id, amount=debt_paid, transaction_type='سداد دين')
            db.session.add(trans1)
            flash(f'✅ تم خصم {debt_paid} ريال من الدين تلقائياً', 'success')

        if amount > 0:
            user.balance += amount
            trans2 = FinancialTransaction(user_id=user.id, amount=amount, transaction_type='إيداع رصيد')
            db.session.add(trans2)
            flash(f'✅ تم شحن {amount} ريال لرصيد الزبون', 'success')

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash('❌ فشلت عملية الشحن بسبب خطأ داخلي.', 'error')

    return redirect('/admin/customers')

@app.route('/admin/export_customers')
def export_customers():
    if session.get('role') != 'admin':
        return redirect('/login')

    si = io.StringIO()
    si.write('\ufeff')
    cw = csv.writer(si)
    cw.writerow(['المعرف (ID)', 'الاسم الكامل', 'رقم الجوال', 'الرصيد الحالي', 'الديون', 'النقاط', 'تاريخ التسجيل'])
    customers = User.query.filter_by(role='customer').all()
    for c in customers:
        cw.writerow([c.id, c.full_name, c.phone, c.balance, c.debt, c.points, c.created_at])

    output = si.getvalue()
    si.close()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=AndalStar_Customers.csv"}
    )

@app.route('/admin/delete_user/<int:uid>', methods=['GET'])
def delete_user(uid):
    if session.get('role') != 'admin':
        return redirect('/login')

    user = db.session.get(User, uid)
    if user:
        try:
            Purchase.query.filter_by(user_id=user.id).delete()
            FinancialTransaction.query.filter_by(user_id=user.id).delete()
            PointsTransaction.query.filter_by(user_id=user.id).delete()
            Notification.query.filter_by(user_id=user.id).delete()

            db.session.delete(user)
            db.session.commit()
            flash(f'✅ تم حذف الزبون {user.full_name} وكافة سجلاته بنجاح!', 'success')
        except Exception as e:
            db.session.rollback()
            flash('❌ حدث خطأ أثناء الحذف.', 'error')

    return redirect('/admin/customers')

@app.route('/admin/give_points', methods=['POST'])
def admin_give_points():
    if session.get('role') != 'admin': return redirect('/login')
    user_id = int(request.form['user_id'])
    points = int(request.form['points'])
    reason = request.form.get('reason', 'هدية إدارية')
    try:
        user = db.session.get(User, user_id)
        user.points += points
        trans = PointsTransaction(user_id=user_id, amount=points, reason=reason)
        db.session.add(trans)
        db.session.commit()
        flash(f'✅ تم إعطاء {points} نقطة للعميل', 'success')
    except:
        db.session.rollback()
        flash('❌ حدث خطأ', 'error')
    return redirect('/admin/customers')

@app.route('/points_history')
def points_history():
    if 'user_id' not in session: return redirect('/login')
    user = db.session.get(User, session['user_id'])
    transactions = PointsTransaction.query.filter_by(user_id=user.id).order_by(PointsTransaction.created_at.desc()).all()
    return render_template('points_history.html', transactions=transactions, user=user)

@app.route('/convert_points', methods=['POST'])
def convert_points():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'يرجى تسجيل الدخول'})

    user = db.session.get(User, session['user_id'])
    if user.points < 100:
        return jsonify({'success': False, 'message': '❌ ليس لديك نقاط كافية (الحد الأدنى 100 نقطة)'})

    try:
        riyals = (user.points // 100) * 100
        user.balance += riyals
        user.points = user.points % 100
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'✅ تم تحويل النقاط إلى {riyals} ريال بنجاح!',
            'new_balance': round(user.balance, 1),
            'new_points': user.points
        })
    except:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'حدث خطأ غير متوقع'})

@app.route('/admin/offers')
def admin_offers():
    if session.get('role') != 'admin': return redirect('/login')
    offers = Offer.query.all()
    return render_template('admin_offers.html', offers=offers)

@app.route('/admin/add_offer', methods=['GET', 'POST'])
def add_offer():
    if session.get('role') != 'admin': return redirect('/login')
    if request.method == 'POST':
        offer = Offer(
            title=request.form['title'],
            slogan=request.form['slogan'],
            package_id=int(request.form['package_id']),
            discount=int(request.form['discount'])
        )
        db.session.add(offer)
        db.session.commit()
        flash('تم إضافة العرض!', 'success')
        return redirect('/admin/offers')
    packages = Package.query.all()
    return render_template('add_offer.html', packages=packages)

@app.route('/admin/add_package', methods=['GET', 'POST'])
def add_package():
    if session.get('role') != 'admin': return redirect('/login')
    if request.method == 'POST':
        p = Package(
            name=request.form['name'],
            price=float(request.form['price']),
            validity_days=int(request.form['days']),
            description=request.form.get('desc', '')
        )
        db.session.add(p)
        db.session.commit()
        flash('تم إضافة الباقة!', 'success')
        return redirect('/admin/packages')
    return render_template('add_package.html')

@app.route('/admin/delete_package/<int:pid>', methods=['POST'])
def delete_package(pid):
    if session.get('role') != 'admin': return redirect('/login')
    p = Package.query.get(pid)
    if p:
        db.session.delete(p)
        db.session.commit()
        flash('تم حذف الباقة', 'success')
    return redirect('/admin/packages')

@app.route('/admin/add_voucher', methods=['GET', 'POST'])
def add_voucher():
    if session.get('role') != 'admin': return redirect('/login')
    if request.method == 'POST':
        try:
            usernames = [u.strip() for u in request.form['usernames'].splitlines() if u.strip()]
            package_id = int(request.form['package_id'])
            added = 0
            for username in usernames:
                if Voucher.query.filter_by(mikrotik_username=username).first():
                    continue
                v = Voucher(mikrotik_username=username, mikrotik_password='', package_id=package_id)
                db.session.add(v)
                added += 1
            db.session.commit()
            flash(f' ✅ تم إضافة {added} كرت بنجاح!', 'success')
            return redirect('/admin/dashboard')
        except Exception as e:
            db.session.rollback()
            flash(f'❌ خطأ في الإدخال', 'error')
            return redirect('/admin/add_voucher')
    packages = Package.query.all()
    return render_template('add_voucher.html', packages=packages)

@app.route('/admin/notifications', methods=['GET', 'POST'])
def admin_notifications():
    if session.get('role') != 'admin': return redirect('/login')
    if request.method == 'POST':
        title = request.form['title']
        message = request.form['message']
        to_all = request.form.get('to_all') == 'on'

        try:
            if to_all:
                users = User.query.filter_by(role='customer').all()
                for user in users:
                    notif = Notification(user_id=user.id, title=title, message=message)
                    db.session.add(notif)
            else:
                user_id = int(request.form.get('user_id'))
                notif = Notification(user_id=user_id, title=title, message=message)
                db.session.add(notif)
            db.session.commit()
            flash('تم إرسال الإشعار بنجاح (داخل النظام)!', 'success')
        except:
            db.session.rollback()
            flash('حدث خطأ', 'error')

        return redirect('/admin/notifications')
    notifications = Notification.query.order_by(Notification.created_at.desc()).all()
    customers = User.query.filter_by(role='customer').all()
    return render_template('admin_notifications.html', notifications=notifications, customers=customers)

@app.route('/notifications')
def customer_notifications():
    if 'user_id' not in session: return redirect('/login')
    user = db.session.get(User, session['user_id'])
    notifications = Notification.query.filter(
        (Notification.user_id == user.id) | (Notification.user_id == None)
    ).order_by(Notification.created_at.desc()).all()

    for n in notifications:
        if not n.is_read:
            n.is_read = True
    db.session.commit()
    return render_template('notifications.html', notifications=notifications)

@app.route('/mark_read/<int:nid>')
def mark_read(nid):
    if 'user_id' not in session: return redirect('/login')
    notif = Notification.query.get_or_404(nid)
    notif.is_read = True
    db.session.commit()
    return redirect('/notifications')

@app.route('/admin/packages')
def manage_packages():
    if session.get('role') != 'admin': return redirect('/login')
    packages = Package.query.all()
    return render_template('manage_packages.html', packages=packages)

@app.route('/admin/stats')
def admin_stats():
    if session.get('role') != 'admin': return redirect('/login')
    revenue = db.session.query(db.func.sum(Purchase.amount_paid)).scalar() or 0
    sold = Voucher.query.filter_by(status='sold').count()
    available = Voucher.query.filter_by(status='available').count()
    users = User.query.filter_by(role='customer').count()
    return render_template('admin_stats.html', revenue=revenue, sold=sold, available=available, users=users)

@app.route('/admin/stock')
def admin_stock():
    if session.get('role') != 'admin': return redirect('/login')
    vouchers = Voucher.query.options(joinedload(Voucher.package)).order_by(Voucher.id.desc()).all()
    total = len(vouchers)
    available = sum(1 for v in vouchers if v.status == 'available')
    sold = total - available
    return render_template('admin_stock.html', vouchers=vouchers, total=total, available=available, sold=sold)

@app.route('/admin/statement/<int:uid>')
def customer_statement(uid):
    if session.get('role') != 'admin': return redirect('/login')
    user = db.session.get(User, uid)
    purchases = Purchase.query.filter_by(user_id=user.id).order_by(Purchase.id.desc()).all()
    transactions = FinancialTransaction.query.filter_by(user_id=user.id).order_by(FinancialTransaction.id.desc()).all()
    return render_template('admin_statement.html', user=user, purchases=purchases, transactions=transactions)

@app.route('/admin/sales_report', methods=['GET', 'POST'])
def admin_sales_report():
    if session.get('role') != 'admin': return redirect('/login')

    start_date = request.form.get('start_date') or request.args.get('start_date')
    end_date = request.form.get('end_date') or request.args.get('end_date')

    purchases = []
    total_revenue = 0
    total_sales = 0
    top_package = "لا يوجد"

    if start_date and end_date:
        start_filter = f"{start_date} 00:00"
        end_filter = f"{end_date} 23:59"
        purchases = Purchase.query.filter(
            Purchase.purchased_at >= start_filter,
            Purchase.purchased_at <= end_filter
        ).order_by(Purchase.id.desc()).all()

        total_revenue = sum(p.amount_paid for p in purchases)
        total_sales = len(purchases)
        if purchases:
            package_counts = Counter(p.package_name for p in purchases)
            top_package = package_counts.most_common(1)[0][0]

    return render_template('admin_sales_report.html',
                           purchases=purchases,
                           total_revenue=total_revenue,
                           total_sales=total_sales,
                           top_package=top_package,
                           start_date=start_date,
                           end_date=end_date)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

