import os
import time
import json
import subprocess
import xml.etree.ElementTree as ET
import sys
import shutil
from datetime import datetime

# =========================
# GLOBAL
# =========================
CONFIG_FILE = "config.json"
PACKAGES_FILE = "packages.json"
monitor_active = False

last_launch_time = {}  # package_name -> timestamp

# =========================
# BASIC UTILS
# =========================
def run_root_cmd(cmd):
    try:
        r = subprocess.run(
            ["su", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=10
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except subprocess.TimeoutExpired:
        log("Command timeout")
        return ""
    except FileNotFoundError:
        log("ERROR: su not found")
        return ""
    except Exception as e:
        log(f"Command error: {e}")
        return ""

def clear_screen():
    os.system("clear")

def log(msg):
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] {msg}")
    sys.stdout.flush()

# =========================
# CPU & RAM MONITORING - CLOUDPHONE/TERMUX VERSION
# =========================
def get_cpu_count():
    """Mendapatkan jumlah CPU cores"""
    try:
        # Method 1: os.cpu_count()
        cpu_count = os.cpu_count()
        if cpu_count:
            return cpu_count
        
        # Method 2: Baca dari /proc/cpuinfo
        with open('/proc/cpuinfo', 'r') as f:
            cpu_count = len([line for line in f if line.startswith('processor')])
            if cpu_count > 0:
                return cpu_count
        
        return 4
    except:
        return 4

def get_ram_info():
    """Mendapatkan informasi RAM - FIXED untuk Cloudphone"""
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
        
        mem_total_kb = 0
        mem_free_kb = 0
        mem_available_kb = 0
        buffers_kb = 0
        cached_kb = 0
        
        for line in lines:
            if line.startswith('MemTotal:'):
                mem_total_kb = int(line.split()[1])
            elif line.startswith('MemFree:'):
                mem_free_kb = int(line.split()[1])
            elif line.startswith('MemAvailable:'):
                mem_available_kb = int(line.split()[1])
            elif line.startswith('Buffers:'):
                buffers_kb = int(line.split()[1])
            elif line.startswith('Cached:'):
                cached_kb = int(line.split()[1])
        
        # Hitung used memory yang lebih akurat
        # Used = Total - Free - Buffers - Cached
        mem_used_kb = mem_total_kb - mem_free_kb - buffers_kb - cached_kb
        
        # Konversi KB ke MB
        mem_total_mb = mem_total_kb / 1024
        mem_used_mb = mem_used_kb / 1024
        mem_available_mb = mem_available_kb / 1024 if mem_available_kb > 0 else mem_free_kb / 1024
        
        # Pastikan used tidak negatif
        if mem_used_mb < 0:
            mem_used_mb = mem_total_mb - mem_available_mb
        
        percent = (mem_used_mb / mem_total_mb) * 100 if mem_total_mb > 0 else 0
        
        return {
            'total_mb': round(mem_total_mb, 2),
            'used_mb': round(mem_used_mb, 2),
            'available_mb': round(mem_available_mb, 2),
            'percent': round(percent, 1)
        }
    except Exception as e:
        log(f"RAM info error: {e}")
        return None


# =========================
# CPU & RAM MONITORING - PER PACKAGE VERSION
# =========================
def get_package_cpu_usage(package_name):
    """
    Mengambil CPU usage dari package Roblox yang spesifik
    Returns: float (CPU percentage) atau 0 jika tidak ditemukan
    """
    try:
        # Cari PID dari package
        result = subprocess.run(
            ['pgrep', '-f', package_name],
            capture_output=True,
            text=True,
            timeout=2
        )
        
        if result.returncode != 0 or not result.stdout.strip():
            return 0.0
        
        pids = result.stdout.strip().split('\n')
        total_cpu = 0.0
        
        for pid in pids:
            if not pid:
                continue
            
            # Ambil CPU % dari setiap PID
            ps_result = subprocess.run(
                ['ps', '-p', pid, '-o', 'pcpu='],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if ps_result.returncode == 0:
                try:
                    cpu = float(ps_result.stdout.strip())
                    total_cpu += cpu
                except:
                    pass
        
        return round(total_cpu, 1)
    
    except Exception as e:
        log(f"Error getting CPU for {package_name}: {e}")
        return 0.0

def get_package_ram_usage(package_name):
    """
    Mengambil RAM usage dari package Roblox yang spesifik
    Returns: dict dengan 'used_mb' dan 'percent' atau None jika tidak ditemukan
    """
    try:
        # Cari PID dari package
        result = subprocess.run(
            ['pgrep', '-f', package_name],
            capture_output=True,
            text=True,
            timeout=2
        )
        
        if result.returncode != 0 or not result.stdout.strip():
            return None
        
        pids = result.stdout.strip().split('\n')
        total_rss_kb = 0
        
        for pid in pids:
            if not pid:
                continue
            
            # Baca dari /proc/[pid]/status
            try:
                with open(f'/proc/{pid}/status', 'r') as f:
                    for line in f:
                        if line.startswith('VmRSS:'):
                            # VmRSS adalah Resident Set Size (RAM yang benar-benar digunakan)
                            rss_kb = int(line.split()[1])
                            total_rss_kb += rss_kb
                            break
            except:
                pass
        
        if total_rss_kb == 0:
            return None
        
        # Konversi KB ke MB
        used_mb = total_rss_kb / 1024
        
        # Dapatkan total RAM sistem untuk hitung persentase
        ram_info = get_ram_info()
        if ram_info:
            percent = (used_mb / ram_info['total_mb']) * 100
        else:
            percent = 0.0
        
        return {
            'used_mb': round(used_mb, 2),
            'percent': round(percent, 1)
        }
    
    except Exception as e:
        log(f"Error getting RAM for {package_name}: {e}")
        return None

def get_all_packages_stats():
    """
    Mengambil CPU dan RAM stats dari semua package yang terdaftar
    Returns: dict {package_name: {'cpu': %, 'ram_mb': MB, 'ram_percent': %}}
    """
    pkgs = load_packages()
    stats = {}
    
    for package_name, info in pkgs.items():
        cpu = get_package_cpu_usage(package_name)
        ram = get_package_ram_usage(package_name)
        
        stats[package_name] = {
            'username': info.get('username', 'Unknown'),
            'cpu': cpu,
            'ram_mb': ram['used_mb'] if ram else 0.0,
            'ram_percent': ram['percent'] if ram else 0.0,
            'running': cpu > 0 or (ram and ram['used_mb'] > 0)
        }
    
    return stats

# =========================
# WEBHOOK DISCORD
# =========================
def send_discord_webhook(webhook_url, title, message, color=None):
    """Kirim pesan ke Discord webhook - IMPROVED VERSION"""
    if not webhook_url or webhook_url == "":
        return False
    
    try:
        color = color or 16711680  # Red default
        
        # Build JSON payload dengan escaping yang benar
        payload = {
            "embeds": [{
                "title": str(title),
                "description": str(message),
                "color": int(color),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }]
        }
        
        # Convert ke JSON string
        payload_str = json.dumps(payload)
        
        # Escape single quotes untuk shell command
        payload_escaped = payload_str.replace("'", "'\"'\"'")
        
        # Build curl command
        cmd = f"curl -s -X POST -H 'Content-Type: application/json' -d '{payload_escaped}' '{webhook_url}'"
        
        # Execute command
        result = os.system(cmd)
        
        return result == 0
    except Exception as e:
        log(f"Webhook error: {e}")
        return False

def build_webhook_status_message(pkgs, cfg):
    """Membuat pesan status untuk webhook - IMPROVED dengan info lengkap"""
    
    # Ambil stats dari semua package
    package_stats = get_all_packages_stats()

    # Hitung total dari semua package
    total_cpu = sum(s['cpu'] for s in package_stats.values())
    total_ram_mb = sum(s['ram_mb'] for s in package_stats.values())

    # Build message
    lines = []
    lines.append("**📊 System Status**")
    lines.append(f"Total Packages: {len(package_stats)}")
    lines.append(f"Running: {sum(1 for s in package_stats.values() if s['running'])}")
    lines.append("")
    
    lines.append("**🖥️ Resource Usage (All Packages)**")
    lines.append(f"CPU: {round(total_cpu, 1)}%")
    lines.append(f"RAM: {round(total_ram_mb, 2)} MB")
    lines.append("")
    
    lines.append("**📦 Per Package Stats:**")
    for pkg, stats in package_stats.items():
        if stats['running']:
            lines.append(f"• {stats['username']}: CPU {stats['cpu']}%, RAM {stats['ram_mb']:.1f} MB")
    
    lines.append("")
    lines.append("**👥 Account Status**")
    
    for pkg, info in pkgs.items():
        username = info["username"]
        status = check_workspace_status(pkg, info, cfg)
        
        if status == "online":
            status_emoji = "✅"
        elif status == "waiting":
            status_emoji = "⏳"
        else:
            status_emoji = "❌"
        
        lines.append(f"{status_emoji} {username}: {status.upper()}")
    
    return "\n".join(lines)

# =========================
# CONFIG
# =========================
def load_config():
    default = {
        "game_id": "",
        "check_interval": 10,
        "workspace_timeout": 180,
        "workspace_check_interval": 5,
        "json_suffix": "_checkyum.json",
        "startup_delay": 8,
        "restart_delay": 3,
        "autoexec_enabled": True,
        "webhook_url": "",
        "webhook_enabled": True,
        "webhook_interval": 10,
        "restart_interval": 0
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                loaded = json.load(f)
                default.update(loaded)
        except Exception as e:
            log(f"Config load error: {e}")
    return default

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        log("Config saved")
    except Exception as e:
        log(f"Config save error: {e}")

# =========================
# PACKAGE DETECTION
# =========================
def get_roblox_packages():
    out = run_root_cmd("pm list packages | grep com.roblox")
    pkgs = []
    for line in out.splitlines():
        if line.startswith("package:"):
            pkgs.append(line.split(":")[1])
    return pkgs

def get_username_from_prefs(package):
    prefs = f"/data/data/{package}/shared_prefs/prefs.xml"
    xml = run_root_cmd(f"cat {prefs}")
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
        for c in root:
            if c.tag == "string" and c.attrib.get("name") == "username":
                return c.text.strip() if c.text else None
    except Exception as e:
        log(f"Prefs parse error {package}: {e}")
    return None

def auto_detect_and_save_packages():
    clear_screen()
    print("AUTO DETECT ROBLOX PACKAGES\n")
    pkgs = get_roblox_packages()
    saved = {}

    for pkg in pkgs:
        print(f"Checking {pkg}...", end=" ")
        user = get_username_from_prefs(pkg)
        if user:
            # Deteksi tiap paket dengan nama package yang benar
            package_info = {
                "username": user,
                "package_name": pkg,  # Simpan nama package lengkap
                "workspace_dir": f"/storage/emulated/0/Android/data/{pkg}/files/gloop/external/Workspace",
                "autoexec_dir": f"/storage/emulated/0/Android/data/{pkg}/files/gloop/external/Autoexecute",
                # PERBAIKAN: File license langsung di folder Cache
                "cache_dir": f"/storage/emulated/0/Android/data/{pkg}/files/gloop/external/Internals/Cache",
                "license_path": f"/storage/emulated/0/Android/data/{pkg}/files/gloop/external/Internals/Cache"
            }
            saved[pkg] = package_info
            print(f"{user} ✓")
        else:
            print("SKIP")

    if saved:
        with open(PACKAGES_FILE, "w") as f:
            json.dump(saved, f, indent=2)
        print(f"\nSaved {len(saved)} packages")
    else:
        print("\nNo valid packages")

    input("\nEnter...")

def load_packages():
    if os.path.exists(PACKAGES_FILE):
        try:
            with open(PACKAGES_FILE) as f:
                return json.load(f)
        except Exception as e:
            log(f"Load packages error: {e}")
    return {}

# =========================
# APP CONTROL
# =========================
def is_app_running(package):
    return bool(run_root_cmd(f"pidof {package}"))

def stop_app(package):
    run_root_cmd(f"am force-stop {package}")
    run_root_cmd(f"pkill -9 -f {package}")
    run_root_cmd(f"killall {package}")
    log(f"Stopped {package}")

def start_app(package, game_id):
    cmd = (
        f"am start -a android.intent.action.VIEW "
        f"-d 'roblox://placeID={game_id}' "
        f"-n {package}/com.roblox.client.ActivityProtocolLaunch"
    )
    run_root_cmd(cmd)
    # RESET TIMER SAAT APP DILAUNCH
    last_launch_time[package] = time.time()
    log(f"Started {package} (launch timer reset)")

# =========================
# CACHE FOLDER MANAGEMENT
# =========================
def find_cache_dir(package_name):
    """Cari folder Cache untuk package tertentu"""
    possible_paths = [
        f"/storage/emulated/0/Android/data/{package_name}/files/gloop/external/Internals/Cache",
        f"/sdcard/Android/data/{package_name}/files/gloop/external/Internals/Cache",
    ]
    
    for cache_dir in possible_paths:
        if not os.path.isdir(cache_dir):
            continue
        
        try:
            files = os.listdir(cache_dir)
            if files:
                return cache_dir
        except:
            pass
    
    return None

def copy_all_cache_files(source_cache_dir, dest_cache_dir, use_root=False):
    """
    Copy semua file dari folder Cache source ke destination
    
    Args:
        source_cache_dir: Path folder Cache sumber
        dest_cache_dir: Path folder Cache tujuan
        use_root: Gunakan root command jika True
    
    Returns:
        tuple: (success_count, failed_count, error_msg)
    """
    success = 0
    failed = 0
    error_msg = ""
    
    try:
        # Pastikan source folder ada
        if not os.path.isdir(source_cache_dir):
            return 0, 0, f"Source folder tidak ditemukan: {source_cache_dir}"
        
        # Buat destination folder jika belum ada
        if use_root:
            run_root_cmd(f"mkdir -p '{dest_cache_dir}'")
        else:
            os.makedirs(dest_cache_dir, exist_ok=True)
        
        # List semua file di source
        try:
            source_files = os.listdir(source_cache_dir)
        except PermissionError:
            # Jika tidak bisa list dengan Python, coba dengan root
            if use_root:
                file_list = run_root_cmd(f"ls '{source_cache_dir}'")
                source_files = file_list.split('\n') if file_list else []
            else:
                return 0, 0, "Permission denied - coba gunakan root mode"
        
        if not source_files:
            return 0, 0, "Source folder kosong"
        
        # Copy setiap file
        for filename in source_files:
            if not filename.strip():
                continue
                
            source_file = os.path.join(source_cache_dir, filename)
            dest_file = os.path.join(dest_cache_dir, filename)
            
            try:
                if use_root:
                    result = run_root_cmd(f"cp '{source_file}' '{dest_file}'")
                    if result == "":  # Empty string means success
                        success += 1
                    else:
                        failed += 1
                        error_msg += f"Failed to copy {filename}; "
                else:
                    shutil.copy2(source_file, dest_file)
                    success += 1
            except Exception as e:
                failed += 1
                error_msg += f"Error copying {filename}: {str(e)}; "
        
        return success, failed, error_msg
    
    except Exception as e:
        return 0, 0, f"General error: {str(e)}"

def copy_license_to_all_packages():
    """Menu untuk copy license ke semua package"""
    clear_screen()
    print("=" * 70)
    print("🗂️ COPY CACHE FILES TO ALL PACKAGES")
    print("=" * 70)
    
    pkgs = load_packages()
    if not pkgs:
        log("No packages found")
        input("\nPress ENTER...")
        return
    
    # Cari source package yang punya cache
    source_pkg = None
    source_cache_dir = None
    
    print("\n📋 Available packages with cache:")
    available = []
    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        cache_dir = find_cache_dir(pkg)
        if cache_dir:
            available.append((pkg, info, cache_dir))
            print(f"  {i}. {info['username']} (Package: {pkg})")
    
    if not available:
        log("❌ No packages with cache files found!")
        input("\nPress ENTER...")
        return
    
    # Pilih source
    choice = input(f"\n📌 Select source package (1-{len(available)}) or Enter for first: ").strip()
    
    if choice == "":
        source_pkg, source_info, source_cache_dir = available[0]
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(available):
                source_pkg, source_info, source_cache_dir = available[idx]
            else:
                log("Invalid choice")
                input("\nPress ENTER...")
                return
        except ValueError:
            log("Invalid input")
            input("\nPress ENTER...")
            return
    
    log(f"\n✅ Source: {source_info['username']} ({source_pkg})")
    log(f"📂 Source cache: {source_cache_dir}")
    
    # Tampilkan file yang akan di-copy
    try:
        files = os.listdir(source_cache_dir)
        log(f"📄 Files to copy: {len(files)}")
        for f in files[:5]:  # Show first 5
            log(f"  - {f}")
        if len(files) > 5:
            log(f"  ... and {len(files) - 5} more files")
    except Exception as e:
        log(f"Error listing files: {e}")
    
    confirm = input("\n⚠️ Copy to ALL other packages? (y/n): ").strip().lower()
    if confirm != 'y':
        log("Cancelled")
        input("\nPress ENTER...")
        return
    
    # Tanya mode
    mode = input("Use root mode? (y/n, default=n): ").strip().lower()
    use_root = (mode == 'y')
    
    print("\n" + "=" * 70)
    log("Starting copy process...")
    print("=" * 70)
    
    total_success = 0
    total_failed = 0
    
    for pkg, info in pkgs.items():
        if pkg == source_pkg:
            continue
        
        username = info['username']
        dest_cache_dir = find_cache_dir(pkg)
        
        if not dest_cache_dir:
            # Coba buat folder
            dest_cache_dir = f"/storage/emulated/0/Android/data/{pkg}/files/gloop/external/Internals/Cache"
            if use_root:
                run_root_cmd(f"mkdir -p '{dest_cache_dir}'")
            else:
                try:
                    os.makedirs(dest_cache_dir, exist_ok=True)
                except:
                    pass
        
        log(f"\n📦 Copying to: {username}")
        success, failed, error = copy_all_cache_files(source_cache_dir, dest_cache_dir, use_root)
        
        if success > 0:
            log(f"  ✅ Success: {success} files")
            total_success += success
        if failed > 0:
            log(f"  ❌ Failed: {failed} files")
            total_failed += failed
        if error:
            log(f"  ⚠️ Errors: {error[:100]}")
    
    print("\n" + "=" * 70)
    log(f"📊 RESULTS:")
    log(f"  ✅ Total success: {total_success} files")
    log(f"  ❌ Total failed: {total_failed} files")
    print("=" * 70)
    
    input("\nPress ENTER...")

def view_license_status():
    """Lihat status cache untuk semua package"""
    clear_screen()
    print("=" * 70)
    print("📊 CACHE FOLDER STATUS")
    print("=" * 70)
    
    pkgs = load_packages()
    if not pkgs:
        log("No packages found")
        input("\nPress ENTER...")
        return
    
    print(f"\n{'No.':<4} {'Username':<20} {'Cache Status':<15} {'File Count':<12}")
    print("-" * 70)
    
    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        username = info['username']
        cache_dir = find_cache_dir(pkg)
        
        if cache_dir:
            try:
                files = os.listdir(cache_dir)
                file_count = len(files)
                status = "✅ Found"
                count_str = str(file_count)
            except:
                status = "⚠️ Error"
                count_str = "N/A"
        else:
            status = "❌ Not Found"
            count_str = "0"
        
        print(f"{i:<4} {username:<20} {status:<15} {count_str:<12}")
    
    print("=" * 70)
    input("\nPress ENTER...")

# =========================
# SCRIPT MANAGEMENT
# =========================
def list_executor_scripts(package_info):
    """Mendapatkan daftar script untuk paket tertentu"""
    autoexec_dir = package_info.get("autoexec_dir", "")
    
    if not autoexec_dir or not os.path.exists(autoexec_dir):
        return []
    
    try:
        return [
            f for f in os.listdir(autoexec_dir)
            if f.endswith((".lua", ".txt"))
        ]
    except:
        return []

def add_script_to_all_packages():
    """Menambahkan script ke semua paket"""
    clear_screen()
    print("=" * 70)
    print("📝 ADD SCRIPT TO ALL PACKAGES")
    print("=" * 70)
    
    pkgs = load_packages()
    if not pkgs:
        log("No packages found")
        input("\nPress ENTER...")
        return
    
    print(f"\nFound {len(pkgs)} packages:")
    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        print(f"  {i}. {info['username']}")
    
    print("\n" + "-" * 70)
    print("Paste script content (press ENTER twice to finish):")
    
    lines = []
    empty_count = 0
    
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        
        if line == "":
            empty_count += 1
            if empty_count >= 2:
                break
        else:
            empty_count = 0
            lines.append(line)
    
    script_content = "\n".join(lines).strip()
    
    if not script_content:
        log("Empty script, cancelled")
        input("\nPress ENTER...")
        return
    
    script_name = input("\n📌 Script name (without .lua): ").strip()
    if not script_name:
        log("Invalid script name")
        input("\nPress ENTER...")
        return
    
    if not script_name.endswith(".lua"):
        script_name += ".lua"
    
    print("\n" + "=" * 70)
    log("Adding script to all packages...")
    print("=" * 70)
    
    success_count = 0
    failed_count = 0
    
    for pkg, info in pkgs.items():
        autoexec_dir = info.get("autoexec_dir", "")
        
        if not autoexec_dir:
            log(f"❌ {info['username']}: No autoexec dir")
            failed_count += 1
            continue
        
        # Buat folder jika belum ada
        if not os.path.exists(autoexec_dir):
            try:
                os.makedirs(autoexec_dir, exist_ok=True)
            except Exception as e:
                log(f"❌ {info['username']}: Can't create dir - {e}")
                failed_count += 1
                continue
        
        # Tulis script
        script_path = os.path.join(autoexec_dir, script_name)
        try:
            with open(script_path, 'w') as f:
                f.write(script_content)
            log(f"✅ {info['username']}")
            success_count += 1
        except Exception as e:
            log(f"❌ {info['username']}: {e}")
            failed_count += 1
    
    print("\n" + "=" * 70)
    log(f"📊 RESULTS: {success_count} success, {failed_count} failed")
    print("=" * 70)
    input("\nPress ENTER...")

def delete_script_from_all_packages():
    """Hapus script dari semua package"""
    clear_screen()
    print("=" * 70)
    print("🗑️ DELETE SCRIPT FROM ALL PACKAGES")
    print("=" * 70)
    
    pkgs = load_packages()
    if not pkgs:
        log("No packages found")
        input("\nPress ENTER...")
        return
    
    # Tampilkan script yang ada
    print("\n📋 Available scripts:")
    all_scripts = set()
    
    for pkg, info in pkgs.items():
        scripts = list_executor_scripts(info)
        all_scripts.update(scripts)
    
    if not all_scripts:
        log("No scripts found in any package")
        input("\nPress ENTER...")
        return
    
    for i, script in enumerate(sorted(all_scripts), 1):
        print(f"  {i}. {script}")
    
    # Pilih script
    script_name = input("\n📌 Script name to delete: ").strip()
    if not script_name:
        log("Cancelled")
        input("\nPress ENTER...")
        return
    
    confirm = input(f"\n⚠️ Delete '{script_name}' from ALL packages? (y/n): ").strip().lower()
    if confirm != 'y':
        log("Cancelled")
        input("\nPress ENTER...")
        return
    
    # Hapus dari semua package
    success_count = 0
    not_found_count = 0
    
    for pkg, info in pkgs.items():
        autoexec_dir = info.get("autoexec_dir", "")
        if not autoexec_dir:
            continue
        
        script_path = os.path.join(autoexec_dir, script_name)
        
        if os.path.exists(script_path):
            try:
                os.remove(script_path)
                log(f"✅ {info['username']}")
                success_count += 1
            except Exception as e:
                log(f"❌ {info['username']}: {e}")
        else:
            not_found_count += 1
    
    print("\n" + "=" * 70)
    log(f"📊 RESULTS: {success_count} deleted, {not_found_count} not found")
    print("=" * 70)
    input("\nPress ENTER...")

def view_scripts_all_packages():
    """Lihat semua script di semua package"""
    clear_screen()
    print("=" * 70)
    print("👁️ VIEW SCRIPTS IN ALL PACKAGES")
    print("=" * 70)
    
    pkgs = load_packages()
    if not pkgs:
        log("No packages found")
        input("\nPress ENTER...")
        return
    
    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        username = info['username']
        scripts = list_executor_scripts(info)
        
        print(f"\n{i}. {username}")
        if scripts:
            for script in scripts:
                print(f"   📜 {script}")
        else:
            print("   (No scripts)")
    
    print("\n" + "=" * 70)
    input("Press ENTER...")

# =========================
# WORKSPACE MONITORING - PERBAIKAN DI SINI
# =========================
def get_workspace_json_path(package_info, username, cfg):
    """Mendapatkan path workspace JSON untuk paket tertentu - DIPERBAIKI"""
    workspace_dir = package_info.get("workspace_dir", 
                     f"/storage/emulated/0/Android/data/com.roblox.client/files/gloop/external/Workspace")
    
    # Buat direktori jika belum ada
    os.makedirs(workspace_dir, exist_ok=True)
    
    return f"{workspace_dir}/{username}{cfg['json_suffix']}"

def get_json_timestamp(package_info, username, cfg):
    """Baca timestamp dari JSON file workspace - DIPERBAIKI DENGAN MULTI-FIELD SUPPORT"""
    json_path = get_workspace_json_path(package_info, username, cfg)
    
    # Cek apakah file ada
    if not os.path.exists(json_path):
        return None
    
    try:
        # Baca timestamp dari file
        with open(json_path, 'r') as f:
            data = json.load(f)
        
        # Cari field timestamp - coba berbagai kemungkinan nama field
        timestamp_field = None
        for field in ["timestamp", "time", "last_update", "updated_at"]:
            if field in data:
                timestamp_field = data[field]
                break
        
        if timestamp_field:
            return timestamp_field
        else:
            # Gunakan waktu modifikasi file sebagai fallback
            return os.path.getmtime(json_path)
    except Exception as e:
        log(f"Error reading JSON for {username}: {e}")
        return None

def parse_timestamp(timestamp):
    """Parse timestamp dari berbagai format - DIPERBAIKI"""
    try:
        if isinstance(timestamp, (int, float)):
            return timestamp
        
        # Coba format ISO (2026-02-04T18:08:15Z)
        if "T" in timestamp and "Z" in timestamp:
            dt_str = timestamp.replace("Z", "+00:00")
            dt = datetime.fromisoformat(dt_str)
            return dt.timestamp()
        
        # Coba format dengan timezone
        if "T" in timestamp and ("+" in timestamp or timestamp.count("-") == 2):
            dt = datetime.fromisoformat(timestamp)
            return dt.timestamp()
        
        # Coba format standar
        try:
            dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            return dt.timestamp()
        except ValueError:
            pass
        
        # Coba format lain
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"]:
            try:
                dt = datetime.strptime(timestamp, fmt)
                return dt.timestamp()
            except ValueError:
                continue
        
        return None
        
    except Exception as e:
        return None

def check_workspace_status(package, package_info, cfg):
    """
    FINAL PC-STYLE LOGIC
    """

    username = package_info["username"]
    now = time.time()

    # 1. APP TIDAK BERJALAN → OFFLINE
    if not is_app_running(package):
        return "offline"

    # 2. AMBIL WAKTU LAUNCH
    launch_time = last_launch_time.get(package)
    if launch_time is None:
        launch_time = now
        last_launch_time[package] = now

    # 3. AMBIL JSON TIMESTAMP
    timestamp = get_json_timestamp(package_info, username, cfg)

    # 4. JSON BELUM ADA → WAITING (grace period)
    if timestamp is None:
        if (now - launch_time) <= cfg["workspace_timeout"]:
            return "waiting"
        else:
            # APP HIDUP TAPI TIDAK ADA JSON → HANG
            log(f"{username}: workspace never appeared, stopping app")
            stop_app(package)
            return "offline"

    json_time = parse_timestamp(timestamp)
    if json_time is None:
        return "waiting"

    # 5. JSON LAMA (SEBELUM LAUNCH) → WAITING
    if json_time < launch_time:
        return "waiting"

    # 6. JSON BARU TAPI TIDAK UPDATE → TIMEOUT
    if (now - json_time) > cfg["workspace_timeout"]:
        log(f"{username}: workspace timeout, stopping app")
        stop_app(package)
        time.sleep(cfg["restart_delay"])
        return "offline"

    # 7. NORMAL ONLINE
    return "online"

def wait_for_workspace(package, package_info, cfg, max_wait=None):
    """Tunggu sampai workspace online - DIPERBAIKI"""
    if max_wait is None:
        max_wait = cfg["workspace_timeout"]
    
    username = package_info["username"]
    log(f"Waiting for workspace: {username}")
    
    start = time.time()
    last_status = None
    
    while (time.time() - start) < max_wait:
        status = check_workspace_status(package, package_info, cfg)
        
        if status != last_status:
            log(f"Status {username}: {status}")
            last_status = status
        
        if status == "online":
            log(f"{username} is online!")
            return True
        
        time.sleep(cfg["workspace_check_interval"])
    
    log(f"{username} timeout")
    return False

# =========================
# SEQUENTIAL STARTUP
# =========================
def sequential_startup(pkgs, cfg):
    """Start semua package satu per satu dan tunggu online"""
    success_count = 0
    
    # TAMBAHAN: Stop semua package dulu sebelum mulai startup
    log("=" * 60)
    log("STOPPING ALL EXISTING INSTANCES")
    log("=" * 60)
    for pkg, info in pkgs.items():
        if is_app_running(pkg):
            log(f"Stopping existing instance: {info['username']}")
            stop_app(pkg)
    
    # Tunggu sebentar setelah stop semua
    log("Waiting after stopping all...")
    time.sleep(cfg["restart_delay"] * 2)
    
    # Mulai startup satu per satu
    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        username = info["username"]
        print(f"\n{'='*60}")
        print(f"Starting {i}/{len(pkgs)}: {username}")
        print(f"{'='*60}")
        
        if is_app_running(pkg):
        log("Stopping existing instance")
        stop_app(pkg)
        time.sleep(cfg["restart_delay"])
        
        # Mulai aplikasi
        log("Starting application")
        start_app(pkg, cfg["game_id"])
        time.sleep(cfg["startup_delay"])
        
        # Tunggu sampai online
        if wait_for_workspace(pkg, info, cfg):
            success_count += 1
            print(f"✅ {username} is ONLINE")
            
            if i < len(pkgs):
                print(f"⏳ Waiting 3 seconds before next account...")
                time.sleep(3)
        else:
            print(f"❌ {username} FAILED to go online")
            
            # Retry
            print(f"🔄 Retrying {username}...")
            stop_app(pkg)
            time.sleep(cfg["restart_delay"])
            start_app(pkg, cfg["game_id"])
            time.sleep(cfg["startup_delay"])
            
            if wait_for_workspace(pkg, info, cfg, max_wait=90):
                success_count += 1
                print(f"✅ {username} is ONLINE (after retry)")
            else:
                print(f"❌ {username} STILL FAILED")
    
    print("\n" + "=" * 60)
    print(f"📊 RESULTS: {success_count}/{len(pkgs)} packages online")
    print("=" * 60)
    
    return success_count

def restart_all_roblox(pkgs, cfg):
    """Restart semua Roblox package"""
    log("=" * 60)
    log("RESTARTING ALL ROBLOX PACKAGES")
    log("=" * 60)
    
    # Stop semua
    for pkg, info in pkgs.items():
        if is_app_running(pkg):
            log(f"Stopping {info['username']}")
            stop_app(pkg)
    
    log("Waiting before restart...")
    time.sleep(cfg["restart_delay"] * 2)
    
    # Start semua sequential
    return sequential_startup(pkgs, cfg)

# =========================
# MONITOR WITH TABLE
# =========================
def display_status_table(pkgs, cfg):
    clear_screen()

    WIDTH = 86
    print("═" * WIDTH)
    print(" ROBLOX OVA Rejoin ".center(WIDTH, "═"))
    print("═" * WIDTH)

    header = (
        f"│ {'No':<3} "
        f"│ {'Username':<18} "
        f"│ {'Package':<32} "
        f"│ {'Status':<12} │"
    )
    print(header)
    print("├" + "─"*5 + "┼" + "─"*20 + "┼" + "─"*34 + "┼" + "─"*14 + "┤")

    all_online = True

    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        username = info["username"]
        status = check_workspace_status(pkg, info, cfg)

        pkg_display = pkg if len(pkg) <= 32 else pkg[:29] + "..."

        if status == "online":
            status_display = "✅ ONLINE"
        elif status == "waiting":
            status_display = "⏳ WAITING"
            all_online = False
        else:
            status_display = "❌ OFFLINE"
            all_online = False

        row = (
            f"│ {i:<3} "
            f"│ {username:<18} "
            f"│ {pkg_display:<32} "
            f"│ {status_display:<12} │"
        )
        print(row)

    print("═" * WIDTH)
    print(
        f" 🕒 {time.strftime('%H:%M:%S')}  |  "
        f"📦 Packages: {len(pkgs)}  |  "
        f"🌐 All Online: {'YES ✅' if all_online else 'NO ❌'}"
    )
    print("═" * WIDTH)

    return all_online

def monitor():
    global monitor_active
    
    cfg = load_config()
    pkgs = load_packages()
    
    if not cfg["game_id"]:
        log("Game ID not set")
        input("Enter...")
        return
    
    if not pkgs:
        log("No packages found")
        input("Enter...")
        return
    
    monitor_active = True
    
    # Sequential startup
    print("=" * 60)
    print("STARTING SEQUENTIAL STARTUP")
    print("=" * 60)
    time.sleep(2)
    
    online_count = sequential_startup(pkgs, cfg)
    
    if online_count == 0:
        log("No accounts went online")
        input("Enter...")
        return
    
    log(f"\n{online_count} accounts online, starting monitor...")
    time.sleep(3)
    
    # Send startup webhook
    if cfg.get("webhook_enabled", False) and cfg.get("webhook_url", ""):
        message = build_webhook_status_message(pkgs, cfg)
        send_discord_webhook(
            cfg["webhook_url"],
            "🚀 Monitor Started",
            message,
            3066993  # Green
        )
    
    try:
        cycle_count = 0
        last_webhook_time = time.time()
        last_restart_time = time.time()
        
        while monitor_active:
            cycle_count += 1
            
            # Tampilkan tabel
            all_online = display_status_table(pkgs, cfg)
            
            if cycle_count == 1 or cycle_count % 5 == 0:
                log(f"Monitoring cycle #{cycle_count}")
            
            # Periksa dan perbaiki status
            needs_fix = False
            for pkg, info in pkgs.items():
                status = check_workspace_status(pkg, info, cfg)
                
                if status == "offline":
                    log(f"Restarting offline: {info['username']}")
                    stop_app(pkg)
                    time.sleep(cfg["restart_delay"])
                    start_app(pkg, cfg["game_id"])
                    time.sleep(cfg["startup_delay"])
                    needs_fix = True
                
            
            if needs_fix:
                log("Waiting for fixes...")
                time.sleep(5)
            
            # Cek webhook interval
            current_time = time.time()
            webhook_interval_seconds = cfg.get("webhook_interval", 10) * 60
            
            if cfg.get("webhook_enabled", False) and cfg.get("webhook_url", ""):
                if (current_time - last_webhook_time) >= webhook_interval_seconds:
                    message = build_webhook_status_message(pkgs, cfg)
                    send_discord_webhook(
                        cfg["webhook_url"],
                        "📊 Status Update",
                        message,
                        3447003  # Blue
                    )
                    last_webhook_time = current_time
                    log("📡 Webhook sent")
            
            # Cek restart interval
            restart_interval_seconds = cfg.get("restart_interval", 0) * 60
            if restart_interval_seconds > 0:
                if (current_time - last_restart_time) >= restart_interval_seconds:
                    log("⏰ Auto restart time reached")
                    
                    # Send webhook before restart
                    if cfg.get("webhook_enabled", False) and cfg.get("webhook_url", ""):
                        send_discord_webhook(
                            cfg["webhook_url"],
                            "🔄 Auto Restart",
                            "Restarting all Roblox packages...",
                            16776960  # Yellow
                        )
                    
                    restart_all_roblox(pkgs, cfg)
                    last_restart_time = current_time
                    
                    # Wait for all to come online
                    log("Waiting for restart...")
                    time.sleep(cfg["startup_delay"] * 2)
            
            time.sleep(cfg["check_interval"])
    
    except Exception as e:
        log(f"Monitor error: {e}")
        
        # Send error webhook
        if cfg.get("webhook_enabled", False) and cfg.get("webhook_url", ""):
            send_discord_webhook(
                cfg["webhook_url"],
                "❌ Monitor Error",
                f"Error occurred: {str(e)}",
                16711680  # Red
            )
        
        input("Press ENTER...")
    finally:
        monitor_active = False

# =========================
# MENU
# =========================
def menu():
    while True:
        clear_screen()
        cfg = load_config()
        pkgs = load_packages()
        
        print("=" * 70)
        print("🤖 ROBLOX MULTI-PACKAGE MANAGER (Termux/Cloudphone)")
        print("=" * 70)
        print(f"🎮 Game ID : {cfg.get('game_id', 'Not set')}")
        print(f"📦 Packages: {len(pkgs)}")
        
        # Webhook info
        webhook_status = "✅" if cfg.get("webhook_enabled", False) else "❌"
        print(f"📡 Webhook : {webhook_status}")
        if cfg.get("webhook_url", ""):
            webhook_display = cfg["webhook_url"][:40] + "..." if len(cfg["webhook_url"]) > 40 else cfg["webhook_url"]
            print(f"   URL     : {webhook_display}")
            print(f"   Interval: {cfg.get('webhook_interval', 10)} minutes")
            restart_text = f"{cfg.get('restart_interval', 0)} minutes" if cfg.get('restart_interval', 0) > 0 else "Disabled"
            print(f"   Restart : {restart_text}")
        
        if pkgs:
            print("\n📋 Registered Packages:")
            for i, (pkg, info) in enumerate(pkgs.items(), 1):
                cache_dir = find_cache_dir(pkg)
                cache_status = "✅" if cache_dir else "❌"
                scripts_count = len(list_executor_scripts(info))
                print(f"  {i}. {info['username']} (🗂️:{cache_status} 📜:{scripts_count})")
        
        print("=" * 70)
        print("1. 🚀 Start Monitor (All Packages)")
        print("2. ⚙️ Set Game ID")
        print("3. 🔍 Auto Detect Packages")
        print("4. 📡 Configure Webhook")
        print("-" * 40)
        print("5. 📝 Add Script to ALL Packages")
        print("6. 🗑️ Delete Script from ALL Packages")
        print("7. 👁️ View Scripts in ALL Packages")
        print("-" * 40)
        print("8. 🗂️ Copy ALL Cache Files to ALL Packages")
        print("9. 📊 View Cache Folder Status")
        print("-" * 40)
        print("T. 🧪 Test Workspace Detection")
        print("W. 📡 Test Webhook")
        print("C. 🖥️ Test CPU/RAM Detection")
        print("0. ❌ Exit\n")
        
        c = input("📌 Select: ").strip()
        
        if c == "1": 
            monitor()
        elif c == "2":
            cfg["game_id"] = input("🎮 Game ID: ").strip()
            save_config(cfg)
            input("Enter...")
        elif c == "3": 
            auto_detect_and_save_packages()
        elif c == "4":
            # Configure webhook
            clear_screen()
            print("=" * 70)
            print("📡 WEBHOOK CONFIGURATION")
            print("=" * 70)
            
            webhook_url = input("🔗 Discord webhook URL (Enter to skip): ").strip()
            if webhook_url:
                cfg["webhook_url"] = webhook_url
            
            enable = input("📡 Enable webhook? (y/n, default=y): ").strip().lower()
            cfg["webhook_enabled"] = (enable == "" or enable == "y")
            
            interval = input("⏱️ Webhook interval in minutes (default=10): ").strip()
            if interval:
                try:
                    cfg["webhook_interval"] = int(interval)
                except:
                    cfg["webhook_interval"] = 10
            
            restart = input("🔄 Restart interval in minutes (0=disabled, default=0): ").strip()
            if restart:
                try:
                    cfg["restart_interval"] = int(restart)
                except:
                    cfg["restart_interval"] = 0
            
            save_config(cfg)
            log("✅ Webhook configuration saved")
            input("\nPress ENTER...")
        elif c == "5": 
            add_script_to_all_packages()
        elif c == "6": 
            delete_script_from_all_packages()
        elif c == "7": 
            view_scripts_all_packages()
        elif c == "8": 
            copy_license_to_all_packages()
        elif c == "9": 
            view_license_status()
        elif c.lower() == "t":
            # Test workspace
            if pkgs:
                clear_screen()
                print("🧪 Testing workspace detection...")
                for pkg, info in pkgs.items():
                    timestamp = get_json_timestamp(info, info["username"], cfg)
                    print(f"{info['username']}: {timestamp}")
                input("\nPress ENTER...")
        elif c.lower() == "w":
            # Test webhook - IMPROVED
            clear_screen()
            print("=" * 70)
            print("📡 TEST WEBHOOK")
            print("=" * 70)
            
            if not cfg.get("webhook_url", ""):
                log("❌ Webhook URL not configured!")
                input("\nPress ENTER...")
            else:
                log(f"Testing webhook: {cfg['webhook_url'][:50]}...")
                
                # Get real-time stats untuk test
                cpu_percent = get_cpu_usage()
                cpu_cores = get_cpu_count()
                ram_info = get_ram_info()
                
                test_message = "**🧪 Test Message**\n"
                test_message += f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                test_message += "**📊 System Info**\n"
                
                if cpu_percent != "N/A":
                    test_message += f"CPU: {cpu_percent}% ({cpu_cores} cores)\n"
                else:
                    test_message += f"CPU: N/A ({cpu_cores} cores)\n"
                
                if ram_info:
                    ram_total_gb = ram_info['total_mb'] / 1024
                    ram_used_gb = ram_info['used_mb'] / 1024
                    test_message += f"RAM: {ram_info['percent']}% ({ram_used_gb:.2f} GB / {ram_total_gb:.2f} GB)"
                else:
                    test_message += "RAM: N/A"
                
                result = send_discord_webhook(
                    cfg["webhook_url"],
                    "🧪 Webhook Test",
                    test_message,
                    3447003  # Blue
                )
                
                if result:
                    log("✅ Webhook sent successfully!")
                else:
                    log("❌ Webhook failed to send!")
                
                input("\nPress ENTER...")
        elif c.lower() == "c":
            # Test CPU/RAM detection - IMPROVED
            clear_screen()
            print("=" * 70)
            print("🖥️ TEST CPU & RAM DETECTION ")
            print("=" * 70)
            
            log("Testing CPU & RAM detection methods...")
            log("Collecting data (please wait)...\n")
            
            cpu_percent = get_cpu_usage()
            cpu_cores = get_cpu_count()
            ram_info = get_ram_info()
            
            print(f"📊 SYSTEM INFORMATION:")
            print(f"  CPU Cores: {cpu_cores}")
            
            if cpu_percent != "N/A":
                print(f"  CPU Usage: {cpu_percent}%")
            else:
                print(f"  CPU Usage: N/A (detection unavailable)")
            
            if ram_info:
                ram_total_gb = ram_info['total_mb'] / 1024
                ram_used_gb = ram_info['used_mb'] / 1024
                ram_available_gb = ram_info['available_mb'] / 1024
                
                print(f"\n  RAM Total: {ram_total_gb:.2f} GB ({ram_info['total_mb']:.0f} MB)")
                print(f"  RAM Used: {ram_used_gb:.2f} GB ({ram_info['used_mb']:.0f} MB)")
                print(f"  RAM Available: {ram_available_gb:.2f} GB ({ram_info['available_mb']:.0f} MB)")
                print(f"  RAM Usage: {ram_info['percent']}%")
            else:
                print(f"  RAM: Detection failed")
            
            # Show Roblox processes
            print(f"\n🎮 ROBLOX PROCESSES:")
            try:
                result = subprocess.run(
                    ['pgrep', '-f', 'com.roblox'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                pids = result.stdout.strip().split('\n')
                if pids and pids[0]:
                    print(f"  Found {len(pids)} Roblox process(es)")
                    for pid in pids[:5]:
                        print(f"    PID: {pid}")
                else:
                    print("  No Roblox processes running")
            except:
                print("  Unable to detect processes")
            
            input("\nPress ENTER...")
        elif c == "0": 
            break

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    menu()
