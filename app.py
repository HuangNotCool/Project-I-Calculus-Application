from flask import Flask, render_template, redirect, url_for, request, flash, Response, jsonify, session, send_file, abort, g
from models import db, User, Badge, Class, ClassJoinRequest, ExamTemplate, ExamBank, Assignment, AssignmentAttempt, Submission, Notification
from random_exam import (
    GeneratedChoice,
    GeneratedQuestion,
    GENERATORS as RANDOM_EXAM_GENERATORS,
    TOPIC_LABELS as RANDOM_EXAM_TOPIC_LABELS,
    generate_replacement_question,
    generate_random_questions,
    generate_random_variants,
    generated_question_from_dict,
    render_random_exam_tex,
    validate_generated_question,
)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate, upgrade, stamp
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, func, or_
from sqlalchemy.exc import IntegrityError
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import sys
import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)
import random
import os
import subprocess
import shutil
import tempfile
import io
import re
import csv
import hashlib
import secrets
import sqlite3
import threading
import time
import click
import numpy as np
import json
from datetime import datetime, timedelta, timezone


SOURCE_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(getattr(sys, '_MEIPASS', SOURCE_DIR)).resolve()


def get_application_data_dir():
    configured = os.getenv('GIAITICH_APP_DATA_DIR', '').strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        local_app_data = os.getenv('LOCALAPPDATA', '').strip()
        root = Path(local_app_data) if local_app_data else (Path.home() / 'AppData' / 'Local')
        return (root / 'GiaiTichHUST').resolve()
    return (SOURCE_DIR / 'instance').resolve()


INSTANCE_DIR = get_application_data_dir()
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)


def load_local_env():
    for env_path in (INSTANCE_DIR / '.env', BASE_DIR / '.env'):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding='utf-8').splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


def get_secret_key():
    configured = os.getenv('SECRET_KEY', '').strip()
    if configured:
        return configured
    secret_path = INSTANCE_DIR / '.secret_key'
    if secret_path.exists():
        return secret_path.read_text(encoding='utf-8').strip()
    generated = secrets.token_urlsafe(48)
    secret_path.write_text(generated, encoding='utf-8')
    return generated


load_local_env()
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / 'templates'),
    static_folder=str(BASE_DIR / 'static'),
    instance_path=str(INSTANCE_DIR),
)
app.config['SECRET_KEY'] = get_secret_key()
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///database.db')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('COOKIE_SECURE', '0') == '1'
app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 5 * 1024 * 1024))
app.config['APP_TIMEZONE'] = os.getenv('APP_TIMEZONE', 'Asia/Bangkok')
app.config['ATTEMPT_SWEEP_INTERVAL_SECONDS'] = max(
    int(os.getenv('ATTEMPT_SWEEP_INTERVAL_SECONDS', '15')),
    5
)
app.config['PDF_QUALITY_GATE'] = os.getenv('PDF_QUALITY_GATE', '1') == '1'

DEFAULT_ADMIN_USERNAME = 'admin'
DEFAULT_ADMIN_PASSWORD = 'hustoneloveonefuture'
APP_VERSION = '0.9.0'


def app_now():
    try:
        configured_timezone = ZoneInfo(app.config['APP_TIMEZONE'])
    except ZoneInfoNotFoundError:
        configured_timezone = timezone(timedelta(hours=float(os.getenv('APP_UTC_OFFSET_HOURS', '7'))))
    return datetime.now(configured_timezone).replace(tzinfo=None)

db.init_app(app)
migrate = Migrate(app, db, compare_type=True, directory=str(BASE_DIR / 'migrations'))

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)


_rate_limit_lock = threading.Lock()
_rate_limit_events = {}
_submission_locks_guard = threading.Lock()
_submission_locks = {}
_attempt_sweeper_guard = threading.Lock()
_attempt_sweeper_started = False
_attempt_sweeper_stop = threading.Event()


def security_error(message, status=403):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': message}), status
    abort(status, description=message)


def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


@app.context_processor
def inject_security_context():
    return {'csrf_token': get_csrf_token}


@app.before_request
def enforce_request_security():
    g.request_started_at = time.monotonic()

    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        supplied_token = request.headers.get('X-CSRF-Token') or request.form.get('_csrf_token')
        expected_token = session.get('_csrf_token')
        if not expected_token or not supplied_token or not secrets.compare_digest(expected_token, supplied_token):
            return security_error('Phiên bảo mật không hợp lệ. Hãy tải lại trang và thử lại.', 400)

    if not request.path.startswith('/api/'):
        return None

    teacher_prefixes = (
        '/api/teacher/',
        '/api/exam-bank',
        '/api/template-library',
        '/api/custom-exam',
        '/api/generate-exam'
    )
    student_prefixes = ('/api/student/',)
    admin_prefixes = ('/api/admin/',)
    required_role = None
    if request.path.startswith(admin_prefixes):
        required_role = 'admin'
    elif request.path.startswith(teacher_prefixes):
        required_role = 'teacher'
    elif request.path.startswith(student_prefixes):
        required_role = 'student'

    if required_role:
        if not current_user.is_authenticated:
            return security_error('Vui lòng đăng nhập để tiếp tục.', 401)
        if current_user.role != required_role:
            return security_error('Bạn không có quyền thực hiện thao tác này.', 403)
    return None


def rate_limit(limit, window_seconds, key_prefix):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if request.method == 'GET':
                return func(*args, **kwargs)
            identity = str(current_user.id) if current_user.is_authenticated else request.remote_addr or 'unknown'
            if key_prefix in {'login', 'register'}:
                identity = f'{identity}:{str(request.form.get("username") or "").strip().lower()}'
            key = f'{key_prefix}:{identity}'
            now = time.monotonic()
            cutoff = now - window_seconds

            with _rate_limit_lock:
                events = [event for event in _rate_limit_events.get(key, []) if event > cutoff]
                if len(events) >= limit:
                    retry_after = max(1, int(window_seconds - (now - events[0])))
                    response = security_error(
                        f'Bạn thao tác quá nhanh. Vui lòng thử lại sau {retry_after} giây.',
                        429
                    )
                    if isinstance(response, tuple):
                        response[0].headers['Retry-After'] = str(retry_after)
                    return response
                events.append(now)
                _rate_limit_events[key] = events
            return func(*args, **kwargs)
        return wrapper
    return decorator


def role_required(role):
    def decorator(func):
        @wraps(func)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role != role:
                return security_error('Bạn không có quyền thực hiện thao tác này.', 403)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def teacher_owns_class(func):
    @wraps(func)
    @role_required('teacher')
    def wrapper(*args, **kwargs):
        class_id = kwargs.get('class_id')
        target_class = db.get_or_404(Class, class_id)
        if target_class.teacher_id != current_user.id:
            return security_error('Bạn không có quyền truy cập lớp này.', 403)
        g.target_class = target_class
        return func(*args, **kwargs)
    return wrapper


def teacher_owns_assignment(func):
    @wraps(func)
    @role_required('teacher')
    def wrapper(*args, **kwargs):
        assignment_id = kwargs.get('assignment_id')
        assignment = db.get_or_404(Assignment, assignment_id)
        if assignment.teacher_id != current_user.id:
            return security_error('Bạn không có quyền truy cập bài được giao này.', 403)
        g.assignment = assignment
        return func(*args, **kwargs)
    return wrapper


def student_can_access_assignment(func):
    @wraps(func)
    @role_required('student')
    def wrapper(*args, **kwargs):
        assignment_id = kwargs.get('assignment_id')
        assignment = db.get_or_404(Assignment, assignment_id)
        if assignment.class_id not in {item.id for item in current_user.enrolled_classes}:
            return security_error('Bạn không thuộc lớp được giao bài này.', 403)
        g.assignment = assignment
        return func(*args, **kwargs)
    return wrapper


def get_submission_lock(attempt_id):
    with _submission_locks_guard:
        lock = _submission_locks.get(attempt_id)
        if lock is None:
            lock = threading.Lock()
            _submission_locks[attempt_id] = lock
        return lock


def get_current_exam_output_dir():
    return Path(app.config.get('CURRENT_EXAM_DIR') or (INSTANCE_DIR / 'current_exams'))


def store_current_exam_tex(tex_content):
    output_dir = get_current_exam_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f'user_{current_user.id}.tex'
    temporary_path = output_dir / f'user_{current_user.id}.tmp'
    temporary_path.write_text(str(tex_content or ''), encoding='utf-8')
    temporary_path.replace(output_path)
    session.pop('current_exam_tex', None)
    session['current_exam_ready'] = True
    session.modified = True


def load_current_exam_tex():
    output_dir = get_current_exam_output_dir()
    output_path = output_dir / f'user_{current_user.id}.tex'
    if output_path.exists():
        return output_path.read_text(encoding='utf-8')
    return session.get('current_exam_tex')


def get_export_settings_path():
    return Path(app.config.get('EXPORT_SETTINGS_PATH') or (INSTANCE_DIR / 'export_settings.json'))


def get_default_export_dir():
    configured = str(app.config.get('DEFAULT_EXPORT_DIR') or os.getenv('GIAITICH_EXPORT_DIR', '')).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    downloads_dir = Path.home() / 'Downloads'
    return (downloads_dir / 'GiaiTichHUST').resolve()


def read_export_settings():
    settings_path = get_export_settings_path()
    if not settings_path.exists():
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_export_settings(settings):
    settings_path = get_export_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = settings_path.with_suffix(settings_path.suffix + '.tmp')
    temporary_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary_path.replace(settings_path)


def get_user_export_dir(user_id=None):
    user_key = str(user_id or current_user.id)
    settings = read_export_settings()
    raw_path = ''
    if isinstance(settings.get(user_key), dict):
        raw_path = str(settings[user_key].get('export_dir') or '').strip()
    export_dir = Path(raw_path).expanduser().resolve() if raw_path else get_default_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def set_user_export_dir(raw_path):
    export_dir = Path(str(raw_path or '').strip()).expanduser().resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    if not export_dir.is_dir():
        raise ValueError('Đường dẫn đã chọn không phải là thư mục.')

    settings = read_export_settings()
    user_key = str(current_user.id)
    user_settings = settings.get(user_key) if isinstance(settings.get(user_key), dict) else {}
    user_settings['export_dir'] = str(export_dir)
    settings[user_key] = user_settings
    write_export_settings(settings)
    return export_dir


def safe_export_filename(filename):
    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(filename or '').strip())
    cleaned = cleaned.strip('._')
    return cleaned or 'download'


def reserve_export_path(export_dir, filename):
    safe_name = safe_export_filename(filename)
    candidate = export_dir / safe_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem or 'download'
    suffix = candidate.suffix
    for index in range(1, 1000):
        numbered = export_dir / f'{stem}_{index}{suffix}'
        if not numbered.exists():
            return numbered
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return export_dir / f'{stem}_{timestamp}{suffix}'


def save_tex_to_export_dir(tex_content, filename):
    export_dir = get_user_export_dir()
    output_path = reserve_export_path(export_dir, filename)
    output_path.write_text(str(tex_content or ''), encoding='utf-8')
    return output_path


def save_pdf_to_export_dir(tex_content, filename):
    pdf_bytes, compile_error = compile_tex_pdf_bytes(tex_content)
    if compile_error:
        raise RuntimeError(compile_error)
    export_dir = get_user_export_dir()
    output_path = reserve_export_path(export_dir, filename)
    output_path.write_bytes(pdf_bytes)
    return output_path


def export_file_payload(output_path):
    return {
        'success': True,
        'filename': output_path.name,
        'path': str(output_path),
        'directory': str(output_path.parent),
        'message': f'Đã lưu file tại {output_path}'
    }


def store_random_exam_draft(draft):
    output_dir = get_current_exam_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f'user_{current_user.id}_random.json'
    temporary_path = output_dir / f'user_{current_user.id}_random.tmp'
    temporary_path.write_text(json.dumps(draft, ensure_ascii=False), encoding='utf-8')
    temporary_path.replace(output_path)


def load_random_exam_draft():
    output_path = get_current_exam_output_dir() / f'user_{current_user.id}_random.json'
    if not output_path.exists():
        return None
    try:
        draft = json.loads(output_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return draft if isinstance(draft, dict) else None


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def parse_advanced_template(template_text):
    def generate_instance(seed_val=1):
        try:
            seed_int = int(seed_val)
        except ValueError:
            seed_int = 1

        def normalize_latex_signs(text):
            # Clean sign pairs created by replacing variables with negative values.
            atom = r'(\\frac\{[^{}]+\}\{[^{}]+\}|\\infty|\d+(?:\.\d+)?)'
            rules = [
                (rf'-\s*-\s*{atom}', r'+ \1'),
                (rf'\+\s*-\s*{atom}', r'- \1'),
                (rf'-\s*\+\s*{atom}', r'- \1'),
                (rf'\+\s*\+\s*{atom}', r'+ \1'),
            ]
            for pattern, replacement in rules:
                text = re.sub(pattern, replacement, text)

            # Clean coefficient 1 created by templates like @a@x when a = 1.
            coefficient_targets = (
                r'(?:[xyztknp]\b|\|[xyztknp]\||'
                r'\\(?:pi|sin|cos|tan|arcsin|arccos|arctan|sinh|cosh|tanh|ln|log|sqrt)\b)'
            )
            text = re.sub(rf'(?<![\d.])-1(?={coefficient_targets})', '-', text)
            text = re.sub(rf'(?<![\d.])1(?={coefficient_targets})', '', text)

            # Clean exponent 1 created by templates like x^@m@ when m = 1.
            text = re.sub(r'(\|[xyztknp]\|)\^\{1\}', r'\1', text)
            text = re.sub(r'(\|[xyztknp]\|)\^1', r'\1', text)
            text = re.sub(r'(?<![A-Za-z\\])([xyztknp])\^\{1\}', r'\1', text)
            text = re.sub(r'(?<![A-Za-z\\])([xyztknp])\^1', r'\1', text)
            text = re.sub(r'(\\right[\)\]\}]|[\)\]\}])\^\{1\}', r'\1', text)
            text = re.sub(r'(\\right[\)\]\}]|[\)\]\}])\^1(?!\d)', r'\1', text)
            return text

        # Hàm xử lý cục bộ cho TỪNG khối câu hỏi (VD: %<cau1>% ... %</cau1>%)
        def process_block(match):
            block_name = match.group(1) # Tên khối (VD: cau1)
            block_content = match.group(2) # Nội dung bên trong khối
            block_seed_bytes = hashlib.sha256(
                f'{seed_int}:{block_name}'.encode('utf-8')
            ).digest()[:8]
            block_seed = int.from_bytes(block_seed_bytes, 'big')
            block_rng = random.Random(block_seed)
            
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
                    current_index = block_seed % total_combinations
                    temp_idx = current_index
                    for var_name, values in declarations.items():
                        if values:
                            length = len(values)
                            chosen_vars[var_name] = values[temp_idx % length]
                            temp_idx = temp_idx // length
                            
# Xử lý Toán học cục bộ
            def eval_math(math_match):
                expr = math_match.group(1).strip()
                
                if expr in chosen_vars:
                    return str(chosen_vars[expr])
                
                expr = expr.replace('^', '**')
                words = re.findall(r'\b[a-zA-Z_]\w*\b', expr)
                
                # Đã bổ sung 'AutoMath' vào danh sách hàm hợp lệ
                math_funcs = ['sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'arcsin', 'arccos', 'arctan', 'exp', 'log', 'ln', 'simplify', 'diff', 'integrate', 'limit', 'lim', 'root', 'sqrt', 'oo', 'inf', 'Sum', 'Rational', 'Symbol', 'Eq', 'And', 'Piecewise', 'doit', 'removeO', 'series', 'subs', 'Subs', 'Derivative', 'pi', 'x', 'y', 'z', 'k', 'n', 'p', 'AutoMath']
                
                for word in words:
                    if word not in chosen_vars and word not in math_funcs:
                        return f"\\text{{[LỖI: Biến '{word}' chưa được khai báo trong khối {block_name}]}}"

                sorted_vars = sorted(chosen_vars.keys(), key=len, reverse=True)
                for var_name in sorted_vars:
                    val = str(chosen_vars[var_name])
                    expr = re.sub(rf'\b{var_name}\b', val, expr)
                
                # 1. HÀM AI SINH NHIỄU SỐ HỌC (AutoMath)
                def custom_automath(val):
                    pool = [sp.Integer(0), sp.Integer(1), sp.Integer(-1), sp.oo, -sp.oo]
                    if hasattr(val, 'is_number') and val.is_number and not val.has(sp.oo):
                        try:
                            pool.extend([val + 1, val - 1, -val])
                            if val != 0: pool.append(1/val)
                            if val != 0: pool.append(val * 2)
                        except: pass
                    
                    correct_tex = sp.latex(val)
                    pool_latex = list(set([sp.latex(p) for p in pool]))
                    if correct_tex in pool_latex:
                        pool_latex.remove(correct_tex)
                        
                    distractors = block_rng.sample(pool_latex, min(3, len(pool_latex)))
                    while len(distractors) < 3:
                        distractors.append("\\text{Khác}")
                        
                    # Trộn ngẫu nhiên A, B, C, D
                    options_html = [
                        f"\\Dung{{$ {correct_tex} $}}",
                        f"\\Sai{{$ {distractors[0]} $}}",
                        f"\\Sai{{$ {distractors[1]} $}}",
                        f"\\Sai{{$ {distractors[2]} $}}"
                    ]
                    block_rng.shuffle(options_html)
                    return "\n".join(options_html)

                try:
                    # Bổ sung AutoMath vào môi trường tính toán
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
                        'k': sp.Symbol('k'), 'n': sp.Symbol('n'), 'p': sp.Symbol('p'), 'pi': sp.pi,
                        'AutoMath': custom_automath
                    }
                    result = eval(expr, {"__builtins__": {'abs': abs, 'int': int, 'float': float}}, safe_dict)
                    
                    # Chặn lỗi hiển thị khi trả về chuỗi của AutoMath
                    if isinstance(result, str) and ('\\Dung' in result or '\\Sai' in result):
                        return result
                        
                    if hasattr(result, 'doit') and not isinstance(result, type):
                        result = result.doit()
                    
                    if isinstance(result, float) and result.is_integer():
                        result = int(result)
                    elif hasattr(result, 'is_Float') and getattr(result, 'is_Float') and int(result) == result:
                        result = int(result)
                        
                    return sp.latex(result)
                except Exception as e:
                    return f"\\text{{[LỖI Toán học tại {block_name}: {str(e)}]}}"

            # 2. Xử lý các thẻ Toán học @...@ trước
            content_after_math = re.sub(r'@(.*?)@', eval_math, clean_content)

            # 3. HÀM AI SINH NHIỄU MỆNH ĐỀ LOGIC (AutoText)
            def process_autotext(text_match):
                correct = text_match.group(1).strip()
                wrongs = [w.strip() for w in text_match.group(2).split(';') if w.strip()]
                
                distractors = block_rng.sample(wrongs, min(3, len(wrongs)))
                while len(distractors) < 3:
                    distractors.append("Sai")
                    
                # Trộn ngẫu nhiên A, B, C, D
                options_html = [
                    f"\\Dung{{{correct}}}",
                    f"\\Sai{{{distractors[0]}}}",
                    f"\\Sai{{{distractors[1]}}}",
                    f"\\Sai{{{distractors[2]}}}"
                ]
                block_rng.shuffle(options_html)
                return "\n".join(options_html)

            # Regex SIÊU CẤP hỗ trợ xuống dòng, khoảng trắng, ngoặc lồng nhau
            autotext_regex = re.compile(r'\\AutoText\s*\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}\s*\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}', re.IGNORECASE | re.DOTALL)
            
            # Quét và áp dụng AutoText
            final_block = autotext_regex.sub(process_autotext, content_after_math)
            final_block = normalize_latex_signs(final_block)

            return final_block

        # QUAN TRỌNG: Quét toàn bộ template gốc, tìm các khối %<tên>% ... %</tên>% và gọi hàm xử lý
        final_document = re.sub(r'%<([^>]+)>%(.*?)%</\1>%', process_block, template_text, flags=re.DOTALL)
        final_document = normalize_latex_signs(final_document)
        return final_document

    return generate_instance


def extract_latex_choices(text):
    choices = []
    command_pattern = re.compile(r'\\(Dung|Sai)\s*\{')
    for match in command_pattern.finditer(str(text or '')):
        command = match.group(1)
        start = match.end()
        depth = 1
        idx = start
        while idx < len(text) and depth > 0:
            char = text[idx]
            prev = text[idx - 1] if idx > 0 else ''
            if char == '{' and prev != '\\':
                depth += 1
            elif char == '}' and prev != '\\':
                depth -= 1
            idx += 1

        if depth == 0:
            choices.append({
                'type': command,
                'body': text[start:idx - 1].strip(),
                'start': match.start(),
                'end': idx,
            })
    return choices


def latex_choice_to_sympy(choice_text):
    """Parse a conservative subset of LaTeX used by generated answer choices."""
    value = str(choice_text or '').strip()
    value = re.sub(r'^\s*\$|\$\s*$', '', value).strip().rstrip('.。')
    value = value.replace('\\left', '').replace('\\right', '')
    value = value.replace('\\dfrac', '\\frac')
    value = re.sub(r'\\(?:,|;|!|quad\b|qquad\b)', '', value)

    unsupported = (
        '\\text', '\\mathrm', '\\mathbb', '\\begin', '\\end',
        '\\infty', '[', ']', '=', ',', ':', ';',
    )
    if not value or any(token in value for token in unsupported):
        return None

    simple_fraction = re.compile(r'\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}')
    simple_root = re.compile(r'\\sqrt\s*\{([^{}]+)\}')
    for _ in range(8):
        updated = simple_fraction.sub(r'((\1)/(\2))', value)
        updated = simple_root.sub(r'sqrt(\1)', updated)
        if updated == value:
            break
        value = updated

    command_replacements = {
        '\\arcsin': 'asin',
        '\\arccos': 'acos',
        '\\arctan': 'atan',
        '\\sinh': 'sinh',
        '\\cosh': 'cosh',
        '\\tanh': 'tanh',
        '\\sin': 'sin',
        '\\cos': 'cos',
        '\\tan': 'tan',
        '\\ln': 'log',
        '\\log': 'log',
        '\\exp': 'exp',
        '\\pi': 'pi',
    }
    for command, replacement in command_replacements.items():
        value = value.replace(command, replacement)

    value = re.sub(r'\^\s*\{([^{}]+)\}', r'^(\1)', value)
    value = value.replace('{', '(').replace('}', ')')
    value = re.sub(r'\s+', ' ', value).strip()

    if '\\' in value or not re.fullmatch(r'[0-9A-Za-z+\-*/^().\s]+', value):
        return None
    if '__' in value or re.search(r'(?<!\d)\.|\.(?!\d)', value):
        return None

    allowed_names = {
        'x', 'y', 'z', 't', 'k', 'n', 'p', 'pi',
        'sin', 'cos', 'tan', 'asin', 'acos', 'atan',
        'sinh', 'cosh', 'tanh', 'sqrt', 'log', 'exp',
    }
    identifiers = set(re.findall(r'[A-Za-z][A-Za-z0-9]*', value))
    if identifiers - allowed_names:
        return None

    symbols = {name: sp.Symbol(name) for name in ('x', 'y', 'z', 't', 'k', 'n', 'p')}
    local_dict = {
        **symbols,
        'pi': sp.pi,
        'sin': sp.sin,
        'cos': sp.cos,
        'tan': sp.tan,
        'asin': sp.asin,
        'acos': sp.acos,
        'atan': sp.atan,
        'sinh': sp.sinh,
        'cosh': sp.cosh,
        'tanh': sp.tanh,
        'sqrt': sp.sqrt,
        'log': sp.log,
        'exp': sp.exp,
    }
    global_dict = {
        'Integer': sp.Integer,
        'Float': sp.Float,
        'Rational': sp.Rational,
        'Symbol': sp.Symbol,
        'Function': sp.Function,
    }
    transformations = standard_transformations + (
        convert_xor,
        implicit_multiplication_application,
    )
    try:
        expression = parse_expr(
            value,
            local_dict=local_dict,
            global_dict=global_dict,
            transformations=transformations,
            evaluate=True,
        )
    except Exception:
        return None

    if not isinstance(expression, sp.Basic) or expression.has(sp.zoo, sp.nan):
        return None
    return expression


def find_equivalent_choice_pairs(choices):
    parsed = []
    for index, choice in enumerate(choices, start=1):
        expression = latex_choice_to_sympy(choice.get('body'))
        if expression is not None:
            parsed.append((index, choice, expression))

    pairs = []
    for left_index in range(len(parsed)):
        first_number, first_choice, first_expr = parsed[left_index]
        for right_index in range(left_index + 1, len(parsed)):
            second_number, second_choice, second_expr = parsed[right_index]
            try:
                equivalent = sp.simplify(first_expr - second_expr) == 0
            except Exception:
                equivalent = False
            if equivalent:
                pairs.append({
                    'first': first_number,
                    'second': second_number,
                    'includes_correct': (
                        first_choice.get('type') == 'Dung'
                        or second_choice.get('type') == 'Dung'
                    ),
                })
    return pairs

def review_question_content(question_content):
    issues = []

    def add_issue(kind, severity, message, suggestion):
        issues.append({
            'type': kind,
            'severity': severity,
            'message': message,
            'suggestion': suggestion
        })

    def extract_choices(text):
        choices = []
        command_pattern = re.compile(r'\\(Dung|Sai)\s*\{')
        for match in command_pattern.finditer(text):
            command = match.group(1)
            start = match.end()
            depth = 1
            idx = start
            while idx < len(text) and depth > 0:
                char = text[idx]
                prev = text[idx - 1] if idx > 0 else ''
                if char == '{' and prev != '\\':
                    depth += 1
                elif char == '}' and prev != '\\':
                    depth -= 1
                idx += 1

            if depth == 0:
                choices.append({
                    'type': command,
                    'body': text[start:idx - 1].strip(),
                    'start': match.start(),
                    'end': idx
                })
        return choices

    def remove_spans(text, spans):
        for start, end in sorted(spans, reverse=True):
            text = text[:start] + text[end:]
        return text

    def normalize_choice_text(text):
        text = re.sub(r'(?<!\\)\$', '', text)
        text = text.replace('\\left', '').replace('\\right', '')
        text = re.sub(r'\\,|\\;|\\!|\\quad|\\qquad', '', text)
        text = re.sub(r'\s+', '', text)
        text = text.rstrip('.。')
        return text

    def contains_any(text, patterns):
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

    def latex_number_to_float(text):
        value = str(text or '').strip()
        value = value.replace('$', '').replace('\\left', '').replace('\\right', '')
        value = value.replace('\\,', '').replace('\\;', '').replace(' ', '')
        value = value.rstrip('.。')
        value = value.replace('\\dfrac', '\\frac')

        if any(token in value for token in ['\\infty', '\\pi']):
            return None

        frac_match = re.fullmatch(r'([+-]?)\\frac\{([+-]?\d+)\}\{([+-]?\d+)\}', value)
        if frac_match:
            sign, numerator, denominator = frac_match.groups()
            denominator = int(denominator)
            if denominator == 0:
                return None
            number = int(numerator) / denominator
            return -number if sign == '-' else number

        try:
            return float(value)
        except ValueError:
            return None

    choices = extract_choices(question_content)
    correct_choices = [choice for choice in choices if choice['type'] == 'Dung']
    wrong_choices = [choice for choice in choices if choice['type'] == 'Sai']
    correct_body = correct_choices[0]['body'] if len(correct_choices) == 1 else ''
    question_lower = question_content.lower()

    if '[LỖI' in question_content:
        add_issue(
            'math',
            'high',
            'Câu hỏi còn chứa thông báo lỗi sinh biểu thức.',
            'Tìm đoạn bắt đầu bằng [LỖI...] trong preview và sửa biến hoặc biểu thức @...@ tương ứng.'
        )

    leftover_vars = re.findall(r'@[^@\n]{1,120}@', question_content)
    if leftover_vars:
        add_issue(
            'template',
            'high',
            'Câu hỏi còn biến template chưa được xử lý.',
            'Kiểm tra khai báo trong %%variables%% hoặc chính tả biến: ' + ', '.join(sorted(set(leftover_vars))[:5])
        )

    if '\\AutoText' in question_content:
        add_issue(
            'template',
            'medium',
            'Lệnh \\AutoText chưa được bung thành đáp án.',
            'Kiểm tra cú pháp \\AutoText{đáp án đúng}{đáp án sai 1; đáp án sai 2; ...}.'
        )

    if '\\AutoMath' in question_content:
        add_issue(
            'template',
            'medium',
            'Lệnh AutoMath chưa được tính toán.',
            'Đặt AutoMath trong cặp @...@, ví dụ @AutoMath(Rational(1,2))@.'
        )

    correct_count = len(correct_choices)
    wrong_count = len(wrong_choices)
    if correct_count == 0:
        add_issue(
            'answer',
            'high',
            'Câu hỏi chưa có đáp án đúng \\Dung{...}.',
            'Mỗi câu trắc nghiệm nên có đúng một đáp án \\Dung{...}.'
        )
    elif correct_count > 1:
        add_issue(
            'answer',
            'high',
            f'Câu hỏi có {correct_count} đáp án đúng.',
            'Chỉ giữ một đáp án đúng hoặc tách thành nhiều câu riêng.'
        )

    if wrong_count < 3:
        add_issue(
            'answer',
            'medium',
            f'Câu hỏi mới có {wrong_count} đáp án sai.',
            'Nên có ít nhất 3 đáp án \\Sai{...} để đủ 4 lựa chọn.'
        )

    if choices:
        seen_choices = {}
        for idx, choice in enumerate(choices, start=1):
            normalized = normalize_choice_text(choice['body'])
            if not normalized:
                continue
            if normalized in seen_choices:
                add_issue(
                    'answer',
                    'medium',
                    f'Đáp án lựa chọn {seen_choices[normalized]} và {idx} đang trùng nhau.',
                    'Thay một đáp án nhiễu khác để tránh hai phương án giống nhau sau khi sinh đề.'
                )
            else:
                seen_choices[normalized] = idx

        for pair in find_equivalent_choice_pairs(choices):
            severity = 'high' if pair['includes_correct'] else 'medium'
            add_issue(
                'semantic',
                severity,
                f"Đáp án lựa chọn {pair['first']} và {pair['second']} tương đương về mặt toán học.",
                'Thay một phương án nhiễu khác; các biểu thức khác cách viết nhưng cùng giá trị vẫn được xem là trùng đáp án.'
            )

    display_math_blocks = re.findall(r'\\\[([\s\S]*?)\\\]', question_content)
    if any(re.search(r'(?<!\\)\$', block) for block in display_math_blocks):
        add_issue(
            'latex',
            'high',
            'Có dấu $...$ nằm bên trong công thức hiển thị \\[...\\].',
            'Không lồng math mode. Nếu biến raw đã có $...$, hãy đặt nó trong văn bản thường thay vì \\[...\\].'
        )

    display_open = len(re.findall(r'\\\[', question_content))
    display_close = len(re.findall(r'\\\]', question_content))
    if display_open != display_close:
        add_issue(
            'latex',
            'high',
            'Số lượng \\[ và \\] không khớp.',
            'Kiểm tra lại các khối công thức hiển thị trong đề hoặc lời giải.'
        )

    unescaped_dollars = re.findall(r'(?<!\\)\$', question_content)
    if len(unescaped_dollars) % 2 != 0:
        add_issue(
            'latex',
            'high',
            'Số lượng dấu $ không chẵn.',
            'Kiểm tra công thức inline, có thể đang thiếu một dấu $ mở hoặc đóng.'
        )

    for env_name in ['question', 'solution']:
        begin_count = len(re.findall(rf'\\begin\s*\{{{env_name}\}}', question_content))
        end_count = len(re.findall(rf'\\end\s*\{{{env_name}\}}', question_content))
        if begin_count != end_count:
            add_issue(
                'latex',
                'high',
                f'Môi trường {env_name} có số begin/end không khớp.',
                f'Kiểm tra \\begin{{{env_name}}} và \\end{{{env_name}}}.'
            )

    brace_balance = 0
    min_balance = 0
    prev = ''
    for char in question_content:
        if char == '{' and prev != '\\':
            brace_balance += 1
        elif char == '}' and prev != '\\':
            brace_balance -= 1
            min_balance = min(min_balance, brace_balance)
        prev = char
    if brace_balance != 0 or min_balance < 0:
        add_issue(
            'latex',
            'medium',
            'Số lượng ngoặc nhọn { } có dấu hiệu không cân bằng.',
            'Kiểm tra các lệnh như \\frac{...}{...}, \\Dung{...}, \\Sai{...}, \\AutoText{...}{...}.'
        )

    empty_fraction_pattern = r'\\(?:dfrac|frac)\s*\{\s*\}\s*\{[^{}]*\}|\\(?:dfrac|frac)\s*\{[^{}]*\}\s*\{\s*\}'
    if re.search(empty_fraction_pattern, question_content):
        add_issue(
            'latex',
            'high',
            'Có phân số bị rỗng tử hoặc mẫu.',
            'Kiểm tra các lệnh \\frac{...}{...} hoặc \\dfrac{...}{...}; không để một vế rỗng.'
        )

    interval_pattern = re.compile(
        r'(\\left\s*)?[\[\(]\s*([^,\[\]\(\)]{1,80}?)\s*,\s*([^,\[\]\(\)]{1,80}?)\s*(\\right\s*)?[\]\)]'
    )
    for choice_idx, choice in enumerate(choices, start=1):
        for interval_match in interval_pattern.finditer(choice['body']):
            left_value = latex_number_to_float(interval_match.group(2))
            right_value = latex_number_to_float(interval_match.group(3))
            if left_value is None or right_value is None:
                continue
            if left_value > right_value:
                add_issue(
                    'answer',
                    'high' if choice['type'] == 'Dung' else 'medium',
                    f'Đáp án lựa chọn {choice_idx} có khoảng bị đảo đầu mút.',
                    'Đổi về dạng [a,b] hoặc (a,b) với a < b; tránh dùng khoảng như [5,3] kể cả trong đáp án nhiễu.'
                )

    if re.search(r'(?<![A-Za-z])e\^\{?\s*0\s*\}?', question_content):
        add_issue(
            'format',
            'medium',
            'Có biểu thức e^0 trong câu hỏi hoặc đáp án.',
            'Nên rút gọn thành 1 để đáp án tự nhiên hơn và tránh bị nhận xét về trình bày.'
        )

    coefficient_one_pattern = r'(?<![A-Za-z0-9\\])-?1(?=(?:[xyzt]\b|\|[xyzt]\||\\(?:sin|cos|tan|arcsin|arccos|arctan|sinh|cosh|tanh|ln|log)\b))'
    exponent_one_pattern = r'(?:\\right[\)\]\}]|[\)\]\}]|\|[xyzt]\||\b[xyzt])\^\{?1\}?'
    if re.search(coefficient_one_pattern, question_content) or re.search(exponent_one_pattern, question_content):
        add_issue(
            'format',
            'medium',
            'Còn hệ số 1 hoặc số mũ 1 hiển thị trong biểu thức.',
            'Kiểm tra seed này trong preview. Nếu còn dạng 1x, 1\\sin x hoặc x^1, nên dùng biến tránh giá trị 1 hoặc bổ sung hàm rút gọn hiển thị.'
        )

    is_domain_question = 'tập xác định' in question_lower
    is_range_question = 'tập giá trị' in question_lower
    looks_like_range_answer = bool(re.search(r'(?<![A-Za-z])T\s*=', correct_body))

    if is_domain_question and contains_any(question_content, [r'\\arcsin', r'\\arccos']):
        if not re.search(r'-\s*1\s*\\le[\s\S]*\\le\s*1', question_content):
            add_issue(
                'rubric',
                'medium',
                'Dạng arcsin/arccos nên nêu điều kiện -1 \\le u \\le 1.',
                'Bổ sung điều kiện xác định của hàm arcsin/arccos trong lời giải.'
            )

        if correct_body and not re.search(r'\\left\s*\[|\[', correct_body):
            add_issue(
                'rubric',
                'medium',
                'Đáp án tập xác định của arcsin/arccos thường là đoạn đóng.',
                'Kiểm tra lại đáp án đúng, nên dùng dạng [a,b] hoặc \\left[a,b\\right] khi giải bất đẳng thức -1 \\le u \\le 1.'
            )

    if (is_range_question or looks_like_range_answer) and '\\arctan' in question_content:
        if correct_body and re.search(r'\\left\s*\[|\[', correct_body):
            add_issue(
                'rubric',
                'high',
                'Tập giá trị của arctan là khoảng mở, không phải đoạn đóng.',
                'Đổi đáp án đúng sang dạng \\left(-\\frac{\\pi}{2},\\frac{\\pi}{2}\\right), có nhân hệ số nếu cần.'
            )
        if correct_body and '\\pi' not in correct_body:
            add_issue(
                'rubric',
                'medium',
                'Đáp án tập giá trị của arctan thường phải có \\pi.',
                'Kiểm tra lại khoảng giá trị, vì arctan cơ bản nhận giá trị trong (-\\pi/2, \\pi/2).'
            )

    if 'hàm số ngược' in question_lower and correct_body and 'f^{-1}' not in correct_body:
        add_issue(
            'rubric',
            'medium',
            'Đáp án đúng của dạng hàm ngược chưa thấy ký hiệu f^{-1}.',
            'Nếu câu hỏi yêu cầu tìm hàm ngược, đáp án đúng nên ghi rõ f^{-1}(x)=... để tránh mơ hồ.'
        )

    if 'hàm chẵn' in question_lower and correct_body:
        even_markers = [r'x\^2', r'x\^4', r'\\cos\b', r'\\cosh\b']
        odd_markers = [r'\\sin\b', r'\\sinh\b', r'\\arcsin\b', r'\\arctan\b']
        has_even_marker = contains_any(correct_body, even_markers)
        has_odd_marker = contains_any(correct_body, odd_markers)
        if not has_even_marker or has_odd_marker:
            add_issue(
                'rubric',
                'medium',
                'Đáp án đúng cho câu hỏi hàm chẵn chưa khớp rõ với ngân hàng hàm chẵn.',
                'Kiểm tra lại tập hàm chẵn: x^2, x^4, cos x, cosh x hoặc tổng các hàm chẵn.'
            )

    if not issues:
        return {
            'status': 'ok',
            'summary': 'Chưa phát hiện lỗi rõ ràng trong câu hỏi này.',
            'issues': [],
            'mode': 'rule'
        }

    has_high = any(issue['severity'] == 'high' for issue in issues)
    return {
        'status': 'error' if has_high else 'warning',
        'summary': f'Phát hiện {len(issues)} vấn đề cần xem lại.',
        'issues': issues,
        'mode': 'rule'
    }


def extract_balanced_latex_commands(text, command_names):
    names = '|'.join(re.escape(name) for name in command_names)
    command_pattern = re.compile(rf'\\({names})\s*\{{')
    items = []

    for match in command_pattern.finditer(text):
        command = match.group(1)
        start = match.end()
        depth = 1
        idx = start
        while idx < len(text) and depth > 0:
            char = text[idx]
            prev = text[idx - 1] if idx > 0 else ''
            if char == '{' and prev != '\\':
                depth += 1
            elif char == '}' and prev != '\\':
                depth -= 1
            idx += 1

        if depth == 0:
            items.append({
                'command': command,
                'body': text[start:idx - 1].strip(),
                'start': match.start(),
                'end': idx
            })

    return items


def remove_latex_spans(text, spans):
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text


def clean_online_latex_fragment(text):
    text = str(text or '').strip()
    text = re.sub(r'\\begin\{solution\}[\s\S]*?\\end\{solution\}', '', text)
    text = re.sub(r'\\DatCot\[\d+\]', '', text)
    text = re.sub(r'\\par\s*\\vspace\*?\{[^{}]*\}', '\n', text)
    text = re.sub(r'\\vspace\*?\{[^{}]*\}', '\n', text)
    text = re.sub(r'\\(?:smallskip|medskip|bigskip)\b', '\n', text)
    text = re.sub(r'\\par\b', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_assignment_questions(tex_content):
    questions = []
    answer_key = []
    question_blocks = re.findall(r'\\begin\{question\}([\s\S]*?)\\end\{question\}', str(tex_content or ''))

    if not question_blocks:
        return [], []

    for q_index, block in enumerate(question_blocks, start=1):
        block_without_solution = re.sub(r'\\begin\{solution\}[\s\S]*?\\end\{solution\}', '', block)
        choices = extract_balanced_latex_commands(block_without_solution, ['Dung', 'Sai'])
        if not choices:
            continue

        prompt = remove_latex_spans(block_without_solution, [(item['start'], item['end']) for item in choices])
        prompt = clean_online_latex_fragment(prompt)

        online_choices = []
        correct_choice = None
        for choice_idx, choice in enumerate(choices):
            letter = chr(65 + choice_idx)
            online_choices.append({
                'letter': letter,
                'content': clean_online_latex_fragment(choice['body'])
            })
            if choice['command'] == 'Dung':
                correct_choice = letter

        if prompt and len(online_choices) >= 2 and correct_choice:
            questions.append({
                'number': q_index,
                'prompt': prompt,
                'choices': online_choices
            })
            answer_key.append(correct_choice)

    return questions, answer_key


def run_custom_template_stress_test(template_text, seed_count):
    seed_count = max(1, min(int(seed_count), 100))
    generator = parse_advanced_template(template_text)
    expected_question_count = None
    output_hashes = set()
    answer_positions = {letter: 0 for letter in 'ABCD'}
    records = []
    pdf_samples = {}
    deterministic_seeds = {1, max(1, (seed_count + 1) // 2), seed_count}

    def add_record_issue(record, issue_type, severity, message, suggestion, question_index=None):
        record['issues'].append({
            'type': issue_type,
            'severity': severity,
            'message': message,
            'suggestion': suggestion,
            'question_index': question_index,
        })

    first_problem_seed = None
    for seed in range(1, seed_count + 1):
        record = {'seed': seed, 'issues': [], 'question_count': 0}
        generated = ''
        try:
            generated = generator(seed)
        except Exception as exc:
            add_record_issue(
                record,
                'generation',
                'high',
                f'Seed {seed} không sinh được đề: {str(exc)[:240]}',
                'Mở seed này và kiểm tra khai báo biến cùng các biểu thức @...@.',
            )
            records.append(record)
            if first_problem_seed is None:
                first_problem_seed = seed
            continue

        output_hashes.add(hashlib.sha256(generated.encode('utf-8')).hexdigest())
        if seed in {1, seed_count}:
            pdf_samples[seed] = generated

        if seed in deterministic_seeds:
            try:
                if generator(seed) != generated:
                    add_record_issue(
                        record,
                        'seed',
                        'high',
                        f'Seed {seed} cho kết quả không ổn định khi sinh lại.',
                        'Không dùng nguồn ngẫu nhiên toàn cục trong template; mọi lựa chọn phải phụ thuộc vào seed.',
                    )
            except Exception as exc:
                add_record_issue(
                    record,
                    'seed',
                    'high',
                    f'Seed {seed} lỗi khi kiểm tra tính ổn định: {str(exc)[:200]}',
                    'Kiểm tra biểu thức hoặc biến có trạng thái thay đổi giữa các lần chạy.',
                )

        question_blocks = re.findall(
            r'\\begin\s*\{question\}[\s\S]*?\\end\s*\{question\}',
            generated,
        )
        record['question_count'] = len(question_blocks)
        if expected_question_count is None:
            expected_question_count = len(question_blocks)

        if not question_blocks:
            add_record_issue(
                record,
                'structure',
                'high',
                f'Seed {seed} không có môi trường question hợp lệ.',
                'Kiểm tra cặp \\begin{question} và \\end{question} trong template.',
            )
        elif len(question_blocks) != expected_question_count:
            add_record_issue(
                record,
                'structure',
                'high',
                f'Seed {seed} có {len(question_blocks)} câu, khác mức chuẩn {expected_question_count} câu.',
                'Không để điều kiện theo biến làm mất hoặc tạo thêm môi trường question.',
            )

        parsed_questions, answer_key = parse_assignment_questions(generated)
        if len(parsed_questions) != len(question_blocks) or len(answer_key) != len(question_blocks):
            add_record_issue(
                record,
                'answer',
                'high',
                f'Seed {seed} không tách đủ câu hỏi hoặc đáp án đúng.',
                'Mỗi câu cần đúng một \\Dung{...} và các lựa chọn phải nằm trong môi trường question.',
            )
        for letter in answer_key:
            if letter in answer_positions:
                answer_positions[letter] += 1

        for question_index, block in enumerate(question_blocks, start=1):
            review = review_question_content(block)
            for issue in review.get('issues', []):
                add_record_issue(
                    record,
                    issue.get('type', 'quality'),
                    issue.get('severity', 'medium'),
                    issue.get('message', 'Câu hỏi cần được kiểm tra lại.'),
                    issue.get('suggestion', 'Mở seed này để kiểm tra.'),
                    question_index=question_index,
                )

        if record['issues'] and first_problem_seed is None:
            first_problem_seed = seed
            pdf_samples[seed] = generated
        records.append(record)

    if first_problem_seed is not None and first_problem_seed not in pdf_samples:
        problem_record = next((item for item in records if item['seed'] == first_problem_seed), None)
        if problem_record:
            try:
                pdf_samples[first_problem_seed] = generator(first_problem_seed)
            except Exception:
                pass

    pdf_checked = 0
    if app.config.get('PDF_QUALITY_GATE', True):
        for seed, generated in list(pdf_samples.items())[:3]:
            if '\\documentclass' not in generated or '\\begin{document}' not in generated:
                continue
            pdf_checked += 1
            _pdf_bytes, compile_error = compile_tex_pdf_bytes(generated)
            if compile_error:
                record = next(item for item in records if item['seed'] == seed)
                add_record_issue(
                    record,
                    'pdf',
                    'high',
                    f'Seed {seed} không biên dịch được PDF: {compile_error[:260]}',
                    'Mở seed này, sửa lỗi LaTeX được nêu rồi kiểm thử lại mẫu.',
                )

    global_issues = []
    if '%%variables%%' in template_text and len(output_hashes) <= 1 and seed_count >= 10:
        global_issues.append({
            'type': 'coverage',
            'severity': 'medium',
            'message': f'{seed_count} seed chỉ tạo ra một biến thể nội dung.',
            'suggestion': 'Kiểm tra lại tập giá trị biến hoặc cách sử dụng biến trong nội dung câu hỏi.',
        })

    answer_total = sum(answer_positions.values())
    if answer_total >= 12:
        dominant_letter, dominant_count = max(answer_positions.items(), key=lambda item: item[1])
        if dominant_count / answer_total > 0.75:
            global_issues.append({
                'type': 'distribution',
                'severity': 'medium',
                'message': f'{dominant_letter} là vị trí đáp án đúng trong {dominant_count}/{answer_total} câu đã thử.',
                'suggestion': 'Bổ sung trộn lựa chọn hoặc kiểm tra lại cách dùng AutoText/AutoMath.',
            })

    totals = {'ok': 0, 'warning': 0, 'error': 0}
    total_issues = len(global_issues)
    problem_records = []
    for record in records:
        total_issues += len(record['issues'])
        has_high = any(issue['severity'] == 'high' for issue in record['issues'])
        status = 'error' if has_high else ('warning' if record['issues'] else 'ok')
        record['status'] = status
        totals[status] += 1
        if status != 'ok':
            problem_records.append({
                **record,
                'issues': record['issues'][:8],
                'hidden_issue_count': max(0, len(record['issues']) - 8),
            })

    global_has_high = any(issue['severity'] == 'high' for issue in global_issues)
    overall_status = 'error' if totals['error'] or global_has_high else (
        'warning' if totals['warning'] or global_issues else 'ok'
    )
    summary = (
        f"Đã kiểm thử {seed_count} seed: {totals['ok']} đạt, "
        f"{totals['warning']} cảnh báo, {totals['error']} lỗi."
    )
    return {
        'status': overall_status,
        'summary': summary,
        'totals': {
            **totals,
            'seeds_checked': seed_count,
            'questions_checked': sum(record['question_count'] for record in records),
            'issues': total_issues,
        },
        'problem_seeds': problem_records[:30],
        'hidden_problem_seed_count': max(0, len(problem_records) - 30),
        'global_issues': global_issues,
        'coverage': {
            'unique_variants': len(output_hashes),
            'answer_positions': answer_positions,
        },
        'pdf_checked': pdf_checked,
        'mode': 'rule-symbolic',
    }


def get_assignment_max_attempts(assignment):
    if assignment.max_attempts is None:
        return None
    try:
        value = int(assignment.max_attempts)
    except (TypeError, ValueError):
        return 1
    return value if value > 0 else None


def is_assignment_result_published(assignment, now=None):
    now = now or app_now()
    policy = assignment.result_release_policy or 'immediate'
    if policy == 'after_due':
        return bool(assignment.due_at and now >= assignment.due_at)
    if policy == 'manual':
        return bool(assignment.results_published)
    return True


def get_assignment_attempt_state(assignment, student_id, now=None):
    now = now or app_now()
    attempts = (
        AssignmentAttempt.query
        .filter_by(assignment_id=assignment.id, student_id=student_id)
        .order_by(AssignmentAttempt.attempt_number.asc(), AssignmentAttempt.id.asc())
        .all()
    )
    active_attempt = next((item for item in reversed(attempts) if not item.is_completed), None)
    completed_attempts = [item for item in attempts if item.is_completed]
    max_attempts = get_assignment_max_attempts(assignment)
    attempts_used = len(completed_attempts)
    attempts_remaining = None if max_attempts is None else max(max_attempts - attempts_used, 0)
    attempts_exhausted = max_attempts is not None and attempts_used >= max_attempts

    retry_available_at = None
    cooldown_seconds = 0
    cooldown_minutes = max(int(assignment.retry_cooldown_minutes or 0), 0)
    if completed_attempts and cooldown_minutes:
        completed_at = completed_attempts[-1].completed_at
        if completed_at:
            retry_available_at = completed_at + timedelta(minutes=cooldown_minutes)
            cooldown_seconds = max(int((retry_available_at - now).total_seconds()), 0)

    can_create_attempt = (
        not active_attempt
        and not attempts_exhausted
        and cooldown_seconds == 0
    )
    return {
        'attempts': attempts,
        'active_attempt': active_attempt,
        'completed_attempts': completed_attempts,
        'attempts_used': attempts_used,
        'attempts_remaining': attempts_remaining,
        'attempts_exhausted': attempts_exhausted,
        'retry_available_at': retry_available_at,
        'cooldown_seconds': cooldown_seconds,
        'can_create_attempt': can_create_attempt
    }


def serialize_student_submission(assignment, submission, attempt=None):
    result_published = is_assignment_result_published(assignment)
    answers_visible = result_published and bool(assignment.show_answers)
    return {
        'submitted': bool(submission),
        'answers': json.loads((attempt.answers_json if attempt else submission.answers_json) or '{}')
        if submission else {},
        'score': submission.score if submission and result_published else None,
        'correct_count': submission.correct_count if submission and result_published else None,
        'submitted_at': submission.submitted_at.strftime('%Y-%m-%d %H:%M')
        if submission and submission.submitted_at else '',
        'result_published': result_published,
        'answers_visible': answers_visible,
        'answer_key': json.loads(attempt.answer_key_json or '[]')
        if attempt and answers_visible else []
    }


def build_submission_response(assignment, attempt, submission, message, timed_out=False):
    result_published = is_assignment_result_published(assignment)
    return {
        'success': True,
        'timed_out': timed_out,
        'message': message,
        'result_published': result_published,
        'score': attempt.score if result_published else None,
        'recorded_score': submission.score if submission and result_published else None,
        'correct_count': attempt.correct_count if result_published else None,
        'total_questions': attempt.total_questions
    }


def serialize_assignment_summary(assignment, include_class=False, student_id=None):
    submission = None
    attempt_state = None
    if student_id:
        submission = Submission.query.filter_by(assignment_id=assignment.id, student_id=student_id).first()
        attempt_state = get_assignment_attempt_state(assignment, student_id)

    now = app_now()
    active_attempt = attempt_state['active_attempt'] if attempt_state else None
    student_attempts = attempt_state['completed_attempts'] if attempt_state else []
    result_published = is_assignment_result_published(assignment, now)
    if not assignment.is_active:
        availability = 'closed'
    elif assignment.opens_at and now < assignment.opens_at:
        availability = 'scheduled'
    elif assignment.due_at and now > assignment.due_at:
        availability = 'expired'
    elif active_attempt:
        availability = 'in_progress'
    elif submission and attempt_state and attempt_state['attempts_exhausted']:
        availability = 'attempts_exhausted'
    elif submission and attempt_state and attempt_state['cooldown_seconds'] > 0:
        availability = 'retry_cooldown'
    elif submission and attempt_state and attempt_state['can_create_attempt']:
        availability = 'retry_available'
    elif submission:
        availability = 'submitted'
    else:
        availability = 'available'

    max_attempts = get_assignment_max_attempts(assignment)
    data = {
        'id': assignment.id,
        'title': assignment.title,
        'description': assignment.description or '',
        'class_id': assignment.class_id,
        'total_questions': assignment.total_questions,
        'opens_at': assignment.opens_at.strftime('%Y-%m-%d %H:%M') if assignment.opens_at else '',
        'due_at': assignment.due_at.strftime('%Y-%m-%d %H:%M') if assignment.due_at else '',
        'duration_minutes': assignment.duration_minutes,
        'allow_retry': max_attempts is None or max_attempts > 1,
        'max_attempts': max_attempts,
        'retry_cooldown_minutes': max(int(assignment.retry_cooldown_minutes or 0), 0),
        'score_policy': assignment.score_policy or 'latest',
        'result_release_policy': assignment.result_release_policy or 'immediate',
        'results_published': bool(assignment.results_published),
        'result_published': result_published,
        'show_answers': bool(assignment.show_answers),
        'answers_visible': result_published and bool(assignment.show_answers),
        'shuffle_questions': bool(assignment.shuffle_questions),
        'shuffle_answers': bool(assignment.shuffle_answers),
        'version_hash': assignment.version_hash or '',
        'created_at': assignment.created_at.strftime('%Y-%m-%d %H:%M') if assignment.created_at else '',
        'is_active': assignment.is_active,
        'availability': availability,
        'can_start': availability in {'available', 'retry_available', 'in_progress'},
        'has_active_attempt': bool(active_attempt),
        'submission_count': len(assignment.submissions),
        'submitted': bool(submission),
        'score': submission.score if submission and result_published else None,
        'correct_count': submission.correct_count if submission and result_published else None,
        'attempt_count': len(student_attempts) if student_id else None,
        'attempts_remaining': attempt_state['attempts_remaining'] if attempt_state else None,
        'retry_cooldown_seconds': attempt_state['cooldown_seconds'] if attempt_state else 0,
        'retry_available_at': (
            attempt_state['retry_available_at'].strftime('%Y-%m-%d %H:%M')
            if attempt_state and attempt_state['retry_available_at'] else ''
        ),
        'attempt_history': [{
            'attempt_number': item.attempt_number,
            'score': item.score if result_published else None,
            'correct_count': item.correct_count if result_published else None,
            'total_questions': item.total_questions or assignment.total_questions,
            'completed_at': item.completed_at.strftime('%Y-%m-%d %H:%M') if item.completed_at else '',
            'completion_reason': item.completion_reason or 'submitted',
            'is_selected': bool(submission and submission.selected_attempt_id == item.id)
        } for item in student_attempts] if student_id else []
    }

    if include_class:
        data['class_name'] = assignment.classroom.name if assignment.classroom else ''
        data['class_code'] = assignment.classroom.code if assignment.classroom else ''

    return data


def build_teacher_class_detail(target_class):
    students = sorted(
        target_class.students,
        key=lambda item: ((item.student_code or '').lower(), item.display_name.lower())
    )
    student_ids = {student.id for student in students}
    assignments = (
        Assignment.query
        .filter_by(class_id=target_class.id, teacher_id=target_class.teacher_id)
        .order_by(Assignment.created_at.desc(), Assignment.id.desc())
        .all()
    )
    pending_requests = (
        ClassJoinRequest.query
        .filter_by(class_id=target_class.id, status='pending')
        .order_by(ClassJoinRequest.requested_at.asc(), ClassJoinRequest.id.asc())
        .all()
    )

    assignment_rows = []
    submissions_by_assignment = {}
    all_recorded_scores = []
    total_submissions = 0
    now = app_now()

    for assignment in assignments:
        submission_map = {
            submission.student_id: submission
            for submission in assignment.submissions
            if submission.student_id in student_ids
        }
        submissions_by_assignment[assignment.id] = submission_map
        scores = [submission.score or 0 for submission in submission_map.values()]
        total_submissions += len(submission_map)
        all_recorded_scores.extend(scores)

        if not assignment.is_active:
            status = 'paused'
            status_label = 'Tạm dừng'
            status_class = 'secondary'
        elif assignment.opens_at and now < assignment.opens_at:
            status = 'scheduled'
            status_label = 'Sắp mở'
            status_class = 'info'
        elif assignment.due_at and now > assignment.due_at:
            status = 'closed'
            status_label = 'Đã đóng'
            status_class = 'dark'
        else:
            status = 'open'
            status_label = 'Đang mở'
            status_class = 'success'

        submitted_count = len(submission_map)
        total_students = len(students)
        assignment_rows.append({
            'assignment': assignment,
            'summary': serialize_assignment_summary(assignment),
            'status': status,
            'status_label': status_label,
            'status_class': status_class,
            'submitted_count': submitted_count,
            'missing_count': max(total_students - submitted_count, 0),
            'completion_rate': round((submitted_count / total_students) * 100, 1)
            if total_students else 0,
            'average_score': round(sum(scores) / len(scores), 2) if scores else None,
            'highest_score': max(scores) if scores else None
        })

    gradebook_rows = []
    for student in students:
        cells = []
        student_scores = []
        for row in assignment_rows:
            submission = submissions_by_assignment[row['assignment'].id].get(student.id)
            if submission:
                student_scores.append(submission.score or 0)
            cells.append({
                'submitted': bool(submission),
                'score': submission.score if submission else None,
                'submitted_at': (
                    submission.submitted_at.strftime('%d/%m/%Y %H:%M')
                    if submission and submission.submitted_at else ''
                )
            })
        gradebook_rows.append({
            'student': student,
            'cells': cells,
            'submitted_count': len(student_scores),
            'average_score': round(sum(student_scores) / len(student_scores), 2)
            if student_scores else None
        })

    total_expected_submissions = len(students) * len(assignments)
    return {
        'students': students,
        'pending_requests': pending_requests,
        'assignment_rows': assignment_rows,
        'gradebook_rows': gradebook_rows,
        'summary': {
            'student_count': len(students),
            'pending_count': len(pending_requests),
            'assignment_count': len(assignments),
            'open_assignment_count': sum(
                1 for row in assignment_rows if row['status'] == 'open'
            ),
            'total_submissions': total_submissions,
            'completion_rate': round(
                (total_submissions / total_expected_submissions) * 100, 1
            ) if total_expected_submissions else 0,
            'average_score': round(
                sum(all_recorded_scores) / len(all_recorded_scores), 2
            ) if all_recorded_scores else None
        }
    }


def get_calendar_assignments(user):
    if user.role == 'teacher':
        return (
            Assignment.query
            .filter_by(teacher_id=user.id)
            .order_by(Assignment.opens_at.asc(), Assignment.due_at.asc(), Assignment.id.asc())
            .all()
        )

    class_ids = [item.id for item in user.enrolled_classes]
    if not class_ids:
        return []
    return (
        Assignment.query
        .filter(Assignment.class_id.in_(class_ids))
        .order_by(Assignment.opens_at.asc(), Assignment.due_at.asc(), Assignment.id.asc())
        .all()
    )


def serialize_calendar_assignment(assignment, user):
    now = app_now()
    if not assignment.is_active:
        status = 'paused'
        status_label = 'Tạm dừng'
    elif assignment.opens_at and now < assignment.opens_at:
        status = 'scheduled'
        status_label = 'Sắp mở'
    elif assignment.due_at and now > assignment.due_at:
        status = 'expired'
        status_label = 'Đã quá hạn'
    else:
        status = 'open'
        status_label = 'Đang mở'

    submitted = False
    score = None
    if user.role == 'student':
        submission = Submission.query.filter_by(
            assignment_id=assignment.id,
            student_id=user.id
        ).first()
        submitted = bool(submission)
        if submission and is_assignment_result_published(assignment):
            score = submission.score
        if submitted:
            status = 'submitted'
            status_label = 'Đã nộp'

    class_name = assignment.classroom.name if assignment.classroom else ''
    class_code = assignment.classroom.code if assignment.classroom else ''
    detail_link = (
        f'/teacher/classes/{assignment.class_id}#classAssignments'
        if user.role == 'teacher'
        else '/student-dashboard#tab-practice'
    )
    return {
        'id': assignment.id,
        'title': assignment.title,
        'description': assignment.description or '',
        'class_id': assignment.class_id,
        'class_name': class_name,
        'class_code': class_code,
        'opens_at': assignment.opens_at.strftime('%Y-%m-%dT%H:%M')
        if assignment.opens_at else '',
        'due_at': assignment.due_at.strftime('%Y-%m-%dT%H:%M')
        if assignment.due_at else '',
        'duration_minutes': assignment.duration_minutes,
        'status': status,
        'status_label': status_label,
        'submitted': submitted,
        'score': score,
        'is_active': bool(assignment.is_active),
        'result_release_policy': assignment.result_release_policy or 'immediate',
        'result_published': is_assignment_result_published(assignment),
        'detail_link': detail_link
    }


def parse_due_at(raw_value):
    raw_value = str(raw_value or '').strip()
    if not raw_value:
        return None

    for fmt in ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M']:
        try:
            return datetime.strptime(raw_value, fmt)
        except ValueError:
            continue
    return None


def parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def normalize_username(value):
    return str(value or '').strip().lower()


def normalize_full_name(value):
    return re.sub(r'\s+', ' ', str(value or '').strip())


def normalize_student_code(value):
    return re.sub(r'\s+', '', str(value or '').strip()).upper()


def validate_username(username):
    if not 3 <= len(username) <= 50:
        return 'Tên đăng nhập phải có từ 3 đến 50 ký tự.'
    if not re.fullmatch(r'[a-z0-9._-]+', username):
        return 'Tên đăng nhập chỉ được gồm chữ thường không dấu, số, dấu chấm, gạch dưới hoặc gạch ngang.'
    return None


def validate_full_name(full_name):
    if not 2 <= len(full_name) <= 150:
        return 'Họ và tên phải có từ 2 đến 150 ký tự.'
    if any(ord(character) < 32 for character in full_name):
        return 'Họ và tên chứa ký tự không hợp lệ.'
    if not any(character.isalpha() for character in full_name):
        return 'Họ và tên phải có ít nhất một chữ cái.'
    return None


def validate_password(password):
    if not 8 <= len(password) <= 128:
        return 'Mật khẩu phải có từ 8 đến 128 ký tự.'
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        return 'Mật khẩu phải có ít nhất một chữ cái và một chữ số.'
    return None


def validate_student_profile(student_code, school_name, major):
    if not re.fullmatch(r'[A-Z0-9-]{5,30}', student_code):
        return 'Mã số sinh viên phải có từ 5 đến 30 ký tự, chỉ gồm chữ, số hoặc dấu gạch ngang.'
    if not 2 <= len(school_name) <= 200:
        return 'Tên trường phải có từ 2 đến 200 ký tự.'
    if not 2 <= len(major) <= 150:
        return 'Tên ngành phải có từ 2 đến 150 ký tự.'
    return None


def dashboard_url_for(user):
    if user.role == 'admin':
        return url_for('admin_dashboard')
    if user.role == 'teacher':
        return url_for('teacher_dashboard')
    return url_for('student_dashboard')


def safe_next_url(target):
    target = str(target or '').strip()
    return target if target.startswith('/') and not target.startswith('//') else ''


def parse_assignment_policy(data, current_assignment=None):
    raw_max_attempts = data.get('max_attempts')
    if raw_max_attempts is None and 'allow_retry' in data:
        raw_max_attempts = '' if parse_bool(data.get('allow_retry')) else '1'
    if raw_max_attempts is None and current_assignment is not None:
        max_attempts = get_assignment_max_attempts(current_assignment)
    else:
        raw_max_attempts = str(raw_max_attempts or '').strip()
        if not raw_max_attempts:
            max_attempts = None
        else:
            try:
                max_attempts = int(raw_max_attempts)
            except ValueError as exc:
                raise ValueError('Số lần làm tối đa phải là số nguyên.') from exc
            if max_attempts < 1:
                raise ValueError('Số lần làm tối đa phải từ 1 trở lên.')

    raw_cooldown = data.get('retry_cooldown_minutes')
    if raw_cooldown is None and current_assignment is not None:
        cooldown_minutes = max(int(current_assignment.retry_cooldown_minutes or 0), 0)
    else:
        try:
            cooldown_minutes = int(str(raw_cooldown or '0').strip())
        except ValueError as exc:
            raise ValueError('Thời gian chờ giữa các lượt phải là số nguyên.') from exc
        if cooldown_minutes < 0:
            raise ValueError('Thời gian chờ giữa các lượt không được âm.')

    result_release_policy = str(
        data.get('result_release_policy')
        or (current_assignment.result_release_policy if current_assignment else 'immediate')
        or 'immediate'
    ).strip().lower()
    if result_release_policy not in {'immediate', 'after_due', 'manual'}:
        raise ValueError('Chính sách công bố kết quả không hợp lệ.')

    return {
        'max_attempts': max_attempts,
        'retry_cooldown_minutes': cooldown_minutes,
        'allow_retry': max_attempts is None or max_attempts > 1,
        'result_release_policy': result_release_policy,
        'results_published': parse_bool(
            data.get('results_published'),
            default=bool(current_assignment.results_published) if current_assignment else False
        ),
        'show_answers': parse_bool(
            data.get('show_answers'),
            default=bool(current_assignment.show_answers) if current_assignment else False
        )
    }


def build_assignment_attempt_content(assignment):
    questions = json.loads(assignment.questions_json or '[]')
    answer_key = json.loads(assignment.answer_key_json or '[]')
    pairs = []

    for index, question in enumerate(questions):
        question_copy = {
            'number': question.get('number', index + 1),
            'source_number': index + 1,
            'prompt': question.get('prompt', ''),
            'choices': [dict(choice) for choice in question.get('choices', [])]
        }
        correct_letter = answer_key[index] if index < len(answer_key) else ''
        pairs.append([question_copy, correct_letter])

    if assignment.shuffle_questions:
        random.shuffle(pairs)

    attempt_questions = []
    attempt_answer_key = []
    for display_index, (question, correct_letter) in enumerate(pairs, start=1):
        choices = [dict(choice) for choice in question.get('choices', [])]
        if assignment.shuffle_answers:
            random.shuffle(choices)

        relabeled_choices = []
        new_correct_letter = ''
        for choice_index, choice in enumerate(choices):
            new_letter = chr(65 + choice_index)
            old_letter = choice.get('letter', '')
            relabeled_choices.append({
                'letter': new_letter,
                'content': choice.get('content', '')
            })
            if old_letter == correct_letter:
                new_correct_letter = new_letter

        question['number'] = display_index
        question['choices'] = relabeled_choices
        attempt_questions.append(question)
        attempt_answer_key.append(new_correct_letter)

    return attempt_questions, attempt_answer_key


def create_assignment_attempt(assignment, student_id):
    attempt_state = get_assignment_attempt_state(assignment, student_id)
    if attempt_state['active_attempt']:
        return attempt_state['active_attempt']
    if attempt_state['attempts_exhausted']:
        raise ValueError('Bạn đã sử dụng hết số lần làm bài.')
    if attempt_state['cooldown_seconds'] > 0:
        raise ValueError(
            f'Bạn cần chờ thêm {attempt_state["cooldown_seconds"]} giây trước khi làm lại.'
        )

    questions, answer_key = build_assignment_attempt_content(assignment)
    now = app_now()
    expires_at = None
    if assignment.duration_minutes:
        expires_at = now + timedelta(minutes=assignment.duration_minutes)
    if assignment.due_at and (expires_at is None or assignment.due_at < expires_at):
        expires_at = assignment.due_at

    previous_count = AssignmentAttempt.query.filter_by(
        assignment_id=assignment.id,
        student_id=student_id
    ).count()
    attempt = AssignmentAttempt(
        assignment_id=assignment.id,
        student_id=student_id,
        attempt_number=previous_count + 1,
        questions_json=json.dumps(questions, ensure_ascii=False),
        answer_key_json=json.dumps(answer_key, ensure_ascii=False),
        answers_json='{}',
        submission_token=secrets.token_urlsafe(32),
        started_at=now,
        expires_at=expires_at,
        total_questions=len(answer_key),
        is_completed=False
    )
    db.session.add(attempt)
    db.session.commit()
    return attempt


def get_latest_completed_attempt(assignment_id, student_id):
    return (
        AssignmentAttempt.query
        .filter_by(assignment_id=assignment_id, student_id=student_id, is_completed=True)
        .order_by(AssignmentAttempt.completed_at.desc(), AssignmentAttempt.id.desc())
        .first()
    )


def get_submission_attempt(assignment, submission):
    if submission and submission.selected_attempt_id:
        attempt = AssignmentAttempt.query.filter_by(
            id=submission.selected_attempt_id,
            assignment_id=assignment.id,
            student_id=submission.student_id,
            is_completed=True
        ).first()
        if attempt:
            return attempt
    return get_latest_completed_attempt(assignment.id, submission.student_id) if submission else None


def normalize_attempt_answers(answer_key, answers):
    normalized_answers = {}
    for idx in range(len(answer_key)):
        selected = str(answers.get(str(idx)) or answers.get(idx) or '').strip().upper()
        normalized_answers[str(idx)] = selected if selected in {'A', 'B', 'C', 'D'} else ''
    return normalized_answers


def choose_scored_attempt(assignment, student_id):
    completed_attempts = (
        AssignmentAttempt.query
        .filter_by(assignment_id=assignment.id, student_id=student_id, is_completed=True)
        .order_by(AssignmentAttempt.attempt_number.asc(), AssignmentAttempt.id.asc())
        .all()
    )
    if not completed_attempts:
        return None

    policy = assignment.score_policy or 'latest'
    if policy == 'first':
        return completed_attempts[0]
    if policy == 'highest':
        return max(completed_attempts, key=lambda item: (item.score or 0, -(item.attempt_number or 0)))
    return completed_attempts[-1]


def refresh_submission_from_attempts(assignment, student_id):
    selected_attempt = choose_scored_attempt(assignment, student_id)
    submission = Submission.query.filter_by(
        assignment_id=assignment.id,
        student_id=student_id
    ).first()

    if not selected_attempt:
        if submission:
            db.session.delete(submission)
        return None

    if not submission:
        submission = Submission(assignment_id=assignment.id, student_id=student_id)
        db.session.add(submission)

    submission.answers_json = selected_attempt.answers_json or '{}'
    submission.correct_count = selected_attempt.correct_count or 0
    submission.total_questions = selected_attempt.total_questions or assignment.total_questions
    submission.score = selected_attempt.score or 0
    submission.max_score = selected_attempt.max_score or 10.0
    submission.selected_attempt_id = selected_attempt.id
    submission.submitted_at = selected_attempt.completed_at or app_now()
    return submission


def create_notification(user_id, title, message, category='info', link='', dedupe_key=None):
    if dedupe_key:
        existing = Notification.query.filter_by(user_id=user_id, dedupe_key=dedupe_key).first()
        if existing:
            return existing
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        category=category,
        link=link,
        dedupe_key=dedupe_key
    )
    db.session.add(notification)
    return notification


BADGE_DEFINITIONS = [
    {
        'code': 'account_created',
        'name': 'Khởi đầu mới',
        'description': 'Tạo tài khoản học sinh trên hệ thống Giải Tích HUST.',
        'icon': 'fa-seedling',
        'color': 'text-success',
        'metric': 'account',
        'target': 1
    },
    {
        'code': 'class_1',
        'name': 'Thành viên lớp học',
        'description': 'Tham gia lớp học đầu tiên.',
        'icon': 'fa-school',
        'color': 'text-primary',
        'metric': 'classes',
        'target': 1
    },
    {
        'code': 'assignment_1',
        'name': 'Bài làm đầu tiên',
        'description': 'Hoàn thành bài tập đầu tiên.',
        'icon': 'fa-file-circle-check',
        'color': 'text-info',
        'metric': 'assignments',
        'target': 1
    },
    {
        'code': 'class_10',
        'name': 'Học viên năng động',
        'description': 'Tham gia 10 lớp học.',
        'icon': 'fa-users',
        'color': 'text-warning',
        'metric': 'classes',
        'target': 10
    },
    {
        'code': 'assignment_10',
        'name': 'Bền bỉ',
        'description': 'Hoàn thành 10 bài tập khác nhau.',
        'icon': 'fa-medal',
        'color': 'text-danger',
        'metric': 'assignments',
        'target': 10
    },
    {
        'code': 'assignment_100',
        'name': 'Bậc thầy luyện tập',
        'description': 'Hoàn thành 100 bài tập khác nhau.',
        'icon': 'fa-trophy',
        'color': 'text-warning',
        'metric': 'assignments',
        'target': 100
    }
]


def ensure_badge_schema():
    inspector = inspect(db.engine)
    if 'badge' not in inspector.get_table_names():
        return
    existing_columns = {column['name'] for column in inspector.get_columns('badge')}
    if 'code' not in existing_columns:
        with db.engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE badge ADD COLUMN code VARCHAR(50)')


def ensure_badge_catalog():
    legacy_starter = Badge.query.filter_by(name='Một hành trình mới').first()
    for definition in BADGE_DEFINITIONS:
        badge = Badge.query.filter_by(code=definition['code']).first()
        if not badge and definition['code'] == 'account_created' and legacy_starter:
            badge = legacy_starter
        if not badge:
            badge = Badge(code=definition['code'])
            db.session.add(badge)
        badge.code = definition['code']
        badge.name = definition['name']
        badge.description = definition['description']
        badge.icon = definition['icon']
        badge.color = definition['color']

    for legacy_name in ('Kẻ hủy diệt', 'Cú đêm chăm chỉ'):
        legacy_badge = Badge.query.filter_by(name=legacy_name).first()
        if legacy_badge:
            for user in list(legacy_badge.users):
                user.badges.remove(legacy_badge)
            db.session.delete(legacy_badge)
    db.session.commit()


def get_student_badge_metrics(student):
    return {
        'account': 1,
        'classes': len(student.enrolled_classes),
        'assignments': Submission.query.filter_by(student_id=student.id).count()
    }


def award_student_badges(student, notify=True):
    if not student or student.role != 'student':
        return []

    metrics = get_student_badge_metrics(student)
    owned_codes = {badge.code for badge in student.badges}
    awarded = []
    for definition in BADGE_DEFINITIONS:
        if metrics[definition['metric']] < definition['target']:
            continue
        if definition['code'] in owned_codes:
            continue
        badge = Badge.query.filter_by(code=definition['code']).first()
        if not badge:
            continue
        student.badges.append(badge)
        owned_codes.add(definition['code'])
        awarded.append(badge)
        if notify:
            create_notification(
                student.id,
                'Mở khóa huy hiệu mới',
                f'Bạn đã nhận huy hiệu “{badge.name}”.',
                category='success',
                link='#tab-achievements',
                dedupe_key=f'badge-earned-{badge.code}'
            )
    return awarded


def serialize_student_badge_progress(student):
    metrics = get_student_badge_metrics(student)
    owned_codes = {badge.code for badge in student.badges}
    items = []
    for definition in BADGE_DEFINITIONS:
        current = metrics[definition['metric']]
        target = definition['target']
        items.append({
            **definition,
            'current': min(current, target),
            'actual_current': current,
            'unlocked': definition['code'] in owned_codes,
            'progress_percent': min(round((current / target) * 100), 100)
        })
    return {
        'items': items,
        'unlocked_count': sum(1 for item in items if item['unlocked']),
        'total_count': len(items),
        'class_count': metrics['classes'],
        'assignment_count': metrics['assignments']
    }


def ensure_due_notifications(student):
    now = app_now()
    class_ids = [item.id for item in student.enrolled_classes]
    if not class_ids:
        return

    assignments = Assignment.query.filter(
        Assignment.class_id.in_(class_ids),
        Assignment.is_active.is_(True),
    ).all()

    for assignment in assignments:
        if Submission.query.filter_by(assignment_id=assignment.id, student_id=student.id).first():
            continue

        if assignment.opens_at:
            hours_until_open = (assignment.opens_at - now).total_seconds() / 3600
            if 0 < hours_until_open <= 24:
                create_notification(
                    student.id,
                    'Bài tập sắp mở',
                    f'{assignment.title} sẽ mở lúc {assignment.opens_at.strftime("%d/%m/%Y %H:%M")}.',
                    category='info',
                    link='/calendar',
                    dedupe_key=f'assignment-open-24h-{assignment.id}'
                )

        if not assignment.due_at:
            continue

        hours_until_due = (assignment.due_at - now).total_seconds() / 3600
        if 0 < hours_until_due <= 3:
            create_notification(
                student.id,
                'Bài tập sắp hết hạn trong 3 giờ',
                f'{assignment.title} sẽ đóng lúc {assignment.due_at.strftime("%d/%m/%Y %H:%M")}.',
                category='danger',
                link='/calendar',
                dedupe_key=f'assignment-due-3h-{assignment.id}'
            )
        elif 3 < hours_until_due <= 24:
            create_notification(
                student.id,
                'Bài tập sắp hết hạn trong 24 giờ',
                f'{assignment.title} sẽ đóng lúc {assignment.due_at.strftime("%d/%m/%Y %H:%M")}.',
                category='warning',
                link='/calendar',
                dedupe_key=f'assignment-due-24h-{assignment.id}'
            )
        elif -168 <= hours_until_due <= 0:
            create_notification(
                student.id,
                'Bài tập đã quá hạn',
                f'{assignment.title} đã đóng lúc {assignment.due_at.strftime("%d/%m/%Y %H:%M")}.',
                category='secondary',
                link='/calendar',
                dedupe_key=f'assignment-overdue-{assignment.id}'
            )


def save_attempt_submission(assignment, attempt, student_id, answers, completion_reason='submitted'):
    answer_key = json.loads(attempt.answer_key_json or '[]')
    normalized_answers = normalize_attempt_answers(answer_key, answers)
    correct_count = 0

    for idx, correct_letter in enumerate(answer_key):
        selected = normalized_answers[str(idx)]
        if selected and selected == correct_letter:
            correct_count += 1

    total = len(answer_key)
    score = round((correct_count / total) * 10, 2) if total else 0
    attempt.answers_json = json.dumps(normalized_answers, ensure_ascii=False)
    attempt.correct_count = correct_count
    attempt.total_questions = total
    attempt.score = score
    attempt.max_score = 10.0
    attempt.is_completed = True
    attempt.completed_at = app_now()
    attempt.last_saved_at = app_now()
    attempt.completion_reason = completion_reason
    submission = refresh_submission_from_attempts(assignment, student_id)
    result_published = is_assignment_result_published(assignment)
    notification_message = (
        f'{assignment.title}: lần {attempt.attempt_number} đạt {score}/10.'
        if result_published
        else f'{assignment.title}: đã ghi nhận lần làm thứ {attempt.attempt_number}. Kết quả sẽ được công bố sau.'
    )
    create_notification(
        student_id,
        'Đã ghi nhận bài nộp',
        notification_message,
        category='success',
        link='#tab-practice',
        dedupe_key=f'attempt-submitted-{attempt.id}'
    )
    if assignment.teacher_id:
        student = db.session.get(User, student_id)
        create_notification(
            assignment.teacher_id,
            'Có bài nộp mới',
            f'{student.display_name if student else "Một sinh viên"} đã nộp {assignment.title} '
            f'(lần {attempt.attempt_number}).',
            category='info',
            link=f'/teacher/classes/{assignment.class_id}#classAssignments',
            dedupe_key=f'teacher-attempt-submitted-{attempt.id}'
        )
    db.session.flush()
    award_student_badges(db.session.get(User, student_id))
    db.session.commit()
    return submission


def get_attempt_effective_deadline(attempt):
    deadline = attempt.expires_at
    assignment_due_at = attempt.assignment.due_at if attempt.assignment else None
    if assignment_due_at and (deadline is None or assignment_due_at < deadline):
        deadline = assignment_due_at
    return deadline


def finalize_expired_attempts(limit=200):
    now = app_now()
    candidates = (
        AssignmentAttempt.query
        .filter_by(is_completed=False)
        .order_by(AssignmentAttempt.id.asc())
        .limit(limit)
        .all()
    )
    finalized_count = 0
    for candidate in candidates:
        deadline = get_attempt_effective_deadline(candidate)
        if not deadline or now <= deadline:
            continue
        with get_submission_lock(candidate.id):
            attempt = db.session.get(AssignmentAttempt, candidate.id)
            if not attempt or attempt.is_completed:
                continue
            deadline = get_attempt_effective_deadline(attempt)
            if not deadline or app_now() <= deadline:
                continue
            try:
                saved_answers = json.loads(attempt.answers_json or '{}')
                save_attempt_submission(
                    attempt.assignment,
                    attempt,
                    attempt.student_id,
                    saved_answers,
                    completion_reason='timeout'
                )
                finalized_count += 1
            except Exception:
                db.session.rollback()
                app.logger.exception('Could not finalize expired attempt %s', candidate.id)
    return finalized_count


def attempt_sweeper_loop():
    interval = app.config['ATTEMPT_SWEEP_INTERVAL_SECONDS']
    while not _attempt_sweeper_stop.wait(interval):
        with app.app_context():
            try:
                finalize_expired_attempts()
            except Exception:
                db.session.rollback()
                app.logger.exception('Expired attempt sweeper failed')
            finally:
                db.session.remove()


def start_attempt_sweeper():
    global _attempt_sweeper_started
    if app.config.get('TESTING') or os.getenv('DISABLE_ATTEMPT_SWEEPER', '0') == '1':
        return
    with _attempt_sweeper_guard:
        if _attempt_sweeper_started:
            return
        worker = threading.Thread(
            target=attempt_sweeper_loop,
            name='assignment-attempt-sweeper',
            daemon=True
        )
        worker.start()
        _attempt_sweeper_started = True


def ensure_exam_bank_schema():
    inspector = inspect(db.engine)
    if 'exam_bank' not in inspector.get_table_names():
        return

    existing_columns = {column['name'] for column in inspector.get_columns('exam_bank')}
    with db.engine.begin() as connection:
        if 'topic' not in existing_columns:
            connection.exec_driver_sql('ALTER TABLE exam_bank ADD COLUMN topic VARCHAR(150)')
        if 'week_number' not in existing_columns:
            connection.exec_driver_sql('ALTER TABLE exam_bank ADD COLUMN week_number INTEGER')


def ensure_assignment_schema():
    inspector = inspect(db.engine)
    if 'assignment' not in inspector.get_table_names():
        return

    existing_columns = {column['name'] for column in inspector.get_columns('assignment')}
    additions = {
        'opens_at': 'DATETIME',
        'duration_minutes': 'INTEGER',
        'allow_retry': 'BOOLEAN DEFAULT 0',
        'max_attempts': 'INTEGER DEFAULT 1',
        'retry_cooldown_minutes': 'INTEGER DEFAULT 0',
        'score_policy': "VARCHAR(20) DEFAULT 'latest'",
        'result_release_policy': "VARCHAR(20) DEFAULT 'immediate'",
        'results_published': 'BOOLEAN DEFAULT 0',
        'show_answers': 'BOOLEAN DEFAULT 0',
        'shuffle_questions': 'BOOLEAN DEFAULT 0',
        'shuffle_answers': 'BOOLEAN DEFAULT 0',
        'version_hash': 'VARCHAR(64)'
    }
    with db.engine.begin() as connection:
        for column_name, column_type in additions.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(
                    f'ALTER TABLE assignment ADD COLUMN {column_name} {column_type}'
                )


def backfill_assignment_policies():
    changed = False
    for assignment in Assignment.query.all():
        if assignment.allow_retry and assignment.max_attempts == 1:
            assignment.max_attempts = None
            changed = True
        elif not assignment.allow_retry and assignment.max_attempts is None:
            assignment.max_attempts = 1
            changed = True
        if assignment.retry_cooldown_minutes is None:
            assignment.retry_cooldown_minutes = 0
            changed = True
        if not assignment.result_release_policy:
            assignment.result_release_policy = 'immediate'
            changed = True
        if assignment.results_published is None:
            assignment.results_published = False
            changed = True
        if assignment.show_answers is None:
            assignment.show_answers = False
            changed = True
    if changed:
        db.session.commit()


def ensure_attempt_schema():
    inspector = inspect(db.engine)
    if 'assignment_attempt' not in inspector.get_table_names():
        return

    existing_columns = {column['name'] for column in inspector.get_columns('assignment_attempt')}
    additions = {
        'answers_json': "TEXT DEFAULT '{}'",
        'last_saved_at': 'DATETIME',
        'completion_reason': 'VARCHAR(30)',
        'correct_count': 'INTEGER DEFAULT 0',
        'total_questions': 'INTEGER DEFAULT 0',
        'score': 'FLOAT DEFAULT 0',
        'max_score': 'FLOAT DEFAULT 10',
        'submission_token': 'VARCHAR(96)',
        'submission_response_json': 'TEXT'
    }
    with db.engine.begin() as connection:
        for column_name, column_type in additions.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(
                    f'ALTER TABLE assignment_attempt ADD COLUMN {column_name} {column_type}'
                )


def ensure_submission_schema():
    inspector = inspect(db.engine)
    if 'submission' not in inspector.get_table_names():
        return
    existing_columns = {column['name'] for column in inspector.get_columns('submission')}
    if 'selected_attempt_id' not in existing_columns:
        with db.engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE submission ADD COLUMN selected_attempt_id INTEGER')


def backfill_assignment_versions():
    changed = False
    for assignment in Assignment.query.filter(
        (Assignment.version_hash.is_(None)) | (Assignment.version_hash == '')
    ).all():
        assignment.version_hash = hashlib.sha256(
            (assignment.content_tex or '').encode('utf-8')
        ).hexdigest()[:16]
        changed = True
    if changed:
        db.session.commit()


def backfill_attempt_results():
    changed = False
    for submission in Submission.query.all():
        if submission.selected_attempt_id:
            continue
        attempt = get_latest_completed_attempt(submission.assignment_id, submission.student_id)
        if not attempt:
            continue
        attempt.answers_json = submission.answers_json or '{}'
        attempt.correct_count = submission.correct_count or 0
        attempt.total_questions = submission.total_questions or 0
        attempt.score = submission.score or 0
        attempt.max_score = submission.max_score or 10.0
        attempt.completion_reason = attempt.completion_reason or 'submitted'
        attempt.last_saved_at = attempt.last_saved_at or attempt.completed_at
        submission.selected_attempt_id = attempt.id
        changed = True
    for attempt in AssignmentAttempt.query.filter(
        (AssignmentAttempt.submission_token.is_(None)) | (AssignmentAttempt.submission_token == '')
    ).all():
        attempt.submission_token = secrets.token_urlsafe(32)
        changed = True
    if changed:
        db.session.commit()


def ensure_system_admin():
    existing = User.query.filter(
        func.lower(User.username) == DEFAULT_ADMIN_USERNAME
    ).first()
    if existing:
        if existing.role != 'admin':
            raise RuntimeError(
                f'Khong the tao admin mac dinh: username "{DEFAULT_ADMIN_USERNAME}" '
                f'da thuoc tai khoan {existing.role}.'
            )
        if existing.approval_status != 'approved':
            existing.approval_status = 'approved'
            db.session.commit()
        return existing

    admin = User(
        username=DEFAULT_ADMIN_USERNAME,
        full_name='Quản trị hệ thống',
        password=generate_password_hash(DEFAULT_ADMIN_PASSWORD),
        role='admin',
        approval_status='approved',
    )
    db.session.add(admin)
    db.session.commit()
    app.logger.warning(
        'Da tao tai khoan admin mac dinh. Hay doi mat khau ngay sau lan dang nhap dau tien.'
    )
    return admin


def bootstrap_application_data():
    with app.app_context():
        table_names = set(inspect(db.engine).get_table_names())
        if not table_names:
            upgrade()
        elif 'alembic_version' not in table_names:
            ensure_badge_schema()
            ensure_exam_bank_schema()
            ensure_assignment_schema()
            ensure_attempt_schema()
            ensure_submission_schema()
            stamp(revision='1e9c0ae60696')
            upgrade()
        else:
            upgrade()
        ensure_system_admin()
        backfill_assignment_policies()
        backfill_assignment_versions()
        backfill_attempt_results()
        ensure_badge_catalog()
        for existing_student in User.query.filter_by(role='student').all():
            award_student_badges(existing_student, notify=False)
        db.session.commit()


if os.getenv('SKIP_SCHEMA_BOOTSTRAP', '0') != '1':
    bootstrap_application_data()


@app.before_request
def ensure_attempt_sweeper_started():
    start_attempt_sweeper()


def backup_sqlite_database(output_path=None):
    with app.app_context():
        if db.engine.url.get_backend_name() != 'sqlite':
            raise RuntimeError('Lệnh sao lưu nội bộ hiện chỉ hỗ trợ SQLite.')
        source_path = Path(db.engine.url.database).resolve()
        if not source_path.exists():
            raise FileNotFoundError(f'Không tìm thấy database: {source_path}')

        backup_dir = (INSTANCE_DIR / 'backups').resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        if output_path:
            destination_path = Path(output_path).expanduser().resolve()
        else:
            timestamp = app_now().strftime('%Y%m%d_%H%M%S')
            destination_path = backup_dir / f'database_{timestamp}.db'
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        source_connection = sqlite3.connect(f'file:{source_path.as_posix()}?mode=ro', uri=True)
        destination_connection = sqlite3.connect(str(destination_path))
        try:
            source_connection.backup(destination_connection)
            integrity_result = destination_connection.execute('PRAGMA integrity_check').fetchone()
            if not integrity_result or integrity_result[0] != 'ok':
                raise RuntimeError('Bản sao lưu không vượt qua kiểm tra toàn vẹn.')
        finally:
            destination_connection.close()
            source_connection.close()

        keep_count = max(int(os.getenv('BACKUP_KEEP', '10')), 1)
        automatic_backups = sorted(
            backup_dir.glob('database_*.db'),
            key=lambda item: item.stat().st_mtime,
            reverse=True
        )
        for old_backup in automatic_backups[keep_count:]:
            old_backup.unlink()
        return destination_path


@app.cli.command('backup-db')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def backup_db_command(output):
    """Create a consistent SQLite backup and verify its integrity."""
    backup_path = backup_sqlite_database(output)
    click.echo(f'Backup created: {backup_path}')


@app.cli.command('finalize-expired-attempts')
def finalize_expired_attempts_command():
    """Finalize every unfinished attempt whose deadline has passed."""
    finalized_count = finalize_expired_attempts()
    click.echo(f'Finalized attempts: {finalized_count}')


@app.cli.command('create-teacher')
@click.option('--username', prompt='Tên đăng nhập giáo viên')
@click.option('--full-name', prompt='Họ và tên giáo viên')
@click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
def create_teacher_command(username, full_name, password):
    """Create a teacher account outside public registration."""
    username = normalize_username(username)
    full_name = normalize_full_name(full_name)
    validation_error = (
        validate_username(username)
        or validate_full_name(full_name)
        or validate_password(password)
    )
    if validation_error:
        raise click.ClickException(validation_error)
    if User.query.filter(func.lower(User.username) == username).first():
        raise click.ClickException('Tên đăng nhập đã tồn tại.')

    db.session.add(User(
        username=username,
        full_name=full_name,
        password=generate_password_hash(password),
        role='teacher',
        approval_status='approved',
    ))
    db.session.commit()
    click.echo(f'Đã tạo tài khoản giáo viên: {username}')


# --- CÁC ĐƯỜNG DẪN CƠ BẢN ---
@app.route('/')
def home():
    return render_template('index.html')


@app.route('/search')
@login_required
def global_search():
    query = ' '.join(str(request.args.get('q') or '').split())[:100]
    results = []
    if len(query) < 2:
        return render_template('search_results.html', query=query, results=results)

    escaped_query = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    pattern = f'%{escaped_query}%'

    def matches(*columns):
        return or_(*(column.ilike(pattern, escape='\\') for column in columns))

    def add_result(kind, title, description, link, icon):
        results.append({
            'kind': kind,
            'title': title,
            'description': description,
            'link': link,
            'icon': icon,
        })

    if current_user.role == 'teacher':
        classes = (
            Class.query
            .filter(
                Class.teacher_id == current_user.id,
                matches(Class.name, Class.code, Class.description),
            )
            .order_by(Class.name.asc())
            .limit(10)
            .all()
        )
        for classroom in classes:
            add_result(
                'Lớp học', classroom.name,
                f'Mã lớp {classroom.code} · {len(classroom.students)} sinh viên',
                url_for('teacher_class_detail', class_id=classroom.id),
                'fa-users-chalkboard',
            )

        bank_items = (
            ExamBank.query
            .filter(
                ExamBank.teacher_id == current_user.id,
                matches(ExamBank.title, ExamBank.topic, ExamBank.description),
            )
            .order_by(ExamBank.updated_at.desc())
            .limit(10)
            .all()
        )
        for item in bank_items:
            add_result(
                'Ngân hàng đề', item.title,
                item.topic or f'{item.total_questions or 0} câu hỏi',
                url_for('teacher_dashboard', _anchor='tab-exam-bank'),
                'fa-database',
            )

        templates = (
            ExamTemplate.query
            .filter(
                ExamTemplate.teacher_id == current_user.id,
                matches(ExamTemplate.title, ExamTemplate.topic, ExamTemplate.description),
            )
            .order_by(ExamTemplate.updated_at.desc())
            .limit(10)
            .all()
        )
        for item in templates:
            add_result(
                'Mẫu sinh đề', item.title, item.topic or 'Mẫu đề tùy chỉnh',
                url_for('teacher_dashboard', _anchor='tab-exam-generator'),
                'fa-file-code',
            )

        assignments = (
            Assignment.query
            .filter(
                Assignment.teacher_id == current_user.id,
                matches(Assignment.title, Assignment.description),
            )
            .order_by(Assignment.updated_at.desc())
            .limit(10)
            .all()
        )
        for assignment in assignments:
            add_result(
                'Bài đã giao', assignment.title,
                assignment.classroom.name if assignment.classroom else '',
                url_for(
                    'teacher_class_detail',
                    class_id=assignment.class_id,
                    _anchor='classAssignments',
                ),
                'fa-clipboard-list',
            )

    elif current_user.role == 'student':
        class_ids = [classroom.id for classroom in current_user.enrolled_classes]
        if class_ids:
            classes = (
                Class.query
                .filter(
                    Class.id.in_(class_ids),
                    matches(Class.name, Class.code, Class.description),
                )
                .order_by(Class.name.asc())
                .limit(10)
                .all()
            )
            for classroom in classes:
                add_result(
                    'Lớp học', classroom.name, f'Mã lớp {classroom.code}',
                    url_for('student_dashboard', _anchor='tab-my-classes'),
                    'fa-school',
                )

            assignments = (
                Assignment.query
                .filter(
                    Assignment.class_id.in_(class_ids),
                    Assignment.is_active.is_(True),
                    matches(Assignment.title, Assignment.description),
                )
                .order_by(Assignment.due_at.asc(), Assignment.id.desc())
                .limit(20)
                .all()
            )
            for assignment in assignments:
                add_result(
                    'Bài tập', assignment.title,
                    assignment.classroom.name if assignment.classroom else '',
                    url_for('student_dashboard', _anchor='tab-practice'),
                    'fa-pen-to-square',
                )

    elif current_user.role == 'admin':
        teachers = (
            User.query
            .filter(
                User.role == 'teacher',
                matches(User.full_name, User.username),
            )
            .order_by(User.full_name.asc())
            .limit(30)
            .all()
        )
        status_labels = {
            'pending': 'Chờ duyệt',
            'approved': 'Đã phê duyệt',
            'rejected': 'Đã từ chối',
        }
        for teacher in teachers:
            add_result(
                'Giảng viên', teacher.display_name,
                f'@{teacher.username} · {status_labels.get(teacher.approval_status, teacher.approval_status)}',
                url_for('admin_dashboard'),
                'fa-chalkboard-user',
            )

    return render_template('search_results.html', query=query, results=results)


@app.route('/help')
@login_required
def help_center():
    return render_template('help.html')


@app.route('/api/security/csrf-token')
def api_security_csrf_token():
    return jsonify({'success': True, 'csrf_token': get_csrf_token()})


@app.route('/api/system/latex-status')
@login_required
def api_system_latex_status():
    command = get_pdflatex_command()
    return jsonify({
        'success': True,
        'available': bool(command),
        'engine': Path(command).name if command else None,
        'download_url': 'https://miktex.org/download',
        'message': (
            'MiKTeX/pdflatex đã sẵn sàng để xuất PDF.'
            if command else
            'Chưa tìm thấy MiKTeX/pdflatex. Bạn vẫn có thể xuất file TEX.'
        ),
    })


@app.route('/register', methods=['GET', 'POST'])
@rate_limit(5, 300, 'register')
def register():
    if current_user.is_authenticated:
        return redirect(dashboard_url_for(current_user))

    form_data = {
        'role': 'student',
        'full_name': '',
        'username': '',
        'student_code': '',
        'school_name': 'Đại học Bách Khoa Hà Nội',
        'major': ''
    }
    if request.method == 'POST':
        requested_role = str(request.form.get('role') or 'student').strip().lower()
        full_name = normalize_full_name(request.form.get('full_name'))
        username = normalize_username(request.form.get('username'))
        password = str(request.form.get('password') or '')
        confirm_password = str(request.form.get('confirm_password') or '')
        student_code = normalize_student_code(request.form.get('student_code'))
        school_name = str(request.form.get('school_name') or '').strip()
        major = str(request.form.get('major') or '').strip()
        form_data.update({
            'role': requested_role,
            'full_name': full_name,
            'username': username,
            'student_code': student_code,
            'school_name': school_name,
            'major': major
        })

        if requested_role not in {'student', 'teacher'}:
            flash('Loại tài khoản đăng ký không hợp lệ.', 'danger')
            return render_template('register.html', form_data=form_data), 400
        validation_error = (
            validate_full_name(full_name)
            or validate_username(username)
            or validate_password(password)
        )
        if not validation_error and requested_role == 'student':
            validation_error = validate_student_profile(student_code, school_name, major)
        if validation_error:
            flash(validation_error, 'danger')
            return render_template('register.html', form_data=form_data), 400
        if password != confirm_password:
            flash('Mật khẩu nhập lại không khớp.', 'danger')
            return render_template('register.html', form_data=form_data), 400

        if User.query.filter(func.lower(User.username) == username).first():
            flash('Tên đăng nhập đã tồn tại.', 'danger')
            return render_template('register.html', form_data=form_data), 409
        if requested_role == 'student' and User.query.filter_by(student_code=student_code).first():
            flash('Mã số sinh viên đã được sử dụng.', 'danger')
            return render_template('register.html', form_data=form_data), 409

        new_user = User(
            username=username,
            full_name=full_name,
            password=generate_password_hash(password),
            role=requested_role,
            approval_status='pending' if requested_role == 'teacher' else 'approved',
            student_code=student_code if requested_role == 'student' else None,
            school_name=school_name if requested_role == 'student' else None,
            major=major if requested_role == 'student' else None,
        )
        try:
            db.session.add(new_user)
            db.session.flush()
            if requested_role == 'student':
                award_student_badges(new_user, notify=False)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Tên đăng nhập hoặc mã số sinh viên đã tồn tại.', 'danger')
            return render_template('register.html', form_data=form_data), 409

        if requested_role == 'teacher':
            flash('Đã gửi đăng ký giảng viên. Bạn có thể đăng nhập sau khi quản trị viên phê duyệt.', 'info')
        else:
            flash('Đăng ký thành công! Vui lòng đăng nhập.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', form_data=form_data)

@app.route('/login', methods=['GET', 'POST'])
@rate_limit(5, 60, 'login')
def login():
    if current_user.is_authenticated:
        return redirect(dashboard_url_for(current_user))
    if request.method == 'POST':
        username = normalize_username(request.form.get('username'))
        password = str(request.form.get('password') or '')
        user = User.query.filter(func.lower(User.username) == username).first()
        
        if user and check_password_hash(user.password, password):
            if user.role == 'teacher' and user.approval_status != 'approved':
                if user.approval_status == 'rejected':
                    flash('Đăng ký giảng viên đã bị từ chối. Vui lòng liên hệ quản trị viên.', 'danger')
                else:
                    flash('Tài khoản giảng viên đang chờ quản trị viên phê duyệt.', 'warning')
                return redirect(url_for('login'))
            next_url = safe_next_url(request.args.get('next'))
            session.clear()
            login_user(user)
            return redirect(next_url or dashboard_url_for(user))
        else:
            flash('Sai tên đăng nhập hoặc mật khẩu. Vui lòng thử lại!', 'danger')
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('home'))


@app.route('/admin-dashboard')
@role_required('admin')
def admin_dashboard():
    teachers = (
        User.query
        .filter_by(role='teacher')
        .order_by(User.id.desc())
        .all()
    )
    grouped_teachers = {
        status: [teacher for teacher in teachers if teacher.approval_status == status]
        for status in ('pending', 'approved', 'rejected')
    }
    database_path = Path(db.engine.url.database).resolve() if db.engine.url.database else None
    latex_command = get_pdflatex_command()
    backup_dir = (INSTANCE_DIR / 'backups').resolve()
    backup_files = sorted(
        backup_dir.glob('database_*.db'),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ) if backup_dir.exists() else []
    system_info = {
        'version': APP_VERSION,
        'is_packaged': bool(getattr(sys, 'frozen', False)),
        'latex_available': bool(latex_command),
        'latex_engine': Path(latex_command).name if latex_command else '',
        'database_size_mb': round(database_path.stat().st_size / (1024 * 1024), 2)
        if database_path and database_path.exists() else 0,
        'data_directory': str(INSTANCE_DIR),
        'backup_count': len(backup_files),
        'latest_backup': backup_files[0].name if backup_files else '',
    }
    return render_template(
        'admin_dashboard.html',
        grouped_teachers=grouped_teachers,
        system_info=system_info,
    )


@app.route('/admin/teachers/<int:user_id>/<action>', methods=['POST'])
@role_required('admin')
def review_teacher_registration(user_id, action):
    teacher = db.get_or_404(User, user_id)
    if teacher.role != 'teacher' or action not in {'approve', 'reject'}:
        abort(404)

    teacher.approval_status = 'approved' if action == 'approve' else 'rejected'
    if action == 'approve':
        create_notification(
            teacher.id,
            'Tài khoản giảng viên đã được duyệt',
            'Bạn có thể sử dụng đầy đủ các chức năng dành cho giảng viên.',
            category='success',
            link='/teacher-dashboard',
            dedupe_key=f'teacher-approved-{teacher.id}',
        )
    db.session.commit()
    flash(
        f'Đã {"phê duyệt" if action == "approve" else "từ chối"} tài khoản {teacher.display_name}.',
        'success' if action == 'approve' else 'warning',
    )
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/data/backup', methods=['POST'])
@role_required('admin')
@rate_limit(3, 60, 'admin-backup')
def admin_backup_database():
    try:
        backup_path = backup_sqlite_database()
    except (RuntimeError, FileNotFoundError, OSError, sqlite3.Error) as exc:
        app.logger.exception('Khong the sao luu database tu giao dien admin')
        flash(f'Không thể sao lưu dữ liệu: {exc}', 'danger')
        return redirect(url_for('admin_dashboard'))

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=backup_path.name,
        mimetype='application/vnd.sqlite3',
    )


@app.route('/profile')
@login_required
def profile():
    if current_user.role == 'student':
        award_student_badges(current_user, notify=False)
        db.session.commit()
    return render_template('profile.html', user=current_user)


@app.route('/profile/update', methods=['POST'])
@login_required
@rate_limit(10, 300, 'profile-update')
def update_profile():
    full_name = normalize_full_name(request.form.get('full_name'))
    username = (
        current_user.username
        if current_user.role == 'admin'
        else normalize_username(request.form.get('username'))
    )
    validation_error = validate_full_name(full_name) or validate_username(username)
    if validation_error:
        flash(validation_error, 'danger')
        return redirect(url_for('profile'))

    existing_username = User.query.filter(
        func.lower(User.username) == username,
        User.id != current_user.id
    ).first()
    if existing_username:
        flash('Tên đăng nhập đã được sử dụng.', 'danger')
        return redirect(url_for('profile'))

    current_user.full_name = full_name
    current_user.username = username
    if current_user.role == 'student':
        student_code = normalize_student_code(request.form.get('student_code'))
        school_name = str(request.form.get('school_name') or '').strip()
        major = str(request.form.get('major') or '').strip()
        if (
            current_user.student_code_verified
            and student_code != current_user.student_code
        ):
            db.session.rollback()
            flash(
                'Mã số sinh viên đã được xác minh nên không thể tự thay đổi. '
                'Hãy liên hệ giáo viên nếu thông tin bị sai.',
                'danger'
            )
            return redirect(url_for('profile'))
        validation_error = validate_student_profile(student_code, school_name, major)
        if validation_error:
            db.session.rollback()
            flash(validation_error, 'danger')
            return redirect(url_for('profile'))
        existing_code = User.query.filter(
            User.student_code == student_code,
            User.id != current_user.id
        ).first()
        if existing_code:
            db.session.rollback()
            flash('Mã số sinh viên đã được sử dụng.', 'danger')
            return redirect(url_for('profile'))
        current_user.student_code = student_code
        current_user.school_name = school_name
        current_user.major = major

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash('Tên đăng nhập hoặc mã số sinh viên đã tồn tại.', 'danger')
        return redirect(url_for('profile'))

    flash('Đã cập nhật thông tin cá nhân.', 'success')
    return redirect(url_for('profile'))


@app.route('/profile/change-password', methods=['POST'])
@login_required
@rate_limit(5, 300, 'password-change')
def change_password():
    current_password = str(request.form.get('current_password') or '')
    new_password = str(request.form.get('new_password') or '')
    confirm_password = str(request.form.get('confirm_password') or '')

    if not check_password_hash(current_user.password, current_password):
        flash('Mật khẩu hiện tại không chính xác.', 'danger')
        return redirect(url_for('profile'))
    validation_error = validate_password(new_password)
    if validation_error:
        flash(validation_error, 'danger')
        return redirect(url_for('profile'))
    if new_password != confirm_password:
        flash('Mật khẩu mới nhập lại không khớp.', 'danger')
        return redirect(url_for('profile'))
    if check_password_hash(current_user.password, new_password):
        flash('Mật khẩu mới phải khác mật khẩu hiện tại.', 'danger')
        return redirect(url_for('profile'))

    current_user.password = generate_password_hash(new_password)
    db.session.commit()
    flash('Đã đổi mật khẩu.', 'success')
    return redirect(url_for('profile'))


@app.route('/teacher-dashboard')
@login_required
def teacher_dashboard():
    if current_user.role != 'teacher':
        flash('Bạn không có quyền truy cập trang này!', 'danger')
        return redirect(dashboard_url_for(current_user))
    
    # Lấy toàn bộ lớp học do giáo viên này tạo từ CSDL
    my_classes = Class.query.filter_by(teacher_id=current_user.id).all()
    
    return render_template(
        'teacher_dashboard.html',
        classes=my_classes,
        export_dir=str(get_user_export_dir()),
    )


@app.route('/teacher/classes/<int:class_id>')
@teacher_owns_class
def teacher_class_detail(class_id):
    detail = build_teacher_class_detail(g.target_class)
    return render_template(
        'teacher_class_detail.html',
        classroom=g.target_class,
        **detail
    )


@app.route('/calendar')
@login_required
def learning_calendar():
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    if current_user.role == 'student':
        ensure_due_notifications(current_user)
        db.session.commit()
        classes = list(current_user.enrolled_classes)
    else:
        classes = Class.query.filter_by(teacher_id=current_user.id).order_by(Class.name.asc()).all()
    return render_template('calendar.html', classes=classes)


# =====================================================================
# --- API DASHBOARD HỌC SINH (ĐÃ CẬP NHẬT DATABASE) ---
# =====================================================================
@app.route('/student-dashboard')
@login_required
def student_dashboard():
    if current_user.role != 'student':
        return redirect(dashboard_url_for(current_user))
    
    # Lấy danh sách các lớp học mà học sinh này đã tham gia (nhờ relationship enrolled_classes trong models)
    my_classes = current_user.enrolled_classes
    award_student_badges(current_user, notify=False)
    db.session.commit()
    badge_progress = serialize_student_badge_progress(current_user)
    pending_join_requests = (
        ClassJoinRequest.query
        .filter_by(student_id=current_user.id, status='pending')
        .order_by(ClassJoinRequest.requested_at.desc())
        .all()
    )
    return render_template(
        'student_dashboard.html',
        classes=my_classes,
        badge_progress=badge_progress,
        pending_join_requests=pending_join_requests
    )


# =====================================================================
# --- API GIẢNG VIÊN: TẠO LỚP HỌC MỚI ---
# =====================================================================
@app.route('/api/teacher/create-class', methods=['POST'])
@role_required('teacher')
def api_create_class():
    name = str(request.form.get('name') or '').strip()
    description = str(request.form.get('description') or '').strip()
    code = str(request.form.get('code') or '').strip().upper()
    join_policy = str(request.form.get('join_policy') or 'open').strip().lower()

    if not name or not code:
        return jsonify({'success': False, 'message': 'Thiếu thông tin lớp học!'})
    if join_policy not in {'open', 'approval'}:
        return jsonify({'success': False, 'message': 'Chế độ tham gia lớp không hợp lệ.'}), 400

    # Kiểm tra mã lớp đã tồn tại chưa
    existing_class = Class.query.filter_by(code=code).first()
    if existing_class:
        return jsonify({'success': False, 'message': 'Mã lớp này đã tồn tại, vui lòng tạo mã khác!'})

    new_class = Class(
        name=name,
        description=description,
        code=code,
        teacher_id=current_user.id,
        join_policy=join_policy
    )
    db.session.add(new_class)
    db.session.commit()

    return jsonify({'success': True})


# =====================================================================
# --- API GIẢNG VIÊN: XÓA LỚP HỌC ---
# =====================================================================
@app.route('/api/teacher/delete-class/<int:class_id>', methods=['POST'])
@teacher_owns_class
def api_delete_class(class_id):
    target_class = g.target_class

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
def parse_random_exam_blueprint(form):
    blueprint = {}
    total = 0
    for topic in RANDOM_EXAM_TOPIC_LABELS:
        levels = {}
        for difficulty in ('easy', 'medium', 'hard'):
            raw_value = form.get(f'matrix_{topic}_{difficulty}', 0)
            try:
                count = int(raw_value or 0)
            except (TypeError, ValueError):
                raise ValueError('Số lượng trong ma trận đề phải là số nguyên.')
            if count < 0:
                raise ValueError('Số lượng trong ma trận đề không được âm.')
            levels[difficulty] = count
            total += count
        blueprint[topic] = levels
    if total < 1 or total > 40:
        raise ValueError('Mỗi mã đề trong ma trận cần từ 1 đến 40 câu hỏi.')
    return blueprint, total


def serialize_random_variant(seed, questions, overlap_ratio=0.0):
    payloads = []
    for index, question in enumerate(questions, start=1):
        payload = question.to_dict(index)
        payload.update({
            'locked': False,
            'manually_edited': False,
            'original_auto': None,
        })
        payloads.append(payload)
    return {
        'seed': seed,
        'questions': payloads,
        'revision': 0,
        'overlap_ratio': overlap_ratio,
    }


def get_random_draft_variants(draft):
    if not draft:
        return []
    variants = draft.get('variants')
    if isinstance(variants, list):
        return variants
    questions = draft.get('questions')
    if isinstance(questions, list):
        return [{
            'seed': draft.get('seed', 1),
            'questions': questions,
            'revision': draft.get('revision', 0),
            'overlap_ratio': 0.0,
        }]
    return []


def combine_random_exam_tex(rendered_variants):
    if not rendered_variants:
        return ''
    first_content = rendered_variants[0]['content']
    first_match = re.search(
        r'\\begin\s*\{document\}(.*?)\\end\s*\{document\}',
        first_content,
        re.DOTALL,
    )
    if not first_match:
        return first_content

    preamble = first_content[:first_match.start()] + '\\begin{document}\n'
    preamble = re.sub(r'\\MaDeThi\{[^{}]*\}', r'\\MaDeThi{NHIỀU MÃ}', preamble)
    bodies = []
    for item in rendered_variants:
        match = re.search(
            r'\\begin\s*\{document\}(.*?)\\end\s*\{document\}',
            item['content'],
            re.DOTALL,
        )
        if match:
            bodies.append(
                f"\\begin{{center}}\\Large\\textbf{{MÃ ĐỀ: {item['seed']}}}\\end{{center}}\n"
                + match.group(1).strip()
            )
    return preamble + '\n\\newpage\n'.join(bodies) + '\n\\end{document}'


@app.route('/api/generate-exam', methods=['POST'])
@role_required('teacher')
def api_generate_exam():
    title = str(request.form.get('exam_title') or 'Đề thi Giải tích I').strip()
    difficulty = str(request.form.get('difficulty') or 'medium').strip().lower()
    use_matrix = parse_bool(request.form.get('use_matrix'))
    topics = request.form.getlist('topics')
    if not topics:
        topics = list(RANDOM_EXAM_GENERATORS)

    try:
        seed = int(request.form.get('seed') or 1)
        variant_count = int(request.form.get('variant_count') or 1)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Seed và số mã đề phải là số nguyên.'}), 400

    blueprint = None
    try:
        if use_matrix:
            blueprint, total_questions = parse_random_exam_blueprint(request.form)
            difficulty_counts = {'easy': 0, 'medium': 0, 'hard': 0}
        elif difficulty == 'mixed':
            difficulty_counts = {
                'easy': int(request.form.get('qty_easy') or 0),
                'medium': int(request.form.get('qty_medium') or 0),
                'hard': int(request.form.get('qty_hard') or 0),
            }
            total_questions = sum(difficulty_counts.values())
        elif difficulty in {'easy', 'medium', 'hard'}:
            quantity = int(request.form.get('quantity') or 0)
            difficulty_counts = {'easy': 0, 'medium': 0, 'hard': 0}
            difficulty_counts[difficulty] = quantity
            total_questions = quantity
        else:
            return jsonify({'success': False, 'message': 'Mức độ sinh đề không hợp lệ.'}), 400
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Số lượng câu hỏi phải là số nguyên.'}), 400

    if any(value < 0 for value in difficulty_counts.values()):
        return jsonify({'success': False, 'message': 'Số lượng câu hỏi không được âm.'}), 400
    if total_questions < 1 or total_questions > 40:
        return jsonify({'success': False, 'message': 'Mỗi đề cần từ 1 đến 40 câu hỏi.'}), 400

    try:
        generated_variants = generate_random_variants(
            seed,
            variant_count,
            difficulty_counts=difficulty_counts,
            topics=topics,
            blueprint=blueprint,
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    variants = [
        serialize_random_variant(
            item['seed'],
            item['questions'],
            item['overlap_ratio'],
        )
        for item in generated_variants
    ]
    rendered_variants = [{
        'seed': item['seed'],
        'content': render_random_exam_tex(title, item['seed'], item['questions'])
    } for item in generated_variants]
    tex_content = combine_random_exam_tex(rendered_variants)

    draft = {
        'title': title,
        'base_seed': seed,
        'difficulty_counts': difficulty_counts,
        'topics': topics,
        'blueprint': blueprint,
        'use_matrix': use_matrix,
        'variants': variants,
        'bank_exam_id': None,
        'quality_report': {
            'status': 'passed',
            'message': 'Đã kiểm tra cấu trúc, đáp án và độ trùng giữa các mã đề.'
        },
    }
    store_current_exam_tex(tex_content)
    store_random_exam_draft(draft)
    topic_counts = {}
    for question in generated_variants[0]['questions']:
        topic_counts[question.topic] = topic_counts.get(question.topic, 0) + 1

    return jsonify({
        'success': True,
        'seed': variants[0]['seed'],
        'base_seed': seed,
        'revision': 0,
        'total_questions': total_questions,
        'total_variants': len(variants),
        'difficulty_counts': difficulty_counts,
        'quality_report': draft['quality_report'],
        'topic_counts': [{
            'topic': topic,
            'label': RANDOM_EXAM_TOPIC_LABELS[topic],
            'count': count
        } for topic, count in topic_counts.items()],
        'questions': variants[0]['questions'],
        'variants': variants,
    })


def rebuild_random_exam_draft(draft):
    rendered_variants = []
    for variant in get_random_draft_variants(draft):
        questions = [generated_question_from_dict(item) for item in variant.get('questions', [])]
        rendered_variants.append({
            'seed': variant.get('seed', 1),
            'content': render_random_exam_tex(
                draft.get('title') or 'Đề thi Giải tích I',
                variant.get('seed', 1),
                questions,
            )
        })
    tex_content = combine_random_exam_tex(rendered_variants)
    store_current_exam_tex(tex_content)
    store_random_exam_draft(draft)
    return tex_content, rendered_variants


@app.route(
    '/api/generate-exam/questions/<int:question_index>/lock',
    defaults={'variant_index': 0},
    methods=['PUT']
)
@app.route(
    '/api/generate-exam/variants/<int:variant_index>/questions/<int:question_index>/lock',
    methods=['PUT']
)
@role_required('teacher')
def api_lock_random_exam_question(question_index, variant_index):
    draft = load_random_exam_draft()
    variants = get_random_draft_variants(draft)
    if variant_index < 0 or variant_index >= len(variants):
        return jsonify({'success': False, 'message': 'Không tìm thấy mã đề trong bản nháp.'}), 404
    questions = variants[variant_index].get('questions', [])
    if question_index < 0 or question_index >= len(questions):
        return jsonify({'success': False, 'message': 'Không tìm thấy câu hỏi trong bản nháp.'}), 404

    locked = parse_bool((request.get_json() or {}).get('locked'), default=True)
    questions[question_index]['locked'] = locked
    store_random_exam_draft(draft)
    return jsonify({
        'success': True,
        'variant_index': variant_index,
        'question_index': question_index,
        'locked': locked,
        'message': 'Đã khóa câu hỏi.' if locked else 'Đã mở khóa câu hỏi.'
    })


@app.route(
    '/api/generate-exam/questions/<int:question_index>/regenerate',
    defaults={'variant_index': 0},
    methods=['POST']
)
@app.route(
    '/api/generate-exam/variants/<int:variant_index>/questions/<int:question_index>/regenerate',
    methods=['POST']
)
@role_required('teacher')
def api_regenerate_random_exam_question(question_index, variant_index):
    draft = load_random_exam_draft()
    variants = get_random_draft_variants(draft)
    if variant_index < 0 or variant_index >= len(variants):
        return jsonify({'success': False, 'message': 'Không tìm thấy mã đề trong bản nháp.'}), 404
    variant = variants[variant_index]
    questions = variant.get('questions', [])
    if question_index < 0 or question_index >= len(questions):
        return jsonify({'success': False, 'message': 'Không tìm thấy câu hỏi trong bản nháp.'}), 404
    if questions[question_index].get('locked'):
        return jsonify({'success': False, 'message': 'Câu hỏi đang được khóa. Hãy mở khóa trước khi sinh lại.'}), 409

    current_question = generated_question_from_dict(questions[question_index])
    revision = int(variant.get('revision') or 0) + 1
    replacement_seed = (
        int(variant.get('seed') or 1)
        + revision * 1000003
        + (question_index + 1) * 9176
    )
    excluded = {
        item.get('fingerprint')
        for item in questions
        if item.get('fingerprint')
    }
    correct_position = ord(current_question.correct_letter) - 65
    try:
        replacement = generate_replacement_question(
            replacement_seed,
            current_question.topic,
            current_question.difficulty,
            correct_position,
            excluded,
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    replacement_payload = replacement.to_dict(question_index + 1)
    replacement_payload.update({
        'locked': False,
        'manually_edited': False,
        'original_auto': None,
    })
    questions[question_index] = replacement_payload
    variant['revision'] = revision
    draft['bank_exam_id'] = None
    rebuild_random_exam_draft(draft)
    return jsonify({
        'success': True,
        'variant_index': variant_index,
        'revision': revision,
        'question': replacement_payload,
        'message': f'Đã sinh lại câu {question_index + 1}.'
    })


@app.route(
    '/api/generate-exam/variants/<int:variant_index>/questions/<int:question_index>',
    methods=['PUT']
)
@role_required('teacher')
def api_edit_random_exam_question(variant_index, question_index):
    draft = load_random_exam_draft()
    variants = get_random_draft_variants(draft)
    if variant_index < 0 or variant_index >= len(variants):
        return jsonify({'success': False, 'message': 'Không tìm thấy mã đề trong bản nháp.'}), 404
    questions = variants[variant_index].get('questions', [])
    if question_index < 0 or question_index >= len(questions):
        return jsonify({'success': False, 'message': 'Không tìm thấy câu hỏi trong bản nháp.'}), 404
    if questions[question_index].get('locked'):
        return jsonify({'success': False, 'message': 'Hãy mở khóa câu hỏi trước khi chỉnh sửa.'}), 409

    data = request.get_json() or {}
    raw_choices = data.get('choices')
    if not isinstance(raw_choices, list) or len(raw_choices) != 4:
        return jsonify({'success': False, 'message': 'Câu hỏi phải có đúng bốn phương án.'}), 400
    choices = tuple(
        GeneratedChoice(
            tex=str(item.get('tex') or '').strip(),
            is_correct=bool(item.get('is_correct')),
            is_math=bool(item.get('is_math', True)),
        )
        for item in raw_choices
        if isinstance(item, dict)
    )
    current = questions[question_index]
    fingerprint_source = json.dumps({
        'prompt': data.get('prompt_tex'),
        'choices': raw_choices,
        'solution': data.get('solution_tex'),
    }, ensure_ascii=False, sort_keys=True)
    edited = GeneratedQuestion(
        topic=current.get('topic'),
        difficulty=current.get('difficulty'),
        prompt_tex=str(data.get('prompt_tex') or '').strip(),
        choices=choices,
        solution_tex=str(data.get('solution_tex') or '').strip(),
        fingerprint='manual-' + hashlib.sha256(fingerprint_source.encode('utf-8')).hexdigest()[:16],
    )
    try:
        validate_generated_question(edited)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    original_auto = current.get('original_auto')
    if not original_auto:
        original_auto = json.loads(json.dumps(current, ensure_ascii=False))
        original_auto.pop('original_auto', None)
    payload = edited.to_dict(question_index + 1)
    payload.update({
        'locked': False,
        'manually_edited': True,
        'original_auto': original_auto,
    })
    questions[question_index] = payload
    variants[variant_index]['revision'] = int(variants[variant_index].get('revision') or 0) + 1
    draft['bank_exam_id'] = None
    rebuild_random_exam_draft(draft)
    return jsonify({
        'success': True,
        'variant_index': variant_index,
        'question': payload,
        'revision': variants[variant_index]['revision'],
        'message': f'Đã cập nhật câu {question_index + 1}.'
    })


@app.route(
    '/api/generate-exam/variants/<int:variant_index>/questions/<int:question_index>/reset',
    methods=['POST']
)
@role_required('teacher')
def api_reset_random_exam_question(variant_index, question_index):
    draft = load_random_exam_draft()
    variants = get_random_draft_variants(draft)
    if variant_index < 0 or variant_index >= len(variants):
        return jsonify({'success': False, 'message': 'Không tìm thấy mã đề trong bản nháp.'}), 404
    questions = variants[variant_index].get('questions', [])
    if question_index < 0 or question_index >= len(questions):
        return jsonify({'success': False, 'message': 'Không tìm thấy câu hỏi trong bản nháp.'}), 404
    original = questions[question_index].get('original_auto')
    if not isinstance(original, dict):
        return jsonify({'success': False, 'message': 'Câu hỏi chưa có bản tự động để khôi phục.'}), 409

    restored = json.loads(json.dumps(original, ensure_ascii=False))
    restored.update({
        'number': question_index + 1,
        'locked': False,
        'manually_edited': False,
        'original_auto': None,
    })
    generated_question_from_dict(restored)
    questions[question_index] = restored
    variants[variant_index]['revision'] = int(variants[variant_index].get('revision') or 0) + 1
    draft['bank_exam_id'] = None
    rebuild_random_exam_draft(draft)
    return jsonify({
        'success': True,
        'variant_index': variant_index,
        'question': restored,
        'revision': variants[variant_index]['revision'],
        'message': f'Đã khôi phục câu {question_index + 1}.'
    })


def evaluate_random_exam_draft(draft):
    issues = []
    variants = get_random_draft_variants(draft)
    expected_count = None
    fingerprint_sets = []
    for variant_index, variant in enumerate(variants):
        questions = variant.get('questions', [])
        if expected_count is None:
            expected_count = len(questions)
        elif len(questions) != expected_count:
            issues.append(f'Mã đề {variant_index + 1} không có cùng số câu với mã đầu tiên.')

        fingerprints = []
        for question_index, item in enumerate(questions):
            try:
                validate_generated_question(generated_question_from_dict(item))
            except ValueError as exc:
                issues.append(f'Mã {variant_index + 1}, câu {question_index + 1}: {exc}')
            fingerprints.append(item.get('fingerprint'))
        if len(fingerprints) != len(set(fingerprints)):
            issues.append(f'Mã đề {variant_index + 1} có câu hỏi bị trùng.')
        fingerprint_sets.append(set(fingerprints))

    for left_index in range(len(fingerprint_sets)):
        for right_index in range(left_index + 1, len(fingerprint_sets)):
            overlap = len(fingerprint_sets[left_index] & fingerprint_sets[right_index])
            ratio = overlap / max(len(fingerprint_sets[left_index]), 1)
            if ratio > 0.25:
                issues.append(
                    f'Mã đề {left_index + 1} và {right_index + 1} trùng '
                    f'{round(ratio * 100)}% số câu.'
                )

    tex_content, rendered_variants = rebuild_random_exam_draft(draft)
    for variant_index, rendered in enumerate(rendered_variants):
        parsed_questions, answer_key = parse_assignment_questions(rendered['content'])
        if len(parsed_questions) != expected_count or len(answer_key) != expected_count:
            issues.append(f'Không tách được đầy đủ câu hỏi của mã đề {variant_index + 1}.')

    pdf_status = 'disabled'
    if not issues and app.config.get('PDF_QUALITY_GATE', True):
        if get_pdflatex_command():
            pdf_status = 'passed'
            _pdf_bytes, compile_error = compile_tex_pdf_bytes(tex_content)
            if compile_error:
                pdf_status = 'failed'
                issues.append('PDF không biên dịch được: ' + compile_error)
        else:
            pdf_status = 'skipped'

    return {
        'status': 'failed' if issues else 'passed',
        'issues': issues,
        'message': (
            f'Phát hiện {len(issues)} vấn đề cần sửa trước khi thêm vào ngân hàng.'
            if issues else (
                'Đã kiểm tra cấu trúc, đáp án và độ trùng. Kiểm tra PDF được bỏ qua vì chưa cài MiKTeX.'
                if pdf_status == 'skipped' else
                'Đã kiểm tra cấu trúc, đáp án, độ trùng và khả năng xuất PDF.'
            )
        ),
        'pdf_status': pdf_status,
        'tex_content': tex_content,
        'rendered_variants': rendered_variants,
    }


@app.route('/api/generate-exam/finalize', methods=['POST'])
@role_required('teacher')
def api_finalize_random_exam():
    draft = load_random_exam_draft()
    variants = get_random_draft_variants(draft)
    if not draft or not variants:
        return jsonify({'success': False, 'message': 'Chưa có đề ngẫu nhiên để thêm.'}), 404

    existing_id = draft.get('bank_exam_id')
    if existing_id:
        existing = ExamBank.query.filter_by(id=existing_id, teacher_id=current_user.id).first()
        if existing:
            return jsonify({
                'success': True,
                'message': 'Đề này đã nằm trong ngân hàng.',
                'bank_exam': serialize_exam_bank_item(existing)
            })

    quality_report = evaluate_random_exam_draft(draft)
    draft['quality_report'] = {
        'status': quality_report['status'],
        'message': quality_report['message'],
        'issues': quality_report['issues'],
        'pdf_status': quality_report.get('pdf_status'),
    }
    store_random_exam_draft(draft)
    if quality_report['status'] != 'passed':
        return jsonify({
            'success': False,
            'message': quality_report['message'],
            'quality_report': draft['quality_report'],
        }), 400

    tex_content = quality_report['tex_content']
    rendered_variants = quality_report['rendered_variants']
    topic_names = []
    for variant in variants:
        for item in variant.get('questions', []):
            label = RANDOM_EXAM_TOPIC_LABELS.get(item.get('topic'), item.get('topic') or '')
            if label and label not in topic_names:
                topic_names.append(label)
    total_revisions = sum(int(item.get('revision') or 0) for item in variants)
    bank_item = ExamBank(
        teacher_id=current_user.id,
        title=str(draft.get('title') or 'Đề thi ngẫu nhiên'),
        topic=', '.join(topic_names),
        description=(
            f'Đề sinh ngẫu nhiên từ seed {draft.get("base_seed", variants[0].get("seed"))}. '
            f'Số mã đề: {len(variants)}. Số lần chỉnh câu: {total_revisions}.'
        ),
        variants_json=json.dumps(rendered_variants, ensure_ascii=False),
        combined_tex=tex_content,
        total_variants=len(variants),
        total_questions=len(variants[0].get('questions', []))
    )
    db.session.add(bank_item)
    db.session.commit()
    draft['bank_exam_id'] = bank_item.id
    store_random_exam_draft(draft)
    return jsonify({
        'success': True,
        'message': 'Đã thêm đề ngẫu nhiên vào ngân hàng đề thi.',
        'bank_exam': serialize_exam_bank_item(bank_item),
        'quality_report': draft['quality_report'],
    })

@app.route('/download/tex')
@login_required
def download_tex():
    tex_content = load_current_exam_tex()
    if not tex_content:
        flash('Không tìm thấy đề thi. Vui lòng tạo lại.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    return Response(tex_content, mimetype="text/plain", headers={"Content-disposition": "attachment; filename=De_Thi_Giai_Tich.tex"})


def get_pdflatex_command():
    configured = os.getenv('LATEX_ENGINE', 'pdflatex').strip() or 'pdflatex'
    configured_path = Path(configured).expanduser()
    if configured_path.is_file():
        return str(configured_path.resolve())
    discovered = shutil.which(configured)
    if discovered:
        return discovered

    candidate_roots = [
        os.getenv('LOCALAPPDATA', ''),
        os.getenv('ProgramFiles', ''),
        os.getenv('ProgramFiles(x86)', ''),
    ]
    relative_candidates = (
        Path('Programs/MiKTeX/miktex/bin/x64/pdflatex.exe'),
        Path('MiKTeX/miktex/bin/x64/pdflatex.exe'),
        Path('MiKTeX 2.9/miktex/bin/x64/pdflatex.exe'),
    )
    for root in candidate_roots:
        if not root:
            continue
        for relative_path in relative_candidates:
            candidate = Path(root) / relative_path
            if candidate.is_file():
                return str(candidate.resolve())
    return None


def subprocess_no_window_options():
    if os.name != 'nt':
        return {}

    options = {}
    create_no_window = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if create_no_window:
        options['creationflags'] = create_no_window

    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, 'STARTF_USESHOWWINDOW', 0)
        startupinfo.wShowWindow = getattr(subprocess, 'SW_HIDE', 0)
        options['startupinfo'] = startupinfo
    except (AttributeError, ValueError):
        pass

    return options


def run_pdflatex_command(args, cwd, timeout=None):
    return subprocess.run(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        **subprocess_no_window_options(),
    )


def compile_tex_pdf_bytes(tex_content, timeout_seconds=60):
    with tempfile.TemporaryDirectory() as tempdir:
        tex_file_path = os.path.join(tempdir, 'exam.tex')
        pdf_file_path = os.path.join(tempdir, 'exam.pdf')
        with open(tex_file_path, 'w', encoding='utf-8') as output_file:
            output_file.write(tex_content)

        cls_path_source = str(BASE_DIR / 'fami-exam.cls')
        if os.path.exists(cls_path_source):
            shutil.copy(cls_path_source, tempdir)

        pdflatex_command = get_pdflatex_command()
        if not pdflatex_command:
            return None, 'Không tìm thấy pdflatex. Hãy cài MiKTeX hoặc cấu hình LATEX_ENGINE.'

        try:
            process = None
            for _ in range(2):
                process = run_pdflatex_command(
                    [pdflatex_command, '-interaction=nonstopmode', '-halt-on-error', 'exam.tex'],
                    cwd=tempdir,
                    timeout=timeout_seconds,
                )
                if process.returncode != 0:
                    break
        except subprocess.TimeoutExpired:
            return None, f'Biên dịch PDF vượt quá {timeout_seconds} giây.'
        except OSError as exc:
            return None, f'Không thể gọi pdflatex: {exc}'

        if process.returncode != 0 or not os.path.exists(pdf_file_path):
            log_lines = (process.stdout or '').splitlines()
            return None, '\n'.join(log_lines[-12:]) or 'pdflatex không tạo được file PDF.'
        with open(pdf_file_path, 'rb') as pdf_file:
            return pdf_file.read(), None


@app.route('/download/pdf')
@login_required
def download_pdf():
    tex_content = load_current_exam_tex()
    if not tex_content:
        flash('Không tìm thấy đề thi. Vui lòng tạo lại.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    return send_pdf_download(tex_content, 'De_Thi_Giai_Tich.pdf')

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
        cls_path_source = str(BASE_DIR / 'fami-exam.cls')
        
        if os.path.exists(cls_path_source):
            shutil.copy(cls_path_source, tempdir)
        else:
            # Thêm chữ 'r' phía trước chuỗi để fix lỗi invalid escape sequence \C
            print(r"\n[CẢNH BÁO] Không tìm thấy file fami-exam.cls ở thư mục dự án. Biên dịch có thể lỗi!\n")

        # 3. Gọi lệnh biên dịch ngay bên trong thư mục tạm
        try:
            pdflatex_command = get_pdflatex_command()
            if not pdflatex_command:
                flash('Không tìm thấy pdflatex. Hãy cài MiKTeX hoặc cấu hình LATEX_ENGINE trong file .env.', 'danger')
                return redirect(url_for('teacher_dashboard'))
            process = run_pdflatex_command(
                [pdflatex_command, '-interaction=nonstopmode', 'exam.tex'],
                cwd=tempdir,
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

    store_current_exam_tex(tex_content)
    session.pop('custom_exam', None) 
    session.modified = True
    
    return jsonify({'success': True})

# =====================================================================
# --- API GIẢNG VIÊN: XEM DANH SÁCH SINH VIÊN TRONG LỚP ---
# =====================================================================
@app.route('/api/teacher/class/<int:class_id>/students', methods=['GET'])
@teacher_owns_class
def api_get_class_students(class_id):
    target_class = g.target_class

    # Đóng gói dữ liệu sinh viên
    students_data = []
    for student in target_class.students:
        students_data.append({
            'id': student.id,
            'username': student.username,
            'full_name': student.full_name,
            'display_name': student.display_name,
            'student_code': student.student_code or '',
            'student_code_verified': bool(student.student_code_verified),
            'student_code_verified_at': (
                student.student_code_verified_at.strftime('%Y-%m-%d %H:%M')
                if student.student_code_verified_at else ''
            ),
            'school_name': student.school_name or '',
            'major': student.major or ''
        })
    pending_requests = (
        ClassJoinRequest.query
        .filter_by(class_id=target_class.id, status='pending')
        .order_by(ClassJoinRequest.requested_at.asc())
        .all()
    )

    return jsonify({
        'success': True, 
        'class_name': target_class.name,
        'class_code': target_class.code,
        'join_policy': target_class.join_policy or 'open',
        'students': students_data,
        'pending_requests': [{
            'id': item.id,
            'username': item.student.username,
            'full_name': item.student.full_name,
            'display_name': item.student.display_name,
            'student_code': item.student.student_code or '',
            'student_code_verified': bool(item.student.student_code_verified),
            'school_name': item.student.school_name or '',
            'major': item.student.major or '',
            'requested_at': item.requested_at.strftime('%Y-%m-%d %H:%M') if item.requested_at else ''
        } for item in pending_requests]
    })


@app.route('/api/teacher/class/<int:class_id>/students/<int:student_id>/verify-code', methods=['PUT'])
@teacher_owns_class
def api_verify_student_code(class_id, student_id):
    student = db.session.get(User, student_id)
    if not student or student not in g.target_class.students:
        return jsonify({'success': False, 'message': 'Sinh viên không thuộc lớp này.'}), 404
    if student.role != 'student' or not student.student_code:
        return jsonify({'success': False, 'message': 'Sinh viên chưa có mã số hợp lệ.'}), 400

    verified = parse_bool((request.get_json() or {}).get('verified'), default=True)
    student.student_code_verified = verified
    student.student_code_verified_at = app_now() if verified else None
    student.student_code_verified_by = current_user.id if verified else None
    create_notification(
        student.id,
        'Mã số sinh viên đã được xác minh' if verified else 'Xác minh mã số sinh viên đã được thu hồi',
        (
            f'Mã số {student.student_code} đã được xác minh trong lớp {g.target_class.name}.'
            if verified
            else f'Giáo viên đã thu hồi xác minh mã số {student.student_code}. Hãy kiểm tra lại hồ sơ.'
        ),
        category='success' if verified else 'warning',
        link='/profile',
        dedupe_key=(
            f'student-code-verified-{student.id}-{student.student_code}'
            if verified
            else f'student-code-unverified-{student.id}-{int(app_now().timestamp())}'
        )
    )
    db.session.commit()
    return jsonify({
        'success': True,
        'verified': verified,
        'message': 'Đã xác minh mã số sinh viên.' if verified else 'Đã thu hồi xác minh.'
    })


@app.route('/api/teacher/class/<int:class_id>/settings', methods=['PUT'])
@teacher_owns_class
def api_update_class_settings(class_id):
    data = request.get_json() or {}
    name = str(data.get('name') or '').strip()
    description = str(data.get('description') or '').strip()
    code = str(data.get('code') or '').strip().upper()
    join_policy = str(data.get('join_policy') or '').strip().lower()

    if not name:
        return jsonify({'success': False, 'message': 'Tên lớp không được để trống.'}), 400
    if not re.fullmatch(r'[A-Z0-9-]{4,20}', code):
        return jsonify({
            'success': False,
            'message': 'Mã lớp chỉ gồm chữ in hoa, số, dấu gạch ngang và dài từ 4 đến 20 ký tự.'
        }), 400
    if join_policy not in {'open', 'approval'}:
        return jsonify({'success': False, 'message': 'Chế độ tham gia lớp không hợp lệ.'}), 400

    duplicate_code = Class.query.filter(
        Class.code == code,
        Class.id != g.target_class.id
    ).first()
    if duplicate_code:
        return jsonify({'success': False, 'message': 'Mã lớp này đã được sử dụng.'}), 409

    g.target_class.name = name
    g.target_class.description = description
    g.target_class.code = code
    g.target_class.join_policy = join_policy
    db.session.commit()
    return jsonify({
        'success': True,
        'message': 'Đã cập nhật thông tin lớp học.',
        'class': {
            'id': g.target_class.id,
            'name': g.target_class.name,
            'description': g.target_class.description or '',
            'code': g.target_class.code,
            'join_policy': g.target_class.join_policy
        }
    })


@app.route('/api/teacher/class/<int:class_id>/students/<int:student_id>', methods=['DELETE'])
@teacher_owns_class
def api_remove_class_student(class_id, student_id):
    student = db.session.get(User, student_id)
    if not student or student not in g.target_class.students:
        return jsonify({'success': False, 'message': 'Sinh viên không còn thuộc lớp này.'}), 404

    g.target_class.students.remove(student)
    join_request = ClassJoinRequest.query.filter_by(
        class_id=class_id,
        student_id=student_id
    ).first()
    if join_request:
        db.session.delete(join_request)
    create_notification(
        student.id,
        'Đã rời khỏi lớp học',
        f'Bạn đã được xóa khỏi lớp {g.target_class.name}.',
        category='warning',
        link='#tab-my-classes',
        dedupe_key=f'class-removed-{class_id}-{student_id}-{int(app_now().timestamp())}'
    )
    db.session.commit()
    return jsonify({'success': True, 'message': 'Đã xóa sinh viên khỏi lớp.'})


@app.route('/api/teacher/class/<int:class_id>/join-requests/<int:request_id>/<action>', methods=['POST'])
@teacher_owns_class
def api_review_class_join_request(class_id, request_id, action):
    if action not in {'approve', 'reject'}:
        return jsonify({'success': False, 'message': 'Thao tác không hợp lệ.'}), 400

    join_request = ClassJoinRequest.query.filter_by(
        id=request_id,
        class_id=class_id,
        status='pending'
    ).first()
    if not join_request:
        return jsonify({'success': False, 'message': 'Yêu cầu không tồn tại hoặc đã được xử lý.'}), 404

    if action == 'approve':
        if join_request.student not in g.target_class.students:
            g.target_class.students.append(join_request.student)
        join_request.status = 'approved'
        award_student_badges(join_request.student)
        notification_title = 'Yêu cầu vào lớp đã được duyệt'
        notification_message = f'Bạn đã được thêm vào lớp {g.target_class.name}.'
        response_message = 'Đã duyệt sinh viên vào lớp.'
    else:
        join_request.status = 'rejected'
        notification_title = 'Yêu cầu vào lớp bị từ chối'
        notification_message = f'Yêu cầu tham gia lớp {g.target_class.name} chưa được chấp nhận.'
        response_message = 'Đã từ chối yêu cầu tham gia.'

    join_request.responded_at = app_now()
    create_notification(
        join_request.student_id,
        notification_title,
        notification_message,
        category='success' if action == 'approve' else 'warning',
        link='#tab-my-classes',
        dedupe_key=f'class-join-{action}-{join_request.id}'
    )
    db.session.commit()
    return jsonify({'success': True, 'message': response_message})


# =====================================================================
# --- API KẾT NỐI: HỌC SINH THAM GIA LỚP HỌC (DATABASE THẬT) ---
# =====================================================================
@app.route('/api/student/join-class', methods=['POST'])
@role_required('student')
def student_join_class():
    class_code = request.form.get('class_code', '').strip().upper()

    if not class_code:
        return jsonify({'success': False, 'message': 'Vui lòng nhập mã lớp!'})

    # 1. Truy vấn Database xem mã lớp có tồn tại không
    target_class = Class.query.filter_by(code=class_code).first()

    if not target_class:
        return jsonify({'success': False, 'message': 'Mã lớp không tồn tại hoặc đã bị xóa.'})

    if current_user in target_class.students:
        return jsonify({'success': False, 'message': 'Bạn đã tham gia lớp học này rồi!'})

    teacher = db.session.get(User, target_class.teacher_id)
    teacher_name = teacher.display_name if teacher else 'Giáo viên'
    existing_request = ClassJoinRequest.query.filter_by(
        class_id=target_class.id,
        student_id=current_user.id
    ).first()

    if target_class.join_policy == 'approval':
        if existing_request and existing_request.status == 'pending':
            return jsonify({
                'success': True,
                'pending': True,
                'message': 'Yêu cầu của bạn đang chờ giáo viên duyệt.'
            })
        if existing_request:
            existing_request.status = 'pending'
            existing_request.requested_at = app_now()
            existing_request.responded_at = None
        else:
            existing_request = ClassJoinRequest(
                class_id=target_class.id,
                student_id=current_user.id,
                status='pending'
            )
            db.session.add(existing_request)
        create_notification(
            target_class.teacher_id,
            'Có yêu cầu tham gia lớp',
            f'{current_user.display_name} ({current_user.student_code or "chưa có mã SV"}) muốn tham gia lớp {target_class.name}.',
            category='info',
            link='#tab-class-management',
            dedupe_key=f'class-join-request-{target_class.id}-{current_user.id}-{int(app_now().timestamp())}'
        )
        db.session.commit()
        return jsonify({
            'success': True,
            'pending': True,
            'message': 'Đã gửi yêu cầu. Bạn cần chờ giáo viên duyệt.',
            'class_info': {
                'code': target_class.code,
                'name': target_class.name,
                'teacher': teacher_name
            }
        })

    target_class.students.append(current_user)
    if existing_request:
        existing_request.status = 'approved'
        existing_request.responded_at = app_now()
    award_student_badges(current_user)
    db.session.commit()
    return jsonify({
        'success': True,
        'pending': False,
        'message': 'Tham gia thành công!',
        'class_info': {
            'code': target_class.code,
            'name': target_class.name,
            'teacher': teacher_name
        }
    })


@app.route('/api/student/join-requests/<int:request_id>', methods=['DELETE'])
@role_required('student')
def api_cancel_class_join_request(request_id):
    join_request = ClassJoinRequest.query.filter_by(
        id=request_id,
        student_id=current_user.id,
        status='pending'
    ).first()
    if not join_request:
        return jsonify({
            'success': False,
            'message': 'Yêu cầu không tồn tại hoặc đã được xử lý.'
        }), 404

    join_request.status = 'canceled'
    join_request.responded_at = app_now()
    create_notification(
        join_request.classroom.teacher_id,
        'Sinh viên đã hủy yêu cầu vào lớp',
        f'{current_user.display_name} đã hủy yêu cầu tham gia lớp {join_request.classroom.name}.',
        category='secondary',
        link=f'/teacher/classes/{join_request.class_id}#classMembers',
        dedupe_key=f'class-join-canceled-{join_request.id}-{int(app_now().timestamp())}'
    )
    db.session.commit()
    return jsonify({'success': True, 'message': 'Đã hủy yêu cầu tham gia lớp.'})


@app.route('/api/student/classes/<int:class_id>', methods=['DELETE'])
@role_required('student')
def api_student_leave_class(class_id):
    target_class = db.get_or_404(Class, class_id)
    if current_user not in target_class.students:
        return jsonify({'success': False, 'message': 'Bạn không còn thuộc lớp này.'}), 404

    target_class.students.remove(current_user)
    join_request = ClassJoinRequest.query.filter_by(
        class_id=class_id,
        student_id=current_user.id
    ).first()
    if join_request:
        join_request.status = 'left'
        join_request.responded_at = app_now()
    create_notification(
        target_class.teacher_id,
        'Sinh viên đã rời lớp',
        f'{current_user.display_name} ({current_user.student_code or "chưa có mã SV"}) '
        f'đã rời lớp {target_class.name}.',
        category='warning',
        link=f'/teacher/classes/{target_class.id}#classMembers',
        dedupe_key=f'class-left-{target_class.id}-{current_user.id}-{int(app_now().timestamp())}'
    )
    db.session.commit()
    return jsonify({
        'success': True,
        'message': 'Đã rời lớp. Các bài đã nộp và điểm cũ vẫn được lưu.'
    })


def serialize_exam_bank_item(item, include_variants=False):
    data = {
        'id': item.id,
        'title': item.title,
        'topic': item.topic or '',
        'week_number': item.week_number,
        'description': item.description or '',
        'total_variants': item.total_variants,
        'total_questions': item.total_questions,
        'created_at': item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else '',
        'updated_at': item.updated_at.strftime('%Y-%m-%d %H:%M') if item.updated_at else ''
    }
    if include_variants:
        try:
            data['variants'] = json.loads(item.variants_json or '[]')
        except json.JSONDecodeError:
            data['variants'] = []
    return data


@app.route('/api/exam-bank', methods=['GET'])
@login_required
def api_exam_bank_list():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    items = (
        ExamBank.query
        .filter_by(teacher_id=current_user.id)
        .order_by(ExamBank.created_at.desc(), ExamBank.id.desc())
        .all()
    )
    return jsonify({
        'success': True,
        'exams': [serialize_exam_bank_item(item) for item in items]
    })


@app.route('/api/exam-bank/<int:exam_id>', methods=['GET'])
@login_required
def api_exam_bank_detail(exam_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'message': 'Không tìm thấy đề trong ngân hàng.'}), 404

    return jsonify({
        'success': True,
        'exam': serialize_exam_bank_item(item, include_variants=True)
    })


@app.route('/api/exam-bank/<int:exam_id>', methods=['PUT'])
@login_required
def api_exam_bank_update(exam_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'message': 'Không tìm thấy đề trong ngân hàng.'}), 404

    data = request.get_json() or {}
    title = str(data.get('title') or '').strip()
    topic = str(data.get('topic') or '').strip()
    description = str(data.get('description') or '').strip()
    week_raw = str(data.get('week_number') or '').strip()

    if not title:
        return jsonify({'success': False, 'message': 'Vui lòng nhập tên đề.'})

    week_number = None
    if week_raw:
        try:
            week_number = int(week_raw)
            if week_number < 1:
                return jsonify({'success': False, 'message': 'Tuần phải là số nguyên dương.'})
        except ValueError:
            return jsonify({'success': False, 'message': 'Tuần phải là số nguyên.'})

    item.title = title
    item.topic = topic
    item.description = description
    item.week_number = week_number
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Đã cập nhật thông tin đề.',
        'exam': serialize_exam_bank_item(item, include_variants=True)
    })


@app.route('/api/exam-bank/<int:exam_id>', methods=['DELETE'])
@login_required
def api_exam_bank_delete(exam_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'message': 'Không tìm thấy đề trong ngân hàng.'}), 404

    db.session.delete(item)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Đã xóa đề khỏi ngân hàng.'
    })


def get_exam_bank_variant(item, variant_index):
    try:
        variants = json.loads(item.variants_json or '[]')
    except json.JSONDecodeError:
        variants = []

    if variant_index < 0 or variant_index >= len(variants):
        return None

    variant = variants[variant_index]
    if not isinstance(variant, dict) or not str(variant.get('content') or '').strip():
        return None
    return variant


def save_export_response(tex_content, filename, file_format):
    try:
        if file_format == 'tex':
            output_path = save_tex_to_export_dir(tex_content, filename)
        elif file_format == 'pdf':
            output_path = save_pdf_to_export_dir(tex_content, filename)
        else:
            return jsonify({'success': False, 'message': 'Định dạng xuất file không hợp lệ.'}), 400
    except RuntimeError as exc:
        return jsonify({'success': False, 'message': f'Không xuất được PDF: {exc}'}), 400
    except OSError as exc:
        return jsonify({'success': False, 'message': f'Không ghi được file vào thư mục xuất: {exc}'}), 500

    return jsonify(export_file_payload(output_path))


@app.route('/api/export/settings', methods=['GET', 'POST'])
@role_required('teacher')
def api_export_settings():
    if request.method == 'GET':
        try:
            export_dir = get_user_export_dir()
        except OSError as exc:
            return jsonify({'success': False, 'message': f'Không mở được thư mục xuất: {exc}'}), 500
        return jsonify({
            'success': True,
            'export_dir': str(export_dir),
            'default_export_dir': str(get_default_export_dir()),
        })

    data = request.get_json(silent=True) or request.form
    raw_path = str(data.get('export_dir') or '').strip()
    if not raw_path:
        return jsonify({'success': False, 'message': 'Vui lòng nhập thư mục xuất file.'}), 400

    try:
        export_dir = set_user_export_dir(raw_path)
    except (OSError, ValueError) as exc:
        return jsonify({'success': False, 'message': f'Không lưu được thư mục xuất: {exc}'}), 400

    return jsonify({
        'success': True,
        'export_dir': str(export_dir),
        'message': f'Đã đặt thư mục xuất file: {export_dir}',
    })


@app.route('/api/export/open-folder', methods=['POST'])
@role_required('teacher')
def api_open_export_folder():
    try:
        export_dir = get_user_export_dir()
    except OSError as exc:
        return jsonify({'success': False, 'message': f'Không mở được thư mục xuất: {exc}'}), 500

    opened = False
    if os.name == 'nt' and not app.config.get('TESTING'):
        try:
            os.startfile(str(export_dir))
            opened = True
        except OSError as exc:
            return jsonify({'success': False, 'message': f'Không mở được thư mục xuất: {exc}'}), 500

    return jsonify({
        'success': True,
        'opened': opened,
        'directory': str(export_dir),
        'message': 'Đã mở thư mục xuất.' if opened else f'Thư mục xuất: {export_dir}',
    })


@app.route('/api/export/current/<file_format>', methods=['POST'])
@role_required('teacher')
def api_export_current_exam(file_format):
    tex_content = load_current_exam_tex()
    if not tex_content:
        return jsonify({'success': False, 'message': 'Không tìm thấy đề thi. Vui lòng tạo lại.'}), 404

    filename = 'De_Thi_Giai_Tich.tex' if file_format == 'tex' else 'De_Thi_Giai_Tich.pdf'
    return save_export_response(tex_content, filename, file_format)


@app.route('/api/export/exam-bank/<int:exam_id>/<file_format>', methods=['POST'])
@role_required('teacher')
def api_export_exam_bank(exam_id, file_format):
    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'message': 'Không tìm thấy đề trong ngân hàng.'}), 404

    filename = f'exam_bank_{item.id}.{file_format}'
    return save_export_response(item.combined_tex, filename, file_format)


@app.route('/api/export/exam-bank/<int:exam_id>/variant/<int:variant_index>/<file_format>', methods=['POST'])
@role_required('teacher')
def api_export_exam_bank_variant(exam_id, variant_index, file_format):
    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first()
    if not item:
        return jsonify({'success': False, 'message': 'Không tìm thấy đề trong ngân hàng.'}), 404

    variant = get_exam_bank_variant(item, variant_index)
    if not variant:
        return jsonify({'success': False, 'message': 'Không tìm thấy mã đề trong ngân hàng.'}), 404

    seed = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(variant.get('seed', variant_index + 1)))
    filename = f'exam_bank_{item.id}_seed_{seed}.{file_format}'
    return save_export_response(variant['content'], filename, file_format)


def send_tex_download(tex_content, filename):
    return Response(
        tex_content,
        mimetype='text/plain',
        headers={'Content-disposition': f'attachment; filename={filename}'}
    )


def send_pdf_download(tex_content, filename):
    pdf_bytes, compile_error = compile_tex_pdf_bytes(tex_content)
    if compile_error:
        flash(f'Không xuất được PDF: {compile_error}', 'danger')
        return redirect(url_for('teacher_dashboard'))

    return send_file(
        __import__('io').BytesIO(pdf_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf',
    )

    import tempfile
    import subprocess
    import io
    import shutil

    pdf_memory = None

    with tempfile.TemporaryDirectory() as tempdir:
        tex_file_path = os.path.join(tempdir, 'exam.tex')
        pdf_file_path = os.path.join(tempdir, 'exam.pdf')

        with open(tex_file_path, 'w', encoding='utf-8') as f:
            f.write(tex_content)

        cls_path_source = str(BASE_DIR / 'fami-exam.cls')
        if os.path.exists(cls_path_source):
            shutil.copy(cls_path_source, tempdir)

        try:
            pdflatex_command = get_pdflatex_command()
            if not pdflatex_command:
                flash('Không tìm thấy pdflatex. Hãy cài MiKTeX hoặc cấu hình LATEX_ENGINE trong file .env.', 'danger')
                return redirect(url_for('teacher_dashboard'))
            process = run_pdflatex_command(
                [pdflatex_command, '-interaction=nonstopmode', 'exam.tex'],
                cwd=tempdir,
            )
            if process.returncode != 0:
                print('\n=== LATEX COMPILE ERROR ===')
                print('\n'.join(process.stdout.split('\n')[-20:]))
                print('===========================\n')
                flash('Có lỗi cú pháp LaTeX khi xuất PDF từ ngân hàng đề.', 'danger')
                return redirect(url_for('teacher_dashboard'))

            with open(pdf_file_path, 'rb') as f:
                pdf_memory = io.BytesIO(f.read())
        except Exception as e:
            print('Lỗi hệ thống khi gọi pdflatex:', str(e))
            flash('Không thể gọi pdflatex. Hãy kiểm tra MiKTeX và biến môi trường PATH.', 'danger')
            return redirect(url_for('teacher_dashboard'))

    if not pdf_memory:
        flash('Lỗi đọc file PDF.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    pdf_memory.seek(0)
    return send_file(pdf_memory, as_attachment=True, download_name=filename, mimetype='application/pdf')


@app.route('/download/exam-bank/<int:exam_id>/tex')
@login_required
def download_exam_bank_tex(exam_id):
    if current_user.role != 'teacher':
        return redirect(url_for('teacher_dashboard'))

    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first_or_404()
    return send_tex_download(item.combined_tex, f'exam_bank_{item.id}.tex')


@app.route('/download/exam-bank/<int:exam_id>/pdf')
@login_required
def download_exam_bank_pdf(exam_id):
    if current_user.role != 'teacher':
        return redirect(url_for('teacher_dashboard'))

    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first_or_404()
    return send_pdf_download(item.combined_tex, f'exam_bank_{item.id}.pdf')


@app.route('/download/exam-bank/<int:exam_id>/variant/<int:variant_index>/tex')
@login_required
def download_exam_bank_variant_tex(exam_id, variant_index):
    if current_user.role != 'teacher':
        return redirect(url_for('teacher_dashboard'))

    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first_or_404()
    variant = get_exam_bank_variant(item, variant_index)
    if not variant:
        abort(404)

    seed = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(variant.get('seed', variant_index + 1)))
    return send_tex_download(variant['content'], f'exam_bank_{item.id}_seed_{seed}.tex')


@app.route('/download/exam-bank/<int:exam_id>/variant/<int:variant_index>/pdf')
@login_required
def download_exam_bank_variant_pdf(exam_id, variant_index):
    if current_user.role != 'teacher':
        return redirect(url_for('teacher_dashboard'))

    item = ExamBank.query.filter_by(id=exam_id, teacher_id=current_user.id).first_or_404()
    variant = get_exam_bank_variant(item, variant_index)
    if not variant:
        abort(404)

    seed = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(variant.get('seed', variant_index + 1)))
    return send_pdf_download(variant['content'], f'exam_bank_{item.id}_seed_{seed}.pdf')


@app.route('/api/teacher/assignments', methods=['POST'])
@role_required('teacher')
def api_teacher_create_assignment():
    data = request.get_json() or {}
    class_id = data.get('class_id')
    class_ids = data.get('class_ids')
    title = str(data.get('title') or '').strip()
    description = str(data.get('description') or '').strip()
    content_tex = str(data.get('content_tex') or '').strip()
    opens_at = parse_due_at(data.get('opens_at'))
    due_at = parse_due_at(data.get('due_at'))
    duration_raw = str(data.get('duration_minutes') or '').strip()
    duration_minutes = None
    if duration_raw:
        try:
            duration_minutes = int(duration_raw)
            if duration_minutes < 1:
                return jsonify({'success': False, 'message': 'Thời lượng phải lớn hơn 0 phút.'})
        except ValueError:
            return jsonify({'success': False, 'message': 'Thời lượng phải là số nguyên.'})
    score_policy = str(data.get('score_policy') or 'latest').strip().lower()
    if score_policy not in {'first', 'latest', 'highest'}:
        score_policy = 'latest'
    try:
        assignment_policy = parse_assignment_policy(data)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    shuffle_questions = parse_bool(data.get('shuffle_questions'))
    shuffle_answers = parse_bool(data.get('shuffle_answers'))

    if class_ids is None:
        class_ids = [class_id] if class_id else []
    if not isinstance(class_ids, list):
        class_ids = [class_ids]

    normalized_class_ids = []
    for raw_id in class_ids:
        try:
            parsed_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if parsed_id not in normalized_class_ids:
            normalized_class_ids.append(parsed_id)

    if not normalized_class_ids:
        return jsonify({'success': False, 'message': 'Vui lòng chọn lớp để giao bài.'})
    if not title:
        return jsonify({'success': False, 'message': 'Vui lòng nhập tiêu đề bài tập.'})
    if not content_tex:
        return jsonify({'success': False, 'message': 'Chưa có đề để giao. Hãy bấm Xem trước trong phần sinh đề tùy chỉnh trước.'})

    if opens_at and due_at and opens_at >= due_at:
        return jsonify({'success': False, 'message': 'Thời gian mở đề phải trước hạn đóng đề.'})
    if assignment_policy['result_release_policy'] == 'after_due' and not due_at:
        return jsonify({
            'success': False,
            'message': 'Cần đặt hạn đóng đề khi chọn công bố kết quả sau hạn.'
        }), 400

    target_classes = Class.query.filter(Class.id.in_(normalized_class_ids)).all()
    if len(target_classes) != len(normalized_class_ids):
        return jsonify({'success': False, 'message': 'Có lớp không tồn tại hoặc đã bị xóa.'}), 404
    if any(target_class.teacher_id != current_user.id for target_class in target_classes):
        return jsonify({'success': False, 'message': 'Bạn không có quyền giao bài cho một hoặc nhiều lớp đã chọn.'}), 403

    questions, answer_key = parse_assignment_questions(content_tex)
    if not questions or not answer_key:
        return jsonify({
            'success': False,
            'message': 'Không tách được câu hỏi trắc nghiệm từ đề này. Hãy kiểm tra đề đã có \\AutoText hoặc \\Dung/\\Sai chưa.'
        })

    assignments = []
    for target_class in target_classes:
        assignment = Assignment(
            class_id=target_class.id,
            teacher_id=current_user.id,
            title=title,
            description=description,
            opens_at=opens_at,
            due_at=due_at,
            duration_minutes=duration_minutes,
            allow_retry=assignment_policy['allow_retry'],
            max_attempts=assignment_policy['max_attempts'],
            retry_cooldown_minutes=assignment_policy['retry_cooldown_minutes'],
            score_policy=score_policy,
            result_release_policy=assignment_policy['result_release_policy'],
            results_published=assignment_policy['results_published'],
            show_answers=assignment_policy['show_answers'],
            shuffle_questions=shuffle_questions,
            shuffle_answers=shuffle_answers,
            version_hash=hashlib.sha256(content_tex.encode('utf-8')).hexdigest()[:16],
            content_tex=content_tex,
            questions_json=json.dumps(questions, ensure_ascii=False),
            answer_key_json=json.dumps(answer_key, ensure_ascii=False),
            total_questions=len(questions),
            is_active=True
        )
        db.session.add(assignment)
        assignments.append(assignment)
    db.session.commit()

    for assignment in assignments:
        for student in assignment.classroom.students:
            create_notification(
                student.id,
                'Có bài tập mới',
                f'{assignment.title} đã được giao cho lớp {assignment.classroom.name}.',
                category='info',
                link='#tab-practice',
                dedupe_key=f'assignment-created-{assignment.id}'
            )
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Đã giao bài tập cho {len(assignments)} lớp.',
        'assignment': serialize_assignment_summary(assignments[0]) if assignments else None,
        'assignments': [serialize_assignment_summary(item, include_class=True) for item in assignments]
    })


@app.route('/api/teacher/class/<int:class_id>/assignments', methods=['GET'])
@teacher_owns_class
def api_teacher_class_assignments(class_id):
    assignments = (
        Assignment.query
        .filter_by(class_id=class_id, teacher_id=current_user.id)
        .order_by(Assignment.created_at.desc(), Assignment.id.desc())
        .all()
    )

    return jsonify({
        'success': True,
        'assignments': [serialize_assignment_summary(item) for item in assignments]
    })


@app.route('/api/teacher/assignments/<int:assignment_id>', methods=['PUT'])
@teacher_owns_assignment
def api_teacher_update_assignment(assignment_id):
    assignment = g.assignment
    was_result_published = is_assignment_result_published(assignment)

    data = request.get_json() or {}
    title = str(data.get('title') or assignment.title).strip()
    if not title:
        return jsonify({'success': False, 'message': 'Vui lòng nhập tiêu đề bài.'})

    opens_at = parse_due_at(data.get('opens_at'))
    due_at = parse_due_at(data.get('due_at'))
    duration_raw = str(data.get('duration_minutes') or '').strip()
    duration_minutes = None
    if duration_raw:
        try:
            duration_minutes = int(duration_raw)
            if duration_minutes < 1:
                return jsonify({'success': False, 'message': 'Thời lượng phải lớn hơn 0 phút.'})
        except ValueError:
            return jsonify({'success': False, 'message': 'Thời lượng phải là số nguyên.'})

    if opens_at and due_at and opens_at >= due_at:
        return jsonify({'success': False, 'message': 'Thời gian mở đề phải trước hạn đóng đề.'})

    assignment.title = title
    assignment.description = str(data.get('description') or '').strip()
    assignment.opens_at = opens_at
    assignment.due_at = due_at
    assignment.duration_minutes = duration_minutes
    try:
        assignment_policy = parse_assignment_policy(data, assignment)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    if assignment_policy['result_release_policy'] == 'after_due' and not due_at:
        return jsonify({
            'success': False,
            'message': 'Cần đặt hạn đóng đề khi chọn công bố kết quả sau hạn.'
        }), 400
    assignment.allow_retry = assignment_policy['allow_retry']
    assignment.max_attempts = assignment_policy['max_attempts']
    assignment.retry_cooldown_minutes = assignment_policy['retry_cooldown_minutes']
    assignment.result_release_policy = assignment_policy['result_release_policy']
    assignment.results_published = assignment_policy['results_published']
    assignment.show_answers = assignment_policy['show_answers']
    score_policy = str(data.get('score_policy') or assignment.score_policy or 'latest').strip().lower()
    if score_policy not in {'first', 'latest', 'highest'}:
        return jsonify({'success': False, 'message': 'Chính sách tính điểm không hợp lệ.'}), 400
    score_policy_changed = score_policy != (assignment.score_policy or 'latest')
    assignment.score_policy = score_policy
    assignment.shuffle_questions = parse_bool(data.get('shuffle_questions'))
    assignment.shuffle_answers = parse_bool(data.get('shuffle_answers'))
    assignment.is_active = parse_bool(data.get('is_active'), default=assignment.is_active)
    if score_policy_changed:
        for student in assignment.classroom.students:
            refresh_submission_from_attempts(assignment, student.id)
    if not was_result_published and is_assignment_result_published(assignment):
        for student in assignment.classroom.students:
            create_notification(
                student.id,
                'Kết quả bài tập đã được công bố',
                f'Bạn có thể xem kết quả của bài “{assignment.title}”.',
                category='success',
                link='#tab-practice',
                dedupe_key=f'assignment-results-published-{assignment.id}'
            )
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Đã cập nhật bài được giao.',
        'assignment': serialize_assignment_summary(assignment, include_class=True)
    })


@app.route('/api/teacher/assignments/<int:assignment_id>', methods=['DELETE'])
@teacher_owns_assignment
def api_teacher_delete_assignment(assignment_id):
    assignment = g.assignment

    db.session.delete(assignment)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Đã xóa bài được giao.'})


@app.route('/api/teacher/assignments/<int:assignment_id>/results', methods=['GET'])
@teacher_owns_assignment
def api_teacher_assignment_results(assignment_id):
    assignment = g.assignment

    submissions_by_student = {submission.student_id: submission for submission in assignment.submissions}
    answer_key = json.loads(assignment.answer_key_json or '[]')
    original_questions = json.loads(assignment.questions_json or '[]')
    attempts_by_student = {
        student_id: get_submission_attempt(assignment, submission)
        for student_id, submission in submissions_by_student.items()
    }
    submitted_submissions = list(submissions_by_student.values())
    total_students = len(assignment.classroom.students)
    submitted_count = len(submitted_submissions)
    scores = [submission.score for submission in submitted_submissions]
    average_score = round(sum(scores) / len(scores), 2) if scores else 0
    question_accumulators = [{
        'correct_count': 0,
        'option_counts': {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'blank': 0}
    } for _ in answer_key]

    for submission in submitted_submissions:
        attempt = attempts_by_student.get(submission.student_id)
        attempt_questions = json.loads(attempt.questions_json or '[]') if attempt else original_questions
        attempt_key = json.loads(attempt.answer_key_json or '[]') if attempt else answer_key
        answers = json.loads(submission.answers_json or '{}')
        for display_idx, question in enumerate(attempt_questions):
            source_idx = int(question.get('source_number') or display_idx + 1) - 1
            if source_idx < 0 or source_idx >= len(question_accumulators):
                continue
            selected = str(answers.get(str(display_idx)) or '').strip().upper()
            correct_letter = attempt_key[display_idx] if display_idx < len(attempt_key) else ''
            count_key = selected if selected in {'A', 'B', 'C', 'D'} else 'blank'
            question_accumulators[source_idx]['option_counts'][count_key] += 1
            if selected and selected == correct_letter:
                question_accumulators[source_idx]['correct_count'] += 1

    question_stats = []
    for idx, accumulator in enumerate(question_accumulators):
        correct_count = accumulator['correct_count']
        correct_rate = round((correct_count / submitted_count) * 100, 1) if submitted_count else 0
        if submitted_count < 3:
            diagnostic = 'Chưa đủ dữ liệu'
        elif correct_rate >= 90:
            diagnostic = 'Có thể quá dễ'
        elif correct_rate <= 20:
            diagnostic = 'Cần kiểm tra đáp án/độ khó'
        else:
            diagnostic = 'Phù hợp'
        question_stats.append({
            'question_number': idx + 1,
            'correct_answer': answer_key[idx] if idx < len(answer_key) else '',
            'correct_count': correct_count,
            'wrong_count': submitted_count - correct_count,
            'correct_rate': correct_rate,
            'diagnostic': diagnostic,
            'option_counts': accumulator['option_counts']
        })
    rows = []
    for student in assignment.classroom.students:
        submission = submissions_by_student.get(student.id)
        question_details = []
        attempt_history = []
        completed_attempts = (
            AssignmentAttempt.query
            .filter_by(assignment_id=assignment.id, student_id=student.id, is_completed=True)
            .order_by(AssignmentAttempt.attempt_number.asc(), AssignmentAttempt.id.asc())
            .all()
        )
        for history_attempt in completed_attempts:
            attempt_history.append({
                'attempt_id': history_attempt.id,
                'attempt_number': history_attempt.attempt_number,
                'score': history_attempt.score,
                'correct_count': history_attempt.correct_count,
                'total_questions': history_attempt.total_questions or assignment.total_questions,
                'started_at': history_attempt.started_at.strftime('%Y-%m-%d %H:%M:%S') if history_attempt.started_at else '',
                'completed_at': history_attempt.completed_at.strftime('%Y-%m-%d %H:%M:%S') if history_attempt.completed_at else '',
                'completion_reason': history_attempt.completion_reason or 'submitted',
                'is_selected': bool(submission and submission.selected_attempt_id == history_attempt.id)
            })
        if submission:
            attempt = attempts_by_student.get(student.id)
            attempt_questions = json.loads(attempt.questions_json or '[]') if attempt else original_questions
            attempt_key = json.loads(attempt.answer_key_json or '[]') if attempt else answer_key
            submitted_answers = json.loads(submission.answers_json or '{}')
            for display_idx, question in enumerate(attempt_questions):
                selected = str(submitted_answers.get(str(display_idx)) or '').strip().upper()
                correct_letter = attempt_key[display_idx] if display_idx < len(attempt_key) else ''
                question_details.append({
                    'question_number': display_idx + 1,
                    'source_number': question.get('source_number', display_idx + 1),
                    'prompt': question.get('prompt', ''),
                    'choices': question.get('choices', []),
                    'selected_answer': selected,
                    'correct_answer': correct_letter,
                    'is_correct': bool(selected and selected == correct_letter)
                })
        rows.append({
            'student_id': student.id,
            'username': student.username,
            'full_name': student.full_name,
            'display_name': student.display_name,
            'submitted': bool(submission),
            'score': submission.score if submission else None,
            'correct_count': submission.correct_count if submission else None,
            'total_questions': assignment.total_questions,
            'answers': json.loads(submission.answers_json or '{}') if submission else {},
            'question_details': question_details,
            'attempt_history': attempt_history,
            'submitted_at': submission.submitted_at.strftime('%Y-%m-%d %H:%M') if submission else ''
        })

    return jsonify({
        'success': True,
        'assignment': serialize_assignment_summary(assignment, include_class=True),
        'summary': {
            'total_students': total_students,
            'submitted_count': submitted_count,
            'missing_count': max(total_students - submitted_count, 0),
            'submitted_rate': round((submitted_count / total_students) * 100, 1) if total_students else 0,
            'average_score': average_score,
            'highest_score': max(scores) if scores else None,
            'lowest_score': min(scores) if scores else None
        },
        'question_stats': question_stats,
        'results': rows
    })


@app.route('/api/teacher/assignments/<int:assignment_id>/results.csv', methods=['GET'])
@teacher_owns_assignment
def api_teacher_assignment_results_csv(assignment_id):
    assignment = g.assignment

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Sinh viên', 'Trạng thái', 'Điểm ghi nhận', 'Số câu đúng',
        'Tổng số câu', 'Số lượt làm', 'Lượt được tính', 'Thời gian nộp'
    ])
    for student in assignment.classroom.students:
        submission = Submission.query.filter_by(
            assignment_id=assignment.id,
            student_id=student.id
        ).first()
        completed_attempts = AssignmentAttempt.query.filter_by(
            assignment_id=assignment.id,
            student_id=student.id,
            is_completed=True
        ).count()
        selected_attempt = get_submission_attempt(assignment, submission)
        writer.writerow([
            student.display_name,
            'Đã nộp' if submission else 'Chưa nộp',
            submission.score if submission else '',
            submission.correct_count if submission else '',
            submission.total_questions if submission else assignment.total_questions,
            completed_attempts,
            selected_attempt.attempt_number if selected_attempt else '',
            submission.submitted_at.strftime('%d/%m/%Y %H:%M:%S') if submission and submission.submitted_at else ''
        ])

    filename = re.sub(r'[^a-zA-Z0-9_-]+', '_', assignment.title).strip('_') or f'assignment_{assignment.id}'
    csv_content = '\ufeff' + output.getvalue()
    return Response(
        csv_content,
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}_results.csv"'}
    )


@app.route('/api/student/assignments', methods=['GET'])
@role_required('student')
def api_student_assignments():
    class_ids = [item.id for item in current_user.enrolled_classes]
    if not class_ids:
        return jsonify({'success': True, 'assignments': []})

    ensure_due_notifications(current_user)
    db.session.commit()

    assignments = (
        Assignment.query
        .filter(Assignment.class_id.in_(class_ids))
        .order_by(Assignment.created_at.desc(), Assignment.id.desc())
        .all()
    )

    return jsonify({
        'success': True,
        'assignments': [
            serialize_assignment_summary(item, include_class=True, student_id=current_user.id)
            for item in assignments
        ]
    })


@app.route('/api/calendar-events', methods=['GET'])
@login_required
def api_calendar_events():
    if current_user.role == 'student':
        ensure_due_notifications(current_user)
        db.session.commit()

    class_id = request.args.get('class_id', type=int)
    status_filter = str(request.args.get('status') or '').strip().lower()
    start_raw = str(request.args.get('start') or '').strip()
    end_raw = str(request.args.get('end') or '').strip()
    start_date = None
    end_date = None
    try:
        if start_raw:
            start_date = datetime.strptime(start_raw, '%Y-%m-%d')
        if end_raw:
            end_date = datetime.strptime(end_raw, '%Y-%m-%d') + timedelta(days=1)
    except ValueError:
        return jsonify({'success': False, 'message': 'Khoảng thời gian không hợp lệ.'}), 400

    items = []
    unscheduled = []
    for assignment in get_calendar_assignments(current_user):
        data = serialize_calendar_assignment(assignment, current_user)
        if class_id and data['class_id'] != class_id:
            continue
        if status_filter and status_filter != 'all' and data['status'] != status_filter:
            continue

        dates = [value for value in (assignment.opens_at, assignment.due_at) if value]
        if not dates:
            unscheduled.append(data)
            continue
        if start_date and max(dates) < start_date:
            continue
        if end_date and min(dates) >= end_date:
            continue
        items.append(data)

    items.sort(key=lambda item: item['due_at'] or item['opens_at'] or '9999')
    return jsonify({
        'success': True,
        'assignments': items,
        'unscheduled': unscheduled,
        'generated_at': app_now().strftime('%Y-%m-%dT%H:%M')
    })


@app.route('/api/notifications', methods=['GET'])
@login_required
def api_notifications():
    if current_user.role == 'student':
        ensure_due_notifications(current_user)
        db.session.commit()
    items = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(30)
        .all()
    )
    return jsonify({
        'success': True,
        'unread_count': sum(1 for item in items if not item.is_read),
        'notifications': [{
            'id': item.id,
            'title': item.title,
            'message': item.message,
            'category': item.category or 'info',
            'link': item.link or '',
            'is_read': bool(item.is_read),
            'created_at': item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else ''
        } for item in items]
    })


@app.route('/api/notifications/read', methods=['POST'])
@login_required
def api_notifications_read():
    data = request.get_json() or {}
    notification_id = data.get('notification_id')
    query = Notification.query.filter_by(user_id=current_user.id, is_read=False)
    if notification_id:
        try:
            query = query.filter_by(id=int(notification_id))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'message': 'Thông báo không hợp lệ.'}), 400
    query.update({'is_read': True}, synchronize_session=False)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/teacher/class/<int:class_id>/join-policy', methods=['PUT'])
@teacher_owns_class
def api_update_class_join_policy(class_id):
    join_policy = str((request.get_json() or {}).get('join_policy') or '').strip().lower()
    if join_policy not in {'open', 'approval'}:
        return jsonify({'success': False, 'message': 'Chế độ tham gia lớp không hợp lệ.'}), 400
    g.target_class.join_policy = join_policy
    db.session.commit()
    return jsonify({'success': True, 'join_policy': join_policy})


@app.route('/api/student/assignments/<int:assignment_id>', methods=['GET'])
@student_can_access_assignment
def api_student_assignment_detail(assignment_id):
    assignment = g.assignment

    now = app_now()
    start_retry = parse_bool(request.args.get('start_retry'))
    submission = Submission.query.filter_by(assignment_id=assignment.id, student_id=current_user.id).first()
    selected_attempt = get_submission_attempt(assignment, submission)
    attempt_state = get_assignment_attempt_state(assignment, current_user.id, now)
    attempt = attempt_state['active_attempt']

    if attempt and get_attempt_effective_deadline(attempt) and now > get_attempt_effective_deadline(attempt):
        draft_answers = json.loads(attempt.answers_json or '{}')
        save_attempt_submission(
            assignment,
            attempt,
            current_user.id,
            draft_answers,
            completion_reason='timeout'
        )
        submission = Submission.query.filter_by(assignment_id=assignment.id, student_id=current_user.id).first()
        selected_attempt = get_submission_attempt(assignment, submission)
        attempt_state = get_assignment_attempt_state(assignment, current_user.id)
        attempt = None

    summary = serialize_assignment_summary(assignment, include_class=True, student_id=current_user.id)
    unavailable = (
        not assignment.is_active
        or (assignment.opens_at and now < assignment.opens_at)
        or (assignment.due_at and now > assignment.due_at)
    )
    if unavailable:
        questions = json.loads(selected_attempt.questions_json or '[]') if selected_attempt else []
        return jsonify({
            'success': True,
            'assignment': summary,
            'questions': questions,
            'attempt': None,
            'can_submit': False,
            'retry_available': False,
            'submission': serialize_student_submission(assignment, submission, selected_attempt)
        })

    if submission and not attempt and not start_retry:
        questions = (
            json.loads(selected_attempt.questions_json or '[]')
            if selected_attempt
            else json.loads(assignment.questions_json or '[]')
        )
        return jsonify({
            'success': True,
            'assignment': summary,
            'questions': questions,
            'attempt': None,
            'can_submit': False,
            'retry_available': summary['availability'] == 'retry_available',
            'submission': serialize_student_submission(assignment, submission, selected_attempt)
        })

    if not attempt:
        attempt_state = get_assignment_attempt_state(assignment, current_user.id)
        if attempt_state['attempts_exhausted']:
            return jsonify({
                'success': False,
                'message': 'Bạn đã sử dụng hết số lần làm bài.'
            }), 409
        if attempt_state['cooldown_seconds'] > 0:
            return jsonify({
                'success': False,
                'message': f'Bạn cần chờ thêm {attempt_state["cooldown_seconds"]} giây trước khi làm lại.',
                'retry_cooldown_seconds': attempt_state['cooldown_seconds']
            }), 409
        try:
            attempt = create_assignment_attempt(assignment, current_user.id)
        except ValueError as exc:
            return jsonify({'success': False, 'message': str(exc)}), 409

    remaining_seconds = None
    if attempt.expires_at:
        remaining_seconds = max(int((attempt.expires_at - app_now()).total_seconds()), 0)

    return jsonify({
        'success': True,
        'assignment': serialize_assignment_summary(assignment, include_class=True, student_id=current_user.id),
        'questions': json.loads(attempt.questions_json or '[]'),
        'attempt': {
            'id': attempt.id,
            'submission_token': attempt.submission_token,
            'attempt_number': attempt.attempt_number,
            'started_at': attempt.started_at.strftime('%Y-%m-%d %H:%M:%S') if attempt.started_at else '',
            'expires_at': attempt.expires_at.strftime('%Y-%m-%d %H:%M:%S') if attempt.expires_at else '',
            'remaining_seconds': remaining_seconds,
            'last_saved_at': attempt.last_saved_at.strftime('%Y-%m-%d %H:%M:%S') if attempt.last_saved_at else ''
        },
        'can_submit': True,
        'submission': {
            'submitted': bool(submission),
            'answers': json.loads(attempt.answers_json or '{}'),
            'score': submission.score if submission and is_assignment_result_published(assignment) else None,
            'correct_count': (
                submission.correct_count
                if submission and is_assignment_result_published(assignment)
                else None
            ),
            'submitted_at': (
                submission.submitted_at.strftime('%Y-%m-%d %H:%M')
                if submission and submission.submitted_at else ''
            ),
            'result_published': is_assignment_result_published(assignment),
            'answers_visible': False,
            'answer_key': []
        }
    })


@app.route('/api/student/assignments/<int:assignment_id>/autosave', methods=['POST'])
@student_can_access_assignment
@rate_limit(30, 60, 'assignment-autosave')
def api_student_autosave_assignment(assignment_id):
    assignment = g.assignment

    data = request.get_json() or {}
    answers = data.get('answers', {})
    if not isinstance(answers, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu đáp án không hợp lệ.'}), 400

    try:
        attempt_id = int(data.get('attempt_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Phiên làm bài không hợp lệ.'}), 400

    attempt = AssignmentAttempt.query.filter_by(
        id=attempt_id,
        assignment_id=assignment.id,
        student_id=current_user.id,
        is_completed=False
    ).first()
    if not attempt:
        return jsonify({'success': False, 'message': 'Phiên làm bài đã kết thúc hoặc không tồn tại.'}), 409

    now = app_now()
    effective_deadline = get_attempt_effective_deadline(attempt)
    if effective_deadline and now > effective_deadline:
        saved_answers = json.loads(attempt.answers_json or '{}')
        submission = save_attempt_submission(
            assignment,
            attempt,
            current_user.id,
            saved_answers,
            completion_reason='timeout'
        )
        return jsonify({
            'success': False,
            'timed_out': True,
            'message': 'Đã hết thời gian và hệ thống đã tự nộp đáp án được lưu gần nhất.',
            'result_published': is_assignment_result_published(assignment),
            'score': submission.score if is_assignment_result_published(assignment) else None
        }), 409

    answer_key = json.loads(attempt.answer_key_json or '[]')
    attempt.answers_json = json.dumps(
        normalize_attempt_answers(answer_key, answers),
        ensure_ascii=False
    )
    attempt.last_saved_at = now
    db.session.commit()
    return jsonify({
        'success': True,
        'saved_at': now.strftime('%H:%M:%S')
    })


@app.route('/api/student/assignments/<int:assignment_id>/submit', methods=['POST'])
@student_can_access_assignment
@rate_limit(6, 60, 'assignment-submit')
def api_student_submit_assignment(assignment_id):
    assignment = g.assignment

    data = request.get_json() or {}
    answers = data.get('answers', {})
    if not isinstance(answers, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu đáp án không hợp lệ.'})

    now = app_now()
    if not assignment.is_active:
        return jsonify({'success': False, 'message': 'Bài làm đã bị đóng hoặc thu hồi.'})
    if assignment.opens_at and now < assignment.opens_at:
        return jsonify({'success': False, 'message': 'Bài làm chưa đến thời gian mở.'})

    attempt_id = data.get('attempt_id')
    submission_token = str(data.get('submission_token') or '').strip()
    try:
        attempt_id = int(attempt_id)
    except (TypeError, ValueError):
        attempt_id = None

    if not attempt_id or not submission_token:
        return jsonify({'success': False, 'message': 'Thiếu mã xác nhận phiên nộp bài.'}), 400

    attempt = AssignmentAttempt.query.filter_by(
        id=attempt_id,
        assignment_id=assignment.id,
        student_id=current_user.id
    ).first()
    if not attempt:
        return jsonify({'success': False, 'message': 'Không tìm thấy phiên làm bài đang hoạt động.'})

    if not attempt.submission_token or not secrets.compare_digest(attempt.submission_token, submission_token):
        return jsonify({'success': False, 'message': 'Mã xác nhận phiên nộp bài không hợp lệ.'}), 403

    with get_submission_lock(attempt.id):
        db.session.expire_all()
        attempt = AssignmentAttempt.query.filter_by(
            id=attempt_id,
            assignment_id=assignment.id,
            student_id=current_user.id
        ).first()
        if attempt.is_completed:
            recorded_submission = Submission.query.filter_by(
                assignment_id=assignment.id,
                student_id=current_user.id
            ).first()
            response_data = build_submission_response(
                assignment,
                attempt,
                recorded_submission,
                'Bài đã được nộp trước đó.',
                timed_out=attempt.completion_reason == 'timeout'
            )
            response_data['duplicate'] = True
            return jsonify(response_data)

        effective_deadline = get_attempt_effective_deadline(attempt)
        timed_out = bool(effective_deadline and now > effective_deadline + timedelta(seconds=10))
        submitted_answers = json.loads(attempt.answers_json or '{}') if timed_out else answers
        submission = save_attempt_submission(
            assignment,
            attempt,
            current_user.id,
            submitted_answers,
            completion_reason='timeout' if timed_out else 'submitted'
        )
        response_data = build_submission_response(
            assignment,
            attempt,
            submission,
            'Đã hết thời gian. Hệ thống tự động nộp bài.' if timed_out else 'Đã nộp bài.',
            timed_out=timed_out
        )
        attempt.submission_response_json = json.dumps(response_data, ensure_ascii=False)
        db.session.commit()
        return jsonify(response_data)

def serialize_exam_template(template, include_content=False):
    data = {
        'id': template.id,
        'title': template.title,
        'topic': template.topic or '',
        'week_number': template.week_number,
        'description': template.description or '',
        'status': template.status or 'draft',
        'created_at': template.created_at.strftime('%Y-%m-%d %H:%M') if template.created_at else '',
        'updated_at': template.updated_at.strftime('%Y-%m-%d %H:%M') if template.updated_at else '',
        'content_preview': (template.content or '').strip()[:160]
    }
    if include_content:
        data['content'] = template.content or ''
    return data


def get_owned_exam_template(template_id):
    template = ExamTemplate.query.filter_by(id=template_id, teacher_id=current_user.id).first()
    if not template:
        return None
    return template


@app.route('/api/template-library', methods=['GET'])
@login_required
def api_template_library_list():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    templates = (
        ExamTemplate.query
        .filter_by(teacher_id=current_user.id)
        .order_by(ExamTemplate.updated_at.desc(), ExamTemplate.id.desc())
        .all()
    )
    return jsonify({
        'success': True,
        'templates': [serialize_exam_template(item) for item in templates]
    })


@app.route('/api/template-library/<int:template_id>', methods=['GET'])
@login_required
def api_template_library_detail(template_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    template = get_owned_exam_template(template_id)
    if not template:
        return jsonify({'success': False, 'message': 'Không tìm thấy mẫu đề hoặc bạn không có quyền mở mẫu này.'}), 404

    return jsonify({
        'success': True,
        'template': serialize_exam_template(template, include_content=True)
    })


@app.route('/api/template-library', methods=['POST'])
@login_required
def api_template_library_save():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    data = request.get_json() or {}
    template_id = data.get('id')
    title = str(data.get('title') or '').strip()
    content = str(data.get('content') or '').strip()
    topic = str(data.get('topic') or '').strip()
    description = str(data.get('description') or '').strip()
    status = str(data.get('status') or 'draft').strip() or 'draft'

    if status not in {'draft', 'ready', 'needs_review'}:
        status = 'draft'

    week_number = data.get('week_number')
    try:
        week_number = int(week_number) if str(week_number).strip() else None
    except (TypeError, ValueError):
        week_number = None

    if not title:
        return jsonify({'success': False, 'message': 'Vui lòng nhập tên mẫu đề.'})
    if not content:
        return jsonify({'success': False, 'message': 'Nội dung template đang rỗng.'})

    try:
        if template_id:
            template = get_owned_exam_template(int(template_id))
            if not template:
                return jsonify({'success': False, 'message': 'Không tìm thấy mẫu đề để cập nhật.'}), 404
        else:
            template = ExamTemplate(teacher_id=current_user.id, title=title, content=content)
            db.session.add(template)

        template.title = title
        template.topic = topic
        template.week_number = week_number
        template.description = description
        template.status = status
        template.content = content
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Đã lưu mẫu đề.',
            'template': serialize_exam_template(template, include_content=True)
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Lỗi lưu mẫu đề: {str(e)}'})


@app.route('/api/template-library/<int:template_id>/duplicate', methods=['POST'])
@login_required
def api_template_library_duplicate(template_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    template = get_owned_exam_template(template_id)
    if not template:
        return jsonify({'success': False, 'message': 'Không tìm thấy mẫu đề để nhân bản.'}), 404

    duplicate = ExamTemplate(
        teacher_id=current_user.id,
        title=f'{template.title} - Bản sao',
        topic=template.topic,
        week_number=template.week_number,
        description=template.description,
        status='draft',
        content=template.content
    )
    db.session.add(duplicate)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Đã nhân bản mẫu đề.',
        'template': serialize_exam_template(duplicate, include_content=True)
    })


@app.route('/api/template-library/<int:template_id>', methods=['DELETE'])
@login_required
def api_template_library_delete(template_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    template = get_owned_exam_template(template_id)
    if not template:
        return jsonify({'success': False, 'message': 'Không tìm thấy mẫu đề để xóa.'}), 404

    db.session.delete(template)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Đã xóa mẫu đề.'})


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

@app.route('/api/custom-exam/check-question-ai', methods=['POST'])
@login_required
def api_check_question_ai():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    data = request.get_json() or {}
    question_content = str(data.get('content', '')).strip()
    if not question_content:
        return jsonify({'success': False, 'message': 'Không có nội dung câu hỏi để kiểm tra.'})

    review = review_question_content(question_content)
    return jsonify({
        'success': True,
        'seed': data.get('seed'),
        'question_index': data.get('question_index'),
        **review
    })

@app.route('/api/custom-exam/check-questions-ai', methods=['POST'])
@login_required
def api_check_questions_ai():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    data = request.get_json() or {}
    questions = data.get('questions', [])
    if not isinstance(questions, list) or not questions:
        return jsonify({'success': False, 'message': 'Không có danh sách câu hỏi để kiểm tra.'})

    results = []
    totals = {'ok': 0, 'warning': 0, 'error': 0}

    for item in questions:
        question_content = str(item.get('content', '')).strip()
        if not question_content:
            review = {
                'status': 'error',
                'summary': 'Không có nội dung câu hỏi để kiểm tra.',
                'issues': [{
                    'type': 'template',
                    'severity': 'high',
                    'message': 'Payload câu hỏi rỗng.',
                    'suggestion': 'Render lại preview rồi thử kiểm tra lại.'
                }],
                'mode': 'rule'
            }
        else:
            review = review_question_content(question_content)

        status = review.get('status', 'warning')
        if status not in totals:
            status = 'warning'
        totals[status] += 1

        results.append({
            'id': item.get('id'),
            'seed': item.get('seed'),
            'question_index': item.get('question_index'),
            **review
        })

    overall_status = 'error' if totals['error'] else ('warning' if totals['warning'] else 'ok')
    summary = f"Đã kiểm tra {len(results)} câu: {totals['ok']} ổn, {totals['warning']} cảnh báo, {totals['error']} lỗi nặng."

    return jsonify({
        'success': True,
        'status': overall_status,
        'summary': summary,
        'totals': totals,
        'results': results,
        'mode': 'rule'
    })


# API: Lưu kết quả đã sinh vào session để xuất PDF/TEX
@app.route('/api/custom-exam/generate-bulk', methods=['POST'])
@login_required
def custom_exam_generate_bulk():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    data = request.get_json()
    if not data or 'variants' not in data:
        return jsonify({'success': False, 'message': 'Không có dữ liệu!'})

    raw_variants = data['variants']
    if not isinstance(raw_variants, list) or not raw_variants:
        return jsonify({'success': False, 'message': 'Danh sách mã đề đang rỗng.'})

    variants = []
    for item in raw_variants[:100]:
        if not isinstance(item, dict):
            continue
        content = str(item.get('content') or '').strip()
        if not content:
            continue
        variants.append({
            'seed': item.get('seed', len(variants) + 1),
            'content': content
        })

    if not variants:
        return jsonify({'success': False, 'message': 'Không có nội dung đề hợp lệ để thêm.'})

    tex_content = ""
    preamble = ""
    body_content = ""
    title = str(data.get('exam_title') or 'Đề thi Tùy chỉnh').strip() or 'Đề thi Tùy chỉnh'
    topic = str(data.get('topic') or '').strip()
    week_raw = str(data.get('week_number') or '').strip()
    week_number = None
    if week_raw:
        try:
            week_number = int(week_raw)
            if week_number < 1:
                week_number = None
        except ValueError:
            week_number = None

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
            if idx == 0:
                preamble = f"\\documentclass[12pt, a4paper]{{article}}\n\\usepackage[utf8]{{vietnam}}\n\\usepackage{{amsmath}}\n\\begin{{document}}\n\\begin{{center}}\\Large\\textbf{{{title}}}\\end{{center}}\n\n"
            
            # Quét dọn rác tránh xung đột LaTeX
            clean_content = re.sub(r'\\documentclass\[.*?\]\{.*?\}|\\documentclass\{.*?\}', '', content)
            clean_content = re.sub(r'\\usepackage\[.*?\]\{.*?\}|\\usepackage\{.*?\}', '', clean_content)
            body_content += f"\\textbf{{[Mã đề: {v['seed']}]}}\n\n {clean_content} \n\n\\vspace{{1cm}}\n"

    if preamble:
        tex_content = preamble + body_content + "\\end{document}"

    total_questions = 0
    if variants:
        parsed_questions, _ = parse_assignment_questions(variants[0]['content'])
        total_questions = len(parsed_questions)

    quality_issues = []
    if total_questions == 0:
        quality_issues.append('Không tách được câu hỏi trắc nghiệm từ mã đề đầu tiên.')
    for variant_index, variant in enumerate(variants):
        parsed_questions, answer_key = parse_assignment_questions(variant['content'])
        if len(parsed_questions) != total_questions or len(answer_key) != total_questions:
            quality_issues.append(
                f'Mã đề {variant_index + 1} không có cùng số câu hoặc thiếu đáp án đúng.'
            )
        question_blocks = re.findall(
            r'\\begin\{question\}[\s\S]*?\\end\{question\}',
            variant['content'],
        )
        for question_index, block in enumerate(question_blocks):
            review = review_question_content(block)
            if review.get('status') == 'error':
                quality_issues.append(
                    f'Mã {variant_index + 1}, câu {question_index + 1}: {review.get("summary")}'
                )

    pdf_status = 'disabled'
    if not quality_issues and app.config.get('PDF_QUALITY_GATE', True):
        if get_pdflatex_command():
            pdf_status = 'passed'
            _pdf_bytes, compile_error = compile_tex_pdf_bytes(tex_content)
            if compile_error:
                pdf_status = 'failed'
                quality_issues.append('PDF không biên dịch được: ' + compile_error)
        else:
            pdf_status = 'skipped'

    if quality_issues:
        return jsonify({
            'success': False,
            'message': 'Đề chưa vượt qua bước kiểm tra chất lượng.',
            'quality_report': {
                'status': 'failed',
                'issues': quality_issues,
                'pdf_status': pdf_status,
            }
        }), 400

    bank_item = ExamBank(
        teacher_id=current_user.id,
        title=title,
        topic=topic,
        week_number=week_number,
        description=f'Lưu tự động khi thêm đề. Số mã đề: {len(variants)}.',
        variants_json=json.dumps(variants, ensure_ascii=False),
        combined_tex=tex_content,
        total_variants=len(variants),
        total_questions=total_questions
    )
    db.session.add(bank_item)
    db.session.commit()

    store_current_exam_tex(tex_content)
    return jsonify({
        'success': True,
        'bank_exam': serialize_exam_bank_item(bank_item),
        'quality_report': {
            'status': 'passed',
            'issues': [],
            'pdf_status': pdf_status,
        },
        'message': 'Đã chọn đề và lưu vào ngân hàng đề thi.'
    })

# API: Tải file mẫu (.cls, .tex)
@app.route('/download/sample/<filename>')
@login_required
def download_sample(filename):
    # CHỐT CHẶN BẢO MẬT: Chỉ cho phép tải đúng 3 file này
    allowed_files = ['fami-exam.cls']
    if filename not in allowed_files:
        abort(404) # Trả về lỗi 404 nếu cố tình tải file khác

    # Trỏ đến thư mục gốc chứa các file này
    file_path = str(BASE_DIR / filename)

    if os.path.exists(file_path):
        # as_attachment=True sẽ ép trình duyệt tải file về thay vì mở trực tiếp trên tab mới
        return send_file(file_path, as_attachment=True)
    else:
        flash(f'Không tìm thấy file {filename} trên hệ thống!', 'danger')
        return redirect(url_for('teacher_dashboard'))

if __name__ == '__main__':
    app.run(
        host=os.getenv('APP_HOST', '127.0.0.1'),
        port=int(os.getenv('APP_PORT', '5000')),
        debug=os.getenv('FLASK_DEBUG', '0') == '1'
    )
