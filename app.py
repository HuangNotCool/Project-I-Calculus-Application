from flask import Flask, render_template, redirect, url_for, request, flash
from models import db, User
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'mot_chuoi_bi_mat_cuc_ky_kho_doan'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()

# --- CÁC ĐƯỜNG DẪN GIAO DIỆN CHÍNH ---

@app.route('/')
def home():
    return render_template('index.html')

# --- LOGIC ĐĂNG KÝ ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')

        # Kiểm tra xem tài khoản đã tồn tại chưa
        user_exists = User.query.filter_by(username=username).first()
        if user_exists:
            flash('Tên đăng nhập hoặc Mã SV đã tồn tại!', 'danger')
            return redirect(url_for('register'))

        # Băm mật khẩu để bảo mật và lưu vào Database
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password, role=role)
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('Đăng ký thành công! Vui lòng đăng nhập.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

# --- LOGIC ĐĂNG NHẬP ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        # Kiểm tra tài khoản và mật khẩu giải mã có khớp không
        if user and check_password_hash(user.password, password):
            login_user(user)
            
            # Điều hướng dựa trên vai trò
            if user.role == 'teacher':
                return redirect(url_for('teacher_dashboard'))
            else:
                return redirect(url_for('student_dashboard'))
        else:
            flash('Sai tên đăng nhập hoặc mật khẩu. Vui lòng thử lại!', 'danger')
            return redirect(url_for('login'))

    return render_template('login.html')

# --- LOGIC ĐĂNG XUẤT ---
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

# --- TRANG QUẢN TRỊ (CHỈ VÀO ĐƯỢC KHI ĐÃ ĐĂNG NHẬP) ---
@app.route('/teacher-dashboard')
@login_required
def teacher_dashboard():
    # Chặn học sinh vào trang của giáo viên
    if current_user.role != 'teacher':
        flash('Bạn không có quyền truy cập trang này!', 'danger')
        return redirect(url_for('student_dashboard'))
    return render_template('teacher_dashboard.html')

@app.route('/student-dashboard')
@login_required
def student_dashboard():
    return render_template('student_dashboard.html')

if __name__ == '__main__':
    app.run(debug=True)