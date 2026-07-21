@extends('layouts.layout')

@section('title', '🛍️ معرض المنتجات والبيانات الغنية')
@section('nav_rich_catalog', 'active')

@section('styles')
<style>
    .rich-catalog-container {
        direction: rtl;
        text-align: right;
    }

    .filter-bar {
        display: flex;
        gap: 1rem;
        flex-wrap: wrap;
        margin-bottom: 2rem;
        background: var(--input-bg);
        border: 1px solid var(--panel-border);
        padding: 1.25rem;
        border-radius: 16px;
    }

    .filter-bar input, .filter-bar select {
        padding: 0.75rem 1.25rem;
        background: rgba(0,0,0,0.25);
        border: 1px solid var(--panel-border);
        color: var(--text-primary);
        border-radius: 12px;
        font-family: inherit;
        outline: none;
        transition: all 0.3s;
    }

    .filter-bar input:focus, .filter-bar select:focus {
        border-color: var(--accent-purple);
        box-shadow: 0 0 15px var(--btn-shadow);
    }

    .rich-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
        gap: 1.75rem;
    }

    .rich-card {
        background: var(--card-bg);
        border: 1px solid var(--panel-border);
        border-radius: 16px;
        overflow: hidden;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        display: flex;
        flex-direction: column;
        height: 100%;
        position: relative;
    }

    .rich-card:hover {
        transform: translateY(-6px);
        border-color: var(--accent-cyan);
        box-shadow: 0 12px 30px var(--btn-shadow);
    }

    .rich-card-img {
        height: 200px;
        background: var(--img-box-bg);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 1rem;
        position: relative;
        border-bottom: 1px solid var(--panel-border);
    }

    .rich-card-img img {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
        transition: transform 0.4s;
    }

    .rich-card:hover .rich-card-img img {
        transform: scale(1.05);
    }

    .rich-card-body {
        padding: 1.25rem;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
        flex: 1;
    }

    .score-badge-rich {
        position: absolute;
        top: 12px;
        left: 12px;
        background: var(--success);
        color: var(--btn-text);
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.7rem;
        font-weight: 800;
        font-family: 'Outfit', sans-serif;
    }

    .category-badge-rich {
        align-self: flex-start;
        background: var(--active-menu-bg);
        border: 1px solid var(--panel-border);
        color: var(--text-primary);
        padding: 2px 8px;
        font-size: 0.7rem;
        font-weight: 800;
        border-radius: 6px;
        margin-bottom: 0.25rem;
    }

    /* Modal Layouts */
    .modal-rich-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 1.5rem;
    }

    @media (max-width: 768px) {
        .modal-rich-grid {
            grid-template-columns: 1fr;
        }
    }

    .nutrition-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 0.75rem;
        margin-top: 0.5rem;
    }

    .nutrition-item {
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--panel-border);
        border-radius: 8px;
        padding: 0.5rem 0.75rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    .nutrition-val {
        font-family: 'Outfit', sans-serif;
        font-weight: 800;
        color: var(--accent-cyan);
    }

    .allergen-warning {
        background: var(--danger-bg);
        border: 1px solid var(--panel-border);
        color: var(--danger);
        padding: 0.75rem 1rem;
        border-radius: 10px;
        font-size: 0.85rem;
        font-weight: bold;
        margin-top: 0.75rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    .bg-checkerboard-rich {
        background-color: #111111;
        background-image: linear-gradient(45deg, #222222 25%, transparent 25%), 
                          linear-gradient(-45deg, #222222 25%, transparent 25%), 
                          linear-gradient(45deg, transparent 75%, #222222 75%), 
                          linear-gradient(-45deg, transparent 75%, #222222 75%);
        background-size: 20px 20px;
        background-position: 0 0, 0 10px, 10px -10px, -10px 0px;
    }
</style>
@endsection

@section('content')
<div class="rich-catalog-container">
    
    <!-- Banner -->
    <div class="glass-panel" style="padding: 1.5rem 2rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;">
        <div>
            <h2 style="font-size: 1.4rem; font-weight: 900; margin: 0; color: var(--text-primary); display: flex; align-items: center; gap: 0.65rem;">
                <i class="fas fa-store" style="color: var(--accent-purple-hover);"></i> معرض المنتجات والبيانات الغنية
            </h2>
            <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.25rem;">
                تصفح المنتجات المستخلصة والمكتملة بنجاح مع استعراض كامل للقيم الغذائية والمكونات وأوصاف الذكاء الاصطناعي.
            </p>
        </div>
        
        <div style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;">
            <!-- Export buttons -->
            <div style="display: flex; gap: 0.5rem;">
                <a href="{{ route('dashboard.rich_catalog.export') }}" class="btn btn-secondary" style="font-size: 0.8rem; padding: 0.5rem 1rem; font-weight: bold; background: rgba(34, 197, 94, 0.1); border-color: rgba(34, 197, 94, 0.2); color: #22c55e;">
                    <i class="fas fa-file-csv"></i> تصدير CSV
                </a>
                <a href="{{ route('dashboard.rich_catalog.export', ['format' => 'json']) }}" class="btn btn-secondary" style="font-size: 0.8rem; padding: 0.5rem 1rem; font-weight: bold; background: rgba(59, 130, 246, 0.1); border-color: rgba(59, 130, 246, 0.2); color: #3b82f6;">
                    <i class="fas fa-code"></i> تصدير JSON
                </a>
            </div>
            
            <span class="score-badge" id="catalogCount" style="background: var(--active-menu-bg); border-color: var(--panel-border); color: var(--text-primary); font-size: 1rem; padding: 0.5rem 1.25rem; border-radius: 12px; font-weight: 900; margin: 0;">
                جاري التحميل...
            </span>
        </div>
    </div>

    <!-- Filters -->
    <div class="filter-bar">
        <input type="text" id="richSearch" onkeyup="filterRichCatalog()" placeholder="🔍 ابحث بالاسم، البراند، الباركود أو المكونات..." style="flex: 2; min-width: 250px;">
        <select id="categoryFilter" onchange="filterRichCatalog()" style="flex: 1; min-width: 180px;">
            <option value="">جميع التصنيفات</option>
        </select>
        <select id="scoreFilter" onchange="filterRichCatalog()" style="flex: 1; min-width: 150px;">
            <option value="">جميع نسب المطابقة</option>
            <option value="90">+90% مطابقة عالية</option>
            <option value="80">+80% مطابقة جيدة</option>
            <option value="70">+70% مطابقة مقبولة</option>
        </select>
    </div>

    <!-- Loading -->
    <div id="richCatalogLoading" class="glass-panel" style="text-align: center; padding: 5rem 2rem;">
        <i class="fas fa-spinner fa-spin" style="font-size: 3rem; color: var(--accent-purple); margin-bottom: 1.5rem;"></i>
        <h3>جاري جلب المنتجات المكتملة وتجميع الكتالوج...</h3>
    </div>

    <!-- Grid Container -->
    <div class="rich-grid" id="richCatalogGrid" style="display: none;"></div>

</div>

<!-- Rich Modal popup -->
<div id="richModal" class="modal">
    <div class="glass-panel" style="max-width: 900px; width: 100%; border-radius: 20px; padding: 2rem; position: relative;">
        <!-- Close button -->
        <button type="button" onclick="closeRichModal()" style="position: absolute; top: 1.5rem; left: 1.5rem; background: rgba(255,255,255,0.05); border: 1px solid var(--panel-border); color: var(--text-primary); width: 35px; height: 35px; border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: all 0.25s;">
            <i class="fas fa-times"></i>
        </button>

        <h3 style="font-size: 1.3rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.5rem; color: var(--accent-purple-hover);">
            <i class="fas fa-edit"></i> تعديل وتدقيق البيانات الغنية للمنتج
        </h3>

        <div class="modal-rich-grid">
            <!-- Col 1: Visuals & Core info inputs -->
            <div style="display: flex; flex-direction: column; gap: 1rem;">
                <div id="modalRichImgContainer" class="bg-checkerboard-rich" style="height: 250px; border-radius: 12px; border: 1px solid var(--panel-border); display: flex; align-items: center; justify-content: center; padding: 1rem; position: relative; transition: all 0.3s;">
                    <img id="modalRichImg" src="" alt="Resolved image" style="max-width: 100%; max-height: 100%; object-fit: contain;">
                </div>
                
                <div id="modalBgRemovalWarning" class="allergen-warning" style="display: none; padding: 0.5rem 0.75rem; margin-top: 0; font-size: 0.75rem; background: rgba(239, 68, 68, 0.1); border-color: rgba(239, 68, 68, 0.2); color: #ef4444;">
                    <i class="fas fa-exclamation-triangle"></i>
                    <span>تنبيه: فشلت إزالة الخلفية لهذه الصورة وتم رفعها كصورة خام.</span>
                </div>
                
                <!-- Background Toggles -->
                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; background: rgba(0,0,0,0.15); padding: 0.5rem 1rem; border-radius: 8px;">
                    <span style="color: var(--text-secondary); font-weight: bold;">خلفية المعاينة:</span>
                    <div style="display: flex; gap: 0.5rem;">
                        <button type="button" class="btn btn-secondary btn-sm" onclick="setPreviewBg('white')" style="padding: 2px 8px; font-size: 0.7rem;">أبيض</button>
                        <button type="button" class="btn btn-secondary btn-sm" onclick="setPreviewBg('dark')" style="padding: 2px 8px; font-size: 0.7rem;">مظلم</button>
                        <button type="button" class="btn btn-secondary btn-sm" onclick="setPreviewBg('checker')" style="padding: 2px 8px; font-size: 0.7rem;">مربعات</button>
                    </div>
                </div>

                <!-- Product IDs Inputs -->
                <div style="display: flex; flex-direction: column; gap: 0.85rem; border-top: 1px solid var(--panel-border); padding-top: 1rem;">
                    <div class="form-group" style="margin-bottom: 0;">
                        <label class="form-label" style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.25rem;">اسم المنتج المعتمد:</label>
                        <input type="text" id="editRichName" class="form-control" style="font-size: 0.85rem; padding: 0.45rem 0.75rem;">
                    </div>
                    <div class="form-group" style="margin-bottom: 0;">
                        <label class="form-label" style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.25rem;">العلامة التجارية (Brand):</label>
                        <input type="text" id="editRichBrand" class="form-control" style="font-size: 0.85rem; padding: 0.45rem 0.75rem;">
                    </div>
                    <div class="form-group" style="margin-bottom: 0;">
                        <label class="form-label" style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.25rem;">الباركود (Barcode):</label>
                        <input type="text" id="editRichBarcode" class="form-control" style="font-size: 0.85rem; padding: 0.45rem 0.75rem; font-family: monospace; background: rgba(0,0,0,0.2);" readonly disabled>
                    </div>
                    <div style="font-size: 0.75rem; color: var(--text-secondary); font-weight: bold;">
                        تاريخ الأرشفة بالخادم: <span id="modalRichDate" style="color: var(--text-primary); font-family: 'Outfit', sans-serif;"></span>
                    </div>
                </div>
            </div>

            <!-- Col 2: Metadata panels editable -->
            <div style="display: flex; flex-direction: column; gap: 1.25rem; max-height: 520px; overflow-y: auto; padding-inline-end: 0.5rem;">
                <!-- Web Category -->
                <div>
                    <span style="font-weight: 800; font-size: 0.9rem; color: var(--text-secondary); display: block; margin-bottom: 0.5rem;">التصنيفات المعتمدة بالمتجر (مستويات 1 ⬅️ 2 ⬅️ 3):</span>
                    <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0.5rem;">
                        <input type="text" id="editRichCatL1" class="form-control" style="font-size: 0.8rem; padding: 0.45rem 0.6rem;" placeholder="التصنيف الرئيسي L1">
                        <input type="text" id="editRichCatL2" class="form-control" style="font-size: 0.8rem; padding: 0.45rem 0.6rem;" placeholder="التصنيف الفرعي L2">
                        <input type="text" id="editRichCatL3" class="form-control" style="font-size: 0.8rem; padding: 0.45rem 0.6rem;" placeholder="التصنيف التفصيلي L3">
                    </div>
                </div>

                <!-- Marketing Text -->
                <div>
                    <span style="font-weight: 800; font-size: 0.9rem; color: var(--text-secondary); display: block; margin-bottom: 0.35rem;">الوصف التسويقي الاحترافي (باللغة العربية):</span>
                    <textarea id="editRichDescAr" class="form-control" rows="3" style="font-size: 0.85rem; padding: 0.5rem; resize: vertical; line-height: 1.5;" placeholder="أدخل الوصف التسويقي للمنتج..."></textarea>
                </div>

                <!-- Ingredients -->
                <div>
                    <span style="font-weight: 800; font-size: 0.9rem; color: var(--text-secondary); display: block; margin-bottom: 0.35rem;">المكونات والتركيب (Ingredients):</span>
                    <textarea id="editRichIngredients" class="form-control" rows="3" style="font-size: 0.85rem; padding: 0.5rem; resize: vertical; line-height: 1.5;" placeholder="حليب، صويا، سكر..." oninput="checkModalAllergenWarning()"></textarea>
                    <div id="modalAllergenWarning" class="allergen-warning" style="display: none; padding: 0.5rem 0.75rem; margin-top: 0.5rem; font-size: 0.75rem;">
                        <i class="fas fa-exclamation-circle"></i>
                        <span>تنبيه: يحتوي المنتج على مسببات حساسية مكتشفة (قمح/حليب/صويا/مكسرات).</span>
                    </div>
                </div>

                <!-- Nutritional values inputs -->
                <div>
                    <span style="font-weight: 800; font-size: 0.9rem; color: var(--text-secondary); display: block; margin-bottom: 0.5rem;">القيم الغذائية لكل 100 جرام:</span>
                    <div class="nutrition-grid">
                        <div class="nutrition-item" style="padding: 0.35rem 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">السعرات (kcal):</span>
                            <input type="text" id="editRichCalories" style="width: 70px; background: rgba(0,0,0,0.3); border: 1px solid var(--panel-border); color: var(--accent-cyan); text-align: center; border-radius: 4px; font-size: 0.8rem; font-weight: bold; font-family: monospace; outline: none;">
                        </div>
                        <div class="nutrition-item" style="padding: 0.35rem 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">الكربوهيدرات (g):</span>
                            <input type="text" id="editRichCarbs" style="width: 70px; background: rgba(0,0,0,0.3); border: 1px solid var(--panel-border); color: var(--accent-cyan); text-align: center; border-radius: 4px; font-size: 0.8rem; font-weight: bold; font-family: monospace; outline: none;">
                        </div>
                        <div class="nutrition-item" style="padding: 0.35rem 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">السكريات (g):</span>
                            <input type="text" id="editRichSugars" style="width: 70px; background: rgba(0,0,0,0.3); border: 1px solid var(--panel-border); color: var(--accent-cyan); text-align: center; border-radius: 4px; font-size: 0.8rem; font-weight: bold; font-family: monospace; outline: none;">
                        </div>
                        <div class="nutrition-item" style="padding: 0.35rem 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">البروتين (g):</span>
                            <input type="text" id="editRichProtein" style="width: 70px; background: rgba(0,0,0,0.3); border: 1px solid var(--panel-border); color: var(--accent-cyan); text-align: center; border-radius: 4px; font-size: 0.8rem; font-weight: bold; font-family: monospace; outline: none;">
                        </div>
                        <div class="nutrition-item" style="padding: 0.35rem 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">الدهون الكلية (g):</span>
                            <input type="text" id="editRichFat" style="width: 70px; background: rgba(0,0,0,0.3); border: 1px solid var(--panel-border); color: var(--accent-cyan); text-align: center; border-radius: 4px; font-size: 0.8rem; font-weight: bold; font-family: monospace; outline: none;">
                        </div>
                        <div class="nutrition-item" style="padding: 0.35rem 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">الدهون المشبعة (g):</span>
                            <input type="text" id="editRichSaturatedFat" style="width: 70px; background: rgba(0,0,0,0.3); border: 1px solid var(--panel-border); color: var(--accent-cyan); text-align: center; border-radius: 4px; font-size: 0.8rem; font-weight: bold; font-family: monospace; outline: none;">
                        </div>
                        <div class="nutrition-item" style="padding: 0.35rem 0.5rem; display: flex; justify-content: space-between; align-items: center;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">الملح (g):</span>
                            <input type="text" id="editRichSalt" style="width: 70px; background: rgba(0,0,0,0.3); border: 1px solid var(--panel-border); color: var(--accent-cyan); text-align: center; border-radius: 4px; font-size: 0.8rem; font-weight: bold; font-family: monospace; outline: none;">
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 1.5rem; border-top: 1px solid var(--panel-border); padding-top: 1.25rem; flex-wrap: wrap; gap: 1rem;">
            <a id="modalCloudinaryLink" href="" target="_blank" class="btn btn-secondary" style="font-size: 0.85rem;">
                <i class="fas fa-external-link-alt"></i> فتح الرابط الأصلي بـ Cloudinary
            </a>
            <div style="display: flex; gap: 0.75rem;">
                <button type="button" class="btn btn-secondary" onclick="closeRichModal()" style="padding: 0.5rem 1.5rem; font-weight: bold;">إلغاء</button>
                <button type="button" class="btn" onclick="saveRichChanges()" style="padding: 0.5rem 2rem; font-weight: bold; background: var(--accent-gradient); color: var(--btn-text);">
                    <i class="fas fa-save"></i> حفظ التغييرات
                </button>
            </div>
        </div>
    </div>
</div>

@endsection

@section('scripts')
<script>
    let richProducts = [];
    let currentEditingBarcode = '';

    window.addEventListener('load', async () => {
        await loadRichProducts();
    });

    async function loadRichProducts() {
        try {
            const res = await fetch('/api/rich-products');
            const data = await res.json();
            
            if (data.status === 'success') {
                richProducts = data.products;
                document.getElementById('catalogCount').innerText = `إجمالي المنتجات الموثقة: ${richProducts.length}`;
                
                // Populate categories filter
                populateCategoryFilter();
                
                renderRichCatalog(richProducts);
            } else {
                alert('فشل تحميل المنتجات: ' + data.error);
            }
        } catch (err) {
            console.error(err);
            alert('خطأ اتصال بالخادم أثناء جلب الكتالوج.');
        } finally {
            document.getElementById('richCatalogLoading').style.display = 'none';
            document.getElementById('richCatalogGrid').style.display = 'grid';
        }
    }

    function populateCategoryFilter() {
        const filter = document.getElementById('categoryFilter');
        // Clear previous except default
        filter.innerHTML = '<option value="">جميع التصنيفات</option>';
        const categories = new Set();
        
        richProducts.forEach(p => {
            if (p.metadata_json) {
                let meta = p.metadata_json;
                if (typeof meta === 'string') {
                    try { meta = JSON.parse(meta); } catch (e) { meta = {}; }
                }
                const cat1 = meta.web_category_l1 || meta.category;
                if (cat1) categories.add(cat1);
            }
        });

        categories.forEach(cat => {
            const opt = document.createElement('option');
            opt.value = cat;
            opt.innerText = cat;
            filter.appendChild(opt);
        });
    }

    function renderRichCatalog(products) {
        const grid = document.getElementById('richCatalogGrid');
        grid.innerHTML = '';

        if (products.length === 0) {
            grid.innerHTML = '<p style="color: var(--text-secondary); text-align: center; grid-column: 1/-1; padding: 4rem; font-weight: bold;">🔍 لم يتم العثور على أي منتجات مطابقة لخيارات الفرز الحالية.</p>';
            return;
        }

        products.forEach(p => {
            const card = document.createElement('div');
            card.className = 'rich-card';
            
            let meta = p.metadata_json || {};
            if (typeof meta === 'string') {
                try { meta = JSON.parse(meta) || {}; } catch (e) { meta = {}; }
            }

            const catText = meta.web_category_l1 || meta.category || 'غير مصنف';
            const scorePercent = p.clip_score ? Math.round(p.clip_score * 100) : 0;
            const scoreHtml = scorePercent > 0 ? `<div class="score-badge-rich">${scorePercent}% Match</div>` : '';

            // Allergen visual scan on card
            const ingredientsText = (meta.ingredients || '').toLowerCase();
            let allergenBadges = '';
            if (ingredientsText.includes('milk') || ingredientsText.includes('حليب')) {
                allergenBadges += '<span title="يحتوي على حليب" style="cursor:help;">🥛</span> ';
            }
            if (ingredientsText.includes('wheat') || ingredientsText.includes('gluten') || ingredientsText.includes('قمح') || ingredientsText.includes('جلوتين')) {
                allergenBadges += '<span title="يحتوي على قمح/جلوتين" style="cursor:help;">🌾</span> ';
            }
            if (ingredientsText.includes('soy') || ingredientsText.includes('صويا')) {
                allergenBadges += '<span title="يحتوي على صويا" style="cursor:help;">🫘</span> ';
            }
            if (ingredientsText.includes('nut') || ingredientsText.includes('peanut') || ingredientsText.includes('hazelnut') || ingredientsText.includes('مكسرات') || ingredientsText.includes('فول سوداني')) {
                allergenBadges += '<span title="يحتوي على مكسرات" style="cursor:help;">🥜</span> ';
            }
            const allergensHtml = allergenBadges ? `<div style="display: flex; gap: 0.25rem; font-size: 1.15rem;" class="allergen-badges-card">${allergenBadges}</div>` : '';
            const bgRemovalFailedHtml = meta.bg_removal_status === 'failed' ? '<div style="position: absolute; bottom: 8px; left: 8px; background: rgba(239, 68, 68, 0.9); color: white; padding: 4px 8px; border-radius: 6px; font-size: 0.65rem; font-weight: bold; z-index: 10; display: flex; align-items: center; gap: 0.25rem; font-family: \'Outfit\', sans-serif;"><i class="fas fa-exclamation-triangle"></i> بدون عزل</div>' : '';

            card.innerHTML = `
                <div class="rich-card-img" style="position: relative;">
                    ${scoreHtml}
                    ${bgRemovalFailedHtml}
                    <img src="${p.cloudinary_url}" alt="Product Image" onerror="this.src='https://placehold.co/200x200?text=Error'">
                </div>
                <div class="rich-card-body">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; margin-bottom: 0.25rem; gap: 0.5rem;">
                        <span class="category-badge-rich" style="margin-bottom: 0;">${catText}</span>
                        ${allergensHtml}
                    </div>
                    <h4 style="font-size: 0.95rem; font-weight: 800; margin: 0; color: var(--text-primary); line-height: 1.4; margin-top: 0.35rem;" title="${p.product_name}">${p.product_name}</h4>
                    <p style="font-size: 0.8rem; color: var(--text-secondary); margin: 0; font-weight: 600; margin-top: 0.25rem;">البراند: <strong style="color: var(--text-primary);">${p.brand}</strong></p>
                    <div style="margin-top: auto; padding-top: 1rem; border-top: 1px solid rgba(255,255,255,0.03); display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-family: monospace; font-size: 0.75rem; color: var(--text-secondary);">${p.barcode}</span>
                        <button type="button" class="btn btn-secondary btn-sm" onclick="showRichDetails('${p.barcode}')" style="padding: 0.35rem 0.75rem; font-size: 0.75rem; border-radius: 8px;">
                            <i class="fas fa-edit"></i> تعديل ودقّق
                        </button>
                    </div>
                </div>
            `;
            grid.appendChild(card);
        });
    }

    function filterRichCatalog() {
        const query = document.getElementById('richSearch').value.toLowerCase().trim();
        const catValue = document.getElementById('categoryFilter').value;
        const scoreValue = document.getElementById('scoreFilter').value;

        const filtered = richProducts.filter(p => {
            let meta = p.metadata_json || {};
            if (typeof meta === 'string') {
                try { meta = JSON.parse(meta) || {}; } catch (e) { meta = {}; }
            }

            // Text search matches name, brand, barcode, or ingredients
            const ingredientsText = (meta.ingredients || '').toLowerCase();
            const textMatch = (p.product_name || '').toLowerCase().includes(query) || 
                              (p.brand || '').toLowerCase().includes(query) || 
                              (p.barcode || '').toLowerCase().includes(query) ||
                              ingredientsText.includes(query);

            // Category match
            const cat1 = meta.web_category_l1 || meta.category || '';
            const catMatch = !catValue || cat1 === catValue;

            // Score match
            const scorePercent = p.clip_score ? Math.round(p.clip_score * 100) : 0;
            let scoreMatch = true;
            if (scoreValue === '90') scoreMatch = scorePercent >= 90;
            else if (scoreValue === '80') scoreMatch = scorePercent >= 80;
            else if (scoreValue === '70') scoreMatch = scorePercent >= 70;

            return textMatch && catMatch && scoreMatch;
        });

        renderRichCatalog(filtered);
    }

    function showRichDetails(barcode) {
        const p = richProducts.find(prod => String(prod.barcode) === String(barcode));
        if (!p) return;

        currentEditingBarcode = barcode;

        let meta = p.metadata_json;
        if (typeof meta === 'string') {
            try { meta = JSON.parse(meta); } catch (e) { meta = {}; }
        }

        // Fill inputs
        document.getElementById('editRichName').value = p.product_name || '';
        document.getElementById('editRichBrand').value = p.brand || '';
        document.getElementById('editRichBarcode').value = p.barcode || '';
        
        const dateStr = p.resolved_at ? new Date(p.resolved_at).toLocaleString('ar-SA') : 'غير متوفر';
        document.getElementById('modalRichDate').innerText = dateStr;

        // Background removal status warning
        const bgWarning = document.getElementById('modalBgRemovalWarning');
        if (meta.bg_removal_status === 'failed') {
            bgWarning.style.display = 'flex';
        } else {
            bgWarning.style.display = 'none';
        }

        // Image
        document.getElementById('modalRichImg').src = p.cloudinary_url;
        document.getElementById('modalCloudinaryLink').href = p.cloudinary_url;

        // Category
        document.getElementById('editRichCatL1').value = meta.web_category_l1 || meta.category || '';
        document.getElementById('editRichCatL2').value = meta.web_category_l2 || '';
        document.getElementById('editRichCatL3').value = meta.web_category_l3 || '';

        // Marketing description
        document.getElementById('editRichDescAr').value = meta.marketing_description_ar || meta.marketing_description || '';

        // Ingredients
        document.getElementById('editRichIngredients').value = meta.ingredients || '';
        checkModalAllergenWarning();

        // Nutrition Values
        document.getElementById('editRichCalories').value = meta.calories !== undefined ? meta.calories : '';
        document.getElementById('editRichCarbs').value = meta.carbohydrates !== undefined ? meta.carbohydrates : '';
        document.getElementById('editRichSugars').value = meta.sugars !== undefined ? meta.sugars : '';
        document.getElementById('editRichProtein').value = meta.protein !== undefined ? meta.protein : '';
        document.getElementById('editRichFat').value = meta.fat_total !== undefined ? meta.fat_total : '';
        document.getElementById('editRichSaturatedFat').value = meta.fat_saturated !== undefined ? meta.fat_saturated : '';
        document.getElementById('editRichSalt').value = meta.salt !== undefined ? meta.salt : '';

        // Open Modal
        document.getElementById('richModal').style.display = 'flex';
    }

    function checkModalAllergenWarning() {
        const ingredients = document.getElementById('editRichIngredients').value || '';
        const lowerIngredients = ingredients.toLowerCase();
        const containsAllergens = ['milk', 'soy', 'wheat', 'gluten', 'peanut', 'egg', 'hazelnut', 'حليب', 'صويا', 'قمح', 'جلوتين', 'فول سوداني', 'بيض'].some(keyword => lowerIngredients.includes(keyword));
        document.getElementById('modalAllergenWarning').style.display = containsAllergens ? 'flex' : 'none';
    }

    async function saveRichChanges() {
        if (!currentEditingBarcode) return;

        const name = document.getElementById('editRichName').value.trim();
        const brand = document.getElementById('editRichBrand').value.trim();

        if (!name || !brand) {
            alert('⚠️ يرجى تعبئة اسم المنتج والعلامة التجارية.');
            return;
        }

        const metadata = {
            web_category_l1: document.getElementById('editRichCatL1').value.trim(),
            web_category_l2: document.getElementById('editRichCatL2').value.trim(),
            web_category_l3: document.getElementById('editRichCatL3').value.trim(),
            marketing_description_ar: document.getElementById('editRichDescAr').value.trim(),
            ingredients: document.getElementById('editRichIngredients').value.trim(),
            calories: document.getElementById('editRichCalories').value.trim(),
            carbohydrates: document.getElementById('editRichCarbs').value.trim(),
            sugars: document.getElementById('editRichSugars').value.trim(),
            protein: document.getElementById('editRichProtein').value.trim(),
            fat_total: document.getElementById('editRichFat').value.trim(),
            fat_saturated: document.getElementById('editRichSaturatedFat').value.trim(),
            salt: document.getElementById('editRichSalt').value.trim()
        };

        try {
            const response = await fetch('/api/rich-products/update', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-TOKEN': document.querySelector('meta[name="csrf-token"]').content
                },
                body: JSON.stringify({
                    barcode: currentEditingBarcode,
                    product_name: name,
                    brand: brand,
                    metadata: metadata
                })
            });

            const data = await response.json();
            if (data.status === 'success') {
                alert('✅ تم حفظ التعديلات بنجاح وتحديث الكتالوج!');
                closeRichModal();
                await loadRichProducts(); // Reload
            } else {
                alert('❌ فشل الحفظ: ' + (data.error || 'خطأ غير معروف'));
            }
        } catch (e) {
            console.error(e);
            alert('❌ خطأ اتصال بالخادم أثناء محاولة الحفظ.');
        }
    }

    function closeRichModal() {
        document.getElementById('richModal').style.display = 'none';
    }

    function setPreviewBg(type) {
        const container = document.getElementById('modalRichImgContainer');
        container.className = '';
        if (type === 'white') {
            container.style.backgroundColor = '#ffffff';
        } else if (type === 'dark') {
            container.style.backgroundColor = '#07080d';
        } else {
            container.className = 'bg-checkerboard-rich';
            container.style.backgroundColor = '';
        }
    }
</script>
@endsection
