# config.py
import os
from decouple import config

class Config:
    # Security - from environment variables
    SECRET_KEY = config('SECRET_KEY', default='dev-super-secret-key-32-characters-minimum')
    
    # Database - from environment variable or default
    SQLALCHEMY_DATABASE_URI = config('DATABASE_URL', default='sqlite:///greenthumb.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Email Configuration - from environment variables
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USE_SSL = False
    MAIL_USERNAME = config('MAIL_USERNAME', default='')
    MAIL_PASSWORD = config('MAIL_PASSWORD', default='')
    MAIL_DEFAULT_SENDER = config('MAIL_USERNAME', default='')
    

    SERVER_NAME = None
    
    # Scheduler settings (disable in production, use Heroku Scheduler)
    SCHEDULER_ENABLED = config('SCHEDULER_ENABLED', default=True, cast=bool)
    
    # Performance optimization
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
    }

class DevelopmentConfig(Config):
    DEBUG = True
    SCHEDULER_ENABLED = True

class ProductionConfig(Config):
    DEBUG = False
    SCHEDULER_ENABLED = False

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False