from werkzeug.security import check_password_hash

from models import Class, ClassJoinRequest, User, db
from tests.conftest import csrf_headers, login_as


def test_student_can_update_profile(app, client, seeded_course):
    login_as(client, seeded_course['student_id'])
    response = client.post(
        '/profile/update',
        data={
            '_csrf_token': 'pytest-csrf-token',
            'username': 'student.updated',
            'student_code': '20269999',
            'school_name': 'Đại học Bách Khoa Hà Nội',
            'major': 'Kỹ thuật máy tính'
        }
    )

    assert response.status_code == 302
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        assert student.username == 'student.updated'
        assert student.student_code == '20269999'
        assert student.major == 'Kỹ thuật máy tính'


def test_password_change_requires_current_password(app, client, seeded_course):
    login_as(client, seeded_course['student_id'])
    rejected = client.post(
        '/profile/change-password',
        data={
            '_csrf_token': 'pytest-csrf-token',
            'current_password': 'wrong-password',
            'new_password': 'NewPassword123',
            'confirm_password': 'NewPassword123'
        }
    )
    assert rejected.status_code == 302

    accepted = client.post(
        '/profile/change-password',
        data={
            '_csrf_token': 'pytest-csrf-token',
            'current_password': 'password',
            'new_password': 'NewPassword123',
            'confirm_password': 'NewPassword123'
        }
    )
    assert accepted.status_code == 302
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        assert check_password_hash(student.password, 'NewPassword123')


def test_open_class_allows_immediate_join(app, client, seeded_course):
    login_as(client, seeded_course['outsider_id'])
    response = client.post(
        '/api/student/join-class',
        data={'class_code': 'TEST-001'},
        headers=csrf_headers()
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload['success'] is True
    assert payload['pending'] is False
    with app.app_context():
        classroom = db.session.get(Class, seeded_course['class_id'])
        assert seeded_course['outsider_id'] in {student.id for student in classroom.students}


def test_approval_class_requires_teacher_review(app, client, seeded_course):
    with app.app_context():
        classroom = db.session.get(Class, seeded_course['class_id'])
        classroom.join_policy = 'approval'
        db.session.commit()

    login_as(client, seeded_course['outsider_id'])
    response = client.post(
        '/api/student/join-class',
        data={'class_code': 'TEST-001'},
        headers=csrf_headers()
    )
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['pending'] is True

    with app.app_context():
        classroom = db.session.get(Class, seeded_course['class_id'])
        assert seeded_course['outsider_id'] not in {student.id for student in classroom.students}
        join_request = ClassJoinRequest.query.filter_by(
            class_id=classroom.id,
            student_id=seeded_course['outsider_id']
        ).one()
        request_id = join_request.id

    login_as(client, seeded_course['teacher_id'])
    approved = client.post(
        f"/api/teacher/class/{seeded_course['class_id']}/join-requests/{request_id}/approve",
        headers=csrf_headers()
    )
    assert approved.status_code == 200
    assert approved.get_json()['success'] is True

    with app.app_context():
        classroom = db.session.get(Class, seeded_course['class_id'])
        join_request = db.session.get(ClassJoinRequest, request_id)
        assert seeded_course['outsider_id'] in {student.id for student in classroom.students}
        assert join_request.status == 'approved'


def test_other_teacher_cannot_review_join_request(app, client, seeded_course):
    with app.app_context():
        classroom = db.session.get(Class, seeded_course['class_id'])
        classroom.join_policy = 'approval'
        join_request = ClassJoinRequest(
            class_id=classroom.id,
            student_id=seeded_course['outsider_id'],
            status='pending'
        )
        db.session.add(join_request)
        db.session.commit()
        request_id = join_request.id

    login_as(client, seeded_course['other_teacher_id'])
    response = client.post(
        f"/api/teacher/class/{seeded_course['class_id']}/join-requests/{request_id}/approve",
        headers=csrf_headers()
    )
    assert response.status_code == 403
