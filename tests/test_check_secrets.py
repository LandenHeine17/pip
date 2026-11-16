"""The pre-commit secret check catches the key names Pip really uses (Google's, Tuya's) and the Pi's Tailscale
address (the repo is public, D-70), without flagging the tests' placeholders."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from check_secrets import scan  # noqa: E402

CAUGHT = ['client_secret = "abcdefghijklmnop"', '"access_secret": "abcdefghijklmnopq"', 'local_key = "abcdef1234567890"',   # secret-check: allow (samples)
          'refresh_token = "1//0abcdefghijabcdefghijabcdefghij12"', "key AIza" + "B" * 35, "GOCSPX-" + "a" * 24,   # secret-check: allow (samples)
          "dashboard at 100.101.102.103:8790", '"api_key": "zzzzzzzzzzzzzzzz"']   # secret-check: allow (samples)
FINE = ["host 100.64.0.1", 'token = ""', 'password = "short"', 'client_id = os.environ["X"]', "version 100.120.3"]


def test_real_key_names_and_the_tailscale_address_are_caught():
    for line in CAUGHT:
        assert scan(line, "x"), line


def test_placeholders_pass():
    for line in FINE:
        assert not scan(line, "x"), line
