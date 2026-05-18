using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
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
    Console.WriteLine("ai-meter-sensor-probe --once | --doctor | --loop-to <file> [interval_ms]");
    Console.WriteLine("Disabled by default because LibreHardwareMonitor can load WinRing0. Set AI_METER_ALLOW_VULNERABLE_SENSOR_PROBE=1 only for isolated local testing.");
    return 0;
}

if (mode == "--persist-driver")
{
    Console.WriteLine("persist:failed:vulnerable_driver_disabled");
    return 2;
}

if (!UnsafeSensorProbeAllowed())
{
    var disabled = DisabledResult();
    if (mode == "--loop-to")
    {
        FreeConsole();
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
            var json = JsonSerializer.Serialize(disabled, jsonOptions);
            File.WriteAllText(tmpPath, json, System.Text.Encoding.UTF8);
            File.Move(tmpPath, outPath, overwrite: true);
            Thread.Sleep(intervalMs);
        }
    }

    Console.WriteLine(JsonSerializer.Serialize(disabled, jsonOptions));
    return 2;
}

// Legacy loop mode: write JSON to a file every N ms when explicitly enabled.
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

static bool UnsafeSensorProbeAllowed()
{
    var value = Environment.GetEnvironmentVariable("AI_METER_ALLOW_VULNERABLE_SENSOR_PROBE") ?? "";
    return value.Equals("1", StringComparison.OrdinalIgnoreCase) ||
           value.Equals("true", StringComparison.OrdinalIgnoreCase) ||
           value.Equals("yes", StringComparison.OrdinalIgnoreCase) ||
           value.Equals("on", StringComparison.OrdinalIgnoreCase);
}

static ProbeResult DisabledResult() => new()
{
    Available = false,
    Source = "sensor_probe:disabled",
    Error = "vulnerable_driver_disabled",
    SensorCount = 0
};

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
