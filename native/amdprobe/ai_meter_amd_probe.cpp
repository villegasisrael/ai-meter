#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cmath>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

#include "ICPUEx.h"
#include "IDeviceManager.h"
#include "IPlatform.h"

namespace {

constexpr wchar_t kSdkRegistryPath[] = L"SOFTWARE\\AMD\\RyzenMasterMonitoringSDK";
constexpr wchar_t kDriverServiceName[] = L"AMDRyzenMasterDriverV32";

using GetPlatformFunc = IPlatform&(__stdcall*)();

struct Metric {
    const char* id;
    const char* kind;
    const char* label;
    double value;
    const char* unit;
};

void print_error(const char* error, DWORD win32_error = ERROR_SUCCESS) {
    std::cout << "{\"available\":false,\"source\":\"amd_ryzen_master_sdk\","
                 "\"error\":\""
              << error << "\"";
    if (win32_error != ERROR_SUCCESS) {
        std::cout << ",\"win32_error\":" << win32_error;
    }
    std::cout << "}\n";
}

bool is_elevated() {
    BOOL is_member = FALSE;
    SID_IDENTIFIER_AUTHORITY nt_authority = SECURITY_NT_AUTHORITY;
    PSID administrators_group = nullptr;
    if (!AllocateAndInitializeSid(
            &nt_authority,
            2,
            SECURITY_BUILTIN_DOMAIN_RID,
            DOMAIN_ALIAS_RID_ADMINS,
            0,
            0,
            0,
            0,
            0,
            0,
            &administrators_group)) {
        return false;
    }
    const BOOL checked = CheckTokenMembership(nullptr, administrators_group, &is_member);
    FreeSid(administrators_group);
    return checked && is_member;
}

enum class DriverState {
    missing,
    stopped,
    running,
    error,
};

DriverState driver_state() {
    SC_HANDLE manager = OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT);
    if (!manager) {
        return DriverState::error;
    }
    SC_HANDLE service = OpenServiceW(manager, kDriverServiceName, SERVICE_QUERY_STATUS);
    if (!service) {
        const DWORD error = GetLastError();
        CloseServiceHandle(manager);
        return error == ERROR_SERVICE_DOES_NOT_EXIST ? DriverState::missing : DriverState::error;
    }
    SERVICE_STATUS status{};
    const BOOL queried = QueryServiceStatus(service, &status);
    CloseServiceHandle(service);
    CloseServiceHandle(manager);
    if (!queried) {
        return DriverState::error;
    }
    return status.dwCurrentState == SERVICE_RUNNING ? DriverState::running : DriverState::stopped;
}

std::wstring sdk_root() {
    DWORD chars = GetEnvironmentVariableW(L"AMDRMMONITORSDKPATH", nullptr, 0);
    if (chars > 1) {
        std::wstring value(chars, L'\0');
        const DWORD written = GetEnvironmentVariableW(
            L"AMDRMMONITORSDKPATH", value.data(), static_cast<DWORD>(value.size()));
        if (written > 0 && written < value.size()) {
            value.resize(written);
            return value;
        }
    }

    DWORD type = 0;
    DWORD bytes = 0;
    if (RegGetValueW(
            HKEY_LOCAL_MACHINE,
            kSdkRegistryPath,
            L"InstallationPath",
            RRF_RT_REG_SZ,
            &type,
            nullptr,
            &bytes) != ERROR_SUCCESS ||
        bytes < sizeof(wchar_t)) {
        return {};
    }
    std::wstring value(bytes / sizeof(wchar_t), L'\0');
    if (RegGetValueW(
            HKEY_LOCAL_MACHINE,
            kSdkRegistryPath,
            L"InstallationPath",
            RRF_RT_REG_SZ,
            &type,
            value.data(),
            &bytes) != ERROR_SUCCESS) {
        return {};
    }
    while (!value.empty() && value.back() == L'\0') {
        value.pop_back();
    }
    return value;
}

std::wstring platform_dll_path() {
    std::wstring root = sdk_root();
    if (root.empty()) {
        return {};
    }
    if (root.back() != L'\\' && root.back() != L'/') {
        root.push_back(L'\\');
    }
    return root + L"bin\\Platform.dll";
}

bool plausible(double value, double minimum, double maximum) {
    return std::isfinite(value) && value >= minimum && value <= maximum;
}

void add_metric(
    std::vector<Metric>& metrics,
    const char* id,
    const char* kind,
    const char* label,
    double value,
    const char* unit,
    double minimum,
    double maximum) {
    if (plausible(value, minimum, maximum)) {
        metrics.push_back({id, kind, label, value, unit});
    }
}

void print_metrics(const CPUParameters& data) {
    std::vector<Metric> metrics;
    add_metric(
        metrics,
        "cpu:0.temperature",
        "temperature",
        "CPU Temperature",
        data.dTemperature,
        "C",
        5.0,
        130.0);
    add_metric(
        metrics,
        "cpu:0.power",
        "power",
        "CPU Core Power",
        data.fVDDCR_VDD_Power,
        "W",
        0.0,
        1000.0);
    add_metric(
        metrics,
        "cpu:0.ppt_power",
        "power",
        "CPU PPT",
        data.fPPTValue,
        "W",
        0.0,
        1000.0);
    add_metric(
        metrics,
        "cpu:0.voltage",
        "voltage",
        "CPU Voltage",
        data.dAvgCoreVoltage,
        "V",
        0.0,
        3.0);
    add_metric(
        metrics,
        "cpu:0.frequency",
        "frequency",
        "CPU Peak Frequency",
        data.dPeakSpeed,
        "MHz",
        0.0,
        10000.0);
    if (plausible(data.fPPTLimit, 0.1, 1000.0)) {
        add_metric(
            metrics,
            "cpu:0.ppt",
            "utilization",
            "CPU PPT",
            100.0 * data.fPPTValue / data.fPPTLimit,
            "%",
            0.0,
            250.0);
    }
    if (plausible(data.fTDCLimit_VDD, 0.1, 1000.0)) {
        add_metric(
            metrics,
            "cpu:0.tdc",
            "utilization",
            "CPU TDC",
            100.0 * data.fTDCValue_VDD / data.fTDCLimit_VDD,
            "%",
            0.0,
            250.0);
    }
    if (plausible(data.fEDCLimit_VDD, 0.1, 1000.0)) {
        add_metric(
            metrics,
            "cpu:0.edc",
            "utilization",
            "CPU EDC",
            100.0 * data.fEDCValue_VDD / data.fEDCLimit_VDD,
            "%",
            0.0,
            250.0);
    }

    std::cout << "{\"available\":true,\"source\":\"amd_ryzen_master_sdk\","
                 "\"metrics\":[";
    for (std::size_t index = 0; index < metrics.size(); ++index) {
        if (index > 0) {
            std::cout << ',';
        }
        const Metric& metric = metrics[index];
        std::cout << "{\"id\":\"" << metric.id
                  << "\",\"device_id\":\"cpu:0\",\"device_type\":\"cpu\","
                     "\"kind\":\""
                  << metric.kind << "\",\"label\":\"" << metric.label
                  << "\",\"value\":" << std::fixed << std::setprecision(3) << metric.value
                  << ",\"unit\":\"" << metric.unit << "\",\"stale_after_s\":3}";
    }
    std::cout << "]}\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc > 2 || (argc == 2 && std::string(argv[1]) != "--once")) {
        print_error("usage");
        return 2;
    }
    if (!is_elevated()) {
        print_error("admin_required");
        return 0;
    }

    switch (driver_state()) {
        case DriverState::missing:
            print_error("driver_not_installed");
            return 0;
        case DriverState::stopped:
            print_error("driver_not_running");
            return 0;
        case DriverState::error:
            print_error("driver_status_unavailable");
            return 0;
        case DriverState::running:
            break;
    }

    const std::wstring dll_path = platform_dll_path();
    if (dll_path.empty()) {
        print_error("sdk_not_installed");
        return 0;
    }

    HMODULE platform_module = LoadLibraryExW(
        dll_path.c_str(),
        nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!platform_module) {
        const DWORD load_error = GetLastError();
        print_error("platform_dll_load_failed", load_error);
        return 0;
    }
    auto get_platform =
        reinterpret_cast<GetPlatformFunc>(GetProcAddress(platform_module, "GetPlatform"));
    if (!get_platform) {
        print_error("platform_api_missing");
        FreeLibrary(platform_module);
        return 0;
    }

    IPlatform& platform = get_platform();
    if (!platform.Init(nullptr, true)) {
        print_error("platform_init_failed");
        FreeLibrary(platform_module);
        return 0;
    }

    IDeviceManager& manager = platform.GetIDeviceManager();
    auto* cpu = static_cast<ICPUEx*>(manager.GetDevice(dtCPU, 0));
    if (!cpu) {
        print_error("cpu_device_unavailable");
        platform.UnInit();
        FreeLibrary(platform_module);
        return 0;
    }

    CPUParameters data{};
    const int result = cpu->GetCPUParameters(data);
    if (result != 0) {
        print_error(
            result == 4 ? "cpu_parameters_unsupported" : "cpu_parameters_failed");
        platform.UnInit();
        FreeLibrary(platform_module);
        return 0;
    }
    print_metrics(data);

    platform.UnInit();
    FreeLibrary(platform_module);
    return 0;
}
