import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'catalog-unsafe-dev-key')
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
    'catalog_api',
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

ROOT_URLCONF = 'catalog_service.urls'

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

WSGI_APPLICATION = 'catalog_service.wsgi.application'
ASGI_APPLICATION = 'catalog_service.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'catalog_db'),
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
    'TITLE': 'NeoMarket Catalog Service API',
    'DESCRIPTION': 'Bootstrap service for NeoMarket microservice architecture.',
    'VERSION': '0.1.0',
}

REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
EVENT_STREAM = os.getenv('EVENT_STREAM', 'neomarket.events')
EVENT_GROUP = os.getenv('EVENT_GROUP', 'catalog')
EVENT_CONSUMER = os.getenv('EVENT_CONSUMER', 'catalog-1')
INTERNAL_SERVICE_KEY = os.getenv('INTERNAL_SERVICE_KEY', 'neomarket-internal-key')
B2B_PRODUCTS_URL = os.getenv('B2B_PRODUCTS_URL', 'http://b2b:8000/api/v1/public/products')
B2B_TIMEOUT = float(os.getenv('B2B_TIMEOUT', '5.0'))

