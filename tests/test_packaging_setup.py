from werkzeug.security import check_password_hash, generate_password_hash

import app as app_module
from models import User, db
from tests.conftest import csrf_headers, login_as


def test_fresh_install_uses_public_home_instead_of_teacher_setup(client):
    response = client.get('/')

    assert response.status_code == 200
    assert client.get('/setup').status_code == 404


def test_bootstrap_creates_default_admin_once(app):
    with app.app_context():
        admin = app_module.ensure_system_admin()
        assert admin.username == 'admin'
        assert admin.role == 'admin'
        assert admin.approval_status == 'approved'
        assert check_password_hash(admin.password, 'hustoneloveonefuture')

        admin.password = generate_password_hash('ChangedPassword123')
        db.session.commit()
        original_id = admin.id

        same_admin = app_module.ensure_system_admin()
        assert same_admin.id == original_id
        assert check_password_hash(same_admin.password, 'ChangedPassword123')
        assert User.query.filter_by(role='admin').count() == 1


def test_admin_dashboard_shows_installation_status_without_password_warning(
    app, client, monkeypatch
):
    with app.app_context():
        admin = User(
            username='admin',
            full_name='Quản trị hệ thống',
            password=generate_password_hash('hustoneloveonefuture'),
            role='admin',
            approval_status='approved',
        )
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

    monkeypatch.setattr(app_module, 'get_pdflatex_command', lambda: None)
    login_as(client, admin_id)
    response = client.get('/admin-dashboard')

    assert response.status_code == 200
    assert 'Trạng thái bản cài'.encode() in response.data
    assert 'Chưa có MiKTeX'.encode() in response.data
    assert 'mật khẩu mặc định'.encode() not in response.data


def test_admin_can_download_verified_database_backup(
    app, client, tmp_path, monkeypatch
):
    with app.app_context():
        admin = User(
            username='admin',
            full_name='Quản trị hệ thống',
            password=generate_password_hash('AdminPassword123'),
            role='admin',
            approval_status='approved',
        )
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

    monkeypatch.setattr(app_module, 'INSTANCE_DIR', tmp_path)
    login_as(client, admin_id)
    response = client.post('/admin/data/backup', headers=csrf_headers())

    assert response.status_code == 200
    assert response.data.startswith(b'SQLite format 3')
    assert 'attachment;' in response.headers['Content-Disposition']
    assert list((tmp_path / 'backups').glob('database_*.db'))
