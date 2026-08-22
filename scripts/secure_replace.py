#!/usr/bin/env python3
import os
import secrets
import shutil
import stat
import sys


def replace(wiki, relative, source):
    parts = relative.split("/")
    if (not relative or relative.startswith("/") or any(p in ("", ".", "..", "archive") for p in parts)):
        raise ValueError("unsafe target path")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("secure directory traversal is unsupported")

    directory = os.open(wiki, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child

        target = parts[-1]
        if not stat.S_ISREG(os.stat(target, dir_fd=directory, follow_symlinks=False).st_mode):
            raise ValueError("target is not a regular file")

        temp = f".memory-hub-publish.{secrets.token_hex(8)}"
        temp_fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with open(source, "rb") as src, os.fdopen(temp_fd, "wb") as dst:
                temp_fd = -1
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(temp, target, src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            if temp_fd >= 0:
                os.close(temp_fd)
            try:
                os.unlink(temp, dir_fd=directory)
            except FileNotFoundError:
                pass
            raise
    finally:
        os.close(directory)


if __name__ == "__main__":
    try:
        replace(*sys.argv[1:])
    except (OSError, ValueError) as exc:
        print(f"secure_replace: {exc}", file=sys.stderr)
        sys.exit(1)
