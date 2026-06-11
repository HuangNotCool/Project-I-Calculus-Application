from models import Class, Submission, User, db
from tests.conftest import csrf_headers, login_as


def test_teacher_can_view_owned_class_detail(app, client, seeded_course):
    with app.app_context():
        submission = Submission(
            assignment_id=seeded_course['assignment_id'],
            student_id=seeded_course['student_id'],
            answers_json='{"0":"A"}',
            correct_count=1,
            total_questions=1,
            score=10,
            max_score=10
        )
        db.session.add(submission)
        db.session.commit()

    login_as(client, seeded_course['teacher_id'])
    response = client.get(f"/teacher/classes/{seeded_course['class_id']}")

    assert response.status_code == 200
    assert 'Calculus 1'.encode() in response.data
    assert 'Policy test'.encode() in response.data
    assert 'Nguyễn Văn Sinh Viên'.encode() in response.data
    assert b'100.0%' in response.data


def test_class_student_api_uses_full_name_as_display_name(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    payload = client.get(
        f"/api/teacher/class/{seeded_course['class_id']}/students"
    ).get_json()

    assert payload['students'][0]['username'] == 'student'
    assert payload['students'][0]['display_name'] == 'Nguyễn Văn Sinh Viên'


def test_other_teacher_cannot_view_class_detail(client, seeded_course):
    login_as(client, seeded_course['other_teacher_id'])
    response = client.get(f"/teacher/classes/{seeded_course['class_id']}")

    assert response.status_code == 403


def test_teacher_can_update_class_settings(app, client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.put(
        f"/api/teacher/class/{seeded_course['class_id']}/settings",
        json={
            'name': 'Calculus Advanced',
            'description': 'Updated class',
            'code': 'HUST-9999',
            'join_policy': 'approval'
        },
        headers=csrf_headers()
    )

    assert response.status_code == 200
    assert response.get_json()['success'] is True
    with app.app_context():
        classroom = db.session.get(Class, seeded_course['class_id'])
        assert classroom.name == 'Calculus Advanced'
        assert classroom.code == 'HUST-9999'
        assert classroom.join_policy == 'approval'


def test_class_code_must_be_unique(app, client, seeded_course):
    with app.app_context():
        duplicate = Class(
            name='Other class',
            code='HUST-DUP',
            teacher_id=seeded_course['teacher_id']
        )
        db.session.add(duplicate)
        db.session.commit()

    login_as(client, seeded_course['teacher_id'])
    response = client.put(
        f"/api/teacher/class/{seeded_course['class_id']}/settings",
        json={
            'name': 'Calculus 1',
            'description': '',
            'code': 'HUST-DUP',
            'join_policy': 'open'
        },
        headers=csrf_headers()
    )

    assert response.status_code == 409
    assert response.get_json()['success'] is False


def test_teacher_can_remove_student_from_owned_class(app, client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    response = client.delete(
        f"/api/teacher/class/{seeded_course['class_id']}/students/{seeded_course['student_id']}",
        headers=csrf_headers()
    )

    assert response.status_code == 200
    assert response.get_json()['success'] is True
    with app.app_context():
        classroom = db.session.get(Class, seeded_course['class_id'])
        assert seeded_course['student_id'] not in {student.id for student in classroom.students}
        assert db.session.get(User, seeded_course['student_id']) is not None


def test_other_teacher_cannot_remove_student(client, seeded_course):
    login_as(client, seeded_course['other_teacher_id'])
    response = client.delete(
        f"/api/teacher/class/{seeded_course['class_id']}/students/{seeded_course['student_id']}",
        headers=csrf_headers()
    )

    assert response.status_code == 403
