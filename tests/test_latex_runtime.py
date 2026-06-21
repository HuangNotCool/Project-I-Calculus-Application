import app as app_module
from models import ExamBank
from tests.conftest import csrf_headers, login_as


def test_latex_status_reports_missing_and_available_engine(client, seeded_course, monkeypatch):
    login_as(client, seeded_course['teacher_id'])

    monkeypatch.setattr(app_module, 'get_pdflatex_command', lambda: None)
    missing = client.get('/api/system/latex-status').get_json()
    assert missing['success'] is True
    assert missing['available'] is False
    assert missing['download_url'] == 'https://miktex.org/download'

    monkeypatch.setattr(
        app_module,
        'get_pdflatex_command',
        lambda: r'C:\Program Files\MiKTeX\miktex\bin\x64\pdflatex.exe',
    )
    available = client.get('/api/system/latex-status').get_json()
    assert available['available'] is True
    assert available['engine'] == 'pdflatex.exe'


def test_random_exam_quality_gate_skips_pdf_when_miktex_is_missing(
    app, client, seeded_course, monkeypatch
):
    login_as(client, seeded_course['teacher_id'])
    app.config['PDF_QUALITY_GATE'] = True
    monkeypatch.setattr(app_module, 'get_pdflatex_command', lambda: None)

    generated = client.post(
        '/api/generate-exam',
        data={
            'exam_title': 'No MiKTeX random exam',
            'seed': '101',
            'variant_count': '1',
            'difficulty': 'mixed',
            'qty_easy': '2',
            'qty_medium': '2',
            'qty_hard': '1',
            'topics': ['limit_zero', 'continuity_parameter', 'discontinuity'],
        },
        headers=csrf_headers(),
    )
    assert generated.status_code == 200

    finalized = client.post('/api/generate-exam/finalize', headers=csrf_headers())
    payload = finalized.get_json()
    assert finalized.status_code == 200
    assert payload['success'] is True
    assert payload['quality_report']['pdf_status'] == 'skipped'
    with app.app_context():
        assert ExamBank.query.filter_by(title='No MiKTeX random exam').count() == 1


def test_custom_exam_quality_gate_skips_pdf_when_miktex_is_missing(
    app, client, seeded_course, monkeypatch
):
    login_as(client, seeded_course['teacher_id'])
    app.config['PDF_QUALITY_GATE'] = True
    monkeypatch.setattr(app_module, 'get_pdflatex_command', lambda: None)
    tex_content = r'''
\documentclass{fami-exam}
\begin{document}
\begin{question}
Giá trị của $1+1$ bằng
\Dung{$2$}
\Sai{$1$}
\Sai{$3$}
\Sai{$4$}
\end{question}
\end{document}
'''

    response = client.post(
        '/api/custom-exam/generate-bulk',
        json={
            'exam_title': 'No MiKTeX custom exam',
            'variants': [{'seed': 1, 'content': tex_content}],
        },
        headers=csrf_headers(),
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['success'] is True
    assert payload['quality_report']['pdf_status'] == 'skipped'


def test_teacher_dashboard_contains_miktex_guidance(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.get('/teacher-dashboard')

    assert response.status_code == 200
    assert b'miktexRequiredModal' in response.data
    assert b'/api/system/latex-status' in response.data
    assert b'https://miktex.org/download' in response.data
