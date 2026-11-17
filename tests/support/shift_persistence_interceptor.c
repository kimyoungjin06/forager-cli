/* Linux test-only injection after a real ledger rename, without production hooks. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int renameat(int oldfd, const char *oldname, int newfd, const char *newname) {
    int (*next)(int, const char *, int, const char *) = dlsym(RTLD_NEXT, "renameat");
    if (!next) _exit(90);
    int result = next(oldfd, oldname, newfd, newname);
    int saved_errno = errno;
    const char *target = getenv("PERSISTENCE_TEST_TARGET");
    const char *offset = getenv("PERSISTENCE_TEST_OFFSET");
    const char *marker = getenv("PERSISTENCE_TEST_MARKER");
    if (result == 0 && target && offset && marker &&
        strcmp(newname, "offdesk_shifts.v1.json") == 0) {
        int witness = open(marker, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
        if (witness < 0 && errno != EEXIST) _exit(91);
        if (witness >= 0) {
            int file = open(target, O_RDWR | O_NOFOLLOW | O_NONBLOCK);
            struct stat before;
            char *end;
            long position = strtol(offset, &end, 10);
            if (file < 0 || fstat(file, &before) || !S_ISREG(before.st_mode) ||
                before.st_nlink != 1 || end == offset || *end || position < 0 || position >= before.st_size) _exit(92);
            char byte;
            if (pread(file, &byte, 1, position) != 1) _exit(93);
            byte = byte == 'x' ? 'y' : 'x';
            if (pwrite(file, &byte, 1, position) != 1 || fsync(file)) _exit(94);
            struct timespec times[2] = {before.st_atim, before.st_mtim};
            if (futimens(file, times) || fsync(file) || close(file)) _exit(95);
            const char receipt[] = "{\"rename_completed\":true,\"mutation_fsynced\":true}\n";
            if (write(witness, receipt, sizeof(receipt)-1) != (ssize_t)(sizeof(receipt)-1) ||
                fsync(witness) || close(witness)) _exit(96);
        }
    }
    errno = saved_errno;
    return result;
}
