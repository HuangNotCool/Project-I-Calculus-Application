from werkzeug.security import generate_password_hash

from models import Class, ExamBank, User, db
from tests.conftest import login_as


def test_home_page_does_not_show_unverified_statistics(client):
    response = client.get('/')

    assert response.status_code == 200
    for fake_value in (b'12.500', b'3.200', b'150.000', b'98%'):
        assert fake_value not in response.data


def test_teacher_search_only_returns_owned_content(app, client, seeded_course):
    with app.app_context():
        hidden_class = Class(
            name='Secret Analysis Class',
            code='SECRET-01',
            teacher_id=seeded_course['other_teacher_id'],
        )
        hidden_exam = ExamBank(
            teacher_id=seeded_course['other_teacher_id'],
            title='Secret Analysis Exam',
            variants_json='[]',
            combined_tex='secret',
        )
        db.session.add_all([hidden_class, hidden_exam])
        db.session.commit()

    login_as(client, seeded_course['teacher_id'])
    owned_response = client.get('/search?q=Policy')
    hidden_response = client.get('/search?q=Secret+Analysis')

    assert owned_response.status_code == 200
    assert b'Policy test' in owned_response.data
    assert b'Secret Analysis Class' not in hidden_response.data
    assert b'Secret Analysis Exam' not in hidden_response.data


def test_student_search_is_limited_to_enrolled_classes(app, client, seeded_course):
    login_as(client, seeded_course['student_id'])
    enrolled_response = client.get('/search?q=Calculus')
    assignment_response = client.get('/search?q=Policy')

    assert b'Calculus 1' in enrolled_response.data
    assert b'Policy test' in assignment_response.data

    login_as(client, seeded_course['outsider_id'])
    outsider_response = client.get('/search?q=Policy')
    assert b'Policy test' not in outsider_response.data


def test_admin_can_search_teacher_accounts(app, client):
    with app.app_context():
        admin = User(
            username='admin',
            full_name='Quản trị hệ thống',
            password=generate_password_hash('AdminPassword123'),
            role='admin',
            approval_status='approved',
        )
        teacher = User(
            username='teacher.searchable',
            full_name='Giảng Viên Tìm Kiếm',
            password=generate_password_hash('Password123'),
            role='teacher',
            approval_status='pending',
        )
        db.session.add_all([admin, teacher])
        db.session.commit()
        admin_id = admin.id

    login_as(client, admin_id)
    response = client.get('/search?q=T%C3%ACm+Ki%E1%BA%BFm')

    assert response.status_code == 200
    assert 'Giảng Viên Tìm Kiếm'.encode() in response.data
    assert 'Chờ duyệt'.encode() in response.data


def test_help_page_is_role_specific(client, seeded_course):
    login_as(client, seeded_course['student_id'])
    student_help = client.get('/help')
    assert student_help.status_code == 200
    assert 'Học tập và làm bài'.encode() in student_help.data
    assert 'Sinh đề và quản lý lớp'.encode() not in student_help.data

    login_as(client, seeded_course['teacher_id'])
    teacher_help = client.get('/help')
    assert 'Sinh đề và quản lý lớp'.encode() in teacher_help.data
