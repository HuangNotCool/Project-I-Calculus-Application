from werkzeug.security import check_password_hash, generate_password_hash

from models import User, db
from tests.conftest import csrf_headers, login_as


def set_csrf(client, token='auth-test-csrf'):
    with client.session_transaction() as session:
        session['_csrf_token'] = token
    return token


def registration_payload(**overrides):
    payload = {
        '_csrf_token': 'auth-test-csrf',
        'full_name': 'Nguyễn Văn An',
        'username': 'student.new',
        'password': 'Password123',
        'confirm_password': 'Password123',
        'role': 'student',
        'student_code': '20261234',
        'school_name': 'Đại học Bách Khoa Hà Nội',
        'major': 'Khoa học máy tính'
    }
    payload.update(overrides)
    return payload


def test_student_registration_saves_verified_profile(app, client):
    set_csrf(client)
    response = client.post('/register', data=registration_payload())

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login')
    with app.app_context():
        user = User.query.filter_by(username='student.new').one()
        assert user.role == 'student'
        assert user.full_name == 'Nguyễn Văn An'
        assert user.student_code == '20261234'
        assert user.student_code_verified is False
        assert user.school_name == 'Đại học Bách Khoa Hà Nội'
        assert user.major == 'Khoa học máy tính'
        assert check_password_hash(user.password, 'Password123')


def test_registration_rejects_password_mismatch(app, client):
    set_csrf(client)
    response = client.post(
        '/register',
        data=registration_payload(confirm_password='Different123')
    )

    assert response.status_code == 400
    with app.app_context():
        assert User.query.filter_by(username='student.new').first() is None


def test_registration_requires_full_name(app, client):
    set_csrf(client)
    response = client.post('/register', data=registration_payload(full_name=''))

    assert response.status_code == 400
    with app.app_context():
        assert User.query.filter_by(username='student.new').first() is None


def test_teacher_registration_waits_for_admin_approval(app, client):
    set_csrf(client)
    response = client.post(
        '/register',
        data=registration_payload(
            role='teacher',
            student_code='',
            school_name='',
            major='',
        )
    )

    assert response.status_code == 302
    with app.app_context():
        teacher = User.query.filter_by(username='student.new').one()
        assert teacher.role == 'teacher'
        assert teacher.approval_status == 'pending'
        assert teacher.student_code is None


def test_pending_teacher_cannot_login(client, app):
    with app.app_context():
        db.session.add(User(
            username='pending.teacher',
            full_name='Giảng Viên Chờ Duyệt',
            password=generate_password_hash('Password123'),
            role='teacher',
            approval_status='pending',
        ))
        db.session.commit()

    set_csrf(client)
    response = client.post('/login', data={
        '_csrf_token': 'auth-test-csrf',
        'username': 'pending.teacher',
        'password': 'Password123',
    })

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login')
    with client.session_transaction() as session:
        assert '_user_id' not in session


def test_admin_can_approve_teacher_registration(client, app):
    with app.app_context():
        admin = User(
            username='admin',
            full_name='Quản trị hệ thống',
            password=generate_password_hash('AdminPassword123'),
            role='admin',
            approval_status='approved',
        )
        teacher = User(
            username='pending.teacher',
            full_name='Giảng Viên Chờ Duyệt',
            password=generate_password_hash('Password123'),
            role='teacher',
            approval_status='pending',
        )
        db.session.add_all([admin, teacher])
        db.session.commit()
        teacher_id = teacher.id

    set_csrf(client)
    login_response = client.post('/login', data={
        '_csrf_token': 'auth-test-csrf',
        'username': 'admin',
        'password': 'AdminPassword123',
    })
    assert login_response.headers['Location'].endswith('/admin-dashboard')

    set_csrf(client, 'admin-review-csrf')
    response = client.post(
        f'/admin/teachers/{teacher_id}/approve',
        data={'_csrf_token': 'admin-review-csrf'},
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/admin-dashboard')
    with app.app_context():
        assert db.session.get(User, teacher_id).approval_status == 'approved'


def test_student_cannot_review_teacher_registration(client, app, seeded_course):
    with app.app_context():
        teacher = User(
            username='pending.teacher',
            full_name='Giảng Viên Chờ Duyệt',
            password=generate_password_hash('Password123'),
            role='teacher',
            approval_status='pending',
        )
        db.session.add(teacher)
        db.session.commit()
        teacher_id = teacher.id

    login_as(client, seeded_course['student_id'])
    response = client.post(
        f'/admin/teachers/{teacher_id}/approve',
        headers=csrf_headers(),
    )

    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(User, teacher_id).approval_status == 'pending'


def test_registration_rejects_duplicate_student_code(app, client):
    with app.app_context():
        db.session.add(User(
            username='existing',
            full_name='Sinh Viên Đã Có',
            password='unused',
            role='student',
            student_code='20261234',
            school_name='Đại học Bách Khoa Hà Nội',
            major='Toán Tin'
        ))
        db.session.commit()

    set_csrf(client)
    response = client.post('/register', data=registration_payload())

    assert response.status_code == 409
    with app.app_context():
        assert User.query.filter_by(username='student.new').first() is None


def test_login_normalizes_username_and_rotates_session(client, seeded_course):
    set_csrf(client)
    response = client.post(
        '/login',
        data={
            '_csrf_token': 'auth-test-csrf',
            'username': '  STUDENT  ',
            'password': 'password'
        }
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/student-dashboard')
    with client.session_transaction() as session:
        assert session['_user_id'] == str(seeded_course['student_id'])
        assert '_csrf_token' not in session
