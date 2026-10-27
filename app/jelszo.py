"""Jelszó-hash készítése a USERS változóhoz.

Használat:  docker exec -it polc python -m app.jelszo
"""
import getpass
import sys

from .services import hash_password


def main() -> None:
    pw = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("Jelszó: ")
    if len(sys.argv) <= 1 and getpass.getpass("Jelszó újra: ") != pw:
        print("A két jelszó nem egyezik.", file=sys.stderr)
        sys.exit(1)
    print(hash_password(pw))


if __name__ == "__main__":
    main()
