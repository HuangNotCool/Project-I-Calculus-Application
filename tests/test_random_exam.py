import app as app_module
from random_exam import (
    generate_random_questions,
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
