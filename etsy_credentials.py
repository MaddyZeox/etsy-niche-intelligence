
import getpass
import keyring

SERVICE = "NicheIntelligence-Etsy"

print("Niche Intelligence - Secure Credentials Setup")
print("---------------------------------------------")

key = getpass.getpass("Enter Etsy Keystring: ").strip()
secret = getpass.getpass("Enter latest Shared Secret: ").strip()

if not key or not secret:
    raise SystemExit("Error: Both credentials are required.")

if ":" in key or ":" in secret:
    raise SystemExit("Error: Unexpected colon in credentials.")

keyring.set_password(SERVICE, "keystring", key)
keyring.set_password(SERVICE, "shared_secret", secret)

saved_key = keyring.get_password(SERVICE, "keystring")
saved_secret = keyring.get_password(SERVICE, "shared_secret")

if saved_key == key and saved_secret == secret:
    print("\nSUCCESS: Credentials securely saved.")
    print("Storage: Windows Credential Manager")
    print("API credentials were not printed.")
else:
    print("\nERROR: Credential verification failed.")
