// AlienCore LHM Bridge
// Reads all hardware sensors via LibreHardwareMonitorLib and writes a JSON
// array to stdout. Called by AlienCore's sensor poll loop — no GUI, no web
// server, no tray icon. LibreHardwareMonitor itself is never launched.
//
// The caller (AlienCore) sets LHM_DIR env var to the folder containing
// LibreHardwareMonitorLib.dll and its dependencies. The assembly resolver
// below loads all LHM DLLs from that folder at runtime.
//
// Build:
//   dotnet build lhm_bridge.csproj -c Release /p:LHMDllPath="<path to dll>" -o dist/

using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using LibreHardwareMonitor.Hardware;

// ── Assembly resolver — must be registered before any LHM types are touched ──
// Loads all LHM dependencies from the directory specified by LHM_DIR env var.
// AppContext.BaseDirectory = directory of lhm_bridge.exe (dist/)
// LHM_DIR env var can override to load from a different location
var lhmDir = Environment.GetEnvironmentVariable("LHM_DIR")
             ?? AppContext.BaseDirectory;

AppDomain.CurrentDomain.AssemblyResolve += (_, args) =>
{
    var name = new AssemblyName(args.Name).Name + ".dll";
    var path = Path.Combine(lhmDir, name);
    return File.Exists(path) ? Assembly.LoadFrom(path) : null;
};

// ── Sensor collection ──────────────────────────────────────────────────────
var computer = new Computer
{
    IsCpuEnabled         = true,
    IsGpuEnabled         = true,
    IsMemoryEnabled      = true,   // DIMM temps (DDR5 via RAMSPDToolkit)
    IsMotherboardEnabled = true,   // VRM, embedded controller temps
    IsStorageEnabled     = true,   // NVMe composite temps
    IsNetworkEnabled     = false,
    IsBatteryEnabled     = false,
};

try
{
    computer.Open();

    var readings = new List<SensorReading>();
    foreach (var hw in computer.Hardware)
    {
        hw.Update();
        Collect(hw, readings);
    }

    computer.Close();

    var options = new JsonSerializerOptions
    {
        PropertyNamingPolicy        = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition      = JsonIgnoreCondition.WhenWritingNull,
    };
    Console.OutputEncoding = System.Text.Encoding.UTF8;
    Console.WriteLine(JsonSerializer.Serialize(readings, options));
    return 0;
}
catch (Exception ex)
{
    Console.Error.WriteLine($"lhm-bridge-error: {ex.Message}");
    return 1;
}

static void Collect(IHardware hw, List<SensorReading> list)
{
    foreach (var sub in hw.SubHardware)
    {
        sub.Update();
        Collect(sub, list);
    }
    foreach (var sensor in hw.Sensors)
    {
        if (sensor.Value.HasValue)
            list.Add(new SensorReading(
                sensor.Name,
                hw.Name,
                hw.HardwareType.ToString(),
                sensor.SensorType.ToString(),
                (float)sensor.Value.Value
            ));
    }
}

record SensorReading(
    string Name,
    string Hardware,
    string HardwareType,
    string Type,
    float  Value
);
