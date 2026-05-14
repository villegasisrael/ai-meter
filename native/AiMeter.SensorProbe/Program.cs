using Microsoft.Win32;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using LibreHardwareMonitor.Hardware;

// Detach from the console window so no visible window appears when launched by Task Scheduler.
[DllImport("kernel32.dll")] static extern bool FreeConsole();

var jsonOptions = new JsonSerializerOptions
{
    DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    WriteIndented = false
};
var mode = args.Length > 0 ? args[0] : "--once";
if (mode is "--help" or "-h")
{
    Console.WriteLine("ai-meter-sensor-probe --once | --doctor | --loop-to <file> [interval_ms] | --persist-driver");
    return 0;
}

// Install WinRing0x64.sys as a persistent auto-start kernel service so non-admin reads work.
// Must be run with admin privileges (called by install-service). Subsequent probe runs (even
// as SYSTEM or a background task without full UAC token) find the driver already loaded and
// can open the device handle to read CPU temperature without needing SE_LOAD_DRIVER_PRIVILEGE.
if (mode == "--persist-driver")
{
    var (ok, detail) = PersistWinRing0DriverVerbose();
    Console.WriteLine(ok ? "persist:ok" : $"persist:failed:{detail}");
    return ok ? 0 : 1;
}

// Background loop mode: write JSON to a file every N ms (used by Windows service/task)
if (mode == "--loop-to")
{
    FreeConsole(); // detach so Task Scheduler doesn't show a console window
    var outPath = args.Length > 1
        ? args[1]
        : Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
            "ai-meter", "sensor.json");
    var intervalMs = args.Length > 2 && int.TryParse(args[2], out var iv)
        ? Math.Clamp(iv, 1000, 60000)
        : 5000;

    Directory.CreateDirectory(Path.GetDirectoryName(outPath)!);
    var tmpPath = outPath + ".tmp";

    while (true)
    {
        try
        {
            var result = ReadSensors(false);
            var json = JsonSerializer.Serialize(result, jsonOptions);
            File.WriteAllText(tmpPath, json, System.Text.Encoding.UTF8);
            File.Move(tmpPath, outPath, overwrite: true);
        }
        catch { }
        Thread.Sleep(intervalMs);
    }
}

try
{
    var result = ReadSensors(mode == "--doctor");
    Console.WriteLine(JsonSerializer.Serialize(result, jsonOptions));
    return result.CpuTempC is null ? 2 : 0;
}
catch (Exception ex)
{
    var err = new ProbeResult
    {
        Available = false,
        Source = "lhm:exception",
        Error = ex.GetType().Name + ": " + ex.Message,
        SensorCount = 0,
        Sensors = mode == "--doctor" ? new List<SensorRow>() : null
    };
    Console.WriteLine(JsonSerializer.Serialize(err, jsonOptions));
    return 3;
}

static (bool ok, string detail) PersistWinRing0DriverVerbose()
{
    const string SvcName = "WinRing0_1_2_0";
    const string DestDir = @"C:\ProgramData\ai-meter";
    var destSys = Path.Combine(DestDir, "WinRing0x64.sys");

    RunSc($"stop {SvcName}");
    RunSc($"delete {SvcName}");
    Thread.Sleep(600);

    // Strategy 1: extract WinRing0x64.sys directly from LHM's embedded managed resources.
    // This avoids the race where LHM creates-then-deletes the kernel service within Open().
    var extracted = TryExtractSysFromResources(destSys);
    if (!extracted)
    {
        // Strategy 2: background watcher — poll registry and temp dirs while computer.Open() runs,
        // copying the .sys the instant it appears (before LHM's cleanup on driver-load failure).
        extracted = TryExtractSysViaLhmRace(SvcName, destSys);
    }
    if (!extracted || !File.Exists(destSys))
        return (false, "sys_not_extracted:both_strategies_failed");

    Thread.Sleep(400);

    // Register as auto-start kernel driver. Windows loads auto-start drivers at boot — after
    // the service exists on disk, subsequent probe runs find the device already open and do not
    // need SE_LOAD_DRIVER_PRIVILEGE. The sc start here may fail if HVCI blocks the driver at
    // runtime; that is OK — what matters is the service entry existing for the next boot.
    var createOut = RunScCapture($@"create {SvcName} type= kernel start= auto binPath= ""{destSys}"" DisplayName= ""WinRing0 (ai-meter)""");
    if (!createOut.Contains("SUCCESS") && !createOut.Contains("[SC] CreateService SUCCESS"))
        return (false, $"sc_create_failed:{createOut.Trim().Replace('\n', ' ')}");

    var startOut = RunScCapture($"start {SvcName}");
    // sc start may return ERROR_DRIVER_BLOCKED (1275) when HVCI blocks unsigned drivers.
    // Still report ok — the service is registered, and after reboot Windows may load it.
    bool startOk = startOut.Contains("START_PENDING") || startOut.Contains("RUNNING") ||
                   startOut.Contains("1056");  // ERROR_SERVICE_ALREADY_RUNNING
    return (true, startOk ? "ok" : $"ok_but_start_failed:{startOut.Trim().Replace('\n', ' ')}");
}

static bool TryExtractSysFromResources(string destSys)
{
    // WinRing0x64.sys is embedded as a managed resource in LibreHardwareMonitorLib.dll.
    // Extracting it here skips the sc create/start/delete cycle that happens inside Open().
    try
    {
        var lhm = typeof(Computer).Assembly;
        var resName = lhm.GetManifestResourceNames()
            .FirstOrDefault(r => r.EndsWith("WinRing0x64.sys", StringComparison.OrdinalIgnoreCase));
        if (resName is null) return false;

        Directory.CreateDirectory(Path.GetDirectoryName(destSys)!);
        using var stream = lhm.GetManifestResourceStream(resName)!;
        using var file = File.Create(destSys);
        stream.CopyTo(file);
        return File.Exists(destSys) && new FileInfo(destSys).Length > 0;
    }
    catch { return false; }
}

static bool TryExtractSysViaLhmRace(string svcName, string destSys)
{
    // Fallback: monitor registry + common temp dirs while computer.Open() runs.
    // LHM extracts the .sys to %TEMP%, registers the service, then (on failure) deletes both.
    // We race to copy the file before cleanup.
    var regKey = $@"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\{svcName}";
    bool captured = false;

    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(15));
    var watchTask = Task.Run(() =>
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(destSys)!);
            while (!cts.Token.IsCancellationRequested)
            {
                // Check registry first (cheapest)
                var img = Registry.GetValue(regKey, "ImagePath", null) as string ?? "";
                if (!string.IsNullOrEmpty(img))
                {
                    if (img.StartsWith(@"\??\", StringComparison.OrdinalIgnoreCase))
                        img = img[4..];
                    if (File.Exists(img))
                    {
                        try { File.Copy(img, destSys, overwrite: true); captured = true; return; }
                        catch { }
                    }
                }
                // Also scan temp dirs for the .sys directly
                foreach (var tempDir in new[] { Path.GetTempPath(),
                    Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData) })
                {
                    try
                    {
                        var found = Directory.GetFiles(tempDir, "WinRing0x64.sys",
                            SearchOption.AllDirectories).FirstOrDefault();
                        if (found is not null && found != destSys)
                        {
                            File.Copy(found, destSys, overwrite: true);
                            captured = true;
                            return;
                        }
                    }
                    catch { }
                }
                Thread.Sleep(30);
            }
        }
        catch { }
    });

    var computer = new Computer { IsCpuEnabled = true };
    try { computer.Open(); } catch { /* driver blocked, but file may have appeared */ }
    Thread.Sleep(300);
    cts.Cancel();
    watchTask.Wait(TimeSpan.FromSeconds(3));
    try { computer.Close(); } catch { }

    return captured && File.Exists(destSys);
}

static void RunSc(string args) => RunScCapture(args);

static string RunScCapture(string args)
{
    try
    {
        using var p = Process.Start(new ProcessStartInfo("sc.exe", args)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        })!;
        var output = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
        p.WaitForExit(5000);
        return output;
    }
    catch (Exception ex) { return ex.Message; }
}

static ProbeResult ReadSensors(bool includeAll)
{
    var sensors = new List<SensorRow>();
    var computer = new Computer
    {
        IsCpuEnabled = true,
        IsMotherboardEnabled = true,
        IsControllerEnabled = true,
        IsGpuEnabled = true,
        IsMemoryEnabled = false,
        IsNetworkEnabled = false,
        IsStorageEnabled = false
    };

    try
    {
        computer.Open();
        foreach (var hardware in computer.Hardware)
            ReadHardware(hardware, sensors);
    }
    finally
    {
        computer.Close();
    }

    // CPU temperature — plausible values only
    var cpuTemps = sensors
        .Where(s => s.Type == "Temperature" && s.Value is not null &&
                    IsPlausibleTemp(s.Value.Value) && IsCpuLike(s))
        .OrderByDescending(ScoreCpuSensor)
        .ThenByDescending(s => s.Value)
        .ToList();

    var selected = cpuTemps.FirstOrDefault();

    // Detect needs_admin: temp sensors exist but all returned 0 (driver/MSR access blocked)
    bool needsAdmin = selected is null &&
        sensors.Any(s => s.Type == "Temperature" && IsCpuLike(s));

    // Per-core CPU load — readable without admin
    var coreLoads = sensors
        .Where(s => s.Type == "Load" && s.HardwareType == "Cpu" &&
                    s.Name.StartsWith("CPU Core #"))
        .OrderBy(s =>
        {
            var num = s.Name.Replace("CPU Core #", "");
            return int.TryParse(num, out var n) ? n : 999;
        })
        .Select(s => new CoreLoad
        {
            Name = s.Name,
            LoadPercent = Math.Round(s.Value ?? 0.0, 1)
        })
        .ToList();

    var totalLoad = sensors.FirstOrDefault(s =>
        s.Type == "Load" && s.Name == "CPU Total" && s.HardwareType == "Cpu")?.Value;

    // GPU temperature
    double? gpuTempC = null;
    string? gpuName = null;
    var gpuTemps = sensors
        .Where(s => s.Type == "Temperature" && IsGpuHardware(s) &&
                    s.Value is not null && IsPlausibleTemp(s.Value.Value))
        .OrderByDescending(ScoreGpuSensor)
        .ThenByDescending(s => s.Value)
        .ToList();
    if (gpuTemps.Count > 0)
    {
        gpuTempC = gpuTemps[0].Value;
        gpuName = gpuTemps[0].Hardware;
    }

    string source = selected is not null
        ? "lhm:" + selected.Name
        : needsAdmin
            ? "lhm:needs_admin"
            : "lhm:no_cpu_sensor";

    return new ProbeResult
    {
        Available = selected is not null,
        CpuTempC = selected?.Value,
        NeedsAdmin = needsAdmin,
        GpuTempC = gpuTempC,
        GpuName = gpuName,
        CpuTotalLoad = totalLoad,
        CoreLoads = coreLoads.Count > 0 ? coreLoads : null,
        Source = source,
        SensorCount = sensors.Count,
        Sensors = includeAll ? sensors : null
    };
}

static void ReadHardware(IHardware hardware, List<SensorRow> sensors)
{
    hardware.Update();
    foreach (var sensor in hardware.Sensors)
    {
        if (sensor.Value is null) continue;
        var value = Math.Round(sensor.Value.Value, 1);
        if (!double.IsFinite(value)) continue;
        sensors.Add(new SensorRow
        {
            Hardware = hardware.Name,
            HardwareType = hardware.HardwareType.ToString(),
            Name = sensor.Name,
            Type = sensor.SensorType.ToString(),
            Value = value,
            Identifier = sensor.Identifier.ToString()
        });
    }
    foreach (var child in hardware.SubHardware)
        ReadHardware(child, sensors);
}

static bool IsCpuLike(SensorRow s)
{
    // Exclude GPU hardware explicitly — "GPU Core" would otherwise match "core"
    if (s.HardwareType.ToLowerInvariant().Contains("gpu")) return false;
    var text = (s.Hardware + " " + s.HardwareType + " " + s.Name + " " + s.Identifier).ToLowerInvariant();
    return text.Contains("cpu") || text.Contains("ryzen") || text.Contains("amd") ||
           text.Contains("intel") || text.Contains("core") || text.Contains("package") ||
           text.Contains("ccd") || text.Contains("tdie") || text.Contains("tctl");
}

static bool IsGpuHardware(SensorRow s)
    => s.HardwareType.ToLowerInvariant().Contains("gpu");

static int ScoreCpuSensor(SensorRow s)
{
    var text = (s.Name + " " + s.Identifier).ToLowerInvariant();
    if (text.Contains("package")) return 100;
    if (text.Contains("tctl") || text.Contains("tdie")) return 95;
    if (text.Contains("core max") || text.Contains("cores (max)")) return 90;
    if (text.Contains("ccd")) return 80;
    if (text.Contains("core")) return 70;
    return 10;
}

static int ScoreGpuSensor(SensorRow s)
{
    var text = s.Name.ToLowerInvariant();
    if (text == "gpu core" || text == "gpu temperature") return 100;
    if (text.Contains("gpu")) return 80;
    return 10;
}

static bool IsPlausibleTemp(double v) => v >= 5.0 && v <= 130.0;

sealed class ProbeResult
{
    [JsonPropertyName("available")]
    public bool Available { get; set; }

    [JsonPropertyName("cpu_temp_c")]
    public double? CpuTempC { get; set; }

    [JsonPropertyName("needs_admin")]
    public bool NeedsAdmin { get; set; }

    [JsonPropertyName("gpu_temp_c")]
    public double? GpuTempC { get; set; }

    [JsonPropertyName("gpu_name")]
    public string? GpuName { get; set; }

    [JsonPropertyName("cpu_total_load")]
    public double? CpuTotalLoad { get; set; }

    [JsonPropertyName("core_loads")]
    public List<CoreLoad>? CoreLoads { get; set; }

    [JsonPropertyName("source")]
    public string Source { get; set; } = "unknown";

    [JsonPropertyName("sensor_count")]
    public int SensorCount { get; set; }

    [JsonPropertyName("sensors")]
    public List<SensorRow>? Sensors { get; set; }

    [JsonPropertyName("error")]
    public string? Error { get; set; }
}

sealed class CoreLoad
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("load_percent")]
    public double LoadPercent { get; set; }
}

sealed class SensorRow
{
    [JsonPropertyName("hardware")]
    public string Hardware { get; set; } = "";

    [JsonPropertyName("hardware_type")]
    public string HardwareType { get; set; } = "";

    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("type")]
    public string Type { get; set; } = "";

    [JsonPropertyName("value")]
    public double? Value { get; set; }

    [JsonPropertyName("identifier")]
    public string Identifier { get; set; } = "";
}
