// AlienCore LHM Bridge — persistent daemon mode
//
// Starts once, stays alive, and responds to poll requests on stdin.
// Each request: one newline written to stdin.
// Each response: one compact JSON line written to stdout.
//
// This eliminates .NET CLR startup cost on every poll cycle.
// AlienCore's lhm_manager.py sends a newline, reads one JSON line.
//
// Build:
//   dotnet build lhm_bridge.csproj -c Release -o dist/

using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using LibreHardwareMonitor.Hardware;

// ── Assembly resolver — must be registered before any LHM types are touched ──
var lhmDir = Environment.GetEnvironmentVariable("LHM_DIR")
             ?? AppContext.BaseDirectory;

AppDomain.CurrentDomain.AssemblyResolve += (_, args) =>
{
    var name = new AssemblyName(args.Name).Name + ".dll";
    var path = Path.Combine(lhmDir, name);
    return File.Exists(path) ? Assembly.LoadFrom(path) : null;
};

Console.OutputEncoding = System.Text.Encoding.UTF8;
Console.InputEncoding  = System.Text.Encoding.UTF8;

var options = new JsonSerializerOptions
{
    PropertyNamingPolicy        = JsonNamingPolicy.CamelCase,
    DefaultIgnoreCondition      = JsonIgnoreCondition.WhenWritingNull,
};

var computer = new Computer
{
    IsCpuEnabled         = true,
    IsGpuEnabled         = true,
    IsMemoryEnabled      = true,
    IsMotherboardEnabled = true,
    IsStorageEnabled     = true,
    IsNetworkEnabled     = false,
    IsBatteryEnabled     = false,
};

try
{
    computer.Open();
}
catch (Exception ex)
{
    Console.Error.WriteLine($"lhm-bridge-error: open failed: {ex.Message}");
    return 1;
}

// ── Daemon loop ────────────────────────────────────────────────────────────────
// Wait for a poll request (any line) → collect sensors → write one JSON line.
// EOF on stdin means AlienCore exited — clean up and exit.
while (true)
{
    string? line;
    try
    {
        line = Console.ReadLine();
    }
    catch
    {
        break;
    }
    if (line == null) break;   // EOF — parent process closed the pipe

    try
    {
        var readings = new List<SensorReading>();
        foreach (var hw in computer.Hardware)
        {
            hw.Update();
            Collect(hw, readings);
        }
        // One compact JSON line per response — no embedded newlines
        Console.WriteLine(JsonSerializer.Serialize(readings, options));
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine($"lhm-bridge-error: poll failed: {ex.Message}");
        Console.WriteLine("[]");   // empty result so Python doesn't hang
    }
}

computer.Close();
return 0;

// ─────────────────────────────────────────────────────────────────────────────

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
