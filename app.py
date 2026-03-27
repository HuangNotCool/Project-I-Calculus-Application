from flask import Flask, render_template, redirect, url_for, request, flash, Response, jsonify, session, send_file
from models import db, User, Badge
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import sympy as sp
import random
import os
import subprocess
import tempfile
import io

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
    
    # Khởi tạo huy hiệu mặc định nếu chưa có
    if not Badge.query.first():
        b1 = Badge(name="Một hành trình mới", description="Tạo tài khoản và gia nhập hệ thống Giải tích HUST.", icon="fa-seedling", color="text-success")
        b2 = Badge(name="Kẻ hủy diệt", description="Hoàn thành bài kiểm tra đầu tiên với điểm số tuyệt đối.", icon="fa-fire", color="text-danger")
        b3 = Badge(name="Cú đêm chăm chỉ", description="Làm bài tập ôn luyện sau 12h đêm.", icon="fa-moon", color="text-warning")
        db.session.add_all([b1, b2, b3])
        db.session.commit()

# --- CÁC ĐƯỜNG DẪN CƠ BẢN ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')

        user_exists = User.query.filter_by(username=username).first()
        if user_exists:
            flash('Tên đăng nhập hoặc Mã SV đã tồn tại!', 'danger')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password, role=role)
        
        db.session.add(new_user)
        db.session.commit() 
        
        # Trao huy hiệu cho học sinh mới
        if role == 'student':
            starter_badge = Badge.query.filter_by(name="Một hành trình mới").first()
            if starter_badge:
                new_user.badges.append(starter_badge)
                db.session.commit()
        
        flash('Đăng ký thành công! Vui lòng đăng nhập.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            if user.role == 'teacher':
                return redirect(url_for('teacher_dashboard'))
            else:
                return redirect(url_for('student_dashboard'))
        else:
            flash('Sai tên đăng nhập hoặc mật khẩu. Vui lòng thử lại!', 'danger')
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)

@app.route('/teacher-dashboard')
@login_required
def teacher_dashboard():
    if current_user.role != 'teacher':
        flash('Bạn không có quyền truy cập trang này!', 'danger')
        return redirect(url_for('student_dashboard'))
    return render_template('teacher_dashboard.html')

@app.route('/student-dashboard')
@login_required
def student_dashboard():
    return render_template('student_dashboard.html')


# --- 1. API NHẬN YÊU CẦU VÀ SINH ĐỀ (LƯU VÀO SESSION) ---
@app.route('/api/generate-exam', methods=['POST'])
@login_required
def api_generate_exam():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập'})

    title = request.form.get('exam_title', 'Đề thi Giải tích I')
    difficulty = request.form.get('difficulty')
    questions = []
    x = sp.Symbol('x')

    def tao_cau_hoi(muc_do):
        if muc_do == 'easy':
            a = random.randint(1, 5)
            b = random.randint(-5, 5)
            expr = a*x**2 + b*x + random.randint(1, 10)
            val = random.randint(0, 2)
        elif muc_do == 'medium':
            if random.choice([True, False]):
                a = random.randint(1, 5)
                c = random.randint(1, 3)
                expr = (a*x**2 - a*c**2) / (x - c)
                val = c
            else:
                a = random.randint(1, 5)
                b = random.randint(1, 5)
                expr = (a*x**2 + 1) / (b*x**2 + 2)
                val = sp.oo 
        else: # muc_do == 'hard'
            # Áp dụng thuật toán sinh đề tổng quát từ giấy nháp
            a = random.randint(2, 9)
            val = 0
            
            # Định nghĩa tập các hàm f(t)
            def f_exp(t): return sp.exp(t) - 1
            def f_sin(t): return sp.sin(t)
            def f_tan(t): return sp.tan(t)
            def f_asin(t): return sp.asin(t)
            def f_atan(t): return sp.atan(t)
            def f_ln(t): return sp.ln(1 + t)
            
            # Chọn ngẫu nhiên 1 hàm trong tập hợp
            f_chosen = random.choice([f_exp, f_sin, f_tan, f_asin, f_atan, f_ln])
            
            # Khởi tạo biểu thức chuẩn f(ax) / x
            expr = f_chosen(a*x) / x

        ans = sp.limit(expr, x, val)
        val_tex = sp.latex(val) 
        de_bai_latex = rf"\displaystyle \lim_{{x \to {val_tex}}} {sp.latex(expr)}"
        return de_bai_latex, ans

    if difficulty == 'mixed':
        qty_easy = int(request.form.get('qty_easy', 0))
        qty_medium = int(request.form.get('qty_medium', 0))
        qty_hard = int(request.form.get('qty_hard', 0))
        for _ in range(qty_easy): questions.append(tao_cau_hoi('easy'))
        for _ in range(qty_medium): questions.append(tao_cau_hoi('medium'))
        for _ in range(qty_hard): questions.append(tao_cau_hoi('hard'))
    else:
        qty = int(request.form.get('quantity', 0))
        for _ in range(qty): questions.append(tao_cau_hoi(difficulty))

    tex_content = f"\\documentclass[12pt, a4paper]{{article}}\n\\usepackage[utf8]{{vietnam}}\n\\usepackage{{amsmath}}\n\\usepackage{{setspace}}\n\\onehalfspacing\n\n\\begin{{document}}\n\n\\begin{{center}}\n\\Large\\textbf{{{title}}}\n\\end{{center}}\n\n\\vspace{{0.5cm}}\n\\begin{{enumerate}}\n"
    bang_dap_an = []

    for idx, (q, ans) in enumerate(questions):
        tex_content += f"    \\item Tính giới hạn sau: $ {q} $\n\n"
        correct_tex = sp.latex(ans)
        pool = [r"0", r"1", r"-1", r"\infty", r"-\infty", r"\text{Không tồn tại}"]
        
        if ans.is_number and not ans.has(sp.oo):
            try:
                pool.extend([sp.latex(ans + 1), sp.latex(ans - 1), sp.latex(-ans)])
            except: pass
                
        pool = list(set([p for p in pool if p != correct_tex]))
        distractors = random.sample(pool, min(3, len(pool)))
        options = [correct_tex] + distractors
        random.shuffle(options)
        
        dap_an_dung = ['A', 'B', 'C', 'D'][options.index(correct_tex)]
        bang_dap_an.append(f"\\textbf{{{idx + 1}.{dap_an_dung}}}")

        tex_content += f"    A. $ {options[0]} $ \\qquad B. $ {options[1]} $ \\qquad C. $ {options[2]} $ \\qquad D. $ {options[3]} $\n\n    \\vspace{{0.3cm}}\n\n"

   # 5. Đóng khối câu hỏi
    tex_content += "\\end{enumerate}\n\n"
    
    # 6. In bảng đáp án
    tex_content += "\\vspace{1cm}\n\\hrule\n\\vspace{0.5cm}\n"
    tex_content += "\\noindent\\textbf{\\Large BẢNG ĐÁP ÁN}\\\\\n\\vspace{0.5cm}\n"
    tex_content += "\\noindent " + " \\quad ".join(bang_dap_an) + "\n\n"
    
    # 7. Kết thúc file LaTeX chuẩn xác (1 ngoặc nhọn)
    tex_content += "\\end{document}"

    # LƯU KẾT QUẢ VÀO SESSION TRÌNH DUYỆT
    session['current_exam_tex'] = tex_content
    
    # Báo cho giao diện biết đã sinh xong
    return jsonify({'success': True})


# --- 2. ROUTE TẢI FILE TEX ---
@app.route('/download/tex')
@login_required
def download_tex():
    tex_content = session.get('current_exam_tex')
    if not tex_content:
        flash('Không tìm thấy đề thi. Vui lòng tạo lại.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    return Response(tex_content, mimetype="text/plain", headers={"Content-disposition": "attachment; filename=De_Thi_Giai_Tich.tex"})


# --- 3. ROUTE GỌI MIKTEX BIÊN DỊCH VÀ TẢI PDF TỪ RAM ---
@app.route('/download/pdf')
@login_required
def download_pdf():
    tex_content = session.get('current_exam_tex')
    if not tex_content:
        flash('Không tìm thấy đề thi. Vui lòng tạo lại.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    import tempfile
    import os
    import subprocess
    import io

    # Chứa dữ liệu PDF trên RAM
    pdf_memory = None

    with tempfile.TemporaryDirectory() as tempdir:
        tex_file_path = os.path.join(tempdir, 'exam.tex')
        pdf_file_path = os.path.join(tempdir, 'exam.pdf')

        with open(tex_file_path, 'w', encoding='utf-8') as f:
            f.write(tex_content)

        try:
            process = subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', '-output-directory', tempdir, tex_file_path],
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True
            )
            
            if process.returncode != 0:
                print("\n=== LỖI BIÊN DỊCH MIKTEX ===")
                print(process.stdout)
                print("============================\n")
                flash('Có lỗi cú pháp khi biên dịch PDF. Hãy xem Terminal (VS Code) để biết chi tiết!', 'danger')
                return redirect(url_for('teacher_dashboard'))
                
            # ĐỌC FILE PDF TỪ Ổ CỨNG VÀO RAM TRƯỚC KHI XOÁ THƯ MỤC TẠM
            with open(pdf_file_path, 'rb') as f:
                pdf_memory = io.BytesIO(f.read())

        except Exception as e:
            print("Lỗi gọi lệnh hệ thống:", str(e))
            flash('Không thể gọi phần mềm MiKTeX. Vui lòng kiểm tra lại cấu hình.', 'danger')
            return redirect(url_for('teacher_dashboard'))

    # Lúc này thư mục tạm đã bị xoá an toàn. Ta gửi PDF từ RAM cho người dùng.
    if pdf_memory:
        return send_file(pdf_memory, as_attachment=True, download_name='De_Thi_Giai_Tich.pdf', mimetype='application/pdf')
    else:
        flash('Lỗi đọc file PDF.', 'danger')
        return redirect(url_for('teacher_dashboard'))


# =====================================================================
# --- CÁC ROUTE XỬ LÝ CHO CHẾ ĐỘ SINH ĐỀ TÙY CHỈNH (ĐÃ TỐI ƯU SIÊU TỐC) ---
# =====================================================================

@app.route('/api/custom-exam/add', methods=['POST'])
@login_required
def custom_exam_add():
    x = sp.Symbol('x')
    expr_type = request.form.get('expr_type')
    limit_val_str = request.form.get('limit_val', '0').strip()
    
    if limit_val_str.lower() == 'oo': val = sp.oo
    elif limit_val_str.lower() == '-oo': val = -sp.oo
    else:
        try: val = sp.sympify(limit_val_str)
        except: val = 0
            
    is_random = request.form.get('is_random') == 'true'
    if is_random:
        a, b, c = random.randint(1, 5), random.randint(1, 5), random.randint(1, 5)
    else:
        try:
            a = int(request.form.get('a', 1))
            b = int(request.form.get('b', 1))
            c = int(request.form.get('c', 1))
        except:
            a, b, c = 1, 1, 1
            
    try:
        if expr_type == 'template_1':
            expr = (a*x**2 + b*x + c) / (x - val if val != sp.oo else x + 1)
        elif expr_type == 'template_2':
            expr = sp.sin(a*x) / (b*x)
        elif expr_type == 'template_3':
            expr = (sp.sqrt(a*x + b) - sp.sqrt(b)) / x
            val = 0 
        else: 
            custom_expr = request.form.get('custom_expr', 'x')
            expr = sp.sympify(custom_expr)
            
        ans = sp.limit(expr, x, val)
        val_tex = sp.latex(val)
        q_tex = rf"\displaystyle \lim_{{x \to {val_tex}}} {sp.latex(expr)}"
        
        # TÍNH TOÁN ĐÁP ÁN NHIỄU VÀ TRỘN NGAY TẠI ĐÂY
        correct_tex = sp.latex(ans)
        pool = [r"0", r"1", r"-1", r"\infty", r"-\infty", r"\text{Không tồn tại}"]
        if ans.is_number and not ans.has(sp.oo):
            try: pool.extend([sp.latex(ans + 1), sp.latex(ans - 1), sp.latex(-ans)])
            except: pass
                
        pool = list(set([p for p in pool if p != correct_tex]))
        distractors = random.sample(pool, min(3, len(pool)))
        options = [correct_tex] + distractors
        random.shuffle(options)
        
        correct_letter = ['A', 'B', 'C', 'D'][options.index(correct_tex)]
        
        question_data = {
            'q_tex': q_tex, 
            'ans_str': str(ans), 
            'options': options,          # Lưu sẵn 4 đáp án A B C D
            'correct_letter': correct_letter # Lưu sẵn chữ cái đúng
        }
        
        if 'custom_exam' not in session:
            session['custom_exam'] = []
        
        custom_list = session['custom_exam']
        custom_list.append(question_data)
        session['custom_exam'] = custom_list
        session.modified = True # Ép Flask lưu session ngay lập tức
        
        return jsonify({'success': True, 'questions': session['custom_exam']})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f"Lỗi cú pháp Toán học: {str(e)}"})


@app.route('/api/custom-exam/remove', methods=['POST'])
@login_required
def custom_exam_remove():
    index = int(request.form.get('index', -1))
    if 'custom_exam' in session and 0 <= index < len(session['custom_exam']):
        custom_list = session['custom_exam']
        custom_list.pop(index)
        session['custom_exam'] = custom_list
        session.modified = True
    return jsonify({'success': True, 'questions': session.get('custom_exam', [])})


@app.route('/api/custom-exam/generate', methods=['POST'])
@login_required
def custom_exam_generate():
    custom_list = session.get('custom_exam', [])
    if not custom_list:
        return jsonify({'success': False, 'message': 'Đề bài chưa có câu hỏi nào!'})

    title = request.form.get('exam_title', 'Đề thi Tùy chỉnh Giải tích I')
    
    tex_content = "\\documentclass[12pt, a4paper]{article}\n\\usepackage[utf8]{vietnam}\n\\usepackage{amsmath}\n\\usepackage{setspace}\n\\onehalfspacing\n\n\\begin{document}\n\n"
    tex_content += f"\\begin{{center}}\n\\Large\\textbf{{{title}}}\n\\end{{center}}\n\n\\vspace{{0.5cm}}\n\\begin{{enumerate}}\n"
    
    bang_dap_an = []

    try:
        # Quá trình gộp đề
        for idx, item in enumerate(custom_list):
            tex_content += f"    \\item Tính giới hạn sau: $ {item['q_tex']} $\n\n"
            opts = item['options'] # Nếu dính câu hỏi cũ không có 'options', sẽ nhảy ngay xuống except
            tex_content += f"    A. $ {opts[0]} $ \\qquad B. $ {opts[1]} $ \\qquad C. $ {opts[2]} $ \\qquad D. $ {opts[3]} $\n\n    \\vspace{{0.3cm}}\n\n"
            bang_dap_an.append(f"\\textbf{{{idx + 1}.{item['correct_letter']}}}")
            
    except KeyError:
        # Bắt lỗi dính Session cũ, tiến hành dọn dẹp
        session.pop('custom_exam', None)
        session.modified = True
        return jsonify({'success': False, 'message': 'Phát hiện câu hỏi cũ gây xung đột! Hệ thống đã tự động dọn dẹp bộ nhớ. Bạn vui lòng thêm lại câu hỏi mới rồi Gộp đề nhé!'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Lỗi hệ thống: {str(e)}'})

    tex_content += "\\end{enumerate}\n\n\\vspace{1cm}\n\\hrule\n\\vspace{0.5cm}\n"
    tex_content += "\\noindent\\textbf{\\Large BẢNG ĐÁP ÁN}\\\\\n\\vspace{0.5cm}\n"
    tex_content += "\\noindent " + " \\quad ".join(bang_dap_an) + "\n\n\\end{document}"

    session['current_exam_tex'] = tex_content
    session.pop('custom_exam', None) 
    session.modified = True
    
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True)