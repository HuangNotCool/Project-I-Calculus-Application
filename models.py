from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

# Bảng trung gian lưu trữ Học sinh sở hữu Huy hiệu nào
user_badges = db.Table('user_badges',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('badge_id', db.Integer, db.ForeignKey('badge.id'), primary_key=True)
)

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False)
    
    # Liên kết: Một user có thể có nhiều badge
    badges = db.relationship('Badge', secondary=user_badges, lazy='subquery',
                             backref=db.backref('users', lazy=True))

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
    
    # Quan hệ nhiều-nhiều với User (Lấy ra danh sách học sinh)
    students = db.relationship('User', secondary=student_classes, lazy='subquery',
        backref=db.backref('enrolled_classes', lazy=True))

class Badge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    teacher = db.relationship('User', backref=db.backref('exam_bank_items', lazy=True))

class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500))
    due_at = db.Column(db.DateTime)
    content_tex = db.Column(db.Text, nullable=False)
    questions_json = db.Column(db.Text, nullable=False)
    answer_key_json = db.Column(db.Text, nullable=False)
    total_questions = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    classroom = db.relationship('Class', backref=db.backref('assignments', lazy=True, cascade='all, delete-orphan'))
    teacher = db.relationship('User', backref=db.backref('created_assignments', lazy=True))

class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    answers_json = db.Column(db.Text, nullable=False)
    correct_count = db.Column(db.Integer, default=0)
    total_questions = db.Column(db.Integer, default=0)
    score = db.Column(db.Float, default=0.0)
    max_score = db.Column(db.Float, default=10.0)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

    assignment = db.relationship('Assignment', backref=db.backref('submissions', lazy=True, cascade='all, delete-orphan'))
    student = db.relationship('User', backref=db.backref('submissions', lazy=True))

    __table_args__ = (
        db.UniqueConstraint('assignment_id', 'student_id', name='uq_submission_assignment_student'),
    )
