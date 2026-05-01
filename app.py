from flask import Flask, render_template, redirect, url_for, request, flash, Response, jsonify, session, send_file,abort
from models import db, User, Badge, Class
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import sympy as sp
import random
import os
import subprocess
import tempfile
import io
import re
import numpy as np

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

def parse_advanced_template(template_text):
    def generate_instance(seed_val=1):
        try:
            seed_int = int(seed_val)
        except ValueError:
            seed_int = 1

        # Hàm xử lý cục bộ cho TỪNG khối câu hỏi (VD: %<cau1>% ... %</cau1>%)
        def process_block(match):
            block_name = match.group(1) # Tên khối (VD: cau1)
            block_content = match.group(2) # Nội dung bên trong khối
            
            # Tách biến của riêng khối này
            var_block_match = re.search(r'%%variables%%(.*?)%%variables%%', block_content, re.DOTALL | re.IGNORECASE)
            declarations = {}
            clean_content = block_content

            if var_block_match:
                var_block = var_block_match.group(1)
                clean_content = re.sub(r'%%variables%%.*?%%variables%%', '', block_content, flags=re.DOTALL | re.IGNORECASE).strip()
                
                lines = var_block.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if not line.startswith('%'): continue
                    
                    match_var = re.match(r'%(\w+)\s*[:=]\s*(.*)', line)
                    if match_var:
                        var_name = match_var.group(1)
                        raw_val = match_var.group(2).strip()
                        
                        if raw_val.startswith('<raw>{'):
                            items = raw_val[6:-1].split(';')
                            declarations[var_name] = [i.strip() for i in items]
                        elif raw_val.startswith('{') or (raw_val.startswith('[') and ';' in raw_val):
                            content = raw_val[1:-1]
                            items = [i.strip() for i in content.split(';')]
                            declarations[var_name] = items
                        elif raw_val.startswith('['):
                            try:
                                content = raw_val[1:-1]
                                params = [float(x.strip()) for x in content.split(',')]
                                if len(params) == 3:
                                    start, stop, step = params
                                    arr = np.arange(start, stop + step/2, step).tolist()
                                    declarations[var_name] = [int(v) if float(v).is_integer() else v for v in arr]
                            except: pass

            # Tính tổ hợp và gán biến cục bộ cho khối này dựa trên Seed
            chosen_vars = {}
            if declarations:
                total_combinations = 1
                for v in declarations.values():
                    if v: total_combinations *= len(v)

                if total_combinations > 0:
                    current_index = max(0, seed_int - 1) % total_combinations
                    temp_idx = current_index
                    for var_name, values in declarations.items():
                        if values:
                            length = len(values)
                            chosen_vars[var_name] = values[temp_idx % length]
                            temp_idx = temp_idx // length
                            
# Xử lý Toán học cục bộ
            def eval_math(math_match):
                expr = math_match.group(1).strip()
                
                # --- SỬA LỖI Ở ĐÂY: Nếu chỉ là tên biến đơn giản (VD: @menhde@), trả về giá trị thô luôn
                if expr in chosen_vars:
                    return str(chosen_vars[expr])
                
                # Nếu là biểu thức phức tạp (VD: @a + b@) thì mới bắt đầu xử lý tiếp
                expr = expr.replace('^', '**')
                
                words = re.findall(r'\b[a-zA-Z_]\w*\b', expr)
                math_funcs = ['sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'arcsin', 'arccos', 'arctan', 'exp', 'log', 'ln', 'simplify', 'diff', 'integrate', 'limit', 'lim', 'root', 'sqrt', 'oo', 'inf', 'Sum', 'Rational', 'Symbol', 'Eq', 'And', 'Piecewise', 'doit', 'removeO', 'series', 'subs', 'Subs', 'Derivative', 'pi', 'x', 'y', 'z', 'k', 'n', 'p']
                
                for word in words:
                    if word not in chosen_vars and word not in math_funcs:
                        return f"\\text{{[LỖI: Biến '{word}' chưa được khai báo trong khối {block_name}]}}"

                sorted_vars = sorted(chosen_vars.keys(), key=len, reverse=True)
                for var_name in sorted_vars:
                    val = str(chosen_vars[var_name])
                    expr = re.sub(rf'\b{var_name}\b', val, expr)
                
                try:
                    safe_dict = {
                        'sp': sp, 'sin': sp.sin, 'cos': sp.cos, 'tan': sp.tan, 
                        'asin': sp.asin, 'acos': sp.acos, 'atan': sp.atan,
                        'arcsin': sp.asin, 'arccos': sp.acos, 'arctan': sp.atan,
                        'exp': sp.exp, 'log': sp.log, 'ln': sp.ln, 'sqrt': sp.sqrt,
                        'limit': sp.limit, 'lim': sp.limit, 'integrate': sp.integrate,
                        'diff': sp.diff, 'simplify': sp.simplify, 'oo': sp.oo, 'inf': sp.oo,
                        'Rational': sp.Rational, 'Sum': sp.Sum, 'Symbol': sp.Symbol,
                        'Eq': sp.Eq, 'And': sp.And, 'Piecewise': sp.Piecewise,
                        'Subs': sp.Subs, 'Derivative': sp.Derivative, 'x': sp.Symbol('x'), 
                        'k': sp.Symbol('k'), 'n': sp.Symbol('n'), 'p': sp.Symbol('p'), 'pi': sp.pi
                    }
                    result = eval(expr, {"__builtins__": {'abs': abs, 'int': int, 'float': float}}, safe_dict)
                    
                    if hasattr(result, 'doit') and not isinstance(result, type):
                        result = result.doit()
                    
                    if isinstance(result, float) and result.is_integer():
                        result = int(result)
                    elif hasattr(result, 'is_Float') and getattr(result, 'is_Float') and int(result) == result:
                        result = int(result)
                        
                    return sp.latex(result)
                except Exception as e:
                    return f"\\text{{[LỖI Toán học tại {block_name}: {str(e)}]}}"

            # Trả về nội dung khối đã được xử lý
            return re.sub(r'@(.*?)@', eval_math, clean_content)

        # QUAN TRỌNG: Quét toàn bộ template gốc, tìm các khối %<tên>% ... %</tên>% và gọi hàm xử lý
        final_document = re.sub(r'%<([^>]+)>%(.*?)%</\1>%', process_block, template_text, flags=re.DOTALL)
        return final_document

    return generate_instance

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
    
    # Lấy toàn bộ lớp học do giáo viên này tạo từ CSDL
    my_classes = Class.query.filter_by(teacher_id=current_user.id).all()
    
    return render_template('teacher_dashboard.html', classes=my_classes)


# =====================================================================
# --- API DASHBOARD HỌC SINH (ĐÃ CẬP NHẬT DATABASE) ---
# =====================================================================
@app.route('/student-dashboard')
@login_required
def student_dashboard():
    if current_user.role != 'student':
        return redirect(url_for('teacher_dashboard'))
    
    # Lấy danh sách các lớp học mà học sinh này đã tham gia (nhờ relationship enrolled_classes trong models)
    my_classes = current_user.enrolled_classes
    return render_template('student_dashboard.html', classes=my_classes)


# =====================================================================
# --- API GIẢNG VIÊN: TẠO LỚP HỌC MỚI ---
# =====================================================================
@app.route('/api/teacher/create-class', methods=['POST'])
@login_required
def api_create_class():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    name = request.form.get('name')
    description = request.form.get('description')
    code = request.form.get('code')

    if not name or not code:
        return jsonify({'success': False, 'message': 'Thiếu thông tin lớp học!'})

    # Kiểm tra mã lớp đã tồn tại chưa
    existing_class = Class.query.filter_by(code=code).first()
    if existing_class:
        return jsonify({'success': False, 'message': 'Mã lớp này đã tồn tại, vui lòng tạo mã khác!'})

    new_class = Class(name=name, description=description, code=code, teacher_id=current_user.id)
    db.session.add(new_class)
    db.session.commit()

    return jsonify({'success': True})


# =====================================================================
# --- API GIẢNG VIÊN: XÓA LỚP HỌC ---
# =====================================================================
@app.route('/api/teacher/delete-class/<int:class_id>', methods=['POST'])
@login_required
def api_delete_class(class_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    # Tìm lớp học trong CSDL
    target_class = Class.query.get_or_404(class_id)
    
    # Xác thực: Chỉ người tạo lớp mới được quyền xóa
    if target_class.teacher_id != current_user.id:
        return jsonify({'success': False, 'message': 'Bạn không có quyền xóa lớp này!'})

    try:
        # Hủy liên kết với tất cả học sinh trước khi xóa lớp
        target_class.students.clear()
        
        # Xóa lớp khỏi CSDL
        db.session.delete(target_class)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Đã xóa lớp học thành công.'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Lỗi hệ thống: {str(e)}'})

# =====================================================================
# --- CÁC ROUTE XỬ LÝ SINH ĐỀ (GIỮ NGUYÊN) ---
# =====================================================================
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
        def f_exp(t): return sp.exp(t) - 1
        def f_sin(t): return sp.sin(t)
        def f_tan(t): return sp.tan(t)
        def f_asin(t): return sp.asin(t)
        def f_atan(t): return sp.atan(t)
        def f_ln(t): return sp.ln(1 + t)
        
        tap_ham = [f_exp, f_sin, f_tan, f_asin, f_atan, f_ln]

        if muc_do == 'easy':
            if random.choice([True, False]):
                a, b, c = random.randint(1, 5), random.randint(-5, 5), random.randint(1, 10)
                expr = a*x**2 + b*x + c
                val = random.randint(0, 3)
            else:
                a, b = random.randint(1, 5), random.randint(1, 5)
                expr = (a*x**2 + 1) / (b*x**2 + 2)
                val = sp.oo 
                
        elif muc_do == 'medium':   
            if random.choice([True, False]):
                a = random.randint(2, 9)
                f_chosen = random.choice(tap_ham)
                expr = f_chosen(a*x) / x
                val = 0
            else:
                a, c = random.randint(1, 5), random.randint(1, 3)
                expr = (a*x**2 - a*c**2) / (x - c)
                val = c
        else: 
            a, b = random.randint(2, 6), random.randint(2, 6)
            f_1 = random.choice(tap_ham)
            f_2 = random.choice(tap_ham)
            
            if random.choice([True, False]):
                expr = f_1(a*x) / f_2(b*x)
            else:
                expr = (f_1(a*x) * f_2(b*x)) / (x**2)
            val = 0

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

    tex_content += "\\end{enumerate}\n\n"
    tex_content += "\\vspace{1cm}\n\\hrule\n\\vspace{0.5cm}\n"
    tex_content += "\\noindent\\textbf{\\Large BẢNG ĐÁP ÁN}\\\\\n\\vspace{0.5cm}\n"
    tex_content += "\\noindent " + " \\quad ".join(bang_dap_an) + "\n\n"
    tex_content += "\\end{document}"

    session['current_exam_tex'] = tex_content
    return jsonify({'success': True})

@app.route('/download/tex')
@login_required
def download_tex():
    tex_content = session.get('current_exam_tex')
    if not tex_content:
        flash('Không tìm thấy đề thi. Vui lòng tạo lại.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    return Response(tex_content, mimetype="text/plain", headers={"Content-disposition": "attachment; filename=De_Thi_Giai_Tich.tex"})

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
    import shutil

    pdf_memory = None

    with tempfile.TemporaryDirectory() as tempdir:
        tex_file_path = os.path.join(tempdir, 'exam.tex')
        pdf_file_path = os.path.join(tempdir, 'exam.pdf')

        # 1. Ghi file nội dung TEX
        with open(tex_file_path, 'w', encoding='utf-8') as f:
            f.write(tex_content)

        # 2. CHÌA KHÓA: Trỏ đường dẫn tuyệt đối dựa trên vị trí của app.py
        basedir = os.path.abspath(os.path.dirname(__file__))
        cls_path_source = os.path.join(basedir, 'fami-exam.cls')
        
        if os.path.exists(cls_path_source):
            shutil.copy(cls_path_source, tempdir)
        else:
            # Thêm chữ 'r' phía trước chuỗi để fix lỗi invalid escape sequence \C
            print(r"\n[CẢNH BÁO] Không tìm thấy file fami-exam.cls ở thư mục dự án. Biên dịch có thể lỗi!\n")

        # 3. Gọi lệnh biên dịch ngay bên trong thư mục tạm
        try:
            process = subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', 'exam.tex'], 
                cwd=tempdir,
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True
            )
            
            if process.returncode != 0:
                print("\n=== LỖI BIÊN DỊCH MIKTEX CỤ THỂ ===")
                log_lines = process.stdout.split('\n')
                print('\n'.join(log_lines[-20:]))
                print("======================================\n")
                
                flash('Có lỗi cú pháp LaTeX. Hãy mở Terminal (VS Code) xem chi tiết!', 'danger')
                return redirect(url_for('teacher_dashboard'))
                
            with open(pdf_file_path, 'rb') as f:
                pdf_memory = io.BytesIO(f.read())

        except Exception as e:
            print("Lỗi hệ thống khi gọi MiKTeX:", str(e))
            flash('Không thể gọi lệnh pdflatex. Hãy chắc chắn bạn đã cài MiKTeX và thêm vào biến môi trường PATH.', 'danger')
            return redirect(url_for('teacher_dashboard'))

    if pdf_memory:
        return send_file(pdf_memory, as_attachment=True, download_name='De_Thi_Giai_Tich.pdf', mimetype='application/pdf')
    else:
        flash('Lỗi đọc file PDF.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    if pdf_memory:
        return send_file(pdf_memory, as_attachment=True, download_name='De_Thi_Giai_Tich.pdf', mimetype='application/pdf')
    else:
        flash('Lỗi đọc file PDF.', 'danger')
        return redirect(url_for('teacher_dashboard'))


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
            'options': options,
            'correct_letter': correct_letter
        }
        
        if 'custom_exam' not in session:
            session['custom_exam'] = []
        
        custom_list = session['custom_exam']
        custom_list.append(question_data)
        session['custom_exam'] = custom_list
        session.modified = True
        
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
        for idx, item in enumerate(custom_list):
            tex_content += f"    \\item Tính giới hạn sau: $ {item['q_tex']} $\n\n"
            opts = item['options']
            tex_content += f"    A. $ {opts[0]} $ \\qquad B. $ {opts[1]} $ \\qquad C. $ {opts[2]} $ \\qquad D. $ {opts[3]} $\n\n    \\vspace{{0.3cm}}\n\n"
            bang_dap_an.append(f"\\textbf{{{idx + 1}.{item['correct_letter']}}}")
            
    except KeyError:
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

# =====================================================================
# --- API GIẢNG VIÊN: XEM DANH SÁCH SINH VIÊN TRONG LỚP ---
# =====================================================================
@app.route('/api/teacher/class/<int:class_id>/students', methods=['GET'])
@login_required
def api_get_class_students(class_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    target_class = Class.query.get_or_404(class_id)
    
    # Xác thực quyền sở hữu lớp
    if target_class.teacher_id != current_user.id:
        return jsonify({'success': False, 'message': 'Bạn không có quyền xem lớp này!'})

    # Đóng gói dữ liệu sinh viên
    students_data = []
    for student in target_class.students:
        students_data.append({
            'id': student.id,
            'username': student.username,
            # Tạm thời để tiến độ ngẫu nhiên hoặc 0%, sau này bạn nối bảng điểm vào đây
            'progress': random.choice([0, 20, 50, 80, 100]) 
        })

    return jsonify({
        'success': True, 
        'class_name': target_class.name,
        'class_code': target_class.code,
        'students': students_data
    })


# =====================================================================
# --- API KẾT NỐI: HỌC SINH THAM GIA LỚP HỌC (DATABASE THẬT) ---
# =====================================================================
@app.route('/api/student/join-class', methods=['POST'])
@login_required
def student_join_class():
    if current_user.role != 'student':
        return jsonify({'success': False, 'message': 'Chỉ học sinh mới có thể tham gia lớp!'})

    class_code = request.form.get('class_code', '').strip().upper()

    if not class_code:
        return jsonify({'success': False, 'message': 'Vui lòng nhập mã lớp!'})

    # 1. Truy vấn Database xem mã lớp có tồn tại không
    target_class = Class.query.filter_by(code=class_code).first()

    if target_class:
        # 2. Kiểm tra xem học sinh này đã tham gia lớp chưa
        if current_user in target_class.students:
            return jsonify({'success': False, 'message': 'Bạn đã tham gia lớp học này rồi!'})
        
        # 3. Thêm học sinh vào lớp và lưu lại
        target_class.students.append(current_user)
        db.session.commit()

        # Lấy tên giáo viên phụ trách để trả về 
        teacher = User.query.get(target_class.teacher_id)
        teacher_name = teacher.username if teacher else 'Giáo viên'

        return jsonify({
            'success': True, 
            'message': 'Tham gia thành công!',
            'class_info': {
                'code': target_class.code,
                'name': target_class.name,
                'teacher': teacher_name
            }
        })
    else:
        return jsonify({'success': False, 'message': 'Mã lớp không tồn tại hoặc đã bị xóa.'})


# API: Nhận Template và trả về danh sách các biến thể đã sinh
@app.route('/api/custom-exam/process-template', methods=['POST'])
@login_required
def api_process_template():
    data = request.get_json()
    template = data.get('template', '')
    seeds_str = str(data.get('seeds', '1')).strip()
    
    # Tách chuỗi nhập vào thành danh sách các số nguyên
    seeds = []
    for s in seeds_str.split(','):
        try:
            seeds.append(int(s.strip()))
        except ValueError:
            pass
            
    if not seeds:
        seeds = [1] # Mặc định là seed 1 nếu nhập sai
    
    try:
        gen_func = parse_advanced_template(template)
        variants = []
        for s in seeds:
            variants.append({
                'seed': s,
                'content': gen_func(s) 
            })
        return jsonify({'success': True, 'variants': variants})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# API: Lưu kết quả đã sinh vào session để xuất PDF/TEX
@app.route('/api/custom-exam/generate-bulk', methods=['POST'])
@login_required
def custom_exam_generate_bulk():
    data = request.get_json()
    if not data or 'variants' not in data:
        return jsonify({'success': False, 'message': 'Không có dữ liệu!'})

    variants = data['variants']
    tex_content = ""
    preamble = ""
    body_content = ""

    for idx, v in enumerate(variants):
        content = str(v['content']).replace('\\(', '$').replace('\\)', '$')
        
        # Regex siêu mạnh: Bắt bất chấp khoảng trắng thừa
        match = re.search(r'\\begin\s*\{document\}(.*?)\\end\s*\{document\}', content, re.DOTALL)
        
        if match:
            if idx == 0:
                preamble = content[:match.start()] + "\\begin{document}\n"
            body_content += f"\\begin{{center}}\\Large\\textbf{{MÃ ĐỀ: {v['seed']}}}\\end{{center}}\n\n"
            body_content += match.group(1).strip() + "\n\\newpage\n"
        else:
            # Fallback an toàn nếu template quên mất \begin{document}
            title = data.get('exam_title', 'Đề thi Tùy chỉnh')
            if idx == 0:
                preamble = f"\\documentclass[12pt, a4paper]{{article}}\n\\usepackage[utf8]{{vietnam}}\n\\usepackage{{amsmath}}\n\\begin{{document}}\n\\begin{{center}}\\Large\\textbf{{{title}}}\\end{{center}}\n\n"
            
            # Quét dọn rác tránh xung đột LaTeX
            clean_content = re.sub(r'\\documentclass\[.*?\]\{.*?\}|\\documentclass\{.*?\}', '', content)
            clean_content = re.sub(r'\\usepackage\[.*?\]\{.*?\}|\\usepackage\{.*?\}', '', clean_content)
            body_content += f"\\textbf{{[Mã đề: {v['seed']}]}}\n\n {clean_content} \n\n\\vspace{{1cm}}\n"

    if preamble:
        tex_content = preamble + body_content + "\\end{document}"

    session['current_exam_tex'] = tex_content
    session.modified = True
    return jsonify({'success': True})

# API: Tải file mẫu (.cls, .tex)
@app.route('/download/sample/<filename>')
@login_required
def download_sample(filename):
    # CHỐT CHẶN BẢO MẬT: Chỉ cho phép tải đúng 3 file này
    allowed_files = ['fami-exam.cls']
    if filename not in allowed_files:
        abort(404) # Trả về lỗi 404 nếu cố tình tải file khác

    # Trỏ đến thư mục gốc chứa các file này
    basedir = os.path.abspath(os.path.dirname(__file__))
    file_path = os.path.join(basedir, filename)

    if os.path.exists(file_path):
        # as_attachment=True sẽ ép trình duyệt tải file về thay vì mở trực tiếp trên tab mới
        return send_file(file_path, as_attachment=True)
    else:
        flash(f'Không tìm thấy file {filename} trên hệ thống!', 'danger')
        return redirect(url_for('teacher_dashboard'))

if __name__ == '__main__':
    app.run(debug=True)