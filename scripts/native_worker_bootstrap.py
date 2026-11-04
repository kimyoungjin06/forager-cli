import hashlib
import os
import stat
import sys
path, expected = sys.argv[1:3]
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
try:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 262144:
        raise ValueError('Invalid bound controller file')
    with os.fdopen(fd, 'rb', closefd=False) as source:
        code = source.read(262145)
    if len(code) > 262144 or hashlib.sha256(code).hexdigest() != expected:
        raise ValueError('Bound controller hash changed')
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('Bound controller changed during read')
finally:
    os.close(fd)
sys.argv = [path] + sys.argv[3:]
exec(compile(code, path, 'exec'), {'__name__': '__main__', '__file__': path})
