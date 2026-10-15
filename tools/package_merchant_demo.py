"""Build a reproducible, source-only merchant demo archive for direct GitHub download."""
import argparse
import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "merchant-demo"
ARCHIVE = ROOT / "Pay_Callback_Demo.zip"
FILES = (
    ".dockerignore", ".env.example", ".gitignore", "Caddyfile", "Dockerfile",
    "app.py", "compose.existing-proxy.yaml", "compose.yaml", "configure.py", "pay_http.py",
    "requirements.txt", "run.sh", "static/app.js", "static/index.html", "static/style.css",
    "tests/test_demo.py", "tests/test_pay_http.py", "wsgi.py",
)


def build_archive():
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(FILES):
            source = SOURCE / name
            if source.is_symlink():
                raise ValueError(f"Refusing symlink: {name}")
            entry = ZipInfo("Pay_Callback_Demo/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (0o100755 if name == "run.sh" else 0o100644) << 16
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, source.read_bytes(), compresslevel=9)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify the committed archive matches its source")
    args = parser.parse_args()
    content = build_archive()
    checksum = f"{hashlib.sha256(content).hexdigest()}  {ARCHIVE.name}\n"
    checksum_path = ARCHIVE.with_suffix(".zip.sha256")
    if args.check:
        if not ARCHIVE.is_file() or ARCHIVE.read_bytes() != content or not checksum_path.is_file() or checksum_path.read_text() != checksum:
            raise SystemExit("Archive is out of date. Run python tools/package_merchant_demo.py")
        print("Merchant archive and SHA-256 match the allowlisted source files.")
    else:
        ARCHIVE.write_bytes(content)
        checksum_path.write_text(checksum)
        print(f"Created {ARCHIVE.name}: {len(FILES)} files, {len(content)} bytes; runtime data excluded.")


if __name__ == "__main__":
    main()
