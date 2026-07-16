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
        box-shadow: 0 0 15px rgba(124, 58, 237, 0.2);
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
        box-shadow: 0 12px 30px rgba(0, 210, 255, 0.08);
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
        background: rgba(16, 185, 129, 0.85);
        color: #fff;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.7rem;
        font-weight: 800;
        font-family: 'Outfit', sans-serif;
    }

    .category-badge-rich {
        align-self: flex-start;
        background: rgba(0, 210, 255, 0.08);
        border: 1px solid rgba(0, 210, 255, 0.15);
        color: var(--accent-cyan);
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
        background: rgba(244, 63, 94, 0.08);
        border: 1px solid rgba(244, 63, 94, 0.2);
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
        background-color: #0c0d14;
        background-image: linear-gradient(45deg, #131520 25%, transparent 25%), 
                          linear-gradient(-45deg, #131520 25%, transparent 25%), 
                          linear-gradient(45deg, transparent 75%, #131520 75%), 
                          linear-gradient(-45deg, transparent 75%, #131520 75%);
        background-size: 20px 20px;
        background-position: 0 0, 0 10px, 10px -10px, -10px 0px;
    }
</style>
@endsection

@section('content')
<div class="rich-catalog-container">
    
    <!-- Banner -->
    <div class="glass-panel" style="padding: 1.5rem 2rem; display: flex; justify-content: space-between; align-items: center;">
        <div>
            <h2 style="font-size: 1.4rem; font-weight: 900; margin: 0; color: var(--text-primary); display: flex; align-items: center; gap: 0.65rem;">
                <i class="fas fa-store" style="color: var(--accent-purple-hover);"></i> معرض المنتجات والبيانات الغنية
            </h2>
            <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 0.25rem;">
                تصفح المنتجات المستخلصة والمكتملة بنجاح مع استعراض كامل للقيم الغذائية والمكونات وأوصاف الذكاء الاصطناعي.
            </p>
        </div>
        <span class="score-badge" id="catalogCount" style="background: rgba(139, 92, 246, 0.15); border-color: rgba(139, 92, 246, 0.25); color: var(--accent-purple-hover); font-size: 1rem; padding: 0.5rem 1.25rem; border-radius: 12px; font-weight: 900;">
            جاري التحميل...
        </span>
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

        <h3 id="modalRichTitle" style="font-size: 1.3rem; font-weight: 800; border-bottom: 1px solid var(--panel-border); padding-bottom: 0.75rem; margin-bottom: 1.5rem; display: flex; align-items: center; gap: 0.5rem; color: var(--accent-purple-hover);">
            اسم المنتج بالكامل
        </h3>

        <div class="modal-rich-grid">
            <!-- Col 1: Visuals & Settings -->
            <div style="display: flex; flex-direction: column; gap: 1rem;">
                <div id="modalRichImgContainer" class="bg-checkerboard-rich" style="height: 250px; border-radius: 12px; border: 1px solid var(--panel-border); display: flex; align-items: center; justify-content: center; padding: 1rem; position: relative; transition: all 0.3s;">
                    <img id="modalRichImg" src="" alt="Resolved image" style="max-width: 100%; max-height: 100%; object-fit: contain;">
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

                <!-- Product IDs -->
                <div style="display: flex; flex-direction: column; gap: 0.35rem; font-size: 0.85rem; border-top: 1px solid var(--panel-border); padding-top: 1rem;">
                    <div>البراند: <strong id="modalRichBrand" style="color: var(--text-primary);"></strong></div>
                    <div>الباركود: <strong id="modalRichBarcode" style="color: var(--text-primary); font-family: monospace;"></strong></div>
                    <div>تاريخ الحفظ: <strong id="modalRichDate" style="color: var(--text-secondary); font-family: 'Outfit', sans-serif;"></strong></div>
                </div>
            </div>

            <!-- Col 2: Metadata panels -->
            <div style="display: flex; flex-direction: column; gap: 1.25rem; max-height: 480px; overflow-y: auto; padding-inline-end: 0.5rem;">
                <!-- Web Category -->
                <div>
                    <span style="font-weight: 800; font-size: 0.9rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">التصنيف المعتمد بالمتجر:</span>
                    <div id="modalRichCategoryText" style="background: rgba(0,210,255,0.05); border: 1px solid rgba(0,210,255,0.1); padding: 0.5rem 1rem; border-radius: 8px; font-weight: 700; color: var(--accent-cyan);"></div>
                </div>

                <!-- Marketing Text -->
                <div>
                    <span style="font-weight: 800; font-size: 0.9rem; color: var(--text-secondary); display: block; margin-bottom: 0.35rem;">الوصف التسويقي للذكاء الاصطناعي:</span>
                    <div style="display: grid; grid-template-columns: 1fr; gap: 0.75rem;">
                        <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--panel-border); padding: 0.75rem; border-radius: 8px;">
                            <strong style="font-size: 0.75rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">بالعربية:</strong>
                            <p id="modalRichDescAr" style="font-size: 0.85rem; margin: 0; line-height: 1.5; color: var(--text-primary);"></p>
                        </div>
                    </div>
                </div>

                <!-- Ingredients -->
                <div>
                    <span style="font-weight: 800; font-size: 0.9rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">المكونات والتركيب (Ingredients):</span>
                    <p id="modalRichIngredients" style="background: rgba(255,255,255,0.01); border: 1px solid var(--panel-border); padding: 0.75rem; border-radius: 8px; font-size: 0.85rem; color: var(--text-primary); margin: 0; line-height: 1.5;"></p>
                    <div id="modalAllergenWarning" class="allergen-warning" style="display: none;">
                        <i class="fas fa-exclamation-circle"></i>
                        <span>تنبيه: يحتوي على مسببات الحساسية المكتشفة (حليب/صويا/قمح/مكسرات).</span>
                    </div>
                </div>

                <!-- Nutritional values -->
                <div>
                    <span style="font-weight: 800; font-size: 0.9rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">القيم الغذائية لكل 100 جرام:</span>
                    <div class="nutrition-grid" id="modalRichNutrition"></div>
                </div>
            </div>
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 1.5rem; border-top: 1px solid var(--panel-border); padding-top: 1.25rem;">
            <a id="modalCloudinaryLink" href="" target="_blank" class="btn btn-secondary" style="font-size: 0.85rem;">
                <i class="fas fa-external-link-alt"></i> فتح الرابط الأصلي بـ Cloudinary
            </a>
            <button type="button" class="btn" onclick="closeRichModal()" style="padding: 0.5rem 1.75rem; font-weight: bold;">إغلاق</button>
        </div>
    </div>
</div>

@endsection

@section('scripts')
<script>
    let richProducts = [];

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
            
            let meta = p.metadata_json;
            if (typeof meta === 'string') {
                try { meta = JSON.parse(meta); } catch (e) { meta = {}; }
            }

            const catText = meta.web_category_l1 || meta.category || 'غير مصنف';
            const scorePercent = p.clip_score ? Math.round(p.clip_score * 100) : 0;
            const scoreHtml = scorePercent > 0 ? `<div class="score-badge-rich">${scorePercent}% Match</div>` : '';

            card.innerHTML = `
                <div class="rich-card-img">
                    ${scoreHtml}
                    <img src="${p.cloudinary_url}" alt="Product Image" onerror="this.src='https://placehold.co/200x200?text=Error'">
                </div>
                <div class="rich-card-body">
                    <span class="category-badge-rich">${catText}</span>
                    <h4 style="font-size: 0.95rem; font-weight: 800; margin: 0; color: var(--text-primary); line-height: 1.4;" title="${p.product_name}">${p.product_name}</h4>
                    <p style="font-size: 0.8rem; color: var(--text-secondary); margin: 0; font-weight: 600;">البراند: <strong style="color: var(--text-primary);">${p.brand}</strong></p>
                    <div style="margin-top: auto; padding-top: 1rem; border-top: 1px solid rgba(255,255,255,0.03); display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-family: monospace; font-size: 0.75rem; color: var(--text-secondary);">${p.barcode}</span>
                        <button type="button" class="btn btn-secondary btn-sm" onclick="showRichDetails('${p.barcode}')" style="padding: 0.35rem 0.75rem; font-size: 0.75rem; border-radius: 8px;">
                            <i class="fas fa-file-invoice"></i> عرض البيانات
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
            let meta = p.metadata_json;
            if (typeof meta === 'string') {
                try { meta = JSON.parse(meta); } catch (e) { meta = {}; }
            }

            // Text search matches name, brand, barcode, or ingredients
            const ingredientsText = (meta.ingredients || '').toLowerCase();
            const textMatch = p.product_name.toLowerCase().includes(query) || 
                              p.brand.toLowerCase().includes(query) || 
                              p.barcode.toLowerCase().includes(query) ||
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
        // Find product
        const p = richProducts.find(prod => String(prod.barcode) === String(barcode));
        if (!p) return;

        let meta = p.metadata_json;
        if (typeof meta === 'string') {
            try { meta = JSON.parse(meta); } catch (e) { meta = {}; }
        }

        // Header info
        document.getElementById('modalRichTitle').innerText = p.product_name;
        document.getElementById('modalRichBrand').innerText = p.brand;
        document.getElementById('modalRichBarcode').innerText = p.barcode;
        
        const dateStr = p.resolved_at ? new Date(p.resolved_at).toLocaleString('ar-SA') : 'غير متوفر';
        document.getElementById('modalRichDate').innerText = dateStr;

        // Image
        document.getElementById('modalRichImg').src = p.cloudinary_url;
        document.getElementById('modalCloudinaryLink').href = p.cloudinary_url;

        // Category
        const l1 = meta.web_category_l1 || '';
        const l2 = meta.web_category_l2 || '';
        const l3 = meta.web_category_l3 || '';
        document.getElementById('modalRichCategoryText').innerText = [l1, l2, l3].filter(Boolean).join(' ⬅️ ');

        // Marketing Descriptions
        const descAr = meta.marketing_description_ar || meta.marketing_description || 'لا يوجد وصف تسويقي عربي مسجل.';
        document.getElementById('modalRichDescAr').innerText = descAr;

        // Ingredients
        const ingredients = meta.ingredients || 'لا توجد مكونات مسجلة.';
        document.getElementById('modalRichIngredients').innerText = ingredients;

        // Allergen warnings (simple keyword scan)
        const lowerIngredients = ingredients.toLowerCase();
        const containsAllergens = ['milk', 'soy', 'wheat', 'gluten', 'peanut', 'egg', 'hazelnut', 'حليب', 'صويا', 'قمح', 'جلوتين', 'فول سوداني', 'بيض'].some(keyword => lowerIngredients.includes(keyword));
        document.getElementById('modalAllergenWarning').style.display = containsAllergens ? 'flex' : 'none';

        // Nutrition Facts
        const nGrid = document.getElementById('modalRichNutrition');
        nGrid.innerHTML = '';
        
        const nutritionKeys = {
            'calories': 'السعرات الحرارية (kcal)',
            'carbohydrates': 'الكربوهيدرات (g)',
            'sugars': 'السكريات (g)',
            'protein': 'البروتينات (g)',
            'fat_total': 'الدهون الكلية (g)',
            'fat_saturated': 'الدهون المشبعة (g)',
            'salt': 'الملح (g)'
        };

        let hasNutrition = false;
        for (const [key, label] of Object.entries(nutritionKeys)) {
            const val = meta[key];
            if (val !== undefined && val !== null) {
                hasNutrition = true;
                const div = document.createElement('div');
                div.className = 'nutrition-item';
                div.innerHTML = `
                    <span style="font-size: 0.8rem; color: var(--text-secondary);">${label}:</span>
                    <span class="nutrition-val">${val}</span>
                `;
                nGrid.appendChild(div);
            }
        }

        if (!hasNutrition) {
            nGrid.innerHTML = '<p style="color: var(--text-secondary); font-style: italic; font-size: 0.85rem; grid-column: 1/-1;">لا تتوفر تفاصيل قيم غذائية لهذا المنتج.</p>';
        }

        // Open Modal
        document.getElementById('richModal').style.display = 'flex';
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
