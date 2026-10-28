/* One unchanged AWG forwarding process per route, in one container. */
#include <ctype.h>
#include <errno.h>
#include <glob.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define MAX_ROUTES 64
#define MAX_VARS 256
#define MAX_LINE 8192

typedef struct { char *key, *value; } setting;
typedef struct {
    char *name;
    setting vars[MAX_VARS];
    int count;
    pid_t pid;
    unsigned backoff;
    time_t started, retry;
} route;

static route routes[MAX_ROUTES];
static size_t route_count;
extern char **environ;

static time_t now(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) { perror("clock_gettime"); exit(1); }
    return ts.tv_sec;
}

static void fail(const char *file, unsigned line, const char *reason) {
    fprintf(stderr, "supervisor: %s:%u: %s\n", file, line, reason);
    exit(1);
}

static char *copy(const char *s) {
    char *p = strdup(s);
    if (!p) { perror("strdup"); exit(1); }
    return p;
}

static char *trim(char *s) {
    while (isspace((unsigned char)*s)) ++s;
    size_t n = strlen(s);
    while (n && isspace((unsigned char)s[n-1])) s[--n] = 0;
    return s;
}

static const char *get(const route *r, const char *key) {
    for (int i = 0; i < r->count; ++i)
        if (!strcmp(r->vars[i].key, key)) return r->vars[i].value;
    return NULL;
}

static void put(route *r, const char *key, const char *value) {
    for (int i = 0; i < r->count; ++i) {
        if (!strcmp(r->vars[i].key, key)) {
            free(r->vars[i].value);
            r->vars[i].value = copy(value);
            return;
        }
    }
    if (r->count == MAX_VARS) fail(r->name, 0, "too many settings");
    r->vars[r->count++] = (setting){copy(key), copy(value)};
}

static int reserved(const char *key) {
    return !strcmp(key, "AWG_CONFIG_DIR") || !strcmp(key, "AWG_PROXY_BIN") ||
           !strcmp(key, "AWG_ROUTE_NAME");
}

static void load(route *r, const char *path, int optional) {
    FILE *f = fopen(path, "r");
    if (!f && optional && errno == ENOENT) return;
    if (!f) fail(path, 0, "cannot open configuration");
    struct stat st;
    if (fstat(fileno(f), &st) || !S_ISREG(st.st_mode) || st.st_size > 65536)
        fail(path, 0, "expected a regular configuration file, at most 64 KiB");
    char line[MAX_LINE];
    char *seen[MAX_VARS];
    int nseen = 0;
    unsigned number = 0;
    while (fgets(line, sizeof(line), f)) {
        ++number;
        if (!strchr(line, '\n') && !feof(f)) fail(path, number, "line too long");
        char *key = trim(line);
        if (!*key || *key == '#') continue;
        char *eq = strchr(key, '=');
        if (!eq) fail(path, number, "expected AWG_NAME=value");
        *eq = 0;
        key = trim(key);
        if (strncmp(key, "AWG_", 4) || !key[4] || reserved(key))
            fail(path, number, "invalid or reserved setting name");
        for (const char *p = key; *p; ++p)
            if (!(*p == '_' || (*p >= 'A' && *p <= 'Z') || isdigit((unsigned char)*p)))
                fail(path, number, "invalid setting name");
        for (int i = 0; i < nseen; ++i)
            if (!strcmp(seen[i], key)) fail(path, number, "duplicate setting");
        if (nseen == MAX_VARS) fail(path, number, "too many settings");
        seen[nseen++] = copy(key);
        char *value = trim(eq + 1);
        if (*value == '\'' || *value == '"') {
            size_t n = strlen(value);
            if (n < 2 || value[n-1] != *value) fail(path, number, "unclosed quote");
            value[n-1] = 0;
            ++value;
        }
        put(r, key, value);
    }
    if (ferror(f)) fail(path, number, "configuration read failed");
    fclose(f);
    for (int i = 0; i < nseen; ++i) free(seen[i]);
}

static int named(const char *s) {
    for (; *s; ++s)
        if (!(isalnum((unsigned char)*s) || *s == '-' || *s == '_' || *s == '.')) return 0;
    return 1;
}

static void validate(const route *r) {
    const char *required[] = {"AWG_LISTEN", "AWG_REMOTE", "AWG_JC", "AWG_JMIN",
        "AWG_JMAX", "AWG_S1", "AWG_S2", "AWG_H1", "AWG_H2", "AWG_H3",
        "AWG_H4", "AWG_SERVER_PUB", NULL};
    for (int i = 0; required[i]; ++i) {
        const char *v = get(r, required[i]);
        if (!v || !*v) fail(r->name, 0, required[i]);
    }
    const char *mode = get(r, "AWG_MODE");
    if (mode && strcmp(mode, "normal") && strcmp(mode, "reverse") && strcmp(mode, "server"))
        fail(r->name, 0, "AWG_MODE must be normal, reverse or server");
    const char *pub = get(r, "AWG_CLIENT_PUB");
    if (!pub || !*pub) {
        const char *pubs = get(r, "AWG_CLIENT_PUBS");
        const char *file = get(r, "AWG_CLIENT_PUBS_FILE");
        if (!mode || strcmp(mode, "server") || ((!pubs || !*pubs) && (!file || !*file)))
            fail(r->name, 0, "client public key(s) missing");
    }
}

static void start(route *r, const char *binary, const sigset_t *oldmask) {
    pid_t pid = fork();
    if (pid < 0) {
        fprintf(stderr, "supervisor: [%s] fork failed, retry in 5s\n", r->name);
        r->retry = now() + 5;
        return;
    }
    if (!pid) {
        /* No AWG settings can leak between routes through the container env. */
        for (size_t i = 0; environ[i]; ) {
            if (!strncmp(environ[i], "AWG_", 4)) {
                char *key = copy(environ[i]);
                char *eq = strchr(key, '=');
                if (eq) *eq = 0;
                unsetenv(key);
                free(key);
                i = 0;
            } else ++i;
        }
        for (int i = 0; i < r->count; ++i)
            if (setenv(r->vars[i].key, r->vars[i].value, 1)) _exit(126);
        if (setenv("AWG_ROUTE_NAME", r->name, 1)) _exit(126);
        if (sigprocmask(SIG_SETMASK, oldmask, NULL)) _exit(126);
        execl(binary, binary, (char *)NULL);
        fprintf(stderr, "supervisor: [%s] exec failed: %s\n", r->name, strerror(errno));
        _exit(127);
    }
    r->pid = pid;
    r->started = now();
    fprintf(stderr, "supervisor: [%s] started pid=%ld listen=%s remote=%s\n",
            r->name, (long)pid, get(r, "AWG_LISTEN"), get(r, "AWG_REMOTE"));
}

int main(int argc, char **argv) {
    int check = 0;
    const char *dir_arg = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--check") && !check) check = 1;
        else if (!strcmp(argv[i], "--config-dir") && !dir_arg && i + 1 < argc)
            dir_arg = argv[++i];
        else {
            fprintf(stderr, "usage: awg-supervisor [--config-dir DIRECTORY] [--check]\n");
            return 1;
        }
    }
    const char *binary_env = getenv("AWG_PROXY_BIN");
    char *binary = copy(binary_env && *binary_env ? binary_env : "/awg-proxy");
    const char *dir = dir_arg ? dir_arg : getenv("AWG_CONFIG_DIR");
    if (!dir || !*dir) {
        if (check) { fprintf(stderr, "supervisor: AWG_CONFIG_DIR is required for --check\n"); return 1; }
        execl(binary, binary, (char *)NULL); /* Original env-only entrypoint. */
        perror("exec awg-proxy");
        return 1;
    }
    if (access(binary, X_OK)) { perror("awg-proxy executable"); return 1; }
    char common[4096], pattern[4096];
    if (snprintf(common, sizeof(common), "%s/common.env", dir) >= (int)sizeof(common) ||
        snprintf(pattern, sizeof(pattern), "%s/*.env", dir) >= (int)sizeof(pattern))
        fail(dir, 0, "directory name too long");
    glob_t files = {0};
    if (glob(pattern, 0, NULL, &files)) fail(dir, 0, "no route .env files found");
    for (size_t i = 0; i < files.gl_pathc; ++i) {
        const char *path = files.gl_pathv[i];
        const char *name = strrchr(path, '/');
        name = name ? name + 1 : path;
        if (!strcmp(name, "common.env")) continue;
        if (!named(name)) fail(path, 0, "route filename must use letters, digits, dot, dash or underscore");
        if (route_count == MAX_ROUTES) fail(dir, 0, "at most 64 routes are supported");
        route *r = &routes[route_count++];
        r->name = copy(name);
        r->name[strlen(r->name) - 4] = 0;
        r->backoff = 1;
        load(r, common, 1);
        load(r, path, 0);
        validate(r);
        for (size_t j = 0; j + 1 < route_count; ++j)
            if (!strcmp(get(r, "AWG_LISTEN"), get(&routes[j], "AWG_LISTEN")))
                fail(path, 0, "AWG_LISTEN already used by another route");
    }
    globfree(&files);
    if (!route_count) fail(dir, 0, "no route .env files found");
    fprintf(stderr, "supervisor: %zu route configurations loaded\n", route_count);
    if (check) return 0;

    sigset_t signals, oldmask;
    sigemptyset(&signals);
    sigaddset(&signals, SIGCHLD);
    sigaddset(&signals, SIGTERM);
    sigaddset(&signals, SIGINT);
    if (sigprocmask(SIG_BLOCK, &signals, &oldmask)) { perror("sigprocmask"); return 1; }
    /* Inherited SIG_IGN must not auto-reap children or discard stop requests. */
    struct sigaction action = {0};
    action.sa_handler = SIG_DFL;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGCHLD, &action, NULL) || sigaction(SIGTERM, &action, NULL) ||
        sigaction(SIGINT, &action, NULL)) { perror("sigaction"); return 1; }
    int stopping = 0, killed = 0;
    time_t deadline = 0;
    for (;;) {
        time_t t = now();
        int alive = 0;
        for (size_t i = 0; i < route_count; ++i) {
            route *r = &routes[i];
            if (!stopping && !r->pid && r->retry <= t) start(r, binary, &oldmask);
            if (r->pid) ++alive;
        }
        if (stopping && !alive) break;
        if (stopping && !killed && t >= deadline) {
            for (size_t i = 0; i < route_count; ++i)
                if (routes[i].pid) kill(routes[i].pid, SIGKILL);
            killed = 1;
        }
        struct timespec timeout = {1, 0};
        int sig = sigtimedwait(&signals, NULL, &timeout);
        if (sig == SIGTERM || sig == SIGINT) {
            if (!stopping) {
                stopping = 1;
                deadline = now() + 5;
                fprintf(stderr, "supervisor: stopping all routes\n");
                for (size_t i = 0; i < route_count; ++i)
                    if (routes[i].pid) kill(routes[i].pid, SIGTERM);
            }
        } else if (sig < 0 && errno != EAGAIN && errno != EINTR) {
            perror("sigtimedwait");
            /* Remain responsible for children even on an unexpected wait error. */
            if (!stopping) {
                stopping = 1;
                deadline = now();
            }
        }
        int status;
        pid_t pid;
        while ((pid = waitpid(-1, &status, WNOHANG)) > 0) {
            for (size_t i = 0; i < route_count; ++i) {
                route *r = &routes[i];
                if (r->pid != pid) continue;
                r->pid = 0;
                if (now() - r->started >= 30) r->backoff = 1;
                r->retry = now() + r->backoff;
                fprintf(stderr, "supervisor: [%s] pid=%ld exited status=%d%s\n",
                        r->name, (long)pid, status, stopping ? " (stopping)" : " (will restart)");
                if (r->backoff < 30) r->backoff = r->backoff < 16 ? r->backoff * 2 : 30;
                break;
            }
        }
    }
    return 0;
}
