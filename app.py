from flask import Flask, render_template, redirect, url_for, request, flash, Response, jsonify, session, send_file,abort
from models import db, User, Badge, Class, ExamTemplate, ExamBank, Assignment, AssignmentAttempt, Submission, Notification
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect
import sympy as sp
import random
import os
import subprocess
import tempfile
import io
import re
import csv
import hashlib
import numpy as np
import json
from datetime import datetime, timedelta

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
                        
                    distractors = random.sample(pool_latex, min(3, len(pool_latex)))
                    while len(distractors) < 3:
                        distractors.append("\\text{Khác}")
                        
                    # Trộn ngẫu nhiên A, B, C, D
                    options_html = [
                        f"\\Dung{{$ {correct_tex} $}}",
                        f"\\Sai{{$ {distractors[0]} $}}",
                        f"\\Sai{{$ {distractors[1]} $}}",
                        f"\\Sai{{$ {distractors[2]} $}}"
                    ]
                    random.shuffle(options_html)
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
                
                distractors = random.sample(wrongs, min(3, len(wrongs)))
                while len(distractors) < 3:
                    distractors.append("Sai")
                    
                # Trộn ngẫu nhiên A, B, C, D
                options_html = [
                    f"\\Dung{{{correct}}}",
                    f"\\Sai{{{distractors[0]}}}",
                    f"\\Sai{{{distractors[1]}}}",
                    f"\\Sai{{{distractors[2]}}}"
                ]
                random.shuffle(options_html)
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


def serialize_assignment_summary(assignment, include_class=False, student_id=None):
    submission = None
    active_attempt = None
    student_attempts = []
    if student_id:
        submission = Submission.query.filter_by(assignment_id=assignment.id, student_id=student_id).first()
        active_attempt = (
            AssignmentAttempt.query
            .filter_by(assignment_id=assignment.id, student_id=student_id, is_completed=False)
            .order_by(AssignmentAttempt.id.desc())
            .first()
        )
        student_attempts = (
            AssignmentAttempt.query
            .filter_by(assignment_id=assignment.id, student_id=student_id, is_completed=True)
            .order_by(AssignmentAttempt.attempt_number.asc(), AssignmentAttempt.id.asc())
            .all()
        )

    now = datetime.now()
    if not assignment.is_active:
        availability = 'closed'
    elif assignment.opens_at and now < assignment.opens_at:
        availability = 'scheduled'
    elif assignment.due_at and now > assignment.due_at:
        availability = 'expired'
    elif active_attempt:
        availability = 'in_progress'
    elif submission and not assignment.allow_retry:
        availability = 'submitted'
    elif submission and assignment.allow_retry:
        availability = 'retry_available'
    else:
        availability = 'available'

    data = {
        'id': assignment.id,
        'title': assignment.title,
        'description': assignment.description or '',
        'class_id': assignment.class_id,
        'total_questions': assignment.total_questions,
        'opens_at': assignment.opens_at.strftime('%Y-%m-%d %H:%M') if assignment.opens_at else '',
        'due_at': assignment.due_at.strftime('%Y-%m-%d %H:%M') if assignment.due_at else '',
        'duration_minutes': assignment.duration_minutes,
        'allow_retry': bool(assignment.allow_retry),
        'score_policy': assignment.score_policy or 'latest',
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
        'score': submission.score if submission else None,
        'correct_count': submission.correct_count if submission else None,
        'attempt_count': len(student_attempts) if student_id else None,
        'attempt_history': [{
            'attempt_number': item.attempt_number,
            'score': item.score,
            'correct_count': item.correct_count,
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
    questions, answer_key = build_assignment_attempt_content(assignment)
    now = datetime.now()
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
    submission.submitted_at = selected_attempt.completed_at or datetime.now()
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


def ensure_due_notifications(student):
    now = datetime.now()
    soon = now + timedelta(hours=24)
    class_ids = [item.id for item in student.enrolled_classes]
    if not class_ids:
        return
    assignments = Assignment.query.filter(
        Assignment.class_id.in_(class_ids),
        Assignment.is_active.is_(True),
        Assignment.due_at.isnot(None),
        Assignment.due_at >= now,
        Assignment.due_at <= soon
    ).all()
    for assignment in assignments:
        if Submission.query.filter_by(assignment_id=assignment.id, student_id=student.id).first():
            continue
        create_notification(
            student.id,
            'Bài tập sắp hết hạn',
            f'{assignment.title} sẽ đóng lúc {assignment.due_at.strftime("%d/%m/%Y %H:%M")}.',
            category='warning',
            link='#tab-practice',
            dedupe_key=f'assignment-due-{assignment.id}'
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
    attempt.completed_at = datetime.now()
    attempt.last_saved_at = datetime.now()
    attempt.completion_reason = completion_reason
    submission = refresh_submission_from_attempts(assignment, student_id)
    create_notification(
        student_id,
        'Đã ghi nhận bài nộp',
        f'{assignment.title}: lần {attempt.attempt_number} đạt {score}/10.',
        category='success',
        link='#tab-practice',
        dedupe_key=f'attempt-submitted-{attempt.id}'
    )
    db.session.commit()
    return submission


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
        'score_policy': "VARCHAR(20) DEFAULT 'latest'",
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
        'max_score': 'FLOAT DEFAULT 10'
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
    if changed:
        db.session.commit()


with app.app_context():
    db.create_all()
    ensure_exam_bank_schema()
    ensure_assignment_schema()
    ensure_attempt_schema()
    ensure_submission_schema()
    backfill_assignment_versions()
    backfill_attempt_results()
    
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


def send_tex_download(tex_content, filename):
    return Response(
        tex_content,
        mimetype='text/plain',
        headers={'Content-disposition': f'attachment; filename={filename}'}
    )


def send_pdf_download(tex_content, filename):
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

        basedir = os.path.abspath(os.path.dirname(__file__))
        cls_path_source = os.path.join(basedir, 'fami-exam.cls')
        if os.path.exists(cls_path_source):
            shutil.copy(cls_path_source, tempdir)

        try:
            process = subprocess.run(
                ['pdflatex', '-interaction=nonstopmode', 'exam.tex'],
                cwd=tempdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
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
@login_required
def api_teacher_create_assignment():
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

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
    allow_retry = parse_bool(data.get('allow_retry'))
    score_policy = str(data.get('score_policy') or 'latest').strip().lower()
    if score_policy not in {'first', 'latest', 'highest'}:
        score_policy = 'latest'
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
            allow_retry=allow_retry,
            score_policy=score_policy,
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
@login_required
def api_teacher_class_assignments(class_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    target_class = Class.query.get_or_404(class_id)
    if target_class.teacher_id != current_user.id:
        return jsonify({'success': False, 'message': 'Bạn không có quyền xem lớp này.'}), 403

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
@login_required
def api_teacher_update_assignment(assignment_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.teacher_id != current_user.id:
        return jsonify({'success': False, 'message': 'Bạn không có quyền sửa bài này.'}), 403

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
    assignment.allow_retry = parse_bool(data.get('allow_retry'))
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
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Đã cập nhật bài được giao.',
        'assignment': serialize_assignment_summary(assignment, include_class=True)
    })


@app.route('/api/teacher/assignments/<int:assignment_id>', methods=['DELETE'])
@login_required
def api_teacher_delete_assignment(assignment_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.teacher_id != current_user.id:
        return jsonify({'success': False, 'message': 'Bạn không có quyền xóa bài này.'}), 403

    db.session.delete(assignment)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Đã xóa bài được giao.'})


@app.route('/api/teacher/assignments/<int:assignment_id>/results', methods=['GET'])
@login_required
def api_teacher_assignment_results(assignment_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'})

    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.teacher_id != current_user.id:
        return jsonify({'success': False, 'message': 'Bạn không có quyền xem kết quả bài này.'}), 403

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
@login_required
def api_teacher_assignment_results_csv(assignment_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'message': 'Không có quyền truy cập!'}), 403

    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.teacher_id != current_user.id:
        return jsonify({'success': False, 'message': 'Bạn không có quyền xuất kết quả bài này.'}), 403

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
            student.username,
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
@login_required
def api_student_assignments():
    if current_user.role != 'student':
        return jsonify({'success': False, 'message': 'Chỉ sinh viên mới xem được danh sách bài tập.'})

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


@app.route('/api/student/assignments/<int:assignment_id>', methods=['GET'])
@login_required
def api_student_assignment_detail(assignment_id):
    if current_user.role != 'student':
        return jsonify({'success': False, 'message': 'Chỉ sinh viên mới xem được bài tập.'})

    assignment = Assignment.query.get_or_404(assignment_id)
    enrolled_class_ids = [item.id for item in current_user.enrolled_classes]
    if assignment.class_id not in enrolled_class_ids:
        return jsonify({'success': False, 'message': 'Bạn không thuộc lớp được giao bài này.'}), 403

    now = datetime.now()
    start_retry = parse_bool(request.args.get('start_retry'))
    submission = Submission.query.filter_by(assignment_id=assignment.id, student_id=current_user.id).first()
    selected_attempt = get_submission_attempt(assignment, submission)
    attempt = (
        AssignmentAttempt.query
        .filter_by(assignment_id=assignment.id, student_id=current_user.id, is_completed=False)
        .order_by(AssignmentAttempt.id.desc())
        .first()
    )

    if attempt and attempt.expires_at and now > attempt.expires_at:
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
            'submission': {
                'submitted': bool(submission),
                'answers': json.loads(submission.answers_json or '{}') if submission else {},
                'score': submission.score if submission else None,
                'correct_count': submission.correct_count if submission else None,
                'submitted_at': submission.submitted_at.strftime('%Y-%m-%d %H:%M') if submission else ''
            }
        })

    if submission and not attempt and (not assignment.allow_retry or not start_retry):
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
            'retry_available': bool(assignment.allow_retry),
            'submission': {
                'submitted': True,
                'answers': json.loads(submission.answers_json or '{}'),
                'score': submission.score,
                'correct_count': submission.correct_count,
                'submitted_at': submission.submitted_at.strftime('%Y-%m-%d %H:%M')
            }
        })

    if not attempt:
        attempt = create_assignment_attempt(assignment, current_user.id)

    remaining_seconds = None
    if attempt.expires_at:
        remaining_seconds = max(int((attempt.expires_at - datetime.now()).total_seconds()), 0)

    return jsonify({
        'success': True,
        'assignment': serialize_assignment_summary(assignment, include_class=True, student_id=current_user.id),
        'questions': json.loads(attempt.questions_json or '[]'),
        'attempt': {
            'id': attempt.id,
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
            'score': submission.score if submission else None,
            'correct_count': submission.correct_count if submission else None,
            'submitted_at': submission.submitted_at.strftime('%Y-%m-%d %H:%M') if submission else ''
        }
    })


@app.route('/api/student/assignments/<int:assignment_id>/autosave', methods=['POST'])
@login_required
def api_student_autosave_assignment(assignment_id):
    if current_user.role != 'student':
        return jsonify({'success': False, 'message': 'Chỉ sinh viên mới lưu được bài làm.'}), 403

    assignment = Assignment.query.get_or_404(assignment_id)
    if assignment.class_id not in [item.id for item in current_user.enrolled_classes]:
        return jsonify({'success': False, 'message': 'Bạn không thuộc lớp được giao bài này.'}), 403

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

    now = datetime.now()
    if attempt.expires_at and now > attempt.expires_at:
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
            'score': submission.score
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
@login_required
def api_student_submit_assignment(assignment_id):
    if current_user.role != 'student':
        return jsonify({'success': False, 'message': 'Chỉ sinh viên mới nộp được bài.'})

    assignment = Assignment.query.get_or_404(assignment_id)
    enrolled_class_ids = [item.id for item in current_user.enrolled_classes]
    if assignment.class_id not in enrolled_class_ids:
        return jsonify({'success': False, 'message': 'Bạn không thuộc lớp được giao bài này.'}), 403

    data = request.get_json() or {}
    answers = data.get('answers', {})
    if not isinstance(answers, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu đáp án không hợp lệ.'})

    now = datetime.now()
    if not assignment.is_active:
        return jsonify({'success': False, 'message': 'Bài làm đã bị đóng hoặc thu hồi.'})
    if assignment.opens_at and now < assignment.opens_at:
        return jsonify({'success': False, 'message': 'Bài làm chưa đến thời gian mở.'})

    existing_submission = Submission.query.filter_by(
        assignment_id=assignment.id,
        student_id=current_user.id
    ).first()
    if existing_submission and not assignment.allow_retry:
        return jsonify({'success': False, 'message': 'Bạn đã nộp bài này rồi.'})

    attempt_id = data.get('attempt_id')
    try:
        attempt_id = int(attempt_id)
    except (TypeError, ValueError):
        attempt_id = None

    attempt = None
    if attempt_id:
        attempt = AssignmentAttempt.query.filter_by(
            id=attempt_id,
            assignment_id=assignment.id,
            student_id=current_user.id,
            is_completed=False
        ).first()
    if not attempt:
        attempt = (
            AssignmentAttempt.query
            .filter_by(assignment_id=assignment.id, student_id=current_user.id, is_completed=False)
            .order_by(AssignmentAttempt.id.desc())
            .first()
        )
    if not attempt:
        return jsonify({'success': False, 'message': 'Không tìm thấy phiên làm bài đang hoạt động.'})

    effective_deadline = attempt.expires_at
    if assignment.due_at and (effective_deadline is None or assignment.due_at < effective_deadline):
        effective_deadline = assignment.due_at
    if effective_deadline and now > effective_deadline + timedelta(seconds=10):
        saved_answers = json.loads(attempt.answers_json or '{}')
        submission = save_attempt_submission(
            assignment,
            attempt,
            current_user.id,
            saved_answers,
            completion_reason='timeout'
        )
        return jsonify({
            'success': True,
            'timed_out': True,
            'message': 'Đã hết thời gian. Hệ thống tự động nộp bài.',
            'score': attempt.score,
            'recorded_score': submission.score,
            'correct_count': attempt.correct_count,
            'total_questions': attempt.total_questions
        })

    submission = save_attempt_submission(
        assignment,
        attempt,
        current_user.id,
        answers,
        completion_reason='submitted'
    )
    return jsonify({
        'success': True,
        'message': 'Đã nộp bài.',
        'score': attempt.score,
        'recorded_score': submission.score,
        'correct_count': attempt.correct_count,
        'total_questions': attempt.total_questions
    })

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

    for item in questions[:100]:
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
        return jsonify({'success': False, 'message': 'Không có nội dung đề hợp lệ để chốt.'})

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

    bank_item = ExamBank(
        teacher_id=current_user.id,
        title=title,
        topic=topic,
        week_number=week_number,
        description=f'Lưu tự động khi chốt đề. Số mã đề: {len(variants)}.',
        variants_json=json.dumps(variants, ensure_ascii=False),
        combined_tex=tex_content,
        total_variants=len(variants),
        total_questions=total_questions
    )
    db.session.add(bank_item)
    db.session.commit()

    session['current_exam_tex'] = tex_content
    session.modified = True
    return jsonify({
        'success': True,
        'bank_exam': serialize_exam_bank_item(bank_item),
        'message': 'Đã chốt đề và lưu vào ngân hàng đề thi.'
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
