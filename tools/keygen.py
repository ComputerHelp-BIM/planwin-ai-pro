#!/usr/bin/env python3
"""PlanWin AI Pro licence generator – FOR COMPUTER HELP INTERNAL USE ONLY.

Keep the private key file OUT of the repository and backed up securely.

Usage:
    python tools/keygen.py init  --out planwin_license_private_key.pem
        Creates a new key pair and prints the public key to paste into
        planwin_ai/licensing/license.py (PUBLIC_KEY_B64). Existing licences
        stop working if you rotate the key.

    python tools/keygen.py issue --key planwin_license_private_key.pem \
        --name "Ravi Kumar" --company "ABC Consultants" --email ravi@abc.in \
        --expires 2028-03-31 --seats 1 --out ABC_Consultants.lic
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from planwin_ai.licensing.license import canonical, verify  # noqa: E402


def cmd_init(a):
    k = Ed25519PrivateKey.generate()
    Path(a.out).write_bytes(
        k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print("Private key written to", a.out)
    print("PUBLIC_KEY_B64 =", base64.b64encode(pub).decode())


def cmd_issue(a):
    key = serialization.load_pem_private_key(Path(a.key).read_bytes(), password=None)
    lic = {
        "name": a.name,
        "company": a.company,
        "email": a.email,
        "edition": "pro",
        "issued": dt.date.today().isoformat(),
        "expires": a.expires,
        "seats": int(a.seats),
    }
    lic["sig"] = base64.b64encode(key.sign(canonical(lic))).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert verify(lic, base64.b64encode(pub).decode()), "self-check failed"
    Path(a.out).write_text(json.dumps(lic, indent=1), encoding="utf-8")
    print("Licence written to", a.out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--out", required=True)
    s = sub.add_parser("issue")
    s.add_argument("--key", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--company", default="")
    s.add_argument("--email", default="")
    s.add_argument("--expires", default="")
    s.add_argument("--seats", default=1)
    s.add_argument("--out", required=True)
    a = ap.parse_args()
    {"init": cmd_init, "issue": cmd_issue}[a.cmd](a)


if __name__ == "__main__":
    main()
