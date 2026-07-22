# test_validate_blade_js.py
import re

file_path = r"f:\automation\dashboard\resources\views\dashboard\batch_automation.blade.php"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Extract script contents
scripts = re.findall(r"<script>(.*?)</script>", content, re.DOTALL)
print(f"Found {len(scripts)} <script> blocks.")

for idx, script in enumerate(scripts):
    open_curly = script.count('{')
    close_curly = script.count('}')
    open_paren = script.count('(')
    close_paren = script.count(')')
    print(f"Script block {idx+1}: braces count = {open_curly} open / {close_curly} close | parens count = {open_paren} open / {close_paren} close")
    if open_curly != close_curly:
        print(f"⚠️ Mismatch in curly braces! {open_curly} != {close_curly}")
    else:
        print("✅ Curly braces count perfectly matched!")
