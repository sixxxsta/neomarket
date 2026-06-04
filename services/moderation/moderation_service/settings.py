import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'moderation-unsafe-dev-key')
DEBUG = os.getenv('DEBUG', '0') == '1'
ALLOWED_HOSTS = [host.strip() for host in os.getenv('ALLOWED_HOSTS', '*').split(',') if host.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_spectacular',
    'moderation_api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'moderation_service.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'moderation_service.wsgi.application'
ASGI_APPLICATION = 'moderation_service.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'moderation_db'),
        'USER': os.getenv('DB_USER', 'neomarket'),
        'PASSWORD': os.getenv('DB_PASSWORD', 'neomarket'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'NeoMarket Moderation Service API',
    'DESCRIPTION': 'Product moderation queue and decisions API.',
    'VERSION': '1.0.0',
}

B2B_PRODUCT_URL_TEMPLATE = os.getenv('B2B_PRODUCT_URL_TEMPLATE', '')
B2B_MODERATION_EVENTS_URL = os.getenv(
    'B2B_MODERATION_EVENTS_URL',
    'http://b2b:8000/api/v1/moderation/events',
)
B2B_REQUEST_TIMEOUT = float(os.getenv('B2B_REQUEST_TIMEOUT', '3.0'))
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
MODERATION_EVENTS_STREAM = os.getenv('MODERATION_EVENTS_STREAM', 'neomarket.events')
MODERATION_EVENTS_GROUP = os.getenv('MODERATION_EVENTS_GROUP', 'moderation')
MODERATION_EVENTS_CONSUMER = os.getenv('MODERATION_EVENTS_CONSUMER', 'moderation-1')
EVENT_SOURCE = 'moderation'
INTERNAL_SERVICE_KEY = os.getenv('INTERNAL_SERVICE_KEY', 'neomarket-internal-key')
MODERATION_IN_REVIEW_TIMEOUT_MINUTES = int(os.getenv('MODERATION_IN_REVIEW_TIMEOUT_MINUTES', '30'))

JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
JWT_SECRET = os.getenv('JWT_SECRET', 'moderation-dev-jwt-secret')
JWT_PUBLIC_KEY = os.getenv('JWT_PUBLIC_KEY', '')
JWT_AUDIENCE = os.getenv('JWT_AUDIENCE', '')
JWT_ISSUER = os.getenv('JWT_ISSUER', '')
