#!/usr/bin/env python3
"""
AstralForge Manifest Generator
Автоматический генератор manifest.json для сборки Minecraft 1.21.1 (NeoForge 21.1.267).

Возможности:
1. Сканирует папку mods/ и вычисляет SHA-512 / SHA-256 хеши каждого мода.
2. Отправляет запрос к Modrinth API: если мод есть на Modrinth, берет быструю ссылку с cdn.modrinth.com.
3. Если мода нет на Modrinth (кастомный мод сервера), прописывает ссылку на GitHub raw.
4. Сканирует папку config/ и добавляет все файлы с ссылками на GitHub raw.
5. Формирует актуальный manifest.json.
"""

import os
import sys
import json
import hashlib
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

CONFIG_FILE = "repo_config.json"
MODRINTH_API = "https://api.modrinth.com/v2/version_files"
USER_AGENT = "Eternum-ManifestGen/1.0"

EXTRA_DIRS = [
    "config",
    "defaultconfigs",
    "resourcepacks",
    "shaderpacks",
    "kubejs",
    "datapacks",
]
EXTRA_ROOT_FILES = ["options.txt", "servers.dat"]


def make_github_raw_url(config, rel_path):
    encoded = urllib.parse.quote(rel_path, safe="/")
    return (
        f"https://raw.githubusercontent.com/{config['repo_owner']}/"
        f"{config['repo_name']}/{config['branch']}/{encoded}"
    )


def load_config():
    config_path = Path(__file__).resolve().parent / CONFIG_FILE
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "repo_owner": "meed0ff",
        "repo_name": "eternum-modpack",
        "branch": "main",
        "pack_version": "1.0 - test",
        "minecraft_version": "26.3",
        "loader_type": "fabric",
        "loader_version": "0.20.1",
        "server_name": "Eternum",
        "server_ip": "eternum.aurorix.pro"
    }


def compute_hashes(filepath):
    """Вычисляет sha256 и sha512 файла блоками по 64 КБ."""
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512()
    size = 0
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
            sha512.update(chunk)
            size += len(chunk)
    return sha256.hexdigest(), sha512.hexdigest(), size


def query_modrinth_files(sha512_hashes):
    """Запрашивает у Modrinth API информацию о файлах по их SHA-512."""
    if not sha512_hashes:
        return {}

    req_data = json.dumps({"hashes": sha512_hashes, "algorithm": "sha512"}).encode("utf-8")
    req = urllib.request.Request(
        MODRINTH_API,
        data=req_data,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"⚠️  Ошибка запроса к Modrinth API: {e}. Файлы будут скачиваться с GitHub.")
    return {}


def main():
    config = load_config()
    print("=" * 60)
    print(f"🚀 Генератор манифеста {config.get('server_name', 'AstralForge')}")
    print(f"📦 Minecraft {config['minecraft_version']} | {config['loader_type']} {config['loader_version']}")
    print(f"🔗 Репозиторий: {config['repo_owner']}/{config['repo_name']} (ветка: {config['branch']})")
    print("=" * 60)

    base_dir = Path(__file__).resolve().parent
    mods_dir = base_dir / "mods"
    config_dir = base_dir / "config"

    mods_dir.mkdir(exist_ok=True)
    config_dir.mkdir(exist_ok=True)

    # 1. Сканирование модов
    mod_files = [f for f in mods_dir.glob("*.jar") if f.is_file()]
    print(f"\n[1/3] Найдено модов в mods/: {len(mod_files)}")

    mod_data_map = {}
    sha512_list = []

    for mod_path in mod_files:
        sha256, sha512, size = compute_hashes(mod_path)
        mod_data_map[mod_path.name] = {
            "path": f"mods/{mod_path.name}",
            "sha256": sha256,
            "sha512": sha512,
            "size": size
        }
        sha512_list.append(sha512)

    # 2. Поиск модов на Modrinth
    print("[2/3] Поиск модов на серверах Modrinth...")
    modrinth_results = {}
    # Modrinth принимает пачками
    chunk_size = 100
    for i in range(0, len(sha512_list), chunk_size):
        chunk = sha512_list[i:i + chunk_size]
        res = query_modrinth_files(chunk)
        modrinth_results.update(res)

    files_list = []
    modrinth_count = 0
    github_count = 0

    for filename, item in mod_data_map.items():
        sha512 = item["sha512"]
        if sha512 in modrinth_results:
            mod_info = modrinth_results[sha512]
            # Находим URL файла в версии Modrinth
            file_url = None
            for file_entry in mod_info.get("files", []):
                if file_entry.get("hashes", {}).get("sha512") == sha512:
                    file_url = file_entry.get("url")
                    break

            if file_url:
                files_list.append({
                    "path": item["path"],
                    "source": "modrinth",
                    "url": file_url,
                    "sha256": item["sha256"],
                    "size": item["size"]
                })
                modrinth_count += 1
                print(f"  ✓ [Modrinth CDN] {filename}")
                continue

        # Если мода нет на Modrinth
        raw_url = make_github_raw_url(config, item["path"])
        files_list.append({
            "path": item["path"],
            "source": "github",
            "url": raw_url,
            "sha256": item["sha256"],
            "size": item["size"]
        })
        github_count += 1
        print(f"  • [GitHub Raw]   {filename}")

    # 3. Сканирование дополнительных папок (config, resourcepacks, shaderpacks, kubejs и т.д.)
    print("\n[3/4] Сканирование конфигураций, ресурспаков, шейдеров, kubejs...")
    extra_count = 0
    for folder_name in EXTRA_DIRS:
        folder_dir = base_dir / folder_name
        folder_dir.mkdir(exist_ok=True)
        for root, _, files in os.walk(folder_dir):
            for file in sorted(files):
                if file == ".gitkeep":
                    continue
                file_path = Path(root) / file
                rel_path = file_path.relative_to(base_dir).as_posix()
                sha256, _, size = compute_hashes(file_path)
                files_list.append({
                    "path": rel_path,
                    "source": "github",
                    "url": make_github_raw_url(config, rel_path),
                    "sha256": sha256,
                    "size": size
                })
                extra_count += 1

    for root_file in EXTRA_ROOT_FILES:
        file_path = base_dir / root_file
        if file_path.is_file():
            sha256, _, size = compute_hashes(file_path)
            files_list.append({
                "path": root_file,
                "source": "github",
                "url": make_github_raw_url(config, root_file),
                "sha256": sha256,
                "size": size
            })
            extra_count += 1

    print(f"  Добавлено доп. файлов: {extra_count}")

    # 4. Сканирование zip-бандлов для клиентской распаковки (bundles/*.zip)
    print("\n[4/4] Сканирование zip-бандлов (bundles/)...")
    bundles_dir = base_dir / "bundles"
    bundles_dir.mkdir(exist_ok=True)
    extract_map = config.get("bundles_extract_map", {})
    bundle_count = 0
    for bundle_path in sorted(bundles_dir.glob("*.zip")):
        if not bundle_path.is_file():
            continue
        rel_path = bundle_path.relative_to(base_dir).as_posix()
        sha256, _, size = compute_hashes(bundle_path)
        extract_to = extract_map.get(
            bundle_path.name,
            "" if bundle_path.stem == "root" else bundle_path.stem,
        )
        files_list.append({
            "path": rel_path,
            "source": "github",
            "url": make_github_raw_url(config, rel_path),
            "sha256": sha256,
            "size": size,
            "extractTo": extract_to,
        })
        bundle_count += 1
        print(f"  📦 [Bundle -> /{extract_to}] {bundle_path.name}")

    # 5. Формирование manifest.json
    loader_type = config["loader_type"].lower()
    if loader_type == "fabric":
        installer_url = (
            f"https://meta.fabricmc.net/v2/versions/loader/"
            f"{config['minecraft_version']}/{config['loader_version']}/profile/json"
        )
    else:
        installer_url = (
            f"https://maven.neoforged.net/releases/net/neoforged/neoforge/"
            f"{config['loader_version']}/neoforge-{config['loader_version']}-installer.jar"
        )

    manifest = {
        "schemaVersion": 1,
        "packVersion": config.get("pack_version", "1.0"),
        "server": {
            "name": config.get("server_name", "Eternum"),
            "ip": config.get("server_ip", "eternum.aurorix.pro")
        },
        "minecraft": {
            "version": config["minecraft_version"]
        },
        "loader": {
            "type": config["loader_type"],
            "version": config["loader_version"],
            "installer_url": installer_url
        },
        "elyby": {
            "clientId": "astralforge1"
        },
        "files": files_list
    }

    manifest_path = base_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"🎉 Готово! manifest.json успешно сгенерирован:")
    print(f"   - Модов с Modrinth CDN: {modrinth_count}")
    print(f"   - Модов с GitHub:        {github_count}")
    print(f"   - Доп. файлов (конфиги/ресурспаки/шейдеры): {extra_count}")
    print(f"   - ZIP-бандлов:           {bundle_count}")
    print(f"   - Всего записей:         {len(files_list)}")
    print(f"Сохранено в: {manifest_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
