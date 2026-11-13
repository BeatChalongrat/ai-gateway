import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("order", ["openai:gpt-image-1", "custom:owned-image,mock:mock-image-1"])
def test_media_migration_keeps_custom_orders_and_history(order):
    path = Path(__file__).parents[1]/"migrations/versions/090_media_readiness.py"
    spec = importlib.util.spec_from_file_location("media_migration", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    db = sqlite3.connect(":memory:")
    db.executescript("""
      CREATE TABLE model_configs(provider TEXT, model TEXT, supports_image_gen BOOLEAN DEFAULT FALSE,
      supports_video_gen BOOLEAN DEFAULT FALSE, is_active BOOLEAN, show_in_chat BOOLEAN,
      cost_per_input_token REAL, source TEXT, UNIQUE(provider,model));
      CREATE TABLE system_settings(key TEXT PRIMARY KEY,value TEXT);
      INSERT INTO model_configs(provider,model,supports_image_gen,is_active,show_in_chat)
      VALUES ('openai','gpt-image-1',TRUE,FALSE,FALSE);
      INSERT INTO model_configs(provider,model,supports_video_gen,is_active,show_in_chat)
      VALUES ('openai','sora-2',TRUE,TRUE,TRUE);
    """)
    db.execute("INSERT INTO system_settings VALUES ('media_order:image',?)", (order,))
    module.op = SimpleNamespace(execute=db.execute)
    module.upgrade(); module.upgrade()
    value = db.execute("SELECT value FROM system_settings").fetchone()[0]
    assert value == ("openai:gpt-image-2.6-flare" if order == "openai:gpt-image-1" else order)
    assert db.execute("SELECT supports_image_gen,is_active,show_in_chat FROM model_configs WHERE model='gpt-image-2.6-flare'").fetchone() == (1,0,0)
    assert db.execute("SELECT supports_video_gen,is_active FROM model_configs WHERE model='sora-2'").fetchone() == (0,0)
    module.downgrade()
    assert db.execute("SELECT count(*) FROM model_configs").fetchone()[0] == 3
    assert db.execute("SELECT is_active FROM model_configs WHERE model='sora-2'").fetchone()[0] == 0
    db.close()
