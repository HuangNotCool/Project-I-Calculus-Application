from tests.conftest import login_as


def test_other_teacher_cannot_read_assignment_results(client, seeded_course):
    login_as(client, seeded_course['other_teacher_id'])
    response = client.get(
        f"/api/teacher/assignments/{seeded_course['assignment_id']}/results"
    )
    assert response.status_code == 403


def test_student_outside_class_cannot_open_assignment(client, seeded_course):
    login_as(client, seeded_course['outsider_id'])
    response = client.get(
        f"/api/student/assignments/{seeded_course['assignment_id']}"
    )
    assert response.status_code == 403
