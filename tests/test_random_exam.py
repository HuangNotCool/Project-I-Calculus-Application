import app as app_module
from models import ExamBank
from random_exam import (
    generate_questions_from_blueprint,
    generate_replacement_question,
    generate_random_questions,
    generate_random_variants,
    render_random_exam_tex,
    validate_generated_question,
)
from tests.conftest import csrf_headers, login_as


def test_teacher_dashboard_renders_structured_random_generator(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.get('/teacher-dashboard')

    assert response.status_code == 200
    assert b'randomExamPreview' in response.data
    assert b'inverse_trig_domain' in response.data
    assert b'continuity_parameter' in response.data


def test_random_generator_is_deterministic_and_avoids_duplicates():
    counts = {'easy': 3, 'medium': 4, 'hard': 3}

    first = generate_random_questions(2026, counts)
    second = generate_random_questions(2026, counts)

    assert first == second
    assert len(first) == 10
    assert len({question.fingerprint for question in first}) == 10
    assert all(validate_generated_question(question) for question in first)


def test_random_generator_respects_topic_and_difficulty():
    questions = generate_random_questions(
        17,
        {'easy': 0, 'medium': 6, 'hard': 0},
        topics=['limit_zero']
    )

    assert {question.topic for question in questions} == {'limit_zero'}
    assert {question.difficulty for question in questions} == {'medium'}


def test_random_generator_balances_correct_answer_positions():
    questions = generate_random_questions(
        31,
        {'easy': 4, 'medium': 4, 'hard': 4}
    )
    counts = {
        letter: sum(question.correct_letter == letter for question in questions)
        for letter in 'ABCD'
    }

    assert max(counts.values()) - min(counts.values()) <= 1


def test_replacement_keeps_topic_and_difficulty_without_duplicate():
    questions = generate_random_questions(73, {'easy': 2, 'medium': 2, 'hard': 2})
    original = questions[2]
    replacement = generate_replacement_question(
        987654,
        original.topic,
        original.difficulty,
        ord(original.correct_letter) - 65,
        {question.fingerprint for question in questions},
    )

    assert replacement.topic == original.topic
    assert replacement.difficulty == original.difficulty
    assert replacement.correct_letter == original.correct_letter
    assert replacement.fingerprint not in {question.fingerprint for question in questions}


def test_blueprint_and_multiple_variants_preserve_exam_matrix():
    blueprint = {
        'inverse_trig_domain': {'easy': 1, 'medium': 0, 'hard': 0},
        'equivalent_infinitesimal': {'easy': 0, 'medium': 1, 'hard': 0},
        'limit_zero': {'easy': 0, 'medium': 1, 'hard': 1},
        'limit_infinity': {'easy': 0, 'medium': 1, 'hard': 0},
        'continuity_parameter': {'easy': 1, 'medium': 0, 'hard': 0},
        'discontinuity': {'easy': 0, 'medium': 1, 'hard': 0},
    }
    questions = generate_questions_from_blueprint(100, blueprint)
    variants = generate_random_variants(100, 3, blueprint=blueprint)

    assert len(questions) == 7
    assert sum(question.topic == 'limit_zero' for question in questions) == 2
    assert sum(question.difficulty == 'hard' for question in questions) == 1
    assert len(variants) == 3
    assert all(len(item['questions']) == 7 for item in variants)
    assert all(item['overlap_ratio'] <= 0.25 for item in variants)


def test_random_exam_tex_can_be_parsed_for_online_assignment():
    questions = generate_random_questions(
        9,
        {'easy': 2, 'medium': 2, 'hard': 2}
    )
    tex_content = render_random_exam_tex('Đề kiểm tra', 9, questions)

    parsed_questions, answer_key = app_module.parse_assignment_questions(tex_content)

    assert len(parsed_questions) == 6
    assert len(answer_key) == 6
    assert answer_key == [question.correct_letter for question in questions]


def test_advanced_template_automath_and_autotext_are_seed_deterministic():
    template = r'''
%<cau1>%
%%variables%%
%a: {2;3;4;5}
%%variables%%
Giá trị @a@.
\AutoText{Đúng @a@}{Sai 1;Sai 2;Sai 3;Sai 4;Sai 5}
@AutoMath(Rational(a,2))@
%</cau1>%
'''
    generator = app_module.parse_advanced_template(template)

    first = generator(123)
    repeated = generator(123)
    different = generator(124)

    assert first == repeated
    assert first != different


def test_teacher_can_generate_structured_random_exam(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    form_data = {
        'exam_title': 'Random structured exam',
        'difficulty': 'mixed',
        'seed': '15',
        'qty_easy': '2',
        'qty_medium': '2',
        'qty_hard': '1',
        'topics': ['limit_zero', 'continuity_parameter', 'discontinuity']
    }

    response = client.post(
        '/api/generate-exam',
        data=form_data,
        headers=csrf_headers()
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['success'] is True
    assert payload['seed'] == 15
    assert payload['total_questions'] == 5
    assert len(payload['questions']) == 5
    assert all(len(question['choices']) == 4 for question in payload['questions'])
    with client.session_transaction() as session:
        assert 'current_exam_tex' not in session
        assert session['current_exam_ready'] is True

    tex_download = client.get('/download/tex')
    assert tex_download.status_code == 200
    assert b'\\documentclass{fami-exam}' in tex_download.data

    repeated = client.post(
        '/api/generate-exam',
        data=form_data,
        headers=csrf_headers()
    ).get_json()
    assert repeated['questions'] == payload['questions']


def test_student_cannot_generate_random_exam(client, seeded_course):
    login_as(client, seeded_course['student_id'])
    response = client.post(
        '/api/generate-exam',
        data={
            'exam_title': 'Forbidden',
            'difficulty': 'easy',
            'seed': '1',
            'quantity': '2'
        },
        headers=csrf_headers()
    )

    assert response.status_code == 403


def test_teacher_can_lock_and_regenerate_one_random_question(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    generated = client.post(
        '/api/generate-exam',
        data={
            'exam_title': 'Editable random exam',
            'difficulty': 'mixed',
            'seed': '28',
            'qty_easy': '2',
            'qty_medium': '2',
            'qty_hard': '2',
            'topics': ['limit_zero', 'limit_infinity', 'continuity_parameter']
        },
        headers=csrf_headers()
    ).get_json()
    original = generated['questions'][0]

    locked = client.put(
        '/api/generate-exam/questions/0/lock',
        json={'locked': True},
        headers=csrf_headers()
    )
    assert locked.status_code == 200
    assert locked.get_json()['locked'] is True

    rejected = client.post(
        '/api/generate-exam/questions/0/regenerate',
        headers=csrf_headers()
    )
    assert rejected.status_code == 409

    client.put(
        '/api/generate-exam/questions/0/lock',
        json={'locked': False},
        headers=csrf_headers()
    )
    regenerated = client.post(
        '/api/generate-exam/questions/0/regenerate',
        headers=csrf_headers()
    )
    payload = regenerated.get_json()

    assert regenerated.status_code == 200
    assert payload['question']['topic'] == original['topic']
    assert payload['question']['difficulty'] == original['difficulty']
    assert payload['question']['correct_letter'] == original['correct_letter']
    assert payload['question']['fingerprint'] != original['fingerprint']


def test_teacher_can_generate_multiple_variants_from_matrix(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.post(
        '/api/generate-exam',
        data={
            'exam_title': 'Matrix exam',
            'difficulty': 'medium',
            'seed': '300',
            'variant_count': '3',
            'use_matrix': 'true',
            'matrix_inverse_trig_domain_easy': '1',
            'matrix_equivalent_infinitesimal_medium': '1',
            'matrix_limit_zero_medium': '1',
            'matrix_limit_zero_hard': '1',
            'matrix_limit_infinity_medium': '1',
            'matrix_continuity_parameter_easy': '1',
            'matrix_discontinuity_medium': '1',
        },
        headers=csrf_headers()
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['total_variants'] == 3
    assert payload['total_questions'] == 7
    assert len(payload['variants']) == 3
    assert all(len(variant['questions']) == 7 for variant in payload['variants'])
    assert all(variant['overlap_ratio'] <= 0.25 for variant in payload['variants'])


def test_teacher_can_edit_and_restore_random_question(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    generated = client.post(
        '/api/generate-exam',
        data={
            'exam_title': 'Manual edit exam',
            'difficulty': 'medium',
            'seed': '501',
            'quantity': '4',
            'topics': ['limit_zero', 'continuity_parameter']
        },
        headers=csrf_headers()
    ).get_json()
    original = generated['questions'][0]
    choices = [dict(choice) for choice in original['choices']]
    choices[1]['tex'] = choices[1]['tex'] + '+0'

    edited_response = client.put(
        '/api/generate-exam/variants/0/questions/0',
        json={
            'prompt_tex': original['prompt_tex'] + ' (đã sửa)',
            'solution_tex': original['solution_tex'] + ' Kiểm tra lại.',
            'choices': choices,
        },
        headers=csrf_headers()
    )
    edited = edited_response.get_json()['question']

    assert edited_response.status_code == 200
    assert edited['manually_edited'] is True
    assert edited['prompt_tex'].endswith('(đã sửa)')

    restored_response = client.post(
        '/api/generate-exam/variants/0/questions/0/reset',
        headers=csrf_headers()
    )
    restored = restored_response.get_json()['question']

    assert restored_response.status_code == 200
    assert restored['manually_edited'] is False
    assert restored['prompt_tex'] == original['prompt_tex']


def test_teacher_can_finalize_random_exam_once(app, client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    client.post(
        '/api/generate-exam',
        data={
            'exam_title': 'Banked random exam',
            'difficulty': 'medium',
            'seed': '88',
            'variant_count': '3',
            'quantity': '5',
            'topics': [
                'inverse_trig_domain',
                'equivalent_infinitesimal',
                'limit_zero',
                'limit_infinity',
                'continuity_parameter',
                'discontinuity'
            ]
        },
        headers=csrf_headers()
    )

    first = client.post('/api/generate-exam/finalize', headers=csrf_headers())
    second = client.post('/api/generate-exam/finalize', headers=csrf_headers())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()['bank_exam']['id'] == second.get_json()['bank_exam']['id']
    with app.app_context():
        saved = ExamBank.query.filter_by(
            teacher_id=seeded_course['teacher_id'],
            title='Banked random exam'
        ).all()
        assert len(saved) == 1
        assert saved[0].total_variants == 3
        assert saved[0].total_questions == 5
