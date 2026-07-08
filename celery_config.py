# celery_config.py
# ملف تكوين مهام طوابير العمل الموزعة Celery (قنوات المهام ومستويات الحماية)

from celery import Celery
import os

# تكوين مسارات الخوادم الوسيطة (RabbitMQ و Redis)
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672//")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    'image_ingestion_pipeline',
    broker=RABBITMQ_URL,
    backend=REDIS_URL
)

# إعداد ملف تكوين الحماية والتوزيع الكلي لطوابير المهام
celery_app.conf.update(
    # إبقاء المهام في طابور العمل حتى يتم تأكيد انتهائها بنجاح من خيوط التنفيذ
    task_acks_late=True,
    
    # إعادة إدراج المهمة تلقائياً في حال فقدان أو انهيار خيط العمل المشغل
    task_reject_on_worker_lost=True,
    
    # تحديد استرجاع المهام لمرة واحدة فقط لكل عامل لضمان عدالة توزيع الحِمل
    worker_prefetch_multiplier=1,
    
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
        'pipeline.tasks.harvest_metadata': {'queue': 'io_queue'},
        'pipeline.tasks.validate_and_embed': {'queue': 'gpu_queue'},
        'pipeline.tasks.upload_and_sync': {'queue': 'api_queue'},
    },
    
    # الحدود الزمنية للمهام لتفادي الدخول في حلقات تشغيل لا نهائية
    task_soft_time_limit=180,  # حد التنبيه والتحذير (ثواني)
    task_time_limit=210,       # حد الإغلاق والإنهاء الإجباري للمهمة (ثواني)
)
