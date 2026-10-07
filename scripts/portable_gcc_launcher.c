#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

/* Preserve an ASCII junction prefix: MinGW otherwise resolves it back to a
   Unicode installation path and the shipped linker exits 116 on this host.
   This changes path handling only; all caller compilation flags are retained. */
#define COMMAND_CAPACITY 32768
static int put(wchar_t *buffer, size_t *used, wchar_t value) {
    if (*used + 1 >= COMMAND_CAPACITY) return 0;
    buffer[(*used)++] = value;
    buffer[*used] = 0;
    return 1;
}
static int quote(wchar_t *buffer, size_t *used, const wchar_t *value) {
    size_t slashes = 0;
    if (!put(buffer, used, L'"')) return 0;
    for (; *value; ++value) {
        if (*value == L'\\') { ++slashes; continue; }
        size_t count = *value == L'"' ? 2 * slashes + 1 : slashes;
        while (count--) if (!put(buffer, used, L'\\')) return 0;
        slashes = 0;
        if (!put(buffer, used, *value)) return 0;
    }
    for (size_t i = 0; i < 2 * slashes; ++i) if (!put(buffer, used, L'\\')) return 0;
    return put(buffer, used, L'"') && put(buffer, used, L' ');
}
int wmain(int argc, wchar_t **argv) {
    wchar_t real[COMMAND_CAPACITY], command[COMMAND_CAPACITY] = {0};
    DWORD length = GetEnvironmentVariableW(L"LUCE_PORTABLE_GCC", real, COMMAND_CAPACITY);
    if (!length || length >= COMMAND_CAPACITY) {
        fwprintf(stderr, L"Use the portable launcher to configure the bundled compiler.\n");
        return 2;
    }
    size_t used = 0;
    if (!quote(command, &used, real) || !quote(command, &used, L"-no-canonical-prefixes")) return 87;
    for (int i = 1; i < argc; ++i) if (!quote(command, &used, argv[i])) return 87;
    HANDLE job = CreateJobObjectW(NULL, NULL);
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = {0};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!job || !SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
        fwprintf(stderr, L"Cannot initialize compiler process ownership: %lu\n", GetLastError());
        if (job) CloseHandle(job);
        return 2;
    }
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION child = {0};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    if (!CreateProcessW(real, command, NULL, NULL, TRUE, CREATE_SUSPENDED | CREATE_NO_WINDOW,
                        NULL, NULL, &startup, &child)) {
        fwprintf(stderr, L"Cannot start bundled compiler: %lu\n", GetLastError());
        CloseHandle(job);
        return 2;
    }
    if (!AssignProcessToJobObject(job, child.hProcess) || ResumeThread(child.hThread) == (DWORD)-1) {
        fwprintf(stderr, L"Cannot supervise compiler process: %lu\n", GetLastError());
        TerminateProcess(child.hProcess, 2);
        CloseHandle(child.hThread); CloseHandle(child.hProcess); CloseHandle(job);
        return 2;
    }
    WaitForSingleObject(child.hProcess, INFINITE);
    DWORD result = 2;
    GetExitCodeProcess(child.hProcess, &result);
    CloseHandle(child.hThread); CloseHandle(child.hProcess); CloseHandle(job);
    return (int)result;
}
