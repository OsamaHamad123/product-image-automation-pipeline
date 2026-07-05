# categories.py
# موديول يحتوي على شجرة التصنيفات المعتمدة للمنتجات ووظائف المطابقة والتحقق

import difflib

# شجرة التصنيفات المستخرجة من قاعدة المعرفة (الملف المرفق)
# الهيكل: L1 (الرئيسي) -> L2 (الفرعي) -> L3 (الفرعي الفرعي)
CATEGORIES = {
    "Eggs & Dairy": {
        "ar": "الألبان والبيض",
        "subs": {
            "milk": {
                "ar": "حليب",
                "sub_subs": {
                    "Fresh": "طازج",
                    "Long Life": "طويل الأجل",
                    "Plant Based": "نباتي",
                    "Flavored": "منكه",
                    "Organic": "عضوي",
                    "Lactose Free": "خالٍ من اللاكتوز",
                    "Condensed": "مكثف",
                    "Evaporated": "مبخر",
                    "Powdered": "بودرة",
                    "Baby": "أطفال",
                    "Coconut Milk": "حليب جوز الهند",
                    "Skimmed": "خالي الدسم",
                    "Full Cream": "كامل الدسم"
                }
            },
            "Dairy": {
                "ar": "ألبان",
                "sub_subs": {
                    "Yogurt": "زبادي",
                    "Greek Yogurt": "زبادي يوناني",
                    "Laban": "لبن",
                    "Labneh": "لبنة",
                    "Cream": "قشطة",
                    "Butter": "زبدة",
                    "Ghee": "سمن",
                    "Dairy Desserts": "حلويات الألبان",
                    "Dairy Drinks": "مشروبات الألبان"
                }
            },
            "Eggs": {
                "ar": "بيض",
                "sub_subs": {
                    "Local": "محلي",
                    "Organic": "عضوي",
                    "Brown": "بني",
                    "White": "أبيض",
                    "Quail": "سمان"
                }
            },
            "Cheese": {
                "ar": "جبن",
                "sub_subs": {
                    "Spreadable": "قابلة للدهن",
                    "Slices": "شرائح",
                    "Shredded": "مبشورة",
                    "Hard": "صلبة",
                    "Soft": "طرية",
                    "Mozzarella": "موزاريلا",
                    "Cheddar": "تشيدر",
                    "Feta": "فيتا",
                    "Halloumi": "حلوم",
                    "Paneer": "رينير",
                    "Vegan": "نباتي"
                }
            }
        }
    },
    "Pantry Snacks": {
        "ar": "الوجبات الخفيفة",
        "subs": {
            "Breakfast": {
                "ar": "إفطار",
                "sub_subs": {
                    "Cereal": "رقائق الذرة",
                    "Jams": "مربى",
                    "Peanut Butter": "زبدة الفول السوداني",
                    "Honey": "عسل",
                    "Breakfast Bars": "ألواح الإفطار"
                }
            },
            "Canned": {
                "ar": "معلب",
                "sub_subs": {
                    "Meat": "لحمة",
                    "Canned Vegetables": "خضراوات معلبة",
                    "Beans": "فول",
                    "Fish": "سمكة",
                    "Tuna": "التونة",
                    "Foul Medames": "فول مدمس",
                    "Fruits": "فواكه",
                    "pineapple": "أناناس",
                    "Candies": "حلوى"
                }
            },
            "Sweets": {
                "ar": "حلويات",
                "sub_subs": {
                    "Mints": "نعناع",
                    "Digestive Biscuits": "بسكويت دايجستف",
                    "Chocolate nuts": "مكسرات الشوكولاتة",
                    "Cookies": "كوكيز",
                    "Chocolate": "الشوكولاتة",
                    "Jelly": "جيلي",
                    "Biscuits": "بسكويت"
                }
            },
            "Snacks": {
                "ar": "وجبات خفيفة",
                "sub_subs": {
                    "Potato Chips": "رقائق البطاطس",
                    "Chips": "شيبس",
                    "Popcorn": "فشار",
                    "Pretzels": "بريتزل",
                    "Nuts": "مكسرات",
                    "Crackers": "كراكرز"
                }
            }
        }
    },
    "Fresh Food": {
        "ar": "الأطعمة الطازجة",
        "subs": {
            "Dairy": {
                "ar": "ألبان",
                "sub_subs": {
                    "Fresh Milk": "حليب طازج",
                    "Plant-Based Milk": "حليب نباتي",
                    "Long-Life Milk": "حليب طويل الأجل",
                    "Yogurt & Laban": "الزبادي واللبن",
                    "Flavored": "منكه",
                    "Lactose-Free": "خالٍ من اللاكتوز",
                    "Condensed & Evaporated": "مكثف ومتبخر"
                }
            },
            "Meat & Chicken": {
                "ar": "اللحوم والدجاج",
                "sub_subs": {
                    "Beef": "لحم",
                    "Chicken": "دجاج",
                    "Lamb": "لحم ضأن",
                    "Processed Meat": "لحوم مصنعة",
                    "Minced Meat": "لحم مفروم",
                    "Camel": "جمل",
                    "Veal": "لحم العجل"
                }
            },
            "Seafood": {
                "ar": "المأكولات البحرية",
                "sub_subs": {
                    "Local": "محلي",
                    "Imported": "مستورد",
                    "Shellfish": "محار",
                    "Oysters & Clams": "محار وبلح بحر",
                    "Cephalopods": "رأسيات الأرجل",
                    "Smoked & Cured": "مدخن ومملح",
                    "Ready-to-Cook": "جاهز للطبخ",
                    "Dried & Salted": "مجفف ومملح",
                    "Fillets": "فيليه",
                    "White Fish": "سمك أبيض",
                    "Shrimps": "روبيان",
                    "Salmon": "سلمون"
                }
            },
            "Fruits": {
                "ar": "فواكه",
                "sub_subs": {
                    "Apples": "تفاح",
                    "Bananas": "موز",
                    "Berries": "توت",
                    "Melons & Grapes": "البطيخ والعنب",
                    "Exotic & Tropical": "استوائي وغريب",
                    "Citrus": "حمضيات",
                    "Avocados": "أفوكادو",
                    "Stone Fruits": "فواكه ذات نواة",
                    "Cut Fruits": "الفواكه المقطعة"
                }
            },
            "Juice & Salad": {
                "ar": "عصائر وسلطات",
                "sub_subs": {
                    "Cold-Pressed": "عصائر باردة",
                    "Pre-made Salads": "السلطات الجاهزة"
                }
            },
            "vegetables": {
                "ar": "خضروات",
                "sub_subs": {
                    "Tomatoes": "طماطم",
                    "Cucumbers": "خيار",
                    "Leafy Greens": "الخضراوات الورقية",
                    "Garlic & Onions": "البصل والثوم",
                    "Potatoes": "بطاطس",
                    "Peppers & Capsicums": "الفلفل الحلو والفلفل",
                    "Root Vegetables": "الخضراوات الجذرية",
                    "Mushrooms": "فطر",
                    "Exotic/Specialty Veg": "خضراوات مميزة/غريبة"
                }
            },
            "Delicatessen": {
                "ar": "أطعمة ديلي",
                "sub_subs": {
                    "Deli Meats": "اللحوم الباردة",
                    "Cheeses": "أجبان",
                    "Antipasti": "مقبلات"
                }
            }
        }
    },
    "Roastery": {
        "ar": "المحمصة",
        "subs": {
            "Dates & Nuts": {
                "ar": "المكسرات والتمر",
                "sub_subs": {
                    "Almonds": "لوز",
                    "Walnuts": "جوز",
                    "Cashews": "كاجو",
                    "Seeds": "بذور",
                    "Mixed": "مخلوط",
                    "Coconut": "جوز الهند",
                    "Cardamom": "هيل",
                    "Pistachios": "فستق",
                    "Macadamia": "ماكاديميا",
                    "Boxes": "علب"
                }
            },
            "Sauces & Oils": {
                "ar": "الزيوت والصلصات",
                "sub_subs": {
                    "Olive Oil": "زيت الزيتون",
                    "Avocado Oil": "زيت الأفوكادو",
                    "Ketchup & Mayonnaise": "المايونيز والكاتشب",
                    "Olives & Pickles": "المخللات والزيتون",
                    "Vinegar & Sauces": "الصلصات والخل",
                    "cooking oil": "زيت طبخ",
                    "Mustard Oil": "زيت الخردل",
                    "Tomato Paste": "معجون الطماطم",
                    "Coconut Oil": "زيت جوز الهند"
                }
            },
            "Cook & Spices": {
                "ar": "الطبخ والتوابل",
                "sub_subs": {
                    "Spices": "توابل",
                    "Baking Essentials": "مستلزمات الخبز",
                    "Ghee & Flour": "السمن والدقيق",
                    "Saffron": "زعفران",
                    "zaatar": "زعتر",
                    "Sugar & Salt": "سكر وملح",
                    "Indian Ingredients": "المكونات الهندية",
                    "Asian": "آسيوي",
                    "Middle Eastern": "الشرق الأوسط",
                    "European": "أوروبي",
                    "cooking pastes": "معاجين الطبخ"
                }
            },
            "Tea & Coffee": {
                "ar": "الشاي والقهوة",
                "sub_subs": {
                    "Green Tea": "الشاي الأخضر",
                    "Black Tea": "الشاي الأسود",
                    "Herbal Tea": "شاي الأعشاب",
                    "Coffee Ground": "القهوة المطحونة",
                    "Coffee Arabic": "القهوة العربية",
                    "Coffee Instant": "قهوة سريعة",
                    "Tea Karak": "شاي الكرك",
                    "Tea Bags": "أكياس الشاي",
                    "Coffee Capsules": "كبسولات قهوة",
                    "Coffee Turkish": "قهوة تركية",
                    "Espresso": "إسبريسو",
                    "Coffee Beans": "حبوب قهوة"
                }
            },
            "Grain & Rice": {
                "ar": "الأرز والحبوب",
                "sub_subs": {
                    "Jasmine & Basmati": "بسمتي وياسمين",
                    "Short Grain Rice": "أرز قصير الحبة",
                    "Sona Masoori": "سونا ماسوري",
                    "Sella & Matta": "ماتا وسيلا",
                    "Healthy Grains": "الحبوب الصحية",
                    "Quinoa Cereals": "حبوب الكينوا",
                    "Oats & Barley": "الشوفان والشعير",
                    "Couscous": "كسكسي",
                    "Popcorn": "فشار",
                    "Flattened Rice": "أرز مسطح"
                }
            },
            "Pasta & Legume": {
                "ar": "المعكرونة والبقوليات",
                "sub_subs": {
                    "Pasta": "معكرونة",
                    "Instant Noodles": "نودلز سريع",
                    "Lentils": "عدس",
                    "Beans": "فاصوليا",
                    "chickpeas": "حمص"
                }
            }
        }
    },
    "Beverages": {
        "ar": "المشروبات",
        "subs": {
            "water": {
                "ar": "ماء",
                "sub_subs": {
                    "Glass": "زجاج",
                    "Mineral": "معدنية",
                    "Alkaline": "قلوي",
                    "Sparkling": "غازية",
                    "Stim": "محفز",
                    "Coconut Water": "ماء جوز الهند",
                    "Flavored Water": "مياه بنكهة"
                }
            },
            "Special Brews": {
                "ar": "مشروبات خاصة",
                "sub_subs": {
                    "Kombucha": "كومبوتشا",
                    "Herbal Drinks": "المشروبات العشبية",
                    "Traditional Beverages": "المشروبات التقليدية",
                    "Seed Drinks": "مشروبات البذور",
                    "Vimto Cordial": "شراب فيمتو"
                }
            },
            "Drink Powder": {
                "ar": "مسحوق مشروب",
                "sub_subs": {
                    "Juices": "عصائر",
                    "Chocolate Mix": "مزيج الشوكولاتة",
                    "Coffee Creamers": "مبيضات القهوة",
                    "Protein Powders": "مساحيق البروتين"
                }
            },
            "Energy Drinks": {
                "ar": "مشروبات الطاقة",
                "sub_subs": {
                    "Electrolyte Drinks": "مشروبات الإلكتروليت",
                    "Energy Shots": "مشروبات الطاقة",
                    "Sports Drinks": "المشروبات الرياضية"
                }
            },
            "Soft Drinks": {
                "ar": "مشروبات غازية",
                "sub_subs": {
                    "Colas": "كولا",
                    "Fruit & Citrus": "حمضيات وفواكه",
                    "Root & Ginger": "زنجبيل وجذور",
                    "Sparkling Water": "مياه غازية",
                    "Iced Coffee & Tea": "شاي وقهوة مثلجة",
                    "Non-Alcoholic": "خالية من الكحول",
                    "Cordials": "شراب مركز / كورديال"
                }
            },
            "Tea & Coffee": {
                "ar": "قهوة وشاي",
                "sub_subs": {
                    "Ground & Beans": "حبوب وقهوة مطحونة",
                    "Coffee Capsules": "كبسولات قهوة",
                    "Coffee Instant": "قهوة سريعة",
                    "Tea Green & Black": "شاي أسود وأخضر",
                    "Fruit & Herbal": "شاي أعشاب وفواكه",
                    "Karak & Arabic": "شاي عربي وكرك"
                }
            },
            "Tonics & Mixers": {
                "ar": "تونك ومخففات",
                "sub_subs": {
                    "Tonic Water": "مياه تونك",
                    "Club Soda": "كلوب صودا",
                    "Cocktail Mixers": "خلطات الكوكتيل",
                    "Ginger & Bitters": "زنجبيل وبيترز"
                }
            },
            "100% Juice": {
                "ar": "عصير طبيعي 100%",
                "sub_subs": {
                    "Fresh Chilled": "طازجة ومبردة",
                    "Exotic & Berries": "غريبة وتوت",
                    "Veg & Orchard": "فواكه وخضار",
                    "Organic Juice": "عصير عضوي",
                    "Lunchbox Cartons": "علب مشروبات"
                }
            },
            "Kids Drinks": {
                "ar": "مشروبات الأطفال",
                "sub_subs": {
                    "Dairy Pouches": "أكياس الألبان",
                    "Low-Sugar": "منخفض السكر",
                    "Kids' Water": "مياه للأطفال",
                    "Squeeze Pouches": "أكياس قابلة للعصر"
                }
            }
        }
    },
    "Bakery": {
        "ar": "المخبز",
        "subs": {
            "Bread Basket": {
                "ar": "سلة الخبز",
                "sub_subs": {
                    "Sliced": "مقطع",
                    "Baguette": "باجيت",
                    "Rolls": "لفائف",
                    "Multigrain": "متعدد الحبوب",
                    "Lebanese": "لبناني",
                    "Arabic": "عربي",
                    "Sandwich": "سندويش",
                    "Toast": "خبز محمص",
                    "Sajj": "صاج"
                }
            },
            "Cake House": {
                "ar": "بيت الكعك",
                "sub_subs": {
                    "Cakes": "كعك",
                    "Cupcakes & Muffins": "الكب كيك والمافن",
                    "Pastries": "معجنات",
                    "Pies & Tarts": "الفطائر والتارت",
                    "Tea Cakes": "كعكات الشاي",
                    "Slices & Jars": "شرائح ومرطبانات"
                }
            },
            "Arabic Bakery": {
                "ar": "مخبز عربي",
                "sub_subs": {
                    "Baklava": "بقلاوة",
                    "Maamoul": "معمول",
                    "Kunafa": "كنافة",
                    "Arabian Sweets": "الحلويات العربية",
                    "Fatayer & Manakish": "مناقيش وفطائر",
                    "Ghorayeba": "غريبة",
                    "Barazek": "برازق",
                    "Lugaimat": "لقيمات"
                }
            },
            "Asian Bakery": {
                "ar": "مخبز آسيوي",
                "sub_subs": {
                    "Rice & Mochi": "موتشي وأرز",
                    "Sweet Buns": "كعكات حلوة",
                    "Milk Bread & Pandesal": "بانديسال وخبز الحليب",
                    "Siopao & Puffs": "فطائر البف و سيوباو",
                    "Chiffon & Sponge": "شيفون وإسفنج",
                    "Egg Tarts & Pies": "فطائر البيض",
                    "South Asian": "جنوب آسيوي"
                }
            },
            "Croissants and pastries": {
                "ar": "الكرواسون والمعجنات",
                "sub_subs": {
                    "Croissants lassic": "كرواسون كلاسيكي",
                    "Fruit Danishes": "فطائر الفاكهة الدنماركية",
                    "Sweet Puff Pastries": "معجنات الباف الحلوة",
                    "Savory Turnovers & Puffs": "فطائر ومعجنات مالحة",
                    "Gourmet Quiches": "كيش فاخر",
                    "Sausage & Meat Rolls": "لفائف النقانق واللحم"
                }
            },
            "Healthy Bake": {
                "ar": "مخبوزات صحية",
                "sub_subs": {
                    "Gluten-Free": "خالي من الغلوتين",
                    "Vegan": "نباتي",
                    "Keto & Low-Carb": "كيتو ومنخفض الكربوهيدرات",
                    "High-Protein Bakes": "غني بالبروتين",
                    "Ancient Grains": "الحبوب القديمة",
                    "No-Added-Sugar": "بدون سكر مضاف"
                }
            },
            "Dates": {
                "ar": "تمور",
                "sub_subs": {
                    "Medjool & Ajwa": "مجدول وعجوة",
                    "Sukkary & Khalas": "سكري وخلاص",
                    "Stuffed & Gourmet": "محشي وفاخر",
                    "Pastes & Syrups": "معجون ودبس",
                    "Chocolate-Coated": "مغطى بالشوكولاتة",
                    "Pressed": "مكبس",
                    "Sagai": "صقعي"
                }
            }
        }
    },
    "Organic": {
        "ar": "عضوي",
        "subs": {
            "Honey": {
                "ar": "عسل",
                "sub_subs": {
                    "Raw": "خام",
                    "Infused": "مشبع",
                    "Medical & Manuka": "مانوكا والطبية",
                    "Flavored & Infused": "منكه ومشبع",
                    "Sidr & Samar": "سدر وسمر",
                    "Bee Pollen & combs": "الأمشاط وحبوب اللقاح",
                    "Royal Jelly": "غذاء ملكات النحل",
                    "Gift sets": "مجموعات الهدايا"
                }
            },
            "Herbs": {
                "ar": "أعشاب",
                "sub_subs": {
                    "Fresh Culinary": "مأكولات طازجة",
                    "Dried Pantry": "المؤن الجافة",
                    "Organic Spices": "توابل عضوية",
                    "Herbal Tea": "شاي الأعشاب",
                    "Medicinal & Wellness": "الطب والعافية",
                    "Potted Kitchen": "مطبخ مصنع"
                }
            },
            "Dried Food": {
                "ar": "الأطعمة المجففة",
                "sub_subs": {
                    "Dried Fruits": "الفواكه المجففة",
                    "Dried Snacks": "سناكات مجففة",
                    "Dehydrated Vegetables": "خضراوات مجففة",
                    "Dried Berries": "التوت المجفف",
                    "Nut & Seed Mixes": "مكسرات وبذور مشكلة",
                    "Super Supplements": "المكملات الغذائية",
                    "Raisins": "زبيب"
                }
            }
        }
    },
    "Pharmacy": {
        "ar": "صيدلية",
        "subs": {
            "Pharmacy": {
                "ar": "صيدلية",
                "sub_subs": {
                    "Cold & Flu": "البرد والإنفلونزا",
                    "Pain Relief": "مسكنات الألم",
                    "Vitamins": "الفيتامينات",
                    "First Aid": "الإسعافات الأولية",
                    "Sanitizers": "معقمات",
                    "Monitors": "أجهزة قياس",
                    "Immune": "المناعة",
                    "Digestive": "الجهاز الهضمي",
                    "Condoms": "الواقي الذكري",
                    "Supplements": "المكملات الغذائية"
                }
            }
        }
    },
    "Personal Care": {
        "ar": "العناية الشخصية",
        "subs": {
            "Hair": {
                "ar": "شعر",
                "sub_subs": {
                    "Conditioner": "بلسم",
                    "Color & Tools": "صبغات وأدوات",
                    "Serum & Mask": "سيروم وماسك",
                    "Cream": "كريم",
                    "Oil & Mist": "زيوت وبخاخات",
                    "Shampoo": "شامبو",
                    "Men's Shampoo": "شامبو رجال",
                    "Female's Shampoo": "شامبو نساء",
                    "Kids Shampoo": "شامبو أطفال",
                    "Therapeutic": "علاجية",
                    "Luxury Hair": "الشعر الفاخر"
                }
            },
            "Body": {
                "ar": "جسم",
                "sub_subs": {
                    "Shower Gels": "جل الاستحمام",
                    "Wash": "غسول",
                    "Daily Care": "عناية يومية",
                    "Fragrance": "عطور",
                    "Glow & Finish": "إشراقة ولمسة",
                    "Loofahs": "ليف",
                    "Bar Soap": "صابون",
                    "Moisturizer": "مرطب",
                    "Sunscreen": "واقي شمس",
                    "Vaseline": "فازلين"
                }
            },
            "Face": {
                "ar": "الوجه",
                "sub_subs": {
                    "Wash": "غسول",
                    "Moisturizer": "مرطب",
                    "Sunscreen": "واقي الشمس",
                    "Serum & Cleanser": "سيروم ومنظف",
                    "Anti-Aging": "مكافحة الشيخوخة",
                    "Oral & Lip": "الفم والشفاه",
                    "Mask & Scrub": "ماسك ومقشر",
                    "Eye & Cream": "العين والكريم",
                    "Bar Soap": "صابون",
                    "Vaseline": "فازلين"
                }
            },
            "Women": {
                "ar": "نساء",
                "sub_subs": {
                    "Sanitary Pads": "فوط صحية",
                    "Pantyliners": "فوط يومية",
                    "Intimate Wash": "غسول نسائي",
                    "Lady Razors": "شفرات حلاقة",
                    "Hair Removal": "إزالة الشعر",
                    "Cotton Pads": "قطن دائري",
                    "Deodorant": "مزيل عرق",
                    "Cream": "كريم"
                }
            },
            "Men": {
                "ar": "رجال",
                "sub_subs": {
                    "Shaving": "حلاقة",
                    "Beard & Scents": "اللحية والعطور",
                    "Spicy Scents": "عطور شرقية",
                    "Arabian Oud": "عود عربي",
                    "Sports Frag": "عطور رياضية",
                    "Musk": "مسك",
                    "Eau de Toilette": "أو دو تواليت",
                    "Face wash": "غسول الوجه",
                    "Serum": "سيروم",
                    "Wax": "شمع",
                    "Grooming": "العناية الشخصية"
                }
            },
            "Cosmetics": {
                "ar": "مستحضرات التجميل",
                "sub_subs": {
                    "Make up": "مكياج",
                    "Blush & Palettes": "أحمر الخدود وباليتات",
                    "Lashes": "رموش",
                    "Polish Remover": "مزيل الأظافر",
                    "Nail & Toes": "الأظافر والقدمين",
                    "Premium Skin": "البشرة الفاخرة",
                    "Beauty Tech": "أجهزة التجميل",
                    "Designer Kits": "مجموعات فاخرة",
                    "Glow Serums": "سيروم الإشراق"
                }
            },
            "Perfumes": {
                "ar": "روائح",
                "sub_subs": {
                    "Men": "رجالي",
                    "Unisex": "للجنسين",
                    "Children": "أطفال",
                    "Women": "نسائي",
                    "Sets": "مجموعات"
                }
            },
            "Make Up": {
                "ar": "مكياج",
                "sub_subs": {
                    "Foundations": "كريم أساس",
                    "Lipsticks": "أحمر شفاه",
                    "Eyeliners": "محدد عيون",
                    "Mascaras": "ماسكارا",
                    "Brushes": "فرش المكياج"
                }
            }
        }
    },
    "Baby products": {
        "ar": "الأطفال",
        "subs": {
            "Food & Drinks": {
                "ar": "المأكولات والمشروبات",
                "sub_subs": {
                    "Baby Milk": "حليب الأطفال",
                    "Baby Cereal": "حبوب الأطفال",
                    "Purees": "هريس",
                    "Baby Snacks": "وجبات خفيفة",
                    "Baby Juices": "عصائر الأطفال",
                    "Organic Meals": "وجبات عضوية"
                }
            },
            "Diapers & Wipes": {
                "ar": "حفاضات ومناديل مبللة",
                "sub_subs": {
                    "Diapers": "حفاضات",
                    "Wet Wipes": "مناديل مبللة",
                    "Pull-ups": "حفاضات تدريب",
                    "Swim Diapers": "حفاضات السباحة",
                    "Diaper Bags": "حقائب الحفاضات",
                    "Changing Mats": "فرش التغيير"
                }
            },
            "Shower & Bath": {
                "ar": "دش وحمام",
                "sub_subs": {
                    "Baby Shampoo": "شامبو الأطفال",
                    "Bubble Bath": "حمام الفقاعات",
                    "Body Wash": "غسول للجسم",
                    "Bath Toys": "ألعاب الاستحمام",
                    "Sponges": "إسفنج",
                    "Baby Towels": "مناشف أطفال"
                }
            },
            "Baby Care": {
                "ar": "رعاية الطفل",
                "sub_subs": {
                    "Pacifiers": "لهايات",
                    "Teethers": "عضاضات الأسنان",
                    "Cotton Buds": "أعواد قطنية",
                    "Oral Care": "العناية بالفم",
                    "Grooming Kit": "مجموعة العناية",
                    "Nasal Care": "العناية بالأنف"
                }
            },
            "Cream & Lotion": {
                "ar": "كريم ولوشن",
                "sub_subs": {
                    "Baby Lotion": "لوشن للأطفال",
                    "Rash Cream": "كريم الطفح",
                    "Massage Oil": "زيت التدليك",
                    "Talcum Powder": "بودرة التلك",
                    "Sun Care": "واقي شمس",
                    "Moisturizer": "مرطب",
                    "Cologne": "كولونيا"
                }
            },
            "accessories": {
                "ar": "إكسسوارات",
                "sub_subs": {
                    "Baby Bibs": "مرايل الأطفال",
                    "Bottles": "زجاجات",
                    "Pacifier Clips": "مشابك اللهاية",
                    "Baby Socks": "جوارب أطفال",
                    "Baby Hats": "قبعات الأطفال",
                    "Nipples": "حلمات",
                    "Tableware": "أدوات المائدة"
                }
            }
        }
    },
    "Frozen Food": {
        "ar": "الأغذية المجمدة",
        "subs": {
            "Meat": {
                "ar": "لحم",
                "sub_subs": {
                    "Burgers Beef": "برجر لحم بقري",
                    "Minced Meat": "لحم مفروم",
                    "Kofta Beef": "كفتة لحم بقري",
                    "Steaks Beef": "شرائح لحم البقر",
                    "Cubes Beef": "مكعبات لحم بقري",
                    "Veal Bobby": "عجل طري"
                }
            },
            "Seafood": {
                "ar": "المأكولات البحرية",
                "sub_subs": {
                    "Fish Fillets": "شرائح السمك",
                    "Fish White": "السمك الأبيض",
                    "Shrimps": "روبيان",
                    "Prawns": "جمبري",
                    "Shellfish": "محار",
                    "Rings Squid": "حلقات الحبار"
                }
            },
            "Ready Meals": {
                "ar": "الوجبات الجاهزة",
                "sub_subs": {
                    "Veggie Meals": "وجبات نباتية",
                    "Meat Meals": "وجبات اللحوم",
                    "Spring Rolls": "لفائف الربيع",
                    "Samosas": "سمبوسة",
                    "Kibbeh": "كبة",
                    "Biryani": "برياني"
                }
            },
            "Frozen Poultry": {
                "ar": "دواجن مجمدة",
                "sub_subs": {
                    "Chicken Whole": "دجاجة كاملة",
                    "Chicken Breast": "صدر دجاج",
                    "Chicken Wings": "أجنحة الدجاج",
                    "Nuggets": "قطع الدجاج",
                    "Strips": "شرائح دجاج",
                    "Chicken Kebab": "كباب الدجاج"
                }
            },
            "Frozen Paratha": {
                "ar": "باراثا مجمدة",
                "sub_subs": {
                    "Plain Paratha": "باراثا سادة",
                    "Stuffed Pan": "مقلاة محشوة",
                    "Puff Pastry": "عجين الفطير",
                    "Filo Sheets": "رقائق الفيلو",
                    "Chapati": "تشاباتي"
                }
            },
            "Sweets & Ice": {
                "ar": "حلويات وآيس كريم",
                "sub_subs": {
                    "Ice Cream Tub": "علبة آيس كريم",
                    "Choco Sticks": "أعواد الشوكولاتة",
                    "Fruit Sorbet": "سوربيه الفواكه",
                    "Ice Mochi": "موتشي آيس",
                    "Frozen Cake": "كعكة مجمدة",
                    "Kunafa": "كنافة"
                }
            },
            "Vegetables": {
                "ar": "خضروات",
                "sub_subs": {
                    "Green Peas": "البازلاء الخضراء",
                    "Mixed Veg": "خضروات مشكلة",
                    "Spinach": "سبانخ",
                    "Molokhia": "ملوخية",
                    "Okra (Bamia)": "بامية",
                    "Sweet Corn": "الذرة الحلوة",
                    "Potatoes": "بطاطس",
                    "Root Vegetables": "الخضراوات الجذرية"
                }
            }
        }
    },
    "Deals": {
        "ar": "عروض",
        "subs": {
            "Grocery Deals": {
                "ar": "عروض البقالة",
                "sub_subs": {
                    "Fresh Offers": "عروض جديدة",
                    "Pantry Sale": "تخفيضات على المؤن",
                    "Bakery Deals": "عروض المخبوزات",
                    "Drinks Offers": "عروض المشروبات",
                    "Frozen Sale": "عروض المنتجات المجمدة",
                    "Snacks Deals": "عروض الوجبات الخفيفة"
                }
            },
            "Home & Beauty": {
                "ar": "المنزل والجمال",
                "sub_subs": {
                    "Skin Sale": "عروض منتجات البشرة",
                    "Hair Sale": "عروض منتجات الشعر",
                    "Makeup Deals": "عروض مكياج",
                    "Cleaning Sale": "تخفيضات على التصفية",
                    "Laundry Deals": "عروض المغسلة",
                    "Paper Offers": "عروض ورقية"
                }
            },
            "Tech & Kids": {
                "ar": "التكنولوجيا والأطفال",
                "sub_subs": {
                    "Phone Deals": "عروض الهواتف",
                    "Laptop Sale": "عروض أجهزة اللابتوب",
                    "TV Offers": "عروض التلفزيون",
                    "Toy Sales": "مبيعات الألعاب",
                    "Baby Deals": "عروض للأطفال",
                    "Gaming Offers": "عروض الألعاب"
                }
            },
            "Lifestyle Sale": {
                "ar": "تخفيضات نمط الحياة",
                "sub_subs": {
                    "Perfume Offers": "عروض العطور",
                    "Sport Deals": "عروض رياضية",
                    "Car Sale": "عروض مستلزمات السيارات",
                    "Tool Deals": "عروض الأدوات"
                }
            },
            "Combo Offers": {
                "ar": "عروض كومبو",
                "sub_subs": {
                    "Buy 1 Get 1": "اشترِ واحد والثاني مجاناً",
                    "Family Packs": "باقات عائلية",
                    "Bundle Sets": "مجموعات الحزم",
                    "Value Meals": "وجبات اقتصادية",
                    "Mix & Match": "امزج وطابق",
                    "Gift Bundles": "باقات الهدايا"
                }
            },
            "Clearance Shop": {
                "ar": "متجر التصفية",
                "sub_subs": {
                    "Last Pieces": "القطع الأخيرة",
                    "Half Price": "نصف السعر",
                    "End Season": "نهاية الموسم",
                    "Outlet Deals": "عروض التخفيضات",
                    "Stock Cleanup": "تنظيف المخزون",
                    "Under 20 AED": "أقل من 20 درهم"
                }
            }
        }
    },
    "Electronics": {
        "ar": "الإلكترونيات",
        "subs": {
            "Televisions": {
                "ar": "أجهزة التلفزيون",
                "sub_subs": {
                    "Smart TVs": "أجهزة التلفزيون الذكية",
                    "OLED TVs": "تلفزيونات OLED",
                    "4K UHD": "4K UHD",
                    "Projectors": "أجهزة العرض",
                    "TV Remotes": "ريموت التلفاز",
                    "TV Mounts": "حوامل التلفزيون"
                }
            },
            "Audios": {
                "ar": "ملفات صوتية",
                "sub_subs": {
                    "Headphones": "سماعات الرأس",
                    "Speakers": "مكبرات الصوت",
                    "Soundbars": "مكبرات الصوت",
                    "Home Theater": "المسرح المنزلي",
                    "Microphones": "ميكروفونات",
                    "Radio": "راديو",
                    "Audio Cables": "كابلات الصوت"
                }
            },
            "Laptops": {
                "ar": "أجهزة الكمبيوتر المحمولة",
                "sub_subs": {
                    "Laptop Gaming": "لابتوب للألعاب",
                    "Work Laptops": "لابتوبات للعمل",
                    "Ultrabooks": "ألترا بوك",
                    "Chromebooks": "كروم بوك",
                    "Laptop Bags": "حقائب اللابتوب",
                    "Chargers": "شواحن"
                }
            },
            "Gaming & Cam": {
                "ar": "ألعاب وكاميرا",
                "sub_subs": {
                    "PlayStation": "بلاي ستيشن",
                    "Xbox": "إكس بوكس",
                    "Nintendo": "نينتندو",
                    "Gaming Gear": "معدات الألعاب",
                    "Action Cams": "كاميرات الحركة",
                    "DSLRs": "كاميرات DSLR"
                }
            },
            "Routers & Extenders": {
                "ar": "أجهزة الشبكات",
                "sub_subs": {
                    "Wi-Fi Routers": "أجهزة واي فاي",
                    "Mesh Wi-Fi": "واي فاي شبكي",
                    "Extenders": "مقويات (موسعات)",
                    "Modems": "مودم",
                    "Network Hubs": "مراكز الشبكة",
                    "LAN Cables": "كابلات الشبكة"
                }
            },
            "Lights & Cables": {
                "ar": "أضواء وكابلات",
                "sub_subs": {
                    "LED Bulbs": "مصابيح LED",
                    "Smart Lights": "الإضاءة الذكية",
                    "Cables": "كابلات",
                    "Flashlights": "مصابيح يدوية",
                    "Security Cam": "كاميرا مراقبة",
                    "Adapters": "محولات",
                    "Cable Ties": "أربطة الكابلات",
                    "Switches": "مفتاح كهربائية"
                }
            },
            "Power & Storage": {
                "ar": "الطاقة والتخزين",
                "sub_subs": {
                    "SSD Storage": "تخزين SSD",
                    "Flash Drives": "ذاكرة فلاش",
                    "Power Banks": "بنوك الطاقة",
                    "Memory Cards": "بطاقات الذاكرة",
                    "Chargers & Adapters": "الشواحن والمحولات",
                    "UPS Systems": "أنظمة UPS",
                    "Batteries": "بطاريات"
                }
            },
            "Personal Care": {
                "ar": "العناية الشخصية",
                "sub_subs": {
                    "Shavers": "ماكينات الحلاقة",
                    "Hair Appliances": "أجهزة الشعر",
                    "Massagers": "أجهزة التدليك",
                    "BP Monitors": "جهاز ضغط الدم",
                    "Thermometers": "موازين الحرارة",
                    "Scales": "موازين"
                }
            },
            "PCs & Printers": {
                "ar": "الكمبيوتر والطابعات",
                "sub_subs": {
                    "Desktops": "أجهزة سطح المكتب",
                    "Monitors": "شاشات",
                    "Printers": "طابعات",
                    "Scanners": "الماسحات الضوئية",
                    "Keyboards": "لوحات المفاتيح",
                    "PC Mice": "فأرة الكمبيوتر"
                }
            },
            "Kitchen Tech": {
                "ar": "تكنولوجيا المطبخ",
                "sub_subs": {
                    "Air Fryers": "قلايات الهواء",
                    "Blenders": "خلاطات",
                    "Coffee Makers": "ماكينات القهوة",
                    "Kettles": "غلايات",
                    "Ovens & Microwaves": "الأفران والميكروويف",
                    "Toasters": "محمصة الخبز",
                    "Juicers": "عصارات",
                    "Hoods": "شفاطات",
                    "Specialty Appliances": "أجهزة متخصصة",
                    "Stoves & Grills": "المواقد والشوايات"
                }
            },
            "Home appliances": {
                "ar": "الأجهزة المنزلية",
                "sub_subs": {
                    "Vacuums": "المكانس الكهربائية",
                    "Iron & Steam": "مكواة وبخار",
                    "Water Heaters": "سخانات المياه",
                    "Air Purifier": "جهاز تنقية الهواء",
                    "Fans": "مراوح",
                    "Smart Locks": "الأقفال الذكية",
                    "Water Dispenser": "موزع المياه",
                    "Water Pumps": "مضخات المياه"
                }
            },
            "Smart Mobility": {
                "ar": "التنقل الذكي",
                "sub_subs": {
                    "Electric Scooters": "سكوترات كهربائية",
                    "Electric Bikes": "درجات كهربائية",
                    "Balance Boards": "ألواح توازن",
                    "Mobility Accessories": "ملحقات التنقل",
                    "Power & Parts": "بطاريات وقطع غيار"
                }
            }
        }
    },
    "Furniture": {
        "ar": "الأثاث",
        "subs": {
            "Furniture": {
                "ar": "أثاث",
                "sub_subs": {
                    "Sofas": "أريكة",
                    "Beds": "أسرة",
                    "Dining Tables": "طاولات الطعام",
                    "Office Chairs": "كراسي المكتب",
                    "Shoe Racks": "رفوف الأحذية",
                    "Hangers": "علاقات الملابس",
                    "Basket": "سلة",
                    "Home Setup": "إعداد المنزل",
                    "Mirror": "مرآة"
                }
            }
        }
    },
    "Home & Living": {
        "ar": "المنزل والمعيشة",
        "subs": {
            "Home appliances": {
                "ar": "الأجهزة المنزلية",
                "sub_subs": {
                    "Vacuums": "المكانس الكهربائية",
                    "Air Purifiers": "أجهزة تنقية الهواء",
                    "Irons": "مكواة",
                    "Steamers": "أجهزة بخارية",
                    "Fans": "مراوح",
                    "Air Curtains": "الستائر الهوائية",
                    "Hangers & Hooks": "علاقات وخطاطيف"
                }
            },
            "Dine & Cook": {
                "ar": "تناول الطعام واطبخ",
                "sub_subs": {
                    "Cookware": "أواني الطبخ",
                    "Tableware": "أدوات المائدة",
                    "Cutlery": "أدوات المائدة",
                    "Drinkware": "أدوات الشرب",
                    "Oven Mitts": "قفازات الفرن",
                    "Kitchen Towels": "مناشف المطبخ",
                    "Charcoal": "فحم",
                    "Fire Starters": "مشعلات النار"
                }
            },
            "Major Appliances": {
                "ar": "الأجهزة الرئيسية",
                "sub_subs": {
                    "Fridges": "ثلاجات",
                    "Washers": "غسالات",
                    "Dishwashers": "غسالات الأطباق",
                    "Gas Cookers": "مواقد الغاز",
                    "Dryers": "مجففات الملابس",
                    "Water Coolers": "مبردات المياه"
                }
            },
            "Luggages": {
                "ar": "أمتعة",
                "sub_subs": {
                    "Suitcases": "حقائب السفر",
                    "Backpacks": "حقائب الظهر",
                    "Travel Bags": "حقائب السفر",
                    "Duffel Bags": "حقائب سفر",
                    "Briefcases": "حقائب",
                    "Luggage Tags": "بطاقات الأمتعة"
                }
            },
            "Stationery": {
                "ar": "الأدوات المكتبية",
                "sub_subs": {
                    "Notebooks": "دفاتر الملاحظات",
                    "Pens & Ink": "أقلام وحبر",
                    "Office Supply": "لوازم مكتبية",
                    "Art Crafts": "الحرف الفنية",
                    "School Supplies": "اللوازم المدرسية",
                    "Calculators": "الآلات الحاسبة"
                }
            },
            "Home & DIY": {
                "ar": "المنزل وأعمال الصيانة",
                "sub_subs": {
                    "Wall Paint": "طلاء الجدران",
                    "Plumbing": "أعمال السباكة",
                    "Flooring": "أرضيات",
                    "Smart Home": "المنزل الذكي",
                    "Storage Boxes": "صناديق التخزين",
                    "Door Locks": "أقفال الأبواب"
                }
            },
            "Decor & Tools": {
                "ar": "ديكورات وأدوات",
                "sub_subs": {
                    "Drills": "مثاقب",
                    "Wrenches": "مفاتيح الربط",
                    "Hammers": "مطارق",
                    "Saw Blades": "شفرات المنشار",
                    "Measuring": "قياس",
                    "Sanders": "سنفرة",
                    "Clocks": "ساعات",
                    "Tools / DIY": "أدوات ومستلزمات"
                }
            },
            "Tools": {
                "ar": "أدوات",
                "sub_subs": {
                    "Brooms": "مكانس",
                    "Mops": "مساحات",
                    "Buckets": "دلاء",
                    "Dustpans": "مجارف الغبار",
                    "Window Clean": "تنظيف النوافذ",
                    "Step Ladders": "السلالم المتحركة"
                }
            },
            "Food Storage": {
                "ar": "تخزين الطعام",
                "sub_subs": {
                    "Lunch Boxes": "علب الغداء",
                    "Glass Jars": "مرطبانات زجاجية",
                    "Plastic Boxes": "علب بلاستيكية",
                    "Food Wraps": "لفائف الطعام",
                    "Vacuum Bags": "أكياس مفرغة",
                    "Flasks": "قوارير"
                }
            }
        }
    },
    "Perfumes": {
        "ar": "العطور",
        "subs": {
            "Women's": {
                "ar": "نسائي",
                "sub_subs": {
                    "Floral": "زهور",
                    "Woody": "خشبي",
                    "Citrus": "حمضيات",
                    "Oriental": "شرقي",
                    "Oud": "عود",
                    "Musk": "مسك",
                    "Gift Set": "مجموعة هدايا",
                    "Eau de Parfum": "أو دو بارفان"
                }
            },
            "Men's": {
                "ar": "رجالي",
                "sub_subs": {
                    "Fresh": "منعش",
                    "Spicy": "حار",
                    "Oud": "عود",
                    "Woody": "خشبي",
                    "Sports Frag": "عطر رياضي",
                    "Musk": "مسك",
                    "Underwear": "ملابس داخلية",
                    "Gift Set": "مجموعة هدايا",
                    "Eau de Toilette": "أو دو تواليت"
                }
            },
            "Unisex": {
                "ar": "للجنسين",
                "sub_subs": {
                    "Niche": "مميز",
                    "Oud": "عود",
                    "Musk": "مسك",
                    "Amber": "عنبر",
                    "Oriental": "شرقي",
                    "Woody": "خشبي",
                    "Spicy": "حار",
                    "Citrus": "حمضيات",
                    "Gift Set": "مجموعة هدايا",
                    "Perfume Oil": "زيت عطر"
                }
            },
            "Body": {
                "ar": "جسم",
                "sub_subs": {
                    "Body Mists": "معطرات الجسم",
                    "Body Sprays": "بخاخات الجسم",
                    "Hair Mists": "بخاخات الشعر",
                    "Hair Oils": "زيوت الشعر",
                    "Glitter Mists": "رذاذ لامع",
                    "Shimmer Spray": "بخاخ لامع"
                }
            },
            "sets": {
                "ar": "مجموعات",
                "sub_subs": {
                    "Gift Sets": "مجموعات الهدايا",
                    "Travel Sets": "مجموعات السفر",
                    "Luxury Sets": "أطقم فاخرة",
                    "Mini Sets": "مجموعات صغيرة",
                    "Discovery Kits": "مجموعات الاكتشاف",
                    "Couple Sets": "أطقم للأزواج"
                }
            },
            "Children's": {
                "ar": "أطفال",
                "sub_subs": {
                    "Mild Scents": "روائح خفيفة",
                    "Alcohol Free": "خالٍ من الكحول",
                    "Floral Mist": "رذاذ الزهور",
                    "Baby Cologne": "كولونيا للأطفال",
                    "Fruity Sprays": "بخاخات الفواكه",
                    "Gift Packs": "علب الهدايا"
                }
            },
            "Home Scents": {
                "ar": "معطرات منزلية",
                "sub_subs": {
                    "Candles": "شموع",
                    "Diffusers": "موزعات العطور",
                    "Incense": "عود",
                    "Bukhoor": "بخور",
                    "Room Sprays": "معطرات الجو",
                    "Fragrant Oils": "الزيوت العطرية"
                }
            }
        }
    },
    "Fashion": {
        "ar": "الأزياء",
        "subs": {
            "New": {
                "ar": "جديد",
                "sub_subs": {
                    "Trending Now": "الأكثر رواجاً",
                    "Seasonal Pick": "اختيار موسمي",
                    "New Brands": "علامات جديدة",
                    "Latest Styles": "أحدث التصاميم",
                    "Celeb Looks": "إطلالات المشاهير",
                    "Just Dropped": "وصل حديثاً"
                }
            },
            "Women": {
                "ar": "نساء",
                "sub_subs": {
                    "Dresses": "فساتين",
                    "Sandle": "صندل",
                    "Shoes": "أحذية",
                    "Handbags": "حقائب اليد",
                    "Accessories": "إكسسوارات",
                    "Skirts": "تنانير",
                    "Tees & Tops": "قمصان وبلوزات",
                    "Jalabiyas": "جلابيب",
                    "Abayas": "عبايات",
                    "Suit": "بدلة"
                }
            },
            "Men": {
                "ar": "رجال",
                "sub_subs": {
                    "Casual Shirts": "قمصان كاجوال",
                    "Shoes": "أحذية",
                    "Watches": "ساعات",
                    "Leather Belts": "أحزمة جلدية",
                    "Trousers": "بنطلونات",
                    "Slippers": "شباشب",
                    "Shemagh": "شماغ",
                    "Jacket": "جاكيت",
                    "Polos": "بولوا"
                }
            },
            "Kids & baby": {
                "ar": "أطفال ورضع",
                "sub_subs": {
                    "Kids Clothing": "ملابس الأطفال",
                    "Kids Footwear": "أحذية الأطفال",
                    "Baby Onesies": "أونزي للأطفال",
                    "Sandal": "صندل",
                    "Baby Shoes": "أحذية أطفال",
                    "School Sets": "مجموعات مدرسية",
                    "Kids Watches": "ساعات الأطفال"
                }
            },
            "Accessories and bags": {
                "ar": "الإكسسوارات والحقائب",
                "sub_subs": {
                    "Sunglasses": "نظارات شمسية",
                    "Wallets": "محافظ",
                    "Caps & Hats": "القبعات والأغطية",
                    "Backpacks": "حقائب الظهر",
                    "Scarves": "أوشحة",
                    "Jewelry": "مجوهرات",
                    "Handbags": "حقائب اليد"
                }
            },
            "Deals": {
                "ar": "عروض",
                "sub_subs": {
                    "Flash Sales": "تخفيضات سريعة",
                    "Combo Offers": "عروض كومبو",
                    "50% Off Shop": "خصم 50%",
                    "70% Off Shop": "خصم 70%",
                    "Clearance": "التخليص الجمركي"
                }
            }
        }
    },
    "Toys": {
        "ar": "الألعاب",
        "subs": {
            "Games & Toys": {
                "ar": "ألعاب وهدايا",
                "sub_subs": {
                    "Daily Deals": "عروض يومية",
                    "Action Figures": "شخصيات الأكشن",
                    "Dolls": "دمى",
                    "remote control": "جهاز تحكم عن بعد",
                    "Play Sets": "مجموعات اللعب",
                    "Board Games": "ألعاب الطاولة",
                    "Plush Toys": "ألعاب قطيفة",
                    "Card Games": "ألعاب الورق",
                    "Fidget Toys": "ألعاب التململ"
                }
            },
            "Learning & Education": {
                "ar": "التعلم والتعليم",
                "sub_subs": {
                    "Story Books": "كتب القصص",
                    "Puzzles": "ألغاز",
                    "STEM Toys": "ألعاب تعليمية STEM",
                    "Flash Cards": "بطاقات تعليمية",
                    "Math Games": "ألعاب الرياضيات",
                    "Science Kits": "مجموعات العلوم"
                }
            },
            "Creative Play": {
                "ar": "اللعب الإبداعي",
                "sub_subs": {
                    "Art Kits": "مجموعات فنية",
                    "Musical Toys": "ألعاب موسيقية",
                    "DIY Kits": "الأعمال اليدوية",
                    "Drawing Tools": "أدوات الرسم",
                    "Clay & Dough": "الطين والعجين",
                    "Craft Sets": "الحرف اليدوية"
                }
            },
            "Outdoor Play": {
                "ar": "اللعب في الهواء الطلق",
                "sub_subs": {
                    "Kids Bikes": "دراجات الأطفال",
                    "Scooters": "دراجات بخارية",
                    "Playhouses": "بيوت اللعب",
                    "Roller Skates": "أحذية التزلج",
                    "Trampolines": "ترامبولين",
                    "Ball Games": "ألعاب الكرة",
                    "Beach Games": "ألعاب الشاطئ"
                }
            },
            "Baby & Toddler": {
                "ar": "الرضع والأطفال الصغار",
                "sub_subs": {
                    "Strollers": "عربات الأطفال",
                    "Baby Monitors": "أجهزة المراقبة",
                    "Feed Bottles": "زجاجات الرضاعة",
                    "Baby Diapers": "حفاضات الأطفال",
                    "Play Mats": "سجادات اللعب",
                    "Walkers": "مشاية"
                }
            },
            "Party & Decor": {
                "ar": "حفلات وديكور",
                "sub_subs": {
                    "Balloons": "بالونات",
                    "Costumes": "أزياء",
                    "Party Favors": "هدايا الحفلات",
                    "Banners Party": "لافتات الحفلات",
                    "Cake Toppers": "زينة الكيك",
                    "Party Lights": "أضواء الحفلات"
                }
            }
        }
    },
    "Sporting Goods": {
        "ar": "السلع الرياضية",
        "subs": {
            "Sports Equipment": {
                "ar": "معدات رياضية",
                "sub_subs": {
                    "Game Balls": "كرات اللعب",
                    "Clubs & Bats": "مضارب وعصي",
                    "Racquets": "مضارب",
                    "Helmets": "خوذات",
                    "Sports Nets": "شبكات رياضية",
                    "Pads & Guards": "وسادات وواقيات",
                    "Counters": "عدادات"
                }
            },
            "Outdoor Recreation": {
                "ar": "الأنشطة الخارجية",
                "sub_subs": {
                    "Camping Gear": "معدات التخييم",
                    "Hiking Gear": "معدات التنزه",
                    "Tents": "خيام",
                    "Sleeping Bags": "أكياس النوم",
                    "Binoculars": "مناظير",
                    "Backpacks": "حقائب الظهر"
                }
            },
            "Fitness & Wellness": {
                "ar": "اللياقة البدنية والصحة",
                "sub_subs": {
                    "Treadmills": "أجهزة المشي",
                    "Yoga Mats": "حصائر اليوغا",
                    "Dumbbells": "دمبل",
                    "Jump Ropes": "حبل القفز",
                    "Kettlebells": "أثقال",
                    "Gym Benches": "مقاعد رياضية"
                }
            },
            "Apparel & Accessories": {
                "ar": "الملابس والإكسسوارات",
                "sub_subs": {
                    "Sportswear": "ملابس رياضية",
                    "Running Shoes": "أحذية الجري",
                    "Gym Bags": "حقائب رياضية",
                    "Sport Socks": "جوارب رياضية",
                    "Gym Gloves": "قفازات رياضية",
                    "Sweatbands": "ربطات رياضية"
                }
            },
            "Fan Shop": {
                "ar": "متجر المشجعين",
                "sub_subs": {
                    "Team Jerseys": "قمصان الفريق",
                    "Sports Caps": "قبعات رياضية",
                    "Fan Scarves": "أوشحة المشجعين",
                    "Collectibles": "مقتنيات",
                    "Flags": "أعلام",
                    "Wristbands": "أساور المعصم"
                }
            },
            "Specialized Products": {
                "ar": "منتجات متخصصة",
                "sub_subs": {
                    "Car Batteries": "بطاريات السيارات",
                    "Engine Parts": "قطع غيار المحرك",
                    "Exterior Accessories": "ملحقات خارجية"
                }
            }
        }
    },
    "Automotive": {
        "ar": "السيارات",
        "subs": {
            "Auto Parts": {
                "ar": "قطع غيار السيارات",
                "sub_subs": {
                    "Brake Pads": "وسادات الفرامل",
                    "Spark Plugs": "شمعات الإشعال",
                    "Oil Filters": "فلاتر الزيت",
                    "Air Filters": "فلاتر الهواء",
                    "Wiper Blades": "شفرات المساحات",
                    "Car Batteries": "بطاريات السيارات"
                }
            },
            "Car Care": {
                "ar": "العناية بالسيارة",
                "sub_subs": {
                    "Car Wash Kits": "مجموعات الغسيل",
                    "Car Waxes": "شمع السيارات",
                    "Tire Shine": "ملمع الإطارات",
                    "Interior Clean": "تنظيف داخلي",
                    "Glass Cleaners": "منظفات الزجاج",
                    "Polishing Pads": "وسادات التلميع"
                }
            },
            "Tires & Wheels": {
                "ar": "الإطارات والعجلات",
                "sub_subs": {
                    "Car Tires": "إطارات السيارات",
                    "Rims & Wheels": "جنوط وعجلات",
                    "Wheel Covers": "أغطية العجلات",
                    "Tire Valves": "صمامات الإطارات",
                    "Lug Nuts": "صواميل العجلات",
                    "Tire Repair": "إصلاح الإطارات"
                }
            },
            "Heavy Duty": {
                "ar": "معدات قوية",
                "sub_subs": {
                    "Truck Parts": "قطع غيار الشاحنات",
                    "Towing Gear": "معدات السحب",
                    "Heavy Filters": "مرشحات ثقيلة",
                    "Tool Boxes": "صناديق الأدوات",
                    "Cargo Nets": "شبكات الشحن",
                    "Safety Lights": "أضواء الأمان"
                }
            },
            "Navigation & Electronics": {
                "ar": "الملاحة والإلكترونيات",
                "sub_subs": {
                    "Dash Cams": "كاميرات لوحة القيادة",
                    "GPS Devices": "أجهزة GPS",
                    "Car Audios": "أنظمة صوت السيارات",
                    "Car Speakers": "سماعات السيارة",
                    "Bluetooth Kits": "أطقم بلوتوث",
                    "Phone Mounts": "حوامل الهواتف"
                }
            },
            "Merchandise & Gifts": {
                "ar": "الهدايا والبضائع",
                "sub_subs": {
                    "Model Cars": "نماذج سيارات",
                    "Keychains": "سلاسل المفاتيح",
                    "Car Stickers": "ملصقات السيارات",
                    "Branded Caps": "قبعات بعلامة تجارية",
                    "Air Freshners": "معطرات الجو",
                    "Sunshades": "مظلات شمسية"
                }
            }
        }
    },
    "Home Essentials": {
        "ar": "مستلزمات المنزل",
        "subs": {
            "Kitchen Tech": {
                "ar": "تكنولوجيا المطبخ",
                "sub_subs": {
                    "Air Fryers": "قلايات الهواء",
                    "Blenders": "خلاطات",
                    "Kettles": "غلايات",
                    "Coffee Makers": "ماكينات القهوة",
                    "Toasters": "محمصة الخبز",
                    "Mixers": "خلاطات"
                }
            },
            "Cleaning Solutions": {
                "ar": "محاليل التنظيف",
                "sub_subs": {
                    "Dish Liquid": "غسيل الأطباق",
                    "Floor Cleaner": "منظف الأرضيات",
                    "Glass Spray": "بخاخ زجاجي",
                    "Bleach": "مبيض",
                    "Degreasers": "مزيلات الشحوم",
                    "Sponges": "إسفنج",
                    "Shoe Cleaning": "تنظيف الأحذية",
                    "Drain Opener": "منظف المجاري",
                    "Gloves": "قفازات"
                }
            },
            "Kitchen Ware": {
                "ar": "أدوات المطبخ",
                "sub_subs": {
                    "Serveware": "أواني التقديم",
                    "Containers": "حاويات",
                    "Disposables": "استخدام واحد"
                }
            },
            "Bath Supplies": {
                "ar": "مستلزمات الاستحمام",
                "sub_subs": {
                    "Bath Towels": "مناشف الحمام",
                    "Bath Mats": "سجادات الحمام",
                    "Accessories": "مزاول",
                    "Soap Holders": "حاملات الصابون"
                }
            },
            "Paper & Waste": {
                "ar": "الورق والنفايات",
                "sub_subs": {
                    "Facial Tissue": "مناديل الوجه",
                    "Toilet Paper": "مناديل المراحيض",
                    "Kitchen Roll": "ورق مطبخ",
                    "Garbage Bags": "أكياس القمامة",
                    "Paper Plates": "أطباق ورقية",
                    "Napkins": "مناديل ورقية"
                }
            },
            "Air Care": {
                "ar": "العناية بالهواء",
                "sub_subs": {
                    "Air Sprays": "بخاخات الهواء",
                    "Diffusers": "موزعات العطور",
                    "Scented Oils": "الزيوت العطرية",
                    "Scented Gel": "جل معطر",
                    "Bakoor": "بخور",
                    "Car Scents": "معطرات السيارات"
                }
            },
            "Laundry Care": {
                "ar": "العناية بالغسيل",
                "sub_subs": {
                    "Detergents": "منظفات",
                    "Softeners": "منعمات الأقمشة",
                    "Spray Starch": "بخاخ النشا",
                    "Dryer Sheets": "مناديل التجفيف",
                    "Laundry Bags": "أكياس الغسيل",
                    "Ironing": "كي الملابس"
                }
            },
            "Pest & Repair": {
                "ar": "الآفات والإصلاح",
                "sub_subs": {
                    "Insect Sprays": "بخاخات الحشرات",
                    "Ant Baits": "طعم النمل",
                    "Mouse Traps": "مصائد الفئران",
                    "Glue & Tapes": "الغراء والأشرطة",
                    "Fuses": "فيوزات",
                    "Pest Control": "مكافحة الآفات",
                    "Wall Plugs": "مقابس الحائط"
                }
            },
            "Pet Care": {
                "ar": "رعاية الحيوانات الأليفة",
                "sub_subs": {
                    "Cat Food": "طعام القطط",
                    "Dog Food": "طعام الكلاب",
                    "Cat Litter": "رمل القطط",
                    "Pet Toys": "ألعاب",
                    "Pet Bowls": "أوعية طعام",
                    "Pet Shampoos": "شامبو"
                }
            }
        }
    }
}

def is_word_subset(str1, str2):
    """
    التحقق مما إذا كانت جميع كلمات إحدى السلسلتين موجودة ككلمات كاملة في السلسلة الأخرى.
    هذا يمنع المطابقات الخاطئة للأجزاء الداخلية من الكلمات مثل (tea في steamers).
    """
    if not str1 or not str2:
        return False
    w1 = set(w.strip() for w in str1.lower().replace("&", "and").split() if w.strip())
    w2 = set(w.strip() for w in str2.lower().replace("&", "and").split() if w.strip())
    if not w1 or not w2:
        return False
    return w1.issubset(w2) or w2.issubset(w1)

def find_closest_match(target, choices):
    """
    البحث عن أفضل مطابقة نصية غير حسالة لحالة الأحرف مع دعم المطابقة التقريبية والتحقق الآمن من الكلمات الكاملة.
    """
    if not target:
        return None
    target_clean = str(target).strip().lower()
    if not target_clean:
        return None

    # 1. مطابقة تامة مع تجاهل حالة الأحرف والمسافات الزائدة
    for choice in choices:
        if choice.strip().lower() == target_clean:
            return choice

    # 2. التحقق من تطابق الكلمات بالكامل (الكلمات الفرعية الآمنة)
    for choice in choices:
        if is_word_subset(choice, target):
            return choice

    # 3. استخدام difflib للمطابقة التقريبية بحد أدنى مرتفع (0.7) لمنع الخلط
    matches = difflib.get_close_matches(target_clean, [c.strip().lower() for c in choices], n=1, cutoff=0.7)
    if matches:
        best_match_lower = matches[0]
        for choice in choices:
            if choice.strip().lower() == best_match_lower:
                return choice

    return None

def normalize_category_path(l1_en, l2_en, l3_en):
    """
    تطبيع وتصحيح مسار التصنيفات بناءً على الشجرة المعتمدة.
    ترجع القيمة كاملة بـ 6 حقول (3 بالإنجليزية و 3 بالعربية).
    """
    l1_en = (l1_en or "").strip()
    l2_en = (l2_en or "").strip()
    l3_en = (l3_en or "").strip()

    matched_l1 = find_closest_match(l1_en, list(CATEGORIES.keys()))

    # محاولة الاستنتاج العكسي في حال كان L1 خاطئاً تماماً
    if not matched_l1:
        # البحث في المستوى الثاني L2
        for temp_l1, l1_data in CATEGORIES.items():
            if l2_en and find_closest_match(l2_en, list(l1_data["subs"].keys())):
                matched_l1 = temp_l1
                break
        
        # البحث في المستوى الثالث L3
        if not matched_l1:
            for temp_l1, l1_data in CATEGORIES.items():
                for temp_l2, l2_data in l1_data["subs"].items():
                    if l3_en and find_closest_match(l3_en, list(l2_data["sub_subs"].keys())):
                        matched_l1 = temp_l1
                        break
                if matched_l1:
                    break

    # في حال تعذر العثور على أي مطابقة لـ L1
    if not matched_l1:
        return {
            "category_l1_en": l1_en,
            "category_l1_ar": "",
            "category_l2_en": l2_en,
            "category_l2_ar": "",
            "category_l3_en": l3_en,
            "category_l3_ar": ""
        }

    l1_data = CATEGORIES[matched_l1]
    matched_l1_ar = l1_data["ar"]

    # مطابقة المستوى الثاني L2
    matched_l2 = None
    if l2_en:
        matched_l2 = find_closest_match(l2_en, list(l1_data["subs"].keys()))

    # استنتاج L2 من L3 إن لم يطابق
    if not matched_l2 and l3_en:
        for temp_l2, l2_data in l1_data["subs"].items():
            if find_closest_match(l3_en, list(l2_data["sub_subs"].keys())):
                matched_l2 = temp_l2
                break

    if not matched_l2:
        return {
            "category_l1_en": matched_l1,
            "category_l1_ar": matched_l1_ar,
            "category_l2_en": l2_en,
            "category_l2_ar": "",
            "category_l3_en": l3_en,
            "category_l3_ar": ""
        }

    l2_data = l1_data["subs"][matched_l2]
    matched_l2_ar = l2_data["ar"]

    # مطابقة المستوى الثالث L3
    matched_l3 = None
    if l3_en:
        matched_l3 = find_closest_match(l3_en, list(l2_data["sub_subs"].keys()))

    if not matched_l3:
        return {
            "category_l1_en": matched_l1,
            "category_l1_ar": matched_l1_ar,
            "category_l2_en": matched_l2,
            "category_l2_ar": matched_l2_ar,
            "category_l3_en": l3_en,
            "category_l3_ar": ""
        }

    matched_l3_ar = l2_data["sub_subs"][matched_l3]

    return {
        "category_l1_en": matched_l1,
        "category_l1_ar": matched_l1_ar,
        "category_l2_en": matched_l2,
        "category_l2_ar": matched_l2_ar,
        "category_l3_en": matched_l3,
        "category_l3_ar": matched_l3_ar
    }
