import os
import tempfile

# A live timed reviewer table on the owner's machine must never change test results (models-tab R19).
if not os.environ.get("RELAY_HOME", "").startswith(tempfile.gettempdir()):
    os.environ["RELAY_HOME"] = tempfile.mkdtemp(prefix="relay-home-")
