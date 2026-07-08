# self_healing.py
# موديول الإصلاح الذاتي والتعديل الديناميكي لعتبات الفرز (Self-Healing & Active Learning Calibration)

import json
import os
import numpy as np

class VLMQueryFallbackHandler:
    """
    معالج الإصلاح الذاتي: يقوم بتحليل الصورة عبر نموذج Vision-Language (Gemini Flash)
    عند فشل عمليات الجلب الأولى لتوليد استعلامات محسنة بدقة وعمق.
    """

    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

    def generate_vlm_fallback_query(self, image_binary):
        """
        إرسال بايتات الصورة لـ Gemini لاستخلاص البراند والاسم والاستعلام المحسن.
        """
        if not self.api_key:
            print("⚠️ [VLM Fallback Warning] لا يوجد مفتاح API لـ Gemini. سيتم تفعيل استعلام المنتج الافتراضي كحل بديل.")
            return "generic product"

        try:
            import google.generativeai as google_gemini
            google_gemini.configure(api_key=self.api_key)
            vision_model = google_gemini.GenerativeModel('gemini-1.5-flash')
            
            prompt = """
            Analyze this product image with high precision. Output a JSON object containing:
            - brand: (Name of the brand if visible)
            - product_name: (Identified name or visual class)
            - color: (Dominant packaging/product color)
            - model_number: (Model code if visible on labels)
            - optimized_search_term: (A clean, search-engine friendly query string of 3-5 keywords)
            """
            
            image_part = {
                "mime_type": "image/jpeg",
                "data": image_binary
            }
            
            response = vision_model.generate_content([prompt, image_part])
            
            # فك وترجمة كتلة الـ JSON المسترجعة
            text_cleaned = response.text.strip()
            # إزالة علامات الاقتباس الخاصة بالـ markdown إن وجدت
            if text_cleaned.startswith("```json"):
                text_cleaned = text_cleaned[7:]
            if text_cleaned.endswith("```"):
                text_cleaned = text_cleaned[:-3]
            text_cleaned = text_cleaned.strip()
            
            metadata = json.loads(text_cleaned)
            term = metadata.get("optimized_search_term", "")
            if term:
                print(f"✅ [VLM Fallback Success] تم استخلاص استعلام بحث محسن بنجاح: '{term}'")
                return term
        except Exception as e:
            print(f"⚠️ [VLM Fallback Error] فشل المعالجة عبر VLM: {e}")
            
        return "generic product"


class ActiveLearningCalibrator:
    """
    معاير عتبات الفرز (Threshold Self-Tuning) باستخدام التعلم النشط
    بناءً على عمليات التعديل اليدوية التي يجريها المشرف البشري (Overrides).
    """

    @staticmethod
    def re_calibrate_validation_parameters(redis_client, feedback_batch_key="config:human_feedback"):
        """
        سحب تقارير التقييمات البشرية وتحديث الأوزان والتحيز برمجياً عبر انحدار اللوجستك.
        """
        raw_feedback = []
        
        # 1. سحب البيانات من خادم Redis إن وجد
        if redis_client is not None:
            try:
                raw_feedback = redis_client.lrange(feedback_batch_key, 0, -1)
            except Exception as e:
                print(f"⚠️ [Active Learning Warning] تعذر سحب البيانات من Redis: {e}")
        
        # التراجع للملف المحلي إن كان Redis غير متاح
        if not raw_feedback:
            local_feedback_file = "human_feedback_local.json"
            if os.path.exists(local_feedback_file):
                try:
                    with open(local_feedback_file, "r", encoding="utf-8") as f:
                        raw_feedback = json.load(f)
                except Exception:
                    raw_feedback = []

        if len(raw_feedback) < 10:  # حد أدنى صغير للاختبار المحلي (الإنتاجي يفضل > 100)
            print("⏳ [Active Learning] عينات التعديل البشري غير كافية للمعايرة النشطة حالياً.")
            return None, None

        X_train = []
        y_train = []
        
        for item in raw_feedback:
            try:
                record = json.loads(item) if isinstance(item, (str, bytes)) else item
                features = [
                    float(record.get("aesthetic_score", 5.0)),
                    float(record.get("sharpness_brenner", 500.0)),
                    float(record.get("product_fill_ratio", 0.85)),
                    float(record.get("background_purity", 0.90)),
                    float(record.get("dinov2_similarity", 0.95))
                ]
                X_train.append(features)
                y_train.append(int(record.get("human_override_approved", 1)))
            except Exception as parse_err:
                print(f"⚠️ [Active Learning] خطأ أثناء تفكيك السجل: {parse_err}")
                
        if len(X_train) < 5:
            return None, None

        X_train = np.array(X_train)
        y_train = np.array(y_train)

        try:
            from sklearn.linear_model import LogisticRegression
            
            # تدريب نموذج اللوجستك لمعايرة قيم الحدود الديناميكية تلقائياً
            calibration_model = LogisticRegression(class_weight='balanced')
            calibration_model.fit(X_train, y_train)
            
            calibrated_weights = calibration_model.coef_[0].tolist()
            calibrated_intercept = float(calibration_model.intercept_[0])
            
            print(f"⚙️ [Active Learning Calibration Completed] الأوزان المحسنة: {calibrated_weights}, التحيز: {calibrated_intercept:.4f}")
            
            # حفظ التحديثات في Redis
            if redis_client is not None:
                try:
                    redis_client.set("config:gate_weights", json.dumps(calibrated_weights))
                    redis_client.set("config:gate_intercept", str(calibrated_intercept))
                except Exception as e:
                    print(f"⚠️ [Active Learning Error] فشل حفظ القيم في Redis: {e}")
                    
            # حفظ التحديثات في ملف محلي كنسخة احتياطية
            with open("calibrated_gate_config.json", "w") as f:
                json.dump({
                    "weights": calibrated_weights,
                    "intercept": calibrated_intercept,
                    "calibrated_at": time.time() if "time" in globals() else 0.0
                }, f, indent=4)
                
            return calibrated_weights, calibrated_intercept
            
        except ImportError:
            print("⚠️ [Active Learning Warning] مكتبة scikit-learn غير مثبتة. تعذر تشغيل انحدار اللوجستك للمعايرة.")
            return None, None
