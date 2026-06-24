from pathlib import Path
from types import SimpleNamespace

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
    assert b'data-file-download' in response.data
    assert b'data-save-url' in response.data
    assert b'exportSettingsModal' in response.data
    assert b'exportDirectoryLabel' in response.data
    assert b'downloadFileFromUrl' in response.data
    assert b'window.location.href = pendingPdfDownloadUrl' not in response.data


def test_pdflatex_runner_hides_windows_console(monkeypatch):
    calls = {}

    class FakeStartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    def fake_run(args, **kwargs):
        calls['args'] = args
        calls.update(kwargs)
        return object()

    monkeypatch.setattr(app_module.os, 'name', 'nt')
    monkeypatch.setattr(app_module.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(app_module.subprocess, 'STARTUPINFO', FakeStartupInfo, raising=False)
    monkeypatch.setattr(app_module.subprocess, 'STARTF_USESHOWWINDOW', 1, raising=False)
    monkeypatch.setattr(app_module.subprocess, 'SW_HIDE', 0, raising=False)
    monkeypatch.setattr(app_module.subprocess, 'run', fake_run)

    app_module.run_pdflatex_command(['pdflatex', 'exam.tex'], cwd='tmp-dir', timeout=12)

    assert calls['args'] == ['pdflatex', 'exam.tex']
    assert calls['cwd'] == 'tmp-dir'
    assert calls['timeout'] == 12
    assert calls['creationflags'] == 0x08000000
    assert calls['startupinfo'].dwFlags & 1
    assert calls['startupinfo'].wShowWindow == 0


def test_compile_pdf_runs_pdflatex_twice_for_aux_totals(monkeypatch):
    calls = []

    def fake_run(args, cwd, timeout=None):
        calls.append((args, cwd, timeout))
        Path(cwd, 'exam.pdf').write_bytes(b'%PDF-test')
        return SimpleNamespace(returncode=0, stdout='')

    monkeypatch.setattr(app_module, 'get_pdflatex_command', lambda: 'pdflatex')
    monkeypatch.setattr(app_module, 'run_pdflatex_command', fake_run)

    pdf_bytes, compile_error = app_module.compile_tex_pdf_bytes(r'\documentclass{fami-exam}\begin{document}\end{document}')

    assert compile_error is None
    assert pdf_bytes == b'%PDF-test'
    assert len(calls) == 2


def test_teacher_can_choose_export_folder_and_save_current_tex(client, seeded_course, tmp_path):
    login_as(client, seeded_course['teacher_id'])
    export_dir = tmp_path / 'my-export-folder'

    settings_response = client.post(
        '/api/export/settings',
        json={'export_dir': str(export_dir)},
        headers=csrf_headers(),
    )
    settings_payload = settings_response.get_json()

    assert settings_response.status_code == 200
    assert settings_payload['success'] is True
    assert Path(settings_payload['export_dir']) == export_dir

    generated = client.post(
        '/api/generate-exam',
        data={
            'exam_title': 'Export folder exam',
            'seed': '33',
            'variant_count': '1',
            'difficulty': 'easy',
            'quantity': '2',
            'topics': ['limit_zero', 'continuity_parameter'],
        },
        headers=csrf_headers(),
    )
    assert generated.status_code == 200

    exported = client.post('/api/export/current/tex', headers=csrf_headers())
    payload = exported.get_json()

    assert exported.status_code == 200
    assert payload['success'] is True
    assert Path(payload['directory']) == export_dir
    output_path = Path(payload['path'])
    assert output_path.exists()
    assert output_path.parent == export_dir
    assert output_path.suffix == '.tex'
    assert '\\documentclass{fami-exam}' in output_path.read_text(encoding='utf-8')


def test_teacher_can_save_current_pdf_to_export_folder(
    client, seeded_course, tmp_path, monkeypatch
):
    login_as(client, seeded_course['teacher_id'])
    export_dir = tmp_path / 'pdf-export'
    client.post(
        '/api/export/settings',
        json={'export_dir': str(export_dir)},
        headers=csrf_headers(),
    )
    client.post(
        '/api/generate-exam',
        data={
            'exam_title': 'PDF export exam',
            'seed': '44',
            'variant_count': '1',
            'difficulty': 'easy',
            'quantity': '1',
            'topics': ['limit_zero'],
        },
        headers=csrf_headers(),
    )
    monkeypatch.setattr(app_module, 'compile_tex_pdf_bytes', lambda tex_content: (b'%PDF-test', None))

    exported = client.post('/api/export/current/pdf', headers=csrf_headers())
    payload = exported.get_json()

    assert exported.status_code == 200
    assert payload['success'] is True
    output_path = Path(payload['path'])
    assert output_path.parent == export_dir
    assert output_path.suffix == '.pdf'
    assert output_path.read_bytes() == b'%PDF-test'


def test_export_folder_open_returns_path_in_testing(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])

    response = client.post('/api/export/open-folder', headers=csrf_headers())
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['success'] is True
    assert payload['opened'] is False
    assert Path(payload['directory']).exists()
