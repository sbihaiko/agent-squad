/* psfast.c: the polling process read, without an interpreter (#1511).
 *
 * `sg_ps` (caps.sh) reads the process table through `squad.procs`, which is a Python module, so every read paid for a new
 * interpreter: about 0.7 s on a loaded loop box against 10 ms for the reader it replaced. The callers that poll ask one
 * question about one pid -- is it alive, is it a zombie, which group does it lead -- and this answers that shape in a few
 * milliseconds: a `-p` list and `-o` over pid, ppid, pgid and stat.
 *
 * Every other shape exits 3 with no output, and caps.sh falls back to `squad.procs`, which stays the one reader of the
 * command line, the start time and the whole table. The fields shared by the two are the same fields of the same kernel
 * structs, read in the same order (proc_bsdinfo, then kinfo_proc), and tests/test_proc_reader.py holds them to one answer.
 *
 * macOS only: caps.sh builds this on Darwin and nowhere else.
 */
#include <libproc.h>
#include <sys/param.h>
#include <sys/proc.h>
#include <sys/sysctl.h>

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define UNSUPPORTED 3          /* caps.sh reads this: the shape is not this reader's, so it runs squad.procs */
#define MAX_PIDS 4096

static int want_pid, want_ppid, want_pgid, want_stat;

struct row {
	int pid, ppid, pgid;
	char stat;
};

/* `pid=,ppid=`, the one field syntax the module accepts: each item ends in `=`, and an unknown field is not ours. */
static int parse_fields(const char *v)
{
	const char *p = v;

	while (*p) {
		const char *e = strchr(p, ',');
		size_t n = e ? (size_t)(e - p) : strlen(p);

		if (n == 4 && !strncmp(p, "pid=", 4))
			want_pid = 1;
		else if (n == 5 && !strncmp(p, "ppid=", 5))
			want_ppid = 1;
		else if (n == 5 && !strncmp(p, "pgid=", 5))
			want_pgid = 1;
		else if (n == 5 && !strncmp(p, "stat=", 5))
			want_stat = 1;
		else
			return 0;
		if (e == NULL)
			return 1;
		p = e + 1;
	}
	return 0;                  /* an empty -o names no field */
}

static int parse_pids(const char *v, int *pids, int *np)
{
	while (*v) {
		char *end;
		long pid;

		errno = 0;
		pid = strtol(v, &end, 10);
		if (end == v || errno != 0 || pid <= 0 || pid > 0x7fffffff || *np >= MAX_PIDS)
			return 0;
		if (*end != '\0' && *end != ',')
			return 0;          /* int() in the module refuses it too, with exit 2 */
		pids[(*np)++] = (int)pid;
		v = *end ? end + 1 : end;
	}
	return *np > 0;
}

static int cmp_pid(const void *a, const void *b)
{
	int x = *(const int *)a, y = *(const int *)b;

	return (x > y) - (x < y);
}

/* The fields of one pid, in the order squad.procs reads them: the bsd info the module prefers, the kinfo the system
 * answers for another user's process. 0 means gone, as the module's None does. */
static int read_row(struct row *r)
{
	struct proc_bsdinfo info;

	if (proc_pidinfo(r->pid, PROC_PIDTBSDINFO, 0, &info, (int)sizeof info) == (int)sizeof info) {
		r->ppid = (int)info.pbi_ppid;
		r->pgid = (int)info.pbi_pgid;
		r->stat = info.pbi_status == SZOMB ? 'Z' : 'S';
		return 1;
	}
	{
		struct kinfo_proc kp;
		size_t len = sizeof kp;
		int mib[4] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, r->pid};

		if (sysctl(mib, 4, &kp, &len, NULL, 0) != 0 || len != sizeof kp)
			return 0;
		r->ppid = kp.kp_eproc.e_ppid;
		r->pgid = kp.kp_eproc.e_pgid;
		r->stat = kp.kp_proc.p_stat == SZOMB ? 'Z' : 'S';
	}
	return 1;
}

int main(int argc, char **argv)
{
	int pids[MAX_PIDS], np = 0, i;
	struct row rows[MAX_PIDS];
	int nr = 0;

	for (i = 1; i < argc; i++) {
		char *a = argv[i];
		int j;

		if (a[0] != '-' || a[1] == '\0')
			return UNSUPPORTED;
		for (j = 1; a[j] != '\0'; j++) {
			char c = a[j];
			const char *value;

			if (c == 'a' || c == 'x' || c == 'w')
				continue;          /* accepted, no effect, as the module accepts them */
			if (c != 'o' && c != 'p')
				return UNSUPPORTED;
			if (a[j + 1] != '\0')
				value = a + j + 1;
			else if (i + 1 < argc)
				value = argv[++i];
			else
				return UNSUPPORTED;
			if (c == 'o') {
				if (!parse_fields(value))
					return UNSUPPORTED;
			} else if (!parse_pids(value, pids, &np)) {
				return UNSUPPORTED;
			}
			break;                 /* the value consumed the rest of this word */
		}
	}
	if (np == 0 || !(want_pid || want_ppid || want_pgid || want_stat))
		return UNSUPPORTED;

	qsort(pids, (size_t)np, sizeof pids[0], cmp_pid);
	for (i = 0; i < np; i++) {
		struct row r;

		if (i > 0 && pids[i] == pids[i - 1])
			continue;              /* the module keys its answer by pid, so one line per pid */
		r.pid = pids[i];
		if (read_row(&r))
			rows[nr++] = r;
	}
	if (nr == 0)
		return 1;                  /* ps exits 1 when no -p pid is live */

	for (i = 0; i < nr; i++) {
		int first = 1;

		if (want_pid) {
			printf("%5d", rows[i].pid);
			first = 0;
		}
		if (want_ppid) {
			printf("%s%5d", first ? "" : " ", rows[i].ppid);
			first = 0;
		}
		if (want_pgid) {
			printf("%s%5d", first ? "" : " ", rows[i].pgid);
			first = 0;
		}
		if (want_stat)
			printf("%s%c", first ? "" : " ", rows[i].stat);
		putchar('\n');
	}
	fflush(stdout);
	return 0;
}
