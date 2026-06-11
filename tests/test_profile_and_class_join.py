from werkzeug.security import check_password_hash

from models import Class, ClassJoinRequest, Submission, User, db
from tests.conftest import csrf_headers, login_as


def test_student_can_update_profile(app, client, seeded_course):
    login_as(client, seeded_course['student_id'])
    response = client.post(
        '/profile/update',
        data={
            '_csrf_token': 'pytest-csrf-token',
            'full_name': 'Nguyễn Văn Đã Sửa',
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
        assert student.full_name == 'Nguyễn Văn Đã Sửa'
        assert student.student_code == '20269999'
        assert student.major == 'Kỹ thuật máy tính'


def test_student_profile_and_class_dashboard_render(client, seeded_course):
    login_as(client, seeded_course['student_id'])

    assert client.get('/profile').status_code == 200
    assert client.get('/student-dashboard').status_code == 200


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


def test_teacher_can_verify_and_revoke_student_code(app, client, seeded_course):
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        student.student_code = '20261234'
        student.school_name = 'Dai hoc Bach Khoa Ha Noi'
        student.major = 'Cong nghe thong tin'
        db.session.commit()

    login_as(client, seeded_course['teacher_id'])
    response = client.put(
        (
            f"/api/teacher/class/{seeded_course['class_id']}/students/"
            f"{seeded_course['student_id']}/verify-code"
        ),
        json={'verified': True},
        headers=csrf_headers()
    )

    assert response.status_code == 200
    assert response.get_json()['verified'] is True
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        assert student.student_code_verified is True
        assert student.student_code_verified_by == seeded_course['teacher_id']
        assert student.student_code_verified_at is not None

    revoked = client.put(
        (
            f"/api/teacher/class/{seeded_course['class_id']}/students/"
            f"{seeded_course['student_id']}/verify-code"
        ),
        json={'verified': False},
        headers=csrf_headers()
    )

    assert revoked.status_code == 200
    assert revoked.get_json()['verified'] is False
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        assert student.student_code_verified is False
        assert student.student_code_verified_by is None
        assert student.student_code_verified_at is None


def test_other_teacher_cannot_verify_student_code(app, client, seeded_course):
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        student.student_code = '20261234'
        db.session.commit()

    login_as(client, seeded_course['other_teacher_id'])
    response = client.put(
        (
            f"/api/teacher/class/{seeded_course['class_id']}/students/"
            f"{seeded_course['student_id']}/verify-code"
        ),
        json={'verified': True},
        headers=csrf_headers()
    )

    assert response.status_code == 403
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        assert student.student_code_verified is False


def test_verified_student_cannot_change_student_code(app, client, seeded_course):
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        student.student_code = '20261234'
        student.student_code_verified = True
        student.student_code_verified_by = seeded_course['teacher_id']
        student.school_name = 'Dai hoc Bach Khoa Ha Noi'
        student.major = 'Cong nghe thong tin'
        db.session.commit()

    login_as(client, seeded_course['student_id'])
    response = client.post(
        '/profile/update',
        data={
            '_csrf_token': 'pytest-csrf-token',
            'full_name': 'Nguyen Van Sinh Vien',
            'username': 'student',
            'student_code': '20269999',
            'school_name': 'Dai hoc Bach Khoa Ha Noi',
            'major': 'Cong nghe thong tin'
        }
    )

    assert response.status_code == 302
    with app.app_context():
        student = db.session.get(User, seeded_course['student_id'])
        assert student.student_code == '20261234'


def test_student_can_cancel_own_pending_join_request(app, client, seeded_course):
    with app.app_context():
        join_request = ClassJoinRequest(
            class_id=seeded_course['class_id'],
            student_id=seeded_course['outsider_id'],
            status='pending'
        )
        db.session.add(join_request)
        db.session.commit()
        request_id = join_request.id

    login_as(client, seeded_course['outsider_id'])
    response = client.delete(
        f'/api/student/join-requests/{request_id}',
        headers=csrf_headers()
    )

    assert response.status_code == 200
    with app.app_context():
        join_request = db.session.get(ClassJoinRequest, request_id)
        assert join_request.status == 'canceled'
        assert join_request.responded_at is not None


def test_student_cannot_cancel_another_students_request(app, client, seeded_course):
    with app.app_context():
        join_request = ClassJoinRequest(
            class_id=seeded_course['class_id'],
            student_id=seeded_course['outsider_id'],
            status='pending'
        )
        db.session.add(join_request)
        db.session.commit()
        request_id = join_request.id

    login_as(client, seeded_course['student_id'])
    response = client.delete(
        f'/api/student/join-requests/{request_id}',
        headers=csrf_headers()
    )

    assert response.status_code == 404
    with app.app_context():
        assert db.session.get(ClassJoinRequest, request_id).status == 'pending'


def test_student_can_leave_class_without_losing_submission(app, client, seeded_course):
    with app.app_context():
        submission = Submission(
            assignment_id=seeded_course['assignment_id'],
            student_id=seeded_course['student_id'],
            answers_json='["A"]',
            correct_count=1,
            total_questions=1,
            score=10,
            max_score=10
        )
        db.session.add(submission)
        db.session.commit()
        submission_id = submission.id

    login_as(client, seeded_course['student_id'])
    response = client.delete(
        f"/api/student/classes/{seeded_course['class_id']}",
        headers=csrf_headers()
    )

    assert response.status_code == 200
    with app.app_context():
        classroom = db.session.get(Class, seeded_course['class_id'])
        assert seeded_course['student_id'] not in {
            student.id for student in classroom.students
        }
        assert db.session.get(Submission, submission_id) is not None
