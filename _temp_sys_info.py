import psutil
import json

def get_system_info():
    try:
        cpu_usage = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory() # returns valid named tuple, but _asdict is safer for serialization
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        
        info = {
            "cpu_usage": cpu_usage,
            "memory_usage": {
                "total": memory.total,
                "available": memory.available,
                "used": memory.used,
                "percent": memory.percent
            },
            "disk_io_counters": {
                "read_count": disk.read_count,
                "write_count": disk.write_count,
                "read_bytes": disk.read_bytes,
                "write_bytes": disk.write_bytes
            } if disk else {},
            "network_traffic": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv
            } if net else {}
        }
        print(json.dumps(info, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    get_system_info()
