# distributed_lock.py
# موديول إدارة الأقفال الموزعة لكارت الشاشة ومنع تداخل الذاكرة (Distributed GPU Lock Manager)

import time
import uuid
import threading

class DistributedGPULockManager:
    """
    منظم أقفال موزعة متزامن باستخدام Redis لمنع تداخل عمليات نماذج الذكاء الاصطناعي على الذاكرة الرسومية (VRAM).
    يحتوي على تراجع ذكي لأقفال الخيوط المحلية (Thread Locks) في حال غياب خادم Redis.
    """

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.local_locks = {}
        self._local_lock_lock = threading.Lock()

    def _get_local_lock(self, lock_key):
        with self._local_lock_lock:
            if lock_key not in self.local_locks:
                self.local_locks[lock_key] = threading.Lock()
            return self.local_locks[lock_key]

    def acquire_gpu_lock(self, lock_key, token=None, timeout_sec=30, lease_ms=60000):
        """
        حيازة القفل برمجياً بشكل متزامن.
        """
        if token is None:
            token = str(uuid.uuid4())

        # مسار التراجع المحلي في غياب خادم Redis
        if self.redis is None:
            local_lock = self._get_local_lock(lock_key)
            acquired = local_lock.acquire(timeout=timeout_sec)
            return token if acquired else None

        # مسار الأقفال الموزعة عبر Redis
        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            # استخدام الأمر الذري SET key token NX PX لتفادي سباقات التعارض
            if self.redis.set(lock_key, token, nx=True, px=lease_ms):
                # تسجيل تفاصيل الحيازة
                self.redis.hset(f"gpu_allocation:{lock_key}", mapping={
                    "allocated_at": time.time(),
                    "token": token
                })
                return token
            time.sleep(0.05)
            
        return None

    def release_gpu_lock(self, lock_key, token):
        """
        تحرير القفل الموزع باستخدام سكربت Lua الذري لتجنب حذف قفل مخصص لخيط عمل آخر.
        """
        if not token:
            return False

        # مسار التراجع المحلي
        if self.redis is None:
            local_lock = self._get_local_lock(lock_key)
            try:
                local_lock.release()
                return True
            except RuntimeError:
                # إطلاق استثناء في حال كان القفل محرراً بالفعل
                return False

        # مسار تحرير القفل الذري عبر Redis Lua
        lua_release = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            redis.call('del', KEYS[1])
            return 1
        else
            return 0
        end
        """
        try:
            result = self.redis.eval(lua_release, 1, lock_key, token)
            # إزالة سجل الحصص أيضاً
            if result == 1:
                self.redis.delete(f"gpu_allocation:{lock_key}")
            return result == 1
        except Exception as e:
            print(f"⚠️ [Redis Lock Warning] تعذر تحرير القفل عبر Redis: {e}")
            return False
