"""DB connection string + credential detector.

Both ``api_key_secret`` (generic credentials) and
``db_connection_string`` (DB URLs with embedded passwords) share the
``[CREDENTIAL]`` mask token base.
"""

from __future__ import annotations

from ._helpers import detector_names_for, mask


class TestDbConnectionStringHappyPath:
    def test_postgres_url(self):
        names = detector_names_for(
            "postgresql://admin:SuperSecret99@db.internal:5432/prod"
        )
        assert "db_connection_string" in names

    def test_mysql_url(self):
        assert "db_connection_string" in detector_names_for(
            "mysql://user:pass@host/db"
        )

    def test_mongodb_url(self):
        assert "db_connection_string" in detector_names_for(
            "mongodb://user:pass@host:27017/db"
        )

    def test_thai_label_with_postgres_url(self):
        text = (
            "เชื่อมต่อฐานข้อมูล: "
            "postgresql://admin:pass@localhost/mydb"
        )
        names = detector_names_for(text)
        assert "db_connection_string" in names


class TestDbConnectionStringBoundary:
    def test_redis_without_user(self):
        # `redis://:password@host` — only password, no user — must fire.
        assert "db_connection_string" in detector_names_for(
            "redis://:password@localhost:6379/0"
        )


class TestDbConnectionStringNegative:
    def test_postgres_without_password(self):
        # No userinfo → not a credential URL.
        assert "db_connection_string" not in detector_names_for(
            "postgresql://host/db"
        )

    def test_https_url(self):
        # No credentials embedded.
        assert "db_connection_string" not in detector_names_for(
            "https://example.com"
        )
