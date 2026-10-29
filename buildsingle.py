"""Bundle app.py + lf_*.py into ONE file (for easy GitHub/Streamlit upload): python build_single.py"""
import os

mods = ["lf_verify", "lf_store", "lf_refresh", "lf_enrich", "lf_gemini"]  # dependency order
out = ['"""LeadForge — single-file build. Upload only this file + requirements.txt."""',
       "import sys as _sys, types as _types", "",
       "def _register(name, src):",
       "    m = _types.ModuleType(name)",
       "    m.__file__ = name + '.py'",
       "    _sys.modules[name] = m",
       "    exec(compile(src, name + '.py', 'exec'), m.__dict__)", ""]
for m in mods:
    out.append(f"_register({m!r}, {open(m + '.py').read()!r})\n")
out += ["# ---------------- app ----------------", open("app.py").read()]
dest = "../leadforge-single-file"
os.makedirs(dest, exist_ok=True)
open(f"{dest}/app.py", "w").write("\n".join(out))
open(f"{dest}/requirements.txt", "w").write(open("requirements.txt").read())
print("built", dest)
