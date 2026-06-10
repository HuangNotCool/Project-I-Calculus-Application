from datetime import timedelta

import app as app_module
from models import Assignment, AssignmentAttempt, Submission, db
from tests.conftest import csrf_headers, login_as


def test_manual_release_hides_score_and_answer_key(app, client, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        assignment.result_release_policy = 'manual'
        assignment.show_answers = True
        attempt = app_module.create_assignment_attempt(assignment, seeded_course['student_id'])
        app_module.save_attempt_submission(
            assignment,
            attempt,
            seeded_course['student_id'],
            {'0': 'A'}
        )

    login_as(client, seeded_course['student_id'])
    response = client.get(f"/api/student/assignments/{seeded_course['assignment_id']}")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['submission']['result_published'] is False
    assert payload['submission']['score'] is None
    assert payload['submission']['answer_key'] == []

    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        assignment.results_published = True
        db.session.commit()

    payload = client.get(
        f"/api/student/assignments/{seeded_course['assignment_id']}"
    ).get_json()
    assert payload['submission']['result_published'] is True
    assert payload['submission']['score'] == 10.0
    assert payload['submission']['answer_key'] == ['A']


def test_retry_cooldown_and_max_attempts_are_enforced(app, client, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        assignment.max_attempts = 2
        assignment.allow_retry = True
        assignment.retry_cooldown_minutes = 10
        attempt = app_module.create_assignment_attempt(assignment, seeded_course['student_id'])
        app_module.save_attempt_submission(
            assignment,
            attempt,
            seeded_course['student_id'],
            {'0': 'B'}
        )

    login_as(client, seeded_course['student_id'])
    response = client.get(
        f"/api/student/assignments/{seeded_course['assignment_id']}?start_retry=1"
    )
    assert response.status_code == 409
    assert response.get_json()['retry_cooldown_seconds'] > 0

    with app.app_context():
        first_attempt = AssignmentAttempt.query.filter_by(
            assignment_id=seeded_course['assignment_id'],
            student_id=seeded_course['student_id']
        ).first()
        first_attempt.completed_at = app_module.app_now() - timedelta(minutes=11)
        db.session.commit()

    response = client.get(
        f"/api/student/assignments/{seeded_course['assignment_id']}?start_retry=1"
    )
    assert response.status_code == 200
    assert response.get_json()['attempt']['attempt_number'] == 2

    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        second_attempt = AssignmentAttempt.query.filter_by(
            assignment_id=assignment.id,
            student_id=seeded_course['student_id'],
            is_completed=False
        ).first()
        app_module.save_attempt_submission(
            assignment,
            second_attempt,
            seeded_course['student_id'],
            {'0': 'A'}
        )

    response = client.get(
        f"/api/student/assignments/{seeded_course['assignment_id']}?start_retry=1"
    )
    assert response.status_code == 409
    assert 'hết số lần' in response.get_json()['message']


def test_expired_attempt_is_finalized_without_student_request(app, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        attempt = app_module.create_assignment_attempt(assignment, seeded_course['student_id'])
        attempt.answers_json = '{"0":"A"}'
        attempt.expires_at = app_module.app_now() - timedelta(seconds=1)
        db.session.commit()

        assert app_module.finalize_expired_attempts() == 1
        db.session.refresh(attempt)
        submission = Submission.query.filter_by(
            assignment_id=assignment.id,
            student_id=seeded_course['student_id']
        ).first()

        assert attempt.is_completed is True
        assert attempt.completion_reason == 'timeout'
        assert submission is not None
        assert submission.score == 10.0


def test_submit_is_idempotent(app, client, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        attempt = app_module.create_assignment_attempt(assignment, seeded_course['student_id'])
        attempt_id = attempt.id
        token = attempt.submission_token

    login_as(client, seeded_course['student_id'])
    request_body = {
        'answers': {'0': 'A'},
        'attempt_id': attempt_id,
        'submission_token': token
    }
    first = client.post(
        f"/api/student/assignments/{seeded_course['assignment_id']}/submit",
        json=request_body,
        headers=csrf_headers()
    )
    second = client.post(
        f"/api/student/assignments/{seeded_course['assignment_id']}/submit",
        json=request_body,
        headers=csrf_headers()
    )

    assert first.status_code == 200
    assert first.get_json()['score'] == 10.0
    assert second.status_code == 200
    assert second.get_json()['duplicate'] is True

    with app.app_context():
        assert AssignmentAttempt.query.filter_by(is_completed=True).count() == 1
        assert Submission.query.count() == 1
