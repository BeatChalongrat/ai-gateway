import os,sys,json,urllib.request
sys.path.insert(0,"/app")
from app.core.security import decrypt_provider_key
from app.core.config import settings
from sqlalchemy import create_engine, text
eng=create_engine(settings.database_url.replace("+asyncpg",""))
with eng.connect() as c:
    row=c.execute(text("select api_key_encrypted from provider_keys where provider_name='openrouter' and is_active and last_test_status='valid' limit 1")).fetchone()
k=decrypt_provider_key(row[0], settings.effective_master_key)
req=urllib.request.Request("https://openrouter.ai/api/v1/credits", headers={"Authorization":"Bearer "+k})
print(urllib.request.urlopen(req,timeout=30).read().decode())
