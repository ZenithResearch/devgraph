"""Fail before token issuance without printing credential contents."""

import os
import sys


def main():
    names = ("SDK_SOURCE_APP_CLIENT_ID", "SDK_SOURCE_APP_PRIVATE_KEY")
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        print(
            "Companion source access is not configured: "
            + ", ".join(missing)
            + ". Follow docs/dev/companion-ci.md; use the approved read-only source App."
            + " No companion checks have run.",
            file=sys.stderr,
        )
        return 1
    print("Companion source-access settings are present (authorization checked by GitHub).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
