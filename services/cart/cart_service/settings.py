import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'cart-unsafe-dev-key')
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
    'cart_api',
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

ROOT_URLCONF = 'cart_service.urls'

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

WSGI_APPLICATION = 'cart_service.wsgi.application'
ASGI_APPLICATION = 'cart_service.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'cart_db'),
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
    'TITLE': 'NeoMarket Cart Service API',
    'DESCRIPTION': 'Bootstrap service for NeoMarket microservice architecture.',
    'VERSION': '0.1.0',
}

CATALOG_PRODUCTS_URL = os.getenv('CATALOG_PRODUCTS_URL', 'http://catalog:8000/api/v1/products')
CATALOG_TIMEOUT = float(os.getenv('CATALOG_TIMEOUT', '3.0'))
INTERNAL_SERVICE_KEY = os.getenv('INTERNAL_SERVICE_KEY', 'neomarket-internal-key')

JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
JWT_SECRET = os.getenv('JWT_SECRET', SECRET_KEY)
JWT_PUBLIC_KEY = os.getenv('JWT_PUBLIC_KEY', '')
JWT_AUDIENCE = os.getenv('JWT_AUDIENCE', '')
JWT_ISSUER = os.getenv('JWT_ISSUER', '')

