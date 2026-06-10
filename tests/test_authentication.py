from werkzeug.security import check_password_hash

from models import User, db


def set_csrf(client, token='auth-test-csrf'):
    with client.session_transaction() as session:
        session['_csrf_token'] = token
    return token


def registration_payload(**overrides):
    payload = {
        '_csrf_token': 'auth-test-csrf',
        'username': 'student.new',
        'password': 'Password123',
        'confirm_password': 'Password123',
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
        assert user.student_code == '20261234'
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


def test_registration_cannot_escalate_to_teacher(app, client):
    set_csrf(client)
    response = client.post(
        '/register',
        data=registration_payload(role='teacher')
    )

    assert response.status_code == 302
    with app.app_context():
        assert User.query.filter_by(username='student.new').one().role == 'student'


def test_registration_rejects_duplicate_student_code(app, client):
    with app.app_context():
        db.session.add(User(
            username='existing',
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
