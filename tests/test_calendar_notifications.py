from datetime import timedelta

import app as app_module
from models import Assignment, Notification, Submission, db
from tests.conftest import login_as


def test_student_calendar_only_contains_enrolled_assignments(app, client, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        assignment.opens_at = app_module.app_now() + timedelta(days=1)
        assignment.due_at = app_module.app_now() + timedelta(days=3)
        db.session.commit()

    login_as(client, seeded_course['student_id'])
    response = client.get('/api/calendar-events')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['success'] is True
    assert [item['id'] for item in payload['assignments']] == [seeded_course['assignment_id']]

    login_as(client, seeded_course['outsider_id'])
    outsider_payload = client.get('/api/calendar-events').get_json()
    assert outsider_payload['assignments'] == []


def test_teacher_calendar_only_contains_owned_assignments(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    payload = client.get('/api/calendar-events').get_json()
    assert [item['id'] for item in payload['unscheduled']] == [seeded_course['assignment_id']]

    login_as(client, seeded_course['other_teacher_id'])
    other_payload = client.get('/api/calendar-events').get_json()
    assert other_payload['assignments'] == []
    assert other_payload['unscheduled'] == []


def test_calendar_page_renders_for_both_roles(client, seeded_course):
    login_as(client, seeded_course['teacher_id'])
    assert client.get('/calendar').status_code == 200

    login_as(client, seeded_course['student_id'])
    assert client.get('/calendar').status_code == 200


def test_due_reminders_use_separate_24h_and_3h_milestones(app, client, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        assignment.due_at = app_module.app_now() + timedelta(hours=20)
        db.session.commit()

    login_as(client, seeded_course['student_id'])
    first_payload = client.get('/api/notifications').get_json()
    assert any('24 giờ' in item['title'] for item in first_payload['notifications'])

    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        assignment.due_at = app_module.app_now() + timedelta(hours=2)
        db.session.commit()

    second_payload = client.get('/api/notifications').get_json()
    assert any('3 giờ' in item['title'] for item in second_payload['notifications'])

    client.get('/api/notifications')
    with app.app_context():
        milestone_keys = {
            item.dedupe_key
            for item in Notification.query.filter_by(user_id=seeded_course['student_id']).all()
        }
        assert f"assignment-due-24h-{seeded_course['assignment_id']}" in milestone_keys
        assert f"assignment-due-3h-{seeded_course['assignment_id']}" in milestone_keys
        assert len(milestone_keys) == len(set(milestone_keys))


def test_overdue_notification_is_created_once(app, client, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        assignment.due_at = app_module.app_now() - timedelta(hours=1)
        db.session.commit()

    login_as(client, seeded_course['student_id'])
    client.get('/api/notifications')
    client.get('/api/notifications')

    with app.app_context():
        count = Notification.query.filter_by(
            user_id=seeded_course['student_id'],
            dedupe_key=f"assignment-overdue-{seeded_course['assignment_id']}"
        ).count()
        assert count == 1


def test_submitted_student_does_not_receive_due_reminder(app, client, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        assignment.due_at = app_module.app_now() + timedelta(hours=2)
        db.session.add(Submission(
            assignment_id=assignment.id,
            student_id=seeded_course['student_id'],
            answers_json='{"0":"A"}',
            correct_count=1,
            total_questions=1,
            score=10,
            max_score=10
        ))
        db.session.commit()

    login_as(client, seeded_course['student_id'])
    client.get('/api/notifications')

    with app.app_context():
        due_notifications = Notification.query.filter(
            Notification.user_id == seeded_course['student_id'],
            Notification.dedupe_key.like('assignment-due-%')
        ).count()
        assert due_notifications == 0


def test_teacher_receives_submission_notification(app, seeded_course):
    with app.app_context():
        assignment = db.session.get(Assignment, seeded_course['assignment_id'])
        attempt = app_module.create_assignment_attempt(assignment, seeded_course['student_id'])
        app_module.save_attempt_submission(
            assignment,
            attempt,
            seeded_course['student_id'],
            {'0': 'A'}
        )

        notification = Notification.query.filter_by(
            user_id=seeded_course['teacher_id'],
            dedupe_key=f'teacher-attempt-submitted-{attempt.id}'
        ).first()
        assert notification is not None
        assert 'Nguyễn Văn Sinh Viên' in notification.message
