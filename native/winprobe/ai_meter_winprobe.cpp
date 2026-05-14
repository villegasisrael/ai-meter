#define _WIN32_WINNT 0x0600
#include <windows.h>
#include <iphlpapi.h>
#include <iostream>
#include <iomanip>
#include <string>

#pragma comment(lib, "iphlpapi.lib")

struct CpuTimes {
    unsigned long long idle = 0;
    unsigned long long kernel = 0;
    unsigned long long user = 0;
};

static unsigned long long ft_to_u64(const FILETIME& ft) {
    ULARGE_INTEGER uli;
    uli.LowPart = ft.dwLowDateTime;
    uli.HighPart = ft.dwHighDateTime;
    return uli.QuadPart;
}

static bool read_cpu(CpuTimes& out) {
    FILETIME idle, kernel, user;
    if (!GetSystemTimes(&idle, &kernel, &user)) return false;
    out.idle = ft_to_u64(idle);
    out.kernel = ft_to_u64(kernel);
    out.user = ft_to_u64(user);
    return true;
}

static double cpu_percent(const CpuTimes& prev, const CpuTimes& cur) {
    const auto idle = cur.idle - prev.idle;
    const auto kernel = cur.kernel - prev.kernel;
    const auto user = cur.user - prev.user;
    const auto total = kernel + user;
    if (total == 0 || idle > total) return 0.0;
    return (double)(total - idle) * 100.0 / (double)total;
}

static void read_net(double& sent_mb, double& recv_mb) {
    sent_mb = 0.0;
    recv_mb = 0.0;
    ULONG size = 0;
    if (GetIfTable(nullptr, &size, FALSE) != ERROR_INSUFFICIENT_BUFFER || size == 0) return;
    auto* table = reinterpret_cast<MIB_IFTABLE*>(new char[size]);
    if (GetIfTable(table, &size, FALSE) != NO_ERROR) {
        delete[] reinterpret_cast<char*>(table);
        return;
    }
    unsigned long long sent = 0;
    unsigned long long recv = 0;
    for (DWORD i = 0; i < table->dwNumEntries; ++i) {
        const MIB_IFROW& row = table->table[i];
        if (row.dwOperStatus != MIB_IF_OPER_STATUS_OPERATIONAL) continue;
        if (row.dwType == IF_TYPE_SOFTWARE_LOOPBACK) continue;
        sent += row.dwOutOctets;
        recv += row.dwInOctets;
    }
    delete[] reinterpret_cast<char*>(table);
    sent_mb = (double)sent / 1048576.0;
    recv_mb = (double)recv / 1048576.0;
}

static double read_disk_percent() {
    ULARGE_INTEGER free_avail, total, free_total;
    if (!GetDiskFreeSpaceExW(nullptr, &free_avail, &total, &free_total)) return 0.0;
    if (total.QuadPart == 0) return 0.0;
    const auto used = total.QuadPart - free_total.QuadPart;
    return (double)used * 100.0 / (double)total.QuadPart;
}

static void emit(double cpu) {
    MEMORYSTATUSEX mem;
    mem.dwLength = sizeof(mem);
    GlobalMemoryStatusEx(&mem);

    double sent_mb = 0.0;
    double recv_mb = 0.0;
    read_net(sent_mb, recv_mb);

    const double ram_total_mb = (double)mem.ullTotalPhys / 1048576.0;
    const double ram_avail_mb = (double)mem.ullAvailPhys / 1048576.0;
    const double ram_used_mb = ram_total_mb - ram_avail_mb;

    std::cout << std::fixed << std::setprecision(1)
        << "{\"source\":\"winprobe_cpp\","
        << "\"cpu_percent\":" << cpu << ","
        << "\"ram_percent\":" << (double)mem.dwMemoryLoad << ","
        << "\"ram_used_mb\":" << ram_used_mb << ","
        << "\"ram_total_mb\":" << ram_total_mb << ","
        << "\"disk_percent\":" << read_disk_percent() << ","
        << "\"net_sent_mb\":" << sent_mb << ","
        << "\"net_recv_mb\":" << recv_mb
        << "}" << std::endl;
}

int main(int argc, char** argv) {
    int interval_ms = 250;
    bool stream = false;
    if (argc >= 2) {
        std::string arg = argv[1];
        stream = arg == "--stream";
        if (argc >= 3) {
            try { interval_ms = std::stoi(argv[2]); } catch (...) { interval_ms = 250; }
        }
    }
    if (interval_ms < 100) interval_ms = 100;
    if (interval_ms > 5000) interval_ms = 5000;

    CpuTimes prev, cur;
    if (!read_cpu(prev)) return 2;
    Sleep((DWORD)interval_ms);
    if (!read_cpu(cur)) return 2;
    emit(cpu_percent(prev, cur));

    while (stream) {
        prev = cur;
        Sleep((DWORD)interval_ms);
        if (!read_cpu(cur)) return 2;
        emit(cpu_percent(prev, cur));
    }
    return 0;
}
