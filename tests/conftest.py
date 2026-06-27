"""Test environment setup — must run before any app module imports.

``app.config.get_settings`` is ``lru_cache``d and ``app.db`` reads it at import
time, so the environment has to be in place before the first app import. pytest
imports conftest.py before collecting test modules, which guarantees that.
"""

import os
import tempfile

# Pin the environment explicitly so the suite is hermetic even when a deployment
# .env (e.g. APN_ENVIRONMENT=production) sits in the working directory — os.environ
# takes precedence over the .env file in pydantic-settings.
os.environ["APN_ENVIRONMENT"] = "development"
os.environ["APN_DATA_DIR"] = tempfile.mkdtemp(prefix="apn-test-")
os.environ["APN_SCHEDULER_ENABLED"] = "false"
os.environ["APN_SECRET_KEY"] = "test-secret"
os.environ["APN_ADMIN_USERNAME"] = "admin"
os.environ["APN_ADMIN_PASSWORD"] = "supersecret-pw-123"
