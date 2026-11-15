mobile nzb command center

## System Command Center (Windows desktop)

A live dashboard for your PC: Windows, CPU, memory, GPU, drives, network and processes.

```powershell
pip install customtkinter psutil pillow
python system_command_center.py
```

Keep `system_monitor.py` next to it. Pages:

- **Dashboard**: CPU, memory, GPU and network gauges with 2-minute graphs, drive space, system status, top processes, alerts
- **CPU & Memory**: per-core load, real clock speed, memory modules, temperatures and fans
- **GPU**: load, VRAM, temperature, power, clocks, fan, PCIe link and engine load (3D, video decode/encode) for every card, plus which apps are using it
- **Drives**: space per volume, a space analyzer (largest folders and files), physical disk health/temperature/wear, live activity, Recycle Bin and temp-file cleanup
- **Network**: throughput, ping/packet loss, Wi-Fi signal, adapters, Flush DNS
- **Processes**: sortable and filterable list with CPU, memory, GPU and disk use; group by app; End task
- **Windows**: edition/build, Defender and firewall status, pending restart, Windows Update check, power plans, startup apps on/off, recent errors from the event log
- **Tools**: one-click Windows tools, maintenance (Restart Explorer, Defender quick scan…), lock/sleep/restart/shut down
- **Alerts / Settings**: thresholds for CPU, RAM, temperatures, VRAM, drive space and battery, with optional Windows notifications

Data sources: psutil; Windows GPU performance counters (any GPU vendor); `nvidia-smi` for NVIDIA temperature, power and clocks;
PowerShell/CIM for hardware, disk health and security status. Windows doesn't expose CPU temperatures or fan speeds by itself:
run [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) in the background and they appear automatically.
Run as administrator to see drive temperatures and wear.
