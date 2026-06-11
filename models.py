from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os

db = SQLAlchemy()


def app_now():
    timezone_name = os.getenv('APP_TIMEZONE', 'Asia/Bangkok')
    try:
        configured_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        configured_timezone = timezone(timedelta(hours=float(os.getenv('APP_UTC_OFFSET_HOURS', '7'))))
    return datetime.now(configured_timezone).replace(tzinfo=None)

# Bảng trung gian lưu trữ Học sinh sở hữu Huy hiệu nào
user_badges = db.Table('user_badges',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('badge_id', db.Integer, db.ForeignKey('badge.id'), primary_key=True)
)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    student_code = db.Column(db.String(50), unique=True)
    student_code_verified = db.Column(db.Boolean, default=False, nullable=False)
    student_code_verified_at = db.Column(db.DateTime)
    student_code_verified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    school_name = db.Column(db.String(200))
    major = db.Column(db.String(150))
    
    # Liên kết: Một user có thể có nhiều badge
    badges = db.relationship('Badge', secondary=user_badges, lazy='subquery',
                             backref=db.backref('users', lazy=True))

    @property
    def display_name(self):
        return (self.full_name or '').strip() or self.username

# Bảng trung gian lưu danh sách Sinh viên tham gia Lớp học
student_classes = db.Table('student_classes',
    db.Column('student_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('class_id', db.Integer, db.ForeignKey('class.id'), primary_key=True)
)

# Bảng Lớp học
class Class(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(200))
    code = db.Column(db.String(20), unique=True, nullable=False) # Mã HUST-XXXX
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    join_policy = db.Column(db.String(20), default='open', nullable=False)
    
    # Quan hệ nhiều-nhiều với User (Lấy ra danh sách học sinh)
    students = db.relationship('User', secondary=student_classes, lazy='subquery',
        backref=db.backref('enrolled_classes', lazy=True))


class ClassJoinRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False)
    requested_at = db.Column(db.DateTime, default=app_now)
    responded_at = db.Column(db.DateTime)

    classroom = db.relationship(
        'Class',
        backref=db.backref('join_requests', lazy=True, cascade='all, delete-orphan')
    )
    student = db.relationship(
        'User',
        backref=db.backref('class_join_requests', lazy=True, cascade='all, delete-orphan')
    )

    __table_args__ = (
        db.UniqueConstraint('class_id', 'student_id', name='uq_class_join_request_student'),
    )


class Badge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(250), nullable=False)
    icon = db.Column(db.String(50), nullable=False)
    color = db.Column(db.String(50), default="text-warning")

class ExamTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    topic = db.Column(db.String(150))
    week_number = db.Column(db.Integer)
    description = db.Column(db.String(500))
    status = db.Column(db.String(30), default='draft')
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=app_now)
    updated_at = db.Column(db.DateTime, default=app_now, onupdate=app_now)

    teacher = db.relationship('User', backref=db.backref('exam_templates', lazy=True))

class ExamBank(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    topic = db.Column(db.String(150))
    week_number = db.Column(db.Integer)
    description = db.Column(db.String(500))
    variants_json = db.Column(db.Text, nullable=False)
    combined_tex = db.Column(db.Text, nullable=False)
    total_variants = db.Column(db.Integer, default=0)
    total_questions = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=app_now)
    updated_at = db.Column(db.DateTime, default=app_now, onupdate=app_now)

    teacher = db.relationship('User', backref=db.backref('exam_bank_items', lazy=True))

class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500))
    opens_at = db.Column(db.DateTime)
    due_at = db.Column(db.DateTime)
    duration_minutes = db.Column(db.Integer)
    allow_retry = db.Column(db.Boolean, default=False)
    max_attempts = db.Column(db.Integer, default=1)
    retry_cooldown_minutes = db.Column(db.Integer, default=0)
    score_policy = db.Column(db.String(20), default='latest')
    result_release_policy = db.Column(db.String(20), default='immediate')
    results_published = db.Column(db.Boolean, default=False)
    show_answers = db.Column(db.Boolean, default=False)
    shuffle_questions = db.Column(db.Boolean, default=False)
    shuffle_answers = db.Column(db.Boolean, default=False)
    version_hash = db.Column(db.String(64))
    content_tex = db.Column(db.Text, nullable=False)
    questions_json = db.Column(db.Text, nullable=False)
    answer_key_json = db.Column(db.Text, nullable=False)
    total_questions = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=app_now)
    updated_at = db.Column(db.DateTime, default=app_now, onupdate=app_now)

    classroom = db.relationship('Class', backref=db.backref('assignments', lazy=True, cascade='all, delete-orphan'))
    teacher = db.relationship('User', backref=db.backref('created_assignments', lazy=True))


class AssignmentAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    attempt_number = db.Column(db.Integer, default=1)
    questions_json = db.Column(db.Text, nullable=False)
    answer_key_json = db.Column(db.Text, nullable=False)
    answers_json = db.Column(db.Text, default='{}', nullable=False)
    started_at = db.Column(db.DateTime, default=app_now)
    expires_at = db.Column(db.DateTime)
    last_saved_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    is_completed = db.Column(db.Boolean, default=False)
    completion_reason = db.Column(db.String(30))
    correct_count = db.Column(db.Integer, default=0)
    total_questions = db.Column(db.Integer, default=0)
    score = db.Column(db.Float, default=0.0)
    max_score = db.Column(db.Float, default=10.0)
    submission_token = db.Column(db.String(96))
    submission_response_json = db.Column(db.Text)

    assignment = db.relationship('Assignment', backref=db.backref('attempts', lazy=True, cascade='all, delete-orphan'))
    student = db.relationship('User', backref=db.backref('assignment_attempts', lazy=True))


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    answers_json = db.Column(db.Text, nullable=False)
    correct_count = db.Column(db.Integer, default=0)
    total_questions = db.Column(db.Integer, default=0)
    score = db.Column(db.Float, default=0.0)
    max_score = db.Column(db.Float, default=10.0)
    selected_attempt_id = db.Column(db.Integer)
    submitted_at = db.Column(db.DateTime, default=app_now)

    assignment = db.relationship('Assignment', backref=db.backref('submissions', lazy=True, cascade='all, delete-orphan'))
    student = db.relationship('User', backref=db.backref('submissions', lazy=True))

    __table_args__ = (
        db.UniqueConstraint('assignment_id', 'student_id', name='uq_submission_assignment_student'),
    )


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    category = db.Column(db.String(30), default='info')
    link = db.Column(db.String(250))
    dedupe_key = db.Column(db.String(200))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=app_now)

    user = db.relationship('User', backref=db.backref('notifications', lazy=True, cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('user_id', 'dedupe_key', name='uq_notification_user_key'),
    )
