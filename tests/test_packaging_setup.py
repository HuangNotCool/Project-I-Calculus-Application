from werkzeug.security import check_password_hash, generate_password_hash

import app as app_module
from models import User, db


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
