from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

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