import os

import pytest
from werkzeug.security import generate_password_hash


os.environ.setdefault('DATABASE_URL', 'sqlite:///pytest.db')
os.environ.setdefault('SECRET_KEY', 'pytest-secret-key')
os.environ.setdefault('SKIP_SCHEMA_BOOTSTRAP', '1')
os.environ.setdefault('DISABLE_ATTEMPT_SWEEPER', '1')

import app as app_module
from models import Assignment, Class, User, db


@pytest.fixture()
def app():
    app_module.app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI='sqlite:///pytest.db'
    )
    app_module._rate_limit_events.clear()
    with app_module.app.app_context():
        db.drop_all()
        db.create_all()
    yield app_module.app
    with app_module.app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def seeded_course(app):
    with app.app_context():
        teacher = User(
            username='teacher',
            password=generate_password_hash('password'),
            role='teacher'
        )
        other_teacher = User(
            username='other-teacher',
            password=generate_password_hash('password'),
            role='teacher'
        )
        student = User(
            username='student',
            password=generate_password_hash('password'),
            role='student'
        )
        outsider = User(
            username='outsider',
            password=generate_password_hash('password'),
            role='student'
        )
        db.session.add_all([teacher, other_teacher, student, outsider])
        db.session.flush()

        classroom = Class(
            name='Calculus 1',
            code='TEST-001',
            teacher_id=teacher.id
        )
        classroom.students.append(student)
        db.session.add(classroom)
        db.session.flush()

        assignment = Assignment(
            class_id=classroom.id,
            teacher_id=teacher.id,
            title='Policy test',
            content_tex='test',
            questions_json='[{"number":1,"prompt":"2+2?","choices":[{"letter":"A","content":"4"},{"letter":"B","content":"5"}]}]',
            answer_key_json='["A"]',
            total_questions=1,
            max_attempts=1,
            retry_cooldown_minutes=0,
            score_policy='latest',
            result_release_policy='immediate',
            results_published=False,
            show_answers=False,
            is_active=True
        )
        db.session.add(assignment)
        db.session.commit()

        return {
            'teacher_id': teacher.id,
            'other_teacher_id': other_teacher.id,
            'student_id': student.id,
            'outsider_id': outsider.id,
            'class_id': classroom.id,
            'assignment_id': assignment.id
        }


def login_as(client, user_id):
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True
        session['_csrf_token'] = 'pytest-csrf-token'


def csrf_headers():
    return {'X-CSRF-Token': 'pytest-csrf-token'}
