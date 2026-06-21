import sympy as sp

import app as app_module
from tests.conftest import csrf_headers, login_as


QUESTION_WITH_EQUIVALENT_CHOICES = r'''
\begin{question}
Chọn đáp án đúng.
\Dung{$\frac{1}{2}$}
\Sai{$\frac{2}{4}$}
\Sai{$1$}
\Sai{$2$}
\end{question}
'''


def test_symbolic_checker_detects_equivalent_answer_choices():
    review = app_module.review_question_content(QUESTION_WITH_EQUIVALENT_CHOICES)

    semantic_issues = [issue for issue in review['issues'] if issue['type'] == 'semantic']
    assert review['status'] == 'error'
    assert semantic_issues
    assert semantic_issues[0]['severity'] == 'high'

    decimal_value = app_module.latex_choice_to_sympy(r'$0.5$')
    fraction_value = app_module.latex_choice_to_sympy(r'$\frac{1}{2}$')
    assert sp.simplify(decimal_value - fraction_value) == 0
    assert app_module.latex_choice_to_sympy(r'$[0,1]$') is None


def test_teacher_checks_only_questions_from_preview(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    preview_questions = [
        {
            'id': 'review-3-1',
            'seed': 3,
            'question_index': 1,
            'content': QUESTION_WITH_EQUIVALENT_CHOICES,
        },
        {
            'id': 'review-8-1',
            'seed': 8,
            'question_index': 1,
            'content': QUESTION_WITH_EQUIVALENT_CHOICES.replace(
                r'\Sai{$\frac{2}{4}$}',
                r'\Sai{$\frac{3}{4}$}',
            ),
        },
    ]

    response = client.post(
        '/api/custom-exam/check-questions-ai',
        json={'questions': preview_questions},
        headers=csrf_headers(),
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['success'] is True
    assert len(payload['results']) == 2
    assert [item['seed'] for item in payload['results']] == [3, 8]
    assert {item['id'] for item in payload['results']} == {'review-3-1', 'review-8-1'}


def test_student_cannot_check_preview_questions(client, seeded_course):
    login_as(client, seeded_course['student_id'])

    response = client.post(
        '/api/custom-exam/check-questions-ai',
        json={'questions': [{
            'id': 'review-1-1',
            'seed': 1,
            'question_index': 1,
            'content': QUESTION_WITH_EQUIVALENT_CHOICES,
        }]},
        headers=csrf_headers(),
    )

    assert response.status_code == 403


def test_teacher_dashboard_renders_preview_checker(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.get('/teacher-dashboard')

    assert response.status_code == 200
    assert b'btnCheckAllQuestions' in response.data
    assert b'/api/custom-exam/check-questions-ai' in response.data
    assert b'stressSeedCount' not in response.data
    assert b'/api/custom-exam/stress-test' not in response.data
