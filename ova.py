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
# WEBHOOK DISCORD
# =========================
def escape_json_string(s):
    """Escape string untuk JSON"""
    s = str(s)
    s = s.replace('\\', '\\\\')
    s = s.replace('"', '\\"')
    s = s.replace('\n', '\\n')
    s = s.replace('\r', '\\r')
    s = s.replace('\t', '\\t')
    return s

def send_discord_webhook(webhook_url, title, message, color=None):
    """Kirim pesan ke Discord webhook"""
    if not webhook_url or webhook_url == "":
        return
    
    color = color or 16711680  # Red default
    payload = '{"embeds":[{"title":"%s","description":"%s","color":%d,"timestamp":"%s"}]}' % (
        escape_json_string(title),
        escape_json_string(message),
        color,
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    
    cmd = 'curl -s -X POST -H "Content-Type: application/json" -d \'%s\' "%s" 2>/dev/null' % (
        payload,
        webhook_url
    )
    os.system(cmd)

def get_cpu_usage():
    """Mendapatkan CPU usage dalam persen - LAZY MODE (hanya saat dipanggil)"""
    try:
        # Baca /proc/stat untuk CPU usage
        with open('/proc/stat', 'r') as f:
            line = f.readline()
            cpu_times = [float(x) for x in line.split()[1:]]
            idle_time = cpu_times[3]
            total_time = sum(cpu_times)
        
        time.sleep(0.5)  # Sleep singkat untuk akurasi
        
        with open('/proc/stat', 'r') as f:
            line = f.readline()
            cpu_times2 = [float(x) for x in line.split()[1:]]
            idle_time2 = cpu_times2[3]
            total_time2 = sum(cpu_times2)
        
        idle_delta = idle_time2 - idle_time
        total_delta = total_time2 - total_time
        usage = 100.0 * (1.0 - idle_delta / total_delta) if total_delta > 0 else 0
        return round(usage, 1)
    except:
        return 0

def get_ram_usage():
    """Mendapatkan RAM usage dalam persen"""
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
        
        mem_total = 0
        mem_available = 0
        
        for line in lines:
            if line.startswith('MemTotal:'):
                mem_total = int(line.split()[1])
            elif line.startswith('MemAvailable:'):
                mem_available = int(line.split()[1])
        
        if mem_total > 0:
            usage = 100.0 * (1.0 - mem_available / mem_total)
            return round(usage, 1)
        return 0
    except:
        return 0

def build_webhook_status_message(pkgs, cfg):
    """Membuat pesan status untuk webhook - CPU/RAM dihitung di sini (lazy)"""
    cpu = get_cpu_usage()  # Hanya dipanggil saat webhook
    ram = get_ram_usage()  # Hanya dipanggil saat webhook
    
    message = f"**📊 System Status**\\n"
    message += f"CPU: {cpu}%\\n"
    message += f"RAM: {ram}%\\n"
    message += f"\\n**👥 Account Status**\\n"
    
    for pkg, info in pkgs.items():
        username = info["username"]
        status = check_workspace_status(pkg, info, cfg)
        
        if status == "online":
            status_emoji = "✅"
        elif status == "waiting":
            status_emoji = "⏳"
        elif status == "stale":
            status_emoji = "⚠️"
        else:
            status_emoji = "❌"
        
        message += f"{status_emoji} {username}: {status.upper()}\\n"
    
    return message

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
    log(f"Stopped {package}")

def start_app(package, game_id):
    cmd = (
        f"am start -a android.intent.action.VIEW "
        f"-d 'roblox://placeID={game_id}' "
        f"-n {package}/com.roblox.client.ActivityProtocolLaunch"
    )
    run_root_cmd(cmd)
    log(f"Started {package}")

# =========================
# LICENSE KEY MANAGEMENT - DIPERBAIKI
# =========================
def find_cache_dir(package_name):
    """Mencari folder Cache yang ada untuk package tertentu"""
    possible_paths = [
        f"/storage/emulated/0/Android/data/{package_name}/files/gloop/external/Internals/Cache",
        f"/sdcard/Android/data/{package_name}/files/gloop/external/Internals/Cache",
        f"/storage/sdcard/Android/data/{package_name}/files/gloop/external/Internals/Cache",
        f"/mnt/sdcard/Android/data/{package_name}/files/gloop/external/Internals/Cache",
    ]
    
    for path in possible_paths:
        if os.path.isdir(path):  # PERBAIKAN: gunakan isdir untuk folder
            return path
    
    return None

def check_license_exists(package_info):
    """Cek apakah file license ada untuk package tertentu"""
    package_name = package_info.get("package_name", "com.roblox.client")
    cache_dir = find_cache_dir(package_name)
    
    if cache_dir:
        # Cek apakah ada file di dalam folder Cache
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
    
    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        username = info['username']
        cache_dir = find_cache_dir(pkg)
        
        print(f"\n{i}. {username} ({pkg})")
        
        if cache_dir:
            print(f"   ✅ Cache folder: {cache_dir}")
            try:
                files = os.listdir(cache_dir)
                print(f"   📄 Files: {len(files)}")
                for f in files[:3]:
                    print(f"      - {f}")
                if len(files) > 3:
                    print(f"      ... and {len(files) - 3} more")
            except Exception as e:
                print(f"   ⚠️ Error reading: {e}")
        else:
            print(f"   ❌ No cache folder found")
    
    print("\n" + "=" * 70)
    input("Press ENTER...")

# =========================
# SCRIPT MANAGEMENT
# =========================
def list_executor_scripts(package_info):
    """List semua script di autoexec folder"""
    autoexec_dir = package_info.get("autoexec_dir", "")
    if not autoexec_dir or not os.path.exists(autoexec_dir):
        return []
    
    try:
        files = [f for f in os.listdir(autoexec_dir) if f.endswith('.lua') or f.endswith('.txt')]
        return files
    except:
        return []

def add_script_to_all_packages():
    """Tambah script ke semua package"""
    clear_screen()
    print("=" * 70)
    print("📝 ADD SCRIPT TO ALL PACKAGES")
    print("=" * 70)
    
    pkgs = load_packages()
    if not pkgs:
        log("No packages found")
        input("\nPress ENTER...")
        return
    
    # Input script name
    script_name = input("\n📌 Script filename (e.g. script.lua): ").strip()
    if not script_name:
        log("Cancelled")
        input("\nPress ENTER...")
        return
    
    # Input script content
    print("\n📝 Enter script content (type 'END' on new line to finish):")
    lines = []
    while True:
        line = input()
        if line == "END":
            break
        lines.append(line)
    
    script_content = "\n".join(lines)
    
    if not script_content.strip():
        log("Empty script, cancelled")
        input("\nPress ENTER...")
        return
    
    # Konfirmasi
    print(f"\n📄 Script: {script_name}")
    print(f"📏 Length: {len(script_content)} chars")
    confirm = input("\n⚠️ Add to ALL packages? (y/n): ").strip().lower()
    
    if confirm != 'y':
        log("Cancelled")
        input("\nPress ENTER...")
        return
    
    # Add ke semua package
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
# WORKSPACE MONITORING
# =========================
def get_json_timestamp(package_info, username, cfg):
    """Baca timestamp dari JSON file workspace"""
    workspace_dir = package_info.get("workspace_dir", "")
    json_file = workspace_dir + "/" + username + cfg["json_suffix"]
    
    if not os.path.exists(json_file):
        return None
    
    try:
        with open(json_file) as f:
            data = json.load(f)
            return data.get("timestamp")
    except:
        return None

def check_workspace_status(package, package_info, cfg):
    """
    Cek status workspace dari timestamp JSON
    Returns: online/waiting/stale/offline
    """
    username = package_info["username"]
    
    # Cek apakah app berjalan
    if not is_app_running(package):
        return "offline"
    
    # Cek timestamp
    ts = get_json_timestamp(package_info, username, cfg)
    if ts is None:
        return "waiting"
    
    age = time.time() - ts
    
    if age < cfg["workspace_timeout"]:
        return "online"
    else:
        return "stale"

def wait_for_workspace(package, package_info, cfg, max_wait=None):
    """Tunggu sampai workspace online"""
    if max_wait is None:
        max_wait = cfg["workspace_timeout"]
    
    username = package_info["username"]
    log(f"Waiting for workspace: {username}")
    
    start = time.time()
    while (time.time() - start) < max_wait:
        status = check_workspace_status(package, package_info, cfg)
        
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
    
    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        username = info["username"]
        print(f"\n{'='*60}")
        print(f"Starting {i}/{len(pkgs)}: {username}")
        print(f"{'='*60}")
        
        # Stop jika sedang running
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
    """Restart semua package Roblox"""
    log("🔄 RESTARTING ALL ROBLOX PACKAGES")
    
    # Stop semua
    for pkg, info in pkgs.items():
        if is_app_running(pkg):
            stop_app(pkg)
    
    time.sleep(cfg["restart_delay"])
    
    # Start semua
    for pkg, info in pkgs.items():
        start_app(pkg, cfg["game_id"])
        time.sleep(2)  # Delay antar start

# =========================
# MONITOR WITH TABLE
# =========================
def display_status_table(pkgs, cfg):
    """Menampilkan tabel status semua paket"""
    clear_screen()
    print("=" * 80)
    print("ROBLOX WORKSPACE MONITOR - ALL PACKAGES")
    print("=" * 80)
    print(f"{'No.':<3} {'Username':<20} {'Package':<25} {'Status':<12} {'Cache':<8}")
    print("-" * 80)
    
    all_online = True
    
    for i, (pkg, info) in enumerate(pkgs.items(), 1):
        username = info["username"]
        status = check_workspace_status(pkg, info, cfg)
        
        # Cek cache
        cache_dir = find_cache_dir(pkg)
        cache_status = "✅" if cache_dir else "❌"
        
        # Potong nama package
        pkg_display = pkg[:22] + "..." if len(pkg) > 25 else pkg
        
        # Format status
        if status == "online":
            status_display = "✅ ONLINE"
        elif status == "waiting":
            status_display = "⏳ WAITING"
            all_online = False
        elif status == "stale":
            status_display = "⚠️ STALE"
            all_online = False
        elif status == "offline":
            status_display = "❌ OFFLINE"
            all_online = False
        else:
            status_display = status.upper()
            all_online = False
        
        print(f"{i:<3} {username:<20} {pkg_display:<25} {status_display:<12} {cache_status:<8}")
    
    print("=" * 80)
    print(f"🕐 Last Update: {time.strftime('%H:%M:%S')} | 📦 Packages: {len(pkgs)}")
    print(f"🌐 All Online: {'✅ YES' if all_online else '❌ NO'}")
    print("Press Ctrl+C to stop monitoring")
    print("=" * 80)
    
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
    
    # Tanya webhook settings
    webhook_choice = input("📡 Send webhook? (y/n, default=y): ").strip().lower()
    if webhook_choice == "" or webhook_choice == "y":
        cfg["webhook_enabled"] = True
        
        if not cfg.get("webhook_url", ""):
            webhook_url = input("🔗 Discord webhook URL: ").strip()
            cfg["webhook_url"] = webhook_url
            save_config(cfg)
        
        webhook_interval = input("⏱️ Webhook interval in minutes (default=10): ").strip()
        if webhook_interval:
            try:
                cfg["webhook_interval"] = int(webhook_interval)
            except:
                cfg["webhook_interval"] = 10
        else:
            cfg["webhook_interval"] = 10
        
        restart_interval = input("🔄 Restart interval in minutes (0=disabled, default=0): ").strip()
        if restart_interval:
            try:
                cfg["restart_interval"] = int(restart_interval)
            except:
                cfg["restart_interval"] = 0
        else:
            cfg["restart_interval"] = 0
        
        save_config(cfg)
        
        log(f"✅ Webhook enabled - every {cfg['webhook_interval']} minutes")
        if cfg["restart_interval"] > 0:
            log(f"✅ Auto restart enabled - every {cfg['restart_interval']} minutes")
        else:
            log("ℹ️ Auto restart disabled")
    else:
        cfg["webhook_enabled"] = False
        log("ℹ️ Webhook disabled")
    
    time.sleep(2)
    
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
    
    # Send initial webhook
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
                    log(f"Starting offline: {info['username']}")
                    start_app(pkg, cfg["game_id"])
                    time.sleep(cfg["startup_delay"])
                    needs_fix = True
                
                elif status == "stale":
                    log(f"Restarting stale: {info['username']}")
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
    
    except KeyboardInterrupt:
        log("\nMonitor stopped")
        
        # Send final webhook
        if cfg.get("webhook_enabled", False) and cfg.get("webhook_url", ""):
            send_discord_webhook(
                cfg["webhook_url"],
                "⛔ Monitor Stopped",
                "Monitoring has been stopped manually.",
                16711680  # Red
            )
        
        input("Press ENTER...")
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
        print("🤖 ROBLOX MULTI-PACKAGE MANAGER")
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
        elif c == "0": 
            break

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    menu()
