from dataclasses import dataclass
import hashlib
import json
import random
import re

import sympy as sp


DIFFICULTY_LABELS = {
    'easy': 'Cơ bản',
    'medium': 'Khá',
    'hard': 'Khó',
}

TOPIC_LABELS = {
    'inverse_trig_domain': 'Tập xác định',
    'equivalent_infinitesimal': 'Vô cùng bé tương đương',
    'limit_zero': 'Giới hạn khi x tiến tới 0',
    'limit_infinity': 'Giới hạn khi x tiến tới vô cùng',
    'continuity_parameter': 'Liên tục theo tham số',
    'discontinuity': 'Phân loại điểm gián đoạn',
}


@dataclass(frozen=True)
class GeneratedChoice:
    tex: str
    is_correct: bool
    is_math: bool = True

    def to_dict(self, letter):
        return {
            'letter': letter,
            'tex': self.tex,
            'is_correct': self.is_correct,
            'is_math': self.is_math,
        }


@dataclass(frozen=True)
class GeneratedQuestion:
    topic: str
    difficulty: str
    prompt_tex: str
    choices: tuple
    solution_tex: str
    fingerprint: str

    @property
    def correct_letter(self):
        index = next(index for index, choice in enumerate(self.choices) if choice.is_correct)
        return chr(65 + index)

    def to_dict(self, number):
        return {
            'number': number,
            'topic': self.topic,
            'topic_label': TOPIC_LABELS[self.topic],
            'difficulty': self.difficulty,
            'difficulty_label': DIFFICULTY_LABELS[self.difficulty],
            'prompt_tex': self.prompt_tex,
            'solution_tex': self.solution_tex,
            'correct_letter': self.correct_letter,
            'fingerprint': self.fingerprint,
            'choices': [
                choice.to_dict(chr(65 + index))
                for index, choice in enumerate(self.choices)
            ],
        }


def _expr_key(value):
    return sp.srepr(sp.simplify(value))


def _tex(value):
    return sp.latex(sp.simplify(value))


def _clean_tex_key(value):
    return re.sub(r'\s+', '', str(value or '').replace(r'\left', '').replace(r'\right', '')).rstrip('.')


def _fingerprint(topic, payload):
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f'{topic}:{normalized}'.encode('utf-8')).hexdigest()[:20]


def _arrange_choices(correct_tex, distractors, correct_position, is_math=True):
    unique = []
    seen = {_clean_tex_key(correct_tex)}
    for distractor in distractors:
        key = _clean_tex_key(distractor)
        if key and key not in seen:
            seen.add(key)
            unique.append(distractor)
        if len(unique) == 3:
            break
    if len(unique) != 3:
        raise ValueError('Không tạo được đủ ba đáp án nhiễu phân biệt.')

    choices = [GeneratedChoice(item, False, is_math) for item in unique]
    choices.insert(correct_position % 4, GeneratedChoice(correct_tex, True, is_math))
    return tuple(choices)


def _math_choices(correct, distractors, correct_position):
    correct_key = _expr_key(correct)
    unique = []
    seen = {correct_key}
    for distractor in distractors:
        key = _expr_key(distractor)
        if key not in seen:
            seen.add(key)
            unique.append(_tex(distractor))
    return _arrange_choices(_tex(correct), unique, correct_position, is_math=True)


def _coefficient_tex(value, symbol='x'):
    expression = sp.simplify(value * sp.Symbol(symbol))
    return _tex(expression)


def _linear_tex(a, b):
    x = sp.Symbol('x')
    return _tex(a * x + b)


def _generate_inverse_trig_domain(rng, difficulty, correct_position):
    a = rng.randint(1, 8)
    b = rng.randint(-5, 5)
    function_name = rng.choice([r'\arcsin', r'\arccos'])
    left = sp.Rational(-1 - b, a)
    right = sp.Rational(1 - b, a)
    left_tex = _tex(left)
    right_tex = _tex(right)
    correct = rf'D=\left[{left_tex},{right_tex}\right]'
    distractors = [
        rf'D=\left({left_tex},{right_tex}\right)',
        r'D=\mathbb{R}',
        rf'D=\left[{right_tex},{left_tex}\right]',
    ]
    prompt = rf'Tập xác định của hàm số $y={function_name}\left({_linear_tex(a, b)}\right)$ là'
    solution = (
        rf'Hàm số xác định khi $-1\le {_linear_tex(a, b)}\le 1$. '
        rf'Vì $a={a}>0$, suy ra ${left_tex}\le x\le {right_tex}$. '
        rf'Vậy ${correct}$.'
    )
    return GeneratedQuestion(
        topic='inverse_trig_domain',
        difficulty=difficulty,
        prompt_tex=prompt,
        choices=_arrange_choices(correct, distractors, correct_position),
        solution_tex=solution,
        fingerprint=_fingerprint('inverse_trig_domain', [function_name, a, b]),
    )


def _generate_equivalent_infinitesimal(rng, difficulty, correct_position):
    n = rng.randint(2, 5)
    candidates = [value for value in range(2, 11) if value != n]
    b = rng.choice(candidates)
    x = sp.Symbol('x')
    correct = sp.Rational(b, n) * x
    distractors = [
        b * x,
        -sp.Rational(b, n) * x,
        sp.Rational(b, n + 1) * x,
        sp.Rational(n, b) * x,
    ]
    prompt = (
        rf'Khi $x\to0$, một vô cùng bé tương đương với '
        rf'$\sqrt[{n}]{{1+{b}x}}-1$ là'
    )
    solution = (
        rf'Dùng $(1+u)^{{1/{n}}}-1\sim \frac{{u}}{{{n}}}$ với $u={b}x$. '
        rf'Do đó $\sqrt[{n}]{{1+{b}x}}-1\sim {_tex(correct)}$.'
    )
    return GeneratedQuestion(
        topic='equivalent_infinitesimal',
        difficulty=difficulty,
        prompt_tex=prompt,
        choices=_math_choices(correct, distractors, correct_position),
        solution_tex=solution,
        fingerprint=_fingerprint('equivalent_infinitesimal', [n, b]),
    )


def _generate_limit_zero(rng, difficulty, correct_position):
    x = sp.Symbol('x')
    if difficulty == 'easy':
        a = rng.randint(2, 9)
        correct = sp.Integer(a)
        distractors = [-a, sp.Rational(1, a), 0, 1]
        prompt = rf'Tính giới hạn $\displaystyle\lim_{{x\to0}}\frac{{\sin({a}x)}}{{x}}$.'
        solution = rf'Vì $\sin({a}x)\sim {a}x$ nên giới hạn bằng ${a}$.'
        payload = ['sin', a]
    elif difficulty == 'hard':
        a = rng.randint(2, 7)
        b = rng.randint(2, 8)
        correct = sp.Rational(a * a, 2 * b)
        distractors = [
            sp.Rational(a * a, b),
            sp.Rational(a, 2 * b),
            sp.Rational(2 * b, a * a),
            0,
        ]
        prompt = (
            rf'Tính giới hạn $\displaystyle\lim_{{x\to0}}'
            rf'\frac{{1-\cos({a}x)}}{{\ln(1+{b}x^2)}}$.'
        )
        solution = (
            rf'Ta có $1-\cos({a}x)\sim\frac{{{a * a}x^2}}{{2}}$ và '
            rf'$\ln(1+{b}x^2)\sim {b}x^2$. Do đó giới hạn bằng ${_tex(correct)}$.'
        )
        payload = ['cos-log', a, b]
    else:
        n = rng.randint(2, 5)
        b = rng.choice([2, 4, 6, 8, 10])
        c = rng.choice([3, 5, 7, 9])
        correct = sp.Rational(b, n * c)
        distractors = [
            sp.Rational(b, c),
            sp.Rational(b, n),
            sp.Rational(n * c, b),
            0,
        ]
        prompt = (
            rf'Tính giới hạn $\displaystyle\lim_{{x\to0}}'
            rf'\frac{{\sqrt[{n}]{{1+{b}x}}-1}}{{\ln(1+{c}\sin x)}}$.'
        )
        solution = (
            rf'Tử số tương đương $\frac{{{b}}}{{{n}}}x$, mẫu số tương đương ${c}x$. '
            rf'Do đó giới hạn bằng ${_tex(correct)}$.'
        )
        payload = ['root-log', n, b, c]
    return GeneratedQuestion(
        topic='limit_zero',
        difficulty=difficulty,
        prompt_tex=prompt,
        choices=_math_choices(correct, distractors, correct_position),
        solution_tex=solution,
        fingerprint=_fingerprint('limit_zero', payload),
    )


def _generate_limit_infinity(rng, difficulty, correct_position):
    a = rng.randint(1, 5)
    c = rng.randint(1, 6)
    d = -rng.randint(1, 5)
    p = {'easy': 1, 'medium': rng.randint(1, 3), 'hard': rng.randint(2, 5)}[difficulty]
    exponent = 'x' if p == 1 else f'{p}x'
    exponent_value = sp.Rational(p * (c - d), a)
    correct = sp.exp(exponent_value)
    distractors = [
        sp.exp(-exponent_value),
        sp.Integer(1),
        sp.exp(sp.Rational(c - d, a)),
        sp.oo,
    ]
    prompt = (
        rf'Tính giới hạn $\displaystyle\lim_{{x\to+\infty}}'
        rf'\left(\frac{{{a}x+{c}}}{{{a}x{d}}}\right)^{{{exponent}}}$.'
    )
    solution = (
        rf'Đây là dạng $1^\infty$. Ta có '
        rf'$\displaystyle\lim_{{x\to+\infty}}{exponent}'
        rf'\left(\frac{{{a}x+{c}}}{{{a}x{d}}}-1\right)={_tex(exponent_value)}$. '
        rf'Vì vậy giới hạn bằng $e^{{{_tex(exponent_value)}}}$.'
    )
    return GeneratedQuestion(
        topic='limit_infinity',
        difficulty=difficulty,
        prompt_tex=prompt,
        choices=_math_choices(correct, distractors, correct_position),
        solution_tex=solution,
        fingerprint=_fingerprint('limit_infinity', [a, c, d, p]),
    )


def _generate_continuity_parameter(rng, difficulty, correct_position):
    k = rng.randint(2, 7)
    if difficulty == 'easy':
        expression = rf'\frac{{\sin({k}x)}}{{x}}'
        correct = sp.Integer(k)
        distractors = [-k, sp.Rational(1, k), 0, 1]
        equivalent = rf'\sin({k}x)\sim {k}x'
    elif difficulty == 'hard':
        expression = rf'\frac{{e^{{{k}x}}-1-{k}x}}{{x^2}}'
        correct = sp.Rational(k * k, 2)
        distractors = [k * k, sp.Rational(k, 2), -sp.Rational(k * k, 2), 0]
        equivalent = rf'e^{{{k}x}}-1-{k}x\sim\frac{{{k * k}x^2}}{{2}}'
    else:
        expression = rf'\frac{{1-\cos({k}x)}}{{x^2}}'
        correct = sp.Rational(k * k, 2)
        distractors = [k * k, -sp.Rational(k * k, 2), 0, 2 * k]
        equivalent = rf'1-\cos({k}x)\sim\frac{{{k * k}x^2}}{{2}}'
    prompt = (
        rf'Tìm $a$ để hàm số '
        rf'$f(x)=\begin{{cases}}{expression},&x\ne0,\\a,&x=0\end{{cases}}$ '
        rf'liên tục tại $x=0$.'
    )
    solution = (
        rf'Hàm số liên tục tại $0$ khi $a=\lim_{{x\to0}}{expression}$. '
        rf'Vì ${equivalent}$ nên $a={_tex(correct)}$.'
    )
    return GeneratedQuestion(
        topic='continuity_parameter',
        difficulty=difficulty,
        prompt_tex=prompt,
        choices=_math_choices(correct, distractors, correct_position),
        solution_tex=solution,
        fingerprint=_fingerprint('continuity_parameter', [difficulty, k]),
    )


def _generate_discontinuity(rng, difficulty, correct_position):
    k = rng.randint(1, 6)
    if difficulty == 'easy':
        expression = rf'\frac{{\tan({k}x)}}{{x}},\quad x\ne0'
        correct = 'Gián đoạn bỏ được'
        solution = (
            rf'Vì $\tan({k}x)\sim {k}x$ nên $\lim_{{x\to0}}f(x)={k}$ hữu hạn, '
            rf'nhưng hàm không xác định tại $0$. Đây là gián đoạn bỏ được.'
        )
        payload = ['removable', k]
    elif difficulty == 'hard':
        expression = rf'\frac{{\ln(1+{k}x)}}{{x^2}},\quad x\ne0'
        correct = 'Gián đoạn loại 2'
        solution = (
            rf'Vì $\ln(1+{k}x)\sim {k}x$ nên $f(x)\sim\frac{{{k}}}{{x}}$. '
            rf'Ít nhất một giới hạn một phía không hữu hạn, nên đây là gián đoạn loại 2.'
        )
        payload = ['type2', k]
    else:
        expression = rf'\frac{{e^{{{k}|x|}}-1}}{{x}},\quad x\ne0'
        correct = 'Gián đoạn loại 1'
        solution = (
            rf'Vì $e^{{{k}|x|}}-1\sim {k}|x|$, ta có '
            rf'$\lim_{{x\to0^+}}f(x)={k}$ và $\lim_{{x\to0^-}}f(x)=-{k}$. '
            rf'Hai giới hạn một phía hữu hạn nhưng khác nhau, nên đây là gián đoạn loại 1.'
        )
        payload = ['type1', k]
    distractors = [
        'Gián đoạn loại 1',
        'Gián đoạn loại 2',
        'Gián đoạn bỏ được',
        'Hàm số liên tục tại $x=0$',
    ]
    distractors = [item for item in distractors if item != correct]
    prompt = rf'Xét hàm số $f(x)={expression}$. Điểm $x=0$ là điểm gián đoạn loại gì?'
    return GeneratedQuestion(
        topic='discontinuity',
        difficulty=difficulty,
        prompt_tex=prompt,
        choices=_arrange_choices(correct, distractors, correct_position, is_math=False),
        solution_tex=solution,
        fingerprint=_fingerprint('discontinuity', payload),
    )


GENERATORS = {
    'inverse_trig_domain': _generate_inverse_trig_domain,
    'equivalent_infinitesimal': _generate_equivalent_infinitesimal,
    'limit_zero': _generate_limit_zero,
    'limit_infinity': _generate_limit_infinity,
    'continuity_parameter': _generate_continuity_parameter,
    'discontinuity': _generate_discontinuity,
}


def validate_generated_question(question):
    if question.topic not in GENERATORS:
        raise ValueError('Dạng bài sinh ra không hợp lệ.')
    if question.difficulty not in DIFFICULTY_LABELS:
        raise ValueError('Mức độ câu hỏi không hợp lệ.')
    if not question.prompt_tex.strip() or not question.solution_tex.strip():
        raise ValueError('Câu hỏi hoặc lời giải đang rỗng.')
    if len(question.choices) != 4:
        raise ValueError('Câu hỏi phải có đúng bốn phương án.')
    if sum(choice.is_correct for choice in question.choices) != 1:
        raise ValueError('Câu hỏi phải có đúng một phương án đúng.')
    normalized_choices = {_clean_tex_key(choice.tex) for choice in question.choices}
    if len(normalized_choices) != 4:
        raise ValueError('Các phương án trả lời đang bị trùng.')
    return True


def generate_random_questions(seed, difficulty_counts, topics=None):
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        seed = 1
    selected_topics = [topic for topic in (topics or GENERATORS.keys()) if topic in GENERATORS]
    if not selected_topics:
        raise ValueError('Cần chọn ít nhất một dạng bài.')

    rng = random.Random(seed)
    topic_cycle = list(selected_topics)
    rng.shuffle(topic_cycle)
    questions = []
    fingerprints = set()
    topic_cursor = 0

    for difficulty in ('easy', 'medium', 'hard'):
        count = max(int(difficulty_counts.get(difficulty, 0) or 0), 0)
        for _ in range(count):
            generated = None
            for attempt in range(60):
                topic = topic_cycle[topic_cursor % len(topic_cycle)]
                topic_cursor += 1
                correct_position = (seed + len(questions)) % 4
                candidate = GENERATORS[topic](rng, difficulty, correct_position)
                validate_generated_question(candidate)
                if candidate.fingerprint not in fingerprints:
                    generated = candidate
                    break
                if attempt and attempt % len(topic_cycle) == 0:
                    rng.shuffle(topic_cycle)
            if generated is None:
                raise ValueError('Không thể sinh đủ câu khác nhau với cấu hình hiện tại.')
            questions.append(generated)
            fingerprints.add(generated.fingerprint)

    if not questions:
        raise ValueError('Số lượng câu hỏi phải lớn hơn 0.')
    return questions


def _escape_latex_text(value):
    replacements = {
        '\\': r'\textbackslash{}',
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
    }
    return ''.join(replacements.get(character, character) for character in str(value or ''))


def render_random_exam_tex(title, seed, questions):
    sections = []
    for question in questions:
        choices = []
        for choice in question.choices:
            command = 'Dung' if choice.is_correct else 'Sai'
            content = f'${choice.tex}$.' if choice.is_math else f'{choice.tex}.'
            choices.append(rf'\{command}{{{content}}}')
        sections.append(
            '\n'.join([
                rf'\begin{{section}}{{{TOPIC_LABELS[question.topic]}}}',
                r'\begin{question}',
                question.prompt_tex,
                r'\DatCot[2]',
                *choices,
                r'\begin{solution}',
                question.solution_tex,
                r'\end{solution}',
                r'\end{question}',
                r'\end{section}',
            ])
        )

    return '\n'.join([
        r'\documentclass{fami-exam}',
        r'\usepackage[utf8]{vietnam}',
        r'\usepackage{amsmath, amssymb}',
        '',
        rf'\MaDeThi{{{seed}}}',
        r'\HocKy{2026.1}',
        r'\HocPhan{Giải tích I}',
        r'\MaHocPhan{MI1111}',
        rf'\GhiChu{{{_escape_latex_text(title)}}}',
        r'\GhiChuDapAn{Đề sinh tự động có lời giải}',
        r'\NgonNgu{vietnamese}',
        r'\LoiGiai{true}',
        '',
        r'\begin{document}',
        '',
        '\n\n'.join(sections),
        '',
        r'\end{document}',
    ])
