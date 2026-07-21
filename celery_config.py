# تكوين بيئة الإنتاج الموزعة لـ Celery و Redis وفق معايير الأنظمة عالية الإنتاجية
import os

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672//")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    from celery import Celery
    celery_app = Celery(
        'image_ingestion_pipeline',
        broker=REDIS_URL,
        backend=REDIS_URL
    )
except ImportError:
    class DummyCeleryConf(dict):
        def update(self, *args, **kwargs):
            dict.update(self, *args, **kwargs)
        def __getattr__(self, name):
            return self.get(name)

    class DummyCelery:
        def __init__(self):
            self.conf = DummyCeleryConf()

    celery_app = DummyCelery()



# إعداد ملف تكوين الحماية والتوزيع الكلي لطوابير المهام
celery_app.conf.update(
    # معايير الترميز والأمان
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='Asia/Riyadh',
    enable_utc=True,

    # معايرة الإنتاجية لمنع تضخم الذاكرة واحتكار المهام
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=300,

    # سياسة إعادة المحاولة التلقائية عند انقطاع الشبكة مع الخادم الوسيط
    task_publish_retry=True,
    task_publish_retry_policy={
        'max_retries': 5,
        'interval_start': 0.5,
        'interval_step': 0.5,
        'interval_max': 5.0,
    },

    # توجيه وقنوات المهام لضمان عزل استهلاك المعالجات وكارت الشاشة (Queue Isolation)
    task_routes={
        'tasks.crawler.fetch_images': {'queue': 'io_bound_crawler'},
        'tasks.image.process_enhancements': {'queue': 'gpu_bound_enhancer'},
        'tasks.vector.extract_embeddings': {'queue': 'gpu_bound_vectorizer'},
        'tasks.dedup.check_phash': {'queue': 'cpu_bound_dedup'},
    },

    # الحدود الزمنية للمهام لتفادي الدخول في حلقات تشغيل لا نهائية
    task_soft_time_limit=180,  # حد التنبيه والتحذير (ثواني)
    task_time_limit=210,       # حد الإغلاق والإنهاء الإجباري للمهمة (ثواني)
)
