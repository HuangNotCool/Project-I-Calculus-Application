import re
from pathlib import Path

from tests.conftest import login_as


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_EXTERNAL_URLS = {'https://miktex.org/download'}


def test_base_page_loads_frontend_runtime_from_local_static(client):
    response = client.get('/')

    assert response.status_code == 200
    expected_assets = (
        '/static/vendor/bootstrap/css/bootstrap.min.css',
        '/static/vendor/mathjax/es5/tex-mml-chtml.js',
        '/static/vendor/fontawesome/css/all.min.css',
        '/static/vendor/aos/aos.css',
        '/static/vendor/bootstrap/js/bootstrap.bundle.min.js',
        '/static/vendor/aos/aos.js',
    )
    for asset in expected_assets:
        assert asset.encode() in response.data
    assert b'cdn.jsdelivr.net' not in response.data
    assert b'cdnjs.cloudflare.com' not in response.data
    assert b'unpkg.com' not in response.data


def test_teacher_dashboard_loads_local_codemirror(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.get('/teacher-dashboard')

    assert response.status_code == 200
    assert b'/static/vendor/codemirror/lib/codemirror.css' in response.data
    assert b'/static/vendor/codemirror/theme/dracula.css' in response.data
    assert b'/static/vendor/codemirror/lib/codemirror.js' in response.data
    assert b'cdnjs.cloudflare.com' not in response.data


def test_exam_bank_topic_badge_wraps_long_content(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.get('/teacher-dashboard')
    stylesheet = client.get('/static/css/style.css')

    assert response.status_code == 200
    assert b'exam-bank-topic-badge' in response.data
    assert b'overflow-wrap: anywhere' in stylesheet.data
    assert b'-webkit-line-clamp: 2' in stylesheet.data
    assert b'white-space: normal' in stylesheet.data


def test_custom_exam_mode_uses_expanded_workspace(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.get('/teacher-dashboard')

    assert response.status_code == 200
    assert b"document.getElementById('sidebarMenu').classList.add('d-none')" in response.data
    assert b"document.getElementById('mainWorkspace').classList.replace('col-lg-9', 'col-lg-12')" in response.data
    assert b"document.getElementById('examCardHeader').classList.add('d-none')" in response.data


def test_required_vendor_assets_are_served(client):
    assets = (
        'vendor/bootstrap/css/bootstrap.min.css',
        'vendor/bootstrap/js/bootstrap.bundle.min.js',
        'vendor/mathjax/es5/tex-mml-chtml.js',
        'vendor/mathjax/es5/output/chtml/fonts/woff-v2/MathJax_Main-Regular.woff',
        'vendor/fontawesome/css/all.min.css',
        'vendor/fontawesome/webfonts/fa-solid-900.woff2',
        'vendor/aos/aos.css',
        'vendor/aos/aos.js',
        'vendor/codemirror/lib/codemirror.css',
        'vendor/codemirror/lib/codemirror.js',
        'vendor/codemirror/theme/dracula.css',
    )
    for asset in assets:
        response = client.get(f'/static/{asset}')
        assert response.status_code == 200, asset
        assert response.data, asset


def test_templates_only_reference_approved_external_urls():
    external_urls = set()
    for template_path in (PROJECT_ROOT / 'templates').glob('*.html'):
        content = template_path.read_text(encoding='utf-8')
        external_urls.update(re.findall(r'https?://[^\s"\'<>]+', content))

    assert external_urls == ALLOWED_EXTERNAL_URLS


def test_footer_uses_compact_project_label_and_sticky_layout(client):
    page = client.get('/')
    stylesheet = client.get('/static/css/style.css')

    assert '2026 Đồ án I - Toán Tin K68'.encode() in page.data
    assert 'Hệ thống Ôn tập Giải tích Bách Khoa'.encode() not in page.data
    assert b'min-height: 100vh' in stylesheet.data
    assert b'flex-direction: column' in stylesheet.data
    assert b'flex: 1 0 auto' in stylesheet.data
