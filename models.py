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

class Badge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(250), nullable=False)
    icon = db.Column(db.String(50), nullable=False)
    color = db.Column(db.String(50), default="text-warning")