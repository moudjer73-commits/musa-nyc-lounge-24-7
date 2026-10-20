# -*- coding: utf-8 -*-
import os, sys, json, time, base64, shutil, sqlite3, threading, subprocess
from huggingface_hub import HfApi, hf_hub_download

HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
REPO = "moudjer/musa-nyc-lounge-vault"

print("📥 [1/3] Decrypting Lounge credentials from Private Vault...")
cfg_file = hf_hub_download(repo_id=REPO, filename="lounge_cfg.dat", repo_type="dataset", token=HF_TOKEN)
raw_b64 = open(cfg_file, encoding="utf-8").read().strip()
decoded_json = base64.b64decode(raw_b64.encode("utf-8")).decode("utf-8")[::-1]
for k, v in json.loads(decoded_json).items():
    os.environ[k] = str(v)

print("📦 [2/3] Installing requirements...")
reqs = hf_hub_download(repo_id=REPO, filename="requirements.txt", repo_type="dataset", token=HF_TOKEN)
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", reqs])

# مؤقت التناوب الذكي: بعد 5 ساعات و20 دقيقة يحفظ قاعدة البيانات ويغلق بانتظام لتبدأ الدورة التالية فوراً
def graceful_cycle_handover():
    time.sleep(19200)  # 5 hours 20 minutes
    print("🔄 Cycle handover reached! Performing final database backup to Private Vault...")
    try:
        db_file = "/content/musa_nyc_lounge_v8.db"
        if os.path.exists(db_file):
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
            print("✅ Final database backup completed!")
    except Exception as e:
        print(f"⚠️ Final backup notice: {e}")
    os._exit(0)

threading.Thread(target=graceful_cycle_handover, daemon=True).start()

print("🏛️ [3/3] Launching Musa's NYC Lounge 24/7...")
os.makedirs("/content", exist_ok=True)
app_file = hf_hub_download(repo_id=REPO, filename="app.py", repo_type="dataset", token=HF_TOKEN)
exec(open(app_file, encoding="utf-8").read(), globals())
