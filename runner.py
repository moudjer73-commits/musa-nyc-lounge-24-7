# -*- coding: utf-8 -*-
import os, sys, json, time, base64, shutil, sqlite3, threading, subprocess
from huggingface_hub import HfApi, hf_hub_download

HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
REPO = "moudjer/musa-nyc-lounge-vault"
os.makedirs("/content/tts_cache", exist_ok=True)

print("📥 [1/4] Decrypting Lounge credentials from Private Vault...")
cfg_file = hf_hub_download(repo_id=REPO, filename="lounge_cfg.dat", repo_type="dataset", token=HF_TOKEN)
raw_b64 = open(cfg_file, encoding="utf-8").read().strip()
decoded_json = base64.b64decode(raw_b64.encode("utf-8")).decode("utf-8")[::-1]
for k, v in json.loads(decoded_json).items():
    os.environ[k] = str(v)
os.environ["HF_TOKEN"] = HF_TOKEN

print("🗄️ [2/4] Restoring SQLite Database from Private Vault...")
try:
    db_vault = hf_hub_download(repo_id=REPO, filename="musa_nyc_lounge_v8.db", repo_type="dataset", token=HF_TOKEN)
    shutil.copy2(db_vault, "/content/musa_nyc_lounge_v8.db")
    print("✅ Database restored to /content/musa_nyc_lounge_v8.db")
except Exception as e:
    print(f"ℹ️ Database restore note: {e}")

print("📦 [3/4] Installing Python packages...")
reqs = hf_hub_download(repo_id=REPO, filename="requirements.txt", repo_type="dataset", token=HF_TOKEN)
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", reqs])

def graceful_cycle_handover():
    time.sleep(19200)  # 5 hours 20 minutes
    print("🔄 Performing end-of-cycle database backup to Private Vault...")
    try:
        db_file = "/content/musa_nyc_lounge_v8.db"
        if os.path.exists(db_file) and os.path.getsize(db_file) > 4096:
            snap = "/tmp/final_cycle_backup.db"
            s_c = sqlite3.connect(db_file, timeout=10.0)
            d_c = sqlite3.connect(snap)
            s_c.backup(d_c)
            d_c.close()
            s_c.close()
            HfApi(token=HF_TOKEN).upload_file(
                path_or_fileobj=snap,
                path_in_repo="musa_nyc_lounge_v8.db",
                repo_id=REPO,
                repo_type="dataset",
                commit_message="End-of-cycle automatic DB sync",
            )
    except Exception as e:
        print(f"⚠️ Final backup notice: {e}")
    os._exit(0)

threading.Thread(target=graceful_cycle_handover, daemon=True).start()

print("🏛️ [4/4] Launching Musa's NYC Lounge 24/7 (Clean Subprocess)...")
app_file = hf_hub_download(repo_id=REPO, filename="app.py", repo_type="dataset", token=HF_TOKEN)
shutil.copy2(app_file, "/content/app.py")

# تشغيل البوت مع إعادة التشغيل التلقائي الفوري لو حدث أي خطأ عابر
while True:
    proc = subprocess.run([sys.executable, "/content/app.py"])
    print(f"⚠️ Bot process exited with code {proc.returncode}. Restarting in 3 seconds...")
    time.sleep(3)
