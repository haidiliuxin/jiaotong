#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>
#include <stdio.h>

/* Private diagnostic runtime only. Native executables stay intact elsewhere. */
int wmain(void) {
    wchar_t module[32768], python[32768], script[32768], command[32768];
    const wchar_t *mode = NULL;
    if (!GetModuleFileNameW(NULL, module, 32768)) return 91;
    wchar_t *name = wcsrchr(module, L'\\');
    name = name ? name + 1 : module;
    if (!_wcsicmp(name, L"NodeAssignModel_car.exe")) mode = L"car";
    if (!_wcsicmp(name, L"NodeAssignModel_taxi.exe")) mode = L"taxi";
    if (!_wcsicmp(name, L"NodeAssignModel_motor.exe")) mode = L"motor";
    if (!_wcsicmp(name, L"NodeAssignModel_truck.exe")) mode = L"truck";
    DWORD p = GetEnvironmentVariableW(L"TRAFFIC_AGENT_PYTHON", python, 32768);
    DWORD s = GetEnvironmentVariableW(L"TRAFFIC_AGENT_BRIDGE_SCRIPT", script, 32768);
    if (!mode || !p || p >= 32768 || !s || s >= 32768) return 92;
    if (swprintf(command, 32768, L"\"%ls\" \"%ls\" %ls", python, script, mode) < 0) return 93;
    STARTUPINFOW si = {0}; PROCESS_INFORMATION pi = {0};
    si.cb = sizeof(si);
    if (!CreateProcessW(python, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) return 94;
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 95;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return (int)code;
}
