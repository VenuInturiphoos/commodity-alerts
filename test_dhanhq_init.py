from dhanhq import dhanhq
try:
    d = dhanhq("fake_id", "fake_token")
    print("Positional succeeded")
except Exception as e:
    print(f"Positional failed: {e}")

try:
    d = dhanhq(client_id="fake_id", access_token="fake_token")
    print("Kwargs succeeded")
except Exception as e:
    print(f"Kwargs failed: {e}")
