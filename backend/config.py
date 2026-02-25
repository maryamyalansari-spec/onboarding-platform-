import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Flask
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")
    ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = ENV == "development"

    # Database
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:password@localhost:5432/itifaq_onboarding"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # Redis / Celery
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL = REDIS_URL
    CELERY_RESULT_BACKEND = REDIS_URL

    # OpenAI (Whisper transcription)
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

    # Anthropic (AI analysis)
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

    # Twilio
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "")
    PORTAL_BASE_URL = os.environ.get("PORTAL_BASE_URL", "http://localhost:5000")

    # SendGrid
    SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")
    SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "noreply@itifaq.ae")

    # Calendly
    CALENDLY_API_KEY        = os.environ.get("CALENDLY_API_KEY", "")
    CALENDLY_LINK           = os.environ.get("CALENDLY_LINK", "")        # e.g. https://calendly.com/firm/consultation
    CALENDLY_WEBHOOK_SECRET = os.environ.get("CALENDLY_WEBHOOK_SECRET", "")

    # DocuSeal
    DOCUSEAL_URL = os.environ.get("DOCUSEAL_URL", "http://localhost:3000")
    DOCUSEAL_API_KEY = os.environ.get("DOCUSEAL_API_KEY", "")

    # File uploads
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 52428800))  # 50 MB
    ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}
    ALLOWED_DOCUMENT_EXTENSIONS = {"pdf", "doc", "docx", "jpg", "jpeg", "png"}
    ALLOWED_AUDIO_EXTENSIONS = {"mp3", "mp4", "wav", "ogg", "webm", "m4a", "ogg"}

    # Security
    TOKEN_EXPIRY_DAYS = int(os.environ.get("TOKEN_EXPIRY_DAYS", 30))
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "").encode() or None

    # Session
    SESSION_TYPE = "redis"
    PERMANENT_SESSION_LIFETIME = 86400 * 7  # 7 days


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
